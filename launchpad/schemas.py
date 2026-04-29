"""
Pydantic schemas for Launchpad API.
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Any, List, Literal, Union
from datetime import datetime

from launchpad.config import MAX_GPU_TASKS_LIMIT

class SubmitJobRequest(BaseModel):
    """Request to submit a Dogme job."""
    run_type: Literal["dogme", "script"] = "dogme"
    project_id: str = Field(..., min_length=1)
    user_id: Optional[str] = None  # Owner user ID (passed by Cortex)
    username: Optional[str] = None  # Human-readable username (for directory naming)
    project_slug: Optional[str] = None  # Human-readable project slug (for directory naming)
    sample_name: str = Field(..., min_length=1)
    mode: str = Field(..., min_length=1)  # DNA, RNA, CDNA
    input_type: Literal["pod5", "bam", "fastq"] = "pod5"  # Type of input files
    input_directory: str = Field(default="", min_length=0)
    reference_genome: Union[str, List[str]] = "mm39"  # Single or multiple genomes
    modifications: Optional[str] = None
    entry_point: Optional[str] = None  # Dogme entry point (e.g., "remap", "basecall")
    parent_block_id: Optional[str] = None
    
    # Resume support
    resume_from_dir: Optional[str] = None  # Path to previous workflow dir to resume with -resume

    # Remote execution (SLURM backend)
    execution_mode: Literal["local", "slurm"] = "local"
    local_max_task_cpus: Optional[int] = None
    local_max_task_memory_gb: Optional[int] = None
    ssh_profile_id: Optional[str] = None
    slurm_account: Optional[str] = None
    slurm_partition: Optional[str] = None
    slurm_gpu_account: Optional[str] = None
    slurm_gpu_partition: Optional[str] = None
    slurm_cpus: Optional[int] = None
    slurm_memory_gb: Optional[int] = None
    slurm_walltime: Optional[str] = None
    slurm_gpus: Optional[int] = None
    slurm_gpu_type: Optional[str] = None
    remote_base_path: Optional[str] = None
    remote_input_path: Optional[str] = None
    staged_remote_input_path: Optional[str] = None
    cache_preflight: Optional[dict] = None
    result_destination: Optional[Literal["local", "remote", "both"]] = None

    # Advanced parameters (optional)
    modkit_filter_threshold: Optional[float] = 0.9  # Modification calling threshold
    min_cov: Optional[int] = None  # Minimum coverage (defaults: 3 reads unless explicitly overridden)
    per_mod: Optional[int] = 5  # Percentage threshold for modifications
    accuracy: Optional[str] = "sup"  # Basecalling accuracy (sup/hac/fast)
    max_gpu_tasks: Optional[int] = None  # None lets Nextflow manage concurrency without maxForks
    custom_dogme_profile: Optional[str] = None
    custom_dogme_bind_paths: list[str] = Field(default_factory=list)

    # Standalone script execution (local only)
    script_id: Optional[str] = None
    script_path: Optional[str] = None
    script_args: list[str] = Field(default_factory=list)
    script_working_directory: Optional[str] = None
    
    @field_validator("reference_genome")
    @classmethod
    def normalize_genome_to_list(cls, v):
        """Normalize reference_genome to list for consistent handling."""
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("max_gpu_tasks")
    @classmethod
    def validate_max_gpu_tasks(cls, value):
        if value is None:
            return value
        if value < 1 or value > MAX_GPU_TASKS_LIMIT:
            raise ValueError(f"max_gpu_tasks must be between 1 and {MAX_GPU_TASKS_LIMIT}")
        return value

    @field_validator("local_max_task_cpus")
    @classmethod
    def validate_local_max_task_cpus(cls, value):
        if value is None:
            return value
        if value < 1 or value > 256:
            raise ValueError("local_max_task_cpus must be between 1 and 256")
        return value

    @field_validator("local_max_task_memory_gb")
    @classmethod
    def validate_local_max_task_memory_gb(cls, value):
        if value is None:
            return value
        if value < 1 or value > 2048:
            raise ValueError("local_max_task_memory_gb must be between 1 and 2048")
        return value

    @field_validator("custom_dogme_bind_paths", mode="before")
    @classmethod
    def normalize_custom_dogme_bind_paths(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]

        normalized: list[str] = []
        for item in value:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @model_validator(mode="after")
    def validate_remote_execution(self):
        if self.run_type == "script":
            if self.execution_mode != "local":
                raise ValueError("run_type 'script' currently supports execution_mode 'local' only")
            if not (self.script_id or self.script_path):
                raise ValueError("run_type 'script' requires explicit script_id or script_path")
            if not self.input_directory:
                raise ValueError("input_directory is required when run_type is 'script'")
            return self

        if self.execution_mode == "slurm":
            if not self.user_id:
                raise ValueError("user_id is required when execution_mode is 'slurm'")
            if not self.ssh_profile_id:
                raise ValueError("ssh_profile_id is required when execution_mode is 'slurm'")
            if not self.input_directory and not (self.staged_remote_input_path or self.remote_input_path):
                raise ValueError(
                    "input_directory is required unless staged_remote_input_path or remote_input_path is provided when execution_mode is 'slurm'"
                )
            return self

        if not self.input_directory:
            raise ValueError("input_directory is required")
        return self

class JobStatusResponse(BaseModel):
    """Response with job status."""
    run_uuid: str
    status: str
    progress_percent: int
    message: str
    tasks: Optional[dict] = None

class JobDetailsResponse(BaseModel):
    """Full job details."""
    run_uuid: str
    project_id: str
    workflow_index: Optional[int] = None
    workflow_alias: Optional[str] = None
    workflow_folder_name: Optional[str] = None
    workflow_display_name: Optional[str] = None
    sample_name: str
    mode: str
    status: str
    progress_percent: int
    submitted_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    output_directory: Optional[str]
    error_message: Optional[str]
    report: Optional[Any]

class JobSubmitResponse(BaseModel):
    """Response from job submission."""
    run_uuid: str
    sample_name: str
    status: str
    work_directory: str
    cache_actions: Optional[dict] = None


class WorkflowRenameRequest(BaseModel):
    """Request to rename a workflow's current folder/display name."""
    new_name: str = Field(..., min_length=1)


