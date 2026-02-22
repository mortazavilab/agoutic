# Quick Reference - AGOUTIC Path Configuration

## Two Root Variables

```bash
# Where source code lives
export AGOUTIC_CODE=/path/to/agoutic

# Where data/database/jobs live
export AGOUTIC_DATA=/path/to/storage
```

**No environment variables needed - defaults work automatically!**

## All Paths Derived From These

```
AGOUTIC_CODE/
├── skills/              → SKILLS_DIR
├── cortex/
├── launchpad/
└── dogme/               → DOGME_REPO

AGOUTIC_DATA/
├── database/
│   └── agoutic_v23.sqlite    → DB_FILE
├── launchpad_work/             → LAUNCHPAD_WORK_DIR (legacy flat jobs)
├── launchpad_logs/             → LAUNCHPAD_LOGS_DIR
└── users/                    → Per-user project directories
    └── {username}/
        └── {project-slug}/
            ├── data/             # Uploaded input data
            ├── workflow1/        # First job output
            │   ├── annot/        # Annotations, stats, counts
            │   ├── bams/         # BAM alignment files
            │   ├── bedMethyl/    # Methylation output
            │   └── fastqs/       # FASTQ files
            └── workflow2/        # Second job (auto-incremented)
```

## Configuration Scenarios

### Default (No Variables)
```bash
cd /Users/eli/code/agoutic
uvicorn launchpad.app:app --port 8001
# Uses: code=/Users/eli/code/agoutic, data=/Users/eli/code/agoutic/data
```

### Custom Storage
```bash
export AGOUTIC_CODE=/fast-ssd/agoutic
export AGOUTIC_DATA=/large-storage/data
uvicorn launchpad.app:app --port 8001
```

### Docker
```bash
docker run \
  -e AGOUTIC_CODE=/app/agoutic \
  -e AGOUTIC_DATA=/mnt/data \
  -v /host/storage:/mnt/data \
  agoutic-server
```

## Check Current Configuration

```bash
python -c "
from launchpad.config import AGOUTIC_CODE, AGOUTIC_DATA
print(f'Code: {AGOUTIC_CODE}')
print(f'Data: {AGOUTIC_DATA}')
"
```

## Set Custom Location

```bash
# In shell
export AGOUTIC_CODE=/path/to/code
export AGOUTIC_DATA=/path/to/data

# Or as environment variable when running
AGOUTIC_CODE=/path/to/code AGOUTIC_DATA=/path/to/data uvicorn launchpad.app:app
```

## Environment Variables

| Variable | Default | Example |
|----------|---------|---------|
| `AGOUTIC_CODE` | Repository dir | `/fast-ssd/agoutic` |
| `AGOUTIC_DATA` | `$AGOUTIC_CODE/data` | `/large-storage/data` |
| `DOGME_REPO` | `$AGOUTIC_CODE/dogme` | `/custom/dogme` |
| `NEXTFLOW_BIN` | `/usr/local/bin/nextflow` | `/usr/bin/nextflow` |

## Common Tasks

### Use Different Storage for Jobs
```bash
export AGOUTIC_DATA=/mnt/fast-storage/agoutic
# Now all jobs go to /mnt/fast-storage/agoutic/launchpad_work/
```

### Keep Code on SSD, Data on HDD
```bash
export AGOUTIC_CODE=/ssd/agoutic              # Source code
export AGOUTIC_DATA=/hdd/storage/agoutic      # Data files
```

### Deploy to Docker
```bash
docker run \
  -e AGOUTIC_CODE=/app/agoutic \
  -e AGOUTIC_DATA=/mnt/data/agoutic \
  -v /host/data:/mnt/data/agoutic \
  agoutic-server
```

### Deploy to Production
```bash
export AGOUTIC_CODE=/opt/agoutic
export AGOUTIC_DATA=/srv/data/agoutic
uvicorn launchpad.app:app --workers 4 --port 8001
```

## Verification

```bash
# Check all files compile
python -m py_compile launchpad/*.py cortex/*.py

# Check paths resolve
python -c "from launchpad.config import AGOUTIC_CODE, AGOUTIC_DATA; print(f'{AGOUTIC_CODE} / {AGOUTIC_DATA}')"

# Check directories can be created
mkdir -p $AGOUTIC_DATA/database $AGOUTIC_DATA/launchpad_work $AGOUTIC_DATA/launchpad_logs
```

## Workflow Browsing Commands

Once a Dogme job completes, you can browse and parse result files via the chat interface:

| Command | What it does |
|---|---|
| `list workflows` | Lists all workflow folders in the project |
| `list files` | Lists files in the current (most recent) workflow |
| `list files in annot` | Lists files in a subfolder — tries workflow first, then project root |
| `list files in data` | Lists `data/` — cascades from workflow to project root |
| `list files in workflow2/annot` | Lists files in a specific workflow's subfolder |
| `list project files` | Lists top-level project directory contents |
| `list project files in data` | Lists `data/` — always relative to project root |
| `parse annot/File.csv` | Finds and parses a CSV file by relative path |
| `parse workflow2/annot/File.csv` | Parses a file in a specific workflow |

These commands are handled by Cortex's safety net (`_auto_generate_data_calls`) which generates `list_job_files`, `find_file`, and `parse_csv_file` Analyzer MCP tool calls automatically.

**Tip:** Use `list project files` to always target the project root. If `list files in <folder>` can't find the folder, it shows suggestions for alternative commands.

## Documentation

- 📖 **[CONFIGURATION.md](CONFIGURATION.md)** - Complete guide
- 📋 **[BEFORE_AFTER.md](BEFORE_AFTER.md)** - Changes explained
- 📝 **[REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md)** - Technical summary

---

**That's it! Everything works with sensible defaults. Override only if needed.**
