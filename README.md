# AGOUTIC: Automated Genomic Orchestrator

**Version:** 3.4.13
**Status:** Active Prototype 

## Latest Updates (2026-03-28)

- **3.4.13 BAM-detail resolution now prefers workflow discovery and explicit
  workflow context** — BAM-detail requests now prioritize workflow/work_dir
  listing and file lookup before any run-UUID-based analyzer call path.

- **Non-job UUIDs are now blocked from analyzer BAM-detail dispatch** —
  project/block UUID values are no longer forwarded as analyzer `run_uuid`
  for BAM-detail calls; unresolved contexts return a controlled fallback.

- **BAM fallback now allows safe workflow-discovery listing calls** —
  project-root workflow discovery (`list_job_files` with shallow depth) is
  permitted during BAM-detail resolution so the assistant can choose the
  correct workflow containing the requested BAM.

- **3.4.12 BAM-detail fallback now uses supported Analyzer tools only** —
  BAM-detail requests under `analyze_job_results` now start by listing run
  files (`list_job_files`) and avoid unsupported direct BAM-inspection tool
  calls.

- **Compatibility mapping for `show_bam_details` now routes to safe file
  listing** — when this hallucinated tool appears, routing falls back to
  `list_job_files` first instead of producing unknown-tool failures.

- **Prompt/skill contract now explicitly enforces list-first BAM triage** —
  skill guidance now states to use `list_job_files`, then `find_file` plus
  supported read/parse tools to inspect alignment/QC-adjacent outputs.

- **3.4.11 security hardening for file references** — user-controlled file
  path inputs for cross-project and analyzer proxy routes are now validated as
  relative-only and reject absolute paths, drive-style absolutes, traversal,
  null bytes, and unsafe wildcard/shell-like tokens.

- **Cross-project staging now supports logical file references safely** —
  selected files may now be provided via `logical_reference` (additive to
  `relative_path`), with deterministic `422` responses for ambiguous/no-match
  resolution outcomes.

- **User-data API responses now avoid absolute path leakage** — file and link
  payloads return safe relative path forms (for example `data/<filename>`) in
  API response bodies.

- **Cross-project API is now merge-ready and routed through Cortex** —
  `/cross-project/*` endpoints are now registered on the main app router, and
  missing cross-project schema contracts were added so imports resolve cleanly.

- **Cross-project recursive scans are now bounded by default** —
  recursive browse/search stop scanning when `limit` is reached by default,
  while still returning `truncated=true`; callers can request full counting
  with `total_count_full_scan=true`.

- **Unrelated project-stats regression was fixed at test-fixture scope only** —
  the in-memory SQLite fixture used by project-management tests now creates
  `dogme_jobs`, avoiding missing-table failures without changing production
  project-stats behavior.

- **New `reconcile_bams` skill and routing support** — added a dedicated skill
  for cross-workflow annotated BAM reconciliation, registered in Cortex skill
  and service registries, with auto-detection keywords for reconcile intents.

- **Reconcile planning flow now includes strict preflight before approval** —
  reconcile plans in `cortex/planner.py` now run helper-based reference checks,
  then run reconcile preflight GTF resolution before `REQUEST_APPROVAL`, and
  only execute reconcile run script after approval.

- **Helper script for workflow Nextflow reference consistency** — added
  `skills/reconcile_bams/scripts/check_workflow_references.py` to parse
  workflow Nextflow config artifacts, normalize reference identifiers, and fail
  on mixed/missing/ambiguous reference resolution.

- **Reconcile script now performs real workflow-scoped execution** —
  `skills/reconcile_bams/scripts/reconcile_bams.py` now enforces BAM filename
  contract (`<sample>.<reference>.annotated.bam`), rejects mixed references,
  resolves default GTFs from Dogme reference config, emits explicit
  `needs_manual_gtf` follow-up state when defaults are unavailable, creates a
  dedicated `workflow_reconcile_*` directory, symlinks inputs, and writes
  reconcile output artifacts.

- **Manual-GTF follow-up state propagated via replanner** —
  `cortex/plan_replanner.py` now inspects reconcile preflight JSON output and
  transitions the downstream approval step into `FOLLOW_UP` when manual GTF
  input is required.

- **Focused reconcile test coverage added and passing** — added targeted tests
  for skill routing, reconcile planner ordering, reconcile preflight/execution,
  and replanner payload extraction.

- **Executor parallel batching for safe step kinds** — execution now batches
  dependency-ready `LOCATE_DATA`, `SEARCH_ENCODE`, and `CHECK_EXISTING` steps
  in parallel, while approval-gated and orchestration-heavy kinds remain
  sequential.

