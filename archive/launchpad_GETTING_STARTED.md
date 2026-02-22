#!/usr/bin/env bash
# AGOUTIC Launchpad - Complete Getting Started Guide

cat << 'EOF'
╔════════════════════════════════════════════════════════════════════════╗
║              AGOUTIC LAUNCHPAD - GETTING STARTED GUIDE                 ║
║            Dogme/Nextflow Job Execution Engine (Week 3)               ║
╚════════════════════════════════════════════════════════════════════════╝

## 📋 TABLE OF CONTENTS

1. Quick Start (5 minutes)
2. Verify Installation
3. Run Server
4. Run Tests
5. Run Demo
6. Check Results
7. Integrate with Cortex
8. Troubleshooting

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 1️⃣  QUICK START (5 minutes)

### Step 1: Setup Environment
    cd /Users/eli/code/agoutic
    bash launchpad/quickstart.sh

### Step 2: Start Launchpad
    # Terminal 1 - Server
    uvicorn launchpad.app:app --host 0.0.0.0 --port 8001 --reload

### Step 3: Verify Server (Terminal 2)
    curl http://localhost:8001/health
    # Expected: {"status":"ok","version":"0.3.0",...}

### Step 4: Run Interactive Demo (Terminal 3)
    python launchpad/demo_launchpad.py
    # This will:
    # 1. Submit a DNA analysis job
    # 2. Track progress for 5 polling cycles
    # 3. Show logs
    # 4. List all jobs

✅ Done! You now have a working Launchpad.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 2️⃣  VERIFY INSTALLATION

### Check Python
    python --version
    # Expected: Python 3.12.x

### Check FastAPI
    python -c "import fastapi; print(fastapi.__version__)"

### Check SQLAlchemy
    python -c "import sqlalchemy; print(sqlalchemy.__version__)"

### Check Nextflow
    nextflow -v
    # Expected: nextflow version...

### Check Imports
    python -c "from launchpad.app import app; print('✅ Imports OK')"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 3️⃣  RUN SERVER

### Development Mode (with auto-reload)
    uvicorn launchpad.app:app --host 0.0.0.0 --port 8001 --reload

    Output should show:
    ✅ Launchpad initialized: Database ready
    📍 Max concurrent jobs: 2
    ⏱️  Job poll interval: 10s
    INFO:     Application startup complete

### Production Mode (4 workers)
    uvicorn launchpad.app:app --host 0.0.0.0 --port 8001 --workers 4

### Alternative Port
    export PORT=8002
    uvicorn launchpad.app:app --host 0.0.0.0 --port $PORT

### Custom Configuration
    # Optional: Set custom storage location (default: agoutic_code/data/)
    export AGOUTIC_ROOT=/custom/path
    
    # Optional: Set custom Dogme location
    export DOGME_REPO=/path/to/dogme
    
    # Optional: Set custom Nextflow location
    export NEXTFLOW_BIN=/path/to/nextflow
    
    export MAX_CONCURRENT_JOBS=4
    export JOB_POLL_INTERVAL=5
    uvicorn launchpad.app:app --port 8001

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 4️⃣  RUN TESTS

### Run All Tests
    pytest launchpad/test_launchpad.py -v

    Expected: 30+ tests PASSED

### Run Specific Test Class
    pytest launchpad/test_launchpad.py::TestNextflowConfig -v
    pytest launchpad/test_launchpad.py::TestDatabase -v
    pytest launchpad/test_launchpad.py::TestIntegration -v

### Run with Coverage
    pytest launchpad/test_launchpad.py --cov=launchpad --cov-report=html
    # Opens: htmlcov/index.html

### Run Integration Tests
    python launchpad/test_integration.py

    Expected: Tests run with Launchpad running

