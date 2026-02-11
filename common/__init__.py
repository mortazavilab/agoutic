"""
Common shared utilities for AGOUTIC servers.

Provides infrastructure code used across multiple servers,
such as the MCP HTTP client for communicating with MCP servers.
"""

from common.mcp_client import MCPHttpClient

__all__ = ["MCPHttpClient"]
