#!/usr/bin/env python3
"""Launcher for XgenePy MCP Server over HTTP."""

from __future__ import annotations

import argparse
import os

import uvicorn
from starlette.routing import Route

from common.logging_config import setup_logging, get_logger
from xgenepy_mcp.mcp_server import server, _tools_schema_endpoint

setup_logging("xgenepy-mcp")
logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="XgenePy MCP Server (HTTP mode)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("XGENEPY_MCP_PORT", "8008")),
        help="Port to bind to (default: 8008)",
    )
    args = parser.parse_args()

    logger.info("XgenePy MCP server starting", host=args.host, port=args.port)

    app = server.http_app() if hasattr(server, "http_app") else server
    app.routes.insert(0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"]))

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
