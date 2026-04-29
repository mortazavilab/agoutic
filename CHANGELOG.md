## [Unreleased]

### Bug Fixes

- **Approval gates now default SLURM CPU memory to 64 GB while still allowing lower explicit overrides**

- **Gate-approved GPU account and partition overrides are now respected in generated `nextflow.config`**

- **The sidebar Brain Model selector now uses the backend-defined LLM alias list and caches it on UI startup, keeping the UI aligned with the current `fast`/`heavy` model choices instead of a stale hard-coded menu**

- **Local Dogme workflows now honor an approval-gate per-task CPU ceiling instead of hard-coding task CPU requests beyond the local host's available cores**

- **Local Dogme workflows now honor an approval-gate per-task RAM ceiling and default local task memory to 64 GB instead of forcing the old 96 GB minimap requirement**

- **Normal `[[PLOT:...]]` chart responses now preserve explicit literal color requests like `in red` instead of falling back to the default blue theme**

- **Nextflow live monitoring no longer freezes completed-task counts when stdout shifts to composite `executor > slurm (...)` summary lines**

- **Transient scheduler poll failures no longer pin execution cards to an old completed count indefinitely**

## [3.6.6] - 2026-04-22

### Bug Fixes

- **The Streamlit UI now closes short-lived API responses eagerly to avoid `Too many open files` under rerun-heavy polling paths**

- **The Streamlit auth helper now routes its direct `/auth/me` and `/auth/logout` calls through the same eager-close response path as the rest of the UI**

- **Project pages with rendered figures no longer leak Streamlit threads and file descriptors through the publication controls path**

- **Switching into an existing project with saved figures now renders chats and plots on the first visit**

## [3.6.5] - 2026-04-21

### Features

- **Fiber-seq open-chromatin comparisons can now build overlap dataframes and auto-render 2-set Venn diagrams across project folders**

- **Cross-workflow open-chromatin overlap requests can now run as monitored background script workflows**

- **Cross-project open-chromatin Venn requests now understand `project_slug:workflowN` shorthand and route through the result-analysis skills automatically**

- **DE plot artifacts now default to publication-quality raster output and ship an SVG companion**

- **DE publication plots can now include transcript labels on request and keep legends off the data region**

- **Heatmap, PCA, and publication-bar requests now share one routed plotting contract from chat through edgePython**

- **Interactive Plotly charts and edgePython artifacts now share AGOUTIC's light publication-style defaults**

### Bug Fixes

- **Two-set overlap venn diagrams now render full-count regions with readable geometry and labels**

- **Full DE compare requests now default to `p-value < 0.05` consistently across summaries, top-gene extraction, and the default volcano plot**

- **DE interpretation now runs GO enrichment separately for up- and down-regulated genes and returns two workflow tables with GO namespace labels**

- **Parsed dataframe previews stay lightweight while full-data consumers now rehydrate the real table on demand**

- **UpSet plots now show intersection counts above each bar and the UI rehydrates truncated plot dataframes without a backend import dependency**

- **Cross-project overlap venn requests now stay on the script-backed workflow path end-to-end**

- **Overlap venn/upset approvals now expose an explicit plot title and parse title phrases separately from sample labels**

## [3.6.4] - 2026-04-18

### Features

- **SLURM DNA runs now use the shared Dogme OpenChromatin GPU container with task-scoped runtime wiring**

- **Failed remote staging cards can now delete the reserved local workflow folder directly from the UI**

### Bug Fixes

- **Remote SLURM defaults lookup now repairs legacy `profile_id` tool calls before they reach Launchpad**

- **DNA approval gates no longer show obsolete custom cluster-modkit controls**

- **OpenChromatin SLURM tasks now survive Nextflow parsing, resolve libtorch correctly, and force GPU selection reliably**

- **Approval-gate GPU account and partition overrides now survive Launchpad submission end to end**

- **SLURM CPU defaults now stay on the intended controller resources instead of falling back to undersized legacy values**

- **Approval-gate CPU and GPU SLURM routing now stay separated during Cortex submission**

- **Filterbed default filtering is now pinned to a 3-read minimum and 5 percent threshold**

- **Remote staging now surfaces byte-level transfer updates in the UI**

- **Broker-backed staging now reports live progress and no longer treats NFS placeholder files as stale cache corruption**

- **Running staging cards now refresh often enough to actually show live transfer progress**

- **Large brokered rsync transfers no longer fail after the copy finishes just because the broker response is too large**

- **Resumed stage-only transfers now show Launchpad's real queued state immediately instead of pretending rsync is already running**

- **Large rsync staging transfers no longer false-stall on carriage-return progress updates**

- **Large broker-backed output downloads no longer time out after 3600 seconds by default**

### Tests

- **Custom `dogme.profile` runtime override regression coverage**

- **Staging transfer byte-progress regression coverage**

- **Broker progress and NFS-resume regression coverage**

- **Running staging UI refresh regression coverage**

- **Broker large-response regression coverage**

- **Stage-only resume status regression coverage**

- **Rsync progress watchdog regression coverage**

- **Brokered output-download timeout regression coverage**

## [3.6.3] - 2026-04-13

### Features

- **GTF-backed gene and transcript annotation with colocated caches**

- **DE and analyzer annotation paths now prefer workflow-local `reconciled.gtf` when present**

- **Live remote staging can now be cancelled from the UI and API**

- **Stage-only transfers can now be resumed from the UI and API**

- **Stage transfers can now be refreshed, cancelled, and resumed from the Task Center**

### Bug Fixes

- **Server startup no longer emits a `runpy` warning while checking shared GTF caches**

- **Remote rsync staging now preserves partial transfers safely and rejects stale remote cache reuse**

- **Broker-backed staging transfers now fail fast when rsync goes silent**

### Tests

- **GTF parser and annotation regression coverage**

- **Remote staging cancellation and rsync-hardening regression coverage**

- **Staging resume and broker-watchdog regression coverage**

### Documentation

- **Remote staging troubleshooting notes corrected for current rsync behavior**

## [3.6.2] - 2026-04-11

### Features

- **Manifest-driven planning through phase 6**

- **Planner/executor service-aware manifest gating**

- **YAML-first skill manifest migration**

- **Deterministic skill-management commands**

- **Slash-command discovery surfaced in the UI and quick reference**

- **IGVF downloads now follow the same approval-gated project save flow as ENCODE downloads**

### Bug Fixes

- **Workflow Results attachment no longer leaks across unrelated responses**

- **IGVF accessions and dataset file rows now survive result formatting and DataFrame extraction**

- **IGVF dataset and file lookups now recover from hallucinated parameter names**

- **IGVF download prompts no longer fall through analyzer file aliases**

### Tests

- **Manifest planning regression suite**

- **Skill command and YAML manifest regression coverage**

- **IGVF parameter-repair regression coverage**

- **IGVF download-routing regression coverage**

### Documentation

- **Planning and skills docs refreshed for manifest-first composition**

- **Skills authoring docs updated for YAML-local manifests and command discovery**

- **Command reference and in-app help refreshed**

---

## [3.6.1] - 2026-04-11

### Features

- **Cross-project Task Center**

### Bug Fixes

- **IGVF required-parameter repair in chat dispatch**

- **IGVF tool routing and alias resolution hardening**

- **Remote staging tasks now survive Launchpad restarts**

- **Dangling task cleanup and stale-task demotion**

### Documentation

- **Top-level docs/version alignment**

---

## [3.6.0] - 2026-04-10

### Features

- **IGVF Data Portal MCP server**

- **IGVF plain-language routing**

### Bug Fixes

- **IGVF negative limit guard**

- **IGVF gene search exact-match priority**

### Tests

- **IGVF plain-language routing tests**

- **IGVF Q&A validation tests**

- **Comprehensive IGVF test suite**

---

## [3.5.3] - 2026-04-10

### Improvements

- **Sync, browsing, and user-data prompts now bypass the wasted first LLM pass**

- **Long-running jobs now keep user sessions alive more reliably**

- **Dogme GPU task concurrency now defaults to no explicit Nextflow cap**

- **SLURM walltime defaults and limits raised to 48-72 hours**

- **Grouped edgePython differential expression now runs directly from reconciled abundance outputs and saved dataframes**

- **Differential-expression histories now capture comparisons, result files, and volcano plots directly into project memory, and workflow results can show the volcano plot inline**

- **The UI help surface now documents grouped DE prompts directly**

### Bug Fixes

- **Reconcile BAM preflight now treats matching remote Watson GTF paths as the local canonical reference**

- **Reconcile approvals no longer default to all CPU cores, and auth timeouts no longer masquerade as global logout**

- **Project task lists now isolate the active workflow instead of mixing in older runs**

- **Analyzer and project detail queries no longer crash when workflow identity migration has not yet been applied**

- **Reconcile plan resolves bare workflow folder names to absolute paths**

- **Workflow result sync now resolves the right run, probes the right remote location, and surfaces actionable failures**

- **Remote SLURM intake now creates an approval gate for new submissions even when profile defaults are incomplete**

- **Remote SLURM live status now reports scheduler concurrency and pipeline totals more accurately**

- **Cross-workflow reconcile now resolves owner paths correctly and no longer hard-fails when a workflow lacks an `annot/` directory**

- **Reconcile-driven DE follow-ups now keep using the selected workflow and no longer stop on a missing prior `de_results` folder**

- **Each grouped DE compare now gets its own fresh `workflowN` instead of reusing the source workflow directory**

- **Workflow switching now persists across turns and no longer reuses corrupted work directories**

---

## [3.5.2] - 2026-04-06

### Improvements

- **Remote staging is now a durable background job**

- **Idle/stall timeout replaces the hard rsync timeout**

- **Retry endpoint for failed staging tasks**

- **Staging poller hardening**

- **Remote SLURM runs can now use a folder already present on the cluster**

### Bug Fixes

- **Remote-input staging approvals now preserve no-copy remote folder reuse**

- **Async remote staging worker now uses the live SLURM backend API**

- **GRCh38 jobs now emit human kallisto references**

---

## [3.5.1] - 2026-04-02

### Improvements

- **Remote stage uploads now fail before the outer MCP request times out**

- **Remote SLURM rsync now skips wasteful compression for BAM-class binaries**

- **Explicit pending dataframe action controls**

- **Broader dataframe intent auto-routing**

- **Date-organised download folders**

- **Plot metadata now persists as project memory**

### Docs

- **UI help now teaches dataframe workflows**

- **New dataframe guide**

### Bug Fixes

- **Wide dataframe plotting respects explicit `by measure` requests**

- **Mistral inline plot tags no longer leak into chat output**

- **Plot titles and axis labels survive end-to-end rendering**

- **Skill switch blocked by unrelated running jobs**

- **"List files" picks reconcile script path instead of workflow dir**

---

## [3.5.0] - 2026-04-02

### Refactors

- **Chat pipeline decomposition**

- **ChatContext dataclass**

- **ChatStage protocol**

- **15 stages across 11 modules** under `cortex/chat_stages/`: Setup (100), Capabilities (200), Prompt Inspection (210), DataFrame Command (220), Memory Command (230), Memory Intent (240), History (300), Context Preparation (400), Plan Detection (500), First-Pass LLM (600), Tag Parsing (700), Overrides (800), Tool Execution (900), Second-Pass LLM (1000), Response Assembly (1100).

- **Pipeline runner**

- **Priority gap convention**

### Features

- **Unified token/context budget**

- **Skill capability manifest**

- **Project rename syncs local folder**

- **Slug displayed in sidebar and toasts**

- **Rename audit trail via memory**

- **Project creation returns full slug**

### Bug Fixes

- **`_suppress_all_tags` ordering**

- **Script-result file access now resolves workflow outputs correctly**

- **Script completion preserves actual output work directory**

- **Chat timestamps render in local time again**

### Docs

- **`docs/CHAT_PIPELINE.md` created**

## [3.4.17] - 2026-04-01

### Features

- **Dual-scope memory system**

- **Chat-line memory CRUD via slash commands**

- **Natural language memory intent detection**

- **Sample metadata annotation**

- **Auto-capture of pipeline steps**

- **Auto-capture of job results**

- **Dynamic memory context injection**

- **Memory-aware system prompt**

- **REST API for memories**

- **Memories UI page**

- **Sidebar memory widget**

- **Dataframe memory**

- **Named-DF keying**

- **Upgrade project memory to global**

- **Memories UI overhaul**

- **Context injection for remembered DFs**

- **`head <name>` command**

- **`/remember-df` slash command**

### Database

- **Alembic migration `a1b2c3d4e5f6`**

### Tests

- **73 tests** in `tests/test_memory.py`

### Docs

- **`docs/MEMORY.md` created**

- **`QUICK_REFERENCE.md` restructured**

- **Sidebar Help expander**

## [3.4.16] - 2026-04-01

### Refactors

- **Planner decomposition**

- **App chat-helper extraction**

### Fixes

- **Tool schema cache no longer permanently empty after startup**

- **`search_data` tool alias added for ENCODE**

- **Log rotation now archives old logs into monthly folders**

- **Tool schema retry no longer shadows module-level imports**

## [3.4.15] - 2026-03-31

### Features

- **UI chat renderer modularization under strict file-size limits**

- **AppUI consolidation cleanup**

### Fixes

- **Manual result sync timeout raised for very large file copy-back**

- **Sync-result run UUID payload is now always safely bound**

- **Post-split AppUI helper tests now resolve extracted functions correctly**

- **Launchpad status fast path now tolerates backends without transfer-detail helpers**

- **Reconcile wrapper JSON output remains parseable during live child-process streaming**

- **Script-backed reconcile runs are now labeled correctly in the UI**

- **UI execution timestamps now consistently render in local time**

- **Import-time session-state crash resolved in split block renderer**

- **Missing renderer callback wiring fixed**

- **Raw fallback payload no longer shown for handled user blocks**

## [3.4.14] - 2026-03-31

### Features

- **Safe project rename semantics (display-name first)**

- **Project rename API response now returns canonical name**

- **Extended timeout control for manual result sync**

### Fixes

- **Owner-scoped duplicate project-name conflicts now rejected cleanly**

- **Project access labels now stay consistent after rename**

- **UI rename feedback is now actionable**

- **Remote result copy-back now materializes symlink targets**

- **Manual sync timeout failures now return actionable guidance**

- **Sync commands no longer trigger dogme pipeline gate**

- **SLURM task completion now tracked correctly**

- **UI timestamps now display in local time**

- **Non-blocking result sync prevents empty-folder copy-back**

### Tests

- Expanded Cortex project rename coverage in `tests/cortex/test_project_endpoints.py` for: successful rename with stable slug-by-default behavior, duplicate-name rejection (`409`), unauthorized rename rejection (`403`), project ID stability, and post-rename block accessibility.

- Added project-files regression in `tests/cortex/test_project_management.py` to verify files remain accessible by existing `project_id` after rename.

- Updated Launchpad transfer expectation in `tests/launchpad/test_file_transfer.py` for `copy_links=True` download mode.

- Added sync timeout/read-timeout behavior tests in `tests/launchpad/test_mcp_tools.py` for `LAUNCHPAD_SYNC_TIMEOUT` and actionable timeout messaging.

- Regression test coverage updates

## [3.4.13] - 2026-03-28

### Features

- **Cross-project reconcile syntax support (`project:workflow`)**

- **Manual SLURM result copy-back retry command**

- **Phase 1 XgenePy service integration**

- **Planner, skill, and approval wiring for XgenePy**

- **Analyzer parser support for XgenePy canonical outputs**

- **Real-time rsync transfer progress**

### Fixes

- **SLURM task completion now tracked correctly**

- **SLURM result copy-back now works on external/mounted filesystems**

- **Broker rsync downloads no longer strip trailing slash from remote source**

- **rsync transfer failures now surface the actual error**

- **Manual sync behavior now returns structured non-SLURM responses**

- **Manual sync failure messages now include actionable details**

