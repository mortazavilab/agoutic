# AGOUTIC Cortex: Agent Engine

**Docs Current For:** AGOUTIC 3.6.6  
**Service Version:** 1.0  
**Status:** Active Development 

## Overview

Cortex is the **orchestration and reasoning engine** for AGOUTIC. It provides an AI agent interface for interpreting user requests, planning workflows, and submitting analysis jobs to Launchpad. Multi-step planning is now manifest-first for the core deterministic analysis flows: skill manifests drive request classification, runtime/input warnings, MCP tool-chain selection, and service gating, while older templates remain fallback for unmigrated paths.

### Key Responsibilities

- 🧠 **AI Reasoning**: LLM-based interpretation of user intent
- 📋 **Workflow Planning**: Classify requests, compose manifest-driven deterministic plans, and fall back safely when a flow is not yet migrated
- 🎯 **Skill Management**: Load and apply bioinformatics skill definitions
- 🔗 **Job Submission**: Orchestrate job submission to Launchpad
- 📊 **Project Tracking**: Maintain project and analysis history
- 🤝 **Integration**: Both REST and MCP interfaces for flexible access
- 📜 **Tool Schema Contracts**: Fetches machine-readable tool schemas from all MCP servers and injects them into the LLM prompt; validates parameters pre-call
- 🗂️ **Structured Conversation State**: Builds a typed `ConversationState` JSON each turn (skill, project, sample, experiment, dataframes, workflows) and injects it into the user message
- 🛡️ **Error-Handling Playbook**: Deterministic failure rules in system prompt + structured `[TOOL_ERROR]` blocks + retry logic for transient failures
- ✅ **Output Contract Validator**: Post-LLM validation catches malformed tags, unknown tools, duplicate approvals, and mixed sources
- 🔍 **Provenance Tags**: Every tool result carries `[TOOL_RESULT: source, tool, params, rows, timestamp]` headers for auditability

## Architecture

### System Flow

```
User Request (REST/Chat)
        ↓
Cortex Agent Engine
  ├─ Load skill definitions (skills/*/SKILL.md)
  ├─ Load skill manifests (skills/*/manifest.yaml via cortex/skill_manifest.py)
  ├─ Classify requests (plan_classifier.py)
  ├─ Compose manifest-driven plans when supported (plan_composer.py)
  ├─ Create system prompt with skill context
  ├─ Call LLM for reasoning
  ├─ (Optional) Request approval from user
  └─ Extract job parameters
        ↓
Launchpad Job Submission (REST or MCP)
  ├─ /jobs/submit endpoint
  └─ Returns run_uuid
        ↓
Results & Tracking
  ├─ Store in project_blocks table
  ├─ Stream progress to user
  └─ Return analysis report
```

### Component Architecture

```
┌───────────────────────────────────────────────┐
│          Cortex Agent Engine                │
├───────────────────────────────────────────────┤
│                                               │
│  ┌─────────────────────────────────────┐    │
│  │  FastAPI App (app.py)               │    │
│  │  - /chat endpoint                   │    │
│  │  - /projects endpoints              │    │
│  │  - /blocks endpoints                │    │
│  └─────────────────────────────────────┘    │
│            ↓                                  │
│  ┌─────────────────────────────────────┐    │
│  │  Agent Engine (agent_engine.py)     │    │
│  │  - Skill loading                    │    │
│  │  - LLM chat interface               │    │
│  │  - Manifest-first workflow planning │    │
│  │  - Tool contract injection          │    │
│  │  - Error-handling playbook          │    │
│  └─────────────────────────────────────┘    │
│            ↓                                  │
│  ┌─────────────────────────────────────┐    │
│  │  Planner Stack                      │    │
│  │  - plan_classifier.py               │    │
│  │  - plan_composer.py                 │    │
│  │  - planner.py                       │    │
│  │  - skill_manifest.py                │    │
│  └─────────────────────────────────────┘    │
│            ↓                                  │
│  ┌─────────────────────────────────────┐    │
│  │  Tool Contracts (tool_contracts.py) │    │
│  │  - Schema fetch + cache             │    │
│  │  - Prompt formatting                │    │
│  │  - Pre-call param validation        │    │
│  └─────────────────────────────────────┘    │
│            ↓                                  │
│  ┌─────────────────────────────────────┐    │
│  │  Launchpad Client (mcp_client.py)    │    │
│  │  - REST API calls                   │    │
│  │  - MCP protocol calls               │    │
│  │  - Job status polling               │    │
│  └─────────────────────────────────────┘    │
│                                               │
└───────────────────────────────────────────────┘
         ↓
    Launchpad / Database
```

