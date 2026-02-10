# Server4 - Analysis Server

Server4 is the **Analysis Server** for the AGOUTIC system. It provides file discovery, parsing, and analysis capabilities for completed Dogme job results.

## Purpose

After a Dogme cDNA/DNA/RNA run completes, result files are scattered across multiple subdirectories in the work directory:
- `annot/` - Annotated results (TSV, CSV)
- `bams/` - Aligned BAM files
- `bedMethyl/` - BED format modification calls
- `fastqs/` - FASTQ files
- `kallisto/` - Kallisto quantification results
- Root directory - Reports, logs, HTML files

Server4 provides tools to:
1. **Discover** all result files
2. **Read** file contents (with preview limits)
3. **Parse** structured data (CSV, TSV, BED)
4. **Analyze** and summarize results
5. **Download** files via API

## Architecture

Server4 follows the **dual-interface pattern** established by Server3:

```
┌─────────────────────────────────────────────────────────┐
│                    AGOUTIC System                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐         ┌─────────────────┐         │
│  │   Server 1   │◄───────►│    Server 3     │         │
│  │ Agent Engine │  REST + │ Execution Engine│         │
│  │   (Port 8000)│   MCP   │   (Port 8003)   │         │
│  └──────┬───────┘         └────────┬────────┘         │
│         │                          │                   │
│         │                          └──→ Nextflow/Dogme │
│         │                                               │
│         │                 ┌─────────────────┐          │
│         └────────────────►│    Server 4     │          │
│                   REST +  │ Analysis Engine │          │
│                    MCP    │   (Port 8004)   │          │
│                           └─────────────────┘          │
│                                                         │
│  ┌──────────────┐                                      │
│  │     UI       │                                      │
│  │ (Port 8501)  │                                      │
│  └──────────────┘                                      │
└─────────────────────────────────────────────────────────┘
```

### Interfaces

**REST API (Port 8004)**
- Traditional HTTP endpoints
- Used by UI and external clients
- Full OpenAPI documentation at `/docs`

**MCP Protocol (Port 8005 or stdio)**
- Model Context Protocol for LLM agents
- Used by Server1's agent engine
- Exposes analysis tools to AI agents

## Components

### Core Files

- **`config.py`** - Configuration (ports, paths, limits)
- **`db.py`** - Database connection (shared with server1/server3)
- **`models.py`** - Database models (reads DogmeJob table)
- **`schemas.py`** - Request/response schemas
- **`analysis_engine.py`** - Core analysis logic
- **`mcp_tools.py`** - MCP tool definitions
- **`mcp_server.py`** - MCP server implementation
- **`app.py`** - FastAPI REST API

### Key Functions

**File Discovery** (`analysis_engine.py`)
- `discover_files()` - Find all files in work directory
- `categorize_files()` - Group by type (txt, csv, bed)
- `get_job_work_dir()` - Locate job work directory

**File Reading**
- `read_file_content()` - Read text files with preview limits
- Security: Path validation, size limits

**Parsing**
- `parse_csv_file()` - Parse CSV/TSV into structured data
- `parse_bed_file()` - Parse BED format genomic records

**Analysis**
- `generate_analysis_summary()` - Comprehensive job summary

## MCP Tools

Server4 exposes 6 MCP tools for AI agents:

1. **`list_job_files`** - List all files with optional extension filtering
2. **`read_file_content`** - Read file content with preview limits
3. **`parse_csv_file`** - Parse CSV/TSV files
4. **`parse_bed_file`** - Parse BED format files
5. **`get_analysis_summary`** - Get comprehensive summary
6. **`categorize_job_files`** - Categorize files by type

### Tool Usage Example

```python
# Via MCP client
from server1.server4_mcp_client import Server4MCPClient

client = Server4MCPClient()
await client.connect()

# List files
files = await client.list_job_files(
    run_uuid="72f9f625-c755-441b-bab4-d91e735b8612",
    extensions=".csv,.bed"
)

# Parse CSV
data = await client.parse_csv_file(
    run_uuid="72f9f625-c755-441b-bab4-d91e735b8612",
    file_path="annot/kourosh.mm39_final_stats.csv",
    max_rows=100
)
```

## REST API Endpoints

### File Discovery
- `GET /analysis/jobs/{run_uuid}/files` - List files
- `GET /analysis/jobs/{run_uuid}/files/categorize` - Categorize files

### File Content
- `GET /analysis/files/content` - Read file content
- `GET /analysis/files/download` - Download file

### Parsing
- `GET /analysis/files/parse/csv` - Parse CSV/TSV
- `GET /analysis/files/parse/bed` - Parse BED

