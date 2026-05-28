from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, TextIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.database import SessionLocal
from cortex.models import Conversation, ConversationMessage, Project, User
from launchpad.config import JobStatus
from launchpad.models import DogmeJob, StagingTask


DEFAULT_ACTIVE_JOB_MAX_AGE_HOURS = 168
ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)
ACTIVE_JOB_TRANSFER_STATES = (
    "uploading_inputs",
    "inputs_uploaded",
    "pending_import",
    "downloading_outputs",
)
ACTIVE_STAGING_TASK_STATUSES = ("queued", "running")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _isoformat(value: Any) -> str | None:
    dt = _normalize_datetime(value)
    return dt.isoformat() if dt is not None else None


def _human_duration(total_seconds: float | int | None) -> str:
    if total_seconds is None:
        return "unknown"
    seconds = max(int(total_seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _relative_time(value: Any, *, now: datetime) -> str:
    dt = _normalize_datetime(value)
    if dt is None:
        return "unknown"
    delta = max(int((now - dt).total_seconds()), 0)
    if delta < 60:
        return f"{delta} seconds ago"
    if delta < 3600:
        minutes = delta // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if delta < 86400:
        hours = delta // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = delta // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def _runtime_seconds(started_at: Any, *, now: datetime) -> int | None:
    dt = _normalize_datetime(started_at)
    if dt is None:
        return None
    return max(int((now - dt).total_seconds()), 0)


def _is_stale(reference_at: Any, *, now: datetime, max_age_hours: int) -> bool:
    runtime_seconds = _runtime_seconds(reference_at, now=now)
    if runtime_seconds is None:
        return False
    return runtime_seconds > max(int(max_age_hours * 3600), 0)


def _display_name(user: User | None) -> str:
    if user is None:
        return "—"
    return str(user.display_name or user.username or user.email or "—")


def _loads_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def collect_recent_user_activity(
    session: Session,
    *,
    last_active_window_minutes: int,
    now: datetime,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(minutes=last_active_window_minutes)
    activities: dict[str, dict[str, Any]] = {}

    chat_rows = session.execute(
        select(
            Conversation.user_id,
            func.max(ConversationMessage.created_at).label("last_chat_at"),
        )
        .join(ConversationMessage, ConversationMessage.conversation_id == Conversation.id)
        .group_by(Conversation.user_id)
        .having(func.max(ConversationMessage.created_at) >= cutoff)
    ).all()
    for row in chat_rows:
        user_id = str(row.user_id or "").strip()
        last_chat_at = _normalize_datetime(row.last_chat_at)
        if not user_id or last_chat_at is None:
            continue
        activities[user_id] = {
            "last_activity_at": last_chat_at,
            "source": "chat",
        }

    job_rows = session.execute(
        select(
            DogmeJob.user_id,
            func.max(DogmeJob.submitted_at).label("last_job_at"),
        )
        .where(DogmeJob.user_id.is_not(None))
        .group_by(DogmeJob.user_id)
        .having(func.max(DogmeJob.submitted_at) >= cutoff)
    ).all()
    for row in job_rows:
        user_id = str(row.user_id or "").strip()
        last_job_at = _normalize_datetime(row.last_job_at)
        if not user_id or last_job_at is None:
            continue
        current = activities.get(user_id)
        if current is None or last_job_at > current["last_activity_at"]:
            activities[user_id] = {
                "last_activity_at": last_job_at,
                "source": "job",
            }

    if not activities:
        return []

    users = {
        user.id: user
        for user in session.execute(
            select(User).where(User.id.in_(list(activities)))
        ).scalars()
    }

    results: list[dict[str, Any]] = []
    for user_id, activity in activities.items():
        user = users.get(user_id)
        activity_at = activity["last_activity_at"]
        results.append(
            {
                "user_id": user_id,
                "name": _display_name(user),
                "email": str(getattr(user, "email", "—") or "—"),
                "last_activity_at": activity_at.isoformat(),
                "source": activity["source"],
                "relative": _relative_time(activity_at, now=now),
            }
        )

    results.sort(key=lambda item: item["last_activity_at"], reverse=True)
    return results


def collect_running_jobs(
    session: Session,
    *,
    now: datetime,
    active_job_max_age_hours: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = session.execute(
        select(
            DogmeJob.run_uuid,
            DogmeJob.workflow_display_name,
            DogmeJob.workflow_key,
            DogmeJob.workflow_folder_name,
            DogmeJob.status,
            DogmeJob.started_at,
            DogmeJob.submitted_at,
            User.email,
            Project.name,
        )
        .select_from(DogmeJob)
        .outerjoin(User, User.id == DogmeJob.user_id)
        .outerjoin(Project, Project.id == DogmeJob.project_id)
        .where(DogmeJob.status.in_(ACTIVE_JOB_STATUSES))
    ).all()

    active_jobs: list[dict[str, Any]] = []
    stale_jobs: list[dict[str, Any]] = []
    for row in rows:
        started_at = _normalize_datetime(row.started_at) or _normalize_datetime(row.submitted_at)
        runtime_seconds = _runtime_seconds(started_at, now=now)
        workflow_type = str(
            row.workflow_display_name
            or row.workflow_key
            or row.workflow_folder_name
            or "unknown"
        )
        record = {
            "run_uuid": row.run_uuid,
            "run_uuid_short": row.run_uuid[:8],
            "workflow_type": workflow_type,
            "owner_email": str(row.email or "—"),
            "project_name": str(row.name or "—"),
            "state": str(row.status or ""),
            "started_at": _isoformat(started_at),
            "runtime_duration": _human_duration(runtime_seconds),
            "runtime_seconds": runtime_seconds,
        }
        if _is_stale(started_at, now=now, max_age_hours=active_job_max_age_hours):
            stale_jobs.append(record)
        else:
            active_jobs.append(record)

    active_jobs.sort(key=lambda item: item["runtime_seconds"] or -1, reverse=True)
    stale_jobs.sort(key=lambda item: item["runtime_seconds"] or -1, reverse=True)
    return active_jobs, stale_jobs


def collect_active_chats(
    session: Session,
    *,
    chat_window_minutes: int,
    now: datetime,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(minutes=chat_window_minutes)
    rows = session.execute(
        select(
            Conversation.id,
            User.email,
            Project.name,
            func.max(ConversationMessage.created_at).label("last_message_at"),
            func.count(ConversationMessage.id).label("message_count"),
        )
        .join(ConversationMessage, ConversationMessage.conversation_id == Conversation.id)
        .outerjoin(User, User.id == Conversation.user_id)
        .outerjoin(Project, Project.id == Conversation.project_id)
        .where(ConversationMessage.created_at >= cutoff)
        .group_by(Conversation.id, User.email, Project.name)
    ).all()

    results = [
        {
            "conversation_id": row.id,
            "owner_email": str(row.email or "—"),
            "project_name": str(row.name or "—"),
            "last_message_at": _isoformat(row.last_message_at),
            "message_count": int(row.message_count or 0),
        }
        for row in rows
    ]
    results.sort(key=lambda item: item["last_message_at"] or "", reverse=True)
    return results


def collect_active_transfers(
    session: Session,
    *,
    now: datetime,
    active_job_max_age_hours: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active_transfers: list[dict[str, Any]] = []
    stale_transfers: list[dict[str, Any]] = []

    job_rows = session.execute(
        select(
            DogmeJob.run_uuid,
            DogmeJob.transfer_state,
            DogmeJob.workflow_display_name,
            DogmeJob.workflow_key,
            DogmeJob.started_at,
            DogmeJob.submitted_at,
            User.email,
            Project.name,
        )
        .select_from(DogmeJob)
        .outerjoin(User, User.id == DogmeJob.user_id)
        .outerjoin(Project, Project.id == DogmeJob.project_id)
        .where(DogmeJob.transfer_state.in_(ACTIVE_JOB_TRANSFER_STATES))
    ).all()
    for row in job_rows:
        started_at = _normalize_datetime(row.started_at) or _normalize_datetime(row.submitted_at)
        runtime_seconds = _runtime_seconds(started_at, now=now)
        record = {
            "source": "dogme_job",
            "identifier": row.run_uuid[:8],
            "run_uuid": row.run_uuid,
            "state": str(row.transfer_state or ""),
            "owner_email": str(row.email or "—"),
            "project_name": str(row.name or "—"),
            "workflow_type": str(row.workflow_display_name or row.workflow_key or "unknown"),
            "started_at": _isoformat(started_at),
            "duration": _human_duration(runtime_seconds),
            "duration_seconds": runtime_seconds,
        }
        if _is_stale(started_at, now=now, max_age_hours=active_job_max_age_hours):
            stale_transfers.append(record)
        else:
            active_transfers.append(record)

    task_rows = session.execute(
        select(
            StagingTask.task_id,
            StagingTask.status,
            StagingTask.created_at,
            StagingTask.params_json,
        ).where(StagingTask.status.in_(ACTIVE_STAGING_TASK_STATUSES))
    ).all()

    user_ids = set()
    project_ids = set()
    parsed_params: dict[str, dict[str, Any]] = {}
    for row in task_rows:
        params = _loads_dict(row.params_json)
        parsed_params[row.task_id] = params
        user_id = str(params.get("user_id") or "").strip()
        project_id = str(params.get("project_id") or "").strip()
        if user_id:
            user_ids.add(user_id)
        if project_id:
            project_ids.add(project_id)

    users = {
        user.id: user
        for user in session.execute(select(User).where(User.id.in_(list(user_ids)))).scalars()
    } if user_ids else {}
    projects = {
        project.id: project
        for project in session.execute(select(Project).where(Project.id.in_(list(project_ids)))).scalars()
    } if project_ids else {}

    for row in task_rows:
        params = parsed_params.get(row.task_id, {})
        created_at = _normalize_datetime(row.created_at)
        duration_seconds = _runtime_seconds(created_at, now=now)
        user = users.get(str(params.get("user_id") or "").strip())
        project = projects.get(str(params.get("project_id") or "").strip())
        record = {
            "source": "staging_task",
            "identifier": row.task_id,
            "state": str(row.status or ""),
            "owner_email": str(getattr(user, "email", "—") or "—"),
            "project_name": str(getattr(project, "name", "—") or "—"),
            "workflow_type": str(params.get("sample_name") or params.get("mode") or "staging"),
            "started_at": _isoformat(created_at),
            "duration": _human_duration(duration_seconds),
            "duration_seconds": duration_seconds,
        }
        if _is_stale(created_at, now=now, max_age_hours=active_job_max_age_hours):
            stale_transfers.append(record)
        else:
            active_transfers.append(record)

    active_transfers.sort(key=lambda item: item.get("duration_seconds") or -1, reverse=True)
    stale_transfers.sort(key=lambda item: item.get("duration_seconds") or -1, reverse=True)
    return active_transfers, stale_transfers


def build_recommendation(snapshot: dict[str, Any]) -> dict[str, Any]:
    jobs = snapshot["jobs"]
    chats = snapshot["chats"]
    if not jobs and not chats:
        return {
            "status": "SAFE TO RESTART",
            "message": "SAFE TO RESTART",
            "longest_running_job": None,
        }

    longest_job = jobs[0] if jobs else None
    if longest_job is not None and longest_job.get("runtime_duration") not in {None, "unknown"}:
        detail = f"Longest-running active job: {longest_job['runtime_duration']}."
    elif jobs:
        detail = "Wait until running jobs complete."
    else:
        detail = "Wait until active chats are idle."

    if jobs and chats:
        message = (
            f"WAIT — {len(jobs)} running job(s) and {len(chats)} active chat session(s). "
            f"{detail}"
        )
    elif jobs:
        message = f"WAIT — {len(jobs)} running job(s). {detail}"
    else:
        message = f"WAIT — {len(chats)} active chat session(s). {detail}"

    return {
        "status": "WAIT",
        "message": message,
        "longest_running_job": longest_job,
    }


def build_snapshot(
    session: Session,
    *,
    last_active_window_minutes: int = 15,
    chat_window_minutes: int = 5,
    active_job_max_age_hours: int = DEFAULT_ACTIVE_JOB_MAX_AGE_HOURS,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or utc_now()
    jobs, stale_jobs = collect_running_jobs(
        session,
        now=current_time,
        active_job_max_age_hours=active_job_max_age_hours,
    )
    transfers, stale_transfers = collect_active_transfers(
        session,
        now=current_time,
        active_job_max_age_hours=active_job_max_age_hours,
    )
    snapshot = {
        "generated_at": current_time.isoformat(),
        "users": collect_recent_user_activity(
            session,
            last_active_window_minutes=last_active_window_minutes,
            now=current_time,
        ),
        "jobs": jobs,
        "stale_jobs": stale_jobs,
        "chats": collect_active_chats(
            session,
            chat_window_minutes=chat_window_minutes,
            now=current_time,
        ),
        "transfers": transfers,
        "stale_transfers": stale_transfers,
    }
    snapshot["recommendation"] = build_recommendation(snapshot)
    return snapshot


def render_text_report(
    snapshot: dict[str, Any],
    *,
    last_active_window_minutes: int,
    chat_window_minutes: int,
    active_job_max_age_hours: int,
) -> str:
    lines = [f"Generated at: {snapshot['generated_at']}", ""]

    lines.append(
        "=== Recently active users (approximated from chat and job activity — AGOUTIC does not track presence) ==="
    )
    if snapshot["users"]:
        for user in snapshot["users"]:
            lines.append(
                f"- {user['name']} <{user['email']}> | {user['last_activity_at']} | {user['source']} | {user['relative']}"
            )
    else:
        lines.append(
            f"No users with chat or job activity in the last {last_active_window_minutes} minute(s)."
        )

    lines.extend(["", "=== Currently running jobs ==="])
    if snapshot["jobs"]:
        for job in snapshot["jobs"]:
            lines.append(
                f"- {job['run_uuid_short']} | {job['workflow_type']} | {job['owner_email']} | {job['project_name']} | {job['state']} | started {job['started_at'] or 'unknown'} | runtime {job['runtime_duration']}"
            )
    else:
        lines.append(
            f"No active jobs in statuses: {', '.join(ACTIVE_JOB_STATUSES)}."
        )

    if snapshot["stale_jobs"]:
        lines.extend([
            "",
            f"=== Stale job rows (excluded from recommendation, age > {active_job_max_age_hours} hours) ===",
        ])
        for job in snapshot["stale_jobs"]:
            lines.append(
                f"- {job['run_uuid_short']} | {job['workflow_type']} | {job['owner_email']} | {job['project_name']} | {job['state']} | started {job['started_at'] or 'unknown'} | runtime {job['runtime_duration']}"
            )

    lines.extend(["", "=== Active chat sessions ==="])
    if snapshot["chats"]:
        for chat in snapshot["chats"]:
            lines.append(
                f"- {chat['owner_email']} | {chat['project_name']} | last message {chat['last_message_at']} | {chat['message_count']} message(s) in window"
            )
    else:
        lines.append(f"No chat activity in the last {chat_window_minutes} minute(s).")

    if snapshot["transfers"]:
        lines.extend(["", "=== Active transfers and workflow imports ==="])
        for transfer in snapshot["transfers"]:
            lines.append(
                f"- {transfer['source']} | {transfer['identifier']} | {transfer['state']} | {transfer['owner_email']} | {transfer['project_name']} | {transfer['workflow_type']} | {transfer['duration']}"
            )

    if snapshot["stale_transfers"]:
        lines.extend([
            "",
            f"=== Stale transfers (excluded from recommendation, age > {active_job_max_age_hours} hours) ===",
        ])
        for transfer in snapshot["stale_transfers"]:
            lines.append(
                f"- {transfer['source']} | {transfer['identifier']} | {transfer['state']} | {transfer['owner_email']} | {transfer['project_name']} | {transfer['workflow_type']} | {transfer['duration']}"
            )

    lines.extend(["", "=== Summary recommendation ===", snapshot["recommendation"]["message"]])
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only AGOUTIC maintenance status report")
    parser.add_argument("--last-active-window", type=int, default=15)
    parser.add_argument("--chat-window", type=int, default=5)
    parser.add_argument("--active-job-max-age", type=int, default=DEFAULT_ACTIVE_JOB_MAX_AGE_HOURS)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    session_factory=SessionLocal,
    stdout: TextIO | None = None,
) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    out = stdout or sys.stdout

    session = session_factory()
    try:
        snapshot = build_snapshot(
            session,
            last_active_window_minutes=args.last_active_window,
            chat_window_minutes=args.chat_window,
            active_job_max_age_hours=args.active_job_max_age,
        )
    finally:
        session.close()

    try:
        if args.quiet:
            out.write(f"{snapshot['recommendation']['status']}\n")
            return 0

        if args.as_json:
            json.dump(snapshot, out, indent=2)
            out.write("\n")
            return 0

        out.write(
            render_text_report(
                snapshot,
                last_active_window_minutes=args.last_active_window,
                chat_window_minutes=args.chat_window,
                active_job_max_age_hours=args.active_job_max_age,
            )
        )
        out.write("\n")
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())