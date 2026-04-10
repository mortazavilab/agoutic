"""
Deterministic plan step executor.

Maps step kinds to concrete tool call sequences.
The LLM does NOT improvise execution — code controls it.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common import MCPHttpClient
from common.logging_config import get_logger
from sqlalchemy import select
from cortex.config import get_service_url
from cortex.plan_validation import PlanValidationError, validate_plan

if TYPE_CHECKING:
    from cortex.agent_engine import AgentEngine
    from cortex.models import ProjectBlock

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Step result
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# Safety classification
# ---------------------------------------------------------------------------

# Safe steps auto-execute without user approval
_SAFE_STEP_KINDS = frozenset({
    "LOCATE_DATA",
    "VALIDATE_INPUTS",
    "PREPARE_DE_INPUT",
    "PARSE_OUTPUT_FILE",
    "SUMMARIZE_QC",
    "GENERATE_PLOT",
    "WRITE_SUMMARY",
    "CHECK_EXISTING",
    "GENERATE_DE_PLOT",
    "INTERPRET_RESULTS",
    "RECOMMEND_NEXT",
    "ANNOTATE_RESULTS",
    "SAVE_RESULTS",
    "FILTER_DE_GENES",
    "RUN_GO_ENRICHMENT",
    "RUN_PATHWAY_ENRICHMENT",
    "PLOT_ENRICHMENT",
    "SUMMARIZE_ENRICHMENT",
    "PARSE_XGENEPY_OUTPUT",
})

# Expensive steps require explicit approval
_APPROVAL_STEP_KINDS = frozenset({
    "SUBMIT_WORKFLOW",
    "DOWNLOAD_DATA",
    "RUN_DE_ANALYSIS",
    "RUN_DE_PIPELINE",
    "RUN_XGENEPY",
    "RUN_SCRIPT",
    "REQUEST_APPROVAL",
})


_PARALLEL_BATCH_SAFE_KINDS = frozenset({
    "LOCATE_DATA",
    "SEARCH_ENCODE",
    "CHECK_EXISTING",
})


def should_auto_execute(step: dict) -> bool:
    """Return True if a step is safe to auto-execute without user approval."""
    if step.get("requires_approval"):
        return False
    kind = step.get("kind", "")
    if kind in _SAFE_STEP_KINDS:
        return True
    if kind in _APPROVAL_STEP_KINDS:
        return False
    # Unknown kinds: don't auto-execute (safe default)
    return False


def _parse_candidate_priority(file_info: dict) -> tuple[int, str]:
    name = str(file_info.get("name") or file_info.get("path") or "").lower()
    path = str(file_info.get("path") or file_info.get("name") or "")
    if "qc_summary" in name:
        return (0, path)
    if "final_stats" in name:
        return (1, path)
    if "stats" in name or "flagstat" in name:
        return (2, path)
    if "summary" in name:
        return (3, path)
    return (10, path)


def _extract_located_tabular_files(step_result: Any) -> tuple[str | None, list[str]]:
    work_dir: str | None = None
    file_entries: list[dict[str, Any]] = []

    results_to_scan = step_result if isinstance(step_result, list) else [step_result]
    for item in results_to_scan:
        if not isinstance(item, dict):
            continue
        payload = item.get("result") if "result" in item else item
        if not isinstance(payload, dict):
            continue
        work_dir = work_dir or payload.get("work_dir")
        files = payload.get("files")
        if isinstance(files, list):
            file_entries.extend(entry for entry in files if isinstance(entry, dict))

    tabular = [
        entry for entry in file_entries
        if str(entry.get("extension") or "").lower() in {".csv", ".tsv"}
    ]
    ordered = sorted(tabular, key=_parse_candidate_priority)
    file_paths = [str(entry.get("path")) for entry in ordered if entry.get("path")]
    if len(file_paths) > 5:
        file_paths = file_paths[:5]
    return work_dir, file_paths


def _derive_parse_tool_calls(plan_payload: dict, step: dict) -> list[dict]:
    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
    dependency_ids = step.get("depends_on") if isinstance(step.get("depends_on"), list) else []

    candidate_locates: list[dict] = []
    for dep_id in dependency_ids:
        dep_step = _find_step(steps, dep_id) if isinstance(dep_id, str) else None
        if dep_step and dep_step.get("kind") == "LOCATE_DATA":
            candidate_locates.append(dep_step)

    if not candidate_locates:
        candidate_locates = [
            s for s in steps
            if isinstance(s, dict) and s.get("kind") == "LOCATE_DATA" and s.get("status") == "COMPLETED"
        ]

    work_dir: str | None = None
    file_paths: list[str] = []
    for locate_step in candidate_locates:
        locate_work_dir, located_files = _extract_located_tabular_files(locate_step.get("result"))
        work_dir = work_dir or locate_work_dir
        for file_path in located_files:
            if file_path not in file_paths:
                file_paths.append(file_path)

    if not file_paths:
        return []

    return [
        {
            "source_key": "analyzer",
            "tool": "parse_csv_file",
            "params": {
                "file_path": file_path,
                "work_dir": work_dir or plan_payload.get("work_dir") or "",
            },
        }
        for file_path in file_paths
    ]


def _iter_dependency_steps(plan_payload: dict, step: dict) -> list[dict]:
    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
    if not isinstance(steps, list):
        return []

    dep_ids = step.get("depends_on")
    if isinstance(dep_ids, list) and dep_ids:
        resolved: list[dict] = []
        for dep_id in dep_ids:
            if isinstance(dep_id, str):
                dep_step = _find_step(steps, dep_id)
                if dep_step:
                    resolved.append(dep_step)
        if resolved:
            return resolved

    step_index = None
    for idx, candidate in enumerate(steps):
        if candidate is step:
            step_index = idx
            break

    if step_index is None:
        return []
    return [candidate for candidate in steps[:step_index] if isinstance(candidate, dict)]


def _coerce_step_result_items(step_result: Any) -> list[dict[str, Any]]:
    if isinstance(step_result, list):
        return [item for item in step_result if isinstance(item, dict)]
    if isinstance(step_result, dict):
        return [step_result]
    return []


def _extract_parsed_tables(step_result: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for item in _coerce_step_result_items(step_result):
        payload = item.get("result") if "result" in item else item
        if not isinstance(payload, dict):
            continue
        if payload.get("success") is False:
            continue
        rows = payload.get("data")
        columns = payload.get("columns")
        if not isinstance(rows, list) or not isinstance(columns, list) or not columns:
            continue
        file_path = str(payload.get("file_path") or item.get("file_path") or "parsed_results.csv")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        tables.append({
            "file_path": file_path,
            "label": Path(file_path).name or file_path,
            "columns": columns,
            "data": rows,
            "row_count": int(payload.get("row_count") or len(rows)),
            "metadata": metadata,
        })
    return tables


def _extract_all_dependency_tables(plan_payload: dict, step: dict) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for dep_step in _iter_dependency_steps(plan_payload, step):
        for table in _extract_parsed_tables(dep_step.get("result")):
            file_path = table.get("file_path")
            if isinstance(file_path, str) and file_path not in seen_paths:
                seen_paths.add(file_path)
                tables.append(table)

    if tables:
        return tables

    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
    for candidate in steps:
        if candidate is step:
            break
        if not isinstance(candidate, dict):
            continue
        if candidate.get("status") != "COMPLETED":
            continue
        for table in _extract_parsed_tables(candidate.get("result")):
            file_path = table.get("file_path")
            if isinstance(file_path, str) and file_path not in seen_paths:
                seen_paths.add(file_path)
                tables.append(table)
    return tables


def _table_plot_priority(table: dict[str, Any]) -> tuple[int, int, str]:
    columns = {str(col).lower() for col in table.get("columns", [])}
    file_path = str(table.get("file_path") or "")
    if {"category", "count"}.issubset(columns):
        return (0, -int(table.get("row_count") or 0), file_path)
    if "file_type" in columns and ("n_reads" in columns or "mapped_pct" in columns):
        return (1, -int(table.get("row_count") or 0), file_path)

    numeric_candidates = [
        str(col) for col in table.get("columns", [])
        if str(col).lower() in {"count", "counts", "value", "values", "n", "total", "mapped_pct", "n_reads"}
    ]
    if numeric_candidates:
        return (2, -int(table.get("row_count") or 0), file_path)
    return (10, -int(table.get("row_count") or 0), file_path)


def _is_number_like(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def _choose_plot_axes(table: dict[str, Any], plot_type: str) -> tuple[str | None, str | None, str | None]:
    columns = [str(col) for col in table.get("columns", [])]
    lower_to_original = {col.lower(): col for col in columns}
    rows = table.get("data") if isinstance(table.get("data"), list) else []

    if "category" in lower_to_original and "count" in lower_to_original:
        return lower_to_original["category"], lower_to_original["count"], None
    if "file_type" in lower_to_original:
        if "n_reads" in lower_to_original:
            return lower_to_original["file_type"], lower_to_original["n_reads"], None
        if "mapped_pct" in lower_to_original:
            return lower_to_original["file_type"], lower_to_original["mapped_pct"], None

    numeric_cols: list[str] = []
    categorical_cols: list[str] = []
    sample_rows = rows[: min(len(rows), 25)]
    for col in columns:
        values = [row.get(col) for row in sample_rows if isinstance(row, dict)]
        present = [value for value in values if value not in (None, "")]
        if present and all(_is_number_like(value) for value in present):
            numeric_cols.append(col)
        else:
            categorical_cols.append(col)

    if plot_type == "scatter" and len(numeric_cols) >= 2:
        return numeric_cols[0], numeric_cols[1], None

    if categorical_cols and numeric_cols:
        return categorical_cols[0], numeric_cols[0], None
    if numeric_cols:
        return numeric_cols[0], None, None
    if columns:
        return columns[0], None, None
    return None, None, None


def _build_workflow_plot_payload(plan_payload: dict, step: dict) -> dict[str, Any] | None:
    tables = _extract_all_dependency_tables(plan_payload, step)
    if not tables:
        return None

    selected = min(tables, key=_table_plot_priority)
    requested_type = str(
        step.get("plot_type")
        or plan_payload.get("plot_type")
        or "bar"
    ).strip().lower() or "bar"
    x_col, y_col, color_col = _choose_plot_axes(selected, requested_type)
    if not x_col:
        return None

    chart_spec: dict[str, Any] = {
        "type": requested_type,
        "df_id": 1,
        "x": x_col,
        "title": step.get("title") or f"{requested_type.title()} chart",
    }
    if y_col:
        chart_spec["y"] = y_col
    if color_col:
        chart_spec["color"] = color_col

    dataframe_payload = {
        selected.get("label") or "plot_source.csv": {
            "columns": selected.get("columns", []),
            "data": selected.get("data", []),
            "row_count": selected.get("row_count", 0),
            "metadata": {
                **(selected.get("metadata") or {}),
                "df_id": 1,
                "visible": True,
                "label": selected.get("label") or "plot_source.csv",
                "row_count": selected.get("row_count", 0),
            },
        }
    }

    return {
        "selected_file": selected.get("file_path"),
        "charts": [chart_spec],
        "_dataframes": dataframe_payload,
    }


def _normalize_table_rows(table: dict[str, Any]) -> Iterable[dict[str, Any]]:
    rows = table.get("data")
    if isinstance(rows, list):
        return (row for row in rows if isinstance(row, dict))
    return []


def _extract_edgepython_dependency_outputs(plan_payload: dict, step: dict) -> dict[str, list[str]]:
    outputs = {
        "top_genes": [],
        "tests": [],
        "saved": [],
        "plots": [],
    }
    for dep_step in _iter_dependency_steps(plan_payload, step):
        for item in _coerce_step_result_items(dep_step.get("result")):
            tool_name = item.get("tool") if isinstance(item, dict) else None
            result_payload = item.get("result") if isinstance(item, dict) else None
            result_text = None
            if isinstance(result_payload, str):
                result_text = result_payload
            elif isinstance(result_payload, dict) and isinstance(result_payload.get("data"), str):
                result_text = result_payload.get("data")
            if not isinstance(tool_name, str) or not isinstance(result_text, str):
                continue
            if tool_name == "get_top_genes":
                outputs["top_genes"].append(result_text)
            elif tool_name in {"exact_test", "test_contrast"}:
                outputs["tests"].append(result_text)
            elif tool_name == "save_results":
                outputs["saved"].append(result_text)
            elif tool_name == "generate_plot":
                outputs["plots"].append(result_text)
    return outputs


def _build_qc_interpretation(plan_payload: dict, step: dict) -> str:
    de_outputs = _extract_edgepython_dependency_outputs(plan_payload, step)
    if any(de_outputs.values()):
        lines: list[str] = []
        if de_outputs["tests"]:
            lines.append(de_outputs["tests"][-1])
        if de_outputs["top_genes"]:
            if lines:
                lines.append("")
            lines.append(de_outputs["top_genes"][-1])
        if de_outputs["saved"]:
            lines.append("")
            lines.append(de_outputs["saved"][-1])
        if de_outputs["plots"]:
            lines.append(de_outputs["plots"][-1])
        return "\n".join(line for line in lines if line)

    tables = _extract_all_dependency_tables(plan_payload, step)
    if not tables:
        return "No parsed result tables were available to interpret."

    qc_table = next(
        (
            table for table in tables
            if "qc_summary" in str(table.get("file_path") or "").lower()
        ),
        None,
    )
    final_stats_table = next(
        (
            table for table in tables
            if "final_stats" in str(table.get("file_path") or "").lower()
        ),
        None,
    )

    observations: list[str] = []
    verdict = "usable"

    if qc_table:
        rows = list(_normalize_table_rows(qc_table))
        bam_row = next((row for row in rows if str(row.get("file_type") or "").lower() == "bam"), None)
        annotated_row = next((row for row in rows if "annotated" in str(row.get("file_type") or "").lower()), None)
        source_row = annotated_row or bam_row
        if source_row:
            mapped_pct = source_row.get("mapped_pct")
            n_reads = source_row.get("n_reads")
            mean_len = source_row.get("mean_len")
            if mapped_pct not in (None, ""):
                observations.append(f"Mapped reads are {float(mapped_pct):.2f}%.")
                if float(mapped_pct) < 70:
                    verdict = "not yet usable"
                elif float(mapped_pct) < 85 and verdict != "not yet usable":
                    verdict = "usable with caution"
            if n_reads not in (None, ""):
                observations.append(f"The main output contains about {int(float(n_reads)):,} reads.")
                if float(n_reads) < 500 and verdict == "usable":
                    verdict = "usable with caution"
            if mean_len not in (None, ""):
                observations.append(f"Mean read length is {float(mean_len):.2f} bases.")

    if final_stats_table:
        rows = list(_normalize_table_rows(final_stats_table))
        counts_by_category = {
            str(row.get("Category") or ""): row.get("Count")
            for row in rows
        }
        known_reads = counts_by_category.get("Reads - Known")
        novel_genes = counts_by_category.get("Novel genes discovered")
        novel_tx = counts_by_category.get("Novel transcripts discovered")
        if known_reads not in (None, ""):
            observations.append(f"Known transcript support accounts for {int(float(known_reads)):,} reads.")
        if novel_genes not in (None, "") or novel_tx not in (None, ""):
            observations.append(
                "Novel discovery is limited "
                f"({int(float(novel_genes or 0))} genes, {int(float(novel_tx or 0))} transcripts)."
            )

    if not observations:
        lead = f"Parsed {len(tables)} result table(s) and generated a plot from the most informative one."
    else:
        lead = " ".join(observations)

    return f"{lead} Overall, this run looks {verdict}."


# ---------------------------------------------------------------------------
# Step kind → tool call mapping
# ---------------------------------------------------------------------------

# Default tool calls per step kind.
# None means the step requires special handling (not a simple MCP call).
STEP_TOOL_DEFAULTS: dict[str, list[dict] | None] = {
    "LOCATE_DATA": [{"source_key": "analyzer", "tool": "list_job_files"}],
    "SEARCH_ENCODE": [{"source_key": "encode", "tool": "search_by_biosample"}],
    "DOWNLOAD_DATA": None,          # handled via existing download flow
    "SUBMIT_WORKFLOW": None,        # handled via existing submit/approval flow
    "MONITOR_WORKFLOW": [{"source_key": "launchpad", "tool": "check_nextflow_status"}],
    "PREPARE_DE_INPUT": None,       # local preprocessing into edgepython-ready inputs
    "PARSE_OUTPUT_FILE": [{"source_key": "analyzer", "tool": "parse_csv_file"}],
    "SUMMARIZE_QC": [{"source_key": "analyzer", "tool": "get_analysis_summary"}],
    "RUN_DE_ANALYSIS": None,        # multi-call edgepython pipeline
    "RUN_SCRIPT": None,             # standalone allowlisted script execution (special handling)
    "COMPARE_SAMPLES": None,        # multi-call analyzer + LLM synthesis
    "GENERATE_PLOT": None,          # uses PLOT tag system
    "WRITE_SUMMARY": None,          # uses LLM analyze_results
    "REQUEST_APPROVAL": None,       # creates APPROVAL_GATE block
    "CHECK_REMOTE_PROFILE_AUTH": None,
    "check_remote_stage": None,
    "CHECK_REMOTE_STAGE": None,
    "remote_stage": None,
    "REMOTE_STAGE": None,
    "complete_stage_only": None,
    "COMPLETE_STAGE_ONLY": None,
    "FIND_REFERENCE_CACHE": None,
    "FIND_DATA_CACHE": None,
    # New plan step kinds
    "CHECK_EXISTING": [{"source_key": "analyzer", "tool": "find_file"}],
    "RUN_DE_PIPELINE": None,        # multi-call edgepython sequence (special handling)
    "RUN_XGENEPY": [{"source_key": "xgenepy", "tool": "run_xgenepy_analysis"}],
    "PARSE_XGENEPY_OUTPUT": [{"source_key": "analyzer", "tool": "parse_xgenepy_outputs"}],
    "GENERATE_DE_PLOT": [{"source_key": "edgepython", "tool": "generate_plot"}],
    "INTERPRET_RESULTS": None,      # LLM call (special handling)
    "RECOMMEND_NEXT": None,         # LLM call (special handling)
    "ANNOTATE_RESULTS": [{"source_key": "edgepython", "tool": "annotate_genes"}],
    "SAVE_RESULTS": [{"source_key": "edgepython", "tool": "save_results"}],
    # Enrichment analysis step kinds:
    "FILTER_DE_GENES": [{"source_key": "edgepython", "tool": "filter_de_genes"}],
    "RUN_GO_ENRICHMENT": [{"source_key": "edgepython", "tool": "run_go_enrichment"}],
    "RUN_PATHWAY_ENRICHMENT": [{"source_key": "edgepython", "tool": "run_pathway_enrichment"}],
    "PLOT_ENRICHMENT": [{"source_key": "edgepython", "tool": "generate_plot",
                         "params": {"plot_type": "enrichment_bar"}}],
    "SUMMARIZE_ENRICHMENT": None,   # LLM interpretation
    # Legacy kinds (backward compat with existing WORKFLOW_PLAN):
    "copy_sample": None,
    "run": None,
    "analysis": None,
}


# ---------------------------------------------------------------------------
# MCP tool call helper
# ---------------------------------------------------------------------------

async def _call_mcp_tool(source_key: str, tool_name: str, params: dict) -> dict:
    """Make a single MCP tool call and return the result dict."""
    url = get_service_url(source_key)
    client = MCPHttpClient(name=source_key, base_url=url)
    try:
        await client.connect()
        result = await client.call_tool(tool_name, **params)
        return result if isinstance(result, dict) else {"data": result}
    except Exception as e:
        logger.error("MCP tool call failed", source=source_key, tool=tool_name, error=str(e))
        return {"error": str(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------

async def execute_step(
    session,
    workflow_block: "ProjectBlock",
    step_id: str,
    *,
    plan_payload: dict,
    engine: "AgentEngine | None" = None,
    user: Any = None,
    project_id: str = "",
) -> StepResult:
    """
    Execute a single plan step.

    1. Resolve tool calls for the step kind
    2. Execute each tool call via MCP
    3. Return a StepResult with data or error
    """
    from cortex.llm_validators import get_block_payload

    # Find the step in the plan
    step = None
    for s in plan_payload.get("steps", []):
        if s.get("id") == step_id:
            step = s
            break
    if not step:
        return StepResult(success=False, error=f"Step {step_id} not found in plan")

    kind = step.get("kind", "")
    logger.info("Executing plan step", step_id=step_id, kind=kind, title=step.get("title"))

    project_dir_path = None
    if project_id and user is not None:
        try:
            from cortex.db_helpers import _resolve_project_dir

            project_dir_path = _resolve_project_dir(session, user, project_id)
        except Exception:
            logger.debug("Failed to resolve project directory for plan execution", exc_info=True)

    # Mark step as running
    step["status"] = "RUNNING"
    step["started_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    _persist_step_update(session, workflow_block, plan_payload)

    # Resolve tool calls: use step-specific calls, or fall back to defaults
    explicit_tool_calls = bool(step.get("tool_calls"))
    tool_calls = step.get("tool_calls") or []
    if not tool_calls:
        defaults = STEP_TOOL_DEFAULTS.get(kind)
        if defaults:
            tool_calls = defaults

    # Special handling for kinds that are not simple MCP calls
    if kind == "REQUEST_APPROVAL":
        # Pause execution — the caller handles creating the approval gate
        step["status"] = "WAITING_APPROVAL"
        plan_payload["status"] = "WAITING_APPROVAL"
        _persist_step_update(session, workflow_block, plan_payload)
        return StepResult(success=True, data={"action": "approval_required"})

    if kind == "VALIDATE_INPUTS":
        input_path = (
            plan_payload.get("input_directory")
            or plan_payload.get("source_path")
            or plan_payload.get("work_dir")
        )
        if not input_path:
            step["status"] = "FAILED"
            step["error"] = "No input path is available for validation."
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, error=step["error"])

        candidate = Path(str(input_path)).expanduser()
        if not candidate.exists():
            step["status"] = "FAILED"
            step["error"] = f"Input path does not exist: {candidate}"
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, error=step["error"])

        file_count = 1 if candidate.is_file() else sum(1 for path in candidate.rglob("*") if path.is_file())
        if file_count == 0:
            step["status"] = "FAILED"
            step["error"] = f"Input path contains no files: {candidate}"
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, error=step["error"])

        step["status"] = "COMPLETED"
        step["result"] = {
            "input_path": str(candidate),
            "is_directory": candidate.is_dir(),
            "file_count": file_count,
        }
        step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _persist_step_update(session, workflow_block, plan_payload)
        return StepResult(success=True, data=step["result"])

    if kind == "CHECK_REMOTE_PROFILE_AUTH":
        step["status"] = "RUNNING"
        _persist_step_update(session, workflow_block, plan_payload)
        return StepResult(success=True, data={"action": "special_handling", "kind": kind})

    if kind == "PREPARE_DE_INPUT":
        from cortex.dataframe_actions import _find_dataframe_payload
        from cortex.de_prep import prepare_de_inputs
        from cortex.models import ProjectBlock

        try:
            source: str | dict[str, Any] | None = step.get("counts_path") or None
            df_id = step.get("df_id")
            if df_id not in (None, ""):
                history_blocks = session.execute(
                    select(ProjectBlock)
                    .where(ProjectBlock.project_id == project_id)
                    .where(ProjectBlock.type == "AGENT_PLAN")
                    .order_by(ProjectBlock.seq.asc())
                ).scalars().all()
                source = _find_dataframe_payload(int(df_id), history_blocks)

            default_output_dir = (
                str(project_dir_path / "de_inputs")
                if project_dir_path is not None
                else step.get("output_dir")
                or str(Path(step.get("work_dir") or ".") / "de_inputs")
            )
            prepared = prepare_de_inputs(
                source,
                output_dir=step.get("output_dir") or default_output_dir,
                group_a_label=str(step.get("group_a_label") or "group1"),
                group_a_samples=list(step.get("group_a_samples") or []),
                group_b_label=str(step.get("group_b_label") or "group2"),
                group_b_samples=list(step.get("group_b_samples") or []),
                level=str(step.get("level") or "gene"),
                work_dir=step.get("work_dir") or plan_payload.get("work_dir"),
            )
            _apply_prepared_de_inputs(plan_payload, step_id, prepared)
            step["status"] = "COMPLETED"
            step["result"] = prepared
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=True, data=prepared)
        except Exception as exc:
            step["status"] = "FAILED"
            step["error"] = str(exc)
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, error=str(exc))

    if kind == "PARSE_OUTPUT_FILE" and not explicit_tool_calls:
        tool_calls = _derive_parse_tool_calls(plan_payload, step)
        if not tool_calls:
            step["status"] = "FAILED"
            step["error"] = "Could not determine result files to parse from prior locate step output."
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, error=step["error"])

    if kind == "GENERATE_PLOT":
        plot_result = _build_workflow_plot_payload(plan_payload, step)
        if not plot_result:
            step["status"] = "FAILED"
            step["error"] = "Could not derive a plot from prior parsed results."
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, error=step["error"])

        step["status"] = "COMPLETED"
        step["result"] = plot_result
        step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _persist_step_update(session, workflow_block, plan_payload)
        return StepResult(success=True, data=plot_result)

    if kind in {"INTERPRET_RESULTS", "WRITE_SUMMARY", "RECOMMEND_NEXT"}:
        summary_text = _build_qc_interpretation(plan_payload, step)
        step["status"] = "COMPLETED"
        step["result"] = {"markdown": summary_text}
        step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _persist_step_update(session, workflow_block, plan_payload)
        return StepResult(success=True, data=step["result"])

    if kind in ("SUBMIT_WORKFLOW", "DOWNLOAD_DATA", "RUN_DE_ANALYSIS",
                "COMPARE_SAMPLES", "RUN_SCRIPT"):
        # These kinds need special orchestration — mark as needing attention
        # and return a marker so the caller can handle them
        step["status"] = "WAITING_APPROVAL" if step.get("requires_approval") else "PENDING"
        _persist_step_update(session, workflow_block, plan_payload)
        return StepResult(
            success=True,
            data={"action": "special_handling", "kind": kind},
        )

    if kind in ("copy_sample", "run", "analysis"):
        # Legacy kinds: handled by existing workflow code
        return StepResult(
            success=True,
            data={"action": "legacy_handling", "kind": kind},
        )

    # Execute MCP tool calls
    if not tool_calls:
        step["status"] = "COMPLETED"
        step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _persist_step_update(session, workflow_block, plan_payload)
        return StepResult(success=True, data={"note": "No tool calls for this step"})

    all_results = []
    for tc in tool_calls:
        source_key = tc.get("source_key", "")
        tool_name = tc.get("tool", "")
        params = dict(tc.get("params", {}))

        if not source_key or not tool_name:
            logger.warning("Skipping empty tool call in step", step_id=step_id)
            continue

        if source_key == "edgepython" and project_dir_path is not None:
            from cortex.tool_dispatch import _inject_edgepython_output_path

            _inject_edgepython_output_path(tool_name, params, project_dir_path)

        result = await _call_mcp_tool(source_key, tool_name, params)
        all_results.append({
            "tool": tool_name,
            "source_key": source_key,
            "result": result,
        })

        # Check for errors
        if isinstance(result, dict) and "error" in result:
            step["status"] = "FAILED"
            step["error"] = result["error"]
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, data={"results": all_results}, error=result["error"])

    # All tool calls succeeded
    step["status"] = "COMPLETED"
    step["result"] = all_results[0]["result"] if len(all_results) == 1 else all_results
    step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    _persist_step_update(session, workflow_block, plan_payload)

    return StepResult(success=True, data={"results": all_results})


# ---------------------------------------------------------------------------
# Plan execution loop
# ---------------------------------------------------------------------------

async def execute_plan(
    session,
    workflow_block: "ProjectBlock",
    *,
    engine: "AgentEngine | None" = None,
    user: Any = None,
    project_id: str = "",
    request_id: str | None = None,
    emit_progress=None,
) -> None:
    """
    Walk through plan steps in order, respecting dependencies and approval requirements.

    Stops when:
    - A step requires approval (creates an APPROVAL_GATE)
    - A step fails (triggers replanning)
    - All steps complete
    """
    from cortex.llm_validators import get_block_payload
    from cortex.plan_replanner import replan_on_failure, replan_with_new_info

    plan_payload = get_block_payload(workflow_block)

    try:
        validate_plan(
            plan_payload,
            allowed_kinds=set(STEP_TOOL_DEFAULTS).union(_SAFE_STEP_KINDS).union(_APPROVAL_STEP_KINDS),
            safe_step_kinds=_SAFE_STEP_KINDS,
            approval_step_kinds=_APPROVAL_STEP_KINDS,
            expected_project_id=getattr(workflow_block, "project_id", None),
        )
    except PlanValidationError as err:
        logger.warning("Plan validation failed before execution", issues=err.to_dict()["issues"])
        plan_payload["status"] = "FAILED"
        plan_payload["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        plan_payload["validation_error"] = err.to_dict()
        _persist_step_update(session, workflow_block, plan_payload)
        if emit_progress:
            emit_progress(request_id, "error", "Execution plan failed validation before dispatch.")
        return

    # Mark plan as running
    plan_payload["status"] = "RUNNING"
    plan_payload.setdefault("started_at", datetime.datetime.utcnow().isoformat() + "Z")
    _persist_step_update(session, workflow_block, plan_payload)

    steps = plan_payload.get("steps", [])
    if not steps:
        plan_payload["status"] = "COMPLETED"
        plan_payload["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _persist_step_update(session, workflow_block, plan_payload)
        return

    while True:
        plan_payload = get_block_payload(workflow_block)
        steps = plan_payload.get("steps", [])

        if not steps:
            break

        ordered_ready = _ordered_ready_steps(steps)
        if not ordered_ready:
            logger.info("No ready steps available, stopping", current_status=plan_payload.get("status"))
            break

        parallel_ready = [
            s for s in ordered_ready
            if s.get("kind") in _PARALLEL_BATCH_SAFE_KINDS and not s.get("requires_approval")
        ]

        if parallel_ready:
            batch_ordered = _ordered_steps(parallel_ready)
            batch_ids = [s.get("id") for s in batch_ordered if isinstance(s.get("id"), str)]
            if not batch_ids:
                logger.warning("Parallel batch had no valid step ids")
                break

            plan_payload["current_step_id"] = batch_ids[0]
            now = datetime.datetime.utcnow().isoformat() + "Z"
            for sid in batch_ids:
                s = _find_step(plan_payload.get("steps", []), sid)
                if not s:
                    continue
                s["status"] = "RUNNING"
                s["started_at"] = now
            _persist_step_update(session, workflow_block, plan_payload)

            if emit_progress:
                emit_progress(request_id, "executing",
                              f"Running batch: {', '.join(batch_ids)}")

            batch_results = await asyncio.gather(
                *[_execute_parallel_safe_step(step) for step in batch_ordered],
                return_exceptions=True,
            )

            plan_payload = get_block_payload(workflow_block)
            failure_step_id: str | None = None
            failure_error: str | None = None

            for step, raw_result in zip(batch_ordered, batch_results, strict=False):
                step_id = step.get("id")
                if not isinstance(step_id, str):
                    continue
                current_step = _find_step(plan_payload.get("steps", []), step_id)
                if not current_step:
                    continue

                completed_at = datetime.datetime.utcnow().isoformat() + "Z"
                if isinstance(raw_result, Exception):
                    current_step["status"] = "FAILED"
                    current_step["error"] = str(raw_result)
                    current_step["completed_at"] = completed_at
                    if failure_step_id is None:
                        failure_step_id = step_id
                        failure_error = str(raw_result)
                else:
                    result = raw_result
                    if not result.success:
                        current_step["status"] = "FAILED"
                        current_step["error"] = result.error
                        current_step["completed_at"] = completed_at
                        if failure_step_id is None:
                            failure_step_id = step_id
                            failure_error = result.error
                    else:
                        current_step["status"] = "COMPLETED"
                        current_step["result"] = result.data.get("results") or result.data
                        current_step["completed_at"] = completed_at
                        # Auto-capture successful step to memory
                        _auto_capture_step_memory(
                            session, workflow_block, current_step, plan_payload,
                            user=user, project_id=project_id,
                        )

                # Persist deterministic per-step batch outcomes in plan order.
                _persist_step_update(session, workflow_block, plan_payload)

                if failure_step_id is None and not isinstance(raw_result, Exception):
                    action = raw_result.data.get("action")
                    if action in ("approval_required", "special_handling"):
                        return

                    updated = replan_with_new_info(
                        session, workflow_block, step_id, raw_result.data,
                    )
                    if updated:
                        plan_payload = updated

            if failure_step_id is not None:
                logger.warning("Plan step failed", step_id=failure_step_id, error=failure_error)
                plan_payload = replan_on_failure(session, workflow_block, failure_step_id)
                plan_payload["status"] = "FAILED"
                plan_payload["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
                _persist_step_update(session, workflow_block, plan_payload)
                return

            continue

        step = ordered_ready[0]
        step_id = step.get("id")
        status = step.get("status", "PENDING")
        if not isinstance(step_id, str):
            logger.warning("Ready step missing id; skipping")
            continue

        # Check if this step needs approval and hasn't been approved yet
        if step.get("requires_approval") and status != "RUNNING":
            if not should_auto_execute(step):
                step["status"] = "WAITING_APPROVAL"
                plan_payload["status"] = "WAITING_APPROVAL"
                plan_payload["current_step_id"] = step_id
                _persist_step_update(session, workflow_block, plan_payload)
                if emit_progress:
                    emit_progress(request_id, "approval",
                                  f"Waiting for approval: {step.get('title', 'step')}")
                return

        if emit_progress:
            emit_progress(request_id, "executing",
                          f"Running: {step.get('title', 'step')}")

        plan_payload["current_step_id"] = step_id

        result = await execute_step(
            session, workflow_block, step_id,
            plan_payload=plan_payload,
            engine=engine, user=user, project_id=project_id,
        )

        plan_payload = get_block_payload(workflow_block)

        if not result.success:
            logger.warning("Plan step failed", step_id=step_id, error=result.error)
            plan_payload = replan_on_failure(session, workflow_block, step_id)
            plan_payload["status"] = "FAILED"
            plan_payload["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return

        # Auto-capture successful step to memory
        _auto_capture_step_memory(
            session, workflow_block, step, plan_payload,
            user=user, project_id=project_id,
        )

        if result.data.get("action") in ("approval_required", "special_handling"):
            return

        updated = replan_with_new_info(
            session, workflow_block, step_id, result.data,
        )
        if updated:
            plan_payload = updated

    # Check if all steps completed
    plan_payload = get_block_payload(workflow_block)
    all_done = all(
        s.get("status") in ("COMPLETED", "SKIPPED", "CANCELLED")
        for s in plan_payload.get("steps", [])
    )
    if all_done:
        plan_payload["status"] = "COMPLETED"
        plan_payload["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _persist_step_update(session, workflow_block, plan_payload)
        logger.info("Plan completed", title=plan_payload.get("title"))


# ---------------------------------------------------------------------------
# Memory auto-capture hook
# ---------------------------------------------------------------------------

def _auto_capture_step_memory(
    session,
    workflow_block: "ProjectBlock",
    step: dict,
    plan_payload: dict,
    *,
    user: Any = None,
    project_id: str = "",
) -> None:
    """Best-effort auto-capture of a completed step to the memory system."""
    try:
        from cortex.memory_service import auto_capture_step
        user_id = getattr(user, "id", None) or getattr(workflow_block, "owner_id", "")
        pid = project_id or getattr(workflow_block, "project_id", "")
        if user_id and pid:
            auto_capture_step(
                session,
                user_id=user_id,
                project_id=pid,
                step=step,
                plan_payload=plan_payload,
                block_id=getattr(workflow_block, "id", None),
            )
    except Exception:
        logger.debug("Memory auto-capture failed for step", exc_info=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_step(steps: list[dict], step_id: str) -> dict | None:
    for s in steps:
        if s.get("id") == step_id:
            return s
    return None


def _apply_prepared_de_inputs(plan_payload: dict, completed_step_id: str, prepared: dict[str, Any]) -> None:
    for step in plan_payload.get("steps", []):
        if (
            completed_step_id not in (step.get("depends_on") or [])
            and step.get("kind") not in {"SAVE_RESULTS", "GENERATE_DE_PLOT"}
        ):
            continue

        if step.get("kind") == "RUN_DE_PIPELINE":
            for tool_call in step.get("tool_calls", []):
                params = tool_call.get("params") if isinstance(tool_call.get("params"), dict) else None
                if not params:
                    continue
                if tool_call.get("tool") == "load_data":
                    params["counts_path"] = prepared["counts_path"]
                    params["sample_info_path"] = prepared["sample_info_path"]
                    params["group_column"] = prepared["group_column"]
                elif tool_call.get("tool") == "exact_test":
                    params["pair"] = prepared["pair"]
                    params["name"] = prepared["result_name"]
                elif tool_call.get("tool") == "test_contrast":
                    params["contrast"] = prepared["contrast"]
                    params["name"] = prepared["result_name"]
                elif tool_call.get("tool") == "get_top_genes":
                    params["name"] = prepared["result_name"]

        if step.get("kind") == "SAVE_RESULTS":
            for tool_call in step.get("tool_calls", []):
                params = tool_call.get("params") if isinstance(tool_call.get("params"), dict) else None
                if params and tool_call.get("tool") == "save_results":
                    params["name"] = prepared["result_name"]

        if step.get("kind") == "GENERATE_DE_PLOT":
            for tool_call in step.get("tool_calls", []):
                params = tool_call.get("params") if isinstance(tool_call.get("params"), dict) else None
                if params and tool_call.get("tool") == "generate_plot":
                    params["result_name"] = prepared["result_name"]


def _ordered_steps(steps: list[dict]) -> list[dict]:
    return sorted(
        steps,
        key=lambda s: (
            int(s.get("order_index", 10**9)) if isinstance(s.get("order_index"), int) else 10**9,
            str(s.get("id") or ""),
        ),
    )


def _ordered_ready_steps(steps: list[dict]) -> list[dict]:
    by_id = {
        s.get("id"): s
        for s in steps
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    }
    ready: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("status", "PENDING") != "PENDING":
            continue
        depends_on = step.get("depends_on")
        if not isinstance(depends_on, list):
            continue
        deps_met = True
        for dep in depends_on:
            dep_step = by_id.get(dep)
            if dep_step and dep_step.get("status") != "COMPLETED":
                deps_met = False
                break
        if deps_met:
            ready.append(step)
    return _ordered_steps(ready)


async def _execute_parallel_safe_step(step: dict) -> StepResult:
    """Execute a parallel-safe step without mutating shared payload/session."""
    kind = str(step.get("kind", ""))
    tool_calls = step.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        return StepResult(success=False, error="Invalid tool_calls for step")

    if not tool_calls:
        defaults = STEP_TOOL_DEFAULTS.get(kind)
        if defaults:
            tool_calls = defaults

    if not tool_calls:
        return StepResult(success=True, data={"note": "No tool calls for this step"})

    all_results = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            return StepResult(success=False, error="Invalid tool call entry")
        source_key = tc.get("source_key", "")
        tool_name = tc.get("tool", "")
        params = dict(tc.get("params", {}))

        if not source_key or not tool_name:
            return StepResult(success=False, error="Empty source_key or tool in tool call")

        result = await _call_mcp_tool(source_key, tool_name, params)
        all_results.append({
            "tool": tool_name,
            "source_key": source_key,
            "result": result,
        })
        if isinstance(result, dict) and "error" in result:
            return StepResult(success=False, data={"results": all_results}, error=result["error"])

    return StepResult(success=True, data={"results": all_results})


def _persist_step_update(session, workflow_block: "ProjectBlock", payload: dict) -> None:
    """Persist updated plan payload and sync tasks."""
    from cortex.task_service import sync_project_tasks

    workflow_block.payload_json = json.dumps(payload)

    # Derive block-level status from plan status
    plan_status = payload.get("status", "PENDING")
    if plan_status in ("COMPLETED", "FAILED"):
        workflow_block.status = "DONE" if plan_status == "COMPLETED" else "FAILED"
    elif plan_status == "WAITING_APPROVAL":
        workflow_block.status = "PENDING"
    else:
        workflow_block.status = plan_status

    session.commit()
    session.refresh(workflow_block)
    sync_project_tasks(session, workflow_block.project_id)
