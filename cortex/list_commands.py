from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.chat_sync_handler import _collect_df_map, _render_list_dfs
from cortex.config import get_service_url
from cortex.conversation_state import _extract_job_context_from_history
from cortex.models import ProjectAccess, UserFile, UserFileProjectLink
from cortex.path_helpers import _resolve_workflow_path
from launchpad.models import DogmeJob as LaunchpadDogmeJob, RemoteStagedSample

logger = get_logger(__name__)

_USAGE = (
    "Usage: /list <samples|staged|imported|dfs|workflows|files> "
    "[target] [--profile NAME] [--project] [--depth N]"
)

_ACTION_ALIASES = {
    "sample": "samples",
    "samples": "samples",
    "local-samples": "samples",
    "local": "samples",
    "staged": "staged",
    "staged-samples": "staged",
    "imported": "imported",
    "imported-samples": "imported",
    "imported-workflows": "imported",
    "dfs": "dfs",
    "dataframes": "dfs",
    "workflows": "workflows",
    "workflow": "workflows",
    "files": "files",
    "file": "files",
}

_PROFILE_REF_RE = re.compile(
    r"\b(?:on|using|via)\s+(?:the\s+)?([A-Za-z0-9_.-]+)(?:\s+profile)?\b",
    re.IGNORECASE,
)
_NL_SAMPLES_RE = re.compile(
    r"^(?:please\s+)?(?:list|show|what)\s+(?:my\s+)?(?:local\s+)?samples?\b.*$",
    re.IGNORECASE,
)
_NL_STAGED_RE = re.compile(
    r"^(?:please\s+)?(?:list|show|what)\s+(?:my\s+)?staged\s+samples?\b.*$",
    re.IGNORECASE,
)
_NL_IMPORTED_RE = re.compile(
    r"^(?:please\s+)?(?:list|show|what)\s+(?:my\s+)?imported\s+(?:samples?|workflows?)\b.*$",
    re.IGNORECASE,
)
_NL_IMPORTED_HAVE_RE = re.compile(
    r"^(?:please\s+)?what\s+imported\s+(?:samples?|workflows?)\s+do\s+i\s+have\b.*$",
    re.IGNORECASE,
)
_NL_WORKFLOWS_RE = re.compile(
    r"^(?:please\s+)?(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b(?:\s+in\s+this\s+project)?$",
    re.IGNORECASE,
)
_NL_PROJECT_FILES_RE = re.compile(
    r"^(?:please\s+)?(?:list|show)\s+project\s+files?\b(?:\s+(?:in|under|at|of)\s+(.+?))?\s*$",
    re.IGNORECASE,
)
_NL_FILES_RE = re.compile(
    r"^(?:please\s+)?(?:list|show|what)\s+(?:the\s+)?files?\b(?:\s+(?:in|under|at|of)\s+(.+?)|\s+(.+?))?\s*$",
    re.IGNORECASE,
)


@dataclass
class ListCommand:
    action: Literal["samples", "staged", "imported", "dfs", "workflows", "files"]
    target_ref: str = ""
    profile_ref: str = ""
    project_scope: bool = False
    max_depth: int | None = None
    error: str = ""


