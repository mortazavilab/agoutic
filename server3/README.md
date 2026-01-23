# AGOUTIC Server 3: Dogme/Nextflow Job Execution Engine

**Version:** 0.3.0  
**Status:** Active Development

## Overview

Server 3 is the **execution engine** for the AGOUTIC bioinformatics platform. It receives job requests from Server 1 (the Agent Engine) and manages the execution of Dogme/Nextflow pipelines for genomic analysis. It provides a dual interface for both REST clients and LLM agents via the Model Context Protocol (MCP).

### Key Responsibilities

- 🚀 **Job Submission**: Receive and queue Dogme pipeline jobs
- 📊 **Job Monitoring**: Track job progress in real-time
- 📝 **Logging**: Capture detailed execution logs
- 🔍 **Status Tracking**: Maintain persistent job state in database
- 📤 **Result Retrieval**: Return analysis outputs and reports
- 🤝 **Dual Interface**: REST API + MCP Protocol for LLM agents

## Architecture

### System Integration

```
Server 1 (Agent)          Server 3 (Executor)        Nextflow/Dogme
┌──────────────┐         ┌──────────────┐           ┌──────────────┐
│ Agent Engine │────────→│  FastAPI App │──────────→│  Nextflow    │
│              │ REST    │              │ subprocess│  Pipeline    │
└──────────────┘ or MCP  │ Job Tracker  │           │              │
                         │              │           │  - Basecall  │
                         │ Background   │←──────────│  - Align     │
                         │ Monitoring   │ logs/     │  - Quantify  │
                         │              │ status    │  - Modify    │
                         └──────────────┘           └──────────────┘
                              ↓
                         ┌──────────────┐
                         │   Database   │
                         │  (Jobs, Logs)│
                         └──────────────┘
```

### Dual Interface Architecture

Server 3 provides **both** interfaces:
- 🌐 **REST API** - HTTP endpoints for web clients, dashboards, and scripting
- 🛠️ **MCP Protocol** - Model Context Protocol tools for LLM agents

See [DUAL_INTERFACE.md](DUAL_INTERFACE.md) for detailed architecture.

## Installation & Setup

### Prerequisites

- Python 3.12+
- SQLAlchemy 2.0+
- FastAPI & Uvicorn
- Nextflow >= 23.0
- Dogme pipeline repository
- SQLite or PostgreSQL (for production)

### Environment Setup

```bash
# 1. Create AGOUTIC work directories

The default location is `agoutic_code/data/` but you can override with environment variables:

# Option A: Use default (agoutic_code/data/)
# Directories will be created automatically

# Option B: Use custom storage location
export AGOUTIC_CODE=/path/to/agoutic
export AGOUTIC_DATA=/path/to/storage
mkdir -p $AGOUTIC_DATA/server3_work
mkdir -p $AGOUTIC_DATA/server3_logs
mkdir -p $AGOUTIC_DATA/database

# 2. Set environment variables (optional - defaults shown)
export LLM_URL="http://localhost:11434/v1"
export MAX_CONCURRENT_JOBS=2
export JOB_POLL_INTERVAL=10

# 3. Install dependencies (already in environment.yml)
conda activate agoutic_core
# or: pip install -r server3/requirements.txt
```

### Configuration

Edit `server3/config.py` to customize:

```python
# Storage paths
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", ...))
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", ...))

SERVER3_WORK_DIR = AGOUTIC_DATA / "server3_work"
SERVER3_LOGS_DIR = AGOUTIC_DATA / "server3_logs"
DATABASE_FILE = AGOUTIC_DATA / "database" / "agoutic_v23.sqlite"

# Nextflow
DOGME_REPO = "/opt/dogme"
NEXTFLOW_BIN = "/usr/local/bin/nextflow"

# Execution
MAX_CONCURRENT_JOBS = 2
JOB_POLL_INTERVAL = 10  # seconds
JOB_TIMEOUT = 172800    # 48 hours

# Reference genomes
REFERENCE_GENOMES = {
    "GRCh38": {...},
    "mm39": {...},
}
```

## Quick Start (5 minutes)

### 1. Setup Environment

```bash
cd /path/to/agoutic
bash server3/quickstart.sh
```

### 2. Start Server 3

```bash
# Terminal 1 - Server
uvicorn server3.app:app --host 0.0.0.0 --port 8001 --reload
```

### 3. Verify Server (Terminal 2)

```bash
curl http://localhost:8001/health
# Expected: {"status":"ok","version":"0.3.0",...}
```

