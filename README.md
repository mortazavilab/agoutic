# AGOUTIC: Automated Genomic Orchestrator

**Version:** 3.4.2
**Status:** Active Prototype 

## Latest Updates (2026-03-18)

- **Remote SLURM config-path correctness** — generated `nextflow.config` for
  remote submissions now prioritizes staged remote reference cache paths in
  `genome_annot_refs`, avoiding local host path leakage in cluster runs.
- **Remote workflow input linking** — submissions now ensure workflow-local
  `pod5`/`bams`/`fastqs` links map to staged remote input cache paths expected
  by Dogme.
- **Correct remote pipeline target** — SLURM submit scripts now run
  `nextflow run mortazavilab/dogme`.
- **Portable cluster runtime bootstrap** — generated sbatch scripts now attempt
  module-based Java/container runtime setup where available and provide
  `apptainer` compatibility for singularity-based Nextflow configs.
- **Actionable failure diagnostics in SLURM logs** — failed jobs now include a
  focused `.nextflow.log` error scan plus head/tail snippets in job output.
- **Expanded Launchpad debug visibility** — `/jobs/{run_uuid}/debug` now
  surfaces discovered `slurm-*.out/.err` previews and focused Nextflow log
  context for failed remote jobs.

## 🧬 Overview

AGOUTIC is a general-purpose agent for analyzing and interpreting long-read genomic data (Nanopore/PacBio). It uses a **Dual Interface** architecture (REST + MCP) to allow both human users and AI agents to orchestrate complex bioinformatics pipelines.

The system is composed of:
- **Cortex**: Agent Engine - AI-powered orchestration and user interaction
- **Atlas**: ENCODE Integration - Public data retrieval via ENCODELIB
- **Launchpad**: Execution Engine - Dogme/Nextflow pipeline management (local + remote SLURM)
- **Analyzer**: Analysis Engine - Results analysis and QC reporting
- **edgePython**: Differential Expression — Bulk/single-cell RNA-seq DE via edgePython
- **UI**: Web interface for monitoring and control

Current status: database infrastructure centralized in `common/database.py`
with Alembic migrations. Gene annotation and enrichment tools moved from
edgePython to Analyzer. The analyzer/server4 adapter layer proxies remaining
edgePython MCP calls upstream.

## 🖥️ Execution Modes

AGOUTIC supports **dual execution modes**:

- **Local**: Runs Nextflow/Dogme pipelines directly on the local machine (default, original behavior)
- **Remote SLURM**: Submits jobs to a remote SLURM cluster via SSH

Remote execution features:
- **Saved SSH profiles** — per-user connection profiles with secure key references (no raw secrets stored). Supports local OS user key access through a per-session broker launched under that Unix account with `su` (password used transiently, never stored)
- **SLURM resource management** — configurable account, partition, CPUs, memory, walltime, GPUs with validation
- **Remote base path model** — a single `remote_base_path` anchors `ref/`, `data/`, and per-workflow remote directories
- **Remote browsing and stage-only intake** — browse saved-cluster paths and stage references/input data without submitting a job
- **Result destination policy** — keep results remote-only, copy back locally, or both
- **Staged approval prompts** — Cortex collects details progressively, presents summary before submission
- **Run and staging status tracking** — dedicated staging tasks plus remote execution stage labels through `completed`
- **Scheduler integration** — SLURM job ID tracking, state polling via sacct/squeue, cancellation via scancel

Phase 1 limitation: Analyzer operates on local-accessible files only. Remote results must be copied back before downstream analysis.

See [`docs/remote_execution_architecture.md`](docs/remote_execution_architecture.md) for architecture details, [`docs/cluster_slurm_setup.md`](docs/cluster_slurm_setup.md) for setup, and [`docs/user_guide_execution_modes.md`](docs/user_guide_execution_modes.md) for usage.

## 🔒 Security & Multi-User Isolation

AGOUTIC enforces access control at every layer:

