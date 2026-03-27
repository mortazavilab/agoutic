"""
Plan generation: classifies user requests and produces structured WORKFLOW_PLAN payloads.

Three request classes:
  - INFORMATIONAL: Simple question, no plan needed (existing flow)
  - SINGLE_TOOL: One tool call, no plan needed (existing flow)
  - MULTI_STEP: Requires a structured plan (new flow)

Nine plan templates:
  - run_workflow: Analyze a local sample (stage → run → analyze)
    - remote_stage_workflow: Stage a sample on a remote SLURM target for later reuse
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
import os
import re
import uuid
from typing import TYPE_CHECKING, Any

from common.logging_config import get_logger
from cortex.plan_validation import PlanValidationError, validate_plan

if TYPE_CHECKING:
    from cortex.agent_engine import AgentEngine
    from cortex.schemas import ConversationState

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Request classification
# ---------------------------------------------------------------------------

# Patterns that signal a MULTI_STEP request
_MULTI_STEP_PATTERNS: list[re.Pattern] = [
    # Reconcile annotated BAMs
    re.compile(r"(?:reconcile|merge|combine)\s+(?:the\s+)?(?:annotated\s+)?bams?", re.I),
    re.compile(r"cross[-\s]?workflow\s+bam\s+reconcil", re.I),
    # Remote stage-only flows
    re.compile(r"stage\s+.+\b(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)[a-zA-Z0-9_-]+(?:\s+profile)?(?:[?.!,]|$)|on\s+slurm|using\s+slurm|remotely|on\s+the\s+cluster|(?:using|via)\s+(?:the\s+)?[a-zA-Z0-9_-]+\s+profile)", re.I),
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
    re.compile(r"(?:summarize|interpret|explain)\s+(?:the\s+)?(?:results?|output|qc)", re.I),
    # Search + compare to local
    re.compile(r"(?:download|get|fetch)\s+.*encode.*(?:compare|vs)", re.I),
    re.compile(r"compare\s+(?:my\s+)?(?:local|sample).*(?:to|with|against)\s+.*(?:encode|public)", re.I),
    # Enrichment analysis
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:GO|gene\s+ontology|enrichment|pathway)\s+(?:analysis|enrichment)", re.I),
    re.compile(r"(?:KEGG|Reactome)\s+(?:enrichment|analysis|pathway)", re.I),
    re.compile(r"(?:what|which)\s+(?:GO\s+terms?|pathways?|biological\s+processes?)\s+(?:are\s+)?enriched", re.I),
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
    Also checks skill-defined plan chains for multi-step detection.
    Defaults to SINGLE_TOOL when uncertain (preserving existing behaviour).
    """
    msg = message.strip()

    # 1. Check for multi-step signals first (highest specificity)
    for pat in _MULTI_STEP_PATTERNS:
        if pat.search(msg):
            logger.info("classify_request: MULTI_STEP", pattern=pat.pattern[:60])
            return "MULTI_STEP"

    # 1b. Check skill-defined plan chains
    from cortex.plan_chains import load_chains_for_skill, match_chain
    chains = load_chains_for_skill(active_skill)
    if chains and match_chain(msg, chains):
        logger.info("classify_request: CHAIN_MULTI_STEP", skill=active_skill)
        return "CHAIN_MULTI_STEP"

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


def _plan_instance_id() -> str:
    return uuid.uuid4().hex


_PLANNER_SAFE_STEP_KINDS = frozenset({
    "LOCATE_DATA",
    "VALIDATE_INPUTS",
    "PARSE_OUTPUT_FILE",
    "SUMMARIZE_QC",
    "GENERATE_PLOT",
    "WRITE_SUMMARY",
    "CHECK_EXISTING",
    "GENERATE_DE_PLOT",
    "INTERPRET_RESULTS",
    "RECOMMEND_NEXT",
    "ANNOTATE_RESULTS",
    "FILTER_DE_GENES",
    "RUN_GO_ENRICHMENT",
    "RUN_PATHWAY_ENRICHMENT",
    "PLOT_ENRICHMENT",
    "SUMMARIZE_ENRICHMENT",
})


_PLANNER_APPROVAL_STEP_KINDS = frozenset({
    "SUBMIT_WORKFLOW",
    "DOWNLOAD_DATA",
    "RUN_DE_ANALYSIS",
    "RUN_DE_PIPELINE",
    "RUN_SCRIPT",
    "REQUEST_APPROVAL",
})


