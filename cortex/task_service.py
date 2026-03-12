"""Persistent project task projection derived from workflow state."""

from __future__ import annotations

import datetime
import json
import uuid

from sqlalchemy import select

from cortex.models import ProjectBlock, ProjectTask
from cortex.llm_validators import get_block_payload


_ACTIVE_STATUSES = {"PENDING", "RUNNING", "FOLLOW_UP"}
_USER_MANAGED_KINDS = {"result_review", "recovery"}
_ANALYSIS_SKILLS = {
    "analyze_job_results",
    "run_dogme_dna",
    "run_dogme_rna",
    "run_dogme_cdna",
}


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _task_metadata(task: ProjectTask) -> dict:
    if not task.metadata_json:
        return {}
    try:
        data = json.loads(task.metadata_json)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def task_to_dict(task: ProjectTask) -> dict:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "kind": task.kind,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "source_key": task.source_key,
        "source_type": task.source_type,
        "source_id": task.source_id,
        "parent_task_id": task.parent_task_id,
        "action_label": task.action_label,
        "action_target": task.action_target,
        "metadata": _task_metadata(task),
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "completed_at": _iso(task.completed_at),
        "archived_at": _iso(task.archived_at),
        "children": [],
    }


def _merged_metadata(task: ProjectTask | None, spec: dict) -> dict:
    merged = _task_metadata(task) if task is not None else {}
    merged.update(spec.get("metadata", {}))
    return merged


def _apply_manual_state(task: ProjectTask, spec: dict, metadata: dict, now: datetime.datetime) -> None:
    if metadata.get("archived_by_user"):
        task.archived_at = task.archived_at or now
        return

    task.archived_at = None
    if task.kind in _USER_MANAGED_KINDS and metadata.get("user_completed"):
        task.status = "COMPLETED"
        task.completed_at = task.completed_at or now


def _upsert_task(session, existing_by_source: dict[str, ProjectTask], spec: dict, *, owner_id: str) -> ProjectTask:
    now = datetime.datetime.utcnow()
    task = existing_by_source.get(spec["source_key"])
    if task is None:
        task = ProjectTask(
            id=str(uuid.uuid4()),
            project_id=spec["project_id"],
            owner_id=owner_id,
            source_key=spec["source_key"],
            created_at=now,
        )
        session.add(task)
        existing_by_source[spec["source_key"]] = task

    prev_status = task.status
    metadata = _merged_metadata(task, spec)
    task.kind = spec["kind"]
    task.title = spec["title"]
    task.status = spec["status"]
    task.priority = spec.get("priority", "normal")
    task.source_type = spec.get("source_type")
    task.source_id = spec.get("source_id")
    task.parent_task_id = spec.get("parent_task_id")
    task.action_label = spec.get("action_label")
    task.action_target = spec.get("action_target")
    task.metadata_json = json.dumps(metadata)
    task.updated_at = now
    _apply_manual_state(task, spec, metadata, now)

    if task.status in {"COMPLETED", "CANCELLED"}:
        if task.completed_at is None:
            task.completed_at = now
    elif prev_status in {"COMPLETED", "CANCELLED"} and not metadata.get("user_completed"):
        task.completed_at = None

    return task


def _build_target(kind: str, *, block_id: str | None = None, run_uuid: str | None = None) -> str:
    target = {"type": kind}
    if block_id:
        target["block_id"] = block_id
    if run_uuid:
        target["run_uuid"] = run_uuid
    return json.dumps(target)


def _action_for_status(status: str, *, pending_label: str, completed_label: str) -> str:
    if status in _ACTIVE_STATUSES:
        return pending_label
    return completed_label


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-") or "task"


