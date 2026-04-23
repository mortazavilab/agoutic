# Atlas (Data Router) — Quick Start

**Status:** ✅ Refactored  
**Purpose:** Unified MCP router for consortium data sources (ENCODE first) and internal services (Launchpad, Analyzer)

## Architecture

Atlas is now a **local Python package** (`atlas/`) inside the AGOUTIC repo. It provides:

1. **Registry-driven routing** — `CONSORTIUM_REGISTRY` (external data sources) and `SERVICE_REGISTRY` (internal servers)
2. **Generic MCP HTTP client** — `MCPHttpClient` connects to any MCP server over HTTP/SSE
3. **Result formatting** — converts MCP results into markdown tables using registry metadata
4. **Unified tag format** — `[[DATA_CALL: consortium=encode, tool=..., ...]]` or `[[DATA_CALL: service=analyzer, tool=..., ...]]`

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
| Launchpad REST | 8003 | Job management REST API |
| Launchpad MCP | 8002 | Job management MCP server |
| Analyzer REST | 8004 | Analysis REST API |
| Analyzer MCP | 8005 | Analysis MCP server |
| ENCODE MCP | 8006 | ENCODE Portal MCP server |
| Cortex | 8000 | Main agent API |

### 3. Check Status
```bash
./agoutic_servers.sh status
```

### 4. Stop All
```bash
./agoutic_servers.sh stop
```

## Files

### New (`atlas/` package — consortium routing)
| File | Purpose |
|------|---------|
| `atlas/__init__.py` | Package exports |
| `atlas/config.py` | `CONSORTIUM_REGISTRY`, consortium helper functions |
| `atlas/result_formatter.py` | Registry-driven markdown table formatting |
| `atlas/launch_encode.py` | Thin wrapper to serve ENCODELIB over HTTP |

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
| `cortex/app.py` | Unified `DATA_CALL` dispatch, Launchpad/4 proxy endpoints use MCP |
| `cortex/agent_engine.py` | Dynamic `DATA_CALL` tag generation based on skill source |
| `cortex/config.py` | Added `SERVICE_REGISTRY` (Launchpad/4 URLs); removed stale vars |
| `launchpad/mcp_server.py` | Added `get_job_logs`, `get_job_debug` tools; expanded `submit_dogme_job` params |
| `launchpad/mcp_tools.py` | Implemented `get_job_logs`, `get_job_debug`; added advanced submit params |
| `analyzer/mcp_server.py` | Migrated from official MCP SDK (stdio) to FastMCP with HTTP support |
| `skills/ENCODE_Search/SKILL.md` | `ENCODE_CALL` → `DATA_CALL: consortium=encode, tool=...` |
| `skills/ENCODE_LongRead/SKILL.md` | `ENCODE_CALL` → `DATA_CALL: consortium=encode, tool=...` |
| `skills/analyze_job_results/SKILL.md` | `ANALYSIS_CALL` → `DATA_CALL: service=analyzer, tool=...` |

### Deleted
| File | Reason |
|------|--------|
| `cortex/atlas_mcp_client.py` | Replaced by `common/mcp_client.py` |
| `cortex/mcp_client.py` | Replaced by `common/mcp_client.py` |
| `cortex/analyzer_mcp_client.py` | Replaced by `common/mcp_client.py` |

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
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=abc-123]]
```

## Adding a New Consortium

1. Add entry to `CONSORTIUM_REGISTRY` in `atlas/config.py`
2. Create a launcher script (like `atlas/launch_encode.py`)
3. Add port to `agoutic_servers.sh`
4. Create `skills/<skill_key>/SKILL.md` and `skills/<skill_key>/manifest.yaml`
5. Set `source.type: consortium` and `source.key: <consortium_key>` in that manifest
6. The unified dispatch in `cortex/app.py` handles routing automatically