### 4. Run Demo (Terminal 3)

```bash
python server3/demo_server3.py
# This will:
# 1. Submit a DNA analysis job
# 2. Track progress for 5 polling cycles
# 3. Show logs
# 4. List all jobs
```

✅ Done! You now have a working Server 3.

## Running Server 3

### Development Mode (with auto-reload)

```bash
uvicorn server3.app:app --host 0.0.0.0 --port 8001 --reload
```

### Production Mode (4 workers)

```bash
uvicorn server3.app:app --host 0.0.0.0 --port 8001 --workers 4
```

### Custom Configuration

```bash
# Optional: Set custom storage location (default: agoutic_code/data/)
export AGOUTIC_CODE=/custom/code/path
export AGOUTIC_DATA=/custom/storage/path

# Optional: Set custom Dogme location
export DOGME_REPO=/path/to/dogme

# Optional: Set custom Nextflow location
export NEXTFLOW_BIN=/path/to/nextflow

export MAX_CONCURRENT_JOBS=4
export JOB_POLL_INTERVAL=5
uvicorn server3.app:app --port 8001
```

## API Endpoints

### Health Check

```http
GET /health

Response:
{
  "status": "ok",
  "version": "0.3.0",
  "running_jobs": 0,
  "database_ok": true
}
```

### Submit Job

```http
POST /jobs/submit

Request Body:
{
  "project_id": "proj_001",
  "sample_name": "liver_dna",
  "mode": "DNA",                    # DNA, RNA, or CDNA
  "input_directory": "/data/pod5",
  "reference_genome": "GRCh38",     # Optional
  "modifications": "5mCG_5hmCG,6mA" # Optional
}

Response:
{
  "run_uuid": "a1b2c3d4-...",
  "sample_name": "liver_dna",
  "status": "RUNNING",
  "work_directory": "/path/to/server3_work/a1b2c3d4-..."
}
```

### Get Job Details

```http
GET /jobs/{run_uuid}

Response:
{
  "run_uuid": "a1b2c3d4-...",
  "project_id": "proj_001",
  "sample_name": "liver_dna",
  "mode": "DNA",
  "status": "RUNNING",
  "progress_percent": 45,
  "submitted_at": "2025-01-22T12:00:00+00:00",
  "started_at": "2025-01-22T12:05:00+00:00",
  "completed_at": null,
  "output_directory": "/path/to/results",
  "error_message": null,
  "report": null
}
```

### Get Job Status (Quick)

```http
GET /jobs/{run_uuid}/status

Response:
{
  "run_uuid": "a1b2c3d4-...",
  "status": "RUNNING",
  "progress_percent": 50,
  "message": "Pipeline executing..."
}
```

### Get Job Logs

```http
GET /jobs/{run_uuid}/logs?limit=100

Response:
{
  "run_uuid": "a1b2c3d4-...",
  "logs": [
    {
      "timestamp": "2025-01-22T12:05:00+00:00",
      "level": "INFO",
      "message": "Basecalling started",
      "source": "nextflow"
    },
    {
      "timestamp": "2025-01-22T12:15:00+00:00",
      "level": "INFO",
      "message": "Alignment completed",
      "source": "nextflow"
    }
  ]
}
```

### List Jobs

```http
GET /jobs?project_id=proj_001&status=RUNNING&limit=50

Response:
{
  "jobs": [...],
  "total_count": 150,
  "running_count": 3
}
```

### Cancel Job

```http
POST /jobs/{run_uuid}/cancel

Response:
{
  "status": "cancelled",
  "run_uuid": "a1b2c3d4-..."
}
```

## Supported Dogme Modes

### DNA Mode
- Analyzes genomic DNA and Fiber-seq data
- Includes modification calling (5mC, 6mA, etc.)
- Parameters: `reference_genome`, `modifications`

```bash
curl -X POST http://localhost:8001/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_001",
    "sample_name": "hela_dna",
    "mode": "DNA",
    "input_directory": "/data/dna_samples",
    "modifications": "5mCG_5hmCG,6mA"
  }'
```

### RNA Mode
- Analyzes Direct RNA-seq data
- Includes native RNA modification calling (m6A, pseU, etc.)
- Splice-aware alignment enabled
- Parameters: `reference_genome`, `modifications`

```bash
curl -X POST http://localhost:8001/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_001",
    "sample_name": "brain_rna",
    "mode": "RNA",
    "input_directory": "/data/rna_samples",
    "modifications": "inosine_m6A,pseU,m5C"
  }'
```