class StageRemoteSampleRequest(BaseModel):
    """Request to stage a sample and references on a remote system without submitting a job."""
    project_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    username: Optional[str] = None
    project_slug: Optional[str] = None
    sample_name: str = Field(..., min_length=1)
    mode: str = Field(..., min_length=1)
    input_directory: str = Field(default="", min_length=0)
    reference_genome: Union[str, List[str]] = "mm39"
    ssh_profile_id: str = Field(..., min_length=1)
    remote_base_path: Optional[str] = None
    remote_input_path: Optional[str] = None

    @field_validator("reference_genome")
    @classmethod
    def normalize_stage_genomes_to_list(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    @model_validator(mode="after")
    def validate_stage_input_source(self):
        if not self.input_directory and not self.remote_input_path:
            raise ValueError("input_directory or remote_input_path is required for stage_remote_sample")
        return self


class StageRemoteSampleResponse(BaseModel):
    """Response from stage-only remote staging."""
    sample_name: str
    ssh_profile_id: str
    ssh_profile_nickname: Optional[str] = None
    remote_base_path: str
    remote_data_path: str
    remote_reference_paths: dict[str, str]
    data_cache_status: str
    reference_cache_statuses: dict[str, str]
    reference_asset_evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StageTaskAcceptedResponse(BaseModel):
    """Returned immediately when staging is started asynchronously."""
    task_id: str
    status: str = "queued"


class StageTaskCancelResponse(BaseModel):
    """Returned when a staging task cancellation is accepted."""
    task_id: str
    status: str = "cancelled"
    message: str


class StageTaskResumeResponse(BaseModel):
    """Returned when a staging task resume is accepted and queued."""
    task_id: str
    status: str = "queued"
    resumed_from_task_id: str


class StagingTaskStatusResponse(BaseModel):
    """Current state of a background staging task."""
    task_id: str
    status: str  # queued | running | completed | failed | cancelled
    progress: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float
    updated_at: float


class JobListResponse(BaseModel):
    """Response with list of jobs."""
    jobs: list[JobDetailsResponse]
    total_count: int
    running_count: int

class LogEntry(BaseModel):
    """A single log entry."""
    timestamp: str
    level: str
    message: str
    source: Optional[str]

class JobLogsResponse(BaseModel):
    """Response with job logs."""
    run_uuid: str
    logs: list[LogEntry]


class JobResultSyncResponse(BaseModel):
    """Response for manual remote->local result synchronization."""
    success: bool
    status: str
    message: str
    run_uuid: str
    remote_work_dir: Optional[str] = None
    local_work_dir: Optional[str] = None
    transfer_state: Optional[str] = None

class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    running_jobs: int
    database_ok: bool


# ==================== SSH Profile Schemas ====================

class SSHProfileCreate(BaseModel):
    """Request to create an SSH profile."""
    user_id: str = Field(..., min_length=1)
    nickname: Optional[str] = None
    ssh_host: str = Field(..., min_length=1)
    ssh_port: int = 22
    ssh_username: str = Field(..., min_length=1)
    auth_method: Literal["key_file", "ssh_agent"] = "key_file"
    key_file_path: Optional[str] = None
    local_username: Optional[str] = None  # Local Unix account unlocked for per-session SSH access
    default_slurm_account: Optional[str] = None
    default_slurm_partition: Optional[str] = None
    default_slurm_gpu_account: Optional[str] = None
    default_slurm_gpu_partition: Optional[str] = None
    remote_base_path: Optional[str] = None

    @model_validator(mode="after")
    def validate_auth_method(self):
        if self.auth_method == "key_file" and not self.key_file_path:
            raise ValueError("key_file_path is required when auth_method is 'key_file'")
        return self


class SSHProfileUpdate(BaseModel):
    """Request to update an SSH profile."""
    nickname: Optional[str] = None
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_username: Optional[str] = None
    auth_method: Optional[Literal["key_file", "ssh_agent"]] = None
    key_file_path: Optional[str] = None
    local_username: Optional[str] = None
    default_slurm_account: Optional[str] = None
    default_slurm_partition: Optional[str] = None
    default_slurm_gpu_account: Optional[str] = None
    default_slurm_gpu_partition: Optional[str] = None
    remote_base_path: Optional[str] = None
    is_enabled: Optional[bool] = None


class SSHProfileOut(BaseModel):
    """Response model for an SSH profile (no secrets)."""
    id: str
    user_id: str
    nickname: Optional[str] = None
    ssh_host: str
    ssh_port: int
    ssh_username: str
    auth_method: str
    has_key_file: bool = False
    key_file_path: Optional[str] = None
    local_username: Optional[str] = None
    default_slurm_account: Optional[str] = None
    default_slurm_partition: Optional[str] = None
    default_slurm_gpu_account: Optional[str] = None
    default_slurm_gpu_partition: Optional[str] = None
    remote_base_path: Optional[str] = None
    is_enabled: bool = True
    created_at: str
    updated_at: str


class SSHProfileTestResult(BaseModel):
    """Result of testing an SSH connection."""
    ok: bool
    message: str
    hostname: Optional[str] = None
    remote_user: Optional[str] = None
    session_started: bool = False
    session_expires_at: Optional[str] = None


class SSHProfileAuthSessionResult(BaseModel):
    """Result of creating/querying/deleting a local auth session."""
    active: bool
    message: str
    session_id: Optional[str] = None
    local_username: Optional[str] = None
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    expires_at: Optional[str] = None


# ==================== Extended Job Schemas ====================

class JobStatusExtendedResponse(JobStatusResponse):
    """Extended job status with remote execution fields."""
    execution_mode: str = "local"
    run_stage: Optional[str] = None
    current_step: Optional[str] = None
    current_step_detail: Optional[str] = None
    slurm_job_id: Optional[str] = None
    slurm_state: Optional[str] = None
    transfer_state: Optional[str] = None
    transfer_detail: Optional[str] = None
    result_destination: Optional[str] = None
    ssh_profile_nickname: Optional[str] = None
    work_directory: Optional[str] = None
    submitted_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[int] = None