_PLANNER_ALLOWED_STEP_KINDS = frozenset({
    "LOCATE_DATA",
    "SEARCH_ENCODE",
    "DOWNLOAD_DATA",
    "SUBMIT_WORKFLOW",
    "MONITOR_WORKFLOW",
    "PARSE_OUTPUT_FILE",
    "SUMMARIZE_QC",
    "RUN_DE_ANALYSIS",
    "RUN_SCRIPT",
    "COMPARE_SAMPLES",
    "GENERATE_PLOT",
    "WRITE_SUMMARY",
    "REQUEST_APPROVAL",
    "CHECK_EXISTING",
    "RUN_DE_PIPELINE",
    "GENERATE_DE_PLOT",
    "INTERPRET_RESULTS",
    "RECOMMEND_NEXT",
    "ANNOTATE_RESULTS",
    "FILTER_DE_GENES",
    "RUN_GO_ENRICHMENT",
    "RUN_PATHWAY_ENRICHMENT",
    "PLOT_ENRICHMENT",
    "SUMMARIZE_ENRICHMENT",
    "CHECK_REMOTE_PROFILE_AUTH",
    "check_remote_stage",
    "CHECK_REMOTE_STAGE",
    "remote_stage",
    "REMOTE_STAGE",
    "complete_stage_only",
    "COMPLETE_STAGE_ONLY",
    "FIND_REFERENCE_CACHE",
    "FIND_DATA_CACHE",
    "copy_sample",
    "run",
    "analysis",
})


