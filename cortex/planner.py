"""
Plan generation: classifies user requests and produces structured WORKFLOW_PLAN payloads.

Three request classes:
  - INFORMATIONAL: Simple question, no plan needed (existing flow)
  - SINGLE_TOOL: One tool call, no plan needed (existing flow)
  - MULTI_STEP: Requires a structured plan (new flow)

Eight plan templates:
  - run_workflow: Analyze a local sample (stage → run → analyze)
  - compare_samples: Compare two or more samples
  - download_analyze: Download from ENCODE/URL, then analyze
  - summarize_results: Summarize existing completed job results
  - run_de_pipeline: Full differential expression analysis
  - parse_plot_interpret: Parse data, plot, and explain
  - compare_workflows: Compare outputs from two workflow runs
  - search_compare_to_local: Search ENCODE, download, compare to local data
"""

from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING

from common.logging_config import get_logger

if TYPE_CHECKING:
    from cortex.agent_engine import AgentEngine
    from cortex.schemas import ConversationState

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Request classification
# ---------------------------------------------------------------------------

# Patterns that signal a MULTI_STEP request
_MULTI_STEP_PATTERNS: list[re.Pattern] = [
    # Download + analyze
    re.compile(r"download.*(?:and|then)\s+(?:run|analyze|process)", re.I),
    re.compile(r"get.*from\s+encode.*(?:and|then)\s+(?:run|analyze|dogme)", re.I),
    re.compile(r"fetch.*(?:and|then)\s+(?:run|analyze|process)", re.I),
    # Compare
    re.compile(r"compare\s+(?:these|the|two|my|both|all)?\s*(?:samples?|results?|workflows?)", re.I),
    re.compile(r"(?:differences?|diff)\s+between\s+(?:the\s+)?(?:samples?|results?)", re.I),
    re.compile(r"(?:treated|control)\s+(?:vs?\.?|versus)\s+", re.I),
    # Pipeline / full run
    re.compile(r"run\s+(?:the\s+)?(?:full\s+)?pipeline\s+(?:on|for)", re.I),
    re.compile(r"analyze.*then\s+compare", re.I),
    re.compile(r"process.*(?:and|then)\s+(?:summarize|compare|report)", re.I),
    # Differential expression
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:differential\s+expression|DE\s+analysis)", re.I),
    re.compile(r"(?:differential\s+expression|DE)\s+(?:on|for|analysis)", re.I),
    # Parse + plot + interpret
    re.compile(r"(?:plot|graph|chart|visuali[sz]e).*(?:from|of|for)\s+(?:my|the|last|this)\s+(?:run|results?|workflow)", re.I),
    re.compile(r"(?:parse|read).*(?:and|then)\s+(?:plot|graph|chart|visuali[sz]e)", re.I),
    # Search + compare to local
    re.compile(r"(?:download|get|fetch)\s+.*encode.*(?:compare|vs)", re.I),
    re.compile(r"compare\s+(?:my\s+)?(?:local|sample).*(?:to|with|against)\s+.*(?:encode|public)", re.I),
]

# Patterns that signal an INFORMATIONAL request
_INFORMATIONAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(?:what|how|why|when|where|who|which|can you|do you|is there|are there)\s", re.I),
    re.compile(r"^(?:show|list|display|tell me about|explain|describe)\s", re.I),
    re.compile(r"^(?:what can you do|help|capabilities)\s*\??$", re.I),
]


def classify_request(
    message: str,
    active_skill: str,
    conv_state: "ConversationState",
) -> str:
    """
    Classify a user request as INFORMATIONAL, SINGLE_TOOL, or MULTI_STEP.

    Uses heuristic keyword matching — no LLM call.
    Defaults to SINGLE_TOOL when uncertain (preserving existing behaviour).
    """
    msg = message.strip()

    # 1. Check for multi-step signals first (highest specificity)
    for pat in _MULTI_STEP_PATTERNS:
        if pat.search(msg):
            logger.info("classify_request: MULTI_STEP", pattern=pat.pattern[:60])
            return "MULTI_STEP"

    # 2. Check for informational signals
    for pat in _INFORMATIONAL_PATTERNS:
        if pat.search(msg):
            return "INFORMATIONAL"

    # 3. Default: existing single-turn flow
    return "SINGLE_TOOL"


# ---------------------------------------------------------------------------
# Plan payload generation
# ---------------------------------------------------------------------------

def _step_id() -> str:
    return uuid.uuid4().hex[:12]


