"""
Analysis Engine for Server4.
Handles file discovery, parsing, and analysis of Dogme job results.
"""

import csv
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from server4.config import (
    AGOUTIC_WORK_DIR,
    MAX_PREVIEW_LINES,
    MAX_FILE_SIZE_BYTES,
    SUPPORTED_TEXT_EXTENSIONS,
    BED_COLUMNS
)
from server4.schemas import (
    FileInfo,
    FileListing,
    FileContentResponse,
    ParsedTableData,
    BedRecord,
    ParsedBedData,
    JobFileSummary,
    AnalysisSummary
)
from server4.models import DogmeJob
from server4.db import get_db


# ==================== File Discovery ====================

def get_job_work_dir(run_uuid: str) -> Optional[Path]:
    """Get work directory path for a job."""
    with get_db() as db:
        job = db.query(DogmeJob).filter(DogmeJob.run_uuid == run_uuid).first()
        if not job:
            return None
        # Use nextflow_work_dir if available, otherwise use output_directory
        work_dir = job.nextflow_work_dir or job.output_directory
        if work_dir:
            return Path(work_dir)
        # Fallback: construct from config
        return AGOUTIC_WORK_DIR / run_uuid


def discover_files(run_uuid: str, extensions: Optional[List[str]] = None) -> FileListing:
    """
    Discover all files in a job's work directory.
    
    Args:
        run_uuid: Job UUID
        extensions: Optional list of extensions to filter (e.g., ['.txt', '.csv'])
    
    Returns:
        FileListing with all discovered files
    """
    work_dir = get_job_work_dir(run_uuid)
    if not work_dir or not work_dir.exists():
        raise FileNotFoundError(f"Work directory not found for job {run_uuid}")
    
    files = []
    total_size = 0
    
    # Recursively find all files
    for file_path in work_dir.rglob("*"):
        if file_path.is_file():
            # Skip files in work/ or dor*/ subdirectories (intermediate processing files)
            relative_path = file_path.relative_to(work_dir)
            if str(relative_path).startswith("work/") or str(relative_path).startswith("dor"):
                continue
            
            # Filter by extension if specified
            if extensions and file_path.suffix.lower() not in extensions:
                continue
            
            # Get file info
            stat = file_path.stat()
            relative_path = file_path.relative_to(work_dir)
            
            files.append(FileInfo(
                path=str(relative_path),
                name=file_path.name,
                size=stat.st_size,
                extension=file_path.suffix,
                modified_time=datetime.fromtimestamp(stat.st_mtime)
            ))
            total_size += stat.st_size
    
    return FileListing(
        run_uuid=run_uuid,
        work_dir=str(work_dir),
        files=sorted(files, key=lambda f: f.path),
        file_count=len(files),
        total_size=total_size
    )


def categorize_files(run_uuid: str) -> JobFileSummary:
    """Categorize files by type."""
    all_files = discover_files(run_uuid)
    
    txt_files = []
    csv_files = []
    bed_files = []
    other_files = []
    
    for file_info in all_files.files:
        ext = file_info.extension.lower()
        if ext == ".bed":
            bed_files.append(file_info)
        elif ext in [".csv", ".tsv"]:
            csv_files.append(file_info)
        elif ext == ".txt":
            txt_files.append(file_info)
        else:
            other_files.append(file_info)
    
    return JobFileSummary(
        txt_files=txt_files,
        csv_files=csv_files,
        bed_files=bed_files,
        other_files=other_files
    )


# ==================== File Reading ====================