- **Chat queries on old projects no longer redirect to the latest project**

- **Sidebar project switch now updates server-side last-project**

- **"List workflows" now uses the correct project directory**

- **Cross-project reconcile plans now resolve absolute workflow paths**

- **Reconcile output directory defaults to the active project**

- **"Try again" / "retry" command replays the last failed prompt**

- **BAM-detail fallback now prefers workflow/work_dir discovery over blind run UUID dispatch**

- **Analyzer param resolution now blocks non-run UUID forwarding for BAM detail requests**

- **Workflow discovery calls are now explicitly allowed during BAM fallback**

### Tests

- Regression test coverage updates

- Added `test_local_broker_rsync_download_preserves_remote_trailing_slash` to verify the broker does not strip trailing slashes from remote rsync sources.

- Updated `test_copy_selected_results_accepts_verified_local_artifacts_after_rsync_warning` → renamed to `test_copy_selected_results_fails_fast_on_rsync_error` to reflect the new fail-fast behavior when rsync reports failure.

- Added `transfer_detail` to fake backend in `test_get_job_status_repolls_completed_slurm_job_while_local_copyback_pending`.

- Regression test coverage updates

- Regression test coverage updates

## [3.4.12] - 2026-03-28

### Fixes

- **BAM-detail requests now use supported Analyzer tool flow instead of unknown-tool failures**

- **`show_bam_details` compatibility path now routes safely to file listing**

- **Analyze-job-results guidance now explicitly enforces supported BAM fallback behavior**

### Tests

- Added focused regression coverage for BAM-detail fallback auto-generation in `tests/cortex/test_auto_generate_data.py`.

- Regression test coverage updates

## [3.4.11] - 2026-03-28

### Security

- **User-controlled file references are now strictly relative-only across cross-project and analyzer proxy endpoints**

- **Cross-project staging now supports additive logical file references with strict ambiguity handling**

- **User-data responses no longer leak absolute filesystem paths**

### Tests

- Added focused regressions for analyzer proxy path rejection, cross-project logical-reference resolution and ambiguity semantics, and user-data no-absolute-path response guarantees.

- Regression test coverage updates

## [3.4.10] - 2026-03-28

### Improvements

- **Cross-project routes are now wired into Cortex app routing**

- **Cross-project API schema contracts are now explicitly defined**

- **Default cross-project scan behavior is now bounded for large trees**

### Fixes

- **Project-stats regression in test-only SQLite fixtures is quarantined at the fixture layer**

### Tests

- Added focused cross-project coverage for router reachability, schema import validity, and default early-stop behavior in browse/search with correct truncation semantics.

- Verified targeted regression set: `tests/cortex/test_cross_project_browser.py`, `tests/cortex/test_cross_project_analysis_action.py`, `tests/cortex/test_project_management.py::TestProjectStats::test_stats_basic`, and `tests/cortex/test_project_endpoints.py`.

## [3.4.9] - 2026-03-26

### Fixes

- **Reconcile execution now runs the immutable `reconcileBams.py` workflow in a standard `workflowN` directory**

- **Reconcile approval editing now lives on the active Streamlit UI entrypoint**

- **Reconcile approvals now preserve the real script contract through submission**

- **Shared environment metadata now declares the reconcile runtime dependency**

- **Reconcile workflow approvals now use plan-aware script review instead of generic Dogme job forms**

- **Plan-owned reconcile script execution now advances cleanly after approval**

- **Script-backed workflow plans now persist and complete their run step correctly**

- **Approval-time SLURM safety checks now block mode/path drift**

- **Stale staged-sample metadata is no longer reused for remote cache hits**

- **Empty-input fingerprints cannot trigger remote data-cache reuse**

- **Remote workflow recovery now avoids duplicate workflow-plan creation**

- **SLURM stage/run transition deadlock hardening in submission flow**

- **Parameter extraction now preserves explicit local intent and absolute paths**

### Tests

- Updated reconcile script regressions in `tests/skills/test_reconcile_bams_scripts.py` to cover workflow-config GTF resolution, `workflowN` root-output layout, and the real `reconcileBams.py` execution path when `pysam` is available, while skipping BAM-construction fixtures cleanly in environments where that compiled dependency is missing.

- Updated reconcile submission regressions in `tests/cortex/test_background_tasks.py` to assert that approval-time edits are converted into the expected wrapper/script argument set before Launchpad submission.

- Added focused reconcile approval lifecycle regressions in `tests/cortex/test_background_tasks.py` covering: reconcile-specific approval-gate payload generation from preflight output, script submission updating the `RUN_SCRIPT` plan step, and job-polling completion that closes the reconcile run step and resumes the plan.

- Added/extended focused remote orchestration and submission regressions in `tests/cortex/test_background_tasks.py` for: empty-fingerprint staged-sample invalidation, explicit source-mismatch invalidation, active workflow reuse without duplication, requested execution-mode mismatch rejection, and no-`run_uuid` failure-state propagation.

- Added extraction regressions in `tests/cortex/test_extract_params.py` for explicit-local override behavior and absolute BAM path preservation.

## [3.4.8] - 2026-03-26

### Improvements

- **New `reconcile_bams` skill registration and routing**

- **Deterministic reconcile planning flow with preflight-before-approval**

- **Workflow-reference helper for Nextflow configs**

- **Reconcile script now executes real workflow-scoped preparation and run**

- **Manual-GTF follow-up pause semantics in replanner**

### Tests

- Added focused reconcile script tests in `tests/skills/test_reconcile_bams_scripts.py` covering: filename contract rejection, mixed-reference rejection, default-GTF success, manual-GTF-needed follow-up state, and manual-GTF preflight success.

- Added reconcile planner tests in `tests/cortex/test_planner_cache_steps.py` for plan-type detection, helper/preflight-before-approval ordering, and annotation-GTF parameter extraction.

- Added reconcile replanner extraction coverage in `tests/cortex/test_plan_replanner_reconcile.py`.

## [3.4.7] - 2026-03-24

### Improvements

- **Executor parallel-batch bridge for safe step kinds**

- **Deterministic persistence ordering for parallel batches**

- **Hybrid-first planner bridge for six non-core flows**

- **Plan-type mismatch safety in hybrid bridge**

- **Strict pre-dispatch plan contract validation**

- **Hybrid fragment-plan composition for planner output**

- **Project-scoped plan metadata continuity**

- **`chat_with_agent` decomposition**

- **Automatic skill-script allowlist discovery**

- **Native Apptainer path for SLURM Nextflow runs**

- **Shared per-user Apptainer cache for remote workflows**

- **BED chromosome-count utility now emits plot-ready dataframes**

- **Modification-by-chromosome planning from workflow `bedMethyl/` outputs**

- **Allowlisted script results now flow into chat dataframes**

- **Smarter fallback charts for BED chromosome-count dataframes**

### Fixes

- **Workflow `GENERATE_PLOT` and `INTERPRET_RESULTS` steps now complete deterministically**

- **Workflow outputs now surface in both step detail and the main chat flow**

- **Nextflow live status refresh restored without reintroducing full-page flicker everywhere**

- **Execution-job freshness indicators now reflect real poll timestamps**

- **Delete/archive confirmations no longer fight the auto-refresh loop**

- **Summarize-results requests now enter the planning path**

- **`PARSE_OUTPUT_FILE` now derives concrete CSV parse targets from prior inventory results**

- **Summarize/parse templates now scope initial discovery to result CSVs**

- **Workflow-plan chat panel now documents execution lifecycle and step outcomes**

- **Decomposition test boundary updates**

- **Follow-up regression hardening for extracted/runtime boundaries**

- **Apptainer compatibility bootstrap now works in mixed-runtime clusters**

- **Remote SLURM submit path restored after cache-root wiring**

- **Remote staging now supports single-file uploads**

- **Workflow-relative BED script chaining now resolves against the workflow directory**

- **BED counting no longer passes invalid script working directories**

- **Deferred plotting language no longer triggers immediate chart rendering**

### Tests

- Added focused plan-contract coverage for duplicate IDs, unknown kinds, dependency integrity, cycle detection, approval-policy constraints, provenance validation, and project-scope mismatch handling.

- Added executor pre-dispatch rejection tests for invalid plans and mismatched project scope.

- Extended Launchpad SLURM regression coverage for native Apptainer config generation, shared remote cache directory propagation, mixed-runtime sbatch bootstrap shims, and the remote-config submit path used by approval-driven job launches.

- Added planner fragment-composition tests for deterministic ID remap, dependency rewrites, dangling-fragment reference rejection, and LLM fragment payload composition paths.

- Added concurrency integration tests validating parallel execution isolation across different projects and across multiple plan instances in the same project.

- Added focused hybrid-bridge tests in `tests/cortex/test_planner_hybrid_bridge.py` covering per-flow hybrid-first behavior, explicit fallback triggers, plan-type mismatch fallback, and core deterministic flow non-regression.

- Added focused executor batch regressions in `tests/cortex/test_plan_executor_concurrency.py` for safe-kind parallelism, dependency gating, sequential fallback behavior, failure-path continuity, approval pause semantics, isolation guarantees, and deterministic persistence ordering.

- Added regressions covering summarize-results multi-step classification and `PARSE_OUTPUT_FILE` reuse of discovered CSV outputs from the prior locate step.

- Added workflow executor regression coverage for parse → plot → interpret chains, including inline plot-payload generation and deterministic interpretation text.

- Expanded UI helper coverage for workflow-result surfacing, selective full-refresh detection, live job timestamp preservation, refresh bootstrap state transitions, and destructive-action refresh suppression behavior.

- Added focused regressions for workflow-relative BED chaining, modification-count auto-planning from `bedMethyl/`, JSON dataframe parsing from `run_allowlisted_script`, deferred-vs-immediate plot intent detection, and specialized fallback plot specs for BED chromosome-count dataframes.

- Added transfer regressions covering single-file rsync uploads in both the direct file-transfer path and the local-auth broker path used by remote staging on locked key-file SSH profiles.

## [3.4.6] - 2026-03-23

### Improvements

- **RUN_SCRIPT workflow step support**

- **Launchpad `/jobs/submit` script mode (local-only)**

- **Explicit allowlist-based script resolution (deny by default)**

- **No repository auto-discovery for runnable scripts**

- **Structured local script monitoring and status**

### Fixes

- **Cortex submission glue for script mode**

- **Cortex submission boundary extraction**

- **Removed temporary submit compatibility export surface**

- **Final workflow-submission app bridge removed**

- **Owned extraction boundary created**

- **Owned polling boundary created**

- **Skills markdown ownership layout refactor**

- **MCP submission parity for script-mode payloads**

### Tests

- Added focused regression tests for: - `RUN_SCRIPT` step dispatch and approval-gated special handling - script-mode schema validation constraints - allowlist/path/args validation helpers - Launchpad script submit success and failure paths - MCP passthrough compatibility for script-mode fields - submit-handler extraction boundary stability via focused background-task/approval tests plus broader Cortex endpoint/chat and Launchpad status/submit suites - final workflow-submission bridge removal stability via compile validation, focused submit/approval/extract suites, broader chat-data-call boundaries, and polling-focused subsets on the new ownership modules - skills-layout boundary stability via compile validation, focused `test_agent_engine`/`test_plan_chains`/`test_skill_detection`, stale flat-path audits (active surfaces), and artifact hygiene checks

## [3.4.5] - 2026-03-23

### Fixes

- **Removed temporary remote-orchestration compatibility bridge**

- **Approval-context extraction dependency now injected at call site**

- **Approval-context defaults continuity when profile enrichment is degraded**

### Tests

- Updated monkeypatch boundaries in remote-execution tests to target runtime call paths correctly (`cortex.app.*` for app-owned call sites and `cortex.remote_orchestration.*` for extracted internals).

## [3.4.4] - 2026-03-21

### Improvements

- **Streamlit UI redesign foundation**

- **Chat block presentation and hierarchy refresh**

- **Approval gate review clarity**

- **Results and Projects UX polish**

- **Page-level consistency pass**

### Fixes

- **Projects → Results run selection handoff**

## [3.4.3] - 2026-03-19

### Fixes

- **Broker-backed remote result copy-back**

- **Selective local result sync for remote workflows**

- **Fail-closed copy-back before local completion**

- **Auto-analysis now waits for copied results**

- **Terminal SLURM status stability in the UI**

- **Accurate live-update timing in job cards**

### Tests

- Regression test coverage updates

## [3.4.2] - 2026-03-18

### Fixes

- **Remote reference-path correctness for SLURM configs**

- **Workflow-local input linking for remote runs**

- **Correct Dogme launch target in SLURM scripts**

- **Cluster-agnostic runtime bootstrap for sbatch scripts**

- **Sbatch script generation stability**

- **Improved remote failure surfacing**

- **Launchpad debug endpoint visibility**

- **Deterministic SLURM submit bootstrap**

- **Preserved scheduler failure details**

- **Remote container bind-path correctness**

- **Remote reference sidecar overrides**

- **CPU-only Nextflow controller jobs**

- **Remote Dogme profile staging**

- **Mode-aware remote reference asset verification**

- **Stage-only approval routing correctness**

- **Saved SSH-profile defaults now survive remote-stage approval**

- **Fail-closed remote reference refresh verification**

- **Long-running remote stage timeout support**

- **Broker timeout and diagnostics coverage**

- **Slow-HPC SSH connection test support**

- **Elapsed-time feedback in Remote Profiles UI**

- **Remote defaults-to-submit approval continuity**

- **Launchpad remote-call context injection**

- **Provenance row-count correctness for dict-style success payloads**

### Tests

- Regression test coverage updates

### Documentation

- Added a dedicated analysis narrative in `README.md` that explains what happens after pipeline execution, including analysis goals, supported analysis types, typical analysis flow, inputs/outputs, visualization support, and analysis limitations.

- Clarified analysis constraints across user docs: remote-only results must be copied back locally before Analyzer-based downstream interpretation.

## [3.4.1] - 2026-03-17

- **Per-user cross-project SLURM cache reuse**

- **Remote cache persistence**

- **Remote base path layout for SLURM runs**

- **Stage-only remote sample intake**

- **Remote staged-sample reuse by name**

- **Remote file browsing**

- **Planner-native remote stage workflow**

- **My Data filesystem visibility**

- **My Data path clarity**

### Improvements

- **SLURM approval reuse UX**

- **Local and remote workflow numbering alignment**

- **Approval and extraction clarity for remote runs**

- **Profile and UI simplification**

- **Workflow planning visibility**

- **Safer SSH profile create errors**

- **Remote cache preflight behavior**

- **Remote staging task UX**

- **Split staging progress visibility**

- **Generic remote browsing prompts**

- **Deterministic remote browse execution**

- **Remote browse result rendering**

### Fixes

- **Execution mode validation**

- **Backend routing safeguards**

- **Launchpad submission consistency**

- **Remote stage plan execution**

- **Workflow step state refresh**

- **Stage-only error routing**

- **Server-side stage error clarity**

- **Brokered transfer auth parity**

- **Brokered auth reuse for remote browsing**

- **Local auth runtime hardening**

- **Remote browse subpath resolution**

- **Remote browse output labeling**

### API

- Added `POST /remote/stage` for stage-only remote staging.

- Added `GET /remote/files` for browsing remote directories via a saved SSH profile.

- Updated job submission and MCP tool payloads to use `remote_base_path` and optional `staged_remote_input_path` reuse.

### Database

- Added new Alembic migrations for: - remote staging cache tables - cache status/path columns on `dogme_jobs` - `cache_preflight_json` on `dogme_jobs` - default remote cache roots on `ssh_profiles`

- Added Alembic migration `6d2c9a4f1b7e` to consolidate old remote path fields on `ssh_profiles` into a single `remote_base_path`.

- Added Alembic migration `1a7c0b4d2e9f` to create `remote_staged_samples` for persistent staged-sample tracking and reuse.

### Tests

