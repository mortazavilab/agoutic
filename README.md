# AGOUTIC: Automated Genomic Orchestrator

**Version:** 2.5  
**Status:** Active Prototype 

## 🧬 Overview

AGOUTIC is a general-purpose agent for analyzing and interpreting long-read genomic data (Nanopore/PacBio). It uses a **Dual Interface** architecture (REST + MCP) to allow both human users and AI agents to orchestrate complex bioinformatics pipelines.

The system is composed of:
- **Server 1**: Agent Engine - AI-powered orchestration and user interaction
- **Server 2**: ENCODE Integration - Public data retrieval via ENCODELIB
- **Server 3**: Execution Engine - Dogme/Nextflow pipeline management
- **Server 4**: Analysis Engine - Results analysis and QC reporting
- **UI**: Web interface for monitoring and control

## 🔒 Security & Multi-User Isolation

AGOUTIC enforces access control at every layer:

- **Authentication**: Google OAuth 2.0 with session cookies (`httponly`, `samesite=lax`, `secure` in production)
- **Authorization**: Role-based access (owner / editor / viewer) checked on every endpoint via `require_project_access()`. Admins bypass all project-level checks; public projects allow viewer access.
- **Job ownership**: Each job records the submitting `user_id`. `require_run_uuid_access()` verifies ownership before exposing debug info or analysis results.
- **File isolation**: User-jailed paths (`AGOUTIC_DATA/users/{user_id}/{project_id}/`) with input sanitization and jail-escape guards.
- **Server-side project IDs**: UUIDs generated server-side via `uuid4()` — clients never control the ID.
- **Project management**: Full dashboard for browsing projects, viewing stats/files/jobs, renaming, archiving, and permanent deletion with cascading cleanup.
- **Migration**: Run `python -m server1.migrate_hardening` to add new columns to existing databases.

## 🚀 Quick Start

### Installation

```bash
# Create environment
conda env create -f environment.yml
conda activate agoutic_core
```

### Run the System

```bash
# Terminal 1: Start Server 3 (Job Execution Engine)
uvicorn server3.app:app --host 0.0.0.0 --port 8001 --reload

# Terminal 2: Start Server 1 (Agent Engine)
uvicorn server1.app:app --host 0.0.0.0 --port 8000 --reload

# Terminal 3: Start UI
cd ui && python app.py

# Optional: Server 2 & 4 start automatically when needed
# But can be started manually for testing:
# cd /Users/eli/code/ENCODELIB && python encode_server.py  # Server 2
# cd server4 && uvicorn app:app --port 8002                # Server 4
```

### Verify Installation

```bash
# Check Server 1 health
curl http://localhost:8000/health

# Check Server 3 health
curl http://localhost:8001/health

# Test Server 2 connection
python server1/server2_mcp_client.py

# Expected: Connection success and K562 search results
```

## 📋 Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                       AGOUTIC System v2.5                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐                                               │
│  │  Web UI  │ (Streamlit)                                   │
│  └────┬─────┘                                               │
│       │ REST API                                            │
│       ↓                                                      │
│  ┌────────────────────────────────────────┐                │
│  │        Server 1 (Agent Engine)         │                │
│  │     AI Orchestration + Coordination    │                │
│  └────┬────────────┬────────────┬─────────┘                │
│       │            │            │                           │
│       │ MCP        │ REST       │ MCP                       │
│       ↓            ↓            ↓                           │
│  ┌────────┐  ┌──────────┐  ┌──────────┐                   │
│  │Server 2│  │ Server 3 │  │ Server 4 │                   │
│  │ENCODE  │  │ Nextflow │  │ Analysis │                   │
│  │ Portal │  │ Pipeline │  │  Engine  │                   │
│  └────┬───┘  └─────┬────┘  └─────┬────┘                   │
│       │            │              │                         │
│       ↓            ↓              ↓                         │
│    ENCODE      Dogme          Results                      │
│    Portal    Pipelines         Files                       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## 🔧 System Components

### Server 1: Agent Engine (Port 8000)
- **Role:** Central orchestrator with LLM reasoning
- **Tech:** FastAPI + OpenAI-compatible LLM
- **Features:**
  - Chat interface with skill-based workflows
  - Coordinates Server 2, 3, and 4
  - Block-based project timeline
  - Background job monitoring
  - User authentication
  - Role-based authorization gates on all endpoints
  - Server-side project CRUD (`POST/GET/PATCH /projects`)

