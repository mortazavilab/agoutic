# Changelog - February 2026

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
  - MCP tools in Server 4 now filter out files in the work/ directory to prevent bloated file counts from Nextflow intermediate artifacts.
  - Modified `discover_files()` in `server4/analysis_engine.py` to exclude files with paths starting with "work/".
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

## Summary

February 2026 saw significant architectural improvements and feature additions to the Agoutic platform:

### Major Features
- **Two New Servers**: Server2 (ENCODE search) and Server4 (Analysis/QC) were fully implemented and integrated
- **MCP Standardization**: All servers now use a unified Model Context Protocol interface through common code
- **Enhanced Multi-user Support**: Better authentication, authorization, and user isolation
- **Advanced LLM Processing**: Second-round LLM processing for improved tool output handling

### Infrastructure Improvements
- Centralized logging system with automatic rotation
- Better server lifecycle management
- Unified MCP client architecture
- Comprehensive test coverage for new servers

### Workflow Enhancements
- Unified ENCODE and local sample intake processes
- Improved Nextflow job submission from UI
- Dogme pipeline options for multiple data types
- Enhanced analysis and QC capabilities

### Files Changed: 80+
### Commits: 15
### Lines Changed: Thousands across the codebase

---
*Generated on February 14, 2026*
