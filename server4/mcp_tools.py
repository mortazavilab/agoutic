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
    generate_analysis_summary
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
    extensions: Optional[str] = None
) -> str:
    """
    List all files in a job's work directory.
    
    Args:
        run_uuid: Job UUID to list files for
        extensions: Optional comma-separated list of extensions (e.g., ".txt,.csv,.bed")
    
    Returns:
        JSON string with file listing including paths, sizes, and metadata
    """
    try:
        # Parse extensions if provided
        ext_list = None
        if extensions:
            ext_list = [ext.strip() for ext in extensions.split(',')]
        
        # Discover files
        file_listing = discover_files(run_uuid, ext_list)
        
        return _dumps({
            "success": True,
            "run_uuid": file_listing.run_uuid,
            "work_dir": file_listing.work_dir,
            "file_count": file_listing.file_count,
            "total_size_bytes": file_listing.total_size,
            "files": [f.dict() for f in file_listing.files]
        })
    
    except FileNotFoundError as e:
        return _dumps({
            "success": False,
            "error": "Job not found or work directory missing",
            "detail": str(e)
        })
    except Exception as e:
        return _dumps({
            "success": False,
            "error": "Failed to list files",
            "detail": str(e)
        })


async def read_file_content_tool(
    run_uuid: str,
    file_path: str,
    preview_lines: Optional[int] = None
) -> str:
    """
    Read content from a specific file in a job's work directory.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to file from work directory
        preview_lines: Optional line limit for preview (default: all lines)
    
    Returns:
        JSON string with file content and metadata
    """
    try:
        # Apply default preview limit if not specified
        if preview_lines is None:
            preview_lines = MAX_PREVIEW_LINES
        
        # Read file content
        content_response = read_file_content(run_uuid, file_path, preview_lines)
        
        return _dumps({
            "success": True,
            "run_uuid": content_response.run_uuid,
            "file_path": content_response.file_path,
            "content": content_response.content,
            "line_count": content_response.line_count,
            "is_truncated": content_response.is_truncated,
            "file_size_bytes": content_response.file_size
        })
    
    except FileNotFoundError as e:
        return _dumps({
            "success": False,
            "error": "File not found",
            "detail": str(e)
        })
    except ValueError as e:
        return _dumps({
            "success": False,
            "error": "Invalid file or path",
            "detail": str(e)
        })
    except Exception as e:
        return _dumps({
            "success": False,
            "error": "Failed to read file",
            "detail": str(e)
        })


async def parse_csv_file_tool(
    run_uuid: str,
    file_path: str,
    max_rows: Optional[int] = 100
) -> str:
    """
    Parse a CSV or TSV file and return structured data.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to CSV/TSV file
        max_rows: Maximum rows to return (default: 100)
    
    Returns:
        JSON string with parsed table data including columns and rows
    """
    try:
        # Parse CSV file
        parsed_data = parse_csv_file(run_uuid, file_path, max_rows)
        
        return _dumps({
            "success": True,
            "run_uuid": parsed_data.run_uuid,
            "file_path": parsed_data.file_path,
            "columns": parsed_data.columns,
            "row_count": parsed_data.row_count,
            "preview_rows": parsed_data.preview_rows,
            "data": parsed_data.data,
            "metadata": parsed_data.metadata
        })
    
    except FileNotFoundError as e:
        return _dumps({
            "success": False,
            "error": "File not found",
            "detail": str(e)
        })
    except ValueError as e:
        return _dumps({
            "success": False,
            "error": "Invalid CSV file or parsing error",
            "detail": str(e)
        })
    except Exception as e:
        return _dumps({
            "success": False,
            "error": "Failed to parse CSV file",
            "detail": str(e)
        })


async def parse_bed_file_tool(
    run_uuid: str,
    file_path: str,
    max_records: Optional[int] = 100
) -> str:
    """
    Parse a BED format file and return structured genomic records.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to BED file
        max_records: Maximum records to return (default: 100)
    
    Returns:
        JSON string with parsed BED records
    """
    try:
        # Parse BED file
        parsed_data = parse_bed_file(run_uuid, file_path, max_records)
        
        return _dumps({
            "success": True,
            "run_uuid": parsed_data.run_uuid,
            "file_path": parsed_data.file_path,
            "record_count": parsed_data.record_count,
            "preview_records": parsed_data.preview_records,
            "records": [r.dict() for r in parsed_data.records],
            "metadata": parsed_data.metadata
        })
    
    except FileNotFoundError as e:
        return _dumps({
            "success": False,
            "error": "File not found",
            "detail": str(e)
        })
    except ValueError as e:
        return _dumps({
            "success": False,
            "error": "Invalid BED file or parsing error",
            "detail": str(e)
        })
    except Exception as e:
        return _dumps({
            "success": False,
            "error": "Failed to parse BED file",
            "detail": str(e)
        })


async def get_analysis_summary_tool(run_uuid: str) -> str:
    """
    Get comprehensive analysis summary for a completed job.
    Includes file categorization, key results, and parsed reports.
    
    Args:
        run_uuid: Job UUID
    
    Returns:
        JSON string with complete analysis summary
    """
    try:
        # Generate summary
        summary = generate_analysis_summary(run_uuid)
        
        return _dumps({
            "success": True,
            "run_uuid": summary.run_uuid,
            "sample_name": summary.sample_name,
            "mode": summary.mode,
            "status": summary.status,
            "work_dir": summary.work_dir,
            "file_summary": {
                "txt_files": [f.dict() for f in summary.file_summary.txt_files],
                "csv_files": [f.dict() for f in summary.file_summary.csv_files],
                "bed_files": [f.dict() for f in summary.file_summary.bed_files],
                "other_files": [f.dict() for f in summary.file_summary.other_files]
            },
            "key_results": summary.key_results,
            "parsed_reports": summary.parsed_reports
        })
    
    except ValueError as e:
        return _dumps({
            "success": False,
            "error": "Job not found",
            "detail": str(e)
        })
    except Exception as e:
        return _dumps({
            "success": False,
            "error": "Failed to generate analysis summary",
            "detail": str(e)
        })


async def categorize_job_files_tool(run_uuid: str) -> str:
    """
    Categorize all files in a job's work directory by type.
    Groups files into txt, csv, bed, and other categories.
    
    Args:
        run_uuid: Job UUID
    
    Returns:
        JSON string with categorized file lists
    """
    try:
        # Categorize files
        file_summary = categorize_files(run_uuid)
        
        return _dumps({
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
        })
    
    except FileNotFoundError as e:
        return _dumps({
            "success": False,
            "error": "Job not found or work directory missing",
            "detail": str(e)
        })
    except Exception as e:
        return _dumps({
            "success": False,
            "error": "Failed to categorize files",
            "detail": str(e)
        })


# ==================== Tool Registry ====================

# Map of tool names to functions for MCP server registration
TOOL_REGISTRY = {
    "list_job_files": list_job_files,
    "read_file_content": read_file_content_tool,
    "parse_csv_file": parse_csv_file_tool,
    "parse_bed_file": parse_bed_file_tool,
    "get_analysis_summary": get_analysis_summary_tool,
    "categorize_job_files": categorize_job_files_tool
}


# Tool schemas for MCP server
TOOL_SCHEMAS = {
    "list_job_files": {
        "description": "List all files in a job's work directory with optional extension filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID to list files for"
                },
                "extensions": {
                    "type": "string",
                    "description": "Optional comma-separated list of extensions (e.g., '.txt,.csv,.bed')"
                }
            },
            "required": ["run_uuid"]
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
