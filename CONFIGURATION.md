# AGOUTIC Configuration Guide

**Status:** ✅ Refactored to use portable path variables  
**Date:** January 22, 2026

## Path Configuration Strategy

AGOUTIC uses **two root path variables** that all other paths are derived from:

```
AGOUTIC_CODE → Source code repository (this folder)
AGOUTIC_DATA → Data, databases, and outputs (separate location)
```

This design allows you to:
- ✅ Run code from any location (no hardcoded paths)
- ✅ Keep data on fast/large storage
- ✅ Keep code on fast SSD for development
- ✅ Scale to production easily
- ✅ Use network-mounted storage

## Environment Variables

### Required Variables

None! Both variables have sensible defaults.

### Optional Variables (Override Defaults)

```bash
# AGOUTIC_CODE: Where source code lives
# Default: Where this repository is checked out
export AGOUTIC_CODE=/path/to/agoutic_code

# AGOUTIC_DATA: Where data/database/jobs live
# Default: $AGOUTIC_CODE/data
export AGOUTIC_DATA=/path/to/storage/agoutic_data

# Additional optional variables:
export DOGME_REPO=$AGOUTIC_CODE/dogme        # Or custom path
export NEXTFLOW_BIN=/usr/local/bin/nextflow   # Or custom path
```

## Derived Paths

All other paths are **automatically derived** from the two root variables:

### From AGOUTIC_CODE (Source Code)
```
AGOUTIC_CODE/
├── skills/              # Workflow definitions
├── server1/             # Agent engine
└── server3/             # Job execution engine
    └── dogme/           # Dogme pipeline (optional)
```

### From AGOUTIC_DATA (Storage)
```
AGOUTIC_DATA/
├── database/
│   └── agoutic_v23.sqlite    # SQLite database
├── server3_work/
│   └── <run_uuid>/           # Individual job directories
│       ├── nextflow.config
│       ├── work/             # Nextflow working directory
│       ├── results/          # Final output
│       └── *.log            # Job logs
└── server3_logs/
    └── *.log                 # Server debug logs
```

## Quick Start Scenarios

### Scenario 1: Default Setup (Code in one place, data in same location)

```bash
# Clone repository
git clone https://github.com/you/agoutic.git /Users/eli/code/agoutic

# Everything is configured automatically!
cd /Users/eli/code/agoutic

# This creates: /Users/eli/code/agoutic/data/
python -c "from server3.config import AGOUTIC_CODE, AGOUTIC_DATA; print(f'Code: {AGOUTIC_CODE}\nData: {AGOUTIC_DATA}')"

# Run Server 3
uvicorn server3.app:app --port 8001
```

### Scenario 2: Code on SSD, Data on Large Drive

```bash
# Clone repository to fast SSD
git clone https://github.com/you/agoutic.git /fast-ssd/agoutic

# Point data to large storage
export AGOUTIC_CODE=/fast-ssd/agoutic
export AGOUTIC_DATA=/large-storage/agoutic_data

# Now running code uses the paths you specified
cd /fast-ssd/agoutic
uvicorn server3.app:app --port 8001
```

Verify paths:
```bash
python -c "from server3.config import AGOUTIC_CODE, AGOUTIC_DATA; print(f'Code: {AGOUTIC_CODE}\nData: {AGOUTIC_DATA}')"
```

### Scenario 3: Code in Docker, Data on Host

```bash
# Host (before running container)
export AGOUTIC_DATA=/Volumes/data/agoutic

# Docker container
docker run \
  -e AGOUTIC_CODE=/app/agoutic \
  -e AGOUTIC_DATA=/mnt/data/agoutic \
  -v /Volumes/data/agoutic:/mnt/data/agoutic \
  agoutic-server3
```

### Scenario 4: Production Setup (Network Storage)

```bash
# All machines use same network storage
export AGOUTIC_CODE=/opt/agoutic          # Local code
export AGOUTIC_DATA=/mnt/nfs/agoutic      # NFS-mounted shared data

# Multiple servers can share same data
uvicorn server3.app:app --port 8001 --workers 4
```

## Configuration in Code

### Server 3 Configuration

[server3/config.py](server3/config.py):

```python
# Root variables (only these are configurable)
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", ...))  # Code location
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", ...))  # Data location

# Everything else is derived
SERVER3_WORK_DIR = AGOUTIC_DATA / "server3_work"     # Job directories
SERVER3_LOGS_DIR = AGOUTIC_DATA / "server3_logs"     # Server logs
DB_FILE = AGOUTIC_DATA / "database" / "agoutic_v23.sqlite"
DOGME_REPO = Path(os.getenv("DOGME_REPO", AGOUTIC_CODE / "dogme"))
```

### Server 1 Configuration

[server1/config.py](server1/config.py):

```python
# Same root variables
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", ...))
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", ...))

# Everything else is derived
SKILLS_DIR = AGOUTIC_CODE / "skills"
DB_FILE = AGOUTIC_DATA / "database" / "agoutic_v23.sqlite"
```

## Verification

### Check Current Configuration

