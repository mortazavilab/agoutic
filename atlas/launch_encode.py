#!/usr/bin/env python3
"""
Launcher for ENCODE MCP Server over HTTP.

ENCODELIB's encode_server.py is designed for stdio transport.
This wrapper imports the FastMCP instance and serves it over HTTP
so it can run as a persistent process.

Usage:
    python atlas/launch_encode.py --port 8006
    python atlas/launch_encode.py --host 0.0.0.0 --port 8006
"""

import argparse
import os
import sys
from pathlib import Path

from common.logging_config import setup_logging, get_logger

setup_logging("encode-mcp")
logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="ENCODE MCP Server (HTTP mode)")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("ENCODE_MCP_PORT", "8006")),
        help="Port to bind to (default: 8006)",
    )
    args = parser.parse_args()

    # Resolve ENCODELIB path
    agoutic_code = Path(os.getenv(
        "AGOUTIC_CODE",
        Path(__file__).resolve().parent.parent,
    ))
    encodelib_path = Path(os.getenv(
        "ENCODELIB_PATH",
        str(agoutic_code / "ENCODELIB"),
    ))

    if not encodelib_path.exists():
        logger.error("ENCODELIB not found", path=str(encodelib_path))
        logger.error("Set ENCODELIB_PATH environment variable to the correct path.")
        sys.exit(1)

    # Add ENCODELIB to Python path so encode_server can be imported.
    # This must happen before importing atlas.mcp_server, which registers
    # additional tools (e.g. search_by_assay) onto ENCODELIB's FastMCP instance.
    sys.path.insert(0, str(encodelib_path))

    try:
        from atlas.mcp_server import server as mcp_instance  # noqa: E402
        from atlas.mcp_server import _tools_schema_endpoint  # noqa: E402
        logger.info("Imported extended MCP server from atlas.mcp_server")
    except ImportError as e:
        logger.error("Failed to import atlas.mcp_server", error=str(e))
        logger.error(
            "Make sure ENCODELIB is installed and atlas/mcp_server.py is present."
        )
        sys.exit(1)

    import uvicorn
    from starlette.routing import Route

    logger.info("ENCODE MCP server starting", host=args.host, port=args.port, encodelib_path=str(encodelib_path))

    # Serve the FastMCP instance over HTTP
    # FastMCP 2.x: http_app is a method that returns a Starlette app
    if hasattr(mcp_instance, 'http_app'):
        app = mcp_instance.http_app()
    else:
        # Fallback: assume it's already an ASGI app
        app = mcp_instance
    
    # Add schema endpoint for Cortex to fetch tool contracts
    app.routes.insert(0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"]))
    
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
