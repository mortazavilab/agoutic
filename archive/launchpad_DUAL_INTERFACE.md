# Launchpad: Dual Interface Architecture (REST + MCP)

**Status:** Week 3 - Enhanced with MCP Protocol Support

## Overview

Launchpad now exposes **both** REST API and MCP (Model Context Protocol) interfaces, allowing flexible integration patterns:

```
┌─────────────────────────────────────────────────────────────┐
│              Launchpad: Dual Interface                       │
├─────────────────────────┬─────────────────────────────────┤
│  REST API               │  MCP Protocol                   │
│  (HTTP/FastAPI)         │  (stdio/subprocess)             │
├─────────────────────────┼─────────────────────────────────┤
│ • 7 endpoints           │ • 7 tools                       │
│ • JSON request/response │ • LLM-friendly API              │
│ • Easy curl/Python      │ • Direct LLM invocation         │
│ • Stateless             │ • Persistent connection         │
│ • Good for scripts      │ • Good for agents               │
└─────────────────────────┴─────────────────────────────────┘
         ↓                           ↓
    Web UIs                    LLM Agents (Cortex)
    Python clients             Direct tool use
    Monitoring tools           Agentic workflows
```

## Architecture

### REST Interface (HTTP)

**Location:** `launchpad/app.py`

**7 Endpoints:**
- `POST /jobs/submit` - Submit job
- `GET /jobs` - List jobs
- `GET /jobs/{uuid}` - Get details
- `GET /jobs/{uuid}/status` - Quick status
- `GET /jobs/{uuid}/logs` - Stream logs
- `POST /jobs/{uuid}/cancel` - Cancel
- `GET /health` - Health check

**Best for:**
- Web dashboards
- Periodic polling
- External integrations
- Scripting (curl, Python requests)

### MCP Interface (stdio)

**Location:** `launchpad/mcp_tools.py` + `launchpad/mcp_server.py`

**7 Tools:**
- `submit_dogme_job()` - Submit analysis
- `check_nextflow_status()` - Check progress
- `get_dogme_report()` - Get results
- `submit_dogme_nextflow()` - Simplified submit
- `find_pod5_directory()` - Locate data
- `generate_dogme_config()` - Preview config
- `scaffold_dogme_dir()` - Setup workspace

**Best for:**
- LLM agents
- Direct tool invocation
- Agent reasoning
- Workflow orchestration

## Using the REST Interface

### Start Server

```bash
# REST API only
uvicorn launchpad.app:app --port 8001

# Or with both interfaces (see below)
uvicorn launchpad.app:app --port 8001 &
python -m launchpad.mcp_server &
```

### Submit Job

```bash
curl -X POST http://localhost:8001/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_001",
    "sample_name": "liver_dna",
    "mode": "DNA",
    "input_directory": "/data/samples/dna",
    "modifications": "5mCG_5hmCG,6mA"
  }'

# Response:
# {
#   "run_uuid": "a1b2c3d4-...",
#   "sample_name": "liver_dna",
#   "status": "RUNNING",
#   "work_directory": "..."
# }
```

### Check Status

```bash
curl http://localhost:8001/jobs/a1b2c3d4-/status

# Response:
# {
#   "run_uuid": "a1b2c3d4-...",
#   "status": "RUNNING",
#   "progress_percent": 45,
#   "message": "Pipeline executing..."
# }
```

## Using the MCP Interface

### Start MCP Server

```bash
# Start the MCP server (runs on stdio)
python -m launchpad.mcp_server

# Keep running in background, or integrate into Cortex process
```

### Call Tool from Cortex

**Python Integration:**

```python
from cortex.mcp_client import LaunchpadMCPClient
import asyncio

async def main():
    client = LaunchpadMCPClient()
    await client.connect()
    
    # Submit job
    result = await client.submit_dogme_job(
        project_id="proj_001",
        sample_name="liver_dna",
        mode="DNA",
        input_directory="/data/samples/dna",
        modifications="5mCG_5hmCG,6mA"
    )
    
    print(f"Job submitted: {result}")
    
    await client.disconnect()

asyncio.run(main())
```

### LLM Agent Usage

The LLM in Cortex can directly use these tools:

```
User: "Analyze my liver DNA sample"

Agent Plan:
  STEP 1: Find the input data
    [[call find_pod5_directory("liver_dna")]]
    → Found: /data/samples/liver_dna (45 pod5 files, 120GB)
  
  STEP 2: Validate setup
    [[call scaffold_dogme_dir("liver_dna", "/data/samples/liver_dna")]]
    → Success: Workspace ready
  
  STEP 3: Request approval
    [[APPROVAL_NEEDED]]
  
  [User approves]
  
  STEP 4: Submit job
    [[call submit_dogme_job(...)]]
    → Job submitted: run_uuid="a1b2c3d4-"
  
  STEP 5: Monitor progress
    [[call check_nextflow_status("a1b2c3d4-")]]
    → Status: RUNNING (45%)
    [[wait 30 seconds]]
    [[call check_nextflow_status("a1b2c3d4-")]]
    → Status: RUNNING (85%)
    [[wait 30 seconds]]
    [[call check_nextflow_status("a1b2c3d4-")]]
    → Status: COMPLETED (100%)
  
  STEP 6: Get results
    [[call get_dogme_report("a1b2c3d4-")]]
    → Results: {...}
```

