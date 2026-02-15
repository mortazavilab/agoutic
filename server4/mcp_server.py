"""
MCP Server for Server4 (Analysis Server).
Exposes analysis tools via Model Context Protocol.
Supports both stdio and HTTP transports.
"""

import asyncio
import json
import sys
from typing import Any, Dict

from fastmcp import FastMCP

from common.logging_config import setup_logging, get_logger
from server4.mcp_tools import TOOL_REGISTRY
from server4.config import SERVER4_MCP_PORT

# Configure logging
setup_logging("server4-mcp")
logger = get_logger(__name__)

# Create FastMCP server instance
mcp = FastMCP("AGOUTIC-Server4-Analysis", version="0.4.0")


# ==================== Register tools from TOOL_REGISTRY ====================

@mcp.tool()
async def list_job_files(run_uuid: str, extensions: str | None = None) -> Dict[str, Any]:
    """List all files in a job's work directory with optional extension filtering."""
    return await TOOL_REGISTRY["list_job_files"](run_uuid=run_uuid, extensions=extensions)

@mcp.tool()
async def find_file(run_uuid: str, file_name: str) -> Dict[str, Any]:
    """Find a specific file by name (exact or partial match) and return its relative path."""
    return await TOOL_REGISTRY["find_file"](run_uuid=run_uuid, file_name=file_name)

@mcp.tool()
async def read_file_content(run_uuid: str, file_path: str, preview_lines: int | None = None) -> Dict[str, Any]:
    """Read content from a specific file in a job's work directory."""
    return await TOOL_REGISTRY["read_file_content"](run_uuid=run_uuid, file_path=file_path, preview_lines=preview_lines)

@mcp.tool()
async def parse_csv_file(run_uuid: str, file_path: str, max_rows: int | None = 100) -> Dict[str, Any]:
    """Parse a CSV or TSV file and return structured tabular data."""
    return await TOOL_REGISTRY["parse_csv_file"](run_uuid=run_uuid, file_path=file_path, max_rows=max_rows)

@mcp.tool()
async def parse_bed_file(run_uuid: str, file_path: str, max_records: int | None = 100) -> Dict[str, Any]:
    """Parse a BED format file and return structured genomic records."""
    return await TOOL_REGISTRY["parse_bed_file"](run_uuid=run_uuid, file_path=file_path, max_records=max_records)

@mcp.tool()
async def get_analysis_summary(run_uuid: str) -> Dict[str, Any]:
    """Get comprehensive analysis summary for a completed job including file categorization and parsed reports."""
    return await TOOL_REGISTRY["get_analysis_summary"](run_uuid=run_uuid)

@mcp.tool()
async def categorize_job_files(run_uuid: str) -> Dict[str, Any]:
    """Categorize all files in a job's work directory by type (txt, csv, bed, other)."""
    return await TOOL_REGISTRY["categorize_job_files"](run_uuid=run_uuid)


# ==================== Server Entry Points ====================

async def main_stdio():
    """Run the MCP server in stdio mode (for subprocess spawning)."""
    logger.info("Starting Server4 MCP Server (stdio mode)")
    logger.info(f"Registered tools: {list(TOOL_REGISTRY.keys())}")
    await mcp.run_async(transport="stdio")


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="AGOUTIC Server 4 MCP Server")
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
        default=SERVER4_MCP_PORT,
        help=f"Port to bind to (default: {SERVER4_MCP_PORT})",
    )

    args = parser.parse_args()

    if args.mode == "stdio":
        asyncio.run(main_stdio())
    else:
        logger.info("Server 4 MCP server starting", host=args.host, port=args.port,
                    tools=list(TOOL_REGISTRY.keys()))

        app = mcp.http_app
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