- **Authentication**: Google OAuth 2.0 with session cookies (`httponly`, `samesite=lax`, `secure` in production)
- **Authorization**: Role-based access (owner / editor / viewer) checked on every endpoint via `require_project_access()`. Admins bypass all project-level checks; public projects allow viewer access.
- **Job ownership**: Each job records the submitting `user_id`. `require_run_uuid_access()` verifies ownership before exposing debug info or analysis results.
- **File isolation**: User-jailed paths (`AGOUTIC_DATA/users/{username}/{project-slug}/`) with input sanitization and jail-escape guards; legacy `{user_id}/{project_id}` paths are still supported for backward compatibility.
- **Server-side project IDs**: UUIDs generated server-side via `uuid4()` — clients never control the ID.
- **Project management**: Full dashboard for browsing projects, viewing stats/files/jobs, renaming, archiving, and permanent deletion with cascading cleanup.
- **Bootstrap & admin scripts**: Run `python scripts/cortex/init_db.py` for a fresh database bootstrap, `python scripts/cortex/set_usernames.py auto` to derive usernames from email addresses on an existing instance, and `python scripts/cortex/bootstrap_project_tasks.py` to seed persistent project tasks from existing workflow history.

## 🚀 Quick Start

### Installation

```bash
# Create environment
conda env create -f environment.yml
conda activate agoutic_core
```

### Run the System

```bash
# Recommended: start the full backend stack
./agoutic_servers.sh --start

# Then start the UI separately
streamlit run ui/app.py --server.address 0.0.0.0 --server.port 8501
```

For local development, you can still run services manually:

```bash
# Terminal 1: Start Launchpad REST
uvicorn launchpad.app:app --host 0.0.0.0 --port 8003 --reload

# Terminal 2: Start Launchpad MCP
python -m launchpad.mcp_server --host 0.0.0.0 --port 8002

# Terminal 3: Start Cortex
uvicorn cortex.app:app --host 0.0.0.0 --port 8000 --reload

# Terminal 4: Start UI
cd ui && streamlit run app.py
```

Note: running `python ui/app.py` directly will not work correctly because the UI
auth flow depends on Streamlit request context and browser cookies.

### Verify Installation

```bash
# Check Cortex health
curl http://localhost:8000/health

# Check Launchpad health
curl http://localhost:8003/health

# Test Atlas connection
python cortex/atlas_mcp_client.py

# Expected: Connection success and K562 search results
```

## Task System

AGOUTIC now maintains a persistent project task list instead of relying on a
hard-coded checklist in the UI.

- Tasks are projected from durable workflow records, mainly `ProjectBlock`
  state plus job progress payloads.
- The chat page groups tasks into pending, running, follow-up, and completed
  sections.
- Parent tasks can include child tasks for workflow stages, per-file download
  progress, analysis completion, and result review.
- Existing history can be backfilled safely with:

```bash
python scripts/cortex/bootstrap_project_tasks.py

# Optional: seed only one project
python scripts/cortex/bootstrap_project_tasks.py --project-id <project_id>
```

## 📋 Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                   AGOUTIC System v3.3.2                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐                                               │
│  │  Web UI  │ (Streamlit)                                   │
│  └────┬─────┘                                               │
│       │ REST API                                            │
│       ↓                                                      │
│  ┌────────────────────────────────────────┐                │
│  │        Cortex (Agent Engine)         │                │
│  │     AI Orchestration + Coordination    │                │
│  └────┬────────────┬────────────┬─────────┘                │
│       │            │            │                           │
│       │ MCP        │ REST       │ MCP                       │
│       ↓            ↓            ↓                           │
│  ┌────────┐  ┌──────────┐  ┌──────────┐                   │
│  │Atlas│  │ Launchpad │  │ Analyzer │                   │
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

