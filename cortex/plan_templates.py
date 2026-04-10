"""
Deterministic plan templates for structured WORKFLOW_PLAN payloads.

Each template function accepts a params dict and returns a complete plan dict
with typed steps, tool_calls, and dependency chains.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from common.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Step / plan ID helpers
# ---------------------------------------------------------------------------

def _step_id() -> str:
    return uuid.uuid4().hex[:12]


def _plan_instance_id() -> str:
    return uuid.uuid4().hex


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


def _normalize_fragment_alias(raw_alias: str, *, fallback: str) -> str:
    alias = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_alias).strip("_")
    return alias or fallback


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

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


def _template_run_de_pipeline(params: dict) -> dict:
    """
    Deterministic plan for full differential expression analysis.
    Steps: CHECK_EXISTING → PREPARE_DE_INPUT? → RUN_DE_PIPELINE → SAVE_RESULTS → ANNOTATE_RESULTS → GENERATE_DE_PLOT → INTERPRET_RESULTS → WRITE_SUMMARY
    """
    counts_path = params.get("counts_path", "counts.csv")
    sample_info = params.get("sample_info_path", "sample_info.csv")
    group_col = params.get("group_column", "condition")
    contrast = params.get("contrast", "treated - control")
    group_a_label = params.get("group_a_label", "group1")
    group_b_label = params.get("group_b_label", "group2")
    group_a_samples = params.get("group_a_samples") or []
    group_b_samples = params.get("group_b_samples") or []
    use_prep = bool(group_a_samples and group_b_samples)
    method = params.get("method") or ("exact_test" if use_prep else "glm")
    work_dir = str(params.get("work_dir") or "").strip()
    result_name = params.get("result_name") or f"{re.sub(r'[^a-zA-Z0-9]+', '_', str(group_a_label)).strip('_').lower() or 'group1'}_vs_{re.sub(r'[^a-zA-Z0-9]+', '_', str(group_b_label)).strip('_').lower() or 'group2'}_{params.get('level', 'gene')}"

    steps = []
    idx = 0

    s_check = _make_step("CHECK_EXISTING", "Check for existing DE results", idx,
                         tool_calls=[{"source_key": "analyzer", "tool": "find_file",
                                      "params": {
                                          "file_name": "de_results",
                                          **({"work_dir": work_dir} if work_dir else {}),
                                      } }])
    steps.append(s_check)
    idx += 1

    de_depends = [s_check["id"]]
    if use_prep:
        s_prep = _make_step(
            "PREPARE_DE_INPUT",
            f"Prepare DE inputs ({group_a_label} vs {group_b_label})",
            idx,
            depends_on=[s_check["id"]],
        )
        s_prep["counts_path"] = params.get("counts_path", "")
        s_prep["work_dir"] = params.get("work_dir", "")
        s_prep["df_id"] = params.get("df_id")
        s_prep["output_dir"] = params.get("prep_output_dir", "")
        s_prep["group_a_label"] = group_a_label
        s_prep["group_a_samples"] = list(group_a_samples)
        s_prep["group_b_label"] = group_b_label
        s_prep["group_b_samples"] = list(group_b_samples)
        s_prep["level"] = params.get("level", "gene")
        steps.append(s_prep)
        idx += 1
        de_depends.append(s_prep["id"])
        sample_info = params.get("sample_info_path", "")
        group_col = "group"
        contrast = f"{group_a_label} - {group_b_label}"

    de_tool_calls = [
        {"source_key": "edgepython", "tool": "load_data",
         "params": {"counts_path": counts_path, "sample_info_path": sample_info,
                    "group_column": group_col}},
        {"source_key": "edgepython", "tool": "filter_genes",
         "params": {"min_count": 10, "min_total_count": 15}},
        {"source_key": "edgepython", "tool": "normalize",
         "params": {"method": "TMM"}},
    ]
    if method == "exact_test":
        de_tool_calls.extend([
            {"source_key": "edgepython", "tool": "estimate_dispersion",
             "params": {"robust": True}},
            {"source_key": "edgepython", "tool": "exact_test",
             "params": {"pair": [group_a_label, group_b_label], "name": result_name}},
        ])
    else:
        de_tool_calls.extend([
            {"source_key": "edgepython", "tool": "set_design",
             "params": {"formula": "~ 0 + group"}},
            {"source_key": "edgepython", "tool": "estimate_dispersion",
             "params": {"robust": True}},
            {"source_key": "edgepython", "tool": "fit_model",
             "params": {"robust": True}},
            {"source_key": "edgepython", "tool": "test_contrast",
             "params": {"contrast": contrast, "name": result_name}},
        ])
    de_tool_calls.append(
        {"source_key": "edgepython", "tool": "get_top_genes",
         "params": {"name": result_name, "n": 20, "fdr_threshold": 0.05}}
    )

    s_de = _make_step("RUN_DE_PIPELINE", f"Run DE analysis ({contrast})", idx,
                      requires_approval=False, depends_on=de_depends,
                      tool_calls=de_tool_calls)
    steps.append(s_de)
    idx += 1

    s_save = _make_step("SAVE_RESULTS", "Save full DE results", idx,
                        depends_on=[s_de["id"]],
                        tool_calls=[{"source_key": "edgepython", "tool": "save_results",
                                     "params": {"name": result_name, "format": "tsv"}}])
    steps.append(s_save)
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
                                     "params": {"plot_type": "volcano", "result_name": result_name}}])
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step("INTERPRET_RESULTS", "Interpret DE results", idx,
                             depends_on=[s_annotate["id"]])
    steps.append(s_interpret)
    idx += 1

    s_summary = _make_step("WRITE_SUMMARY", "Write DE analysis summary", idx,
                           depends_on=[s_save["id"], s_plot["id"], s_interpret["id"]])
    steps.append(s_summary)

    return {
        "plan_type": "run_de_pipeline",
        "title": f"Differential expression: {contrast}",
        "goal": params.get("goal", f"Run DE analysis: {contrast}"),
        "workflow_type": "de_analysis",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "work_dir": work_dir,
        "steps": steps,
        "artifacts": [],
    }


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


def _template_run_xgenepy_analysis(params: dict) -> dict:
    """
    Deterministic plan for local XgenePy cis/trans analysis.
    Steps: CHECK_EXISTING -> REQUEST_APPROVAL -> RUN_XGENEPY -> PARSE_XGENEPY_OUTPUT -> WRITE_SUMMARY
    """
    project_dir = params.get("project_dir") or params.get("work_dir") or ""
    counts_path = params.get("counts_path", "counts.csv")
    metadata_path = params.get("metadata_path", "metadata.csv")
    output_subdir = params.get("output_subdir", "xgenepy_runs")
    trans_model = params.get("trans_model", "log_additive")
    alpha = float(params.get("alpha", 0.05))

    steps = []
    idx = 0

    s_check = _make_step(
        "CHECK_EXISTING",
        "Check for existing XgenePy manifest",
        idx,
        tool_calls=[
            {
                "source_key": "analyzer",
                "tool": "find_file",
                "params": {
                    "work_dir": project_dir,
                    "file_name": "run_manifest.json",
                },
            }
        ],
    )
    steps.append(s_check)
    idx += 1

    s_approve = _make_step(
        "REQUEST_APPROVAL",
        "Approve local XgenePy execution",
        idx,
        requires_approval=True,
        depends_on=[s_check["id"]],
    )
    steps.append(s_approve)
    idx += 1

    s_run = _make_step(
        "RUN_XGENEPY",
        "Run local XgenePy cis/trans analysis",
        idx,
        requires_approval=False,
        depends_on=[s_approve["id"]],
        tool_calls=[
            {
                "source_key": "xgenepy",
                "tool": "run_xgenepy_analysis",
                "params": {
                    "project_dir": project_dir,
                    "counts_path": counts_path,
                    "metadata_path": metadata_path,
                    "output_subdir": output_subdir,
                    "trans_model": trans_model,
                    "alpha": alpha,
                    "execution_mode": "local",
                },
            }
        ],
    )
    steps.append(s_run)
    idx += 1

    s_parse = _make_step(
        "PARSE_XGENEPY_OUTPUT",
        "Parse canonical XgenePy outputs",
        idx,
        depends_on=[s_run["id"]],
        tool_calls=[
            {
                "source_key": "analyzer",
                "tool": "parse_xgenepy_outputs",
                "params": {
                    "work_dir": project_dir,
                    "output_dir": output_subdir,
                },
            }
        ],
    )
    steps.append(s_parse)
    idx += 1

    s_summary = _make_step(
        "WRITE_SUMMARY",
        "Summarize XgenePy analysis results",
        idx,
        depends_on=[s_parse["id"]],
    )
    steps.append(s_summary)

    return {
        "plan_type": "run_xgenepy_analysis",
        "title": "Run XgenePy cis/trans analysis",
        "goal": params.get("goal", "Run XgenePy analysis"),
        "workflow_type": "xgenepy_analysis",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }


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
                        "allow_missing": True,
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
                    "allow_missing": True,
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

    # After the script finishes, locate the output files so the parse step
    # can discover which tabular files (TSV/CSV) were produced.
    _list_out_dir = output_directory or work_dir or ""
    s_locate_out = _make_step(
        "LOCATE_DATA",
        "List reconcile output files for parsing",
        idx,
        depends_on=[s_run["id"]],
        tool_calls=[
            {
                "source_key": "analyzer",
                "tool": "list_job_files",
                "params": {
                    "work_dir": _list_out_dir,
                    "max_depth": 1,
                    "allow_missing": True,
                },
            }
        ],
    )
    steps.append(s_locate_out)
    idx += 1

    s_parse = _make_step(
        "PARSE_OUTPUT_FILE",
        "Parse reconcile result tables",
        idx,
        depends_on=[s_locate_out["id"]],
    )
    steps.append(s_parse)
    idx += 1

    s_summary = _make_step(
        "WRITE_SUMMARY",
        "Summarize reconcile outputs and generated files",
        idx,
        depends_on=[s_parse["id"]],
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
# Template dispatch
# ---------------------------------------------------------------------------

# NOTE: _deterministic_template_for_plan_type was moved to planner.py
# so monkeypatching re-exported template names in planner's namespace
# is picked up by the dispatch function at call time.
