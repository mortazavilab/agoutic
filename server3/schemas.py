"""
Pydantic schemas for Server 3 API.
"""
from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime

class SubmitJobRequest(BaseModel):
    """Request to submit a Dogme job."""
    project_id: str = Field(..., min_length=1)
    sample_name: str = Field(..., min_length=1)
    mode: str = Field(..., min_length=1)  # DNA, RNA, CDNA
    input_directory: str = Field(..., min_length=1)
    reference_genome: str = "GRCh38"
    modifications: Optional[str] = None
    parent_block_id: Optional[str] = None

class JobStatusResponse(BaseModel):
    """Response with job status."""
    run_uuid: str
    status: str
    progress_percent: int
    message: str

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