```bash
cd /Users/eli/code/agoutic

python -c "
from server3.config import AGOUTIC_CODE, AGOUTIC_DATA, SERVER3_WORK_DIR, DOGME_REPO, DB_FILE
from server1.config import SKILLS_DIR

print('📍 Current Configuration:')
print()
print('  SOURCE CODE (AGOUTIC_CODE):')
print(f'    Root: {AGOUTIC_CODE}')
print(f'    Skills: {SKILLS_DIR}')
print(f'    Dogme: {DOGME_REPO}')
print()
print('  DATA STORAGE (AGOUTIC_DATA):')
print(f'    Root: {AGOUTIC_DATA}')
print(f'    Work: {SERVER3_WORK_DIR}')
print(f'    Database: {DB_FILE}')
"
```

### Check Environment Variables

```bash
# Show current values
echo "AGOUTIC_CODE=$AGOUTIC_CODE"
echo "AGOUTIC_DATA=$AGOUTIC_DATA"
echo "DOGME_REPO=$DOGME_REPO"

# Or check if they're set
env | grep AGOUTIC
```

### Verify Paths Exist

```bash
# Check code directory
ls -la $AGOUTIC_CODE/server1
ls -la $AGOUTIC_CODE/server3
ls -la $AGOUTIC_CODE/skills

# Check (or create) data directory
mkdir -p $AGOUTIC_DATA/{database,server3_work,server3_logs}
ls -la $AGOUTIC_DATA/
```

## Moving or Redeploying

### Move Code to New Location

```bash
# Old location
old=/Users/eli/code/agoutic

# New location
new=/opt/agoutic

# Move code
mv $old $new

# Set environment
export AGOUTIC_CODE=$new
export AGOUTIC_DATA=$new/data

# Data remains in same location (if not moved)
```

### Migrate Data to New Storage

```bash
# Old data location
old_data=/Users/eli/code/agoutic/data

# New storage location
new_data=/large-storage/agoutic_data

# Copy data
cp -r $old_data/* $new_data/

# Update environment
export AGOUTIC_DATA=$new_data

# Verify
python -c "from server3.config import AGOUTIC_DATA; print(f'Data: {AGOUTIC_DATA}')"
```

## Docker / Container Usage

### Building Container

Create `Dockerfile`:

```dockerfile
FROM python:3.12
WORKDIR /app

# Copy code
COPY . /app/agoutic

# Install dependencies
RUN pip install -r /app/agoutic/requirements.txt

# Use environment variables for paths
ENV AGOUTIC_CODE=/app/agoutic
ENV AGOUTIC_DATA=/data/agoutic

# Create data directories
RUN mkdir -p $AGOUTIC_DATA/{database,server3_work,server3_logs}

CMD ["uvicorn", "server3.app:app", "--host", "0.0.0.0", "--port", "8001"]
```

### Running Container

```bash
# Point to host storage
docker run \
  -e AGOUTIC_CODE=/app/agoutic \
  -e AGOUTIC_DATA=/mnt/data/agoutic \
  -v /host/storage/agoutic_data:/mnt/data/agoutic \
  -p 8001:8001 \
  my-agoutic-image
```

## Environment Setup Checklist

- [ ] Clone repository to desired code location
- [ ] Set `AGOUTIC_CODE` environment variable (optional, defaults to repo location)
- [ ] Set `AGOUTIC_DATA` environment variable (optional, defaults to `$AGOUTIC_CODE/data`)
- [ ] Verify paths exist: `python -c "from server3.config import *; print('OK')"`
- [ ] Create data directories: `mkdir -p $AGOUTIC_DATA/{database,server3_work,server3_logs}`
- [ ] Set other optional variables (DOGME_REPO, NEXTFLOW_BIN)
- [ ] Run tests: `pytest server3/test_server3.py -v`
- [ ] Start server: `uvicorn server3.app:app --port 8001`

## Troubleshooting

### Paths Show Hardcoded User Names

Problem: You see `/Users/eli/code/agoutic` in output

Solution: The paths are correctly defaulting to the repository location. This is expected behavior and not hardcoded. You can override with environment variables.

### Database File in Wrong Location

Problem: `agoutic_v23.sqlite` created in unexpected location

Solution: Set `AGOUTIC_DATA` before running:
```bash
export AGOUTIC_DATA=/path/to/desired/location
python -c "from server3.config import DB_FILE; print(f'Database: {DB_FILE}')"
```

### Can't Find Skills Directory

Problem: Skills files not found when running Server 1

Solution: Set `AGOUTIC_CODE` to correct code location:
```bash
export AGOUTIC_CODE=/path/to/agoutic_code
python -c "from server1.config import SKILLS_DIR; ls -la $SKILLS_DIR"
```

### Job Output in Wrong Directory

Problem: `server3_work` directory in unexpected location

Solution: Verify `AGOUTIC_DATA`:
```bash
echo $AGOUTIC_DATA
python -c "from server3.config import SERVER3_WORK_DIR; print(SERVER3_WORK_DIR)"
```

## Summary

| Variable | Purpose | Default |
|----------|---------|---------|
| `AGOUTIC_CODE` | Source code location | Repository location |
| `AGOUTIC_DATA` | Data/storage location | `$AGOUTIC_CODE/data` |
| `DOGME_REPO` | Dogme pipeline path | `$AGOUTIC_CODE/dogme` |
| `NEXTFLOW_BIN` | Nextflow executable | `/usr/local/bin/nextflow` |

All paths are **automatically derived** from these variables. No hardcoded user paths!

✅ **Portable. Scalable. Production-Ready.**
