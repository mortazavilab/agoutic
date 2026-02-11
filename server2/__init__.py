"""
Server 2 - Consortium Router & MCP Client Layer

Routes tool calls to external consortium MCP servers (ENCODE, GEO, SRA, etc.)
and internal service MCP servers (Server 3 Job Execution, Server 4 Analysis).

All downstream communication uses MCP over HTTP/SSE to persistent server processes.
"""

from server2.config import CONSORTIUM_REGISTRY, SERVICE_REGISTRY, get_service_url
from server2.mcp_http_client import MCPHttpClient
from server2.result_formatter import format_results

__all__ = [
    "CONSORTIUM_REGISTRY",
    "SERVICE_REGISTRY",
    "get_service_url",
    "MCPHttpClient",
    "format_results",
]
