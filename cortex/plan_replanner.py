"""
Replanning: updates the remaining steps of a plan when conditions change.

Triggers:
  - Step failure    → mark dependent steps as SKIPPED, add recovery note
  - New information → adjust remaining steps when results differ from expectations
"""

from __future__ import annotations

import re
import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from common.logging_config import get_logger
from common.workflow_paths import next_workflow_number, workflow_dir_name

if TYPE_CHECKING:
    from cortex.models import ProjectBlock

logger = get_logger(__name__)


_WORKFLOW_SUFFIX_RE = re.compile(r"^workflow(\d+)$", re.IGNORECASE)


def _workflow_dirs_from_reconcile_inputs(bam_inputs: list[dict]) -> list[str]:
    workflow_dirs: list[str] = []
    seen: set[str] = set()
    for item in bam_inputs:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        bam_path = PurePosixPath(raw_path)
        workflow_dir = bam_path.parent.parent if bam_path.parent.name == "annot" else bam_path.parent
        workflow_dir_text = str(workflow_dir)
        if workflow_dir_text and workflow_dir_text not in seen:
            seen.add(workflow_dir_text)
            workflow_dirs.append(workflow_dir_text)
    return workflow_dirs


def _reference_suffix(reference: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(reference or "reference"))
    normalized = normalized.strip("_")
    return normalized or "reference"


def _planned_reconcile_output_dirs(
    requested_output_directory: str | None,
    output_root: str | None,
    group_count: int,
) -> list[str]:
    if group_count <= 0:
        return []

    requested = str(requested_output_directory or "").strip()
    fallback_root = str(output_root or "").strip()
    requested_name = PurePosixPath(requested).name if requested else ""
    workflow_match = _WORKFLOW_SUFFIX_RE.fullmatch(requested_name)
    if workflow_match:
        parent_dir = PurePosixPath(requested).parent
        start_index = int(workflow_match.group(1))
        return [
            str(parent_dir / f"workflow{start_index + offset}")
            for offset in range(group_count)
        ]

    root_path = Path(fallback_root or requested or ".").expanduser()
    start_index = next_workflow_number(root_path)
    return [
        str(root_path / workflow_dir_name(start_index + offset))
        for offset in range(group_count)
    ]


