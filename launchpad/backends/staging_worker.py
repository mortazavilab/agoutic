"""Background staging worker for durable HPC file transfers.

Remote staging is a long-running rsync upload that should survive HTTP
timeouts and client disconnects.  This module provides:

* ``StagingTaskState`` — in-memory state for a single background staging task.
* ``run_staging()``     — async coroutine that drives the staging pipeline
                          and can be spawned with ``asyncio.create_task()``.
* ``cleanup_old_tasks()`` — periodic purge of terminated task records.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from common.logging_config import get_logger

logger = get_logger(__name__)

MAX_CONCURRENT_STAGING_TASKS = int(os.getenv("MAX_CONCURRENT_STAGING_TASKS", "3"))

# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------

@dataclass
class StagingTaskState:
    """Mutable in-memory state for a single background staging task."""

    task_id: str
    status: str = "queued"  # queued | running | completed | failed
    progress: dict = field(default_factory=dict)
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    params: dict = field(default_factory=dict)

    # asyncio.Task handle — set by the caller after create_task()
    _task: asyncio.Task | None = field(default=None, repr=False)

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


def active_task_count() -> int:
    return sum(1 for t in _staging_tasks.values() if t.status in ("queued", "running"))


# ---------------------------------------------------------------------------
# Background coroutine
# ---------------------------------------------------------------------------

async def run_staging(task: StagingTaskState) -> None:
    """Execute the full staging pipeline in the background.

    Updates *task* in-place with progress, result, or error.
    """
    from launchpad.backends.slurm_backend import SlurmBackend, SubmitParams
    from launchpad.backends.ssh_manager import SSHManager
    from launchpad.backends.remote_path_validation import validate_remote_paths, check_all_paths_ok

    task.status = "running"
    task.touch()

    params = SubmitParams(**task.params)
    backend = SlurmBackend(SSHManager())
    profile = None
    conn = None

    def _on_progress(info: dict) -> None:
        """Callback invoked by rsync stdout parsing — updates task state."""
        task.progress.update(info)
        task.touch()

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

        stage_result = await backend._stage_sample_inputs(
            params, profile, conn, run_uuid=None, on_progress=_on_progress,
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
        }
        task.status = "completed"
        task.touch()
        logger.info(
            "Background staging completed",
            task_id=task.task_id,
            sample_name=params.sample_name,
        )
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
        task.touch()
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
    now = time.time()
    to_remove = [
        tid
        for tid, t in _staging_tasks.items()
        if t.status in ("completed", "failed") and (now - t.updated_at) > _CLEANUP_TTL_SECONDS
    ]
    for tid in to_remove:
        _staging_tasks.pop(tid, None)
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