def read_file_content(
    run_uuid: str,
    file_path: str,
    preview_lines: Optional[int] = None
) -> FileContentResponse:
    """
    Read content from a file.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path from work directory
        preview_lines: Optional line limit for preview
    
    Returns:
        FileContentResponse with file content
    """
    work_dir = get_job_work_dir(run_uuid)
    if not work_dir:
        raise FileNotFoundError(f"Work directory not found for job {run_uuid}")
    
    # Construct and validate file path
    full_path = work_dir / file_path
    
    # Security: ensure path is within work directory
    try:
        full_path = full_path.resolve()
        work_dir = work_dir.resolve()
        if not str(full_path).startswith(str(work_dir)):
            raise ValueError(f"Invalid file path: {file_path}")
    except Exception as e:
        raise ValueError(f"Invalid file path: {file_path}") from e
    
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {file_path} (absolute path: {full_path})")
    
    # Check file size
    file_size = full_path.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"File too large: {file_size} bytes (max: {MAX_FILE_SIZE_BYTES})")
    
    # Read content
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            if preview_lines:
                lines = []
                for i, line in enumerate(f):
                    if i >= preview_lines:
                        return FileContentResponse(
                            run_uuid=run_uuid,
                            file_path=file_path,
                            content=''.join(lines),
                            line_count=preview_lines,
                            is_truncated=True,
                            file_size=file_size
                        )
                    lines.append(line)
                
                # Count remaining lines
                remaining = sum(1 for _ in f)
                total_lines = preview_lines + remaining
                
                return FileContentResponse(
                    run_uuid=run_uuid,
                    file_path=file_path,
                    content=''.join(lines),
                    line_count=total_lines,
                    is_truncated=remaining > 0,
                    file_size=file_size
                )
            else:
                content = f.read()
                line_count = content.count('\n') + 1
                return FileContentResponse(
                    run_uuid=run_uuid,
                    file_path=file_path,
                    content=content,
                    line_count=line_count,
                    is_truncated=False,
                    file_size=file_size
                )
    except UnicodeDecodeError:
        raise ValueError(f"File is not a text file: {file_path}")


# ==================== CSV/TSV Parsing ====================

def parse_csv_file(
    run_uuid: str,
    file_path: str,
    max_rows: Optional[int] = None
) -> ParsedTableData:
    """
    Parse CSV/TSV file.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path from work directory
        max_rows: Maximum rows to return (for preview)
    
    Returns:
        ParsedTableData with structured data
    """
    work_dir = get_job_work_dir(run_uuid)
    if not work_dir:
        raise FileNotFoundError(f"Work directory not found for job {run_uuid}")
    
    full_path = (work_dir / file_path).resolve()
    
    # Security check
    if not str(full_path).startswith(str(work_dir.resolve())):
        raise ValueError(f"Invalid file path: {file_path}")
    
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {file_path} (absolute path: {full_path})")
    
    # Determine delimiter
    delimiter = '\t' if file_path.endswith('.tsv') else ','
    
    # Parse CSV
    rows = []
    columns = []
    total_rows = 0
    
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            columns = reader.fieldnames or []
            
            for i, row in enumerate(reader):
                total_rows += 1
                if max_rows is None or i < max_rows:
                    rows.append(dict(row))
                elif i == max_rows:
                    # Continue counting but don't store rows
                    continue
    except Exception as e:
        raise ValueError(f"Error parsing CSV file: {str(e)}")
    
    # Generate metadata
    metadata = {
        "delimiter": delimiter,
        "column_count": len(columns),
        "total_rows": total_rows,
        "is_truncated": max_rows is not None and total_rows > max_rows
    }
    
    return ParsedTableData(
        run_uuid=run_uuid,
        file_path=file_path,
        columns=columns,
        row_count=total_rows,
        data=rows,
        preview_rows=len(rows),
        metadata=metadata
    )


# ==================== BED File Parsing ====================

