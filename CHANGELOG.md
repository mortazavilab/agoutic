# Changelog - February 2026

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
