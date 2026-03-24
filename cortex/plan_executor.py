"""
Deterministic plan step executor.

Maps step kinds to concrete tool call sequences.
The LLM does NOT improvise execution — code controls it.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common import MCPHttpClient
from common.logging_config import get_logger
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

# Expensive steps require explicit approval
_APPROVAL_STEP_KINDS = frozenset({
    "SUBMIT_WORKFLOW",
    "DOWNLOAD_DATA",
    "RUN_DE_ANALYSIS",
    "RUN_DE_PIPELINE",
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
    "GENERATE_DE_PLOT": [{"source_key": "edgepython", "tool": "generate_plot"}],
    "INTERPRET_RESULTS": None,      # LLM call (special handling)
    "RECOMMEND_NEXT": None,         # LLM call (special handling)
    "ANNOTATE_RESULTS": [{"source_key": "edgepython", "tool": "annotate_genes"}],
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

    if kind == "PARSE_OUTPUT_FILE" and not explicit_tool_calls:
        tool_calls = _derive_parse_tool_calls(plan_payload, step)
        if not tool_calls:
            step["status"] = "FAILED"
            step["error"] = "Could not determine result files to parse from prior locate step output."
            step["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            _persist_step_update(session, workflow_block, plan_payload)
            return StepResult(success=False, error=step["error"])

    if kind in ("SUBMIT_WORKFLOW", "DOWNLOAD_DATA", "RUN_DE_ANALYSIS",
                "COMPARE_SAMPLES", "GENERATE_PLOT", "WRITE_SUMMARY",
                "RUN_DE_PIPELINE", "RUN_SCRIPT", "INTERPRET_RESULTS", "RECOMMEND_NEXT"):
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
        plan_payload["validation_error"] = err.to_dict()
        _persist_step_update(session, workflow_block, plan_payload)
        if emit_progress:
            emit_progress(request_id, "error", "Execution plan failed validation before dispatch.")
        return

    # Mark plan as running
    plan_payload["status"] = "RUNNING"
    _persist_step_update(session, workflow_block, plan_payload)

    steps = plan_payload.get("steps", [])
    if not steps:
        plan_payload["status"] = "COMPLETED"
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
            _persist_step_update(session, workflow_block, plan_payload)
            return

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
        _persist_step_update(session, workflow_block, plan_payload)
        logger.info("Plan completed", title=plan_payload.get("title"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_step(steps: list[dict], step_id: str) -> dict | None:
    for s in steps:
        if s.get("id") == step_id:
            return s
    return None


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
