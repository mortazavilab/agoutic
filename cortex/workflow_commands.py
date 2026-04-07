from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from launchpad.models import DogmeJob as LaunchpadDogmeJob
from cortex.remote_orchestration import _launchpad_internal_headers, _launchpad_rest_base_url


@dataclass
class WorkflowCommand:
    action: Literal["rerun", "delete", "rename"]
    workflow_ref: str
    new_name: str = ""


_SLASH_RERUN = re.compile(r"^/rerun\s+(\S+)$", re.IGNORECASE)
_SLASH_DELETE = re.compile(r"^/delete\s+(\S+)$", re.IGNORECASE)
_SLASH_RENAME = re.compile(r"^/rename\s+(\S+)\s+(.+)$", re.IGNORECASE | re.DOTALL)

_NL_RERUN = re.compile(r"^(?:please\s+)?rerun\s+(\S+)$", re.IGNORECASE)
_NL_DELETE = re.compile(r"^(?:please\s+)?delete\s+(\S+)$", re.IGNORECASE)
_NL_RENAME = re.compile(r"^(?:please\s+)?rename\s+(\S+)\s+(?:to\s+)?(.+)$", re.IGNORECASE | re.DOTALL)


def parse_workflow_command(message: str) -> WorkflowCommand | None:
    msg = str(message or "").strip()
    if not msg.startswith("/"):
        return None

    match = _SLASH_RERUN.match(msg)
    if match:
        return WorkflowCommand(action="rerun", workflow_ref=match.group(1).strip())

    match = _SLASH_DELETE.match(msg)
    if match:
        return WorkflowCommand(action="delete", workflow_ref=match.group(1).strip())

    match = _SLASH_RENAME.match(msg)
    if match:
        return WorkflowCommand(
            action="rename",
            workflow_ref=match.group(1).strip(),
            new_name=match.group(2).strip(),
        )

    return None


def detect_workflow_intent(message: str) -> WorkflowCommand | None:
    msg = str(message or "").strip()
    if not msg or msg.startswith("/"):
        return None

    match = _NL_RERUN.match(msg)
    if match:
        return WorkflowCommand(action="rerun", workflow_ref=match.group(1).strip())

    match = _NL_DELETE.match(msg)
    if match:
        return WorkflowCommand(action="delete", workflow_ref=match.group(1).strip())

    match = _NL_RENAME.match(msg)
    if match:
        return WorkflowCommand(
            action="rename",
            workflow_ref=match.group(1).strip(),
            new_name=match.group(2).strip(),
        )

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
        aliases = [
            getattr(job, "workflow_alias", None),
            getattr(job, "workflow_folder_name", None),
            getattr(job, "workflow_display_name", None),
            getattr(job, "sample_name", None),
        ]
        return any(str(value or "").strip().lower() == normalized for value in aliases if value)

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
) -> str:
    job = resolve_workflow_reference(session, project_id, command.workflow_ref)
    if job is None:
        return f"I couldn't find `{command.workflow_ref}` in this project."

    base_url = _launchpad_rest_base_url()
    headers = _launchpad_internal_headers()

    async with httpx.AsyncClient(timeout=60.0) as client:
        if command.action == "rerun":
            resp = await client.post(f"{base_url}/jobs/{job.run_uuid}/rerun", headers=headers)
            if resp.status_code >= 400:
                detail = _response_detail(resp)
                return f"Rerun failed for `{command.workflow_ref}`: {detail}"
            payload = resp.json() or {}
            return f"Rerunning `{command.workflow_ref}` as `{payload.get('sample_name') or job.sample_name}`. New run UUID: `{payload.get('run_uuid', '')}`."

        if command.action == "delete":
            resp = await client.delete(f"{base_url}/jobs/{job.run_uuid}", headers=headers)
            if resp.status_code >= 400:
                detail = _response_detail(resp)
                return f"Delete failed for `{command.workflow_ref}`: {detail}"
            payload = resp.json() or {}
            return payload.get("message") or f"Deleted `{command.workflow_ref}`."

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