### Server 2: ENCODELIB (Port 8080)
- **Role:** ENCODE Portal data retrieval
- **Tech:** fastmcp + ENCODE API
- **Features:**
  - Search experiments by biosample/organism/target
  - Download experiment files
  - Metadata caching
  - 15 MCP tools for data access
- **Docs:** [SERVER2_IMPLEMENTATION.md](SERVER2_IMPLEMENTATION.md)

### Server 3: Execution Engine (Port 8001)
- **Role:** Nextflow pipeline execution
- **Tech:** FastAPI + Nextflow + Dogme
- **Features:**
  - Submit Dogme DNA/RNA/cDNA pipelines
  - Real-time job monitoring
  - Log streaming
  - User-jailed working directories
- **Docs:** [server3/README.md](server3/README.md)

### Server 4: Analysis Engine (Port 8002)
- **Role:** Results analysis and QC
- **Tech:** fastmcp + Python analysis tools
- **Features:**
  - Parse pipeline outputs
  - Generate QC reports
  - Summarize results
  - File content analysis
- **Docs:** [server4/README.md](server4/README.md)

## 📁 Project Structure

```
agoutic/
├── README.md                     # This file
├── environment.yml               # Conda environment specification
├── CONFIGURATION.md              # Path configuration guide
├── ARCHITECTURE_UPDATE.md        # System architecture (all servers)
├── SERVER2_IMPLEMENTATION.md     # Server 2 integration guide
├── SERVER2_QUICKSTART.md         # Server 2 quick reference
│
├── server1/                      # Agent Engine
│   ├── README.md                # Server 1 documentation
│   ├── app.py                   # FastAPI application
│   ├── agent_engine.py          # AI agent orchestration
│   ├── dependencies.py          # Auth gates (require_project_access, require_run_uuid_access)
│   ├── user_jail.py             # Path traversal guards & file isolation
│   ├── auth.py                  # Google OAuth 2.0 + cookie hardening
│   ├── models.py                # Database models
│   ├── schemas.py               # Request/response schemas
│   ├── config.py                # Configuration
│   ├── db.py                    # Database connection
│   ├── init_db.py               # Full schema creation (fresh install)
│   ├── migrate_hardening.py     # Migration for hardening sprint columns
│   └── test_chat.py             # Tests
│
├── server3/                      # Execution Engine
│   ├── README.md                # Server 3 documentation
│   ├── app.py                   # FastAPI application
│   ├── nextflow_executor.py    # Nextflow wrapper
│   ├── mcp_tools.py            # MCP tool definitions
│   ├── mcp_server.py           # MCP server
│   ├── models.py               # Database models
│   ├── schemas.py              # Request/response schemas
│   ├── config.py               # Configuration
│   ├── db.py                   # Database connection
│   ├── demo_server3.py         # Demo script
│   ├── quickstart.sh           # Quick start setup
│   ├── test_server3.py         # Tests
│   ├── test_integration.py     # Integration tests
│   ├── DUAL_INTERFACE.md       # REST + MCP architecture
│   └── IMPLEMENTATION_SUMMARY.md # Implementation details
│
├── ui/                          # Web Interface
│   ├── README.md               # UI documentation
│   ├── app.py                  # Streamlit main app (chat, sidebar, auto-refresh)
│   └── pages/
│       ├── projects.py         # Projects dashboard (stats, files, bulk actions)
│       ├── results.py          # Job results analysis (auto-lists project jobs)
│       └── admin.py            # Admin user management
│
├── skills/                      # Workflow Definitions
│   ├── Dogme_DNA.md            # DNA pipeline definition
│   ├── Dogme_RNA.md            # RNA pipeline definition
│   ├── Dogme_cDNA.md           # cDNA pipeline definition
│   ├── ENCODE_LongRead.md      # ENCODE pipeline definition
│   ├── Local_Sample_Intake.md  # Sample intake workflow
│
└── data/                        # Data & Database (created at runtime)
    ├── database/
    │   └── agoutic_v24.sqlite
    ├── server3_work/            # Job execution directories
    ├── server3_logs/            # Server logs
    └── users/                   # Per-user jailed project dirs
```

## 🔑 Key Concepts

### Dual Interface Architecture

AGOUTIC provides two complementary interfaces:

1. **REST API** - For web clients, dashboards, and scripting
   - Traditional HTTP endpoints
   - Easy integration with existing tools
   - Language-agnostic clients

2. **MCP Protocol** - For LLM agents and AI orchestration
   - Model Context Protocol (MCP)
   - Tools exposed as structured capabilities
   - Seamless AI agent integration