### cDNA Mode
- Analyzes polyA+ cDNA and isoform data
- **No modification calling** (faster processing)
- Transcript quantification focus
- Parameters: `reference_genome` only

```bash
curl -X POST http://localhost:8001/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_001",
    "sample_name": "liver_cdna",
    "mode": "CDNA",
    "input_directory": "/data/cdna_samples"
  }'
```

## Job Lifecycle

### State Diagram

```
PENDING
  ↓
RUNNING ←→ (monitoring with progress updates)
  ↓
  ├→ COMPLETED (success)
  └→ FAILED (error)

(Can be cancelled from PENDING or RUNNING)
```

### Example Flow

1. **Submit** - Job starts in PENDING state
2. **Monitor** - Background task polls Nextflow status every 10s
3. **Progress** - Updates progress_percent as pipeline runs
4. **Complete** - Parses results and sets COMPLETED
5. **Retrieve** - Results available via `/jobs/{uuid}` endpoint

## Database Schema

### DogmeJob Table

```sql
CREATE TABLE dogme_jobs (
    run_uuid TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    sample_name TEXT NOT NULL,
    mode TEXT NOT NULL,           -- DNA, RNA, CDNA
    input_directory TEXT NOT NULL,
    reference_genome TEXT,
    modifications TEXT,
    status TEXT DEFAULT 'PENDING', -- PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    progress_percent INTEGER DEFAULT 0,
    submitted_at DATETIME,
    started_at DATETIME,
    completed_at DATETIME,
    output_directory TEXT,
    report_json TEXT,
    error_message TEXT,
    nextflow_work_dir TEXT,
    nextflow_process_id INTEGER
);

CREATE TABLE job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid TEXT NOT NULL,
    timestamp DATETIME,
    level TEXT,  -- INFO, WARN, ERROR
    message TEXT NOT NULL,
    source TEXT
);
```

## Core Components

### 1. Configuration (`config.py`)

- Storage paths for work directories and logs
- Nextflow and Dogme pipeline paths
- Reference genome configuration (GRCh38, mm39)
- Modification motifs for DNA/RNA/cDNA modes
- Job execution limits and polling intervals

### 2. Database Models (`models.py`)

**DogmeJob** - Main job tracking table  
**JobLog** - Detailed execution logs

### 3. Nextflow Executor (`nextflow_executor.py`)

**NextflowConfig class**
- Generates Nextflow configurations for all 3 modes
- Converts Python dicts to Groovy format
- Handles modification motifs per mode

**NextflowExecutor class**
- `submit_job()` - Submits Nextflow job asynchronously
- `check_status()` - Polls job progress
- `get_results()` - Parses and returns analysis outputs

### 4. FastAPI Server (`app.py`)

**Endpoints:** (7 core endpoints)
- `GET /health` - Server health check
- `POST /jobs/submit` - Submit new job
- `GET /jobs/{uuid}` - Get full job details
- `GET /jobs/{uuid}/status` - Get quick status
- `GET /jobs/{uuid}/logs` - Stream job logs
- `GET /jobs` - List jobs (filtered)
- `POST /jobs/{uuid}/cancel` - Cancel job

**Background Tasks:**
- `monitor_job()` - Async monitoring per submitted job
- Polls Nextflow status every 10 seconds
- Updates database with progress
- Parses results on completion

### 5. MCP Integration

**MCP Tools** (`mcp_tools.py`)
- 7 async tool methods for LLM agents
- Tool registry with input schemas
- Job management functionality

**MCP Server** (`mcp_server.py`)
- Stdio-based MCP server
- Tool listing and call routing
- Error handling and protocol compliance

See [DUAL_INTERFACE.md](DUAL_INTERFACE.md) for architecture details.

## Testing

### Run Test Suite

```bash
# All tests
pytest server3/test_server3.py -v

# Specific test class
pytest server3/test_server3.py::TestNextflowConfig -v

# With coverage
pytest server3/test_server3.py --cov=server3 --cov-report=html
```

### Test Coverage

- ✅ Nextflow configuration generation (all 3 modes)
- ✅ Database CRUD operations
- ✅ Schema validation
- ✅ API endpoint logic
- ✅ Job lifecycle tracking
- ✅ Error handling & edge cases
- ✅ Integration with Server 1

## Monitoring & Logging

### View Running Jobs

```bash
curl http://localhost:8001/jobs?status=RUNNING
```

### View Job Logs in Real-time

