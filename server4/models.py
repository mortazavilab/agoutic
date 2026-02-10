"""Database models for Server4.

Server4 primarily reads from server3's DogmeJob table.
This file imports the necessary models for analysis operations.
"""

from sqlalchemy import String, Integer, DateTime, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime
from typing import Optional


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class DogmeJob(Base):
    """
    DogmeJob model (read-only for server4).
    Primary table is in server3, server4 reads it for analysis.
    Must match server3/models.py schema exactly.
    """
    __tablename__ = "dogme_jobs"
    
    # Primary identifier
    run_uuid: Mapped[str] = mapped_column(String, primary_key=True)
    
    # Job metadata
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    sample_name: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # DNA, RNA, CDNA
    
    # Input data
    input_directory: Mapped[str] = mapped_column(String, nullable=False)
    reference_genome: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    modifications: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Nextflow execution
    nextflow_work_dir: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    nextflow_config_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    nextflow_process_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Status tracking
    status: Mapped[str] = mapped_column(String, default="PENDING")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    
    # Execution timeline
    submitted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Outputs and reporting
    output_directory: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    report_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Logging
    log_file: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stderr_log: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AnalysisCache(Base):
    """
    Cache for parsed analysis results to avoid re-parsing large files.
    Optional optimization for future use.
    """
    __tablename__ = "analysis_cache"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_uuid: Mapped[str] = mapped_column(String, nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=False)  # MD5 or SHA256
    parsed_data: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
