"""Background staging worker for durable HPC file transfers.

Remote staging is a long-running rsync upload that should survive HTTP
timeouts, client disconnects, and Launchpad restarts. This module keeps a
hot in-memory registry for active tasks while persisting state transitions to
the database for restart recovery.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.logging_config import get_logger

logger = get_logger(__name__)

MAX_CONCURRENT_STAGING_TASKS = int(os.getenv("MAX_CONCURRENT_STAGING_TASKS", "3"))
_PROGRESS_PERSIST_INTERVAL_SECONDS = float(
    os.getenv("STAGING_TASK_PROGRESS_PERSIST_INTERVAL_SECONDS", "5")
)
_WAIT_REASON_KEY = "wait_reason"
_WAITING_FOR_AUTH_KEY = "waiting_for_auth"
_CANCELLED_TASK_MESSAGE = "Remote staging cancelled by user."

# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------

@dataclass
class StagingTaskState:
    """Mutable in-memory state for a single background staging task."""

    task_id: str
    status: str = "queued"  # queued | running | completed | failed | cancelled
    progress: dict = field(default_factory=dict)
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    params: dict = field(default_factory=dict)

    # asyncio.Task handle — set by the caller after create_task()
    _task: asyncio.Task | None = field(default=None, repr=False)
    _last_persisted_at: float = field(default=0.0, repr=False)
    _persist_lock: asyncio.Lock | None = field(default=None, repr=False)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d


def new_task_id() -> str:
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

_staging_tasks: dict[str, StagingTaskState] = {}


def get_staging_tasks() -> dict[str, StagingTaskState]:
    """Return the module-level staging-task registry."""
    return _staging_tasks


async def _load_staging_task_state(task_id: str) -> StagingTaskState:
    task = _staging_tasks.get(task_id)
    if task is None:
        await hydrate_incomplete_staging_tasks(task_id=task_id)
        task = _staging_tasks.get(task_id)

    if task is None:
        from launchpad.db import get_staging_task_record

        task_record = await get_staging_task_record(task_id)
        if task_record is None:
            raise LookupError(f"Staging task {task_id} not found")
        task = staging_task_state_from_record(task_record)

    return task


def staging_task_state_from_record(record: Any) -> StagingTaskState:
    """Rebuild in-memory task state from a persisted DB row."""
    task = StagingTaskState(
        task_id=record.task_id,
        status=record.status,
        progress=dict(record.progress_json or {}),
        result=record.result_json,
        error=record.error,
        created_at=float(record.created_at),
        updated_at=float(record.updated_at),
        params=dict(record.params_json or {}),
    )
    task._last_persisted_at = float(record.updated_at)
    return task


def _is_terminal_status(status: str) -> bool:
    return status in {"completed", "failed", "cancelled"}


def running_task_count() -> int:
    """Return the number of scheduled task coroutines currently occupying slots."""
    return sum(
        1
        for task in _staging_tasks.values()
        if task._task is not None and not task._task.done()
    )


def active_task_count() -> int:
    return sum(
        1
        for task in _staging_tasks.values()
        if task.status == "running"
        or (task.status == "queued" and not task.progress.get(_WAITING_FOR_AUTH_KEY))
    )


def _set_task_wait_reason(task: StagingTaskState, reason: str) -> bool:
    reason = str(reason or "").strip()
    if not reason:
        return _clear_task_wait_reason(task)
    if task.progress.get(_WAIT_REASON_KEY) == reason and task.progress.get(_WAITING_FOR_AUTH_KEY) is True:
        return False
    task.progress[_WAIT_REASON_KEY] = reason
    task.progress[_WAITING_FOR_AUTH_KEY] = True
    task.touch()
    return True


def _clear_task_wait_reason(task: StagingTaskState) -> bool:
    changed = False
    if task.progress.pop(_WAIT_REASON_KEY, None) is not None:
        changed = True
    if task.progress.pop(_WAITING_FOR_AUTH_KEY, None) is not None:
        changed = True
    if changed:
        task.touch()
    return changed


async def get_staging_task_start_block_reason(task: StagingTaskState) -> str | None:
    """Return a human-readable reason a recovered task cannot resume yet."""
    from launchpad.backends.local_auth_sessions import get_local_auth_session_manager
    from launchpad.backends.ssh_manager import resolve_key_file_path
    from launchpad.db import get_ssh_profile

    profile_id = str(task.params.get("ssh_profile_id") or "").strip()
    user_id = task.params.get("user_id")
    if not profile_id:
        return "Waiting for SSH profile configuration before staging can resume."

    profile = await get_ssh_profile(profile_id, user_id=user_id)
    if profile is None:
        return f"Waiting for SSH profile {profile_id} to become available before staging can resume."
    if not profile.is_enabled:
        return f"Waiting for SSH profile {profile.nickname or profile.id} to be re-enabled before staging can resume."

    if profile.auth_method == "ssh_agent":
        if not os.getenv("SSH_AUTH_SOCK"):
            return (
                "Waiting for SSH agent access after restart. Restart Launchpad from an authenticated shell "
                "or switch this profile to a readable key file."
            )
        return None

    if profile.auth_method == "key_file":
        if profile.local_username:
            session = await get_local_auth_session_manager().get_active_session(profile)
            if session is not None:
                return None
        try:
            resolved_key = Path(resolve_key_file_path(profile))
        except Exception as exc:
            return f"Waiting for SSH key access: {exc}"

        if resolved_key.exists() and resolved_key.is_file() and os.access(resolved_key, os.R_OK):
            return None

        if profile.local_username:
            return (
                "Waiting for the Remote Profile unlock to be re-established after restart before staging can resume."
            )
        return f"Waiting for Launchpad to access the configured SSH key: {resolved_key}"

    return None


async def persist_staging_task(
    task: StagingTaskState,
    *,
    force: bool = False,
    raise_on_error: bool = False,
) -> bool:
    """Persist task state to the DB, throttling progress-only updates."""
    if task._persist_lock is None:
        task._persist_lock = asyncio.Lock()

    async with task._persist_lock:
        now = time.time()
        if not force and (now - task._last_persisted_at) < _PROGRESS_PERSIST_INTERVAL_SECONDS:
            return False

        from launchpad.db import upsert_staging_task

        try:
            await upsert_staging_task(task)
        except Exception:
            logger.warning(
                "Failed to persist staging task state",
                task_id=task.task_id,
                exc_info=True,
            )
            if raise_on_error:
                raise
            return False

        task._last_persisted_at = time.time()
        return True


def schedule_staging_task_persist(task: StagingTaskState) -> None:
    """Schedule a best-effort async persist for non-terminal progress updates."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(persist_staging_task(task))