- Regression test coverage updates

- Regression test coverage updates

## [3.4.0] - 2026-03-16

### Features

- **Remote SLURM execution (Phase 1)**

- **Execution backend abstraction**

- **SSH connection profiles**

- **SLURM resource management**

- **Remote path configuration**

- **Result destination policy**

- **13-stage run tracking**

- **SLURM scheduler integration**

- **Staged approval prompts**

- **File transfer**

- **Run audit logging**

- **User execution preferences**

### Database

- New tables: `ssh_profiles`, `slurm_defaults`, `remote_path_configs`, `run_audit_logs`, `user_execution_preferences`

- Extended `dogme_jobs` with 16 new columns: `execution_mode`, `ssh_profile_id`, `slurm_job_id`, `slurm_state`, `slurm_account`, `slurm_partition`, `slurm_cpus`, `slurm_memory_gb`, `slurm_walltime`, `slurm_gpus`, `slurm_gpu_type`, `remote_work_dir`, `remote_output_dir`, `result_destination`, `transfer_state`, `run_stage`

- Alembic migration `b95a2c38062c` with full upgrade/downgrade

- Alembic migration `9e12f5bb7e6e` adds `local_username` to `ssh_profiles`

- Alembic migration `c7a2e7b6b2df` adds per-profile SLURM/path defaults to `ssh_profiles`

### API

- SSH profile CRUD: `POST/GET/PUT/DELETE /ssh-profiles`, `POST /ssh-profiles/{id}/test`

- Test endpoint accepts transient `local_password` in body to unlock a per-session local auth broker when needed

- New local auth session endpoints: `GET/POST/DELETE /ssh-profiles/{id}/auth-session`

- UI includes `X-Internal-Secret` header for Launchpad service-to-service auth

- New MCP tools: `list_ssh_profiles`, `test_ssh_connection`, `get_slurm_defaults`, `cancel_slurm_job`

### UI

- SSH profile manager page

- SLURM resource form component

- Remote paths form component

- Enhanced run status panel with stage emojis

### Cortex

- `Remote_Execution` skill registered in skills registry

- `ConversationState` extended with `execution_mode`, `ssh_profile_id`, `ssh_profile_nickname`, `slurm_resources`, `remote_paths`, `result_destination`

### Documentation

- `docs/remote_execution_architecture.md`

- `docs/ssh_profile_security.md`

- `docs/cluster_slurm_setup.md`

- `docs/troubleshooting_remote.md`

- `docs/user_guide_execution_modes.md`

- README updated with execution modes section

### Tests

- `tests/test_slurm_backend.py`

- `tests/test_ssh_profile.py`

- `tests/test_remote_paths.py`

### Notes

- Phase 1 limitation: Analyzer operates on local-accessible files only; remote results must be copied back before downstream analysis

- All new DogmeJob columns are nullable with sensible defaults; existing local runs are completely unaffected

- `asyncssh` required for remote execution

## [3.3.2] - 2026-03-15

### Bug Fixes

- **ENCODE assay alias resolution in main tag-parsing path**

### Changes

- `cortex/encode_helpers.py`

## [3.3.1] - 2026-03-15

### Bug Fixes

- **ENCODE search retry now handles list results**

- **Compound query relaxation before tool swap**

### Changes

- `cortex/app.py`

- `tests/cortex/test_chat_data_calls.py`

## [3.3.0] - 2026-03-14

### Features

- **Centralized database infrastructure**

- **Alembic database migrations**

- **Gene annotation & enrichment tools moved to Analyzer**

- **Per-conversation enrichment state**

- **Simplified enrichment API**

### Changes

- `common/database.py`

- `common/__init__.py`

- `cortex/models.py`, `launchpad/models.py`, `analyzer/models.py`

- `analyzer/models.py`

- `cortex/config.py`, `launchpad/config.py`, `analyzer/config.py`

- `cortex/db.py`, `launchpad/db.py`, `analyzer/db.py`

- `cortex/dependencies.py`

- `cortex/app.py`

- `launchpad/app.py`

- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`

- `environment.yml`

- `analyzer/gene_tools.py`

- `analyzer/enrichment_engine.py`

- `analyzer/mcp_tools.py`

- `analyzer/mcp_server.py`

- `cortex/config.py`

- `skills/Enrichment_Analysis.md`

- `skills/Differential_Expression.md`

- `edgepython_mcp/edgepython_server.py`

- `edgepython_mcp/tool_schemas.py`

- 23 test files

## [3.2.11] - 2026-03-13

### Features

- **GO & pathway enrichment analysis**

- **Enrichment visualizations**

- **Enrichment plan template**

- **Enrichment skill routing**

- **Enrichment auto-generation**

### Changes

- `edgepython_mcp/edgepython_server.py`

- `edgepython_mcp/tool_schemas.py`

- `skills/Enrichment_Analysis.md`

- `cortex/config.py`

- `cortex/plan_executor.py`

- `cortex/planner.py`

- `cortex/prompt_templates/planning_system_prompt.md`

- `cortex/app.py`

- `cortex/data_call_generator.py`

- `cortex/plan_chains.py`

- `cortex/llm_validators.py`

- `skills/Welcome.md`

- `skills/Differential_Expression.md`

## [3.2.10] - 2026-03-13

### Bug Fixes

- **Gene lookup routing & parsing pipeline**

- **Pre-LLM auto-skill detection for gene queries**

- **DATA_CALL regex fix for JSON arrays**

- **Bracket-aware param parser**

- **Mistral `[TOOL_CALLS]DATA_CALL:` fallback**

- **`lookup_gene` JSON string coercion**

### Changes

- `cortex/app.py`

- `cortex/llm_validators.py`

- `edgepython_mcp/edgepython_server.py`

- `skills/ENCODE_Search.md`

- `skills/Welcome.md`

## [3.2.9] - 2026-03-13

### Features

- **Skill-defined plan chains**

- **Comprehensive skills system documentation**

- **Multi-phrasing query support**

- **Three-layer plot guarantee**

- **Second-pass PLOT instructions**

- **Literal plot colors and palette override**

- **Search+plot safety net for new ENCODE subjects**

- **Same-turn plot rebinding for newly created dataframes**

- **DF inspection quick commands**

### Bug Fixes

- **Plot spec deduplication and intent-based selection**

### New Files

- `cortex/plan_chains.py`

- `SKILLS.md`

### Changes

- `cortex/planner.py`

- `cortex/app.py`

- `cortex/prompt_templates/second_pass_system_prompt.md`

- `skills/ENCODE_Search.md`

- `ui/appUI.py`

- `tests/ui/test_app_source_helpers.py`

- `tests/cortex/test_plan_chains.py`

- `tests/cortex/test_chat_data_calls.py`

- `tests/cortex/test_auto_generate_data.py`

## [3.2.8] - 2026-03-12

### Features

- **Bidirectional gene lookup**

- **Reverse gene ID translation**

- **Tool alias safety net for gene tools**

- **Cross-source re-routing**

- **Skill guidance for gene queries**

### Changes

- `common/gene_annotation.py`

- `edgepython_mcp/edgepython_server.py`

- `edgepython_mcp/tool_schemas.py`

- `cortex/app.py`

- `cortex/llm_validators.py`

- `skills/ENCODE_Search.md`

- `skills/Differential_Expression.md`

## [3.2.7] - 2026-03-12

### Features

- **Gene annotation & ID translation**

- **Version stripping & organism detection**

- **Two new MCP tools**

- **ANNOTATE_RESULTS plan step**

- **Graceful degradation**

### New Files

- `common/gene_annotation.py`

- `scripts/build_gene_reference.py`

- `data/reference/human_genes.tsv`

- `data/reference/mouse_genes.tsv`

- `tests/common/test_gene_annotation.py`

### Changes

- `common/__init__.py`

- `edgepython_mcp/edgepython_server.py`

- `edgepython_mcp/tool_schemas.py`

- `cortex/planner.py`

- `cortex/plan_executor.py`

- `cortex/task_service.py`

- `cortex/prompt_templates/planning_system_prompt.md`

- `skills/Differential_Expression.md`

## [3.2.6] - 2026-03-12

### Features

- **Expanded plan template catalog (4 → 8)**

- **5 new step types (13 → 18)**

- **CHECK_EXISTING guards**

- **Plot type auto-selection**

- **Enhanced existing templates**

- **Priority-ordered plan detection**

### Changes

- `cortex/planner.py`

- `cortex/plan_executor.py`

- `cortex/plan_replanner.py`

- `cortex/task_service.py`

- `cortex/prompt_templates/planning_system_prompt.md`

## [3.2.5] - 2026-03-12

### Features

- **Plan-Execute-Observe-Replan layer**

- **Four plan templates**

- **Automatic replanning**

- **Safe-step auto-execution**

- **Plan-aware conversation state**

### New Files

- `cortex/planner.py`

- `cortex/plan_executor.py`

- `cortex/plan_replanner.py`

- `cortex/prompt_templates/planning_system_prompt.md`

### Changes

- `cortex/app.py`

- `cortex/agent_engine.py`

- `cortex/schemas.py`

- `cortex/conversation_state.py`

- `cortex/task_service.py`

## [3.2.4] - 2026-03-11

### Features

- **Bottom-pinned task dock**

- **Todo-driven local sample workflow**

- **Existing staged-sample decision gate**

### Fixes

- **Launchpad submission payload cleanup**

- **Workflow-aware task projection**

### Docs

- Updated the local sample intake skill guidance to document the staged-copy execution model and the reuse-versus-replace decision point for existing staged folders.

### Tests

- Regression test coverage updates

## [3.2.3] - 2026-03-11

### Features

- **Persistent task hierarchy expanded**

- **Task bootstrap script**

### Docs

- Updated the main README with the persistent task-list overview, bootstrap instructions for existing servers, and the focused task validation command.

- Clarified the supported startup flow: use `agoutic_servers.sh` for the backend stack and start the UI with `streamlit run ui/appUI.py` rather than running the Streamlit script directly with Python.

### Tests

- Added focused task hierarchy coverage for download-file children and workflow stage children in the Cortex project endpoint suite.

## [3.2.2] - 2026-03-11

### Features

- **Analyzer-side edgePython adapter foundation**

### Notes

- This is groundwork only. Cortex still routes DE traffic directly to the existing `edgepython` service path; analyzer/server4 is not the live DE adapter yet.

### Tests

- Added analyzer adapter unit tests covering upstream-session helpers, artifact relocation, and proxy registry/schema generation.

## [3.2.1] - 2026-03-10

### Features

- **DE plot images in UI**

### Fixes

- **DE outputs saved to project folder**

### Tests

- Regression test coverage updates

## [3.2.0] - 2026-03-10

### Features

- **edgePython MCP server**

- **`differential_expression` skill**

- **Skill routing for DE**

- **JSON Schema tool contracts**

- **Server-side param extraction**

- **Mistral `[TOOL_CALLS]` format converter**

### Fixes

- **`save_results` FunctionTool crash**

- **MCP tool errors killing all tool calls**

- **edgePython early-stop**

### Tests

- Regression test coverage updates

## [3.1.4] - 2026-03-10

### Features

- **Human-readable prompt templates**

- **Prompt inspection in chat**

- **Prompt rendering API**

## [3.1.3] - 2026-03-09

### Features

- **Download cancel button**

- **"List my data" chat command**

- **Improved skill routing**

## [3.1.2] - 2026-03-09

### Features

- **Chat stop button**

- **Download cancel + cleanup**

- **Nextflow job cancellation**

- **Post-cancel Delete / Resubmit**

- **Chat-based deletion**

- **DELETED status enum**

### Features

- Fixed file downloads to go to `users/{user}/data` instead of `users/{user}/{project}/data`.

### Fixes

- Fixed `reference_genome` deserialization crash on resubmit

- Fixed `resume_from_dir` being dropped on approval

- Fixed MCP server `submit_dogme_job` missing `resume_from_dir` parameter that caused a Pydantic validation error on resubmit.

### Tests

- Regression test coverage updates

## [3.1.2] - 2026-03-07

### Cleanup

- Removed deprecated manual test and migration scripts that were superseded by the consolidated `tests/` suite and current runtime/bootstrap workflow, including the old in-package `test_*.py` helpers, `migrate_*.py` scripts, and the root-level `test_*.sh` shell scripts.

- Moved the remaining manual admin and operational utilities into a dedicated `scripts/` tree: `scripts/cortex/init_db.py`, `scripts/cortex/set_usernames.py`, `scripts/launchpad/debug_job.py`, and `scripts/launchpad/submit_real_job.py`.

- Updated the active README and quickstart documentation to point at the `scripts/` locations and the current pytest-based test commands.

## [3.1.1] - 2026-03-07

### Fixes

- Fixed permanent project deletion so user project directories are resolved before the database row is removed, which now cleans up slug-based project folders under `AGOUTIC_DATA/users/...` instead of leaving them behind.

- Restored analyzer proxy compatibility after the route extraction by making the extracted proxy endpoints honor the legacy `cortex.app` `require_run_uuid_access` override used by older tests, which fixes the `GET /analysis/jobs/{run_uuid}/summary` and `GET /analysis/jobs/{run_uuid}/files` pytest failures seen on Watson.

- Preserved token accounting after permanent project deletion by archiving each deleted project's lifetime and daily token totals in new Cortex models and folding those archived totals back into the user and admin token-usage endpoints.

- Added deleted-project token archive support to the manual database bootstrap script so fresh databases can create the new accounting tables.

### Tests

- Regression test coverage updates

- Added new unit-test coverage in this release cycle for Launchpad Nextflow config generation, Launchpad DB/MCP helpers, logging middleware behavior, Atlas config and launcher branches, Analyzer app error handling, and UI helper functions.

## [3.1.0] - 2026-03-06

### Refactor

- Tier 3: Extract Route Handlers & Shared DB Helpers

### Refactor

- Tier 2: Extract Stateful Helpers from cortex/app.py

### Refactor

- Tier 1: Extract Pure Functions from cortex/app.py

## [3.0.9] - 2026-03-05

### Added

- Comprehensive Test Suite for Pre-Refactor Safety Net

### Fixed

- test_approval_suppressed_non_job_skill mock

### Fixed

- Removed Accidental Python Bytecode Files From Version Control

## [3.0.8] - 2026-03-03

### Fixed

- ENCODE Follow-Up Questions Not Injecting Previous Data

# Changelog - February 2026

## [3.0.7] - 2026-02-27

### Fixed

- Modified Dogme_DNA.md and DOGME_QUick_WORKFLOW_GUIDE.md to reflect bedMethyl and openChromatin file structure

### Fixed

- LLM Context Window Stuck at 4,096 Tokens

## [3.0.6] - 2026-02-27

### Fixed

- Typo in Analyze_Job_Results.md RNA routing

### Fixed

- Modified Dogme_RNA.md to reflect bedMethyl file structure

### Fixed

- Polling Timeout Too Short for Long-Running Pipelines

### Fixed

- Auto-Analysis Not Triggered After Server Restart Recovery

### Fixed

- LLM Displayed Wrong Sample Name from Conversation History

### Fixed

- LLM Emitted `[[TOOL_CALL:...]]` Instead of `[[DATA_CALL:...]]` for Analyzer

## [3.0.5] - 2026-02-26

### Fixed

- Successful Nextflow Jobs Marked as Failed After Server Restart

### Fixed

- Trace File Lookup Preferred Wrong Filename

## [3.0.4] - 2026-02-26

### Fixed

- Nextflow Task Names Missing for Non-Main Entry Points

## [3.0.3] - 2026-02-25

### Changed

- Centralised Version Number

### Fixed

- Chat Messages Disappearing from UI

## [3.0.2] - 2026-02-25

### Fixed

- ENCODE `search_by_assay` Missing from Tool Routing & DataFrame Pipeline

### Fixed

- ENCODE Search Retry on 0 Results

### Fixed

- `search_by_assay` Results Not Rendered as DataFrames

---

## [3.0.1] - 2026-02-24

### Fixed

- Cortex Restart Kills Job Monitoring

### Fixed

- SKILL_SWITCH Incorrectly Blocked on Post-Restart Stale Blocks

### Fixed

- "analyze the result" Not Routing to analyze_job_results Skill

---

## [3.0.0] - 2026-02-24

### Added

- Run Dogme from Downloaded BAM Files

### Fixed

- BAM Symlink Naming Conventions

### Fixed

- Genome Reference Validation for modkit/annotateRNA

### Fixed

- Skill Routing for BAM Analysis Requests

### Fixed

- Sample Name and Path Extraction

### Fixed

- SyntaxError in cortex/app.py

### Added

- Cortex Entry Point Detection for Downloaded BAMs

## [2.9] - 2026-02-23

### Added

- GPU Concurrency Control in Approval Form

## [2.8] - 2026-02-22

### Fixed

- FastMCP 2.x Compatibility

### Fixed

- Spurious ENCODE Auto-Calls on Visualization / DF Follow-ups

### Fixed

- `UnboundLocalError` on `source_key` in System Prompt

### Fixed

- False "Unexpected Data" on Download Workflows

### Fixed

- Multi-File Download Lost Previous Files

### Fixed

- Provenance Block Swallowed Results in Streamlit

### Fixed

- Download Chain Not Triggered from ENCODE Skills

### Fixed

- Browsing Commands Injected Previous ENCODE DataFrame

### Added

- Cascading Path Resolution for "list files in \<subfolder\>"

### Added

- "list project files" Command

### Added

- Browsing Error Suggestions

### Fixed

- `_validate_analyzer_params` Overriding Auto-Generated Paths

### Fixed

- "list project files" Not Detected as Browsing Command

### Fixed

- Spurious Approval Gate on ENCODE Search After Dogme Context

### Fixed

- "plot this" Used Wrong DataFrame

### Fixed

- Stale `latest_dataframe` in Cached Conversation State

### Fixed

- Empty Response When LLM Only Emits a PLOT Tag

### Fixed

- "plot this" on Dogme Skills Triggered Job Submission

### Fixed

- Multi-File Download Missing Files

### Fixed

- Raw File Metadata JSON Displayed in Downloads

---

## [2.8] - 2026-02-22

### Changed

- Server Rename

### Added

- Architecture Hardening: Tool Contracts, Structured State, and Observability

---

## [2.7] - 2026-02-20

### Added

- **"download ENCFF..." resolves metadata and starts download automatically** - `_auto_generate_data_calls()` detects download intent (keywords: download, grab, fetch, save) + ENCFF accession and generates a `get_file_metadata` call with `_chain: "download"`. After metadata resolves, the chain handler extracts the download URL (prefers `cloud_metadata.url` / S3 link, falls back to ENCODE `href`), sets `active_skill = "download_files"` and `needs_approval = True`. - Parent experiment resolution: checks the current user message for ENCSR accessions first (e.g. "download ENCFF921XAH from experiment ENCSR160HKZ"), then falls back to `_find_experiment_for_file()` which scans conversation history. - Applied to: [cortex/app.py](cortex/app.py)

- **Download chain fires for both auto-generated and LLM-generated tags** - The chain handler now triggers on `_chain == "download"` (auto-generated calls) OR `active_skill == "download_files"` (LLM-generated tags). Previously, when the LLM correctly generated `get_file_metadata`, the chain didn't fire because LLM tags don't carry `_chain`, causing the approval gate to fall through to Dogme job params. - Applied to: [cortex/app.py](cortex/app.py)

- **Auto-generated calls now go through tool correction pipeline** - Auto-generated calls from `_auto_generate_data_calls()` previously bypassed `_correct_tool_routing()` and `_validate_encode_params()`, causing ENCFF accessions to be passed as experiment accessions to the wrong tool. Now the same correction pipeline is applied: `_correct_tool_routing` + `_validate_encode_params` for ENCODE, `_validate_analyzer_params` for analyzer. - Applied to: [cortex/app.py](cortex/app.py)

- **Download-specific approval gate (not Dogme job params)** - When `gate_action == "download"`, the approval gate shows the file list with sizes and target directory instead of calling `extract_job_parameters_from_conversation` (which always extracted Dogme DNA/RNA params). UI renders a dedicated download approval form with Approve/Cancel buttons. - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **Per-file byte-level download progress** - `_download_files_background()` now tracks `expected_total_bytes`, `current_file_bytes`, `current_file_expected` from `size_bytes` metadata or `Content-Length` header. Progress updates are throttled to every 0.5s to avoid DB spam. UI shows a Streamlit progress bar with MB downloaded/expected for the current file, with fallback to overall byte progress or file-count progress. - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **`_auto_detect_skill_switch` detects download intent** - "download" + ENCFF or URL now triggers a skill switch to `download_files` regardless of current skill. - Applied to: [cortex/app.py](cortex/app.py)

- **`download_after_approval()` prefers gate file list** - Refactored to check `gate_params.get("files")` first (populated by the download chain), then falls back to URL scanning. Also uses `target_dir` from the gate when available. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **`_validate_analyzer_params` no longer overwrites auto-resolved subdirectory paths** - After downloading a file to `data/`, "list files in data" showed the project root (1 entry: `data/`) instead of the data directory contents. The auto-generated call correctly resolved `work_dir=/project/data`, but `_validate_analyzer_params` overwrote it with the project-level `work_dir` from context. Now preserves the incoming `work_dir` when it's already a valid subdirectory of the resolved context dir. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **LLM replaces correct ENCSR with a hallucinated one in its text response** - When calling a tool like `get_files_by_type(accession=ENCSR160HKZ)`, the LLM would correctly fetch the right data but then write "There are 12 BAM files for ENCSR000CQZ" in its summary. Added a post-processing pass on `clean_markdown`: if the user message contains exactly one ENCSR accession, any different ENCSR appearing in the LLM's text is replaced with the correct one. - Applied to: [cortex/app.py](cortex/app.py)

- **LLM calls wrong tool/accession for ENCFF file metadata ("metadata for ENCFF921XAH" → fetched ENCSR503QZJ)** - The LLM called `get_files_by_type(accession=ENCSR503QZJ)` (completely hallucinated experiment) instead of `get_file_metadata(accession=ENCSR160HKZ, file_accession=ENCFF921XAH)`. The ENCFF-detection logic in `_correct_tool_routing` only applied to `get_experiment`, not to other ENCODE tools. Added a general ENCFF catch-all at the end of `_correct_tool_routing`: if ENCFF appears in the user message and the tool is anything other than `get_file_metadata`, redirect to `get_file_metadata` with the parent experiment found from conversation history. - Applied to: [cortex/app.py](cortex/app.py)

- **`_validate_encode_params` Fix 3: replace hallucinated ENCSR in tool params** - If the user explicitly stated an ENCSR accession in their message and the LLM's tag used a different ENCSR, the parameter is corrected before the API call. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **`data/` and other non-workflow dirs excluded from "list workflows"** - "list workflows" listed everything in the project directory, including `data/`. Added `name_pattern` parameter to `discover_files()`, `list_job_files()`, and all MCP registrations. Cortex now passes `name_pattern="workflow*"` for "list workflows" calls so only `workflow1/`, `workflow2/`, etc. are returned. - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/mcp_tools.py](analyzer/mcp_tools.py), [analyzer/mcp_server.py](analyzer/mcp_server.py), [cortex/app.py](cortex/app.py)

### Fixed

- **Browsing commands no longer trigger APPROVAL_NEEDED gate** - When on a Dogme skill (e.g. `run_dogme_cdna`), the LLM emitted `[[APPROVAL_NEEDED]]` alongside a browsing response, causing the approval confirmation UI to appear. The browsing override now also sets `needs_approval = False` and `plot_specs = []`. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **Browsing commands now skill-independent (not gated on Dogme skills)** - "list workflows", "list files", "list files in X" now work from ANY skill

- **Project directory as minimum context for file browsing** - Browsing commands previously required `work_dir` from EXECUTION_JOB history (only available after a Dogme job ran). Now the project directory (resolved from `_resolve_project_dir()`) is always available as a fallback. Added `project_dir` parameter to `_auto_generate_data_calls()` and `_validate_analyzer_params()`. - Applied to: [cortex/app.py](cortex/app.py)

- **"list workflows" from ENCODE skill routed to ENCODE instead of analyzer** - The ENCODE catch-all extracted "workflows" as a search term and generated `search_by_biosample`. Added a browsing-command override in the main chat handler that detects browsing patterns and forces the correct analyzer auto-generated calls, suppressing any LLM-generated ENCODE tags. - Applied to: [cortex/app.py](cortex/app.py)

- **"list files in workflow1/annot" showed workflow2/annot instead** - The subfolder rescue prioritized the LLM's invented param (`subfolder=annot`, which lost the `workflow1/` prefix) over the user message. Swapped priority: user message parsing now comes first (preserving the full `workflow1/annot` path), with the LLM hint as fallback only. - Applied to: [cortex/app.py](cortex/app.py)

- **Previous ENCODE DataFrame shown alongside file listings** - When browsing from an ENCODE skill, `_inject_job_context()` injected the previous K562 dataframe. The browsing override cleared LLM tags but not `_injected_dfs`. Now also clears `_injected_dfs = {}` and `_injected_previous_data = False`. - Applied to: [cortex/app.py](cortex/app.py)

- **Smarter project-dir derivation for "list workflows"** - Previously blindly stripped the last path component to get the project dir, which would over-strip if `work_dir` was already the project dir. Now only strips `/workflowN/` suffix; otherwise treats `work_dir` as already project-level. Applied to both `_auto_generate_data_calls()` and `_validate_analyzer_params()`. - Applied to: [cortex/app.py](cortex/app.py)

### Added

- **File browsing instructions in system prompt (all skills)** - Added a "FILE BROWSING" section to the system prompt that's always visible regardless of active skill. Tells the LLM it can use `list_job_files` via DATA_CALL tags for browsing at any time, and that the system automatically resolves the correct project/workflow directory. - Applied to: [cortex/agent_engine.py](cortex/agent_engine.py)

### Fixed

- **Always force `work_dir` from conversation context

- **Include `EXECUTION_JOB` blocks in history query** - The DB query that builds `history_blocks` only fetched `USER_MESSAGE` and `AGENT_PLAN` blocks. `_extract_job_context_from_history()` looks for `EXECUTION_JOB` blocks to find the authoritative `work_dir`, but never found any

- **Pass `history_blocks` to `_auto_generate_data_calls()`** - The safety-net auto-generation function called `_extract_job_context_from_history()` without `history_blocks`, so it could never find EXECUTION_JOB blocks even when they existed. Now `history_blocks` is threaded through from the call site. - Applied to: [cortex/app.py](cortex/app.py)

- **Analyzer tool aliases for hallucinated tool names** - The LLM frequently generates `list_workflows`, `find_files`, `parse_csv`, `read_file` etc. instead of the real MCP tool names. Added alias mapping: `list_workflows`→`list_job_files`, `find_files`→`find_file`, `parse_csv`→`parse_csv_file`, `read_file`→`read_file_content`, `analysis_summary`→`get_analysis_summary`, etc. - Applied to: [cortex/app.py](cortex/app.py)

- **Strip unknown Analyzer params (e.g. `sample=Jamshid`)** - The LLM adds invented params like `sample=Jamshid` that Analyzer tools don't accept, causing Pydantic validation errors. `_validate_analyzer_params()` now strips any param not in a per-tool allowlist before the MCP call. - Applied to: [cortex/app.py](cortex/app.py)

- **"list workflows" returns 502 files → now shows top-level dirs only** - When using `list_job_files` on the project dir, `discover_files()` recursively listed all files. Added `max_depth` parameter: `max_depth=1` lists only immediate children (workflow folders). Both the auto-generated safety-net call and `_validate_analyzer_params()` now set `max_depth=1` for "list workflows" commands. - Applied to: [cortex/app.py](cortex/app.py), [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/mcp_tools.py](analyzer/mcp_tools.py), [analyzer/mcp_server.py](analyzer/mcp_server.py)

- **File listing results showed "..." for all fields** - Analyzer `list_job_files` results are dicts with a `files` array. The result formatter's `_compact_dict()` truncated nested file objects to `"..."` at depth 2. Added a dedicated file-listing table formatter in `_format_data()` that renders files as a proper markdown table with Name, Path, and human-readable Size columns. - Applied to: [atlas/result_formatter.py](atlas/result_formatter.py)

- **`workflow1/` filtered out by `discover_files()` skip logic** - The `work/` and `dor*/` skip filter used `startswith("work")` which matched `workflow1`, `workflow2`, etc. Changed to exact `child.name == "work"` for directories and `parts[0] == "work"` for the recursive path, so only the Nextflow `work/` directory is skipped. - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py)

- **"list files in annot" ignored the subfolder** - When the LLM generated `subfolder=annot` (an invented param), it was stripped as unknown, and the subpath was lost. Now `_validate_analyzer_params()` rescues subfolder hints from invented LLM params AND parses the user message for "list files in <subpath>"

- **Sample name contaminated by file listing table headers** - The `sample_name` regex matched bare `Name` in markdown table headers (`| Name | Path | Size |`), capturing "| Path | Size |" as the sample name. Tightened to require `Sample Name` (not bare `Name`) and added `|` to the exclusion character class. - Applied to: [cortex/app.py](cortex/app.py)

- **Skip second-pass LLM for file browsing commands** - "list files" and "list workflows" now show the formatted file table directly instead of passing it through a second LLM call that would produce an unwanted summary/interpretation. - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed `work_dir=/work_dir` and `work_dir={work_dir_from_context}` placeholders in Analyzer DATA_CALL tags** *(superseded by always-force fix above)* - When the LLM copies template variables from skill docs, it emits literal strings like `/work_dir`, `<work_dir>`, or `{work_dir_from_context}` instead of the real workflow path, causing "Work directory not found" errors. - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed CDNA misidentified as DNA in Local Sample Intake** - The `sample_type` keyword heuristic was gated by `if "sample_type" not in _collected`, so if a previous assistant message had echoed "Data Type: DNA" (wrong), the regex would extract "DNA" and the heuristic would never run. Now the user-message keyword heuristic ALWAYS runs and overrides regex-extracted values, since user messages are the source of truth. - Applied to: [cortex/app.py](cortex/app.py)

### Added

- **"list workflows" command** - Detects "list workflows" / "show workflows" in user messages and auto-generates a `list_job_files` call on the project base directory (parent of the current workflow). Allows users to see all workflow folders (workflow1, workflow2, etc.) in their project. - Applied to: [cortex/app.py](cortex/app.py)

- **"list files" command with subfolder support** - Detects "list files" / "list files in workflow2/annot" patterns. Resolves the path using `_resolve_workflow_path()` which handles: empty subpath → current workflow, "workflow2" → that workflow's dir, "workflow2/annot" → that workflow's annot/ subdir, "annot" → current workflow's annot/ subdir. - Applied to: [cortex/app.py](cortex/app.py)

- **Relative path file parsing** - "parse annot/File.csv" and "parse workflow2/annot/File.csv" now work. Added `_resolve_file_path()` which splits user paths, checks if the first component is a known workflow folder name, resolves to the correct `work_dir`, and extracts the basename for `find_file` searches. - Applied to: [cortex/app.py](cortex/app.py)

- **Removed template variables from skill docs** - Replaced all `{work_dir}`, `{work_dir_from_context}`, and `{tool}` template variables in `DOGME_QUICK_WORKFLOW_GUIDE.md` with concrete example paths and explicit "NEVER use template variables" warnings. Added "Browsing Commands" reference section. - Applied to: [skills/DOGME_QUICK_WORKFLOW_GUIDE.md](skills/DOGME_QUICK_WORKFLOW_GUIDE.md)

- **Fixed sample_name not extracted from current message** - The "called <name>" heuristic only scanned `conversation_history`, not the current message. On the first turn (empty history), `sample_name` was never collected even when the user said "called Jamshid", leaving the LLM to extract it (unreliably). - Applied to: [cortex/app.py](cortex/app.py)

- **Stronger context injection when all 4 fields are collected** - When all 4 required fields (sample_name, path, sample_type, reference_genome) are extracted, the `[CONTEXT]` line now says "Use these EXACT values" with each field on its own line and specifies the pipeline name ("Dogme CDNA"), rather than a generic "Do NOT re-ask" message that the LLM would ignore. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **Replaced hardcoded `biosample_keywords` with heuristic detection** - Previously, `cortex/app.py` used a hardcoded set of 10 biosamples (K562, HeLa, etc.) to determine if a query was a "new search" or a "follow-up". Unknown cell lines (like HL-60) were incorrectly treated as follow-ups, causing the system to inject stale data from previous queries (e.g., MEL) and hallucinate answers based on the wrong dataset. - Replaced the hardcoded list with a three-layer heuristic: 1. **New-query patterns**: Regex matches for explicit searches (e.g., "how many X experiments", "search encode for X"). 2. **Follow-up signals**: Referential language (e.g., "of them", "those", "DF3") that overrides new-query patterns. 3. **Extracted-term vs. previous DFs**: Extracts the search term and checks if it appears in previous dataframe labels. If it's a new subject, it's treated as a new query. - Added pronouns ("them", "those", "these", etc.) to `_ENCODE_STOP_WORDS` so the fallback tokenizer doesn't treat them as search terms. - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed missing `visible: True` metadata on fresh ENCODE search dataframes** - When a fresh `search_by_biosample` API call returned results, the resulting dataframe was stored without the `visible: True` metadata flag. - The auto-selector for follow-up queries (which looks for the most recent visible dataframe) ignored these fresh dataframes and fell back to older, explicitly visible dataframes (like injected filtered results). - Added `"visible": True` to the metadata of all `_SEARCH_TOOLS` dataframes so they can be correctly targeted by follow-up queries. - Applied to: [cortex/app.py](cortex/app.py)

- **Replaced exhaustive `biosamples` dict with catch-all extractor** - The safety net (`_auto_generate_data_calls`) previously relied on a hardcoded dictionary of known biosamples to generate API calls. If a term wasn't in the dict, the LLM would hallucinate a response without making an API call. - Replaced the exhaustive dict with a slim `_KNOWN_ORGANISMS` dict (used only for organism hints) and a new `_extract_encode_search_term()` regex-based catch-all extractor that handles ANY biosample name. - Added a hallucination guard: if no tool was executed but the LLM claims "there are N experiments" or "interactive table below", the response is stripped and a disambiguation message is shown. - Applied to: [cortex/app.py](cortex/app.py)

- **Fixed organism auto-addition and missing `search_term`** - The system was incorrectly auto-adding `organism=Homo sapiens` to mouse cell lines (like MEL) and placing the cell line name in `assay_title` instead of `search_term`. - Created `_validate_encode_params()` to fix missing `search_term` by moving non-assay values from `assay_title`, and to strip `organism` unless the user explicitly mentioned a species. - Applied to: [cortex/app.py](cortex/app.py)

- **Added tool aliases and deduplication** - Added `"search": "search_by_biosample"` to `tool_aliases` and `Search (...)` to `fallback_patterns` to fix "Unknown tool: search" errors. - Added a deduplication step after collecting all tool calls to prevent duplicate dataframes from appearing in the UI. - Applied to: [cortex/app.py](cortex/app.py), [atlas/config.py](atlas/config.py)

- **Updated ENCODE and Local Sample Intake skills** - Updated `ENCODE_Search.md` to instruct the LLM not to limit itself to known terms and to never invent a count. - Updated `Local_Sample_Intake.md` with anti-step directives ("DO NOT describe your reasoning steps"), fixed field name mismatches (`data_type`→`sample_type`, `data_path`→`path`), and added mouse/human genome inference. - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md), [skills/Local_Sample_Intake.md](skills/Local_Sample_Intake.md)

## [Unreleased] - 2026-02-19

### Changed

- **All Analyzer analysis tools now accept `work_dir` as the primary parameter** - `work_dir` (absolute path to the workflow folder, e.g. `.../users/alice/project/workflow1/`) is now the preferred way to identify which job's files to access. `run_uuid` is retained as a legacy fallback for backward compatibility. - New `resolve_work_dir(work_dir, run_uuid)` function in `analysis_engine.py` acts as a central resolver: tries `work_dir` first (direct path), falls back to UUID-based DB lookup. - All 7 MCP tool functions (`list_job_files`, `find_file`, `read_file_content`, `parse_csv_file`, `parse_bed_file`, `get_analysis_summary`, `categorize_job_files`) updated with `work_dir` as first parameter. - All 7 MCP protocol wrappers in `mcp_server.py` updated. - Response dicts now return `work_dir` instead of `run_uuid`. - TOOL_SCHEMAS rewritten with `work_dir` documented as preferred. - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/mcp_tools.py](analyzer/mcp_tools.py), [analyzer/mcp_server.py](analyzer/mcp_server.py), [analyzer/schemas.py](analyzer/schemas.py)

- **Context injection now provides workflow directories instead of UUIDs** - `_extract_job_context_from_history()`

- **`_auto_generate_data_calls()` uses `work_dir` for file operations** - DATA_CALL find_file calls now use `work_dir=` instead of `run_uuid=`. - Smart matching: when multiple workflows exist, matches filename prefix to sample_name to pick the correct workflow directory. - Chained tool calls extract `work_dir` from find_file response. - Applied to: [cortex/app.py](cortex/app.py)

- **Auto-analysis no longer shows UUIDs to users** - `_auto_trigger_analysis()` LLM prompt uses "Work directory:" instead of "Run UUID:". - Analysis header shows "**Workflow:** workflowN" instead of UUID. - `_build_static_analysis_summary()` fallback template shows workflow folder name. - Applied to: [cortex/app.py](cortex/app.py)

- **All skill docs updated to use `work_dir` instead of `run_uuid`** - `DOGME_QUICK_WORKFLOW_GUIDE.md`

### Added

- **`_auto_trigger_analysis()` now passes results through the LLM** - Previously, when a Dogme job completed, the auto-analysis step built a static markdown template listing file counts and filenames

- **`model` field added to `EXECUTION_JOB` block payload** - The EXECUTION_JOB block now carries the `model` key from the approval gate, so `_auto_trigger_analysis` can instantiate `AgentEngine` with the same model the user selected for the conversation. - Applied to: [cortex/app.py](cortex/app.py)

- **Helper functions extracted for clarity** - `_build_auto_analysis_context()`

### Added

- **`atlas/mcp_server.py`

- **`atlas/launch_encode.py` updated to load extended server** - Previously imported directly from ENCODELIB's `encode_server`. Now imports `server` from `atlas.mcp_server` (after ENCODELIB is added to `sys.path`), so all ENCODELIB tools plus `search_by_assay` are registered before the HTTP server starts. - Applied to: [atlas/launch_encode.py](atlas/launch_encode.py)

### Fixed

- **Structural guard: non-ENCSR accessions redirect to correct search tool** - `_correct_tool_routing()` in the chat endpoint now applies a structural check rather than a name-list check: any `accession` parameter passed to `get_experiment` that does not match `^ENCSR[A-Z0-9]{6}$` is redirected to the appropriate search tool. This handles unknown cell lines and biosample terms regardless of naming convention. - Added `_ENCSR_PATTERN` and `_ENCFF_PATTERN` compiled regexes; `_ENCFF_PATTERN` similarly redirects `get_experiment` calls with ENCFF accessions to `get_file_metadata`. - Applied to: [cortex/app.py](cortex/app.py)

- **Assay-only queries routed to `search_by_assay`** - Queries such as *"how many RNA-seq experiments are in ENCODE"* previously had no routing path and sometimes triggered wrong tool calls. `_correct_tool_routing()` now has a **Case A** branch: when the apparent search term looks like an assay name (detected via `_looks_like_assay()`) with no biosample context, the tool is redirected to `search_by_assay`. - `_auto_generate_data_calls()` safety net likewise emits `search_by_assay` (not `search_by_organism`) for assay-only fallback. - Added helpers: `_looks_like_assay(s)`, `_ASSAY_INDICATORS` tuple, `_ENCODE_ASSAY_ALIASES` map (`rna-seq` → `total RNA-seq`, etc.). - Applied to: [cortex/app.py](cortex/app.py)

- **`ENCODE_Search.md` skill updated with anti-pattern guard and new tool docs** - Added `⛔ NEVER` block listing 4 wrong tool-call patterns and 3 correct alternatives. - Decision rule updated: *cell line → `search_by_biosample`; assay only → `search_by_assay`; ENCSR accession → `get_experiment`*. - Quick-reference table gains 3 `search_by_assay` examples. - `search_by_assay` parameter docs added to Available Tools section. - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md)

### Added

- **`username` column on `User` model** - New nullable `TEXT` column with a conditional unique index (`WHERE username IS NOT NULL`). Distinct from `google_id` and `email`

- **`cortex/migrate_usernames.py`

- **`cortex/set_usernames.py`

- **Filesystem paths use `username/project-slug/` hierarchy** - User-jailed paths now resolve to `$AGOUTIC_DATA/users/{username}/{project_slug}/` (falling back to `user_{id}` if username is not yet set). - Workflow directories use `{username}/{project_slug}/workflow{N}/`. - Applied to: [cortex/user_jail.py](cortex/user_jail.py), [launchpad/nextflow_executor.py](launchpad/nextflow_executor.py), [analyzer/config.py](analyzer/config.py)

- **Admin endpoints for username management** - `PATCH /admin/users/{user_id}/username`

- **UI

### Fixed

- **Context injection for `analyze_local_sample` skill added to `_inject_job_context()`** - `_inject_job_context()` had handlers for Dogme skills (UUID/work_dir injection) and ENCODE skills (dataframe injection), but **nothing for `analyze_local_sample`**. When the system prompt grew with PLOT docs, `download_files` skill, and expanded `ENCODE_Search.md`, devstral-2 could no longer reliably track parameters across turns

### Fixed

- **`extract_job_parameters_from_conversation()` scoped to the most recent submission cycle** - Previously the function scanned **all** blocks in the project and concatenated every user message into one string. The regex `r'called\s+(\w+)'` did `re.search` which finds the **first** match

### Fixed

- **`_auto_detect_skill_switch()` now switches to `analyze_local_sample` from Dogme analysis skills** - Previously, the pre-LLM skill switch excluded `run_dogme_dna`, `run_dogme_rna`, and `run_dogme_cdna` from the `analyze_local_sample` detection to avoid false positives. This meant a user saying "Analyze the local mouse CDNA sample called Jamshid2 at /path" while on `run_dogme_cdna` would **not** switch skills

### Improved — Block Headers Show Project Name and Workflow Folder

- **Block metadata header shows project name instead of UUID** - `render_block()` in the UI now resolves the project name from the cached projects list for the `📌 Proj:` header. Falls back to a truncated UUID (`c7f75875…`) if the project isn't in the cache. Previously displayed the raw UUID (`c7f75875-b7f8-4835-8f94-28fd4f8090bf`). - Applied to: [ui/appUI.py](ui/appUI.py)

- **EXECUTION_JOB block shows `workflowN` folder name alongside run UUID** - Cortex now stores the `work_directory` path (returned by Launchpad's `submit_dogme_job`) in the EXECUTION_JOB block payload. The UI extracts the folder name (e.g. `workflow2`) from the path and displays it as `📁 workflow2 | Run UUID: 3862fd86...`. Previously only the opaque run UUID was shown, with no visible connection to the actual directory on disk. - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

### Fixed

- **MCP tool registration updated to pass through `username` and `project_slug`** - The `submit_dogme_job()` wrapper in `mcp_server.py` defines the parameters exposed via FastMCP. When `username` and `project_slug` were added to `mcp_tools.py`'s `LaunchpadMCPTools.submit_dogme_job()`, the MCP wrapper was never updated to match. This caused FastMCP to reject `username` and `project_slug` as "unexpected keyword arguments" when Cortex called the tool after the approval gate. - Added `username: str | None = None` and `project_slug: str | None = None` to both the wrapper function signature and the inner `await tools.submit_dogme_job(...)` call. - Applied to: [launchpad/mcp_server.py](launchpad/mcp_server.py)

### Fixed

- **Fix A

- **Fix B

### Added

- **`AgentEngine` now captures `response.usage` from every LLM call** - `think()` and `analyze_results()` both return `(content, usage_dict)` tuples instead of plain strings. A `_usage_to_dict()` helper normalises `prompt_tokens`, `completion_tokens`, `total_tokens` from the OpenAI usage object and returns zeros gracefully on errors or `None` usage objects. - Applied to: [cortex/agent_engine.py](cortex/agent_engine.py)

- **Token counts stored in two places per assistant turn** - Chat endpoint (`POST /chat`) unpacks both LLM passes (think + analyze_results), sums their usage into `_total_usage`, and: 1. Embeds a `"tokens": {prompt_tokens, completion_tokens, total_tokens, model}` key in every `AGENT_PLAN` block's `payload_json`

- **Four new nullable columns on `ConversationMessage`** - `prompt_tokens INTEGER`, `completion_tokens INTEGER`, `total_tokens INTEGER`, `model_name TEXT`

- **Idempotent migration script** - `cortex/migrate_token_tracking.py` adds the four columns with `PRAGMA table_info` guards (safe to re-run). Respects the `$AGOUTIC_DATA` environment variable identically to `cortex/config.py`, so it works on Watson and local dev without modification. Accepts an optional path argument. - Applied to: [cortex/migrate_token_tracking.py](cortex/migrate_token_tracking.py)

- **`GET /user/token-usage` endpoint (authenticated, own data)** - Returns: `lifetime` totals, `by_conversation` list (project_id, title, token breakdown, last_message_at), `daily` time-series, and `tracking_since` (ISO date of earliest tracked message). - Applied to: [cortex/app.py](cortex/app.py)

- **`GET /admin/token-usage/summary` endpoint (admin-only)** - Returns per-user leaderboard sorted by total tokens consumed, plus a global daily time-series. Aggregates across all users via JOIN on `conversations → conversation_messages`. - Applied to: [cortex/admin.py](cortex/admin.py)

- **`GET /admin/token-usage` endpoint (admin-only, filterable)** - Optional `?user_id=` and `?project_id=` query params. Returns per-conversation breakdown and daily time-series for the matching scope. - Applied to: [cortex/admin.py](cortex/admin.py)

- **`GET /projects/{id}/stats`

- **Per-message token caption in chat UI** - Each `AGENT_PLAN` block renders a caption under the message content: `🪙 1,234 tokens (↑ 890 prompt · ↓ 344 completion) · llama3.2`. Reads from the `tokens` key in block payload. Pre-migration messages silently show nothing. - Applied to: [ui/appUI.py](ui/appUI.py)

- **Live token counter in sidebar** - `🪙 Tokens: 12,345` expander updates on every page rerun (API call moved outside the expander widget so it fetches unconditionally). Expander label shows the live total. Inside: Total + Completion metrics, daily line chart (when >1 day of data), and tracking-since date. - Applied to: [ui/appUI.py](ui/appUI.py)

- **Lifetime tokens metric in Projects dashboard** - Summary row alongside Disk Usage now shows "Lifetime Tokens Used". Per-project table gains a "🪙 Tokens" column populated from `/projects/{id}/stats`. - Applied to: [ui/pages/projects.py](ui/pages/projects.py)

- **"🪙 Token Usage" admin tab** - New fourth tab in the Admin page. Shows: global daily line chart, per-user leaderboard `st.dataframe`, user drill-down selector with per-conversation table and per-user daily chart. - Applied to: [ui/pages/admin.py](ui/pages/admin.py)

### Added

- **`token_limit` column on `User` model** - New nullable `INTEGER` column. `NULL` = unlimited (default). When set, enforces a hard cap on the user's lifetime token consumption. - Applied to: [cortex/models.py](cortex/models.py)

- **Migration extended for `users.token_limit`** - `cortex/migrate_token_tracking.py` gains a 5th idempotent migration step adding `users.token_limit`. Re-running the script skips already-present columns. - Applied to: [cortex/migrate_token_tracking.py](cortex/migrate_token_tracking.py)

- **`PATCH /admin/users/{user_id}/token-limit` endpoint (admin-only)** - Accepts `{"token_limit": <int or null>}`. Validates positive integer or null. Returns updated user record. Allows admins to set or clear a per-user cap without touching other user fields. - Applied to: [cortex/admin.py](cortex/admin.py)

- **`GET /admin/users`

- **Hard 429 enforcement in chat endpoint** - After `require_project_access()`, the chat endpoint queries the requesting user's lifetime `total_tokens`. If a `token_limit` is set and is exceeded, raises `HTTPException(429)` with structured detail `{error, message, tokens_used, token_limit}`. Admin users are exempt. - Applied to: [cortex/app.py](cortex/app.py)

- **`GET /user/token-usage`

- **Sidebar quota progress bar** - When a `token_limit` is set, the expander label changes to `🪙 X / Y tokens (Z%)`. Inside: `st.progress()` bar showing consumption ratio. At ≥ 90% a `st.warning("⚠️ Approaching token limit

- **Chat UI

- **Admin tab2

- **Admin tab3

- **Admin tab4

---

## [Unreleased] - 2026-02-18

### Added

- **`[[PLOT:...]]` tag system

- **`AGENT_PLOT` block rendering in UI** - New block type dispatched in `render_block()`. Renders inside a `st.chat_message("assistant", avatar="📊")` bubble. Calls `_render_plot_block()` which groups specs by `(df_id, type)` for multi-trace overlays and falls back to single-chart rendering via `_build_plotly_figure()`. Shows `st.warning()` if the referenced DF is not found. - Helper functions added: `_resolve_df_by_id()`, `_build_plotly_figure()`, `_render_plot_block()` - Applied to: [ui/appUI.py](ui/appUI.py)

- **Plotting instructions in LLM system prompt** - Large PLOTTING section injected into the system prompt (after `{data_call_block}`). Explicitly forbids Python code (❌ markers), shows correct `[[PLOT:...]]` syntax, lists all 6 chart types with required/optional params, and includes working examples. - OUTPUT FORMATTING RULE #3 added: "For plots/charts/visualizations, ONLY use `[[PLOT:...]]` tags. NEVER write Python code for plotting." - Applied to: [cortex/agent_engine.py](cortex/agent_engine.py)

- **Server-side code-to-plot fallback** - If the LLM writes a Python code block containing `matplotlib`/`plt.`/`plotly`/`px.` instead of a `[[PLOT:...]]` tag, Cortex detects it, auto-generates a `[[PLOT:...]]` spec from the user's message (infers chart type and column from natural language), and strips the code and any "Here is…"/"Explanation:" boilerplate from the visible response. - Applied to: [cortex/app.py](cortex/app.py)

- **Visualization hints in skill files** - `ENCODE_Search.md`: added "📊 Visualization Hints" section with examples for bar chart of assay distribution and pie chart of status breakdown. - `Analyze_Job_Results.md`: added "📊 Visualization Hints" section with examples for QC metric histograms, scatter, file type bar chart, BED score histogram, chromosome distribution, and correlation heatmap. - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md), [skills/Analyze_Job_Results.md](skills/Analyze_Job_Results.md)

