"""Configuration for edgePython MCP server."""

import os
from pathlib import Path

# --- ROOT PATH CONFIGURATION ---
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))

# Server configuration
EDGEPYTHON_MCP_PORT = int(os.getenv("EDGEPYTHON_MCP_PORT", "8007"))
EDGEPYTHON_MCP_HOST = os.getenv("EDGEPYTHON_MCP_HOST", "0.0.0.0")