### Summary
- `GET /analysis/summary/{run_uuid}` - Analysis summary

## Configuration

### Environment Variables

```bash
# Server configuration
SERVER4_HOST=0.0.0.0
SERVER4_PORT=8004
SERVER4_MCP_PORT=8005

# Database (shared with server1/server3)
DATABASE_URL=sqlite:///./data/database/agoutic.db

# Work directory (where job results are stored)
AGOUTIC_WORK_DIR=./data/server3_work

# File limits
MAX_PREVIEW_LINES=1000
MAX_FILE_SIZE_MB=100
```

### Security Features

1. **Path Validation** - Prevents directory traversal attacks
2. **File Size Limits** - Prevents memory exhaustion
3. **Extension Filtering** - Only serves expected file types
4. **Work Directory Isolation** - Files must be within job work dir

## Running Server4

### As REST API Server

```bash
# Start REST API on port 8004
python -m server4.app
```

### As MCP Server (for agent integration)

```bash
# Start MCP server on stdio
python -m server4.mcp_server
```

### With uvicorn (production)

```bash
uvicorn server4.app:app --host 0.0.0.0 --port 8004 --workers 4
```

## Integration with Server1

Server1's agent engine can use Server4 tools via MCP:

```python
from server1.server4_mcp_client import Server4MCPClient

# In agent_engine.py
class AgentEngine:
    def __init__(self):
        self.analysis_client = Server4MCPClient()
        await self.analysis_client.connect()
    
    async def analyze_job_results(self, run_uuid: str):
        # Agent can now use analysis tools
        summary = await self.analysis_client.get_analysis_summary(run_uuid)
        return summary
```

## Example: Analyzing Dogme cDNA Results

```python
import asyncio
from server1.server4_mcp_client import Server4MCPClient

async def analyze_cdna_run():
    client = Server4MCPClient()
    await client.connect()
    
    run_uuid = "72f9f625-c755-441b-bab4-d91e735b8612"
    
    # Get summary
    summary = await client.get_analysis_summary(run_uuid)
    print(f"Sample: {summary['sample_name']}")
    print(f"Status: {summary['status']}")
    print(f"Total files: {summary['key_results']['total_files']}")
    
    # List CSV files
    categories = await client.categorize_job_files(run_uuid)
    for csv_file in categories['csv_files']:
        print(f"CSV: {csv_file['name']} ({csv_file['size']} bytes)")
        
        # Parse CSV
        data = await client.parse_csv_file(run_uuid, csv_file['path'], max_rows=5)
        print(f"  Columns: {data['columns']}")
        print(f"  Rows: {data['row_count']}")
    
    await client.disconnect()

asyncio.run(analyze_cdna_run())
```

## Output File Patterns

### Common Dogme cDNA Files

| File Pattern | Type | Description |
|-------------|------|-------------|
| `*_qc_summary.csv` | CSV | Quality control metrics |
| `*_final_stats.csv` | CSV | Final alignment statistics |
| `*_dogme_abundance.tsv` | TSV | Transcript abundance |
| `*.bed` | BED | Methylation calls |
| `*.bam` | BAM | Aligned reads |
| `report.html` | HTML | Nextflow execution report |
| `*_timeline.html` | HTML | Timeline visualization |
| `*_trace.txt` | TXT | Task execution trace |

## Future Enhancements

1. **Visualization** - Generate plots from parsed data
2. **Comparative Analysis** - Compare results across jobs
3. **Advanced BED Parsing** - Methylation-specific analysis
4. **Result Caching** - Cache parsed data for faster access
5. **Batch Analysis** - Analyze multiple jobs simultaneously
6. **Export** - Export summaries to PDF/Excel

## Dependencies

- **fastapi** - REST API framework
- **uvicorn** - ASGI server
- **sqlalchemy** - Database ORM
- **pydantic** - Data validation
- **mcp** - Model Context Protocol SDK

## Testing

```bash
# Test MCP server
python server1/server4_mcp_client.py

# Test REST API
curl http://localhost:8004/health
curl http://localhost:8004/analysis/jobs/{run_uuid}/files
```

## Troubleshooting

**"Work directory not found"**
- Ensure `AGOUTIC_WORK_DIR` points to correct location
- Check that job completed successfully (check server3 database)

**"File too large"**
- Adjust `MAX_FILE_SIZE_MB` environment variable
- Use preview_lines parameter to limit content

**"Invalid file path"**
- Path must be relative to work directory
- Security validation prevents directory traversal

**MCP connection fails**
- Ensure server4 dependencies installed
- Check that Python module path includes server4/
