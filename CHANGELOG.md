# Changelog - February 2026

## [Unreleased] - 2026-02-20

### Fixed — "list workflows" shows only workflow directories

- **`data/` and other non-workflow dirs excluded from "list workflows"**
  - "list workflows" listed everything in the project directory, including `data/`. Added `name_pattern` parameter to `discover_files()`, `list_job_files()`, and all MCP registrations. Server1 now passes `name_pattern="workflow*"` for "list workflows" calls so only `workflow1/`, `workflow2/`, etc. are returned.
  - Applied to: [server4/analysis_engine.py](server4/analysis_engine.py), [server4/mcp_tools.py](server4/mcp_tools.py), [server4/mcp_server.py](server4/mcp_server.py), [server1/app.py](server1/app.py)

### Fixed — Approval gate triggered for file browsing commands

- **Browsing commands no longer trigger APPROVAL_NEEDED gate**
  - When on a Dogme skill (e.g. `run_dogme_cdna`), the LLM emitted `[[APPROVAL_NEEDED]]` alongside a browsing response, causing the approval confirmation UI to appear. The browsing override now also sets `needs_approval = False` and `plot_specs = []`.
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — File Browsing Routing and Skill Independence

- **Browsing commands now skill-independent (not gated on Dogme skills)**
  - "list workflows", "list files", "list files in X" now work from ANY skill (ENCODE_Search, welcome, etc.) — not just Dogme analysis skills. Moved browsing logic to the top of `_auto_generate_data_calls()` so it runs first, before any ENCODE or other skill-specific catch-all can claim the message.
  - Applied to: [server1/app.py](server1/app.py)

- **Project directory as minimum context for file browsing**
  - Browsing commands previously required `work_dir` from EXECUTION_JOB history (only available after a Dogme job ran). Now the project directory (resolved from `_resolve_project_dir()`) is always available as a fallback. Added `project_dir` parameter to `_auto_generate_data_calls()` and `_validate_server4_params()`.
  - Applied to: [server1/app.py](server1/app.py)

- **"list workflows" from ENCODE skill routed to ENCODE instead of server4**
  - The ENCODE catch-all extracted "workflows" as a search term and generated `search_by_biosample`. Added a browsing-command override in the main chat handler that detects browsing patterns and forces the correct server4 auto-generated calls, suppressing any LLM-generated ENCODE tags.
  - Applied to: [server1/app.py](server1/app.py)

- **"list files in workflow1/annot" showed workflow2/annot instead**
  - The subfolder rescue prioritized the LLM's invented param (`subfolder=annot`, which lost the `workflow1/` prefix) over the user message. Swapped priority: user message parsing now comes first (preserving the full `workflow1/annot` path), with the LLM hint as fallback only.
  - Applied to: [server1/app.py](server1/app.py)

- **Previous ENCODE DataFrame shown alongside file listings**
  - When browsing from an ENCODE skill, `_inject_job_context()` injected the previous K562 dataframe. The browsing override cleared LLM tags but not `_injected_dfs`. Now also clears `_injected_dfs = {}` and `_injected_previous_data = False`.
  - Applied to: [server1/app.py](server1/app.py)

- **Smarter project-dir derivation for "list workflows"**
  - Previously blindly stripped the last path component to get the project dir, which would over-strip if `work_dir` was already the project dir. Now only strips `/workflowN/` suffix; otherwise treats `work_dir` as already project-level. Applied to both `_auto_generate_data_calls()` and `_validate_server4_params()`.
  - Applied to: [server1/app.py](server1/app.py)

### Added — File Browsing in System Prompt

- **File browsing instructions in system prompt (all skills)**
  - Added a "FILE BROWSING" section to the system prompt that's always visible regardless of active skill. Tells the LLM it can use `list_job_files` via DATA_CALL tags for browsing at any time, and that the system automatically resolves the correct project/workflow directory.
  - Applied to: [server1/agent_engine.py](server1/agent_engine.py)

### Fixed — Server 4 Placeholder `work_dir` and Local Sample Intake Bugs

- **Always force `work_dir` from conversation context — never trust the LLM**
  - Previously `_validate_server4_params()` only caught placeholder patterns (`/work_dir`, `{work_dir}`, `<work_dir>` etc.). The LLM also invents wrong paths like `/media/.../project`. Now the function always resolves `work_dir` from EXECUTION_JOB block history and overrides whatever the LLM supplied. Special case: "list workflows" trims to the project parent dir.
  - Applied to: [server1/app.py](server1/app.py)

- **Include `EXECUTION_JOB` blocks in history query**
  - The DB query that builds `history_blocks` only fetched `USER_MESSAGE` and `AGENT_PLAN` blocks. `_extract_job_context_from_history()` looks for `EXECUTION_JOB` blocks to find the authoritative `work_dir`, but never found any — falling back to fragile text parsing. Added `EXECUTION_JOB` to the query filter.
  - Applied to: [server1/app.py](server1/app.py)

- **Pass `history_blocks` to `_auto_generate_data_calls()`**
  - The safety-net auto-generation function called `_extract_job_context_from_history()` without `history_blocks`, so it could never find EXECUTION_JOB blocks even when they existed. Now `history_blocks` is threaded through from the call site.
  - Applied to: [server1/app.py](server1/app.py)

- **Server 4 tool aliases for hallucinated tool names**
  - The LLM frequently generates `list_workflows`, `find_files`, `parse_csv`, `read_file` etc. instead of the real MCP tool names. Added alias mapping: `list_workflows`→`list_job_files`, `find_files`→`find_file`, `parse_csv`→`parse_csv_file`, `read_file`→`read_file_content`, `analysis_summary`→`get_analysis_summary`, etc.
  - Applied to: [server1/app.py](server1/app.py)

- **Strip unknown Server 4 params (e.g. `sample=Jamshid`)**
  - The LLM adds invented params like `sample=Jamshid` that Server 4 tools don't accept, causing Pydantic validation errors. `_validate_server4_params()` now strips any param not in a per-tool allowlist before the MCP call.
  - Applied to: [server1/app.py](server1/app.py)

- **"list workflows" returns 502 files → now shows top-level dirs only**
  - When using `list_job_files` on the project dir, `discover_files()` recursively listed all files. Added `max_depth` parameter: `max_depth=1` lists only immediate children (workflow folders). Both the auto-generated safety-net call and `_validate_server4_params()` now set `max_depth=1` for "list workflows" commands.
  - Applied to: [server1/app.py](server1/app.py), [server4/analysis_engine.py](server4/analysis_engine.py), [server4/mcp_tools.py](server4/mcp_tools.py), [server4/mcp_server.py](server4/mcp_server.py)

- **File listing results showed "..." for all fields**
  - Server 4 `list_job_files` results are dicts with a `files` array. The result formatter's `_compact_dict()` truncated nested file objects to `"..."` at depth 2. Added a dedicated file-listing table formatter in `_format_data()` that renders files as a proper markdown table with Name, Path, and human-readable Size columns.
  - Applied to: [server2/result_formatter.py](server2/result_formatter.py)

- **`workflow1/` filtered out by `discover_files()` skip logic**
  - The `work/` and `dor*/` skip filter used `startswith("work")` which matched `workflow1`, `workflow2`, etc. Changed to exact `child.name == "work"` for directories and `parts[0] == "work"` for the recursive path, so only the Nextflow `work/` directory is skipped.
  - Applied to: [server4/analysis_engine.py](server4/analysis_engine.py)

- **"list files in annot" ignored the subfolder**
  - When the LLM generated `subfolder=annot` (an invented param), it was stripped as unknown, and the subpath was lost. Now `_validate_server4_params()` rescues subfolder hints from invented LLM params AND parses the user message for "list files in <subpath>" — then appends the subpath to `work_dir` via `_resolve_workflow_path()`.
  - Applied to: [server1/app.py](server1/app.py)

- **Sample name contaminated by file listing table headers**
  - The `sample_name` regex matched bare `Name` in markdown table headers (`| Name | Path | Size |`), capturing "| Path | Size |" as the sample name. Tightened to require `Sample Name` (not bare `Name`) and added `|` to the exclusion character class.
  - Applied to: [server1/app.py](server1/app.py)

- **Skip second-pass LLM for file browsing commands**
  - "list files" and "list workflows" now show the formatted file table directly instead of passing it through a second LLM call that would produce an unwanted summary/interpretation.
  - Applied to: [server1/app.py](server1/app.py)

- **Fixed `work_dir=/work_dir` and `work_dir={work_dir_from_context}` placeholders in Server 4 DATA_CALL tags** *(superseded by always-force fix above)*
  - When the LLM copies template variables from skill docs, it emits literal strings like `/work_dir`, `<work_dir>`, or `{work_dir_from_context}` instead of the real workflow path, causing "Work directory not found" errors.
  - Applied to: [server1/app.py](server1/app.py)

- **Fixed CDNA misidentified as DNA in Local Sample Intake**
  - The `sample_type` keyword heuristic was gated by `if "sample_type" not in _collected`, so if a previous assistant message had echoed "Data Type: DNA" (wrong), the regex would extract "DNA" and the heuristic would never run. Now the user-message keyword heuristic ALWAYS runs and overrides regex-extracted values, since user messages are the source of truth.
  - Applied to: [server1/app.py](server1/app.py)

