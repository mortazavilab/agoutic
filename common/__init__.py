"""
Common shared utilities for AGOUTIC servers.

Provides infrastructure code used across multiple servers,
such as the MCP HTTP client for communicating with MCP servers,
unified structured logging, and shared database infrastructure.
"""

from common.mcp_client import MCPHttpClient
from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
from common.gene_annotation import GeneAnnotator
from common.database import (
    Base,
    DATABASE_URL,
    SessionLocal,
    AsyncSessionLocal,
    get_sync_engine,
    get_async_engine,
    init_db_sync,
    init_db_async,
    is_sqlite,
    reset_engines,
)

__all__ = [
    "MCPHttpClient",
    "setup_logging",
    "get_logger",
    "RequestLoggingMiddleware",
    "GeneAnnotator",
    "Base",
    "DATABASE_URL",
    "SessionLocal",
    "AsyncSessionLocal",
    "get_sync_engine",
    "get_async_engine",
    "init_db_sync",
    "init_db_async",
    "is_sqlite",
    "reset_engines",
]
