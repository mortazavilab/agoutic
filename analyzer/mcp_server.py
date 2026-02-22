"""
MCP Server for Analyzer (Analysis Server).
Exposes analysis tools via Model Context Protocol.
Supports both stdio and HTTP transports.
"""

import asyncio
import json
import sys
from typing import Any, Dict

from fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

from common.logging_config import setup_logging, get_logger
from analyzer.mcp_tools import TOOL_REGISTRY, TOOL_SCHEMAS
from analyzer.config import ANALYZER_MCP_PORT

# Configure logging
setup_logging("analyzer-mcp")
logger = get_logger(__name__)

# Create FastMCP server instance
mcp = FastMCP("AGOUTIC-Analyzer", version="0.4.0")


# --- Schema endpoint (fetched by Cortex at startup) ---
async def _tools_schema_endpoint(request):
    """Return machine-readable tool schemas for all Analyzer MCP tools."""
    return JSONResponse(TOOL_SCHEMAS)


# ==================== Register tools from TOOL_REGISTRY ====================

@mcp.tool()
async def list_job_files(work_dir: str | None = None, run_uuid: str | None = None, extensions: str | None = None, max_depth: int | None = None, name_pattern: str | None = None) -> Dict[str, Any]:
    """List all files in a workflow directory with optional extension filtering."""
    return await TOOL_REGISTRY["list_job_files"](work_dir=work_dir, run_uuid=run_uuid, extensions=extensions, max_depth=max_depth, name_pattern=name_pattern)

@mcp.tool()
async def find_file(file_name: str, work_dir: str | None = None, run_uuid: str | None = None) -> Dict[str, Any]:
    """Find a specific file by name (exact or partial match) and return its relative path."""
    return await TOOL_REGISTRY["find_file"](file_name=file_name, work_dir=work_dir, run_uuid=run_uuid)

@mcp.tool()
async def read_file_content(file_path: str, work_dir: str | None = None, run_uuid: str | None = None, preview_lines: int | None = None) -> Dict[str, Any]:
    """Read content from a specific file in a workflow directory."""
    return await TOOL_REGISTRY["read_file_content"](file_path=file_path, work_dir=work_dir, run_uuid=run_uuid, preview_lines=preview_lines)

@mcp.tool()
async def parse_csv_file(file_path: str, work_dir: str | None = None, run_uuid: str | None = None, max_rows: int | None = 100) -> Dict[str, Any]:
    """Parse a CSV or TSV file and return structured tabular data."""
    return await TOOL_REGISTRY["parse_csv_file"](file_path=file_path, work_dir=work_dir, run_uuid=run_uuid, max_rows=max_rows)

@mcp.tool()
async def parse_bed_file(file_path: str, work_dir: str | None = None, run_uuid: str | None = None, max_records: int | None = 100) -> Dict[str, Any]:
    """Parse a BED format file and return structured genomic records."""
    return await TOOL_REGISTRY["parse_bed_file"](file_path=file_path, work_dir=work_dir, run_uuid=run_uuid, max_records=max_records)

@mcp.tool()
async def get_analysis_summary(run_uuid: str | None = None, work_dir: str | None = None) -> Dict[str, Any]:
    """Get comprehensive analysis summary for a completed job including file categorization and parsed reports."""
    return await TOOL_REGISTRY["get_analysis_summary"](run_uuid=run_uuid, work_dir=work_dir)

@mcp.tool()
async def categorize_job_files(work_dir: str | None = None, run_uuid: str | None = None) -> Dict[str, Any]:
    """Categorize all files in a workflow directory by type (txt, csv, bed, other)."""
    return await TOOL_REGISTRY["categorize_job_files"](work_dir=work_dir, run_uuid=run_uuid)


# ==================== Server Entry Points ====================

async def main_stdio():
    """Run the MCP server in stdio mode (for subprocess spawning)."""
    logger.info("Starting Analyzer MCP Server (stdio mode)")
    logger.info(f"Registered tools: {list(TOOL_REGISTRY.keys())}")
    await mcp.run_async(transport="stdio")


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="AGOUTIC Analyzer MCP Server")
    parser.add_argument(
        "--mode",
        choices=["http", "stdio"],
        default="http",
        help="Transport mode: 'http' (default) or 'stdio'",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0 — all interfaces)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=ANALYZER_MCP_PORT,
        help=f"Port to bind to (default: {ANALYZER_MCP_PORT})",
    )

    args = parser.parse_args()

    if args.mode == "stdio":
        asyncio.run(main_stdio())
    else:
        logger.info("Analyzer MCP server starting", host=args.host, port=args.port,
                    tools=list(TOOL_REGISTRY.keys()))

        app = mcp.http_app
        app.routes.insert(0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"]))
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
