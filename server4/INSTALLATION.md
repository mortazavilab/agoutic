# Server4 Installation and Setup Guide

## Prerequisites

- Python 3.8+
- SQLAlchemy
- FastAPI
- Pydantic
- MCP SDK (mcp package)

## Installation

### 1. Install Dependencies

From the agoutic project root:

```bash
# If using conda (recommended)
conda activate agoutic_core

# Install required packages
pip install fastapi uvicorn sqlalchemy pydantic mcp

# Or if you have a requirements file
pip install -r server4/requirements.txt
```

### 2. Verify Installation

```bash
# Check Python imports
python3 -c "import fastapi, sqlalchemy, mcp; print('✅ All imports successful')"
```

### 3. Set Environment Variables

Create or update your `.env` file:

```bash
# Server4 configuration
export SERVER4_HOST=0.0.0.0
export SERVER4_PORT=8004
export SERVER4_MCP_PORT=8005

# Database (shared with server1/server3)
export DATABASE_URL=sqlite:///./data/database/agoutic.db

# Work directory (where Dogme job results are stored)
export AGOUTIC_WORK_DIR=/media/backup_disk/agoutic_root/server3_work

# File limits
export MAX_PREVIEW_LINES=1000
export MAX_FILE_SIZE_MB=100
```

Load the environment:
```bash
source .env
```

### 4. Verify Directory Structure

```bash
# Ensure data directories exist
mkdir -p data/database
mkdir -p data/server3_work

# Or use your configured work directory
mkdir -p $AGOUTIC_WORK_DIR
```

## Running Server4

### Option 1: Using Startup Script (Recommended)

```bash
# Make script executable (first time only)
chmod +x server4/start.sh

# Run tests
./server4/start.sh test

# Start REST API
./server4/start.sh rest

# Start in development mode (auto-reload)
./server4/start.sh dev

# Start MCP server (for agent integration)
./server4/start.sh mcp
```

### Option 2: Direct Python Execution

```bash
# REST API
python3 -m server4.app

# MCP Server
python3 -m server4.mcp_server

# Run tests
python3 server4/test_analysis.py
```

### Option 3: Using Uvicorn (Production)

```bash
# Single worker
uvicorn server4.app:app --host 0.0.0.0 --port 8004

# Multiple workers for production
uvicorn server4.app:app --host 0.0.0.0 --port 8004 --workers 4

# With SSL
uvicorn server4.app:app --host 0.0.0.0 --port 8004 \
    --ssl-keyfile=/path/to/key.pem \
    --ssl-certfile=/path/to/cert.pem
```

## Verification

### 1. Check Server Health

```bash
# REST API health check
curl http://localhost:8004/health

# Should return: {"status":"healthy"}
```

### 2. Check API Documentation

Open in browser:
- Swagger UI: http://localhost:8004/docs
- ReDoc: http://localhost:8004/redoc

### 3. Test with Sample Job

```bash
# Replace with actual run_uuid from your server3_work directory
RUN_UUID="your-job-uuid-here"

# List files
curl "http://localhost:8004/analysis/jobs/$RUN_UUID/files"

# Get summary
curl "http://localhost:8004/analysis/summary/$RUN_UUID"
```

### 4. Run Test Suite

```bash
./server4/start.sh test
```

Expected output:
```
Server4 Analysis Engine Test Suite
====================================
TEST 1: File Discovery
...
✅ All tests passed!
```

## Integration with Existing System

### With Server1 (Agent Engine)

Server1 can now use Server4 analysis tools via MCP:

```python
# In server1/agent_engine.py or wherever agents are initialized

from server1.server4_mcp_client import Server4MCPClient

class EnhancedAgent:
    def __init__(self):
        self.analysis_client = Server4MCPClient()
        
    async def setup(self):
        await self.analysis_client.connect()
        
    async def analyze_job(self, run_uuid):
        # Agent can now analyze results
        summary = await self.analysis_client.get_analysis_summary(run_uuid)
        return summary
```

### With Server3 (Execution Engine)

Server4 reads from the same database as Server3:
- Accesses `DogmeJob` table for job metadata
- Reads from `work_dir` paths stored in database
- Independent operation - no direct coupling

### With UI (Streamlit)

Add results viewing page in `ui/pages/`:

```python
# ui/pages/results.py

import streamlit as st
import requests

st.title("Job Results Analysis")

run_uuid = st.text_input("Job UUID")
if run_uuid:
    # Get summary
    response = requests.get(f"http://localhost:8004/analysis/summary/{run_uuid}")
    summary = response.json()
    
    st.write(f"Sample: {summary['sample_name']}")
    st.write(f"Status: {summary['status']}")
    
    # Show files
    categories = summary['file_summary']
    st.write(f"CSV files: {len(categories['csv_files'])}")
    st.write(f"BED files: {len(categories['bed_files'])}")
```