def _make_step(
    kind: str,
    title: str,
    order_index: int,
    *,
    tool_calls: list[dict] | None = None,
    requires_approval: bool = False,
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "id": _step_id(),
        "kind": kind,
        "title": title,
        "status": "PENDING",
        "order_index": order_index,
        "tool_calls": tool_calls or [],
        "requires_approval": requires_approval,
        "depends_on": depends_on or [],
        "result": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
    }


# ---- Template: run_workflow ------------------------------------------------

def _template_run_workflow(params: dict) -> dict:
    """
    Deterministic plan for running a local sample through Dogme.
    Steps: locate → validate → CHECK_EXISTING → [approval] → submit → monitor → summarize QC
    """
    sample_name = params.get("sample_name", "sample")
    work_dir = params.get("work_dir", "")
    input_dir = params.get("input_directory", "")

    steps = []
    idx = 0

    s_locate = _make_step("LOCATE_DATA", f"Locate data for {sample_name}", idx,
                          tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                       "params": {"work_dir": input_dir or work_dir}}])
    steps.append(s_locate)
    idx += 1

    s_validate = _make_step("VALIDATE_INPUTS", f"Validate input files for {sample_name}", idx,
                            depends_on=[s_locate["id"]])
    steps.append(s_validate)
    idx += 1

    s_check = _make_step("CHECK_EXISTING", f"Check for existing results for {sample_name}", idx,
                         depends_on=[s_validate["id"]],
                         tool_calls=[{"source_key": "analyzer", "tool": "find_file",
                                      "params": {"work_dir": work_dir, "file_name": "final_stats"}}])
    steps.append(s_check)
    idx += 1

    s_approve = _make_step("REQUEST_APPROVAL", f"Approve workflow submission for {sample_name}", idx,
                           requires_approval=True, depends_on=[s_check["id"]])
    steps.append(s_approve)
    idx += 1

    s_submit = _make_step("SUBMIT_WORKFLOW", f"Submit Dogme pipeline for {sample_name}", idx,
                          requires_approval=True, depends_on=[s_approve["id"]])
    steps.append(s_submit)
    idx += 1

    s_monitor = _make_step("MONITOR_WORKFLOW", f"Monitor pipeline progress for {sample_name}", idx,
                           depends_on=[s_submit["id"]])
    steps.append(s_monitor)
    idx += 1

    s_summary = _make_step("SUMMARIZE_QC", f"Summarize QC and results for {sample_name}", idx,
                           depends_on=[s_monitor["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "run_workflow",
        "title": f"Run analysis pipeline for {sample_name}",
        "goal": params.get("goal", f"Analyze {sample_name}"),
        "workflow_type": "local_sample_intake",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "sample_name": sample_name,
        "steps": steps,
        "artifacts": [],
    }


# ---- Template: compare_samples -------------------------------------------

def _template_compare_samples(params: dict) -> dict:
    """
    Deterministic plan for comparing two samples.
    Steps: locate(x2) → parse(x2) → compare → generate plot → write summary
    """
    samples = params.get("samples", ["sample A", "sample B"])
    if len(samples) < 2:
        samples = ["sample A", "sample B"]
    s_a, s_b = samples[0], samples[1]

    steps = []
    idx = 0

    s_loc_a = _make_step("LOCATE_DATA", f"Locate data for {s_a}", idx)
    steps.append(s_loc_a)
    idx += 1

    s_loc_b = _make_step("LOCATE_DATA", f"Locate data for {s_b}", idx)
    steps.append(s_loc_b)
    idx += 1

    s_parse_a = _make_step("PARSE_OUTPUT_FILE", f"Parse QC and results for {s_a}", idx,
                           depends_on=[s_loc_a["id"]])
    steps.append(s_parse_a)
    idx += 1

    s_parse_b = _make_step("PARSE_OUTPUT_FILE", f"Parse QC and results for {s_b}", idx,
                           depends_on=[s_loc_b["id"]])
    steps.append(s_parse_b)
    idx += 1

    s_compare = _make_step("COMPARE_SAMPLES", f"Compare {s_a} vs {s_b}", idx,
                           depends_on=[s_parse_a["id"], s_parse_b["id"]])
    steps.append(s_compare)
    idx += 1

    s_plot = _make_step("GENERATE_PLOT", f"Generate comparison plots", idx,
                        depends_on=[s_compare["id"]])
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step("INTERPRET_RESULTS", f"Interpret comparison of {s_a} vs {s_b}", idx,
                             depends_on=[s_compare["id"]])
    steps.append(s_interpret)
    idx += 1

    s_summary = _make_step("WRITE_SUMMARY", f"Summarize comparison findings", idx,
                           depends_on=[s_plot["id"], s_interpret["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "compare_samples",
        "title": f"Compare {s_a} and {s_b}",
        "goal": params.get("goal", f"Compare {s_a} and {s_b}"),
        "workflow_type": "compare",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


# ---- Template: download_analyze ------------------------------------------

def _template_download_analyze(params: dict) -> dict:
    """
    Deterministic plan for downloading ENCODE data and running analysis.
    Steps: search → CHECK_EXISTING → download → submit → monitor → summarize
    """
    search_term = params.get("search_term", "data")
    sample_name = params.get("sample_name", search_term)

    steps = []
    idx = 0

    s_search = _make_step("SEARCH_ENCODE", f"Search ENCODE for {search_term}", idx,
                          tool_calls=[{"source_key": "encode", "tool": "search_by_biosample",
                                       "params": {"search_term": search_term}}])
    steps.append(s_search)
    idx += 1

    s_check = _make_step("CHECK_EXISTING", f"Check for previously downloaded {search_term}", idx,
                         depends_on=[s_search["id"]],
                         tool_calls=[{"source_key": "analyzer", "tool": "find_file",
                                      "params": {"file_name": search_term}}])
    steps.append(s_check)
    idx += 1

    s_download = _make_step("DOWNLOAD_DATA", f"Download files for {search_term}", idx,
                            requires_approval=True, depends_on=[s_check["id"]])
    steps.append(s_download)
    idx += 1

    s_submit = _make_step("SUBMIT_WORKFLOW", f"Submit Dogme pipeline for {sample_name}", idx,
                          requires_approval=True, depends_on=[s_download["id"]])
    steps.append(s_submit)
    idx += 1

    s_monitor = _make_step("MONITOR_WORKFLOW", f"Monitor pipeline progress", idx,
                           depends_on=[s_submit["id"]])
    steps.append(s_monitor)
    idx += 1

    s_summary = _make_step("SUMMARIZE_QC", f"Summarize QC and results", idx,
                           depends_on=[s_monitor["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "download_analyze",
        "title": f"Download and analyze {search_term}",
        "goal": params.get("goal", f"Download {search_term} from ENCODE and analyze"),
        "workflow_type": "encode_download",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


# ---- Template: summarize_results -----------------------------------------

def _template_summarize_results(params: dict) -> dict:
    """
    Deterministic plan for summarizing existing job results.
    Steps: locate → parse → summarize QC → generate plot → write summary
    """
    sample_name = params.get("sample_name", "sample")
    work_dir = params.get("work_dir", "")

    steps = []
    idx = 0

    s_locate = _make_step("LOCATE_DATA", f"Locate output files for {sample_name}", idx,
                          tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                       "params": {"work_dir": work_dir}}])
    steps.append(s_locate)
    idx += 1

    s_parse = _make_step("PARSE_OUTPUT_FILE", f"Parse key output files", idx,
                         depends_on=[s_locate["id"]])
    steps.append(s_parse)
    idx += 1

    s_qc = _make_step("SUMMARIZE_QC", f"Summarize QC metrics for {sample_name}", idx,
                      depends_on=[s_parse["id"]],
                      tool_calls=[{"source_key": "analyzer", "tool": "get_analysis_summary",
                                   "params": {"work_dir": work_dir}}])
    steps.append(s_qc)
    idx += 1

    s_plot = _make_step("GENERATE_PLOT", f"Generate summary plots", idx,
                        depends_on=[s_qc["id"]])
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step("INTERPRET_RESULTS", f"Interpret results for {sample_name}", idx,
                             depends_on=[s_qc["id"]])
    steps.append(s_interpret)
    idx += 1

    s_summary = _make_step("WRITE_SUMMARY", f"Write analysis summary", idx,
                           depends_on=[s_plot["id"], s_interpret["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "summarize_results",
        "title": f"Summarize results for {sample_name}",
        "goal": params.get("goal", f"Summarize {sample_name} results"),
        "workflow_type": "summarize",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


# ---------------------------------------------------------------------------
# Plan-type detection
# ---------------------------------------------------------------------------

_DE_PATTERNS = [
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:differential\s+expression|DE\s+analysis)", re.I),
    re.compile(r"(?:differential\s+expression|DE)\s+(?:on|for|analysis)", re.I),
    re.compile(r"edger?\s+(?:analysis|pipeline)", re.I),
]

_SEARCH_COMPARE_LOCAL_PATTERNS = [
    re.compile(r"(?:download|get|fetch)\s+.*(?:encode|public).*(?:compare|vs)", re.I),
    re.compile(r"compare\s+(?:my\s+)?(?:local|sample).*(?:to|with|against)\s+.*(?:encode|public)", re.I),
    re.compile(r"(?:encode|public)\s+.*compare\s+.*(?:my|local)", re.I),
]

_COMPARE_WORKFLOW_PATTERNS = [
    re.compile(r"compare\s+(?:these|the|two|my|both|all)?\s*(?:workflows?|jobs?|runs?|pipelines?)", re.I),
    re.compile(r"(?:differences?|diff)\s+between\s+(?:the\s+)?(?:workflows?|jobs?|runs?)", re.I),
]

_COMPARE_PATTERNS = [
    re.compile(r"compare\s+(?:these|the|two|my|both|all)?\s*(?:samples?|results?|workflows?)", re.I),
    re.compile(r"(?:differences?|diff)\s+between", re.I),
    re.compile(r"\bvs?\.?\b.*\bvs?\.?\b", re.I),
    re.compile(r"(?:treated|control)\s+(?:vs?\.?|versus)\s+", re.I),
]

_DOWNLOAD_ANALYZE_PATTERNS = [
    re.compile(r"download.*(?:and|then)\s+(?:run|analyze|process)", re.I),
    re.compile(r"get.*from\s+encode.*(?:and|then)", re.I),
    re.compile(r"fetch.*(?:and|then)\s+(?:run|analyze|process)", re.I),
]

_PARSE_PLOT_PATTERNS = [
    re.compile(r"(?:plot|graph|chart|visuali[sz]e).*(?:from|of|for)\s+(?:my|the|last|this)\s+(?:run|results?|workflow)", re.I),
    re.compile(r"(?:parse|read).*(?:and|then)\s+(?:plot|graph|chart|visuali[sz]e)", re.I),
    re.compile(r"(?:show|display)\s+(?:the\s+)?(?:qc|quality|metrics?).*(?:plot|graph|chart)", re.I),
]

_RUN_WORKFLOW_PATTERNS = [
    re.compile(r"run\s+(?:the\s+)?(?:full\s+)?pipeline\s+(?:on|for)", re.I),
    re.compile(r"process\s+(?:my\s+)?(?:local\s+)?(?:sample|data|pod5|fastq|bam)", re.I),
    re.compile(r"analyze\s+(?:my\s+)?(?:local\s+)?(?:sample|data)", re.I),
]


def _detect_plan_type(message: str) -> str | None:
    """Return plan type string or None.

    Priority ordering: most specific patterns first.
    """
    # 1. DE analysis (most specific)
    for pat in _DE_PATTERNS:
        if pat.search(message):
            return "run_de_pipeline"
    # 2. Search + compare to local
    for pat in _SEARCH_COMPARE_LOCAL_PATTERNS:
        if pat.search(message):
            return "search_compare_to_local"
    # 3. Compare workflows (before generic compare)
    for pat in _COMPARE_WORKFLOW_PATTERNS:
        if pat.search(message):
            return "compare_workflows"
    # 4. Compare samples
    for pat in _COMPARE_PATTERNS:
        if pat.search(message):
            return "compare_samples"
    # 5. Download + analyze
    for pat in _DOWNLOAD_ANALYZE_PATTERNS:
        if pat.search(message):
            return "download_analyze"
    # 6. Parse + plot + interpret
    for pat in _PARSE_PLOT_PATTERNS:
        if pat.search(message):
            return "parse_plot_interpret"
    # 7. Run workflow
    for pat in _RUN_WORKFLOW_PATTERNS:
        if pat.search(message):
            return "run_workflow"
    return None


def _extract_plan_params(message: str, conv_state: "ConversationState", plan_type: str) -> dict:
    """Extract relevant parameters from the user message and conversation state."""
    params: dict = {"goal": message}

    if plan_type == "compare_samples":
        # Try to extract sample names from the message
        sample_matches = re.findall(r"(\b\w+(?:_\w+)*)\s+(?:and|vs?\.?|versus)\s+(\b\w+(?:_\w+)*)", message, re.I)
        if sample_matches:
            params["samples"] = list(sample_matches[0])
        elif conv_state.workflows and len(conv_state.workflows) >= 2:
            params["samples"] = [
                conv_state.workflows[-2].get("sample_name", "sample A"),
                conv_state.workflows[-1].get("sample_name", "sample B"),
            ]
        return params

    if plan_type == "compare_workflows":
        # Extract workflow names/indices from message or state
        sample_matches = re.findall(r"(\b\w+(?:_\w+)*)\s+(?:and|vs?\.?|versus)\s+(\b\w+(?:_\w+)*)", message, re.I)
        if sample_matches:
            params["workflows"] = list(sample_matches[0])
        elif conv_state.workflows and len(conv_state.workflows) >= 2:
            params["workflows"] = [
                conv_state.workflows[-2].get("sample_name", "workflow A"),
                conv_state.workflows[-1].get("sample_name", "workflow B"),
            ]
            # Also carry work_dirs
            params["work_dir_a"] = conv_state.workflows[-2].get("work_dir", "")
            params["work_dir_b"] = conv_state.workflows[-1].get("work_dir", "")
        return params

    if plan_type == "download_analyze":
        # Extract search term from message
        m = re.search(r"(?:download|get|fetch)\s+(.+?)(?:\s+(?:from|and|then))", message, re.I)
        if m:
            params["search_term"] = m.group(1).strip()
        return params

    if plan_type == "search_compare_to_local":
        # Extract search term and local sample from message
        m = re.search(r"(?:download|get|fetch)\s+(.+?)(?:\s+(?:from|and|then|compare))", message, re.I)
        if m:
            params["search_term"] = m.group(1).strip()
        # Local sample from state
        if conv_state.sample_name:
            params["local_sample"] = conv_state.sample_name
        if conv_state.work_dir:
            params["local_work_dir"] = conv_state.work_dir
        return params

    if plan_type == "run_de_pipeline":
        # Extract counts path and sample info path from message
        m = re.search(r"counts?\s+(?:at|in|from|path)?\s*[=:]?\s*(\S+\.(?:csv|tsv|txt))", message, re.I)
        if m:
            params["counts_path"] = m.group(1)
        m = re.search(r"(?:sample[_ ]?info|metadata|design)\s+(?:at|in|from|path)?\s*[=:]?\s*(\S+\.(?:csv|tsv|txt))", message, re.I)
        if m:
            params["sample_info_path"] = m.group(1)
        m = re.search(r"(?:group|condition)\s+(?:column)?\s*[=:]?\s*(\w+)", message, re.I)
        if m:
            params["group_column"] = m.group(1)
        m = re.search(r"(\w+)\s+(?:vs?\.?|versus|compared?\s+to)\s+(\w+)", message, re.I)
        if m:
            params["contrast"] = f"{m.group(1)} - {m.group(2)}"
        return params

    if plan_type == "parse_plot_interpret":
        params["plot_type"] = _select_plot_type(message)
        # Fall through to pick up sample_name / work_dir below

    # run_workflow / summarize_results / parse_plot_interpret
    if conv_state.sample_name:
        params["sample_name"] = conv_state.sample_name
    if conv_state.work_dir:
        params["work_dir"] = conv_state.work_dir

    return params


# ---------------------------------------------------------------------------
# Plot selection heuristic
# ---------------------------------------------------------------------------

def _select_plot_type(message: str) -> str:
    """Keyword-based chart type selection from the user's request."""
    msg = message.lower()
    if any(w in msg for w in ("volcano", "de plot", "differential expression plot")):
        return "volcano"
    if any(w in msg for w in ("heatmap", "heat map", "cluster")):
        return "heatmap"
    if any(w in msg for w in ("box", "boxplot", "box plot", "distribution")):
        return "box"
    if any(w in msg for w in ("pie", "proportion", "fraction", "percentage")):
        return "pie"
    if any(w in msg for w in ("scatter", "correlation", "xy")):
        return "scatter"
    if any(w in msg for w in ("histogram", "hist", "frequency")):
        return "histogram"
    # Default: bar chart for categorical / count data
    return "bar"


# ---- Template: run_de_pipeline -------------------------------------------

def _template_run_de_pipeline(params: dict) -> dict:
    """
    Deterministic plan for full differential expression analysis.
    Steps: CHECK_EXISTING → RUN_DE_PIPELINE → GENERATE_DE_PLOT → INTERPRET_RESULTS → WRITE_SUMMARY
    """
    counts_path = params.get("counts_path", "counts.csv")
    sample_info = params.get("sample_info_path", "sample_info.csv")
    group_col = params.get("group_column", "condition")
    contrast = params.get("contrast", "treated - control")

    steps = []
    idx = 0

    s_check = _make_step("CHECK_EXISTING", "Check for existing DE results", idx,
                         tool_calls=[{"source_key": "analyzer", "tool": "find_file",
                                      "params": {"file_name": "de_results"}}])
    steps.append(s_check)
    idx += 1

    s_de = _make_step("RUN_DE_PIPELINE", f"Run DE analysis ({contrast})", idx,
                      requires_approval=True, depends_on=[s_check["id"]],
                      tool_calls=[
                          {"source_key": "edgepython", "tool": "load_data",
                           "params": {"counts_path": counts_path, "sample_info_path": sample_info,
                                      "group_column": group_col}},
                          {"source_key": "edgepython", "tool": "filter_genes",
                           "params": {"min_count": 10, "min_total_count": 15}},
                          {"source_key": "edgepython", "tool": "normalize",
                           "params": {"method": "TMM"}},
                          {"source_key": "edgepython", "tool": "set_design",
                           "params": {"formula": "~ 0 + group"}},
                          {"source_key": "edgepython", "tool": "estimate_dispersion",
                           "params": {"robust": True}},
                          {"source_key": "edgepython", "tool": "fit_model",
                           "params": {"robust": True}},
                          {"source_key": "edgepython", "tool": "test_contrast",
                           "params": {"contrast": contrast}},
                      ])
    steps.append(s_de)
    idx += 1

    s_plot = _make_step("GENERATE_DE_PLOT", "Generate volcano plot", idx,
                        depends_on=[s_de["id"]],
                        tool_calls=[{"source_key": "edgepython", "tool": "generate_plot",
                                     "params": {"plot_type": "volcano"}}])
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step("INTERPRET_RESULTS", "Interpret DE results", idx,
                             depends_on=[s_de["id"]])
    steps.append(s_interpret)
    idx += 1

    s_summary = _make_step("WRITE_SUMMARY", "Write DE analysis summary", idx,
                           depends_on=[s_plot["id"], s_interpret["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "run_de_pipeline",
        "title": f"Differential expression: {contrast}",
        "goal": params.get("goal", f"Run DE analysis: {contrast}"),
        "workflow_type": "de_analysis",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


# ---- Template: parse_plot_interpret --------------------------------------

def _template_parse_plot_interpret(params: dict) -> dict:
    """
    Deterministic plan for parsing data, plotting, and explaining.
    Steps: LOCATE_DATA → PARSE_OUTPUT_FILE → GENERATE_PLOT → INTERPRET_RESULTS
    """
    sample_name = params.get("sample_name", "sample")
    work_dir = params.get("work_dir", "")
    plot_type = params.get("plot_type", "bar")

    steps = []
    idx = 0

    s_locate = _make_step("LOCATE_DATA", f"Locate output files for {sample_name}", idx,
                          tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                       "params": {"work_dir": work_dir}}])
    steps.append(s_locate)
    idx += 1

    s_parse = _make_step("PARSE_OUTPUT_FILE", "Parse key result files", idx,
                         depends_on=[s_locate["id"]])
    steps.append(s_parse)
    idx += 1

    s_plot = _make_step("GENERATE_PLOT", f"Generate {plot_type} chart", idx,
                        depends_on=[s_parse["id"]])
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step("INTERPRET_RESULTS", "Explain what the results mean", idx,
                             depends_on=[s_plot["id"]])
    steps.append(s_interpret)

    return {
        "plan_type": "parse_plot_interpret",
        "title": f"Plot and interpret results for {sample_name}",
        "goal": params.get("goal", f"Plot and interpret {sample_name} results"),
        "workflow_type": "visualize",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "plot_type": plot_type,
        "steps": steps,
        "artifacts": [],
    }


# ---- Template: compare_workflows ----------------------------------------

def _template_compare_workflows(params: dict) -> dict:
    """
    Deterministic plan for comparing two workflow/job outputs.
    Steps: locate(×2) → parse(×2) → compare → plot → INTERPRET_RESULTS → summary
    """
    workflows = params.get("workflows", ["workflow A", "workflow B"])
    w_a, w_b = workflows[0], workflows[1] if len(workflows) >= 2 else "workflow B"
    work_dir_a = params.get("work_dir_a", "")
    work_dir_b = params.get("work_dir_b", "")

    steps = []
    idx = 0

    s_loc_a = _make_step("LOCATE_DATA", f"Locate output for {w_a}", idx,
                         tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                      "params": {"work_dir": work_dir_a}}] if work_dir_a else [])
    steps.append(s_loc_a)
    idx += 1

    s_loc_b = _make_step("LOCATE_DATA", f"Locate output for {w_b}", idx,
                         tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                      "params": {"work_dir": work_dir_b}}] if work_dir_b else [])
    steps.append(s_loc_b)
    idx += 1

    s_parse_a = _make_step("PARSE_OUTPUT_FILE", f"Parse results for {w_a}", idx,
                           depends_on=[s_loc_a["id"]])
    steps.append(s_parse_a)
    idx += 1

    s_parse_b = _make_step("PARSE_OUTPUT_FILE", f"Parse results for {w_b}", idx,
                           depends_on=[s_loc_b["id"]])
    steps.append(s_parse_b)
    idx += 1

    s_compare = _make_step("COMPARE_SAMPLES", f"Compare {w_a} vs {w_b}", idx,
                           depends_on=[s_parse_a["id"], s_parse_b["id"]])
    steps.append(s_compare)
    idx += 1

    s_plot = _make_step("GENERATE_PLOT", "Generate comparison plots", idx,
                        depends_on=[s_compare["id"]])
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step("INTERPRET_RESULTS", "Interpret comparison findings", idx,
                             depends_on=[s_compare["id"]])
    steps.append(s_interpret)
    idx += 1

    s_summary = _make_step("WRITE_SUMMARY", "Summarize workflow comparison", idx,
                           depends_on=[s_plot["id"], s_interpret["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "compare_workflows",
        "title": f"Compare {w_a} and {w_b}",
        "goal": params.get("goal", f"Compare workflows {w_a} and {w_b}"),
        "workflow_type": "compare",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


# ---- Template: search_compare_to_local ----------------------------------

def _template_search_compare_to_local(params: dict) -> dict:
    """
    Deterministic plan for searching ENCODE, downloading, comparing to local data.
    Steps: search → CHECK_EXISTING → download → locate local → parse(×2) → compare → plot → summary
    """
    search_term = params.get("search_term", "data")
    local_sample = params.get("local_sample", "local sample")
    local_work_dir = params.get("local_work_dir", "")

    steps = []
    idx = 0

    s_search = _make_step("SEARCH_ENCODE", f"Search ENCODE for {search_term}", idx,
                          tool_calls=[{"source_key": "encode", "tool": "search_by_biosample",
                                       "params": {"search_term": search_term}}])
    steps.append(s_search)
    idx += 1

    s_check = _make_step("CHECK_EXISTING", "Check for previously downloaded data", idx,
                         depends_on=[s_search["id"]],
                         tool_calls=[{"source_key": "analyzer", "tool": "find_file",
                                      "params": {"file_name": search_term}}])
    steps.append(s_check)
    idx += 1

    s_download = _make_step("DOWNLOAD_DATA", f"Download {search_term} from ENCODE", idx,
                            requires_approval=True, depends_on=[s_check["id"]])
    steps.append(s_download)
    idx += 1

    s_locate_local = _make_step("LOCATE_DATA", f"Locate local data for {local_sample}", idx,
                                tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                             "params": {"work_dir": local_work_dir}}] if local_work_dir else [])
    steps.append(s_locate_local)
    idx += 1

    s_parse_remote = _make_step("PARSE_OUTPUT_FILE", f"Parse downloaded {search_term} data", idx,
                                depends_on=[s_download["id"]])
    steps.append(s_parse_remote)
    idx += 1

    s_parse_local = _make_step("PARSE_OUTPUT_FILE", f"Parse local {local_sample} data", idx,
                               depends_on=[s_locate_local["id"]])
    steps.append(s_parse_local)
    idx += 1

    s_compare = _make_step("COMPARE_SAMPLES", f"Compare {local_sample} vs {search_term}", idx,
                           depends_on=[s_parse_remote["id"], s_parse_local["id"]])
    steps.append(s_compare)
    idx += 1

    s_plot = _make_step("GENERATE_PLOT", "Generate comparison plots", idx,
                        depends_on=[s_compare["id"]])
    steps.append(s_plot)
    idx += 1

    s_summary = _make_step("WRITE_SUMMARY", f"Summarize {local_sample} vs {search_term}", idx,
                           depends_on=[s_compare["id"], s_plot["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "search_compare_to_local",
        "title": f"Compare {local_sample} to ENCODE {search_term}",
        "goal": params.get("goal", f"Compare local {local_sample} to ENCODE {search_term}"),
        "workflow_type": "compare",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


# ---------------------------------------------------------------------------
# LLM-based plan parsing  (fallback for when no template matches)
# ---------------------------------------------------------------------------

def _parse_llm_plan(raw_response: str) -> dict | None:
    """Parse a [[PLAN:{...}]] JSON tag from LLM output."""
    m = re.search(r'\[\[PLAN:\s*(\{.*?\})\s*\]\]', raw_response, re.DOTALL)
    if not m:
        return None
    try:
        plan = json.loads(m.group(1))
        if isinstance(plan, dict) and "steps" in plan:
            return plan
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse LLM plan JSON", raw=m.group(1)[:200])
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_plan(
    message: str,
    active_skill: str,
    conv_state: "ConversationState",
    engine: "AgentEngine",
    conversation_history: list | None = None,
) -> dict | None:
    """
    Generate a structured plan payload for a MULTI_STEP request.

    1. Try deterministic template matching first.
    2. Fall back to LLM plan() call if no template matches.
    3. Return None if planning fails (caller falls back to existing flow).
    """
    plan_type = _detect_plan_type(message)
    params = _extract_plan_params(message, conv_state, plan_type or "")

    # 1. Deterministic templates
    if plan_type == "run_de_pipeline":
        plan = _template_run_de_pipeline(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return plan
    if plan_type == "search_compare_to_local":
        plan = _template_search_compare_to_local(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return plan
    if plan_type == "compare_workflows":
        plan = _template_compare_workflows(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return plan
    if plan_type == "run_workflow":
        plan = _template_run_workflow(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return plan
    if plan_type == "compare_samples":
        plan = _template_compare_samples(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return plan
    if plan_type == "download_analyze":
        plan = _template_download_analyze(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return plan
    if plan_type == "parse_plot_interpret":
        plan = _template_parse_plot_interpret(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return plan

    # Summarize results: needs work_dir from state
    if conv_state.work_dir and re.search(
        r"(?:summarize|interpret|explain)\s+(?:the\s+)?(?:results?|output|qc)", message, re.I
    ):
        plan = _template_summarize_results(params)
        logger.info("Generated plan from template", plan_type="summarize_results", steps=len(plan["steps"]))
        return plan

    # 2. LLM fallback — ask the engine to produce a structured plan
    try:
        state_json = conv_state.to_json()
        raw, _usage = engine.plan(message, state_json, conversation_history)
        if raw:
            llm_plan = _parse_llm_plan(raw)
            if llm_plan:
                # Ensure required fields
                llm_plan.setdefault("plan_type", "custom")
                llm_plan.setdefault("title", message[:80])
                llm_plan.setdefault("goal", message)
                llm_plan.setdefault("status", "PENDING")
                llm_plan.setdefault("auto_execute_safe_steps", True)
                llm_plan.setdefault("artifacts", [])
                # Assign IDs and order to steps if missing
                for i, step in enumerate(llm_plan.get("steps", [])):
                    step.setdefault("id", _step_id())
                    step.setdefault("status", "PENDING")
                    step.setdefault("order_index", i)
                    step.setdefault("tool_calls", [])
                    step.setdefault("requires_approval", False)
                    step.setdefault("depends_on", [])
                    step.setdefault("result", None)
                    step.setdefault("error", None)
                    step.setdefault("started_at", None)
                    step.setdefault("completed_at", None)
                if llm_plan.get("steps"):
                    llm_plan.setdefault("current_step_id", llm_plan["steps"][0]["id"])
                logger.info("Generated plan from LLM", steps=len(llm_plan.get("steps", [])))
                return llm_plan
    except Exception as e:
        logger.warning("LLM plan generation failed", error=str(e))

    # 3. No plan could be generated — return None (caller uses existing flow)
    logger.info("No plan generated, falling back to single-turn flow")
    return None


# ---------------------------------------------------------------------------
# Plan markdown rendering (user-visible Level A view)
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "PENDING": "\u2b1c",
    "RUNNING": "\U0001f504",
    "COMPLETED": "\u2705",
    "FAILED": "\u274c",
    "SKIPPED": "\u23ed\ufe0f",
    "WAITING_APPROVAL": "\U0001f512",
    "CANCELLED": "\u23f9\ufe0f",
}


def render_plan_markdown(payload: dict) -> str:
    """Convert a plan payload into user-visible markdown."""
    title = payload.get("title", "Execution Plan")
    goal = payload.get("goal", "")
    lines = [f"## Plan: {title}\n"]
    if goal and goal != title:
        lines.append(f"> {goal}\n")

    for i, step in enumerate(payload.get("steps", []), 1):
        status = step.get("status", "PENDING")
        icon = _STATUS_ICONS.get(status, "\u2b1c")
        approval = " *(requires approval)*" if step.get("requires_approval") else ""
        lines.append(f"{i}. {icon} {step.get('title', 'Step')}{approval}")

    return "\n".join(lines)