def parse_list_command(message: str) -> ListCommand | None:
    msg = str(message or "").strip()
    if not msg.startswith("/"):
        return None

    try:
        tokens = shlex.split(msg)
    except ValueError as exc:
        return ListCommand(action="samples", error=f"Could not parse /list arguments: {exc}")

    if not tokens or tokens[0].lower() != "/list":
        return None
    if len(tokens) == 1:
        return ListCommand(action="samples", error=f"Choose what to list.\n\n{_USAGE}")

    action = _ACTION_ALIASES.get(tokens[1].strip().lower())
    if not action:
        return ListCommand(action="samples", error=f"Unsupported /list target '{tokens[1]}'.\n\n{_USAGE}")

    target_tokens: list[str] = []
    profile_ref = ""
    project_scope = False
    max_depth: int | None = None
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "--profile":
            if index + 1 >= len(tokens):
                return ListCommand(action=action, error="Missing value for --profile.")
            profile_ref = tokens[index + 1].strip()
            index += 2
            continue
        if token.startswith("--profile="):
            profile_ref = token.split("=", 1)[1].strip()
            index += 1
            continue
        if token == "--project":
            project_scope = True
            index += 1
            continue
        if token == "--depth":
            if index + 1 >= len(tokens):
                return ListCommand(action=action, error="Missing value for --depth.")
            try:
                max_depth = int(tokens[index + 1])
            except ValueError:
                return ListCommand(action=action, error="--depth must be an integer.")
            if max_depth <= 0:
                return ListCommand(action=action, error="--depth must be greater than zero.")
            index += 2
            continue
        if token.startswith("--depth="):
            try:
                max_depth = int(token.split("=", 1)[1])
            except ValueError:
                return ListCommand(action=action, error="--depth must be an integer.")
            if max_depth <= 0:
                return ListCommand(action=action, error="--depth must be greater than zero.")
            index += 1
            continue
        if token.startswith("--"):
            return ListCommand(action=action, error=f"Unknown option: {token}")

        target_tokens.append(token)
        index += 1

    return ListCommand(
        action=action,
        target_ref=" ".join(target_tokens).strip(),
        profile_ref=profile_ref,
        project_scope=project_scope,
        max_depth=max_depth,
    )


def detect_list_intent(message: str) -> ListCommand | None:
    msg = str(message or "").strip()
    if not msg or msg.startswith("/"):
        return None

    if _NL_SAMPLES_RE.match(msg):
        return ListCommand(action="samples")

    if _NL_STAGED_RE.match(msg):
        profile_ref = _extract_profile_ref(msg)
        return ListCommand(action="staged", profile_ref=profile_ref)

    if _NL_IMPORTED_RE.match(msg) or _NL_IMPORTED_HAVE_RE.match(msg):
        return ListCommand(action="imported")

    if _NL_WORKFLOWS_RE.match(msg):
        return ListCommand(action="workflows")

    match = _NL_PROJECT_FILES_RE.match(msg)
    if match:
        target_ref = _clean_target_ref(match.group(1) or "")
        return ListCommand(action="files", target_ref=target_ref, project_scope=True)

    match = _NL_FILES_RE.match(msg)
    if match:
        target_ref = _clean_target_ref(match.group(1) or match.group(2) or "")
        return ListCommand(action="files", target_ref=target_ref)

    return None


def _extract_profile_ref(message: str) -> str:
    match = _PROFILE_REF_RE.search(str(message or ""))
    return (match.group(1) or "").strip() if match else ""


def _clean_target_ref(raw_value: str) -> str:
    return str(raw_value or "").strip().strip('"\'`')


def _usage_message(detail: str = "") -> str:
    if detail:
        return f"{detail}\n\n{_USAGE}"
    return _USAGE


def _format_size(size_bytes: int | None) -> str:
    if size_bytes is None or size_bytes < 0:
        return "—"
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.2f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    if size_bytes == 0:
        return "0 B"
    return f"{size_bytes} B"


def _format_timestamp(value) -> str:
    if not value:
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10] or "—"


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join("---" for _ in headers) + " |"
    body_lines = []
    for row in rows:
        escaped = [str(cell).replace("|", "\\|") for cell in row]
        body_lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join([header_line, separator_line, *body_lines])


def _workflow_disk_name(job) -> str:
    for attr in ("workflow_folder_name", "nextflow_work_dir", "remote_work_dir"):
        raw_value = str(getattr(job, attr, "") or "").strip().rstrip("/")
        if not raw_value:
            continue
        tail = raw_value.rsplit("/", 1)[-1].strip()
        if tail:
            return tail
    workflow_index = getattr(job, "workflow_index", None)
    if workflow_index is not None:
        return f"workflow{workflow_index}"
    alias = str(getattr(job, "workflow_alias", "") or "").strip()
    if alias:
        return alias
    return str(getattr(job, "sample_name", "") or getattr(job, "run_uuid", "") or "workflow").strip()


def _workflow_reference_candidates(job) -> list[str]:
    candidates = [
        getattr(job, "workflow_alias", None),
        getattr(job, "workflow_folder_name", None),
        getattr(job, "workflow_display_name", None),
        getattr(job, "sample_name", None),
    ]
    for path_attr in ("output_directory", "nextflow_work_dir", "remote_work_dir"):
        raw_path = str(getattr(job, path_attr, "") or "").strip().rstrip("/")
        if not raw_path:
            continue
        tail = raw_path.rsplit("/", 1)[-1].strip()
        if tail:
            candidates.append(tail)
    return candidates


