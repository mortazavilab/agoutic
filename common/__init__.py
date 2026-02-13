"""
Common shared utilities for AGOUTIC servers.

Provides infrastructure code used across multiple servers,
such as the MCP HTTP client for communicating with MCP servers
and unified structured logging.
"""

from common.mcp_client import MCPHttpClient
from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware

__all__ = ["MCPHttpClient", "setup_logging", "get_logger", "RequestLoggingMiddleware"]
