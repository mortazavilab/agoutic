"""
Server 2 - Consortium Router

Routes tool calls to external consortium MCP servers (ENCODE, GEO, SRA, etc.).
Each consortium is an external MCP server providing domain-specific data tools.

Internal services (Server 3/4) are owned by Server 1 directly.
The generic MCP HTTP client lives in common/mcp_client.py.
"""

from server2.config import CONSORTIUM_REGISTRY
from server2.result_formatter import format_results

__all__ = [
    "CONSORTIUM_REGISTRY",
    "format_results",
]