See [server3/DUAL_INTERFACE.md](server3/DUAL_INTERFACE.md) for detailed architecture.

### Job Submission Workflow

```
User Request
    ↓
Server 1 (Agent)
  - Interprets intent
  - Plans workflow
  - (Optional) Requests approval
    ↓
Server 3 (Executor)
  - Receives job
  - Generates Nextflow config
  - Submits to cluster/local
  - Monitors progress
    ↓
Dogme Pipeline
  - Basecalling
  - Alignment
  - Quantification
  - Modification calling
    ↓
Results & Reports
  - Return to Agent
  - Display in UI
```

### Supported Analysis Modes

**DNA Mode**
- Genomic DNA and Fiber-seq analysis
- Includes modification calling (5mC, 6mA, etc.)
- Full basecalling → alignment → quantification pipeline

**RNA Mode**
- Direct RNA-seq analysis
- Native RNA modification calling (m6A, pseU, etc.)
- Splice-aware alignment

**cDNA Mode**
- Polyubiquitin cDNA and isoform analysis
- No modification calling (faster processing)
- Transcript quantification focus

## ⚙️ Configuration

AGOUTIC uses two root path variables with sensible defaults:

```bash
# Where source code lives (auto-detected)
export AGOUTIC_CODE=/path/to/agoutic

# Where data/database/jobs live (defaults to $AGOUTIC_CODE/data)
export AGOUTIC_DATA=/path/to/storage
```

**No environment variables needed!** Defaults work automatically. See [CONFIGURATION.md](CONFIGURATION.md) for detailed configuration options.

### Path Resolution

```
AGOUTIC_CODE/
├── server1/          # Agent engine
├── server3/          # Execution engine
├── ui/               # Web interface
└── skills/           # Workflow definitions

AGOUTIC_DATA/
├── database/         # SQLite database
├── server3_work/     # Job working directories
└── server3_logs/     # Server logs
```

## � Logging & Observability

AGOUTIC uses **structlog** for unified structured logging across all servers. Every log entry is a JSON object written to both per-server and unified log files.

### Log Files

Logs are written to `$AGOUTIC_DATA/logs/`:

```
$AGOUTIC_DATA/logs/
├── agoutic.jsonl          # Unified log (all servers)
├── server1.jsonl          # Server 1 only
├── server3-rest.jsonl     # Server 3 REST API
├── server3-mcp.jsonl      # Server 3 MCP
├── server4-rest.jsonl     # Server 4 REST API
├── server4-mcp.jsonl      # Server 4 MCP
├── encode-mcp.jsonl       # ENCODE MCP server
├── *.log                  # Raw stdout/stderr (safety net)
└── *.YYYYMMDD_HHMMSS.*   # Rotated previous logs
```

### Reading Logs

```bash
# Stream the unified log
tail -f $AGOUTIC_DATA/logs/agoutic.jsonl | jq .

# Filter by server
cat $AGOUTIC_DATA/logs/agoutic.jsonl | jq 'select(.server == "server1")'

# Filter by log level
cat $AGOUTIC_DATA/logs/agoutic.jsonl | jq 'select(.level == "error")'

# Filter requests by path
cat $AGOUTIC_DATA/logs/agoutic.jsonl | jq 'select(.path == "/chat")'

# Find slow requests (>1s)
cat $AGOUTIC_DATA/logs/agoutic.jsonl | jq 'select(.duration_ms > 1000)'

# Trace a request across servers by request_id
cat $AGOUTIC_DATA/logs/agoutic.jsonl | jq 'select(.request_id == "some-uuid")'
```

### Request Tracing

Every HTTP request receives a unique `X-Request-ID` header. This ID is:
- Bound to all log entries emitted during the request
- Returned in the response `X-Request-ID` header
- Available at `request.state.request_id` in route handlers

### Log Rotation

When servers are started or restarted via `agoutic_servers.sh`, existing log files are automatically renamed with a timestamp (e.g., `server1.20260213_143052.jsonl`). Empty log files are skipped.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AGOUTIC_LOG_FORMAT` | `json` | Set to `dev` for coloured human-readable console output |

## �🚀 Running the System

### Development Mode

```bash
# Terminal 1: Start Server 3
cd /path/to/agoutic
uvicorn server3.app:app --port 8001 --reload

# Terminal 2: Start Server 1
uvicorn server1.app:app --port 8000 --reload