def _job_local_work_dir(job) -> str:
    for path_attr in ("output_directory", "nextflow_work_dir"):
        raw_path = str(getattr(job, path_attr, "") or "").strip().rstrip("/")
        if raw_path:
            return raw_path
    return ""


def _project_jobs(session: Session, project_id: str) -> list:
    if not project_id:
        return []
    try:
        return list(
            session.execute(
                select(LaunchpadDogmeJob)
                .where(LaunchpadDogmeJob.project_id == project_id)
                .order_by(desc(LaunchpadDogmeJob.submitted_at), desc(LaunchpadDogmeJob.run_uuid))
            ).scalars().all()
        )
    except Exception:
        return []


def _resolve_tracked_local_work_dir(session: Session, project_id: str, *, run_uuid: str = "", workflow_ref: str = "") -> str:
    jobs = _project_jobs(session, project_id)
    if not jobs:
        return ""

    normalized_run_uuid = str(run_uuid or "").strip().lower()
    if normalized_run_uuid:
        for job in jobs:
            if str(getattr(job, "run_uuid", "") or "").strip().lower() == normalized_run_uuid:
                return _job_local_work_dir(job)

    normalized_ref = str(workflow_ref or "").strip().lower()
    if not normalized_ref:
        return ""
    for job in jobs:
        if any(str(candidate or "").strip().lower() == normalized_ref for candidate in _workflow_reference_candidates(job) if candidate):
            return _job_local_work_dir(job)
    return ""


def _localize_workflows(session: Session, project_id: str, workflows: list[dict]) -> list[dict]:
    localized: list[dict] = []
    for workflow in workflows:
        localized_workflow = dict(workflow)
        local_work_dir = _resolve_tracked_local_work_dir(
            session,
            project_id,
            run_uuid=str(workflow.get("run_uuid") or ""),
            workflow_ref=str(workflow.get("sample_name") or workflow.get("work_dir") or ""),
        )
        if local_work_dir:
            localized_workflow["work_dir"] = local_work_dir
        localized.append(localized_workflow)
    return localized


def _workflow_display_name(job) -> str:
    sample = str(getattr(job, "workflow_display_name", "") or getattr(job, "sample_name", "") or "").strip()
    return sample or _workflow_disk_name(job)


def _list_local_sample_rows(session: Session, user_id: str) -> list[dict]:
    files = list(
        session.execute(
            select(UserFile)
            .where(UserFile.user_id == user_id)
            .order_by(UserFile.sample_name, UserFile.filename)
        ).scalars().all()
    )
    if not files:
        return []

    links = list(
        session.execute(
            select(UserFileProjectLink).where(
                UserFileProjectLink.user_file_id.in_([item.id for item in files])
            )
        ).scalars().all()
    )
    access_rows = list(
        session.execute(
            select(ProjectAccess).where(ProjectAccess.user_id == user_id)
        ).scalars().all()
    )
    project_name_by_id = {
        str(getattr(item, "project_id", "") or ""): str(getattr(item, "project_name", "") or "").strip()
        for item in access_rows
    }
    project_ids_by_file: dict[str, set[str]] = {}
    for link in links:
        project_ids_by_file.setdefault(str(link.user_file_id), set()).add(str(link.project_id))

    grouped: dict[str, dict] = {}
    for item in files:
        key = str(item.sample_name or item.filename or "sample").strip()
        bucket = grouped.setdefault(
            key,
            {
                "sample_name": key,
                "file_count": 0,
                "total_size_bytes": 0,
                "sources": set(),
                "projects": set(),
                "latest_added": None,
            },
        )
        bucket["file_count"] += 1
        bucket["total_size_bytes"] += int(item.size_bytes or 0)
        if item.source:
            bucket["sources"].add(str(item.source))
        for project_id in project_ids_by_file.get(str(item.id), set()):
            project_name = project_name_by_id.get(project_id) or project_id
            if project_name:
                bucket["projects"].add(project_name)
        created_at = getattr(item, "created_at", None)
        if bucket["latest_added"] is None or str(created_at) > str(bucket["latest_added"]):
            bucket["latest_added"] = created_at

    rows = []
    for item in grouped.values():
        rows.append(
            {
                "sample_name": item["sample_name"],
                "file_count": item["file_count"],
                "total_size": _format_size(item["total_size_bytes"]),
                "sources": ", ".join(sorted(item["sources"])) or "—",
                "projects": ", ".join(sorted(item["projects"])) or "—",
                "added": _format_timestamp(item["latest_added"]),
            }
        )
    rows.sort(key=lambda row: row["sample_name"].lower())
    return rows


