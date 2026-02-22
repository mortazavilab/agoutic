# AGOUTIC Code Folder Path Configuration

**Updated:** January 22, 2026  
**Status:** All paths now reference `agoutic_code` folder structure

## Overview

The AGOUTIC system has been updated to use **relative paths** that work within the `agoutic_code` folder structure. This replaces the previous hardcoded absolute paths like `/media/backup_disk/agoutic_root`.

## Default Folder Structure

```
agoutic_code/
├── cortex/                    # Cortex (Agent Engine)
├── launchpad/                    # Launchpad (Job Execution)
├── skills/                     # LLM Skills
├── data/                       # Storage (AUTO-CREATED)
│   ├── database/
│   │   └── agoutic_v23.sqlite
│   ├── launchpad_work/           # Job work directories
│   └── launchpad_logs/           # Job logs
├── dogme/                      # Dogme pipeline (optional)
└── environment.yml
```

## Environment Variables

All paths can be customized with environment variables:

```bash
# Storage root (default: agoutic_code/data/)
export AGOUTIC_ROOT=/custom/storage/path

# Dogme pipeline location (default: agoutic_code/dogme/)
export DOGME_REPO=/path/to/dogme

# Nextflow executable (default: /usr/local/bin/nextflow)
export NEXTFLOW_BIN=/path/to/nextflow
```

## Path Resolution

### Launchpad Configuration (launchpad/config.py)

```python
# Storage paths - Falls back to agoutic_code/data/
AGOUTIC_ROOT = Path(os.getenv("AGOUTIC_ROOT", 
    Path(__file__).resolve().parent.parent / "data"))

# Dogme pipeline - Falls back to agoutic_code/dogme/
DOGME_REPO_DEFAULT = Path(__file__).resolve().parent.parent / "dogme"
DOGME_REPO = Path(os.getenv("DOGME_REPO", str(DOGME_REPO_DEFAULT)))

# Nextflow - Uses system PATH
NEXTFLOW_BIN = Path(os.getenv("NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
```

### Cortex Configuration (cortex/config.py)

```python
# Storage paths - Falls back to agoutic_code/data/
AGOUTIC_ROOT = Path(os.getenv("AGOUTIC_ROOT",
    Path(__file__).resolve().parent.parent / "data"))

# Code root - Resolves to agoutic_code/
CODE_ROOT = Path(__file__).resolve().parent.parent

# Skills directory - Resolves to agoutic_code/skills/
SKILLS_DIR = CODE_ROOT / "skills"
```

## Quick Setup

### Option 1: Use Defaults (Recommended)

All paths automatically resolve within `agoutic_code/` folder:

```bash
cd agoutic_code

# Directories create automatically
python launchpad/app.py  # Will create data/ folder as needed
```

### Option 2: Custom Storage Location

Point to external storage while keeping code in `agoutic_code/`:

```bash
cd agoutic_code

# Use custom storage
export AGOUTIC_ROOT=/mnt/large_disk/agoutic_data

# Start server
uvicorn launchpad.app:app --port 8001
```

### Option 3: Custom Everything

For development or testing with alternative pipelines:

```bash
cd agoutic_code

export AGOUTIC_ROOT=/tmp/test_data
export DOGME_REPO=/home/user/dogme_dev
export NEXTFLOW_BIN=/home/user/bin/nextflow

uvicorn launchpad.app:app --port 8001
```

## Database Files

SQLite database creates automatically in `data/database/`:

```bash
# View database location
ls -la agoutic_code/data/database/agoutic_v23.sqlite

# Query database
sqlite3 agoutic_code/data/database/agoutic_v23.sqlite

# Backup database
cp agoutic_code/data/database/agoutic_v23.sqlite backup/
```

## Job Work Directories

Jobs create working directories in `data/launchpad_work/`:

```bash
# View all job work directories
ls -la agoutic_code/data/launchpad_work/

# View specific job
ls -la agoutic_code/data/launchpad_work/<run_uuid>/

# Check job outputs
ls agoutic_code/data/launchpad_work/<run_uuid>/results/

# View job logs
cat agoutic_code/data/launchpad_work/<run_uuid>/log.txt
```

## Log Files

Server logs write to `data/launchpad_logs/`:

```bash
# View available log files
ls agoutic_code/data/launchpad_logs/

# Watch logs in real-time
tail -f agoutic_code/data/launchpad_logs/*.log

# Search logs
grep ERROR agoutic_code/data/launchpad_logs/*.log
```

## Path Resolution Examples

### When Environment Variables Are NOT Set

```python
# AGOUTIC_ROOT defaults to:
/path/to/agoutic_code/data/

# DOGME_REPO defaults to:
/path/to/agoutic_code/dogme/

# CODE_ROOT resolves to:
/path/to/agoutic_code/

# SKILLS_DIR resolves to:
/path/to/agoutic_code/skills/
```

### When Environment Variables ARE Set

```bash
export AGOUTIC_ROOT=/external/storage
export DOGME_REPO=/custom/dogme

# Now uses:
# - AGOUTIC_ROOT = /external/storage
# - DOGME_REPO = /custom/dogme
# - CODE_ROOT = /path/to/agoutic_code/ (unchanged)
# - SKILLS_DIR = /path/to/agoutic_code/skills/ (unchanged)
```

## Migration from Old Paths

If you have existing data at old locations, you can migrate:

```bash
# Old location
OLD_DATA=/media/backup_disk/agoutic_root

# New location
NEW_DATA=./agoutic_code/data

# Copy existing data
mkdir -p $NEW_DATA/database
cp $OLD_DATA/database/agoutic_v23.sqlite $NEW_DATA/database/

# Use old location via environment variable
export AGOUTIC_ROOT=$OLD_DATA
```

## Troubleshooting Path Issues

### Path Not Found Error

**Problem:** `FileNotFoundError: agoutic_code/dogme/`

**Solution:** Set environment variable:
```bash
export DOGME_REPO=/actual/path/to/dogme
```

### Wrong Directory Being Used

**Problem:** Data going to unexpected location

**Solution:** Check active environment variables:
```bash
echo $AGOUTIC_ROOT
echo $DOGME_REPO
echo $NEXTFLOW_BIN
```

### Database Not Creating

**Problem:** `sqlite3.OperationalError`

**Solution:** Ensure write permissions:
```bash
mkdir -p agoutic_code/data/database
chmod 755 agoutic_code/data
```

## Summary

| Component | Default Path | Env Variable | Notes |
|-----------|--------------|--------------|-------|
| Storage | `agoutic_code/data/` | `AGOUTIC_ROOT` | Database, logs, job work dirs |
| Database | `agoutic_code/data/database/` | - | Auto-created from AGOUTIC_ROOT |
| Logs | `agoutic_code/data/launchpad_logs/` | - | Auto-created from AGOUTIC_ROOT |
| Jobs | `agoutic_code/data/launchpad_work/` | - | Auto-created from AGOUTIC_ROOT |
| Dogme | `agoutic_code/dogme/` | `DOGME_REPO` | Optional if in default location |
| Nextflow | System PATH | `NEXTFLOW_BIN` | Searches `/usr/local/bin/nextflow` |
| Code | `agoutic_code/` | - | Fixed - where this file is |
| Skills | `agoutic_code/skills/` | - | Fixed - relative to CODE_ROOT |

---

All configurations are **backward compatible** with environment variables and work seamlessly within the `agoutic_code` folder structure.