def _rebuild_reconcile_steps_for_reference_groups(payload: dict, completed_step_id: str, reconcile_preflight: dict) -> bool:
    from cortex.plan_templates import _make_step

    steps = payload.get("steps", [])
    reference_groups = reconcile_preflight.get("reference_groups")
    if not isinstance(reference_groups, list) or not reference_groups:
        return False

    completed_step = next((step for step in steps if step.get("id") == completed_step_id), None)
    if not isinstance(completed_step, dict):
        return False

    preserved_steps: list[dict] = []
    for step in steps:
        preserved_steps.append(step)
        if step.get("id") == completed_step_id:
            break

    output_root = str((reconcile_preflight.get("outputs") or {}).get("output_root") or payload.get("output_directory") or "").strip()
    base_output_prefix = str((reconcile_preflight.get("outputs") or {}).get("output_prefix") or payload.get("output_prefix") or "reconciled").strip() or "reconciled"
    next_order_index = max((int(step.get("order_index") or 0) for step in preserved_steps), default=-1) + 1
    previous_dependency = completed_step_id
    new_steps: list[dict] = []
    realized_references: list[str] = []
    all_references = [
        str(group.get("reference") or "").strip()
        for group in reference_groups
        if isinstance(group, dict) and str(group.get("reference") or "").strip()
    ]
    shared_reconcile_authorization = len(all_references) > 1
    group_output_dirs = _planned_reconcile_output_dirs(payload.get("output_directory"), output_root, len(all_references))
    shared_approval_step_id: str | None = None

    valid_group_index = 0
    for group in reference_groups:
        if not isinstance(group, dict):
            continue
        reference = str(group.get("reference") or "").strip()
        bam_inputs = (group.get("inputs") or {}).get("bams") or []
        workflow_dirs = _workflow_dirs_from_reconcile_inputs(bam_inputs)
        if not reference or not workflow_dirs:
            continue

        realized_references.append(reference)
        group_output_dir = group_output_dirs[valid_group_index] if valid_group_index < len(group_output_dirs) else ""
        valid_group_index += 1
        group_output_root = (
            str(PurePosixPath(group_output_dir).parent)
            if group_output_dir else
            str((group.get("outputs") or {}).get("output_root") or output_root).strip()
        )
        group_output_prefix = str((group.get("outputs") or {}).get("output_prefix") or "").strip()
        if not group_output_prefix:
            group_output_prefix = f"{base_output_prefix}_{_reference_suffix(reference)}"
        annotation_gtf = str(((group.get("gtf") or {}).get("path") or "")).strip()

        script_args = ["--json", "--reference", reference, "--output-prefix", group_output_prefix]
        for workflow_dir in workflow_dirs:
            script_args.extend(["--workflow-dir", workflow_dir])
        if group_output_dir:
            script_args.extend(["--output-dir", group_output_dir])
        elif group_output_root:
            script_args.extend(["--output-dir", group_output_root])
        if annotation_gtf:
            script_args.extend(["--annotation-gtf", annotation_gtf])

        approval_step = _make_step(
            "REQUEST_APPROVAL",
            (
                f"Approve reconcile BAM execution for all detected references ({', '.join(all_references)})"
                if shared_reconcile_authorization and shared_approval_step_id is None
                else f"Approve reconcile BAM execution for {reference}"
            ),
            next_order_index,
            requires_approval=True,
            depends_on=[previous_dependency],
        )
        approval_step["preflight_summary"] = group
        approval_step["output_directory"] = group_output_dir or group_output_root
        if shared_reconcile_authorization and shared_approval_step_id is None:
            approval_step["shared_reconcile_authorization"] = True
            approval_step["approved_references"] = all_references
            shared_approval_step_id = approval_step["id"]
        elif shared_reconcile_authorization:
            approval_step["auto_approve_from_shared_reconcile_authorization"] = True
            approval_step["shared_reconcile_authorization_step_id"] = shared_approval_step_id
        new_steps.append(approval_step)
        next_order_index += 1

        run_step = _make_step(
            "RUN_SCRIPT",
            f"Run reconcile BAM script for {reference} using symlinked workflow inputs",
            next_order_index,
            requires_approval=True,
            depends_on=[approval_step["id"]],
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
        run_step["preflight_summary"] = group
        run_step["output_directory"] = group_output_dir or group_output_root
        new_steps.append(run_step)
        next_order_index += 1

        locate_step = _make_step(
            "LOCATE_DATA",
            f"List reconcile output files for parsing ({reference})",
            next_order_index,
            depends_on=[run_step["id"]],
            tool_calls=[
                {
                    "source_key": "analyzer",
                    "tool": "list_job_files",
                    "params": {
                        "work_dir": group_output_dir or group_output_root or output_root or ".",
                        "max_depth": 1,
                        "allow_missing": True,
                    },
                }
            ],
        )
        new_steps.append(locate_step)
        next_order_index += 1

        parse_step = _make_step(
            "PARSE_OUTPUT_FILE",
            f"Parse reconcile result tables for {reference}",
            next_order_index,
            depends_on=[locate_step["id"]],
        )
        new_steps.append(parse_step)
        next_order_index += 1

        summary_step = _make_step(
            "WRITE_SUMMARY",
            f"Summarize reconcile outputs and generated files for {reference}",
            next_order_index,
            depends_on=[parse_step["id"]],
        )
        new_steps.append(summary_step)
        next_order_index += 1
        previous_dependency = summary_step["id"]

    if not new_steps:
        return False

    payload["steps"] = preserved_steps + new_steps
    payload["status"] = "WAITING_APPROVAL"
    payload["current_step_id"] = new_steps[0]["id"]
    payload["reference_groups"] = realized_references
    if shared_reconcile_authorization:
        payload["output_directory"] = output_root or str(PurePosixPath(group_output_dirs[0]).parent)
        payload["shared_reconcile_authorization_required"] = True
        payload["shared_reconcile_approval_granted"] = False
        payload["shared_reconcile_authorization_step_id"] = shared_approval_step_id
    goal = str(payload.get("goal") or "").strip()
    if goal and "separately per genome" not in goal.lower():
        payload["goal"] = f"{goal.rstrip('.')} separately per genome."
    return True


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
        if reconcile_preflight and reconcile_preflight.get("status") == "split_by_reference":
            if _rebuild_reconcile_steps_for_reference_groups(payload, completed_step_id, reconcile_preflight):
                changed = True
                logger.info(
                    "Replan: splitting reconcile workflow by BAM reference",
                    step_id=completed_step_id,
                    references=payload.get("reference_groups"),
                )
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
                if result_data.get("file_count") == 0:
                    return True
                data = result_data.get("data")
                if isinstance(data, list) and len(data) == 0:
                    return True
                files = result_data.get("files")
                if isinstance(files, list) and len(files) == 0:
                    return True
                paths = result_data.get("paths")
                if isinstance(paths, list) and len(paths) == 0:
                    return True
    elif isinstance(results, dict):
        if results.get("total") == 0 or results.get("count") == 0 or results.get("file_count") == 0:
            return True
        data = results.get("data")
        if isinstance(data, list) and len(data) == 0:
            return True
        paths = results.get("paths")
        if isinstance(paths, list) and len(paths) == 0:
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
                if result_data.get("file_count", 0) > 0:
                    return True
                paths = result_data.get("paths")
                if isinstance(paths, list) and len(paths) > 0:
                    return True
                if result_data.get("primary_path"):
                    return True
                if result_data.get("found"):
                    return True
                if result_data.get("path"):
                    return True
        return False
    if isinstance(results, dict):
        if results.get("found") or results.get("path") or results.get("primary_path"):
            return True
        files = results.get("files")
        if isinstance(files, list) and len(files) > 0:
            return True
        if results.get("file_count", 0) > 0:
            return True
        paths = results.get("paths")
        if isinstance(paths, list) and len(paths) > 0:
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

        structured_output = result_data.get("script_output")
        if isinstance(structured_output, dict):
            return structured_output

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