def _list_staged_sample_rows(session: Session, user_id: str, profile_ref: str = "") -> list[dict]:
    staged_rows = list(
        session.execute(
            select(RemoteStagedSample)
            .where(RemoteStagedSample.user_id == user_id)
            .order_by(desc(RemoteStagedSample.updated_at), RemoteStagedSample.sample_name)
        ).scalars().all()
    )
    normalized_profile_ref = str(profile_ref or "").strip().lower()
    if normalized_profile_ref:
        staged_rows = [
            row
            for row in staged_rows
            if normalized_profile_ref in {
                str(getattr(row, "ssh_profile_id", "") or "").strip().lower(),
                str(getattr(row, "ssh_profile_nickname", "") or "").strip().lower(),
            }
        ]

    return [
        {
            "sample_name": str(getattr(row, "sample_name", "") or "").strip() or "sample",
            "mode": str(getattr(row, "mode", "") or "").strip() or "—",
            "profile": str(getattr(row, "ssh_profile_nickname", "") or getattr(row, "ssh_profile_id", "") or "").strip() or "—",
            "status": str(getattr(row, "status", "") or "").strip() or "UNKNOWN",
            "remote_data_path": str(getattr(row, "remote_data_path", "") or "").strip() or "—",
            "last_staged": _format_timestamp(getattr(row, "last_staged_at", None)),
            "last_used": _format_timestamp(getattr(row, "last_used_at", None)),
        }
        for row in staged_rows
    ]


def _list_imported_sample_rows(session: Session, user_id: str) -> list[dict]:
    access_rows = list(
        session.execute(
            select(ProjectAccess)
            .where(ProjectAccess.user_id == user_id)
            .order_by(desc(ProjectAccess.last_accessed))
        ).scalars().all()
    )
    accessible_project_ids = [str(item.project_id) for item in access_rows if getattr(item, "project_id", None)]
    if not accessible_project_ids:
        return []
    project_name_by_id = {
        str(item.project_id): str(getattr(item, "project_name", "") or item.project_id)
        for item in access_rows
    }

    jobs = list(
        session.execute(
            select(LaunchpadDogmeJob)
            .where(LaunchpadDogmeJob.project_id.in_(accessible_project_ids))
            .where(LaunchpadDogmeJob.imported_source_kind.is_not(None))
            .order_by(desc(LaunchpadDogmeJob.submitted_at), desc(LaunchpadDogmeJob.run_uuid))
        ).scalars().all()
    )
    rows = []
    for job in jobs:
        status = str(getattr(job, "status", "") or "").upper()
        if status == "DELETED":
            continue
        workflow_name = _workflow_disk_name(job)
        rows.append(
            {
                "sample_name": _workflow_display_name(job),
                "project": project_name_by_id.get(str(getattr(job, "project_id", "") or ""), str(getattr(job, "project_id", "") or "")),
                "workflow": workflow_name,
                "source_kind": str(getattr(job, "imported_source_kind", "") or "").strip() or "—",
                "source_path": str(getattr(job, "imported_source_path", "") or "").strip() or "—",
                "status": str(getattr(job, "status", "") or "UNKNOWN").strip(),
                "completed": _format_timestamp(getattr(job, "completed_at", None) or getattr(job, "submitted_at", None)),
            }
        )
    return rows