### Quick Syntax Check
    python -m py_compile launchpad/*.py
    # No output = success

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 5️⃣  RUN DEMO

### Full Interactive Demo
    python launchpad/demo_launchpad.py

### Submit DNA Job Only
    python launchpad/demo_launchpad.py dna
    # Returns: run_uuid (copy this)

### Submit RNA Job
    python launchpad/demo_launchpad.py rna

### Submit cDNA Job
    python launchpad/demo_launchpad.py cdna

### Track Specific Job
    python launchpad/demo_launchpad.py track <run_uuid>

### View Job Logs
    python launchpad/demo_launchpad.py logs <run_uuid>

### List All Jobs
    python launchpad/demo_launchpad.py list

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 6️⃣  CHECK RESULTS

### Health Check
    curl http://localhost:8001/health
    # Shows: running_jobs, database status, version

### List All Jobs
    curl http://localhost:8001/jobs | jq

### Get Specific Job
    curl http://localhost:8001/jobs/<run_uuid> | jq

### Get Job Status
    curl http://localhost:8001/jobs/<run_uuid>/status | jq

### Get Job Logs
    curl http://localhost:8001/jobs/<run_uuid>/logs | jq
    curl http://localhost:8001/jobs/<run_uuid>/logs?limit=50 | jq

### List Jobs by Project
    curl 'http://localhost:8001/jobs?project_id=proj_001' | jq

### List Running Jobs
    curl 'http://localhost:8001/jobs?status=RUNNING' | jq

### View Database
    sqlite3 $AGOUTIC_ROOT/database/agoutic_v23.sqlite
    
    sqlite> .tables
    sqlite> SELECT run_uuid, status, progress_percent FROM dogme_jobs;
    sqlite> SELECT timestamp, level, message FROM job_logs LIMIT 20;

### View Logs Files
    ls -la $AGOUTIC_ROOT/launchpad_logs/
    tail -f $AGOUTIC_ROOT/launchpad_logs/*.log

### View Work Directories
    ls -la /media/backup_disk/agoutic_root/launchpad_work/
    # Each has: nextflow.config, work/, timeline.html, report.html

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 7️⃣  INTEGRATE WITH CORTEX

### Python Client Example
    import requests
    import json
    
    # Submit job from Cortex Agent
    response = requests.post(
        "http://localhost:8001/jobs/submit",
        json={
            "project_id": "proj_001",
            "sample_name": "liver_dna",
            "mode": "DNA",
            "input_directory": "/data/samples/dna",
            "reference_genome": "GRCh38",
            "modifications": "5mCG_5hmCG,6mA"
        }
    )
    
    job = response.json()
    run_uuid = job["run_uuid"]
    
    # Poll status
    import time
    while True:
        status_resp = requests.get(f"http://localhost:8001/jobs/{run_uuid}/status")
        status = status_resp.json()
        print(f"Status: {status['status']} - {status['progress_percent']}%")
        
        if status['status'] in ('COMPLETED', 'FAILED'):
            break
        time.sleep(5)
    
    # Get results
    results = requests.get(f"http://localhost:8001/jobs/{run_uuid}").json()
    print(json.dumps(results, indent=2))

### Bash/Curl Integration
    # 1. Submit job
    RUN_UUID=$(curl -s -X POST http://localhost:8001/jobs/submit \
      -H "Content-Type: application/json" \
      -d '{
        "project_id":"proj_001",
        "sample_name":"liver_dna",
        "mode":"DNA",
        "input_directory":"/data/samples/dna"
      }' | jq -r '.run_uuid')
    
    echo "Job submitted: $RUN_UUID"
    
    # 2. Poll status
    for i in {1..10}; do
      curl -s http://localhost:8001/jobs/$RUN_UUID/status | jq
      sleep 5
    done
    
    # 3. Get results
    curl -s http://localhost:8001/jobs/$RUN_UUID | jq

### Connect from Cortex (Future)
    # In cortex/app.py @app.post("/chat")
    
    from launchpad.schemas import SubmitJobRequest
    
    # After user approval:
    job_req = SubmitJobRequest(
        project_id=project_id,
        sample_name=req.sample_name,
        mode=detected_mode,  # From ENCODE metadata
        input_directory=downloaded_path,
        reference_genome="GRCh38"
    )
    
    launchpad_response = requests.post(
        "http://launchpad:8001/jobs/submit",
        json=job_req.dict()
    )
    
    run_uuid = launchpad_response.json()["run_uuid"]
    
    # Store in database for UI polling
    # Periodically poll /jobs/{run_uuid}/status

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 8️⃣  TROUBLESHOOTING

### Server Won't Start

Problem: "Address already in use"
Solution:
  lsof -i :8001
  kill -9 <PID>
  # Or use different port: --port 8002

Problem: "ModuleNotFoundError"
Solution:
  python -m pip install -r requirements.txt
  # Or: conda activate agoutic_core

Problem: "Database locked"
Solution:
  # SQLite issue, restart server
  killall uvicorn
  sleep 2
  uvicorn launchpad.app:app --port 8001

### Job Stuck in RUNNING

Problem: Job never completes
Solution:
  # Check if Nextflow is running
  ps aux | grep nextflow
  
  # Check logs
  tail -f $AGOUTIC_ROOT/launchpad_logs/*.log
  
  ls -la $AGOUTIC_ROOT/launchpad_work/
  
  # Cancel stuck job
  curl -X POST http://localhost:8001/jobs/<run_uuid>/cancel

### No Results Returned

Problem: Job shows COMPLETED but no output
Solution:
  # Check work directory
  ls -la $AGOUTIC_ROOT/launchpad_work/<run_uuid>/results/
  
  # Check Nextflow reports
  cat $AGOUTIC_ROOT/launchpad_work/<run_uuid>/report.html
  
  # Check database
  sqlite3 $AGOUTIC_ROOT/database/agoutic_v23.sqlite
  SELECT output_directory, report_json FROM dogme_jobs WHERE run_uuid='...';

### API Returns 500 Error

Problem: Internal server error
Solution:
  # Check server logs
  # Look for traceback in terminal where uvicorn is running
  
  # Try health check
  curl http://localhost:8001/health
  
  # Check database connectivity
  python -c "from launchpad.db import SessionLocal; SessionLocal()"
  
  # Restart server
  killall uvicorn
  uvicorn launchpad.app:app --port 8001

### Invalid Mode Error

Problem: Job submission rejected with "invalid mode"
Solution:
  # Valid modes are: DNA, RNA, CDNA (case-sensitive)
  
  curl -X POST http://localhost:8001/jobs/submit \
    -d '{
      "mode": "DNA"  # ✅ Correct
      "mode": "dna"  # ❌ Wrong (case)
    }'

### Can't Find Nextflow

Problem: "Nextflow not found in PATH"
Solution:
  which nextflow
  # If not found, install:
  curl -s https://get.nextflow.io | bash
  
  # Or set explicitly:
  export NEXTFLOW_BIN=/usr/local/bin/nextflow
  
  # Or update config.py:
  NEXTFLOW_BIN = "/path/to/nextflow"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📚 DOCUMENTATION FILES

- README.md - Complete API documentation
- IMPLEMENTATION_SUMMARY.md - Technical overview
- FILES.md - File structure and purposes
- VERIFICATION_CHECKLIST.md - Requirements checklist
- quickstart.sh - Automated setup
- This file: GETTING_STARTED.md

## 📞 QUICK REFERENCE

Server health:     curl http://localhost:8001/health
API info:          curl http://localhost:8001/
Submit job:        POST /jobs/submit
Get status:        GET /jobs/{uuid}/status
Get details:       GET /jobs/{uuid}
Get logs:          GET /jobs/{uuid}/logs
List jobs:         GET /jobs
Cancel job:        POST /jobs/{uuid}/cancel

Test suite:        pytest launchpad/test_launchpad.py -v
Integration test:  python launchpad/test_integration.py
Interactive demo:  python launchpad/demo_launchpad.py

Database:          $AGOUTIC_ROOT/database/agoutic_v23.sqlite
Work directory:    $AGOUTIC_ROOT/launchpad_work/
Log files:         $AGOUTIC_ROOT/launchpad_logs/

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎉 You're all set! Start Launchpad and begin submitting jobs.

For more details: see README.md and other documentation files in launchpad/

EOF
