"""
ExecutionBackend protocol and shared types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from launchpad.config import resolve_dogme_accuracy


@dataclass
class SubmitParams:
    """Parameters for job submission, common across backends."""
    # Identity
    project_id: str = ""
    user_id: str | None = None
    username: str | None = None
    project_slug: str | None = None
    workflow_key: str = "dogme"
    workflow_executor: Any | None = None

    # Dogme-specific
    sample_name: str = ""
    mode: str | None = None  # Dogme-only mode: DNA, RNA, CDNA
    input_type: str = "pod5"
    input_directory: str = ""
    reference_fasta: str | None = None
    vcf: str | None = None
    sample_sheet: str | None = None
    cutter: str | None = None
    workflow_repo: str | None = None
    workflow_version: str | None = None
    report_filename: str | None = None
    output_flags: dict[str, bool] = field(default_factory=dict)
    reference_genome: list[str] = field(default_factory=lambda: ["GRCh38"])
    modifications: str | None = None
    entry_point: str | None = None
    modkit_filter_threshold: float = 0.9
    min_cov: int | None = None
    per_mod: int = 5
    accuracy: str | None = None
    max_gpu_tasks: Optional[int] = None
    local_max_task_cpus: int | None = None
    local_max_task_memory_gb: int | None = None
    resume_from_dir: str | None = None
    rerun_in_place: bool = False
    parent_block_id: str | None = None
    custom_dogme_profile: str | None = None
    custom_dogme_bind_paths: list[str] = field(default_factory=list)

    # Remote execution (SLURM backend only)
    ssh_profile_id: str | None = None
    slurm_account: str | None = None
    slurm_partition: str | None = None
    slurm_gpu_account: str | None = None
    slurm_gpu_partition: str | None = None
    slurm_cpus: int | None = None
    slurm_memory_gb: int | None = None
    slurm_walltime: str | None = None
    slurm_gpus: int | None = None
    slurm_gpu_type: str | None = None
    remote_base_path: str | None = None
    workflow_number: int | None = None
    remote_input_path: str | None = None
    staged_remote_input_path: str | None = None
    result_destination: str = "local"  # "local", "remote", "both"
    cache_preflight: dict | None = None
    reference_cache_path: str | None = None
    data_cache_path: str | None = None
    remote_work_dir: str | None = None
    remote_output_dir: str | None = None
    remote_nextflow_work_dir: str | None = None

    # Standalone script execution (local only)
    run_type: str = "dogme"  # "dogme" or "script"
    script_id: str | None = None
    script_path: str | None = None
    script_args: list[str] = field(default_factory=list)
    script_working_directory: str | None = None

    def __post_init__(self) -> None:
        if str(self.workflow_key or "dogme").strip().lower() == "dogme":
            self.accuracy = resolve_dogme_accuracy(self.mode, self.accuracy)


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
    transfer_detail: str | None = None
    result_destination: str | None = None
    ssh_profile_nickname: str | None = None
    work_directory: str | None = None
    workflow_usage: dict[str, Any] | None = None
    workflow_usage_synced_at: str | None = None


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

    def get_transfer_detail(self, run_uuid: str) -> str | None:
        """Return in-flight transfer progress details when supported."""
        ...
