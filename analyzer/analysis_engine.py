"""
Analysis Engine for Analyzer.
Handles file discovery, parsing, and analysis of Dogme job results.
"""

import csv
import fnmatch
import json
import re
from collections import OrderedDict
import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from analyzer.config import (
    AGOUTIC_WORK_DIR,
    AGOUTIC_DATA,
    MAX_PREVIEW_LINES,
    MAX_FILE_SIZE_BYTES,
    SUPPORTED_TEXT_EXTENSIONS,
    BED_COLUMNS
)
from analyzer.schemas import (
    FileInfo,
    FileListing,
    FileContentResponse,
    ParsedTableData,
    BedRecord,
    ParsedBedData,
    ParsedXgenePyOutputs,
    JobFileSummary,
    AnalysisSummary
)
from sqlalchemy.orm import load_only
from analyzer.models import DogmeJob
from analyzer.db import get_db


# ==================== File Discovery ====================

def resolve_work_dir(
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
) -> Optional[Path]:
    """
    Resolve the work directory from *either* a direct path or a run UUID.

    Precedence:
      1. ``work_dir`` — used directly if it is an absolute path that exists.
      2. ``run_uuid`` — looked up via DB (legacy path).

    At least one of the two must be provided.
    """
    # Direct path — preferred (avoids DB lookup entirely)
    if work_dir:
        p = Path(work_dir)
        if p.is_absolute() and p.exists():
            return p
        # Could be an absolute path that doesn't exist yet — still return it
        if p.is_absolute():
            return p

    # Fallback: UUID-based DB lookup
    if run_uuid:
        return get_job_work_dir(run_uuid)

    return None


def get_job_work_dir(run_uuid: str) -> Optional[Path]:
    """
    Get work directory path for a job (legacy UUID-based lookup).

    Resolves in order:
      1. nextflow_work_dir from the job record (may be slug-based or UUID-based)
      2. output_directory from the job record
      3. Legacy jailed path: AGOUTIC_DATA/users/{user_id}/{project_id}/{run_uuid}/
      4. Legacy flat path: AGOUTIC_WORK_DIR/{run_uuid}/
    """
    with get_db() as db:
        job = db.query(DogmeJob).options(
            load_only(
                DogmeJob.run_uuid, DogmeJob.nextflow_work_dir,
                DogmeJob.output_directory, DogmeJob.user_id, DogmeJob.project_id,
            )
        ).filter(DogmeJob.run_uuid == run_uuid).first()
        if not job:
            return None
        # Use nextflow_work_dir if available — this is the authoritative source.
        # New jobs store slug-based paths (e.g. users/eli/my-project/workflow1/),
        # old jobs store UUID-based paths (e.g. users/{uuid}/{uuid}/{run_uuid}/).
        wd = job.nextflow_work_dir or job.output_directory
        if wd:
            return Path(wd)
        # Try jailed path if user_id is available (legacy UUID layout)
        user_id = getattr(job, 'user_id', None)
        if user_id and job.project_id:
            jailed = AGOUTIC_DATA / "users" / user_id / job.project_id / run_uuid
            if jailed.exists():
                return jailed
        # Fallback: construct from config (legacy flat layout)
        return AGOUTIC_WORK_DIR / run_uuid


