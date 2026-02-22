"""
Database models for Launchpad job tracking.
"""
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Text, DateTime, func, JSON
from datetime import datetime

class Base(DeclarativeBase):
    pass

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