### Cortex: Agent Engine (Port 8000)
- **Role:** Central orchestrator with LLM reasoning
- **Tech:** FastAPI + OpenAI-compatible LLM
- **Features:**
  - Chat interface with skill-based workflows
  - Coordinates Atlas, Launchpad, and Analyzer
  - Block-based project timeline
  - Persistent project task list with child-task projection for downloads,
    workflow stages, analysis, and follow-up review
  - Background job monitoring with **stop/cancel** buttons
  - **Download cancel button** — "🛑 Cancel Download" on running downloads with partial-file cleanup
  - **"List my data" command** — chat-based central data folder listing (DB + disk fallback)
  - **Post-cancel workflow management** — Delete / Resubmit buttons on cancelled jobs; chat-based deletion via natural language
  - User authentication
  - Role-based authorization gates on all endpoints
  - Server-side project CRUD (`POST/GET/PATCH /projects`)
  - `[[PLOT:...]]` tag parsing → `AGENT_PLOT` blocks for inline Plotly charts (histogram, scatter, bar, box, heatmap, pie)
  - **Per-message and per-conversation token tracking** — every LLM response records `prompt_tokens`, `completion_tokens`, `total_tokens`, and `model_name` in the database; exposed via `GET /user/token-usage` (own data) and `GET /admin/token-usage` (all users)
  - **`find_file` echo recovery** — when a weak model emits a `find_file` JSON result verbatim instead of a `[[DATA_CALL:...]]` tag, the pipeline intercepts the response, auto-chains to `parse_csv_file`/`parse_bed_file`/`read_file_content`, and strips the bad block from conversation history to prevent looping
  - **ENCODE tool routing guards** — structural checks prevent LLM misrouting (e.g. cell-line names sent to `get_experiment`); assay-only queries are routed to `search_by_assay`; assay name aliases (e.g. `RNA-seq` → `total RNA-seq`, `ChIP-seq` → `TF ChIP-seq`) are resolved before MCP dispatch
  - **Tool Schema Contracts** — machine-readable JSON Schema for every MCP tool, fetched at startup from `/tools/schema` endpoints on all servers. Injected into the system prompt as a compact reference and used for pre-call param validation (strip unknown params, check required fields, normalise enums).
  - **Structured Conversation State** — typed `ConversationState` JSON (skill, project, sample, experiment, dataframes, workflows) built each turn and injected as `[STATE]...[/STATE]` so the LLM always sees current context
  - **Error-Handling Playbook** — deterministic failure rules in the system prompt + structured `[TOOL_ERROR]` blocks + single-retry for transient failures
  - **Output Contract Validator** — post-LLM validation catches malformed `DATA_CALL` tags, duplicate `APPROVAL_NEEDED`, unknown tools, and mixed sources
  - **Provenance Tags** — `[TOOL_RESULT: source, tool, params, rows, timestamp]` headers on every tool result for auditability; persisted in AGENT_PLAN blocks
  - **Plan-Execute-Observe-Replan** — structured multi-step planning layer that decomposes complex requests into deterministic execution plans with dependency tracking, approval gates, and automatic replanning on failure. 9 plan templates covering: run workflow, compare samples, download+analyze, summarize results, run DE pipeline, run enrichment, parse+plot+interpret, compare workflows, and search+compare to local data. CHECK_EXISTING guards skip expensive operations when results already exist.
  - **Gene Annotation & ID Translation** — offline Ensembl gene ID ↔ symbol translation (human + mouse) via pre-built lookup tables. Auto-annotates gene symbols when DE data is loaded; all downstream outputs (top genes, heatmaps, summaries) automatically use readable symbols instead of raw Ensembl IDs. Bidirectional `lookup_gene` tool answers "what is the Ensembl ID for TP53?" style queries. Pre-LLM auto-skill detection routes gene questions to the correct skill from any context (including Welcome). MCP tools: `annotate_genes` (edgePython, DE-stateful), `translate_gene_ids` and `lookup_gene` (Analyzer).
  - **Robust DATA_CALL tag parsing** — bracket-aware parameter parser handles JSON arrays inside DATA_CALL tags (e.g. `gene_symbols=["TP53", "BRCA1"]`). Mistral-native `[TOOL_CALLS]DATA_CALL:` format is auto-normalized to standard `[[DATA_CALL:...]]` tags.
  - **Skill-defined plan chains** — skill authors can declare multi-step workflows in skill Markdown files under a `## Plan Chains` section. A single message like "get K562 experiments and make a plot by assay type" is detected at classify-time and produces both a data search and a visualization. Trigger phrases support multi-phrasing (AND/OR keyword groups) for flexible matching. See `SKILLS.md` for the full authoring guide.
  - **Skills system documentation** — new top-level `SKILLS.md` documents the complete skills framework: skill file structure, routing patterns, `[[DATA_CALL:...]]` / `[[PLOT:...]]` tag system, plan chains format, and a step-by-step guide for creating new skills.
  - **Inline Plotly visualizations with deduplication** — `[[PLOT:...]]` tags produce interactive bar, scatter, pie, histogram, box, and heatmap charts rendered directly in chat. A three-layer pipeline guarantees chart generation: chain context injection → second-pass PLOT tag instructions → post-DataFrame fallback. Deduplication and prompt-intent selection prevent duplicate/overlapping traces and stale style leakage across turns. Supports explicit colors (`color=green`) and grouping palettes.
  - **DF inspection quick commands** — `list dfs` lists all dataframes in the conversation with their metadata; `head df1` (or `head df3 5`) shows the first N rows as a markdown table. Both bypass the LLM entirely — zero token cost.