def discover_files(
    run_uuid: Optional[str] = None,
    extensions: Optional[List[str]] = None,
    *,
    work_dir_path: Optional[str] = None,
    max_depth: Optional[int] = None,
    name_pattern: Optional[str] = None,
) -> FileListing:
    """
    Discover all files in a job's work directory.

    Args:
        run_uuid: Job UUID (legacy — prefer work_dir_path)
        extensions: Optional list of extensions to filter (e.g., ['.txt', '.csv'])
        work_dir_path: Absolute path to the workflow directory
        max_depth: If set, only list entries up to this many levels deep.
                   1 = immediate children only (files + dirs).
        name_pattern: Optional fnmatch glob to filter entry names
                      (e.g., 'workflow*' to show only workflow dirs).

    Returns:
        FileListing with all discovered files
    """
    work_dir = resolve_work_dir(work_dir=work_dir_path, run_uuid=run_uuid)
    if not work_dir or not work_dir.exists():
        _id = work_dir_path or run_uuid or '?'
        raise FileNotFoundError(f"Work directory not found for {_id}")
    
    files = []
    total_size = 0

    # --- Depth-limited listing (e.g. for "list workflows") ---
    if max_depth is not None and max_depth >= 1:
        # Only list immediate children (depth=1) — include both files and
        # directories (directories are returned as entries with size=0 and
        # a trailing '/' in the name so callers can distinguish them).
        for child in sorted(work_dir.iterdir()):
            relative_path = child.relative_to(work_dir)
            rel_str = str(relative_path)
            # Skip work/ and dor*/ directories (Nextflow intermediates)
            _dirname = child.name
            if _dirname == "work" or _dirname.startswith("dor"):
                continue
            # Apply optional name pattern filter (fnmatch glob)
            if name_pattern and not fnmatch.fnmatch(_dirname, name_pattern):
                continue
            if child.is_dir():
                files.append(FileInfo(
                    path=rel_str + "/",
                    name=child.name + "/",
                    size=0,
                    extension="",
                    modified_time=datetime.fromtimestamp(child.stat().st_mtime)
                ))
            elif child.is_file():
                stat = child.stat()
                if extensions and child.suffix.lower() not in extensions:
                    continue
                files.append(FileInfo(
                    path=rel_str,
                    name=child.name,
                    size=stat.st_size,
                    extension=child.suffix,
                    modified_time=datetime.fromtimestamp(stat.st_mtime)
                ))
                total_size += stat.st_size
        return FileListing(
            run_uuid=run_uuid or "",
            work_dir=str(work_dir),
            files=files,
            file_count=len(files),
            total_size=total_size
        )
    
    # --- Full recursive listing ---
    # Recursively find all files
    for file_path in work_dir.rglob("*"):
        if file_path.is_file():
            # Skip files in work/ or dor*/ subdirectories (intermediate processing files)
            relative_path = file_path.relative_to(work_dir)
            _parts = relative_path.parts
            if _parts and (_parts[0] == "work" or _parts[0].startswith("dor")):
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
        run_uuid=run_uuid or "",
        work_dir=str(work_dir),
        files=sorted(files, key=lambda f: f.path),
        file_count=len(files),
        total_size=total_size
    )


def categorize_files(
    run_uuid: Optional[str] = None,
    *,
    work_dir_path: Optional[str] = None,
) -> JobFileSummary:
    """Categorize files by type."""
    all_files = discover_files(run_uuid, work_dir_path=work_dir_path)
    
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
    run_uuid: Optional[str] = None,
    file_path: str = "",
    preview_lines: Optional[int] = None,
    *,
    work_dir_path: Optional[str] = None,
) -> FileContentResponse:
    """
    Read content from a file.

    Args:
        run_uuid: Job UUID (legacy — prefer work_dir_path)
        file_path: Relative path from work directory
        preview_lines: Optional line limit for preview
        work_dir_path: Absolute path to the workflow directory

    Returns:
        FileContentResponse with file content
    """
    work_dir = resolve_work_dir(work_dir=work_dir_path, run_uuid=run_uuid)
    _id = work_dir_path or run_uuid or '?'
    if not work_dir:
        raise FileNotFoundError(f"Work directory not found for {_id}")
    
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
        _uuid = run_uuid or ""
        with open(full_path, 'r', encoding='utf-8') as f:
            if preview_lines:
                lines = []
                for i, line in enumerate(f):
                    if i >= preview_lines:
                        return FileContentResponse(
                            run_uuid=_uuid,
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
                    run_uuid=_uuid,
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
                    run_uuid=_uuid,
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
    run_uuid: Optional[str] = None,
    file_path: str = "",
    max_rows: Optional[int] = None,
    *,
    work_dir_path: Optional[str] = None,
) -> ParsedTableData:
    """
    Parse CSV/TSV file using pandas for proper type detection.

    Args:
        run_uuid: Job UUID (legacy — prefer work_dir_path)
        file_path: Relative path from work directory
        max_rows: Maximum rows to return (for preview)
        work_dir_path: Absolute path to the workflow directory

    Returns:
        ParsedTableData with structured data
    """
    work_dir = resolve_work_dir(work_dir=work_dir_path, run_uuid=run_uuid)
    _id = work_dir_path or run_uuid or '?'
    if not work_dir:
        raise FileNotFoundError(f"Work directory not found for {_id}")
    
    full_path = (work_dir / file_path).resolve()
    
    # Security check
    if not str(full_path).startswith(str(work_dir.resolve())):
        raise ValueError(f"Invalid file path: {file_path}")
    
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {file_path} (absolute path: {full_path})")
    
    # Determine separator
    sep = '\t' if file_path.endswith('.tsv') else ','
    
    try:
        df = pd.read_csv(full_path, sep=sep, low_memory=False)
    except Exception as e:
        raise ValueError(f"Error parsing CSV file: {str(e)}")
    
    total_rows = len(df)
    columns = list(df.columns)
    
    # Build column statistics
    col_stats = {}
    for col in columns:
        info: Dict[str, Any] = {"dtype": str(df[col].dtype), "nulls": int(df[col].isna().sum())}
        if pd.api.types.is_numeric_dtype(df[col]):
            desc = df[col].describe()
            info.update({
                "min": _safe_scalar(desc.get("min")),
                "max": _safe_scalar(desc.get("max")),
                "mean": _safe_scalar(desc.get("mean")),
                "median": _safe_scalar(df[col].median()),
                "std": _safe_scalar(desc.get("std")),
            })
        else:
            info["unique"] = int(df[col].nunique())
            top = df[col].value_counts().head(5)
            info["top_values"] = {str(k): int(v) for k, v in top.items()}
        col_stats[col] = info
    
    # Truncate for preview if needed
    preview_df = df.head(max_rows) if max_rows else df
    # Convert to list of dicts, handling NaN/Inf
    rows = json.loads(preview_df.to_json(orient="records", default_handler=str))
    
    metadata = {
        "separator": sep,
        "column_count": len(columns),
        "total_rows": total_rows,
        "is_truncated": max_rows is not None and total_rows > max_rows,
        "column_stats": col_stats,
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
    }
    
    return ParsedTableData(
        run_uuid=run_uuid or "",
        file_path=file_path,
        columns=columns,
        row_count=total_rows,
        data=rows,
        preview_rows=len(rows),
        metadata=metadata
    )