def compose_plan_fragments(
    fragments: list[dict[str, Any]],
    *,
    title: str,
    goal: str,
    plan_type: str = "custom",
    project_id: str | None = None,
) -> dict[str, Any]:
    """Compose plan fragments into a single plan with deterministic id remapping."""
    if not isinstance(fragments, list) or not fragments:
        raise ValueError("fragments must be a non-empty list")

    composed_entries: list[tuple[dict[str, Any], str, dict[str, str]]] = []
    all_ids: set[str] = set()
    used_aliases: set[str] = set()

    for frag_idx, fragment in enumerate(fragments, start=1):
        if not isinstance(fragment, dict):
            raise ValueError(f"Fragment at index {frag_idx - 1} must be an object")

        raw_alias = str(fragment.get("fragment_alias") or fragment.get("fragment_id") or f"frag{frag_idx}")
        alias = _normalize_fragment_alias(raw_alias, fallback=f"frag{frag_idx}")
        if alias in used_aliases:
            alias = f"{alias}_{frag_idx}"
        used_aliases.add(alias)

        fragment_steps = fragment.get("steps")
        if not isinstance(fragment_steps, list) or not fragment_steps:
            raise ValueError(f"Fragment '{alias}' must contain a non-empty steps list")

        local_id_map: dict[str, str] = {}
        for local_idx, step in enumerate(fragment_steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Fragment '{alias}' step {local_idx - 1} must be an object")

            local_step_id = str(step.get("id") or f"step{local_idx}")
            canonical_step_id = f"{alias}__{local_step_id}"
            if canonical_step_id in all_ids:
                raise ValueError(f"Duplicate canonical step id '{canonical_step_id}'")
            all_ids.add(canonical_step_id)
            local_id_map[local_step_id] = canonical_step_id

            composed_step = dict(step)
            composed_step["id"] = canonical_step_id
            composed_step.setdefault("title", local_step_id)
            composed_step.setdefault("status", "PENDING")
            composed_step.setdefault("tool_calls", [])
            composed_step.setdefault("requires_approval", False)
            composed_step.setdefault("depends_on", [])
            composed_step.setdefault("result", None)
            composed_step.setdefault("error", None)
            composed_step.setdefault("started_at", None)
            composed_step.setdefault("completed_at", None)

            provenance = composed_step.get("provenance")
            if provenance is None or not isinstance(provenance, dict):
                provenance = {}

            provenance.setdefault("fragment_id", str(fragment.get("fragment_id") or alias))
            if fragment.get("fragment_version") is not None:
                provenance.setdefault("fragment_version", str(fragment.get("fragment_version")))
            if fragment.get("source_template") is not None:
                provenance.setdefault("source_template", str(fragment.get("source_template")))
            composed_step["provenance"] = provenance

            composed_entries.append((composed_step, alias, local_id_map))

    for step, alias, local_map in composed_entries:
        depends_on = step.get("depends_on")
        if depends_on is None:
            depends_on = []
        if not isinstance(depends_on, list):
            raise ValueError(f"Step '{step.get('id')}' has invalid depends_on type")

        rewritten: list[str] = []
        for dep in depends_on:
            if not isinstance(dep, str) or not dep.strip():
                raise ValueError(f"Step '{step.get('id')}' has invalid dependency id")
            if dep in local_map:
                rewritten.append(local_map[dep])
            elif "__" in dep:
                rewritten.append(dep)
            else:
                raise ValueError(
                    f"Step '{step.get('id')}' in fragment '{alias}' references non-canonical cross-fragment dependency '{dep}'"
                )
        step["depends_on"] = rewritten

    for step, _alias, _map in composed_entries:
        for dep in step.get("depends_on", []):
            if dep not in all_ids:
                raise ValueError(f"Step '{step.get('id')}' depends on unknown step id '{dep}'")

    steps = [entry[0] for entry in composed_entries]
    for index, step in enumerate(steps):
        step["order_index"] = index

    return {
        "plan_type": plan_type,
        "title": title,
        "goal": goal,
        "project_id": project_id,
        "plan_instance_id": _plan_instance_id(),
        "status": "PENDING",
        "auto_execute_safe_steps": True,
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


def _normalize_fragment_alias(raw_alias: str, *, fallback: str) -> str:
    alias = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_alias).strip("_")
    return alias or fallback


def _finalize_plan(plan: dict[str, Any], conv_state: "ConversationState") -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None

    plan.setdefault("project_id", conv_state.active_project)
    plan.setdefault("plan_instance_id", _plan_instance_id())
    plan.setdefault(
        "planner_metadata",
        {
            "planning_mode": "deterministic",
            "hybrid_attempted": False,
            "hybrid_succeeded": False,
            "hybrid_failed": False,
            "deterministic_fallback_used": False,
            "reason_code": "",
        },
    )

    try:
        validate_plan(
            plan,
            allowed_kinds=_PLANNER_ALLOWED_STEP_KINDS,
            safe_step_kinds=_PLANNER_SAFE_STEP_KINDS,
            approval_step_kinds=_PLANNER_APPROVAL_STEP_KINDS,
            expected_project_id=conv_state.active_project,
        )
    except PlanValidationError as exc:
        logger.warning("Generated plan failed validation", issues=exc.to_dict()["issues"])
        return None
    return plan


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

def _template_remote_stage_workflow(params: dict) -> dict:
    """
    Deterministic plan for staging a sample on a remote SLURM target.
    Steps: locate → validate → check remote stage → check profile auth → approval → stage → complete
    """
    sample_name = params.get("sample_name", "sample")
    work_dir = params.get("work_dir", "")
    input_dir = params.get("input_directory", "")

    steps = []
    idx = 0

    s_locate = _make_step(
        "LOCATE_DATA",
        f"Locate data for {sample_name}",
        idx,
        tool_calls=[{"source_key": "analyzer", "tool": "list_job_files", "params": {"work_dir": input_dir or work_dir}}],
    )
    s_locate["id"] = "locate_data"
    steps.append(s_locate)
    idx += 1

    s_validate = _make_step("VALIDATE_INPUTS", f"Validate input files for {sample_name}", idx, depends_on=[s_locate["id"]])
    s_validate["id"] = "validate_inputs"
    steps.append(s_validate)
    idx += 1

    s_check = _make_step("check_remote_stage", f"Check remote staged data for {sample_name}", idx, depends_on=[s_validate["id"]])
    s_check["id"] = "check_remote_stage"
    steps.append(s_check)
    idx += 1

    s_auth = _make_step(
        "CHECK_REMOTE_PROFILE_AUTH",
        f"Check remote profile authorization for {sample_name}",
        idx,
        depends_on=[s_check["id"]],
    )
    s_auth["id"] = "check_remote_profile_auth"
    steps.append(s_auth)
    idx += 1

    s_approve = _make_step(
        "REQUEST_APPROVAL",
        f"Approve remote staging for {sample_name}",
        idx,
        requires_approval=True,
        depends_on=[s_auth["id"]],
    )
    s_approve["id"] = "approve_remote_stage"
    steps.append(s_approve)
    idx += 1

    s_stage = _make_step(
        "remote_stage",
        f"Stage remote input for {sample_name}",
        idx,
        depends_on=[s_approve["id"]],
    )
    s_stage["id"] = "stage_input"
    steps.append(s_stage)
    idx += 1

    s_complete = _make_step("complete_stage_only", f"Complete remote staging for {sample_name}", idx, depends_on=[s_stage["id"]])
    s_complete["id"] = "complete_stage_only"
    steps.append(s_complete)

    return {
        "plan_type": "remote_stage_workflow",
        "title": f"Stage remote sample for {sample_name}",
        "goal": params.get("goal", f"Stage {sample_name} on remote execution target"),
        "workflow_type": "remote_sample_intake",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "sample_name": sample_name,
        "input_directory": input_dir,
        "work_dir": work_dir,
        "remote_action": "stage_only",
        "steps": steps,
        "artifacts": [],
    }

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

    s_ref_cache = _make_step("FIND_REFERENCE_CACHE", f"Check staged references for {sample_name}", idx,
                             depends_on=[s_check["id"]])
    steps.append(s_ref_cache)
    idx += 1

    s_data_cache = _make_step("FIND_DATA_CACHE", f"Check staged input data for {sample_name}", idx,
                              depends_on=[s_ref_cache["id"]])
    steps.append(s_data_cache)
    idx += 1

    s_approve = _make_step("REQUEST_APPROVAL", f"Approve workflow submission for {sample_name}", idx,
                           requires_approval=True, depends_on=[s_data_cache["id"]])
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

    s_locate = _make_step("LOCATE_DATA", f"Locate result CSV files for {sample_name}", idx,
                          tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                       "params": {"work_dir": work_dir, "extensions": ".csv"}}])
    steps.append(s_locate)
    idx += 1

    s_parse = _make_step("PARSE_OUTPUT_FILE", f"Parse key result CSV files", idx,
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

_RECONCILE_BAMS_PATTERNS = [
    re.compile(r"(?:reconcile|merge|combine)\s+(?:the\s+)?(?:annotated\s+)?bams?", re.I),
    re.compile(r"cross[-\s]?workflow\s+bam\s+reconcil", re.I),
]

_REMOTE_STAGE_PATTERNS = [
    re.compile(r"stage(?:\s+only)?\s+(?:the\s+)?(?:sample\s+)?(?:.+?)\s+(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)[a-zA-Z0-9_-]+(?:\s+profile)?(?:[?.!,]|$)|on\s+slurm|using\s+slurm|remotely|on\s+the\s+cluster|(?:using|via)\s+(?:the\s+)?[a-zA-Z0-9_-]+\s+profile)", re.I),
]

_ENRICHMENT_PATTERNS = [
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:GO|gene\s+ontology|enrichment|pathway)\s+(?:analysis|enrichment)", re.I),
    re.compile(r"(?:GO|gene\s+ontology|enrichment|pathway)\s+(?:analysis|enrichment)\s+(?:on|for|of)", re.I),
    re.compile(r"(?:what|which)\s+(?:GO\s+terms?|pathways?|biological\s+processes?)\s+(?:are\s+)?enriched", re.I),
    re.compile(r"(?:KEGG|Reactome)\s+(?:enrichment|analysis|pathway)", re.I),
    re.compile(r"(?:enrichment|GO)\s+(?:on|for)\s+(?:the\s+)?(?:up|down|significant|DE)", re.I),
]


