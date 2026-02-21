"""
MCP Tools for Server4 (Analysis Server).
Provides file discovery, reading, parsing, and analysis tools via MCP protocol.
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional

from server4.analysis_engine import (
    discover_files,
    categorize_files,
    read_file_content,
    parse_csv_file,
    parse_bed_file,
    generate_analysis_summary,
    get_job_work_dir,
    resolve_work_dir,
)
from server4.config import MAX_PREVIEW_LINES


def _json_serial(obj: Any) -> str:
    """JSON serializer for objects not serializable by default (e.g. datetime)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _dumps(obj: Any) -> str:
    """json.dumps wrapper that handles datetime serialisation."""
    return json.dumps(obj, indent=2, default=_json_serial)


# ==================== MCP Tool Definitions ====================

async def list_job_files(
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
    extensions: Optional[str] = None,
    compact: bool = True,
    max_depth: Optional[int] = None,
    name_pattern: Optional[str] = None,
) -> Dict[str, Any]:
    """
    List all files in a job's work directory.

    Args:
        work_dir: Absolute path to the workflow directory (preferred)
        run_uuid: Job UUID (legacy fallback)
        extensions: Optional comma-separated list of extensions (e.g., ".txt,.csv,.bed")
        compact: If True and file_count > 100, return only path and name (faster, smaller response)
        max_depth: If set, only list entries up to this many levels deep (1 = immediate children).

    Returns:
        Dict with file listing including paths, sizes, and metadata
    """
    try:
        ext_list = None
        if extensions:
            ext_list = [ext.strip() for ext in extensions.split(',')]

        file_listing = discover_files(run_uuid, ext_list, work_dir_path=work_dir,
                                      max_depth=max_depth,
                                      name_pattern=name_pattern)

        if compact and file_listing.file_count > 100:
            return {
                "success": True,
                "work_dir": file_listing.work_dir,
                "file_count": file_listing.file_count,
                "total_size_bytes": file_listing.total_size,
                "files": [{"path": f.path, "name": f.name, "size": f.size} for f in file_listing.files]
            }

        return {
            "success": True,
            "work_dir": file_listing.work_dir,
            "file_count": file_listing.file_count,
            "total_size_bytes": file_listing.total_size,
            "files": [f.dict() for f in file_listing.files]
        }
    
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": "Job not found or work directory missing",
            "detail": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to list files",
            "detail": str(e)
        }


