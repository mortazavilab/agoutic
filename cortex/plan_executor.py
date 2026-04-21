"""
Deterministic plan step executor.

Maps step kinds to concrete tool call sequences.
The LLM does NOT improvise execution — code controls it.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common import MCPHttpClient
from common.logging_config import get_logger
from common.workflow_paths import next_workflow_number, workflow_dir_name
from cortex.skill_manifest import check_service_availability
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


def _configured_service_keys() -> set[str]:
    from atlas.config import CONSORTIUM_REGISTRY
    from cortex.config import SERVICE_REGISTRY

    return set(SERVICE_REGISTRY) | set(CONSORTIUM_REGISTRY)


def _step_service_gate_error(step: dict[str, Any], source_key: str) -> str | None:
    skill_key = step.get("skill_key")
    if not isinstance(skill_key, str) or not skill_key:
        return None

    services_ok, missing_services = check_service_availability(skill_key, _configured_service_keys())
    if services_ok:
        return None
    if source_key and source_key not in missing_services:
        return None
    return (
        f"Required service(s) unavailable for skill '{skill_key}': "
        f"{', '.join(missing_services)}"
    )


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


def _reconcile_parse_candidate_priority(file_info: dict) -> tuple[int, str]:
    name = str(file_info.get("name") or file_info.get("path") or "").lower()
    path = str(file_info.get("path") or file_info.get("name") or "")
    if name.endswith("_abundance.tsv") or name.endswith("_abundance.csv"):
        return (0, path)
    if name.endswith("_novelty_by_sample.csv"):
        return (1, path)
    if name.endswith(".inputs.tsv"):
        return (2, path)
    if name.endswith(".mapping.tsv"):
        return (3, path)
    return (20, path)


def _extract_located_files(step_result: Any) -> tuple[str | None, list[dict[str, Any]]]:
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

    return work_dir, file_entries


def _extract_located_tabular_files(
    step_result: Any,
    *,
    priority_fn=_parse_candidate_priority,
) -> tuple[str | None, list[str]]:
    work_dir, file_entries = _extract_located_files(step_result)

    def _entry_extension(entry: dict[str, Any]) -> str:
        explicit_extension = str(entry.get("extension") or "").lower()
        if explicit_extension:
            return explicit_extension
        candidate = str(entry.get("path") or entry.get("name") or "")
        if candidate.endswith("/"):
            return ""
        return Path(candidate).suffix.lower()

    tabular = [
        entry for entry in file_entries
        if _entry_extension(entry) in {".csv", ".tsv"}
    ]
    ordered = sorted(tabular, key=priority_fn)
    file_paths = [str(entry.get("path")) for entry in ordered if entry.get("path")]
    if len(file_paths) > 5:
        file_paths = file_paths[:5]
    return work_dir, file_paths


def _derive_parse_tool_calls(plan_payload: dict, step: dict) -> list[dict]:
    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
    dependency_ids = step.get("depends_on") if isinstance(step.get("depends_on"), list) else []
    priority_fn = (
        _reconcile_parse_candidate_priority
        if plan_payload.get("plan_type") == "reconcile_bams"
        else _parse_candidate_priority
    )

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
        locate_work_dir, located_files = _extract_located_tabular_files(
            locate_step.get("result"),
            priority_fn=priority_fn,
        )
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


def _resolve_latest_completed_run_script_work_directory(
    plan_payload: dict,
    step: dict,
) -> str | None:
    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
    latest_work_dir: str | None = None
    for candidate in steps:
        if candidate is step:
            break
        if not isinstance(candidate, dict):
            continue
        if candidate.get("kind") != "RUN_SCRIPT" or candidate.get("status") != "COMPLETED":
            continue
        resolved = _extract_step_work_directory(candidate)
        if resolved:
            latest_work_dir = resolved
    return latest_work_dir


async def _derive_runtime_parse_tool_calls(plan_payload: dict, step: dict) -> list[dict]:
    priority_fn = (
        _reconcile_parse_candidate_priority
        if plan_payload.get("plan_type") == "reconcile_bams"
        else _parse_candidate_priority
    )
    runtime_work_dir = (
        _resolve_runtime_work_directory(plan_payload, step)
        or _resolve_latest_completed_run_script_work_directory(plan_payload, step)
    )
    if not runtime_work_dir:
        return []

    locate_result = await _call_mcp_tool(
        "analyzer",
        "list_job_files",
        {
            "work_dir": runtime_work_dir,
            "max_depth": 1,
            "allow_missing": True,
        },
    )
    if not isinstance(locate_result, dict) or locate_result.get("error"):
        return []

    located_work_dir, file_paths = _extract_located_tabular_files(
        locate_result,
        priority_fn=priority_fn,
    )
    if not file_paths:
        return []

    return [
        {
            "source_key": "analyzer",
            "tool": "parse_csv_file",
            "params": {
                "file_path": file_path,
                "work_dir": located_work_dir or runtime_work_dir,
            },
        }
        for file_path in file_paths
    ]


def _extract_step_work_directory(step: dict[str, Any]) -> str | None:
    for key in ("work_directory", "work_dir", "output_directory"):
        candidate = step.get(key)
        if isinstance(candidate, str) and Path(candidate).is_absolute():
            return candidate

    for item in _coerce_step_result_items(step.get("result")):
        payload = item.get("result") if "result" in item else item
        if not isinstance(payload, dict):
            continue

        for key in ("work_directory", "work_dir", "output_directory"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and Path(candidate).is_absolute():
                return candidate

        workflow_payload = payload.get("workflow")
        if isinstance(workflow_payload, dict):
            for key in ("directory", "output_directory"):
                candidate = workflow_payload.get(key)
                if isinstance(candidate, str) and Path(candidate).is_absolute():
                    return candidate

    return None


def _resolve_runtime_work_directory(plan_payload: dict, step: dict) -> str | None:
    for dep_step in _iter_dependency_steps(plan_payload, step):
        if dep_step.get("kind") != "RUN_SCRIPT":
            continue
        candidate = _extract_step_work_directory(dep_step)
        if candidate:
            return candidate
    return None


def _apply_runtime_tool_call_overrides(
    plan_payload: dict,
    step: dict,
    tool_calls: list[dict],
) -> list[dict]:
    runtime_work_dir = _resolve_runtime_work_directory(plan_payload, step)
    de_workflow_dir = _resolve_de_storage_root(plan_payload, step, None)
    plan_work_dir = plan_payload.get("work_dir")
    if not isinstance(plan_work_dir, str) or not Path(plan_work_dir).is_absolute():
        plan_work_dir = None

    overridden: list[dict] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            overridden.append(tool_call)
            continue

        updated = dict(tool_call)
        params = dict(updated.get("params") or {})
        if (
            step.get("kind") == "LOCATE_DATA"
            and runtime_work_dir
            and updated.get("source_key") == "analyzer"
            and updated.get("tool") == "list_job_files"
        ):
            params["work_dir"] = runtime_work_dir
        if (
            step.get("kind") == "CHECK_EXISTING"
            and updated.get("source_key") == "analyzer"
            and updated.get("tool") == "find_file"
        ):
            if de_workflow_dir is not None:
                params["work_dir"] = str(de_workflow_dir)
            elif not params.get("work_dir") and plan_work_dir:
                params["work_dir"] = plan_work_dir
        updated["params"] = params
        overridden.append(updated)

    return overridden


def _resolve_de_storage_root(
    plan_payload: dict,
    step: dict,
    project_dir_path: Path | None,
) -> Path | None:
    for candidate in (
        step.get("de_work_dir"),
        plan_payload.get("de_work_dir"),
        step.get("work_dir"),
        plan_payload.get("work_dir"),
    ):
        if isinstance(candidate, str) and Path(candidate).is_absolute():
            return Path(candidate)
    return project_dir_path


def _is_check_existing_not_found_result(kind: str, tool_name: str, result: Any) -> bool:
    if kind != "CHECK_EXISTING" or tool_name != "find_file" or not isinstance(result, dict):
        return False
    error_text = str(result.get("error") or "")
    return error_text.startswith("File not found") or error_text == "Work directory not found"


def _ensure_de_workflow_context(
    plan_payload: dict,
    project_dir_path: Path | None,
) -> Path | None:
    if plan_payload.get("plan_type") != "run_de_pipeline":
        return None

    existing_work_dir = plan_payload.get("de_work_dir")
    if isinstance(existing_work_dir, str) and Path(existing_work_dir).is_absolute():
        de_workflow_dir = Path(existing_work_dir)
        de_workflow_dir.mkdir(parents=True, exist_ok=True)
    else:
        if project_dir_path is None:
            return None
        project_dir_path.mkdir(parents=True, exist_ok=True)
        while True:
            workflow_index = next_workflow_number(project_dir_path)
            de_workflow_dir = project_dir_path / workflow_dir_name(workflow_index)
            try:
                de_workflow_dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                continue
        plan_payload["de_workflow_index"] = workflow_index
        plan_payload["de_workflow_alias"] = de_workflow_dir.name
        plan_payload["de_work_dir"] = str(de_workflow_dir)

    plan_payload.setdefault("de_workflow_alias", de_workflow_dir.name)
    plan_payload.setdefault("de_work_dir", str(de_workflow_dir))

    for candidate in plan_payload.get("steps", []):
        if not isinstance(candidate, dict):
            continue
        kind = candidate.get("kind")
        if kind == "PREPARE_DE_INPUT":
            candidate["de_work_dir"] = str(de_workflow_dir)
            candidate["output_dir"] = str(de_workflow_dir / "de_inputs")
        elif kind == "CHECK_EXISTING":
            candidate["de_work_dir"] = str(de_workflow_dir)
        elif kind in {
            "RUN_DE_PIPELINE",
            "SAVE_RESULTS",
            "ANNOTATE_RESULTS",
            "GENERATE_DE_PLOT",
            "INTERPRET_RESULTS",
            "WRITE_SUMMARY",
        }:
            candidate["de_work_dir"] = str(de_workflow_dir)

    return de_workflow_dir


def _normalize_check_existing_result(params: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized.pop("error", None)
    normalized["success"] = True
    normalized["file_count"] = 0
    normalized["paths"] = []
    normalized["primary_path"] = None
    normalized["search_term"] = normalized.get("search_term") or params.get("file_name") or ""
    normalized["not_found"] = True
    if params.get("work_dir") and not normalized.get("work_dir"):
        normalized["work_dir"] = params.get("work_dir")
    return normalized


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


def _iter_prior_steps(plan_payload: dict, step: dict) -> list[dict]:
    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
    if not isinstance(steps, list):
        return []

    step_index = None
    for idx, candidate in enumerate(steps):
        if candidate is step:
            step_index = idx
            break

    if step_index is None and isinstance(step.get("id"), str):
        for idx, candidate in enumerate(steps):
            if isinstance(candidate, dict) and candidate.get("id") == step.get("id"):
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


def _has_dependency_kind(plan_payload: dict, step: dict, kind: str) -> bool:
    return any(dep_step.get("kind") == kind for dep_step in _iter_dependency_steps(plan_payload, step))


def _extract_latest_located_files(
    plan_payload: dict,
    step: dict,
    *,
    prefer_dependency_kind: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
    candidates: list[tuple[str | None, list[dict[str, Any]]]] = []

    for candidate in steps:
        if candidate is step:
            break
        if not isinstance(candidate, dict):
            continue
        if candidate.get("kind") != "LOCATE_DATA" or candidate.get("status") != "COMPLETED":
            continue
        if prefer_dependency_kind and not _has_dependency_kind(plan_payload, candidate, prefer_dependency_kind):
            continue
        work_dir, files = _extract_located_files(candidate.get("result"))
        if files:
            candidates.append((work_dir, files))

    if candidates:
        return candidates[-1]
    if prefer_dependency_kind:
        return _extract_latest_located_files(plan_payload, step)
    return None, []


def _format_file_list(names: list[str], *, limit: int = 3) -> str:
    labels = [Path(name).name or name for name in names[:limit]]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    summary = ", ".join(labels[:-1])
    return f"{summary}, and {labels[-1]}"


def _build_reconcile_interpretation(plan_payload: dict, step: dict) -> str:
    work_dir, located_files = _extract_latest_located_files(
        plan_payload,
        step,
        prefer_dependency_kind="RUN_SCRIPT",
    )
    output_files = []
    for entry in located_files:
        path = str(entry.get("path") or "")
        name = str(entry.get("name") or path)
        if not path or path.endswith("/") or name.endswith("/"):
            continue
        output_files.append(path)

    abundance_files = [path for path in output_files if path.lower().endswith(("_abundance.tsv", "_abundance.csv"))]
    novelty_files = [path for path in output_files if path.lower().endswith("_novelty_by_sample.csv")]
    manifest_files = [path for path in output_files if path.lower().endswith(".inputs.tsv")]
    mapping_files = [path for path in output_files if path.lower().endswith(".mapping.tsv")]
    bam_files = [path for path in output_files if path.lower().endswith(".bam")]
    gtf_files = [path for path in output_files if path.lower().endswith(".gtf")]
    report_files = [path for path in output_files if path.lower().endswith("_summary.txt")]

    runtime_work_dir = (
        _resolve_runtime_work_directory(plan_payload, step)
        or _resolve_latest_completed_run_script_work_directory(plan_payload, step)
    )
    if runtime_work_dir and (not work_dir or not output_files):
        work_dir = runtime_work_dir

    observations: list[str] = []
    workflow_name = Path(work_dir).name if work_dir else ""
    if workflow_name:
        observations.append(f"Reconcile outputs were written to {workflow_name}.")
    if output_files:
        observations.append(f"Located {len(output_files)} top-level output file(s).")

    key_tables: list[str] = []
    if abundance_files:
        key_tables.append(abundance_files[0])
    if novelty_files:
        key_tables.append(novelty_files[0])
    if manifest_files:
        key_tables.append(manifest_files[0])
    if key_tables:
        observations.append(f"Key tables: {_format_file_list(key_tables, limit=3)}.")

    if bam_files:
        observations.append(
            f"Generated {len(bam_files)} reconciled BAM file(s): {_format_file_list(bam_files, limit=3)}."
        )
    if mapping_files:
        observations.append(f"Produced {len(mapping_files)} transcript remapping table(s).")
    if gtf_files:
        observations.append(f"Merged annotation written to {Path(gtf_files[0]).name}.")
    if report_files:
        observations.append(f"Summary report: {Path(report_files[0]).name}.")

    if not observations:
        tables = _extract_all_dependency_tables(plan_payload, step)
        if not tables:
            return "No reconcile outputs were available to summarize."
        labels = [str(table.get("label") or "results") for table in tables[:4]]
        extra_count = max(0, len(tables) - len(labels))
        label_summary = ", ".join(labels)
        if extra_count:
            label_summary = f"{label_summary}, and {extra_count} more"
        return f"Parsed {len(tables)} reconcile result table(s): {label_summary}."

    return " ".join(observations)


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


def _extract_text_result(result_payload: Any) -> str | None:
    if isinstance(result_payload, str):
        text = result_payload.strip()
        return text or None
    if isinstance(result_payload, dict) and isinstance(result_payload.get("data"), str):
        text = str(result_payload.get("data") or "").strip()
        return text or None
    return None


def _extract_de_comparison_context(plan_payload: dict, step: dict) -> dict[str, Any]:
    context: dict[str, Any] = {
        "group_a_label": "",
        "group_a_samples": [],
        "group_b_label": "",
        "group_b_samples": [],
        "result_name": "",
        "level": "",
        "source_label": "",
        "source_work_dir": "",
        "source_workflow": "",
        "output_work_dir": "",
        "output_workflow": "",
    }

    prior_steps = _iter_prior_steps(plan_payload, step)

    for dep_step in reversed(prior_steps):
        if dep_step.get("kind") != "PREPARE_DE_INPUT":
            continue
        for candidate in (dep_step.get("result"), dep_step):
            if not isinstance(candidate, dict):
                continue
            if not context["group_a_label"] and candidate.get("group_a_label"):
                context["group_a_label"] = str(candidate.get("group_a_label") or "")
            if not context["group_a_samples"] and isinstance(candidate.get("group_a_samples"), list):
                context["group_a_samples"] = [str(item) for item in candidate.get("group_a_samples") or []]
            if not context["group_b_label"] and candidate.get("group_b_label"):
                context["group_b_label"] = str(candidate.get("group_b_label") or "")
            if not context["group_b_samples"] and isinstance(candidate.get("group_b_samples"), list):
                context["group_b_samples"] = [str(item) for item in candidate.get("group_b_samples") or []]
            if not context["result_name"] and candidate.get("result_name"):
                context["result_name"] = str(candidate.get("result_name") or "")
            if not context["level"] and candidate.get("level"):
                context["level"] = str(candidate.get("level") or "")
            if not context["source_label"] and candidate.get("source_label"):
                context["source_label"] = str(candidate.get("source_label") or "")
        break

    source_work_dir = plan_payload.get("work_dir")
    if isinstance(source_work_dir, str) and source_work_dir:
        context["source_work_dir"] = source_work_dir
        context["source_workflow"] = Path(source_work_dir).name

    output_work_dir = plan_payload.get("de_work_dir") or plan_payload.get("work_dir")
    if isinstance(output_work_dir, str) and output_work_dir:
        context["output_work_dir"] = output_work_dir
        context["output_workflow"] = Path(output_work_dir).name

    if not context["group_a_label"] or not context["group_b_label"]:
        for dep_step in reversed(prior_steps):
            if dep_step.get("kind") != "RUN_DE_PIPELINE":
                continue
            for tool_call in dep_step.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                params = tool_call.get("params") if isinstance(tool_call.get("params"), dict) else {}
                if tool_call.get("tool") == "exact_test":
                    pair = params.get("pair") if isinstance(params.get("pair"), list) else []
                    if len(pair) >= 2:
                        context["group_a_label"] = context["group_a_label"] or str(pair[0])
                        context["group_b_label"] = context["group_b_label"] or str(pair[1])
                    context["result_name"] = context["result_name"] or str(params.get("name") or "")
                elif tool_call.get("tool") == "test_contrast":
                    contrast = str(params.get("contrast") or "")
                    if contrast and (not context["group_a_label"] or not context["group_b_label"]):
                        parts = [part.strip() for part in contrast.split("-")]
                        if len(parts) >= 2:
                            context["group_a_label"] = context["group_a_label"] or parts[0]
                            context["group_b_label"] = context["group_b_label"] or parts[1]
                    context["result_name"] = context["result_name"] or str(params.get("name") or "")
            break

    return context


def _extract_edgepython_dependency_outputs(plan_payload: dict, step: dict) -> dict[str, list[str]]:
    outputs = {
        "top_genes": [],
        "tests": [],
        "saved": [],
        "plots": [],
    }
    for dep_step in _iter_prior_steps(plan_payload, step):
        step_result_items = _coerce_step_result_items(dep_step.get("result"))
        if not step_result_items:
            tool_calls = dep_step.get("tool_calls") if isinstance(dep_step.get("tool_calls"), list) else []
            if len(tool_calls) == 1 and isinstance(tool_calls[0], dict):
                step_result_items = [{
                    "tool": tool_calls[0].get("tool"),
                    "result": dep_step.get("result"),
                }]

        for item in step_result_items:
            tool_name = item.get("tool") if isinstance(item, dict) else None
            result_payload = item.get("result") if isinstance(item, dict) else None
            result_text = _extract_text_result(result_payload)
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


def _extract_de_test_summary(de_outputs: dict[str, list[str]]) -> dict[str, Any]:
    test_text = de_outputs.get("tests", [])[-1] if de_outputs.get("tests") else ""
    if not test_text:
        return {}

    summary: dict[str, Any] = {
        "raw_text": test_text,
    }
    test_name_match = re.search(r"^Test:\s*(.+)$", test_text, re.MULTILINE)
    if test_name_match:
        summary["test_name"] = test_name_match.group(1).strip()

    count_match = re.search(
        r"DE genes \(FDR < 0\.05\):\s*(\d+)\s+up,\s*(\d+)\s+down(?:,\s*(\d+)\s+NS|\s*\((\d+)\s+total\))?",
        test_text,
    )
    if not count_match:
        return summary

    n_up = int(count_match.group(1))
    n_down = int(count_match.group(2))
    n_ns = count_match.group(3)
    explicit_total = count_match.group(4)
    summary["n_up"] = n_up
    summary["n_down"] = n_down
    summary["n_significant"] = int(explicit_total) if explicit_total else n_up + n_down
    if n_ns is not None:
        summary["n_ns"] = int(n_ns)
    return summary


def _extract_saved_output_path(messages: list[str], *, prefix: str) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, str):
            continue
        for line in reversed(message.splitlines()):
            if line.startswith(prefix):
                path = line.split(":", 1)[1].strip()
                return path or None
    return None


def _build_inline_image_entry(path_value: str, caption: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": path_value,
        "caption": caption,
    }
    try:
        image_path = Path(path_value)
        if image_path.exists() and image_path.is_file():
            entry["data_b64"] = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except Exception:
        pass
    return entry


def _build_de_interpretation(plan_payload: dict, step: dict) -> str:
    de_outputs = _extract_edgepython_dependency_outputs(plan_payload, step)
    summary = _extract_de_test_summary(de_outputs)

    lines: list[str] = []
    if summary.get("n_significant") is not None:
        test_name = str(summary.get("test_name") or "DE test")
        line = (
            f"{test_name} found {int(summary['n_significant'])} significant genes at FDR < 0.05 "
            f"({int(summary['n_up'])} up, {int(summary['n_down'])} down"
        )
        if summary.get("n_ns") is not None:
            line += f", {int(summary['n_ns'])} not significant"
        line += ")."
        lines.append(line)
    elif de_outputs["tests"]:
        lines.append(de_outputs["tests"][-1])

    if de_outputs["top_genes"]:
        if lines:
            lines.append("")
        lines.append(de_outputs["top_genes"][-1])

    return "\n".join(line for line in lines if line)


def _build_de_summary_payload(plan_payload: dict, step: dict) -> dict[str, Any]:
    comparison = _extract_de_comparison_context(plan_payload, step)
    de_outputs = _extract_edgepython_dependency_outputs(plan_payload, step)
    deg_summary = _extract_de_test_summary(de_outputs)
    result_path = _extract_saved_output_path(de_outputs.get("saved", []), prefix="Saved results to:")
    plot_path = _extract_saved_output_path(de_outputs.get("plots", []), prefix="Volcano plot saved to:")
    plot_svg_path = _extract_saved_output_path(de_outputs.get("plots", []), prefix="Volcano plot SVG saved to:")
    interpretation = _build_de_interpretation(plan_payload, step)

    def _format_group(label: str, samples: list[str]) -> str:
        if samples:
            return f"{label} ({', '.join(samples)})"
        return label

    summary_sections: list[str] = []
    group_a_label = str(comparison.get("group_a_label") or "group 1")
    group_b_label = str(comparison.get("group_b_label") or "group 2")
    summary_sections.append(
        f"Compared {_format_group(group_a_label, comparison.get('group_a_samples', []))} against "
        f"{_format_group(group_b_label, comparison.get('group_b_samples', []))}."
    )

    output_workflow = str(comparison.get("output_workflow") or "")
    source_workflow = str(comparison.get("source_workflow") or "")
    source_label = str(comparison.get("source_label") or "")
    workflow_parts: list[str] = []
    if source_label:
        workflow_parts.append(f"Source table: {source_label}.")
    if source_workflow and output_workflow and source_workflow != output_workflow:
        workflow_parts.append(
            f"Read abundance values from {source_workflow} and wrote DE artifacts to {output_workflow}."
        )
    elif output_workflow:
        workflow_parts.append(f"Artifacts were written to {output_workflow}.")
    if workflow_parts:
        summary_sections.append(" ".join(workflow_parts))

    if deg_summary.get("n_significant") is not None:
        counts_line = (
            f"Significant genes at FDR < 0.05: {int(deg_summary['n_significant'])} total "
            f"({int(deg_summary['n_up'])} up, {int(deg_summary['n_down'])} down"
        )
        if deg_summary.get("n_ns") is not None:
            counts_line += f", {int(deg_summary['n_ns'])} not significant"
        counts_line += ")."
        summary_sections.append(counts_line)
    elif interpretation:
        summary_sections.append(interpretation)

    artifact_lines: list[str] = []
    artifacts: dict[str, str] = {}
    if result_path:
        artifacts["results_table"] = result_path
        artifact_lines.append(f"Results table: {result_path}")
    if plot_path:
        artifacts["volcano_plot"] = plot_path
        artifact_lines.append(f"Volcano plot: {plot_path}")
    if plot_svg_path:
        artifacts["volcano_plot_svg"] = plot_svg_path
        artifact_lines.append(f"Volcano plot SVG: {plot_svg_path}")
    if artifact_lines:
        summary_sections.append("\n".join(artifact_lines))

    top_genes_text = de_outputs.get("top_genes", [])[-1] if de_outputs.get("top_genes") else ""
    if top_genes_text:
        summary_sections.append(top_genes_text)

    payload: dict[str, Any] = {
        "markdown": "\n\n".join(section for section in summary_sections if section).strip(),
        "comparison": comparison,
    }
    if deg_summary:
        payload["deg_summary"] = deg_summary
    if artifacts:
        payload["artifacts"] = artifacts
    if plot_path:
        payload["image_files"] = [
            _build_inline_image_entry(
                plot_path,
                f"Volcano plot · {group_a_label} vs {group_b_label}",
            )
        ]
    return payload


def _build_de_plot_step_payload(plan_payload: dict, step: dict, result_payload: Any) -> Any:
    result_text = _extract_text_result(result_payload)
    if not result_text:
        return result_payload

    plot_path = _extract_saved_output_path([result_text], prefix="Volcano plot saved to:")
    if not plot_path:
        return result_payload
    plot_svg_path = _extract_saved_output_path([result_text], prefix="Volcano plot SVG saved to:")

    comparison = _extract_de_comparison_context(plan_payload, step)
    group_a_label = str(comparison.get("group_a_label") or "group 1")
    group_b_label = str(comparison.get("group_b_label") or "group 2")

    if isinstance(result_payload, dict):
        payload = dict(result_payload)
    else:
        payload = {"data": result_text}
    payload["artifacts"] = {
        **(payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}),
        "volcano_plot": plot_path,
    }
    if plot_svg_path:
        payload["artifacts"]["volcano_plot_svg"] = plot_svg_path
    payload["image_files"] = [
        _build_inline_image_entry(
            plot_path,
            f"Volcano plot · {group_a_label} vs {group_b_label}",
        )
    ]
    return payload


def _build_qc_interpretation(plan_payload: dict, step: dict) -> str:
    de_outputs = _extract_edgepython_dependency_outputs(plan_payload, step)
    if any(de_outputs.values()):
        interpretation = _build_de_interpretation(plan_payload, step)
        if interpretation:
            return interpretation
        lines: list[str] = []
        if de_outputs["saved"]:
            lines.append(de_outputs["saved"][-1])
        if de_outputs["plots"]:
            lines.append(de_outputs["plots"][-1])
        return "\n".join(line for line in lines if line)

    if plan_payload.get("plan_type") == "reconcile_bams":
        return _build_reconcile_interpretation(plan_payload, step)

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
        labels = [str(table.get("label") or "results") for table in tables[:4]]
        extra_count = max(0, len(tables) - len(labels))
        label_summary = ", ".join(labels)
        if extra_count:
            label_summary = f"{label_summary}, and {extra_count} more"
        lead = f"Parsed {len(tables)} result table(s): {label_summary}."
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

    if plan_payload.get("plan_type") == "run_de_pipeline":
        try:
            _ensure_de_workflow_context(plan_payload, project_dir_path)
        except Exception:
            logger.debug("Failed to initialize DE workflow directory", exc_info=True)

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
    tool_calls = _apply_runtime_tool_call_overrides(plan_payload, step, tool_calls)

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

            de_storage_root = _resolve_de_storage_root(plan_payload, step, project_dir_path)
            default_output_dir = (
                step.get("output_dir")
                or (str(de_storage_root / "de_inputs") if de_storage_root is not None else "")
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
            tool_calls = await _derive_runtime_parse_tool_calls(plan_payload, step)
        if not tool_calls:
            step["status"] = "FAILED"
            step["error"] = "Could not determine result files to parse from prior locate step output or runtime workflow listing."
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
        if plan_payload.get("plan_type") == "run_de_pipeline" and kind == "WRITE_SUMMARY":
            summary_payload = _build_de_summary_payload(plan_payload, step)
        else:
            summary_payload = {"markdown": _build_qc_interpretation(plan_payload, step)}
        step["status"] = "COMPLETED"
        step["result"] = summary_payload
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

        service_gate_error = _step_service_gate_error(step, source_key)
        if service_gate_error:
            step["status"] = "FAILED"
            step["error"] = service_gate_error
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, data={"results": all_results}, error=service_gate_error)

        if source_key == "edgepython":
            from cortex.tool_dispatch import _inject_edgepython_output_path

            de_storage_root = _resolve_de_storage_root(plan_payload, step, project_dir_path)
            if de_storage_root is not None:
                _inject_edgepython_output_path(tool_name, params, de_storage_root)

        result = await _call_mcp_tool(source_key, tool_name, params)
        if _is_check_existing_not_found_result(kind, tool_name, result):
            result = _normalize_check_existing_result(params, result)
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
    final_result = all_results[0]["result"] if len(all_results) == 1 else all_results
    if kind == "GENERATE_DE_PLOT" and len(all_results) == 1:
        final_result = _build_de_plot_step_payload(plan_payload, step, final_result)
    step["result"] = final_result
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

    project_dir_path = None
    if project_id and user is not None:
        try:
            from cortex.db_helpers import _resolve_project_dir

            project_dir_path = _resolve_project_dir(session, user, project_id)
        except Exception:
            logger.debug("Failed to resolve project directory for plan execution", exc_info=True)

    if plan_payload.get("plan_type") == "run_de_pipeline":
        try:
            _ensure_de_workflow_context(plan_payload, project_dir_path)
        except Exception:
            logger.debug("Failed to initialize DE workflow directory", exc_info=True)

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
                *[_execute_parallel_safe_step(step, plan_payload=plan_payload) for step in batch_ordered],
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


async def _execute_parallel_safe_step(step: dict, *, plan_payload: dict | None = None) -> StepResult:
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

    if isinstance(plan_payload, dict):
        tool_calls = _apply_runtime_tool_call_overrides(plan_payload, step, tool_calls)

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
        if _is_check_existing_not_found_result(kind, tool_name, result):
            result = _normalize_check_existing_result(params, result)
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
