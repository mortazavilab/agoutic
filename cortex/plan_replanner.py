"""
Replanning: updates the remaining steps of a plan when conditions change.

Triggers:
  - Step failure    → mark dependent steps as SKIPPED, add recovery note
  - New information → adjust remaining steps when results differ from expectations
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from common.logging_config import get_logger

if TYPE_CHECKING:
    from cortex.models import ProjectBlock

logger = get_logger(__name__)


def replan_on_failure(
    session,
    workflow_block: "ProjectBlock",
    failed_step_id: str,
) -> dict:
    """
    When a step fails, mark all dependent steps as SKIPPED.

    Returns the updated plan payload.
    """
    from cortex.llm_validators import get_block_payload

    payload = get_block_payload(workflow_block)
    steps = payload.get("steps", [])

    # Build dependency graph: step_id -> set of step_ids that depend on it
    dependents: dict[str, list[str]] = {}
    for s in steps:
        for dep in s.get("depends_on", []):
            dependents.setdefault(dep, []).append(s["id"])

    # BFS: find all transitive dependents of the failed step
    to_skip: set[str] = set()
    queue = [failed_step_id]
    while queue:
        current = queue.pop(0)
        for dep_id in dependents.get(current, []):
            if dep_id not in to_skip:
                to_skip.add(dep_id)
                queue.append(dep_id)

    # Mark dependents as SKIPPED
    skipped_count = 0
    for s in steps:
        if s["id"] in to_skip and s.get("status") not in ("COMPLETED", "FAILED"):
            s["status"] = "SKIPPED"
            s["error"] = f"Skipped — depends on failed step"
            skipped_count += 1

    if skipped_count:
        logger.info("Replanned after failure: skipped dependent steps",
                    failed_step=failed_step_id, skipped=skipped_count)

    # Persist
    workflow_block.payload_json = json.dumps(payload)
    session.commit()
    session.refresh(workflow_block)

    return payload


def replan_with_new_info(
    session,
    workflow_block: "ProjectBlock",
    completed_step_id: str,
    step_result: dict,
) -> dict | None:
    """
    After a step completes, check if its results require adjusting the remaining plan.

    Examples:
      - SEARCH_ENCODE returns 0 results → skip DOWNLOAD_DATA step
      - LOCATE_DATA finds no output files → skip PARSE_OUTPUT_FILE
      - CHECK_EXISTING finds files → convert downstream expensive step to WAITING_APPROVAL
      - RUN_DE_PIPELINE failure → skip GENERATE_DE_PLOT and INTERPRET_RESULTS
      - PARSE_OUTPUT_FILE empty → skip GENERATE_PLOT and COMPARE_SAMPLES

    Returns updated payload if changes were made, None otherwise.
    """
    from cortex.llm_validators import get_block_payload

    payload = get_block_payload(workflow_block)
    steps = payload.get("steps", [])

    # Find the completed step
    completed_step = None
    for s in steps:
        if s.get("id") == completed_step_id:
            completed_step = s
            break
    if not completed_step:
        return None

    kind = completed_step.get("kind", "")
    results_data = step_result.get("results", [])
    changed = False

    # --- SEARCH_ENCODE with 0 results: skip DOWNLOAD_DATA ---
    if kind == "SEARCH_ENCODE":
        empty = _is_empty_result(results_data)
        if empty:
            for s in steps:
                if (s.get("kind") == "DOWNLOAD_DATA"
                        and completed_step_id in s.get("depends_on", [])
                        and s.get("status") == "PENDING"):
                    s["status"] = "SKIPPED"
                    s["error"] = "Skipped — search returned no results"
                    changed = True
                    logger.info("Replan: skipping DOWNLOAD_DATA (empty search)",
                               step_id=s["id"])

    # --- LOCATE_DATA with no files: skip downstream parse ---
    if kind == "LOCATE_DATA":
        empty = _is_empty_result(results_data)
        if empty:
            for s in steps:
                if (s.get("kind") == "PARSE_OUTPUT_FILE"
                        and completed_step_id in s.get("depends_on", [])
                        and s.get("status") == "PENDING"):
                    s["status"] = "SKIPPED"
                    s["error"] = "Skipped — no data files found"
                    changed = True
                    logger.info("Replan: skipping PARSE_OUTPUT_FILE (no data)",
                               step_id=s["id"])

    # --- CHECK_EXISTING finds files: flag downstream expensive step ---
    if kind == "CHECK_EXISTING":
        reconcile_preflight = _extract_reconcile_preflight_payload(results_data)
        if reconcile_preflight and reconcile_preflight.get("status") == "needs_manual_gtf":
            follow_up_reason = (
                (reconcile_preflight.get("required_input") or {}).get("reason")
                or reconcile_preflight.get("message")
                or "Manual annotation GTF is required before approval."
            )
            for s in steps:
                if (
                    s.get("kind") == "REQUEST_APPROVAL"
                    and completed_step_id in s.get("depends_on", [])
                    and s.get("status") == "PENDING"
                ):
                    s["status"] = "FOLLOW_UP"
                    s["error"] = follow_up_reason
                    s["title"] = "Provide manual annotation GTF path before approval"
                    payload["status"] = "FOLLOW_UP"
                    payload["current_step_id"] = s.get("id")
                    changed = True
                    logger.info(
                        "Replan: pausing reconcile workflow for manual GTF follow-up",
                        step_id=s.get("id"),
                    )

        has_files = _has_existing_files(results_data)
        if has_files:
            _EXPENSIVE_KINDS = {"SUBMIT_WORKFLOW", "DOWNLOAD_DATA", "RUN_DE_ANALYSIS"}
            for s in steps:
                if (s.get("kind") in _EXPENSIVE_KINDS
                        and completed_step_id in s.get("depends_on", [])
                        and s.get("status") == "PENDING"):
                    s["status"] = "WAITING_APPROVAL"
                    s["title"] = s.get("title", "") + " (existing results found — rerun?)"
                    changed = True
                    logger.info("Replan: existing results found, flagging step for approval",
                               step_id=s["id"], step_kind=s.get("kind"))

    # --- PARSE_OUTPUT_FILE empty: skip downstream GENERATE_PLOT / COMPARE_SAMPLES ---
    if kind == "PARSE_OUTPUT_FILE":
        empty = _is_empty_result(results_data)
        if empty:
            _SKIP_AFTER_EMPTY_PARSE = {"GENERATE_PLOT", "GENERATE_DE_PLOT", "COMPARE_SAMPLES"}
            for s in steps:
                if (s.get("kind") in _SKIP_AFTER_EMPTY_PARSE
                        and completed_step_id in s.get("depends_on", [])
                        and s.get("status") == "PENDING"):
                    s["status"] = "SKIPPED"
                    s["error"] = "Skipped — parsed data was empty"
                    changed = True
                    logger.info("Replan: skipping step after empty parse",
                               step_id=s["id"], step_kind=s.get("kind"))

    if changed:
        workflow_block.payload_json = json.dumps(payload)
        session.commit()
        session.refresh(workflow_block)
        return payload

    return None


def _is_empty_result(results: list | dict) -> bool:
    """Check if a tool call result indicates empty/no-data."""
    if isinstance(results, list):
        if not results:
            return True
        for r in results:
            result_data = r.get("result", {}) if isinstance(r, dict) else r
            if isinstance(result_data, dict):
                # Check for explicit empty markers
                if result_data.get("total") == 0:
                    return True
                if result_data.get("count") == 0:
                    return True
                data = result_data.get("data")
                if isinstance(data, list) and len(data) == 0:
                    return True
                files = result_data.get("files")
                if isinstance(files, list) and len(files) == 0:
                    return True
    elif isinstance(results, dict):
        if results.get("total") == 0 or results.get("count") == 0:
            return True
        data = results.get("data")
        if isinstance(data, list) and len(data) == 0:
            return True
    return False


def _has_existing_files(results: list | dict) -> bool:
    """Check if a CHECK_EXISTING step result found files (non-empty)."""
    # Inverse of empty — if not empty, we have files
    if _is_empty_result(results):
        return False
    # Also check for explicit positive indicators
    if isinstance(results, list):
        for r in results:
            result_data = r.get("result", {}) if isinstance(r, dict) else r
            if isinstance(result_data, dict):
                files = result_data.get("files")
                if isinstance(files, list) and len(files) > 0:
                    return True
                if result_data.get("found"):
                    return True
                if result_data.get("path"):
                    return True
        # Non-empty list of results is a positive signal
        return bool(results)
    if isinstance(results, dict):
        if results.get("found") or results.get("path"):
            return True
        files = results.get("files")
        if isinstance(files, list) and len(files) > 0:
            return True
    return False


def _extract_reconcile_preflight_payload(results: list | dict) -> dict | None:
    """Extract JSON payload from reconcile preflight script stdout when available."""
    if not isinstance(results, list):
        return None

    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("tool") != "run_allowlisted_script":
            continue

        result_data = item.get("result")
        if not isinstance(result_data, dict):
            continue
        if result_data.get("script_id") != "reconcile_bams/reconcile_bams":
            continue

        stdout_text = result_data.get("stdout")
        if not isinstance(stdout_text, str) or not stdout_text.strip():
            continue

        try:
            parsed = json.loads(stdout_text)
        except (TypeError, ValueError):
            continue

        if isinstance(parsed, dict):
            return parsed

    return None
