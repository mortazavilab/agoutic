# AGOUTIC Quick Reference

A brief reference for path configuration, chat commands, slash commands, and shortcuts.
See [README.md](README.md), [CONFIGURATION.md](CONFIGURATION.md), and
[docs/MEMORY.md](docs/MEMORY.md) for full detail.

## Table of Contents

1. [Path Configuration](#two-root-variables)
2. [Slash Commands](#slash-commands)
3. [Workflow Browsing Commands](#workflow-browsing-commands)
4. [Natural Language Memory](#natural-language-memory)
5. [Dataframe Quick Commands](#dataframe-quick-commands)
6. [Chat Shortcuts](#chat-shortcuts)
7. [Remote Cluster](#remote-cluster-docs)
8. [Documentation](#documentation)

---

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

## Slash Commands

Slash commands currently fall into two buckets:

- **Deterministic quick exits** — skill commands, workflow commands, and all memory commands are handled by the backend without calling the LLM.
- **Planner-routed commands** — `/de` enters the differential-expression planning flow and can still ask follow-up questions when required inputs are missing.

### Skill Commands

| Command | Syntax | Description | Aliases |
|---|---|---|---|
| `/skills` | `/skills` | List all registered skills and show which one is active | `/list-skills` |
| `/skill` | `/skill differential_expression` | Show the manifest-backed description of a skill | `/describe-skill <skill_key>` |
| `/use-skill` | `/use-skill IGVF_Search` | Switch the active skill for subsequent turns | `/switch-skill <skill_key>` |

### Workflow Commands

| Command | Syntax | Description |
|---|---|---|
| `/use` | `/use workflow7` | Switch the active workflow context for follow-up browsing and analysis |
| `/rerun` | `/rerun workflow7` | Rerun the matching workflow/job in the current project |
| `/rename` | `/rename workflow7 treated-r1` | Rename the matching workflow/job |
| `/delete` | `/delete workflow7` | Delete the matching workflow/job |

Workflow references can match a workflow folder, workflow alias, workflow display name, or sample name.

### Differential Expression Slash Command

| Command | Syntax | Description |
|---|---|---|
| `/de` | `/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2` | Run edgePython differential expression from the current workflow abundance context with explicit sample groups |

Use natural-language DE requests when you want to point at a dataframe or counts file, or when you want AGOUTIC to clarify missing groups before running the plan.

### Memory Slash Commands

All memory slash commands are processed by the backend without calling the LLM — zero token cost.

| Command | Syntax | Description |
|---------|--------|--------------|
| `/remember` | `/remember <text>` | Save a project-scoped memory |
| `/remember-global` | `/remember-global <text>` | Save a user-global memory |
| `/remember-df` | `/remember-df DF5` or `/remember-df DF5 as c2c12DF` | Save a conversation dataframe; optional user-given alias |
| `/forget` | `/forget <text>` or `/forget #<id>` | Soft-delete by content match or ID prefix |
| `/memories` | `/memories` or `/memories --global` | List active memories (project + global, or global only) |
| `/pin` | `/pin #<id>` | Pin a memory (surfaced first in `[MEMORY]` context) |
| `/unpin` | `/unpin #<id>` | Unpin a memory |
| `/restore` | `/restore #<id>` | Restore a soft-deleted memory |
| `/annotate` | `/annotate <sample> <key>=<value>...` | Annotate a sample with key/value metadata |
| `/search-memories` | `/search-memories <query>` | Full-text search across memory content |
| `/upgrade-to-global` | `/upgrade-to-global #<id>` | Promote a project-scoped memory to global scope |

Commands with multi-word names also accept underscore variants such as `/remember_global`, `/remember_df`, and `/upgrade_global`.

**Aliases**: `/make-global` and `/upgrade-global` are equivalent to `/upgrade-to-global`.

Memory IDs in list output are 8-character prefixes (e.g. `a1b2c3d4`).
Use them with `#` in pin, forget, restore, and upgrade commands.

See [docs/MEMORY.md](docs/MEMORY.md) for the full architecture reference.

---

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

---

## Natural Language Memory

These phrases are detected before the message reaches the LLM and fire as a
side-effect alongside the normal LLM response (no extra turn needed).

| Pattern | Example | Effect |
|---------|---------|--------|
| `remember [that] ...` | "remember that sample A is from mouse" | Creates a project-scoped memory |
| `note that ...` / `save that ...` | "note that I prefer grouped bar charts" | Creates a project-scoped memory |
| `sample X is [a/an] Y` | "sample C2C12 is a myoblast cell line" | Annotates sample with `condition=Y` |
| `mark/label/tag sample X as Y` | "mark sample KO as treated" | Annotates sample with `condition=Y` |
| `forget the memory about ...` | "forget the memory about the old run" | Soft-deletes by content match |
| `what do you remember about ...?` | "what do you remember about sample B?" | Searches memories and shows results |

---

## Dataframe Quick Commands

These commands bypass the LLM entirely — zero token cost — and return
deterministic output.

| Command | Description |
|---------|-------------|
| `list dfs` | List all dataframes in the conversation (labels, columns, row counts) |
| `head DF5` | Show first 10 rows of conversation dataframe DF5 as a markdown table |
| `head DF5 20` | Show first 20 rows of DF5 |
| `head c2c12DF` | Show first 10 rows of a remembered named dataframe |
| `head c2c12DF 20` | Show first 20 rows of a remembered named dataframe |

**Tip**: Named remembered DFs (saved with `/remember-df DF5 as c2c12DF`) appear
in `list dfs` and can be referenced by name in plot and analysis requests —
the full table is injected into the LLM context automatically.

### Dataframe Actions

These actions operate on existing conversation dataframes in memory and create
new dataframe results in chat.

| Pattern | Example | Effect |
|---------|---------|--------|
| Filter / subset rows | `subset DF3 to rows where reads > 100` | Creates a filtered dataframe |
| Keep columns | `subset DF3 to columns sample, modification, reads` | Keeps only selected columns |
| Rename columns | `rename DF2 columns old_reads to reads and old_sample to sample` | Renames one or more columns |
| Sort | `sort DF4 by reads descending` | Sorts by one or more columns |
| Melt / reshape | `reshape DF1 into long format with columns sample, modification, reads` | Converts wide data to long format |
| Aggregate | `summarize DF4 by sample and sum reads` | Groups and aggregates rows |
| Join | `join DF3 and DF5 on sample` | Joins two dataframes |
| Pivot | `pivot DF6 index sample columns modification values reads` | Converts long data wider |

### Dataframe Plotting

| Prompt | Effect |
|--------|--------|
| `plot DF5 by assay` | Creates an inline plot from DF5 |
| `make a grouped bar chart of DF3 by modification color by sample` | Groups bars by sample |
| `plot this color by sample` | Uses the latest dataframe and can auto-melt wide sample columns |

If AGOUTIC asks for confirmation before applying a dataframe transform, use the
`PENDING_ACTION` block's Apply or Dismiss buttons in chat.

See [docs/DATAFRAMES.md](docs/DATAFRAMES.md) for a fuller guide.

---

## Chat Shortcuts

| Input | Description |
|-------|-------------|
| `try again` | Re-sends the last failed prompt (HTTP error or plan failure). Works repeatedly; clears on success. |
| `retry` | Same as `try again`. |

---

## Remote Cluster Docs

For AGOUTIC's remote SLURM workflow, use these references:

- **Setup guide:** [docs/cluster_slurm_setup.md](docs/cluster_slurm_setup.md)
- **Architecture:** [docs/remote_execution_architecture.md](docs/remote_execution_architecture.md)
- **Execution modes:** [docs/user_guide_execution_modes.md](docs/user_guide_execution_modes.md)
- **Troubleshooting:** [docs/troubleshooting_remote.md](docs/troubleshooting_remote.md)

Common remote prompts:

| Command | What it does |
|---|---|
| `list files on localCluster` | Lists the saved profile's `remote_base_path` |
| `list files in data on localCluster` | Lists `{remote_base_path}/data` |
| `stage sample sampleA to localCluster` | Stages references and input data without submitting a job |

### Remote SLURM Runtime Bootstrap (Portable)

Generated SLURM scripts now include a cluster-agnostic runtime bootstrap:

- Attempts module-based setup when available (`java`, `singularity`, `apptainer`)
- Verifies Java exists before running Nextflow
- Uses a local `singularity` shim when only `apptainer` exists
- Sets `NXF_SINGULARITY_CACHEDIR` under the workflow directory

If a remote run fails before task execution, inspect:

```bash
tail -n 200 slurm-<jobid>.out
grep -nEi "error|exception|caused by|singularity|apptainer|denied|timeout" .nextflow.log | tail -n 120
```

## Documentation

- 📖 **[CONFIGURATION.md](CONFIGURATION.md)** - Complete configuration guide
- 🧠 **[docs/MEMORY.md](docs/MEMORY.md)** - Memory system architecture
- 📋 **[BEFORE_AFTER.md](BEFORE_AFTER.md)** - Changes explained
- 📝 **[REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md)** - Technical summary

---

**That's it! Everything works with sensible defaults. Override only if needed.**
