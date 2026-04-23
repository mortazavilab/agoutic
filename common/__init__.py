"""
Common shared utilities for AGOUTIC servers.

Provides infrastructure code used across multiple servers,
such as the MCP HTTP client for communicating with MCP servers,
unified structured logging, and shared database infrastructure.
"""

from __future__ import annotations

from common.mcp_client import MCPHttpClient
from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
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


def __getattr__(name: str):
    if name == "GeneAnnotator":
        from common.gene_annotation import GeneAnnotator

        return GeneAnnotator
    raise AttributeError(f"module 'common' has no attribute {name!r}")