async def _mark_task_cancelled(task: StagingTaskState, message: str) -> None:
    _clear_task_wait_reason(task)
    task.status = "cancelled"
    task.error = str(message or _CANCELLED_TASK_MESSAGE).strip() or _CANCELLED_TASK_MESSAGE
    task.touch()
    await persist_staging_task(task, force=True)


async def _cancel_local_broker_transfer(task: StagingTaskState) -> bool:
    from launchpad.backends.local_auth_sessions import get_local_auth_session_manager
    from launchpad.db import get_ssh_profile

    profile_id = str(task.params.get("ssh_profile_id") or "").strip()
    user_id = str(task.params.get("user_id") or "").strip()
    if not profile_id or not user_id:
        return False

    profile = await get_ssh_profile(profile_id, user_id=user_id)
    if profile is None or profile.auth_method != "key_file" or not profile.local_username:
        return False

    session_manager = get_local_auth_session_manager()
    session = await session_manager.get_active_session(profile)
    if session is None:
        return False

    try:
        response = await session_manager.invoke(
            session,
            {
                "op": "cancel_request",
                "request_id": task.task_id,
                "timeout_seconds": 5,
            },
        )
    except Exception:
        logger.debug("Failed to cancel local broker transfer", task_id=task.task_id, exc_info=True)
        return False
    return bool(response.get("ok"))


async def cancel_staging_task(task_id: str, *, message: str = _CANCELLED_TASK_MESSAGE) -> StagingTaskState:
    task = await _load_staging_task_state(task_id)

    if task.status == "cancelled":
        return task
    if _is_terminal_status(task.status):
        raise ValueError(f"Cannot cancel staging task with status {task.status}")

    if task.status == "queued" and (task._task is None or task._task.done()):
        await _mark_task_cancelled(task, message)
        _staging_tasks[task.task_id] = task
        return task

    broker_cancelled = await _cancel_local_broker_transfer(task)
    if task._task is not None and not task._task.done():
        task._task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task._task), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    if task.status != "cancelled":
        await _mark_task_cancelled(task, message)

    logger.info(
        "Background staging cancellation requested",
        task_id=task.task_id,
        broker_cancelled=broker_cancelled,
    )
    return task