def parse_bed_file(
    run_uuid: str,
    file_path: str,
    max_records: Optional[int] = None
) -> ParsedBedData:
    """
    Parse BED format file.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path from work directory
        max_records: Maximum records to return (for preview)
    
    Returns:
        ParsedBedData with structured records
    """
    work_dir = get_job_work_dir(run_uuid)
    if not work_dir:
        raise FileNotFoundError(f"Work directory not found for job {run_uuid}")
    
    full_path = (work_dir / file_path).resolve()
    
    # Security check
    if not str(full_path).startswith(str(work_dir.resolve())):
        raise ValueError(f"Invalid file path: {file_path}")
    
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {file_path} (absolute path: {full_path})")
    
    records = []
    total_records = 0
    
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            for line in f:
                # Skip comments and empty lines
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('track') or line.startswith('browser'):
                    continue
                
                total_records += 1
                
                # Only parse if within max_records
                if max_records is None or len(records) < max_records:
                    fields = line.split('\t')
                    
                    # Parse standard BED fields
                    record = BedRecord(
                        chrom=fields[0],
                        chromStart=int(fields[1]),
                        chromEnd=int(fields[2]),
                        name=fields[3] if len(fields) > 3 else None,
                        score=float(fields[4]) if len(fields) > 4 and fields[4] != '.' else None,
                        strand=fields[5] if len(fields) > 5 else None,
                        extra_fields={}
                    )
                    
                    # Parse additional fields (BED6+)
                    if len(fields) > 6:
                        for i, field in enumerate(fields[6:], start=6):
                            if i < len(BED_COLUMNS):
                                record.extra_fields[BED_COLUMNS[i]] = field
                            else:
                                record.extra_fields[f"field_{i}"] = field
                    
                    records.append(record)
    except Exception as e:
        raise ValueError(f"Error parsing BED file: {str(e)}")
    
    # Generate metadata
    metadata = {
        "total_records": total_records,
        "is_truncated": max_records is not None and total_records > max_records,
        "has_header": False  # BED files typically don't have headers
    }
    
    return ParsedBedData(
        run_uuid=run_uuid,
        file_path=file_path,
        record_count=total_records,
        records=records,
        preview_records=len(records),
        metadata=metadata
    )


# ==================== Analysis Summary ====================