### Atlas: ENCODELIB (Port 8006 MCP)
- **Role:** ENCODE Portal data retrieval
- **Tech:** fastmcp + ENCODE API, extended via `atlas/mcp_server.py`
- **Features:**
  - Search experiments by biosample/organism/target
  - **`search_by_assay`** — assay-first search (e.g. *"how many RNA-seq experiments"*) across both organisms, returning combined counts and per-organism lists
  - Download experiment files
  - Metadata caching
  - 15+ MCP tools for data access
  - Agent routing guards in Cortex prevent structural misrouting (e.g. cell-line names sent to `get_experiment`)
- **Extension pattern:** `atlas/mcp_server.py` imports ENCODELIB's FastMCP `server` instance and registers additional tools on it. `launch_encode.py` imports from this module so extensions are available without modifying ENCODELIB.
- **Tool schemas:** `atlas/tool_schemas.py` defines JSON Schema contracts for all 16 ENCODE tools, served via `/tools/schema` GET endpoint.
- **Docs:** [ATLAS_IMPLEMENTATION.md](ATLAS_IMPLEMENTATION.md)

### Launchpad: Execution Engine (Ports 8003 REST / 8002 MCP)
- **Role:** Nextflow pipeline execution
- **Tech:** FastAPI + Nextflow + Dogme
- **Features:**
  - Submit Dogme DNA/RNA/cDNA pipelines
  - Real-time job monitoring
  - Log streaming
  - User-jailed working directories
  - **Job cancellation** — SIGTERM-based cancel with cooperative `.nextflow_cancelled` marker; properly displays CANCELLED (not FAILED) in UI
  - **Workflow folder deletion** — DELETE endpoint removes work directory and sets status to DELETED; block status updated immediately so UI reflects deletion
  - **Job resume** — resubmit cancelled/failed jobs with Nextflow `-resume` flag to reuse cached task results in the same workflow directory instead of starting fresh
  - **`delete_job_data` MCP tool** — enables chat-based deletion ("delete workflow1")
- **Docs:** [launchpad/README.md](launchpad/README.md)

### edgePython: Differential Expression (Port 8007)
- **Role:** Bulk and single-cell RNA-seq differential expression analysis
- **Tech:** FastMCP + edgePython
- **Features:**
  - Full DE pipeline: load → filter → normalize → design → dispersion → fit → test → results → plots
  - Gene list filtering from DE results by FDR, logFC, and direction (up/down/all)
  - Gene annotation (annotate_genes) on DE results in-place
  - Stateful pipeline — each step builds on previous results within a session
  - Volcano, MDS, MA, BCV, heatmap plot generation
  - TSV/CSV/JSON result export
  - JSON Schema tool contracts via `/tools/schema`
- **Docs:** [edgepython_mcp/](edgepython_mcp/)

### Analyzer: Analysis Engine (Ports 8004 REST / 8005 MCP)
- **Role:** Results analysis, QC reporting, gene annotation, and GO/pathway enrichment
- **Tech:** fastmcp + Python analysis tools + g:Profiler
- **Features:**
  - Parse pipeline outputs (CSV, TSV, BED files)
  - Generate QC reports and analysis summaries
  - File discovery and content reading
  - Workflow folder browsing via `list_job_files`
  - Gene ID translation (`translate_gene_ids`) and bidirectional lookup (`lookup_gene`) via Ensembl reference tables
  - GO enrichment (BP/MF/CC) and pathway enrichment (KEGG/Reactome) via g:Profiler
  - Per-conversation enrichment state management
  - Species auto-detection from gene ID prefixes (ENSG → human, ENSMUSG → mouse)
