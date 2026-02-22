"""
Pydantic schemas for Launchpad API.
"""
from pydantic import BaseModel, Field, field_validator
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
    
    # Advanced parameters (optional)
    modkit_filter_threshold: Optional[float] = 0.9  # Modification calling threshold
    min_cov: Optional[int] = None  # Minimum coverage (defaults: 1 for DNA, 3 for RNA/CDNA)
    per_mod: Optional[int] = 5  # Percentage threshold for modifications
    accuracy: Optional[str] = "sup"  # Basecalling accuracy (sup/hac/fast)
    
    @field_validator("reference_genome")
    @classmethod
    def normalize_genome_to_list(cls, v):
        """Normalize reference_genome to list for consistent handling."""
        if isinstance(v, str):
            return [v]
        return v

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