## File Structure

### New MCP Files

```
launchpad/
├── mcp_tools.py          # MCP tool definitions
├── mcp_server.py         # MCP server wrapper
└── [existing REST files]

cortex/
├── mcp_client.py         # MCP client for Cortex
└── [existing Agent code]
```

### Tool Registry

`mcp_tools.py` defines all tools with:
- Description
- Input schema (JSON Schema)
- Async implementation

Each tool:
- Returns JSON-serializable results
- Includes error handling
- Provides clear success/failure feedback

## Integration Patterns

### Pattern 1: REST Only (Existing)

```
UI (Streamlit)
    ↓
Cortex (Agent)
    ↓
REST API (Launchpad)
    ↓
Nextflow
```

**When to use:** Simple polling, web dashboards

### Pattern 2: MCP Only (New)

```
LLM Agent (Cortex)
    ↓
MCP Protocol
    ↓
Launchpad Tools
    ↓
Nextflow
```

**When to use:** Full agent automation, direct tool use

### Pattern 3: Hybrid (Recommended)

```
LLM Agent (Cortex)
├─ MCP (for reasoning/planning)
│  └→ Launchpad Tools
│
UI (Streamlit)
├─ REST API (for dashboards)
│  └→ Launchpad REST
│
Both communicate with same
launchpad database & jobs
```

**When to use:** Maximum flexibility, both agent automation + UI visibility

## Comparison Table

| Feature | REST API | MCP Protocol |
|---------|----------|--------------|
| Transport | HTTP | stdio/subprocess |
| Client Type | HTTP client | MCP client |
| Best for | Web, scripts | LLM agents |
| Request Format | JSON | JSON-RPC |
| Stateless | Yes | No (connection) |
| Real-time Tools | Polling | Direct calls |
| Server Start | `uvicorn` | `python -m` |
| Easy Curl | ✅ Yes | ❌ No |
| Easy LLM | ❌ No | ✅ Yes |
| Same Backend | ✅ Yes | ✅ Yes |

## Implementation Details

### MCP Tools (`mcp_tools.py`)

```python
class LaunchpadMCPTools:
    async def submit_dogme_job(...) -> dict
    async def check_nextflow_status(...) -> dict
    async def get_dogme_report(...) -> dict
    async def submit_dogme_nextflow(...) -> str
    async def find_pod5_directory(...) -> dict
    async def generate_dogme_config(...) -> dict
    async def scaffold_dogme_dir(...) -> dict
```

All tools:
- Are async/await compatible
- Accept JSON-serializable arguments
- Return JSON-serializable results
- Include comprehensive docstrings

### MCP Server (`mcp_server.py`)

Stdio-based MCP server that:
- Lists available tools
- Routes tool calls to implementations
- Handles errors gracefully
- Maintains stdio communication

### MCP Client (`cortex/mcp_client.py`)

Connection to MCP server:
- Subprocess management
- JSON-RPC protocol handling
- Tool call routing
- Result parsing

### Integration Layer (`cortex/mcp_client.py`)

`CortexAgentWithMCP` class:
- Manages MCP connection
- Handles approval gates
- Polls job status
- Extracts parameters from user requests

## Running Both Interfaces

### Option 1: Separate Processes

```bash
# Terminal 1: REST API
uvicorn launchpad.app:app --port 8001

# Terminal 2: MCP Server (optional, can be on-demand)
python -m launchpad.mcp_server
```

### Option 2: Integrated in Cortex

```bash
# Cortex starts both:
# 1. REST server on port 8001
# 2. MCP subprocess on-demand

# See: cortex/agent_engine.py (future integration)
```

## Benefits of Dual Interface

✅ **Flexibility** - Choose REST or MCP based on use case
✅ **Backward Compatible** - Existing REST clients still work
✅ **LLM-Ready** - Direct tool access for agents
✅ **Shared Backend** - Same database, jobs, results
✅ **Easy Migration** - Add REST or MCP at any time
✅ **Testing** - Both interfaces covered by tests

## Future Enhancements

- [ ] Streaming results via Server-Sent Events (REST)
- [ ] Tool result caching in MCP
- [ ] Webhook callbacks for job completion
- [ ] Job dependency chains
- [ ] Real-time log streaming
- [ ] Multi-tool transactions

## Troubleshooting

### MCP Server Won't Start

```bash
# Check Python environment
python -c "import mcp; print(mcp.__version__)"

# Run with verbose output
python -m launchpad.mcp_Atlas>&1 | head -20

# Verify fastmcp is installed
pip list | grep mcp
```

### MCP Client Connection Fails

```bash
# Make sure MCP server is running
ps aux | grep mcp_server

# Check error output
python cortex/mcp_client.py
```

### Job Results Different Between REST and MCP

Both interfaces query the same database, so results should be identical. If not:
- Check database consistency
- Verify job UUID
- Check timestamp synchronization

## Documentation

- **REST API:** See [README.md](README.md)
- **MCP Tools:** See [mcp_tools.py](mcp_tools.py)
- **Integration:** See [cortex/mcp_client.py](../cortex/mcp_client.py)
- **Testing:** See [test_integration.py](test_integration.py)

## Summary

Launchpad now provides **both** interfaces:

1. **REST API** - For web, dashboards, simple clients
2. **MCP Protocol** - For LLM agents, direct tool use

Choose one or use both based on your needs!