### Added — Workflow Browsing and Relative Path Support

- **"list workflows" command**
  - Detects "list workflows" / "show workflows" in user messages and auto-generates a `list_job_files` call on the project base directory (parent of the current workflow). Allows users to see all workflow folders (workflow1, workflow2, etc.) in their project.
  - Applied to: [server1/app.py](server1/app.py)

- **"list files" command with subfolder support**
  - Detects "list files" / "list files in workflow2/annot" patterns. Resolves the path using `_resolve_workflow_path()` which handles: empty subpath → current workflow, "workflow2" → that workflow's dir, "workflow2/annot" → that workflow's annot/ subdir, "annot" → current workflow's annot/ subdir.
  - Applied to: [server1/app.py](server1/app.py)

- **Relative path file parsing**
  - "parse annot/File.csv" and "parse workflow2/annot/File.csv" now work. Added `_resolve_file_path()` which splits user paths, checks if the first component is a known workflow folder name, resolves to the correct `work_dir`, and extracts the basename for `find_file` searches.
  - Applied to: [server1/app.py](server1/app.py)

- **Removed template variables from skill docs**
  - Replaced all `{work_dir}`, `{work_dir_from_context}`, and `{tool}` template variables in `DOGME_QUICK_WORKFLOW_GUIDE.md` with concrete example paths and explicit "NEVER use template variables" warnings. Added "Browsing Commands" reference section.
  - Applied to: [skills/DOGME_QUICK_WORKFLOW_GUIDE.md](skills/DOGME_QUICK_WORKFLOW_GUIDE.md)

- **Fixed sample_name not extracted from current message**
  - The "called <name>" heuristic only scanned `conversation_history`, not the current message. On the first turn (empty history), `sample_name` was never collected even when the user said "called Jamshid", leaving the LLM to extract it (unreliably).
  - Applied to: [server1/app.py](server1/app.py)

- **Stronger context injection when all 4 fields are collected**
  - When all 4 required fields (sample_name, path, sample_type, reference_genome) are extracted, the `[CONTEXT]` line now says "Use these EXACT values" with each field on its own line and specifies the pipeline name ("Dogme CDNA"), rather than a generic "Do NOT re-ask" message that the LLM would ignore.
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — ENCODE Search Context Injection and Hallucination Bugs

- **Replaced hardcoded `biosample_keywords` with heuristic detection**
  - Previously, `server1/app.py` used a hardcoded set of 10 biosamples (K562, HeLa, etc.) to determine if a query was a "new search" or a "follow-up". Unknown cell lines (like HL-60) were incorrectly treated as follow-ups, causing the system to inject stale data from previous queries (e.g., MEL) and hallucinate answers based on the wrong dataset.
  - Replaced the hardcoded list with a three-layer heuristic:
    1. **New-query patterns**: Regex matches for explicit searches (e.g., "how many X experiments", "search encode for X").
    2. **Follow-up signals**: Referential language (e.g., "of them", "those", "DF3") that overrides new-query patterns.
    3. **Extracted-term vs. previous DFs**: Extracts the search term and checks if it appears in previous dataframe labels. If it's a new subject, it's treated as a new query.
  - Added pronouns ("them", "those", "these", etc.) to `_ENCODE_STOP_WORDS` so the fallback tokenizer doesn't treat them as search terms.
  - Applied to: [server1/app.py](server1/app.py)

- **Fixed missing `visible: True` metadata on fresh ENCODE search dataframes**
  - When a fresh `search_by_biosample` API call returned results, the resulting dataframe was stored without the `visible: True` metadata flag.
  - The auto-selector for follow-up queries (which looks for the most recent visible dataframe) ignored these fresh dataframes and fell back to older, explicitly visible dataframes (like injected filtered results).
  - Added `"visible": True` to the metadata of all `_SEARCH_TOOLS` dataframes so they can be correctly targeted by follow-up queries.
  - Applied to: [server1/app.py](server1/app.py)

- **Replaced exhaustive `biosamples` dict with catch-all extractor**
  - The safety net (`_auto_generate_data_calls`) previously relied on a hardcoded dictionary of known biosamples to generate API calls. If a term wasn't in the dict, the LLM would hallucinate a response without making an API call.
  - Replaced the exhaustive dict with a slim `_KNOWN_ORGANISMS` dict (used only for organism hints) and a new `_extract_encode_search_term()` regex-based catch-all extractor that handles ANY biosample name.
  - Added a hallucination guard: if no tool was executed but the LLM claims "there are N experiments" or "interactive table below", the response is stripped and a disambiguation message is shown.
  - Applied to: [server1/app.py](server1/app.py)

- **Fixed organism auto-addition and missing `search_term`**
  - The system was incorrectly auto-adding `organism=Homo sapiens` to mouse cell lines (like MEL) and placing the cell line name in `assay_title` instead of `search_term`.
  - Created `_validate_encode_params()` to fix missing `search_term` by moving non-assay values from `assay_title`, and to strip `organism` unless the user explicitly mentioned a species.
  - Applied to: [server1/app.py](server1/app.py)

- **Added tool aliases and deduplication**
  - Added `"search": "search_by_biosample"` to `tool_aliases` and `Search (...)` to `fallback_patterns` to fix "Unknown tool: search" errors.
  - Added a deduplication step after collecting all tool calls to prevent duplicate dataframes from appearing in the UI.
  - Applied to: [server1/app.py](server1/app.py), [server2/config.py](server2/config.py)

- **Updated ENCODE and Local Sample Intake skills**
  - Updated `ENCODE_Search.md` to instruct the LLM not to limit itself to known terms and to never invent a count.
  - Updated `Local_Sample_Intake.md` with anti-step directives ("DO NOT describe your reasoning steps"), fixed field name mismatches (`data_type`→`sample_type`, `data_path`→`path`), and added mouse/human genome inference.
  - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md), [skills/Local_Sample_Intake.md](skills/Local_Sample_Intake.md)

## [Unreleased] - 2026-02-19

### Changed — Workflow Directory (`work_dir`) Replaces `run_uuid` as Primary Identifier

- **All Server 4 analysis tools now accept `work_dir` as the primary parameter**
  - `work_dir` (absolute path to the workflow folder, e.g. `.../users/alice/project/workflow1/`) is now the preferred way to identify which job's files to access. `run_uuid` is retained as a legacy fallback for backward compatibility.
  - New `resolve_work_dir(work_dir, run_uuid)` function in `analysis_engine.py` acts as a central resolver: tries `work_dir` first (direct path), falls back to UUID-based DB lookup.
  - All 7 MCP tool functions (`list_job_files`, `find_file`, `read_file_content`, `parse_csv_file`, `parse_bed_file`, `get_analysis_summary`, `categorize_job_files`) updated with `work_dir` as first parameter.
  - All 7 MCP protocol wrappers in `mcp_server.py` updated.
  - Response dicts now return `work_dir` instead of `run_uuid`.
  - TOOL_SCHEMAS rewritten with `work_dir` documented as preferred.
  - Applied to: [server4/analysis_engine.py](server4/analysis_engine.py), [server4/mcp_tools.py](server4/mcp_tools.py), [server4/mcp_server.py](server4/mcp_server.py), [server4/schemas.py](server4/schemas.py)

- **Context injection now provides workflow directories instead of UUIDs**
  - `_extract_job_context_from_history()` — rewritten to scan history blocks for EXECUTION_JOB entries and build a `workflows` list with `{work_dir, sample_name, mode, run_uuid}` for each job. Previously only extracted the most recent UUID.
  - `_inject_job_context()` Dogme section — injects ALL workflow dirs with sample names. Single workflow: `[CONTEXT: work_dir=..., sample=...]`. Multiple workflows: enumerates each with folder name, sample, and mode.
  - Applied to: [server1/app.py](server1/app.py)

- **`_auto_generate_data_calls()` uses `work_dir` for file operations**
  - DATA_CALL find_file calls now use `work_dir=` instead of `run_uuid=`.
  - Smart matching: when multiple workflows exist, matches filename prefix to sample_name to pick the correct workflow directory.
  - Chained tool calls extract `work_dir` from find_file response.
  - Applied to: [server1/app.py](server1/app.py)

- **Auto-analysis no longer shows UUIDs to users**
  - `_auto_trigger_analysis()` LLM prompt uses "Work directory:" instead of "Run UUID:".
  - Analysis header shows "**Workflow:** workflowN" instead of UUID.
  - `_build_static_analysis_summary()` fallback template shows workflow folder name.
  - Applied to: [server1/app.py](server1/app.py)

- **All skill docs updated to use `work_dir` instead of `run_uuid`**
  - `DOGME_QUICK_WORKFLOW_GUIDE.md` — UUID validation section replaced with simpler work_dir guidance; all DATA_CALL examples use `work_dir=`.
  - `Dogme_cDNA.md`, `Dogme_DNA.md`, `Dogme_RNA.md` — context injection references, find_file examples, and DATA_CALL templates updated.
  - `Analyze_Job_Results.md` — inputs, DATA_CALLs, API endpoint docs, QC report template, and information gathering section updated.
  - `Local_Sample_Intake.md` — routing condition updated.
  - Applied to: [skills/](skills/)

### Added — LLM-Powered Auto-Analysis After Job Completion

