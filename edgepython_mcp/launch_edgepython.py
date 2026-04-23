#!/usr/bin/env python3
"""
Launcher for edgePython MCP Server over HTTP.

The upstream edgepython_server.py is designed for stdio transport.
This wrapper imports the FastMCP instance and serves it over HTTP
so it can run as a persistent process.

Usage:
    python edgepython_mcp/launch_edgepython.py --port 8007
    python edgepython_mcp/launch_edgepython.py --host 0.0.0.0 --port 8007
"""

import argparse
import os
import sys
from pathlib import Path

from common.logging_config import setup_logging, get_logger

setup_logging("edgepython-mcp")
logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="edgePython MCP Server (HTTP mode)")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("EDGEPYTHON_MCP_PORT", "8007")),
        help="Port to bind to (default: 8007)",
    )
    args = parser.parse_args()

    try:
        from edgepython_mcp.mcp_server import server as mcp_instance
        from edgepython_mcp.mcp_server import _tools_schema_endpoint
        logger.info("Imported edgePython MCP server")
    except ImportError as e:
        logger.error("Failed to import edgepython_mcp.mcp_server", error=str(e))
        logger.error(
            "Make sure edgepython is installed: pip install 'edgepython[all]'"
        )
        sys.exit(1)

    import uvicorn
    from starlette.routing import Route

    logger.info(
        "edgePython MCP server starting",
        host=args.host,
        port=args.port,
    )

    # Serve the FastMCP instance over HTTP
    if hasattr(mcp_instance, "http_app"):
        app = mcp_instance.http_app()
    else:
        app = mcp_instance

    # Add schema endpoint for Cortex to fetch tool contracts
    app.routes.insert(
        0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"])
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