def _list_workflow_rows(session: Session, project_id: str, project_dir: str = "") -> list[dict]:
    jobs = list(
        session.execute(
            select(LaunchpadDogmeJob)
            .where(LaunchpadDogmeJob.project_id == project_id)
            .order_by(desc(LaunchpadDogmeJob.submitted_at), desc(LaunchpadDogmeJob.run_uuid))
        ).scalars().all()
    )
    rows_by_disk_name: dict[str, dict] = {}
    for job in jobs:
        status = str(getattr(job, "status", "") or "").upper()
        if status == "DELETED":
            continue
        disk_name = _workflow_disk_name(job)
        rows_by_disk_name[disk_name] = {
            "workflow": disk_name,
            "display_name": _workflow_display_name(job),
            "tracked": "yes",
            "on_disk": "no",
            "status": str(getattr(job, "status", "") or "UNKNOWN").strip(),
            "run_uuid": str(getattr(job, "run_uuid", "") or "").strip(),
        }

    raw_project_dir = str(project_dir or "").strip()
    if raw_project_dir and os.path.isdir(raw_project_dir):
        try:
            for entry in sorted(Path(raw_project_dir).iterdir(), key=lambda item: item.name.lower()):
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if not re.fullmatch(r"workflow\d+", entry.name, re.IGNORECASE):
                    continue
                bucket = rows_by_disk_name.setdefault(
                    entry.name,
                    {
                        "workflow": entry.name,
                        "display_name": entry.name,
                        "tracked": "no",
                        "on_disk": "yes",
                        "status": "UNTRACKED",
                        "run_uuid": "—",
                    },
                )
                bucket["on_disk"] = "yes"
        except OSError:
            logger.warning("Failed to inspect project workflow folders", project_dir=raw_project_dir)

    rows = list(rows_by_disk_name.values())
    rows.sort(key=lambda row: row["workflow"].lower())
    return rows


def _resolve_file_list_target(
    session: Session,
    project_id: str,
    command: ListCommand,
    *,
    history_blocks: list | None = None,
    project_dir: str = "",
) -> tuple[str, int | None]:
    context = _extract_job_context_from_history(None, history_blocks=history_blocks)
    workflows = _localize_workflows(session, project_id, context.get("workflows") or [])
    active_run_uuid = str(context.get("run_uuid") or "")
    default_work_dir = _resolve_tracked_local_work_dir(session, project_id, run_uuid=active_run_uuid) or str(context.get("work_dir") or "") or str(project_dir or "")
    normalized_project_dir = str(project_dir or "").strip()

    if command.project_scope:
        if not normalized_project_dir:
            return "", None
        if command.target_ref:
            return f"{normalized_project_dir.rstrip('/')}/{command.target_ref.strip().strip('/')}".rstrip("/"), command.max_depth or 1
        return normalized_project_dir, command.max_depth or 1

    target_ref = str(command.target_ref or "").strip()
    if target_ref:
        parts = target_ref.replace("\\", "/").split("/", 1)
        workflow_head = parts[0].strip()
        remainder = parts[1].strip("/") if len(parts) > 1 else ""
        tracked_work_dir = _resolve_tracked_local_work_dir(session, project_id, workflow_ref=workflow_head)
        if tracked_work_dir:
            resolved = tracked_work_dir.rstrip("/")
            if remainder:
                resolved = f"{resolved}/{remainder}"
            return resolved, command.max_depth or 1
        resolved = _resolve_workflow_path(target_ref, default_work_dir, workflows)
        return resolved, command.max_depth or 1

    return default_work_dir, command.max_depth


async def _list_file_rows(
    command: ListCommand,
    *,
    session: Session,
    project_id: str,
    history_blocks: list | None = None,
    project_dir: str = "",
) -> tuple[str, list[dict], int]:
    work_dir, max_depth = _resolve_file_list_target(session, project_id, command, history_blocks=history_blocks, project_dir=project_dir)
    if not work_dir:
        return "", [], 0

    analyzer_url = get_service_url("analyzer")
    client = MCPHttpClient(name="analyzer", base_url=analyzer_url)
    await client.connect()
    try:
        result = await client.call_tool(
            "list_job_files",
            work_dir=work_dir,
            max_depth=max_depth,
        )
    finally:
        await client.disconnect()

    if not isinstance(result, dict) or result.get("success") is False:
        detail = ""
        if isinstance(result, dict):
            detail = str(result.get("detail") or result.get("error") or "")
        raise RuntimeError(detail or f"Could not list files under {work_dir}.")

    files = result.get("files") or []
    rows = []
    for item in files:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "path": str(item.get("path") or item.get("name") or "").strip() or "—",
                "size": _format_size(item.get("size")),
                "modified": _format_timestamp(item.get("modified_time")),
            }
        )
    return str(result.get("work_dir") or work_dir), rows, int(result.get("file_count") or len(rows))


