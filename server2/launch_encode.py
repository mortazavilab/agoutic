#!/usr/bin/env python3
"""
Launcher for ENCODE MCP Server over HTTP.

ENCODELIB's encode_server.py is designed for stdio transport.
This wrapper imports the FastMCP instance and serves it over HTTP
so it can run as a persistent process.

Usage:
    python server2/launch_encode.py --port 8006
    python server2/launch_encode.py --host 0.0.0.0 --port 8006
"""

import argparse
import os
import sys
from pathlib import Path


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
        print(f"❌ ENCODELIB not found at: {encodelib_path}", file=sys.stderr)
        print("   Set ENCODELIB_PATH environment variable to the correct path.", file=sys.stderr)
        sys.exit(1)

    # Add ENCODELIB to Python path so we can import encode_server
    sys.path.insert(0, str(encodelib_path))

    try:
        # Import the FastMCP instance from ENCODELIB's encode_server
        from encode_server import mcp  # noqa: E402
    except ImportError as e:
        print(f"❌ Failed to import encode_server from {encodelib_path}: {e}", file=sys.stderr)
        print("   Make sure ENCODELIB is installed and encode_server.py exists.", file=sys.stderr)
        sys.exit(1)

    import uvicorn

    print(f"🧬 ENCODE MCP server starting on HTTP at {args.host}:{args.port}", file=sys.stderr)
    print(f"   ENCODELIB path: {encodelib_path}", file=sys.stderr)
    print(f"📍 Access endpoint: http://{args.host}:{args.port}", file=sys.stderr)

    # Serve the FastMCP instance over HTTP
    app = mcp.http_app
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
