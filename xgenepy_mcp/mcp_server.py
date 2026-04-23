"""MCP server for local XgenePy analysis execution."""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP
from starlette.responses import JSONResponse

from common.logging_config import setup_logging, get_logger
from xgenepy_mcp.service import run_xgenepy_analysis as run_xgenepy_analysis_service
from xgenepy_mcp.tool_schemas import TOOL_SCHEMAS

setup_logging("xgenepy-mcp")
logger = get_logger(__name__)

server = FastMCP("AGOUTIC-XgenePy", version="0.1.0")


async def _tools_schema_endpoint(request):
    """Return machine-readable tool schemas for all XgenePy MCP tools."""
    return JSONResponse(TOOL_SCHEMAS)


@server.tool()
async def run_xgenepy_analysis(
    project_dir: str,
    counts_path: str,
    metadata_path: str,
    output_subdir: str | None = None,
    trans_model: str = "log_additive",
    fields_to_test: list[str] | None = None,
    combo: str | None = None,
    alpha: float = 0.05,
    execution_mode: str = "local",
    project_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run XgenePy locally and produce canonical output artifacts."""
    return run_xgenepy_analysis_service(
        project_dir=project_dir,
        counts_path=counts_path,
        metadata_path=metadata_path,
        output_subdir=output_subdir,
        trans_model=trans_model,
        fields_to_test=fields_to_test,
        combo=combo,
        alpha=alpha,
        execution_mode=execution_mode,
        project_id=project_id,
        run_id=run_id,
    )


async def main_stdio() -> None:
    """Run server in stdio mode."""
    logger.info("Starting XgenePy MCP server (stdio mode)")
    await server.run_async(transport="stdio")


if __name__ == "__main__":
    asyncio.run(main_stdio())