- **Workflow Directory Layout:**
  ```
  $AGOUTIC_DATA/users/{username}/{project-slug}/
  ├── data/              # Uploaded input data
  ├── workflow1/         # First job's output
  │   ├── annot/         # Annotations, final stats, counts
  │   ├── bams/          # BAM alignment files
  │   ├── bedMethyl/     # Methylation BED output
  │   ├── fastqs/        # FASTQ files
  │   └── ...
  └── workflow2/         # Second job's output
  ```
- **Agent Commands** (handled automatically by Cortex's safety net):
  - `list my data` / `list my files` — lists all files in your central data folder
  - `list workflows` — lists all workflow folders in the project
  - `list files` / `list files in workflow2/annot` — lists files in a workflow or subfolder
  - `parse annot/File.csv` — finds and parses a file by relative path
  - `parse workflow2/annot/File.csv` — parses a file in a specific workflow
- **Docs:** [analyzer/README.md](analyzer/README.md)

## 📁 Project Structure

```
agoutic/
├── README.md                     # This file
├── environment.yml               # Conda environment specification
├── alembic.ini                  # Alembic migration configuration
├── CONFIGURATION.md              # Path configuration guide
├── ARCHITECTURE_UPDATE.md        # System architecture (all servers)
├── ATLAS_IMPLEMENTATION.md     # Atlas integration guide
├── ATLAS_QUICKSTART.md         # Atlas quick reference
│
├── cortex/                      # Agent Engine
│   ├── README.md                # Cortex documentation
│   ├── app.py                   # FastAPI application
│   ├── agent_engine.py          # AI agent orchestration
│   ├── planner.py               # Request classifier + plan templates
│   ├── plan_executor.py         # Deterministic step execution engine
│   ├── plan_replanner.py        # Failure recovery + plan adjustment
│   ├── dependencies.py          # Auth gates (require_project_access, require_run_uuid_access)
│   ├── user_jail.py             # Path traversal guards & file isolation
│   ├── auth.py                  # Google OAuth 2.0 + cookie hardening
│   ├── models.py                # Database models
│   ├── schemas.py               # Request/response schemas
│   ├── config.py                # Configuration
│   ├── db.py                    # Database connection
│   ├── prompt_templates/        # LLM system prompts (first-pass, planning, second-pass)
│   └── routes/                  # Extracted REST route modules
│
├── launchpad/                      # Execution Engine
│   ├── README.md                # Launchpad documentation
│   ├── app.py                   # FastAPI application
│   ├── nextflow_executor.py    # Nextflow wrapper
│   ├── mcp_tools.py            # MCP tool definitions
│   ├── mcp_server.py           # MCP server
│   ├── models.py               # Database models
│   ├── schemas.py              # Request/response schemas
│   ├── config.py               # Configuration
│   ├── db.py                   # Database connection
│   ├── quickstart.sh           # Quick start setup
│   ├── DUAL_INTERFACE.md       # REST + MCP architecture
│   └── IMPLEMENTATION_SUMMARY.md # Implementation details
│
├── scripts/                     # Manual admin and operational utilities
│   ├── cortex/
│   │   ├── init_db.py           # Fresh database bootstrap utility
│   │   ├── set_usernames.py     # Username/slug admin CLI
│   │   └── bootstrap_project_tasks.py # Backfill persistent project tasks
│   ├── launchpad/
│   │   ├── debug_job.py         # Job inspection helper
│   │   └── submit_real_job.py   # Manual job submission helper
│   └── build_gene_reference.py  # One-time Gencode GTF → TSV builder
│
├── ui/                          # Web Interface
│   ├── README.md               # UI documentation
│   ├── app.py                  # Streamlit main app (chat, sidebar, auto-refresh)
│   └── pages/
│       ├── projects.py         # Projects dashboard (stats, files, bulk actions)
│       ├── results.py          # Job results analysis (auto-lists project jobs)
│       └── admin.py            # Admin user management
│
├── atlas/                      # ENCODE MCP Extension
│   ├── launch_encode.py        # HTTP launcher (imports mcp_server for extensions)
│   ├── mcp_server.py           # Extends ENCODELIB FastMCP with search_by_assay + /tools/schema
│   ├── tool_schemas.py         # JSON Schema contracts for all 16 ENCODE tools
│   ├── config.py               # Atlas configuration
│   └── result_formatter.py     # Result formatting helpers
│
├── edgepython_mcp/              # edgePython DE Server
│   ├── edgepython_server.py    # FastMCP tool definitions (DE + filtering)
│   ├── mcp_server.py           # Server wrapper + /tools/schema endpoint
│   ├── launch_edgepython.py    # HTTP launcher
│   ├── tool_schemas.py         # JSON Schema contracts for DE tools
│   └── config.py               # Configuration
│
├── common/                      # Shared Utilities
│   ├── database.py            # Centralized DB infrastructure (Base, engines, sessions)
│   ├── gene_annotation.py      # Ensembl gene ID ↔ symbol translation (bidirectional)
│   ├── mcp_client.py           # Shared MCP HTTP client
│   ├── logging_config.py       # Structured logging setup
│   └── logging_middleware.py   # Request logging middleware
│
├── skills/                      # Workflow Definitions
│   ├── Dogme_DNA.md            # DNA pipeline definition
│   ├── Dogme_RNA.md            # RNA pipeline definition
│   ├── Dogme_cDNA.md           # cDNA pipeline definition
│   ├── Differential_Expression.md # edgePython DE skill
│   ├── Enrichment_Analysis.md  # GO & pathway enrichment skill
│   ├── ENCODE_LongRead.md      # ENCODE pipeline definition
│   ├── ENCODE_Search.md        # ENCODE search skill + routing rules
│   ├── Local_Sample_Intake.md  # Sample intake workflow
│   ├── Analyze_Job_Results.md  # Post-pipeline results analysis
│   ├── Download_Files.md       # File download workflow
│   ├── Welcome.md              # New-user onboarding
│   ├── SKILL_ROUTING_PATTERN.md  # Skill routing reference
│   └── DOGME_QUICK_WORKFLOW_GUIDE.md # Quick pipeline reference
│
└── data/                        # Data & Database (created at runtime)
    ├── database/
    │   └── agoutic_v24.sqlite
    ├── reference/               # Gene annotation reference files
    │   ├── human_genes.tsv
    │   └── mouse_genes.tsv
    ├── launchpad_work/            # Job execution directories
    ├── launchpad_logs/            # Server logs
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

See [launchpad/DUAL_INTERFACE.md](launchpad/DUAL_INTERFACE.md) for detailed architecture.

### Job Submission Workflow

```
User Request
    ↓
Cortex (Agent)
  - Interprets intent
  - Plans workflow
  - (Optional) Requests approval
    ↓
Launchpad (Executor)
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

**No required environment variables for default local setup.** Defaults work automatically, but `AGOUTIC_CODE` and `AGOUTIC_DATA` can be overridden if needed. See [CONFIGURATION.md](CONFIGURATION.md) for detailed configuration options.

### Path Resolution

```
AGOUTIC_CODE/
├── cortex/          # Agent engine
├── launchpad/          # Execution engine
├── ui/               # Web interface
└── skills/           # Workflow definitions

AGOUTIC_DATA/
├── database/         # SQLite database
├── launchpad_work/     # Job working directories
├── launchpad_logs/     # Server logs
├── logs/             # Structured logs (all servers)
└── users/            # Per-user jailed project dirs
    └── {username}/   # e.g. eli/
        └── {project-slug}/  # e.g. k562-atac-seq/
            ├── data/
            ├── results/
            └── workflow1/
```

## � Logging & Observability

AGOUTIC uses **structlog** for unified structured logging across all servers. Every log entry is a JSON object written to both per-server and unified log files.

### Log Files

Logs are written to `$AGOUTIC_DATA/logs/`:

```
$AGOUTIC_DATA/logs/
├── agoutic.jsonl          # Unified log (all servers)
├── cortex.jsonl          # Cortex only
├── launchpad-rest.jsonl     # Launchpad REST API
├── launchpad-mcp.jsonl      # Launchpad MCP
├── analyzer-rest.jsonl     # Analyzer REST API
├── analyzer-mcp.jsonl      # Analyzer MCP
├── encode-mcp.jsonl       # ENCODE MCP server
├── *.log                  # Raw stdout/stderr (safety net)
└── *.YYYYMMDD_HHMMSS.*   # Rotated previous logs
```

### Reading Logs

```bash
# Stream the unified log
tail -f $AGOUTIC_DATA/logs/agoutic.jsonl | jq .

# Filter by server
cat $AGOUTIC_DATA/logs/agoutic.jsonl | jq 'select(.server == "cortex")'

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

When servers are started or restarted via `agoutic_servers.sh`, existing log files are automatically renamed with a timestamp (e.g., `cortex.20260213_143052.jsonl`). Empty log files are skipped.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AGOUTIC_LOG_FORMAT` | `json` | Set to `dev` for coloured human-readable console output |

## �🚀 Running the System

### Development Mode

```bash
# Recommended for a full local stack
./agoutic_servers.sh --start

# Start the UI separately
streamlit run ui/app.py --server.port 8501
```

If you need manual development startup:

```bash
# Terminal 1: Start Launchpad
cd /path/to/agoutic
uvicorn launchpad.app:app --port 8003 --reload

# Terminal 2: Start Launchpad MCP
python -m launchpad.mcp_server --host 0.0.0.0 --port 8002

# Terminal 3: Start Cortex
uvicorn cortex.app:app --port 8000 --reload

# Terminal 4: Start UI (if using Streamlit)
cd ui && streamlit run app.py
```

### Using MCP Interface

```python
from cortex.mcp_client import LaunchpadMCPClient

# Connect to MCP server
client = LaunchpadMCPClient()
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
curl -X POST http://localhost:8003/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_001",
    "sample_name": "liver_dna",
    "mode": "DNA",
    "input_directory": "/data/pod5"
  }'

# Check status
curl http://localhost:8003/jobs/{run_uuid}/status

# Get results
curl http://localhost:8003/jobs/{run_uuid}
```

## 📚 Documentation

### Component Documentation
- [cortex/README.md](cortex/README.md) - Agent Engine documentation
- [launchpad/README.md](launchpad/README.md) - Execution Engine documentation  
- [ui/README.md](ui/README.md) - Web UI documentation

### Configuration & Setup
- [CONFIGURATION.md](CONFIGURATION.md) - Full configuration guide
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Path configuration quick start

### Architecture & Details
- [launchpad/DUAL_INTERFACE.md](launchpad/DUAL_INTERFACE.md) - REST + MCP architecture
- [launchpad/IMPLEMENTATION_SUMMARY.md](launchpad/IMPLEMENTATION_SUMMARY.md) - Implementation details

## 🧪 Testing

### Run All Tests

The project has **1068 tests** providing comprehensive coverage.

```bash
# Run the full test suite (1068 tests)
pytest tests/ -q

# Cortex tests only
pytest tests/cortex/ -q

# With coverage report
pytest tests/cortex/ --cov=cortex/app --cov-report=term-missing

# Other components
pytest tests/atlas/ tests/common/ tests/analyzer/ tests/launchpad/ tests/ui/ -q

# Single test file
pytest tests/cortex/test_chat_data_calls.py -x -q

# Focused task lifecycle and hierarchy coverage
pytest tests/cortex/test_project_endpoints.py -q
```

### Test Architecture

- **In-memory SQLite** with `StaticPool` for fast, isolated tests
- **Mocked LLM** via `AgentEngine` patches (no real model calls)
- **Mocked MCP** via `MCPHttpClient` patches (no real service connections)
- **37 cortex test files** covering: chat endpoint, approval gates, background tasks, project management, block endpoints, conversations, auth, admin, downloads, uploads, pure helpers, tool routing, skill detection, validation, planning
- **Task coverage** includes persistent task projection, task actions,
  download-file children, and workflow-stage children in
  `tests/cortex/test_project_endpoints.py`
- **Shared fixtures** in `tests/conftest.py` for DB engine, sessions, mock users

### Bootstrap Existing History

If you deploy this release onto a server that already has projects and stored
workflow blocks, run the task bootstrap once after the application starts:

```bash
python scripts/cortex/bootstrap_project_tasks.py
```

The script is idempotent: it reconciles the current workflow state and is safe
to re-run.

### Run Demo

```bash
# Interactive demo for Launchpad
python launchpad/demo_launchpad.py
```

## 🐛 Troubleshooting

### Server Won't Start
- Check port availability: `lsof -i :8003` (Launchpad REST), `lsof -i :8002` (Launchpad MCP), or `lsof -i :8000` (Cortex)
- Check database connectivity: `python -c "from launchpad.db import SessionLocal; SessionLocal()"`
- Check Python version: `python --version` (requires 3.12+)

### Job Stuck in RUNNING
- Check Nextflow process: `ps aux | grep nextflow`
- Check logs: `tail -f $AGOUTIC_DATA/launchpad_logs/*.log`
- Cancel job: `curl -X POST http://localhost:8003/jobs/{run_uuid}/cancel`

### Configuration Issues
- Verify configuration: `python -c "from launchpad.config import *; print(f'Code: {AGOUTIC_CODE}')"` 
- Check paths: `ls -la $AGOUTIC_DATA/launchpad_work`

## 📊 Performance

### GPU Concurrency (Per-Pipeline)

Control the number of simultaneous GPU tasks (dorado basecalling, openChromatin) within a single pipeline run. Configurable in the approval form or via environment variable:

```bash
export DEFAULT_MAX_GPU_TASKS=1  # Default: 1 (safe for single-GPU). Range: 1-8
```

Users can also override per-job in the approval form dropdown or via chat ("limit dorado to 3 concurrent tasks").

### Concurrent Jobs

Limit concurrent jobs to avoid resource exhaustion:

```bash
export MAX_CONCURRENT_JOBS=2  # Adjust based on server capacity
```

### Database Selection

- **Development**: SQLite (default, no setup required — `create_all()` at startup)
- **Production**: PostgreSQL with Alembic migrations (`alembic upgrade head`)

```python
# Set via environment variable
DATABASE_URL = "postgresql://user:pass@localhost/agoutic"
```

## 📝 Workflow Skills

Pre-defined bioinformatics workflows are available in `skills/`:

- **Dogme_DNA.md** - Genomic DNA analysis workflow
- **Dogme_RNA.md** - Direct RNA-seq workflow
- **Dogme_cDNA.md** - cDNA isoform workflow
- **Differential_Expression.md** - edgePython DE pipeline (with gene annotation)
- **Enrichment_Analysis.md** - GO & pathway enrichment analysis
- **ENCODE_LongRead.md** - ENCODE consortium workflow
- **ENCODE_Search.md** - ENCODE search and data discovery
- **Local_Sample_Intake.md** - Sample intake and validation
- **Analyze_Job_Results.md** - Post-pipeline results analysis
- **Download_Files.md** - File download orchestration
- **Welcome.md** - New-user onboarding

## 🤝 Contributing

### Code Structure

- Use type hints throughout
- Write tests for new features
- Document configuration changes
- Update this README for major changes

### Testing Requirements

```bash
# Run full test suite (1040+ tests)
pytest tests/ -q

# With coverage
pytest tests/ --cov=cortex --cov=launchpad --cov-report=html
```

## 📞 Support

- Check [CONFIGURATION.md](CONFIGURATION.md) for configuration issues
- Check [launchpad/README.md](launchpad/README.md) for execution engine issues
- Check [cortex/README.md](cortex/README.md) for agent engine issues

## 📦 Version Information

- **Release**: 3.3.2 — ENCODE assay alias resolution in main search path; list-type zero-result retry fix; centralized DB + Alembic migrations
- **Python**: 3.12+
- **FastAPI**: Latest (from environment.yml)
- **SQLAlchemy**: 2.0+
- **Nextflow**: >= 23.0
- **Test Coverage**: 1068 tests across 54 test files
- **Status**: Active Development

## 🗓️ Development Timeline

- complete: Core infrastructure, dual interface, MCP integration
- complete: Web UI job monitoring, approval gates, project management
- complete: Plan-execute-observe-replan, gene annotation, expanded templates
- complete: Centralized DB, Alembic migrations, enrichment tools in Analyzer
- current: Cortex modularisation and DE adapter integration
- next: Production deployment preparation