async def execute_list_command(
    session: Session,
    command: ListCommand,
    *,
    user_id: str,
    project_id: str = "",
    project_dir: str = "",
    history_blocks: list | None = None,
) -> str:
    if command.error:
        return _usage_message(command.error)

    if command.action == "dfs":
        df_map = _collect_df_map(history_blocks or [], db=session, user_id=user_id, project_id=project_id)
        return _render_list_dfs(df_map)

    if command.action == "samples":
        rows = _list_local_sample_rows(session, user_id)
        if not rows:
            return "No local samples found in your central data folder yet."
        return "\n".join(
            [
                f"Local samples ({len(rows)}):",
                _render_table(
                    ["Sample", "Files", "Total Size", "Sources", "Projects", "Added"],
                    [
                        [row["sample_name"], row["file_count"], row["total_size"], row["sources"], row["projects"], row["added"]]
                        for row in rows
                    ],
                ),
            ]
        )

    if command.action == "staged":
        rows = _list_staged_sample_rows(session, user_id, command.profile_ref)
        if not rows:
            if command.profile_ref:
                return f"No staged samples found for profile `{command.profile_ref}`."
            return "No staged remote samples found yet."
        return "\n".join(
            [
                f"Staged samples ({len(rows)}):",
                _render_table(
                    ["Sample", "Mode", "Profile", "Status", "Remote Path", "Last Staged", "Last Used"],
                    [
                        [
                            row["sample_name"],
                            row["mode"],
                            row["profile"],
                            row["status"],
                            row["remote_data_path"],
                            row["last_staged"],
                            row["last_used"],
                        ]
                        for row in rows
                    ],
                ),
            ]
        )

    if command.action == "imported":
        rows = _list_imported_sample_rows(session, user_id)
        if not rows:
            return "No imported samples found across your accessible projects."
        return "\n".join(
            [
                f"Imported samples ({len(rows)}):",
                _render_table(
                    ["Sample", "Project", "Workflow", "Source", "Imported From", "Status", "Completed"],
                    [
                        [
                            row["sample_name"],
                            row["project"],
                            row["workflow"],
                            row["source_kind"],
                            row["source_path"],
                            row["status"],
                            row["completed"],
                        ]
                        for row in rows
                    ],
                ),
            ]
        )

    if command.action == "workflows":
        rows = _list_workflow_rows(session, project_id, project_dir=project_dir)
        if not rows:
            return "No workflows found for this project yet."
        return "\n".join(
            [
                f"Workflows in this project ({len(rows)}):",
                _render_table(
                    ["Workflow", "Display Name", "Tracked", "On Disk", "Status", "Run UUID"],
                    [
                        [
                            row["workflow"],
                            row["display_name"],
                            row["tracked"],
                            row["on_disk"],
                            row["status"],
                            row["run_uuid"],
                        ]
                        for row in rows
                    ],
                ),
            ]
        )

    if command.action == "files":
        try:
            work_dir, rows, file_count = await _list_file_rows(
                command,
                session=session,
                project_id=project_id,
                history_blocks=history_blocks,
                project_dir=project_dir,
            )
        except Exception as exc:
            logger.error("List files command failed", target_ref=command.target_ref, error=str(exc))
            return f"/list files failed: {exc}"

        if not work_dir:
            return (
                "I could not resolve an active workflow or project directory for `/list files`. "
                "Use `/use <workflow>` first, target a workflow path such as `workflow10/annot`, or pass `--project`."
            )
        if not rows:
            return f"No files found under `{work_dir}`."
        return "\n".join(
            [
                f"Files under `{work_dir}` ({file_count}):",
                _render_table(
                    ["Path", "Size", "Modified"],
                    [[row["path"], row["size"], row["modified"]] for row in rows],
                ),
            ]
        )

    return _usage_message()