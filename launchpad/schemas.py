"""
Pydantic schemas for Launchpad API.
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Any, List, Literal, Union
from datetime import datetime

class SubmitJobRequest(BaseModel):
    """Request to submit a Dogme job."""
    project_id: str = Field(..., min_length=1)
    user_id: Optional[str] = None  # Owner user ID (passed by Cortex)
    username: Optional[str] = None  # Human-readable username (for directory naming)
    project_slug: Optional[str] = None  # Human-readable project slug (for directory naming)
    sample_name: str = Field(..., min_length=1)
    mode: str = Field(..., min_length=1)  # DNA, RNA, CDNA
    input_type: Literal["pod5", "bam", "fastq"] = "pod5"  # Type of input files
    input_directory: str = Field(..., min_length=1)
    reference_genome: Union[str, List[str]] = "mm39"  # Single or multiple genomes
    modifications: Optional[str] = None
    entry_point: Optional[str] = None  # Dogme entry point (e.g., "remap", "basecall")
    parent_block_id: Optional[str] = None
    
    # Resume support
    resume_from_dir: Optional[str] = None  # Path to previous workflow dir to resume with -resume

    # Remote execution (SLURM backend)
    execution_mode: Literal["local", "slurm"] = "local"
    ssh_profile_id: Optional[str] = None
    slurm_account: Optional[str] = None
    slurm_partition: Optional[str] = None
    slurm_cpus: Optional[int] = None
    slurm_memory_gb: Optional[int] = None
    slurm_walltime: Optional[str] = None
    slurm_gpus: Optional[int] = None
    slurm_gpu_type: Optional[str] = None
    remote_input_path: Optional[str] = None
    remote_work_path: Optional[str] = None
    remote_output_path: Optional[str] = None
    result_destination: Optional[Literal["local", "remote", "both"]] = None

    # Advanced parameters (optional)
    modkit_filter_threshold: Optional[float] = 0.9  # Modification calling threshold
    min_cov: Optional[int] = None  # Minimum coverage (defaults: 1 for DNA, 3 for RNA/CDNA)
    per_mod: Optional[int] = 5  # Percentage threshold for modifications
    accuracy: Optional[str] = "sup"  # Basecalling accuracy (sup/hac/fast)
    max_gpu_tasks: Optional[int] = 1  # Max concurrent GPU tasks (dorado/openChromatin) per pipeline run
    
    @field_validator("reference_genome")
    @classmethod
    def normalize_genome_to_list(cls, v):
        """Normalize reference_genome to list for consistent handling."""
        if isinstance(v, str):
            return [v]
        return v

    @model_validator(mode="after")
    def validate_remote_execution(self):
        if self.execution_mode == "slurm":
            if not self.user_id:
                raise ValueError("user_id is required when execution_mode is 'slurm'")
            if not self.ssh_profile_id:
                raise ValueError("ssh_profile_id is required when execution_mode is 'slurm'")
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
    default_remote_input_path: Optional[str] = None
    default_remote_work_path: Optional[str] = None
    default_remote_output_path: Optional[str] = None

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
    default_remote_input_path: Optional[str] = None
    default_remote_work_path: Optional[str] = None
    default_remote_output_path: Optional[str] = None
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
    local_username: Optional[str] = None
    default_slurm_account: Optional[str] = None
    default_slurm_partition: Optional[str] = None
    default_slurm_gpu_account: Optional[str] = None
    default_slurm_gpu_partition: Optional[str] = None
    default_remote_input_path: Optional[str] = None
    default_remote_work_path: Optional[str] = None
    default_remote_output_path: Optional[str] = None
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
    slurm_job_id: Optional[str] = None
    slurm_state: Optional[str] = None
    transfer_state: Optional[str] = None
    result_destination: Optional[str] = None
    ssh_profile_nickname: Optional[str] = None
