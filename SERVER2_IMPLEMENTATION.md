# SERVER2 Implementation: Data Router & MCP Unification

**Status:** ✅ Refactored  
**Date:** June 2025  
**Purpose:** Unified MCP-over-HTTP routing for all downstream data sources and services

## Overview

Server 2 has been refactored from a hardcoded ENCODE-only MCP client into a **registry-driven router** that connects Server 1 to any number of:

- **Consortium data sources** (ENCODE, and future additions) via `CONSORTIUM_REGISTRY`
- **Internal services** (Server 3 for job management, Server 4 for analysis) via `SERVICE_REGISTRY`

All communication uses **MCP over HTTP** (JSON-RPC 2.0 via FastMCP's `http_app`), replacing the previous mix of stdio subprocess spawning and raw REST calls.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AGOUTIC System                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────┐                                                  │
│  │   Server 1    │         MCP over HTTP (JSON-RPC 2.0)            │
│  │  (Agent API)  │─────────────────────────────────────┐           │
│  │   port 8000   │                                     │           │
│  └───┬───┬───┬───┘                                     │           │
│      │   │   │                                         │           │
│      │   │   │  ┌──────────────┐    ┌──────────────┐   │           │
│      │   │   └─►│  ENCODE MCP  │    │  Server 3    │◄──┘           │
│      │   │      │  port 8006   │    │  MCP: 8002   │               │
│      │   │      │ (ENCODELIB)  │    │  REST: 8003  │               │
│      │   │      └──────┬───────┘    └──────┬───────┘               │
│      │   │             │                   │                        │
│      │   │             ▼                   ▼                        │
│      │   │      ┌──────────────┐    ┌──────────────┐               │
│      │   └─────►│ ENCODE Portal│    │  Nextflow /  │               │
│      │          │     API      │    │   Dogme      │               │
│      │          └──────────────┘    └──────────────┘               │
│      │                                                              │
│      │          ┌──────────────┐                                    │
│      └─────────►│  Server 4    │                                    │
│                 │  MCP: 8005   │                                    │
│                 │  REST: 8004  │                                    │
│                 └──────┬───────┘                                    │
│                        │                                            │
│                        ▼                                            │
│                 ┌──────────────┐                                    │
│                 │ Job Results  │                                    │
│                 │  Analysis    │                                    │
│                 └──────────────┘                                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. `server2/config.py` — Registry Configuration

Two dictionaries drive all routing:

```python
CONSORTIUM_REGISTRY = {
    "encode": {
        "url": "http://localhost:8006",
        "display_name": "ENCODE Portal",
        "emoji": "🧬",
        "table_columns": [("Accession", "accession"), ("Assay", "assay_title"), ...],
        "count_field": "@graph",
        "skills": ["ENCODE_Search", "ENCODE_LongRead"],
        "fallback_patterns": [...]
    }
    # Add new consortia here (e.g., "4dn", "geo", ...)
}

SERVICE_REGISTRY = {
    "server3": {
        "url": "http://localhost:8002",
        "display_name": "Job Manager",
        # ...
    },
    "server4": {
        "url": "http://localhost:8005",
        "display_name": "Analysis Engine",
        # ...
    }
}
```

**Helper functions:**
- `get_consortium_url(key)` — returns URL for a consortium
- `get_consortium_entry(key)` — returns full consortium entry
- `get_all_fallback_patterns()` — collects regex fallback patterns from all consortia

Internal services (Server 3/4) are configured in `server1/config.py` (`SERVICE_REGISTRY`).

### 2. `common/mcp_client.py` — Generic MCP Client

`MCPHttpClient` replaces all three old clients (`server2_mcp_client.py`, `mcp_client.py`, `server4_mcp_client.py`):

```python
client = MCPHttpClient(name="encode", base_url="http://localhost:8006")
await client.connect()        # MCP initialize handshake
result = await client.call_tool("search_by_biosample", search_term="K562")
await client.disconnect()
```

**Key features:**
- HTTP POST to `/mcp/` endpoint (FastMCP convention)
- Handles MCP session IDs and request ID sequencing
- Auto-extracts results from `content[0].text` JSON-RPC wrapping
- Configurable timeout (default 30s)
- Works with any FastMCP `http_app` server

### 3. `server2/result_formatter.py` — Registry-Driven Formatting

Converts raw MCP results into markdown tables using `table_columns` and `count_field` from the registry:

```python
from server2 import format_results
from common import MCPHttpClient
markdown = format_results("encode", raw_result)
# → "🧬 **ENCODE Portal Results** (42 experiments)\n| Accession | Assay | ..."
```

### 4. `server2/launch_encode.py` — ENCODE MCP HTTP Server

Thin wrapper that:
1. Imports ENCODELIB's `mcp` FastMCP instance
2. Serves it via `uvicorn.run(mcp.http_app)` on port 8006

### 5. `agoutic_servers.sh` — Unified Process Manager

```bash
./agoutic_servers.sh start    # Launch all servers
./agoutic_servers.sh stop     # Stop all servers
./agoutic_servers.sh status   # Check running state
./agoutic_servers.sh restart  # Restart all
```

Manages PID files in `pids/`, logs in `logs/`.

## Unified Tag Format

### Old (removed)
```
[[ENCODE_CALL: search_by_biosample, search_term=K562]]
[[ANALYSIS_CALL: summary, run_uuid=abc-123]]
```

### New (unified)
```
[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=abc-123]]
```

**Format:** `[[DATA_CALL: (consortium|service)=<key>, tool=<tool_name>, param1=val1, ...]]`

The dispatch logic in `server1/app.py`:
1. Regex-matches all `DATA_CALL` tags in agent output
2. Groups calls by source key
3. Connects to each source's MCP server (URL from registry)
4. Dispatches tool calls via `MCPHttpClient`
5. Formats results via `format_results()`
6. Appends formatted results to the conversation

Legacy `ENCODE_CALL` and `ANALYSIS_CALL` tags are still recognized (backward compat) but mapped to the unified format.

## Port Assignments

| Service | Port | Protocol |
|---------|------|----------|
| Server 1 (Agent API) | 8000 | REST |
| Server 3 (Jobs MCP) | 8002 | MCP/HTTP |
| Server 3 (Jobs REST) | 8003 | REST |
| Server 4 (Analysis REST) | 8004 | REST |
| Server 4 (Analysis MCP) | 8005 | MCP/HTTP |
| ENCODE MCP | 8006 | MCP/HTTP |
| UI (Streamlit) | 8501 | HTTP |

## Migration Summary

### What Changed

| Before | After |
|--------|-------|
| Server1 spawns ENCODELIB via `subprocess` (stdio MCP) | ENCODELIB runs as persistent HTTP MCP server |
| Server1 talks to Server3 via `requests.post/get` (REST) | Server1 talks to Server3 via `MCPHttpClient` |
| Server1 talks to Server4 via `httpx` (REST) | Server1 talks to Server4 via `MCPHttpClient` |
| Three separate MCP clients (666 + 200 + 150 lines) | One generic `MCPHttpClient` (~120 lines) |
| Hardcoded `ENCODE_CALL` tags | Dynamic `DATA_CALL: consortium=<key>, tool=...` |
| Hardcoded `ANALYSIS_CALL` tags | Dynamic `DATA_CALL: service=<key>, tool=...` |
| System prompt hardcodes ENCODE examples | System prompt dynamically generates examples from registry |
| Server4 MCP used official `mcp` SDK (stdio only) | Server4 MCP uses FastMCP (HTTP + stdio) |

### Files Deleted
- `server1/server2_mcp_client.py` (666 lines) — replaced by `common/mcp_client.py`
- `server1/mcp_client.py` (~200 lines) — replaced by `common/mcp_client.py`
- `server1/server4_mcp_client.py` (~150 lines) — replaced by `common/mcp_client.py`

## Adding a New Consortium

To add a new data source (e.g., 4DN, GEO):

1. **Registry entry** — Add to `CONSORTIUM_REGISTRY` in `server2/config.py`:
   ```python
   "4dn": {
       "url": os.getenv("4DN_MCP_URL", "http://localhost:8007"),
       "display_name": "4D Nucleome",
       "emoji": "🔬",
       "table_columns": [...],
       "count_field": "total",
       "skills": ["4DN_Search"],
       "fallback_patterns": [...]
   }
   ```

2. **Launcher** — Create `server2/launch_4dn.py` (imports the external MCP server, serves via uvicorn)

3. **Startup** — Add port and process to `agoutic_servers.sh`

4. **Skill file** — Create `skills/4DN_Search.md` with `[[DATA_CALL: consortium=4dn, tool=..., ...]]` tags

5. **No code changes needed** in `server1/app.py` or `server1/agent_engine.py` — the dispatch is fully registry-driven.

## MCP Protocol Details

All MCP communication uses JSON-RPC 2.0 over HTTP POST to `/mcp/`:

```json
// Initialize
{"jsonrpc": "2.0", "id": 1, "method": "initialize",
 "params": {"protocolVersion": "2024-11-05",
             "clientInfo": {"name": "agoutic", "version": "1.0.0"},
             "capabilities": {}}}

// Tool call
{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
 "params": {"name": "search_by_biosample",
             "arguments": {"search_term": "K562"}}}
```

Response format:
```json
{"jsonrpc": "2.0", "id": 2,
 "result": {"content": [{"type": "text", "text": "{\"@graph\": [...]}"}]}}
```

The `MCPHttpClient._extract_result()` method unwraps this automatically.