def _safe_scalar(val) -> Any:
    """Convert numpy scalar to Python native for JSON serialisation."""
    if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        return None
    if hasattr(val, "item"):
        return val.item()
    return val


def _parse_bed_records_from_path(
    full_path: Path,
    max_records: Optional[int] = None,
) -> Tuple[List[BedRecord], int]:
    """Parse BED records from an absolute file path."""
    records: list[BedRecord] = []
    total_records = 0

    try:
        with open(full_path, 'r', encoding='utf-8') as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith('#') or line.startswith('track') or line.startswith('browser'):
                    continue

                fields = line.split('\t')
                if len(fields) < 3:
                    raise ValueError(f"BED line has fewer than 3 columns: {line}")

                chrom_start = int(fields[1])
                chrom_end = int(fields[2])
                if chrom_end <= chrom_start:
                    continue

                total_records += 1
                if max_records is not None and len(records) >= max_records:
                    continue

                record = BedRecord(
                    chrom=fields[0],
                    chromStart=chrom_start,
                    chromEnd=chrom_end,
                    name=fields[3] if len(fields) > 3 else None,
                    score=float(fields[4]) if len(fields) > 4 and fields[4] != '.' else None,
                    strand=fields[5] if len(fields) > 5 else None,
                    extra_fields={},
                )
                if len(fields) > 6:
                    for index, field in enumerate(fields[6:], start=6):
                        if index < len(BED_COLUMNS):
                            record.extra_fields[BED_COLUMNS[index]] = field
                        else:
                            record.extra_fields[f"field_{index}"] = field
                records.append(record)
    except Exception as exc:
        raise ValueError(f"Error parsing BED file: {exc}") from exc

    return records, total_records


def _resolve_input_path(
    path_value: str,
    *,
    work_dir_path: Optional[str] = None,
    expect_dir: bool = False,
) -> Path:
    """Resolve an input path that may be absolute or relative to work_dir."""
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        if not work_dir_path:
            raise ValueError(f"Relative path requires work_dir: {path_value}")
        candidate = Path(work_dir_path).expanduser() / candidate
    resolved = candidate.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Path not found: {resolved}")
    if expect_dir and not resolved.is_dir():
        raise NotADirectoryError(f"Expected a directory: {resolved}")
    if not expect_dir and not resolved.is_file():
        raise FileNotFoundError(f"Expected a file: {resolved}")
    return resolved


