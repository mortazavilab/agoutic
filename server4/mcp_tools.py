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
    get_job_work_dir
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
    run_uuid: str,
    extensions: Optional[str] = None,
    compact: bool = True
) -> Dict[str, Any]:
    """
    List all files in a job's work directory.
    
    Args:
        run_uuid: Job UUID to list files for
        extensions: Optional comma-separated list of extensions (e.g., ".txt,.csv,.bed")
        compact: If True and file_count > 100, return only path and name (faster, smaller response)
    
    Returns:
        Dict with file listing including paths, sizes, and metadata
    """
    try:
        # Parse extensions if provided
        ext_list = None
        if extensions:
            ext_list = [ext.strip() for ext in extensions.split(',')]
        
        # Discover files
        file_listing = discover_files(run_uuid, ext_list)
        
        # For large listings, return compact format to avoid truncation
        if compact and file_listing.file_count > 100:
            return {
                "success": True,
                "run_uuid": file_listing.run_uuid,
                "work_dir": file_listing.work_dir,
                "file_count": file_listing.file_count,
                "total_size_bytes": file_listing.total_size,
                "files": [{"path": f.path, "name": f.name, "size": f.size} for f in file_listing.files]
            }
        
        # For smaller listings, return full details
        return {
            "success": True,
            "run_uuid": file_listing.run_uuid,
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
    run_uuid: str,
    file_name: str
) -> Dict[str, Any]:
    """
    Find a specific file by name in a job's work directory.
    Returns the relative path if found, avoiding serialization/display truncation.
    
    Args:
        run_uuid: Job UUID
        file_name: Filename to search for (exact or partial match)
    
    Returns:
        Dict with matching file paths
    """
    try:
        work_dir = get_job_work_dir(run_uuid)
        if not work_dir or not work_dir.exists():
            return {
                "success": False,
                "error": "Job not found or work directory missing",
                "run_uuid": run_uuid
            }
        
        # Search for files matching the name
        # IMPORTANT: Only return files in result directories, NOT intermediate work/ folder
        file_paths = []  # Flat list of paths to avoid MCP truncation
        for file_path in work_dir.rglob("*"):
            if file_path.is_file() and file_name.lower() in file_path.name.lower():
                relative_path = str(file_path.relative_to(work_dir))
                # FILTER: Skip any paths in work/ folder (intermediate cached results)
                if not relative_path.startswith("work/"):
                    file_paths.append(relative_path)
        
        if not file_paths:
            return {
                "success": False,
                "error": "File not found (checked result directories only, ignored work/ folder)",
                "search_term": file_name,
                "run_uuid": run_uuid
            }
        
        # Return with flat paths array and primary_path highlighted
        # primary_path is the ONLY path agents should use
        return {
            "success": True,
            "run_uuid": run_uuid,
            "search_term": file_name,
            "file_count": len(file_paths),
            "paths": file_paths,  # Flat array of result paths (work/ paths excluded)
            "primary_path": file_paths[0]  # Use this path EXACTLY as shown
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to search for file",
            "detail": str(e)
        }