- **Deterministic batch persistence ordering** — parallel batch outcomes are
  persisted in deterministic plan order (with stable step-id tie-break), so
  persisted state is consistent even when completion timing differs.

- **Hybrid-first planner bridge for six non-core flows** — planner now
  attempts hybrid plan generation first for `compare_samples`,
  `download_analyze`, `summarize_results`, `parse_plot_interpret`,
  `compare_workflows`, and `search_compare_to_local`.

- **Deterministic fallback is now reason-coded and explicit** — scoped flows
  fall back only for defined hybrid failure reasons (engine/parse/fragment/
  finalize failures and flow `plan_type` mismatch).

- **Core deterministic flow ownership remains unchanged** —
  `run_workflow`, `run_de_pipeline`, `run_enrichment`, and
  `remote_stage_workflow` continue to use deterministic routing in this pass.

- **Strict pre-dispatch plan validation** — workflow plans are now validated
  against a centralized contract before execution begins. Invalid step kinds,
  malformed dependencies, cyclic graphs, and project-scope mismatches now fail
  early with structured validation errors.

- **Hybrid fragment plan composition support** — planner output now supports
  fragment-based composition with deterministic step-ID remapping and
  dependency rewriting, enabling safe assembly of reusable planning fragments.

- **Project-scoped plan instance continuity** — planning and execution now
  preserve explicit `project_id` and `plan_instance_id` metadata for improved
  isolation across concurrent workflows.

- **Concurrency integration coverage added** — tests now validate parallel
  plan execution isolation across separate projects and for multiple
  plan instances inside one shared project.

- **`chat_with_agent` decomposition** — extracted tag parsing, tool-call
  dispatch, and embedded dataframe/plot handling from `cortex/app.py` into
  owned helper modules to reduce app-level coupling and shrink the main chat
  orchestration surface.
- **Skill script auto-discovery for Launchpad allowlists** — `skills/*/scripts`
  directories are now discovered automatically at startup and registered into
  `LAUNCHPAD_SCRIPT_ALLOWLIST_IDS` and `LAUNCHPAD_SCRIPT_ALLOWLIST_ROOTS` with
  deny-by-default behavior preserved for all non-discovered paths.

- **RUN_SCRIPT plan-step support** — added a dedicated workflow step kind for
  standalone utility-script execution paths so plans can represent script runs
  without routing through full workflow-only submission.
- **Local-only standalone script submission** — Launchpad `/jobs/submit` now
  supports `run_type="script"` while preserving default Dogme behavior.
- **Deny-by-default allowlist enforcement for scripts** — execution requires
  an explicit allowlisted `script_id` mapping or an explicit absolute
  `script_path` inside configured allowlisted roots.
- **No repository script auto-discovery** — runnable scripts are not inferred
  from repository contents; an explicit allowlisted selector is mandatory.
- **Script status and log surfacing in existing job APIs** — script runs now
  expose structured status and failure visibility through existing Launchpad
  status/log paths to support plan dependency handling.

- **Remote orchestration bridge removal** — removed the temporary
  compatibility shim used during helper extraction and eliminated reverse
  import coupling from remote orchestration back into Cortex app logic.
- **Submission-handler extraction** — moved
  `submit_job_after_approval` from `cortex/app.py` to
  `cortex/workflow_submission.py` to keep app-level route/orchestration glue
  thinner while preserving runtime behavior.
- **Submit boundary ownership now direct** —
  `cortex/app.py` dispatches directly to
  `cortex.workflow_submission.submit_job_after_approval`, and
  `cortex.app.submit_job_after_approval` is no longer maintained as an export
  surface.
- **Final submit bridge removed** —
  `cortex/workflow_submission.py` no longer depends on `cortex.app` for runtime
  behavior; deferred `app_module` lookup has been eliminated.
- **Owned extraction module introduced** —
  `extract_job_parameters_from_conversation` now lives in
  `cortex/job_parameters.py` and app/workflow call sites import from this owned
  boundary.
- **Owned polling module introduced** —
  `poll_job_status`, `_completed_job_results_ready`,
  `_resolved_job_work_directory`, and `_auto_trigger_analysis` now live in
  `cortex/job_polling.py` for clean ownership without app back-links.
- **Approval-context extractor injection** — remote approval-context building
  now receives parameter extraction from the app call site, preserving behavior
  while keeping module boundaries one-directional.
- **Defaults fallback continuity for degraded profile enrichment** — when live
  profile enrichment is unavailable, approval-context generation now preserves
  `slurm_account`/`slurm_partition` from resolved `get_slurm_defaults` data so
  approval summaries and gates remain stable.

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
- **Remote defaults-to-approval continuity** — when `get_slurm_defaults`
  resolves values from saved SSH-profile defaults, Cortex now treats them as
  valid submit-ready defaults and builds a proper approval gate instead of
  emitting fallback "0 SLURM defaults" messaging.