def _detect_plan_type(message: str) -> str | None:
    """Return plan type string or None.

    Priority ordering: most specific patterns first.
    """
    # 1. DE analysis (most specific)
    for pat in _DE_PATTERNS:
        if pat.search(message):
            return "run_de_pipeline"
    # 1b. Reconcile BAM workflows
    for pat in _RECONCILE_BAMS_PATTERNS:
        if pat.search(message):
            return "reconcile_bams"

    # 2. Enrichment analysis
    for pat in _ENRICHMENT_PATTERNS:
        if pat.search(message):
            return "run_enrichment"
    # 3. Search + compare to local
    for pat in _SEARCH_COMPARE_LOCAL_PATTERNS:
        if pat.search(message):
            return "search_compare_to_local"
    # 4. Compare workflows (before generic compare)
    for pat in _COMPARE_WORKFLOW_PATTERNS:
        if pat.search(message):
            return "compare_workflows"
    # 5. Compare samples
    for pat in _COMPARE_PATTERNS:
        if pat.search(message):
            return "compare_samples"
    # 6. Download + analyze
    for pat in _DOWNLOAD_ANALYZE_PATTERNS:
        if pat.search(message):
            return "download_analyze"
    # 7. Parse + plot + interpret
    for pat in _PARSE_PLOT_PATTERNS:
        if pat.search(message):
            return "parse_plot_interpret"
    # 8. Remote stage workflow
    for pat in _REMOTE_STAGE_PATTERNS:
        if pat.search(message):
            return "remote_stage_workflow"
    # 9. Run workflow
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

    if plan_type == "run_enrichment":
        msg = message.lower()
        if "up" in msg and "down" not in msg:
            params["direction"] = "up"
        elif "down" in msg and "up" not in msg:
            params["direction"] = "down"
        else:
            params["direction"] = "all"
        if "kegg" in msg:
            params["database"] = "KEGG"
        elif "reactome" in msg:
            params["database"] = "REAC"
        return params

    if plan_type == "reconcile_bams":
        m = re.search(r"(?:output\s+(?:prefix|name)|prefix)\s*[=:]?\s*([a-zA-Z0-9._-]+)", message, re.I)
        if m:
            params["output_prefix"] = m.group(1)

        m = re.search(r"(?:output\s+(?:dir|directory))\s+(\S+)", message, re.I)
        if m:
            params["output_directory"] = m.group(1).rstrip(".,;:!?")
        else:
            m = re.search(r"into\s+(\S+)", message, re.I)
            if m:
                params["output_directory"] = m.group(1).rstrip(".,;:!?")
            else:
                m = re.search(r"\bto\s+((?:/|~|\.|[A-Za-z0-9._-]+/)\S*)", message, re.I)
                if m:
                    params["output_directory"] = m.group(1).rstrip(".,;:!?")

        m = re.search(r"(?:annotation\s+gtf|gtf\s+(?:path|file)|use\s+gtf)\s*[=:]?\s*(\S+\.(?:gtf|gtf\.gz))", message, re.I)
        if m:
            params["annotation_gtf"] = m.group(1).rstrip(".,;:!?")

        workflow_dirs: list[str] = []
        selected_names: list[str] = []
        selected_workflow_tokens = {
            match.strip().lower()
            for match in re.findall(r"\b(workflow[\w.-]+)\b", message, re.I)
        }

        workflow_qualified_mentions = re.findall(
            r"([a-zA-Z0-9_.-]+)\s+in\s+(workflow[\w.-]+)",
            message,
            re.I,
        )
        if workflow_qualified_mentions:
            selected_names = [sample for sample, _wf in workflow_qualified_mentions]
            selected_workflow_tokens.update(wf.lower() for _sample, wf in workflow_qualified_mentions)

        named_pair_patterns = [
            r"(?:bams?|workflows?)\s+(?:of|from|between)\s+([a-zA-Z0-9_.-]+)\s+(?:and|vs?\.?|versus)\s+([a-zA-Z0-9_.-]+)",
            r"([a-zA-Z0-9_.-]+)\s+(?:and|vs?\.?|versus)\s+([a-zA-Z0-9_.-]+)",
        ]
        if not selected_names:
            for pattern in named_pair_patterns:
                match = re.search(pattern, message, re.I)
                if not match:
                    continue
                selected_names = [match.group(1), match.group(2)]
                break

        if getattr(conv_state, "workflows", None):
            normalized_targets = {name.lower() for name in selected_names}
            for wf in conv_state.workflows:
                if not isinstance(wf, dict):
                    continue
                wf_dir = wf.get("work_dir")
                if not isinstance(wf_dir, str) or not wf_dir:
                    continue
                work_dir_name = wf_dir.rstrip("/").split("/")[-1].lower()

                if selected_workflow_tokens and work_dir_name not in selected_workflow_tokens:
                    continue

                if normalized_targets:
                    sample_name = str(wf.get("sample_name") or "").strip().lower()
                    if sample_name not in normalized_targets and work_dir_name not in normalized_targets:
                        continue

                if wf_dir not in workflow_dirs:
                    workflow_dirs.append(wf_dir)

            if not workflow_dirs:
                for wf in conv_state.workflows:
                    if isinstance(wf, dict):
                        wf_dir = wf.get("work_dir")
                        if isinstance(wf_dir, str) and wf_dir and wf_dir not in workflow_dirs:
                            workflow_dirs.append(wf_dir)

        known_workflow_basenames = {
            wf_dir.rstrip("/").split("/")[-1].lower()
            for wf_dir in workflow_dirs
            if isinstance(wf_dir, str) and wf_dir
        }
        for match in re.findall(r"(workflow[\w.-]+)", message, re.I):
            normalized = match.strip()
            if normalized and normalized.lower() not in known_workflow_basenames and normalized not in workflow_dirs:
                workflow_dirs.append(normalized)

        if workflow_dirs:
            params["workflow_dirs"] = workflow_dirs

        return params

    if plan_type == "parse_plot_interpret":
        params["plot_type"] = _select_plot_type(message)
        # Fall through to pick up sample_name / work_dir below

    if plan_type == "remote_stage_workflow":
        sample_match = re.search(r'(?:called|named)\s+([a-zA-Z0-9_-]+)', message, re.I)
        if not sample_match:
            sample_match = re.search(r'(?:the\s+)?sample\s+([a-zA-Z0-9_-]+)', message, re.I)
        if sample_match:
            params["sample_name"] = sample_match.group(1)

        input_match = re.search(r"(?:at|from)\s+(\S+)", message, re.I)
        if input_match:
            params["input_directory"] = input_match.group(1).rstrip(".,;:!?")

    # run_workflow / remote_stage_workflow / summarize_results / parse_plot_interpret
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
    Steps: CHECK_EXISTING → RUN_DE_PIPELINE → ANNOTATE_RESULTS → GENERATE_DE_PLOT → INTERPRET_RESULTS → WRITE_SUMMARY
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

    s_annotate = _make_step("ANNOTATE_RESULTS", "Annotate gene symbols", idx,
                            depends_on=[s_de["id"]],
                            tool_calls=[{"source_key": "edgepython", "tool": "annotate_genes",
                                         "params": {}}])
    steps.append(s_annotate)
    idx += 1

    s_plot = _make_step("GENERATE_DE_PLOT", "Generate volcano plot", idx,
                        depends_on=[s_annotate["id"]],
                        tool_calls=[{"source_key": "edgepython", "tool": "generate_plot",
                                     "params": {"plot_type": "volcano"}}])
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step("INTERPRET_RESULTS", "Interpret DE results", idx,
                             depends_on=[s_annotate["id"]])
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