## Configuration Options

### Work Directory

Server4 needs access to completed job directories. Set this to match Server3's work directory:

```bash
# Local development
export AGOUTIC_WORK_DIR=./data/server3_work

# Production (from your example)
export AGOUTIC_WORK_DIR=/media/backup_disk/agoutic_root/server3_work
```

### Database

Server4 shares the database with Server1/Server3:

```bash
# SQLite (development)
export DATABASE_URL=sqlite:///./data/database/agoutic.db

# PostgreSQL (production)
export DATABASE_URL=postgresql://user:password@localhost/agoutic
```

### File Limits

Adjust based on your expected file sizes:

```bash
# Preview limits
export MAX_PREVIEW_LINES=1000      # Lines to read by default
export MAX_FILE_SIZE_MB=100        # Max file size to read

# For large genome files, increase:
export MAX_FILE_SIZE_MB=500
```

### Ports

Ensure ports don't conflict:

```bash
export SERVER4_PORT=8004           # REST API
export SERVER4_MCP_PORT=8005       # MCP server (if using HTTP transport)
```

## Troubleshooting

### Import Errors

```bash
# Error: ModuleNotFoundError: No module named 'mcp'
pip install mcp

# Error: ModuleNotFoundError: No module named 'server4'
# Make sure you're running from project root and Python path is correct
cd /Users/eli/code/agoutic
python3 -m server4.app
```

### Database Connection Issues

```bash
# Check database file exists
ls -la data/database/agoutic.db

# Check database is accessible
sqlite3 data/database/agoutic.db ".tables"

# Should show: dogme_jobs, job_logs, etc.
```

### Work Directory Issues

```bash
# Check work directory exists and has jobs
ls -la $AGOUTIC_WORK_DIR

# Check permissions
ls -ld $AGOUTIC_WORK_DIR

# Should have read/execute permissions for your user
```

### Port Already in Use

```bash
# Check what's using the port
lsof -i :8004

# Kill the process
kill -9 <PID>

# Or use a different port
export SERVER4_PORT=8005
```

### File Not Found Errors

```bash
# Verify job UUID exists
ls $AGOUTIC_WORK_DIR/{run_uuid}

# Check database has job record
sqlite3 data/database/agoutic.db "SELECT run_uuid, work_dir, status FROM dogme_jobs WHERE run_uuid='{run_uuid}';"
```

## Production Deployment

### Using systemd (Linux)

Create `/etc/systemd/system/agoutic-server4.service`:

```ini
[Unit]
Description=AGOUTIC Server4 Analysis Server
After=network.target

[Service]
Type=simple
User=agoutic
WorkingDirectory=/opt/agoutic
Environment="PATH=/opt/conda/envs/agoutic_core/bin"
Environment="SERVER4_HOST=0.0.0.0"
Environment="SERVER4_PORT=8004"
Environment="AGOUTIC_WORK_DIR=/media/backup_disk/agoutic_root/server3_work"
ExecStart=/opt/conda/envs/agoutic_core/bin/python -m server4.app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable agoutic-server4
sudo systemctl start agoutic-server4
sudo systemctl status agoutic-server4
```

### Using Docker

Create `server4/Dockerfile`:

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server4/ ./server4/
COPY data/ ./data/

EXPOSE 8004

CMD ["python", "-m", "server4.app"]
```

Build and run:
```bash
docker build -t agoutic-server4 -f server4/Dockerfile .
docker run -p 8004:8004 -v /media/backup_disk/agoutic_root/server3_work:/data/server3_work agoutic-server4
```

### Behind Nginx (Reverse Proxy)

Add to nginx config:

```nginx
location /analysis/ {
    proxy_pass http://localhost:8004/analysis/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## Monitoring

### Logs

```bash
# Direct Python execution logs to stdout
python3 -m server4.app 2>&1 | tee server4.log

# Uvicorn logs
uvicorn server4.app:app --log-level info --log-config logging.ini
```

### Health Checks

```bash
# Simple health check
curl http://localhost:8004/health

# Add to monitoring system (e.g., Nagios, Prometheus)
*/5 * * * * curl -f http://localhost:8004/health || systemctl restart agoutic-server4
```

## Next Steps

1. **Test with Real Data**: Run a Dogme job and analyze results
2. **Integrate with UI**: Add results viewing page
3. **Configure Monitoring**: Set up health checks
4. **Enable MCP**: Connect Server1 agent to Server4 MCP server
5. **Optimize**: Tune file size limits and caching

## Support

For issues or questions:
- Check logs: `tail -f server4.log`
- Run tests: `./server4/start.sh test`
- Review API docs: http://localhost:8004/docs
- Check configuration: `env | grep SERVER4`
