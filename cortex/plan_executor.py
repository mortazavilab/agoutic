"""
Deterministic plan step executor.

Maps step kinds to concrete tool call sequences.
The LLM does NOT improvise execution — code controls it.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.config import get_service_url

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
    "REQUEST_APPROVAL",
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
    "COMPARE_SAMPLES": None,        # multi-call analyzer + LLM synthesis
    "GENERATE_PLOT": None,          # uses PLOT tag system
    "WRITE_SUMMARY": None,          # uses LLM analyze_results
    "REQUEST_APPROVAL": None,       # creates APPROVAL_GATE block
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

    if kind in ("SUBMIT_WORKFLOW", "DOWNLOAD_DATA", "RUN_DE_ANALYSIS",
                "COMPARE_SAMPLES", "GENERATE_PLOT", "WRITE_SUMMARY",
                "RUN_DE_PIPELINE", "INTERPRET_RESULTS", "RECOMMEND_NEXT"):
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

    # Mark plan as running
    plan_payload["status"] = "RUNNING"
    _persist_step_update(session, workflow_block, plan_payload)

    steps = plan_payload.get("steps", [])
    if not steps:
        plan_payload["status"] = "COMPLETED"
        _persist_step_update(session, workflow_block, plan_payload)
        return

    step_index = 0
    while True:
        plan_payload = get_block_payload(workflow_block)
        steps = plan_payload.get("steps", [])
        if step_index >= len(steps):
            break

        step = steps[step_index]
        step_id = step.get("id")
        status = step.get("status", "PENDING")

        # Skip already-finished steps
        if status in ("COMPLETED", "SKIPPED", "CANCELLED"):
            step_index += 1
            continue

        # Check dependencies
        depends_on = step.get("depends_on", [])
        deps_met = True
        for dep_id in depends_on:
            dep_step = _find_step(steps, dep_id)
            if dep_step and dep_step.get("status") != "COMPLETED":
                deps_met = False
                break
        if not deps_met:
            logger.info("Step dependencies not met, stopping", step_id=step_id,
                       depends_on=depends_on)
            break

        # Check if this step needs approval and hasn't been approved yet
        if step.get("requires_approval") and status != "RUNNING":
            if not should_auto_execute(step):
                step["status"] = "WAITING_APPROVAL"
                plan_payload["current_step_id"] = step_id
                _persist_step_update(session, workflow_block, plan_payload)
                if emit_progress:
                    emit_progress(request_id, "approval",
                                  f"Waiting for approval: {step.get('title', 'step')}")
                return  # Pause here — will resume after approval

        # Emit progress
        if emit_progress:
            emit_progress(request_id, "executing",
                          f"Running: {step.get('title', 'step')}")

        # Update current step
        plan_payload["current_step_id"] = step_id

        # Execute the step
        result = await execute_step(
            session, workflow_block, step_id,
            plan_payload=plan_payload,
            engine=engine, user=user, project_id=project_id,
        )

        # Re-read payload (execute_step may have updated it)
        plan_payload = get_block_payload(workflow_block)

        if not result.success:
            logger.warning("Plan step failed", step_id=step_id, error=result.error)
            # Trigger replanning
            plan_payload = replan_on_failure(session, workflow_block, step_id)
            plan_payload["status"] = "FAILED"
            _persist_step_update(session, workflow_block, plan_payload)
            return

        # Check if step result requires replanning
        if result.data.get("action") in ("approval_required", "special_handling"):
            # Execution paused — these need external handling
            return

        # Check if new info requires plan adjustment
        updated = replan_with_new_info(
            session, workflow_block, step_id, result.data,
        )
        if updated:
            plan_payload = updated
        step_index += 1

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