- **Launchpad context param safety net** — remote Launchpad calls now inject
  missing context fields (`user_id`, plus `project_id` for
  `get_slurm_defaults`) before execution to prevent missing-argument failures
  from degraded model tags.
- **Dedicated analysis capabilities guide** — README now includes a full
  post-execution analysis section covering scientific interpretation goals,
  supported analysis modes, example requests, visualization outputs, and
  current analysis limitations.
- **Post-extraction validation sweep completed** — focused submit/approval
  tests plus broader Cortex endpoint/chat suites and Launchpad status/submit
  suites passed after the submission-handler module move.
- **Bridge-removal validation sweep completed** — compile checks plus focused
  submit/approval/extract tests and broader chat-data-call boundary suites
  passed after moving extraction/polling ownership out of `cortex/app.py`.
- **Skills layout ownership refactor completed** — skills now live under
  `skills/<skill_key>/SKILL.md`, shared markdown references moved to
  `skills/shared/`, and registry/loader paths were updated without changing
  skill routing or execution behavior.
- **Skills-layout validation sweep completed** — compile checks, focused
  skill-loading/plan-chain/skill-detection suites, stale-path audits, and
  artifact hygiene checks passed after folderizing skill markdown ownership.

## 🧬 Overview

AGOUTIC is a general-purpose agent for analyzing and interpreting long-read genomic data (Nanopore/PacBio). It uses a **Dual Interface** architecture (REST + MCP) to allow both human users and AI agents to orchestrate complex bioinformatics pipelines.

The system is composed of:
- **Cortex**: Agent Engine - AI-powered orchestration and user interaction
- **Atlas**: ENCODE Integration - Public data retrieval via ENCODELIB
- **Launchpad**: Execution Engine - Dogme/Nextflow pipeline management (local + remote SLURM)
- **Analyzer**: Analysis Engine - Results analysis and QC reporting
- **edgePython**: Differential Expression — Bulk/single-cell RNA-seq DE via edgePython
- **XgenePy MCP**: Cis/trans regulatory modeling — local XgenePy execution with canonical artifacts
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

## 🔬 Analysis Capabilities

AGOUTIC combines computational workflow execution with agent-guided biological
interpretation. After a pipeline finishes, the platform helps users move from
raw output folders to scientific insight, including isoform behavior,
modification patterns, pathway shifts, and gene-level functional context.

### Analysis Goals

AGOUTIC is designed to help users:
- Interpret isoform discovery and transcript structure outputs from long-read workflows
- Summarize RNA and DNA modification signals from workflow artifacts
- Review QC metrics across runs and identify quality or completeness issues
- Inspect gene-level and transcript-level result tables with context
- Run downstream differential expression and enrichment analysis
- Compare outputs across samples, conditions, and workflows

### Supported Analysis Types

- **Quality Control (QC) analysis**
  - Parse run summaries, count/stat tables, alignment summaries, and basecalling outputs
  - Validate run completeness and surface troubleshooting context from logs and artifacts
  - Generate QC summaries for quick review across workflow outputs

- **Transcriptomic analysis**
  - Explore gene- and transcript-level quantification outputs
  - Support isoform-aware interpretation from long-read RNA/cDNA pipelines
  - Review splice-aware and transcript-structure-relevant outputs

- **Epitranscriptomic and epigenomic analysis**
  - Summarize RNA modification outputs from direct RNA workflows
  - Summarize DNA modification outputs from DNA workflows
  - Parse and interpret `bedMethyl` outputs, including region- and gene-linked review where applicable

- **Differential analysis**
  - Run bulk and single-cell RNA-seq differential expression through edgePython
  - Use the stateful DE flow: load → filter → normalize → design → fit → test → results
  - Filter by FDR/logFC and report annotated top genes for interpretation

- **Functional interpretation**
  - Run GO enrichment (BP, MF, CC)
  - Run Reactome and KEGG pathway enrichment
  - Translate Ensembl IDs and normalize symbols for human and mouse datasets

For deeper tool-level details, see [`analyzer/README.md`](analyzer/README.md),
[`skills/differential_expression/SKILL.md`](skills/differential_expression/SKILL.md),
[`skills/enrichment_analysis/SKILL.md`](skills/enrichment_analysis/SKILL.md), and
[`SKILLS.md`](SKILLS.md).

### Typical Analysis Workflow

Pipeline execution
  ↓
Result discovery
  ↓
QC parsing and summary generation
  ↓
