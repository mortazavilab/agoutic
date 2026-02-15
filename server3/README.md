# AGOUTIC Server 3: Dogme/Nextflow Job Execution Engine

**Version:** 0.3.0  
**Status:** Active Development

## Overview

Server 3 is the **execution engine** for the AGOUTIC bioinformatics platform. It receives job requests from Server 1 (the Agent Engine) and manages the execution of **Dogme/Nextflow** pipelines for genomic analysis.

### Key Responsibilities
- 🚀 **Job Submission**: Receive and queue Dogme pipeline jobs (DNA/RNA/cDNA)
- 📊 **Job Monitoring**: Track job progress in real-time
- 📝 **Logging**: Capture detailed execution logs
- 🔍 **Status Tracking**: Maintain persistent job state in database
- 📤 **Result Retrieval**: Return analysis outputs and reports
- 🤝 **Dual Interface**: REST API + MCP Protocol for LLM agents

## Architecture: Dual Interface

Server 3 provides **both** interfaces, allowing flexible integration patterns:

### 1. REST API (HTTP)
- **Port**: 8003 (default)
- **Location**: `server3/app.py`
- **Use Case**: Web dashboards, external scripts, periodic polling.
- **Endpoints**:
    - `POST /jobs/submit` - Submit job
    - `GET /jobs` - List jobs
    - `GET /jobs/{uuid}` - Get details
    - `GET /jobs/{uuid}/logs` - Stream logs

### 2. MCP Protocol (Model Context Protocol)
- **Port**: 8002 (MCP-over-HTTP)
- **Location**: `server3/mcp_server.py`
- **Use Case**: LLM Agents (Server 1) directly controlling tools.
- **Tools**:
    - `submit_dogme_job`: Generic submission wrapper
    - `submit_dogme_nextflow`: Low-level submission with config generation
    - `check_nextflow_status`: Query database for status
    - `find_pod5_directory`: Locate data files

## Installation & Setup

### Prerequisites
- Python 3.10+
- Nextflow (installed and in PATH)
- Java 11+ (for Nextflow)

### Quick Start

1.  **Environment Setup**:
    ```bash
    cd /path/to/agoutic
    source load_env.sh
    ```

2.  **Start the Server**:
    ```bash
    # Starts both REST (8003) and MCP (8002) interfaces
    ./agoutic_servers.sh start server3
    ```

3.  **Verify Status**:
    ```bash
    curl http://localhost:8003/health
    # Expected: {"status":"ok","version":"0.3.0",...}
    ```

## Project Structure

- **`app.py`**: FastAPI application entry point (REST).
- **`mcp_server.py`**: FastMCP application (MCP-over-HTTP).
- **`mcp_tools.py`**: Logic for MCP tools integration.
- **`nextflow_executor.py`**: Core logic for spawning and monitoring Nextflow processes.
- **`models.py`**: SQLAlchemy models for `DogmeJob` and `JobLog`.
- **`db.py`**: Database connection and CRUD operations.
- **`config.py`**: Configuration for paths, reference genomes, and pipelines.

## Development

### Running Tests
```bash
pytest server3/test_server3.py
pytest server3/test_integration.py
```

### Running the Demo
A standalone demo script submits a mock job and polls for completion:
```bash
python server3/demo_server3.py
```
