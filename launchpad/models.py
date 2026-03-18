"""
Database models for Launchpad job tracking.
"""
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Text, DateTime, Boolean, UniqueConstraint, func, JSON
from datetime import datetime
from common.database import Base

class DogmeJob(Base):
    """Tracks a Dogme/Nextflow job submission."""
    __tablename__ = "dogme_jobs"
    
    # Primary identifier
    run_uuid: Mapped[str] = mapped_column(String, primary_key=True)
    
    # Job metadata
    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)  # Owner; nullable for legacy jobs
    sample_name: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # DNA, RNA, CDNA
    
    # Input data
    input_directory: Mapped[str] = mapped_column(String, nullable=False)
    reference_genome: Mapped[str] = mapped_column(String, nullable=True)
    modifications: Mapped[str] = mapped_column(String, nullable=True)
    
    # Nextflow execution
    nextflow_work_dir: Mapped[str] = mapped_column(String, nullable=True)
    nextflow_config_path: Mapped[str] = mapped_column(String, nullable=True)
    nextflow_process_id: Mapped[int] = mapped_column(Integer, nullable=True)
    
    # Status tracking
    status: Mapped[str] = mapped_column(String, default="PENDING", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    
    # Execution timeline
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    # Outputs and reporting
    output_directory: Mapped[str | None] = mapped_column(String, nullable=True)
    report_json: Mapped[str] = mapped_column(Text, nullable=True)  # JSON string
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Logging
    log_file: Mapped[str] = mapped_column(String, nullable=True)
    stderr_log: Mapped[str] = mapped_column(String, nullable=True)
    
    # Linked to parent request (if applicable)
    parent_block_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- Remote execution fields (Phase 1) ---
    execution_mode: Mapped[str] = mapped_column(String, default="local", server_default="local", nullable=False)  # "local" or "slurm"
    ssh_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    slurm_state: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_account: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_partition: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_cpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slurm_memory_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slurm_walltime: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_gpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slurm_gpu_type: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_work_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_output_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    result_destination: Mapped[str | None] = mapped_column(String, nullable=True)  # "remote", "local", "both"
    transfer_state: Mapped[str | None] = mapped_column(String, nullable=True)  # "none","uploading_inputs","inputs_uploaded","downloading_outputs","outputs_downloaded","transfer_failed"
    run_stage: Mapped[str | None] = mapped_column(String, nullable=True)  # Detailed stage label
    cache_preflight_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reference_cache_status: Mapped[str | None] = mapped_column(String, nullable=True)  # "reused","staged","refreshed","skipped"
    data_cache_status: Mapped[str | None] = mapped_column(String, nullable=True)  # "reused","staged","skipped"
    reference_cache_path: Mapped[str | None] = mapped_column(String, nullable=True)
    data_cache_path: Mapped[str | None] = mapped_column(String, nullable=True)

class JobLog(Base):
    """Stores streaming logs from Nextflow execution."""
    __tablename__ = "job_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_uuid: Mapped[str] = mapped_column(String, index=True, nullable=False)
    
    # Log entry
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String, nullable=False)  # INFO, WARN, ERROR
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=True)  # Process name, etc.


# ==================== SSH Profile ====================

class SSHProfile(Base):
    """Saved SSH connection profile for remote execution. Per-user isolated."""
    __tablename__ = "ssh_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "ssh_host", "ssh_username", name="uq_ssh_profile_user_host"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    nickname: Mapped[str | None] = mapped_column(String, nullable=True)
    ssh_host: Mapped[str] = mapped_column(String, nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    ssh_username: Mapped[str] = mapped_column(String, nullable=False)
    auth_method: Mapped[str] = mapped_column(String, nullable=False, default="key_file")  # "key_file", "ssh_agent"
    key_file_path: Mapped[str | None] = mapped_column(String, nullable=True)  # Path on server filesystem
    local_username: Mapped[str | None] = mapped_column(String, nullable=True)  # OS user who owns the key file
    default_slurm_account: Mapped[str | None] = mapped_column(String, nullable=True)
    default_slurm_partition: Mapped[str | None] = mapped_column(String, nullable=True)
    default_slurm_gpu_account: Mapped[str | None] = mapped_column(String, nullable=True)
    default_slurm_gpu_partition: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_base_path: Mapped[str | None] = mapped_column(String, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ==================== SLURM Defaults ====================

class SlurmDefaults(Base):
    """Saved SLURM resource defaults. Per-user, optionally per-project."""
    __tablename__ = "slurm_defaults"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_slurm_defaults_user_project"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    ssh_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    account: Mapped[str] = mapped_column(String, nullable=False)
    partition: Mapped[str] = mapped_column(String, nullable=False)
    cpus: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    memory_gb: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    walltime: Mapped[str] = mapped_column(String, default="04:00:00", nullable=False)
    gpus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gpu_type: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ==================== Remote Path Config ====================

class RemotePathConfig(Base):
    """Saved remote path defaults for a user/project/profile combination."""
    __tablename__ = "remote_path_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", "ssh_profile_id", name="uq_remote_paths_user_project_profile"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    ssh_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_input_path: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_work_path: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_output_path: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_log_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ==================== Run Audit Log ====================

class RunAuditLog(Base):
    """Audit trail entry for every significant run event."""
    __tablename__ = "run_audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_uuid: Mapped[str] = mapped_column(String, index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    ssh_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_account: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_destination: Mapped[str | None] = mapped_column(String, nullable=True)
    event: Mapped[str] = mapped_column(String, nullable=False)  # "submitted","approved","cancelled","completed","failed",...
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ==================== Remote Cache Metadata ====================

class RemoteReferenceCache(Base):
    """Tracks staged references on remote systems for reuse across projects."""
    __tablename__ = "remote_reference_cache"
    __table_args__ = (
        UniqueConstraint("user_id", "ssh_profile_id", "reference_id", name="uq_remote_ref_cache_user_profile_ref"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    ssh_profile_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    reference_id: Mapped[str] = mapped_column(String, nullable=False)
    source_signature: Mapped[str | None] = mapped_column(String, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="READY", nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class RemoteInputCache(Base):
    """Tracks staged input datasets on remote systems for reuse across projects."""
    __tablename__ = "remote_input_cache"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "ssh_profile_id",
            "reference_id",
            "input_fingerprint",
            name="uq_remote_input_cache_user_profile_ref_fp",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    ssh_profile_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    reference_id: Mapped[str] = mapped_column(String, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    remote_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="READY", nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class RemoteStagedSample(Base):
    """User-visible remote staged sample metadata for reuse by sample name."""
    __tablename__ = "remote_staged_samples"
    __table_args__ = (
        UniqueConstraint("user_id", "ssh_profile_id", "sample_slug", name="uq_remote_staged_sample_user_profile_slug"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    ssh_profile_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    ssh_profile_nickname: Mapped[str | None] = mapped_column(String, nullable=True)
    sample_name: Mapped[str] = mapped_column(String, nullable=False)
    sample_slug: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    reference_genome_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_path: Mapped[str] = mapped_column(String, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    remote_base_path: Mapped[str] = mapped_column(String, nullable=False)
    remote_data_path: Mapped[str] = mapped_column(String, nullable=False)
    remote_reference_paths_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, default="READY", nullable=False)
    last_staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