Expression / isoform / modification result extraction
  ↓
Optional differential expression analysis
  ↓
Functional enrichment
  ↓
Agent-guided visualization and biological interpretation

### What Happens Next in Analysis

Once parsing and summaries are complete, AGOUTIC can pivot into follow-up
interpretation tasks such as cross-workflow comparison, condition-focused
differential analysis, and targeted functional hypotheses (for example,
pathway-level shifts or biologically coherent gene programs).

### Analysis Inputs

The analysis layer consumes:
- Pipeline result folders (for example `workflow1/`, `workflow2/`)
- CSV/TSV/BED/bedMethyl files
- Counts and summary/statistics tables
- Annotation and quantification outputs
- User-selected files from workflow subdirectories

### Analysis Outputs

The analysis layer returns:
- Parsed result tables
- QC summaries and run-validation context
- Annotated gene/transcript lists
- Differential expression result tables
- GO/pathway enrichment tables
- Interactive plots and chart-ready summaries
- Chat-readable scientific interpretation for downstream decisions

### Example Analysis Requests

- `Summarize the QC for workflow2`
- `List the important files in workflow1/annot`
- `Parse the bedMethyl output and summarize methylation patterns`
- `Show the top expressed genes from this result file`
- `Run differential expression between control and treatment`
- `Annotate these Ensembl IDs`
- `Run GO enrichment on the upregulated genes`
- `Compare workflow1 and workflow2 outputs`

### Visualization Support

- Inline Plotly visualizations directly in chat
- Interactive bar, scatter, heatmap, box, histogram, and pie plots
- Automatic plotting from parsed tables when chartable data are detected
- Plot generation from DE and enrichment outputs for rapid interpretation

### Current Analysis Limitations

- Analyzer currently requires local-accessible files
- Remote-only results must be copied back before downstream analysis
- Some analysis pathways are file-format dependent and assume expected output conventions
- Cross-run comparison is strongest when workflows use consistent references and naming
- Interpretation depth depends on pipeline completeness and annotation availability

See [`docs/remote_execution_architecture.md`](docs/remote_execution_architecture.md)
for remote execution constraints and staging/copy-back behavior.

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
streamlit run ui/appUI.py --server.address 0.0.0.0 --server.port 8501
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
cd ui && streamlit run appUI.py
```

Note: running `python ui/appUI.py` directly will not work correctly because the UI
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
│   ├── welcome/SKILL.md                 # New-user onboarding
│   ├── ENCODE_Search/SKILL.md           # ENCODE search skill + routing rules
│   ├── ENCODE_LongRead/SKILL.md         # ENCODE pipeline definition
│   ├── run_dogme_dna/SKILL.md           # DNA pipeline definition
│   ├── run_dogme_rna/SKILL.md           # RNA pipeline definition
│   ├── run_dogme_cdna/SKILL.md          # cDNA pipeline definition
│   ├── analyze_local_sample/SKILL.md    # Sample intake workflow
│   ├── analyze_job_results/SKILL.md     # Post-pipeline results analysis
│   ├── download_files/SKILL.md          # File download workflow
│   ├── differential_expression/SKILL.md # edgePython DE skill
│   ├── enrichment_analysis/SKILL.md     # GO & pathway enrichment skill
│   ├── remote_execution/SKILL.md        # Remote SLURM workflow
│   └── shared/
│       ├── SKILL_ROUTING_PATTERN.md     # Shared skill routing reference
│       └── DOGME_QUICK_WORKFLOW_GUIDE.md # Shared workflow parsing guide
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
streamlit run ui/appUI.py --server.port 8501
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
cd ui && streamlit run appUI.py
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

- **welcome/SKILL.md** - New-user onboarding
- **ENCODE_Search/SKILL.md** - ENCODE search and data discovery
- **ENCODE_LongRead/SKILL.md** - ENCODE consortium workflow
- **run_dogme_dna/SKILL.md** - Genomic DNA analysis workflow
- **run_dogme_rna/SKILL.md** - Direct RNA-seq workflow
- **run_dogme_cdna/SKILL.md** - cDNA isoform workflow
- **analyze_local_sample/SKILL.md** - Sample intake and validation
- **analyze_job_results/SKILL.md** - Post-pipeline results analysis
- **download_files/SKILL.md** - File download orchestration
- **differential_expression/SKILL.md** - edgePython DE pipeline (with gene annotation)
- **enrichment_analysis/SKILL.md** - GO & pathway enrichment analysis
- **remote_execution/SKILL.md** - Remote execution workflow
- **shared/SKILL_ROUTING_PATTERN.md** and **shared/DOGME_QUICK_WORKFLOW_GUIDE.md** - Shared reference docs

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
