"""
MCP Tools for Analyzer (Analysis Server).
Provides file discovery, reading, parsing, and analysis tools via MCP protocol.
"""

import json
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Dict, Any, Optional

from analyzer.analysis_engine import (
    discover_files,
    categorize_files,
    read_file_content,
    parse_csv_file,
    parse_bed_file,
    parse_xgenepy_outputs,
    generate_analysis_summary,
    get_job_work_dir,
    resolve_work_dir,
)
from analyzer.edgepython_adapter import call_edgepython_tool, relocate_edgepython_artifact
from analyzer.config import MAX_PREVIEW_LINES
from edgepython_mcp.tool_schemas import TOOL_SCHEMAS as EDGEPYTHON_TOOL_SCHEMAS
from analyzer.gene_tools import build_gene_cache, lookup_gene, translate_gene_ids
from analyzer.enrichment_engine import (
    run_go_enrichment, run_pathway_enrichment,
    get_enrichment_results, get_term_genes,
)

# Tools that have moved from edgePython to analyzer — exclude from proxy
_EXCLUDED_FROM_PROXY = {
    "translate_gene_ids", "lookup_gene", "build_gene_cache",
    "run_go_enrichment", "run_pathway_enrichment",
    "get_enrichment_results", "get_term_genes",
}


def _json_serial(obj: Any) -> str:
    """JSON serializer for objects not serializable by default (e.g. datetime)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _dumps(obj: Any) -> str:
    """json.dumps wrapper that handles datetime serialisation."""
    return json.dumps(obj, indent=2, default=_json_serial)


def _prefixed_edgepython_schema() -> Dict[str, Any]:
    """Return analyzer-owned proxy schemas for edgePython tools.

    The adapter adds a required conversation_id so analyzer can reuse upstream
    MCP sessions, and project_dir for artifact-producing tools.
    """
    proxy_schemas: Dict[str, Any] = {}
    for tool_name, schema in EDGEPYTHON_TOOL_SCHEMAS.items():
        if tool_name in _EXCLUDED_FROM_PROXY:
            continue
        prefixed_name = f"edgepython_{tool_name}"
        parameters = json.loads(json.dumps(schema.get("parameters", {})))
        properties = parameters.setdefault("properties", {})
        required = parameters.setdefault("required", [])
        properties.setdefault(
            "conversation_id",
            {
                "type": "string",
                "description": "Conversation/session identifier used by analyzer to reuse an upstream edgePython MCP session.",
            },
        )
        if "conversation_id" not in required:
            required.append("conversation_id")
        if tool_name in {"generate_plot", "save_results", "save_sc_results"}:
            properties.setdefault(
                "project_dir",
                {
                    "type": "string",
                    "description": "Absolute Agoutic project directory used to relocate generated DE artifacts into project-scoped storage.",
                },
            )
        proxy_schemas[prefixed_name] = {
            "description": f"Analyzer adapter wrapper for edgePython tool '{tool_name}'.",
            "parameters": parameters,
        }
    return proxy_schemas


EDGEPYTHON_PROXY_TOOL_SCHEMAS = _prefixed_edgepython_schema()


async def _edgepython_proxy_tool(
    edgepython_tool_name: str,
    *,
    conversation_id: str,
    project_dir: Optional[str] = None,
    **params: Any,
) -> Any:
    """Proxy a DE tool call to the upstream edgePython MCP service."""
    result = await call_edgepython_tool(
        edgepython_tool_name,
        conversation_id=conversation_id,
        **params,
    )

    if (
        isinstance(result, str)
        and project_dir
        and edgepython_tool_name in {"generate_plot", "save_results", "save_sc_results"}
    ):
        explicit_output = params.get("output_path")
        explicit_svg_output = params.get("svg_output_path")
        filename = Path(explicit_output).name if explicit_output else None
        filename_overrides = {}
        if explicit_output:
            filename_overrides[Path(explicit_output).suffix.lower()] = Path(explicit_output).name
        if explicit_svg_output:
            filename_overrides[Path(explicit_svg_output).suffix.lower()] = Path(explicit_svg_output).name
        relocated, _target_path = relocate_edgepython_artifact(
            result,
            project_dir=project_dir,
            filename=filename,
            filename_overrides=filename_overrides or None,
        )
        if relocated is not None:
            return relocated

    return result


EDGEPYTHON_PROXY_TOOL_REGISTRY = {
    f"edgepython_{tool_name}": partial(_edgepython_proxy_tool, tool_name)
    for tool_name in EDGEPYTHON_TOOL_SCHEMAS.keys()
    if tool_name not in _EXCLUDED_FROM_PROXY
}


# ==================== MCP Tool Definitions ====================

async def list_job_files(
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
    extensions: Optional[str] = None,
    compact: bool = True,
    max_depth: Optional[int] = None,
    name_pattern: Optional[str] = None,
    allow_missing: bool = False,
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
        if allow_missing:
            requested = str(Path(work_dir).expanduser()) if work_dir else ""
            return {
                "success": True,
                "work_dir": requested,
                "file_count": 0,
                "total_size_bytes": 0,
                "files": [],
                "missing_work_dir": True,
            }
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
        max_rows: Maximum rows to return (default: 100). Use null to load the full file.

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


async def parse_xgenepy_outputs_tool(
    output_dir: str,
    work_dir: Optional[str] = None,
    run_uuid: Optional[str] = None,
    max_rows: Optional[int] = 200,
) -> Dict[str, Any]:
    """Parse canonical XgenePy output artifacts and return structured content."""
    try:
        parsed_data = parse_xgenepy_outputs(
            run_uuid=run_uuid,
            output_dir=output_dir,
            max_rows=max_rows,
            work_dir_path=work_dir,
        )

        return {
            "success": True,
            "work_dir": work_dir or "",
            "output_dir": parsed_data.output_dir,
            "required_outputs_present": parsed_data.required_outputs_present,
            "missing_outputs": parsed_data.missing_outputs,
            "fit_summary": parsed_data.fit_summary,
            "model_metadata": parsed_data.model_metadata,
            "run_manifest": parsed_data.run_manifest,
            "assignments": parsed_data.assignments,
            "proportion_cis": parsed_data.proportion_cis,
            "plots": parsed_data.plots,
            "metadata": parsed_data.metadata,
        }

    except FileNotFoundError as e:
        return {
            "success": False,
            "error": "XgenePy output directory not found",
            "detail": str(e)
        }
    except ValueError as e:
        return {
            "success": False,
            "error": "Invalid XgenePy output path or parse error",
            "detail": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "error": "Failed to parse XgenePy outputs",
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
    "parse_xgenepy_outputs": parse_xgenepy_outputs_tool,
    "get_analysis_summary": get_analysis_summary_tool,
    "categorize_job_files": categorize_job_files_tool,
    # Gene annotation tools (moved from edgePython)
    "translate_gene_ids": translate_gene_ids,
    "build_gene_cache": build_gene_cache,
    "lookup_gene": lookup_gene,
    # Enrichment tools (moved from edgePython)
    "run_go_enrichment": run_go_enrichment,
    "run_pathway_enrichment": run_pathway_enrichment,
    "get_enrichment_results": get_enrichment_results,
    "get_term_genes": get_term_genes,
}

TOOL_REGISTRY.update(EDGEPYTHON_PROXY_TOOL_REGISTRY)


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
                    "description": "Maximum rows to return (default: 100). Use null to load the full file."
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
    "parse_xgenepy_outputs": {
        "description": "Parse canonical XgenePy output artifacts (JSON/TSV plus plots directory).",
        "parameters": {
            "type": "object",
            "properties": {
                "output_dir": {
                    "type": "string",
                    "description": "Relative path from work_dir to XgenePy run output directory"
                },
                "work_dir": {
                    "type": "string",
                    "description": "Absolute path to the workflow/project directory (preferred)"
                },
                "run_uuid": {
                    "type": "string",
                    "description": "Job UUID (legacy fallback — prefer work_dir)"
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Maximum rows to return for assignments/proportion previews (default: 200)"
                }
            },
            "required": ["output_dir"]
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
    },
    # Gene annotation tools (moved from edgePython)
    "translate_gene_ids": {
        "description": "Translate Ensembl gene or transcript IDs to gene symbols.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Ensembl gene IDs (e.g. ['ENSG00000141510'])"
                },
                "transcript_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of Ensembl transcript IDs (e.g. ['ENST00000378418'])"
                },
                "organism": {
                    "type": "string",
                    "description": "Optional organism label. Auto-detected from ID prefix if not provided."
                },
            },
            "required": []
        }
    },
    "build_gene_cache": {
        "description": "Build colocated gene/transcript cache TSVs for a specific GTF file.",
        "parameters": {
            "type": "object",
            "properties": {
                "gtf_path": {
                    "type": "string",
                    "description": "Path to a GTF or GTF.GZ file. Cache TSVs are written alongside it."
                },
                "organism": {
                    "type": "string",
                    "description": "Optional organism label for custom GTFs."
                }
            },
            "required": ["gtf_path"]
        }
    },
    "lookup_gene": {
        "description": "Look up genes or transcripts by symbol or Ensembl ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Gene symbols (e.g. ['TP53', 'BRCA1'])"
                },
                "gene_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ensembl gene IDs (e.g. ['ENSG00000141510'])"
                },
                "transcript_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ensembl transcript IDs (e.g. ['ENST00000378418'])"
                },
                "organism": {
                    "type": "string",
                    "description": "Optional organism label. Auto-detected if not provided."
                },
            },
            "required": []
        }
    },
    # Enrichment tools (moved from edgePython)
    "run_go_enrichment": {
        "description": "Run Gene Ontology enrichment analysis on a gene list.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_list": {
                    "type": "string",
                    "description": "Comma-separated gene IDs or symbols (e.g. 'TP53,BRCA1,MDM2')"
                },
                "species": {
                    "type": "string",
                    "description": "'auto', 'human', or 'mouse'. Default: auto-detect."
                },
                "sources": {
                    "type": "string",
                    "description": "GO sources, comma-separated. Default: 'GO:BP,GO:MF,GO:CC'."
                },
                "name": {
                    "type": "string",
                    "description": "Label for this enrichment result."
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Conversation/session identifier for state management."
                },
            },
            "required": ["gene_list"]
        }
    },
    "run_pathway_enrichment": {
        "description": "Run pathway enrichment analysis (KEGG or Reactome) on a gene list.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_list": {
                    "type": "string",
                    "description": "Comma-separated gene IDs or symbols"
                },
                "species": {
                    "type": "string",
                    "description": "'auto', 'human', or 'mouse'. Default: auto-detect."
                },
                "database": {
                    "type": "string",
                    "description": "'KEGG' or 'REAC' (Reactome). Default: 'KEGG'."
                },
                "name": {
                    "type": "string",
                    "description": "Label for this enrichment result."
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Conversation/session identifier for state management."
                },
            },
            "required": ["gene_list"]
        }
    },
    "get_enrichment_results": {
        "description": "Retrieve stored enrichment results with optional filtering.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Enrichment result name. Default: most recent."
                },
                "max_terms": {
                    "type": "integer",
                    "description": "Maximum terms to return. Default: 30."
                },
                "pvalue_threshold": {
                    "type": "number",
                    "description": "Filter terms by p-value. Default: 0.05."
                },
                "source_filter": {
                    "type": "string",
                    "description": "Filter by source (e.g. 'GO:BP', 'KEGG')."
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Conversation/session identifier for state management."
                },
            },
            "required": []
        }
    },
    "get_term_genes": {
        "description": "Show genes contributing to a specific GO term or pathway.",
        "parameters": {
            "type": "object",
            "properties": {
                "term_id": {
                    "type": "string",
                    "description": "Term identifier (e.g. 'GO:0006915' or 'KEGG:04110')"
                },
                "name": {
                    "type": "string",
                    "description": "Enrichment result name. Default: most recent."
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Conversation/session identifier for state management."
                },
            },
            "required": ["term_id"]
        }
    },
}

TOOL_SCHEMAS.update(EDGEPYTHON_PROXY_TOOL_SCHEMAS)