- **`plotly>=5.18.0` added to `environment.yml`** - New `# --- Visualization ---` section added before `# --- Testing ---`. - Applied to: [environment.yml](environment.yml)

### Fixed

- **`scalar_one_or_none()` → `scalars().all()` + dedup** - `track_project_access()` crashed with `sqlalchemy.exc.MultipleResultsFound` when duplicate `ProjectAccess` rows existed for the same `(user_id, project_id)` pair. Fixed: now fetches all matching rows with `scalars().all()`, keeps the first, deletes duplicates, and returns the surviving row. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **Inject DF metadata into context for dogme skills when a `DF<N>` reference is detected** - When a user said "plot a histogram of DF1" while a dogme skill (e.g. `run_dogme_cdna`) was active, the LLM called `find_file(file_name=DF1)` via a analyzer DATA_CALL tag instead of emitting `[[PLOT: df=DF1, ...]]`. Root cause: `_inject_job_context` returned early for dogme skills after injecting run UUID context, so the LLM had no information that DF1 was an in-memory DataFrame. - Fix: The dogme skills branch now also checks for `DF\d+` references in the user message. If found, it searches history blocks for the matching DataFrame, extracts its label, columns, and row count, and appends a `[NOTE: DF<N> is an in-memory DataFrame

### Fixed

- **`[^\]]+` regex breaks on `]` inside tag content (e.g. `Columns: ['A', 'B']`)** - Root cause: `plot_tag_pattern = r'\[\[PLOT:\s*([^\]]+)\]\]'` uses `[^\]]+` which stops at the **first** `]` in the string. When the LLM writes `Columns: ['Category', 'Count']` inside a natural-language tag, the `]` after `'Count'` terminates the character class match early, the trailing `\]\]` can't match, and `re.finditer` returns zero results

- **NL fallback parser for `[[PLOT:...]]` tags** - After the DF-note fix above, when the tag IS matched, the LLM wrote natural language inside: `[[PLOT: histogram of DF1 with Category on the x-axis...]]` instead of `key=value` pairs. `_parse_tag_params` returned an empty dict, `df_id` remained `None`. - Fix: When `_parse_tag_params` yields no `df=` key, a natural-language extraction pass fires on the raw tag text

### Fixed

- **`px.histogram` on a categorical x column counts rows (each = 1) instead of reading the value column** - `_build_plotly_figure()` called `px.histogram(df, x="Category")` which bins raw values

### Fixed

- **`_resolve_df_by_id` constructed DataFrame without column names** - `pd.DataFrame(rows)` was called without passing `columns=`, so the DataFrame had integer column names `[0, 1]` instead of `["Category", "Count"]`. All `x_col`/`y_col` lookups failed (case-insensitive match returned nothing), the bar branch fell through to `value_counts()`, and every bar rendered at height 1. - Fix: now passes `columns=fdata.get("columns") or None` to the DataFrame constructor. - Applied to: [ui/appUI.py](ui/appUI.py)

- **Bar branch fell back to `value_counts()` even for pre-aggregated tables** - When `y_col=None` and `agg` is not `"count"`, the bar branch now checks if `x_col` is categorical and a numeric companion column exists. If so, it selects that column as `y` (preferring names like `count`, `value`, `n`, `total`, `freq`) instead of counting rows. This mirrors the same logic added to the histogram branch. - Applied to: [ui/appUI.py](ui/appUI.py)

- **Auto-fallback spec incorrectly set `agg="count"` for bar charts** - The code-fallback path that auto-generates a `[[PLOT:...]]` spec was adding `agg="count"` to every bar chart, which bypasses the pre-aggregated column detection and always counts rows. Removed; `_build_plotly_figure` now infers `y` automatically. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **`search_experiments` / `search_experiment` aliased to `search_by_biosample`** - LLM occasionally called `search_experiments` (non-existent tool name), returning a tool-not-found error instead of running the search. Added both variants to the tool alias map. - Applied to: [atlas/config.py](atlas/config.py)

- **Comprehensive biosample parameter aliases for `search_by_biosample`** - LLM used various param names (`biosample`, `biosample_name`, `cell_line`, `sample`, `biosample_term_name`, `cell_type`, `tissue`) when the tool expected `search_term`. All variants now silently map to `search_term`. - Applied to: [atlas/config.py](atlas/config.py)

---

### Fixed

- **Parsed CSV/BED data formatted as markdown table for LLM** - Previously, parsed CSV data was sent to the second-pass LLM as a massive JSON blob (with `column_stats`, `dtypes`, nested metadata) causing it to respond "The query did not return the expected data." Now `_format_data` in result_formatter.py detects structured CSV/BED results (has `columns` + `data` list) and renders them as a clean markdown table

- **CSV/BED dataframes kept visible (reverted `visible: False`)** - The previous fix of hiding CSV DFs caused the interactive widget to be buried in the details section. Since the DF widget is the primary way to display parsed file data (with column selection, sorting, download), it's now visible again. The LLM text summary and the DF widget complement each other. - Applied to: [cortex/app.py](cortex/app.py)

### Added

- **Debug toggle in sidebar** - New 🐛 Debug toggle in the sidebar enables per-response debug info display without needing server logs. - Applied to: [ui/appUI.py](ui/appUI.py)

- **Debug info section per AGENT_PLAN block** - When debug mode is on, each assistant response shows a collapsed "🐛 Debug Info" expander with JSON diagnostic data including: target_df_id, detected filters (assay, output_type, file_type), injected DF names/row counts, suppressed API calls, LLM raw response preview, auto-generated calls, and more. - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **`_inject_job_context` returns 3-tuple** - Now returns `(augmented_message, injected_dataframes, debug_info)` to pass diagnostic data from the injection logic through to the UI payload. - Applied to: [cortex/app.py](cortex/app.py)

### Fixed

- **Word-boundary matching for assay, output_type, and file_type filters** - All three filter detection loops in `_inject_job_context` now use `re.search(r'\b...\b')` instead of plain `if alias in message` substring check. This prevents false positives like `"hic"` matching inside `"which"`, which was causing the assay filter to fire as `"in situ Hi-C"` and the file_type filter as `"hic"` on queries like "which of them are the output type methylated reads?"

### Added

- **Sequential DF IDs assigned to every dataframe** - Each dataframe produced (from API calls or follow-up filters) gets a sequential ID: DF1, DF2, DF3, etc., scoped to the conversation. IDs are stored in `metadata.df_id` and persist across the session. - Applied to: [cortex/app.py](cortex/app.py)

- **Explicit DF references in follow-up queries** - Users can now reference a specific dataframe by ID: e.g. "filter DF3 by methylated reads" or "show me DF1". `_inject_job_context` parses `DF\d+` patterns and uses exactly that dataframe, bypassing the file-type/assay heuristic guessing that previously caused wrong dataframe selection. - Applied to: [cortex/app.py](cortex/app.py)

- **DF IDs shown in UI expander headers** - Dataframe expanders now display their ID: `📊 DF3: ENCSR160HKZ bam files

