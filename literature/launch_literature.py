"""Run the literature FastMCP server over HTTP."""
from __future__ import annotations

import argparse

import uvicorn
from starlette.routing import Route

from literature.config import LITERATURE_MCP_PORT
from literature.mcp_server import _tools_schema_endpoint, server


def main() -> None:
    parser = argparse.ArgumentParser(description="AGOUTIC literature MCP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=LITERATURE_MCP_PORT)
    args = parser.parse_args()
    app = server.http_app() if hasattr(server, "http_app") else server
    app.routes.insert(0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"]))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