## Installation & Setup

### Prerequisites

- Python 3.12+
- OpenAI client library
- FastAPI & Uvicorn
- SQLAlchemy 2.0+
- SQLite or PostgreSQL

### Environment Setup

```bash
# 1. Create environment (already included in root environment.yml)
cd /path/to/agoutic
conda env create -f environment.yml
conda activate agoutic_core

# 2. Set LLM configuration (optional - defaults to local Ollama)
export LLM_URL="http://localhost:11434/v1"  # Local Ollama
# OR
export LLM_URL="https://api.openai.com/v1"  # OpenAI (requires OPENAI_API_KEY)
```

### Configuration

Edit `cortex/config.py` to customize:

```python
# LLM Settings
LLM_URL = os.getenv("LLM_URL", "http://localhost:11434/v1")

LLM_MODELS = {
    "default": "mistral",      # Default model
    "fast": "neural-chat",     # Fast inference
    "smart": "neural-chat",    # Smart reasoning
}

# Skills Directory
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# Database
DATABASE_URL = "sqlite:///./agoutic_v23.sqlite"
```

## Running Cortex

### Start the Server

```bash
# Development mode (with auto-reload)
uvicorn cortex.app:app --host 0.0.0.0 --port 8000 --reload

# Production mode
uvicorn cortex.app:app --host 0.0.0.0 --port 8000 --workers 4
```

### Verify Server is Running

```bash
# Check health endpoint
curl http://localhost:8000/health

# Expected response:
# {"status":"ok","server":"cortex","version":"1.0.0"}
```

## API Endpoints

### Chat with Agent

```http
POST /chat

Request Body:
{
  "project_id": "proj_001",
  "message": "Analyze this liver DNA sample with 5mC modifications",
  "skill": "Dogme_DNA",          # Optional: Dogme_DNA, Dogme_RNA, Dogme_cDNA
  "model": "default"              # Optional: default, fast, smart
}

Response:
{
  "project_id": "proj_001",
  "block_id": "blk_123456",
  "type": "agent_response",
  "status": "processing",
  "response": "I'll analyze the liver DNA sample with 5mC calling...",
  "jobs": [
    {
      "run_uuid": "a1b2c3d4-...",
      "sample_name": "liver_dna",
      "status": "RUNNING"
    }
  ]
}
```

### Get Project

```http
GET /projects/{project_id}

Response:
{
  "project_id": "proj_001",
  "created_at": "2025-01-22T12:00:00+00:00",
  "blocks_count": 5,
  "status": "active"
}
```

### List Project Blocks

```http
GET /projects/{project_id}/blocks?skip=0&limit=50

Response:
{
  "blocks": [
    {
      "id": "blk_001",
      "seq": 1,
      "type": "user_message",
      "status": "completed",
      "created_at": "2025-01-22T12:00:00+00:00",
      "payload": {...}
    },
    {
      "id": "blk_002",
      "seq": 2,
      "type": "agent_response",
      "status": "processing",
      "created_at": "2025-01-22T12:01:00+00:00",
      "payload": {...}
    }
  ],
  "total": 5
}
```

### Stream Results

```http
GET /projects/{project_id}/blocks/{block_id}/stream

Response: (Server-Sent Events)
data: {"progress": 0}
data: {"progress": 25}
data: {"progress": 50}
data: {"progress": 100, "results": {...}}
```

## Core Components

### 1. Agent Engine (`agent_engine.py`)

Handles LLM interaction and workflow reasoning:

```python
from cortex.agent_engine import AgentEngine

# Initialize agent with a specific model
agent = AgentEngine(model_key="default")

# Load skill and create the rendered first-pass system prompt
skill_prompt = agent.construct_system_prompt("run_dogme_dna")

# Inspect the currently rendered prompt without calling the LLM
prompt_preview = agent.render_system_prompt("run_dogme_dna", "first_pass")

# Run the first-pass planning call
response, usage = agent.think(
  user_message="Analyze DNA sample",
  skill_key="run_dogme_dna",
  conversation_history=[...]
)
```