def _workflow_action_for_step(step: dict) -> tuple[str | None, str | None]:
    status = step.get("status", "PENDING")
    kind = step.get("kind", "")
    if status == "FOLLOW_UP" and step.get("decision_gate_id"):
        return "Review choice", _build_target("block", block_id=step["decision_gate_id"])
    # --- Plan step kinds ---
    if kind in ("LOCATE_DATA", "VALIDATE_INPUTS", "SEARCH_ENCODE",
                "PARSE_OUTPUT_FILE", "SUMMARIZE_QC", "GENERATE_PLOT", "WRITE_SUMMARY",
                "CHECK_EXISTING", "GENERATE_DE_PLOT", "INTERPRET_RESULTS", "RECOMMEND_NEXT"):
        return _action_for_status(status, pending_label="Waiting", completed_label="Done"), None
    if kind == "REQUEST_APPROVAL":
        _gate_target = _build_target("block", block_id=step["gate_block_id"]) if step.get("gate_block_id") else None
        return _action_for_status(status, pending_label="Review", completed_label="Approved"), _gate_target
    if kind in ("DOWNLOAD_DATA", "SUBMIT_WORKFLOW", "MONITOR_WORKFLOW",
                "RUN_DE_ANALYSIS", "COMPARE_SAMPLES", "RUN_DE_PIPELINE"):
        _step_target = _build_target("block", block_id=step["block_id"], run_uuid=step.get("run_uuid")) if step.get("block_id") else None
        return _action_for_status(status, pending_label="Running", completed_label="View results"), _step_target
    # --- Legacy step kinds ---
    if kind == "analysis" and step.get("run_uuid"):
        return _action_for_status(status, pending_label="Analyzing", completed_label="Open results"), _build_target("results", run_uuid=step["run_uuid"])
    if kind == "run" and step.get("block_id"):
        return _action_for_status(status, pending_label="View run", completed_label="View run"), _build_target("block", block_id=step["block_id"], run_uuid=step.get("run_uuid"))
    if kind == "copy_sample":
        if status == "COMPLETED":
            return "Staged", None
        if status == "RUNNING":
            return "Copying", None
        return "Waiting", None
    return _action_for_status(status, pending_label="Open task", completed_label="Done"), None


def _iter_workflow_plan_specs(project_id: str, parent_task_id: str, block: ProjectBlock, payload: dict):
    next_step = payload.get("next_step")
    for index, step in enumerate(payload.get("steps", [])):
        action_label, action_target = _workflow_action_for_step(step)
        yield {
            "project_id": project_id,
            "source_key": f"workflow-step:{block.id}:{step.get('id', index)}",
            "kind": step.get("kind") or "workflow_step",
            "title": step.get("title") or step.get("id") or "Workflow step",
            "status": step.get("status", "PENDING"),
            "priority": "high" if step.get("status") in {"FAILED", "FOLLOW_UP"} else "normal",
            "source_type": "block",
            "source_id": block.id,
            "parent_task_id": parent_task_id,
            "action_label": action_label,
            "action_target": action_target,
            "metadata": {
                "workflow_block_id": block.id,
                "workflow_type": payload.get("workflow_type"),
                "step_id": step.get("id"),
                "order_index": step.get("order_index", index),
                "is_next_step": step.get("id") == next_step,
                "source_path": step.get("source_path"),
                "staged_input_directory": step.get("staged_input_directory"),
                "run_uuid": step.get("run_uuid"),
            },
        }


