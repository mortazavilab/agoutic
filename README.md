# AGOUTIC: Automated Genomic Orchestrator

**Version:** 2.3  
**Status:** Active Prototype 

## 🧬 Overview

AGOUTIC is a general-purpose agent for analyzing and interpreting long-read genomic data (Nanopore/PacBio). It uses a **Dual Interface** architecture (REST + MCP) to allow both human users and AI agents to orchestrate complex bioinformatics pipelines.

The system is composed of:
- **Server 1**: Agent Engine - AI-powered orchestration and user interaction
- **Server 3**: Execution Engine - Dogme/Nextflow pipeline management
- **UI**: Web interface for monitoring and control

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
```

### Verify Installation

```bash
# Check Server 3 health
curl http://localhost:8001/health

# Expected: {"status":"ok","version":"0.3.0",...}
```

## 📋 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        AGOUTIC System                       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────┐              ┌──────────────────┐     │
│  │   Web UI        │              │   AI Agent       │     │
│  │  (Dashboard)    │◄────REST────►│   (Server 1)     │     │
│  └─────────────────┘              └────────┬─────────┘     │
│                                            │                │
│                                    REST API│ + MCP          │
│                                            ↓                │
│                                  ┌──────────────────┐       │
│                                  │   Job Executor   │       │
│                                  │   (Server 3)     │       │
│                                  └────────┬─────────┘       │
│                                           │                 │
│                            Nextflow API   │                 │
│                                           ↓                 │
│                           ┌──────────────────────┐          │
│                           │  Dogme Pipelines     │          │
│                           │  - Basecalling       │          │
│                           │  - Alignment         │          │
│                           │  - Quantification    │          │
│                           │  - Modification Call │          │
│                           └──────────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

## 📁 Project Structure

```
agoutic/
├── README.md                    # This file
├── environment.yml              # Conda environment specification
├── CONFIGURATION.md             # Path configuration guide
├── QUICK_REFERENCE.md           # Quick reference for configs
│
├── server1/                     # Agent Engine
│   ├── README.md               # Server 1 documentation
│   ├── app.py                  # FastAPI application
│   ├── agent_engine.py         # AI agent orchestration
│   ├── mcp_client.py           # MCP client for Server 3
│   ├── models.py               # Database models
│   ├── schemas.py              # Request/response schemas
│   ├── config.py               # Configuration
│   ├── db.py                   # Database connection
│   └── test_chat.py            # Tests
│
├── server3/                     # Execution Engine
│   ├── README.md               # Server 3 documentation
│   ├── app.py                  # FastAPI application
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
│   ├── app.py                  # Streamlit/Flask app
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
    │   └── agoutic_v23.sqlite
    ├── server3_work/            # Job execution directories
    └── server3_logs/            # Server logs
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

## 🚀 Running the System

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

- **Release**: Week 3 Prototype
- **Python**: 3.12+
- **FastAPI**: Latest (from environment.yml)
- **SQLAlchemy**: 2.0+
- **Nextflow**: >= 23.0
- **Status**: Active Development

## 🗓️ Development Timeline

- **Week 1-2**: Core infrastructure and basic API
- **Week 3**: Complete (Current) - Dual interface, MCP integration
- **Week 4**: Integration with Server 1 approval gates
- **Week 5**: Web UI job monitoring dashboard
- **Week 6**: Performance optimization & deployment