**Key Methods:**
- `construct_system_prompt(skill_key)` - Render the first-pass planning prompt from Markdown templates plus dynamic skill/tool sections
- `construct_analysis_prompt()` - Render the second-pass analysis prompt from its Markdown template
- `render_system_prompt(skill_key, prompt_type)` - Return the exact rendered first-pass or second-pass prompt for inspection
- `think(message, skill_key, history)` - First-pass LLM interaction
- `_load_skill_text(skill_key)` - Load skill definition

**Prompt templates:**
- `cortex/prompt_templates/first_pass_system_prompt.md` - First-pass planning prompt template
- `cortex/prompt_templates/second_pass_system_prompt.md` - Second-pass analysis prompt template

### 2. Tool Contracts (`tool_contracts.py`)

Central module for machine-readable tool schemas:

- `fetch_all_tool_schemas(service_registry, consortium_registry)` — Fetches `/tools/schema` from all MCP servers at startup, caches results
- `format_tool_contract(source_key, source_type)` — Formats schemas into compact prompt block (`TOOL: name / REQUIRED / OPTIONAL / ⚠️ warnings`)
- `validate_against_schema(tool_name, params, source_key)` — Pre-call validation: strips unknown params, checks required fields, normalises enum case
- `invalidate_schema_cache()` — Clears cache after server restarts

### 3. FastAPI Application (`app.py`)

Provides REST endpoints:

- `POST /chat` - Chat with agent
- `GET /projects/{project_id}` - Get project details
- `GET /projects/{project_id}/blocks` - List project history
- `GET /projects/{project_id}/blocks/{block_id}/stream` - Stream results

**Runtime pipeline additions:**
- `_build_conversation_state()` — Builds `ConversationState` JSON each turn (fast path: cached from last AGENT_PLAN; slow path: scans blocks)
- `_validate_llm_output()` — Post-LLM output validation: malformed DATA_CALL, duplicate APPROVAL_NEEDED, unknown tools, mixed sources
- Provenance tracking — `[TOOL_RESULT: source, tool, params, rows, timestamp]` headers on all tool results
- Pre-call schema validation via `validate_against_schema()`
- Structured `[TOOL_ERROR]` blocks with error classification

### 3. MCP Client (`mcp_client.py`)

Communicates with Launchpad:

```python
from cortex.mcp_client import LaunchpadMCPClient

# Connect to Launchpad MCP
client = LaunchpadMCPClient()
await client.connect()

# Submit job via MCP
result = await client.submit_dogme_job(
    project_id="proj_001",
    sample_name="dna_sample",
    mode="DNA",
    input_directory="/data/pod5"
)
```

### 4. Database Models (`models.py`)

**ProjectBlock** - Project history and tracking

```sql
CREATE TABLE project_blocks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    seq INTEGER NOT NULL,           -- Sequence per project
    type TEXT NOT NULL,             -- user_message, agent_response, etc.
    status TEXT DEFAULT 'NEW',      -- NEW, PROCESSING, COMPLETED, FAILED
    payload JSON,                   -- Message content, job details, results
    parent_id TEXT,                 -- Reference to parent block
    created_at DATETIME
);
```

## Skills System

Skills are Markdown files in `skills/<skill_key>/SKILL.md` that define workflows:

- **skills/run_dogme_dna/SKILL.md** - Genomic DNA analysis workflow
- **skills/run_dogme_rna/SKILL.md** - Direct RNA-seq workflow
- **skills/run_dogme_cdna/SKILL.md** - cDNA isoform workflow
- **skills/ENCODE_LongRead/SKILL.md** - ENCODE consortium workflow
- **skills/analyze_local_sample/SKILL.md** - Sample intake workflow

### Loading Skills

```python
# Auto-discovered from skills/<skill_key>/manifest.yaml
# and re-exported as SKILLS_REGISTRY for backward compatibility.

# Agent loads via:
skill_text = agent._load_skill_text("run_dogme_dna")
```

## Configuration Examples

### Use Default Local LLM (Ollama)

```bash
# Start Ollama
ollama serve

# In another terminal, start Cortex
export LLM_URL="http://localhost:11434/v1"
uvicorn cortex.app:app --port 8000
```

### Use OpenAI API

```bash
export LLM_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="sk-..."
uvicorn cortex.app:app --port 8000
```

### Use Custom LM Studio

```bash
export LLM_URL="http://localhost:1234/v1"
uvicorn cortex.app:app --port 8000
```