### Fixed

- **`get_file_types` aliased to `get_files_by_type`** - Problem: LLM occasionally called `get_file_types` (returns only `["bam", "bed bed3+", "tar"]`

- **Per-file-type dataframes instead of one merged table** - Problem: All file types (bam, bed, tar, fastq, etc.) were flattened into a single 69-row table with a "File Type" column. Follow-up queries like "which BAM files are methylated reads?" injected all 69 rows and the LLM returned results from every file type. - Fix: `get_files_by_type` results are now stored as **one dataframe per file type** (e.g. `"ENCSR160HKZ bam files (12)"`, `"ENCSR160HKZ bed bed3+ files (54)"`), each tagged with `metadata.file_type`. The UI renders them as separate expandable tables. - Applied to: [cortex/app.py](cortex/app.py)

- **File-type-scoped context injection for follow-up queries** - Problem: "which of the BAM files are methylated reads?" injected all file-type dataframes, so the LLM received 69 rows across BED/tar/etc. and hallucinated or mixed results from bed files. - Fix: `_inject_job_context` now detects the file-type context from the current message or recent conversation history (e.g. "bam" from "how many bam files?") and skips dataframes whose `metadata.file_type` does not match. The LLM receives **only the 12 BAM rows**. - Applied to: [cortex/app.py](cortex/app.py)

- **Suppression of redundant `get_files_by_type` DATA_CALL tags** - Problem: Even after correct context injection, the LLM still emitted `[[DATA_CALL: ... get_files_by_type]]` for follow-up filter queries (because words like "bam" and "accessions" appear in the message), triggering a fresh API fetch that overwrote the injected filtered data. - Fix: When `_injected_previous_data` is true and not row-capped, any `get_files_by_type` or `get_experiment` DATA_CALL tags emitted by the LLM are dropped before execution. - Applied to: [cortex/app.py](cortex/app.py)

- **Removed "methylated" from `_auto_generate_data_calls` file keywords** - "methylated" was in the keyword list that triggers auto-generation of `get_files_by_type` calls, causing the system to re-fetch all files on any follow-up that mentioned methylation, ignoring injected context. - Applied to: [cortex/app.py](cortex/app.py)

- **Stripped `<details>` raw data from conversation history sent to LLM** - Problem: Even with per-file-type dataframe injection working correctly (only 12 BAM rows injected), the LLM's conversation history still contained the **full** `<details>` block from the previous turn

- **Only show relevant file-type tables in the UI** - Problem: "How many BAM files for ENCSR160HKZ?" showed all three file-type tables (tar, bam, bed) prominently, even though the user only asked about BAM. - Fix: When storing per-file-type dataframes, the server detects which file type(s) the user asked about and tags matching dataframes with `metadata.visible = True`. The UI checks for this flag: if any dataframe is marked visible, only those are rendered; otherwise all are shown. Non-visible dataframes are still stored for follow-up queries. - Applied to: [cortex/app.py](cortex/app.py), [ui/appUI.py](ui/appUI.py)

- **Follow-up filter queries now show interactive dataframe of matching rows** - Problem: "Which of them are methylated reads?" correctly filtered to 2 rows but only showed the LLM's text answer

- **DATA_CALL suppression now covers legacy `[[ENCODE_CALL:]]` tags** - Problem: The LLM emitted `[[ENCODE_CALL: get_experiment, accession=ENCSR160HKZ]]` (legacy format) which bypassed the suppression block that only checked unified `[[DATA_CALL:]]` format. This caused a redundant `get_experiment` API call on follow-up questions. - Fix: Suppression now filters both `data_call_matches` and `legacy_encode_matches` for tools in `{"get_files_by_type", "get_experiment"}`. - Applied to: [cortex/app.py](cortex/app.py)

- **ENCODE search answer quality

---

## [Unreleased] - 2026-02-17

### Fixed

- **Root cause 1: Missing ownership fallback in `require_project_access`** - If a user's `project_access` row was missing (e.g. after a failed project creation), they received a 403 even on their own project because the check only looked at the `project_access` table and not `projects.owner_id` - Fix: added a third check

- **Root cause 2: Projects created only client-side never registered in DB** - When `POST /projects` timed out or failed, the UI silently fell back to a locally-generated UUID. Any subsequent `/chat` call then hit 403 because no `Project` or `ProjectAccess` row existed for that UUID - Fix: `/chat` endpoint now auto-registers the project (creates both `Project` and `ProjectAccess` rows as owner) if the project ID is not found in the DB, before running the access check - Applied to: [cortex/app.py](cortex/app.py)

- **Improved project creation timeout** - Raised `POST /projects` timeout in the UI from 5 s to 10 s to reduce the likelihood of the fallback UUID path being taken on slow startup - Applied to: [ui/appUI.py](ui/appUI.py)

---

## [Unreleased] - 2026-02-17

### Added

- **Projects Dashboard Page (`ui/pages/projects.py`)** - New Streamlit page with full project management UI - Editable data table with checkbox columns for bulk Archive and Delete operations - Columns: Name, Jobs, Size (MB), Messages, Files, Created, Last Active, Status - Bulk actions: archive selected, permanently delete selected (with confirmation) - Per-project detail view with 4 tabs: Stats, Jobs, Files, Conversations - Inline project rename, disk usage summary at page top - Applied to: [ui/pages/projects.py](ui/pages/projects.py)

- **Backend Project Management Endpoints** - `GET /projects/{id}/stats`

- **Enhanced Sidebar Project Switcher** - Shows all projects (was limited to 5) with job count per project - Search/filter box when >4 projects - Archive button per project (inline, non-current projects) - Current project shown in bold with pin icon - Applied to: [ui/appUI.py](ui/appUI.py)

- **Project Name in Chat Title** - Title shows `🧬 Project Name` instead of raw UUID - Falls back to truncated UUID if name lookup fails - Projects cached in session state for instant title display - Applied to: [ui/appUI.py](ui/appUI.py)

- **Results Page Auto-Lists Jobs** - Job dropdown auto-populated from current project's jobs (via `/projects/{id}/stats`) - Shows status emoji, sample name, and UUID prefix per job - Manual UUID input preserved as fallback - Removed hardcoded example UUIDs - Applied to: [ui/pages/results.py](ui/pages/results.py)

### Fixed

- **Page Flicker During Job Monitoring** - Problem: Page visibly refreshed/flashed every 2 seconds during job monitoring

### Fixed

- **Disk Usage Showing 0 for All Projects** - Problem: `/projects/{id}/stats`, `/projects/{id}/files`, and `/user/disk-usage` only scanned jailed path `AGOUTIC_DATA/users/{user_id}/{project_id}/` which is empty for legacy jobs - Solution: All three endpoints now also scan actual `nextflow_work_dir` paths stored in `dogme_jobs` table, covering both jailed and legacy `launchpad_work/` directories - Fixed key mismatch: files endpoint returns `size_bytes` consistently, UI handles both `size_bytes` and `size` as fallback - Fixed `pathlib.Path` vs FastAPI `Path` import collision (`from fastapi import Path as FastAPIPath`) - Applied to: [cortex/app.py](cortex/app.py), [ui/pages/projects.py](ui/pages/projects.py)

## [Unreleased] - 2026-02-17

### Added

- **Role-Based Authorization Gates on All Endpoints** - Added `require_project_access(project_id, user, min_role)` dependency that enforces viewer/editor/owner hierarchy on every project-scoped endpoint - Added `require_run_uuid_access(run_uuid, user)` for job-scoped endpoints (debug, analysis) - Admin users bypass all project-level checks; public projects allow viewer-level access - Wired into ~15 endpoints: `/blocks`, `/chat`, `/conversations`, `/jobs`, `/analysis/*`, `/projects/*/access` - Applied to: [cortex/dependencies.py](cortex/dependencies.py), [cortex/app.py](cortex/app.py)

- **Server-Side Project Creation (UUID)** - `POST /projects`

- **Job Ownership Binding (`user_id` on `dogme_jobs`)** - Added `user_id` column to DogmeJob in both Launchpad and Analyzer models (nullable for legacy jobs) - Cortex passes `user_id` through MCP submit flow → Launchpad REST → DB - `require_run_uuid_access()` checks `user_id` for direct ownership, falls back to project access - Applied to: [launchpad/models.py](launchpad/models.py), [launchpad/schemas.py](launchpad/schemas.py), [launchpad/db.py](launchpad/db.py), [launchpad/app.py](launchpad/app.py), [launchpad/mcp_server.py](launchpad/mcp_server.py), [launchpad/mcp_tools.py](launchpad/mcp_tools.py), [analyzer/models.py](analyzer/models.py)

- **User-Jailed File Paths for Nextflow Jobs** - Hardened `user_jail.py` with `_validate_id()` (rejects `/`, `\`, `.`, null bytes) and `_ensure_within_jail()` defense-in-depth - Nextflow executor now creates jailed work dirs at `AGOUTIC_DATA/users/{user_id}/{project_id}/{run_uuid}/` when `user_id` is present, falls back to flat legacy path - Analysis engine resolves jailed paths when looking up job work directories - Applied to: [cortex/user_jail.py](cortex/user_jail.py), [launchpad/nextflow_executor.py](launchpad/nextflow_executor.py), [analyzer/analysis_engine.py](analyzer/analysis_engine.py)

- **Cookie Hardening** - `Secure` flag is now environment-gated: `secure=True` when `ENVIRONMENT != "development"` - `httponly` and `samesite=lax` were already set - Added `ENVIRONMENT` config variable (defaults to `"development"`) - Applied to: [cortex/auth.py](cortex/auth.py), [cortex/config.py](cortex/config.py)

- **Database Migration Script** - `migrate_hardening.py`

### Fixed

- **CSV/TSV File Parsing Returns Path Only, No Contents** - Problem: When user asks "parse jamshid.mm39_final_stats.csv", the system calls `find_file` (returns the relative path) but never follows up with `parse_csv_file` to read the actual data - Root cause (v1): `_auto_generate_data_calls()` only generated a `find_file` call with no chaining - Root cause (v2): Even after adding `_chain` key to auto_calls, the LLM generates its own `[[DATA_CALL: ... tool=find_file ...]]` tag (trained by skill files like DOGME_QUICK_WORKFLOW_GUIDE.md), which sets `has_any_tags = True` and **skips auto_calls entirely**

- **Job Progress Not Auto-Refreshing During Nextflow Runs** - Problem: User had to manually refresh the browser to see Nextflow job progress updates (running tasks, completion status), even with "Live Stream" toggle on - Root cause (v1): Auto-refresh depended on the "Live Stream" toggle. No forced refresh for running jobs. - Root cause (v2): Used `locals().get("_has_running_job")` to pass the running-job flag to the auto-refresh section at script bottom

### Added

- **Job Duration & Last Update Timestamps in Nextflow Progress Display** - Shows elapsed runtime ("Running for 2m 34s") calculated from block creation time - Shows recency of last status poll ("Updated 3s ago") from a `last_updated` timestamp stored in the EXECUTION_JOB payload on each poll cycle - Applied to: [ui/appUI.py](ui/appUI.py), [cortex/app.py](cortex/app.py)

## [Unreleased] - 2026-02-16

### Added

- **Real-Time Chat Progress Feedback** - Problem: With local LLMs, responses can take a long time and the UI showed no indication that anything was happening - Solution: Added a progress tracking system with polling-based status updates - Backend: Added `_emit_progress()` status tracker, `request_id` field on `ChatRequest`, and `/chat/status/{request_id}` polling endpoint. Status events emitted at key stages: "Thinking...", "Switching skill...", "Querying [service]...", "Analyzing results...", "Done" - Frontend: Chat input now uses a background thread + `st.status()` widget that polls the backend every 1.5s for progress updates. Shows animated stage icons, descriptive messages, and elapsed time

- **Pre-LLM Automatic Skill Switch Detection** - Problem: LLM sometimes fails to emit `[[SKILL_SWITCH_TO:...]]` tags when the user's message clearly requires a different skill

### Fixed

- **Post-Job Analysis Summary Showing "Total files: 0"** - Problem: After a Dogme job completes successfully (11 tasks, files clearly present in work directory), the auto-triggered analysis shows "Total files: 0" with all category counts at 0 - Root cause: The `get_analysis_summary` MCP tool in analyzer returns `{"success": True, "summary": "<markdown text>"}`

- **LLM Re-querying Instead of Using Data Already in Conversation** - Problem: After fetching data (file listings, search results), follow-up questions like "which of them are methylated reads?" or "what are the accessions for the long read RNA-seq samples?" cause the LLM to make new API calls instead of reading its own previous response - Root cause (v1): Keyword-based detection (`_filter_words`, `_referential`) was too narrow

- **LLM Hallucinating Wrong ENCODE Tool Names in DATA_CALL Tags** - Problem: When asking "how many bam files do you have for ENCSR160HKZ?", the LLM generated `tool=get_files_types` instead of the correct `tool=get_files_by_type`, causing "Unknown tool" MCP errors - Root cause: The existing fallback patterns only fix plain-text tool invocations (e.g., `Get Files By Type(...)` → DATA_CALL), but cannot fix wrong tool names *inside* properly-formatted DATA_CALL tags - Solution: Added a `tool_aliases` map in `atlas/config.py` that maps ~25 common LLM-hallucinated tool names to their correct equivalents. Applied during DATA_CALL parsing in `cortex/app.py` before tool execution - Covers aliases for all ENCODE tools: `get_files_by_type`, `get_file_types`, `get_files_summary`, `get_experiment`, `search_by_biosample`, `search_by_target`, `search_by_organism`, `get_available_output_types`, `get_all_metadata`, `list_experiments`, `get_cache_stats`, `get_server_info` - Also added `get_all_tool_aliases()` helper function to `atlas/config.py` - Applied to: [cortex/app.py](cortex/app.py), [atlas/config.py](atlas/config.py)

- **LLM Failing to Call File-Level ENCODE Tools Correctly** - Problem: `get_file_metadata` and `get_file_url` require TWO parameters

- **ENCODE Follow-Up Questions Losing Biosample Context** - Problem: After asking "how many C2C12 experiments?" then "give me the accession for the long read RNA-seq samples", the LLM searched ALL long-read RNA-seq across ENCODE instead of filtering to C2C12 - Root cause: The LLM generated its own DATA_CALL tags (so auto-generation didn't trigger), but searched without the C2C12 filter because the current message had no explicit biosample mention - Solution: Extended `_inject_job_context()` to also handle ENCODE skills

- **Conversational References Losing Context for Biosample Queries** - Problem: When user asked "how many C2C12 experiments?" then "give me a list of them", the LLM asked for clarification instead of re-running the C2C12 search - Root cause: The auto-generation safety net only scanned conversation history for ENCSR accessions, not biosample terms. "list of them" has no biosample keyword in the current message, so the biosample detection path never triggered - Solution: Extended `_auto_generate_data_calls()` to scan conversation history for previous biosample search terms when referential words ("them", "those", "list", etc.) are detected - Also added "list" to the referential words list (previously missing) - Refactored biosamples dict to function scope so it's reusable across detection paths - Result: "give me a list of them" after a biosample search now re-emits the same search automatically - Applied to: [cortex/app.py](cortex/app.py)

### Added

- **Job Context Injection for Dogme Skills** - System now automatically injects UUID and work directory context into user messages for Dogme analysis skills - Added `_inject_job_context()` function that prepends `[CONTEXT: run_uuid=..., work_dir=...]` to messages - Added `_extract_job_context_from_history()` to scan conversation history for most recent job context - Context injection happens before both initial `engine.think()` call and skill-switch re-run - Eliminates LLM wasting time searching conversation history for known information - Applied to: [cortex/app.py](cortex/app.py)

- **Auto-Generation Safety Net for File Parsing** - Extended `_auto_generate_data_calls()` with Dogme file-parsing patterns - When in a Dogme skill and user says "parse {filename}", auto-generates `find_file` DATA_CALL if LLM fails to - Extracts filename from user message using pattern matching for common verbs (parse, show, read, etc.) - Retrieves UUID from conversation history automatically - Provides failsafe similar to existing ENCODE auto-generation - Applied to: [cortex/app.py](cortex/app.py)

### Changed

- **Updated Dogme Skill Instructions for Context Injection** - Updated all Dogme skills (DNA, RNA, cDNA) to check for `[CONTEXT: run_uuid=...]` first - Modified "Retrieving Filename" section in DOGME_QUICK_WORKFLOW_GUIDE.md to emphasize context injection - Changed STEP 2 from "Extract UUID from current conversation" to "Extract UUID from context injection or conversation" - Skills now instruct LLM to use injected context directly instead of searching conversation history - Applied to: [DOGME_QUICK_WORKFLOW_GUIDE.md](skills/DOGME_QUICK_WORKFLOW_GUIDE.md), [Dogme_cDNA.md](skills/Dogme_cDNA.md), [Dogme_DNA.md](skills/Dogme_DNA.md), [Dogme_RNA.md](skills/Dogme_RNA.md)

- **Enhanced UI Project Switch Cleanup** - Added cleanup of stale widget keys (form keys, checkbox keys, rejection state) on new project creation - Clears all keys with prefixes: `params_form_`, `logs_`, `rejecting_`, `rejection_reason_`, `submit_reject_`, `cancel_reject_` - Prevents form widget state from previous projects leaking into new projects - Applied to: [ui/appUI.py](ui/appUI.py)

- **Improved UI Render Defense Against Ghost Messages** - Modified `render_block()` to accept `expected_project_id` parameter - Silently skips rendering blocks that don't match the expected project ID (last line of defense) - Wrapped chat rendering in `st.empty()` container which is fully replaced on every rerun (no DOM reuse) - Added `_suppress_auto_refresh` counter (3 cycles) after project switch to allow frontend to settle - Prevents Streamlit DOM-reuse artifacts where old messages briefly appear after project switch - Applied to: [ui/appUI.py](ui/appUI.py)

### Fixed

- **UI Project Switching Race Condition** - Problem: When creating a new project, old chat messages from previous project would briefly appear below welcome message - Root cause: Streamlit's `text_input` widget takes 2-3 rerun cycles to sync its displayed value after programmatic changes. During this window, the old project ID in the text input triggered sync logic that silently reverted `active_project_id` back to the old project, which then fetched and rendered the old project's blocks - Solution: - Replaced boolean `_project_just_switched` flag with grace counter `_switch_grace_reruns = 3` that survives multiple rerun cycles - Added `_welcome_sent_for` to cleanup list on new project creation and all project-switch paths - Added defensive render-time filter to drop any block whose `project_id` doesn't match active project - Result: New projects now show only their own messages with no ghost messages from previous projects - Applied to: [ui/appUI.py](ui/appUI.py)

### Changed

- **Enhanced Analyzer Analysis File Discovery** - Extended file filtering to exclude both `work/` and `dor*/` subdirectories from analysis to prevent bloated file counts from Nextflow intermediate artifacts - Added intelligent key file identification based on analysis mode (DNA/RNA/cDNA) with mode-specific patterns - Modified `generate_analysis_summary()` to: - Filter displayed files to key result files only (qc_summary, stats, flagstat, gene_counts, etc.) - Keep full file counts in separate `all_file_counts` field for reference - Parse mode-specific files (gene_counts, transcript_counts for cDNA mode) - Extract key metrics from parsed reports into top-level `key_results` dict - Added `all_file_counts` field to `AnalysisSummary` schema to preserve total file statistics - Result: Analysis summaries now focus on important result files while maintaining awareness of total file counts - Applied to: [analyzer/analysis_engine.py](analyzer/analysis_engine.py), [analyzer/schemas.py](analyzer/schemas.py)

- **Improved Analysis Summary Formatting** - Changed `get_analysis_summary_tool` to return formatted markdown tables instead of raw JSON structures - Summary now includes: - Basic info table (Sample Name, Mode, Status, Work Directory) - File summary table (counts by file type) - Key results with availability status - Top 10 files from each category with size information - Natural language description of analysis results - Result: Analysis summaries are more readable and user-friendly, avoiding nested JSON truncation by MCP protocol - Applied to: [analyzer/mcp_tools.py](analyzer/mcp_tools.py)

## [Unreleased] - 2026-02-15

### Fixed

- **Welcome Skill Not Routing UUID + Parse Requests** - Problem: When user said "use UUID: X" + "parse filename", Welcome skill didn't route to analyze_job_results - Instead stayed in Welcome and said "I don't have tools to parse files" - Root cause: Welcome routing rules weren't looking for UUID patterns or "parse" keyword - Solution: Added explicit detection for: - "use UUID:" pattern - "parse {filename}" pattern - Combined UUID + parse requests - Now: Welcome immediately routes to analyze_job_results when UUID + parse request detected - Applied to: `Welcome.md`

- **UUID Corruption/Truncation by LLM (Known Issue with Enhanced Workaround)** - Problem: LLMs corrupt long UUIDs during transmission (truncation AND character scrambling) - Examples: - Truncation: `b954620b-a2c7-4474-9249-f31d8d55856f` → `b954620b-a2c7-4474-9249-f31d8d558566` (last 3 chars lost) - Character corruption: `b954620b-a2c7-4474-9249-f31d8d55856f` → `b954620b-a274-4474-9249-f31d8d55856f` (a2c7→a274) - Root cause: Model-level string corruption in transmission layer, not skill configuration - Workaround enhanced: Added UUID format validation (not just length check) - Now agent validates: - Length must be exactly 36 characters - Format must be 8-4-4-4-12 segments separated by dashes - All characters must be lowercase hex (0-9, a-f) - If "Job not found" error, agent compares tool's UUID char-by-char to original user input - If mismatch detected, agent re-reads original user input and uses correct UUID - Applied to: `Analyze_Job_Results.md`, `DOGME_QUICK_WORKFLOW_GUIDE.md`

- **MCP Tool Return Types – Protocol Serialization** - All Analyzer MCP tools were returning JSON strings via `_dumps()` instead of native Python dicts - FastMCP was aggressively summarizing/truncating nested responses, showing fields as `"..."` instead of values - Changed all 7 tools in `mcp_tools.py` to return `Dict[str, Any]` natively; removed all `_dumps()` calls - Updated tool decorator return types in `mcp_server.py` to declare `Dict[str, Any]` for proper protocol handling - Result: Response fields are no longer truncated; structured data is preserved through MCP protocol layer

- **File Discovery Response Structure – Avoiding Nested Object Truncation** - `find_file` response had a `matches` array with nested objects that MCP was truncating to `"..."` - Removed problematic `matches` array entirely from response - Added flat top-level fields: `paths` (simple string array), `primary_path` (first match for direct use) - Response now contains only: `success`, `run_uuid`, `search_term`, `file_count`, `paths`, `primary_path` - Eliminates confusion about which path to use and prevents field truncation

- **Missing `find_file` Tool Registration** - `find_file` tool was implemented in `mcp_tools.py` but not registered in `TOOL_REGISTRY` - Added `find_file_tool` to `TOOL_REGISTRY` and MCP server registration in `mcp_server.py` - Tool now available: `[[DATA_CALL: service=analyzer, tool=find_file, run_uuid=..., file_name=...]]`

- **File Path Resolution Error Messages** - Parse and read operations showed generic "File not found" error without diagnostic info - Enhanced error messages in `analysis_engine.py` for `read_file_content`, `parse_csv_file`, `parse_bed_file` - Error now shows both relative and absolute paths: `"File not found: {file_path} (absolute path: {full_path})"` - Enables rapid diagnosis of work directory or path resolution failures

### Changed

- **Enhanced UUID Validation in Analysis Skills** - Added explicit 36-character format validation to Analyze_Job_Results.md - Agent now checks UUID completeness before executing any tool calls - Detects and flags potential LLM truncation of UUIDs - Instructs agent to re-read original user input if truncation is suspected - Applied to: `Analyze_Job_Results.md`, `DOGME_QUICK_WORKFLOW_GUIDE.md`

- **Fixed Duplication in Dogme Skills Workflows** - Realized we had re-created duplication by putting STEP 1-4 in each skill file - Consolidated back: All detailed workflow steps now live in `DOGME_QUICK_WORKFLOW_GUIDE.md` - Each skill now just references the guide with brief mode-specific tool hints - True single source of truth

- **Enhanced STEP 4-5 (Results Presentation) in Shared Guide** - Expanded with explicit ❌ anti-patterns agent should never exhibit: - ❌ "The query did not return expected data" (it is there in `data` field) - ❌ "content is not provided in usable format" (extract and format it) - ❌ "results show metadata but actual content..." (extract `data` field immediately) - Added explicit presentation formats (markdown tables for CSV, tabular for BED, text blocks) - Added tool selection guide: when to use parse_csv_file vs parse_bed_file vs read_file_content - Result: Agent knows exactly what to do when parse succeeds

- **Auto-Load Referenced Skill Files (Generalized)** - Enhanced `_load_skill_text()` in `agent_engine.py` to detect and auto-load referenced .md files - Pattern: `[filename.md](filename.md)` automatically triggers file inclusion in skill context - When agent loads `run_dogme_dna`, it now gets both the skill AND the referenced `DOGME_QUICK_WORKFLOW_GUIDE.md` - Files are appended with clear section markers: `[INCLUDED REFERENCE: filename.md]` - Applies to any skill that references other markdown files using this pattern - Result: Agents have full context without manual file configuration

- **Consolidated Dogme Workflow Documentation** - Created `DOGME_QUICK_WORKFLOW_GUIDE.md` as single source of truth for all Dogme analysis workflow steps - Moved repetitive UUID verification (~300 lines) and directory prefix warnings from individual skills to shared guide - Refactored `Dogme_DNA.md` and `Dogme_RNA.md` to reference shared guide instead of duplicating sections - Kept mode-specific content: file types (bedMethyl/, modkit/, counts/) and interpretation guidance - Reduces documentation maintenance burden; all three Dogme skills now use consistent workflow reference - Applied to: `Dogme_DNA.md`, `Dogme_RNA.md`

- **Agent Workflow Routing for Specific File Requests** - Updated `Analyze_Job_Results.md` with "SPECIAL CASE: User Requests Specific File Parsing" section - When user says "parse [filename]", analyzeJobResults now routes immediately to appropriate Dogme skill - Prevents verbose workflow description loops; enables fast-track execution - Example: "parse jamshid.mm39_final_stats.csv" → Get mode → Route to Dogme_cDNA → Execute quick workflow

- **File Path Extraction in Dogme Skills** - Completely rewrote "Quick Workflow: User Asks to Parse a File" section in all three Dogme skills - Added 5-step critical workflow with explicit copy-paste examples - Enhanced "MOST COMMON MISTAKE" section showing 3 specific wrong patterns: - ❌ `file_path=jamshid.mm39_final_stats.csv` (dropped directory part) - ❌ `file_path=final_stats.csv` (extracted partial filename) - ❌ `file_path=Annot/jamshid...` (modified case) - Added bold ✅ CORRECT example: use EXACT copy of `primary_path` from find_file response - Applied to: `Dogme_DNA.md`, `Dogme_RNA.md`, `Dogme_cDNA.md`

- **MCP Tool Return Type Declarations** - Updated all `@mcp.tool()` decorators in `mcp_server.py` to declare `Dict[str, Any]` return types - Ensures FastMCP properly recognizes and serializes dict responses without truncation - Affected tools: `list_job_files`, `find_file`, `read_file_content`, `parse_csv_file`, `parse_bed_file`, `get_analysis_summary`, `categorize_job_files`

### Added

- **find_file MCP Tool** - New tool for locating files by partial name match in job work directories - Parameters: `run_uuid`, `file_name` (case-insensitive substring match) - Returns: Structured response with flat `paths` array and `primary_path` highlighting best match - Purpose: Enable quick file discovery without browsing full directory listings - Useful when response truncation makes `list_job_files` unusable for large directories

- **Skill Routing Pattern Template** - Added `skills/SKILL_ROUTING_PATTERN.md` as reusable template for defining skill boundaries - Provides standard "Skill Scope & Routing" section structure with ✅/❌ lists and examples - Used as template for all Dogme skills, Analyze_Job_Results, and ENCODE skills - Ensures consistent agent routing behavior across all skills

- **Skill Scope & Routing Sections** - Added comprehensive scope definitions to all Dogme skills (DNA, RNA, cDNA) - Added scope definitions to `Analyze_Job_Results.md` routing skill - Each section includes: ✅ what skill handles, ❌ what it doesn't, 🔀 routing rules with examples - Clarifies mode-specific analysis vs job submission vs ENCODE data vs local samples

### Documentation

- **Enhanced UUID Guidance in All Dogme Skills** - Added 🚨 CRITICAL section at top of Analysis Workflow in all three Dogme skills - Emphasizes: "Use ONLY the MOST RECENT UUID from current conversation" - Includes WRONG (old UUID) vs RIGHT (current UUID) examples - Prevents agent from using cached UUIDs from earlier in conversation history

- **SKILL_ROUTING_PATTERN.md** - Comprehensive documentation of skill scope/routing architecture - Lists 6 core skills with their scope boundaries and routing targets - Provides implementation guidelines for adding routing sections to new skills - Key principle: "Always route rather than refusing"

---

## [Unreleased] - 2026-02-14

### Fixed

- **Results Page – MCP Response Format Alignment** - Fixed UI to match MCP response field names: `total_size` → `total_size_bytes` (file listing), `workflow_type` → `mode` (summary). - Made `file_count`, `total_size_bytes`, and `files` accesses defensive with `.get()` to prevent `KeyError` crashes. - Fixed `_auto_trigger_analysis` to read `mode` field correctly from MCP summary. - Cortex analysis proxy endpoints remain MCP-based (consistent with the rest of the architecture).

- **MCP Tool Errors Silently Swallowed** - `_call_analyzer_tool` and `_call_launchpad_tool` now check the `success` field in MCP responses; if `false`, they raise a proper HTTP 422 with the error message instead of returning the error dict as a 200. - Added fallback guard in the results page UI to detect and display any MCP error that slips through. - This was the root cause of the "all N/A" results page

- **`datetime` Not JSON Serializable in MCP Tools** - `FileInfo.modified_time` (a `datetime` object) caused `json.dumps` to crash in all Analyzer MCP tools. - Added `_dumps()` helper in `analyzer/mcp_tools.py` with a `default` handler that converts `datetime` to ISO 8601 strings; replaced all 20+ `json.dumps` calls.

- **Skill Resets to "welcome" After Job Completion** - After auto-analysis created an `AGENT_PLAN` block post-job, follow-up messages (e.g. "parse qc_summary.csv") incorrectly reset skill to "welcome" because an older `APPROVAL_GATE` block existed. - Fixed skill resolution in `/chat` to compare sequence numbers: if the latest `AGENT_PLAN` is newer than the latest `APPROVAL_GATE`, continue with the agent's skill instead of resetting.

- **Wrong MCP Tool Names in Skill Files** - `Dogme_cDNA.md` and `Dogme_RNA.md` referenced `tool=parse_csv` instead of `tool=parse_csv_file` (the actual MCP tool name), causing tool-not-found errors when the LLM emitted DATA_CALL tags.

- **LLM Using Sample Name Instead of UUID for Analysis Tools** - Auto-analysis block didn't include the `run_uuid` in the markdown summary, so the LLM used the sample name (e.g., "Jamshid") instead of the actual UUID when calling Analyzer analysis tools. - Added `**Run UUID:** <backtick>uuid<backtick>` line to the auto-analysis summary. - Added explicit UUID-finding instructions to all three Dogme analysis skills (DNA, RNA, cDNA) with examples of how to extract the UUID from conversation history.

- **CSV/BED File Data Truncated to "..." in Results** - `_compact_dict` in `result_formatter.py` limited depth to 2, causing row data in parsed CSV/BED responses to show as `{"sample": "...", "n_reads": "..."}` instead of actual values. - Increased depth limit from 2 to 4 for analysis data (detected by presence of `columns`, `records`, or `preview_rows` keys in the response).

- **File Discovery Filtering Out Work Folder Files** - MCP tools in Analyzer now filter out files in the work/ and dor*/ directories to prevent bloated file counts from Nextflow intermediate artifacts. - Modified `discover_files()` in `analyzer/analysis_engine.py` to exclude files with paths starting with "work/" or "dor". - This affects all MCP tools: `get_analysis_summary`, `list_job_files`, `find_file`, and parsing tools.

### Documentation

- **Consolidated & Standardized Docs** - Moved various `*_IMPLEMENTATION.md` files to `archive/` to reduce clutter. - Standardized Atlas documentation: moved `ATLAS_IMPLEMENTATION.md` to `atlas/README.md`. - Consolidated Launchpad documentation: merged `FILES.md`, `DUAL_INTERFACE.md`, etc., into a single `launchpad/README.md`.

### Changed

- **UI Architecture: Cortex Abstraction Layer** - Refactored `ui/pages/results.py` to route all requests through Cortex proxy endpoints instead of calling Analyzer directly. - Added file download proxy endpoint (`/analysis/files/download`) to Cortex to stream files from Analyzer without exposing backend URLs. - Added `rest_url` field to Analyzer `SERVICE_REGISTRY` entry for REST-specific proxying. - All UI pages now exclusively communicate with Cortex; backend architecture is fully abstracted from the frontend.

- **Auto-Analysis on Job Completion** - When a Dogme job finishes, Cortex now automatically fetches the analysis summary from Analyzer and presents it in the chat. - The agent skill switches to the mode-specific analysis skill (DNA/RNA/cDNA) so follow-up questions use the right context. - Users see a file overview with counts and key results immediately, with suggested next steps.

- **Chat Management** - Added `DELETE /projects/{project_id}/blocks` endpoint to Cortex for clearing chat history. - Replaced the no-op "Force Clear" button with a working "Clear Chat" button that deletes all blocks from the database. - Added a "Refresh" button for manual reloads. - Long conversations now show only the last 30 messages by default, with a "Load older messages" button to page back.

### Added

- **Atlas Implementation** (2026-02-10) - Initial implementation of ENCODE search server - Integration with Cortex via MCP client - Result formatting for ENCODE data - Documentation: `ATLAS_IMPLEMENTATION.md`, `ATLAS_QUICKSTART.md` - Skills: ENCODE_LongRead.md, ENCODE_Search.md

- **Analyzer Implementation** (2026-02-09) - Initial implementation of analysis/QC server - Analysis engine for quality control workflows - MCP server interface for job result analysis - Database models and schemas for analysis storage - Documentation: `ANALYZER_IMPLEMENTATION.md`, `INSTALLATION.md`, `QUICKSTART.md` - Test scripts: `test_analyzer_direct.sh`, `test_analyzer_integration.sh`, `quick_qc_test.sh` - UI integration for displaying analysis results - New skill: Analyze_Job_Results.md

- **Second-Round LLM Processing** (2026-02-13) - Added capability for processing tool outputs with additional LLM rounds - Enhanced MCP client to support iterative processing - Improved result formatting in Atlas

- **Unified Logging System** (2026-02-13) - Centralized logging configuration in `common/logging_config.py` - Logging middleware in `common/logging_middleware.py` - Automatic log rotation and better log management - Server stopping and log rollover in `agoutic_servers.sh`

- **Multi-User Support** (2026-02-09) - Enhanced authentication and authorization - User jail/isolation functionality - Admin panel improvements - Multi-user configuration in Cortex and UI

### Changed

- **MCP Architecture Refactoring** (2026-02-11) - Moved MCP client code to common area (`common/mcp_client.py`) - Refactored Atlas, Launchpad, and Analyzer to use unified MCP interface - Consolidated MCP client implementations from individual servers - Updated Cortex agent engine to use common MCP client

- **Launchpad Job Launch Improvements** (2026-02-14) - Fixed job launching in Launchpad - Unified ENCODE and local sample intake workflows - Updated MCP server and tools for better job submission - Enhanced schema definitions for job parameters

- **Nextflow Integration** (2026-02-03, 2026-02-05) - Updated Nextflow executor for Launchpad - Enabled job submission from UI - Added Dogme pipeline options (DNA, RNA, cDNA) - Direct job submission script improvements

- **Skills Updates** (2026-02-13, 2026-02-14) - Updated ENCODE_Search skill with improved guidance - Refreshed all Dogme skills (DNA, RNA, cDNA) - Enhanced Welcome and Local_Sample_Intake skills - Updated Analyze_Job_Results skill

### Fixed

- **Environment Configuration** (2026-02-12) - Restored environment code in `.env` and `load_env.sh`

- **MCP for ENCODE** (2026-02-12) - Fixed MCP client issues in Atlas - Updated configuration for proper ENCODE integration - Updated `.gitignore` for better file management

- **Log Management** (2026-02-13) - Better server stopping mechanisms - Improved log rotation and rollover

## Summary – What Changed

### Message Processing Pipeline (Points 1, 4, 6, 7)

---