async def _enqueue_replacement_task(source_task: StagingTaskState) -> StagingTaskState:
    if active_task_count() >= MAX_CONCURRENT_STAGING_TASKS:
        raise RuntimeError("Too many concurrent staging tasks")

    replacement = StagingTaskState(task_id=new_task_id(), params=dict(source_task.params))
    await persist_staging_task(replacement, force=True, raise_on_error=True)
    _staging_tasks[replacement.task_id] = replacement
    start_staging_task(replacement)
    return replacement


async def requeue_staging_task(
    task_id: str,
    *,
    allowed_statuses: set[str],
    action_name: str,
    cancel_active: bool = False,
    cancel_message: str = "Remote staging cancelled so it can resume.",
) -> StagingTaskState:
    task = await _load_staging_task_state(task_id)

    if task.status in {"queued", "running"} and cancel_active:
        task = await cancel_staging_task(task_id, message=cancel_message)

    if task.status not in allowed_statuses:
        allowed = ", ".join(sorted(allowed_statuses))
        raise ValueError(
            f"Only {allowed} tasks can be {action_name} (current status: {task.status})"
        )

    replacement = await _enqueue_replacement_task(task)
    logger.info(
        "Background staging task re-queued",
        action=action_name,
        old_task_id=task_id,
        new_task_id=replacement.task_id,
    )
    return replacement


async def resume_staging_task(task_id: str) -> StagingTaskState:
    return await requeue_staging_task(
        task_id,
        allowed_statuses={"failed", "cancelled"},
        action_name="resumed",
        cancel_active=True,
    )


async def _run_staging_lifecycle(task: StagingTaskState) -> None:
    try:
        await run_staging(task)
    finally:
        task._task = None
        await resume_queued_staging_tasks()


def start_staging_task(task: StagingTaskState) -> bool:
    """Start a queued staging task if it is not already scheduled."""
    if task._task is not None and not task._task.done():
        return False
    task._task = asyncio.create_task(_run_staging_lifecycle(task))
    return True


async def resume_queued_staging_tasks(*, profile_id: str | None = None) -> int:
    """Start queued tasks whose auth prerequisites are currently satisfied."""
    started = 0
    profile_filter = str(profile_id or "").strip() or None
    queued_tasks = [
        task
        for task in _staging_tasks.values()
        if task.status == "queued"
        and (task._task is None or task._task.done())
        and (profile_filter is None or str(task.params.get("ssh_profile_id") or "").strip() == profile_filter)
    ]

    for task in queued_tasks:
        if running_task_count() >= MAX_CONCURRENT_STAGING_TASKS:
            break

        wait_reason = await get_staging_task_start_block_reason(task)
        if wait_reason:
            if _set_task_wait_reason(task, wait_reason):
                await persist_staging_task(task, force=True)
            continue

        if _clear_task_wait_reason(task):
            await persist_staging_task(task, force=True)
        if start_staging_task(task):
            started += 1
    return started


async def hydrate_incomplete_staging_tasks(
    *,
    profile_id: str | None = None,
    task_id: str | None = None,
) -> list[StagingTaskState]:
    """Load incomplete staging tasks from durable storage into the in-memory registry."""
    from launchpad.db import get_incomplete_staging_tasks, get_staging_task_record

    profile_filter = str(profile_id or "").strip() or None
    task_filter = str(task_id or "").strip() or None

    if task_filter:
        record = await get_staging_task_record(task_filter)
        records = [record] if record is not None else []
    else:
        records = await get_incomplete_staging_tasks()

    hydrated: list[StagingTaskState] = []
    for record in records:
        if record is None:
            continue
        if record.task_id in _staging_tasks:
            continue

        task = staging_task_state_from_record(record)
        if task.status not in {"queued", "running"}:
            continue
        if profile_filter and str(task.params.get("ssh_profile_id") or "").strip() != profile_filter:
            continue

        if task.status == "running":
            task.status = "queued"
            task.touch()
            await persist_staging_task(task, force=True)

        _staging_tasks[task.task_id] = task
        hydrated.append(task)

    return hydrated


async def recover_staging_tasks() -> list[StagingTaskState]:
    """Reload queued/running staging tasks from durable storage on startup."""
    recovered = await hydrate_incomplete_staging_tasks()
    await resume_queued_staging_tasks()
    return recovered


# ---------------------------------------------------------------------------
# Background coroutine
# ---------------------------------------------------------------------------