## Testing

### Run Tests

```bash
pytest tests/cortex -q
```

### Manual Testing

```bash
# Start servers
# Terminal 1:
uvicorn launchpad.app:app --port 8001 --reload

# Terminal 2:
uvicorn cortex.app:app --port 8000 --reload

# Terminal 3: Test chat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "test_proj",
    "message": "Analyze a DNA sample",
    "skill": "Dogme_DNA"
  }'
```

## Integration with Launchpad

### REST API Path

```python
# cortex/mcp_client.py
def submit_via_rest(job_params):
    response = requests.post(
        "http://localhost:8001/jobs/submit",
        json=job_params
    )
    return response.json()
```

### MCP Protocol Path

```python
# cortex/mcp_client.py
async def submit_via_mcp(job_params):
    async with LaunchpadMCPClient() as client:
        return await client.submit_dogme_job(**job_params)
```

## Integration with Analyzer

Cortex calls Analyzer MCP tools for file discovery, parsing, and analysis of completed workflow results.

### Workflow Directory Structure

Each completed Dogme job writes output to a `workflow{N}/` folder inside the project directory:

```
$AGOUTIC_DATA/users/{username}/{project-slug}/
├── data/              # Uploaded input data
├── workflow1/         # First job
│   ├── annot/         # Annotations, final stats, gene counts
│   ├── bams/          # BAM alignment files
│   ├── bedMethyl/     # Methylation BED output
│   ├── fastqs/        # FASTQ files
│   ├── kallisto/      # Kallisto quantification (cDNA)
│   └── work/          # Nextflow intermediates (excluded from listing)
└── workflow2/         # Second job (auto-incremented)
```

### Agent-Level Browsing Commands

Cortex's `_auto_generate_data_calls()` safety net detects browsing commands and generates the appropriate Analyzer tool calls automatically — the LLM doesn't need to produce `[[DATA_CALL:...]]` tags for these:

| User says | Cortex generates |
|---|---|
| `list workflows` | `list_job_files(work_dir=<project_dir>)` |
| `list files` | `list_job_files(work_dir=<current_workflow>)` |
| `list files in annot` | `list_job_files(work_dir=<workflow>/annot)` — cascades: tries workflow first, then project root |
| `list files in data` | `list_job_files(work_dir=<workflow>/data)` or `<project_dir>/data` — uses `os.path.isdir` to find the real directory |
| `list files in workflow2/annot` | `list_job_files(work_dir=<workflow2>/annot)` |
| `list project files` | `list_job_files(work_dir=<project_dir>)` — always targets the project root |
| `list project files in data` | `list_job_files(work_dir=<project_dir>/data)` — always relative to project root |
| `parse annot/File.csv` | `find_file(work_dir=<workflow>, file_name=File.csv)` → `parse_csv_file(...)` |
| `parse workflow2/annot/File.csv` | `find_file(work_dir=<workflow2>, file_name=File.csv)` → `parse_csv_file(...)` |

**Path resolution cascade for `list files in <subpath>`:**
1. Try `<current_workflow>/<subpath>` — if it exists on disk, use it
2. Try `<project_dir>/<subpath>` — if it exists on disk, use it
3. Fall back to whichever path is available; if the directory doesn't exist, show error with suggestions

When a browsing command fails, the error is shown directly (no LLM re-interpretation) with suggested alternative commands.

### Path Resolution Helpers

- **`_resolve_workflow_path(subpath, default_work_dir, workflows)`** — resolves user subpaths like `"workflow2/annot"` to absolute directory paths
- **`_resolve_file_path(user_path, default_work_dir, workflows)`** — resolves user file paths like `"workflow2/annot/File.csv"` to `(work_dir, filename)` tuples
- **`_validate_analyzer_params(...)`** — catches placeholder `work_dir` values (`/work_dir`, `{work_dir}`, `<work_dir>`) and replaces them with the real path from EXECUTION_JOB blocks

### Context Injection

For Dogme skills, `_inject_job_context()` prepends a `[CONTEXT: ...]` line with the workflow directory path(s) so the LLM can reference them:

```
[CONTEXT: work_dir=/media/.../users/alice/my-project/workflow1, sample=jamshid]
```

For multi-workflow projects:
```
[CONTEXT: workflows=[
  workflow1 (sample=jamshid, mode=CDNA): work_dir=/media/.../workflow1
  workflow2 (sample=ali, mode=DNA): work_dir=/media/.../workflow2
], active_workflow=workflow2]
```