async def read_file_content_tool(
    run_uuid: str,
    file_path: str,
    preview_lines: Optional[int] = None
) -> Dict[str, Any]:
    """
    Read content from a specific file in a job's work directory.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to file from work directory
        preview_lines: Optional line limit for preview (default: all lines)
    
    Returns:
        Dict with file content and metadata
    """
    try:
        # Apply default preview limit if not specified
        if preview_lines is None:
            preview_lines = MAX_PREVIEW_LINES
        
        # Read file content
        content_response = read_file_content(run_uuid, file_path, preview_lines)
        
        return {
            "success": True,
            "run_uuid": content_response.run_uuid,
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
    run_uuid: str,
    file_path: str,
    max_rows: Optional[int] = 100
) -> Dict[str, Any]:
    """
    Parse a CSV or TSV file and return structured data.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to CSV/TSV file
        max_rows: Maximum rows to return (default: 100)
    
    Returns:
        Dict with parsed table data including columns and rows
    """
    try:
        # Parse CSV file
        parsed_data = parse_csv_file(run_uuid, file_path, max_rows)
        
        return {
            "success": True,
            "run_uuid": parsed_data.run_uuid,
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
    run_uuid: str,
    file_path: str,
    max_records: Optional[int] = 100
) -> Dict[str, Any]:
    """
    Parse a BED format file and return structured genomic records.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to BED file
        max_records: Maximum records to return (default: 100)
    
    Returns:
        Dict with parsed BED records
    """
    try:
        # Parse BED file
        parsed_data = parse_bed_file(run_uuid, file_path, max_records)
        
        return {
            "success": True,
            "run_uuid": parsed_data.run_uuid,
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


async def get_analysis_summary_tool(run_uuid: str) -> Dict[str, Any]:
    """
    Get comprehensive analysis summary for a completed job.
    Includes file categorization, key results, and parsed reports.
    
    Args:
        run_uuid: Job UUID
    
    Returns:
        Dict with success and formatted summary
    """
    try:
        # Generate summary
        summary = generate_analysis_summary(run_uuid)
        
        # Format as markdown tables
        output = f"Analysis Summary for UUID: {summary.run_uuid}\n\n"
        
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
            "summary": output
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


async def categorize_job_files_tool(run_uuid: str) -> Dict[str, Any]:
    """
    Categorize all files in a job's work directory by type.
    Groups files into txt, csv, bed, and other categories.
    
    Args:
        run_uuid: Job UUID
    
    Returns:
        Dict with categorized file lists
    """
    try:
        # Categorize files
        file_summary = categorize_files(run_uuid)
        
        return {
            "success": True,
            "run_uuid": run_uuid,
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
        "description": "List all files in a job's work directory with optional extension filtering. Returns compact format (path, name, size only) for large directories.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID to list files for"
                },
                "extensions": {
                    "type": "string",
                    "description": "Optional comma-separated list of extensions (e.g., '.txt,.csv,.bed') to filter files"
                },
                "compact": {
                    "type": "boolean",
                    "description": "If true (default), returns minimal info (path, name, size) for large listings to avoid truncation"
                }
            },
            "required": ["run_uuid"]
        }
    },
    "find_file": {
        "description": "Find a specific file by name (exact or partial match) and return its relative path. Useful when file listings are truncated or to get direct path without browsing.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID to search in"
                },
                "file_name": {
                    "type": "string",
                    "description": "Filename to search for (exact or partial match, case-insensitive)"
                }
            },
            "required": ["run_uuid", "file_name"]
        }
    },
    "read_file_content": {
        "description": "Read content from a specific file in a job's work directory",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID"
                },
                "file_path": {
                    "type": "string",
                    "description": "Relative path to file from work directory"
                },
                "preview_lines": {
                    "type": "integer",
                    "description": "Optional line limit for preview"
                }
            },
            "required": ["run_uuid", "file_path"]
        }
    },
    "parse_csv_file": {
        "description": "Parse a CSV or TSV file and return structured tabular data",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID"
                },
                "file_path": {
                    "type": "string",
                    "description": "Relative path to CSV/TSV file"
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Maximum rows to return (default: 100)"
                }
            },
            "required": ["run_uuid", "file_path"]
        }
    },
    "parse_bed_file": {
        "description": "Parse a BED format file and return structured genomic records",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID"
                },
                "file_path": {
                    "type": "string",
                    "description": "Relative path to BED file"
                },
                "max_records": {
                    "type": "integer",
                    "description": "Maximum records to return (default: 100)"
                }
            },
            "required": ["run_uuid", "file_path"]
        }
    },
    "get_analysis_summary": {
        "description": "Get comprehensive analysis summary for a completed job including file categorization and parsed reports",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID"
                }
            },
            "required": ["run_uuid"]
        }
    },
    "categorize_job_files": {
        "description": "Categorize all files in a job's work directory by type (txt, csv, bed, other)",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID"
                }
            },
            "required": ["run_uuid"]
        }
    }
}