def _iter_workflow_stage_specs(project_id: str, parent_task_id: str, block: ProjectBlock, payload: dict, *, run_uuid: str, sample_name: str):
    tasks = payload.get("job_status", {}).get("tasks", {})
    completed_names = tasks.get("completed", []) if isinstance(tasks, dict) else []
    running_names = tasks.get("running", []) if isinstance(tasks, dict) else []

    seen: set[str] = set()
    for stage_name in completed_names:
        if not stage_name or stage_name in seen:
            continue
        seen.add(stage_name)
        stage_key = f"workflow-stage:{run_uuid or block.id}:{_normalize_name(stage_name)}"
        yield {
            "project_id": project_id,
            "source_key": stage_key,
            "kind": "workflow_stage",
            "title": stage_name,
            "status": "COMPLETED",
            "priority": "normal",
            "source_type": "run_uuid",
            "source_id": run_uuid or block.id,
            "parent_task_id": parent_task_id,
            "action_label": "View run",
            "action_target": _build_target("block", block_id=block.id, run_uuid=run_uuid),
            "metadata": {
                "run_uuid": run_uuid,
                "sample_name": sample_name,
                "stage_name": stage_name,
            },
        }

    for stage_name in running_names:
        if not stage_name or stage_name in seen:
            continue
        seen.add(stage_name)
        stage_key = f"workflow-stage:{run_uuid or block.id}:{_normalize_name(stage_name)}"
        yield {
            "project_id": project_id,
            "source_key": stage_key,
            "kind": "workflow_stage",
            "title": stage_name,
            "status": "RUNNING",
            "priority": "normal",
            "source_type": "run_uuid",
            "source_id": run_uuid or block.id,
            "parent_task_id": parent_task_id,
            "action_label": "View run",
            "action_target": _build_target("block", block_id=block.id, run_uuid=run_uuid),
            "metadata": {
                "run_uuid": run_uuid,
                "sample_name": sample_name,
                "stage_name": stage_name,
            },
        }


def _iter_download_file_specs(project_id: str, parent_task_id: str, block: ProjectBlock, payload: dict):
    files = payload.get("files", []) or []
    downloaded = int(payload.get("downloaded", 0) or 0)
    current_file = payload.get("current_file") or ""

    for idx, file_info in enumerate(files):
        filename = file_info.get("filename") or file_info.get("url", "file")
        file_key = f"download-file:{block.id}:{idx}:{_normalize_name(filename)}"

        if idx < downloaded:
            child_status = "COMPLETED"
        elif block.status == "FAILED" and (current_file == filename or idx == downloaded):
            child_status = "FAILED"
        elif block.status == "RUNNING" and (current_file == filename or idx == downloaded):
            child_status = "RUNNING"
        else:
            child_status = "PENDING"

        yield {
            "project_id": project_id,
            "source_key": file_key,
            "kind": "download_file",
            "title": filename,
            "status": child_status,
            "priority": "normal",
            "source_type": "block",
            "source_id": block.id,
            "parent_task_id": parent_task_id,
            "action_label": "Open task",
            "action_target": _build_target("block", block_id=block.id),
            "metadata": {
                "filename": filename,
                "url": file_info.get("url"),
                "download_index": idx,
            },
        }


def _collect_analysis_state(blocks: list[ProjectBlock]) -> tuple[set[str], set[str]]:
    analyzed_samples: set[str] = set()
    reviewed_samples: set[str] = set()
    for block in blocks:
        if block.type != "AGENT_PLAN":
            continue
        payload = get_block_payload(block)
        skill = payload.get("skill")
        markdown = payload.get("markdown", "") or ""
        if skill not in _ANALYSIS_SKILLS and "### 📊 Analysis:" not in markdown:
            continue
        marker = "### 📊 Analysis:"
        sample_name = ""
        if marker in markdown:
            sample_name = markdown.split(marker, 1)[1].splitlines()[0].strip()
        if sample_name:
            analyzed_samples.add(sample_name)
            reviewed_samples.add(sample_name)
    return analyzed_samples, reviewed_samples