- **`_auto_trigger_analysis()` now passes results through the LLM**
  - Previously, when a Dogme job completed, the auto-analysis step built a static markdown template listing file counts and filenames — no LLM was involved (`model: "system"`). Now the function fetches structured data from Server 4 **and** parses key CSV files (`final_stats`, `qc_summary`) via `parse_csv_file` MCP calls, then passes everything to the LLM for an intelligent first interpretation.
  - The LLM receives a structured context block (file inventory + parsed CSV tables) and is asked to highlight key metrics, QC concerns, and suggest next steps. Tags (`[[DATA_CALL:...]]`, `[[SKILL_SWITCH_TO:...]]`, `[[APPROVAL_NEEDED]]`) are stripped from the LLM response as a safety measure.
  - Token usage is tracked: the AGENT_PLAN block now includes a `tokens` payload with `prompt_tokens`, `completion_tokens`, `total_tokens`, and `model` — consistent with regular chat turns.
  - Both the system USER_MESSAGE and the assistant AGENT_PLAN are saved to conversation history (`save_conversation_message`), so follow-up turns see the auto-analysis context.
  - **Graceful fallback**: if the LLM call fails (Ollama down, timeout, etc.) the original static template is used and `model` is set to `"system"` with zero token counts.
  - Applied to: [server1/app.py](server1/app.py)

- **`model` field added to `EXECUTION_JOB` block payload**
  - The EXECUTION_JOB block now carries the `model` key from the approval gate, so `_auto_trigger_analysis` can instantiate `AgentEngine` with the same model the user selected for the conversation.
  - Applied to: [server1/app.py](server1/app.py)

- **Helper functions extracted for clarity**
  - `_build_auto_analysis_context()` — builds a structured text context (file inventory + markdown tables from parsed CSVs) for the LLM prompt.
  - `_build_static_analysis_summary()` — the original static template, now a standalone function used as the fallback.
  - Applied to: [server1/app.py](server1/app.py)

### Added — `search_by_assay` MCP Tool (Server 2 extension)

- **`server2/mcp_server.py` — extends ENCODELIB without modifying it**
  - Added `search_by_assay(assay_title, organism=None, target=None, exclude_revoked=True)` as a new `@server.tool()` registered on ENCODELIB's existing FastMCP `server` instance.
  - When `organism` is omitted, both `Homo sapiens` and `Mus musculus` are queried and results are merged into a single response with `total`, `human_count`, `mouse_count`, `human`, and `mouse` keys.
  - When `organism` is specified, returns `{total, assay_title, organism, experiments}` for the single organism.
  - Imports `server` and `get_encode_instance` from ENCODELIB's `encode_server` module; no changes to ENCODELIB itself.
  - Applied to: [server2/mcp_server.py](server2/mcp_server.py) *(new file)*

- **`server2/launch_encode.py` updated to load extended server**
  - Previously imported directly from ENCODELIB's `encode_server`. Now imports `server` from `server2.mcp_server` (after ENCODELIB is added to `sys.path`), so all ENCODELIB tools plus `search_by_assay` are registered before the HTTP server starts.
  - Applied to: [server2/launch_encode.py](server2/launch_encode.py)

### Fixed — ENCODE Agent Tool Misrouting

- **Structural guard: non-ENCSR accessions redirect to correct search tool**
  - `_correct_tool_routing()` in the chat endpoint now applies a structural check rather than a name-list check: any `accession` parameter passed to `get_experiment` that does not match `^ENCSR[A-Z0-9]{6}$` is redirected to the appropriate search tool. This handles unknown cell lines and biosample terms regardless of naming convention.
  - Added `_ENCSR_PATTERN` and `_ENCFF_PATTERN` compiled regexes; `_ENCFF_PATTERN` similarly redirects `get_experiment` calls with ENCFF accessions to `get_file_metadata`.
  - Applied to: [server1/app.py](server1/app.py)

- **Assay-only queries routed to `search_by_assay`**
  - Queries such as *"how many RNA-seq experiments are in ENCODE"* previously had no routing path and sometimes triggered wrong tool calls. `_correct_tool_routing()` now has a **Case A** branch: when the apparent search term looks like an assay name (detected via `_looks_like_assay()`) with no biosample context, the tool is redirected to `search_by_assay`.
  - `_auto_generate_data_calls()` safety net likewise emits `search_by_assay` (not `search_by_organism`) for assay-only fallback.
  - Added helpers: `_looks_like_assay(s)`, `_ASSAY_INDICATORS` tuple, `_ENCODE_ASSAY_ALIASES` map (`rna-seq` → `total RNA-seq`, etc.).
  - Applied to: [server1/app.py](server1/app.py)

- **`ENCODE_Search.md` skill updated with anti-pattern guard and new tool docs**
  - Added `⛔ NEVER` block listing 4 wrong tool-call patterns and 3 correct alternatives.
  - Decision rule updated: *cell line → `search_by_biosample`; assay only → `search_by_assay`; ENCSR accession → `get_experiment`*.
  - Quick-reference table gains 3 `search_by_assay` examples.
  - `search_by_assay` parameter docs added to Available Tools section.
  - Applied to: [skills/ENCODE_Search.md](skills/ENCODE_Search.md)

### Added — Username & Slug System

- **`username` column on `User` model**
  - New nullable `TEXT` column with a conditional unique index (`WHERE username IS NOT NULL`). Distinct from `google_id` and `email` — a short, readable handle used for filesystem paths and display.
  - Applied to: [server1/models.py](server1/models.py)

- **`server1/migrate_usernames.py` — idempotent migration**
  - Adds `users.username` via `ALTER TABLE` (no UNIQUE constraint on the ALTER, avoiding SQLite limitations) then creates a `CREATE UNIQUE INDEX IF NOT EXISTS ... WHERE username IS NOT NULL` separately.
  - Applied to: [server1/migrate_usernames.py](server1/migrate_usernames.py) *(new file)*

- **`server1/set_usernames.py` — CLI for username assignment**
  - `auto` sub-command derives slugified usernames from email prefixes for all users who have none.
  - `set <user_id> <username>` assigns a specific username.
  - `slugify-projects` normalises existing project names to URL-safe slugs.
  - Applied to: [server1/set_usernames.py](server1/set_usernames.py) *(new file)*

- **Filesystem paths use `username/project-slug/` hierarchy**
  - User-jailed paths now resolve to `$AGOUTIC_DATA/users/{username}/{project_slug}/` (falling back to `user_{id}` if username is not yet set).
  - Workflow directories use `{username}/{project_slug}/workflow{N}/`.
  - Applied to: [server1/user_jail.py](server1/user_jail.py), [server3/nextflow_executor.py](server3/nextflow_executor.py), [server4/config.py](server4/config.py)

- **Admin endpoints for username management**
  - `PATCH /admin/users/{user_id}/username` — set or clear a user's username.
  - `GET /admin/users` — list now includes `username` field.
  - Applied to: [server1/admin.py](server1/admin.py)

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
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — Parameter Extraction Leaks Previous Sample Names Into New Submissions

- **`extract_job_parameters_from_conversation()` scoped to the most recent submission cycle**
  - Previously the function scanned **all** blocks in the project and concatenated every user message into one string. The regex `r'called\s+(\w+)'` did `re.search` which finds the **first** match — so "called Jamshid" from the original submission always won over "called Jamshid2" or "called retest" from later submissions in the same project.
  - Now the function finds the last `EXECUTION_JOB` or approved `APPROVAL_GATE` block as a **boundary marker** and only considers blocks after that point. This ensures each new submission starts fresh without contamination from prior jobs.
  - Explicit name extraction patterns (`called X`, `named X`, etc.) now iterate user messages in **reverse order** (most recent first), so the latest submission request always wins.
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — New Sample Submission Blocked While on Dogme Analysis Skill

- **`_auto_detect_skill_switch()` now switches to `analyze_local_sample` from Dogme analysis skills**
  - Previously, the pre-LLM skill switch excluded `run_dogme_dna`, `run_dogme_rna`, and `run_dogme_cdna` from the `analyze_local_sample` detection to avoid false positives. This meant a user saying "Analyze the local mouse CDNA sample called Jamshid2 at /path" while on `run_dogme_cdna` would **not** switch skills — the LLM would interpret "Jamshid2" as a filename in the current job's results and call `find_file`, which fails.
  - The exclusion list now only contains `analyze_local_sample` itself. When the current skill is a Dogme analysis skill, a **stricter threshold** is applied: the message must have a local path **plus at least 2** of (analysis verb, sample keyword, data type keyword). From non-Dogme skills, the original threshold of path + 1 signal applies. This prevents false positives on messages like "parse /path/to/result.csv" while correctly catching "Analyze the local CDNA sample at /path".
  - Applied to: [server1/app.py](server1/app.py)

### Improved — Block Headers Show Project Name and Workflow Folder

- **Block metadata header shows project name instead of UUID**
  - `render_block()` in the UI now resolves the project name from the cached projects list for the `📌 Proj:` header. Falls back to a truncated UUID (`c7f75875…`) if the project isn't in the cache. Previously displayed the raw UUID (`c7f75875-b7f8-4835-8f94-28fd4f8090bf`).
  - Applied to: [ui/app.py](ui/app.py)

