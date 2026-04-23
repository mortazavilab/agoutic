"""
edgePython MCP server wrapper.

Imports the upstream FastMCP instance from edgepython_server and exposes
it alongside a /tools/schema endpoint for Cortex discovery.

This module must be imported by launch_edgepython.py.
"""

from starlette.responses import JSONResponse

from edgepython_mcp.edgepython_server import mcp as server
from edgepython_mcp.tool_schemas import TOOL_SCHEMAS


async def _tools_schema_endpoint(request):
    """Return machine-readable tool schemas for all edgePython MCP tools."""
    return JSONResponse(TOOL_SCHEMAS)
