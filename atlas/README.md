# ATLAS: Data Router & MCP Unification

**Status:** Active  
**Purpose:** Unified MCP-over-HTTP routing for all downstream data sources and services

## Overview

Atlas serves as a **registry-driven router** that connects Cortex to any number of:

- **Consortium data sources** (ENCODE, and future additions) via `CONSORTIUM_REGISTRY`
- **Internal services** (Launchpad for job management, Analyzer for analysis) via `SERVICE_REGISTRY`

All communication uses **MCP over HTTP** (JSON-RPC 2.0 via FastMCP's `http_app`), replacing the previous mix of stdio subprocess spawning and raw REST calls.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AGOUTIC System                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────┐                                                  │
│  │   Cortex    │         MCP over HTTP (JSON-RPC 2.0)            │
│  │  (Agent API)  │─────────────────────────────────────┐           │
│  │   port 8000   │                                     │           │
│  └───┬───┬───┬───┘                                     │           │
│      │   │   │                                         │           │
│      │   │   │  ┌──────────────┐    ┌──────────────┐   │           │
│      │   │   └─►│  ENCODE MCP  │    │  Launchpad    │◄──┘           │
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
│      └─────────►│  Analyzer    │                                    │
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

### 1. `atlas/config.py` — Registry Configuration

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
    "launchpad": {
        "url": "http://localhost:8002",
        "display_name": "Job Manager",
        # ...
    },
    "analyzer": {
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

Internal services (Launchpad/4) are configured in `cortex/config.py` (`SERVICE_REGISTRY`).

### 2. `common/mcp_client.py` — Generic MCP Client

`MCPHttpClient` replaces all three old clients (`atlas_mcp_client.py`, `mcp_client.py`, `analyzer_mcp_client.py`):

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

### 3. `atlas/result_formatter.py` — Registry-Driven Formatting

Converts raw MCP results into markdown tables using `table_columns` and `count_field` from the registry:

```python
from atlas import format_results
from common import MCPHttpClient
markdown = format_results("encode", raw_result)
# → "🧬 **ENCODE Portal Results** (42 experiments)\n| Accession | Assay | ..."
```

### 4. `atlas/tool_schemas.py` — Machine-Readable Tool Schemas

JSON Schema definitions for all 16 ENCODE MCP tools. Each schema specifies:
- Parameter types, descriptions, and required fields
- `pattern` constraints (e.g. `^ENCSR[0-9A-Z]{6}$` for accessions)
- `enum` values (e.g. assay types, organisms)
- Critical warnings (e.g. "NEVER pass ENCFF file accessions here")

Cortex fetches these at startup via the `/tools/schema` endpoint and injects a compact version into the LLM system prompt.

### 5. `atlas/launch_encode.py` — ENCODE MCP HTTP Server

Thin wrapper that:
1. Imports ENCODELIB's `mcp` FastMCP instance
2. Mounts the `/tools/schema` endpoint (via Starlette `Route`)
3. Serves it via `uvicorn.run(mcp.http_app)` on port 8006

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
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=abc-123]]
```

**Format:** `[[DATA_CALL: (consortium|service)=<key>, tool=<tool_name>, param1=val1, ...]]`

The dispatch logic in `cortex/app.py`:
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
| Cortex (Agent API) | 8000 | REST |
| Launchpad (Jobs MCP) | 8002 | MCP/HTTP |
| Launchpad (Jobs REST) | 8003 | REST |
| Analyzer (Analysis REST) | 8004 | REST |
| Analyzer (Analysis MCP) | 8005 | MCP/HTTP |
| ENCODE MCP | 8006 | MCP/HTTP |
| UI (Streamlit) | 8501 | HTTP |

## Migration Summary

### What Changed

| Before | After |
|--------|-------|
| Cortex spawns ENCODELIB via `subprocess` (stdio MCP) | ENCODELIB runs as persistent HTTP MCP server |
| Cortex talks to Launchpad via `requests.post/get` (REST) | Cortex talks to Launchpad via `MCPHttpClient` |
| Cortex talks to Analyzer via `httpx` (REST) | Cortex talks to Analyzer via `MCPHttpClient` |
| Three separate MCP clients (666 + 200 + 150 lines) | One generic `MCPHttpClient` (~120 lines) |
| Hardcoded `ENCODE_CALL` tags | Dynamic `DATA_CALL: consortium=<key>, tool=...` |
| Hardcoded `ANALYSIS_CALL` tags | Dynamic `DATA_CALL: service=<key>, tool=...` |
| System prompt hardcodes ENCODE examples | System prompt dynamically generates examples from registry |
| Analyzer MCP used official `mcp` SDK (stdio only) | Analyzer MCP uses FastMCP (HTTP + stdio) |

### Files Deleted
- `cortex/atlas_mcp_client.py` (666 lines) — replaced by `common/mcp_client.py`
- `cortex/mcp_client.py` (~200 lines) — replaced by `common/mcp_client.py`
- `cortex/analyzer_mcp_client.py` (~150 lines) — replaced by `common/mcp_client.py`

## Adding a New Consortium

To add a new data source (e.g., 4DN, GEO):

1. **Registry entry** — Add to `CONSORTIUM_REGISTRY` in `atlas/config.py`:
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

2. **Launcher** — Create `atlas/launch_4dn.py` (imports the external MCP server, serves via uvicorn)

3. **Startup** — Add port and process to `agoutic_servers.sh`

4. **Skill file** — Create `skills/4DN_Search.md` with `[[DATA_CALL: consortium=4dn, tool=..., ...]]` tags

5. **No code changes needed** in `cortex/app.py` or `cortex/agent_engine.py` — the dispatch is fully registry-driven.

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