- **EXECUTION_JOB block shows `workflowN` folder name alongside run UUID**
  - Server 1 now stores the `work_directory` path (returned by Server 3's `submit_dogme_job`) in the EXECUTION_JOB block payload. The UI extracts the folder name (e.g. `workflow2`) from the path and displays it as `📁 workflow2 | Run UUID: 3862fd86...`. Previously only the opaque run UUID was shown, with no visible connection to the actual directory on disk.
  - Applied to: [server1/app.py](server1/app.py), [ui/app.py](ui/app.py)

### Fixed — `submit_dogme_job` MCP Wrapper Missing `username` / `project_slug`

- **MCP tool registration updated to pass through `username` and `project_slug`**
  - The `submit_dogme_job()` wrapper in `mcp_server.py` defines the parameters exposed via FastMCP. When `username` and `project_slug` were added to `mcp_tools.py`'s `Server3MCPTools.submit_dogme_job()`, the MCP wrapper was never updated to match. This caused FastMCP to reject `username` and `project_slug` as "unexpected keyword arguments" when Server 1 called the tool after the approval gate.
  - Added `username: str | None = None` and `project_slug: str | None = None` to both the wrapper function signature and the inner `await tools.submit_dogme_job(...)` call.
  - Applied to: [server3/mcp_server.py](server3/mcp_server.py)

### Fixed — `find_file` JSON Echo Loop (weak-model regression)

- **Fix A — Bare `find_file` JSON blocks stripped from conversation history**
  - When a weaker model (e.g. `devstral-2`) emits a `find_file` result JSON verbatim instead of a `[[DATA_CALL:...]]` tag, that raw JSON was previously saved as an `AGENT_PLAN` block and reloaded into conversation history on the next turn. The LLM would then see it as a "completed action" and echo it again — creating an infinite loop.
  - The history builder in the `chat_with_agent` endpoint now detects AGENT_PLAN blocks whose entire content (after stripping `<details>` and code fences) parses as `{success: true, primary_path: ...}` JSON. Such blocks are excluded from `conversation_history` via `continue` rather than being appended.
  - Detection is loose: strips `` ```json `` fences, normalises whitespace, attempts `json.loads()` — only skips on clean parse. Narrative responses that happen to mention `"primary_path"` are not affected.
  - Applied to: [server1/app.py](server1/app.py)

- **Fix B — Recovery guard auto-chains `find_file` → `parse_csv_file` at response time**
  - When `all_results` is empty (no DATA_CALL tags fired) but `clean_markdown` looks like a bare `find_file` JSON echo, the endpoint now intercepts before the AGENT_PLAN block is saved and automatically executes the parse chain:
    1. Parses `primary_path` and `run_uuid` directly from the echoed JSON (no history scanning needed — the `find_file` response already carries `run_uuid`).
    2. Calls `_pick_file_tool(primary_path)` to select `parse_csv_file`, `parse_bed_file`, or `read_file_content`.
    3. Instantiates `MCPHttpClient(name="server4", ...)` and calls the parse tool with appropriate row/record limits.
    4. Injects the result into `all_results["server4"]` and clears `clean_markdown` — the existing `has_real_data → analyze_results` second-pass flow then runs normally with no code duplication.
  - If the parse call fails, the guard logs the error and falls through silently — the original (empty) response is preserved rather than crashing.
  - Applied to: [server1/app.py](server1/app.py)

### Added — Per-Message & Per-Conversation Token Tracking

- **`AgentEngine` now captures `response.usage` from every LLM call**
  - `think()` and `analyze_results()` both return `(content, usage_dict)` tuples instead of plain strings. A `_usage_to_dict()` helper normalises `prompt_tokens`, `completion_tokens`, `total_tokens` from the OpenAI usage object and returns zeros gracefully on errors or `None` usage objects.
  - Applied to: [server1/agent_engine.py](server1/agent_engine.py)

- **Token counts stored in two places per assistant turn**
  - Chat endpoint (`POST /chat`) unpacks both LLM passes (think + analyze_results), sums their usage into `_total_usage`, and:
    1. Embeds a `"tokens": {prompt_tokens, completion_tokens, total_tokens, model}` key in every `AGENT_PLAN` block's `payload_json` — so each block is self-describing with no join needed.
    2. Persists the same counts on the `ConversationMessage` row via updated `save_conversation_message()` signature (new optional `token_data` and `model_name` kwargs).
  - Applied to: [server1/app.py](server1/app.py)

- **Four new nullable columns on `ConversationMessage`**
  - `prompt_tokens INTEGER`, `completion_tokens INTEGER`, `total_tokens INTEGER`, `model_name TEXT` — all nullable so pre-migration rows are unaffected.
  - Applied to: [server1/models.py](server1/models.py)

- **Idempotent migration script**
  - `server1/migrate_token_tracking.py` adds the four columns with `PRAGMA table_info` guards (safe to re-run). Respects the `$AGOUTIC_DATA` environment variable identically to `server1/config.py`, so it works on Watson and local dev without modification. Accepts an optional path argument.
  - Applied to: [server1/migrate_token_tracking.py](server1/migrate_token_tracking.py)

- **`GET /user/token-usage` endpoint (authenticated, own data)**
  - Returns: `lifetime` totals, `by_conversation` list (project_id, title, token breakdown, last_message_at), `daily` time-series, and `tracking_since` (ISO date of earliest tracked message).
  - Applied to: [server1/app.py](server1/app.py)

- **`GET /admin/token-usage/summary` endpoint (admin-only)**
  - Returns per-user leaderboard sorted by total tokens consumed, plus a global daily time-series. Aggregates across all users via JOIN on `conversations → conversation_messages`.
  - Applied to: [server1/admin.py](server1/admin.py)

- **`GET /admin/token-usage` endpoint (admin-only, filterable)**
  - Optional `?user_id=` and `?project_id=` query params. Returns per-conversation breakdown and daily time-series for the matching scope.
  - Applied to: [server1/admin.py](server1/admin.py)

- **`GET /projects/{id}/stats` — `token_usage` field added**
  - Response now includes `token_usage: {prompt_tokens, completion_tokens, total_tokens}` aggregated from all conversations in the project.
  - Applied to: [server1/app.py](server1/app.py)

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
  - Applied to: [server1/models.py](server1/models.py)

- **Migration extended for `users.token_limit`**
  - `server1/migrate_token_tracking.py` gains a 5th idempotent migration step adding `users.token_limit`. Re-running the script skips already-present columns.
  - Applied to: [server1/migrate_token_tracking.py](server1/migrate_token_tracking.py)

- **`PATCH /admin/users/{user_id}/token-limit` endpoint (admin-only)**
  - Accepts `{"token_limit": <int or null>}`. Validates positive integer or null. Returns updated user record. Allows admins to set or clear a per-user cap without touching other user fields.
  - Applied to: [server1/admin.py](server1/admin.py)

- **`GET /admin/users` — `token_limit` field included**
  - `UserListItem` schema updated; list endpoint now surfaces `token_limit` for each user so the admin UI can display and edit it.
  - Applied to: [server1/admin.py](server1/admin.py)

- **Hard 429 enforcement in chat endpoint**
  - After `require_project_access()`, the chat endpoint queries the requesting user's lifetime `total_tokens`. If a `token_limit` is set and is exceeded, raises `HTTPException(429)` with structured detail `{error, message, tokens_used, token_limit}`. Admin users are exempt.
  - Applied to: [server1/app.py](server1/app.py)

- **`GET /user/token-usage` — `token_limit` field included**
  - Response now exposes the user's own `token_limit` (or `null` if unlimited) so the UI can render quota progress without a separate call.
  - Applied to: [server1/app.py](server1/app.py)

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
  - The agent can now request inline charts by embedding structured tags in its response: `[[PLOT: type=histogram df=DF1 x=mapq_score title=MAPQ Distribution]]`. Server 1 parses these tags, resolves the referenced DataFrame from conversation history, and creates an `AGENT_PLOT` block that the UI renders via `st.plotly_chart()`.
  - Supported chart types: `histogram`, `scatter`, `bar`, `box`, `heatmap`, `pie`
  - Applied to: [server1/app.py](server1/app.py)

- **`AGENT_PLOT` block rendering in UI**
  - New block type dispatched in `render_block()`. Renders inside a `st.chat_message("assistant", avatar="📊")` bubble. Calls `_render_plot_block()` which groups specs by `(df_id, type)` for multi-trace overlays and falls back to single-chart rendering via `_build_plotly_figure()`. Shows `st.warning()` if the referenced DF is not found.
  - Helper functions added: `_resolve_df_by_id()`, `_build_plotly_figure()`, `_render_plot_block()`
  - Applied to: [ui/app.py](ui/app.py)

- **Plotting instructions in LLM system prompt**
  - Large PLOTTING section injected into the system prompt (after `{data_call_block}`). Explicitly forbids Python code (❌ markers), shows correct `[[PLOT:...]]` syntax, lists all 6 chart types with required/optional params, and includes working examples.
  - OUTPUT FORMATTING RULE #3 added: "For plots/charts/visualizations, ONLY use `[[PLOT:...]]` tags. NEVER write Python code for plotting."
  - Applied to: [server1/agent_engine.py](server1/agent_engine.py)

- **Server-side code-to-plot fallback**
  - If the LLM writes a Python code block containing `matplotlib`/`plt.`/`plotly`/`px.` instead of a `[[PLOT:...]]` tag, Server 1 detects it, auto-generates a `[[PLOT:...]]` spec from the user's message (infers chart type and column from natural language), and strips the code and any "Here is…"/"Explanation:" boilerplate from the visible response.
  - Applied to: [server1/app.py](server1/app.py)

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
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — Dogme skills: DF reference in plot request treated as a file lookup

- **Inject DF metadata into context for dogme skills when a `DF<N>` reference is detected**
  - When a user said "plot a histogram of DF1" while a dogme skill (e.g. `run_dogme_cdna`) was active, the LLM called `find_file(file_name=DF1)` via a server4 DATA_CALL tag instead of emitting `[[PLOT: df=DF1, ...]]`. Root cause: `_inject_job_context` returned early for dogme skills after injecting run UUID context, so the LLM had no information that DF1 was an in-memory DataFrame.
  - Fix: The dogme skills branch now also checks for `DF\d+` references in the user message. If found, it searches history blocks for the matching DataFrame, extracts its label, columns, and row count, and appends a `[NOTE: DF<N> is an in-memory DataFrame — NOT a file to look up. Use [[PLOT:...]] tags.]` to the injected context sent to the LLM.
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — `[[PLOT:...]]` tag with natural-language content produces no chart

- **`[^\]]+` regex breaks on `]` inside tag content (e.g. `Columns: ['A', 'B']`)**
  - Root cause: `plot_tag_pattern = r'\[\[PLOT:\s*([^\]]+)\]\]'` uses `[^\]]+` which stops at the **first** `]` in the string. When the LLM writes `Columns: ['Category', 'Count']` inside a natural-language tag, the `]` after `'Count'` terminates the character class match early, the trailing `\]\]` can't match, and `re.finditer` returns zero results — `plot_specs` stays empty, no `AGENT_PLOT` block is created.
  - Fix: Changed to non-greedy `r'\[\[PLOT:\s*(.*?)\]\]'` with `re.DOTALL`. The `.*?` expands until it finds the first `]]`, correctly skipping over any `[` or `]` characters inside the tag body.
  - Also extended the auto-fallback condition to fire when `_user_wants_plot and not plot_specs` (covers the case where the regex match fails entirely, not just when specs have no `df_id`).
  - Also added a guard in AGENT_PLOT block creation to skip any spec where `df_id is None` (instead of creating a broken block with key `"no_df"` that the UI renders as "Chart missing DataFrame reference").
  - Applied to: [server1/app.py](server1/app.py)

- **NL fallback parser for `[[PLOT:...]]` tags**
  - After the DF-note fix above, when the tag IS matched, the LLM wrote natural language inside: `[[PLOT: histogram of DF1 with Category on the x-axis...]]` instead of `key=value` pairs. `_parse_tag_params` returned an empty dict, `df_id` remained `None`.
  - Fix: When `_parse_tag_params` yields no `df=` key, a natural-language extraction pass fires on the raw tag text — it regex-extracts the chart type (keyword match), DF reference (`DF\d+`), x column (`<word> on the x-axis`, `x=<word>`), and y column (`<word> on the y-axis`, `y=<word>`).
  - Applied to: [server1/app.py](server1/app.py)

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
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — ENCODE Search Tool & Parameter Aliases

- **`search_experiments` / `search_experiment` aliased to `search_by_biosample`**
  - LLM occasionally called `search_experiments` (non-existent tool name), returning a tool-not-found error instead of running the search. Added both variants to the tool alias map.
  - Applied to: [server2/config.py](server2/config.py)

- **Comprehensive biosample parameter aliases for `search_by_biosample`**
  - LLM used various param names (`biosample`, `biosample_name`, `cell_line`, `sample`, `biosample_term_name`, `cell_type`, `tissue`) when the tool expected `search_term`. All variants now silently map to `search_term`.
  - Applied to: [server2/config.py](server2/config.py)

---

### Fixed — CSV/BED File Display: LLM Gets Readable Table, DF Widget Visible

- **Parsed CSV/BED data formatted as markdown table for LLM**
  - Previously, parsed CSV data was sent to the second-pass LLM as a massive JSON blob (with `column_stats`, `dtypes`, nested metadata) causing it to respond "The query did not return the expected data." Now `_format_data` in result_formatter.py detects structured CSV/BED results (has `columns` + `data` list) and renders them as a clean markdown table — the same format the LLM can actually analyze and summarize.
  - Applied to: [server2/result_formatter.py](server2/result_formatter.py)

- **CSV/BED dataframes kept visible (reverted `visible: False`)**
  - The previous fix of hiding CSV DFs caused the interactive widget to be buried in the details section. Since the DF widget is the primary way to display parsed file data (with column selection, sorting, download), it's now visible again. The LLM text summary and the DF widget complement each other.
  - Applied to: [server1/app.py](server1/app.py)

### Added — Debug UI Panel

- **Debug toggle in sidebar**
  - New 🐛 Debug toggle in the sidebar enables per-response debug info display without needing server logs.
  - Applied to: [ui/app.py](ui/app.py)

- **Debug info section per AGENT_PLAN block**
  - When debug mode is on, each assistant response shows a collapsed "🐛 Debug Info" expander with JSON diagnostic data including: target_df_id, detected filters (assay, output_type, file_type), injected DF names/row counts, suppressed API calls, LLM raw response preview, auto-generated calls, and more.
  - Applied to: [server1/app.py](server1/app.py), [ui/app.py](ui/app.py)

- **`_inject_job_context` returns 3-tuple**
  - Now returns `(augmented_message, injected_dataframes, debug_info)` to pass diagnostic data from the injection logic through to the UI payload.
  - Applied to: [server1/app.py](server1/app.py)

### Fixed — False Positive Filter Matching ("hic" in "which")

- **Word-boundary matching for assay, output_type, and file_type filters**
  - All three filter detection loops in `_inject_job_context` now use `re.search(r'\b...\b')` instead of plain `if alias in message` substring check. This prevents false positives like `"hic"` matching inside `"which"`, which was causing the assay filter to fire as `"in situ Hi-C"` and the file_type filter as `"hic"` on queries like "which of them are the output type methylated reads?" — completely blocking the correct `output_type_filter = "methylated reads"` from being detected.
  - Root cause of the persistent "methylated reads" follow-up failure.
  - Applied to: [server1/app.py](server1/app.py)

### Added — Named Dataframes (DF1, DF2, ...)

- **Sequential DF IDs assigned to every dataframe**
  - Each dataframe produced (from API calls or follow-up filters) gets a sequential ID: DF1, DF2, DF3, etc., scoped to the conversation. IDs are stored in `metadata.df_id` and persist across the session.
  - Applied to: [server1/app.py](server1/app.py)

- **Explicit DF references in follow-up queries**
  - Users can now reference a specific dataframe by ID: e.g. "filter DF3 by methylated reads" or "show me DF1". `_inject_job_context` parses `DF\d+` patterns and uses exactly that dataframe, bypassing the file-type/assay heuristic guessing that previously caused wrong dataframe selection.
  - Applied to: [server1/app.py](server1/app.py)

- **DF IDs shown in UI expander headers**
  - Dataframe expanders now display their ID: `📊 DF3: ENCSR160HKZ bam files (12) — 12 rows × 5 columns` so users know which ID to reference.
  - Applied to: [ui/app.py](ui/app.py)

### Fixed — ENCODE File Query & Follow-up Filter Accuracy

- **`get_file_types` aliased to `get_files_by_type`**
  - Problem: LLM occasionally called `get_file_types` (returns only `["bam", "bed bed3+", "tar"]` — type names only) instead of `get_files_by_type` (returns full file metadata per type). This caused counts like "1 BAM file" instead of "12 BAM files" and no dataframe.
  - Fix: Added `get_file_types` → `get_files_by_type` alias in the tool alias map so any call to the wrong tool is silently corrected before execution.
  - Applied to: [server2/config.py](server2/config.py)

- **Per-file-type dataframes instead of one merged table**
  - Problem: All file types (bam, bed, tar, fastq, etc.) were flattened into a single 69-row table with a "File Type" column. Follow-up queries like "which BAM files are methylated reads?" injected all 69 rows and the LLM returned results from every file type.
  - Fix: `get_files_by_type` results are now stored as **one dataframe per file type** (e.g. `"ENCSR160HKZ bam files (12)"`, `"ENCSR160HKZ bed bed3+ files (54)"`), each tagged with `metadata.file_type`. The UI renders them as separate expandable tables.
  - Applied to: [server1/app.py](server1/app.py)

- **File-type-scoped context injection for follow-up queries**
  - Problem: "which of the BAM files are methylated reads?" injected all file-type dataframes, so the LLM received 69 rows across BED/tar/etc. and hallucinated or mixed results from bed files.
  - Fix: `_inject_job_context` now detects the file-type context from the current message or recent conversation history (e.g. "bam" from "how many bam files?") and skips dataframes whose `metadata.file_type` does not match. The LLM receives **only the 12 BAM rows**.
  - Applied to: [server1/app.py](server1/app.py)

- **Suppression of redundant `get_files_by_type` DATA_CALL tags**
  - Problem: Even after correct context injection, the LLM still emitted `[[DATA_CALL: ... get_files_by_type]]` for follow-up filter queries (because words like "bam" and "accessions" appear in the message), triggering a fresh API fetch that overwrote the injected filtered data.
  - Fix: When `_injected_previous_data` is true and not row-capped, any `get_files_by_type` or `get_experiment` DATA_CALL tags emitted by the LLM are dropped before execution.
  - Applied to: [server1/app.py](server1/app.py)

- **Removed "methylated" from `_auto_generate_data_calls` file keywords**
  - "methylated" was in the keyword list that triggers auto-generation of `get_files_by_type` calls, causing the system to re-fetch all files on any follow-up that mentioned methylation, ignoring injected context.
  - Applied to: [server1/app.py](server1/app.py)

- **Stripped `<details>` raw data from conversation history sent to LLM**
  - Problem: Even with per-file-type dataframe injection working correctly (only 12 BAM rows injected), the LLM's conversation history still contained the **full** `<details>` block from the previous turn — all 69 file rows (bam + bed + tar). The LLM read the unfiltered history instead of the filtered `[PREVIOUS QUERY DATA:]`, returning 54 BED accessions for a BAM follow-up.
  - Fix: When building `conversation_history` from AGENT_PLAN blocks, `<details>...</details>` sections (and their preceding `---` separator) are stripped via regex. The raw data remains in the stored block payload for the UI's collapsible widget; the LLM only sees the filtered data injected by `_inject_job_context`.
  - Applied to: [server1/app.py](server1/app.py)

- **Only show relevant file-type tables in the UI**
  - Problem: "How many BAM files for ENCSR160HKZ?" showed all three file-type tables (tar, bam, bed) prominently, even though the user only asked about BAM.
  - Fix: When storing per-file-type dataframes, the server detects which file type(s) the user asked about and tags matching dataframes with `metadata.visible = True`. The UI checks for this flag: if any dataframe is marked visible, only those are rendered; otherwise all are shown. Non-visible dataframes are still stored for follow-up queries.
  - Applied to: [server1/app.py](server1/app.py), [ui/app.py](ui/app.py)

- **Follow-up filter queries now show interactive dataframe of matching rows**
  - Problem: "Which of them are methylated reads?" correctly filtered to 2 rows but only showed the LLM's text answer — no interactive table. The user had no way to verify the accessions.
  - Fix: `_inject_job_context` now returns filtered dataframes alongside the augmented message. These are merged into `_embedded_dataframes` so the UI renders them as interactive `st.dataframe` tables with download button.
  - Applied to: [server1/app.py](server1/app.py)

- **DATA_CALL suppression now covers legacy `[[ENCODE_CALL:]]` tags**
  - Problem: The LLM emitted `[[ENCODE_CALL: get_experiment, accession=ENCSR160HKZ]]` (legacy format) which bypassed the suppression block that only checked unified `[[DATA_CALL:]]` format. This caused a redundant `get_experiment` API call on follow-up questions.
  - Fix: Suppression now filters both `data_call_matches` and `legacy_encode_matches` for tools in `{"get_files_by_type", "get_experiment"}`.
  - Applied to: [server1/app.py](server1/app.py)

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
  - Applied to: [server1/app.py](server1/app.py), [server1/agent_engine.py](server1/agent_engine.py), [server2/result_formatter.py](server2/result_formatter.py), [ui/app.py](ui/app.py), [skills/ENCODE_Search.md](skills/ENCODE_Search.md)

---

## [Unreleased] - 2026-02-17

### Fixed — 403 "You do not have access to this project" on Chat

- **Root cause 1: Missing ownership fallback in `require_project_access`**
  - If a user's `project_access` row was missing (e.g. after a failed project creation), they received a 403 even on their own project because the check only looked at the `project_access` table and not `projects.owner_id`
  - Fix: added a third check — if no access row exists but `Project.owner_id == user.id`, the missing row is automatically recreated and the request proceeds
  - Applied to: [server1/dependencies.py](server1/dependencies.py)

- **Root cause 2: Projects created only client-side never registered in DB**
  - When `POST /projects` timed out or failed, the UI silently fell back to a locally-generated UUID. Any subsequent `/chat` call then hit 403 because no `Project` or `ProjectAccess` row existed for that UUID
  - Fix: `/chat` endpoint now auto-registers the project (creates both `Project` and `ProjectAccess` rows as owner) if the project ID is not found in the DB, before running the access check
  - Applied to: [server1/app.py](server1/app.py)

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
  - Applied to: [server1/app.py](server1/app.py)

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
  - Solution: All three endpoints now also scan actual `nextflow_work_dir` paths stored in `dogme_jobs` table, covering both jailed and legacy `server3_work/` directories
  - Fixed key mismatch: files endpoint returns `size_bytes` consistently, UI handles both `size_bytes` and `size` as fallback
  - Fixed `pathlib.Path` vs FastAPI `Path` import collision (`from fastapi import Path as FastAPIPath`)
  - Applied to: [server1/app.py](server1/app.py), [ui/pages/projects.py](ui/pages/projects.py)

## [Unreleased] - 2026-02-17

### Added — Security Hardening Sprint

- **Role-Based Authorization Gates on All Endpoints**
  - Added `require_project_access(project_id, user, min_role)` dependency that enforces viewer/editor/owner hierarchy on every project-scoped endpoint
  - Added `require_run_uuid_access(run_uuid, user)` for job-scoped endpoints (debug, analysis)
  - Admin users bypass all project-level checks; public projects allow viewer-level access
  - Wired into ~15 endpoints: `/blocks`, `/chat`, `/conversations`, `/jobs`, `/analysis/*`, `/projects/*/access`
  - Applied to: [server1/dependencies.py](server1/dependencies.py), [server1/app.py](server1/app.py)

- **Server-Side Project Creation (UUID)**
  - `POST /projects` — creates project with `uuid4()` server-side, mkdir-before-DB pattern (orphan empty dir is harmless, zombie DB row is not)
  - `GET /projects` — lists user's projects (owned + shared), supports `include_archived` param
  - `PATCH /projects/{project_id}` — rename or archive (owner role required)
  - UI updated to call server endpoints instead of generating UUIDs client-side
  - Applied to: [server1/app.py](server1/app.py), [ui/app.py](ui/app.py)

- **Job Ownership Binding (`user_id` on `dogme_jobs`)**
  - Added `user_id` column to DogmeJob in both Server 3 and Server 4 models (nullable for legacy jobs)
  - Server 1 passes `user_id` through MCP submit flow → Server 3 REST → DB
  - `require_run_uuid_access()` checks `user_id` for direct ownership, falls back to project access
  - Applied to: [server3/models.py](server3/models.py), [server3/schemas.py](server3/schemas.py), [server3/db.py](server3/db.py), [server3/app.py](server3/app.py), [server3/mcp_server.py](server3/mcp_server.py), [server3/mcp_tools.py](server3/mcp_tools.py), [server4/models.py](server4/models.py)

- **User-Jailed File Paths for Nextflow Jobs**
  - Hardened `user_jail.py` with `_validate_id()` (rejects `/`, `\`, `.`, null bytes) and `_ensure_within_jail()` defense-in-depth
  - Nextflow executor now creates jailed work dirs at `AGOUTIC_DATA/users/{user_id}/{project_id}/{run_uuid}/` when `user_id` is present, falls back to flat legacy path
  - Analysis engine resolves jailed paths when looking up job work directories
  - Applied to: [server1/user_jail.py](server1/user_jail.py), [server3/nextflow_executor.py](server3/nextflow_executor.py), [server4/analysis_engine.py](server4/analysis_engine.py)

- **Cookie Hardening**
  - `Secure` flag is now environment-gated: `secure=True` when `ENVIRONMENT != "development"`
  - `httponly` and `samesite=lax` were already set
  - Added `ENVIRONMENT` config variable (defaults to `"development"`)
  - Applied to: [server1/auth.py](server1/auth.py), [server1/config.py](server1/config.py)

- **Database Migration Script**
  - `migrate_hardening.py` — adds `dogme_jobs.user_id`, `projects.is_archived`, `project_access.role` to existing databases
  - Idempotent (checks `PRAGMA table_info` before altering), safe to run multiple times
  - `init_db.py` updated for fresh installs: includes all new columns, `project_access` table, `conversations`, `conversation_messages`, `job_results` tables
  - Applied to: [server1/migrate_hardening.py](server1/migrate_hardening.py), [server1/init_db.py](server1/init_db.py)

### Fixed

- **CSV/TSV File Parsing Returns Path Only, No Contents**
  - Problem: When user asks "parse jamshid.mm39_final_stats.csv", the system calls `find_file` (returns the relative path) but never follows up with `parse_csv_file` to read the actual data
  - Root cause (v1): `_auto_generate_data_calls()` only generated a `find_file` call with no chaining
  - Root cause (v2): Even after adding `_chain` key to auto_calls, the LLM generates its own `[[DATA_CALL: ... tool=find_file ...]]` tag (trained by skill files like DOGME_QUICK_WORKFLOW_GUIDE.md), which sets `has_any_tags = True` and **skips auto_calls entirely** — so the `_chain` key never reaches the execution loop
  - Solution: Made chaining independent of how `find_file` was triggered. In the execution loop, **any** successful `find_file` call automatically chains to the appropriate parse/read tool by inferring it from the filename extension via `_pick_file_tool()` (`.csv/.tsv` → `parse_csv_file`, `.bed` → `parse_bed_file`, else → `read_file_content`). Works regardless of whether the call came from auto_calls or LLM-generated DATA_CALL tags.
  - Applied to: [server1/app.py](server1/app.py)

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
  - Applied to: [ui/app.py](ui/app.py), [server1/app.py](server1/app.py)

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
  - Applied to: [server1/app.py](server1/app.py)
  - Cleanup: stale progress entries auto-purged after 5 minutes
  - Applied to: [server1/app.py](server1/app.py), [ui/app.py](ui/app.py)

### Fixed
- **Post-Job Analysis Summary Showing "Total files: 0"**
  - Problem: After a Dogme job completes successfully (11 tasks, files clearly present in work directory), the auto-triggered analysis shows "Total files: 0" with all category counts at 0
  - Root cause: The `get_analysis_summary` MCP tool in server4 returns `{"success": True, "summary": "<markdown text>"}` — a pre-formatted markdown string. But `_auto_trigger_analysis()` in server1 expected structured dict fields (`file_summary`, `key_results`) that don't exist at the top level of the response
  - Solution (2 parts):
    1. Extended MCP tool response in `server4/mcp_tools.py` to include structured data fields (`all_file_counts`, `file_summary`, `key_results`, `mode`, `status`, etc.) alongside the markdown `summary` string
    2. Fixed `_auto_trigger_analysis()` in `server1/app.py` to read from `all_file_counts` for totals (not `key_results`) and use fallback arithmetic from `file_summary` lists
  - Applied to: [server1/app.py](server1/app.py), [server4/mcp_tools.py](server4/mcp_tools.py)

- **LLM Re-querying Instead of Using Data Already in Conversation**
  - Problem: After fetching data (file listings, search results), follow-up questions like "which of them are methylated reads?" or "what are the accessions for the long read RNA-seq samples?" cause the LLM to make new API calls instead of reading its own previous response
  - Root cause (v1): Keyword-based detection (`_filter_words`, `_referential`) was too narrow — only caught phrasings like "which of them", "those", "sort them" but missed "what are the accessions for..." style questions
  - Root cause (v2): LLM doesn't recognize it already has the data in conversation history `<details>` blocks
  - Solution (3 parts):
    1. Added "Answer From Existing Data First" rule to ENCODE_Search.md skill with two examples (file filtering + search subset extraction) — tells LLM to check previous responses and injected `[PREVIOUS QUERY DATA:]` before making new API calls
    2. Broadened `_inject_job_context()` ENCODE follow-up detection: removed fragile keyword lists entirely; now ANY follow-up without a new explicit subject (no new accession, no new biosample) gets the most recent `<details>` raw data injected with `[PREVIOUS QUERY DATA:]` context. Falls back to biosample context injection if no `<details>` data exists
    3. Added `_injected_previous_data` guard in auto-generation safety net — when previous data was injected, absence of DATA_CALL tags is correct behavior (LLM is answering from context), so auto-generation is skipped
  - Applied to: [server1/app.py](server1/app.py), [ENCODE_Search.md](skills/ENCODE_Search.md)

- **LLM Hallucinating Wrong ENCODE Tool Names in DATA_CALL Tags**
  - Problem: When asking "how many bam files do you have for ENCSR160HKZ?", the LLM generated `tool=get_files_types` instead of the correct `tool=get_files_by_type`, causing "Unknown tool" MCP errors
  - Root cause: The existing fallback patterns only fix plain-text tool invocations (e.g., `Get Files By Type(...)` → DATA_CALL), but cannot fix wrong tool names *inside* properly-formatted DATA_CALL tags
  - Solution: Added a `tool_aliases` map in `server2/config.py` that maps ~25 common LLM-hallucinated tool names to their correct equivalents. Applied during DATA_CALL parsing in `server1/app.py` before tool execution
  - Covers aliases for all ENCODE tools: `get_files_by_type`, `get_file_types`, `get_files_summary`, `get_experiment`, `search_by_biosample`, `search_by_target`, `search_by_organism`, `get_available_output_types`, `get_all_metadata`, `list_experiments`, `get_cache_stats`, `get_server_info`
  - Also added `get_all_tool_aliases()` helper function to `server2/config.py`
  - Applied to: [server1/app.py](server1/app.py), [server2/config.py](server2/config.py)

- **LLM Failing to Call File-Level ENCODE Tools Correctly**
  - Problem: `get_file_metadata` and `get_file_url` require TWO parameters — `accession` (parent experiment ENCSR) + `file_accession` (file ENCFF) — but the LLM consistently gets this wrong in multiple ways:
    - Uses `get_experiment` instead of `get_file_metadata` for file accessions
    - Mangles the ENCFF prefix to ENCSR to match `get_experiment`'s expected format
    - Hallucinated a completely different ENCSR accession while user message has ENCFF
    - Provides only one parameter when two are required
  - Root cause: LLM doesn't distinguish experiment vs file accession types, and the tool signatures weren't documented well enough in skill files
  - Solution (multi-layered):
    1. **`_correct_tool_routing()`** in `server1/app.py` — comprehensive routing correction function that:
       - Case 1: Detects `get_experiment` called with ENCFF → reroutes to `get_file_metadata`
       - Case 2: Detects mangled ENCFF→ENCSR prefix by cross-checking user message → restores correct prefix
       - Case 3: Detects `get_experiment` with hallucinated ENCSR when user message contains ENCFF → extracts correct ENCFF and reroutes
       - Detects `get_file_metadata` called without experiment accession → automatically finds it
    2. **`_find_experiment_for_file()`** helper — scans conversation history to find the parent ENCSR experiment for a given ENCFF file accession (first looks for messages mentioning both, then falls back to most recent ENCSR)
    3. **Graceful error for cold file queries** — when no parent experiment can be found in history (e.g., user asks about ENCFF with no prior context), the system short-circuits with a clear message ("Please first query the experiment that contains this file") instead of making a doomed MCP call that returns a cryptic Pydantic error
    4. **Removed incorrect `param_aliases`** in `server2/config.py` — the previous fix wrongly mapped `accession→file_accession` which would remove the required experiment accession parameter
    4. **Updated skill docs** — ENCODE_Search.md and ENCODE_LongRead.md now show `get_file_metadata` requires both params, with accession type reference table and warning to never change prefixes
  - Applied to: [server1/app.py](server1/app.py), [server2/config.py](server2/config.py), [ENCODE_Search.md](skills/ENCODE_Search.md), [ENCODE_LongRead.md](skills/ENCODE_LongRead.md)

- **ENCODE Follow-Up Questions Losing Biosample Context**
  - Problem: After asking "how many C2C12 experiments?" then "give me the accession for the long read RNA-seq samples", the LLM searched ALL long-read RNA-seq across ENCODE instead of filtering to C2C12
  - Root cause: The LLM generated its own DATA_CALL tags (so auto-generation didn't trigger), but searched without the C2C12 filter because the current message had no explicit biosample mention
  - Solution: Extended `_inject_job_context()` to also handle ENCODE skills — when the user's message has no explicit biosample or accession, the function scans conversation history for the previous search subject and injects `[CONTEXT: previous search was about C2C12, organism=Mus musculus]` so the LLM generates the correct filtered search
  - Also handles follow-ups referencing ENCSR accessions from previous results
  - Applied to: [server1/app.py](server1/app.py)

- **Conversational References Losing Context for Biosample Queries**
  - Problem: When user asked "how many C2C12 experiments?" then "give me a list of them", the LLM asked for clarification instead of re-running the C2C12 search
  - Root cause: The auto-generation safety net only scanned conversation history for ENCSR accessions, not biosample terms. "list of them" has no biosample keyword in the current message, so the biosample detection path never triggered
  - Solution: Extended `_auto_generate_data_calls()` to scan conversation history for previous biosample search terms when referential words ("them", "those", "list", etc.) are detected
  - Also added "list" to the referential words list (previously missing)
  - Refactored biosamples dict to function scope so it's reusable across detection paths
  - Result: "give me a list of them" after a biosample search now re-emits the same search automatically
  - Applied to: [server1/app.py](server1/app.py)

### Added
- **Job Context Injection for Dogme Skills**
  - System now automatically injects UUID and work directory context into user messages for Dogme analysis skills
  - Added `_inject_job_context()` function that prepends `[CONTEXT: run_uuid=..., work_dir=...]` to messages
  - Added `_extract_job_context_from_history()` to scan conversation history for most recent job context
  - Context injection happens before both initial `engine.think()` call and skill-switch re-run
  - Eliminates LLM wasting time searching conversation history for known information
  - Applied to: [server1/app.py](server1/app.py)

- **Auto-Generation Safety Net for File Parsing**
  - Extended `_auto_generate_data_calls()` with Dogme file-parsing patterns
  - When in a Dogme skill and user says "parse {filename}", auto-generates `find_file` DATA_CALL if LLM fails to
  - Extracts filename from user message using pattern matching for common verbs (parse, show, read, etc.)
  - Retrieves UUID from conversation history automatically
  - Provides failsafe similar to existing ENCODE auto-generation
  - Applied to: [server1/app.py](server1/app.py)

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
- **Enhanced Server4 Analysis File Discovery**
  - Extended file filtering to exclude both `work/` and `dor*/` subdirectories from analysis to prevent bloated file counts from Nextflow intermediate artifacts
  - Added intelligent key file identification based on analysis mode (DNA/RNA/cDNA) with mode-specific patterns
  - Modified `generate_analysis_summary()` to:
    - Filter displayed files to key result files only (qc_summary, stats, flagstat, gene_counts, etc.)
    - Keep full file counts in separate `all_file_counts` field for reference
    - Parse mode-specific files (gene_counts, transcript_counts for cDNA mode)
    - Extract key metrics from parsed reports into top-level `key_results` dict
  - Added `all_file_counts` field to `AnalysisSummary` schema to preserve total file statistics
  - Result: Analysis summaries now focus on important result files while maintaining awareness of total file counts
  - Applied to: [server4/analysis_engine.py](server4/analysis_engine.py), [server4/schemas.py](server4/schemas.py)

- **Improved Analysis Summary Formatting**
  - Changed `get_analysis_summary_tool` to return formatted markdown tables instead of raw JSON structures
  - Summary now includes:
    - Basic info table (Sample Name, Mode, Status, Work Directory)
    - File summary table (counts by file type)
    - Key results with availability status
    - Top 10 files from each category with size information
    - Natural language description of analysis results
  - Result: Analysis summaries are more readable and user-friendly, avoiding nested JSON truncation by MCP protocol
  - Applied to: [server4/mcp_tools.py](server4/mcp_tools.py)

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
  - All Server4 MCP tools were returning JSON strings via `_dumps()` instead of native Python dicts
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
  - Tool now available: `[[DATA_CALL: service=server4, tool=find_file, run_uuid=..., file_name=...]]`

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
  - Server 1 analysis proxy endpoints remain MCP-based (consistent with the rest of the architecture).

- **MCP Tool Errors Silently Swallowed**
  - `_call_server4_tool` and `_call_server3_tool` now check the `success` field in MCP responses; if `false`, they raise a proper HTTP 422 with the error message instead of returning the error dict as a 200.
  - Added fallback guard in the results page UI to detect and display any MCP error that slips through.
  - This was the root cause of the "all N/A" results page — an MCP tool error was returned as a valid 200 JSON response.

- **`datetime` Not JSON Serializable in MCP Tools**
  - `FileInfo.modified_time` (a `datetime` object) caused `json.dumps` to crash in all Server 4 MCP tools.
  - Added `_dumps()` helper in `server4/mcp_tools.py` with a `default` handler that converts `datetime` to ISO 8601 strings; replaced all 20+ `json.dumps` calls.

- **Skill Resets to "welcome" After Job Completion**
  - After auto-analysis created an `AGENT_PLAN` block post-job, follow-up messages (e.g. "parse qc_summary.csv") incorrectly reset skill to "welcome" because an older `APPROVAL_GATE` block existed.
  - Fixed skill resolution in `/chat` to compare sequence numbers: if the latest `AGENT_PLAN` is newer than the latest `APPROVAL_GATE`, continue with the agent's skill instead of resetting.

- **Wrong MCP Tool Names in Skill Files**
  - `Dogme_cDNA.md` and `Dogme_RNA.md` referenced `tool=parse_csv` instead of `tool=parse_csv_file` (the actual MCP tool name), causing tool-not-found errors when the LLM emitted DATA_CALL tags.

- **LLM Using Sample Name Instead of UUID for Analysis Tools**
  - Auto-analysis block didn't include the `run_uuid` in the markdown summary, so the LLM used the sample name (e.g., "Jamshid") instead of the actual UUID when calling Server 4 analysis tools.
  - Added `**Run UUID:** <backtick>uuid<backtick>` line to the auto-analysis summary.
  - Added explicit UUID-finding instructions to all three Dogme analysis skills (DNA, RNA, cDNA) with examples of how to extract the UUID from conversation history.

- **CSV/BED File Data Truncated to "..." in Results**
  - `_compact_dict` in `result_formatter.py` limited depth to 2, causing row data in parsed CSV/BED responses to show as `{"sample": "...", "n_reads": "..."}` instead of actual values.
  - Increased depth limit from 2 to 4 for analysis data (detected by presence of `columns`, `records`, or `preview_rows` keys in the response).

- **File Discovery Filtering Out Work Folder Files**
  - MCP tools in Server 4 now filter out files in the work/ and dor*/ directories to prevent bloated file counts from Nextflow intermediate artifacts.
  - Modified `discover_files()` in `server4/analysis_engine.py` to exclude files with paths starting with "work/" or "dor".
  - This affects all MCP tools: `get_analysis_summary`, `list_job_files`, `find_file`, and parsing tools.

### Documentation
- **Consolidated & Standardized Docs**
  - Moved various `*_IMPLEMENTATION.md` files to `archive/` to reduce clutter.
  - Standardized Server 2 documentation: moved `SERVER2_IMPLEMENTATION.md` to `server2/README.md`.
  - Consolidated Server 3 documentation: merged `FILES.md`, `DUAL_INTERFACE.md`, etc., into a single `server3/README.md`.

### Changed
- **UI Architecture: Server 1 Abstraction Layer**
  - Refactored `ui/pages/results.py` to route all requests through Server 1 proxy endpoints instead of calling Server 4 directly.
  - Added file download proxy endpoint (`/analysis/files/download`) to Server 1 to stream files from Server 4 without exposing backend URLs.
  - Added `rest_url` field to Server 4 `SERVICE_REGISTRY` entry for REST-specific proxying.
  - All UI pages now exclusively communicate with Server 1; backend architecture is fully abstracted from the frontend.

- **Auto-Analysis on Job Completion**
  - When a Dogme job finishes, Server 1 now automatically fetches the analysis summary from Server 4 and presents it in the chat.
  - The agent skill switches to the mode-specific analysis skill (DNA/RNA/cDNA) so follow-up questions use the right context.
  - Users see a file overview with counts and key results immediately, with suggested next steps.

- **Chat Management**
  - Added `DELETE /projects/{project_id}/blocks` endpoint to Server 1 for clearing chat history.
  - Replaced the no-op "Force Clear" button with a working "Clear Chat" button that deletes all blocks from the database.
  - Added a "Refresh" button for manual reloads.
  - Long conversations now show only the last 30 messages by default, with a "Load older messages" button to page back.

### Added
- **Server2 Implementation** (2026-02-10)
  - Initial implementation of ENCODE search server
  - Integration with Server1 via MCP client
  - Result formatting for ENCODE data
  - Documentation: `SERVER2_IMPLEMENTATION.md`, `SERVER2_QUICKSTART.md`
  - Skills: ENCODE_LongRead.md, ENCODE_Search.md

- **Server4 Implementation** (2026-02-09)
  - Initial implementation of analysis/QC server
  - Analysis engine for quality control workflows
  - MCP server interface for job result analysis
  - Database models and schemas for analysis storage
  - Documentation: `SERVER4_IMPLEMENTATION.md`, `INSTALLATION.md`, `QUICKSTART.md`
  - Test scripts: `test_server4_direct.sh`, `test_server4_integration.sh`, `quick_qc_test.sh`
  - UI integration for displaying analysis results
  - New skill: Analyze_Job_Results.md

- **Second-Round LLM Processing** (2026-02-13)
  - Added capability for processing tool outputs with additional LLM rounds
  - Enhanced MCP client to support iterative processing
  - Improved result formatting in Server2

- **Unified Logging System** (2026-02-13)
  - Centralized logging configuration in `common/logging_config.py`
  - Logging middleware in `common/logging_middleware.py`
  - Automatic log rotation and better log management
  - Server stopping and log rollover in `agoutic_servers.sh`

- **Multi-User Support** (2026-02-09)
  - Enhanced authentication and authorization
  - User jail/isolation functionality
  - Admin panel improvements
  - Multi-user configuration in Server1 and UI

### Changed
- **MCP Architecture Refactoring** (2026-02-11)
  - Moved MCP client code to common area (`common/mcp_client.py`)
  - Refactored Server2, Server3, and Server4 to use unified MCP interface
  - Consolidated MCP client implementations from individual servers
  - Updated Server1 agent engine to use common MCP client

- **Server3 Job Launch Improvements** (2026-02-14)
  - Fixed job launching in Server3
  - Unified ENCODE and local sample intake workflows
  - Updated MCP server and tools for better job submission
  - Enhanced schema definitions for job parameters

- **Nextflow Integration** (2026-02-03, 2026-02-05)
  - Updated Nextflow executor for Server3
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
  - Fixed MCP client issues in Server2
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
Updated Server4 analysis tools to return both human-friendly markdown and structured fields (`all_file_counts`, `file_summary`, `key_results`). Updated Server1 auto-analysis logic to read the correct fields and compute totals accurately.

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
Refactored the UI to route all analysis requests through a Server 1 proxy layer rather than contacting Server 4 directly. This hides backend URLs and unifies authentication and logging at the gateway.

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
│    Server 1 Proxy (gateway) (#12)   │  ← all backends hidden behind here
│    ├── Server 2 (ENCODE)            │
│    ├── Server 3 (jobs/Nextflow)     │
│    └── Server 4 (analysis/files)    │
└─────────────────────────────────────┘
     │
     ▼
  Response to User
```

---
*Updated on February 17, 2026*
