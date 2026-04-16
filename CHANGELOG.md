## [Unreleased]

### Features

- **SLURM DNA approvals can now override `dogme.profile` with cluster-hosted
  modkit settings at run time** — the approval gate now offers a DNA-only
  custom `dogme.profile` option for SLURM runs, lets users enter extra remote
  bind paths and environment exports, and shows a preview of the exact bind
  list and profile text that will be submitted. Launchpad stages the custom
  profile only for DNA runs, validates the extra remote bind paths before
  submission, and ignores the custom override fields for RNA/cDNA so the
  standard non-DNA profile behavior is preserved.
  (`ui/appui_block_part1.py`, `cortex/workflow_submission.py`,
  `launchpad/backends/base.py`, `launchpad/schemas.py`,
  `launchpad/app.py`, `launchpad/mcp_tools.py`,
  `launchpad/mcp_server.py`, `launchpad/nextflow_executor.py`,
  `launchpad/backends/local_backend.py`,
  `launchpad/backends/slurm_backend.py`)

### Bug Fixes

- **Remote staging now surfaces byte-level transfer updates in the UI** —
  Launchpad tracks current-file transferred bytes and total-size estimates from
  rsync progress, Cortex persists that snapshot on `STAGING_TASK` blocks, and
  the staging card now shows current file, transferred bytes, total input size,
  file counts, and speed while a transfer is running or after it fails.
  (`launchpad/backends/file_transfer.py`,
  `launchpad/backends/slurm_backend.py`, `cortex/job_polling.py`,
  `cortex/app.py`, `ui/appui_block_part2.py`)

- **Broker-backed staging now reports live progress and no longer treats NFS
  placeholder files as stale cache corruption** — Launchpad now polls the
  local auth broker for in-flight rsync progress so the staging UI updates
  during brokered transfers, and remote cache inspection ignores transient
  `.nfs*` placeholders when deciding whether an interrupted staged-input cache
  can be resumed instead of being deleted.
  (`launchpad/backends/file_transfer.py`,
  `launchpad/backends/local_user_broker.py`,
  `launchpad/backends/slurm_backend.py`)

- **Running staging cards now refresh often enough to actually show live
  transfer progress** — the Streamlit staging UI no longer waits up to 90 s
  before asking Cortex for a fresher running-task snapshot, and it retries on
  a shorter cadence so resumed broker-backed rsync transfers surface byte
  counters while the page is open.
  (`ui/appui_block_part2.py`)

- **Large brokered rsync transfers no longer fail after the copy finishes just
  because the broker response is too large** — Launchpad now reads local auth
  broker replies to EOF instead of using line-length-limited framing, and the
  broker compacts oversized rsync stdout before returning it so completed large
  single-file uploads do not get misreported as failed at response-parse time.
  (`launchpad/backends/local_auth_sessions.py`,
  `launchpad/backends/local_user_broker.py`,
  `launchpad/backends/file_transfer.py`)

- **Resumed stage-only transfers now show Launchpad's real queued state
  immediately instead of pretending rsync is already running** — after Resume
  Staging, Cortex now syncs the replacement staging task's current status right
  away, so stage-only blocks can surface "waiting for unlock/resume" messages
  when no brokered rsync has actually restarted yet.
  (`cortex/app.py`)

- **Large rsync staging transfers no longer false-stall on carriage-return
  progress updates** — the direct and brokered rsync watchdogs now treat any
  stdout activity as progress instead of waiting for newline-delimited output,
  which avoids aborting long single-file transfers that only emit `\r`
  progress frames. The default staging idle watchdog was also relaxed from
  600 s to 1800 s. Background staging uploads also no longer inherit the old
  `LAUNCHPAD_STAGE_TIMEOUT - reserve` request budget; they now default to the
  full `STAGE_MAX_TOTAL_TIMEOUT_SECONDS` ceiling unless
  `REMOTE_STAGE_TRANSFER_TIMEOUT_SECONDS` is set explicitly.
  (`launchpad/backends/file_transfer.py`,
  `launchpad/backends/local_user_broker.py`)

### Tests

- **Custom `dogme.profile` runtime override regression coverage** — added
  focused tests for DNA-only profile override resolution, non-DNA ignore
  behavior, SLURM remote bind/profile staging, Launchpad MCP payload
  forwarding, and Cortex approval-path propagation of the DNA-only custom
  fields.
  (`tests/launchpad/test_nextflow_executor.py`,
  `tests/test_slurm_backend_cache_flow.py`,
  `tests/launchpad/test_mcp_server.py`,
  `tests/launchpad/test_mcp_tools.py`,
  `tests/cortex/test_background_tasks.py`)

- **Staging transfer byte-progress regression coverage** — added focused tests
  for rsync byte parsing, Cortex staging-payload persistence of transfer
  snapshots, and refresh-path propagation of transfer byte details.
  (`tests/launchpad/test_file_transfer.py`,
  `tests/cortex/test_background_tasks.py`,
  `tests/cortex/test_block_endpoints.py`)

- **Broker progress and NFS-resume regression coverage** — added focused tests
  for broker request-status snapshots, live broker progress polling in the file
  transfer layer, and resumable staged-input caches that contain transient
  `.nfs*` placeholders.
  (`tests/launchpad/test_local_auth_sessions.py`,
  `tests/launchpad/test_file_transfer.py`,
  `tests/launchpad/test_slurm_backend.py`)

- **Running staging UI refresh regression coverage** — added focused tests for
  the active refresh thresholds that govern when a visible `STAGING_TASK`
  snapshot is refreshed while a transfer is still running.
  (`tests/ui/test_app_source_helpers.py`)

- **Broker large-response regression coverage** — added focused tests for
  local auth broker responses that exceed `StreamReader.readline()` limits and
  for oversized rsync stdout payloads being compacted before they are returned
  to Launchpad.
  (`tests/launchpad/test_local_auth_sessions.py`)

- **Stage-only resume status regression coverage** — added focused tests for
  resumed staging blocks that should immediately reflect a queued replacement
  task waiting on Launchpad auth/restart recovery.
  (`tests/cortex/test_block_endpoints.py`)

- **Rsync progress watchdog regression coverage** — added focused tests for
  both direct and brokered transfer runners to ensure carriage-return rsync
  progress output counts as activity and does not trigger a false stall.
  (`tests/launchpad/test_file_transfer.py`,
  `tests/launchpad/test_local_auth_sessions.py`)

## [3.6.3] - 2026-04-13

### Features

- **GTF-backed gene and transcript annotation with colocated caches** —
  AGOUTIC now derives gene and transcript annotations directly from source GTF
  files, writes compact `.genes.tsv` and `.transcripts.tsv` caches beside each
  source annotation, supports both versioned and unversioned Ensembl IDs, and
  preserves original symbol capitalization while keeping symbol lookup
  case-insensitive. Shared reference GTF caches are generated on demand, and
  workflow-specific or custom GTFs can be loaded the same way.
  (`common/gtf_parser.py`, `common/gene_annotation.py`,
  `scripts/build_gene_reference.py`)

- **DE and analyzer annotation paths now prefer workflow-local
  `reconciled.gtf` when present** — edgePython data loading and manual
  annotation now detect `reconciled.gtf` beside the active counts/work
  directory, shared reference caches remain available as fallback, and the
  analyzer MCP surface now exposes transcript-aware lookup/translation plus a
  `build_gene_cache` tool for user-provided GTFs.
  (`edgepython_mcp/edgepython_server.py`,
  `edgepython_mcp/tool_schemas.py`, `analyzer/gene_tools.py`,
  `analyzer/mcp_server.py`, `analyzer/mcp_tools.py`,
  `cortex/plan_templates.py`, `skills/differential_expression/SKILL.md`)

- **Live remote staging can now be cancelled from the UI and API** — running
  `STAGING_TASK` blocks now expose a cancel control, Launchpad accepts
  `POST /remote/stage/{task_id}/cancel`, Cortex proxies the request and marks
  the staging block and related workflow steps as `CANCELLED`, and brokered
  transfers reuse the staging task id as the rsync request id so cancellation
  can stop the active broker-side process as well.
  (`ui/appui_block_part2.py`, `cortex/app.py`, `cortex/job_polling.py`,
  `cortex/remote_orchestration.py`, `cortex/task_service.py`,
  `launchpad/app.py`, `launchpad/backends/staging_worker.py`,
  `launchpad/backends/local_user_broker.py`)

- **Stage-only transfers can now be resumed from the UI and API** — cancelled
  or failed `STAGING_TASK` blocks now expose a resume control, Launchpad can
  queue a replacement staging task from the persisted request parameters,
  Cortex swaps the block over to the new task id, resets the related workflow
  steps to `RUNNING`/`PENDING`, and restarts background polling without making
  the user re-submit the original staging request.
  (`ui/appui_block_part2.py`, `cortex/app.py`, `cortex/remote_stage_status.py`,
  `launchpad/app.py`, `launchpad/backends/staging_worker.py`,
  `launchpad/schemas.py`)

- **Stage transfers can now be refreshed, cancelled, and resumed from the
  Task Center** — `stage_transfer` tasks now carry the Launchpad staging task
  id, stale running transfers reconcile through the Cortex refresh endpoint
  before showing actions, fresh running transfers can be cancelled, and failed
  or cancelled transfers can be resumed directly from the tasks page.
  (`ui/appui_tasks.py`, `ui/appui_block_part2.py`, `cortex/app.py`,
  `cortex/task_service.py`)

### Bug Fixes

- **Server startup no longer emits a `runpy` warning while checking shared GTF
  caches** — the `common` package now exposes `GeneAnnotator` lazily instead of
  importing it eagerly at package import time, which avoids preloading
  `common.gtf_parser` before `python -m common.gtf_parser --ensure-caches`
  executes.
  (`common/__init__.py`, `agoutic_servers.sh`)

- **Remote rsync staging now preserves partial transfers safely and rejects
  stale remote cache reuse** — rsync uploads/downloads now use
  `.rsync-partial`, timeout and lost-task messages point users to that
  recovery path, direct and brokered rsync processes are terminated as a full
  process group on cancellation/timeouts, and SLURM input-cache reuse now
  refreshes remote directories that contain symlinks or size mismatches while
  resuming deterministic cache paths that still contain `.rsync-partial`
  state from an interrupted transfer instead of restarting from zero.
  (`launchpad/backends/file_transfer.py`,
  `launchpad/backends/local_user_broker.py`,
  `launchpad/backends/slurm_backend.py`, `cortex/job_polling.py`)

- **Broker-backed staging transfers now fail fast when rsync goes silent** —
  the local auth broker now applies the same idle-stall watchdog used by the
  direct rsync path, so dead brokered transfers stop after prolonged silence
  instead of sitting in `RUNNING` until the full transfer timeout expires.
  (`launchpad/backends/file_transfer.py`,
  `launchpad/backends/local_user_broker.py`)

### Tests

- **GTF parser and annotation regression coverage** — added focused tests for
  human and mouse GTF parsing, gzip support, stale-cache rebuilds, colocated
  cache generation, transcript annotation, case-insensitive symbol lookup, and
  reconciled-GTF precedence over legacy reference lookups.
  (`tests/common/test_gtf_parser.py`,
  `tests/common/test_gene_annotation.py`)

- **Remote staging cancellation and rsync-hardening regression coverage** —
  added targeted tests for Launchpad staging cancellation, broker-side cancel
  requests, direct rsync partial-dir handling and timeout cleanup, Cortex block
  cancellation propagation, and SLURM remote cache refresh when cached input
  contents no longer match the local source.
  (`tests/launchpad/test_staging_worker.py`,
  `tests/launchpad/test_app_status_endpoint.py`,
  `tests/launchpad/test_local_auth_sessions.py`,
  `tests/launchpad/test_file_transfer.py`,
  `tests/launchpad/test_slurm_backend.py`,
  `tests/cortex/test_block_endpoints.py`)

- **Staging resume and broker-watchdog regression coverage** — added focused
  tests for broker idle-timeout forwarding and stall handling, replacement
  staging task creation, Launchpad resume responses, Cortex resume/refresh
  propagation, task-page staging action helpers, and partial-cache resume
  instead of cache deletion.
  (`tests/launchpad/test_local_auth_sessions.py`,
  `tests/launchpad/test_staging_worker.py`,
  `tests/launchpad/test_app_status_endpoint.py`,
  `tests/cortex/test_block_endpoints.py`,
  `tests/launchpad/test_slurm_backend.py`,
  `tests/ui/test_app_source_helpers.py`)

### Documentation

- **Remote staging troubleshooting notes corrected for current rsync
  behavior** — the troubleshooting guide now documents that AGOUTIC does not
  currently auto-fallback to SFTP when `rsync` is missing and points users to
  the actual rsync-based recovery path.
  (`docs/troubleshooting_remote.md`)

## [3.6.2] - 2026-04-11

### Features

- **Manifest-driven planning through phase 6** — Cortex now treats
  `SkillManifest` as the planner-facing source of truth for core deterministic
  flows. Skill manifests declare request triggers, expected inputs, required
  services, runtime buckets, MCP tool chains, and dependency hints; the
  planner now attempts manifest composition first; and a new `plan_composer`
  module builds DE, enrichment, and XgenePy plans from that metadata while
  preserving template fallback for unmigrated flows.
  (`cortex/skill_manifest.py`, `cortex/plan_classifier.py`,
  `cortex/plan_composer.py`, `cortex/planner.py`,
  `cortex/plan_templates.py`)

- **Planner/executor service-aware manifest gating** — composed plans now
  surface manifest-derived runtime summaries plus missing-input and
  unavailable-service warnings, and executor-side step dispatch now fails fast
  for manifest-tagged steps when the required MCP backend is unavailable.
  (`cortex/planner.py`, `cortex/plan_executor.py`)

- **YAML-first skill manifest migration** — skill-specific planner metadata now
  lives beside each skill in `skills/<skill_key>/manifest.yaml`, with
  `cortex/skill_manifest.py` loading manifests dynamically and service /
  consortium skill ownership derived from manifest `source.type` and
  `source.key`. For existing backends, adding or updating a skill no longer
  requires editing Cortex-side registries.
  (`skills/*/manifest.yaml`, `cortex/skill_manifest.py`, `cortex/config.py`,
  `atlas/config.py`, `environment.yml`)

- **Deterministic skill-management commands** — added `/skills`,
  `/skill <skill_key>`, and `/use-skill <skill_key>` plus equivalent explicit
  English queries such as “what skills are available?”, “tell me about the
  differential expression skill”, and “switch to the IGVF Search skill”.
  Manual switching persists across turns by updating the active skill via the
  same AGENT_PLAN state path used by normal chat continuation.
  (`cortex/skill_commands.py`, `cortex/chat_stages/quick_exits.py`)

- **Slash-command discovery surfaced in the UI and quick reference** — the
  in-app help panel, sidebar help shortcuts, and deterministic capabilities
  response now enumerate the live skill, workflow, DE, and memory slash
  commands, and the quick reference now documents the complete existing
  slash-command surface in one place.
  (`QUICK_REFERENCE.md`, `ui/appui_sidebar.py`, `ui/appui_state.py`,
  `cortex/chat_stages/quick_exits.py`)

- **IGVF downloads now follow the same approval-gated project save flow as
  ENCODE downloads** — Cortex now treats IGVF file URL resolution as a real
  download chain, so users can move from IGVF dataset/file accessions to the
  normal project download approval path instead of getting a bare URL back.
  (`cortex/tool_dispatch.py`, `skills/download_files/SKILL.md`,
  `skills/IGVF_Search/SKILL.md`)

### Bug Fixes

- **Workflow Results attachment no longer leaks across unrelated responses** —
  agent-side workflow highlights now require explicit workflow linkage or a
  real plan-title match, preventing stale DE workflow results from appearing
  under unrelated non-DE agent responses.
  (`ui/appui_services.py`, `cortex/job_polling.py`)

- **IGVF accessions and dataset file rows now survive result formatting and
  DataFrame extraction** — IGVF search and dataset-file payloads are now
  unwrapped before generic dict compaction, which preserves real accession
  values in second-pass answers and restores interactive DataFrames for IGVF
  result sets.
  (`atlas/result_formatter.py`, `cortex/chat_dataframes.py`)

- **IGVF dataset and file lookups now recover from hallucinated parameter
  names** — IGVF dataset/file tool calls now repair aliases such as
  `dataset_id`, `dataset_accession`, and misplaced file accessions, and they
  prefer the user-stated `IGVFDS` / `IGVFFI` accession when the model emits a
  mismatched value.
  (`atlas/config.py`, `cortex/igvf_helpers.py`)

- **IGVF download prompts no longer fall through analyzer file aliases** —
  download requests mentioning `IGVFFI` / `IGVFDS` accessions now switch into
  `download_files` before generic consortium-search routing, `get_file` is
  accepted as a repairable IGVF alias, and consortium-specific alias mappings
  now preserve `get_file -> get_file_metadata` instead of rewriting it to the
  analyzer `read_file_content` path. IGVF `get_file_metadata` calls also stay
  on the IGVF-specific repair path instead of passing through ENCODE
  experiment/file rerouting logic, which avoids spurious `ENCSR` parent
  experiment errors for valid `IGVFFI` lookups.
  (`atlas/config.py`, `cortex/llm_validators.py`,
  `cortex/tool_dispatch.py`)

### Tests

- **Manifest planning regression suite** — added dedicated coverage for
  manifest schema defaults, manifest-backed detection, plan composition,
  planner fallback, and executor service gating, while keeping the adjacent
  planner and executor regression suites green.
  (`tests/cortex/test_skill_manifest.py`,
  `tests/cortex/test_manifest_driven_planning.py`,
  `tests/cortex/test_plan_composer.py`,
  `tests/cortex/test_plan_executor_run_script.py`,
  `tests/cortex/test_planner_cache_steps.py`,
  `tests/cortex/test_planner_hybrid_bridge.py`,
  `tests/cortex/test_xgenepy_planning.py`,
  `tests/cortex/test_plan_executor_concurrency.py`)

- **Skill command and YAML manifest regression coverage** — added tests for
  folder-local YAML manifest discovery, manifest-derived source routing,
  deterministic skill list/describe/use commands, natural-language command
  equivalents, persisted manual skill switching across turns, workflow
  highlight attachment safety, and UI help-intent detection for slash-command
  discovery.
  (`tests/cortex/test_skill_manifest.py`,
  `tests/cortex/test_config_skill_sources.py`,
  `tests/cortex/test_skill_commands.py`,
  `tests/cortex/test_chat_entry.py`,
  `tests/ui/test_app_source_helpers.py`,
  `tests/cortex/test_background_tasks.py`)

- **IGVF parameter-repair regression coverage** — added helper tests covering
  dataset lookup alias repair, `get_files_for_dataset` dataset accession
  repair, and file-download accession normalization for `IGVFFI` values.
  (`tests/cortex/test_igvf_helpers.py`)

- **IGVF download-routing regression coverage** — added tests ensuring IGVF
  file and dataset download prompts switch into `download_files`, `get_file`
  aliases are not rejected during validation, and consortium alias resolution
  keeps IGVF `get_file` mapped to `get_file_metadata` rather than the generic
  analyzer file-reader alias. Added a dispatch regression asserting that IGVF
  `get_file_metadata` tags remain on the IGVF path and do not inherit ENCODE
  file-metadata routing errors.
  (`tests/cortex/test_skill_detection.py`,
  `tests/cortex/test_validation.py`,
  `tests/cortex/test_tool_dispatch_path_resolution.py`)

### Documentation

- **Planning and skills docs refreshed for manifest-first composition** —
  updated the root architecture overview, skills authoring guide, Cortex
  overview, and architecture diagrams to describe the new `SkillManifest` +
  `plan_composer` planning path and template fallback model.
  (`README.md`, `SKILLS.md`, `cortex/README.md`,
  `docs/architecture_diagrams.md`, `VERSION`)

- **Skills authoring docs updated for YAML-local manifests and command
  discovery** — documented `skills/<skill_key>/manifest.yaml` as the skill
  metadata source of truth, removed stale instructions to edit Cortex
  registries for normal skill changes, documented the new skill-management
  commands and English-query equivalents, and cleaned up edgePython naming in
  user-facing docs.
  (`SKILLS.md`, `cortex/README.md`, `docs/architecture_diagrams.md`,
  `atlas/README.md`, `atlas/QUICKSTART.md`,
  `skills/differential_expression/SKILL.md`, `skills/welcome/SKILL.md`)

- **Command reference and in-app help refreshed** — documented the full live
  slash-command inventory in the quick reference and aligned the sidebar/local
  help copy with the actual implemented command surface, including workflow
  commands, `/de`, memory aliases, and skill-management commands.
  (`QUICK_REFERENCE.md`, `ui/appui_sidebar.py`, `ui/appui_state.py`)

---

## [3.6.1] - 2026-04-11

### Features

- **Cross-project Task Center** — added a dedicated Tasks page that aggregates
  persistent work across all accessible projects, groups items by project
  instead of by raw task stream, switches context automatically when opening a
  task from another project, and surfaces follow-up and stale work in a
  project-first layout.
  (`cortex/routes/projects.py`, `cortex/schemas.py`,
  `cortex/task_service.py`, `ui/pages/tasks.py`, `ui/appui_tasks.py`,
  `ui/appui_sidebar.py`)

### Bug Fixes

- **IGVF required-parameter repair in chat dispatch** — Cortex now repairs
  malformed IGVF `DATA_CALL` tags before MCP execution by backfilling
  `search_by_sample.sample_term` and `search_by_assay.assay_title` from the
  user message when possible. Calls still missing required parameters after
  repair are now dropped during schema validation instead of reaching the MCP
  layer and surfacing raw Pydantic validation errors in chat.
  (`cortex/igvf_helpers.py`, `cortex/tool_dispatch.py`)

- **IGVF tool routing and alias resolution hardening** — Cortex now preserves
  IGVF canonical tools from generic alias shadowing, preventing routes such as
  `get_gene` being redirected to analyzer gene lookup and `search_files` being
  redirected to the generic file finder. It also reroutes mis-emitted
  `search_measurement_sets` calls to the correct IGVF file, analysis-set,
  prediction-set, or sample search tools based on the supplied parameters, and
  normalizes organism values such as `Mus_musculus` and `Homo_sapiens` before
  validation. This fixes file/analysis/prediction queries silently dropping
  filters and improves recovery from common LLM parameter hallucinations.
  (`atlas/config.py`, `cortex/igvf_helpers.py`, `cortex/tool_dispatch.py`)

- **Remote staging tasks now survive Launchpad restarts** — Launchpad no
  longer relies solely on the in-memory `_staging_tasks` registry for
  background remote staging. Task state is now persisted to a durable
  `staging_tasks` table, progress/result/error updates are written through as
  transfers run, queued and in-flight rows are recovered on startup,
  `/remote/stage/{task_id}` and retry requests fall back to persisted rows
  after a restart, and TTL cleanup removes expired task records from both
  memory and storage. This closes the gap where Cortex could observe repeated
  404s after a Launchpad restart and mark an otherwise resumable transfer as
  lost.
  (`launchpad/backends/staging_worker.py`, `launchpad/db.py`,
  `launchpad/models.py`, `launchpad/app.py`,
  `alembic/versions/b7c3a9d1e4f2_add_staging_tasks_table.py`,
  `tests/launchpad/test_staging_worker.py`, `tests/launchpad/test_db.py`,
  `tests/launchpad/test_app_status_endpoint.py`)

- **Dangling task cleanup and stale-task demotion** — task freshness now uses
  source activity from workflow/block payloads rather than the `ProjectTask`
  sync timestamp, which was being refreshed on every projection pass. Running
  tasks with no recorded source updates for more than 24 hours are now
  reclassified as stale follow-up work in the Task Center, and per-project
  task docks hide stale root tasks older than 48 hours to keep active project
  views focused on current work.
  (`cortex/task_service.py`, `cortex/remote_orchestration.py`,
  `ui/appUI.py`, `ui/appui_tasks.py`,
  `tests/cortex/test_project_endpoints.py`,
  `tests/ui/test_app_source_helpers.py`)

### Documentation

- **Top-level docs/version alignment** — refreshed the root README and version
  metadata so Atlas is described as a registry-driven ENCODE + IGVF data layer
  rather than ENCODE-only.
  (`README.md`, `VERSION`)

---

## [3.6.0] - 2026-04-10

### Features

- **IGVF Data Portal MCP server** — a new consortium MCP server provides 14
  tools for querying the Impact of Genomic Variation on Function (IGVF) Data
  Portal at `api.data.igvf.org`. Covers MeasurementSets, AnalysisSets,
  PredictionSets, files, genes, and samples with search, lookup, and download
  URL generation. The server runs on port 8009 and integrates with Cortex via
  the existing consortium registry, DATA_CALL tag parsing, tool/param alias
  correction, result formatting, and skill routing.
  (`atlas/igvf_client.py`, `atlas/igvf_mcp_server.py`,
  `atlas/igvf_tool_schemas.py`, `atlas/launch_igvf.py`,
  `atlas/config.py`, `cortex/skill_manifest.py`,
  `skills/IGVF_Search/SKILL.md`, `agoutic_servers.sh`)

  Tools: `search_measurement_sets`, `search_analysis_sets`,
  `search_prediction_sets`, `search_by_sample`, `search_by_assay`,
  `search_files`, `get_dataset`, `get_file_metadata`,
  `get_file_download_url`, `get_files_for_dataset`, `search_genes`,
  `get_gene`, `search_samples`, `get_server_info`.

  Integration includes 16 tool-name aliases (`search_datasets` →
  `search_measurement_sets`, etc.), parameter aliases for 7 tools
  (`biosample` → `sample_term`, `gene_symbol` → `query`, etc.), 9 fallback
  regex patterns for LLM hallucination repair, and an `IGVF_Search`
  SkillManifest registered in `cortex/skill_manifest.py`.

  The IGVF HTTP client handles the Snovault API quirk where empty search
  results return HTTP 404 with valid JSON, treating those as normal
  zero-result responses instead of errors.

- **IGVF plain-language routing** — added IGVF keyword signals to
  `_auto_detect_skill_switch()` so that queries mentioning "IGVF", "IGVF
  portal", "measurement sets", "prediction sets", "analysis sets", or IGVF
  accessions (IGVFDS/IGVFFI) auto-route to the IGVF_Search skill without
  manual skill selection. Also added an IGVF_Search routing entry to the
  welcome skill's decision table.
  (`cortex/llm_validators.py`, `skills/welcome/SKILL.md`)

### Bug Fixes

- **IGVF negative limit guard** — negative `limit` values passed to
  `IGVFClient.search()` are now clamped to 0, preventing a 500 Internal
  Server Error from the IGVF API. (`atlas/igvf_client.py`)

- **IGVF gene search exact-match priority** — `search_genes` now promotes
  exact symbol matches to the top of results (the IGVF API ranks partial
  matches like BRCA1P1 above BRCA1). `get_gene` now tries an exact `symbol=`
  filter before falling back to free-text `query=`, and fetches the full
  object via `@id` so all fields (taxa, locations) are populated.
  (`atlas/igvf_mcp_server.py`)

### Tests

- **IGVF plain-language routing tests** — 32 tests verifying auto-detect
  skill switching for IGVF queries (positive/negative/accession cases),
  welcome skill routing presence, and DATA_CALL tag parsing for all 14 IGVF
  tools. (`tests/igvf_plain_language_test.py`)

- **IGVF Q&A validation tests** — 26 tests simulating real user questions
  ("How many ATAC-seq datasets does IGVF have?", "Search IGVF for K562",
  "Tell me about gene BRCA1", etc.), verifying auto-routing, tool
  dispatch, answer correctness via cross-validation against the live IGVF
  API, multi-step consistency (dataset → files → file metadata), negative
  cases, and count accuracy for all dataset types.
  (`tests/igvf_qa_validation_test.py`)

- **Comprehensive IGVF test suite** — 161 tests across 25 suites covering
  pagination & boundary values, SQL/XSS injection safety, Unicode & special
  characters, parameter type coercion, cache behavior & key isolation,
  schema–tool signature alignment, Cortex dispatch config validation (tool
  aliases, param aliases, fallback patterns), `/tools/schema` endpoint,
  DATA_CALL tag parsing, cross-tool workflows (search → dataset → files →
  download), server-down error handling, concurrent request handling,
  response format consistency, data integrity spot-checks, and real LLM
  hallucination scenarios.
  (`tests/igvf_comprehensive_test.py`, `tests/igvf_integration_test.py`)

---

## [3.5.3] - 2026-04-10

### Improvements

- **Sync, browsing, and user-data prompts now bypass the wasted first LLM pass** —
  Cortex now detects these intents earlier in the chat pipeline, skips the
  first-pass/tag-parsing path when it is not needed, and emits immediate
  progress for result-sync requests instead of making the UI wait through an
  unnecessary model round trip. This improves responsiveness for commands such
  as `sync workflow6 locally` and other direct override-style requests.
  (`cortex/chat_stages/early_overrides.py`, `cortex/chat_stages/__init__.py`,
  `tests/cortex/test_early_overrides.py`)

- **Long-running jobs now keep user sessions alive more reliably** — session
  expiry now defaults to 72 hours, Cortex exposes `POST /auth/heartbeat`, the
  auth middleware renews sessions when they are past half-life, and the
  Streamlit UI sends heartbeats while jobs are running. This reduces unwanted
  logouts during long SLURM and sync activity without changing explicit logout
  behavior.
  (`cortex/config.py`, `cortex/auth.py`, `cortex/middleware.py`, `ui/appUI.py`)

- **Dogme GPU task concurrency now defaults to no explicit Nextflow cap** —
  Launchpad now treats omitted `max_gpu_tasks` as "no maximum", so Dogme
  Nextflow configs no longer emit `maxForks` for GPU-bound processes unless an
  explicit limit is requested. Explicit per-run and environment-configured GPU
  limits are now validated up to a maximum of 16, the approval UI exposes
  `No maximum` plus `1-16` as selectable values, and the post-approval
  submission path now preserves an explicit `null`/`None` limit instead of
  reintroducing `max_gpu_tasks=1` before Launchpad receives the request.
  (`launchpad/config.py`, `launchpad/schemas.py`, `launchpad/app.py`,
  `launchpad/nextflow_executor.py`, `launchpad/backends/base.py`,
  `launchpad/mcp_tools.py`, `ui/appui_block_part1.py`,
  `cortex/workflow_submission.py`)

- **SLURM walltime defaults and limits raised to 48-72 hours** — Dogme SLURM
  submissions now default to `48:00:00`, resource validation enforces a minimum
  walltime of 48 hours and a maximum of 72 hours, and shared SLURM presets/UI
  defaults were aligned to the same range.
  (`launchpad/config.py`, `launchpad/backends/resource_validator.py`,
  `launchpad/backends/slurm_backend.py`,
  `launchpad/backends/sbatch_generator.py`, `launchpad/models.py`,
  `ui/appui_block_part1.py`, `ui/components/slurm_form.py`,
  `docs/user_guide_execution_modes.md`, `README.md`)

- **Grouped edgePython differential expression now runs directly from
  reconciled abundance outputs and saved dataframes** — users can compare named
  sample groups from the active workflow abundance table or a previously parsed
  dataframe, Cortex prepares edgePython-ready counts and sample-info files in a
  workflow-local DE staging directory, defaults two-group reconcile compares to
  exact tests, saves full results and plots under the selected workflow, and
  asks for clarification instead of guessing when the user has not named the
  groups.
  (`cortex/de_prep.py`, `cortex/plan_params.py`, `cortex/plan_templates.py`,
  `cortex/plan_executor.py`, `cortex/chat_stages/plan_detection.py`,
  `tests/cortex/test_de_prep.py`, `tests/cortex/test_planner_cache_steps.py`,
  `tests/cortex/test_plan_executor_run_script.py`,
  `tests/cortex/test_chat_entry.py`)

- **Differential-expression histories now capture comparisons, result files,
  and volcano plots directly into project memory, and workflow results can show
  the volcano plot inline** — successful DE prep, test, save, and plot steps
  now auto-record compared sample groups, DEG counts, saved result-table
  artifacts, and volcano-plot artifacts as project memories as soon as each
  step completes, so the record survives downstream failures. DE workflow
  summaries and volcano-plot step payloads now carry structured artifact
  metadata plus inline image payloads, and the UI also recognizes legacy
  text-only `Volcano plot saved to: ...` results when rendering workflow
  highlights.
  (`cortex/memory_service.py`, `cortex/plan_executor.py`,
  `ui/appui_renderers.py`, `tests/cortex/test_memory_service.py`,
  `tests/cortex/test_plan_executor_concurrency.py`,
  `tests/cortex/test_plan_executor_run_script.py`,
  `tests/ui/test_app_source_helpers.py`)

- **The UI help surface now documents grouped DE prompts directly** — the
  deterministic Streamlit help card, sidebar shortcuts, and canned Cortex help
  response now show concrete prompts for comparing reconcile abundance samples
  and dataframe-backed DE requests, so the new workflow is discoverable from
  the UI without trial-and-error prompting.
  (`ui/appui_state.py`, `ui/appui_sidebar.py`,
  `cortex/chat_stages/quick_exits.py`, `README.md`, `ui/README.md`)

### Bug Fixes

- **Reconcile BAM preflight now treats matching remote Watson GTF paths as the
  local canonical reference** — when workflow configs or manual approval input
  point at a remote `annot:` GTF whose filename matches the local reference
  catalog for the selected genome, reconcile now maps that path to the local
  canonical GTF instead of forcing a manual correction. The approval UI also
  shows the original configured path and the mapped local path so users can see
  exactly what was resolved.
  (`skills/reconcile_bams/scripts/reconcile_bams.py`,
  `ui/appui_block_part1.py`, `tests/skills/test_reconcile_bams_scripts.py`)

- **Reconcile approvals no longer default to all CPU cores, and auth timeouts
  no longer masquerade as global logout** — reconcile thread counts are now
  bounded in the wrapper script, underlying script, submission builder, and UI
  approval form using env-configurable defaults/caps, which avoids host
  starvation after approval. The Streamlit auth layer now distinguishes
  `/auth/me` timeouts and connection failures from a real logged-out state, so
  users see a server-unavailable/retry message instead of the misleading
  `You are not logged in` prompt when the API is temporarily overloaded.
  (`skills/reconcile_bams/scripts/reconcile_bams.py`,
  `skills/reconcile_bams/scripts/reconcileBams.py`,
  `cortex/workflow_submission.py`, `ui/appui_block_part1.py`, `ui/auth.py`,
  `tests/skills/test_reconcile_bams_scripts.py`,
  `tests/cortex/test_workflow_submission_reconcile_threads.py`,
  `tests/ui/test_auth_helpers.py`)

- **Project task lists now isolate the active workflow instead of mixing in
  older runs** — task sync now keeps only the latest active workflow plan (or,
  if all workflows are finished, only the most recent completed one), skips
  approval/staging/execution blocks tied to superseded workflow plans, and
  archives stale task rows before rendering a newly generated plan. This fixes
  task lists where a new run such as `EXC` could still show older items from
  prior runs like `GKO` or `LWF` in the same project.
  (`cortex/task_service.py`, `cortex/chat_stages/plan_detection.py`,
  `tests/cortex/test_workflow_task_isolation.py`)

- **Analyzer and project detail queries no longer crash when workflow identity
  migration has not yet been applied** — `generate_analysis_summary` and
  `get_job_work_dir` now use `load_only()` to select only the columns they
  need, and the project-detail raw SQL falls back gracefully when
  `workflow_index` / `workflow_alias` columns are absent.
  (`analyzer/analysis_engine.py`, `cortex/routes/projects.py`)

- **Reconcile plan resolves bare workflow folder names to absolute paths** —
  when the user names workflows like `workflow5` without an explicit path and
  `conv_state.workflows` has no matching entries, the extracted `workflow_dirs`
  are now resolved against `project_dir` instead of being passed as relative
  names that the analyzer cannot find.
  (`cortex/plan_params.py`, `tests/cortex/test_planner_cache_steps.py`)

- **Workflow result sync now resolves the right run, probes the right remote
  location, and surfaces actionable failures** — manual sync now keeps using
  the exact `run_uuid` when the named workflow is confidently matched from
  conversation history, while still falling back to server-side
  `project_id` + `workflow_label` resolution when history is stale or
  incomplete. On the Launchpad side, artifact discovery now prefers
  `remote_output_dir`, derives it as `remote_work_dir / "output"` for older
  rows, and falls back to the workflow root when an existing `output/`
  directory is empty but the actual result folders live directly under the
  workflow directory. Sync failures now persist and return the real transfer or
  artifact-discovery error so the UI shows a meaningful reason instead of a
  generic retry message. New endpoint: `POST /jobs/sync-results-by-workflow`.
  (`launchpad/app.py`, `launchpad/db.py`, `launchpad/mcp_tools.py`,
  `launchpad/mcp_server.py`, `launchpad/backends/slurm_backend.py`,
  `cortex/data_call_generator.py`, `cortex/chat_stages/overrides.py`,
  `ui/appui_block_part1.py`, `ui/appui_block_part2.py`,
  `tests/launchpad/test_app_status_endpoint.py`,
  `tests/launchpad/test_slurm_backend.py`,
  `tests/cortex/test_auto_generate_data.py`)

- **Remote SLURM intake now creates an approval gate for new submissions even
  when profile defaults are incomplete** — remote execution no longer lets a
  model-emitted `submit_dogme_job` fire before approval, `ssh_profile_id`
  placeholders like `<profile_id>` are downgraded to nickname-based profile
  lookup instead of being forwarded to Launchpad, and a saved SSH profile is
  now sufficient to build a reviewable approval gate even when
  `get_slurm_defaults` returns `found=false`. The approval summary now tells
  the user that no saved SLURM defaults were found while still presenting the
  derived remote submission parameters for approval.
  (`cortex/tool_dispatch.py`, `cortex/remote_orchestration.py`,
  `tests/cortex/test_chat_data_calls.py`)

- **Remote SLURM live status now reports scheduler concurrency and pipeline
  totals more accurately** — Launchpad status polling now tails large
  Nextflow trace files instead of reading them wholesale, falls back to stdout
  progress hints when trace reads fail, and separates unfinished-task counts
  from actual SLURM job counts in the UI. Scheduler metrics now count Nextflow
  child jobs under the workflow subtree using real SLURM short states such as
  `R` and `PD`, instead of dropping them when their working directories are
  nested below the workflow root. Pipeline progress totals now aggregate task
  families instead of treating a single active `X of Y` counter as the whole
  workflow, so earlier completed singleton tasks contribute to the displayed
  completed/total counts. Retry-heavy runs now keep retry attempts out of the
  unique pipeline total and remaining-task count: failed attempts that are
  later retried no longer inflate `Failed`, `Remaining`, or the denominator,
  while Nextflow's `retries: N` signal is surfaced as its own UI metric.
  Family-level progress now takes precedence over the top-level executor count
  when that executor count includes retried attempts, preventing `executor >
  slurm (N)` from overstating the pipeline total. The UI now labels the
  stdout-derived task-name list as a recent sample rather than implying it is
  the authoritative running-job count.
  (`launchpad/backends/slurm_backend.py`, `ui/appui_block_part2.py`,
  `tests/launchpad/test_slurm_backend.py`,
  `tests/test_slurm_backend_cache_flow.py`)

- **Cross-workflow reconcile now resolves owner paths correctly and no longer
  hard-fails when a workflow lacks an `annot/` directory** — project path
  resolution now prefers the project owner's username/UUID path instead of the
  requesting user, reconcile locate steps mark missing `annot/` folders as an
  empty result instead of an immediate fatal error, and the reconcile script
  now reports missing explicit workflow directories clearly during preflight.
  (`cortex/db_helpers.py`, `cortex/plan_templates.py`,
  `analyzer/mcp_tools.py`, `skills/reconcile_bams/scripts/reconcile_bams.py`,
  `tests/cortex/test_db_helpers.py`, `tests/analyzer/test_mcp_tools.py`,
  `tests/skills/test_reconcile_bams_scripts.py`)

- **Reconcile-driven DE follow-ups now keep using the selected workflow and no
  longer stop on a missing prior `de_results` folder** — grouped DE
  `CHECK_EXISTING` probes now treat `find_file` misses as an empty preflight
  instead of a fatal error, reconcile output parsing and summaries stay on the
  resolved reconcile workflow directory, and grouped DE prep plus saved
  edgePython results/plots now remain workflow-local under the selected
  workflow.
  (`cortex/plan_executor.py`, `cortex/plan_replanner.py`,
  `cortex/plan_params.py`, `cortex/tool_dispatch.py`,
  `tests/cortex/test_plan_executor_run_script.py`,
  `tests/cortex/test_plan_executor_concurrency.py`,
  `tests/cortex/test_planner_cache_steps.py`)

- **Each grouped DE compare now gets its own fresh `workflowN` instead of
  reusing the source workflow directory** — runtime DE execution now reserves
  the next project workflow number using the same numbering logic as Nextflow
  jobs and reconcile runs, writes prepared DE inputs under
  `workflowN/de_inputs`, and saves edgePython result tables plus plots under
  `workflowN/de_results`, which prevents repeated compares from colliding with
  source-workflow artifacts.
  (`common/workflow_paths.py`, `cortex/plan_executor.py`,
  `launchpad/nextflow_executor.py`,
  `skills/reconcile_bams/scripts/reconcile_bams.py`,
  `tests/cortex/test_plan_executor_concurrency.py`,
  `tests/cortex/test_plan_executor_run_script.py`,
  `tests/launchpad/test_nextflow_executor.py`,
  `tests/skills/test_reconcile_bams_scripts.py`)

- **Workflow switching now persists across turns and no longer reuses corrupted
  work directories** — the new chat-level workflow selector now stores the
  updated conversation state in the AGENT_PLAN cache so follow-up requests like
  listing files keep using the chosen workflow, and conversation-state rebuilds
  now sanitize trailing junk such as literal `\\n",` suffixes from cached
  `work_directory` / `run_uuid` values before Analyzer tools use them.
  (`cortex/chat_stages/quick_exits.py`, `cortex/conversation_state.py`,
  `tests/cortex/test_workflow_commands.py`,
  `tests/cortex/test_build_conv_state.py`)

---

## [3.5.2] - 2026-04-06

### Improvements

- **Remote staging is now a durable background job** — `POST /remote/stage`
  returns a `task_id` immediately and runs the rsync transfer as an
  `asyncio.Task`.  Cortex polls `GET /remote/stage/{task_id}` with an
  adaptive schedule and updates the STAGING_TASK block with live progress
  (file count, percent, speed).  Large uploads that previously timed out or
  were killed by the HTTP request deadline now run to completion.
  (`launchpad/backends/staging_worker.py` — new,
  `launchpad/app.py`, `launchpad/schemas.py`,
  `launchpad/mcp_tools.py`, `launchpad/mcp_server.py`,
  `cortex/job_polling.py`, `cortex/workflow_submission.py`)

- **Idle/stall timeout replaces the hard rsync timeout** — when progress
  tracking is active (background staging), rsync is killed only if no output
  arrives for `STAGE_IDLE_TIMEOUT_SECONDS` (default 600 s).  A safety ceiling
  of `STAGE_MAX_TOTAL_TIMEOUT_SECONDS` (default 28 800 s / 8 h) caps any
  single transfer.  Transfers that are slow but still making progress are no
  longer killed.  (`launchpad/backends/file_transfer.py`)

- **Retry endpoint for failed staging tasks** — `POST /remote/stage/{task_id}/retry`
  re-queues a failed staging task so the user can retry without re-submitting.

- **Staging poller hardening** — the Cortex staging poller now detects
  Launchpad restarts (three consecutive 404s mark the STAGING_TASK block as
  FAILED with an actionable re-submit message) and enforces a schedule
  exhaustion failsafe so polling never runs indefinitely.
  (`cortex/job_polling.py`)

- **Remote SLURM runs can now use a folder already present on the cluster**
  — users can provide a remote input folder and submit or stage a job without
  first copying the starting data into the local AGOUTIC data area. Cortex now
  carries an explicit `remote_input_path` through approval and submission,
  approval summaries show the remote path directly, Launchpad accepts remote-only
  SLURM input requests, and the SLURM backend auto-detects the remote input type
  from file extensions, registers the folder for future staged-sample reuse, and
  defaults these runs to keeping results in both places unless overridden.
  (`cortex/job_parameters.py`, `cortex/remote_orchestration.py`,
  `cortex/workflow_submission.py`, `launchpad/schemas.py`,
  `launchpad/mcp_tools.py`, `launchpad/mcp_server.py`, `launchpad/app.py`,
  `launchpad/backends/base.py`, `launchpad/backends/slurm_backend.py`,
  `skills/remote_execution/SKILL.md`)

### Bug Fixes

- **Remote-input staging approvals now preserve no-copy remote folder reuse** —
  stage-only approvals that carry a `remote:/...` input path no longer lose
  `remote_input_path` when the approval form is submitted.  The server now
  recovers remote input paths from `remote:/...` values defensively, the UI
  preserves `remote_input_path` and `staged_remote_input_path` in edited
  approval payloads, and stage-only remote-folder requests continue through the
  `use_remote_path` flow instead of falling back to placeholder local staging.
  (`cortex/remote_orchestration.py`, `ui/appui_block_part1.py`,
  `tests/cortex/test_background_tasks.py`)

- **Async remote staging worker now uses the live SLURM backend API** — the
  background `/remote/stage` worker no longer imports removed path-validation
  symbols or instantiates `SlurmBackend` with a stale SSH manager argument,
  fixing stage-only tasks that could otherwise stall before SSH validation or
  rsync startup.
  (`launchpad/backends/staging_worker.py`,
  `tests/launchpad/test_staging_worker.py`)

- **GRCh38 jobs now emit human kallisto references** — Nextflow config
  generation now reads the human kallisto index and transcript-to-gene files
  from the canonical `GRCh38` reference definition instead of falling back to
  the mouse defaults when building `kallistoIndex` and `t2g`.
  (`launchpad/config.py`, `tests/launchpad/test_config.py`,
  `tests/launchpad/test_nextflow_executor.py`)

---

## [3.5.1] - 2026-04-02

### Improvements

- **Remote stage uploads now fail before the outer MCP request times out**
  — remote sample staging now enforces an inner rsync timeout budget for
  direct and brokered uploads, sharing that budget across the
  `--skip-compress` compatibility retry so Launchpad returns a concrete stage
  failure instead of leaving Cortex with a bare `ReadTimeout('')`. Timeout
  errors now tell the user that partial files may already exist remotely and
  point to the relevant timeout knobs for unusually large samples.
  (`launchpad/backends/file_transfer.py`,
  `launchpad/backends/local_user_broker.py`,
  `launchpad/mcp_tools.py`)

- **Remote SLURM rsync now skips wasteful compression for BAM-class binaries**
  — remote staging and result sync still use rsync compression for HPC links,
  but now pass a `--skip-compress` suffix list so already-compressed genomics
  binaries such as `bam`, `bai`, `cram`, `crai`, `pod5`, and `fast5` avoid
  unnecessary CPU overhead. When a cluster's rsync is too old to support that
  option, AGOUTIC retries once without it so transfers remain compatible.
  (`launchpad/backends/file_transfer.py`,
  `launchpad/backends/local_user_broker.py`)

- **Explicit pending dataframe action controls** — `PENDING_ACTION` chat blocks
  now render dedicated Apply and Dismiss buttons in the UI instead of relying
  on a follow-up free-text `yes`. The backend executes or rejects the exact
  saved block selected by the user, avoiding accidental confirmation of a
  different pending action when multiple saved dataframe transforms exist.
  (`ui/appui_block_part1.py`, `cortex/app.py`, `cortex/pending_dataframe_actions.py`)

- **Broader dataframe intent auto-routing** — local Cortex dataframe actions
  now auto-trigger for more natural dataframe phrasing, including
  `subset DF3 to columns ...`, `keep only ... in DF2`, multi-column rename
  requests, and common group-by/summarize patterns. These requests are now
  intercepted before they fall back to generic analyzer summary calls.
  (`cortex/dataframe_actions.py`)

- **Date-organised download folders** — files downloaded or uploaded into a
  user's central data directory are now written into a `YYYY-MM-DD` subfolder
  (e.g. `data/2026-04-02/ENCFF001.bam`) instead of a single flat directory.
  Deduplication and project symlinks continue to work as before.
  (`cortex/routes/files.py`)

- **Plot metadata now persists as project memory** — every `AGENT_PLOT` block
  now auto-creates a project-scoped `plot` memory containing the chart spec,
  linked block id, dataframe ids, and descriptive metadata so charts can be
  recovered from project memory later. Plot memory deduplication is keyed by
  block id so repeated identical plots are still stored as distinct events.
  (`cortex/memory_service.py`, `cortex/chat_stages/response_assembly.py`)

### Docs

- **UI help now teaches dataframe workflows** — the deterministic help view and
  sidebar help panel now include dataframe inspection, transform, and plotting
  examples, including grouped `color by sample` prompts and saved action
  confirmation behavior.
  (`ui/appui_state.py`, `ui/appui_sidebar.py`)

- **New dataframe guide** — added `docs/DATAFRAMES.md` and updated
  `QUICK_REFERENCE.md`, `README.md`, `ui/README.md`, and `docs/CHAT_PIPELINE.md`
  to document in-memory dataframe actions, `PENDING_ACTION` blocks, and wide
  dataframe auto-melt plotting behavior.
  (`docs/DATAFRAMES.md`, `QUICK_REFERENCE.md`, `README.md`, `ui/README.md`, `docs/CHAT_PIPELINE.md`)

### Bug Fixes

- **Wide dataframe plotting respects explicit `by measure` requests** — plot
  intent selection now overrides stale synthetic x-axis values from the model
  when the user explicitly asks for `plot DF1 by measure` or `plot DF1 by sample`.
  Wide sample tables can now render grouped bars under each measure instead of
  silently reusing the sample-axis chart.
  (`cortex/chat_dataframes.py`, `ui/appui_renderers.py`)

- **Mistral inline plot tags no longer leak into chat output** — raw
  `[TOOL_CALLS]PLOT: ...` responses are now normalized into standard
  `[[PLOT: ...]]` tags before parsing, so the rendered chart appears without
  exposing tool-call syntax in assistant markdown.
  (`cortex/tag_parser.py`)

- **Plot titles and axis labels survive end-to-end rendering** — explicit
  `title`, `xlabel`, and `ylabel` values now flow from parsed plot tags into
  stored chart payloads and final Plotly layout, including combined multi-trace
  chart blocks.
  (`cortex/chat_stages/response_assembly.py`, `ui/appui_renderers.py`, `cortex/prompt_templates/first_pass_system_prompt.md`, `cortex/prompt_templates/second_pass_system_prompt.md`)

- **Skill switch blocked by unrelated running jobs** — the
  `SKILL_SWITCH_TO` output guard now checks whether the user sent a message
  *after* the running `EXECUTION_JOB`. If they did, the switch is allowed
  because the user has moved on to a new request. Previously, *any* running
  job in the project would block all skill switches, even for unrelated
  tasks like parsing a CSV while a reconcile job was still running.
  (`cortex/llm_validators.py`)

- **"List files" picks reconcile script path instead of workflow dir** —
  `_extract_job_context_from_history` now prefers pipeline (`run_type=dogme`)
  `EXECUTION_JOB` blocks over utility scripts (`run_type=script`) when
  resolving `work_dir`. Previously, if a `reconcile_bams` script ran after a
  Dogme workflow, "list files" would show files from the skill's scripts
  folder instead of the project's workflow directory. Affects both context
  injection and `_validate_analyzer_params` work_dir override.
  (`cortex/conversation_state.py`)

---

## [3.5.0] - 2026-04-02

### Refactors

- **Chat pipeline decomposition** — replaced the monolithic 1,637-line
  `chat_with_agent` function with a registry-based dispatch pipeline.
  `cortex/app.py` reduced from 2,348 → 805 lines; the endpoint itself is now
  56 lines.

- **ChatContext dataclass** (`cortex/chat_context.py`) — all per-request
  mutable state extracted into a single `@dataclass` with ~68 fields and a
  `short_circuit()` helper for early exit.

- **ChatStage protocol** (`cortex/chat_stages/__init__.py`) — each stage
  implements `name`, `priority`, `should_run(ctx)`, and `run(ctx)`. Stages
  self-register at import time and execute in priority order.

- **15 stages across 11 modules** under `cortex/chat_stages/`:
  Setup (100), Capabilities (200), Prompt Inspection (210),
  DataFrame Command (220), Memory Command (230), Memory Intent (240),
  History (300), Context Preparation (400), Plan Detection (500),
  First-Pass LLM (600), Tag Parsing (700), Overrides (800),
  Tool Execution (900), Second-Pass LLM (1000), Response Assembly (1100).

- **Pipeline runner** (`cortex/chat_pipeline.py`) — iterates stages, respects
  `should_run()` guards, and returns on the first `ctx.response` short-circuit.

- **Priority gap convention** — stages are spaced by 100 so new stages can be
  inserted without renumbering existing ones.

### Features

- **Unified token/context budget** (`cortex/context_budget.py`) — added a
  `ContextBudgetManager` that allocates the LLM context window across system
  prompt, skill content, memory, tool schemas, and conversation history using
  tiktoken (`cl100k_base`) for accurate token counting instead of character
  heuristics. Six priority-ranked budget slots with hard/soft trimming,
  min-token guarantees, and oldest-first history eviction. Both `think()` and
  `analyze_results()` in `AgentEngine` now enforce the budget before every LLM
  call.

- **Skill capability manifest** (`cortex/skill_manifest.py`) — replaced the
  flat `SKILLS_REGISTRY` dict with declarative `SkillManifest` dataclasses.
  Each skill now declares expected inputs, output types, required MCP services,
  estimated runtime, supported sample types, and routing flags
  (`analysis_only`, `switchable`). Query helpers `skills_for_service()`,
  `skills_for_sample_type()`, and `check_service_availability()` enable
  smarter planner routing and runtime availability checks. Backward-compatible
  `SKILLS_REGISTRY` dict still exported from `cortex/config.py`.

- **Project rename syncs local folder** — renaming a project now
  auto-generates a new slug from the new name and renames the on-disk
  directory (`AGOUTIC_DATA/users/{username}/{slug}/`) atomically so the UI
  name, database slug, and filesystem folder stay in sync.

- **Slug displayed in sidebar and toasts** — when a project's slug differs
  from a plain-lowercased version of its display name (e.g. `project-2026-03-27-2`
  vs `project-2026-03-27`), the sidebar now shows a `(slug)` hint next to the
  project button; project creation and rename toasts also report the actual
  folder name.

- **Rename audit trail via memory** — each project rename automatically
  creates two `finding` memories ("Project renamed from X to Y" and
  "Project folder renamed from X to Y") so the history of name changes is
  traceable from the Memories page.

- **Project creation returns full slug** — `POST /projects` and the UI
  wrapper now return `{id, slug, name}` instead of a bare string so callers
  can display the actual on-disk folder name immediately.

### Bug Fixes

- **`_suppress_all_tags` ordering** — fixed a bug in the overrides stage where
  `ctx.auto_calls` was wiped by `_suppress_all_tags()` when called after
  assignment; suppress is now called first.

- **Script-result file access now resolves workflow outputs correctly** —
  fixed Analyzer path resolution for follow-up commands after script runs,
  including `list files`, `list files in workflow2`, `parse ...csv`, and
  other `find_file` / `read_file_content` / `parse_csv_file` flows. These
  requests no longer reuse a skill script directory such as
  `skills/reconcile_bams/scripts`; they now prefer the real
  project/workflow output directory, re-resolve explicit workflow subpaths
  from the project root, and avoid falling back to `run_uuid` routing for
  normal file-browsing requests.

- **Script completion preserves actual output work directory** — standalone
  script jobs now capture the final `Outputs are in: ...` path from stdout and
  retain that as the authoritative work directory during polling and follow-up
  analysis instead of reverting to the script working directory.

- **Chat timestamps render in local time again** — block timestamps in the UI
  now treat naive stored timestamps as UTC before converting to the browser's
  local timezone, fixing displays that had regressed to raw UTC instead of PDT.

### Docs

- **`docs/CHAT_PIPELINE.md` created** — full architecture reference covering
  the pipeline flow, ChatContext fields, ChatStage protocol, stage registry,
  all 15 stages with descriptions, and a guide for adding new stages.

## [3.4.17] - 2026-04-01

### Features

- **Dual-scope memory system** — added persistent memory with both user-global
  and per-project scopes. Memories are stored in a dedicated `memories` table
  with categories: `result`, `sample_annotation`, `pipeline_step`,
  `preference`, `finding`, and `custom`.

- **Chat-line memory CRUD via slash commands** — users can manage memories
  directly from chat: `/remember`, `/remember-global`, `/forget`, `/memories`,
  `/pin`, `/unpin`, `/restore`, `/annotate`, `/search-memories`.

- **Natural language memory intent detection** — phrases like "remember
  that...", "sample X is an AD sample", "forget the memory about...", and
  "what do you remember about...?" are detected as side-effects alongside
  normal LLM responses.

- **Sample metadata annotation** — `/annotate sample1 condition=AD` and
  natural language "sample X is a Y" dual-write to both `UserFile.tags_json`
  and the memory table, creating an auditable annotation trail.

- **Auto-capture of pipeline steps** — successful workflow plan steps are
  automatically recorded as `pipeline_step` memories with deduplication by
  block + step ID.

- **Auto-capture of job results** — completed job results from the polling
  loop are automatically pinned as `result` memories with run UUID, sample
  name, workflow type, and status.

- **Dynamic memory context injection** — a `[MEMORY]` block is prepended to
  every LLM turn with pinned memories prioritized, sample annotations grouped,
  and a configurable token budget (default 2,000 tokens) with soft-delete
  filtering.

- **Memory-aware system prompt** — LLM prompt templates now instruct the model
  to reference `[MEMORY]` context for sample conditions, prior results, and
  user preferences.

- **REST API for memories** — full CRUD endpoints at `/memories` with
  filtering by project, category, pinned status; soft-delete with restore;
  pin/unpin toggle.

- **Memories UI page** — Streamlit page with project/global scope tabs,
  category filtering, pinned-only toggle, deleted-item recovery, and
  emoji-badged memory cards.

- **Sidebar memory widget** — sidebar expander showing pinned count, recent
  memories, and a link to the full memories page.

- **Dataframe memory** — users can save any conversation dataframe into
  project or global memory with `/remember-df DF5 as c2c12DF`. Named saved
  DFs are usable like regular conversation DFs: they appear in `list dfs`,
  respond to `head c2c12DF`, and the LLM receives their full row data for
  `plot c2c12DF by assay` commands.

- **Named-DF keying** — remembered named DFs use their user-given name (e.g.
  `"c2c12DF"`) as the key in `df_map` rather than a synthetic `DF900+`
  integer, so the name is consistently shown in `list dfs` output and
  referenced in head/plot commands.

- **Upgrade project memory to global** — any project-scoped memory can be
  promoted to global scope via `/upgrade-to-global <id>` (chat) or the 🌐
  button in the Memories UI. Unnamed DF memories are blocked at the service,
  API, and UI layers with a descriptive error.

- **Memories UI overhaul** — the memories page now shows the project slug
  next to each project-scoped memory (📁 slug) or a global badge (🌐 Global);
  includes an expandable ➕ Add Memory form; delete 🗑️ / restore ♻️ buttons
  per card; upgrade-to-global 🌐 button (disabled with tooltip for unnamed
  DFs); and a DF preview showing column list and name/unnamed warning.

- **Context injection for remembered DFs** — `_inject_job_context` now
  accepts `db`/`user_id`/`project_id` and detects named DF references in the
  user message. The full table data is injected as `[PREVIOUS QUERY DATA:]`
  for all skill types (ENCODE, Dogme, and others) so the LLM can plot or
  analyse saved DFs without making a new DATA_CALL.

- **`head <name>` command** — `head c2c12DF` and `head c2c12DF 20` work for
  named remembered DFs via a new `_HEAD_NAMED_RE` detection pattern with
  case-insensitive lookup.

- **`/remember-df` slash command** — `/remember-df DF5` saves unnamed,
  `/remember-df DF5 as c2c12DF` saves with alias; `"dataframe"` added to
  `VALID_CATEGORIES`; `/upgrade-to-global` and `/make-global` aliases added.

### Database

- **Alembic migration `a1b2c3d4e5f6`** — creates `memories` table with
  composite index on `(user_id, project_id, is_deleted)` and index on
  `related_file_id`.

### Tests

- **73 tests** in `tests/test_memory.py` — covers memory service CRUD
  (create, dedup, soft-delete, restore, pin/unpin, update, wrong-user,
  invalid category), listing (project+global, deleted, pinned, by-category),
  search, `upgrade_to_global` (normal, named DF, unnamed DF blocked,
  wrong-user), `remember_dataframe` (named/unnamed, `get_remembered_df_map`
  including global merge, numeric key for unnamed DFs), all slash command
  parsing (`/remember`, `/remember-global`, `/forget`, `/memories`, `/pin`,
  `/unpin`, `/restore`, `/search-memories`, `/annotate`, `/upgrade-to-global`,
  `/make-global`, `/remember-df` variants), command execution, DF integration
  (`_collect_df_map`, `_render_list_dfs`, `_render_head_df` with named keys
  and case-insensitive lookup, `head <name>` detection), and context injection
  (named DF injection for all skill types, no injection without db params).

### Docs

- **`docs/MEMORY.md` created** — full architecture reference for the memory
  system covering the data model, categories, scopes, slash commands, NL
  detection, auto-capture, context injection, DF memories, DF context injection,
  REST API, UI, and database migration.

- **`QUICK_REFERENCE.md` restructured** — rewritten as a full command reference
  with a table of contents and four new sections: Memory Slash Commands (all 11
  commands with syntax), Natural Language Memory patterns, Dataframe Quick
  Commands (`list dfs`, `head DF`, `head <name>`), and Chat Shortcuts
  (`try again` / `retry`). Existing path configuration and workflow browsing
  content is preserved.

- **Sidebar Help expander** — quick command suggestions reorganised into
  Workflows / Dataframes / Memory / Shortcuts groups, with sendable buttons for
  `/memories`, `list dfs`, and `try again`, and inline captions for
  reference-only command syntax.

## [3.4.16] - 2026-04-01

### Refactors

- **Planner decomposition** — split `planner.py` (2,127 LOC) into four
  focused modules: `plan_templates.py` (987 LOC, 12 template functions),
  `plan_classifier.py` (238 LOC, classification patterns), `plan_params.py`
  (366 LOC, parameter extraction), and a slim `planner.py` orchestration
  layer (642 LOC). All public symbols are re-exported from `cortex.planner`
  for backward compatibility.

- **App chat-helper extraction** — extracted three helper modules from
  `app.py` (3,303 → 2,340 LOC): `chat_sync_handler.py` (279 LOC, progress
  tracking, DataFrame commands, plot style params), `chat_approval.py`
  (553 LOC, plan approval flow, SSH auth), and `chat_downloads.py` (232 LOC,
  download orchestration, auto-execute). All symbols re-exported from
  `cortex.app` for backward compatibility.

### Fixes

- **Tool schema cache no longer permanently empty after startup** — if all
  MCP servers are unreachable at startup, `fetch_all_tool_schemas` now leaves
  `_CACHE_LOADED=False` so the next request retries the fetch instead of
  permanently serving an empty cache. A pre-`think()` async retry in `app.py`
  ensures tool contracts are available before the first LLM call.

- **`search_data` tool alias added for ENCODE** — the LLM occasionally
  confuses the `SEARCH_DATA` plan-chain label for a tool name; added
  `"search_data" → "search_by_biosample"` alias in `atlas/config.py`.

- **Log rotation now archives old logs into monthly folders** — rotated log
  files older than 30 days are moved into `logs/logs_YYYYMM/` directories
  (e.g. `logs/logs_202603/` for March 2026).

- **Tool schema retry no longer shadows module-level imports** — the
  pre-`think()` async retry of `fetch_all_tool_schemas` now uses module-level
  aliases (`import cortex.config as _cfg`) instead of `from` imports, preventing
  Python's scoping rules from shadowing the module-level `SERVICE_REGISTRY`
  binding for the entire `chat_with_agent` function.

## [3.4.15] - 2026-03-31

### Features

- **UI chat renderer modularization under strict file-size limits** — split
  the Streamlit chat block rendering pipeline into focused modules
  (`appui_block_part1.py`, `appui_block_part2.py`, `appui_sidebar.py`,
  `appui_chat_runtime.py`, `appui_services.py`) so no active appui module
  exceeds 1000 LOC while preserving prior behavior.

- **AppUI consolidation cleanup** — removed intermediate compatibility shim
  layers and consolidated service/workflow helpers into `appui_services.py`
  to reduce file sprawl and simplify runtime imports.

### Fixes

- **Manual result sync timeout raised for very large file copy-back** —
  `LAUNCHPAD_SYNC_TIMEOUT` now defaults to `9600s`, giving
  `sync_job_results` substantially more time to finish very large transfers
  before timing out.

- **Sync-result run UUID payload is now always safely bound** — the Cortex
  chat execution path now initializes `_sync_run_uuid` before conditional tool
  result handling, preventing `UnboundLocalError` when sync metadata is absent
  but the final plan payload still checks for a sync run UUID.

- **Post-split AppUI helper tests now resolve extracted functions correctly** —
  the AST-based UI helper test loader now follows functions moved from
  `appUI.py` into extracted modules and injects wrapper `_impl` call targets,
  restoring focused helper coverage after the AppUI modularization.

- **Launchpad status fast path now tolerates backends without transfer-detail
  helpers** — pending local copy-back status responses now treat
  `get_transfer_detail` as optional, avoiding `AttributeError` in lightweight
  fake backends while preserving live transfer details when supported.

- **Reconcile wrapper JSON output remains parseable during live child-process
  streaming** — `reconcile_bams.py` now routes streamed child stdout away from
  wrapper stdout in `--json` mode so machine-readable reconcile payloads are
  not polluted by live execution output.

- **Script-backed reconcile runs are now labeled correctly in the UI** —
  execution cards for allowlisted script jobs such as `reconcile_bams` now
  render as `Skill Script` instead of `Nextflow Job`, while preserving the
  existing live execution monitoring surface.

- **UI execution timestamps now consistently render in local time** —
  execution/job timestamps now treat naive API timestamps as UTC before
  converting them for display, preventing UTC wall-clock values from leaking
  into the Streamlit UI on local-time systems such as PDT.

- **Import-time session-state crash resolved in split block renderer** —
  `appui_block_part2.py` no longer executes leaked top-level runtime code on
  import, preventing `AttributeError: st.session_state has no attribute
  active_project_id` before initialization.

- **Missing renderer callback wiring fixed** — `_block_timestamp` is now
  correctly passed into `render_block_part1`, resolving `NameError` during
  `USER_MESSAGE` rendering.

- **Raw fallback payload no longer shown for handled user blocks** — split
  renderers now return handled flags and fallback rendering is centralized in
  `appUI.py`, preventing duplicate raw lines such as
  `[USER_MESSAGE] {'text': '...'}` from appearing in the GUI.

## [3.4.14] - 2026-03-31

### Features

- **Safe project rename semantics (display-name first)** — `PATCH /projects/{project_id}`
  now supports name-only renames without auto-changing slug/folder paths,
  preserving stable `project_id` identity and on-disk project layout unless an
  explicit slug change is requested.

- **Project rename API response now returns canonical name** — successful
  project updates now return `{status, id, name, slug}` so UI/API callers can
  immediately render the authoritative updated project name.

- **Extended timeout control for manual result sync** — Launchpad MCP adds
  `LAUNCHPAD_SYNC_TIMEOUT` (default `9600s`) used specifically for
  `/jobs/{run_uuid}/sync-results` requests, decoupling long sync operations
  from shorter generic MCP request timeouts.

### Fixes

- **Owner-scoped duplicate project-name conflicts now rejected cleanly** —
  project rename enforces a normalized, case-insensitive duplicate check within
  the owner scope and returns `409` with a clear error message.

- **Project access labels now stay consistent after rename** — all
  `project_access.project_name` rows for the project are synchronized on rename
  so shared/project member views reflect the updated name consistently.

- **UI rename feedback is now actionable** — Projects dashboard rename flow now
  surfaces specific error details for conflict (`409`) and authorization (`403`)
  and displays the canonical server-returned name on success.

- **Remote result copy-back now materializes symlink targets** — Launchpad file
  transfer download path now uses `copy_links=True` so symlinked artifacts are
  copied as concrete files into local workflow directories.

- **Manual sync timeout failures now return actionable guidance** — MCP sync
  now catches `httpx.ReadTimeout` and surfaces explicit next steps (check job
  status/transfer state or increase `LAUNCHPAD_SYNC_TIMEOUT`).

- **Sync commands no longer trigger dogme pipeline gate** — "sync workflow5
  locally" was intercepted by the LLM which generated dogme run tags, bypassing
  the sync auto-generator.  Added a sync-results override that fires before the
  LLM tag gate, ensuring sync requests always route directly to the launchpad
  `sync_job_results` endpoint.

- **SLURM task completion now tracked correctly** — Remote Nextflow tasks
  previously showed as "Running" but never "Completed" in the task dock.
  Three root causes: (1) the remote trace file reader used a fragile
  `find … -exec ls` shell pipeline that silently returned empty output —
  replaced with a simple `cat *_trace.txt` glob; (2) the stdout line parser
  took the first word after `]` as the task name, but SLURM Nextflow output
  prefixes lines with `Submitted process >` — now strips everything before
  `>` to extract the real task name; (3) stdout `✔` markers were only used
  to exclude tasks from Running but never counted as Completed — now counted
  as completed (with FAILED going to failed) when the trace file has no
  results.  ANSI escape codes in `slurm-*.out` are also stripped before
  parsing, and cross-naming scheme deduplication prevents double-counting
  between trace (`basecall:basecallWorkflow:X`) and stdout
  (`mainWorkflow:X`) task names.

- **UI timestamps now display in local time** — All formatted timestamps
  (job started/completed, plan steps, chat blocks, log entries) are converted
  from UTC to the server's local timezone before rendering.

- **Non-blocking result sync prevents empty-folder copy-back** — Manual
  `sync_job_results` previously awaited the full rsync inline, so the cortex→
  launchpad MCP client timeout (120 s) would kill the transfer mid-stream for
  large BAM runs, leaving empty directories locally.  `sync_results_to_local`
  now fires the rsync as a background task and returns immediately; the chat
  shows a brief acknowledgement ("📥 sync started — track progress in the task
  dock") while real-time rsync progress (current file, files transferred, speed)
  is displayed in the task dock's Sync segment, matching the existing download
  progress UX.  The cortex→launchpad MCP client timeout is also raised to
  `LAUNCHPAD_STAGE_TIMEOUT` (default 3600 s) for all launchpad tool calls, and
  the UI chat request timeout is raised from 300 s to 900 s.

### Tests

- Expanded Cortex project rename coverage in
  `tests/cortex/test_project_endpoints.py` for:
  successful rename with stable slug-by-default behavior,
  duplicate-name rejection (`409`), unauthorized rename rejection (`403`),
  project ID stability, and post-rename block accessibility.

- Added project-files regression in
  `tests/cortex/test_project_management.py` to verify files remain accessible by
  existing `project_id` after rename.

- Updated Launchpad transfer expectation in
  `tests/launchpad/test_file_transfer.py` for `copy_links=True` download mode.

- Added sync timeout/read-timeout behavior tests in
  `tests/launchpad/test_mcp_tools.py` for `LAUNCHPAD_SYNC_TIMEOUT` and
  actionable timeout messaging.

- Verified focused suites passing:
  `tests/cortex/test_project_endpoints.py` and
  `tests/cortex/test_project_management.py`.

## [3.4.13] - 2026-03-28

### Features

- **Cross-project reconcile syntax support (`project:workflow`)** —
  `reconcile_bams` parameter extraction now supports prompts like
  `sample in projectX:workflowN`, resolves to absolute workflow directories
  when base path context is available, and otherwise keeps deterministic
  relative `project/workflow` references.

- **Manual SLURM result copy-back retry command** — added end-to-end manual
  synchronization support for remote-to-local outputs through Launchpad
  (`POST /jobs/{run_uuid}/sync-results`), MCP exposure (`sync_job_results`),
  and Cortex natural-language auto-routing for sync/retry phrasing.

- **Phase 1 XgenePy service integration** — added a first-class local
  `xgenepy_mcp` service with strict relative-path validation, metadata contract
  checks (`sample_id`, `strain`, `allele` required), canonical output
  enforcement (`fit_summary.json`, `assignments.tsv`, `proportion_cis.tsv`,
  `model_metadata.json`, `run_manifest.json`, `plots/`), and clear dependency
  failure messaging when XgenePy is not installed.

- **Planner, skill, and approval wiring for XgenePy** — added dedicated
  `xgenepy_analysis` skill routing, deterministic plan support for
  `run_xgenepy_analysis`, and approval-before-run gating via
  `RUN_XGENEPY` step classification.

- **Analyzer parser support for XgenePy canonical outputs** — added
  `parse_xgenepy_outputs` in analyzer engine/MCP/REST with secure path checks
  and structured parsing of manifest, summaries, tabular artifacts, and plots.

- **Real-time rsync transfer progress** — rsync result sync now streams
  stdout line-by-line, parsing current filename, file count
  (`xfr#N, to-chk=X/Y`), and transfer speed.  Progress is surfaced via a
  new `transfer_detail` field on `JobStatusExtendedResponse` and displayed
  as a live info callout in the UI during active sync.

### Fixes

- **SLURM task completion now tracked correctly** — Remote Nextflow tasks
  previously showed as "Running" but never "Completed" in the task dock.
  Three root causes: (1) the remote trace file reader used a fragile
  `find … -exec ls` shell pipeline that silently returned empty output —
  replaced with a simple `cat *_trace.txt` glob; (2) the stdout line parser
  took the first word after `]` as the task name, but SLURM Nextflow output
  prefixes lines with `Submitted process >` — now strips everything before
  `>` to extract the real task name; (3) stdout `✔` markers were only used
  to exclude tasks from Running but never counted as Completed — now counted
  as completed (with FAILED going to failed).  ANSI escape codes in
  `slurm-*.out` are also stripped before parsing.

- **SLURM result copy-back now works on external/mounted filesystems** —
  rsync downloads previously failed with "Operation not permitted" on
  filesystems that don't support `utime()` or `chmod()` (e.g. exFAT/NTFS
  on `/media/`).  Added `--omit-dir-times` and `--no-perms` flags to both
  the direct rsync path and the local auth broker rsync path.

- **Broker rsync downloads no longer strip trailing slash from remote source** —
  The local auth broker applied `_normalize_local_rsync_source()` to all
  sources including remote `user@host:path/` strings.  `Path()` stripped the
  trailing slash, causing rsync to copy the directory itself (creating a
  nested subdirectory) instead of its contents.  The broker now only
  normalizes local sources (`":"` detection).

- **rsync transfer failures now surface the actual error** —
  `_copy_selected_results_to_local()` previously ran `_verify_local_result_artifacts()`
  before checking `transfer.get("ok")`, masking rsync errors behind generic
  "missing artifacts" messages.  Now checks rsync success first and raises
  with the actual rsync stderr on failure.

- **Manual sync behavior now returns structured non-SLURM responses** —
  sync requests on non-SLURM runs now return `status=not_applicable` payloads
  instead of hard API failures.

- **Manual sync failure messages now include actionable details** —
  Launchpad MCP sync error handling now preserves HTTP status/detail context
  and avoids empty opaque exception text.

- **Chat queries on old projects no longer redirect to the latest project** —
  The sidebar Project ID text_input used a value-comparison sync on every
  rerun that could silently revert `active_project_id` when Streamlit's
  widget state lagged behind programmatic updates during rapid reruns
  (welcome auto-send, bootstrap rerun, auto-refresh).  Replaced the fragile
  grace-counter approach with an `on_change` callback that fires **only** on
  explicit user edits.  `st.chat_input` is also now captured before bootstrap
  reruns so the one-shot prompt value is persisted before any `st.rerun()`
  can discard it.

- **Sidebar project switch now updates server-side last-project** — Clicking
  a project in the sidebar now calls `PUT /user/last-project` so that session
  recovery (e.g. WebSocket reconnect) lands on the correct project instead
  of the last project that received a `/chat` call.

- **"List workflows" now uses the correct project directory** — The auto-call
  generator and parameter validator both extracted `work_dir` from
  conversation history (most recent job), which could point to an unrelated
  skill scripts directory instead of the project root.  Both
  `_auto_generate_data_calls()` and `_validate_analyzer_params()` now prefer
  the explicitly-passed `project_dir` (resolved from the DB) for
  project-level browsing commands like "list workflows".

- **Cross-project reconcile plans now resolve absolute workflow paths** —
  When the current project has no prior jobs, the planner had no
  `candidate_base_dirs` to resolve cross-project `project:workflow`
  references, producing relative paths that the analyzer couldn't find.
  The user root directory (parent of `project_dir`) is now seeded as a
  fallback base directory so all workflow paths are absolute.

- **Reconcile output directory defaults to the active project** — Previously
  the output directory was derived from `os.path.commonpath()` of the source
  workflow roots, which would write into the source project instead of the
  current one.  Now defaults to `project_dir` (the active project) when no
  explicit output directory is specified.

- **"Try again" / "retry" command replays the last failed prompt** — Typing
  `try again` or `retry` in the chat re-sends the last prompt that failed
  (HTTP error or plan execution failure).  Works repeatedly and clears
  automatically on success.

- **BAM-detail fallback now prefers workflow/work_dir discovery over blind
  run UUID dispatch** — BAM-detail auto-calls now prioritize explicit
  workflow context and use safe `list_job_files(work_dir=...)` +
  `find_file(...)` flow when available.

- **Analyzer param resolution now blocks non-run UUID forwarding for BAM
  detail requests** — project/block UUID values are no longer sent as
  analyzer `run_uuid`; resolution now requires workflow-associated context
  and returns a controlled fallback when no completed run can be resolved.

- **Workflow discovery calls are now explicitly allowed during BAM fallback**
  — safe project-root workflow discovery listing for BAM requests is no longer
  blocked by the BAM guardrail.

### Tests

- Added focused coverage for cross-project reconcile extraction and manual sync
  routing/transport behavior in:
  `tests/cortex/test_planner_cache_steps.py`,
  `tests/cortex/test_auto_generate_data.py`,
  `tests/launchpad/test_slurm_backend.py`,
  `tests/launchpad/test_mcp_server.py`, and
  `tests/launchpad/test_mcp_tools.py`.

- Added `test_local_broker_rsync_download_preserves_remote_trailing_slash`
  to verify the broker does not strip trailing slashes from remote rsync
  sources.

- Updated `test_copy_selected_results_accepts_verified_local_artifacts_after_rsync_warning`
  → renamed to `test_copy_selected_results_fails_fast_on_rsync_error` to
  reflect the new fail-fast behavior when rsync reports failure.

- Added `transfer_detail` to fake backend in
  `test_get_job_status_repolls_completed_slurm_job_while_local_copyback_pending`.

- Added and updated focused regressions in
  `tests/cortex/test_auto_generate_data.py` and
  `tests/cortex/test_pure_helpers2.py` to cover:
  explicit-workflow BAM requests, invalid UUID rejection, controlled fallback,
  and workflow-discovery pass-through behavior.

- Verified focused suites passing:
  `tests/cortex/test_auto_generate_data.py`,
  `tests/cortex/test_pure_helpers2.py`, and
  `tests/cortex/test_validation.py`.

## [3.4.12] - 2026-03-28

### Fixes

- **BAM-detail requests now use supported Analyzer tool flow instead of
  unknown-tool failures** — BAM-detail intent in `analyze_job_results` now
  auto-generates `list_job_files` first, so file triage starts with an
  explicit run/workflow file listing and proceeds through supported tools only.

- **`show_bam_details` compatibility path now routes safely to file listing**
  — hallucinated `show_bam_details` calls are now mapped to
  `list_job_files` instead of unsupported execution paths.

- **Analyze-job-results guidance now explicitly enforces supported BAM
  fallback behavior** — skill instructions now require list-first triage and
  explicitly prohibit unsupported BAM-inspection tool calls.

### Tests

- Added focused regression coverage for BAM-detail fallback auto-generation in
  `tests/cortex/test_auto_generate_data.py`.

- Verified focused suites passing:
  `tests/cortex/test_auto_generate_data.py` and
  `tests/cortex/test_validation.py`.

## [3.4.11] - 2026-03-28

### Security

- **User-controlled file references are now strictly relative-only across
  cross-project and analyzer proxy endpoints** — path validation now rejects
  absolute paths, Windows drive-style absolute paths, traversal (`..`), null
  bytes, and unsafe wildcard/shell-like tokens before any downstream file
  handling.

- **Cross-project staging now supports additive logical file references with
  strict ambiguity handling** — `SelectedFileInput` now accepts either
  `relative_path` or `logical_reference`, and staging returns `422` when a
  logical selector resolves to zero or multiple matches (including source
  project-name mismatches).

- **User-data responses no longer leak absolute filesystem paths** — user file
  payloads and link responses now expose stable relative paths (for example,
  `data/<filename>`) instead of host-absolute path values.

### Tests

- Added focused regressions for analyzer proxy path rejection,
  cross-project logical-reference resolution and ambiguity semantics, and
  user-data no-absolute-path response guarantees.

- Verified focused suites passing:
  `tests/cortex/test_cross_project_browser.py`,
  `tests/cortex/test_cross_project_analysis_action.py`,
  `tests/cortex/test_analyzer_proxy.py`, and
  `tests/cortex/test_user_data.py`.

## [3.4.10] - 2026-03-28

### Improvements

- **Cross-project routes are now wired into Cortex app routing** —
  `cortex/app.py` now includes the cross-project router so
  `/cross-project/*` endpoints are reachable through the main API.

- **Cross-project API schema contracts are now explicitly defined** —
  `cortex/schemas.py` now includes the missing cross-project request/response
  models required by route imports and endpoint response models.

- **Default cross-project scan behavior is now bounded for large trees** —
  `cortex/routes/cross_project.py` now stops recursive browse/search scans once
  the response limit is reached by default, while preserving
  `truncated=true`; callers can opt into full counting via
  `total_count_full_scan=true`.

### Fixes

- **Project-stats regression in test-only SQLite fixtures is quarantined at the
  fixture layer** — `tests/cortex/test_project_management.py` now creates the
  `dogme_jobs` table in its in-memory test database so
  `TestProjectStats.test_stats_basic` does not fail on missing-table errors.

### Tests

- Added focused cross-project coverage for router reachability, schema import
  validity, and default early-stop behavior in browse/search with correct
  truncation semantics.

- Verified targeted regression set:
  `tests/cortex/test_cross_project_browser.py`,
  `tests/cortex/test_cross_project_analysis_action.py`,
  `tests/cortex/test_project_management.py::TestProjectStats::test_stats_basic`,
  and `tests/cortex/test_project_endpoints.py`.

## [3.4.9] - 2026-03-26

### Fixes

- **Reconcile execution now runs the immutable `reconcileBams.py` workflow in a
  standard `workflowN` directory** —
  `skills/reconcile_bams/scripts/reconcile_bams.py` no longer performs its own
  fallback merge path as the final algorithm. It now resolves the shared
  annotation GTF from workflow config artifacts when possible, requires manual
  override when provenance is missing or ambiguous, stages symlinked BAM inputs
  under `workflowN/input`, and invokes `reconcileBams.py` with the native
  `--bams`, `--annotation`, `--out_prefix`, `--outdir`, and optional tuning
  arguments, writing outputs directly under `workflowN`.

- **Reconcile approval editing now lives on the active Streamlit UI entrypoint**
  — `ui/appUI.py` now renders the reconcile-specific approval form that lets
  users edit annotation path, output root/prefix, ID/tag prefixes, thread and
  filtering controls, and review annotation provenance before approval, while
  the legacy `ui/app.py` is no longer used for this flow.

- **Reconcile approvals now preserve the real script contract through
  submission** — `cortex/app.py` now carries execution defaults and annotation
  evidence from preflight into the approval payload, and
  `cortex/workflow_submission.py` rebuilds wrapper arguments from approved BAM
  inputs and edited reconcile parameters so the submitted script-backed run
  stays aligned with the underlying `reconcileBams.py` CLI.

- **Shared environment metadata now declares the reconcile runtime dependency**
  — `environment.yml` now lists `pysam`, matching the existing dependency of
  `skills/reconcile_bams/scripts/reconcileBams.py`.

- **Reconcile workflow approvals now use plan-aware script review instead of
  generic Dogme job forms** — `cortex/app.py` now builds
  `gate_action=reconcile_bams` approval payloads directly from reconcile
  preflight output, including validated BAM inputs, resolved shared reference,
  annotation GTF provenance, and output destination, while `ui/appUI.py` renders
  a dedicated reconcile approval summary instead of unrelated
  sample/mode/input-type fields.

- **Plan-owned reconcile script execution now advances cleanly after
  approval** — approving a reconcile gate now marks the plan’s
  `REQUEST_APPROVAL` step complete, advances the workflow pointer to the
  `RUN_SCRIPT` step, submits the allowlisted reconcile script through the
  script-backed job path, and avoids falling back into the generic
  workflow-plan resume behavior that previously left reconcile stuck or
  misrouted.

- **Script-backed workflow plans now persist and complete their run step
  correctly** — `cortex/workflow_submission.py` now stores `run_type` on the
  execution job payload and maps launched script jobs onto `RUN_SCRIPT` plan
  steps, while `cortex/job_polling.py` now completes/fails that step on job
  status updates and resumes the remaining plan without incorrectly triggering
  post-Dogme auto-analysis.

- **Approval-time SLURM safety checks now block mode/path drift** —
  `cortex/remote_orchestration.py` now rejects approvals when requested vs
  extracted execution mode diverge, requested absolute input path differs from
  extracted input path, or a rewritten absolute mount path shows duplicated
  `/media/` segments.

- **Stale staged-sample metadata is no longer reused for remote cache hits** —
  staged sample lookup now invalidates and skips reuse when the stored input
  fingerprint is empty (`e3b0...`) and when an explicitly provided input source
  path mismatches the staged source.

- **Empty-input fingerprints cannot trigger remote data-cache reuse** —
  SLURM cache preflight now forces `data_action=stage` for empty/invalid
  fingerprints instead of incorrectly selecting reuse from cache metadata.

- **Remote workflow recovery now avoids duplicate workflow-plan creation** —
  when resuming remote analysis for the same sample without a `run_uuid`,
  Cortex now reuses the active `remote_sample_intake` workflow block rather
  than creating a second concurrent workflow shell.

- **SLURM stage/run transition deadlock hardening in submission flow** —
  `cortex/workflow_submission.py` now advances `stage_input` to
  `RUNNING`/`COMPLETED` before submit for non-stage-only SLURM runs, and marks
  both `stage_input` and `run_dogme` as `FAILED` when submission fails (including
  no `run_uuid` responses), preventing stale
  `check_remote_stage=COMPLETED` + `stage_input=PENDING` plan states.

- **Parameter extraction now preserves explicit local intent and absolute
  paths** — `cortex/job_parameters.py` now keeps absolute input paths unchanged,
  prefers absolute over relative path matches, and lets explicit local language
  override stale SLURM context from prior approvals.

### Tests

- Updated reconcile script regressions in
  `tests/skills/test_reconcile_bams_scripts.py` to cover workflow-config GTF
  resolution, `workflowN` root-output layout, and the real `reconcileBams.py`
  execution path when `pysam` is available, while skipping BAM-construction
  fixtures cleanly in environments where that compiled dependency is missing.

- Updated reconcile submission regressions in
  `tests/cortex/test_background_tasks.py` to assert that approval-time edits are
  converted into the expected wrapper/script argument set before Launchpad
  submission.

- Added focused reconcile approval lifecycle regressions in
  `tests/cortex/test_background_tasks.py` covering:
  reconcile-specific approval-gate payload generation from preflight output,
  script submission updating the `RUN_SCRIPT` plan step, and job-polling
  completion that closes the reconcile run step and resumes the plan.

- Added/extended focused remote orchestration and submission regressions in
  `tests/cortex/test_background_tasks.py` for:
  empty-fingerprint staged-sample invalidation, explicit source-mismatch
  invalidation, active workflow reuse without duplication, requested
  execution-mode mismatch rejection, and no-`run_uuid` failure-state
  propagation.

- Added extraction regressions in `tests/cortex/test_extract_params.py` for
  explicit-local override behavior and absolute BAM path preservation.

## [3.4.8] - 2026-03-26

### Improvements

- **New `reconcile_bams` skill registration and routing** — added
  `reconcile_bams` to Cortex skill and Launchpad service registries and added
  reconcile-intent keyword routing in `cortex/llm_validators.py`.

- **Deterministic reconcile planning flow with preflight-before-approval** —
  `cortex/planner.py` now detects reconcile requests, extracts reconcile
  parameters (including optional annotation GTF path), and generates a scoped
  plan sequence:
  `LOCATE_DATA -> CHECK_EXISTING(helper) -> CHECK_EXISTING(preflight) -> REQUEST_APPROVAL -> RUN_SCRIPT -> WRITE_SUMMARY`.

- **Workflow-reference helper for Nextflow configs** — added
  `skills/reconcile_bams/scripts/check_workflow_references.py` to inspect
  per-workflow Nextflow config artifacts (including includes), normalize
  reference aliases, and report hard failures for mixed/missing/ambiguous
  references.

- **Reconcile script now executes real workflow-scoped preparation and run** —
  `skills/reconcile_bams/scripts/reconcile_bams.py` now enforces BAM filename
  contract (`<sample>.<reference>.annotated.bam`), rejects mixed references,
  resolves default GTF from `launchpad.config.REFERENCE_GENOMES`, supports
  explicit manual GTF override with reference checks, creates dedicated
  `workflow_reconcile_<timestamp>` directories, symlinks inputs, and writes
  reconcile artifacts (reconciled BAM + manifest, plus index when available).

- **Manual-GTF follow-up pause semantics in replanner** —
  `cortex/plan_replanner.py` now parses reconcile preflight JSON output and
  converts dependent approval step state to `FOLLOW_UP` when
  `status=needs_manual_gtf` is returned.

### Tests

- Added focused reconcile script tests in
  `tests/skills/test_reconcile_bams_scripts.py` covering:
  filename contract rejection, mixed-reference rejection, default-GTF success,
  manual-GTF-needed follow-up state, and manual-GTF preflight success.

- Added reconcile planner tests in `tests/cortex/test_planner_cache_steps.py`
  for plan-type detection, helper/preflight-before-approval ordering, and
  annotation-GTF parameter extraction.

- Added reconcile replanner extraction coverage in
  `tests/cortex/test_plan_replanner_reconcile.py`.

## [3.4.7] - 2026-03-24

### Improvements

- **Executor parallel-batch bridge for safe step kinds** —
  `cortex/plan_executor.py` now executes dependency-ready batches in parallel
  for `LOCATE_DATA`, `SEARCH_ENCODE`, and `CHECK_EXISTING` via
  `asyncio.gather`, while non-allowlisted and approval-sensitive steps remain
  sequential.

- **Deterministic persistence ordering for parallel batches** — batched step
  outcomes are now persisted in deterministic order (plan order, then step id)
  regardless of completion timing.

- **Hybrid-first planner bridge for six non-core flows** —
  `cortex/planner.py` now attempts hybrid planning first for
  `compare_samples`, `download_analyze`, `summarize_results`,
  `parse_plot_interpret`, `compare_workflows`, and
  `search_compare_to_local`, then falls back to deterministic templates only
  on explicit bridge failure reasons.

- **Plan-type mismatch safety in hybrid bridge** — hybrid output is now
  rejected for scoped flows when returned `plan_type` does not match the
  requested flow, and deterministic fallback is used.

- **Strict pre-dispatch plan contract validation** — added a centralized
  validator (`cortex/plan_validation.py`) and enforced validation at executor
  entry so unknown step kinds, invalid dependencies, malformed step shapes,
  and project-scope mismatches fail before any step dispatch.

- **Hybrid fragment-plan composition for planner output** — planner flow now
  supports composing reusable plan fragments with deterministic step-id remap,
  dependency rewrite, and optional provenance metadata checks so fragment-based
  plans can be merged safely.

- **Project-scoped plan metadata continuity** — workflow plans now carry
  explicit `project_id` and `plan_instance_id` metadata through planning and
  execution boundaries to reduce cross-plan leakage risk in parallel sessions.

- **`chat_with_agent` decomposition** — extracted tag parsing, tool-call
  dispatch, and embedded dataframe/plot resolution from `cortex/app.py` into
  owned helper modules:
  `cortex/tag_parser.py`, `cortex/tool_dispatch.py`, and
  `cortex/chat_dataframes.py`. This reduces the size of the app-level chat
  orchestration path while preserving existing runtime behavior.

- **Automatic skill-script allowlist discovery** — Launchpad now scans
  `skills/<skill_key>/scripts/*.py` at startup, adds discovered script roots to
  `LAUNCHPAD_SCRIPT_ALLOWLIST_ROOTS`, and registers script identifiers in
  `LAUNCHPAD_SCRIPT_ALLOWLIST_IDS` as `<skill_key>/<script_name>`.

- **Native Apptainer path for SLURM Nextflow runs** — Launchpad SLURM
  submissions now generate `apptainer { enabled = true }` config instead of
  relying on Singularity compatibility mode, disable host filesystem
  auto-mounts for task launches via `--no-mount hostfs`, and keep explicit
  workflow/input/reference bind paths only.

- **Shared per-user Apptainer cache for remote workflows** — remote SLURM
  runs now point both `apptainer.cacheDir` and exported
  `NXF_APPTAINER_CACHEDIR`/`NXF_SINGULARITY_CACHEDIR` at a stable
  `${remote_base_path}/.nxf-apptainer-cache` directory so container images are
  reused across workflow runs for the same user instead of being repulled into
  each workflow folder.

- **BED chromosome-count utility now emits plot-ready dataframes** —
  `skills/analyze_job_results/scripts/count_bed.py` now accepts one or more
  BED files, parses `sample/genome/strand/modification` from filenames like
  `<sample>.<genome>.<plus|minus>.<modification>.filtered.bed`, aggregates
  counts per `Sample`, `Genome`, `Modification`, and `Chromosome`, and emits
  structured JSON when invoked with `--json`.

- **Modification-by-chromosome planning from workflow `bedMethyl/` outputs** —
  `analyze_job_results` can now satisfy requests such as “count inosine
  modifications by chromosome” by listing `.bed` files under the workflow’s
  `bedMethyl/` folder, selecting matching `plus`/`minus` files for the
  requested modification, and combining them into a single per-genome result
  dataframe.

- **Allowlisted script results now flow into chat dataframes** — Launchpad
  `run_allowlisted_script` now parses structured JSON stdout into a dataframe
  payload, and Cortex embeds that payload as a conversation dataframe so
  downstream plotting can target BED-count results directly.

- **Smarter fallback charts for BED chromosome-count dataframes** — explicit
  plot requests against the BED-count dataframe shape now default to a grouped
  bar chart using `Chromosome` on the x-axis, `Count` on the y-axis, and
  `Genome` as the grouping dimension when multiple genomes are present.

### Fixes

- **Workflow `GENERATE_PLOT` and `INTERPRET_RESULTS` steps now complete
  deterministically** — `cortex/plan_executor.py` now derives plot payloads
  directly from prior parsed result tables and generates markdown
  interpretations/summaries for workflow-local result review instead of
  leaving those steps pending under special handling.

- **Workflow outputs now surface in both step detail and the main chat
  flow** — the UI renders workflow-local chart payloads inline inside
  completed `WORKFLOW_PLAN` steps and mirrors completed plot/interpretation
  outputs into the related `AGENT_PLAN` chat bubble so results no longer feel
  trapped inside step expanders.

- **Nextflow live status refresh restored without reintroducing full-page
  flicker everywhere** — `ui/appUI.py` now uses fragment refresh for normal
  chat/workflow updates, restores whole-page reruns only for active
  `EXECUTION_JOB` and `DOWNLOAD_TASK` cards, and bootstraps that heavier
  refresh path automatically when a fragment first discovers an active job.

- **Execution-job freshness indicators now reflect real poll timestamps** —
  live status polls store per-run update times in session state and preserve
  them in the job card instead of always displaying a misleading synthetic
  "Updated 0s ago" timestamp.

- **Delete/archive confirmations no longer fight the auto-refresh loop** —
  destructive actions now pause refresh through stable confirmation-aware
  suppression logic, preserving working project deletion/archive flows without
  leaving Nextflow monitoring stuck in a suppressed state afterward.

- **Summarize-results requests now enter the planning path** — broadened
  multi-step classification so summarize/interpret/explain-results requests are
  routed into `WORKFLOW_PLAN` generation instead of falling through to the
  single-turn analyzer flow.

- **`PARSE_OUTPUT_FILE` now derives concrete CSV parse targets from prior
  inventory results** — parse steps now reuse the analyzer `list_job_files`
  result set, prioritize key result files such as `qc_summary` and
  `final_stats`, and dispatch `parse_csv_file` with explicit `file_path` and
  `work_dir` params instead of failing on missing arguments.

- **Summarize/parse templates now scope initial discovery to result CSVs** —
  `summarize_results` and `parse_plot_interpret` start by locating `.csv`
  outputs only, reducing noisy full-directory inventories and aligning the
  follow-up parse step with the files the analyzer already uses for summaries.

- **Workflow-plan chat panel now documents execution lifecycle and step
  outcomes** — the GUI now shows overall plan status, current step, per-step
  state, timestamps, dependencies, and persisted result/error payloads for
  auto-executed safe steps.

- **Decomposition test boundary updates** — Cortex chat tests now patch tool
  dispatch dependencies at their new ownership boundary in
  `cortex.tool_dispatch` rather than through `cortex.app`, restoring focused
  chat/data-call test coverage after the extraction.

- **Follow-up regression hardening for extracted/runtime boundaries** — fixed
  stale background-task session patching, stabilized approval/remote-stage chat
  tests, aligned SLURM cache-flow submit assertions with current backend
  contracts, and repaired UI/helper test harness gaps so the focused regression
  set covering these areas passes again.

- **Apptainer compatibility bootstrap now works in mixed-runtime clusters** —
  the generated SLURM sbatch bootstrap now provides compatibility shims in both
  directions (`singularity -> apptainer` and `apptainer -> singularity`) and
  mirrors engine-prefixed environment variables between
  `SINGULARITYENV_*` and `APPTAINERENV_*` when only one runtime binary is
  present on the cluster path.

- **Remote SLURM submit path restored after cache-root wiring** — fixed a
  Launchpad submission regression where remote config generation referenced an
  undefined `remote_roots` variable while computing the shared Apptainer cache
  path, which caused approval-driven `submit_dogme_job` calls to fail with an
  HTTP 500 before job submission.

- **Remote staging now supports single-file uploads** — brokered and direct
  rsync input transfers no longer force a trailing slash onto local upload
  sources, so approval-driven remote runs can stage a single BAM/FASTQ/POD5
  file without rsync misclassifying it as a directory.

- **Workflow-relative BED script chaining now resolves against the workflow
  directory** — `find_file` results that return relative BED paths are now
  resolved against analyzer `work_dir` before invoking the allowlisted script,
  avoiding incorrect resolution against the code checkout.

- **BED counting no longer passes invalid script working directories** — the
  chained script invocation now omits `script_working_directory` for workflow
  BED counting, preventing Launchpad allowlist validation failures for
  non-allowlisted workflow folders.

- **Deferred plotting language no longer triggers immediate chart rendering** —
  phrases like “so the data can be plotted later” are no longer treated as an
  immediate visualization request by the plot fallback logic.

### Tests

- Added focused plan-contract coverage for duplicate IDs, unknown kinds,
  dependency integrity, cycle detection, approval-policy constraints,
  provenance validation, and project-scope mismatch handling.

- Added executor pre-dispatch rejection tests for invalid plans and mismatched
  project scope.

- Extended Launchpad SLURM regression coverage for native Apptainer config
  generation, shared remote cache directory propagation, mixed-runtime sbatch
  bootstrap shims, and the remote-config submit path used by approval-driven
  job launches.

- Added planner fragment-composition tests for deterministic ID remap,
  dependency rewrites, dangling-fragment reference rejection, and LLM fragment
  payload composition paths.

- Added concurrency integration tests validating parallel execution isolation
  across different projects and across multiple plan instances in the same
  project.

- Added focused hybrid-bridge tests in
  `tests/cortex/test_planner_hybrid_bridge.py` covering per-flow hybrid-first
  behavior, explicit fallback triggers, plan-type mismatch fallback, and core
  deterministic flow non-regression.

- Added focused executor batch regressions in
  `tests/cortex/test_plan_executor_concurrency.py` for safe-kind parallelism,
  dependency gating, sequential fallback behavior, failure-path continuity,
  approval pause semantics, isolation guarantees, and deterministic persistence
  ordering.

- Added regressions covering summarize-results multi-step classification and
  `PARSE_OUTPUT_FILE` reuse of discovered CSV outputs from the prior locate
  step.

- Added workflow executor regression coverage for parse → plot → interpret
  chains, including inline plot-payload generation and deterministic
  interpretation text.

- Expanded UI helper coverage for workflow-result surfacing, selective
  full-refresh detection, live job timestamp preservation, refresh bootstrap
  state transitions, and destructive-action refresh suppression behavior.

- Added focused regressions for workflow-relative BED chaining,
  modification-count auto-planning from `bedMethyl/`, JSON dataframe parsing
  from `run_allowlisted_script`, deferred-vs-immediate plot intent detection,
  and specialized fallback plot specs for BED chromosome-count dataframes.

- Added transfer regressions covering single-file rsync uploads in both the
  direct file-transfer path and the local-auth broker path used by remote
  staging on locked key-file SSH profiles.

## [3.4.6] - 2026-03-23

### Improvements

- **RUN_SCRIPT workflow step support** — added `RUN_SCRIPT` as a supported
  plan step kind so workflow plans can represent standalone Python utility
  execution without forcing full Nextflow workflow submission.

- **Launchpad `/jobs/submit` script mode (local-only)** — extended job
  submission to support `run_type="script"` with structured script metadata
  (`script_id`, `script_path`, `script_args`, `script_working_directory`) while
  preserving existing Dogme submission behavior by default.

- **Explicit allowlist-based script resolution (deny by default)** — script
  execution now requires an explicit allowlisted selector:
  - `script_id` mapped in `LAUNCHPAD_SCRIPT_ALLOWLIST_IDS`, or
  - explicit absolute `script_path` under `LAUNCHPAD_SCRIPT_ALLOWLIST_ROOTS`.

- **No repository auto-discovery for runnable scripts** — Launchpad does not
  infer runnable scripts from repository contents; execution is only allowed
  when an explicit allowlisted selector is provided.

- **Structured local script monitoring and status** — script-mode runs now
  record execution/log metadata, surface status through existing status/log
  endpoints, and report clear success/failure outcomes for downstream plan
  dependencies.

### Fixes

- **Cortex submission glue for script mode** — approval submission now forwards
  script-mode fields and enforces local execution for script runs while keeping
  existing Dogme/remote staging paths unchanged.

- **Cortex submission boundary extraction** — moved
  `submit_job_after_approval` from `cortex/app.py` into
  `cortex/workflow_submission.py` while keeping
  `extract_job_parameters_from_conversation`, `poll_job_status`, and
  `_auto_trigger_analysis` in `cortex/app.py`.

- **Removed temporary submit compatibility export surface** —
  `cortex.app.submit_job_after_approval` is no longer re-exported; app now
  dispatches directly to `cortex.workflow_submission.submit_job_after_approval`
  at call sites.

- **Final workflow-submission app bridge removed** —
  `cortex/workflow_submission.py` no longer resolves runtime behavior through
  `cortex.app`; deferred `app_module` lookup was removed and submit flow now
  imports owned symbols directly.

- **Owned extraction boundary created** —
  moved `extract_job_parameters_from_conversation` to
  `cortex/job_parameters.py` and rewired app/workflow/test boundaries to the
  new owner module.

- **Owned polling boundary created** —
  moved `poll_job_status`, `_completed_job_results_ready`,
  `_resolved_job_work_directory`, and `_auto_trigger_analysis` to
  `cortex/job_polling.py`, eliminating the last workflow-submission dependency
  on app-owned runtime symbols.

- **Skills markdown ownership layout refactor** — moved skill definitions from
  flat `skills/*.md` files to per-skill paths at
  `skills/<skill_key>/SKILL.md`, moved shared markdown references to
  `skills/shared/`, and updated `SKILLS_REGISTRY`/loader path resolution while
  preserving runtime routing and execution semantics.

- **MCP submission parity for script-mode payloads** — Launchpad MCP wrappers
  now pass script-mode fields through `submit_dogme_job` when explicitly
  provided, with backward-compatible defaults for existing callers.

### Tests

- Added focused regression tests for:
  - `RUN_SCRIPT` step dispatch and approval-gated special handling
  - script-mode schema validation constraints
  - allowlist/path/args validation helpers
  - Launchpad script submit success and failure paths
  - MCP passthrough compatibility for script-mode fields
  - submit-handler extraction boundary stability via focused
    background-task/approval tests plus broader Cortex endpoint/chat and
    Launchpad status/submit suites
  - final workflow-submission bridge removal stability via compile validation,
    focused submit/approval/extract suites, broader chat-data-call boundaries,
    and polling-focused subsets on the new ownership modules
  - skills-layout boundary stability via compile validation, focused
    `test_agent_engine`/`test_plan_chains`/`test_skill_detection`, stale
    flat-path audits (active surfaces), and artifact hygiene checks

## [3.4.5] - 2026-03-23

### Fixes

- **Removed temporary remote-orchestration compatibility bridge** — eliminated
  the `_app_override` test-compat shim in Cortex remote orchestration and
  removed the module back-import from `remote_orchestration` into `app`.

- **Approval-context extraction dependency now injected at call site** —
  `_build_remote_stage_approval_context` now receives
  `extract_job_parameters_from_conversation` from `cortex/app.py`, preserving
  behavior while removing reverse module coupling.

- **Approval-context defaults continuity when profile enrichment is degraded** —
  when live SSH profile enrichment is unavailable, approval-context construction
  now falls back to resolved `get_slurm_defaults` values for
  `slurm_account`/`slurm_partition` so summaries and gates remain consistent.

### Tests

- Updated monkeypatch boundaries in remote-execution tests to target runtime
  call paths correctly (`cortex.app.*` for app-owned call sites and
  `cortex.remote_orchestration.*` for extracted internals).

## [3.4.4] - 2026-03-21

### Improvements

- **Streamlit UI redesign foundation** — introduced shared UI primitives and
  theme infrastructure for consistent section headers, status chips, metadata
  rows, empty states, review panels, and progress visuals across core surfaces.

- **Chat block presentation and hierarchy refresh** — updated primary chat
  block rendering for `AGENT_PLAN`, `APPROVAL_GATE`, `STAGING_TASK`,
  `EXECUTION_JOB`, `AGENT_PLOT`, `DOWNLOAD_TASK`, and `WORKFLOW_PLAN` to
  improve scannability while preserving payload contracts and existing behavior.

- **Approval gate review clarity** — tightened approval grouping and review
  hierarchy with clearer sectioning and summary presentation in pending forms.

- **Results and Projects UX polish** — added a compact action tray and
  run-overview metadata/status surface on Results, plus an at-a-glance
  overview/status strip on Projects.

- **Page-level consistency pass** — aligned key pages (`Projects`, `Results`,
  `My Data`, `Remote Profiles`, `Admin`) and related form components with the
  shared visual language and empty-state handling.

### Fixes

- **Projects → Results run selection handoff** — selecting a job in Projects
  now persists the chosen `run_uuid` before navigating to Results so the
  target run opens prefilled consistently.

## [3.4.3] - 2026-03-19

### Fixes

- **Broker-backed remote result copy-back** — completed remote workflows that
  copy results back to a local workflow now use the same local authentication
  broker path as brokered uploads when an active unlocked session exists,
  including selective rsync include rules for result sync.

- **Selective local result sync for remote workflows** — on remote completion,
  Launchpad now copies back the local-analysis result set from the matching
  remote workflow directory: `annot`, `bams`, `bedMethyl`, `kallisto`,
  `openChromatin`, `stats`, plus top-level `.config`, `.html`, `.txt`,
  `.csv`, and `.tsv` files when present.

- **Fail-closed copy-back before local completion** — SLURM jobs with
  `result_destination=local|both` no longer become locally complete just
  because the remote scheduler says `COMPLETED`; Launchpad now keeps them in a
  result-download phase until the selected outputs are present in the local
  workflow folder, and marks the transfer failed if required copied artifacts
  are still missing.

- **Auto-analysis now waits for copied results** — Cortex no longer auto-
  launches analysis for local/both remote runs until Launchpad reports
  `transfer_state=outputs_downloaded`, preventing analysis from starting before
  copied workflow outputs exist locally.

- **Terminal SLURM status stability in the UI** — once a SLURM job is truly in
  a terminal state, Launchpad now returns the terminal DB state directly rather
  than reopening the job with a stale backend poll; jobs still pending local
  copy-back continue polling until that transfer finishes.

- **Accurate live-update timing in job cards** — the Streamlit job card now
  reports “Updated Xs ago” based on the timestamp of the successful live status
  fetch instead of stale persisted block metadata.

### Tests

- Added/updated tests for:
  - broker-backed result downloads with selective rsync include patterns
  - required result-sync include patterns for remote workflow copy-back
  - status endpoint behavior for terminal SLURM rows vs. pending local copy-back
  - delayed auto-analysis until `outputs_downloaded` is reached
  - live-update timestamp selection for successful UI status polls

## [3.4.2] - 2026-03-18

### Fixes

- **Remote reference-path correctness for SLURM configs** — remote submissions
  now consistently generate `genome_annot_refs` using staged remote reference
  cache paths instead of local host reference paths.

- **Workflow-local input linking for remote runs** — SLURM submissions now
  ensure workflow-local `pod5`/`bams`/`fastqs` links point to staged remote
  input cache locations expected by the Dogme pipeline.

- **Correct Dogme launch target in SLURM scripts** — remote jobs now invoke
  `nextflow run mortazavilab/dogme` instead of `nextflow run main.nf`.

- **Cluster-agnostic runtime bootstrap for sbatch scripts** — generated scripts
  now attempt module-based Java/container runtime setup when available,
  validate required runtime commands, and support apptainer-only environments
  via a local `singularity` compatibility shim.

- **Sbatch script generation stability** — fixed nested-heredoc delimiter
  collisions that could truncate submit scripts and cause pre-launch bash
  syntax errors.

- **Improved remote failure surfacing** — on Nextflow failure, generated SLURM
  scripts now emit focused `.nextflow.log` diagnostics (error scan + head/tail)
  to make cluster/runtime root causes visible in `slurm-*.out`.

- **Launchpad debug endpoint visibility** — `/jobs/{run_uuid}/debug` now
  reports richer failed-run context including top-level workflow listing,
  discovered `slurm-*.out/.err` previews, and focused `.nextflow.log` lines.

- **Deterministic SLURM submit bootstrap** — generated sbatch scripts now avoid
  sourcing interactive shell startup files, resolve `nextflow` explicitly from
  safe locations like `$HOME/bin`, and emit early runtime breadcrumbs so remote
  startup failures are visible in `slurm-*.out`.

- **Preserved scheduler failure details** — Launchpad now stores and surfaces
  SLURM batch exit details for failed remote jobs instead of collapsing them to
  a generic “Job failed” message.

- **Remote container bind-path correctness** — SLURM Nextflow configs no longer
  inject local workstation paths such as `/media/backup_disk/...` into remote
  Singularity/Apptainer container options, preventing container launch failures
  on the cluster.

- **Remote reference sidecar overrides** — staged remote reference overrides now
  also replace auxiliary assets like `kallistoIndex` and `t2g`, not just FASTA
  and GTF paths.

- **CPU-only Nextflow controller jobs** — the outer sbatch job that launches
  Nextflow now uses CPU/default account and partition resources, leaving GPU
  requests to individual pipeline tasks configured inside `nextflow.config`.

- **Remote Dogme profile staging** — SLURM workflow setup now writes an empty
  `dogme.profile` file alongside `nextflow.config` so remote Dogme tasks can
  safely source `${launchDir}/dogme.profile`.

- **Mode-aware remote reference asset verification** — stage-only remote
  staging now reports per-reference remote asset evidence, verifies FASTA/GTF
  for all modes, and only requires `kallistoIndex`/`t2g` sidecars for RNA and
  cDNA samples instead of DNA runs.

- **Stage-only approval routing correctness** — Cortex now preserves remote
  stage intent through approval handling, forces SLURM mode when a request is
  clearly targeting a remote profile such as `hpc3`, and prevents those
  requests from falling into local sample staging or accidentally submitting a
  Nextflow job.

- **Saved SSH-profile defaults now survive remote-stage approval** — when
  Launchpad successfully returns stored SLURM defaults for a remote profile,
  Cortex now carries those normalized values into the approval summary and the
  actual approval gate instead of reverting to “0 Slurm defaults” or falling
  back to `execution_mode=local`.

- **Fail-closed remote reference refresh verification** — after remote staging,
  Launchpad now re-checks required reference assets on the cluster, force-
  refreshes missing files such as `kallistoIndex` and `t2g` sidecars when
  needed, and aborts the stage if the repaired cache is still incomplete.

- **Long-running remote stage timeout support** — stage-only remote staging now
  uses an env-backed `LAUNCHPAD_STAGE_TIMEOUT` default of 3600 seconds in both
  Cortex and Launchpad so large reference refreshes do not fail prematurely.

- **Broker timeout and diagnostics coverage** — brokered `ssh` and `rsync`
  operations now enforce explicit per-operation timeouts, log start/finish
  markers with host/user/exit status, and surface timeout details back to the
  caller instead of hanging indefinitely.

- **Slow-HPC SSH connection test support** — remote profile connection tests
  now use a 10-minute SSH connect/probe timeout, preserve noninteractive
  public-key-only transport settings, and return concrete probe failure text
  instead of generic “Unexpected response” errors.

- **Elapsed-time feedback in Remote Profiles UI** — the Streamlit Remote
  Profiles page now keeps the test-connection request alive for slow clusters
  and shows a live elapsed-time indicator while the probe is running.

- **Remote defaults-to-submit approval continuity** — remote execution now
  treats `get_slurm_defaults(found=true, source=ssh_profile_defaults)` as a
  valid defaults signal for submit intents, generating deterministic
  submit-ready approval summaries/gates instead of falling through to generic
  second-pass "0 SLURM defaults" failure copy.

- **Launchpad remote-call context injection** — Cortex now auto-injects
  required Launchpad context parameters (`user_id`, and `project_id` for
  `get_slurm_defaults`) at execution time when model-emitted DATA_CALL tags
  omit them.

- **Provenance row-count correctness for dict-style success payloads** —
  `TOOL_RESULT` row counts now treat `found=true`/`ok=true` dict responses as
  successful one-row outcomes, preventing misleading `rows=0` audit signals
  for defaults/profile lookups.

### Tests

- Added/updated tests for:
  - remote reference override behavior when cache metadata is partial/missing
  - SLURM submit script target (`mortazavilab/dogme`)
  - portable runtime bootstrap and apptainer shim generation
  - enhanced sbatch failure diagnostics output
  - remote `kallistoIndex`/`t2g` override rendering for SLURM configs
  - CPU/default controller resource selection for remote Nextflow head jobs
  - remote `dogme.profile` creation during SLURM workflow staging
  - mode-aware remote reference asset evidence for DNA vs RNA/cDNA staging
  - remote stage-only approval routing when the request carries remote intent
    but an incomplete or degraded execution-mode payload
  - saved SSH-profile SLURM defaults flowing through remote-stage approval and
    execution without prompting again
  - fail-closed repair behavior when refreshed remote reference sidecars are
    still missing after a forced cache refresh
  - env-backed 3600-second remote stage timeout wiring in Launchpad MCP calls
  - broker timeout propagation for brokered `ssh` and `rsync` operations
  - long SSH connect/probe timeout defaults and surfaced broker failure text
    during remote profile connection tests
  - Remote Profiles UI support for long-running connection-test requests
  - auto-injection of missing `user_id`/`project_id` context for
    `get_slurm_defaults` Launchpad calls
  - remote run intent path using SSH-profile-backed defaults to produce a
    submit approval gate (not stage-only) and suppress misleading
    "0 SLURM defaults" fallback messaging

### Documentation

- Added a dedicated analysis narrative in `README.md` that explains what
  happens after pipeline execution, including analysis goals, supported
  analysis types, typical analysis flow, inputs/outputs, visualization support,
  and analysis limitations.

- Clarified analysis constraints across user docs: remote-only results must be
  copied back locally before Analyzer-based downstream interpretation.

## [3.4.1] - 2026-03-17

- **Per-user cross-project SLURM cache reuse** — added cache preflight planning
  and execution for remote references and input datasets. Jobs now carry cache
  metadata (roots, actions, reasons) from Cortex approval through Launchpad
  submission and workflow status updates.

- **Remote cache persistence** — introduced remote cache metadata storage for
  staged references and input fingerprints, including use counters and
  validation timestamps for reuse decisions.

- **Remote base path layout for SLURM runs** — remote execution now derives all
  staging and run directories from a single `remote_base_path` instead of a
  bundle of per-path fields or invented `/scratch` fallbacks. References stage
  under `ref/`, sample data under `data/`, and each remote run under
  `{project_slug}/workflowN`.

- **Stage-only remote sample intake** — added a dedicated remote staging flow
  that stages references and sample inputs without submitting a scheduler job.
  This supports a two-step workflow where a sample can be staged first and run
  later.

- **Remote staged-sample reuse by name** — introduced persistent
  `RemoteStagedSample` records so prompts like “Analyze Jamshid on localCluster” can
  reuse an existing staged remote dataset when the profile, sample, and genome
  match.

- **Remote file browsing** — added `list_remote_files` support so users can
  browse the configured remote base path, including top-level `ref/`, `data/`,
  and project workflow folders.

- **Planner-native remote stage workflow** — remote stage-only requests are now
  recognized as a first-class multi-step planner template with explicit
  validation, approval, remote staging, and completion steps.

- **My Data filesystem visibility** — `/user/data` now supports an
  `include_untracked` mode so users can view files present on disk even when
  no DB record exists.

- **My Data path clarity** — UI now shows each file's folder/path context with:
  folder column in table view, disambiguated selector labels, and full disk
  path in file details.

### Improvements

- **SLURM approval reuse UX** — approval gates now expose a saved SSH profile
  picker that auto-fills profile defaults (account/partition and remote path
  templates). Parameter extraction now also seeds SLURM settings from the most
  recent approved SLURM run in the same project cycle, reducing repeat data
  entry on reruns.

- **Local and remote workflow numbering alignment** — SLURM submissions now
  create local `workflowN` directories before submission and use the same
  workflow number for the remote project run directory.

- **Approval and extraction clarity for remote runs** — Cortex now carries
  `remote_base_path`, `staged_remote_input_path`, stage-only intent, and staged
  sample metadata through extraction, approval, and submission flows.

- **Profile and UI simplification** — SSH profiles and SLURM approval forms now
  use a single `remote_base_path` field instead of separate remote input/work/
  output path defaults.

- **Workflow planning visibility** — planner templates now include
  `FIND_REFERENCE_CACHE` and `FIND_DATA_CACHE` steps before approval for remote
  runs, with cache preflight details attached to gate payloads.

- **Safer SSH profile create errors** — Launchpad now maps duplicate profile
  conflicts to `400` with clear messaging, and schema drift (`no such table` /
  `no such column`) to `503` with explicit migration guidance.

- **Remote cache preflight behavior** — missing profile path configuration now
  degrades to an explicit `needs_remote_base_path` state instead of attempting
  unsafe path assumptions.

- **Remote staging task UX** — stage-only remote approvals now produce a
  dedicated staging task instead of a generic Nextflow job card, with
  stage-specific approval copy and completion/failure messaging.

- **Split staging progress visibility** — remote stage-only tasks now show
  separate progress sections for references and sample data. Cached parts are
  shown as already complete, while only the missing part remains active.

- **Generic remote browsing prompts** — remote file browsing now recognizes
  saved SSH profile nicknames generically instead of relying on hardcoded
  profile names, so prompts like “list files on <nickname>” route correctly.

- **Deterministic remote browse execution** — explicit remote browsing requests
  now bypass incorrect LLM clarifications or skill switches and directly invoke
  Launchpad remote listing, even when the conversation was previously in a
  local results-analysis skill.

- **Remote browse result rendering** — successful `list_remote_files` calls
  now render directly as remote directory listings instead of falling through
  to generic “query did not return the expected data” messaging.

### Fixes

- **Execution mode validation** — added defensive normalization of `execution_mode`
  parameter in Cortex and Launchpad to prevent incorrect backend selection. Local
  jobs with malformed or uppercase execution_mode values are now correctly routed
  to NextflowExecutor instead of accidentally triggering SLURM backend paths
  (which tried to create `/scratch` directories on macOS, causing permission errors).

- **Backend routing safeguards** — Cortex now validates `execution_mode` before
  passing to Launchpad, includes logging of selected backend, and defaults to
  "local" for any invalid values (None, empty, uppercase, or unrecognized).

- **Launchpad submission consistency** — improved error messages and logging when
  execution_mode is normalized or corrected, making debugging backend selection
  issues easier.

- **Remote stage plan execution** — fixed stage-only workflow plans that could
  stall before approval because `VALIDATE_INPUTS` was still routed through a
  placeholder analyzer tool path instead of performing real local input
  validation.

- **Workflow step state refresh** — fixed planner auto-execution to re-read the
  persisted workflow payload between steps so remote staging progress and
  dependency checks observe current step status instead of stale in-memory data.

- **Stage-only error routing** — remote staging failures now stay in
  `STAGING_TASK` blocks rather than surfacing as failed `EXECUTION_JOB` cards
  with missing run UUIDs.

- **Server-side stage error clarity** — Launchpad `/remote/stage` now translates
  backend exceptions into explicit HTTP details, including source-path and
  reference-cache upload failures.

- **Brokered transfer auth parity** — file staging for `key_file` profiles with
  `local_username` now prefers an active local auth broker session for rsync
  transfers, matching the SSH connection test/auth path and avoiding local key
  read permission failures during staging.

- **Brokered auth reuse for remote browsing** — remote directory listing now
  reuses the same local auth broker session across Launchpad processes instead
  of falling back to direct key-file reads when the browse call is handled by
  the MCP server.

- **Local auth runtime hardening** — broker runtime files now default to an
  AGOUTIC-scoped directory under `AGOUTIC_DATA` instead of `/tmp`, while
  shared session metadata files are created atomically with `0600`
  permissions from the start.

- **Remote browse subpath resolution** — prompts like “list files in data on
  localCluster” now resolve relative browse paths under the SSH profile’s configured
  `remote_base_path` instead of incorrectly treating them as paths relative to
  the remote login directory.

- **Remote browse output labeling** — Launchpad remote file listings now use a
  browse-specific header instead of the generic “Job Execution
  (Nextflow/Dogme)” label.

### API

- Added `POST /remote/stage` for stage-only remote staging.

- Added `GET /remote/files` for browsing remote directories via a saved SSH
  profile.

- Updated job submission and MCP tool payloads to use `remote_base_path` and
  optional `staged_remote_input_path` reuse.

### Database

- Added new Alembic migrations for:
  - remote staging cache tables
  - cache status/path columns on `dogme_jobs`
  - `cache_preflight_json` on `dogme_jobs`
  - default remote cache roots on `ssh_profiles`

- Added Alembic migration `6d2c9a4f1b7e` to consolidate old remote path fields
  on `ssh_profiles` into a single `remote_base_path`.

- Added Alembic migration `1a7c0b4d2e9f` to create
  `remote_staged_samples` for persistent staged-sample tracking and reuse.

### Tests

- Added coverage for:
  - planner cache preflight steps
  - SLURM backend cache hit/miss/refresh/fallback behavior
  - stage-only remote workflow auto-execution through approval
  - dedicated staging-task success/failure behavior in Cortex
  - broker-preferred rsync transfers for locked key-file SSH profiles
  - remote cache DB upsert/retrieval helpers

- Added focused coverage for:
  - execution_mode normalization edge cases
  - stage-only remote submission and workflow/task updates
  - staged sample DB upsert and lookup helpers
  - MCP stage-only and remote file browsing tools
  - planner detection and template generation for remote stage-only requests
  - SLURM remote path derivation and staged input reuse
  - remote browse markdown rendering and direct-display bypass

## [3.4.0] - 2026-03-16

### Features

- **Remote SLURM execution (Phase 1)** — AGOUTIC now supports dual
  execution modes: local (existing behavior, unchanged) and remote SLURM
  via SSH.  Users can save SSH connection profiles, configure SLURM resources,
  set remote paths, and choose where final results are stored.

- **Execution backend abstraction** — new `launchpad/backends/` package with
  `ExecutionBackend` protocol, `LocalBackend` (wraps existing NextflowExecutor),
  and `SlurmBackend` (SSH + sbatch).  Cortex and UI remain backend-agnostic.

- **SSH connection profiles** — per-user saved profiles with host, port,
  username, and auth method (key_file path reference or ssh-agent).  Full CRUD
  via REST API and MCP tools.  Connection test support.  No raw secrets stored.
  Supports `local_username` for local OS user key access when the service process
  runs as a different OS user.  Users can unlock a per-session local auth broker
  with their local Unix password; the password is used only to launch the broker
  via `su` and is never stored.  Profiles can also save default CPU/GPU account
  and partition values plus remote input/work/output path templates for
  pre-populating SLURM approval gates.

- **SLURM resource management** — configurable account, partition, CPUs, memory,
  walltime, GPUs, and GPU type with validation against safe limits.  Saved
  defaults per user/project.  Resource presets for common use cases.

- **Remote path configuration** — saved input, work, output, and log paths per
  user/project/profile.  Remote path validation over SSH before submission.

- **Result destination policy** — users choose where final outputs are stored:
  remote only, local only, or both.  Transfer state tracked per job.

- **13-stage run tracking** — granular stage labels from `awaiting_details`
  through `syncing_results` to `completed`/`failed`/`cancelled`.  Stage state
  machine enforces valid transitions.

- **SLURM scheduler integration** — job ID tracking, state polling via
  sacct/squeue, state mapping to AGOUTIC statuses, cancellation via scancel,
  human-readable pending-reason and failure-reason interpretation.

- **Staged approval prompts** — Cortex `Remote_Execution` skill collects
  execution details progressively, checks saved preferences first, and presents
  a plain-language approval summary before remote submission.

- **File transfer** — rsync/sftp-based input upload and output download with
  partial transfer/retry support and transfer state tracking.

- **Run audit logging** — `RunAuditLog` records every significant event
  (submitted, approved, cancelled, completed, failed) with user, profile,
  account, resources, and timestamps.  No secrets in audit trail.

- **User execution preferences** — saved preferred execution mode, SSH profile,
  and result destination per user.

### Database

- New tables: `ssh_profiles`, `slurm_defaults`, `remote_path_configs`,
  `run_audit_logs`, `user_execution_preferences`
- Extended `dogme_jobs` with 16 new columns: `execution_mode`, `ssh_profile_id`,
  `slurm_job_id`, `slurm_state`, `slurm_account`, `slurm_partition`,
  `slurm_cpus`, `slurm_memory_gb`, `slurm_walltime`, `slurm_gpus`,
  `slurm_gpu_type`, `remote_work_dir`, `remote_output_dir`,
  `result_destination`, `transfer_state`, `run_stage`
- Alembic migration `b95a2c38062c` with full upgrade/downgrade
- Alembic migration `9e12f5bb7e6e` adds `local_username` to `ssh_profiles`
- Alembic migration `c7a2e7b6b2df` adds per-profile SLURM/path defaults to `ssh_profiles`

### API

- SSH profile CRUD: `POST/GET/PUT/DELETE /ssh-profiles`, `POST /ssh-profiles/{id}/test`
- Test endpoint accepts transient `local_password` in body to unlock a per-session
  local auth broker when needed
- New local auth session endpoints: `GET/POST/DELETE /ssh-profiles/{id}/auth-session`
- UI includes `X-Internal-Secret` header for Launchpad service-to-service auth
- New MCP tools: `list_ssh_profiles`, `test_ssh_connection`, `get_slurm_defaults`,
  `cancel_slurm_job`

### UI

- SSH profile manager page (`ui/pages/remote_profiles.py`)
- SLURM resource form component (`ui/components/slurm_form.py`)
- Remote paths form component (`ui/components/remote_paths.py`)
- Enhanced run status panel with stage emojis (`ui/components/run_status.py`)

### Cortex

- `Remote_Execution` skill registered in skills registry
- `ConversationState` extended with `execution_mode`, `ssh_profile_id`,
  `ssh_profile_nickname`, `slurm_resources`, `remote_paths`, `result_destination`

### Documentation

- `docs/remote_execution_architecture.md` — architecture and data flow
- `docs/ssh_profile_security.md` — credential handling and security model
- `docs/cluster_slurm_setup.md` — setup guide for a generic remote SLURM cluster connection
- `docs/troubleshooting_remote.md` — common issues and fixes
- `docs/user_guide_execution_modes.md` — local vs remote SLURM usage guide
- README updated with execution modes section

### Tests

- `tests/test_slurm_backend.py` — 42 tests: resource validation, walltime parsing,
  SLURM state mapping, stage transitions, sbatch generation
- `tests/test_ssh_profile.py` — 9 tests: schema validation for SSH profiles
- `tests/test_remote_paths.py` — 5 tests: path validation result types

### Notes

- Phase 1 limitation: Analyzer operates on local-accessible files only; remote
  results must be copied back before downstream analysis
- All new DogmeJob columns are nullable with sensible defaults; existing local
  runs are completely unaffected
- `asyncssh` required for remote execution (`pip install asyncssh`)

## [3.3.2] - 2026-03-15

### Bug Fixes

- **ENCODE assay alias resolution in main tag-parsing path** — when the LLM
  emits `assay_title=RNA-seq` in a `[[DATA_CALL:...]]` tag, ENCODELIB's
  exact-match filter fails because experiments use canonical names like
  `"total RNA-seq"`.  The `_ENCODE_ASSAY_ALIASES` map was only applied in
  the auto-generation and tool-rerouting paths, not in `_validate_encode_params()`.
  Now all assay titles are normalized before the MCP call (e.g. `RNA-seq` →
  `total RNA-seq`, `ChIP-seq` → `TF ChIP-seq`), so searches return correct
  results on the first try instead of falling back to the relaxation retry.

### Changes

- `cortex/encode_helpers.py` — added Fix 2 (assay alias resolution) to
  `_validate_encode_params()`; renumbered subsequent fixes

## [3.3.1] - 2026-03-15

### Bug Fixes

- **ENCODE search retry now handles list results** — `search_by_biosample`
  (ENCODELIB) returns a list, but the zero-result retry guard only checked
  `isinstance(result_data, dict)`.  Empty list `[]` now correctly triggers
  the retry pipeline, matching the existing dict-based `{"total": 0}` path.

- **Compound query relaxation before tool swap** — when a combined search
  (`search_term=K562, assay_title=total RNA-seq`) returns 0 results, the
  system now drops the `assay_title` filter and retries with just the
  biosample first (Phase 1).  Only if relaxation also fails does it fall
  through to the biosample↔assay tool swap (Phase 2).  Previously, the
  swap would try `search_by_assay(assay_title=K562)` — nonsensical and
  guaranteed to fail.

### Changes

- `cortex/app.py` — rewrote ENCODE search retry block: two-phase strategy
  (relax filter → swap tool); detect zero results for both list and dict
  return types
- `tests/cortex/test_chat_data_calls.py` — added `test_empty_list_triggers_swap`
  and `test_compound_query_relaxation` to `TestEncodeSearchSwap`

## [3.3.0] - 2026-03-14

### Features

- **Centralized database infrastructure** — new `common/database.py` provides
  shared `Base`, `DATABASE_URL`, lazy engine/session factories, and
  `_import_all_models()` for all three services (cortex, launchpad, analyzer).
  Eliminates three duplicate `Base(DeclarativeBase)` classes, three
  `DATABASE_URL` definitions, and the `sqlite+aiosqlite → sqlite` URL
  replacement hack that appeared in every service.

- **Alembic database migrations** — `alembic/` with baseline migration
  capturing all 18 existing tables. Existing databases stamped with
  `alembic stamp head`. SQLite dev uses `create_all()` (gated behind
  `is_sqlite()`); Postgres will use `alembic upgrade head`.

- **Gene annotation & enrichment tools moved to Analyzer** — `lookup_gene`,
  `translate_gene_ids`, `run_go_enrichment`, `run_pathway_enrichment`,
  `get_enrichment_results`, and `get_term_genes` relocated from
  edgePython MCP server to Analyzer where they belong. These tools have
  no dependency on DE state — they use `common/gene_annotation.py`
  (GeneAnnotator) and `gprofiler-official` directly.

- **Per-conversation enrichment state** — enrichment results in Analyzer
  are keyed by `conversation_id`, preventing state collisions across
  concurrent conversations.

- **Simplified enrichment API** — Analyzer enrichment tools accept only
  explicit `gene_list` (comma-separated). No `result_name`/`fdr_threshold`/
  `logfc_threshold` parameters. The LLM bridges DE → enrichment: call
  `filter_de_genes` in edgePython, then pass the returned gene list to
  Analyzer's `run_go_enrichment`/`run_pathway_enrichment`.

### Changes

- `common/database.py` — **new**: shared DB infrastructure (Base, engines,
  sessions, DATABASE_URL, is_sqlite, init_db_sync/async, reset_engines)
- `common/__init__.py` — database exports added to `__all__`
- `cortex/models.py`, `launchpad/models.py`, `analyzer/models.py` — use
  shared `Base` from `common.database`
- `analyzer/models.py` — deleted duplicate `DogmeJob`, re-exports from
  launchpad
- `cortex/config.py`, `launchpad/config.py`, `analyzer/config.py` — removed
  per-service `DB_FOLDER`/`DB_FILE`/`DATABASE_URL`; import from common
- `cortex/db.py`, `launchpad/db.py`, `analyzer/db.py` — thin wrappers over
  `common.database`
- `cortex/dependencies.py` — removed inline `create_engine` + sync URL hack
- `cortex/app.py` — removed inline engine creation in `resubmit_job`;
  startup gated behind `is_sqlite()`; routing split into
  `_EDGEPYTHON_ONLY_TOOLS` (annotate_genes, filter_de_genes) and
  `_ANALYZER_ONLY_TOOLS` (6 gene/enrichment tools)
- `launchpad/app.py` — startup gated behind `is_sqlite()`
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako` — **new**: Alembic
  configuration and baseline migration
- `environment.yml` — added `alembic>=1.13`, `asyncpg`, `psycopg2-binary`,
  `gprofiler-official`
- `analyzer/gene_tools.py` — **new**: translate_gene_ids, lookup_gene
- `analyzer/enrichment_engine.py` — **new**: enrichment tools with
  per-conversation state
- `analyzer/mcp_tools.py` — registered 6 new native tools; excluded from
  edgePython proxy
- `analyzer/mcp_server.py` — added @mcp.tool() wrappers for 6 new tools
- `cortex/config.py` — moved `enrichment_analysis` skill from edgepython
  to analyzer in SERVICE_REGISTRY
- `skills/Enrichment_Analysis.md` — DATA_CALL tags route enrichment tools
  to `service:analyzer`; `filter_de_genes` stays at `service:edgepython`
- `skills/Differential_Expression.md` — gene lookup DATA_CALLs route to
  `service:analyzer`
- `edgepython_mcp/edgepython_server.py` — removed 6 tools + `_detect_species_code`
  helper (moved to analyzer); kept `filter_de_genes`, `annotate_genes`
- `edgepython_mcp/tool_schemas.py` — removed 6 tool schemas
- 23 test files — `Base` import changed to `common.database`

## [3.2.11] - 2026-03-13

### Features

- **GO & pathway enrichment analysis** — new `enrichment_analysis` skill
  with 5 MCP tools (`filter_de_genes`, `run_go_enrichment`,
  `run_pathway_enrichment`, `get_enrichment_results`, `get_term_genes`)
  wrapping g:Profiler for Gene Ontology (BP/MF/CC), KEGG, and Reactome
  enrichment. Supports dual-mode input: from DE results or explicit gene
  lists. Species auto-detected from gene ID prefixes (ENSG→human,
  ENSMUSG→mouse).

- **Enrichment visualizations** — two new plot types in `generate_plot`:
  `enrichment_bar` (horizontal bar chart colored by source) and
  `enrichment_dot` (bubble plot with gene ratio, intersection size, and
  p-value). Both render as static PNGs via matplotlib.

- **Enrichment plan template** — 9th deterministic plan template
  (`run_enrichment`) generates FILTER_DE_GENES → RUN_GO_ENRICHMENT →
  RUN_PATHWAY_ENRICHMENT → PLOT_ENRICHMENT → SUMMARIZE_ENRICHMENT. All
  enrichment steps are safe (no approval required).

- **Enrichment skill routing** — enrichment keywords auto-detected in
  `_auto_detect_skill_switch` and routed from Welcome, DE, and any other
  skill. Welcome menu updated with option 6. DE skill routes enrichment
  requests to the new skill.

- **Enrichment auto-generation** — `data_call_generator.py` pattern-matches
  GO/pathway/KEGG/Reactome keywords and auto-generates tool calls when the
  LLM fails to emit proper DATA_CALL tags. Direction (up/down/all)
  extracted from user message.

### Changes

- `edgepython_mcp/edgepython_server.py` — 5 new enrichment tools, 2 helper
  functions, 2 enrichment plot types, enrichment state management
- `edgepython_mcp/tool_schemas.py` — 5 new tool schemas (41 total)
- `skills/Enrichment_Analysis.md` — new skill file with routing, plan
  chains, DATA_CALL examples
- `cortex/config.py` — registered enrichment_analysis skill + service
- `cortex/plan_executor.py` — 5 new safe step kinds + tool defaults
- `cortex/planner.py` — enrichment patterns, template, detection, dispatch
- `cortex/prompt_templates/planning_system_prompt.md` — 5 new step kinds
- `cortex/app.py` — enrichment tools in _EDGEPYTHON_ONLY_TOOLS
- `cortex/data_call_generator.py` — enrichment auto-generation + validation
- `cortex/plan_chains.py` — enrichment step icons
- `cortex/llm_validators.py` — enrichment skill auto-detection
- `skills/Welcome.md` — option 6 + enrichment routing rule
- `skills/Differential_Expression.md` — routes enrichment to new skill

## [3.2.10] - 2026-03-13

### Bug Fixes

- **Gene lookup routing & parsing pipeline** — fixes end-to-end failure
  where gene ID questions (e.g. "what is the Ensembl ID for TP53?")
  never reached the `lookup_gene` tool.

- **Pre-LLM auto-skill detection for gene queries** — new keyword
  heuristics in `_auto_detect_skill_switch` catch gene-related questions
  ("ensembl", "gene id", "gene symbol", "ENSG0", etc.) and route them
  to the `differential_expression` skill before the LLM runs, regardless
  of the current active skill.

- **DATA_CALL regex fix for JSON arrays** — the `[^\]]+` params group
  in the DATA_CALL regex truncated array values like `["TP53"]` at the
  first `]`. Changed to greedy `.+` so the regex correctly captures
  `gene_symbols=["TP53", "BRCA1"]` as a complete value.

- **Bracket-aware param parser** — `_parse_tag_params` now tracks
  bracket depth when splitting on commas, so JSON arrays inside
  DATA_CALL tags are preserved intact. Values starting with `[` are
  JSON-parsed into native Python lists, avoiding Pydantic validation
  errors from FastMCP.

- **Mistral `[TOOL_CALLS]DATA_CALL:` fallback** — devstral-small-2
  consistently emits `[TOOL_CALLS]DATA_CALL: key=value, ...` instead
  of `[[DATA_CALL: ...]]`. New normalization step converts this format
  to standard `[[DATA_CALL:...]]` tags before parsing.

- **`lookup_gene` JSON string coercion** — when the DATA_CALL parser
  passes array params as JSON-encoded strings (e.g. `'["TP53"]'`),
  the tool now tries `json.loads` before falling back to single-element
  wrapping.

### Changes

- `cortex/app.py` — DATA_CALL regex fix; `[TOOL_CALLS]DATA_CALL:`
  inline normalization fallback
- `cortex/llm_validators.py` — bracket-aware `_parse_tag_params` with
  JSON array support; gene query auto-detection in
  `_auto_detect_skill_switch`
- `edgepython_mcp/edgepython_server.py` — `import json`; `lookup_gene`
  JSON string coercion for array params
- `skills/ENCODE_Search.md` — gene lookup section moved to top with
  strong "NOT an ENCODE tool" guidance
- `skills/Welcome.md` — gene lookup routing rule to
  `differential_expression`

## [3.2.9] - 2026-03-13

### Features

- **Skill-defined plan chains** — new `cortex/plan_chains.py` module lets
  skill authors declare multi-step workflows directly in their Markdown
  files under a `## Plan Chains` section. Each chain specifies trigger
  keyword groups (AND logic between groups, OR within), ordered steps, and
  optional auto-approval and plot hints. The planner detects matching chains
  at classify-time and injects the chain plan into the LLM context so a
  single user message like "get the K562 experiments in ENCODE and make a
  plot by assay type" produces both a data search **and** a visualization.

- **Comprehensive skills system documentation** — new top-level `SKILLS.md`
  file documents the complete skills framework: skill file structure, skill
  routing patterns, tag system (`[[DATA_CALL:…]]`, `[[PLOT:…]]`), plan chains
  format, and a step-by-step guide for creating new skills. Replaces scattered
  documentation and provides a single authoritative reference.

- **Multi-phrasing query support** — plan chain triggers are flexible enough
  to match equivalent phrasings: "Plot the K562 experiments…", "Get K562
  experiments… and make a chart", "Plot by assay type the K562 experiments…"
  all resolve to the same `search_and_visualize` chain.

- **Three-layer plot guarantee** — after data retrieval, plots are ensured
  via: (1) chain context injection tells the first-pass LLM to emit both
  `[[DATA_CALL:…]]` and `[[PLOT:…]]` tags, (2) the second-pass analysis
  prompt now includes full PLOT tag instructions, (3) a post-DataFrame
  fallback auto-generates a plot spec if the LLM still omitted one.

- **Second-pass PLOT instructions** — `second_pass_system_prompt.md` now
  documents the `[[PLOT:…]]` tag syntax, chart types, parameter rules, and
  examples so the analysis LLM can emit visualization tags after reviewing
  retrieved data.

- **Literal plot colors and palette override** — the UI plot renderer now
  honors literal color requests such as `color=red` instead of silently
  falling back to Plotly's default blue, and also accepts a separate
  `palette=` styling parameter so color grouping can stay distinct from
  explicit chart colors. The backend plot fallback now also preserves
  explicit color requests from malformed LLM plot output, so prompts like
  "plot DF1 by assay in green" still render with the requested color.

- **Search+plot safety net for new ENCODE subjects** — compound requests like
  "plot C2C12 experiments in encode by assay type in purple" now auto-generate
  the ENCODE search step even when the LLM misses it, instead of treating the
  prompt as a pure visualization and reusing the latest visible dataframe from
  an earlier query.

- **Same-turn plot rebinding for newly created dataframes** — when a compound
  search+plot request creates a new dataframe in the current turn, stale
  LLM-guessed plot references like `DF4` are now rebound to the new result
  dataframe instead of plotting older results from the conversation history.

- **DF inspection quick commands** — `list dfs` and `head DF` (e.g.
  `head df1`, `head df3 5`) bypass the LLM and return deterministic
  markdown tables showing dataframe metadata or row previews from the
  conversation context. Supports custom row counts and defaults to the
  latest dataframe when no DF number is specified.

### Bug Fixes

- **Plot spec deduplication and intent-based selection** — fixes duplicate
  overlapping bars in rendered charts by implementing three-layer deduplication:
  (1) exact-match dedup removes byte-identical specs, (2) semantic dedup removes
  specs differing only in non-trace metadata (title, df string), (3) for
  competing specs within the same request (e.g., assay vs. accession grouping),
  a best-match selector aligns each spec to the current prompt's intent (x-column,
  chart type, styling). Prevents stale styling (e.g., colors from earlier requests)
  from leaking into unrelated visualizations.

### New Files

- `cortex/plan_chains.py` — plan chain parser & matcher: `PlanChain` /
  `ChainStep` dataclasses, `parse_chains_from_skill()`, `match_chain()`,
  `render_chain_plan()`, `load_chains_for_skill()`
- `SKILLS.md` — top-level documentation for the skills system, covering
  skill file structure, plan chains format, tag system, routing patterns,
  and how to create new skills

### Changes

- `cortex/planner.py` — `classify_request()` now returns
  `CHAIN_MULTI_STEP` when a plan chain matches the user query
- `cortex/app.py` — CHAIN_MULTI_STEP handling (shows chain plan, injects
  chain context + PLOT hint), post-second-pass `[[PLOT:…]]` re-parsing,
  post-DF-assignment fallback plot generation, DF inspection quick commands
  (`list dfs`, `head DF`)
- `cortex/prompt_templates/second_pass_system_prompt.md` — added PLOT tag
  section with syntax, chart types, and examples
- `skills/ENCODE_Search.md` — added `## Plan Chains` section with three
  chains: `search_and_visualize`, `visualize_existing`,
  `search_filter_visualize`
- `ui/appUI.py` — `[[PLOT:…]]` rendering now supports literal colors and a
  separate `palette` override without breaking existing `color=red`
  prompts
- `tests/ui/test_app_source_helpers.py` — added regression tests for
  literal `color=` and `palette=` plot styling
- `tests/cortex/test_plan_chains.py` — added parser, matcher, and planner
  integration tests for skill-defined plan chains
- `tests/cortex/test_chat_data_calls.py` — added regression coverage for
  malformed plot fallback preserving explicit color requests, plus a chat-level
  regression that ensures search+plot requests target the newly requested
  ENCODE subject rather than a stale dataframe, including stale `DFn`
  references emitted by the LLM during same-turn search+plot requests
- `tests/cortex/test_auto_generate_data.py` — added regression coverage for
  compound ENCODE search+plot prompts in the DATA_CALL safety net

## [3.2.8] - 2026-03-12

### Features

- **Bidirectional gene lookup** — new `lookup_gene` MCP tool in edgePython
  accepts gene symbols *or* Ensembl IDs and returns full annotation (ID,
  symbol, biotype, name). Answers questions like "what is the Ensembl ID
  for TP53?" that previously caused hallucinated tool errors.

- **Reverse gene ID translation** — `GeneAnnotator` now supports
  symbol → Ensembl ID lookup via `reverse_translate()` and a unified
  `lookup()` method for bidirectional resolution. Lazy-built reverse
  index avoids overhead when unused.

- **Tool alias safety net for gene tools** — hallucinated tool names
  (`get_gene_info`, `gene_info`, `gene_lookup`, `find_gene`, `get_gene`)
  are now aliased to `lookup_gene`. Parameter aliases handle common LLM
  mistakes (`gene_name` → `gene_symbols`, `symbols` → `gene_symbols`).

- **Cross-source re-routing** — gene tools (`lookup_gene`,
  `translate_gene_ids`, `annotate_genes`) emitted under the wrong source
  (e.g. `consortium=encode`) are automatically re-routed to edgePython.

- **Skill guidance for gene queries** — ENCODE_Search and
  Differential_Expression skills now document `lookup_gene` usage with
  example DATA_CALL tags, reducing hallucination likelihood.

### Changes

- `common/gene_annotation.py` — added `_reverse_maps`, `_ensure_reverse_map()`,
  `reverse_translate()`, `lookup()`; removed unused `reverse_translate_batch`
- `edgepython_mcp/edgepython_server.py` — new `lookup_gene` tool
- `edgepython_mcp/tool_schemas.py` — schema for `lookup_gene`
- `cortex/app.py` — tool aliases, param aliases, `_EDGEPYTHON_ONLY_TOOLS`
  re-routing frozenset
- `cortex/llm_validators.py` — added `lookup_gene`, `translate_gene_ids`,
  `annotate_genes` to known tools set
- `skills/ENCODE_Search.md` — gene lookup section
- `skills/Differential_Expression.md` — `lookup_gene` documentation

## [3.2.7] - 2026-03-12

### Features

- **Gene annotation & ID translation** — new `GeneAnnotator` service in
  `common/gene_annotation.py` provides offline Ensembl gene ID to symbol
  translation for human and mouse. Auto-annotates gene symbols when DE data
  is loaded via `load_data()` / `load_data_auto()`. All downstream outputs
  (top genes tables, heatmap labels, LLM summaries) now show readable gene
  symbols (e.g. TP53) instead of raw Ensembl IDs (e.g. ENSG00000141510).

- **Version stripping & organism detection** — automatically handles
  versioned IDs (`ENSG00000141510.17` → `ENSG00000141510`) and detects
  organism from ID prefix (ENSG → human, ENSMUSG → mouse).

- **Two new MCP tools** — `annotate_genes` (annotate the loaded DGEList)
  and `translate_gene_ids` (standalone batch ID → symbol translation).

- **ANNOTATE_RESULTS plan step** — the DE pipeline plan template now
  includes an annotation step between RUN_DE_PIPELINE and GENERATE_DE_PLOT.

- **Graceful degradation** — if reference TSV files are missing, all
  existing behavior is unchanged. No crashes, no new dependencies.

### New Files

- `common/gene_annotation.py` — GeneAnnotator class: strip_version,
  detect_organism, translate, translate_batch, annotate_dataframe
- `scripts/build_gene_reference.py` — one-time Gencode GTF → TSV builder
- `data/reference/human_genes.tsv` — ~100 representative human genes
- `data/reference/mouse_genes.tsv` — ~100 representative mouse genes
- `tests/common/test_gene_annotation.py` — 40 unit tests

### Changes

- `common/__init__.py` — export GeneAnnotator
- `edgepython_mcp/edgepython_server.py` — auto-annotate in load_data/
  load_data_auto, `_gene_name()` prefers Symbol over GeneID, two new tools
- `edgepython_mcp/tool_schemas.py` — schemas for annotate_genes,
  translate_gene_ids
- `cortex/planner.py` — ANNOTATE_RESULTS step in DE pipeline template
- `cortex/plan_executor.py` — ANNOTATE_RESULTS in safe steps + defaults
- `cortex/task_service.py` — ANNOTATE_RESULTS in action classification
- `cortex/prompt_templates/planning_system_prompt.md` — new step kind
- `skills/Differential_Expression.md` — annotation usage documented

## [3.2.6] - 2026-03-12

### Features

- **Expanded plan template catalog (4 → 8)** — four new deterministic plan
  templates: `run_de_pipeline` (CHECK_EXISTING → RUN_DE_PIPELINE →
  GENERATE_DE_PLOT → INTERPRET_RESULTS → summary), `parse_plot_interpret`
  (locate → parse → plot → interpret), `compare_workflows` (locate × 2 →
  parse × 2 → compare → plot → interpret → summary), and
  `search_compare_to_local` (ENCODE search → CHECK_EXISTING → download →
  locate local → parse × 2 → compare → plot → summary).

- **5 new step types (13 → 18)** — CHECK_EXISTING (guard against redundant
  expensive ops), RUN_DE_PIPELINE (full DE with approval gate),
  GENERATE_DE_PLOT (volcano/heatmap from DE results), INTERPRET_RESULTS
  (LLM explains findings), RECOMMEND_NEXT (LLM suggests follow-up).

- **CHECK_EXISTING guards** — expensive steps (downloads, pipeline
  submissions, DE runs) are now preceded by a CHECK_EXISTING step that
  verifies whether results already exist. When they do, the replanner
  converts the downstream step to WAITING_APPROVAL with an "(existing
  results found — rerun?)" label.

- **Plot type auto-selection** — `_select_plot_type()` heuristic picks the
  chart type (bar, pie, histogram, scatter, box, heatmap, volcano) based on
  request keywords, so GENERATE_PLOT steps produce the right visualization.

- **Enhanced existing templates** — `run_workflow` gained a CHECK_EXISTING
  guard before submission; `compare_samples` and `summarize_results` gained
  an INTERPRET_RESULTS step; `download_analyze` gained a CHECK_EXISTING
  guard before download.

- **Priority-ordered plan detection** — plan type classifier checks patterns
  from most specific (DE analysis) to least (run workflow) to avoid
  ambiguous matches.

### Changes

- `cortex/planner.py` — expanded from ~540 to ~970 lines: new classifier
  patterns, 7 pattern sets with priority detection, 4 new templates, 4
  enhanced templates, plot selection heuristic, expanded param extraction
- `cortex/plan_executor.py` — 5 new step kinds in safety sets and tool
  default mappings
- `cortex/plan_replanner.py` — 3 new replan rules (CHECK_EXISTING →
  WAITING_APPROVAL, PARSE_OUTPUT_FILE empty → skip plots, RUN_DE_PIPELINE
  failure → skip DE plot/interpret) and `_has_existing_files()` helper
- `cortex/task_service.py` — new step kinds in action classification
- `cortex/prompt_templates/planning_system_prompt.md` — updated step kinds
  list and plan_type enum

## [3.2.5] - 2026-03-12

### Features

- **Plan-Execute-Observe-Replan layer** — Cortex now decomposes complex
  multi-step requests into structured execution plans before acting. The
  planner classifies requests as informational, single-tool, or multi-step,
  then generates a deterministic plan with dependency tracking, approval gates,
  and step-by-step execution.

- **Four plan templates** — deterministic, LLM-free plan generation for the
  most common request patterns: run workflow (stage → submit → monitor →
  summarize), compare samples (locate × 2 → parse × 2 → compare → plot →
  summary), download + analyze (search → download → submit → monitor →
  summarize), and summarize existing results (locate → parse → QC → plot →
  summary).

- **Automatic replanning** — when a plan step fails, all transitive dependents
  are marked SKIPPED and a recovery note is attached. When step results
  indicate changed conditions (e.g. ENCODE search returns zero results), the
  downstream steps are adjusted automatically.

- **Safe-step auto-execution** — read-only steps (locate, validate, parse,
  summarize, plot) auto-execute in the background. Expensive steps (downloads,
  pipeline submissions, DE analysis) pause at approval gates.

- **Plan-aware conversation state** — `ConversationState` now tracks
  `active_plan_id` and `active_plan_step` so the LLM and UI know when a plan
  is in progress.

### New Files

- `cortex/planner.py` — request classifier, plan templates, LLM fallback planner, plan markdown renderer
- `cortex/plan_executor.py` — deterministic step execution engine with MCP tool call mapping
- `cortex/plan_replanner.py` — failure recovery and plan adjustment logic
- `cortex/prompt_templates/planning_system_prompt.md` — LLM planning prompt (fallback only)

### Changes

- `cortex/app.py` — plan detection branch in chat endpoint, `_auto_execute_plan_steps` background task, plan resumption on approval gate approval
- `cortex/agent_engine.py` — added `plan()` method for LLM planning pass
- `cortex/schemas.py` — added `active_plan_id` and `active_plan_step` to `ConversationState`
- `cortex/conversation_state.py` — extract active plan context from `WORKFLOW_PLAN` blocks
- `cortex/task_service.py` — extended `_workflow_action_for_step()` with new plan step kinds

## [3.2.4] - 2026-03-11

### Features

- **Bottom-pinned task dock** — moved the project task list into a dedicated
  bottom viewport pane so the chat scrolls independently, while only rendering
  the dock when there are active tasks to show.

- **Todo-driven local sample workflow** — local `.pod5` intake now creates an
  ordered `stage -> run -> analyze` workflow plan. Local samples are staged into
  `AGOUTIC_DATA/users/{username}/data/{sample_slug}` before Dogme submission,
  and analysis only auto-starts when it is the next ready workflow step.

- **Existing staged-sample decision gate** — when a staged local-sample folder
  already exists, the system now pauses and asks whether to reuse the existing
  copy or replace it with a fresh copy from the original source path.

### Fixes

- **Launchpad submission payload cleanup** — stripped Cortex-only
  `staged_input_directory` metadata from the `submit_dogme_job` MCP call so
  staged local-sample runs submit successfully after the copy step.

- **Workflow-aware task projection** — workflow plans now project as ordered
  parent/child tasks, suppress duplicate top-level run tasks, and keep
  follow-up decisions attached to the correct workflow in the UI.

### Docs

- Updated the local sample intake skill guidance to document the staged-copy
  execution model and the reuse-versus-replace decision point for existing
  staged folders.

### Tests

- Added regression coverage for local-sample staging, existing-folder decision
  gates, workflow-plan task projection, and the rule that
  `staged_input_directory` is never forwarded to Launchpad.

## [3.2.3] - 2026-03-11

### Features

- **Persistent task hierarchy expanded** — project tasks now project deeper child
  tasks for per-file downloads and per-run workflow stages, in addition to the
  earlier run → analysis/review/recovery hierarchy.

- **Task bootstrap script** — added `scripts/cortex/bootstrap_project_tasks.py`
  to seed persistent project tasks from existing `ProjectBlock` history for one
  project or the full database.

### Docs

- Updated the main README with the persistent task-list overview, bootstrap
  instructions for existing servers, and the focused task validation command.
- Clarified the supported startup flow: use `agoutic_servers.sh` for the backend
  stack and start the UI with `streamlit run ui/appUI.py` rather than running the
  Streamlit script directly with Python.

### Tests

- Added focused task hierarchy coverage for download-file children and workflow
  stage children in the Cortex project endpoint suite.

## [3.2.2] - 2026-03-11

### Features

- **Analyzer-side edgePython adapter foundation** — added initial server4
  adapter plumbing for the planned migration from the local `edgepython_mcp/`
  package to an upstream edgePython MCP service. The new foundation includes
  configurable upstream MCP URL/session TTL settings in `analyzer/config.py`,
  conversation-scoped upstream client/session helpers in
  `analyzer/edgepython_adapter.py`, and analyzer-owned proxy tool/schema
  registries in `analyzer/mcp_tools.py`.

### Notes

- This is groundwork only. Cortex still routes DE traffic directly to the
  existing `edgepython` service path; analyzer/server4 is not the live DE
  adapter yet.

### Tests

- Added analyzer adapter unit tests covering upstream-session helpers,
  artifact relocation, and proxy registry/schema generation.

## [3.2.1] - 2026-03-10

### Features

- **DE plot images in UI** — volcano, MD, and other edgePython-generated plots
  are now base64-embedded in the AGENT_PLAN payload and rendered inline in the
  chat with `st.image()`. No separate file-serving endpoint needed.

### Fixes

- **DE outputs saved to project folder** — `generate_plot` and `save_results`
  output files are now redirected to `{project_dir}/de_results/` instead of the
  server CWD or the input data directory. Filenames are preserved; plots default
  to `{plot_type}.png`. Explicit `output_path` on plots is still respected.

### Tests

- 6 new output path injection tests (27 total DE tests).

## [3.2.0] - 2026-03-10

### Features — edgePython Differential Expression Pipeline

- **edgePython MCP server** (`edgepython_mcp/`, port 8007) — new FastMCP service
  wrapping edgePython for bulk RNA-seq differential expression analysis. Exposes
  30+ tools for the full DE workflow: `load_data` → `filter_genes` → `normalize`
  → `set_design` → `estimate_dispersion` → `fit_model` → `test_contrast` →
  `get_top_genes` → `generate_plot` → `save_results`. Also supports single-cell
  DE, ChIP-seq enrichment, DTU/splice analysis, and voom-limma.

- **`differential_expression` skill** — new skill definition
  (`skills/Differential_Expression.md`) routes DE/DEG requests to the edgePython
  pipeline. Cortex detects keywords like "DE", "DEG", "differential expression"
  and auto-switches to this skill.

- **Skill routing for DE** — early pre-check in `_auto_detect_skill_switch()`
  ensures DE requests with CSV file paths are not hijacked by
  `analyze_local_sample`.

- **JSON Schema tool contracts** — `edgepython_mcp/tool_schemas.py` provides
  proper JSON Schema format for all 30 tools (with `properties`, `required`,
  `type`). Cortex fetches these at startup for param validation.

- **Server-side param extraction** — `_validate_edgepython_params()` in
  `data_call_generator.py` fills missing DE params (counts path, metadata path,
  group column, contrast, formula) from the user message via regex.

- **Mistral `[TOOL_CALLS]` format converter** — fallback parser in `cortex/app.py`
  handles devstral-small-2's native `[TOOL_CALLS]DATA_CALL[ARGS]{json}` format,
  converting it to standard `[[DATA_CALL:...]]` tags.

### Fixes

- **`save_results` FunctionTool crash** — `save_results` called the
  `@mcp.tool()`-decorated `get_result_table` directly, which became a
  `FunctionTool` object after decoration and was not callable. Extracted shared
  logic into `_get_result_table_impl()` so both tools call a plain function.

- **MCP tool errors killing all tool calls** — `RuntimeError` from
  `mcp_client.call_tool()` was not caught by the inner error handler (only
  `ConnectionError`/`TimeoutError`/`OSError` were caught), so it bubbled up to
  the outer `except` which applied the same error to ALL tool calls. Added
  `except RuntimeError` to isolate per-tool MCP errors and trigger edgepython
  early-stop correctly.

- **edgePython early-stop** — sequential pipeline steps now stop on first
  failure instead of re-running all 10+ steps with the same error.

### Tests

- 21 new DE tests: 8 routing, 8 param extraction, 2 schema format, 3 Mistral
  conversion.

## [3.1.4] - 2026-03-10

### Features — Template-Based System Prompts

- **Human-readable prompt templates** — moved the Cortex first-pass and
  second-pass system prompt bodies out of inline Python strings into Markdown
  templates under `cortex/prompt_templates/`, while preserving dynamic skill
  injection, DATA_CALL guidance, tool contracts, and current runtime behavior.

- **Prompt inspection in chat** — users can now ask to see the current system
  prompt from chat. The server returns the exact rendered prompt directly,
  asks whether the user wants the first-pass or second-pass prompt when the
  request is ambiguous, and bypasses the LLM for these inspection requests.

- **Prompt rendering API** — `AgentEngine` now renders prompts through a shared
  template-backed API so the same rendered prompt text is used both for LLM
  calls and for prompt inspection responses.

## [3.1.3] - 2026-03-09

### Features — Universal Cancel & User Data

- **Download cancel button** — new "🛑 Cancel Download" button appears on all
  running `DOWNLOAD_TASK` blocks. Calls `DELETE /projects/{pid}/downloads/{did}`
  to set the `cancelled` flag; the background streaming task checks this flag
  every 64 KB chunk, deletes partial files, and updates the block to CANCELLED.
  Handles stale downloads (server restart) gracefully with a 404 warning instead
  of crashing.

- **"List my data" chat command** — users can now say "list my data",
  "list my files", "show my data", or "what files do I have" in the chat to
  see all files in their central data folder. The feature queries the
  `user_files` DB table first (rich metadata: source, accession, sample name),
  with a disk-scan fallback for files not yet registered in the DB.
  Results are rendered as a markdown table directly — no LLM round-trip needed.

- **Improved skill routing** — `_auto_detect_skill_switch()` now routes
  "list files", "show files", "list workflows" to `analyze_job_results` for
  job file browsing, while "list my data" / "list my files" are handled
  directly by the user-data override in the chat handler (no skill switch).
  Welcome.md updated with file-browsing routing signals.

## [3.1.2] - 2026-03-09

### Features — Job Lifecycle Management

- **Chat stop button** — cancel in-flight LLM requests with stage-aware
  messages ("Stopped during planning", "Stopped during data retrieval", etc.).
  Uses cooperative `ChatCancelled` exception with cancellation checkpoints
  throughout the chat pipeline.

- **Download cancel + cleanup** — per-chunk cancellation check during file
  downloads; partial files are automatically deleted on cancel with an
  informative message.

- **Nextflow job cancellation** — Cancel button on RUNNING jobs sends SIGTERM
  to the Nextflow process. A `.nextflow_cancelled` marker file prevents the
  background monitor from overwriting the status with FAILED. The UI correctly
  shows CANCELLED with task progress at time of cancellation.

- **Post-cancel Delete / Resubmit** — CANCELLED job blocks show two action
  buttons:
  - 🗑️ **Delete** — two-click confirmation, calls `DELETE /jobs/{uuid}`,
    removes the workflow folder via `shutil.rmtree`, sets Launchpad DB status
    to DELETED, and updates the Cortex EXECUTION_JOB block immediately so the
    UI renders the deleted state without stale RUNNING artifacts.
  - 🔄 **Resubmit** — creates a pre-populated APPROVAL_GATE with the original
    job parameters. After approval, submits with Nextflow `-resume` flag
    pointing at the same workflow directory, so cached tasks (e.g. basecalling)
    are skipped.

- **Chat-based deletion** — users can say "delete workflow1" in chat; the
  Welcome skill routes to the `delete_job_data` MCP tool on Launchpad.

- **DELETED status enum** — new `JobStatus.DELETED` across Launchpad config,
  models, and UI rendering. `check_status()` returns DELETED when the work
  directory no longer exists. `get_job_status` short-circuits for all terminal
  DB states (DELETED, COMPLETED, FAILED, CANCELLED) without hitting the
  filesystem.

### Features — Download Path Fix

- Fixed file downloads to go to `users/{user}/data` instead of
  `users/{user}/{project}/data`.

### Fixes

- Fixed `reference_genome` deserialization crash on resubmit — the DB stores it
  as a JSON string like `'["mm39"]'`; both the resubmit endpoint and the UI
  multiselect widget now parse and normalize it correctly.

- Fixed `resume_from_dir` being dropped on approval — the UI's `edited_params`
  now preserves `resume_from_dir` from `extracted_params`, and
  `submit_job_after_approval` falls back to `extracted_params` as a safety net.

- Fixed MCP server `submit_dogme_job` missing `resume_from_dir` parameter that
  caused a Pydantic validation error on resubmit.

### Tests

- All 771 tests passing (718 cortex + 53 launchpad).

## [3.1.2] - 2026-03-07

### Cleanup

- Removed deprecated manual test and migration scripts that were superseded by
  the consolidated `tests/` suite and current runtime/bootstrap workflow,
  including the old in-package `test_*.py` helpers, `migrate_*.py` scripts,
  and the root-level `test_*.sh` shell scripts.

- Moved the remaining manual admin and operational utilities into a dedicated
  `scripts/` tree:
  `scripts/cortex/init_db.py`, `scripts/cortex/set_usernames.py`,
  `scripts/launchpad/debug_job.py`, and
  `scripts/launchpad/submit_real_job.py`.

- Updated the active README and quickstart documentation to point at the
  `scripts/` locations and the current pytest-based test commands.

## [3.1.1] - 2026-03-07

### Fixes

- Fixed permanent project deletion so user project directories are resolved
  before the database row is removed, which now cleans up slug-based project
  folders under `AGOUTIC_DATA/users/...` instead of leaving them behind.

- Restored analyzer proxy compatibility after the route extraction by making
  the extracted proxy endpoints honor the legacy `cortex.app`
  `require_run_uuid_access` override used by older tests, which fixes the
  `GET /analysis/jobs/{run_uuid}/summary` and
  `GET /analysis/jobs/{run_uuid}/files` pytest failures seen on Watson.

- Preserved token accounting after permanent project deletion by archiving each
  deleted project's lifetime and daily token totals in new Cortex models and
  folding those archived totals back into the user and admin token-usage
  endpoints.

- Added deleted-project token archive support to the manual database bootstrap
  script so fresh databases can create the new accounting tables.

### Tests

- Added new Cortex regression tests covering permanent project deletion cleanup,
  preservation of deleted-project token accounting in user and admin usage
  endpoints, and analyzer proxy endpoint compatibility after the route
  extraction.

- Added new unit-test coverage in this release cycle for Launchpad Nextflow
  config generation, Launchpad DB/MCP helpers, logging middleware behavior,
  Atlas config and launcher branches, Analyzer app error handling, and UI
  helper functions.

## [3.1.0] - 2026-03-06

### Refactor — Tier 3: Extract Route Handlers & Shared DB Helpers

`cortex/app.py` reduced from **5,087 → 3,451 lines** (-1,636 lines, -32%) by
extracting all REST endpoints (except chat/block/approval) and shared database
helpers into focused modules. All 865 tests pass. Zero behavioral change.

**New modules:**

- **`cortex/db_helpers.py`** (194 lines) — Shared database utilities used by
  multiple route modules: `_resolve_project_dir`, `_create_block_internal`,
  `save_conversation_message`, `track_project_access`

- **`cortex/routes/projects.py`** (844 lines) — Project management endpoints:
  create/list/update/delete projects, project stats, file listing, token usage,
  disk usage, soft & permanent delete. Includes `_slugify`, `_dedup_slug`.

- **`cortex/routes/conversations.py`** (173 lines) — Conversation/job endpoints:
  list conversations, fetch messages, list jobs, link job to conversation.

- **`cortex/routes/files.py`** (398 lines) — File download/upload endpoints:
  initiate download, get status, cancel, background download task, upload files.

- **`cortex/routes/analyzer_proxy.py`** (210 lines) — Analyzer/Launchpad MCP
  proxy endpoints: analysis summary, job files, file content, CSV/BED parsing.

**Import pattern:** Route modules use module-level imports (`import cortex.db as _db`)
so that existing test patches on `cortex.db.SessionLocal` propagate automatically.

**Test files updated (3):** `test_analyzer_proxy.py`, `test_download_upload.py`,
`test_project_management.py` — added companion patches for new module paths.

**Result: 865/865 passing on macOS.**

### Refactor — Tier 2: Extract Stateful Helpers from cortex/app.py

`cortex/app.py` reduced from **6,575 → 5,252 lines** (-1,323 lines, -20%) by
extracting session-aware and LLM-dependent helper functions into three new modules.
All 865 tests pass. Zero behavioral change.

**New modules:**

- **`cortex/conversation_state.py`** (270 lines) — Conversation state management:
  `_build_conversation_messages`, `_get_project_context`, `_inject_recent_results`

- **`cortex/context_injection.py`** (671 lines) — Context injection and skill routing:
  `_inject_context_for_skill`, `_maybe_switch_skill`, `_build_system_prompt`

- **`cortex/data_call_generator.py`** (459→~600 lines) — Data call automation:
  `_auto_generate_data_calls`, `_validate_analyzer_params`

**Test files updated (1):** `test_pure_helpers2.py` — updated import for
`_validate_analyzer_params` now in `data_call_generator`.

**Result: 865/865 passing on macOS.**

### Refactor — Tier 1: Extract Pure Functions from cortex/app.py

`cortex/app.py` reduced from **7,412 → 6,575 lines** (-837 lines, -11%) by
extracting all pure, zero-coupling functions into four new focused modules.
All 865 tests pass on macOS and Watson. Zero behavioral change.

**New modules:**

- **`cortex/encode_helpers.py`** (475 lines) — ENCODE constants and routing logic:
  `_ENCODE_ASSAY_ALIASES`, `_looks_like_assay`, `_correct_tool_routing`,
  `_validate_encode_params`, `_find_experiment_for_file`, `_extract_encode_search_term`

- **`cortex/llm_validators.py`** (220 lines) — LLM output validation and skill detection:
  `get_block_payload`, `_parse_tag_params`, `_validate_llm_output`,
  `_auto_detect_skill_switch`

- **`cortex/analysis_helpers.py`** (125 lines) — Analysis context/summary builders:
  `_build_auto_analysis_context`, `_build_static_analysis_summary`

- **`cortex/path_helpers.py`** (102 lines) — File/workflow path resolution:
  `_pick_file_tool`, `_resolve_workflow_path`, `_resolve_file_path`

**Dependency chain:** `encode_helpers` ← `llm_validators` ← `app.py`
(no circular imports, no DB deps in any extracted module)

**Test files updated (8):** `test_block_endpoints.py`, `test_chat_edge_cases.py`,
`test_encode_helpers.py`, `test_pure_helpers.py`, `test_pure_helpers2.py`,
`test_skill_detection.py`, `test_tool_routing.py`, `test_validation.py`

**Result: 865/865 passing on macOS and Watson.**

## [3.0.9] - 2026-03-05

### Added — Comprehensive Test Suite for Pre-Refactor Safety Net

Built a full black-box test suite to enable safe refactoring of `cortex/app.py`
(7,412-line monolith). **865 tests** across all components, with **82% coverage**
on the Cortex engine (up from 0% at start of effort).

**Cortex tests (690 across 34 files):**
- Pure helper functions: `_parse_tag_params`, `_looks_like_assay`, `_pick_file_tool`, `_validate_llm_output`, `_correct_tool_routing`, `_emit_progress`, `_resolve_workflow_path`, `_resolve_file_path`
- Chat endpoint: entry validation, skill detection, DATA_CALL execution, ENCODE search swap retry, browsing tool bypass, download workflow, plot tag parsing (key-value, natural language, code fallback, command override), hallucination detection, chain recovery, large result truncation, token tracking
- Approval gate: creation, suppression for non-job skills, parameter extraction
- Background tasks: `poll_job_status`, `_auto_trigger_analysis`, `_post_download_suggestions`
- Project management: CRUD, stats dashboard, files listing, disk usage, token usage, permanent delete cascade, upload with filename sanitization
- Block endpoints: create, read, update, sequence numbering
- Conversation: save/load messages, state building, inject job context
- Auth & admin: login/logout, session management, user CRUD, admin token usage
- Analyzer/Launchpad proxy: MCP helper calls, connection error handling
- Download endpoints: initiate, status, cancel, background task, `_update_download_block`

**Other component tests (175):**
- Atlas: result formatter, tool schemas
- Common: MCP client
- Analyzer: analysis engine
- Launchpad: models, config
- UI: app configuration

**Test infrastructure:**
- In-memory SQLite with `StaticPool` for isolation
- `_patch_session` context manager patching `SessionLocal` across 6 modules
- `_make_client` pattern with mocked `AgentEngine` (`.think`, `.analyze_results`)
- `MCPHttpClient` mocking for all external service calls
- `pytest.ini` with `--cov` across all components
- Shared fixtures in `tests/conftest.py`

Files changed: `tests/` (34 cortex test files + conftest + component tests), `pytest.ini`

### Fixed — test_approval_suppressed_non_job_skill mock

`_make_client` in `test_chat_flow.py` was not setting `analyze_results` on
the mocked `AgentEngine`, causing a `ValueError` when the ENCODE_Search
suppression path fell through to the second-pass LLM call. Added default
`analyze_results` lambda to return `(str, dict)`. Also patched
`require_run_uuid_access` in `TestAnalyzerProxyEndpoints` to bypass the
real DB lookup during tests.

**Result: 865/865 passing, 0 failures.** Validated on both macOS and Watson.

### Fixed - Removed Accidental Python Bytecode Files From Version Control

Cleaned up accidentally committed Python cache artifacts and added generic ignore
rules so compiled files are no longer tracked.

Files changed: `.gitignore`

## [3.0.8] - 2026-03-03

### Fixed — ENCODE Follow-Up Questions Not Injecting Previous Data

When asking "what are the accessions for the long read RNA-seq samples?" after
a C2C12 search (58 results), the system returned 9 wrong file accessions (ENCFF...)
from a single experiment instead of the 3 correct experiment accessions (ENCSR...).

**Root cause (two issues):**

1. **`_followup_signals` too narrow**: The `_inject_job_context` ENCODE branch
   didn't recognise "the accessions", "the samples", "the experiments", or
   "which are/is..." as follow-up language. The parallel `_ref_words` list (used
   for auto-generation) *did* include "the accessions", creating an inconsistency.
   Result: the message was not flagged as a follow-up, so no data was injected.

2. **Assay terms triggered "new query" detection**: `_extract_encode_search_term`
   extracted "accessions long read RNA-seq". Since that didn't match any previous
   DF label ("c2c12"), the code set `_is_new_query = True` and skipped injection
   entirely. Assay names are filters on existing data, not new search subjects.

**Fixes:**

- Added 4 new follow-up signal patterns to `_followup_signals`:
  `the accessions?`, `the samples?`, `the experiments?`, `which are|is|were|have`
- Added `_looks_like_assay()` guard in the "new search subject" detection — if
  the extracted term contains assay indicators (e.g. "long read", "rna-seq"), it
  is treated as a follow-up filter, not a new query
- Added "accessions" / "accession" to `_ENCODE_STOP_WORDS` so the fallback
  tokenizer doesn't treat them as biosample search terms
- Updated `ENCODE_Search.md` skill example to cover this exact failure scenario

Files changed: `cortex/app.py`, `skills/ENCODE_Search.md`

# Changelog - February 2026

## [3.0.7] - 2026-02-27

### Fixed - Modified Dogme_DNA.md and DOGME_QUick_WORKFLOW_GUIDE.md to reflect bedMethyl and openChromatin file structure

The skills file only focused on m6A and 5mCG. Added 5hmCG.
Also cleaned up several paths. No change to python code, so no change to version.

### Fixed — LLM Context Window Stuck at 4,096 Tokens (ROOT CAUSE)

Every Ollama API call was using the default context window (4,096 tokens), even
though devstral supports 256k. The system prompt alone can exceed 4k tokens once
skill definitions, tool contracts, DATA_CALL format instructions, and examples
are included — meaning the LLM was seeing a truncated prompt on every call.

This was the root cause of multiple symptoms:
- LLM emitting `[[TOOL_CALL:...]]` instead of `[[DATA_CALL:...]]`
- Narrating "STEP 1 / STEP 2" instead of executing tool calls
- Hallucinating data instead of querying real services
- Ignoring format instructions and conversation history

**Fix:** Pass `extra_body={"options": {"num_ctx": LLM_NUM_CTX}}` to both
`client.chat.completions.create()` calls in `agent_engine.py`. The value
defaults to 131,072 (128k) and is configurable via the `LLM_NUM_CTX`
environment variable.

- Files changed: `cortex/agent_engine.py`, `cortex/config.py`

## [3.0.6] - 2026-02-27

### Fixed - Typo in Analyze_Job_Results.md RNA routing
No change to python code, so no change to version.

### Fixed - Modified Dogme_RNA.md to reflect bedMethyl file structure

The skills file only focused on m6A and pseudouridine. Added m5C, Nm and Inosine.
Also cleaned up several paths. No change to python code, so no change to version.

### Fixed — Polling Timeout Too Short for Long-Running Pipelines

The `poll_job_status` background task used a flat loop of 600 polls × 3 s =
30 minutes. RNA BAM remap jobs can take ~2.5 hours, so the poller would give
up long before the job finished, silently preventing auto-analysis.

- **Adaptive polling schedule**: Replaced the flat loop with a 4-tier schedule:
  120 × 3 s (6 min), 120 × 10 s (20 min), 120 × 30 s (60 min), 960 × 30 s
  (~8 h). Total coverage: ~10 hours — enough for the longest pipelines.
- **Nested loop break fix**: The initial implementation used nested `for` loops
  but `break` only exited the inner loop. Added a `_job_done` sentinel flag
  checked by both loops.
- Files changed: `cortex/app.py` (`poll_job_status`)

### Fixed — Auto-Analysis Not Triggered After Server Restart Recovery

When Cortex was restarted while a Nextflow job was running, the startup
recovery code correctly detected the completed job and marked the block as
DONE, but it did not call `_auto_trigger_analysis`. The user had to manually
request analysis.

- Startup recovery now fires `_auto_trigger_analysis` via `asyncio.create_task`
  for any orphaned RUNNING block whose inner status is already COMPLETED.
- Files changed: `cortex/app.py` (startup recovery in `@app.on_event("startup")`)

### Fixed — LLM Displayed Wrong Sample Name from Conversation History

When submitting a second sample (e.g. C2C12r2) in the same project after a
previous sample (C2C12r1), the LLM echoed the old sample name in its plan
text. The actual job execution used the correct name (the parameter extraction
pipeline was fine), but the display confused users.

Root cause: two code paths fed stale sample names to the LLM:
1. `_build_conversation_state` fast path restored the cached `[STATE]` from the
   previous AGENT_PLAN block, which contained `sample_name: "C2C12r1"`.
2. The slow-path `collected_params` extraction iterated conversation history
   **forward**, so the first (older) match won.

- Fast path now re-extracts `collected_params` from the most recent user
  messages, overriding stale cached values for sample_name, path, sample_type,
  and reference_genome.
- Slow path now iterates conversation history in **reverse** (most recent
  first), matching the same pattern used by `extract_job_parameters_from_conversation`.
- Files changed: `cortex/app.py` (`_build_conversation_state`)

### Fixed — LLM Emitted `[[TOOL_CALL:...]]` Instead of `[[DATA_CALL:...]]` for Analyzer

When asked to analyse job results, the LLM sometimes emitted
`[[TOOL_CALL: GET /analysis/jobs/{work_dir}/summary?work_dir=...]]` — a
REST-style tag that the system doesn't recognise — instead of the correct
`[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=...]]`.
The result: the analysis tool was never called, `auto_calls_count: 0`, and the
LLM's narrated "STEP 1 / STEP 2" plan was displayed without any data.

- **Fallback converter**: Added a regex in the fallback-fix pipeline that
  catches `[[TOOL_CALL: GET /analysis/...?param=val]]` and converts it to a
  proper `[[DATA_CALL: service=analyzer, tool=..., param=val]]` tag.
- **System prompt fix**: Updated the analyzer examples in
  `construct_system_prompt` to use `work_dir=` (the preferred parameter)
  instead of the legacy `run_uuid=`, and added explicit "FORBIDDEN" examples
  for the TOOL_CALL and STEP-narration anti-patterns.
- **Auto-generation safety net**: `_auto_generate_data_calls` now has a
  catch-all for the `analyze_job_results` skill: when no tags are parsed and
  a `work_dir` or `run_uuid` is available from context, it auto-generates a
  `get_analysis_summary` call so the analysis executes even if the LLM
  produces no usable tags.
- Files changed: `cortex/app.py` (fallback pipeline, `_auto_generate_data_calls`),
  `cortex/agent_engine.py` (`construct_system_prompt` analyzer examples)

## [3.0.5] - 2026-02-26

### Fixed — Successful Nextflow Jobs Marked as Failed After Server Restart

When Launchpad (server3) was restarted while a Nextflow job was running, the
in-memory `_monitor_process` coroutine was lost. On the next `check_status`
call, the code saw the PID was gone and `.nextflow_running` still existed, so
it declared "Process died unexpectedly" — even though Nextflow had finished
successfully with all tasks COMPLETED.

- **Trace-based recovery**: Before declaring failure, `check_status` now reads
  the trace file and checks whether every task has status `COMPLETED` or
  `CACHED`. If so, it writes `.nextflow_success` ("Recovered at …") and
  returns success instead of failure.
- **ANSI / version-nag filtering**: Nextflow prints `\e[33mNextflow 25.10.4
  is available - Please consider updating\e[m` to stderr. This was being
  captured verbatim as the "error" message. Both `_monitor_process` and the
  `check_status` dead-process handler now strip ANSI escape codes and drop
  lines containing "is available" / "consider updating" before writing
  `.nextflow_error`.
- Files changed: `launchpad/nextflow_executor.py` (`check_status`,
  `_monitor_process`)

### Fixed — Trace File Lookup Preferred Wrong Filename

The progress parser tried `trace.txt` first, then fell back to `*_trace.txt`.
Since Nextflow's config sets `trace.enabled = true` with
`file = "${params.sample}_trace.txt"`, an empty `trace.txt` could shadow the
real sample-specific file (e.g. `C2C12r1_trace.txt`).

- Reversed lookup order: glob `*_trace.txt` first, fall back to `trace.txt`.
- Files changed: `launchpad/nextflow_executor.py` (trace file discovery)

## [3.0.4] - 2026-02-26

### Fixed — Nextflow Task Names Missing for Non-Main Entry Points

When analysing a BAM file (remap entry point), the job progress UI showed "0/1
completed" without any task names. The same applied to `basecall`, `modkit`,
`annotateRNA`, and `reports` entry points.

Root cause: both the `trace.txt` parser and the `stdout` parser in
`nextflow_executor.py` were hardcoded to only accept tasks prefixed with
`mainWorkflow:`. Alternate entry points produce tasks like `remap:minimapTask`
which were silently skipped.

- Changed filter from `task_name.startswith('mainWorkflow:')` to `':' in task_name`
  — this accepts tasks from **all** Nextflow workflow entry points while still
  filtering out internal Nextflow tasks (which have no colon).
- Applied the same fix in the stdout running-task parser.
- Files changed: `launchpad/nextflow_executor.py` (trace parser + stdout parser)

## [3.0.3] - 2026-02-25

### Changed — Centralised Version Number

Version was hardcoded in 5 separate places (`agoutic_servers.sh`, `ui/appUI.py` ×2,
`cortex/app.py` health endpoint, `README.md`) and frequently fell out of sync.

- **Single source of truth**: New `VERSION` file at repo root contains the version string.
- `ui/appUI.py` reads `VERSION` via `pathlib` and uses it in the page title and sidebar caption.
- `cortex/app.py` reads `VERSION` the same way; the `/health` endpoint now returns the live value.
- `agoutic_servers.sh` reads `VERSION` via `cat` instead of a hardcoded variable.
- `README.md` updated to 3.0.3.
- Files changed: `VERSION` (new), `ui/appUI.py`, `cortex/app.py`, `agoutic_servers.sh`, `README.md`

### Fixed — Chat Messages Disappearing from UI

_(carried forward from 3.0.2 development)_

Messages and results would silently vanish from the chat during auto-refresh cycles.
Root cause: `get_sanitized_blocks()` caught ALL exceptions and returned `[]`, and
`_render_chat()` unconditionally overwrote `st.session_state.blocks` — so a single
transient fetch failure (timeout, server busy) wiped the displayed conversation.

- **Resilient block fetch**: `get_sanitized_blocks` now returns `(blocks, fetch_ok)`;
  on failure, `_render_chat` keeps the cached session-state blocks instead of blanking
  the chat. A "⚠️ Could not refresh" indicator is shown when falling back to cache.
- **Increased fetch limit**: 100 → 500 blocks (long conversations were silently
  truncated — oldest-first ordering missed the newest messages)
- **Newest-first for large projects**: When the block count exceeds the limit, the
  server now fetches the *newest* N blocks via a DESC sub-query, re-sorted to ASC
  for chronological display.
- **Longer timeout**: Block fetch timeout 5 s → 10 s to reduce transient failures.
- **Error logging**: Failures are now logged with status code / exception detail
  instead of being silently swallowed.
- Files changed: `ui/appUI.py` (`get_sanitized_blocks`, `_render_chat`),
  `cortex/app.py` (`get_blocks` endpoint)

## [3.0.2] - 2026-02-25

### Fixed — ENCODE `search_by_assay` Missing from Tool Routing & DataFrame Pipeline

When the LLM generated `tool=search` (a generic alias) with assay-style params like `assay_title=long read RNA-seq, organism=Mus musculus`, the alias resolved to `search_by_biosample` which requires `search_term`. With no `search_term`, the call returned 0 results.

- **Pre-call routing**: `_correct_tool_routing` now detects `search_by_biosample` called without `search_term` but with a valid `assay_title`, and reroutes to `search_by_assay`
- **Alias validation**: Added `"search"` to `_known_tools` in `_validate_llm_output` so it no longer triggers a false "Unknown tool" violation
- Files changed: `cortex/app.py` (`_correct_tool_routing`, `_validate_llm_output`)

### Fixed — ENCODE Search Retry on 0 Results (Biosample ↔ Assay Swap)

When a search term is ambiguous (could be a biosample name or an assay type), the LLM may pick the wrong tool. If the first call returns 0 results, the system now automatically retries with the alternate tool.

- `search_by_biosample` returns 0 → retry as `search_by_assay` (term treated as assay)
- `search_by_assay` returns 0 and term doesn't look like an assay → retry as `search_by_biosample` (term treated as biosample)
- If both return 0, original empty result is kept
- Files changed: `cortex/app.py` (MCP call execution loop)

### Fixed — `search_by_assay` Results Not Rendered as DataFrames

`search_by_assay` was missing from `_SEARCH_TOOLS`, so its results were never converted to interactive DataFrames in the UI — they showed as raw JSON in the query details instead.

- Added `search_by_assay` to `_SEARCH_TOOLS` set
- Added dict-format normalisation: `search_by_assay` returns `{"total", "experiments": [...]}` or `{"human": [...], "mouse": [...]}`, unlike other search tools that return a plain list. Both formats are now extracted to a flat experiment list before DataFrame creation
- Fixed registry lookup for table columns: was only checking `SERVICE_REGISTRY` (Launchpad/Analyzer), now also checks `CONSORTIUM_REGISTRY` (ENCODE) so the configured column set (Accession, Assay, Biosample, Target) is used
- Added `assay_title` as a DataFrame label source (e.g. "long read RNA-seq (72 results)")
- Files changed: `cortex/app.py` (DataFrame extraction in step 7)

---

## [3.0.1] - 2026-02-24

### Fixed — Cortex Restart Kills Job Monitoring (Startup Recovery)

When Cortex was restarted while a Dogme job was running, the background `poll_job_status` asyncio task was silently killed. The `EXECUTION_JOB` block stayed permanently stuck in `RUNNING`, which had two cascading effects: `_auto_trigger_analysis` never fired (no analysis result appeared after job completion) and any subsequent skill switch was stripped by the output validator.

- **Startup recovery**: on every Cortex startup, orphaned `EXECUTION_JOB` blocks still marked `RUNNING` are detected. If their inner `job_status.status` payload already shows `COMPLETED`/`FAILED`, the block is immediately marked done. Otherwise, `poll_job_status` is re-spawned to resume monitoring — including triggering auto-analysis on completion.
- Files changed: `cortex/app.py` (`startup_event`)

### Fixed — SKILL_SWITCH Incorrectly Blocked on Post-Restart Stale Blocks

The output validator's guard against skill switches during active jobs compared only `block.status`, which could be `RUNNING` even after job completion if the server had restarted. This caused legitimate skill switches (e.g., switching to `run_dogme_rna` to show analysis results) to be stripped, leaving the user stuck on the `welcome` skill with a blank response.

- Guard now cross-checks the nested `job_status.status` in the block payload before stripping. If the inner status is `COMPLETED` or `FAILED`, the block is treated as done and the switch is allowed.
- Files changed: `cortex/app.py` (`_validate_llm_output`)

### Fixed — "analyze the result" Not Routing to analyze_job_results Skill

The pre-LLM skill router only matched `"analyze results"` exactly. Phrases with articles or singular forms ("analyze the result", "show me the results", "see the result") did not match, so the `welcome` skill was used and the `auto_skill_detected` debug field showed `null`.

- Expanded `_results_words` keyword list to include: `"analyze the result"`, `"analyse the result"`, `"analyse results"`, `"show the results"`, `"view the result"`, `"view results"`, `"show result"`, `"see the result"`, `"the results"`
- Files changed: `cortex/app.py` (`_auto_detect_skill_switch`)

---

## [3.0.0] - 2026-02-24

### Added — Run Dogme from Downloaded BAM Files

Users can now run the Dogme pipeline starting from the **remap** entry point using downloaded BAM files (e.g., from ENCODE). The system automatically symlinks BAMs into the workflow's `bams/` folder with the correct naming conventions.

- **Remap from BAM**: BAM files in `projectdir/data/` are symlinked as `{sample_name}.unmapped.bam` and run with `-entry remap`
- **Directory input support**: `input_dir` can now be a directory (not just a single file) — the executor scans for `*.bam` files and matches by sample name
- **Relative path resolution**: Paths like `data/ENCFF921XAH.bam` are automatically resolved to absolute project paths
- Files changed: `launchpad/nextflow_executor.py`, `cortex/app.py`, `skills/Local_Sample_Intake.md`, `skills/Download_Files.md`, `skills/ENCODE_LongRead.md`

### Fixed — BAM Symlink Naming Conventions

Corrected symlink naming for all Dogme entry points to match what the pipeline expects:

- **remap**: `{sample_name}.unmapped.bam` (was `{sample_name}.bam`)
- **modkit**: `{sample_name}.{genome_ref}.bam` (was `{sample_name}.bam`) — genome ref is required, not guessed
- **annotateRNA**: `{sample_name}.{genome_ref}.bam` (was `{sample_name}.bam`) — genome ref is required, not guessed
- Files changed: `launchpad/nextflow_executor.py`

### Fixed — Genome Reference Validation for modkit/annotateRNA

For `modkit` and `annotateRNA` entry points, the reference genome must match what the BAM was actually mapped to. The system no longer silently defaults to `mm39`.

- Raises `RuntimeError` if `reference_genome` is empty for modkit/annotateRNA
- Cortex clears the auto-default genome when entry point is modkit/annotateRNA and no genome was explicitly mentioned, forcing the intake skill to ask the user
- Files changed: `launchpad/nextflow_executor.py`, `cortex/app.py`, `skills/Local_Sample_Intake.md`

### Fixed — Skill Routing for BAM Analysis Requests

Requests like "analyze the sample c2c12r1 using the bam file data/ENCFF921XAH.bam as a rna sample" now correctly route to `analyze_local_sample` instead of getting stuck on `analyze_job_results`.

- **Relative path detection**: `_auto_detect_skill_switch` now recognizes relative paths with sequencing extensions (`.bam`, `.pod5`, `.fastq`)
- **BAM as signal word**: Added `.bam`, `bam file`, `bam files` to skill routing signals
- **Skill alias mapping**: LLM-hallucinated skill names (`run_workflow`, `submit_job`, `run_dogme`) now map to real registry keys
- Files changed: `cortex/app.py`

### Fixed — Sample Name and Path Extraction

Parameter extraction from conversation now handles natural phrasing and relative paths:

- **Sample name**: Added patterns for "the sample X" and "analyze X using"
- **Relative paths**: `data/ENCFF921XAH.bam` is detected and resolved to the full project path
- **Expanded skip words**: Prevents false positives from common words like "name", "type", "data", "rna"
- Files changed: `cortex/app.py`

### Fixed — SyntaxError in cortex/app.py

Fixed a merge issue where the BAM resolution block was inserted inside the `job_data` dict literal, leaving orphaned entries and an unmatched `}`.

- Files changed: `cortex/app.py`

### Added — Cortex Entry Point Detection for Downloaded BAMs

Cortex now auto-detects "downloaded bam", "from bam", and BAM-from-data scenarios, automatically setting `entry_point=remap` and `input_type=bam`.

- Files changed: `cortex/app.py`

## [2.9] - 2026-02-23

### Added — GPU Concurrency Control in Approval Form

Users can now control the maximum number of simultaneous GPU tasks (dorado basecalling, openChromatin) per pipeline run directly from the plan approval form.

- New "🖥️ Max Concurrent GPU Tasks" selectbox (1–8) in the Streamlit approval form
- Applies Nextflow `maxForks` directive to `doradoTask`, `openChromatinTaskBg`, `openChromatinTaskBed`
- Default: 1 (safe for single-GPU machines), configurable via `DEFAULT_MAX_GPU_TASKS` env var
- Natural language extraction: "limit dorado to 3 concurrent tasks" → `max_gpu_tasks: 3`
- Files changed: `launchpad/config.py`, `launchpad/schemas.py`, `launchpad/nextflow_executor.py`, `launchpad/app.py`, `launchpad/mcp_server.py`, `launchpad/mcp_tools.py`, `cortex/app.py`, `ui/appUI.py`, `skills/Local_Sample_Intake.md`

## [2.8] - 2026-02-22

### Fixed — FastMCP 2.x Compatibility

FastMCP upgraded to v2.14.2 where `mcp.http_app` changed from a property to a method. All three MCP servers failed to start after the upgrade.

- `launchpad/mcp_server.py`: `mcp.http_app` → `mcp.http_app()`
- `analyzer/mcp_server.py`: `mcp.http_app` → `mcp.http_app()`
- `atlas/launch_encode.py`: `mcp_instance.http_app` → `mcp_instance.http_app()`

### Fixed — Spurious ENCODE Auto-Calls on Visualization / DF Follow-ups

When the user typed "plot DF1 by assay", the system fired a spurious `search_by_biosample` call because `_auto_generate_data_calls()` didn't recognise DataFrame references or visualization keywords.

- Added early-return guards in `_auto_generate_data_calls()` for DF references (`\bDF\s*\d+\b`) and visualization keywords (`plot`, `chart`, `graph`, `histogram`, `scatter`, etc.)
- Expanded `_ENCODE_STOP_WORDS` with visualization/follow-up terms (`plot`, `chart`, `filter`, `sort`, `group`, `aggregate`, `table`, etc.)
- Applied to: `cortex/app.py`

### Fixed — `UnboundLocalError` on `source_key` in System Prompt

`construct_system_prompt()` in `agent_engine.py` crashed for skills without a data source (e.g. `download_files`) because `format_tool_contract(source_key, source_type)` was outside the `if source_info:` block.

- Indented `format_tool_contract()` call inside the `if source_info:` guard
- Applied to: `cortex/agent_engine.py`

### Fixed — False "Unexpected Data" on Download Workflows

File download metadata went through the second-pass LLM, which reported "The query did not return the expected data" instead of building the download approval gate.

- Skip second-pass LLM for download workflows: detect via `_chain == "download"` or `active_skill == "download_files"`
- Applied to: `cortex/app.py`

### Fixed — Multi-File Download Lost Previous Files

`_pending_download_files = [_dl_file_info]` replaced the list on each iteration of the download chain, losing previously resolved files.

- Changed to `_pending_download_files.append(_dl_file_info)`
- Rebuilt download plan message to list all pending files with individual sizes and total
- Applied to: `cortex/app.py`

### Fixed — Provenance Block Swallowed Results in Streamlit

Wrapping `[TOOL_RESULT:...]` provenance in `<details>` at the top of browsing output caused Streamlit to render everything inside the collapsed section.

- Separated provenance from display data: plain-text provenance prepended for LLM, collapsed `<details>` appended at end for UI
- Applied to: `cortex/app.py`

### Fixed — Download Chain Not Triggered from ENCODE Skills

"Download files ENCFF546HTC..." stayed on `ENCODE_LongRead` skill instead of switching to `download_files`.

- Added `_user_wants_download` detection from user message keywords (`download`, `grab`, `fetch`, `save`)
- Updated `skills/ENCODE_Search.md`: added "🔀 DOWNLOAD ROUTING" section routing download+ENCFF requests to `download_files`
- Updated `skills/ENCODE_LongRead.md`: removed "download" as a trigger, added download-specific-files routing to `download_files`

### Fixed — Browsing Commands Injected Previous ENCODE DataFrame

"list files in data" while on an ENCODE skill responded with "📋 Answering from previous data (DF6)..." instead of listing files.

- Added browsing-command early exit in `_inject_job_context()` for ENCODE skills — patterns for `list/show workflows` and `list/show files`
- Applied to: `cortex/app.py`

### Added — Cascading Path Resolution for "list files in \<subfolder\>"

"list files in data" resolved to `workflow2/data` which didn't exist; the `data/` folder was at the project root.

- `list files in <subpath>` now cascades: try `work_dir/<subpath>` first (via `os.path.isdir`), fall back to `project_dir/<subpath>`
- Applied to: `cortex/app.py`

### Added — "list project files" Command

New explicit command to target the project root directory, bypassing the current workflow.

- `list project files` → lists top-level project directory
- `list project files in data` → always resolves to `<project_dir>/data`
- Applied to: `cortex/app.py`

### Added — Browsing Error Suggestions

When a `list_job_files` browsing command fails (e.g. directory not found), the error is shown directly with helpful suggestions instead of going through the LLM.

- Suggestions include: `list files`, `list project files`, `list project files in data`, `list files in workflow2/annot`, `list workflows`
- Applied to: `cortex/app.py`

### Fixed — `_validate_analyzer_params` Overriding Auto-Generated Paths

`list files in data` correctly resolved to `test20/data` (via cascading `os.path.isdir`), but `_validate_analyzer_params` overrode it back to `workflow2` because the path wasn't a subdirectory of the current workflow.

- Subdirectory check now also accepts paths under the **project directory** (parent of the workflow dir)
- Applied to: `cortex/app.py`

### Fixed — "list project files" Not Detected as Browsing Command

"list project files" wasn't matched by the browsing-command override at the caller level, so the LLM handled it instead — generating a spurious Dogme approval gate.

- Added `(?:project\s+)?` to the browsing override regex: `r'\b(?:list|show)\s+(?:the\s+)?(?:project\s+)?files?\b'`
- Applied to: `cortex/app.py`

### Fixed — Spurious Approval Gate on ENCODE Search After Dogme Context

After a Dogme job or `analyze_local_sample` conversation, switching to ENCODE_Search and running a query (e.g. "how many HL-60 experiments?") triggered a Dogme approval gate. The LLM echoed `[[APPROVAL_NEEDED]]` from conversation history.

- Added `_APPROVAL_SKILLS` whitelist: only `run_dogme_dna`, `run_dogme_rna`, `run_dogme_cdna`, `analyze_local_sample`, and `download_files` may trigger approval gates
- Any `[[APPROVAL_NEEDED]]` tag from other skills is suppressed and stripped
- Applied to: `cortex/app.py`

### Fixed — "plot this" Used Wrong DataFrame (Hallucinated DF1)

"plot this by lab" after an ENCODE query producing DF10 plotted DF1 (or DF3) instead. The LLM hallucinated earlier DF references despite having the correct `latest_dataframe` in its state.

- Added `latest_dataframe` field to `ConversationState` in `cortex/schemas.py`
- Updated `_build_conversation_state()` to populate `latest_dataframe` from the most recent embedded DF
- Updated PLOT instructions in `agent_engine.py` system prompt: when user says "this/it/the data", use `latest_dataframe` from `[STATE]` JSON
- Added deterministic post-processing override: if user message has no explicit `DF\d+` reference, replace the LLM's df_id in `[[PLOT:...]]` tags with `_conv_state.latest_dataframe`
- Applied to: `cortex/app.py`, `cortex/agent_engine.py`, `cortex/schemas.py`

### Fixed — Stale `latest_dataframe` in Cached Conversation State

The fast-path cache in `_build_conversation_state()` returned `latest_dataframe` from the **previous** request's state, because the state was saved _before_ the current block's DFs were embedded. This caused the PLOT df_id override to be a no-op (e.g. replacing DF1 with DF1).

- **Fast-path patch**: after loading cached state from the last AGENT_PLAN block, scan that block's `_dataframes` for any DF IDs newer than `latest_dataframe` and update accordingly
- **Save-time update**: after embedding DFs into the current block, update `_conv_state.latest_dataframe` and `known_dataframes` before saving, so the cached state is accurate for the next request
- Applied to: `cortex/app.py`

### Fixed — Empty Response When LLM Only Emits a PLOT Tag

"plot this by lab" showed brief running info then disappeared with an empty response. The LLM output consisted solely of a `[[PLOT:...]]` tag — after stripping it, `clean_markdown` was empty, producing a blank AGENT_PLAN block.

- When `clean_markdown` is empty but `plot_specs` exist, a brief descriptive placeholder is inserted (e.g. "Here is the bar for **DF2**:")
- Placeholder uses the `"df"` key (string like "DF2") instead of `"df_id"` (integer) for correct formatting
- The AGENT_PLOT block still renders the interactive chart below
- Applied to: `cortex/app.py`

### Fixed — "plot this" on Dogme Skills Triggered Job Submission

"plot this" after parsing a CSV on a Dogme skill (`run_dogme_cdna`) caused the LLM to emit `list_job_files` + `[[APPROVAL_NEEDED]]` instead of a `[[PLOT:...]]` tag. The user saw a spurious approval gate and a failed Nextflow job.

- **Plot-command override** (5a-c): when `_user_wants_plot` and valid `plot_specs` exist, suppress all `DATA_CALL`, legacy, and `APPROVAL_NEEDED` tags from the LLM response
- **Implicit DF injection in `_inject_job_context`**: "plot this" on Dogme skills now detects viz keywords even without an explicit `DF\d+` reference, looks up the latest DF from history blocks, and injects a `[NOTE: DF<N> is an in-memory DataFrame...]` hint so the LLM generates `[[PLOT:...]]` instead of tool calls
- Applied to: `cortex/app.py`

### Fixed — Multi-File Download Missing Files

"Download ENCFF546HTC and ENCFF212RUB from ENCSR160HKZ" only fetched one file — the LLM emitted a single `get_file_metadata` tag instead of two.

- After the tool call loop, detect all `ENCFF` accessions in the user message and compare against fetched files
- Auto-fetch any missing file metadata via a direct MCP call to the ENCODE server
- Rebuild the Download Plan to include all files
- Applied to: `cortex/app.py`

### Fixed — Raw File Metadata JSON Displayed in Downloads

"Download ENCFF921XAH" showed the full `get_file_metadata` JSON (lab, replicate, cloud_metadata, etc.) instead of the Download Plan summary.

- Split the browsing/download display path: browsing shows formatted data directly, downloads keep the Download Plan
- Raw file metadata is collapsed into a `<details><summary>📋 File Metadata Details</summary>` section
- Applied to: `cortex/app.py`

---

## [2.8] - 2026-02-22

### Changed — Server Rename

Full codebase rename from generic `server1/2/3/4` to meaningful names:

| Old | New | Role |
|-----|-----|------|
| `server1/` | `cortex/` | Agent Engine — LLM orchestration, routing, approval gates |
| `server2/` | `atlas/` | Data Portal — ENCODE consortium access (+ future sources) |
| `server3/` | `launchpad/` | Pipeline Execution — Dogme/Nextflow job submission & monitoring |
| `server4/` | `analyzer/` | Results Analysis — file discovery, parsing, QC reporting |

- Directories renamed: `cortex/`, `atlas/`, `launchpad/`, `analyzer/`
- All Python imports, registry keys (`SERVICE_REGISTRY`), `DATA_CALL` service tags, `MCPHttpClient` names, function names (`_validate_analyzer_params`, `_call_analyzer_tool`, `_call_launchpad_tool`), class names (`LaunchpadMCPTools`), and log prefixes updated
- Shell scripts: `agoutic_servers.sh` port vars (`CORTEX_PORT`, `LAUNCHPAD_PORT`, `ANALYZER_PORT`), module paths (`cortex.app:app`, `launchpad.mcp_server`, etc.)
- Data directories: `data/launchpad_work/`, `data/launchpad_logs/`
- Archive files: `launchpad_*.md`, `ANALYZER_IMPLEMENTATION.md`
- FastMCP instance names: `AGOUTIC-Launchpad-MCP`, `AGOUTIC-Analyzer`
- `pytest.ini`: `testpaths = launchpad`
- 35 markdown docs, 3,374 Python files verified clean — zero `server[1-4]` references remain

### Added — Architecture Hardening: Tool Contracts, Structured State, and Observability

Five cross-cutting improvements to reduce LLM hallucinations, enforce deterministic error behaviour, and add an audit trail.

- **Step 1 — Auto-Generated Tool Schema Contract**
  - New `atlas/tool_schemas.py`: Machine-readable JSON Schema for all 16 ENCODE MCP tools (types, required fields, `pattern` for ENCSR/ENCFF, `enum` values, warnings).
  - New `cortex/tool_contracts.py`: Central module with `fetch_all_tool_schemas()` (async, pulls `/tools/schema` from every MCP server at startup → cached), `format_tool_contract()` (compact prompt block: `TOOL: name / REQUIRED / OPTIONAL / ⚠️ warnings`), and `validate_against_schema()` (pre-call: strips unknown params, checks required fields, normalises enum case).
  - New `/tools/schema` GET endpoint on Atlas (`mcp_server.py` + `launch_encode.py`), Launchpad (`mcp_server.py`), and Analyzer (`mcp_server.py`).
  - Cortex `agent_engine.py`: `construct_system_prompt()` now appends a "📋 TOOL PARAMETER CONTRACTS" section with the compact tool reference for each active data source.
  - Cortex `app.py`: At startup, `fetch_all_tool_schemas()` is awaited. Before each MCP dispatch, `validate_against_schema()` strips unknown params and logs violations.
  - Applied to: [cortex/tool_contracts.py](cortex/tool_contracts.py) *(new)*, [atlas/tool_schemas.py](atlas/tool_schemas.py) *(new)*, [cortex/agent_engine.py](cortex/agent_engine.py), [cortex/app.py](cortex/app.py), [atlas/mcp_server.py](atlas/mcp_server.py), [atlas/launch_encode.py](atlas/launch_encode.py), [launchpad/mcp_server.py](launchpad/mcp_server.py), [analyzer/mcp_server.py](analyzer/mcp_server.py)

- **Step 2 — Structured Conversation State**
  - New `ConversationState` dataclass in `cortex/schemas.py` with typed fields: `active_skill`, `active_project`, `work_dir`, `sample_name`, `sample_type`, `reference_genome`, `active_experiment`, `active_file`, `known_dataframes`, `collected_params`, `workflows`, `active_workflow_index`.
  - `to_dict()` strips `None`/empty values; `to_json()` produces compact JSON; `from_dict()` tolerantly reconstructs.
  - New `_build_conversation_state()` in `app.py`: fast path reads cached state from last `AGENT_PLAN` block; slow path scans `EXECUTION_JOB` blocks for workflows, extracts ENCSR/ENCFF from history, and collects local sample intake params.
  - State injected as `[STATE]\n{json}\n[/STATE]` into every user message before the LLM sees it.
  - State persisted in each `AGENT_PLAN` block payload under `"state"` key for fast-path reload on next turn.
  - Applied to: [cortex/schemas.py](cortex/schemas.py), [cortex/app.py](cortex/app.py)

- **Step 3 — Error-Handling Playbook**
  - Seven deterministic "🛡️ TOOL FAILURE RULES" injected into the system prompt (before the `INSTRUCTIONS` block) in `agent_engine.py`: error reporting, accession confirmation, path safety, empty results, connection failures, permissions, ambiguity.
  - Structured `[TOOL_ERROR: tool=..., error_type=..., message="..."]` blocks appended to formatted data on failure, with error classification (`not_found`, `connection_failed`, `permission_denied`, `routing_error`, `unknown`).
  - Single-retry with 2 s back-off for transient `ConnectionError` / `TimeoutError` / `OSError` before the MCP dispatch try/except.
  - Applied to: [cortex/agent_engine.py](cortex/agent_engine.py), [cortex/app.py](cortex/app.py)

- **Step 4 — Output Contract Validator**
  - New `_validate_llm_output(raw_response, known_tools, active_skill, has_running_job)` function in `app.py`. Checks for: malformed `DATA_CALL` (no `tool=`), duplicate `APPROVAL_NEEDED`, `SKILL_SWITCH` during a running job, unknown tool names (hardcoded set of ~30 known tools), and mixed sources in tags.
  - Auto-corrects recoverable violations (strips malformed tags, deduplicates approval) and logs all as `WARNING`.
  - Wired after both `engine.think()` calls (initial + post-skill-switch re-run). Violations saved to debug payload under `_output_violations`.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Step 5 — Provenance Tags on Tool Results**
  - After the MCP execution loop, builds a `_provenance: list[dict]` with `source`, `tool`, `params`, `timestamp`, `success`, `rows`, `error` for each tool call.
  - Prepends `[TOOL_RESULT: source=..., tool=..., params={...}, rows=..., timestamp=...]` headers to `formatted_data` before the second LLM pass, so the LLM can cite its sources.
  - Provenance saved in the `AGENT_PLAN` block payload under `"_provenance"` key for auditing.
  - Applied to: [cortex/app.py](cortex/app.py)

---

## [2.7] - 2026-02-20

### Added — ENCODE file download infrastructure

- **"download ENCFF..." resolves metadata and starts download automatically**
  - `_auto_generate_data_calls()` detects download intent (keywords: download, grab, fetch, save) + ENCFF accession and generates a `get_file_metadata` call with `_chain: "download"`. After metadata resolves, the chain handler extracts the download URL (prefers `cloud_metadata.url` / S3 link, falls back to ENCODE `href`), sets `active_skill = "download_files"` and `needs_approval = True`.
  - Parent experiment resolution: checks the current user message for ENCSR accessions first (e.g. "download ENCFF921XAH from experiment ENCSR160HKZ"), then falls back to `_find_experiment_for_file()` which scans conversation history.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Download chain fires for both auto-generated and LLM-generated tags**
  - The chain handler now triggers on `_chain == "download"` (auto-generated calls) OR `active_skill == "download_files"` (LLM-generated tags). Previously, when the LLM correctly generated `get_file_metadata`, the chain didn't fire because LLM tags don't carry `_chain`, causing the approval gate to fall through to Dogme job params.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Auto-generated calls now go through tool correction pipeline**
  - Auto-generated calls from `_auto_generate_data_calls()` previously bypassed `_correct_tool_routing()` and `_validate_encode_params()`, causing ENCFF accessions to be passed as experiment accessions to the wrong tool. Now the same correction pipeline is applied: `_correct_tool_routing` + `_validate_encode_params` for ENCODE, `_validate_analyzer_params` for analyzer.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Download-specific approval gate (not Dogme job params)**
  - When `gate_action == "download"`, the approval gate shows the file list with sizes and target directory instead of calling `extract_job_parameters_from_conversation` (which always extracted Dogme DNA/RNA params). UI renders a dedicated download approval form with Approve/Cancel buttons.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **Per-file byte-level download progress**
  - `_download_files_background()` now tracks `expected_total_bytes`, `current_file_bytes`, `current_file_expected` from `size_bytes` metadata or `Content-Length` header. Progress updates are throttled to every 0.5s to avoid DB spam. UI shows a Streamlit progress bar with MB downloaded/expected for the current file, with fallback to overall byte progress or file-count progress.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **`_auto_detect_skill_switch` detects download intent**
  - "download" + ENCFF or URL now triggers a skill switch to `download_files` regardless of current skill.
  - Applied to: [cortex/app.py](cortex/app.py)

- **`download_after_approval()` prefers gate file list**
  - Refactored to check `gate_params.get("files")` first (populated by the download chain), then falls back to URL scanning. Also uses `target_dir` from the gate when available.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — "list files in data" returns parent directory instead of contents

- **`_validate_analyzer_params` no longer overwrites auto-resolved subdirectory paths**
  - After downloading a file to `data/`, "list files in data" showed the project root (1 entry: `data/`) instead of the data directory contents. The auto-generated call correctly resolved `work_dir=/project/data`, but `_validate_analyzer_params` overwrote it with the project-level `work_dir` from context. Now preserves the incoming `work_dir` when it's already a valid subdirectory of the resolved context dir.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — ENCODE accession hallucinations

- **LLM replaces correct ENCSR with a hallucinated one in its text response**
  - When calling a tool like `get_files_by_type(accession=ENCSR160HKZ)`, the LLM would correctly fetch the right data but then write "There are 12 BAM files for ENCSR000CQZ" in its summary. Added a post-processing pass on `clean_markdown`: if the user message contains exactly one ENCSR accession, any different ENCSR appearing in the LLM's text is replaced with the correct one.
  - Applied to: [cortex/app.py](cortex/app.py)

- **LLM calls wrong tool/accession for ENCFF file metadata ("metadata for ENCFF921XAH" → fetched ENCSR503QZJ)**
  - The LLM called `get_files_by_type(accession=ENCSR503QZJ)` (completely hallucinated experiment) instead of `get_file_metadata(accession=ENCSR160HKZ, file_accession=ENCFF921XAH)`. The ENCFF-detection logic in `_correct_tool_routing` only applied to `get_experiment`, not to other ENCODE tools. Added a general ENCFF catch-all at the end of `_correct_tool_routing`: if ENCFF appears in the user message and the tool is anything other than `get_file_metadata`, redirect to `get_file_metadata` with the parent experiment found from conversation history.
  - Applied to: [cortex/app.py](cortex/app.py)

- **`_validate_encode_params` Fix 3: replace hallucinated ENCSR in tool params**
  - If the user explicitly stated an ENCSR accession in their message and the LLM's tag used a different ENCSR, the parameter is corrected before the API call.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — "list workflows" shows only workflow directories

- **`data/` and other non-workflow dirs excluded from "list workflows"**
  - "list workflows" listed everything in the project directory, including `data/`. Added `name_pattern` parameter to `discover_files()`, `list_job_files()`, and all MCP registrations. Cortex now passes `name_pattern="workflow*"` for "list workflows" calls so only `workflow1/`, `workflow2/`, etc. are returned.
  - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/mcp_tools.py](analyzer/mcp_tools.py), [analyzer/mcp_server.py](analyzer/mcp_server.py), [cortex/app.py](cortex/app.py)

### Fixed — Approval gate triggered for file browsing commands

- **Browsing commands no longer trigger APPROVAL_NEEDED gate**
  - When on a Dogme skill (e.g. `run_dogme_cdna`), the LLM emitted `[[APPROVAL_NEEDED]]` alongside a browsing response, causing the approval confirmation UI to appear. The browsing override now also sets `needs_approval = False` and `plot_specs = []`.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — File Browsing Routing and Skill Independence

- **Browsing commands now skill-independent (not gated on Dogme skills)**
  - "list workflows", "list files", "list files in X" now work from ANY skill (ENCODE_Search, welcome, etc.) — not just Dogme analysis skills. Moved browsing logic to the top of `_auto_generate_data_calls()` so it runs first, before any ENCODE or other skill-specific catch-all can claim the message.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Project directory as minimum context for file browsing**
  - Browsing commands previously required `work_dir` from EXECUTION_JOB history (only available after a Dogme job ran). Now the project directory (resolved from `_resolve_project_dir()`) is always available as a fallback. Added `project_dir` parameter to `_auto_generate_data_calls()` and `_validate_analyzer_params()`.
  - Applied to: [cortex/app.py](cortex/app.py)

- **"list workflows" from ENCODE skill routed to ENCODE instead of analyzer**
  - The ENCODE catch-all extracted "workflows" as a search term and generated `search_by_biosample`. Added a browsing-command override in the main chat handler that detects browsing patterns and forces the correct analyzer auto-generated calls, suppressing any LLM-generated ENCODE tags.
  - Applied to: [cortex/app.py](cortex/app.py)

- **"list files in workflow1/annot" showed workflow2/annot instead**
  - The subfolder rescue prioritized the LLM's invented param (`subfolder=annot`, which lost the `workflow1/` prefix) over the user message. Swapped priority: user message parsing now comes first (preserving the full `workflow1/annot` path), with the LLM hint as fallback only.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Previous ENCODE DataFrame shown alongside file listings**
  - When browsing from an ENCODE skill, `_inject_job_context()` injected the previous K562 dataframe. The browsing override cleared LLM tags but not `_injected_dfs`. Now also clears `_injected_dfs = {}` and `_injected_previous_data = False`.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Smarter project-dir derivation for "list workflows"**
  - Previously blindly stripped the last path component to get the project dir, which would over-strip if `work_dir` was already the project dir. Now only strips `/workflowN/` suffix; otherwise treats `work_dir` as already project-level. Applied to both `_auto_generate_data_calls()` and `_validate_analyzer_params()`.
  - Applied to: [cortex/app.py](cortex/app.py)

### Added — File Browsing in System Prompt

- **File browsing instructions in system prompt (all skills)**
  - Added a "FILE BROWSING" section to the system prompt that's always visible regardless of active skill. Tells the LLM it can use `list_job_files` via DATA_CALL tags for browsing at any time, and that the system automatically resolves the correct project/workflow directory.
  - Applied to: [cortex/agent_engine.py](cortex/agent_engine.py)

### Fixed — Analyzer Placeholder `work_dir` and Local Sample Intake Bugs

- **Always force `work_dir` from conversation context — never trust the LLM**
  - Previously `_validate_analyzer_params()` only caught placeholder patterns (`/work_dir`, `{work_dir}`, `<work_dir>` etc.). The LLM also invents wrong paths like `/media/.../project`. Now the function always resolves `work_dir` from EXECUTION_JOB block history and overrides whatever the LLM supplied. Special case: "list workflows" trims to the project parent dir.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Include `EXECUTION_JOB` blocks in history query**
  - The DB query that builds `history_blocks` only fetched `USER_MESSAGE` and `AGENT_PLAN` blocks. `_extract_job_context_from_history()` looks for `EXECUTION_JOB` blocks to find the authoritative `work_dir`, but never found any — falling back to fragile text parsing. Added `EXECUTION_JOB` to the query filter.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Pass `history_blocks` to `_auto_generate_data_calls()`**
  - The safety-net auto-generation function called `_extract_job_context_from_history()` without `history_blocks`, so it could never find EXECUTION_JOB blocks even when they existed. Now `history_blocks` is threaded through from the call site.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Analyzer tool aliases for hallucinated tool names**
  - The LLM frequently generates `list_workflows`, `find_files`, `parse_csv`, `read_file` etc. instead of the real MCP tool names. Added alias mapping: `list_workflows`→`list_job_files`, `find_files`→`find_file`, `parse_csv`→`parse_csv_file`, `read_file`→`read_file_content`, `analysis_summary`→`get_analysis_summary`, etc.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Strip unknown Analyzer params (e.g. `sample=Jamshid`)**
  - The LLM adds invented params like `sample=Jamshid` that Analyzer tools don't accept, causing Pydantic validation errors. `_validate_analyzer_params()` now strips any param not in a per-tool allowlist before the MCP call.
  - Applied to: [cortex/app.py](cortex/app.py)

- **"list workflows" returns 502 files → now shows top-level dirs only**
  - When using `list_job_files` on the project dir, `discover_files()` recursively listed all files. Added `max_depth` parameter: `max_depth=1` lists only immediate children (workflow folders). Both the auto-generated safety-net call and `_validate_analyzer_params()` now set `max_depth=1` for "list workflows" commands.
  - Applied to: [cortex/app.py](cortex/app.py), [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/mcp_tools.py](analyzer/mcp_tools.py), [analyzer/mcp_server.py](analyzer/mcp_server.py)

- **File listing results showed "..." for all fields**
  - Analyzer `list_job_files` results are dicts with a `files` array. The result formatter's `_compact_dict()` truncated nested file objects to `"..."` at depth 2. Added a dedicated file-listing table formatter in `_format_data()` that renders files as a proper markdown table with Name, Path, and human-readable Size columns.
  - Applied to: [atlas/result_formatter.py](atlas/result_formatter.py)

- **`workflow1/` filtered out by `discover_files()` skip logic**
  - The `work/` and `dor*/` skip filter used `startswith("work")` which matched `workflow1`, `workflow2`, etc. Changed to exact `child.name == "work"` for directories and `parts[0] == "work"` for the recursive path, so only the Nextflow `work/` directory is skipped.
  - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py)

- **"list files in annot" ignored the subfolder**
  - When the LLM generated `subfolder=annot` (an invented param), it was stripped as unknown, and the subpath was lost. Now `_validate_analyzer_params()` rescues subfolder hints from invented LLM params AND parses the user message for "list files in <subpath>" — then appends the subpath to `work_dir` via `_resolve_workflow_path()`.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Sample name contaminated by file listing table headers**
  - The `sample_name` regex matched bare `Name` in markdown table headers (`| Name | Path | Size |`), capturing "| Path | Size |" as the sample name. Tightened to require `Sample Name` (not bare `Name`) and added `|` to the exclusion character class.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Skip second-pass LLM for file browsing commands**
  - "list files" and "list workflows" now show the formatted file table directly instead of passing it through a second LLM call that would produce an unwanted summary/interpretation.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed `work_dir=/work_dir` and `work_dir={work_dir_from_context}` placeholders in Analyzer DATA_CALL tags** *(superseded by always-force fix above)*
  - When the LLM copies template variables from skill docs, it emits literal strings like `/work_dir`, `<work_dir>`, or `{work_dir_from_context}` instead of the real workflow path, causing "Work directory not found" errors.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed CDNA misidentified as DNA in Local Sample Intake**
  - The `sample_type` keyword heuristic was gated by `if "sample_type" not in _collected`, so if a previous assistant message had echoed "Data Type: DNA" (wrong), the regex would extract "DNA" and the heuristic would never run. Now the user-message keyword heuristic ALWAYS runs and overrides regex-extracted values, since user messages are the source of truth.
  - Applied to: [cortex/app.py](cortex/app.py)

### Added — Workflow Browsing and Relative Path Support

- **"list workflows" command**
  - Detects "list workflows" / "show workflows" in user messages and auto-generates a `list_job_files` call on the project base directory (parent of the current workflow). Allows users to see all workflow folders (workflow1, workflow2, etc.) in their project.
  - Applied to: [cortex/app.py](cortex/app.py)

- **"list files" command with subfolder support**
  - Detects "list files" / "list files in workflow2/annot" patterns. Resolves the path using `_resolve_workflow_path()` which handles: empty subpath → current workflow, "workflow2" → that workflow's dir, "workflow2/annot" → that workflow's annot/ subdir, "annot" → current workflow's annot/ subdir.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Relative path file parsing**
  - "parse annot/File.csv" and "parse workflow2/annot/File.csv" now work. Added `_resolve_file_path()` which splits user paths, checks if the first component is a known workflow folder name, resolves to the correct `work_dir`, and extracts the basename for `find_file` searches.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Removed template variables from skill docs**
  - Replaced all `{work_dir}`, `{work_dir_from_context}`, and `{tool}` template variables in `DOGME_QUICK_WORKFLOW_GUIDE.md` with concrete example paths and explicit "NEVER use template variables" warnings. Added "Browsing Commands" reference section.
  - Applied to: [skills/DOGME_QUICK_WORKFLOW_GUIDE.md](skills/DOGME_QUICK_WORKFLOW_GUIDE.md)

- **Fixed sample_name not extracted from current message**
  - The "called <name>" heuristic only scanned `conversation_history`, not the current message. On the first turn (empty history), `sample_name` was never collected even when the user said "called Jamshid", leaving the LLM to extract it (unreliably).
  - Applied to: [cortex/app.py](cortex/app.py)

- **Stronger context injection when all 4 fields are collected**
  - When all 4 required fields (sample_name, path, sample_type, reference_genome) are extracted, the `[CONTEXT]` line now says "Use these EXACT values" with each field on its own line and specifies the pipeline name ("Dogme CDNA"), rather than a generic "Do NOT re-ask" message that the LLM would ignore.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — ENCODE Search Context Injection and Hallucination Bugs

- **Replaced hardcoded `biosample_keywords` with heuristic detection**
  - Previously, `cortex/app.py` used a hardcoded set of 10 biosamples (K562, HeLa, etc.) to determine if a query was a "new search" or a "follow-up". Unknown cell lines (like HL-60) were incorrectly treated as follow-ups, causing the system to inject stale data from previous queries (e.g., MEL) and hallucinate answers based on the wrong dataset.
  - Replaced the hardcoded list with a three-layer heuristic:
    1. **New-query patterns**: Regex matches for explicit searches (e.g., "how many X experiments", "search encode for X").
    2. **Follow-up signals**: Referential language (e.g., "of them", "those", "DF3") that overrides new-query patterns.
    3. **Extracted-term vs. previous DFs**: Extracts the search term and checks if it appears in previous dataframe labels. If it's a new subject, it's treated as a new query.
  - Added pronouns ("them", "those", "these", etc.) to `_ENCODE_STOP_WORDS` so the fallback tokenizer doesn't treat them as search terms.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed missing `visible: True` metadata on fresh ENCODE search dataframes**
  - When a fresh `search_by_biosample` API call returned results, the resulting dataframe was stored without the `visible: True` metadata flag.
  - The auto-selector for follow-up queries (which looks for the most recent visible dataframe) ignored these fresh dataframes and fell back to older, explicitly visible dataframes (like injected filtered results).
  - Added `"visible": True` to the metadata of all `_SEARCH_TOOLS` dataframes so they can be correctly targeted by follow-up queries.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Replaced exhaustive `biosamples` dict with catch-all extractor**
  - The safety net (`_auto_generate_data_calls`) previously relied on a hardcoded dictionary of known biosamples to generate API calls. If a term wasn't in the dict, the LLM would hallucinate a response without making an API call.
  - Replaced the exhaustive dict with a slim `_KNOWN_ORGANISMS` dict (used only for organism hints) and a new `_extract_encode_search_term()` regex-based catch-all extractor that handles ANY biosample name.
  - Added a hallucination guard: if no tool was executed but the LLM claims "there are N experiments" or "interactive table below", the response is stripped and a disambiguation message is shown.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed organism auto-addition and missing `search_term`**
  - The system was incorrectly auto-adding `organism=Homo sapiens` to mouse cell lines (like MEL) and placing the cell line name in `assay_title` instead of `search_term`.
  - Created `_validate_encode_params()` to fix missing `search_term` by moving non-assay values from `assay_title`, and to strip `organism` unless the user explicitly mentioned a species.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Added tool aliases and deduplication**
  - Added `"search": "search_by_biosample"` to `tool_aliases` and `Search (...)` to `fallback_patterns` to fix "Unknown tool: search" errors.
  - Added a deduplication step after collecting all tool calls to prevent duplicate dataframes from appearing in the UI.
  - Applied to: [cortex/app.py](cortex/app.py), [atlas/config.py](atlas/config.py)

- **Updated ENCODE and Local Sample Intake skills**
  - Updated `ENCODE_Search.md` to instruct the LLM not to limit itself to known terms and to never invent a count.
  - Updated `Local_Sample_Intake.md` with anti-step directives ("DO NOT describe your reasoning steps"), fixed field name mismatches (`data_type`→`sample_type`, `data_path`→`path`), and added mouse/human genome inference.
  - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md), [skills/Local_Sample_Intake.md](skills/Local_Sample_Intake.md)

## [Unreleased] - 2026-02-19

### Changed — Workflow Directory (`work_dir`) Replaces `run_uuid` as Primary Identifier

- **All Analyzer analysis tools now accept `work_dir` as the primary parameter**
  - `work_dir` (absolute path to the workflow folder, e.g. `.../users/alice/project/workflow1/`) is now the preferred way to identify which job's files to access. `run_uuid` is retained as a legacy fallback for backward compatibility.
  - New `resolve_work_dir(work_dir, run_uuid)` function in `analysis_engine.py` acts as a central resolver: tries `work_dir` first (direct path), falls back to UUID-based DB lookup.
  - All 7 MCP tool functions (`list_job_files`, `find_file`, `read_file_content`, `parse_csv_file`, `parse_bed_file`, `get_analysis_summary`, `categorize_job_files`) updated with `work_dir` as first parameter.
  - All 7 MCP protocol wrappers in `mcp_server.py` updated.
  - Response dicts now return `work_dir` instead of `run_uuid`.
  - TOOL_SCHEMAS rewritten with `work_dir` documented as preferred.
  - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/mcp_tools.py](analyzer/mcp_tools.py), [analyzer/mcp_server.py](analyzer/mcp_server.py), [analyzer/schemas.py](analyzer/schemas.py)

- **Context injection now provides workflow directories instead of UUIDs**
  - `_extract_job_context_from_history()` — rewritten to scan history blocks for EXECUTION_JOB entries and build a `workflows` list with `{work_dir, sample_name, mode, run_uuid}` for each job. Previously only extracted the most recent UUID.
  - `_inject_job_context()` Dogme section — injects ALL workflow dirs with sample names. Single workflow: `[CONTEXT: work_dir=..., sample=...]`. Multiple workflows: enumerates each with folder name, sample, and mode.
  - Applied to: [cortex/app.py](cortex/app.py)

- **`_auto_generate_data_calls()` uses `work_dir` for file operations**
  - DATA_CALL find_file calls now use `work_dir=` instead of `run_uuid=`.
  - Smart matching: when multiple workflows exist, matches filename prefix to sample_name to pick the correct workflow directory.
  - Chained tool calls extract `work_dir` from find_file response.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Auto-analysis no longer shows UUIDs to users**
  - `_auto_trigger_analysis()` LLM prompt uses "Work directory:" instead of "Run UUID:".
  - Analysis header shows "**Workflow:** workflowN" instead of UUID.
  - `_build_static_analysis_summary()` fallback template shows workflow folder name.
  - Applied to: [cortex/app.py](cortex/app.py)

- **All skill docs updated to use `work_dir` instead of `run_uuid`**
  - `DOGME_QUICK_WORKFLOW_GUIDE.md` — UUID validation section replaced with simpler work_dir guidance; all DATA_CALL examples use `work_dir=`.
  - `Dogme_cDNA.md`, `Dogme_DNA.md`, `Dogme_RNA.md` — context injection references, find_file examples, and DATA_CALL templates updated.
  - `Analyze_Job_Results.md` — inputs, DATA_CALLs, API endpoint docs, QC report template, and information gathering section updated.
  - `Local_Sample_Intake.md` — routing condition updated.
  - Applied to: [skills/](skills/)

### Added — LLM-Powered Auto-Analysis After Job Completion

- **`_auto_trigger_analysis()` now passes results through the LLM**
  - Previously, when a Dogme job completed, the auto-analysis step built a static markdown template listing file counts and filenames — no LLM was involved (`model: "system"`). Now the function fetches structured data from Analyzer **and** parses key CSV files (`final_stats`, `qc_summary`) via `parse_csv_file` MCP calls, then passes everything to the LLM for an intelligent first interpretation.
  - The LLM receives a structured context block (file inventory + parsed CSV tables) and is asked to highlight key metrics, QC concerns, and suggest next steps. Tags (`[[DATA_CALL:...]]`, `[[SKILL_SWITCH_TO:...]]`, `[[APPROVAL_NEEDED]]`) are stripped from the LLM response as a safety measure.
  - Token usage is tracked: the AGENT_PLAN block now includes a `tokens` payload with `prompt_tokens`, `completion_tokens`, `total_tokens`, and `model` — consistent with regular chat turns.
  - Both the system USER_MESSAGE and the assistant AGENT_PLAN are saved to conversation history (`save_conversation_message`), so follow-up turns see the auto-analysis context.
  - **Graceful fallback**: if the LLM call fails (Ollama down, timeout, etc.) the original static template is used and `model` is set to `"system"` with zero token counts.
  - Applied to: [cortex/app.py](cortex/app.py)

- **`model` field added to `EXECUTION_JOB` block payload**
  - The EXECUTION_JOB block now carries the `model` key from the approval gate, so `_auto_trigger_analysis` can instantiate `AgentEngine` with the same model the user selected for the conversation.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Helper functions extracted for clarity**
  - `_build_auto_analysis_context()` — builds a structured text context (file inventory + markdown tables from parsed CSVs) for the LLM prompt.
  - `_build_static_analysis_summary()` — the original static template, now a standalone function used as the fallback.
  - Applied to: [cortex/app.py](cortex/app.py)

### Added — `search_by_assay` MCP Tool (Atlas extension)

- **`atlas/mcp_server.py` — extends ENCODELIB without modifying it**
  - Added `search_by_assay(assay_title, organism=None, target=None, exclude_revoked=True)` as a new `@server.tool()` registered on ENCODELIB's existing FastMCP `server` instance.
  - When `organism` is omitted, both `Homo sapiens` and `Mus musculus` are queried and results are merged into a single response with `total`, `human_count`, `mouse_count`, `human`, and `mouse` keys.
  - When `organism` is specified, returns `{total, assay_title, organism, experiments}` for the single organism.
  - Imports `server` and `get_encode_instance` from ENCODELIB's `encode_server` module; no changes to ENCODELIB itself.
  - Applied to: [atlas/mcp_server.py](atlas/mcp_server.py) *(new file)*

- **`atlas/launch_encode.py` updated to load extended server**
  - Previously imported directly from ENCODELIB's `encode_server`. Now imports `server` from `atlas.mcp_server` (after ENCODELIB is added to `sys.path`), so all ENCODELIB tools plus `search_by_assay` are registered before the HTTP server starts.
  - Applied to: [atlas/launch_encode.py](atlas/launch_encode.py)

### Fixed — ENCODE Agent Tool Misrouting

- **Structural guard: non-ENCSR accessions redirect to correct search tool**
  - `_correct_tool_routing()` in the chat endpoint now applies a structural check rather than a name-list check: any `accession` parameter passed to `get_experiment` that does not match `^ENCSR[A-Z0-9]{6}$` is redirected to the appropriate search tool. This handles unknown cell lines and biosample terms regardless of naming convention.
  - Added `_ENCSR_PATTERN` and `_ENCFF_PATTERN` compiled regexes; `_ENCFF_PATTERN` similarly redirects `get_experiment` calls with ENCFF accessions to `get_file_metadata`.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Assay-only queries routed to `search_by_assay`**
  - Queries such as *"how many RNA-seq experiments are in ENCODE"* previously had no routing path and sometimes triggered wrong tool calls. `_correct_tool_routing()` now has a **Case A** branch: when the apparent search term looks like an assay name (detected via `_looks_like_assay()`) with no biosample context, the tool is redirected to `search_by_assay`.
  - `_auto_generate_data_calls()` safety net likewise emits `search_by_assay` (not `search_by_organism`) for assay-only fallback.
  - Added helpers: `_looks_like_assay(s)`, `_ASSAY_INDICATORS` tuple, `_ENCODE_ASSAY_ALIASES` map (`rna-seq` → `total RNA-seq`, etc.).
  - Applied to: [cortex/app.py](cortex/app.py)

- **`ENCODE_Search.md` skill updated with anti-pattern guard and new tool docs**
  - Added `⛔ NEVER` block listing 4 wrong tool-call patterns and 3 correct alternatives.
  - Decision rule updated: *cell line → `search_by_biosample`; assay only → `search_by_assay`; ENCSR accession → `get_experiment`*.
  - Quick-reference table gains 3 `search_by_assay` examples.
  - `search_by_assay` parameter docs added to Available Tools section.
  - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md)

### Added — Username & Slug System

- **`username` column on `User` model**
  - New nullable `TEXT` column with a conditional unique index (`WHERE username IS NOT NULL`). Distinct from `google_id` and `email` — a short, readable handle used for filesystem paths and display.
  - Applied to: [cortex/models.py](cortex/models.py)

- **`cortex/migrate_usernames.py` — idempotent migration**
  - Adds `users.username` via `ALTER TABLE` (no UNIQUE constraint on the ALTER, avoiding SQLite limitations) then creates a `CREATE UNIQUE INDEX IF NOT EXISTS ... WHERE username IS NOT NULL` separately.
  - Applied to: [cortex/migrate_usernames.py](cortex/migrate_usernames.py) *(new file)*

- **`cortex/set_usernames.py` — CLI for username assignment**
  - `auto` sub-command derives slugified usernames from email prefixes for all users who have none.
  - `set <user_id> <username>` assigns a specific username.
  - `slugify-projects` normalises existing project names to URL-safe slugs.
  - Applied to: [cortex/set_usernames.py](cortex/set_usernames.py) *(new file)*

- **Filesystem paths use `username/project-slug/` hierarchy**
  - User-jailed paths now resolve to `$AGOUTIC_DATA/users/{username}/{project_slug}/` (falling back to `user_{id}` if username is not yet set).
  - Workflow directories use `{username}/{project_slug}/workflow{N}/`.
  - Applied to: [cortex/user_jail.py](cortex/user_jail.py), [launchpad/nextflow_executor.py](launchpad/nextflow_executor.py), [analyzer/config.py](analyzer/config.py)

- **Admin endpoints for username management**
  - `PATCH /admin/users/{user_id}/username` — set or clear a user's username.
  - `GET /admin/users` — list now includes `username` field.
  - Applied to: [cortex/admin.py](cortex/admin.py)

- **UI — slugified project name default**
  - New project default name changed from `"Project YYYY-MM-DD HH:MM"` to `"project-YYYY-MM-DD"` (slug-friendly).
  - New Project button replaced with an expander form; project name is pre-filled with the slug default and run through `_slugify_project_name()` before creation.
  - Applied to: [ui/appUI.py](ui/appUI.py)

### Fixed — Agent Forgets Parameters During `analyze_local_sample` Multi-Turn Conversations

- **Context injection for `analyze_local_sample` skill added to `_inject_job_context()`**
  - `_inject_job_context()` had handlers for Dogme skills (UUID/work_dir injection) and ENCODE skills (dataframe injection), but **nothing for `analyze_local_sample`**. When the system prompt grew with PLOT docs, `download_files` skill, and expanded `ENCODE_Search.md`, devstral-2 could no longer reliably track parameters across turns — e.g. replying "mm39" for reference genome caused the agent to forget `sample_name`, `data_path`, and `data_type` collected in earlier turns.
  - New `analyze_local_sample` branch extracts already-collected parameters (`sample_name`, `data_path`, `data_type`, `reference_genome`) from conversation history using regex patterns that handle both plain text and Markdown bold formatting (e.g. `**Sample Name:** Jamshid`).
  - Reference genome pattern is tightened to only match known genomes (`GRCh38|mm39|mm10|hg38|T2T-CHM13`) — prevents false matches on the word "Missing" from agent summaries.
  - Injects a `[CONTEXT: Parameters already collected from this conversation: ... Do NOT re-ask for these. Only ask for fields still missing.]` line at the top of the user message so the LLM sees all prior answers without needing to scan full history.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — Parameter Extraction Leaks Previous Sample Names Into New Submissions

- **`extract_job_parameters_from_conversation()` scoped to the most recent submission cycle**
  - Previously the function scanned **all** blocks in the project and concatenated every user message into one string. The regex `r'called\s+(\w+)'` did `re.search` which finds the **first** match — so "called Jamshid" from the original submission always won over "called Jamshid2" or "called retest" from later submissions in the same project.
  - Now the function finds the last `EXECUTION_JOB` or approved `APPROVAL_GATE` block as a **boundary marker** and only considers blocks after that point. This ensures each new submission starts fresh without contamination from prior jobs.
  - Explicit name extraction patterns (`called X`, `named X`, etc.) now iterate user messages in **reverse order** (most recent first), so the latest submission request always wins.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — New Sample Submission Blocked While on Dogme Analysis Skill

- **`_auto_detect_skill_switch()` now switches to `analyze_local_sample` from Dogme analysis skills**
  - Previously, the pre-LLM skill switch excluded `run_dogme_dna`, `run_dogme_rna`, and `run_dogme_cdna` from the `analyze_local_sample` detection to avoid false positives. This meant a user saying "Analyze the local mouse CDNA sample called Jamshid2 at /path" while on `run_dogme_cdna` would **not** switch skills — the LLM would interpret "Jamshid2" as a filename in the current job's results and call `find_file`, which fails.
  - The exclusion list now only contains `analyze_local_sample` itself. When the current skill is a Dogme analysis skill, a **stricter threshold** is applied: the message must have a local path **plus at least 2** of (analysis verb, sample keyword, data type keyword). From non-Dogme skills, the original threshold of path + 1 signal applies. This prevents false positives on messages like "parse /path/to/result.csv" while correctly catching "Analyze the local CDNA sample at /path".
  - Applied to: [cortex/app.py](cortex/app.py)

### Improved — Block Headers Show Project Name and Workflow Folder

- **Block metadata header shows project name instead of UUID**
  - `render_block()` in the UI now resolves the project name from the cached projects list for the `📌 Proj:` header. Falls back to a truncated UUID (`c7f75875…`) if the project isn't in the cache. Previously displayed the raw UUID (`c7f75875-b7f8-4835-8f94-28fd4f8090bf`).
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **EXECUTION_JOB block shows `workflowN` folder name alongside run UUID**
  - Cortex now stores the `work_directory` path (returned by Launchpad's `submit_dogme_job`) in the EXECUTION_JOB block payload. The UI extracts the folder name (e.g. `workflow2`) from the path and displays it as `📁 workflow2 | Run UUID: 3862fd86...`. Previously only the opaque run UUID was shown, with no visible connection to the actual directory on disk.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

### Fixed — `submit_dogme_job` MCP Wrapper Missing `username` / `project_slug`

- **MCP tool registration updated to pass through `username` and `project_slug`**
  - The `submit_dogme_job()` wrapper in `mcp_server.py` defines the parameters exposed via FastMCP. When `username` and `project_slug` were added to `mcp_tools.py`'s `LaunchpadMCPTools.submit_dogme_job()`, the MCP wrapper was never updated to match. This caused FastMCP to reject `username` and `project_slug` as "unexpected keyword arguments" when Cortex called the tool after the approval gate.
  - Added `username: str | None = None` and `project_slug: str | None = None` to both the wrapper function signature and the inner `await tools.submit_dogme_job(...)` call.
  - Applied to: [launchpad/mcp_server.py](launchpad/mcp_server.py)

### Fixed — `find_file` JSON Echo Loop (weak-model regression)

- **Fix A — Bare `find_file` JSON blocks stripped from conversation history**
  - When a weaker model (e.g. `devstral-2`) emits a `find_file` result JSON verbatim instead of a `[[DATA_CALL:...]]` tag, that raw JSON was previously saved as an `AGENT_PLAN` block and reloaded into conversation history on the next turn. The LLM would then see it as a "completed action" and echo it again — creating an infinite loop.
  - The history builder in the `chat_with_agent` endpoint now detects AGENT_PLAN blocks whose entire content (after stripping `<details>` and code fences) parses as `{success: true, primary_path: ...}` JSON. Such blocks are excluded from `conversation_history` via `continue` rather than being appended.
  - Detection is loose: strips `` ```json `` fences, normalises whitespace, attempts `json.loads()` — only skips on clean parse. Narrative responses that happen to mention `"primary_path"` are not affected.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Fix B — Recovery guard auto-chains `find_file` → `parse_csv_file` at response time**
  - When `all_results` is empty (no DATA_CALL tags fired) but `clean_markdown` looks like a bare `find_file` JSON echo, the endpoint now intercepts before the AGENT_PLAN block is saved and automatically executes the parse chain:
    1. Parses `primary_path` and `run_uuid` directly from the echoed JSON (no history scanning needed — the `find_file` response already carries `run_uuid`).
    2. Calls `_pick_file_tool(primary_path)` to select `parse_csv_file`, `parse_bed_file`, or `read_file_content`.
    3. Instantiates `MCPHttpClient(name="analyzer", ...)` and calls the parse tool with appropriate row/record limits.
    4. Injects the result into `all_results["analyzer"]` and clears `clean_markdown` — the existing `has_real_data → analyze_results` second-pass flow then runs normally with no code duplication.
  - If the parse call fails, the guard logs the error and falls through silently — the original (empty) response is preserved rather than crashing.
  - Applied to: [cortex/app.py](cortex/app.py)

### Added — Per-Message & Per-Conversation Token Tracking

- **`AgentEngine` now captures `response.usage` from every LLM call**
  - `think()` and `analyze_results()` both return `(content, usage_dict)` tuples instead of plain strings. A `_usage_to_dict()` helper normalises `prompt_tokens`, `completion_tokens`, `total_tokens` from the OpenAI usage object and returns zeros gracefully on errors or `None` usage objects.
  - Applied to: [cortex/agent_engine.py](cortex/agent_engine.py)

- **Token counts stored in two places per assistant turn**
  - Chat endpoint (`POST /chat`) unpacks both LLM passes (think + analyze_results), sums their usage into `_total_usage`, and:
    1. Embeds a `"tokens": {prompt_tokens, completion_tokens, total_tokens, model}` key in every `AGENT_PLAN` block's `payload_json` — so each block is self-describing with no join needed.
    2. Persists the same counts on the `ConversationMessage` row via updated `save_conversation_message()` signature (new optional `token_data` and `model_name` kwargs).
  - Applied to: [cortex/app.py](cortex/app.py)

- **Four new nullable columns on `ConversationMessage`**
  - `prompt_tokens INTEGER`, `completion_tokens INTEGER`, `total_tokens INTEGER`, `model_name TEXT` — all nullable so pre-migration rows are unaffected.
  - Applied to: [cortex/models.py](cortex/models.py)

- **Idempotent migration script**
  - `cortex/migrate_token_tracking.py` adds the four columns with `PRAGMA table_info` guards (safe to re-run). Respects the `$AGOUTIC_DATA` environment variable identically to `cortex/config.py`, so it works on Watson and local dev without modification. Accepts an optional path argument.
  - Applied to: [cortex/migrate_token_tracking.py](cortex/migrate_token_tracking.py)

- **`GET /user/token-usage` endpoint (authenticated, own data)**
  - Returns: `lifetime` totals, `by_conversation` list (project_id, title, token breakdown, last_message_at), `daily` time-series, and `tracking_since` (ISO date of earliest tracked message).
  - Applied to: [cortex/app.py](cortex/app.py)

- **`GET /admin/token-usage/summary` endpoint (admin-only)**
  - Returns per-user leaderboard sorted by total tokens consumed, plus a global daily time-series. Aggregates across all users via JOIN on `conversations → conversation_messages`.
  - Applied to: [cortex/admin.py](cortex/admin.py)

- **`GET /admin/token-usage` endpoint (admin-only, filterable)**
  - Optional `?user_id=` and `?project_id=` query params. Returns per-conversation breakdown and daily time-series for the matching scope.
  - Applied to: [cortex/admin.py](cortex/admin.py)

- **`GET /projects/{id}/stats` — `token_usage` field added**
  - Response now includes `token_usage: {prompt_tokens, completion_tokens, total_tokens}` aggregated from all conversations in the project.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Per-message token caption in chat UI**
  - Each `AGENT_PLAN` block renders a caption under the message content: `🪙 1,234 tokens  (↑ 890 prompt · ↓ 344 completion)  ·  llama3.2`. Reads from the `tokens` key in block payload. Pre-migration messages silently show nothing.
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Live token counter in sidebar**
  - `🪙 Tokens: 12,345` expander updates on every page rerun (API call moved outside the expander widget so it fetches unconditionally). Expander label shows the live total. Inside: Total + Completion metrics, daily line chart (when >1 day of data), and tracking-since date.
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Lifetime tokens metric in Projects dashboard**
  - Summary row alongside Disk Usage now shows "Lifetime Tokens Used". Per-project table gains a "🪙 Tokens" column populated from `/projects/{id}/stats`.
  - Applied to: [ui/pages/projects.py](ui/pages/projects.py)

- **"🪙 Token Usage" admin tab**
  - New fourth tab in the Admin page. Shows: global daily line chart, per-user leaderboard `st.dataframe`, user drill-down selector with per-conversation table and per-user daily chart.
  - Applied to: [ui/pages/admin.py](ui/pages/admin.py)

### Added — Admin Token Limit Controls

- **`token_limit` column on `User` model**
  - New nullable `INTEGER` column. `NULL` = unlimited (default). When set, enforces a hard cap on the user's lifetime token consumption.
  - Applied to: [cortex/models.py](cortex/models.py)

- **Migration extended for `users.token_limit`**
  - `cortex/migrate_token_tracking.py` gains a 5th idempotent migration step adding `users.token_limit`. Re-running the script skips already-present columns.
  - Applied to: [cortex/migrate_token_tracking.py](cortex/migrate_token_tracking.py)

- **`PATCH /admin/users/{user_id}/token-limit` endpoint (admin-only)**
  - Accepts `{"token_limit": <int or null>}`. Validates positive integer or null. Returns updated user record. Allows admins to set or clear a per-user cap without touching other user fields.
  - Applied to: [cortex/admin.py](cortex/admin.py)

- **`GET /admin/users` — `token_limit` field included**
  - `UserListItem` schema updated; list endpoint now surfaces `token_limit` for each user so the admin UI can display and edit it.
  - Applied to: [cortex/admin.py](cortex/admin.py)

- **Hard 429 enforcement in chat endpoint**
  - After `require_project_access()`, the chat endpoint queries the requesting user's lifetime `total_tokens`. If a `token_limit` is set and is exceeded, raises `HTTPException(429)` with structured detail `{error, message, tokens_used, token_limit}`. Admin users are exempt.
  - Applied to: [cortex/app.py](cortex/app.py)

- **`GET /user/token-usage` — `token_limit` field included**
  - Response now exposes the user's own `token_limit` (or `null` if unlimited) so the UI can render quota progress without a separate call.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Sidebar quota progress bar**
  - When a `token_limit` is set, the expander label changes to `🪙 X / Y tokens (Z%)`. Inside: `st.progress()` bar showing consumption ratio. At ≥ 90% a `st.warning("⚠️ Approaching token limit — contact an admin.")` is displayed. Token metrics/chart still shown below.
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Chat UI — friendly 429 quota-exceeded message**
  - When the server returns HTTP 429, the chat input shows `st.warning()` with the used/limit figures extracted from the error JSON instead of a generic error.
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Admin tab2 (Active Users) — per-user limit editor**
  - Layout changed to 4-column. Fourth column is an expander `🪙 Limit: X` containing a `st.form` with a number input (step 50,000) and Save button that calls `PATCH /admin/users/{id}/token-limit`.
  - Applied to: [ui/pages/admin.py](ui/pages/admin.py)

- **Admin tab3 (All Users) — token_limit column**
  - Users dataframe now includes a `🪙 Token Limit` column via `st.column_config.NumberColumn`.
  - Applied to: [ui/pages/admin.py](ui/pages/admin.py)

- **Admin tab4 (Token Usage) — leaderboard enriched + bulk set form**
  - Leaderboard now shows `token_limit` and `% used` computed columns alongside raw totals.
  - New "🪙 Set Token Limits" `st.form` below the leaderboard: number_input per non-admin user, "💾 Save All Limits" button patches all users in one submit.
  - Applied to: [ui/pages/admin.py](ui/pages/admin.py)

---

## [Unreleased] - 2026-02-18

### Added — Interactive Plotly Charts (`AGENT_PLOT` blocks)

- **`[[PLOT:...]]` tag system — LLM-triggered chart generation**
  - The agent can now request inline charts by embedding structured tags in its response: `[[PLOT: type=histogram df=DF1 x=mapq_score title=MAPQ Distribution]]`. Cortex parses these tags, resolves the referenced DataFrame from conversation history, and creates an `AGENT_PLOT` block that the UI renders via `st.plotly_chart()`.
  - Supported chart types: `histogram`, `scatter`, `bar`, `box`, `heatmap`, `pie`
  - Applied to: [cortex/app.py](cortex/app.py)

- **`AGENT_PLOT` block rendering in UI**
  - New block type dispatched in `render_block()`. Renders inside a `st.chat_message("assistant", avatar="📊")` bubble. Calls `_render_plot_block()` which groups specs by `(df_id, type)` for multi-trace overlays and falls back to single-chart rendering via `_build_plotly_figure()`. Shows `st.warning()` if the referenced DF is not found.
  - Helper functions added: `_resolve_df_by_id()`, `_build_plotly_figure()`, `_render_plot_block()`
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Plotting instructions in LLM system prompt**
  - Large PLOTTING section injected into the system prompt (after `{data_call_block}`). Explicitly forbids Python code (❌ markers), shows correct `[[PLOT:...]]` syntax, lists all 6 chart types with required/optional params, and includes working examples.
  - OUTPUT FORMATTING RULE #3 added: "For plots/charts/visualizations, ONLY use `[[PLOT:...]]` tags. NEVER write Python code for plotting."
  - Applied to: [cortex/agent_engine.py](cortex/agent_engine.py)

- **Server-side code-to-plot fallback**
  - If the LLM writes a Python code block containing `matplotlib`/`plt.`/`plotly`/`px.` instead of a `[[PLOT:...]]` tag, Cortex detects it, auto-generates a `[[PLOT:...]]` spec from the user's message (infers chart type and column from natural language), and strips the code and any "Here is…"/"Explanation:" boilerplate from the visible response.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Visualization hints in skill files**
  - `ENCODE_Search.md`: added "📊 Visualization Hints" section with examples for bar chart of assay distribution and pie chart of status breakdown.
  - `Analyze_Job_Results.md`: added "📊 Visualization Hints" section with examples for QC metric histograms, scatter, file type bar chart, BED score histogram, chromosome distribution, and correlation heatmap.
  - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md), [skills/Analyze_Job_Results.md](skills/Analyze_Job_Results.md)

- **`plotly>=5.18.0` added to `environment.yml`**
  - New `# --- Visualization ---` section added before `# --- Testing ---`.
  - Applied to: [environment.yml](environment.yml)

### Fixed — `MultipleResultsFound` DB crash in `track_project_access`

- **`scalar_one_or_none()` → `scalars().all()` + dedup**
  - `track_project_access()` crashed with `sqlalchemy.exc.MultipleResultsFound` when duplicate `ProjectAccess` rows existed for the same `(user_id, project_id)` pair. Fixed: now fetches all matching rows with `scalars().all()`, keeps the first, deletes duplicates, and returns the surviving row.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — Dogme skills: DF reference in plot request treated as a file lookup

- **Inject DF metadata into context for dogme skills when a `DF<N>` reference is detected**
  - When a user said "plot a histogram of DF1" while a dogme skill (e.g. `run_dogme_cdna`) was active, the LLM called `find_file(file_name=DF1)` via a analyzer DATA_CALL tag instead of emitting `[[PLOT: df=DF1, ...]]`. Root cause: `_inject_job_context` returned early for dogme skills after injecting run UUID context, so the LLM had no information that DF1 was an in-memory DataFrame.
  - Fix: The dogme skills branch now also checks for `DF\d+` references in the user message. If found, it searches history blocks for the matching DataFrame, extracts its label, columns, and row count, and appends a `[NOTE: DF<N> is an in-memory DataFrame — NOT a file to look up. Use [[PLOT:...]] tags.]` to the injected context sent to the LLM.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — `[[PLOT:...]]` tag with natural-language content produces no chart

- **`[^\]]+` regex breaks on `]` inside tag content (e.g. `Columns: ['A', 'B']`)**
  - Root cause: `plot_tag_pattern = r'\[\[PLOT:\s*([^\]]+)\]\]'` uses `[^\]]+` which stops at the **first** `]` in the string. When the LLM writes `Columns: ['Category', 'Count']` inside a natural-language tag, the `]` after `'Count'` terminates the character class match early, the trailing `\]\]` can't match, and `re.finditer` returns zero results — `plot_specs` stays empty, no `AGENT_PLOT` block is created.
  - Fix: Changed to non-greedy `r'\[\[PLOT:\s*(.*?)\]\]'` with `re.DOTALL`. The `.*?` expands until it finds the first `]]`, correctly skipping over any `[` or `]` characters inside the tag body.
  - Also extended the auto-fallback condition to fire when `_user_wants_plot and not plot_specs` (covers the case where the regex match fails entirely, not just when specs have no `df_id`).
  - Also added a guard in AGENT_PLOT block creation to skip any spec where `df_id is None` (instead of creating a broken block with key `"no_df"` that the UI renders as "Chart missing DataFrame reference").
  - Applied to: [cortex/app.py](cortex/app.py)

- **NL fallback parser for `[[PLOT:...]]` tags**
  - After the DF-note fix above, when the tag IS matched, the LLM wrote natural language inside: `[[PLOT: histogram of DF1 with Category on the x-axis...]]` instead of `key=value` pairs. `_parse_tag_params` returned an empty dict, `df_id` remained `None`.
  - Fix: When `_parse_tag_params` yields no `df=` key, a natural-language extraction pass fires on the raw tag text — it regex-extracts the chart type (keyword match), DF reference (`DF\d+`), x column (`<word> on the x-axis`, `x=<word>`), and y column (`<word> on the y-axis`, `y=<word>`).
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — Histogram of pre-aggregated Category/Count table shows all bars at height 1

- **`px.histogram` on a categorical x column counts rows (each = 1) instead of reading the value column**
  - `_build_plotly_figure()` called `px.histogram(df, x="Category")` which bins raw values — since each Category appears exactly once, every bar rendered at height 1 regardless of the actual Count column.
  - Fix: When `chart_type == "histogram"` and the resolved `x_col` is non-numeric (categorical), the code now switches to `px.bar` automatically. It prefers a companion numeric column whose name matches common count/value names (`count`, `value`, `n`, `total`, `freq`, etc.); if none match by name it takes the first numeric column; if there is no numeric column it falls back to `value_counts()`.
  - Applied to: [ui/appUI.py](ui/appUI.py)

### Fixed — Bar/histogram plot of DF shows bars at height 1 due to missing column names and wrong aggregation

- **`_resolve_df_by_id` constructed DataFrame without column names**
  - `pd.DataFrame(rows)` was called without passing `columns=`, so the DataFrame had integer column names `[0, 1]` instead of `["Category", "Count"]`. All `x_col`/`y_col` lookups failed (case-insensitive match returned nothing), the bar branch fell through to `value_counts()`, and every bar rendered at height 1.
  - Fix: now passes `columns=fdata.get("columns") or None` to the DataFrame constructor.
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Bar branch fell back to `value_counts()` even for pre-aggregated tables**
  - When `y_col=None` and `agg` is not `"count"`, the bar branch now checks if `x_col` is categorical and a numeric companion column exists. If so, it selects that column as `y` (preferring names like `count`, `value`, `n`, `total`, `freq`) instead of counting rows. This mirrors the same logic added to the histogram branch.
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Auto-fallback spec incorrectly set `agg="count"` for bar charts**
  - The code-fallback path that auto-generates a `[[PLOT:...]]` spec was adding `agg="count"` to every bar chart, which bypasses the pre-aggregated column detection and always counts rows. Removed; `_build_plotly_figure` now infers `y` automatically.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — ENCODE Search Tool & Parameter Aliases

- **`search_experiments` / `search_experiment` aliased to `search_by_biosample`**
  - LLM occasionally called `search_experiments` (non-existent tool name), returning a tool-not-found error instead of running the search. Added both variants to the tool alias map.
  - Applied to: [atlas/config.py](atlas/config.py)

- **Comprehensive biosample parameter aliases for `search_by_biosample`**
  - LLM used various param names (`biosample`, `biosample_name`, `cell_line`, `sample`, `biosample_term_name`, `cell_type`, `tissue`) when the tool expected `search_term`. All variants now silently map to `search_term`.
  - Applied to: [atlas/config.py](atlas/config.py)

---

### Fixed — CSV/BED File Display: LLM Gets Readable Table, DF Widget Visible

- **Parsed CSV/BED data formatted as markdown table for LLM**
  - Previously, parsed CSV data was sent to the second-pass LLM as a massive JSON blob (with `column_stats`, `dtypes`, nested metadata) causing it to respond "The query did not return the expected data." Now `_format_data` in result_formatter.py detects structured CSV/BED results (has `columns` + `data` list) and renders them as a clean markdown table — the same format the LLM can actually analyze and summarize.
  - Applied to: [atlas/result_formatter.py](atlas/result_formatter.py)

- **CSV/BED dataframes kept visible (reverted `visible: False`)**
  - The previous fix of hiding CSV DFs caused the interactive widget to be buried in the details section. Since the DF widget is the primary way to display parsed file data (with column selection, sorting, download), it's now visible again. The LLM text summary and the DF widget complement each other.
  - Applied to: [cortex/app.py](cortex/app.py)

### Added — Debug UI Panel

- **Debug toggle in sidebar**
  - New 🐛 Debug toggle in the sidebar enables per-response debug info display without needing server logs.
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Debug info section per AGENT_PLAN block**
  - When debug mode is on, each assistant response shows a collapsed "🐛 Debug Info" expander with JSON diagnostic data including: target_df_id, detected filters (assay, output_type, file_type), injected DF names/row counts, suppressed API calls, LLM raw response preview, auto-generated calls, and more.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **`_inject_job_context` returns 3-tuple**
  - Now returns `(augmented_message, injected_dataframes, debug_info)` to pass diagnostic data from the injection logic through to the UI payload.
  - Applied to: [cortex/app.py](cortex/app.py)

### Fixed — False Positive Filter Matching ("hic" in "which")

- **Word-boundary matching for assay, output_type, and file_type filters**
  - All three filter detection loops in `_inject_job_context` now use `re.search(r'\b...\b')` instead of plain `if alias in message` substring check. This prevents false positives like `"hic"` matching inside `"which"`, which was causing the assay filter to fire as `"in situ Hi-C"` and the file_type filter as `"hic"` on queries like "which of them are the output type methylated reads?" — completely blocking the correct `output_type_filter = "methylated reads"` from being detected.
  - Root cause of the persistent "methylated reads" follow-up failure.
  - Applied to: [cortex/app.py](cortex/app.py)

### Added — Named Dataframes (DF1, DF2, ...)

- **Sequential DF IDs assigned to every dataframe**
  - Each dataframe produced (from API calls or follow-up filters) gets a sequential ID: DF1, DF2, DF3, etc., scoped to the conversation. IDs are stored in `metadata.df_id` and persist across the session.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Explicit DF references in follow-up queries**
  - Users can now reference a specific dataframe by ID: e.g. "filter DF3 by methylated reads" or "show me DF1". `_inject_job_context` parses `DF\d+` patterns and uses exactly that dataframe, bypassing the file-type/assay heuristic guessing that previously caused wrong dataframe selection.
  - Applied to: [cortex/app.py](cortex/app.py)

- **DF IDs shown in UI expander headers**
  - Dataframe expanders now display their ID: `📊 DF3: ENCSR160HKZ bam files (12) — 12 rows × 5 columns` so users know which ID to reference.
  - Applied to: [ui/appUI.py](ui/appUI.py)

### Fixed — ENCODE File Query & Follow-up Filter Accuracy

- **`get_file_types` aliased to `get_files_by_type`**
  - Problem: LLM occasionally called `get_file_types` (returns only `["bam", "bed bed3+", "tar"]` — type names only) instead of `get_files_by_type` (returns full file metadata per type). This caused counts like "1 BAM file" instead of "12 BAM files" and no dataframe.
  - Fix: Added `get_file_types` → `get_files_by_type` alias in the tool alias map so any call to the wrong tool is silently corrected before execution.
  - Applied to: [atlas/config.py](atlas/config.py)

- **Per-file-type dataframes instead of one merged table**
  - Problem: All file types (bam, bed, tar, fastq, etc.) were flattened into a single 69-row table with a "File Type" column. Follow-up queries like "which BAM files are methylated reads?" injected all 69 rows and the LLM returned results from every file type.
  - Fix: `get_files_by_type` results are now stored as **one dataframe per file type** (e.g. `"ENCSR160HKZ bam files (12)"`, `"ENCSR160HKZ bed bed3+ files (54)"`), each tagged with `metadata.file_type`. The UI renders them as separate expandable tables.
  - Applied to: [cortex/app.py](cortex/app.py)

- **File-type-scoped context injection for follow-up queries**
  - Problem: "which of the BAM files are methylated reads?" injected all file-type dataframes, so the LLM received 69 rows across BED/tar/etc. and hallucinated or mixed results from bed files.
  - Fix: `_inject_job_context` now detects the file-type context from the current message or recent conversation history (e.g. "bam" from "how many bam files?") and skips dataframes whose `metadata.file_type` does not match. The LLM receives **only the 12 BAM rows**.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Suppression of redundant `get_files_by_type` DATA_CALL tags**
  - Problem: Even after correct context injection, the LLM still emitted `[[DATA_CALL: ... get_files_by_type]]` for follow-up filter queries (because words like "bam" and "accessions" appear in the message), triggering a fresh API fetch that overwrote the injected filtered data.
  - Fix: When `_injected_previous_data` is true and not row-capped, any `get_files_by_type` or `get_experiment` DATA_CALL tags emitted by the LLM are dropped before execution.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Removed "methylated" from `_auto_generate_data_calls` file keywords**
  - "methylated" was in the keyword list that triggers auto-generation of `get_files_by_type` calls, causing the system to re-fetch all files on any follow-up that mentioned methylation, ignoring injected context.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Stripped `<details>` raw data from conversation history sent to LLM**
  - Problem: Even with per-file-type dataframe injection working correctly (only 12 BAM rows injected), the LLM's conversation history still contained the **full** `<details>` block from the previous turn — all 69 file rows (bam + bed + tar). The LLM read the unfiltered history instead of the filtered `[PREVIOUS QUERY DATA:]`, returning 54 BED accessions for a BAM follow-up.
  - Fix: When building `conversation_history` from AGENT_PLAN blocks, `<details>...</details>` sections (and their preceding `---` separator) are stripped via regex. The raw data remains in the stored block payload for the UI's collapsible widget; the LLM only sees the filtered data injected by `_inject_job_context`.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Only show relevant file-type tables in the UI**
  - Problem: "How many BAM files for ENCSR160HKZ?" showed all three file-type tables (tar, bam, bed) prominently, even though the user only asked about BAM.
  - Fix: When storing per-file-type dataframes, the server detects which file type(s) the user asked about and tags matching dataframes with `metadata.visible = True`. The UI checks for this flag: if any dataframe is marked visible, only those are rendered; otherwise all are shown. Non-visible dataframes are still stored for follow-up queries.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **Follow-up filter queries now show interactive dataframe of matching rows**
  - Problem: "Which of them are methylated reads?" correctly filtered to 2 rows but only showed the LLM's text answer — no interactive table. The user had no way to verify the accessions.
  - Fix: `_inject_job_context` now returns filtered dataframes alongside the augmented message. These are merged into `_embedded_dataframes` so the UI renders them as interactive `st.dataframe` tables with download button.
  - Applied to: [cortex/app.py](cortex/app.py)

- **DATA_CALL suppression now covers legacy `[[ENCODE_CALL:]]` tags**
  - Problem: The LLM emitted `[[ENCODE_CALL: get_experiment, accession=ENCSR160HKZ]]` (legacy format) which bypassed the suppression block that only checked unified `[[DATA_CALL:]]` format. This caused a redundant `get_experiment` API call on follow-up questions.
  - Fix: Suppression now filters both `data_call_matches` and `legacy_encode_matches` for tools in `{"get_files_by_type", "get_experiment"}`.
  - Applied to: [cortex/app.py](cortex/app.py)

- **ENCODE search answer quality — LLM response accuracy**
  - Problem: LLM gave wrong counts (e.g. "3 experiments" instead of 2503), showed markdown pipe-tables instead of interactive dataframes, reproduced thousands of rows from memory causing timeouts, and answered follow-up assay/output-type filter questions by hallucinating accessions from truncated `<details>` text.
  - Fixes applied across multiple iterations:
    - `analyze_results` prompt rewritten: prose-only for counts, aggregation-only tables allowed, ≤200 word limit, explicit "do NOT reproduce rows"
    - Summary table now written **before** raw rows in `result_formatter.py` so LLM always sees totals even when the row table is truncated by `MAX_DATA_CHARS`
    - Full search results stored in `_embedded_dataframes` before truncation; UI renders them as `st.dataframe` with column filter, row cap, and download button
    - UI rendering order: LLM answer → dataframe (expanded) → raw details (collapsed)
    - Streamlit widget key sanitization via `re.sub(r"[^a-zA-Z0-9_]", "_", ...)` to prevent silent widget skipping
    - `_inject_job_context` reads `_dataframes` directly from the most recent AGENT_PLAN block payload instead of extracting from `<details>` text (which was capped at 4000–6000 chars)
    - Server-side assay-type row filtering using `_ENCODE_ASSAY_ALIASES` (module-level map) so "how many long read RNA-seq?" after a K562 search injects only matching rows
    - `_auto_generate_data_calls` uses `_ENCODE_ASSAY_ALIASES` to re-query with `assay_title=` when the dataset is too large to filter in-memory
  - Applied to: [cortex/app.py](cortex/app.py), [cortex/agent_engine.py](cortex/agent_engine.py), [atlas/result_formatter.py](atlas/result_formatter.py), [ui/appUI.py](ui/appUI.py), [skills/ENCODE_Search.md](skills/ENCODE_Search.md)

---

## [Unreleased] - 2026-02-17

### Fixed — 403 "You do not have access to this project" on Chat

- **Root cause 1: Missing ownership fallback in `require_project_access`**
  - If a user's `project_access` row was missing (e.g. after a failed project creation), they received a 403 even on their own project because the check only looked at the `project_access` table and not `projects.owner_id`
  - Fix: added a third check — if no access row exists but `Project.owner_id == user.id`, the missing row is automatically recreated and the request proceeds
  - Applied to: [cortex/dependencies.py](cortex/dependencies.py)

- **Root cause 2: Projects created only client-side never registered in DB**
  - When `POST /projects` timed out or failed, the UI silently fell back to a locally-generated UUID. Any subsequent `/chat` call then hit 403 because no `Project` or `ProjectAccess` row existed for that UUID
  - Fix: `/chat` endpoint now auto-registers the project (creates both `Project` and `ProjectAccess` rows as owner) if the project ID is not found in the DB, before running the access check
  - Applied to: [cortex/app.py](cortex/app.py)

- **Improved project creation timeout**
  - Raised `POST /projects` timeout in the UI from 5 s to 10 s to reduce the likelihood of the fallback UUID path being taken on slow startup
  - Applied to: [ui/appUI.py](ui/appUI.py)

---

## [Unreleased] - 2026-02-17

### Added — Project Management & Dashboard

- **Projects Dashboard Page (`ui/pages/projects.py`)**
  - New Streamlit page with full project management UI
  - Editable data table with checkbox columns for bulk Archive and Delete operations
  - Columns: Name, Jobs, Size (MB), Messages, Files, Created, Last Active, Status
  - Bulk actions: archive selected, permanently delete selected (with confirmation)
  - Per-project detail view with 4 tabs: Stats, Jobs, Files, Conversations
  - Inline project rename, disk usage summary at page top
  - Applied to: [ui/pages/projects.py](ui/pages/projects.py)

- **Backend Project Management Endpoints**
  - `GET /projects/{id}/stats` — job count, disk usage, message count, conversation count, file count, full job list with details
  - `GET /projects/{id}/files` — lists all files across jailed dirs and legacy `nextflow_work_dir` paths with size, extension, modified date
  - `GET /user/disk-usage` — total disk usage across all projects with per-project breakdown, scans actual job work directories
  - `DELETE /projects/{id}` — soft-delete (archive, sets `is_archived=True`)
  - `DELETE /projects/{id}/permanent` — permanent delete: cascades through conversation_messages, job_results, conversations, project_blocks, dogme_jobs, project_access, project record, and removes on-disk directory
  - `GET /projects` now includes `job_count` per project (lightweight query from `dogme_jobs` table)
  - Applied to: [cortex/app.py](cortex/app.py)

- **Enhanced Sidebar Project Switcher**
  - Shows all projects (was limited to 5) with job count per project
  - Search/filter box when >4 projects
  - Archive button per project (inline, non-current projects)
  - Current project shown in bold with pin icon
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Project Name in Chat Title**
  - Title shows `🧬 Project Name` instead of raw UUID
  - Falls back to truncated UUID if name lookup fails
  - Projects cached in session state for instant title display
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Results Page Auto-Lists Jobs**
  - Job dropdown auto-populated from current project's jobs (via `/projects/{id}/stats`)
  - Shows status emoji, sample name, and UUID prefix per job
  - Manual UUID input preserved as fallback
  - Removed hardcoded example UUIDs
  - Applied to: [ui/pages/results.py](ui/pages/results.py)

### Fixed — Flicker-Free Auto-Refresh

- **Page Flicker During Job Monitoring**
  - Problem: Page visibly refreshed/flashed every 2 seconds during job monitoring — JS `window.parent.location.reload()`, `st.empty()` DOM destruction, and `time.sleep(2) + st.rerun()` all caused full-page re-execution
  - Solution: Complete rewrite using `@st.fragment(run_every=timedelta(seconds=2))` — Streamlit's native partial-rerun that only re-executes the chat rendering function while sidebar, title, and input stay stable
  - Removed: `st.empty()` container, JS `window.parent.location.reload()` hack, `import streamlit.components.v1`, blocking `time.sleep() + st.rerun()` loop
  - Added: Bootstrap logic for one-time full rerun when running job first detected (to register fragment), 30-second grace window after job completion
  - Applied to: [ui/appUI.py](ui/appUI.py)

### Fixed — Disk Usage Reporting

- **Disk Usage Showing 0 for All Projects**
  - Problem: `/projects/{id}/stats`, `/projects/{id}/files`, and `/user/disk-usage` only scanned jailed path `AGOUTIC_DATA/users/{user_id}/{project_id}/` which is empty for legacy jobs
  - Solution: All three endpoints now also scan actual `nextflow_work_dir` paths stored in `dogme_jobs` table, covering both jailed and legacy `launchpad_work/` directories
  - Fixed key mismatch: files endpoint returns `size_bytes` consistently, UI handles both `size_bytes` and `size` as fallback
  - Fixed `pathlib.Path` vs FastAPI `Path` import collision (`from fastapi import Path as FastAPIPath`)
  - Applied to: [cortex/app.py](cortex/app.py), [ui/pages/projects.py](ui/pages/projects.py)

## [Unreleased] - 2026-02-17

### Added — Security Hardening Sprint

- **Role-Based Authorization Gates on All Endpoints**
  - Added `require_project_access(project_id, user, min_role)` dependency that enforces viewer/editor/owner hierarchy on every project-scoped endpoint
  - Added `require_run_uuid_access(run_uuid, user)` for job-scoped endpoints (debug, analysis)
  - Admin users bypass all project-level checks; public projects allow viewer-level access
  - Wired into ~15 endpoints: `/blocks`, `/chat`, `/conversations`, `/jobs`, `/analysis/*`, `/projects/*/access`
  - Applied to: [cortex/dependencies.py](cortex/dependencies.py), [cortex/app.py](cortex/app.py)

- **Server-Side Project Creation (UUID)**
  - `POST /projects` — creates project with `uuid4()` server-side, mkdir-before-DB pattern (orphan empty dir is harmless, zombie DB row is not)
  - `GET /projects` — lists user's projects (owned + shared), supports `include_archived` param
  - `PATCH /projects/{project_id}` — rename or archive (owner role required)
  - UI updated to call server endpoints instead of generating UUIDs client-side
  - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **Job Ownership Binding (`user_id` on `dogme_jobs`)**
  - Added `user_id` column to DogmeJob in both Launchpad and Analyzer models (nullable for legacy jobs)
  - Cortex passes `user_id` through MCP submit flow → Launchpad REST → DB
  - `require_run_uuid_access()` checks `user_id` for direct ownership, falls back to project access
  - Applied to: [launchpad/models.py](launchpad/models.py), [launchpad/schemas.py](launchpad/schemas.py), [launchpad/db.py](launchpad/db.py), [launchpad/app.py](launchpad/app.py), [launchpad/mcp_server.py](launchpad/mcp_server.py), [launchpad/mcp_tools.py](launchpad/mcp_tools.py), [analyzer/models.py](analyzer/models.py)

- **User-Jailed File Paths for Nextflow Jobs**
  - Hardened `user_jail.py` with `_validate_id()` (rejects `/`, `\`, `.`, null bytes) and `_ensure_within_jail()` defense-in-depth
  - Nextflow executor now creates jailed work dirs at `AGOUTIC_DATA/users/{user_id}/{project_id}/{run_uuid}/` when `user_id` is present, falls back to flat legacy path
  - Analysis engine resolves jailed paths when looking up job work directories
  - Applied to: [cortex/user_jail.py](cortex/user_jail.py), [launchpad/nextflow_executor.py](launchpad/nextflow_executor.py), [analyzer/analysis_engine.py](analyzer/analysis_engine.py)

- **Cookie Hardening**
  - `Secure` flag is now environment-gated: `secure=True` when `ENVIRONMENT != "development"`
  - `httponly` and `samesite=lax` were already set
  - Added `ENVIRONMENT` config variable (defaults to `"development"`)
  - Applied to: [cortex/auth.py](cortex/auth.py), [cortex/config.py](cortex/config.py)

- **Database Migration Script**
  - `migrate_hardening.py` — adds `dogme_jobs.user_id`, `projects.is_archived`, `project_access.role` to existing databases
  - Idempotent (checks `PRAGMA table_info` before altering), safe to run multiple times
  - `init_db.py` updated for fresh installs: includes all new columns, `project_access` table, `conversations`, `conversation_messages`, `job_results` tables
  - Applied to: [cortex/migrate_hardening.py](cortex/migrate_hardening.py), [cortex/init_db.py](cortex/init_db.py)

### Fixed

- **CSV/TSV File Parsing Returns Path Only, No Contents**
  - Problem: When user asks "parse jamshid.mm39_final_stats.csv", the system calls `find_file` (returns the relative path) but never follows up with `parse_csv_file` to read the actual data
  - Root cause (v1): `_auto_generate_data_calls()` only generated a `find_file` call with no chaining
  - Root cause (v2): Even after adding `_chain` key to auto_calls, the LLM generates its own `[[DATA_CALL: ... tool=find_file ...]]` tag (trained by skill files like DOGME_QUICK_WORKFLOW_GUIDE.md), which sets `has_any_tags = True` and **skips auto_calls entirely** — so the `_chain` key never reaches the execution loop
  - Solution: Made chaining independent of how `find_file` was triggered. In the execution loop, **any** successful `find_file` call automatically chains to the appropriate parse/read tool by inferring it from the filename extension via `_pick_file_tool()` (`.csv/.tsv` → `parse_csv_file`, `.bed` → `parse_bed_file`, else → `read_file_content`). Works regardless of whether the call came from auto_calls or LLM-generated DATA_CALL tags.
  - Applied to: [cortex/app.py](cortex/app.py)

- **Job Progress Not Auto-Refreshing During Nextflow Runs**
  - Problem: User had to manually refresh the browser to see Nextflow job progress updates (running tasks, completion status), even with "Live Stream" toggle on
  - Root cause (v1): Auto-refresh depended on the "Live Stream" toggle. No forced refresh for running jobs.
  - Root cause (v2): Used `locals().get("_has_running_job")` to pass the running-job flag to the auto-refresh section at script bottom — unreliable in Streamlit's execution model since the variable was only defined inside a conditional `else:` branch
  - Solution:
    1. Replaced `locals().get()` with `st.session_state["_has_running_job"]` for reliable flag persistence across Streamlit reruns
    2. Track EXECUTION_JOB blocks with RUNNING status + APPROVAL_GATE blocks just approved (async submission in-flight) to force auto-refresh
    3. Clear flag when job finishes (DONE/FAILED) to stop unnecessary polling
  - Applied to: [ui/appUI.py](ui/appUI.py)

### Added
- **Job Duration & Last Update Timestamps in Nextflow Progress Display**
  - Shows elapsed runtime ("Running for 2m 34s") calculated from block creation time
  - Shows recency of last status poll ("Updated 3s ago") from a `last_updated` timestamp stored in the EXECUTION_JOB payload on each poll cycle
  - Applied to: [ui/appUI.py](ui/appUI.py), [cortex/app.py](cortex/app.py)

## [Unreleased] - 2026-02-16

### Added
- **Real-Time Chat Progress Feedback**
  - Problem: With local LLMs, responses can take a long time and the UI showed no indication that anything was happening
  - Solution: Added a progress tracking system with polling-based status updates
  - Backend: Added `_emit_progress()` status tracker, `request_id` field on `ChatRequest`, and `/chat/status/{request_id}` polling endpoint. Status events emitted at key stages: "Thinking...", "Switching skill...", "Querying [service]...", "Analyzing results...", "Done"
  - Frontend: Chat input now uses a background thread + `st.status()` widget that polls the backend every 1.5s for progress updates. Shows animated stage icons, descriptive messages, and elapsed time

- **Pre-LLM Automatic Skill Switch Detection**
  - Problem: LLM sometimes fails to emit `[[SKILL_SWITCH_TO:...]]` tags when the user's message clearly requires a different skill — e.g., asking to "analyze a local cDNA sample at /path/..." while in ENCODE_Search, the LLM just describes what should be done instead of switching
  - Solution: Added `_auto_detect_skill_switch()` function that runs BEFORE the LLM call to catch obvious skill mismatches and switch proactively, avoiding a wasted LLM round-trip with the wrong skill
  - Detection signals:
    - **analyze_local_sample**: file path + (analysis verb OR sample keyword OR data type like "cDNA/DNA/RNA")
    - **ENCODE_Search**: ENCODE keyword + search verb, or explicit ENCSR accession
    - **analyze_job_results**: QC report, parse, show results, etc.
  - Only triggers on strong unambiguous signals to avoid false positives
  - Applied to: [cortex/app.py](cortex/app.py)
  - Cleanup: stale progress entries auto-purged after 5 minutes
  - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

### Fixed
- **Post-Job Analysis Summary Showing "Total files: 0"**
  - Problem: After a Dogme job completes successfully (11 tasks, files clearly present in work directory), the auto-triggered analysis shows "Total files: 0" with all category counts at 0
  - Root cause: The `get_analysis_summary` MCP tool in analyzer returns `{"success": True, "summary": "<markdown text>"}` — a pre-formatted markdown string. But `_auto_trigger_analysis()` in cortex expected structured dict fields (`file_summary`, `key_results`) that don't exist at the top level of the response
  - Solution (2 parts):
    1. Extended MCP tool response in `analyzer/mcp_tools.py` to include structured data fields (`all_file_counts`, `file_summary`, `key_results`, `mode`, `status`, etc.) alongside the markdown `summary` string
    2. Fixed `_auto_trigger_analysis()` in `cortex/app.py` to read from `all_file_counts` for totals (not `key_results`) and use fallback arithmetic from `file_summary` lists
  - Applied to: [cortex/app.py](cortex/app.py), [analyzer/mcp_tools.py](analyzer/mcp_tools.py)

- **LLM Re-querying Instead of Using Data Already in Conversation**
  - Problem: After fetching data (file listings, search results), follow-up questions like "which of them are methylated reads?" or "what are the accessions for the long read RNA-seq samples?" cause the LLM to make new API calls instead of reading its own previous response
  - Root cause (v1): Keyword-based detection (`_filter_words`, `_referential`) was too narrow — only caught phrasings like "which of them", "those", "sort them" but missed "what are the accessions for..." style questions
  - Root cause (v2): LLM doesn't recognize it already has the data in conversation history `<details>` blocks
  - Solution (3 parts):
    1. Added "Answer From Existing Data First" rule to ENCODE_Search.md skill with two examples (file filtering + search subset extraction) — tells LLM to check previous responses and injected `[PREVIOUS QUERY DATA:]` before making new API calls
    2. Broadened `_inject_job_context()` ENCODE follow-up detection: removed fragile keyword lists entirely; now ANY follow-up without a new explicit subject (no new accession, no new biosample) gets the most recent `<details>` raw data injected with `[PREVIOUS QUERY DATA:]` context. Falls back to biosample context injection if no `<details>` data exists
    3. Added `_injected_previous_data` guard in auto-generation safety net — when previous data was injected, absence of DATA_CALL tags is correct behavior (LLM is answering from context), so auto-generation is skipped
  - Applied to: [cortex/app.py](cortex/app.py), [ENCODE_Search.md](skills/ENCODE_Search.md)

- **LLM Hallucinating Wrong ENCODE Tool Names in DATA_CALL Tags**
  - Problem: When asking "how many bam files do you have for ENCSR160HKZ?", the LLM generated `tool=get_files_types` instead of the correct `tool=get_files_by_type`, causing "Unknown tool" MCP errors
  - Root cause: The existing fallback patterns only fix plain-text tool invocations (e.g., `Get Files By Type(...)` → DATA_CALL), but cannot fix wrong tool names *inside* properly-formatted DATA_CALL tags
  - Solution: Added a `tool_aliases` map in `atlas/config.py` that maps ~25 common LLM-hallucinated tool names to their correct equivalents. Applied during DATA_CALL parsing in `cortex/app.py` before tool execution
  - Covers aliases for all ENCODE tools: `get_files_by_type`, `get_file_types`, `get_files_summary`, `get_experiment`, `search_by_biosample`, `search_by_target`, `search_by_organism`, `get_available_output_types`, `get_all_metadata`, `list_experiments`, `get_cache_stats`, `get_server_info`
  - Also added `get_all_tool_aliases()` helper function to `atlas/config.py`
  - Applied to: [cortex/app.py](cortex/app.py), [atlas/config.py](atlas/config.py)

- **LLM Failing to Call File-Level ENCODE Tools Correctly**
  - Problem: `get_file_metadata` and `get_file_url` require TWO parameters — `accession` (parent experiment ENCSR) + `file_accession` (file ENCFF) — but the LLM consistently gets this wrong in multiple ways:
    - Uses `get_experiment` instead of `get_file_metadata` for file accessions
    - Mangles the ENCFF prefix to ENCSR to match `get_experiment`'s expected format
    - Hallucinated a completely different ENCSR accession while user message has ENCFF
    - Provides only one parameter when two are required
  - Root cause: LLM doesn't distinguish experiment vs file accession types, and the tool signatures weren't documented well enough in skill files
  - Solution (multi-layered):
    1. **`_correct_tool_routing()`** in `cortex/app.py` — comprehensive routing correction function that:
       - Case 1: Detects `get_experiment` called with ENCFF → reroutes to `get_file_metadata`
       - Case 2: Detects mangled ENCFF→ENCSR prefix by cross-checking user message → restores correct prefix
       - Case 3: Detects `get_experiment` with hallucinated ENCSR when user message contains ENCFF → extracts correct ENCFF and reroutes
       - Detects `get_file_metadata` called without experiment accession → automatically finds it
    2. **`_find_experiment_for_file()`** helper — scans conversation history to find the parent ENCSR experiment for a given ENCFF file accession (first looks for messages mentioning both, then falls back to most recent ENCSR)
    3. **Graceful error for cold file queries** — when no parent experiment can be found in history (e.g., user asks about ENCFF with no prior context), the system short-circuits with a clear message ("Please first query the experiment that contains this file") instead of making a doomed MCP call that returns a cryptic Pydantic error
    4. **Removed incorrect `param_aliases`** in `atlas/config.py` — the previous fix wrongly mapped `accession→file_accession` which would remove the required experiment accession parameter
    4. **Updated skill docs** — ENCODE_Search.md and ENCODE_LongRead.md now show `get_file_metadata` requires both params, with accession type reference table and warning to never change prefixes
  - Applied to: [cortex/app.py](cortex/app.py), [atlas/config.py](atlas/config.py), [ENCODE_Search.md](skills/ENCODE_Search.md), [ENCODE_LongRead.md](skills/ENCODE_LongRead.md)

- **ENCODE Follow-Up Questions Losing Biosample Context**
  - Problem: After asking "how many C2C12 experiments?" then "give me the accession for the long read RNA-seq samples", the LLM searched ALL long-read RNA-seq across ENCODE instead of filtering to C2C12
  - Root cause: The LLM generated its own DATA_CALL tags (so auto-generation didn't trigger), but searched without the C2C12 filter because the current message had no explicit biosample mention
  - Solution: Extended `_inject_job_context()` to also handle ENCODE skills — when the user's message has no explicit biosample or accession, the function scans conversation history for the previous search subject and injects `[CONTEXT: previous search was about C2C12, organism=Mus musculus]` so the LLM generates the correct filtered search
  - Also handles follow-ups referencing ENCSR accessions from previous results
  - Applied to: [cortex/app.py](cortex/app.py)

- **Conversational References Losing Context for Biosample Queries**
  - Problem: When user asked "how many C2C12 experiments?" then "give me a list of them", the LLM asked for clarification instead of re-running the C2C12 search
  - Root cause: The auto-generation safety net only scanned conversation history for ENCSR accessions, not biosample terms. "list of them" has no biosample keyword in the current message, so the biosample detection path never triggered
  - Solution: Extended `_auto_generate_data_calls()` to scan conversation history for previous biosample search terms when referential words ("them", "those", "list", etc.) are detected
  - Also added "list" to the referential words list (previously missing)
  - Refactored biosamples dict to function scope so it's reusable across detection paths
  - Result: "give me a list of them" after a biosample search now re-emits the same search automatically
  - Applied to: [cortex/app.py](cortex/app.py)

### Added
- **Job Context Injection for Dogme Skills**
  - System now automatically injects UUID and work directory context into user messages for Dogme analysis skills
  - Added `_inject_job_context()` function that prepends `[CONTEXT: run_uuid=..., work_dir=...]` to messages
  - Added `_extract_job_context_from_history()` to scan conversation history for most recent job context
  - Context injection happens before both initial `engine.think()` call and skill-switch re-run
  - Eliminates LLM wasting time searching conversation history for known information
  - Applied to: [cortex/app.py](cortex/app.py)

- **Auto-Generation Safety Net for File Parsing**
  - Extended `_auto_generate_data_calls()` with Dogme file-parsing patterns
  - When in a Dogme skill and user says "parse {filename}", auto-generates `find_file` DATA_CALL if LLM fails to
  - Extracts filename from user message using pattern matching for common verbs (parse, show, read, etc.)
  - Retrieves UUID from conversation history automatically
  - Provides failsafe similar to existing ENCODE auto-generation
  - Applied to: [cortex/app.py](cortex/app.py)

### Changed
- **Updated Dogme Skill Instructions for Context Injection**
  - Updated all Dogme skills (DNA, RNA, cDNA) to check for `[CONTEXT: run_uuid=...]` first
  - Modified "Retrieving Filename" section in DOGME_QUICK_WORKFLOW_GUIDE.md to emphasize context injection
  - Changed STEP 2 from "Extract UUID from current conversation" to "Extract UUID from context injection or conversation"
  - Skills now instruct LLM to use injected context directly instead of searching conversation history
  - Applied to: [DOGME_QUICK_WORKFLOW_GUIDE.md](skills/DOGME_QUICK_WORKFLOW_GUIDE.md), [Dogme_cDNA.md](skills/Dogme_cDNA.md), [Dogme_DNA.md](skills/Dogme_DNA.md), [Dogme_RNA.md](skills/Dogme_RNA.md)

- **Enhanced UI Project Switch Cleanup**
  - Added cleanup of stale widget keys (form keys, checkbox keys, rejection state) on new project creation
  - Clears all keys with prefixes: `params_form_`, `logs_`, `rejecting_`, `rejection_reason_`, `submit_reject_`, `cancel_reject_`
  - Prevents form widget state from previous projects leaking into new projects
  - Applied to: [ui/appUI.py](ui/appUI.py)

- **Improved UI Render Defense Against Ghost Messages**
  - Modified `render_block()` to accept `expected_project_id` parameter
  - Silently skips rendering blocks that don't match the expected project ID (last line of defense)
  - Wrapped chat rendering in `st.empty()` container which is fully replaced on every rerun (no DOM reuse)
  - Added `_suppress_auto_refresh` counter (3 cycles) after project switch to allow frontend to settle
  - Prevents Streamlit DOM-reuse artifacts where old messages briefly appear after project switch
  - Applied to: [ui/appUI.py](ui/appUI.py)

### Fixed
- **UI Project Switching Race Condition**
  - Problem: When creating a new project, old chat messages from previous project would briefly appear below welcome message
  - Root cause: Streamlit's `text_input` widget takes 2-3 rerun cycles to sync its displayed value after programmatic changes. During this window, the old project ID in the text input triggered sync logic that silently reverted `active_project_id` back to the old project, which then fetched and rendered the old project's blocks
  - Solution: 
    - Replaced boolean `_project_just_switched` flag with grace counter `_switch_grace_reruns = 3` that survives multiple rerun cycles
    - Added `_welcome_sent_for` to cleanup list on new project creation and all project-switch paths
    - Added defensive render-time filter to drop any block whose `project_id` doesn't match active project
  - Result: New projects now show only their own messages with no ghost messages from previous projects
  - Applied to: [ui/appUI.py](ui/appUI.py)

### Changed
- **Enhanced Analyzer Analysis File Discovery**
  - Extended file filtering to exclude both `work/` and `dor*/` subdirectories from analysis to prevent bloated file counts from Nextflow intermediate artifacts
  - Added intelligent key file identification based on analysis mode (DNA/RNA/cDNA) with mode-specific patterns
  - Modified `generate_analysis_summary()` to:
    - Filter displayed files to key result files only (qc_summary, stats, flagstat, gene_counts, etc.)
    - Keep full file counts in separate `all_file_counts` field for reference
    - Parse mode-specific files (gene_counts, transcript_counts for cDNA mode)
    - Extract key metrics from parsed reports into top-level `key_results` dict
  - Added `all_file_counts` field to `AnalysisSummary` schema to preserve total file statistics
  - Result: Analysis summaries now focus on important result files while maintaining awareness of total file counts
  - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/schemas.py](analyzer/schemas.py)

- **Improved Analysis Summary Formatting**
  - Changed `get_analysis_summary_tool` to return formatted markdown tables instead of raw JSON structures
  - Summary now includes:
    - Basic info table (Sample Name, Mode, Status, Work Directory)
    - File summary table (counts by file type)
    - Key results with availability status
    - Top 10 files from each category with size information
    - Natural language description of analysis results
  - Result: Analysis summaries are more readable and user-friendly, avoiding nested JSON truncation by MCP protocol
  - Applied to: [analyzer/mcp_tools.py](analyzer/mcp_tools.py)

## [Unreleased] - 2026-02-15

### Fixed
- **Welcome Skill Not Routing UUID + Parse Requests**
  - Problem: When user said "use UUID: X" + "parse filename", Welcome skill didn't route to analyze_job_results
  - Instead stayed in Welcome and said "I don't have tools to parse files"
  - Root cause: Welcome routing rules weren't looking for UUID patterns or "parse" keyword
  - Solution: Added explicit detection for:
    - "use UUID:" pattern
    - "parse {filename}" pattern  
    - Combined UUID + parse requests
  - Now: Welcome immediately routes to analyze_job_results when UUID + parse request detected
  - Applied to: `Welcome.md`

- **UUID Corruption/Truncation by LLM (Known Issue with Enhanced Workaround)**
  - Problem: LLMs corrupt long UUIDs during transmission (truncation AND character scrambling)
  - Examples:
    - Truncation: `b954620b-a2c7-4474-9249-f31d8d55856f` → `b954620b-a2c7-4474-9249-f31d8d558566` (last 3 chars lost)
    - Character corruption: `b954620b-a2c7-4474-9249-f31d8d55856f` → `b954620b-a274-4474-9249-f31d8d55856f` (a2c7→a274)
  - Root cause: Model-level string corruption in transmission layer, not skill configuration
  - Workaround enhanced: Added UUID format validation (not just length check)
  - Now agent validates:
    - Length must be exactly 36 characters
    - Format must be 8-4-4-4-12 segments separated by dashes
    - All characters must be lowercase hex (0-9, a-f)
    - If "Job not found" error, agent compares tool's UUID char-by-char to original user input
    - If mismatch detected, agent re-reads original user input and uses correct UUID
  - Applied to: `Analyze_Job_Results.md`, `DOGME_QUICK_WORKFLOW_GUIDE.md`

- **MCP Tool Return Types – Protocol Serialization**
  - All Analyzer MCP tools were returning JSON strings via `_dumps()` instead of native Python dicts
  - FastMCP was aggressively summarizing/truncating nested responses, showing fields as `"..."` instead of values
  - Changed all 7 tools in `mcp_tools.py` to return `Dict[str, Any]` natively; removed all `_dumps()` calls
  - Updated tool decorator return types in `mcp_server.py` to declare `Dict[str, Any]` for proper protocol handling
  - Result: Response fields are no longer truncated; structured data is preserved through MCP protocol layer

- **File Discovery Response Structure – Avoiding Nested Object Truncation**
  - `find_file` response had a `matches` array with nested objects that MCP was truncating to `"..."`
  - Removed problematic `matches` array entirely from response
  - Added flat top-level fields: `paths` (simple string array), `primary_path` (first match for direct use)
  - Response now contains only: `success`, `run_uuid`, `search_term`, `file_count`, `paths`, `primary_path`
  - Eliminates confusion about which path to use and prevents field truncation

- **Missing `find_file` Tool Registration**
  - `find_file` tool was implemented in `mcp_tools.py` but not registered in `TOOL_REGISTRY`
  - Added `find_file_tool` to `TOOL_REGISTRY` and MCP server registration in `mcp_server.py`
  - Tool now available: `[[DATA_CALL: service=analyzer, tool=find_file, run_uuid=..., file_name=...]]`

- **File Path Resolution Error Messages**
  - Parse and read operations showed generic "File not found" error without diagnostic info
  - Enhanced error messages in `analysis_engine.py` for `read_file_content`, `parse_csv_file`, `parse_bed_file`
  - Error now shows both relative and absolute paths: `"File not found: {file_path} (absolute path: {full_path})"`
  - Enables rapid diagnosis of work directory or path resolution failures

### Changed
- **Enhanced UUID Validation in Analysis Skills**
  - Added explicit 36-character format validation to Analyze_Job_Results.md
  - Agent now checks UUID completeness before executing any tool calls
  - Detects and flags potential LLM truncation of UUIDs
  - Instructs agent to re-read original user input if truncation is suspected
  - Applied to: `Analyze_Job_Results.md`, `DOGME_QUICK_WORKFLOW_GUIDE.md`

- **Fixed Duplication in Dogme Skills Workflows** 
  - Realized we had re-created duplication by putting STEP 1-4 in each skill file
  - Consolidated back: All detailed workflow steps now live in `DOGME_QUICK_WORKFLOW_GUIDE.md`
  - Each skill now just references the guide with brief mode-specific tool hints
  - True single source of truth — workflow updates only need to happen once

- **Enhanced STEP 4-5 (Results Presentation) in Shared Guide**
  - Expanded with explicit ❌ anti-patterns agent should never exhibit:
    - ❌ "The query did not return expected data" (it is there in `data` field)
    - ❌ "content is not provided in usable format" (extract and format it)
    - ❌ "results show metadata but actual content..." (extract `data` field immediately)
  - Added explicit presentation formats (markdown tables for CSV, tabular for BED, text blocks)
  - Added tool selection guide: when to use parse_csv_file vs parse_bed_file vs read_file_content
  - Result: Agent knows exactly what to do when parse succeeds — extract and present

- **Auto-Load Referenced Skill Files (Generalized)**
  - Enhanced `_load_skill_text()` in `agent_engine.py` to detect and auto-load referenced .md files
  - Pattern: `[filename.md](filename.md)` automatically triggers file inclusion in skill context
  - When agent loads `run_dogme_dna`, it now gets both the skill AND the referenced `DOGME_QUICK_WORKFLOW_GUIDE.md`
  - Files are appended with clear section markers: `[INCLUDED REFERENCE: filename.md]`
  - Applies to any skill that references other markdown files using this pattern
  - Result: Agents have full context without manual file configuration

- **Consolidated Dogme Workflow Documentation**
  - Created `DOGME_QUICK_WORKFLOW_GUIDE.md` as single source of truth for all Dogme analysis workflow steps
  - Moved repetitive UUID verification (~300 lines) and directory prefix warnings from individual skills to shared guide
  - Refactored `Dogme_DNA.md` and `Dogme_RNA.md` to reference shared guide instead of duplicating sections
  - Kept mode-specific content: file types (bedMethyl/, modkit/, counts/) and interpretation guidance
  - Reduces documentation maintenance burden; all three Dogme skills now use consistent workflow reference
  - Applied to: `Dogme_DNA.md`, `Dogme_RNA.md` (cDNA already used external reference)

- **Agent Workflow Routing for Specific File Requests**
  - Updated `Analyze_Job_Results.md` with "SPECIAL CASE: User Requests Specific File Parsing" section
  - When user says "parse [filename]", analyzeJobResults now routes immediately to appropriate Dogme skill
  - Prevents verbose workflow description loops; enables fast-track execution
  - Example: "parse jamshid.mm39_final_stats.csv" → Get mode → Route to Dogme_cDNA → Execute quick workflow

- **File Path Extraction in Dogme Skills**
  - Completely rewrote "Quick Workflow: User Asks to Parse a File" section in all three Dogme skills
  - Added 5-step critical workflow with explicit copy-paste examples
  - Enhanced "MOST COMMON MISTAKE" section showing 3 specific wrong patterns:
    - ❌ `file_path=jamshid.mm39_final_stats.csv` (dropped directory part)
    - ❌ `file_path=final_stats.csv` (extracted partial filename)
    - ❌ `file_path=Annot/jamshid...` (modified case)
  - Added bold ✅ CORRECT example: use EXACT copy of `primary_path` from find_file response
  - Applied to: `Dogme_DNA.md`, `Dogme_RNA.md`, `Dogme_cDNA.md`

- **MCP Tool Return Type Declarations**
  - Updated all `@mcp.tool()` decorators in `mcp_server.py` to declare `Dict[str, Any]` return types
  - Ensures FastMCP properly recognizes and serializes dict responses without truncation
  - Affected tools: `list_job_files`, `find_file`, `read_file_content`, `parse_csv_file`, `parse_bed_file`, `get_analysis_summary`, `categorize_job_files`

### Added
- **find_file MCP Tool**
  - New tool for locating files by partial name match in job work directories
  - Parameters: `run_uuid`, `file_name` (case-insensitive substring match)
  - Returns: Structured response with flat `paths` array and `primary_path` highlighting best match
  - Purpose: Enable quick file discovery without browsing full directory listings
  - Useful when response truncation makes `list_job_files` unusable for large directories

- **Skill Routing Pattern Template**
  - Added `skills/SKILL_ROUTING_PATTERN.md` as reusable template for defining skill boundaries
  - Provides standard "Skill Scope & Routing" section structure with ✅/❌ lists and examples
  - Used as template for all Dogme skills, Analyze_Job_Results, and ENCODE skills
  - Ensures consistent agent routing behavior across all skills

- **Skill Scope & Routing Sections**
  - Added comprehensive scope definitions to all Dogme skills (DNA, RNA, cDNA)
  - Added scope definitions to `Analyze_Job_Results.md` routing skill
  - Each section includes: ✅ what skill handles, ❌ what it doesn't, 🔀 routing rules with examples
  - Clarifies mode-specific analysis vs job submission vs ENCODE data vs local samples

### Documentation
- **Enhanced UUID Guidance in All Dogme Skills**
  - Added 🚨 CRITICAL section at top of Analysis Workflow in all three Dogme skills
  - Emphasizes: "Use ONLY the MOST RECENT UUID from current conversation"
  - Includes WRONG (old UUID) vs RIGHT (current UUID) examples
  - Prevents agent from using cached UUIDs from earlier in conversation history

- **SKILL_ROUTING_PATTERN.md**
  - Comprehensive documentation of skill scope/routing architecture
  - Lists 6 core skills with their scope boundaries and routing targets
  - Provides implementation guidelines for adding routing sections to new skills
  - Key principle: "Always route rather than refusing"

---

## [Unreleased] - 2026-02-14

### Fixed
- **Results Page – MCP Response Format Alignment**
  - Fixed UI to match MCP response field names: `total_size` → `total_size_bytes` (file listing), `workflow_type` → `mode` (summary).
  - Made `file_count`, `total_size_bytes`, and `files` accesses defensive with `.get()` to prevent `KeyError` crashes.
  - Fixed `_auto_trigger_analysis` to read `mode` field correctly from MCP summary.
  - Cortex analysis proxy endpoints remain MCP-based (consistent with the rest of the architecture).

- **MCP Tool Errors Silently Swallowed**
  - `_call_analyzer_tool` and `_call_launchpad_tool` now check the `success` field in MCP responses; if `false`, they raise a proper HTTP 422 with the error message instead of returning the error dict as a 200.
  - Added fallback guard in the results page UI to detect and display any MCP error that slips through.
  - This was the root cause of the "all N/A" results page — an MCP tool error was returned as a valid 200 JSON response.

- **`datetime` Not JSON Serializable in MCP Tools**
  - `FileInfo.modified_time` (a `datetime` object) caused `json.dumps` to crash in all Analyzer MCP tools.
  - Added `_dumps()` helper in `analyzer/mcp_tools.py` with a `default` handler that converts `datetime` to ISO 8601 strings; replaced all 20+ `json.dumps` calls.

- **Skill Resets to "welcome" After Job Completion**
  - After auto-analysis created an `AGENT_PLAN` block post-job, follow-up messages (e.g. "parse qc_summary.csv") incorrectly reset skill to "welcome" because an older `APPROVAL_GATE` block existed.
  - Fixed skill resolution in `/chat` to compare sequence numbers: if the latest `AGENT_PLAN` is newer than the latest `APPROVAL_GATE`, continue with the agent's skill instead of resetting.

- **Wrong MCP Tool Names in Skill Files**
  - `Dogme_cDNA.md` and `Dogme_RNA.md` referenced `tool=parse_csv` instead of `tool=parse_csv_file` (the actual MCP tool name), causing tool-not-found errors when the LLM emitted DATA_CALL tags.

- **LLM Using Sample Name Instead of UUID for Analysis Tools**
  - Auto-analysis block didn't include the `run_uuid` in the markdown summary, so the LLM used the sample name (e.g., "Jamshid") instead of the actual UUID when calling Analyzer analysis tools.
  - Added `**Run UUID:** <backtick>uuid<backtick>` line to the auto-analysis summary.
  - Added explicit UUID-finding instructions to all three Dogme analysis skills (DNA, RNA, cDNA) with examples of how to extract the UUID from conversation history.

- **CSV/BED File Data Truncated to "..." in Results**
  - `_compact_dict` in `result_formatter.py` limited depth to 2, causing row data in parsed CSV/BED responses to show as `{"sample": "...", "n_reads": "..."}` instead of actual values.
  - Increased depth limit from 2 to 4 for analysis data (detected by presence of `columns`, `records`, or `preview_rows` keys in the response).

- **File Discovery Filtering Out Work Folder Files**
  - MCP tools in Analyzer now filter out files in the work/ and dor*/ directories to prevent bloated file counts from Nextflow intermediate artifacts.
  - Modified `discover_files()` in `analyzer/analysis_engine.py` to exclude files with paths starting with "work/" or "dor".
  - This affects all MCP tools: `get_analysis_summary`, `list_job_files`, `find_file`, and parsing tools.

### Documentation
- **Consolidated & Standardized Docs**
  - Moved various `*_IMPLEMENTATION.md` files to `archive/` to reduce clutter.
  - Standardized Atlas documentation: moved `ATLAS_IMPLEMENTATION.md` to `atlas/README.md`.
  - Consolidated Launchpad documentation: merged `FILES.md`, `DUAL_INTERFACE.md`, etc., into a single `launchpad/README.md`.

### Changed
- **UI Architecture: Cortex Abstraction Layer**
  - Refactored `ui/pages/results.py` to route all requests through Cortex proxy endpoints instead of calling Analyzer directly.
  - Added file download proxy endpoint (`/analysis/files/download`) to Cortex to stream files from Analyzer without exposing backend URLs.
  - Added `rest_url` field to Analyzer `SERVICE_REGISTRY` entry for REST-specific proxying.
  - All UI pages now exclusively communicate with Cortex; backend architecture is fully abstracted from the frontend.

- **Auto-Analysis on Job Completion**
  - When a Dogme job finishes, Cortex now automatically fetches the analysis summary from Analyzer and presents it in the chat.
  - The agent skill switches to the mode-specific analysis skill (DNA/RNA/cDNA) so follow-up questions use the right context.
  - Users see a file overview with counts and key results immediately, with suggested next steps.

- **Chat Management**
  - Added `DELETE /projects/{project_id}/blocks` endpoint to Cortex for clearing chat history.
  - Replaced the no-op "Force Clear" button with a working "Clear Chat" button that deletes all blocks from the database.
  - Added a "Refresh" button for manual reloads.
  - Long conversations now show only the last 30 messages by default, with a "Load older messages" button to page back.

### Added
- **Atlas Implementation** (2026-02-10)
  - Initial implementation of ENCODE search server
  - Integration with Cortex via MCP client
  - Result formatting for ENCODE data
  - Documentation: `ATLAS_IMPLEMENTATION.md`, `ATLAS_QUICKSTART.md`
  - Skills: ENCODE_LongRead.md, ENCODE_Search.md

- **Analyzer Implementation** (2026-02-09)
  - Initial implementation of analysis/QC server
  - Analysis engine for quality control workflows
  - MCP server interface for job result analysis
  - Database models and schemas for analysis storage
  - Documentation: `ANALYZER_IMPLEMENTATION.md`, `INSTALLATION.md`, `QUICKSTART.md`
  - Test scripts: `test_analyzer_direct.sh`, `test_analyzer_integration.sh`, `quick_qc_test.sh`
  - UI integration for displaying analysis results
  - New skill: Analyze_Job_Results.md

- **Second-Round LLM Processing** (2026-02-13)
  - Added capability for processing tool outputs with additional LLM rounds
  - Enhanced MCP client to support iterative processing
  - Improved result formatting in Atlas

- **Unified Logging System** (2026-02-13)
  - Centralized logging configuration in `common/logging_config.py`
  - Logging middleware in `common/logging_middleware.py`
  - Automatic log rotation and better log management
  - Server stopping and log rollover in `agoutic_servers.sh`

- **Multi-User Support** (2026-02-09)
  - Enhanced authentication and authorization
  - User jail/isolation functionality
  - Admin panel improvements
  - Multi-user configuration in Cortex and UI

### Changed
- **MCP Architecture Refactoring** (2026-02-11)
  - Moved MCP client code to common area (`common/mcp_client.py`)
  - Refactored Atlas, Launchpad, and Analyzer to use unified MCP interface
  - Consolidated MCP client implementations from individual servers
  - Updated Cortex agent engine to use common MCP client

- **Launchpad Job Launch Improvements** (2026-02-14)
  - Fixed job launching in Launchpad
  - Unified ENCODE and local sample intake workflows
  - Updated MCP server and tools for better job submission
  - Enhanced schema definitions for job parameters

- **Nextflow Integration** (2026-02-03, 2026-02-05)
  - Updated Nextflow executor for Launchpad
  - Enabled job submission from UI
  - Added Dogme pipeline options (DNA, RNA, cDNA)
  - Direct job submission script improvements

- **Skills Updates** (2026-02-13, 2026-02-14)
  - Updated ENCODE_Search skill with improved guidance
  - Refreshed all Dogme skills (DNA, RNA, cDNA)
  - Enhanced Welcome and Local_Sample_Intake skills
  - Updated Analyze_Job_Results skill

### Fixed
- **Environment Configuration** (2026-02-12)
  - Restored environment code in `.env` and `load_env.sh`

- **MCP for ENCODE** (2026-02-12)
  - Fixed MCP client issues in Atlas
  - Updated configuration for proper ENCODE integration
  - Updated `.gitignore` for better file management

- **Log Management** (2026-02-13)
  - Better server stopping mechanisms
  - Improved log rotation and rollover

## Summary – What Changed

**1. Make file requests actually return file contents (not just paths)**
Fixed the "parse X.csv" flow so `find_file` automatically chains into the correct reader (`parse_csv_file` / `parse_bed_file` / `read_file_content`) based on extension — regardless of whether the `find_file` call came from auto-calls or LLM DATA_CALL tags.

**2. Fix live job progress so it updates without manual refresh**
Reworked Streamlit auto-refresh so running Nextflow jobs reliably trigger reruns using `st.session_state` instead of fragile `locals()`, and stop polling when jobs finish.

**3. Add richer job progress UX**
Added runtime ("Running for…") and "last updated" timestamps. Added real-time chat progress feedback via backend status events and a polling UI widget, so long local-LLM waits show staged feedback: Thinking → Switching skill → Querying → Analyzing → Done.

**4. Prevent wasted LLM calls by switching skills before calling the model**
Added `_auto_detect_skill_switch()` to detect strong signals (local sample paths, ENCODE searches, job-result analysis) and switch skills before the LLM runs.

**5. Fix analysis summary being empty or misread**
Updated Analyzer analysis tools to return both human-friendly markdown and structured fields (`all_file_counts`, `file_summary`, `key_results`). Updated Cortex auto-analysis logic to read the correct fields and compute totals accurately.

**6. Stop the agent from re-querying data it already has**
Added an "Answer from existing data first" rule to skills. Injected `[PREVIOUS QUERY DATA:]` for follow-ups that don't introduce a new subject, with guards to prevent auto-generation from conflicting with correct answer-from-context behavior.

**7. Harden tool calling against LLM mistakes**
Added `tool_aliases` to fix hallucinated tool names inside valid DATA_CALL tags. Added robust routing correction for ENCODE file-vs-experiment accessions (ENCFF vs ENCSR), including finding missing parent experiments from conversation history, restoring mangled prefixes, and returning a clear error for cold ENCFF queries with no prior context.

**8. Preserve context across multi-turn ENCODE conversations**
Improved biosample carryover so "list them" / "give me accessions" retains the previous biosample filter (e.g., C2C12) instead of searching globally. Expanded auto-generation to recognize biosample context from history, not just ENCSR accessions.

**9. Make UI project switching stable (no ghost messages)**
Added multi-rerun grace handling, widget-key cleanup, render-time filtering by `project_id`, and container replacement to prevent old project messages from flashing after a project switch.

**10. Fix MCP protocol and serialization issues**
Returned native dicts from MCP tools (not JSON strings) to prevent FastMCP truncation. Flattened `find_file` responses by removing nested `matches` objects. Fixed `datetime` serialization and made UI field access defensive to prevent crashes and "N/A everywhere" results pages.

**11. Consolidate documentation so skills stay consistent**
Moved duplicated Dogme workflow steps into one shared guide (`DOGME_QUICK_WORKFLOW_GUIDE.md`). Added routing/scope pattern templates and corrected tool names in skill docs. Strengthened UUID handling guidance to cover both truncation and character-level corruption.

**12. Centralize backend security and routing**
Refactored the UI to route all analysis requests through a Cortex proxy layer rather than contacting Analyzer directly. This hides backend URLs and unifies authentication and logging at the gateway.

**13. Security hardening sprint**
Added role-based authorization gates (`require_project_access`, `require_run_uuid_access`) on every project- and job-scoped endpoint. Hardened `user_jail.py` with path traversal guards. Bound job ownership via `user_id` column on `dogme_jobs`. Moved project creation server-side with `uuid4()`. Environment-gated cookie `Secure` flag. Created idempotent migration script (`migrate_hardening.py`).

### Message Processing Pipeline (Points 1, 4, 6, 7)

```
User Message
     │
     ▼
┌─────────────────────────────────────┐
│         _sanitize_user_message()    │  ← normalize accessions, biosamples
└─────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│      _auto_detect_skill_switch()    │  ← switch skill BEFORE LLM call (#4)
└─────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│        _inject_job_context()        │  ← inject UUID, biosample, prev data (#6, #8)
└─────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│           LLM (engine.think())      │
└─────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│  Parse DATA_CALL tags from response │
│  → resolve_tool_name()  (#7)        │  ← alias map → fuzzy match
│  → _correct_tool_routing()  (#7)    │  ← ENCFF vs ENCSR correction
└─────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│         Execute MCP Tool Call       │
│  find_file → _pick_file_tool() (#1) │  ← auto-chain to correct parser
└─────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│    Cortex Proxy (gateway) (#12)   │  ← all backends hidden behind here
│    ├── Atlas (ENCODE)            │
│    ├── Launchpad (jobs/Nextflow)     │
│    └── Analyzer (analysis/files)    │
└─────────────────────────────────────┘
     │
     ▼
  Response to User
```

---
*Updated on February 17, 2026*
