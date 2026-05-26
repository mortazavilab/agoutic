from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from launchpad.models import DogmeJob as LaunchpadDogmeJob, SSHProfile as LaunchpadSSHProfile
from cortex.conversation_state import _build_conversation_state
from cortex.db_helpers import _create_block_internal
from cortex.job_polling import _auto_trigger_analysis, poll_job_status
from cortex.llm_validators import get_block_payload
from cortex.models import Project, ProjectBlock, User
from cortex.remote_orchestration import _launchpad_internal_headers, _launchpad_rest_base_url


@dataclass
class WorkflowCommand:
    action: Literal["rerun", "reanalyze", "delete", "rename", "use", "import", "sync", "cancel_sync", "list_tracked"]
    workflow_ref: str = ""
    workflow_refs: list[str] = field(default_factory=list)
    new_name: str = ""
    source_path: str = ""
    source_kind: Literal["local", "slurm"] = "local"
    ssh_profile_nickname: str = ""
    full_copy: bool = False
    force: bool = False
    sample_name: str = ""
    mode: str = ""
    reference_genome: list[str] = field(default_factory=list)
    modifications: str | None = None


_SLASH_RERUN = re.compile(r"^/rerun(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_SLASH_REANALYZE = re.compile(r"^/(?:reanaly[sz]e|rerun-analysis|auto-analyze)(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_SLASH_DELETE = re.compile(r"^/delete(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_SLASH_RENAME = re.compile(r"^/rename\s+(\S+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_SLASH_USE = re.compile(r"^/use\s+(\S+)$", re.IGNORECASE)
_SLASH_LIST_TRACKED = re.compile(r"^/(?:list-launchpad-workflows|list-tracked-workflows)$", re.IGNORECASE)
_SLASH_IMPORT = re.compile(r"^/import-workflow(?:\s+.*)?$", re.IGNORECASE | re.DOTALL)
_SLASH_SYNC = re.compile(r"^/sync-workflows?(?:\s+.*)?$", re.IGNORECASE | re.DOTALL)
_SLASH_CANCEL_SYNC = re.compile(r"^/cancel-sync(?:\s+.*)?$", re.IGNORECASE | re.DOTALL)

_NL_RERUN = re.compile(r"^(?:please\s+)?rerun(?:\s+(.+))?$", re.IGNORECASE | re.DOTALL)
_NL_REANALYZE = re.compile(r"^(?:please\s+)?re-?analy[sz]e(?:\s+(.+))?$", re.IGNORECASE | re.DOTALL)
_NL_RERUN_ANALYSIS = re.compile(
    r"^(?:please\s+)?(?:re-?run|rerun|regenerate)\s+(?:the\s+)?(?:(?:automatic|auto)\s+)?analysis(?:\s+(?:for|of))?(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
_NL_ANALYZE_WORKFLOW = re.compile(
    r"^(?:please\s+)?analy[sz]e(?:\s+(?:results?\s+(?:for|of)\s+)?)?(workflow\S*(?:[\s,]+(?:and\s+)?workflow\S*)*)$",
    re.IGNORECASE | re.DOTALL,
)
_NL_DELETE = re.compile(r"^(?:please\s+)?delete(?:\s+(.+))?$", re.IGNORECASE | re.DOTALL)
_NL_RENAME = re.compile(r"^(?:please\s+)?rename\s+(\S+)\s+(?:to\s+)?(.+)$", re.IGNORECASE | re.DOTALL)
_NL_USE = re.compile(
    r"^(?:please\s+)?(?:use|switch\s+to|set\s+(?:active\s+)?workflow(?:\s+to)?)\s+(\S+)$",
    re.IGNORECASE,
)
_NL_LIST_TRACKED = re.compile(
    r"^(?:please\s+)?(?:list|show)(?:\s+me)?\s+(?:the\s+)?(?:launchpad|tracked)\s+workflows?(?:\s+for\s+this\s+project)?$",
    re.IGNORECASE,
)
_NL_IMPORT = re.compile(r"^(?:please\s+)?import\s+(remote\s+)?workflow\s+from\s+(.+)$", re.IGNORECASE | re.DOTALL)
_NL_SYNC = re.compile(
    r"^(?:please\s+)?(?:sync(?:\s+workflows?)?|sync\s+results(?:\s+for)?)?(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
_NL_CANCEL_SYNC = re.compile(
    r"^(?:please\s+)?(?:cancel|stop)\s+sync(?:\s+for)?(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
_IMPORT_PROFILE_SUFFIX = re.compile(r"^(?P<path>.+?)\s+(?:on|using|via)\s+(?P<profile>[A-Za-z0-9_.-]+)\s*$", re.IGNORECASE | re.DOTALL)


def _workflow_reference_candidates(job) -> list[str]:
    candidates = [
        getattr(job, "workflow_alias", None),
        getattr(job, "workflow_folder_name", None),
        getattr(job, "workflow_display_name", None),
        getattr(job, "sample_name", None),
    ]
    for path_attr in ("nextflow_work_dir", "remote_work_dir"):
        raw_path = str(getattr(job, path_attr, "") or "").strip().rstrip("/")
        if not raw_path:
            continue
        folder_name = raw_path.rsplit("/", 1)[-1].strip()
        if folder_name:
            candidates.append(folder_name)
    return candidates


def _workflow_path_tail(job) -> str | None:
    for path_attr in ("nextflow_work_dir", "remote_work_dir"):
        raw_path = str(getattr(job, path_attr, "") or "").strip().rstrip("/")
        if raw_path:
            tail = raw_path.rsplit("/", 1)[-1].strip()
            if tail:
                return tail
    return None


def _workflow_label(job) -> str:
    return (
        str(getattr(job, "workflow_folder_name", "") or "").strip()
        or str(getattr(job, "workflow_alias", "") or "").strip()
        or str(_workflow_path_tail(job) or "").strip()
        or str(getattr(job, "run_uuid", "") or "").strip()
    )


def _job_work_directory(job) -> str:
    for path_attr in ("nextflow_work_dir", "output_directory", "remote_work_dir", "remote_output_dir"):
        raw_path = str(getattr(job, path_attr, "") or "").strip().rstrip("/")
        if raw_path:
            return raw_path
    return ""


def _job_results_ready(job) -> bool:
    status = str(getattr(job, "status", "") or "").upper()
    if status != "COMPLETED":
        return False
    result_destination = str(getattr(job, "result_destination", "") or "").strip().lower()
    if result_destination not in {"local", "both"}:
        return True
    return str(getattr(job, "transfer_state", "") or "") == "outputs_downloaded"


def _normalize_workflow_ref(ref: str) -> str:
    value = str(ref or "").strip().rstrip(",").rstrip(".").rstrip(";").rstrip(":")
    return value.strip()


def _parse_workflow_ref_list(raw: str | None) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    text = re.sub(r"\band\b", ",", text, flags=re.IGNORECASE)
    refs = []
    for part in re.split(r"[\s,]+", text):
        ref = _normalize_workflow_ref(part)
        if ref:
            refs.append(ref)
    return refs


def _command_with_refs(action: str, raw_refs: str | None = None, **kwargs) -> WorkflowCommand:
    refs = _parse_workflow_ref_list(raw_refs)
    return WorkflowCommand(
        action=action,
        workflow_ref=refs[0] if refs else "",
        workflow_refs=refs,
        **kwargs,
    )


def _build_workflow_command_history(session: Session, project_id: str) -> tuple[list, list[dict]]:
    history_blocks = list(
        session.execute(
            select(ProjectBlock)
            .where(ProjectBlock.project_id == project_id)
            .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB", "WORKFLOW_PLAN", "PENDING_ACTION"]))
            .order_by(ProjectBlock.seq.asc())
        ).scalars().all()
    )

    conversation_history: list[dict] = []
    for block in history_blocks:
        payload = get_block_payload(block)
        if block.type == "USER_MESSAGE":
            conversation_history.append({"role": "user", "content": payload.get("text", "")})
        elif block.type == "AGENT_PLAN":
            conversation_history.append({"role": "assistant", "content": payload.get("markdown", "")})
    return history_blocks, conversation_history


def _load_active_workflow_ref(session: Session, project_id: str) -> str | None:
    history_blocks, conversation_history = _build_workflow_command_history(session, project_id)
    state = _build_conversation_state(
        "welcome",
        conversation_history,
        history_blocks=history_blocks,
        project_id=project_id,
    )
    work_dir = str(getattr(state, "work_dir", "") or "").strip().rstrip("/")
    if work_dir:
        tail = work_dir.rsplit("/", 1)[-1].strip()
        if tail:
            return tail
    workflows = getattr(state, "workflows", []) or []
    active_index = getattr(state, "active_workflow_index", None)
    if isinstance(active_index, int) and 0 <= active_index < len(workflows):
        work_dir = str(workflows[active_index].get("work_dir", "") or "").strip().rstrip("/")
        if work_dir:
            tail = work_dir.rsplit("/", 1)[-1].strip()
            if tail:
                return tail
    return None


def _resolve_command_refs(session: Session, project_id: str, command: WorkflowCommand) -> list[str]:
    refs = [_normalize_workflow_ref(ref) for ref in (command.workflow_refs or []) if _normalize_workflow_ref(ref)]
    if refs:
        return refs
    if command.workflow_ref:
        ref = _normalize_workflow_ref(command.workflow_ref)
        return [ref] if ref else []
    active_ref = _load_active_workflow_ref(session, project_id)
    return [active_ref] if active_ref else []


def _missing_workflow_target_message(action: str) -> str:
    examples = {
        "reanalyze": "Try `reanalyze`, `reanalyze workflow5`, or `reanalyze workflow5, workflow6`.",
        "rerun": "Try `rerun`, `rerun workflow5`, or `rerun workflow5, workflow6`.",
        "delete": "Try `delete active workflow`, `delete workflow5`, or `delete workflow5, workflow6`.",
        "sync": "Try `sync workflow`, `sync workflow5`, or `sync workflow5, workflow6`.",
        "cancel_sync": "Try `cancel sync workflow5`.",
    }
    return (
        "I couldn't determine which workflow to use. "
        "Set an active workflow with `/use <workflow>` or name one explicitly. "
        + examples.get(action, "")
    ).strip()


def _combine_workflow_messages(action: str, messages: list[str]) -> str:
    if not messages:
        return "No workflow actions were executed."
    if len(messages) == 1:
        return messages[0]
    title_map = {
        "reanalyze": "Manual workflow analysis results:",
        "rerun": "Workflow rerun results:",
        "delete": "Workflow delete results:",
        "sync": "Workflow sync results:",
        "cancel_sync": "Workflow sync-cancel results:",
    }
    header = title_map.get(action, "Workflow command results:")
    body = "\n".join(f"- {message}" for message in messages)
    return f"{header}\n{body}"


def _format_tracked_workflows(session: Session, project_id: str) -> str:
    jobs = list(
        session.execute(
            select(LaunchpadDogmeJob)
            .where(LaunchpadDogmeJob.project_id == project_id)
            .order_by(desc(LaunchpadDogmeJob.submitted_at), desc(LaunchpadDogmeJob.run_uuid))
        ).scalars().all()
    )
    tracked_jobs = [job for job in jobs if str(getattr(job, "status", "") or "").upper() != "DELETED"]
    if not tracked_jobs:
        return (
            "Launchpad is not tracking any workflows for this project. "
            "The project directory may still contain untracked workflow folders on disk."
        )

    lines = [f"Tracked Launchpad workflows for this project ({len(tracked_jobs)}):"]
    for job in tracked_jobs:
        label = _workflow_label(job)
        alias = str(getattr(job, "workflow_alias", "") or "").strip()
        sample = str(getattr(job, "workflow_display_name", "") or getattr(job, "sample_name", "") or "").strip()
        status = str(getattr(job, "status", "") or "UNKNOWN").strip()
        run_uuid = str(getattr(job, "run_uuid", "") or "").strip()

        parts = [f"`{label}`"]
        if alias and alias != label:
            parts.append(f"alias `{alias}`")
        if sample and sample not in {label, alias}:
            parts.append(f"sample `{sample}`")
        parts.append(f"status `{status}`")
        parts.append(f"run UUID `{run_uuid}`")
        lines.append(f"- {' | '.join(parts)}")

    return "\n".join(lines)


def _delete_untracked_workflow_dir(project_dir: str | None, workflow_ref: str) -> str | None:
    raw_project_dir = str(project_dir or "").strip()
    ref = str(workflow_ref or "").strip().rstrip("/")
    if not raw_project_dir or not ref or "/" in ref or ref in {".", ".."}:
        return None
    if not ref.lower().startswith("workflow"):
        return None

    project_root = Path(raw_project_dir).expanduser()
    try:
        resolved_root = project_root.resolve()
    except OSError:
        return None

    candidate = (resolved_root / ref).resolve()
    if candidate.parent != resolved_root:
        return None
    if not candidate.exists() or not candidate.is_dir() or candidate.is_symlink():
        return None

    file_count = 0
    for _root, _dirs, files in os.walk(candidate):
        file_count += len(files)
    shutil.rmtree(candidate)
    return (
        f"Deleted untracked workflow folder `{candidate.name}` ({file_count} files removed). "
        "It was present on disk but not tracked by Launchpad."
    )


def parse_workflow_command(message: str) -> WorkflowCommand | None:
    msg = str(message or "").strip()
    if not msg.startswith("/"):
        return None

    if _SLASH_LIST_TRACKED.match(msg):
        return WorkflowCommand(action="list_tracked")

    if _SLASH_IMPORT.match(msg):
        return _parse_import_workflow_command(msg)

    if _SLASH_SYNC.match(msg):
        return _parse_sync_workflow_command(msg)

    if _SLASH_CANCEL_SYNC.match(msg):
        return _parse_cancel_sync_command(msg)

    match = _SLASH_RERUN.match(msg)
    if match:
        return _command_with_refs("rerun", match.group(1))

    match = _SLASH_REANALYZE.match(msg)
    if match:
        return _command_with_refs("reanalyze", match.group(1))

    match = _SLASH_DELETE.match(msg)
    if match:
        return _command_with_refs("delete", match.group(1))

    match = _SLASH_RENAME.match(msg)
    if match:
        return WorkflowCommand(
            action="rename",
            workflow_ref=match.group(1).strip(),
            new_name=match.group(2).strip(),
        )

    match = _SLASH_USE.match(msg)
    if match:
        return WorkflowCommand(action="use", workflow_ref=match.group(1).strip())

    return None


def detect_workflow_intent(message: str) -> WorkflowCommand | None:
    msg = str(message or "").strip()
    if not msg or msg.startswith("/"):
        return None

    match = _NL_LIST_TRACKED.match(msg)
    if match:
        return WorkflowCommand(action="list_tracked")

    match = _NL_RERUN_ANALYSIS.match(msg)
    if match:
        return _command_with_refs("reanalyze", match.group(1))

    match = _NL_ANALYZE_WORKFLOW.match(msg)
    if match:
        return _command_with_refs("reanalyze", match.group(1))

    match = _NL_RERUN.match(msg)
    if match:
        return _command_with_refs("rerun", match.group(1))

    match = _NL_REANALYZE.match(msg)
    if match:
        return _command_with_refs("reanalyze", match.group(1))

    match = _NL_DELETE.match(msg)
    if match:
        _raw_refs = match.group(1)
        if _raw_refs and _raw_refs.lower().strip() in {"workflow", "active workflow", "the active workflow"}:
            _raw_refs = None
        return _command_with_refs("delete", _raw_refs)

    match = _NL_RENAME.match(msg)
    if match:
        return WorkflowCommand(
            action="rename",
            workflow_ref=match.group(1).strip(),
            new_name=match.group(2).strip(),
        )

    match = _NL_USE.match(msg)
    if match:
        return WorkflowCommand(action="use", workflow_ref=match.group(1).strip())

    match = _NL_IMPORT.match(msg)
    if match:
        source_kind = "slurm" if match.group(1) else "local"
        source_path = match.group(2).strip()
        ssh_profile_nickname = ""
        if source_kind == "slurm":
            source_path, ssh_profile_nickname = _split_import_source_and_profile(source_path)
        return WorkflowCommand(
            action="import",
            source_kind=source_kind,
            source_path=source_path,
            ssh_profile_nickname=ssh_profile_nickname,
        )

    match = _NL_SYNC.match(msg)
    if match:
        _raw_refs = match.group(1)
        if msg.lower() in {"sync", "sync workflow", "sync workflows", "sync results"}:
            _raw_refs = None
        return _command_with_refs("sync", _raw_refs)

    match = _NL_CANCEL_SYNC.match(msg)
    if match:
        return _command_with_refs("cancel_sync", match.group(1))

    return None


def resolve_workflow_reference(session: Session, project_id: str, workflow_ref: str):
    normalized = str(workflow_ref or "").strip().lower()
    if not normalized:
        return None

    jobs = list(
        session.execute(
            select(LaunchpadDogmeJob)
            .where(LaunchpadDogmeJob.project_id == project_id)
            .order_by(desc(LaunchpadDogmeJob.submitted_at), desc(LaunchpadDogmeJob.run_uuid))
        ).scalars().all()
    )
    if not jobs:
        return None

    def _matches(job) -> bool:
        return any(
            str(value or "").strip().lower() == normalized
            for value in _workflow_reference_candidates(job)
            if value
        )

    matches = [job for job in jobs if _matches(job)]
    if not matches:
        return None

    for job in matches:
        if str(getattr(job, "status", "") or "").upper() != "DELETED":
            return job
    return matches[0]


async def execute_workflow_command(
    session: Session,
    command: WorkflowCommand,
    *,
    project_id: str,
    project_dir: str | None = None,
    owner_id: str | None = None,
    model: str | None = None,
) -> str:
    if command.action == "list_tracked":
        return _format_tracked_workflows(session, project_id)

    target_refs = _resolve_command_refs(session, project_id, command)
    if command.action in {"rerun", "delete", "sync", "cancel_sync"} and not target_refs:
        return _missing_workflow_target_message(command.action)

    job = None
    if command.action == "rename":
        job = resolve_workflow_reference(session, project_id, command.workflow_ref)
        if job is None:
            return f"I couldn't find `{command.workflow_ref}` in this project."

    base_url = _launchpad_rest_base_url()
    headers = _launchpad_internal_headers()

    async with httpx.AsyncClient(timeout=60.0) as client:
        if command.action == "import":
            if not command.source_path:
                return (
                    "Usage: `/import-workflow <path> [--remote] [--profile NAME] [--full-copy] [--sample-name NAME] `"
                    "`[--mode DNA|RNA|CDNA] [--reference GRCh38,mm39] [--modifications MODS]`."
                )
            owner_username, project_slug = _get_project_import_context(session, project_id)
            payload = {
                "project_id": project_id,
                "user_id": owner_id or "",
                "username": owner_username,
                "project_slug": project_slug,
                "source_path": command.source_path,
                "source_kind": command.source_kind,
                "full_copy": command.full_copy,
            }
            if command.ssh_profile_nickname:
                if not owner_id:
                    return "Import failed: no user context was available to resolve the requested SSH profile."
                ssh_profile_id = _resolve_import_ssh_profile_id(session, owner_id, command.ssh_profile_nickname)
                if not ssh_profile_id:
                    return f"Import failed: I couldn't find an enabled SSH profile named `{command.ssh_profile_nickname}`."
                payload["ssh_profile_id"] = ssh_profile_id
            if command.sample_name:
                payload["sample_name"] = command.sample_name
            if command.mode:
                payload["mode"] = command.mode
            if command.reference_genome:
                payload["reference_genome"] = command.reference_genome
            if command.modifications is not None:
                payload["modifications"] = command.modifications

            resp = await client.post(f"{base_url}/jobs/import", headers=headers, json=payload)
            if resp.status_code >= 400:
                detail = _response_detail(resp)
                return f"Import failed for `{command.source_path}`: {detail}"
            import_payload = resp.json() or {}
            if owner_id:
                _register_imported_job_block(
                    session,
                    project_id=project_id,
                    owner_id=owner_id,
                    model=model or "default",
                    payload=import_payload,
                )

            workflow_name = str(import_payload.get("work_directory") or "").rstrip("/").rsplit("/", 1)[-1] or "workflow"
            message = (
                f"Imported `{command.source_path}` into `{workflow_name}`. "
                f"Run UUID: `{import_payload.get('run_uuid', '')}`."
            )
            warning = str(import_payload.get("import_warning_message") or "").strip()
            if warning:
                message = f"{message} {warning}"
            return message

        if command.action in {"sync", "cancel_sync", "rerun", "delete"}:
            messages: list[str] = []
            for workflow_ref in target_refs:
                job = resolve_workflow_reference(session, project_id, workflow_ref)
                if job is None and command.action == "delete":
                    deleted_message = _delete_untracked_workflow_dir(project_dir, workflow_ref)
                    if deleted_message is not None:
                        messages.append(deleted_message)
                        continue
                if job is None:
                    messages.append(f"I couldn't find `{workflow_ref}` in this project.")
                    continue

                if command.action == "sync":
                    transfer_state = str(getattr(job, "transfer_state", "") or "").strip().lower()
                    imported_retry = bool(getattr(job, "imported_source_kind", None)) and transfer_state == "outputs_downloaded"
                    sync_force = bool(command.force or imported_retry)
                    resp = await client.post(
                        f"{base_url}/jobs/{job.run_uuid}/sync-results",
                        headers=headers,
                        params={"force": str(sync_force).lower()},
                    )
                    if resp.status_code >= 400:
                        detail = _response_detail(resp)
                        messages.append(f"Sync failed for `{workflow_ref}`: {detail}")
                        continue
                    payload = resp.json() or {}
                    warning = str(payload.get("import_warning_message") or "").strip()
                    message = payload.get("message") or f"Sync started for `{workflow_ref}`."
                    messages.append(f"{message} {warning}".strip())
                    continue

                if command.action == "cancel_sync":
                    resp = await client.post(f"{base_url}/jobs/{job.run_uuid}/cancel", headers=headers)
                    if resp.status_code >= 400:
                        detail = _response_detail(resp)
                        messages.append(f"Cancel sync failed for `{workflow_ref}`: {detail}")
                        continue
                    payload = resp.json() or {}
                    messages.append(payload.get("message") or f"Sync cancelled for `{workflow_ref}`.")
                    continue

                if command.action == "rerun":
                    resp = await client.post(f"{base_url}/jobs/{job.run_uuid}/rerun", headers=headers)
                    if resp.status_code >= 400:
                        detail = _response_detail(resp)
                        messages.append(f"Rerun failed for `{workflow_ref}`: {detail}")
                        continue
                    payload = resp.json() or {}
                    messages.append(
                        f"Rerunning `{workflow_ref}` as `{payload.get('sample_name') or job.sample_name}`. New run UUID: `{payload.get('run_uuid', '')}`."
                    )
                    continue

                resp = await client.delete(f"{base_url}/jobs/{job.run_uuid}", headers=headers)
                if resp.status_code >= 400:
                    detail = _response_detail(resp)
                    messages.append(f"Delete failed for `{workflow_ref}`: {detail}")
                    continue
                payload = resp.json() or {}
                messages.append(payload.get("message") or f"Deleted `{workflow_ref}`.")

            return _combine_workflow_messages(command.action, messages)

        resp = await client.post(
            f"{base_url}/jobs/{job.run_uuid}/rename",
            headers=headers,
            json={"new_name": command.new_name},
        )
        if resp.status_code >= 400:
            detail = _response_detail(resp)
            return f"Rename failed for `{command.workflow_ref}`: {detail}"
        payload = resp.json() or {}
        return f"Renamed `{command.workflow_ref}` to `{payload.get('new_name') or command.new_name}`."


async def execute_manual_workflow_analysis(
    session: Session,
    command: WorkflowCommand,
    *,
    project_id: str,
    owner_id: str | None = None,
    model: str | None = None,
) -> tuple[object | None, str | None]:
    target_refs = _resolve_command_refs(session, project_id, command)
    if not target_refs:
        return None, _missing_workflow_target_message("reanalyze")

    job = resolve_workflow_reference(session, project_id, target_refs[0])
    if job is None:
        return None, f"I couldn't find `{target_refs[0]}` in this project."

    label = _workflow_label(job)
    if not _job_results_ready(job):
        status = str(getattr(job, "status", "") or "UNKNOWN").strip() or "UNKNOWN"
        return None, (
            f"`{label}` is currently `{status}`. Manual automatic analysis is only available "
            "after the workflow finishes and any required local result sync is complete."
        )

    agent_block = await _auto_trigger_analysis(
        project_id,
        str(getattr(job, "run_uuid", "") or ""),
        {
            "sample_name": str(getattr(job, "sample_name", "") or "Unknown").strip() or "Unknown",
            "mode": str(getattr(job, "mode", "") or "DNA").strip() or "DNA",
            "model": model or "default",
            "work_directory": _job_work_directory(job),
        },
        owner_id,
        persist_request_message=False,
        force=True,
    )
    if agent_block is None:
        return None, f"Manual automatic analysis failed for `{label}`."
    return agent_block, None


def execute_use_workflow(
    conv_state,
    project_dir: str,
    workflow_ref: str,
) -> tuple[object, str]:
    """Switch active workflow in *conv_state* and return (updated_state, markdown).

    Resolution order:
    1. Match against ``conv_state.workflows[i]["work_dir"]`` folder name.
    2. Fall back to checking ``project_dir / workflow_ref`` on disk.

    Returns the **mutated** conv_state and a user-facing markdown string.
    """
    ref = workflow_ref.strip().rstrip("/")

    # 1. Try known workflows in conversation state
    for idx, wf in enumerate(conv_state.workflows or []):
        wd = wf.get("work_dir") or wf.get("work_directory") or ""
        folder = wd.rstrip("/").rsplit("/", 1)[-1] if wd else ""
        if folder and folder.lower() == ref.lower():
            conv_state.work_dir = wd
            conv_state.active_workflow_index = idx
            return conv_state, f"Switched active workflow to **{ref}** (`{wd}`)."

    # 2. Fall back to disk check
    if project_dir:
        candidate = os.path.join(project_dir, ref)
        if os.path.isdir(candidate):
            conv_state.work_dir = candidate
            conv_state.active_workflow_index = None
            return conv_state, f"Switched active workflow to **{ref}** (`{candidate}`)."

    return conv_state, f"Could not find **{ref}** in known workflows or on disk."


def _response_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json() or {}
        detail = payload.get("detail") or payload.get("message")
        if detail:
            return str(detail)
    except Exception:
        pass
    text = (resp.text or "").strip()
    return text[:200] or f"HTTP {resp.status_code}"


def _parse_import_workflow_command(message: str) -> WorkflowCommand:
    tokens = shlex.split(str(message or "").strip())
    command = WorkflowCommand(action="import")
    index = 1
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered == "--remote":
            command.source_kind = "slurm"
            index += 1
            continue
        if lowered in {"--profile", "--ssh-profile"} and index + 1 < len(tokens):
            command.ssh_profile_nickname = tokens[index + 1].strip()
            command.source_kind = "slurm"
            index += 2
            continue
        if lowered == "--full-copy":
            command.full_copy = True
            index += 1
            continue
        if lowered == "--sample-name" and index + 1 < len(tokens):
            command.sample_name = tokens[index + 1].strip()
            index += 2
            continue
        if lowered == "--mode" and index + 1 < len(tokens):
            command.mode = tokens[index + 1].strip().upper()
            index += 2
            continue
        if lowered == "--reference" and index + 1 < len(tokens):
            raw_references = tokens[index + 1].split(",")
            command.reference_genome = [ref.strip() for ref in raw_references if ref.strip()]
            index += 2
            continue
        if lowered == "--modifications" and index + 1 < len(tokens):
            command.modifications = tokens[index + 1]
            index += 2
            continue
        if not command.source_path:
            command.source_path = token
        index += 1
    return command


def _split_import_source_and_profile(source_text: str) -> tuple[str, str]:
    raw = str(source_text or "").strip()
    if not raw:
        return "", ""
    match = _IMPORT_PROFILE_SUFFIX.match(raw)
    if not match:
        return raw, ""
    candidate_path = match.group("path").strip()
    candidate_profile = match.group("profile").strip()
    if candidate_path.startswith("/") and candidate_profile:
        return candidate_path, candidate_profile
    return raw, ""


def _resolve_import_ssh_profile_id(session: Session, owner_id: str, nickname: str) -> str | None:
    normalized = str(nickname or "").strip().lower()
    if not owner_id or not normalized:
        return None

    profiles = list(
        session.execute(
            select(LaunchpadSSHProfile)
            .where(LaunchpadSSHProfile.user_id == owner_id)
            .where(LaunchpadSSHProfile.is_enabled.is_(True))
        ).scalars().all()
    )
    for profile in profiles:
        if str(getattr(profile, "nickname", "") or "").strip().lower() == normalized:
            return str(profile.id)
    return None


def _parse_sync_workflow_command(message: str) -> WorkflowCommand:
    tokens = shlex.split(str(message or "").strip())
    command = WorkflowCommand(action="sync")
    index = 1
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered == "--force":
            command.force = True
        else:
            ref = _normalize_workflow_ref(token)
            if ref:
                command.workflow_refs.append(ref)
        index += 1
    if command.workflow_refs:
        command.workflow_ref = command.workflow_refs[0]
    return command


def _parse_cancel_sync_command(message: str) -> WorkflowCommand:
    tokens = shlex.split(str(message or "").strip())
    command = WorkflowCommand(action="cancel_sync")
    if len(tokens) > 1:
        command.workflow_refs = [
            ref for ref in (_normalize_workflow_ref(token) for token in tokens[1:]) if ref
        ]
        if command.workflow_refs:
            command.workflow_ref = command.workflow_refs[0]
    return command


def _get_project_import_context(session: Session, project_id: str) -> tuple[str | None, str | None]:
    project = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
    if project is None:
        return None, None
    owner_username = None
    if getattr(project, "owner_id", None):
        owner_username = session.execute(
            select(User.username).where(User.id == project.owner_id)
        ).scalar_one_or_none()
    return owner_username, getattr(project, "slug", None)


def _register_imported_job_block(
    session: Session,
    *,
    project_id: str,
    owner_id: str,
    model: str,
    payload: dict,
) -> None:
    run_uuid = str(payload.get("run_uuid") or "").strip()
    work_directory = str(payload.get("work_directory") or "").strip()
    sample_name = str(payload.get("sample_name") or "sample").strip() or "sample"
    mode = str(payload.get("mode") or "").strip()
    transfer_state = str(payload.get("transfer_state") or "").strip().lower()
    execution_mode = str(payload.get("execution_mode") or "local").strip().lower()
    importing = execution_mode == "slurm" and transfer_state != "outputs_downloaded"
    job_status = {
        "status": "RUNNING" if importing else "COMPLETED",
        "progress_percent": 99 if importing else 100,
        "message": str(payload.get("message") or "Imported workflow registered."),
        "tasks": {},
        "transfer_state": payload.get("transfer_state"),
        "import_warning_message": payload.get("import_warning_message"),
        "imported_source_complete": payload.get("imported_source_complete"),
    }
    block = _create_block_internal(
        session,
        project_id,
        "EXECUTION_JOB",
        {
            "run_uuid": run_uuid,
            "work_directory": work_directory,
            "sample_name": sample_name,
            "mode": mode,
            "run_type": "dogme",
            "model": model,
            "status": job_status["status"],
            "message": job_status["message"],
            "job_status": job_status,
            "logs": [],
            "imported_source_kind": payload.get("imported_source_kind"),
            "imported_source_complete": payload.get("imported_source_complete"),
            "import_warning_message": payload.get("import_warning_message"),
        },
        status="RUNNING" if importing else "DONE",
        owner_id=owner_id,
    )
    if importing and run_uuid:
        asyncio.create_task(poll_job_status(project_id, block.id, run_uuid))