def sync_project_tasks(session, project_id: str) -> list[ProjectTask]:
    blocks = session.execute(
        select(ProjectBlock)
        .where(ProjectBlock.project_id == project_id)
        .order_by(ProjectBlock.seq.asc())
    ).scalars().all()

    existing_tasks = session.execute(
        select(ProjectTask)
        .where(ProjectTask.project_id == project_id)
    ).scalars().all()
    existing_by_source = {task.source_key: task for task in existing_tasks}

    analyzed_samples, reviewed_samples = _collect_analysis_state(blocks)
    seen_sources: set[str] = set()
    workflow_parent_ids: dict[str, str] = {}
    workflow_run_ids: set[str] = set()

    for block in blocks:
        payload = get_block_payload(block)
        owner_id = block.owner_id

        if block.type == "WORKFLOW_PLAN":
            source_key = f"workflow-plan:{block.id}"
            seen_sources.add(source_key)
            plan_task = _upsert_task(
                session,
                existing_by_source,
                {
                    "project_id": project_id,
                    "source_key": source_key,
                    "kind": "workflow_plan",
                    "title": payload.get("title") or f"Workflow for {payload.get('sample_name', 'sample')}",
                    "status": payload.get("status", block.status or "PENDING"),
                    "priority": "high" if payload.get("status") in {"FAILED", "FOLLOW_UP"} else "normal",
                    "source_type": "block",
                    "source_id": block.id,
                    "action_label": None,
                    "action_target": None,
                    "metadata": {
                        "workflow_type": payload.get("workflow_type"),
                        "sample_name": payload.get("sample_name"),
                        "next_step": payload.get("next_step"),
                    },
                },
                owner_id=owner_id,
            )
            workflow_parent_ids[block.id] = plan_task.id
            if payload.get("run_uuid"):
                workflow_run_ids.add(payload["run_uuid"])
            for child_spec in _iter_workflow_plan_specs(project_id, plan_task.id, block, payload):
                seen_sources.add(child_spec["source_key"])
                _upsert_task(session, existing_by_source, child_spec, owner_id=owner_id)
            continue

        if block.type == "APPROVAL_GATE":
            task_status = {
                "PENDING": "PENDING",
                "APPROVED": "COMPLETED",
                "REJECTED": "CANCELLED",
            }.get(block.status, "PENDING")
            gate_action = payload.get("gate_action") or payload.get("extracted_params", {}).get("gate_action")
            label = payload.get("label") or "Review and approve workflow"
            action_label = _action_for_status(task_status, pending_label="Review", completed_label="Reviewed")
            if gate_action == "local_sample_existing":
                label = "Decide whether to reuse the existing staged sample folder"
                action_label = _action_for_status(task_status, pending_label="Review choice", completed_label="Resolved")
            source_key = f"approval:{block.id}"
            seen_sources.add(source_key)
            _upsert_task(
                session,
                existing_by_source,
                {
                    "project_id": project_id,
                    "source_key": source_key,
                    "kind": "approval",
                    "title": label,
                    "status": task_status,
                    "priority": "high",
                    "source_type": "block",
                    "source_id": block.id,
                    "parent_task_id": workflow_parent_ids.get(block.parent_id) if block.parent_id else None,
                    "action_label": action_label,
                    "action_target": _build_target("block", block_id=block.id),
                    "metadata": {
                        "block_type": block.type,
                        "block_status": block.status,
                        "gate_action": gate_action,
                    },
                },
                owner_id=owner_id,
            )
            continue

        if block.type == "DOWNLOAD_TASK":
            files = payload.get("files", [])
            total_files = payload.get("total_files", len(files))
            task_status = {
                "RUNNING": "RUNNING",
                "DONE": "COMPLETED",
                "FAILED": "FAILED",
            }.get(block.status, "RUNNING")
            source_key = f"download:{block.id}"
            seen_sources.add(source_key)
            download_task = _upsert_task(
                session,
                existing_by_source,
                {
                    "project_id": project_id,
                    "source_key": source_key,
                    "kind": "download",
                    "title": f"Download {total_files} file{'s' if total_files != 1 else ''}",
                    "status": task_status,
                    "priority": "high" if task_status == "FAILED" else "normal",
                    "source_type": "block",
                    "source_id": block.id,
                    "action_label": _action_for_status(task_status, pending_label="Open task", completed_label="Downloaded"),
                    "action_target": _build_target("block", block_id=block.id),
                    "metadata": {
                        "download_id": payload.get("download_id"),
                        "downloaded": payload.get("downloaded", 0),
                        "total_files": total_files,
                    },
                },
                owner_id=owner_id,
            )
            for child_spec in _iter_download_file_specs(project_id, download_task.id, block, payload):
                seen_sources.add(child_spec["source_key"])
                _upsert_task(session, existing_by_source, child_spec, owner_id=owner_id)
            continue

        if block.type != "EXECUTION_JOB":
            continue

        run_uuid = payload.get("run_uuid", "")
        if run_uuid and (run_uuid in workflow_run_ids or payload.get("workflow_plan_block_id")):
            continue
        sample_name = payload.get("sample_name") or "Sample"
        source_key = f"job:{block.id}"
        task_status = {
            "RUNNING": "RUNNING",
            "DONE": "COMPLETED",
            "FAILED": "FAILED",
        }.get(block.status, "PENDING")
        seen_sources.add(source_key)
        run_task = _upsert_task(
            session,
            existing_by_source,
            {
                "project_id": project_id,
                "source_key": source_key,
                "kind": "run",
                "title": f"Run analysis for {sample_name}",
                "status": task_status,
                "priority": "high" if task_status == "FAILED" else "normal",
                "source_type": "block",
                "source_id": block.id,
                "action_label": _action_for_status(task_status, pending_label="View progress", completed_label="View run"),
                "action_target": _build_target("block", block_id=block.id, run_uuid=run_uuid),
                "metadata": {
                    "run_uuid": run_uuid,
                    "sample_name": sample_name,
                    "mode": payload.get("mode"),
                    "progress_percent": payload.get("job_status", {}).get("progress_percent", 0),
                },
            },
            owner_id=owner_id,
        )

        for child_spec in _iter_workflow_stage_specs(
            project_id,
            run_task.id,
            block,
            payload,
            run_uuid=run_uuid,
            sample_name=sample_name,
        ):
            seen_sources.add(child_spec["source_key"])
            _upsert_task(session, existing_by_source, child_spec, owner_id=owner_id)

        if block.status == "DONE":
            analysis_source = f"analysis:{run_uuid or block.id}"
            analysis_status = "COMPLETED" if sample_name in analyzed_samples else "RUNNING"
            seen_sources.add(analysis_source)
            _upsert_task(
                session,
                existing_by_source,
                {
                    "project_id": project_id,
                    "source_key": analysis_source,
                    "kind": "analysis",
                    "title": f"Analyze results for {sample_name}",
                    "status": analysis_status,
                    "priority": "high",
                    "source_type": "run_uuid",
                    "source_id": run_uuid or block.id,
                    "parent_task_id": run_task.id,
                    "action_label": _action_for_status(analysis_status, pending_label="Analyzing", completed_label="Analysis ready"),
                    "action_target": _build_target("results", run_uuid=run_uuid),
                    "metadata": {
                        "run_uuid": run_uuid,
                        "sample_name": sample_name,
                    },
                },
                owner_id=owner_id,
            )

            review_source = f"review:{run_uuid or block.id}"
            review_status = "FOLLOW_UP" if sample_name in reviewed_samples else "PENDING"
            seen_sources.add(review_source)
            _upsert_task(
                session,
                existing_by_source,
                {
                    "project_id": project_id,
                    "source_key": review_source,
                    "kind": "result_review",
                    "title": f"Review results for {sample_name}",
                    "status": review_status,
                    "priority": "normal",
                    "source_type": "run_uuid",
                    "source_id": run_uuid or block.id,
                    "parent_task_id": run_task.id,
                    "action_label": "Open results",
                    "action_target": _build_target("results", run_uuid=run_uuid),
                    "metadata": {
                        "run_uuid": run_uuid,
                        "sample_name": sample_name,
                    },
                },
                owner_id=owner_id,
            )

        if block.status == "FAILED":
            retry_source = f"retry:{run_uuid or block.id}"
            seen_sources.add(retry_source)
            _upsert_task(
                session,
                existing_by_source,
                {
                    "project_id": project_id,
                    "source_key": retry_source,
                    "kind": "recovery",
                    "title": f"Fix or resubmit {sample_name}",
                    "status": "FOLLOW_UP",
                    "priority": "high",
                    "source_type": "run_uuid",
                    "source_id": run_uuid or block.id,
                    "parent_task_id": run_task.id,
                    "action_label": "Open run",
                    "action_target": _build_target("block", block_id=block.id, run_uuid=run_uuid),
                    "metadata": {
                        "run_uuid": run_uuid,
                        "sample_name": sample_name,
                        "error": payload.get("error") or payload.get("message"),
                    },
                },
                owner_id=owner_id,
            )

    now = datetime.datetime.utcnow()
    for task in existing_tasks:
        if task.source_key in seen_sources:
            continue
        if task.archived_at is None:
            task.archived_at = now
            task.updated_at = now

    session.commit()
    return session.execute(
        select(ProjectTask)
        .where(ProjectTask.project_id == project_id)
        .where(ProjectTask.archived_at.is_(None))
        .order_by(ProjectTask.created_at.asc())
    ).scalars().all()