# ---- Template: run_enrichment ---------------------------------------------

def _template_run_enrichment(params: dict) -> dict:
    """
    Deterministic plan for enrichment analysis.
    Steps: FILTER_DE_GENES → RUN_GO_ENRICHMENT → RUN_PATHWAY_ENRICHMENT → PLOT_ENRICHMENT → SUMMARIZE_ENRICHMENT
    """
    direction = params.get("direction", "all")
    database = params.get("database")

    steps = []
    idx = 0

    s_filter = _make_step("FILTER_DE_GENES", f"Filter DE genes ({direction})", idx,
                           tool_calls=[{"source_key": "edgepython", "tool": "filter_de_genes",
                                        "params": {"direction": direction}}])
    steps.append(s_filter)
    idx += 1

    s_go = _make_step("RUN_GO_ENRICHMENT", f"GO enrichment ({direction})", idx,
                       depends_on=[s_filter["id"]],
                       tool_calls=[{"source_key": "edgepython", "tool": "run_go_enrichment",
                                    "params": {"direction": direction}}])
    steps.append(s_go)
    idx += 1

    # Add pathway enrichment unless user specifically asked for GO only
    if database != "GO_ONLY":
        db = database or "KEGG"
        s_pathway = _make_step("RUN_PATHWAY_ENRICHMENT", f"{db} pathway enrichment ({direction})", idx,
                                depends_on=[s_filter["id"]],
                                tool_calls=[{"source_key": "edgepython", "tool": "run_pathway_enrichment",
                                             "params": {"direction": direction, "database": db}}])
        steps.append(s_pathway)
        idx += 1
        plot_deps = [s_go["id"], s_pathway["id"]]
    else:
        plot_deps = [s_go["id"]]

    s_plot = _make_step("PLOT_ENRICHMENT", "Plot enrichment results", idx,
                         depends_on=plot_deps,
                         tool_calls=[{"source_key": "edgepython", "tool": "generate_plot",
                                      "params": {"plot_type": "enrichment_bar"}}])
    steps.append(s_plot)
    idx += 1

    s_summarize = _make_step("SUMMARIZE_ENRICHMENT", "Interpret enrichment results", idx,
                              depends_on=[s_plot["id"]])
    steps.append(s_summarize)

    return {
        "plan_type": "run_enrichment",
        "title": f"Enrichment analysis ({direction})",
        "goal": params.get("goal", f"Run enrichment analysis ({direction})"),
        "workflow_type": "enrichment_analysis",
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

    s_locate = _make_step("LOCATE_DATA", f"Locate result CSV files for {sample_name}", idx,
                          tool_calls=[{"source_key": "analyzer", "tool": "list_job_files",
                                       "params": {"work_dir": work_dir, "extensions": ".csv"}}])
    steps.append(s_locate)
    idx += 1

    s_parse = _make_step("PARSE_OUTPUT_FILE", "Parse key result CSV files", idx,
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


# ---- Template: reconcile_bams -------------------------------------------

def _template_reconcile_bams(params: dict) -> dict:
    """
    Deterministic plan for reconciling annotated BAMs across workflows.
    Steps: locate inputs -> preflight validate -> approval -> run reconcile script -> summarize
    """
    output_prefix = params.get("output_prefix", "reconciled")
    output_directory = params.get("output_directory", "")
    annotation_gtf = params.get("annotation_gtf", "")
    work_dir = params.get("work_dir", "")
    workflow_dirs = [str(item) for item in params.get("workflow_dirs", []) if isinstance(item, str) and item]
    if not output_directory:
        if workflow_dirs:
            workflow_roots = [os.path.dirname(path.rstrip("/")) or "/" for path in workflow_dirs]
            output_directory = os.path.commonpath(workflow_roots)
        elif work_dir:
            output_directory = work_dir

    steps = []
    idx = 0

    locate_tool_calls = []
    if workflow_dirs:
        for workflow_dir in workflow_dirs:
            locate_tool_calls.append(
                {
                    "source_key": "analyzer",
                    "tool": "list_job_files",
                    "params": {
                        "work_dir": f"{workflow_dir.rstrip('/')}/annot",
                        "extensions": ".bam",
                        "max_depth": 1,
                    },
                }
            )
    elif work_dir:
        locate_tool_calls.append(
            {
                "source_key": "analyzer",
                "tool": "list_job_files",
                "params": {
                    "work_dir": f"{work_dir.rstrip('/')}/annot",
                    "extensions": ".bam",
                    "max_depth": 1,
                },
            }
        )

    s_locate = _make_step(
        "LOCATE_DATA",
        "Locate annotated BAM candidates across workflow annot directories",
        idx,
        tool_calls=locate_tool_calls,
    )
    steps.append(s_locate)
    idx += 1

    preflight_args = ["--json", "--preflight-only", "--output-prefix", output_prefix]
    if workflow_dirs:
        for workflow_dir in workflow_dirs:
            preflight_args.extend(["--workflow-dir", workflow_dir])
    elif work_dir:
        preflight_args.extend(["--project-dir", work_dir])
    if output_directory:
        preflight_args.extend(["--output-dir", output_directory])
    if isinstance(annotation_gtf, str) and annotation_gtf:
        preflight_args.extend(["--annotation-gtf", annotation_gtf])

    s_preflight = _make_step(
        "CHECK_EXISTING",
        "Run reconcile preflight and validate shared reference before approval",
        idx,
        depends_on=[s_locate["id"]],
        tool_calls=[
            {
                "source_key": "launchpad",
                "tool": "run_allowlisted_script",
                "params": {
                    "script_id": "reconcile_bams/reconcile_bams",
                    "script_args": preflight_args,
                },
            }
        ],
    )
    steps.append(s_preflight)
    idx += 1

    s_approve = _make_step(
        "REQUEST_APPROVAL",
        "Approve reconcile BAM execution",
        idx,
        requires_approval=True,
        depends_on=[s_preflight["id"]],
    )
    steps.append(s_approve)
    idx += 1

    script_args = ["--json", "--output-prefix", output_prefix]
    if workflow_dirs:
        for workflow_dir in workflow_dirs:
            script_args.extend(["--workflow-dir", workflow_dir])
    elif work_dir:
        script_args.extend(["--project-dir", work_dir])

    if output_directory:
        script_args.extend(["--output-dir", output_directory])
    if isinstance(annotation_gtf, str) and annotation_gtf:
        script_args.extend(["--annotation-gtf", annotation_gtf])

    s_run = _make_step(
        "RUN_SCRIPT",
        "Run reconcile BAM script using symlinked workflow inputs",
        idx,
        requires_approval=True,
        depends_on=[s_approve["id"]],
        tool_calls=[
            {
                "source_key": "launchpad",
                "tool": "run_allowlisted_script",
                "params": {
                    "script_id": "reconcile_bams/reconcile_bams",
                    "script_args": script_args,
                },
            }
        ],
    )
    steps.append(s_run)
    idx += 1

    s_summary = _make_step(
        "WRITE_SUMMARY",
        "Summarize reconcile outputs and generated files",
        idx,
        depends_on=[s_run["id"]],
    )
    steps.append(s_summary)

    return {
        "plan_type": "reconcile_bams",
        "title": "Reconcile annotated BAMs across workflows",
        "goal": params.get("goal", "Reconcile annotated BAM outputs using a shared reference"),
        "workflow_type": "reconcile_bams",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "output_prefix": output_prefix,
        "output_directory": output_directory,
        "annotation_gtf": annotation_gtf,
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
    start = raw_response.find("[[PLAN:")
    if start < 0:
        return None
    end = raw_response.find("]]", start)
    if end < 0:
        return None
    payload = raw_response[start + len("[[PLAN:"):end].strip()
    try:
        plan = json.loads(payload)
        if isinstance(plan, dict) and ("steps" in plan or "fragments" in plan):
            return plan
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse LLM plan JSON", raw=payload[:200])
    return None


_HYBRID_FIRST_FLOWS = frozenset({
    "compare_samples",
    "download_analyze",
    "summarize_results",
    "parse_plot_interpret",
    "compare_workflows",
    "search_compare_to_local",
})


def _is_summarize_results_request(message: str, conv_state: "ConversationState") -> bool:
    return bool(
        conv_state.work_dir
        and re.search(r"(?:summarize|interpret|explain)\s+(?:the\s+)?(?:results?|output|qc)", message, re.I)
    )


def _deterministic_template_for_plan_type(plan_type: str, params: dict) -> dict | None:
    if plan_type == "compare_samples":
        return _template_compare_samples(params)
    if plan_type == "download_analyze":
        return _template_download_analyze(params)
    if plan_type == "summarize_results":
        return _template_summarize_results(params)
    if plan_type == "parse_plot_interpret":
        return _template_parse_plot_interpret(params)
    if plan_type == "compare_workflows":
        return _template_compare_workflows(params)
    if plan_type == "search_compare_to_local":
        return _template_search_compare_to_local(params)
    if plan_type == "run_workflow":
        return _template_run_workflow(params)
    if plan_type == "run_de_pipeline":
        return _template_run_de_pipeline(params)
    if plan_type == "run_enrichment":
        return _template_run_enrichment(params)
    if plan_type == "remote_stage_workflow":
        return _template_remote_stage_workflow(params)
    if plan_type == "reconcile_bams":
        return _template_reconcile_bams(params)
    return None


def _generate_hybrid_first_scoped_plan(
    *,
    requested_plan_type: str,
    params: dict,
    message: str,
    conv_state: "ConversationState",
    engine: "AgentEngine",
    conversation_history: list | None,
) -> dict | None:
    def _fallback(reason_code: str) -> dict | None:
        logger.info(
            "Hybrid bridge fallback",
            plan_type=requested_plan_type,
            hybrid_attempted=True,
            hybrid_succeeded=False,
            hybrid_failed=True,
            reason_code=reason_code,
            deterministic_fallback_used=True,
        )
        fallback_plan = _deterministic_template_for_plan_type(requested_plan_type, params)
        if not fallback_plan:
            return None
        fallback_plan["planner_metadata"] = {
            "planning_mode": "deterministic",
            "hybrid_attempted": True,
            "hybrid_succeeded": False,
            "hybrid_failed": True,
            "deterministic_fallback_used": True,
            "reason_code": reason_code,
        }
        return _finalize_plan(fallback_plan, conv_state)

    try:
        state_json = conv_state.to_json()
        raw, _usage = engine.plan(message, state_json, conversation_history)
    except Exception as exc:
        logger.warning("Hybrid bridge engine.plan failed", plan_type=requested_plan_type, error=str(exc))
        return _fallback("engine_plan_exception")

    if not raw:
        return _fallback("engine_plan_empty")

    llm_plan = _parse_llm_plan(raw)
    if not llm_plan:
        return _fallback("parse_no_plan")

    returned_plan_type = llm_plan.get("plan_type")
    if not isinstance(returned_plan_type, str) or returned_plan_type != requested_plan_type:
        return _fallback("plan_type_mismatch_for_requested_flow")

    fragments = llm_plan.get("fragments")
    if isinstance(fragments, list) and fragments:
        try:
            llm_plan = compose_plan_fragments(
                fragments,
                title=llm_plan.get("title", message[:80]),
                goal=llm_plan.get("goal", message),
                plan_type=returned_plan_type,
                project_id=conv_state.active_project,
            )
        except Exception as exc:
            logger.warning(
                "Hybrid bridge fragment composition failed",
                plan_type=requested_plan_type,
                error=str(exc),
            )
            return _fallback("fragment_compose_failed")

    llm_plan.setdefault("plan_type", requested_plan_type)
    llm_plan.setdefault("title", message[:80])
    llm_plan.setdefault("goal", message)
    llm_plan.setdefault("status", "PENDING")
    llm_plan.setdefault("auto_execute_safe_steps", True)
    llm_plan.setdefault("artifacts", [])
    llm_plan.setdefault("project_id", conv_state.active_project)
    llm_plan.setdefault("plan_instance_id", _plan_instance_id())

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

    finalized = _finalize_plan(llm_plan, conv_state)
    if finalized is None:
        return _fallback("finalize_validation_failed")

    finalized["planner_metadata"] = {
        "planning_mode": "hybrid",
        "hybrid_attempted": True,
        "hybrid_succeeded": True,
        "hybrid_failed": False,
        "deterministic_fallback_used": False,
        "reason_code": "",
    }

    logger.info(
        "Hybrid bridge succeeded",
        plan_type=requested_plan_type,
        hybrid_attempted=True,
        hybrid_succeeded=True,
        hybrid_failed=False,
        reason_code="",
        deterministic_fallback_used=False,
    )
    return finalized


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
    if not plan_type and _is_summarize_results_request(message, conv_state):
        plan_type = "summarize_results"

    params = _extract_plan_params(message, conv_state, plan_type or "")

    # Temporary bridge: hybrid-first for selected non-core flows.
    if plan_type in _HYBRID_FIRST_FLOWS:
        bridge_plan = _generate_hybrid_first_scoped_plan(
            requested_plan_type=plan_type,
            params=params,
            message=message,
            conv_state=conv_state,
            engine=engine,
            conversation_history=conversation_history,
        )
        if bridge_plan is not None:
            return bridge_plan
        logger.info("Hybrid bridge returned no plan", plan_type=plan_type)
        return None

    # 1. Deterministic templates
    if plan_type == "run_de_pipeline":
        plan = _template_run_de_pipeline(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return _finalize_plan(plan, conv_state)
    if plan_type == "run_enrichment":
        plan = _template_run_enrichment(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return _finalize_plan(plan, conv_state)
    if plan_type == "remote_stage_workflow":
        plan = _template_remote_stage_workflow(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return _finalize_plan(plan, conv_state)
    if plan_type == "run_workflow":
        plan = _template_run_workflow(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return _finalize_plan(plan, conv_state)
    if plan_type == "reconcile_bams":
        plan = _template_reconcile_bams(params)
        logger.info("Generated plan from template", plan_type=plan_type, steps=len(plan["steps"]))
        return _finalize_plan(plan, conv_state)

    # 2. LLM fallback — ask the engine to produce a structured plan
    try:
        state_json = conv_state.to_json()
        raw, _usage = engine.plan(message, state_json, conversation_history)
        if raw:
            llm_plan = _parse_llm_plan(raw)
            if llm_plan:
                fragments = llm_plan.get("fragments")
                if isinstance(fragments, list) and fragments:
                    llm_plan = compose_plan_fragments(
                        fragments,
                        title=llm_plan.get("title", message[:80]),
                        goal=llm_plan.get("goal", message),
                        plan_type=llm_plan.get("plan_type", "custom"),
                        project_id=conv_state.active_project,
                    )

                # Ensure required fields
                llm_plan.setdefault("plan_type", "custom")
                llm_plan.setdefault("title", message[:80])
                llm_plan.setdefault("goal", message)
                llm_plan.setdefault("status", "PENDING")
                llm_plan.setdefault("auto_execute_safe_steps", True)
                llm_plan.setdefault("artifacts", [])
                llm_plan.setdefault("project_id", conv_state.active_project)
                llm_plan.setdefault("plan_instance_id", _plan_instance_id())
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
                llm_plan.setdefault(
                    "planner_metadata",
                    {
                        "planning_mode": "llm",
                        "hybrid_attempted": False,
                        "hybrid_succeeded": False,
                        "hybrid_failed": False,
                        "deterministic_fallback_used": False,
                        "reason_code": "",
                    },
                )
                logger.info("Generated plan from LLM", steps=len(llm_plan.get("steps", [])))
                return _finalize_plan(llm_plan, conv_state)
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
