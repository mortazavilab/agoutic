# Server2 (Data Router) — Quick Start

**Status:** ✅ Refactored  
**Purpose:** Unified MCP router for consortium data sources (ENCODE first) and internal services (Server3, Server4)

## Architecture

Server2 is now a **local Python package** (`server2/`) inside the AGOUTIC repo. It provides:

1. **Registry-driven routing** — `CONSORTIUM_REGISTRY` (external data sources) and `SERVICE_REGISTRY` (internal servers)
2. **Generic MCP HTTP client** — `MCPHttpClient` connects to any MCP server over HTTP/SSE
3. **Result formatting** — converts MCP results into markdown tables using registry metadata
4. **Unified tag format** — `[[DATA_CALL: consortium=encode, tool=..., ...]]` or `[[DATA_CALL: service=server4, tool=..., ...]]`

## Quick Start

### 1. Set ENCODELIB Location
```bash
export ENCODELIB_PATH="/path/to/ENCODELIB"
```

### 2. Start All Servers
```bash
./agoutic_servers.sh start
```

This launches:
| Service | Port | Description |
|---------|------|-------------|
| Server 3 REST | 8003 | Job management REST API |
| Server 3 MCP | 8002 | Job management MCP server |
| Server 4 REST | 8004 | Analysis REST API |
| Server 4 MCP | 8005 | Analysis MCP server |
| ENCODE MCP | 8006 | ENCODE Portal MCP server |
| Server 1 | 8000 | Main agent API |

### 3. Check Status
```bash
./agoutic_servers.sh status
```

### 4. Stop All
```bash
./agoutic_servers.sh stop
```

## Files

### New (`server2/` package — consortium routing)
| File | Purpose |
|------|---------|
| `server2/__init__.py` | Package exports |
| `server2/config.py` | `CONSORTIUM_REGISTRY`, consortium helper functions |
| `server2/result_formatter.py` | Registry-driven markdown table formatting |
| `server2/launch_encode.py` | Thin wrapper to serve ENCODELIB over HTTP |

### New (`common/` package — shared infrastructure)
| File | Purpose |
|------|---------|
| `common/__init__.py` | Package exports |
| `common/mcp_client.py` | `MCPHttpClient` — generic MCP-over-HTTP client |

### New (root)
| File | Purpose |
|------|---------|
| `agoutic_servers.sh` | Unified start/stop/status for all server processes |

### Modified
| File | Change |
|------|--------|
| `server1/app.py` | Unified `DATA_CALL` dispatch, Server3/4 proxy endpoints use MCP |
| `server1/agent_engine.py` | Dynamic `DATA_CALL` tag generation based on skill source |
| `server1/config.py` | Added `SERVICE_REGISTRY` (Server 3/4 URLs); removed stale vars |
| `server3/mcp_server.py` | Added `get_job_logs`, `get_job_debug` tools; expanded `submit_dogme_job` params |
| `server3/mcp_tools.py` | Implemented `get_job_logs`, `get_job_debug`; added advanced submit params |
| `server4/mcp_server.py` | Migrated from official MCP SDK (stdio) to FastMCP with HTTP support |
| `skills/ENCODE_Search.md` | `ENCODE_CALL` → `DATA_CALL: consortium=encode, tool=...` |
| `skills/ENCODE_LongRead.md` | `ENCODE_CALL` → `DATA_CALL: consortium=encode, tool=...` |
| `skills/Analyze_Job_Results.md` | `ANALYSIS_CALL` → `DATA_CALL: service=server4, tool=...` |

### Deleted
| File | Reason |
|------|--------|
| `server1/server2_mcp_client.py` | Replaced by `common/mcp_client.py` |
| `server1/mcp_client.py` | Replaced by `common/mcp_client.py` |
| `server1/server4_mcp_client.py` | Replaced by `common/mcp_client.py` |

## Example Usage

### Python — Call an MCP tool directly
```python
from common import MCPHttpClient

async def search_encode():
    client = MCPHttpClient(name="encode", base_url="http://localhost:8006")
    await client.connect()
    result = await client.call_tool("search_by_biosample", search_term="K562", organism="Homo sapiens")
    await client.disconnect()
    return result
```

### Agent Tags (in LLM output)
```
[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=abc-123]]
```

## Adding a New Consortium

1. Add entry to `CONSORTIUM_REGISTRY` in `server2/config.py`
2. Create a launcher script (like `server2/launch_encode.py`)
3. Add port to `agoutic_servers.sh`
4. Create skill files in `skills/`
5. The unified dispatch in `server1/app.py` handles routing automatically
