# AGOUTIC Server 1: Agent Engine

**Version:** 1.0  
**Status:** Active Development 

## Overview

Server 1 is the **orchestration and reasoning engine** for AGOUTIC. It provides an AI agent interface for interpreting user requests, planning workflows, and submitting analysis jobs to Server 3.

### Key Responsibilities

- 🧠 **AI Reasoning**: LLM-based interpretation of user intent
- 📋 **Workflow Planning**: Convert requests into executable pipelines
- 🎯 **Skill Management**: Load and apply bioinformatics skill definitions
- 🔗 **Job Submission**: Orchestrate job submission to Server 3
- 📊 **Project Tracking**: Maintain project and analysis history
- 🤝 **Integration**: Both REST and MCP interfaces for flexible access

## Architecture

### System Flow

```
User Request (REST/Chat)
        ↓
Server 1 Agent Engine
  ├─ Load skill definitions (Dogme_DNA.md, Dogme_RNA.md, etc.)
  ├─ Create system prompt with skill context
  ├─ Call LLM for reasoning
  ├─ (Optional) Request approval from user
  └─ Extract job parameters
        ↓
Server 3 Job Submission (REST or MCP)
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
│          Server 1 Agent Engine                │
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
│  │  - Workflow planning                │    │
│  └─────────────────────────────────────┘    │
│            ↓                                  │
│  ┌─────────────────────────────────────┐    │
│  │  Server 3 Client (mcp_client.py)    │    │
│  │  - REST API calls                   │    │
│  │  - MCP protocol calls               │    │
│  │  - Job status polling               │    │
│  └─────────────────────────────────────┘    │
│                                               │
└───────────────────────────────────────────────┘
         ↓
    Server 3 / Database
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

Edit `server1/config.py` to customize:

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

## Running Server 1

### Start the Server

```bash
# Development mode (with auto-reload)
uvicorn server1.app:app --host 0.0.0.0 --port 8000 --reload

# Production mode
uvicorn server1.app:app --host 0.0.0.0 --port 8000 --workers 4
```

### Verify Server is Running

```bash
# Check health endpoint
curl http://localhost:8000/health

# Expected response:
# {"status":"ok","server":"server1","version":"1.0.0"}
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
from server1.agent_engine import AgentEngine

# Initialize agent with a specific model
agent = AgentEngine(model_key="default")

# Load skill and create system prompt
skill_prompt = agent.construct_system_prompt("Dogme_DNA")

# Chat with agent
response = agent.chat(
    user_message="Analyze DNA sample",
    system_prompt=skill_prompt,
    conversation_history=[...]
)
```

**Key Methods:**
- `construct_system_prompt(skill_key)` - Build system prompt from skill
- `chat(message, system_prompt, history)` - LLM interaction
- `_load_skill_text(skill_key)` - Load skill definition

### 2. FastAPI Application (`app.py`)

Provides REST endpoints:

- `POST /chat` - Chat with agent
- `GET /projects/{project_id}` - Get project details
- `GET /projects/{project_id}/blocks` - List project history
- `GET /projects/{project_id}/blocks/{block_id}/stream` - Stream results

### 3. MCP Client (`mcp_client.py`)

Communicates with Server 3:

```python
from server1.mcp_client import Server3MCPClient

# Connect to Server 3 MCP
client = Server3MCPClient()
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

Skills are Markdown files in `skills/` that define workflows:

- **Dogme_DNA.md** - Genomic DNA analysis workflow
- **Dogme_RNA.md** - Direct RNA-seq workflow
- **Dogme_cDNA.md** - cDNA isoform workflow
- **ENCODE_LongRead.md** - ENCODE consortium workflow
- **Local_Sample_Intake.md** - Sample intake workflow

### Loading Skills

```python
# Auto-registered in config.py
SKILLS_REGISTRY = {
    "Dogme_DNA": "Dogme_DNA.md",
    "Dogme_RNA": "Dogme_RNA.md",
    "Dogme_cDNA": "Dogme_cDNA.md",
    "ENCODE_LongRead": "ENCODE_LongRead.md",
    "Local_Sample_Intake": "Local_Sample_Intake.md",
}

# Agent loads via:
skill_text = agent._load_skill_text("Dogme_DNA")
```

## Configuration Examples

### Use Default Local LLM (Ollama)

```bash
# Start Ollama
ollama serve

# In another terminal, start Server 1
export LLM_URL="http://localhost:11434/v1"
uvicorn server1.app:app --port 8000
```

### Use OpenAI API

```bash
export LLM_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="sk-..."
uvicorn server1.app:app --port 8000
```

### Use Custom LM Studio

```bash
export LLM_URL="http://localhost:1234/v1"
uvicorn server1.app:app --port 8000
```

## Testing

### Run Tests

```bash
pytest server1/test_chat.py -v
```

### Manual Testing

```bash
# Start servers
# Terminal 1:
uvicorn server3.app:app --port 8001 --reload

# Terminal 2:
uvicorn server1.app:app --port 8000 --reload

# Terminal 3: Test chat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "test_proj",
    "message": "Analyze a DNA sample",
    "skill": "Dogme_DNA"
  }'
```

## Integration with Server 3

### REST API Path

```python
# server1/mcp_client.py
def submit_via_rest(job_params):
    response = requests.post(
        "http://localhost:8001/jobs/submit",
        json=job_params
    )
    return response.json()
```

### MCP Protocol Path

```python
# server1/mcp_client.py
async def submit_via_mcp(job_params):
    async with Server3MCPClient() as client:
        return await client.submit_dogme_job(**job_params)
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
- Verify skill file exists: `ls skills/Dogme_DNA.md`
- Check SKILLS_REGISTRY in config.py
- Verify file path in registry matches actual file

### Database Locked

```
Error: Database is locked
```

**Solution:**
- Restart Server 1
- Check for other processes: `ps aux | grep server1`
- Use PostgreSQL for production instead of SQLite

### Server 3 Connection Error

```
Error: Cannot connect to Server 3
```

**Solution:**
- Check Server 3 is running: `curl http://localhost:8001/health`
- Verify Server 3 port: `echo $SERVER3_URL`
- Check firewall/network connectivity

## Performance Tuning

### Concurrent Chat Requests

```python
# server1/config.py
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
# server1/config.py
# Use PostgreSQL for production
DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/agoutic"
```

## Development Guidelines

### Adding a New Skill

1. Create markdown file in `skills/`:
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

2. Register in `server1/config.py`:
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