def _matches_bed_pattern(file_path: Path, pattern: str) -> bool:
    if file_path.suffix.lower() != '.bed':
        return False
    normalized = (pattern or "*.bed").strip()
    if not normalized:
        return True
    if any(token in normalized for token in "*?["):
        return fnmatch.fnmatch(file_path.name, normalized) or fnmatch.fnmatch(str(file_path), normalized)
    return normalized.lower() in file_path.name.lower()


def _resolve_bed_source(
    *,
    bed_path: Optional[str],
    folder_path: Optional[str],
    pattern: Optional[str],
    sample_label: Optional[str],
    work_dir_path: Optional[str],
) -> Dict[str, Any]:
    if bed_path:
        resolved_path = _resolve_input_path(bed_path, work_dir_path=work_dir_path, expect_dir=False)
        return {
            "path": resolved_path,
            "label": sample_label or resolved_path.stem,
            "source_mode": "explicit_path",
            "search_pattern": None,
        }

    if not folder_path:
        raise ValueError("Each BED source needs either an explicit bed_path or a folder_path.")

    resolved_folder = _resolve_input_path(folder_path, work_dir_path=work_dir_path, expect_dir=True)
    search_pattern = pattern or "*.bed"
    matches = [
        candidate for candidate in resolved_folder.rglob("*")
        if candidate.is_file() and _matches_bed_pattern(candidate, search_pattern)
    ]
    if not matches:
        raise FileNotFoundError(
            f"No BED files matched pattern '{search_pattern}' under {resolved_folder}"
        )

    selected = sorted(matches, key=lambda candidate: (candidate.stat().st_mtime, str(candidate)))[-1]
    return {
        "path": selected,
        "label": sample_label or selected.stem,
        "source_mode": "folder_search",
        "search_pattern": search_pattern,
        "search_root": str(resolved_folder),
        "matched_count": len(matches),
    }


def _deduplicate_sample_labels(label_a: str, label_b: str) -> Tuple[str, str]:
    if label_a.strip().lower() != label_b.strip().lower():
        return label_a, label_b
    return f"{label_a} (A)", f"{label_b} (B)"


def _slugify_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (value or "sample").strip()).strip("_")
    return cleaned or "sample"


def _format_interval_strings(intervals: List[Dict[str, Any]]) -> str:
    return "; ".join(
        f"{interval['chrom']}:{interval['start']}-{interval['end']}"
        for interval in intervals
    )