def build_task_sections(tasks: list[ProjectTask]) -> dict:
    all_task_dicts = [task_to_dict(task) for task in tasks]
    child_map: dict[str, list[dict]] = {}
    for task in all_task_dicts:
        parent_id = task.get("parent_task_id")
        if not parent_id:
            continue
        child_map.setdefault(parent_id, []).append(task)

    for children in child_map.values():
        children.sort(key=lambda item: item.get("metadata", {}).get("order_index", 9999))

    sections = {
        "pending": [],
        "running": [],
        "follow_up": [],
        "completed": [],
    }
    for task, data in zip(tasks, all_task_dicts):
        data["children"] = child_map.get(task.id, [])
        if task.status == "RUNNING":
            sections["running"].append(data)
        elif task.status in {"FOLLOW_UP", "FAILED"}:
            sections["follow_up"].append(data)
        elif task.status in {"COMPLETED", "CANCELLED"}:
            sections["completed"].append(data)
        else:
            sections["pending"].append(data)
    return sections


def update_task_action(session, project_id: str, task_id: str, action: str) -> ProjectTask | None:
    task = session.execute(
        select(ProjectTask)
        .where(ProjectTask.project_id == project_id)
        .where(ProjectTask.id == task_id)
    ).scalar_one_or_none()
    if task is None:
        return None

    now = datetime.datetime.utcnow()
    metadata = _task_metadata(task)
    if action == "complete":
        metadata["user_completed"] = True
        metadata.pop("archived_by_user", None)
        task.status = "COMPLETED"
        task.completed_at = now
        task.archived_at = None
    elif action == "archive":
        metadata["archived_by_user"] = True
        task.archived_at = now
    elif action == "reopen":
        metadata.pop("user_completed", None)
        metadata.pop("archived_by_user", None)
        task.archived_at = None
        task.completed_at = None
        if task.kind == "recovery":
            task.status = "FOLLOW_UP"
        else:
            task.status = "PENDING"
    else:
        raise ValueError(f"Unsupported task action: {action}")

    task.metadata_json = json.dumps(metadata)
    task.updated_at = now
    session.commit()

    sync_project_tasks(session, project_id)
    return session.execute(
        select(ProjectTask)
        .where(ProjectTask.project_id == project_id)
        .where(ProjectTask.id == task_id)
    ).scalar_one_or_none()


def clear_project_tasks(session, project_id: str) -> None:
    tasks = session.execute(
        select(ProjectTask).where(ProjectTask.project_id == project_id)
    ).scalars().all()
    for task in tasks:
        session.delete(task)
    session.commit()