## Project & Block Structure

### Project Blocks

Each user interaction creates a "block" in the project timeline:

```
Project (proj_001)
├── Block 1: User message
│   └── "Analyze liver DNA with 5mC"
├── Block 2: Agent response
│   ├── Reasoning: "Planning DNA analysis"
│   └── Job submission: {run_uuid: "abc123..."}
├── Block 3: Job status update
│   ├── Progress: 50%
│   └── Status: RUNNING
└── Block 4: Job completion
    ├── Results: {...}
    └── Status: COMPLETED
```

### Block Types

- `user_message` - User query or command
- `agent_response` - Agent reasoning and action
- `job_status` - Job progress update
- `job_completion` - Final results
- `approval_request` - Request for user approval
- `error` - Error or exception
- `AGENT_PLOT` - Plotly chart specification generated when the agent uses a `[[PLOT:...]]` tag

### `[[PLOT:...]]` Tag System

The LLM instructs Cortex to create a chart by embedding a structured tag in its response:

```
[[PLOT: type=histogram df=DF1 x=mapq_score title=MAPQ Distribution]]
[[PLOT: type=scatter df=DF2 x=read_length y=alignment_score color=sample_id]]
[[PLOT: type=bar df=DF1 x=assay_title agg=count title=Experiment Types]]
```

Supported chart types: `histogram`, `scatter`, `line`, `area`, `bar`, `box`, `violin`, `strip`, `heatmap`, `pie`, `venn`, `upset`

Cortex parses these tags, looks up the referenced DataFrame from the conversation history, and creates an `AGENT_PLOT` block that the UI renders via `st.plotly_chart()`.

## Troubleshooting

### LLM Connection Error

```
Error: Connection refused when calling LLM
```

**Solution:**
- Check LLM_URL environment variable: `echo $LLM_URL`
- Verify LLM server is running: `curl http://localhost:11434/v1/models`
- Check firewall/network connectivity

### Skill Not Found

```
Error: Skill 'Dogme_DNA' not found in Registry
```

**Solution:**
- Verify skill file exists: `ls skills/run_dogme_dna/SKILL.md`
- Verify `skills/run_dogme_dna/manifest.yaml` exists and is valid YAML
- Verify the skill folder name matches the intended skill key

### Database Locked

```
Error: Database is locked
```

**Solution:**
- Restart Cortex
- Check for other processes: `ps aux | grep cortex`
- Use PostgreSQL for production instead of SQLite

### Launchpad Connection Error

```
Error: Cannot connect to Launchpad
```

**Solution:**
- Check Launchpad is running: `curl http://localhost:8001/health`
- Verify Launchpad port: `echo $LAUNCHPAD_URL`
- Check firewall/network connectivity

## Performance Tuning

### Concurrent Chat Requests

```python
# cortex/config.py
MAX_CONCURRENT_REQUESTS = 10  # Limit concurrent LLM calls
```

### LLM Model Selection

```bash
# Use faster model for quick responses
export LLM_MODEL="neural-chat"  # Fast

# Use smarter model for complex reasoning
export LLM_MODEL="mistral"      # Smart
```

### Database Optimization

```python
# cortex/config.py
# Use PostgreSQL for production
DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/agoutic"
```

## Development Guidelines

### Adding a New Skill

1. Create markdown file in `skills/<skill_key>/SKILL.md`:
   ```markdown
   # My Analysis Skill
   
   ## Description
   This skill performs X analysis...
   
   ## Parameters
   - input_directory: Path to input files
   - reference_genome: e.g., GRCh38
   
   ## Output
   - report.json: Summary statistics
   - alignment.bam: Aligned reads
   ```

2. Register in `cortex/config.py`:
   ```python
   "MySkill": "MySkill.md"
   ```

3. Agent can now load and use the skill

### Extending Agent Capabilities

Add new methods to `AgentEngine` class:

```python
class AgentEngine:
    def my_new_capability(self, param1, param2):
        """New capability description."""
        # Implementation
        pass
```

## Version Information

- **Version**: 1.0.0
- **Python**: 3.12+
- **FastAPI**: Latest (from environment.yml)
- **SQLAlchemy**: 2.0+
- **Status**: Active Development

## Next Steps

- Approval gate integration
- Web dashboard integration
- Performance optimization
