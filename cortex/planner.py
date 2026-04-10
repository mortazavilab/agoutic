"""
Plan generation: classifies user requests and produces structured WORKFLOW_PLAN payloads.

Three request classes:
  - INFORMATIONAL: Simple question, no plan needed (existing flow)
  - SINGLE_TOOL: One tool call, no plan needed (existing flow)
  - MULTI_STEP: Requires a structured plan (new flow)

Ten plan templates:
  - run_workflow: Analyze a local sample (stage → run → analyze)
    - remote_stage_workflow: Stage a sample on a remote SLURM target for later reuse
  - compare_samples: Compare two or more samples
  - download_analyze: Download from ENCODE/URL, then analyze
  - summarize_results: Summarize existing completed job results
  - run_de_pipeline: Full differential expression analysis
    - run_xgenepy_analysis: Local cis/trans analysis with XgenePy
  - parse_plot_interpret: Parse data, plot, and explain
  - compare_workflows: Compare outputs from two workflow runs
  - search_compare_to_local: Search ENCODE, download, compare to local data
"""

from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING, Any

from common.logging_config import get_logger
from cortex.plan_validation import PlanValidationError, validate_plan

# ---------------------------------------------------------------------------
# Imports from extracted submodules
# ---------------------------------------------------------------------------
from cortex.plan_classifier import (  # noqa: F401 — re-exported
    _INFORMATIONAL_PATTERNS,
    _MULTI_STEP_PATTERNS,
    _detect_plan_type,
    _is_summarize_results_request,
    classify_request,
)
from cortex.plan_params import (  # noqa: F401 — re-exported
    _extract_plan_params,
    _select_plot_type,
)
from cortex.plan_templates import (  # noqa: F401 — re-exported
    _make_step,
    _normalize_fragment_alias,
    _plan_instance_id,
    _step_id,
    _template_compare_samples,
    _template_compare_workflows,
    _template_download_analyze,
    _template_parse_plot_interpret,
    _template_reconcile_bams,
    _template_remote_stage_workflow,
    _template_run_de_pipeline,
    _template_run_enrichment,
    _template_run_workflow,
    _template_run_xgenepy_analysis,
    _template_search_compare_to_local,
    _template_summarize_results,
)

if TYPE_CHECKING:
    from cortex.agent_engine import AgentEngine
    from cortex.schemas import ConversationState

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dispatch table — kept here so monkeypatching re-exported template names
# in this module's namespace is honoured at call time.
# ---------------------------------------------------------------------------
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
    if plan_type == "run_xgenepy_analysis":
        return _template_run_xgenepy_analysis(params)
    if plan_type == "remote_stage_workflow":
        return _template_remote_stage_workflow(params)
    if plan_type == "reconcile_bams":
        return _template_reconcile_bams(params)
    return None


# ---------------------------------------------------------------------------
# Step-kind whitelists
# ---------------------------------------------------------------------------

_PLANNER_SAFE_STEP_KINDS = frozenset({
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


_PLANNER_APPROVAL_STEP_KINDS = frozenset({
    "SUBMIT_WORKFLOW",
    "DOWNLOAD_DATA",
    "RUN_DE_ANALYSIS",
    "RUN_DE_PIPELINE",
    "RUN_XGENEPY",
    "RUN_SCRIPT",
    "REQUEST_APPROVAL",
})


_PLANNER_ALLOWED_STEP_KINDS = frozenset({
    "LOCATE_DATA",
    "SEARCH_ENCODE",
    "DOWNLOAD_DATA",
    "SUBMIT_WORKFLOW",
    "MONITOR_WORKFLOW",
    "PREPARE_DE_INPUT",
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
    "RUN_XGENEPY",
    "PARSE_XGENEPY_OUTPUT",
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


# ---------------------------------------------------------------------------
# Fragment composition
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Plan finalization / validation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# LLM-based plan parsing
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


# ---------------------------------------------------------------------------
# Hybrid bridge
# ---------------------------------------------------------------------------

_HYBRID_FIRST_FLOWS = frozenset({
    "compare_samples",
    "download_analyze",
    "summarize_results",
    "parse_plot_interpret",
    "compare_workflows",
    "search_compare_to_local",
})


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
    project_dir: str = "",
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

    params = _extract_plan_params(message, conv_state, plan_type or "",
                                    project_dir=project_dir)

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
    if plan_type == "run_xgenepy_analysis":
        plan = _template_run_xgenepy_analysis(params)
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