# Terminal 3: Start UI (if using Streamlit)
cd ui && streamlit run app.py
```

### Using MCP Interface

```python
from server1.mcp_client import Server3MCPClient

# Connect to MCP server
client = Server3MCPClient()
await client.connect()

# Submit a job
job = await client.submit_dogme_job(
    project_id="proj_001",
    sample_name="liver_dna",
    mode="DNA",
    input_directory="/data/pod5"
)

# Monitor progress
status = await client.check_nextflow_status(job["run_uuid"])
```

### Using REST API

```bash
# Submit job
curl -X POST http://localhost:8001/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_001",
    "sample_name": "liver_dna",
    "mode": "DNA",
    "input_directory": "/data/pod5"
  }'

# Check status
curl http://localhost:8001/jobs/{run_uuid}/status

# Get results
curl http://localhost:8001/jobs/{run_uuid}
```

## 📚 Documentation

### Component Documentation
- [server1/README.md](server1/README.md) - Agent Engine documentation
- [server3/README.md](server3/README.md) - Execution Engine documentation  
- [ui/README.md](ui/README.md) - Web UI documentation

### Configuration & Setup
- [CONFIGURATION.md](CONFIGURATION.md) - Full configuration guide
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Path configuration quick start

### Architecture & Details
- [server3/DUAL_INTERFACE.md](server3/DUAL_INTERFACE.md) - REST + MCP architecture
- [server3/IMPLEMENTATION_SUMMARY.md](server3/IMPLEMENTATION_SUMMARY.md) - Implementation details

## 🧪 Testing

### Run All Tests

```bash
# Server 3 tests
pytest server3/test_server3.py -v

# Server 1 tests
pytest server1/test_chat.py -v

# Integration tests
pytest server3/test_integration.py -v
```

### Run Demo

```bash
# Interactive demo for Server 3
python server3/demo_server3.py
```

## 🐛 Troubleshooting

### Server Won't Start
- Check port availability: `lsof -i :8001` (Server 3) or `lsof -i :8000` (Server 1)
- Check database connectivity: `python -c "from server3.db import SessionLocal; SessionLocal()"`
- Check Python version: `python --version` (requires 3.12+)

### Job Stuck in RUNNING
- Check Nextflow process: `ps aux | grep nextflow`
- Check logs: `tail -f $AGOUTIC_DATA/server3_logs/*.log`
- Cancel job: `curl -X POST http://localhost:8001/jobs/{run_uuid}/cancel`

### Configuration Issues
- Verify configuration: `python -c "from server3.config import *; print(f'Code: {AGOUTIC_CODE}')"` 
- Check paths: `ls -la $AGOUTIC_DATA/server3_work`

## 📊 Performance

### Concurrent Jobs

Limit concurrent jobs to avoid resource exhaustion:

```bash
export MAX_CONCURRENT_JOBS=2  # Adjust based on server capacity
```

### Database Selection

- **Development**: SQLite (default, no setup required)
- **Production**: PostgreSQL or MySQL for concurrent access

```python
# server3/config.py
DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/agoutic"
```

## 📝 Workflow Skills

Pre-defined bioinformatics workflows are available in `skills/`:

- **Dogme_DNA.md** - Genomic DNA analysis workflow
- **Dogme_RNA.md** - Direct RNA-seq workflow
- **Dogme_cDNA.md** - cDNA isoform workflow
- **ENCODE_LongRead.md** - ENCODE consortium workflow
- **Local_Sample_Intake.md** - Sample intake and validation

## 🤝 Contributing

### Code Structure

- Use type hints throughout
- Write tests for new features
- Document configuration changes
- Update this README for major changes

### Testing Requirements

```bash
# Run full test suite
pytest --cov=server1 --cov=server3 --cov-report=html

# Check code quality
pylint server1 server3
```

## 📞 Support

- Check [CONFIGURATION.md](CONFIGURATION.md) for configuration issues
- Check [server3/README.md](server3/README.md) for execution engine issues
- Check [server1/README.md](server1/README.md) for agent engine issues

## 📦 Version Information

- **Release**: Security Hardening Sprint
- **Python**: 3.12+
- **FastAPI**: Latest (from environment.yml)
- **SQLAlchemy**: 2.0+
- **Nextflow**: >= 23.0
- **Status**: Active Development

## 🗓️ Development Timeline

- complete: Core infrastructure and basic API
- current: Complete (Current) - Dual interface, MCP integration
- next: Integration with Server 1 approval gates
- future: Web UI job monitoring dashboard
- future: Performance optimization & deployment
