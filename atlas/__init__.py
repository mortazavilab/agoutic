"""
Atlas - Consortium Router

Routes tool calls to external consortium MCP servers (ENCODE, GEO, SRA, etc.).
Each consortium is an external MCP server providing domain-specific data tools.

Internal services (Launchpad/4) are owned by Cortex directly.
The generic MCP HTTP client lives in common/mcp_client.py.
"""

from atlas.config import CONSORTIUM_REGISTRY
from atlas.result_formatter import format_results

__all__ = [
    "CONSORTIUM_REGISTRY",
    "format_results",
]