async def find_file_tool(
    file_name: str,
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find a specific file by name in a job's work directory.
    Returns the relative path if found, avoiding serialization/display truncation.

    Args:
        file_name: Filename to search for (exact or partial match)
        work_dir: Absolute path to the workflow directory (preferred)
        run_uuid: Job UUID (legacy fallback)

    Returns:
        Dict with matching file paths
    """
    try:
        resolved = resolve_work_dir(work_dir=work_dir, run_uuid=run_uuid)
        _id = work_dir or run_uuid or "?"
        if not resolved or not resolved.exists():
            return {
                "success": False,
                "error": "Work directory not found",
                "work_dir": work_dir or "",
            }

        # Search for files matching the name
        # IMPORTANT: Only return files in result directories, NOT intermediate work/ folder
        file_paths = []
        for file_path in resolved.rglob("*"):
            if file_path.is_file() and file_name.lower() in file_path.name.lower():
                relative_path = str(file_path.relative_to(resolved))
                if not relative_path.startswith("work/"):
                    file_paths.append(relative_path)

        if not file_paths:
            return {
                "success": False,
                "error": "File not found (checked result directories only, ignored work/ folder)",
                "search_term": file_name,
                "work_dir": str(resolved),
            }

        return {
            "success": True,
            "work_dir": str(resolved),
            "search_term": file_name,
            "file_count": len(file_paths),
            "paths": file_paths,
            "primary_path": file_paths[0]
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to search for file",
            "detail": str(e)
        }


async def read_file_content_tool(
    file_path: str,
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
    preview_lines: Optional[int] = None
) -> Dict[str, Any]:
    """
    Read content from a specific file in a job's work directory.

    Args:
        file_path: Relative path to file from work directory
        work_dir: Absolute path to the workflow directory (preferred)
        run_uuid: Job UUID (legacy fallback)
        preview_lines: Optional line limit for preview (default: all lines)

    Returns:
        Dict with file content and metadata
    """
    try:
        if preview_lines is None:
            preview_lines = MAX_PREVIEW_LINES

        content_response = read_file_content(
            run_uuid, file_path, preview_lines, work_dir_path=work_dir
        )

        return {
            "success": True,
            "work_dir": work_dir or "",
            "file_path": content_response.file_path,
            "content": content_response.content,
            "line_count": content_response.line_count,
            "is_truncated": content_response.is_truncated,
            "file_size_bytes": content_response.file_size
        }
    
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": "File not found",
            "detail": str(e)
        }
    except ValueError as e:
        return {
            "success": False,
            "error": "Invalid file or path",
            "detail": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to read file",
            "detail": str(e)
        }


async def parse_csv_file_tool(
    file_path: str,
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
    max_rows: Optional[int] = 100
) -> Dict[str, Any]:
    """
    Parse a CSV or TSV file and return structured data.

    Args:
        file_path: Relative path to CSV/TSV file
        work_dir: Absolute path to the workflow directory (preferred)
        run_uuid: Job UUID (legacy fallback)
        max_rows: Maximum rows to return (default: 100)

    Returns:
        Dict with parsed table data including columns and rows
    """
    try:
        parsed_data = parse_csv_file(
            run_uuid, file_path, max_rows, work_dir_path=work_dir
        )

        return {
            "success": True,
            "work_dir": work_dir or "",
            "file_path": parsed_data.file_path,
            "columns": parsed_data.columns,
            "row_count": parsed_data.row_count,
            "preview_rows": parsed_data.preview_rows,
            "data": parsed_data.data,
            "metadata": parsed_data.metadata
        }
    
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": "File not found",
            "detail": str(e)
        }
    except ValueError as e:
        return {
            "success": False,
            "error": "Invalid CSV file or parsing error",
            "detail": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to parse CSV file",
            "detail": str(e)
        }


async def parse_bed_file_tool(
    file_path: str,
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
    max_records: Optional[int] = 100
) -> Dict[str, Any]:
    """
    Parse a BED format file and return structured genomic records.

    Args:
        file_path: Relative path to BED file
        work_dir: Absolute path to the workflow directory (preferred)
        run_uuid: Job UUID (legacy fallback)
        max_records: Maximum records to return (default: 100)

    Returns:
        Dict with parsed BED records
    """
    try:
        parsed_data = parse_bed_file(
            run_uuid, file_path, max_records, work_dir_path=work_dir
        )

        return {
            "success": True,
            "work_dir": work_dir or "",
            "file_path": parsed_data.file_path,
            "record_count": parsed_data.record_count,
            "preview_records": parsed_data.preview_records,
            "records": [r.dict() for r in parsed_data.records],
            "metadata": parsed_data.metadata
        }
    
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": "File not found",
            "detail": str(e)
        }
    except ValueError as e:
        return {
            "success": False,
            "error": "Invalid BED file or parsing error",
            "detail": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to parse BED file",
            "detail": str(e)
        }


async def get_analysis_summary_tool(
    run_uuid: Optional[str] = None,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get comprehensive analysis summary for a completed job.
    Includes file categorization, key results, and parsed reports.

    Args:
        run_uuid: Job UUID (still needed for DB metadata lookup)
        work_dir: Absolute path to the workflow directory (used for file ops)

    Returns:
        Dict with success and formatted summary
    """
    try:
        summary = generate_analysis_summary(
            run_uuid, work_dir_path=work_dir
        )

        output = f"Analysis Summary for: {summary.sample_name}\n\n"
        
        # Basic info table
        output += "Field | Value\n--- | ---\n"
        output += f"Sample Name | {summary.sample_name}\n"
        output += f"Mode | {summary.mode}\n"
        output += f"Status | {summary.status}\n"
        output += f"Work Directory | {summary.work_dir}\n\n"
        
        # File summary table
        output += "File Summary\n"
        output += "File Type | Count\n--- | ---\n"
        counts = summary.all_file_counts
        output += f"TXT Files | {counts.get('txt_count', 0)}\n"
        output += f"CSV Files | {counts.get('csv_count', 0)}\n"
        output += f"BED Files | {counts.get('bed_count', 0)}\n"
        output += f"Other Files | {counts.get('other_count', 0)}\n"
        output += f"Total | {counts.get('total_files', 0)}\n\n"
        
        # Key results
        output += "Key Results\n"
        for key, value in summary.key_results.items():
            output += f"{key}: {value}\n"
        
        # Add file names if available
        if summary.file_summary.txt_files:
            output += "\nTXT Files:\n"
            for f in summary.file_summary.txt_files[:10]:  # Limit to 10
                output += f"- {f.name} ({f.size} bytes)\n"
            if len(summary.file_summary.txt_files) > 10:
                output += f"- ... and {len(summary.file_summary.txt_files) - 10} more\n"
        
        if summary.file_summary.csv_files:
            output += "\nCSV Files:\n"
            for f in summary.file_summary.csv_files[:10]:
                output += f"- {f.name} ({f.size} bytes)\n"
            if len(summary.file_summary.csv_files) > 10:
                output += f"- ... and {len(summary.file_summary.csv_files) - 10} more\n"
        
        if summary.file_summary.bed_files:
            output += "\nBED Files:\n"
            for f in summary.file_summary.bed_files[:10]:
                output += f"- {f.name} ({f.size} bytes)\n"
            if len(summary.file_summary.bed_files) > 10:
                output += f"- ... and {len(summary.file_summary.bed_files) - 10} more\n"
        
        # Description
        counts = summary.all_file_counts
        output += f"\nThis analysis is for a {summary.mode} sample named {summary.sample_name}, and the job has {summary.status.lower()} successfully. "
        output += f"The work directory contains {counts.get('total_files', 0)} files, including {counts.get('txt_count', 0)} TXT files, {counts.get('csv_count', 0)} CSV files, {counts.get('bed_count', 0)} BED files, and {counts.get('other_count', 0)} other files."
        
        return {
            "success": True,
            "summary": output,
            # Structured data for programmatic access
            "mode": summary.mode,
            "status": summary.status,
            "sample_name": summary.sample_name,
            "work_dir": summary.work_dir,
            "all_file_counts": summary.all_file_counts,
            "key_results": summary.key_results,
            "file_summary": {
                "csv_files": [f.dict() for f in summary.file_summary.csv_files],
                "bed_files": [f.dict() for f in summary.file_summary.bed_files],
                "txt_files": [f.dict() for f in summary.file_summary.txt_files],
                "other_files": [f.dict() for f in summary.file_summary.other_files],
            },
        }
    
    except ValueError as e:
        return {
            "success": False,
            "error": f"Job not found - {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Analysis failed - {str(e)}"
        }


async def categorize_job_files_tool(
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Categorize all files in a job's work directory by type.
    Groups files into txt, csv, bed, and other categories.

    Args:
        work_dir: Absolute path to the workflow directory (preferred)
        run_uuid: Job UUID (legacy fallback)

    Returns:
        Dict with categorized file lists
    """
    try:
        file_summary = categorize_files(run_uuid, work_dir_path=work_dir)

        return {
            "success": True,
            "work_dir": work_dir or "",
            "txt_files": [f.dict() for f in file_summary.txt_files],
            "csv_files": [f.dict() for f in file_summary.csv_files],
            "bed_files": [f.dict() for f in file_summary.bed_files],
            "other_files": [f.dict() for f in file_summary.other_files],
            "counts": {
                "txt": len(file_summary.txt_files),
                "csv": len(file_summary.csv_files),
                "bed": len(file_summary.bed_files),
                "other": len(file_summary.other_files)
            }
        }
    
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": "Job not found or work directory missing",
            "detail": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to categorize files",
            "detail": str(e)
        }


# ==================== Tool Registry ====================

# Map of tool names to functions for MCP server registration
TOOL_REGISTRY = {
    "list_job_files": list_job_files,
    "find_file": find_file_tool,
    "read_file_content": read_file_content_tool,
    "parse_csv_file": parse_csv_file_tool,
    "parse_bed_file": parse_bed_file_tool,
    "get_analysis_summary": get_analysis_summary_tool,
    "categorize_job_files": categorize_job_files_tool
}


# Tool schemas for MCP server
TOOL_SCHEMAS = {
    "list_job_files": {
        "description": "List all files in a workflow directory with optional extension filtering.",
        "parameters": {
            "type": "object",
            "properties": {
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow directory (preferred)"
                },
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (legacy fallback \u2014 prefer work_dir)"
                },
                "extensions": {
                    "type": "string",
                    "description": "Optional comma-separated list of extensions (e.g., '.txt,.csv,.bed')"
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Only list entries up to this many levels deep (1 = immediate children)"
                },
                "name_pattern": {
                    "type": "string",
                    "description": "Optional fnmatch glob to filter entry names (e.g., 'workflow*')"
                },
            },
            "required": []
        }
    },
    "find_file": {
        "description": "Find a specific file by name (exact or partial match) and return its relative path.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "Filename to search for (exact or partial match, case-insensitive)"
                },
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow directory (preferred)"
                },
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (legacy fallback \u2014 prefer work_dir)"
                },
            },
            "required": ["file_name"]
        }
    },
    "read_file_content": {
        "description": "Read content from a specific file in a workflow directory",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to file from work directory"
                },
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow directory (preferred)"
                },
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (legacy fallback \u2014 prefer work_dir)"
                },
                "preview_lines": {
                    "type": "integer",
                    "description": "Optional line limit for preview"
                }
            },
            "required": ["file_path"]
        }
    },
    "parse_csv_file": {
        "description": "Parse a CSV or TSV file and return structured tabular data",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to CSV/TSV file"
                },
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow directory (preferred)"
                },
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (legacy fallback \u2014 prefer work_dir)"
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Maximum rows to return (default: 100)"
                }
            },
            "required": ["file_path"]
        }
    },
    "parse_bed_file": {
        "description": "Parse a BED format file and return structured genomic records",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to BED file"
                },
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow directory (preferred)"
                },
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (legacy fallback \u2014 prefer work_dir)"
                },
                "max_records": {
                    "type": "integer",
                    "description": "Maximum records to return (default: 100)"
                }
            },
            "required": ["file_path"]
        }
    },
    "get_analysis_summary": {
        "description": "Get comprehensive analysis summary for a completed job",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (needed for DB metadata lookup)"
                },
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow directory"
                },
            },
            "required": ["run_uuid"]
        }
    },
    "categorize_job_files": {
        "description": "Categorize all files in a workflow directory by type (txt, csv, bed, other)",
        "parameters": {
            "type": "object",
            "properties": {
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow directory (preferred)"
                },
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (legacy fallback \u2014 prefer work_dir)"
                },
            },
            "required": []
        }
    }
}
