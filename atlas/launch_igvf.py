#!/usr/bin/env python3
"""
Launcher for IGVF MCP Server over HTTP.

Serves the IGVF FastMCP server as a persistent HTTP process
with a /tools/schema endpoint for Cortex discovery.

Usage:
    python -m atlas.launch_igvf --port 8009
    python -m atlas.launch_igvf --host 0.0.0.0 --port 8009
"""

import argparse
import os

from common.logging_config import setup_logging, get_logger

setup_logging("igvf-mcp")
logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="IGVF MCP Server (HTTP mode)")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("IGVF_MCP_PORT", "8009")),
        help="Port to bind to (default: 8009)",
    )
    args = parser.parse_args()

    from atlas.igvf_mcp_server import server as mcp_instance
    from atlas.igvf_mcp_server import _tools_schema_endpoint

    import uvicorn
    from starlette.routing import Route

    logger.info("IGVF MCP server starting", host=args.host, port=args.port)

    # Serve the FastMCP instance over HTTP
    if hasattr(mcp_instance, 'http_app'):
        app = mcp_instance.http_app()
    else:
        app = mcp_instance

    # Add schema endpoint for Cortex to fetch tool contracts
    app.routes.insert(0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"]))

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