def _serialize_component_rows(rows: List[Dict[str, Any]], columns: List[str]) -> List[Dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        serialized.append({column: row.get(column) for column in columns})
    return serialized


def _write_bed_rows(path: Path, rows: List[Dict[str, Any]], name_key: str) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t')
        for row in rows:
            chrom = row.get('chrom')
            start = row.get('bed_start')
            end = row.get('bed_end')
            if chrom is None or start is None or end is None:
                continue
            writer.writerow([chrom, int(start), int(end), row.get(name_key) or row.get('component_id') or 'component'])


def compare_bed_region_overlaps(
    *,
    bed_a_path: Optional[str] = None,
    bed_b_path: Optional[str] = None,
    folder_a: Optional[str] = None,
    folder_b: Optional[str] = None,
    pattern_a: Optional[str] = None,
    pattern_b: Optional[str] = None,
    sample_a_label: Optional[str] = None,
    sample_b_label: Optional[str] = None,
    min_overlap_bp: int = 1,
    export_dir: Optional[str] = None,
    work_dir_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare two BED files and build connected overlap-component tables."""
    if min_overlap_bp < 1:
        raise ValueError("min_overlap_bp must be at least 1")

    resolved_a = _resolve_bed_source(
        bed_path=bed_a_path,
        folder_path=folder_a,
        pattern=pattern_a,
        sample_label=sample_a_label,
        work_dir_path=work_dir_path,
    )
    resolved_b = _resolve_bed_source(
        bed_path=bed_b_path,
        folder_path=folder_b,
        pattern=pattern_b,
        sample_label=sample_b_label,
        work_dir_path=work_dir_path,
    )

    label_a, label_b = _deduplicate_sample_labels(
        str(resolved_a["label"]),
        str(resolved_b["label"]),
    )
    resolved_a["label"] = label_a
    resolved_b["label"] = label_b

    chrom_groups: dict[str, list[dict[str, Any]]] = {}
    for sample_key, source in (("a", resolved_a), ("b", resolved_b)):
        records, total_records = _parse_bed_records_from_path(source["path"])
        source["record_count"] = total_records
        intervals = chrom_groups
        for record in records:
            interval = {
                "sample_key": sample_key,
                "sample_label": source["label"],
                "chrom": record.chrom,
                "start": int(record.chromStart),
                "end": int(record.chromEnd),
                "name": record.name,
            }
            intervals.setdefault(record.chrom, []).append(interval)

    all_components: list[dict[str, Any]] = []
    component_index = 1
    overlap_threshold = min_overlap_bp
    for chrom in sorted(chrom_groups):
        chrom_intervals = sorted(chrom_groups[chrom], key=lambda item: (item["start"], item["end"], item["sample_key"]))
        current: Optional[dict[str, Any]] = None
        for interval in chrom_intervals:
            if current is None:
                current = {
                    "chrom": chrom,
                    "component_start": interval["start"],
                    "component_end": interval["end"],
                    "intervals": [interval],
                }
                continue

            if interval["start"] <= current["component_end"] - overlap_threshold:
                current["intervals"].append(interval)
                current["component_end"] = max(current["component_end"], interval["end"])
                continue

            all_components.append(current)
            current = {
                "chrom": chrom,
                "component_start": interval["start"],
                "component_end": interval["end"],
                "intervals": [interval],
            }

        if current is not None:
            all_components.append(current)

    component_rows: list[dict[str, Any]] = []
    shared_rows: list[dict[str, Any]] = []
    sample_a_only_rows: list[dict[str, Any]] = []
    sample_b_only_rows: list[dict[str, Any]] = []

    for component in all_components:
        sample_a_intervals = [item for item in component["intervals"] if item["sample_key"] == "a"]
        sample_b_intervals = [item for item in component["intervals"] if item["sample_key"] == "b"]
        has_a = bool(sample_a_intervals)
        has_b = bool(sample_b_intervals)
        component_id = f"C{component_index}"
        component_index += 1

        row = {
            "component_id": component_id,
            "chrom": component["chrom"],
            "component_start": component["component_start"],
            "component_end": component["component_end"],
            label_a: has_a,
            label_b: has_b,
            "overlap_class": "shared" if has_a and has_b else (f"{label_a} only" if has_a else f"{label_b} only"),
            "sample_a_start": min((item["start"] for item in sample_a_intervals), default=None),
            "sample_a_end": max((item["end"] for item in sample_a_intervals), default=None),
            "sample_b_start": min((item["start"] for item in sample_b_intervals), default=None),
            "sample_b_end": max((item["end"] for item in sample_b_intervals), default=None),
            "sample_a_interval_count": len(sample_a_intervals),
            "sample_b_interval_count": len(sample_b_intervals),
            "sample_a_regions": _format_interval_strings(sample_a_intervals) if has_a else "",
            "sample_b_regions": _format_interval_strings(sample_b_intervals) if has_b else "",
        }
        component_rows.append(row)

        if has_a and has_b:
            shared_rows.append({
                **row,
                "bed_start": row["component_start"],
                "bed_end": row["component_end"],
            })
        elif has_a:
            sample_a_only_rows.append({
                **row,
                "bed_start": row["sample_a_start"],
                "bed_end": row["sample_a_end"],
            })
        else:
            sample_b_only_rows.append({
                **row,
                "bed_start": row["sample_b_start"],
                "bed_end": row["sample_b_end"],
            })

    component_columns = [
        "component_id", "chrom", "component_start", "component_end",
        label_a, label_b, "overlap_class",
        "sample_a_start", "sample_a_end", "sample_b_start", "sample_b_end",
        "sample_a_interval_count", "sample_b_interval_count",
        "sample_a_regions", "sample_b_regions",
    ]
    summary_rows = [
        {"combination": f"{label_a} only", label_a: True, label_b: False, "count": len(sample_a_only_rows)},
        {"combination": "shared", label_a: True, label_b: True, "count": len(shared_rows)},
        {"combination": f"{label_b} only", label_a: False, label_b: True, "count": len(sample_b_only_rows)},
    ]

    shared_columns = component_columns
    summary_columns = ["combination", label_a, label_b, "count"]

    slug_a = _slugify_label(label_a)
    slug_b = _slugify_label(label_b)
    comparison_slug = f"{slug_a}__vs__{slug_b}"
    if export_dir:
        export_root = Path(export_dir).expanduser()
        if not export_root.is_absolute():
            if not work_dir_path:
                raise ValueError(f"Relative export_dir requires work_dir: {export_dir}")
            export_root = Path(work_dir_path).expanduser() / export_root
        comparison_dir = export_root.resolve() / comparison_slug
    else:
        comparison_dir = resolved_a["path"].parent / "agoutic_bed_overlaps" / comparison_slug
    comparison_dir.mkdir(parents=True, exist_ok=True)

    shared_a_bed = comparison_dir / f"shared_{slug_a}.bed"
    shared_b_bed = comparison_dir / f"shared_{slug_b}.bed"
    sample_a_only_bed = comparison_dir / f"{slug_a}_only.bed"
    sample_b_only_bed = comparison_dir / f"{slug_b}_only.bed"

    shared_a_rows = [{**row, "bed_start": row["sample_a_start"], "bed_end": row["sample_a_end"]} for row in shared_rows]
    shared_b_rows = [{**row, "bed_start": row["sample_b_start"], "bed_end": row["sample_b_end"]} for row in shared_rows]
    _write_bed_rows(shared_a_bed, shared_a_rows, "component_id")
    _write_bed_rows(shared_b_bed, shared_b_rows, "component_id")
    _write_bed_rows(sample_a_only_bed, sample_a_only_rows, "component_id")
    _write_bed_rows(sample_b_only_bed, sample_b_only_rows, "component_id")

    dataframe_bundle: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    comparison_label = f"{label_a} vs {label_b}"
    dataframe_bundle[f"Shared open chromatin overlaps ({comparison_label})"] = {
        "columns": shared_columns,
        "data": _serialize_component_rows(shared_rows, shared_columns),
        "row_count": len(shared_rows),
        "metadata": {
            "label": f"Shared open chromatin overlaps ({comparison_label})",
            "visible": True,
            "kind": "shared_bed_overlap_components",
            "sample_labels": [label_a, label_b],
        },
    }
    dataframe_bundle[f"{label_a} only open chromatin ({comparison_label})"] = {
        "columns": shared_columns,
        "data": _serialize_component_rows(sample_a_only_rows, shared_columns),
        "row_count": len(sample_a_only_rows),
        "metadata": {
            "label": f"{label_a} only open chromatin ({comparison_label})",
            "visible": True,
            "kind": "bed_only_components",
            "sample_label": label_a,
        },
    }
    dataframe_bundle[f"{label_b} only open chromatin ({comparison_label})"] = {
        "columns": shared_columns,
        "data": _serialize_component_rows(sample_b_only_rows, shared_columns),
        "row_count": len(sample_b_only_rows),
        "metadata": {
            "label": f"{label_b} only open chromatin ({comparison_label})",
            "visible": True,
            "kind": "bed_only_components",
            "sample_label": label_b,
        },
    }
    dataframe_bundle[f"Overlap combination counts ({comparison_label})"] = {
        "columns": summary_columns,
        "data": summary_rows,
        "row_count": len(summary_rows),
        "metadata": {
            "label": f"Overlap combination counts ({comparison_label})",
            "visible": True,
            "kind": "bed_overlap_summary",
            "set_columns": [label_a, label_b],
        },
    }
    dataframe_bundle[f"Overlap membership components ({comparison_label})"] = {
        "columns": component_columns,
        "data": _serialize_component_rows(component_rows, component_columns),
        "row_count": len(component_rows),
        "metadata": {
            "label": f"Overlap membership components ({comparison_label})",
            "visible": True,
            "kind": "overlap_membership",
            "set_columns": [label_a, label_b],
            "source_labels": [label_a, label_b],
            "match_key_column": "component_id",
        },
    }

    return {
        "comparison_label": comparison_label,
        "sample_a": {
            "label": label_a,
            "bed_path": str(resolved_a["path"]),
            "source_mode": resolved_a["source_mode"],
            "search_pattern": resolved_a.get("search_pattern"),
            "search_root": resolved_a.get("search_root"),
            "matched_count": resolved_a.get("matched_count"),
            "record_count": resolved_a.get("record_count", 0),
        },
        "sample_b": {
            "label": label_b,
            "bed_path": str(resolved_b["path"]),
            "source_mode": resolved_b["source_mode"],
            "search_pattern": resolved_b.get("search_pattern"),
            "search_root": resolved_b.get("search_root"),
            "matched_count": resolved_b.get("matched_count"),
            "record_count": resolved_b.get("record_count", 0),
        },
        "counts": {
            "shared_components": len(shared_rows),
            "sample_a_only_components": len(sample_a_only_rows),
            "sample_b_only_components": len(sample_b_only_rows),
            "total_components": len(component_rows),
        },
        "export_dir": str(comparison_dir),
        "exported_files": {
            "shared_sample_a_bed": str(shared_a_bed),
            "shared_sample_b_bed": str(shared_b_bed),
            "sample_a_only_bed": str(sample_a_only_bed),
            "sample_b_only_bed": str(sample_b_only_bed),
        },
        "columns": shared_columns,
        "data": _serialize_component_rows(shared_rows, shared_columns),
        "row_count": len(shared_rows),
        "metadata": {
            "label": f"Shared open chromatin overlaps ({comparison_label})",
            "sample_labels": [label_a, label_b],
            "min_overlap_bp": min_overlap_bp,
        },
        "_dataframes": dataframe_bundle,
    }


# ==================== BED File Parsing ====================

def parse_bed_file(
    run_uuid: Optional[str] = None,
    file_path: str = "",
    max_records: Optional[int] = None,
    *,
    work_dir_path: Optional[str] = None,
) -> ParsedBedData:
    """
    Parse BED format file.

    Args:
        run_uuid: Job UUID (legacy — prefer work_dir_path)
        file_path: Relative path from work directory
        max_records: Maximum records to return (for preview)
        work_dir_path: Absolute path to the workflow directory

    Returns:
        ParsedBedData with structured records
    """
    work_dir = resolve_work_dir(work_dir=work_dir_path, run_uuid=run_uuid)
    _id = work_dir_path or run_uuid or '?'
    if not work_dir:
        raise FileNotFoundError(f"Work directory not found for {_id}")
    
    full_path = (work_dir / file_path).resolve()
    
    # Security check
    if not str(full_path).startswith(str(work_dir.resolve())):
        raise ValueError(f"Invalid file path: {file_path}")
    
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {file_path} (absolute path: {full_path})")
    
    records, total_records = _parse_bed_records_from_path(full_path, max_records=max_records)
    
    # Generate metadata
    metadata = {
        "total_records": total_records,
        "is_truncated": max_records is not None and total_records > max_records,
        "has_header": False  # BED files typically don't have headers
    }
    
    return ParsedBedData(
        run_uuid=run_uuid or "",
        file_path=file_path,
        record_count=total_records,
        records=records,
        preview_records=len(records),
        metadata=metadata
    )


def parse_xgenepy_outputs(
    run_uuid: Optional[str] = None,
    output_dir: str = "",
    max_rows: Optional[int] = 200,
    *,
    work_dir_path: Optional[str] = None,
) -> ParsedXgenePyOutputs:
    """Parse canonical XgenePy output artifacts from a workflow/project directory."""
    work_dir = resolve_work_dir(work_dir=work_dir_path, run_uuid=run_uuid)
    _id = work_dir_path or run_uuid or "?"
    if not work_dir:
        raise FileNotFoundError(f"Work directory not found for {_id}")

    run_dir = (work_dir / output_dir).resolve() if output_dir else work_dir.resolve()
    if not str(run_dir).startswith(str(work_dir.resolve())):
        raise ValueError(f"Invalid output_dir path: {output_dir}")
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"XgenePy output directory not found: {output_dir or str(work_dir)}")

    required = [
        "fit_summary.json",
        "assignments.tsv",
        "proportion_cis.tsv",
        "model_metadata.json",
        "run_manifest.json",
        "plots",
    ]
    missing: list[str] = []
    for name in required:
        target = run_dir / name
        if name == "plots":
            if not target.exists() or not target.is_dir():
                missing.append("plots/")
        else:
            if not target.exists() or not target.is_file():
                missing.append(name)

    def _load_json(name: str) -> Dict[str, Any]:
        path = run_dir / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Failed to parse {name}: {exc}") from exc

    def _load_table(name: str) -> List[Dict[str, Any]]:
        path = run_dir / name
        if not path.exists():
            return []
        try:
            df = pd.read_csv(path, sep="\t", low_memory=False)
            if max_rows is not None:
                df = df.head(max_rows)
            return json.loads(df.to_json(orient="records", default_handler=str))
        except Exception as exc:
            raise ValueError(f"Failed to parse {name}: {exc}") from exc

    plots: List[str] = []
    plots_dir = run_dir / "plots"
    if plots_dir.exists() and plots_dir.is_dir():
        for item in sorted(plots_dir.iterdir()):
            if item.is_file():
                plots.append(str(item.relative_to(work_dir.resolve())))

    fit_summary = _load_json("fit_summary.json")
    model_metadata = _load_json("model_metadata.json")
    run_manifest = _load_json("run_manifest.json")
    assignments = _load_table("assignments.tsv")
    proportion_cis = _load_table("proportion_cis.tsv")

    metadata = {
        "work_dir": str(work_dir),
        "resolved_output_dir": str(run_dir),
        "preview_rows": max_rows,
        "plot_count": len(plots),
    }

    resolved_output_ref = str(run_dir.relative_to(work_dir.resolve())) if run_dir != work_dir.resolve() else "."

    return ParsedXgenePyOutputs(
        run_uuid=run_uuid or "",
        output_dir=resolved_output_ref,
        required_outputs_present=not missing,
        missing_outputs=missing,
        fit_summary=fit_summary,
        model_metadata=model_metadata,
        run_manifest=run_manifest,
        assignments=assignments,
        proportion_cis=proportion_cis,
        plots=plots,
        metadata=metadata,
    )


# ==================== Analysis Summary ====================

def generate_analysis_summary(
    run_uuid: Optional[str] = None,
    *,
    work_dir_path: Optional[str] = None,
) -> AnalysisSummary:
    """
    Generate comprehensive analysis summary for a job.

    Args:
        run_uuid: Job UUID (still needed — summary requires DB metadata)
        work_dir_path: Absolute path to the workflow directory (used for file discovery)

    Returns:
        AnalysisSummary with all available information
    """
    # We need the DB record for sample_name/mode/status, so run_uuid is still required
    # for generate_analysis_summary.  work_dir_path is passed through for file ops.
    if not run_uuid:
        raise ValueError("run_uuid is required for generate_analysis_summary")
    with get_db() as db:
        job = db.query(DogmeJob).options(
            load_only(
                DogmeJob.run_uuid, DogmeJob.sample_name, DogmeJob.mode,
                DogmeJob.status, DogmeJob.nextflow_work_dir, DogmeJob.output_directory,
            )
        ).filter(DogmeJob.run_uuid == run_uuid).first()
        if not job:
            raise ValueError(f"Job not found: {run_uuid}")

        _wdp = work_dir_path or (job.nextflow_work_dir or job.output_directory or None)

        # Categorize all files
        all_file_summary = categorize_files(run_uuid, work_dir_path=_wdp)
        
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
        work_dir = resolve_work_dir(work_dir=_wdp, run_uuid=run_uuid)
        if work_dir:
            # Parse QC summary if exists
            qc_files = [f for f in file_summary.csv_files if 'qc_summary' in f.name.lower() or 'qc' in f.name.lower()]
            if qc_files:
                try:
                    qc_data = parse_csv_file(run_uuid, qc_files[0].path, max_rows=100, work_dir_path=_wdp)
                    parsed_reports['qc_summary'] = qc_data.dict()
                except Exception:
                    pass
            
            # Parse stats files
            stats_files = [f for f in file_summary.csv_files if 'stats' in f.name.lower() or 'flagstat' in f.name.lower()]
            if stats_files:
                try:
                    stats_data = parse_csv_file(run_uuid, stats_files[0].path, max_rows=100, work_dir_path=_wdp)
                    parsed_reports['stats'] = stats_data.dict()
                except Exception:
                    pass
            
            # Mode-specific parsing
            if job.mode.upper() == 'CDNA':
                # Parse gene counts
                gene_files = [f for f in file_summary.csv_files if 'gene_counts' in f.name.lower() or 'counts' in f.name.lower() and 'gene' in f.name.lower()]
                if gene_files:
                    try:
                        gene_data = parse_csv_file(run_uuid, gene_files[0].path, max_rows=50, work_dir_path=_wdp)
                        parsed_reports['gene_counts'] = gene_data.dict()
                    except Exception:
                        pass
                
                # Parse transcript counts
                transcript_files = [f for f in file_summary.csv_files if 'transcript_counts' in f.name.lower() or 'isoform' in f.name.lower()]
                if transcript_files:
                    try:
                        transcript_data = parse_csv_file(run_uuid, transcript_files[0].path, max_rows=50, work_dir_path=_wdp)
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