```bash
# Get last 50 log entries
curl http://localhost:8001/jobs/a1b2c3d4-/logs?limit=50

# Parse with jq for filtering
curl http://localhost:8001/jobs/a1b2c3d4-/logs | jq '.logs[] | select(.level=="ERROR")'
```

### Database Query Examples

```sql
-- All running jobs
SELECT * FROM dogme_jobs WHERE status = 'RUNNING';

-- Jobs by project
SELECT * FROM dogme_jobs WHERE project_id = 'proj_001';

-- Recent errors
SELECT * FROM job_logs WHERE level = 'ERROR' ORDER BY timestamp DESC LIMIT 20;

-- Job progress over time
SELECT run_uuid, progress_percent, timestamp FROM job_logs WHERE level = 'INFO' ORDER BY timestamp;
```

## Performance Tuning

### Concurrent Jobs

Limit concurrent Nextflow jobs to avoid resource exhaustion:

```bash
export MAX_CONCURRENT_JOBS=2  # Adjust based on server capacity
```

### Polling Interval

Balance responsiveness vs. database load:

```bash
export JOB_POLL_INTERVAL=10  # seconds (default)
# Increase for lower database load
# Decrease for faster status updates
```

### Database

Use PostgreSQL for production instead of SQLite:

```python
# server3/config.py
DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/agoutic"
```

## Troubleshooting

### Server Won't Start

```bash
# Check port is available
lsof -i :8001

# Check database connectivity
python -c "from server3.db import SessionLocal; SessionLocal()"

# Check imports
python -c "from server3.app import app"
```

### Job Stuck in RUNNING

```bash
# Check Nextflow process
ps aux | grep nextflow

# Check logs
tail -f $AGOUTIC_DATA/server3_logs/*.log

# Cancel stuck job
curl -X POST http://localhost:8001/jobs/{run_uuid}/cancel
```

### Database Locked

```bash
# SQLite only - restart server to release lock
killall uvicorn

# Or use PostgreSQL for concurrent access
```

### Nextflow Not Found

```bash
# Check Nextflow installation
which nextflow

# Add to PATH or set NEXTFLOW_BIN
export NEXTFLOW_BIN=/usr/local/bin/nextflow
```

## Integration with Server 1

### Server 1 → Server 3 Communication

Server 1 (Agent Engine) calls Server 3 when a user asks for analysis:

```
User: "Analyze the liver DNA sample"
        ↓
Server 1 Agent:
- Plans the analysis
- Calls [[APPROVAL_NEEDED]] if approval gate
- On approval: POST /jobs/submit to Server 3 (or MCP call)
        ↓
Server 3:
- Queues job
- Returns run_uuid
- Starts background monitoring
        ↓
Server 1:
- Updates UI with job status
- Polls /jobs/{run_uuid}/status periodically
```

## Project Structure

```
server3/
├── __init__.py              # Package marker
├── app.py                   # FastAPI application & endpoints
├── config.py                # Configuration (paths, models, genomes)
├── models.py                # SQLAlchemy database models
├── db.py                    # Database connection & CRUD operations
├── schemas.py               # Pydantic validation schemas
├── nextflow_executor.py     # Nextflow wrapper & job execution
├── mcp_tools.py             # MCP tool definitions
├── mcp_server.py            # MCP server implementation
├── test_server3.py          # Comprehensive test suite (30+ tests)
├── test_integration.py      # Server 1 ↔ Server 3 integration tests
├── demo_server3.py          # Interactive demo & examples
├── quickstart.sh            # Quick-start setup script
├── README.md                # This file
├── DUAL_INTERFACE.md        # REST + MCP architecture details
└── IMPLEMENTATION_SUMMARY.md # Implementation details
```

## Version Information

- **Release**: Week 3 Prototype
- **API Version**: 0.3.0
- **Python**: 3.12+
- **FastAPI**: Latest (from environment.yml)
- **SQLAlchemy**: 2.0+
- **Status**: Fully Functional

## Next Steps (Future Weeks)

- Week 4: Integration with Server 1 approval gates
- Week 5: Web UI job monitoring dashboard
- Week 6: Performance optimization & deployment

## Documentation

- [DUAL_INTERFACE.md](DUAL_INTERFACE.md) - REST + MCP architecture
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - Detailed implementation notes
- [GETTING_STARTED.md](GETTING_STARTED.md) - Additional getting started guide
- [MCP_SUMMARY.md](MCP_SUMMARY.md) - MCP integration details
- [FILES.md](FILES.md) - File reference
