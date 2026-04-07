"""
Local execution backend — wraps existing NextflowExecutor.

This is a thin adapter that maintains 100% backward compatibility
with the existing local execution path.
"""
from __future__ import annotations

from common.logging_config import get_logger
from launchpad.backends.base import ExecutionBackend, SubmitParams, JobStatus, LogEntry
from launchpad.backends.stage_machine import RunStage

logger = get_logger(__name__)


class LocalBackend:
    """Local execution via NextflowExecutor (existing behavior)."""

    def __init__(self):
        from launchpad.nextflow_executor import NextflowExecutor
        self._executor = NextflowExecutor()

    async def submit(self, run_uuid: str, params: SubmitParams) -> str:
        """Submit a local Nextflow job."""
        _run_uuid, work_dir = await self._executor.submit_job(
            run_uuid=run_uuid,
            sample_name=params.sample_name,
            mode=params.mode,
            input_type=params.input_type,
            input_dir=params.input_directory,
            reference_genome=params.reference_genome,
            modifications=params.modifications,
            entry_point=params.entry_point,
            modkit_filter_threshold=params.modkit_filter_threshold,
            min_cov=params.min_cov,
            per_mod=params.per_mod,
            accuracy=params.accuracy,
            max_gpu_tasks=params.max_gpu_tasks,
            rerun_in_place=params.rerun_in_place,
            user_id=params.user_id,
            project_id=params.project_id,
            username=params.username,
            project_slug=params.project_slug,
            resume_from_dir=params.resume_from_dir,
        )
        return _run_uuid

    async def check_status(self, run_uuid: str) -> JobStatus:
        """Check local job status via existing executor."""
        from launchpad.db import get_job_by_uuid as get_job
        job = await get_job(run_uuid)
        if not job:
            return JobStatus(run_uuid=run_uuid, status="UNKNOWN", message="Job not found")

        status = JobStatus(
            run_uuid=run_uuid,
            status=job.status,
            progress_percent=job.progress_percent,
            message=job.error_message or "",
            execution_mode="local",
            run_stage=job.run_stage or (
                RunStage.RUNNING.value if job.status == "RUNNING"
                else RunStage.COMPLETED.value if job.status == "COMPLETED"
                else RunStage.FAILED.value if job.status == "FAILED"
                else RunStage.CANCELLED.value if job.status == "CANCELLED"
                else RunStage.SUBMITTING_JOB.value
            ),
        )
        return status

    async def cancel(self, run_uuid: str) -> bool:
        """Cancel a local job."""
        from launchpad.db import get_job_by_uuid as get_job
        job = await get_job(run_uuid)
        if not job or not job.nextflow_process_id:
            return False

        import os
        import signal
        try:
            os.kill(job.nextflow_process_id, signal.SIGTERM)
            return True
        except ProcessLookupError:
            return False

    async def get_logs(self, run_uuid: str, limit: int = 50) -> list[LogEntry]:
        """Get log entries for a local job."""
        from launchpad.db import get_job_logs
        logs = await get_job_logs(run_uuid, limit=limit)
        return [
            LogEntry(
                timestamp=str(log.timestamp),
                level=log.level,
                message=log.message,
                source=log.source,
            )
            for log in logs
        ]

    async def cleanup(self, run_uuid: str) -> bool:
        """Clean up local job artifacts."""
        from launchpad.db import get_job_by_uuid as get_job
        import shutil
        job = await get_job(run_uuid)
        if not job or not job.nextflow_work_dir:
            return False

        try:
            shutil.rmtree(job.nextflow_work_dir, ignore_errors=True)
            return True
        except Exception:
            return False

    def get_transfer_detail(self, run_uuid: str) -> str | None:
        """Local jobs do not have remote result-transfer progress."""
        return None