def generate_analysis_summary(run_uuid: str) -> AnalysisSummary:
    """
    Generate comprehensive analysis summary for a job.
    
    Args:
        run_uuid: Job UUID
    
    Returns:
        AnalysisSummary with all available information
    """
    with get_db() as db:
        job = db.query(DogmeJob).filter(DogmeJob.run_uuid == run_uuid).first()
        if not job:
            raise ValueError(f"Job not found: {run_uuid}")
        
        # Categorize all files
        all_file_summary = categorize_files(run_uuid)
        
        # Filter to key result files only for display
        key_file_patterns = []
        if job.mode.upper() == 'CDNA':
            key_file_patterns = [
                'qc_summary', 'qc', 'stats', 'flagstat', 'gene_counts', 'transcript_counts', 
                'isoform', 'junctions', 'counts'
            ]
        elif job.mode.upper() == 'DNA':
            key_file_patterns = [
                'qc_summary', 'qc', 'stats', 'flagstat', 'modkit', 'methylation', 'mod_freq'
            ]
        elif job.mode.upper() == 'RNA':
            key_file_patterns = [
                'qc_summary', 'qc', 'stats', 'flagstat', 'gene_counts', 'transcript_counts', 'isoform'
            ]
        
        def is_key_file(file_info):
            name_lower = file_info.name.lower()
            return any(pattern in name_lower for pattern in key_file_patterns)
        
        # Filter file lists to key files only for display
        filtered_txt = [f for f in all_file_summary.txt_files if is_key_file(f)]
        filtered_csv = [f for f in all_file_summary.csv_files if is_key_file(f)]
        filtered_bed = [f for f in all_file_summary.bed_files if is_key_file(f)]
        filtered_other = [f for f in all_file_summary.other_files if is_key_file(f)]
        
        file_summary = JobFileSummary(
            txt_files=filtered_txt,
            csv_files=filtered_csv,
            bed_files=filtered_bed,
            other_files=filtered_other
        )
        
        # Parse key result files
        key_results = {}
        parsed_reports = {}
        
        # Look for common report files
        work_dir = get_job_work_dir(run_uuid)
        if work_dir:
            # Parse QC summary if exists
            qc_files = [f for f in file_summary.csv_files if 'qc_summary' in f.name.lower() or 'qc' in f.name.lower()]
            if qc_files:
                try:
                    qc_data = parse_csv_file(run_uuid, qc_files[0].path, max_rows=100)
                    parsed_reports['qc_summary'] = qc_data.dict()
                except Exception:
                    pass
            
            # Parse stats files
            stats_files = [f for f in file_summary.csv_files if 'stats' in f.name.lower() or 'flagstat' in f.name.lower()]
            if stats_files:
                try:
                    stats_data = parse_csv_file(run_uuid, stats_files[0].path, max_rows=100)
                    parsed_reports['stats'] = stats_data.dict()
                except Exception:
                    pass
            
            # Mode-specific parsing
            if job.mode.upper() == 'CDNA':
                # Parse gene counts
                gene_files = [f for f in file_summary.csv_files if 'gene_counts' in f.name.lower() or 'counts' in f.name.lower() and 'gene' in f.name.lower()]
                if gene_files:
                    try:
                        gene_data = parse_csv_file(run_uuid, gene_files[0].path, max_rows=50)
                        parsed_reports['gene_counts'] = gene_data.dict()
                    except Exception:
                        pass
                
                # Parse transcript counts
                transcript_files = [f for f in file_summary.csv_files if 'transcript_counts' in f.name.lower() or 'isoform' in f.name.lower()]
                if transcript_files:
                    try:
                        transcript_data = parse_csv_file(run_uuid, transcript_files[0].path, max_rows=50)
                        parsed_reports['transcript_counts'] = transcript_data.dict()
                    except Exception:
                        pass
            
            # Count key file types from all files
            key_results = {
                "total_files": all_file_summary.txt_files.__len__() + 
                              all_file_summary.csv_files.__len__() + 
                              all_file_summary.bed_files.__len__() + 
                              all_file_summary.other_files.__len__(),
                "txt_count": len(all_file_summary.txt_files),
                "csv_count": len(all_file_summary.csv_files),
                "bed_count": len(all_file_summary.bed_files),
                "other_count": len(all_file_summary.other_files)
            }
            
            # Add availability of parsed reports
            key_results["QC Summary"] = "Available" if 'qc_summary' in parsed_reports else "Not found"
            key_results["Stats"] = "Available" if 'stats' in parsed_reports else "Not found"
            
            # Extract key metrics from parsed reports
            if 'qc_summary' in parsed_reports and parsed_reports['qc_summary'].get('data'):
                qc_data = parsed_reports['qc_summary']['data']
                if qc_data:
                    # Extract common QC metrics
                    for row in qc_data:
                        if 'metric' in row and 'value' in row:
                            key_results[row['metric']] = row['value']
            
            if 'stats' in parsed_reports and parsed_reports['stats'].get('data'):
                stats_data = parsed_reports['stats']['data']
                if stats_data:
                    for row in stats_data:
                        for key, value in row.items():
                            if key.lower() in ['mapped_reads', 'total_reads', 'mapping_rate', 'duplicates']:
                                key_results[key] = value
            
            # Mode-specific key results
            if job.mode.upper() == 'CDNA':
                key_results["Gene Counts"] = "Available" if 'gene_counts' in parsed_reports else "Not found"
                key_results["Transcript Counts"] = "Available" if 'transcript_counts' in parsed_reports else "Not found"
                
                if 'gene_counts' in parsed_reports and parsed_reports['gene_counts'].get('data'):
                    gene_data = parsed_reports['gene_counts']['data']
                    key_results['genes_detected'] = len(gene_data)
                
                if 'transcript_counts' in parsed_reports and parsed_reports['transcript_counts'].get('data'):
                    transcript_data = parsed_reports['transcript_counts']['data']
                    key_results['transcripts_detected'] = len(transcript_data)
        
        return AnalysisSummary(
            run_uuid=run_uuid,
            sample_name=job.sample_name,
            mode=job.mode,
            status=job.status,
            work_dir=str(work_dir) if work_dir else "",
            file_summary=file_summary,  # Filtered
            all_file_counts={
                "txt_count": len(all_file_summary.txt_files),
                "csv_count": len(all_file_summary.csv_files),
                "bed_count": len(all_file_summary.bed_files),
                "other_count": len(all_file_summary.other_files),
                "total_files": len(all_file_summary.txt_files) + len(all_file_summary.csv_files) + len(all_file_summary.bed_files) + len(all_file_summary.other_files)
            },
            key_results=key_results,
            parsed_reports=parsed_reports
        )
