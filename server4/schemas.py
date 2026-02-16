"""Request and response schemas for Server4 API."""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime


# File listing schemas
class FileInfo(BaseModel):
    """Information about a single file."""
    path: str = Field(description="Relative path from work directory")
    name: str = Field(description="Filename")
    size: int = Field(description="File size in bytes")
    extension: str = Field(description="File extension")
    modified_time: Optional[datetime] = Field(None, description="Last modified timestamp")


class FileListing(BaseModel):
    """Response for file listing."""
    run_uuid: str
    work_dir: str
    files: List[FileInfo]
    file_count: int
    total_size: int


# File content schemas
class FileContentRequest(BaseModel):
    """Request to read file content."""
    run_uuid: str
    file_path: str
    preview_lines: Optional[int] = Field(None, description="Number of lines to preview")


class FileContentResponse(BaseModel):
    """Response with file content."""
    run_uuid: str
    file_path: str
    content: str
    line_count: Optional[int] = None
    is_truncated: bool = False
    file_size: int


# CSV/TSV parsing schemas
class ParsedTableData(BaseModel):
    """Parsed tabular data."""
    run_uuid: str
    file_path: str
    columns: List[str]
    row_count: int
    data: List[Dict[str, Any]]  # List of row dicts
    preview_rows: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


# BED file parsing schemas
class BedRecord(BaseModel):
    """Single BED format record."""
    chrom: str
    chromStart: int
    chromEnd: int
    name: Optional[str] = None
    score: Optional[float] = None
    strand: Optional[str] = None
    # Additional optional fields
    extra_fields: Dict[str, Any] = Field(default_factory=dict)


class ParsedBedData(BaseModel):
    """Parsed BED file data."""
    run_uuid: str
    file_path: str
    record_count: int
    records: List[BedRecord]
    preview_records: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


# Analysis summary schemas
class JobFileSummary(BaseModel):
    """Summary of files for a job."""
    txt_files: List[FileInfo]
    csv_files: List[FileInfo]
    bed_files: List[FileInfo]
    other_files: List[FileInfo]


class AnalysisSummary(BaseModel):
    """Complete analysis summary for a job."""
    run_uuid: str
    sample_name: str
    mode: str  # DNA/RNA/CDNA
    status: str
    work_dir: str
    file_summary: JobFileSummary  # Filtered key files
    all_file_counts: Dict[str, int] = Field(default_factory=dict)  # Counts for all files
    key_results: Dict[str, Any] = Field(default_factory=dict)
    parsed_reports: Dict[str, Any] = Field(default_factory=dict)


# Error schemas
class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    run_uuid: Optional[str] = None