async def run_staging(task: StagingTaskState) -> None:
    """Execute the full staging pipeline in the background.

    Updates *task* in-place with progress, result, or error.
    """
    from launchpad.backends.slurm_backend import SlurmBackend, SubmitParams
    from launchpad.backends.path_validator import validate_remote_paths, check_all_paths_ok

    _clear_task_wait_reason(task)
    task.status = "running"
    task.touch()
    await persist_staging_task(task, force=True)

    params = SubmitParams(**task.params)
    backend = SlurmBackend()
    profile = None
    conn = None

    def _on_progress(info: dict) -> None:
        """Callback invoked by rsync stdout parsing — updates task state."""
        task.progress.update(info)
        task.touch()
        schedule_staging_task_persist(task)

    try:
        profile = await backend._load_profile(params.ssh_profile_id, params.user_id)
        remote_roots = backend._derive_remote_roots(params, profile)

        conn = await backend._ssh_manager.connect(profile)

        results = await validate_remote_paths(
            conn,
            {
                "base": remote_roots["remote_base_path"],
                "ref": remote_roots["ref_root"],
                "data": remote_roots["data_root"],
            },
        )
        ok, path_errors = check_all_paths_ok(results)
        if not ok:
            raise ValueError(f"Remote path validation failed: {'; '.join(path_errors)}")

        if params.remote_input_path or params.staged_remote_input_path:
            stage_result = await backend._reuse_pre_staged_input(
                None, params, profile=profile, conn=conn,
            )
        else:
            stage_result = await backend._stage_sample_inputs(
                params,
                profile,
                conn,
                run_uuid=None,
                on_progress=_on_progress,
                transfer_id=task.task_id,
            )
        reference_statuses = dict(stage_result.get("reference_cache_statuses") or {})
        reference_asset_evidence, reference_statuses = await backend._ensure_reference_assets_present(
            params=params,
            profile=profile,
            conn=conn,
            remote_reference_paths=stage_result["remote_reference_paths"],
            reference_statuses=reference_statuses,
        )

        task.result = {
            "sample_name": params.sample_name,
            "ssh_profile_id": profile.id,
            "ssh_profile_nickname": profile.nickname,
            "remote_base_path": remote_roots["remote_base_path"],
            "remote_data_path": stage_result["remote_input"],
            "remote_reference_paths": stage_result["remote_reference_paths"],
            "data_cache_status": stage_result["data_cache_status"],
            "reference_cache_statuses": reference_statuses,
            "reference_asset_evidence": reference_asset_evidence,
            "detected_input_type": stage_result.get("detected_input_type") or params.input_type,
        }
        task.status = "completed"
        task.touch()
        await persist_staging_task(task, force=True)
        logger.info(
            "Background staging completed",
            task_id=task.task_id,
            sample_name=params.sample_name,
        )
    except asyncio.CancelledError:
        await _mark_task_cancelled(task, task.error or _CANCELLED_TASK_MESSAGE)
        logger.info(
            "Background staging cancelled",
            task_id=task.task_id,
            sample_name=params.sample_name,
        )
    except Exception as exc:
        wait_reason = await get_staging_task_start_block_reason(task)
        if wait_reason:
            task.status = "queued"
            task.error = None
            _set_task_wait_reason(task, wait_reason)
            await persist_staging_task(task, force=True)
            logger.warning(
                "Background staging paused awaiting auth",
                task_id=task.task_id,
                wait_reason=wait_reason,
                error=str(exc).strip() or f"{type(exc).__name__}: {exc!r}",
            )
        else:
            task.status = "failed"
            task.error = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
            task.touch()
            await persist_staging_task(task, force=True)
            logger.error(
                "Background staging failed",
                task_id=task.task_id,
                error=task.error,
                exc_info=True,
            )
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

_CLEANUP_TTL_SECONDS = float(os.getenv("STAGING_TASK_TTL_SECONDS", "86400"))  # 24 h


async def cleanup_old_tasks() -> int:
    """Remove terminated task entries older than TTL.  Returns count removed."""
    from launchpad.db import delete_staging_task

    now = time.time()
    to_remove = [
        tid
        for tid, t in _staging_tasks.items()
        if _is_terminal_status(t.status) and (now - t.updated_at) > _CLEANUP_TTL_SECONDS
    ]
    for tid in to_remove:
        _staging_tasks.pop(tid, None)
        try:
            await delete_staging_task(tid)
        except Exception:
            logger.warning("Failed to delete persisted staging task", task_id=tid, exc_info=True)
    if to_remove:
        logger.info("Cleaned up staging tasks", count=len(to_remove))
    return len(to_remove)


async def periodic_cleanup(interval: float = 3600.0) -> None:
    """Run cleanup in a loop.  Meant to be spawned once at app startup."""
    while True:
        await asyncio.sleep(interval)
        try:
            await cleanup_old_tasks()
        except Exception:
            logger.debug("Staging task cleanup error", exc_info=True)
