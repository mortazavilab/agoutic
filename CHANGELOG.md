# Changelog - March 2026

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

Version was hardcoded in 5 separate places (`agoutic_servers.sh`, `ui/app.py` ×2,
`cortex/app.py` health endpoint, `README.md`) and frequently fell out of sync.

- **Single source of truth**: New `VERSION` file at repo root contains the version string.
- `ui/app.py` reads `VERSION` via `pathlib` and uses it in the page title and sidebar caption.
- `cortex/app.py` reads `VERSION` the same way; the `/health` endpoint now returns the live value.
- `agoutic_servers.sh` reads `VERSION` via `cat` instead of a hardcoded variable.
- `README.md` updated to 3.0.3.
- Files changed: `VERSION` (new), `ui/app.py`, `cortex/app.py`, `agoutic_servers.sh`, `README.md`

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
- Files changed: `ui/app.py` (`get_sanitized_blocks`, `_render_chat`),
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
- Files changed: `launchpad/config.py`, `launchpad/schemas.py`, `launchpad/nextflow_executor.py`, `launchpad/app.py`, `launchpad/mcp_server.py`, `launchpad/mcp_tools.py`, `cortex/app.py`, `ui/app.py`, `skills/Local_Sample_Intake.md`

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
  - Applied to: [cortex/app.py](cortex/app.py), [ui/app.py](ui/app.py)

- **Per-file byte-level download progress**
  - `_download_files_background()` now tracks `expected_total_bytes`, `current_file_bytes`, `current_file_expected` from `size_bytes` metadata or `Content-Length` header. Progress updates are throttled to every 0.5s to avoid DB spam. UI shows a Streamlit progress bar with MB downloaded/expected for the current file, with fallback to overall byte progress or file-count progress.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

- **EXECUTION_JOB block shows `workflowN` folder name alongside run UUID**
  - Cortex now stores the `work_directory` path (returned by Launchpad's `submit_dogme_job`) in the EXECUTION_JOB block payload. The UI extracts the folder name (e.g. `workflow2`) from the path and displays it as `📁 workflow2 | Run UUID: 3862fd86...`. Previously only the opaque run UUID was shown, with no visible connection to the actual directory on disk.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

- **Live token counter in sidebar**
  - `🪙 Tokens: 12,345` expander updates on every page rerun (API call moved outside the expander widget so it fetches unconditionally). Expander label shows the live total. Inside: Total + Completion metrics, daily line chart (when >1 day of data), and tracking-since date.
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

- **Chat UI — friendly 429 quota-exceeded message**
  - When the server returns HTTP 429, the chat input shows `st.warning()` with the used/limit figures extracted from the error JSON instead of a generic error.
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

### Fixed — Bar/histogram plot of DF shows bars at height 1 due to missing column names and wrong aggregation

- **`_resolve_df_by_id` constructed DataFrame without column names**
  - `pd.DataFrame(rows)` was called without passing `columns=`, so the DataFrame had integer column names `[0, 1]` instead of `["Category", "Count"]`. All `x_col`/`y_col` lookups failed (case-insensitive match returned nothing), the bar branch fell through to `value_counts()`, and every bar rendered at height 1.
  - Fix: now passes `columns=fdata.get("columns") or None` to the DataFrame constructor.
  - Applied to: [ui/app.py](ui/app.py)

- **Bar branch fell back to `value_counts()` even for pre-aggregated tables**
  - When `y_col=None` and `agg` is not `"count"`, the bar branch now checks if `x_col` is categorical and a numeric companion column exists. If so, it selects that column as `y` (preferring names like `count`, `value`, `n`, `total`, `freq`) instead of counting rows. This mirrors the same logic added to the histogram branch.
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

- **Debug info section per AGENT_PLAN block**
  - When debug mode is on, each assistant response shows a collapsed "🐛 Debug Info" expander with JSON diagnostic data including: target_df_id, detected filters (assay, output_type, file_type), injected DF names/row counts, suppressed API calls, LLM raw response preview, auto-generated calls, and more.
  - Applied to: [cortex/app.py](cortex/app.py), [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [cortex/app.py](cortex/app.py), [ui/app.py](ui/app.py)

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
  - Applied to: [cortex/app.py](cortex/app.py), [cortex/agent_engine.py](cortex/agent_engine.py), [atlas/result_formatter.py](atlas/result_formatter.py), [ui/app.py](ui/app.py), [skills/ENCODE_Search.md](skills/ENCODE_Search.md)

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
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

- **Project Name in Chat Title**
  - Title shows `🧬 Project Name` instead of raw UUID
  - Falls back to truncated UUID if name lookup fails
  - Projects cached in session state for instant title display
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

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
  - Applied to: [cortex/app.py](cortex/app.py), [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

### Added
- **Job Duration & Last Update Timestamps in Nextflow Progress Display**
  - Shows elapsed runtime ("Running for 2m 34s") calculated from block creation time
  - Shows recency of last status poll ("Updated 3s ago") from a `last_updated` timestamp stored in the EXECUTION_JOB payload on each poll cycle
  - Applied to: [ui/app.py](ui/app.py), [cortex/app.py](cortex/app.py)

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
  - Applied to: [cortex/app.py](cortex/app.py), [ui/app.py](ui/app.py)

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
  - Applied to: [ui/app.py](ui/app.py)

- **Improved UI Render Defense Against Ghost Messages**
  - Modified `render_block()` to accept `expected_project_id` parameter
  - Silently skips rendering blocks that don't match the expected project ID (last line of defense)
  - Wrapped chat rendering in `st.empty()` container which is fully replaced on every rerun (no DOM reuse)
  - Added `_suppress_auto_refresh` counter (3 cycles) after project switch to allow frontend to settle
  - Prevents Streamlit DOM-reuse artifacts where old messages briefly appear after project switch
  - Applied to: [ui/app.py](ui/app.py)

### Fixed
- **UI Project Switching Race Condition**
  - Problem: When creating a new project, old chat messages from previous project would briefly appear below welcome message
  - Root cause: Streamlit's `text_input` widget takes 2-3 rerun cycles to sync its displayed value after programmatic changes. During this window, the old project ID in the text input triggered sync logic that silently reverted `active_project_id` back to the old project, which then fetched and rendered the old project's blocks
  - Solution: 
    - Replaced boolean `_project_just_switched` flag with grace counter `_switch_grace_reruns = 3` that survives multiple rerun cycles
    - Added `_welcome_sent_for` to cleanup list on new project creation and all project-switch paths
    - Added defensive render-time filter to drop any block whose `project_id` doesn't match active project
  - Result: New projects now show only their own messages with no ghost messages from previous projects
  - Applied to: [ui/app.py](ui/app.py)

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
