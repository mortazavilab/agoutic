"""
ExecutionBackend protocol and shared types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class SubmitParams:
    """Parameters for job submission, common across backends."""
    # Identity
    project_id: str = ""
    user_id: str | None = None
    username: str | None = None
    project_slug: str | None = None

    # Dogme-specific
    sample_name: str = ""
    mode: str = ""  # DNA, RNA, CDNA
    input_type: str = "pod5"
    input_directory: str = ""
    reference_genome: list[str] = field(default_factory=lambda: ["GRCh38"])
    modifications: str | None = None
    entry_point: str | None = None
    modkit_filter_threshold: float = 0.9
    min_cov: int | None = None
    per_mod: int = 5
    accuracy: str = "sup"
    max_gpu_tasks: int = 1
    resume_from_dir: str | None = None
    parent_block_id: str | None = None

    # Remote execution (SLURM backend only)
    ssh_profile_id: str | None = None
    slurm_account: str | None = None
    slurm_partition: str | None = None
    slurm_cpus: int | None = None
    slurm_memory_gb: int | None = None
    slurm_walltime: str | None = None
    slurm_gpus: int | None = None
    slurm_gpu_type: str | None = None
    remote_base_path: str | None = None
    workflow_number: int | None = None
    staged_remote_input_path: str | None = None
    result_destination: str = "local"  # "local", "remote", "both"
    cache_preflight: dict | None = None
    reference_cache_path: str | None = None
    data_cache_path: str | None = None


@dataclass
class JobStatus:
    """Unified status returned by all backends."""
    run_uuid: str = ""
    status: str = "PENDING"
    progress_percent: int = 0
    message: str = ""
    tasks: dict | None = None

    # Extended fields for remote execution
    execution_mode: str = "local"
    run_stage: str | None = None
    slurm_job_id: str | None = None
    slurm_state: str | None = None
    transfer_state: str | None = None
    result_destination: str | None = None
    ssh_profile_nickname: str | None = None
    work_directory: str | None = None


@dataclass
class LogEntry:
    """A single log entry from any backend."""
    timestamp: str = ""
    level: str = "INFO"
    message: str = ""
    source: str | None = None


@runtime_checkable
class ExecutionBackend(Protocol):
    """Protocol defining the interface for all execution backends."""

    async def submit(self, run_uuid: str, params: SubmitParams) -> str:
        """Submit a job. Returns run_uuid."""
        ...

    async def check_status(self, run_uuid: str) -> JobStatus:
        """Check job status. Returns unified JobStatus."""
        ...

    async def cancel(self, run_uuid: str) -> bool:
        """Cancel a running job. Returns True if successful."""
        ...

    async def get_logs(self, run_uuid: str, limit: int = 50) -> list[LogEntry]:
        """Get recent log entries."""
        ...

    async def cleanup(self, run_uuid: str) -> bool:
        """Clean up job artifacts. Returns True if successful."""
        ...
