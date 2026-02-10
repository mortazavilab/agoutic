# Server4 Quick Reference

## Quick Start

```bash
# Make startup script executable
chmod +x server4/start.sh

# Test the installation
./server4/start.sh test

# Start REST API
./server4/start.sh rest

# Start in development mode (auto-reload)
./server4/start.sh dev
```

## API Examples

### List Files
```bash
# List all files
curl "http://localhost:8004/analysis/jobs/{RUN_UUID}/files"

# List only CSV and BED files
curl "http://localhost:8004/analysis/jobs/{RUN_UUID}/files?extensions=.csv,.bed"
```

### Categorize Files
```bash
curl "http://localhost:8004/analysis/jobs/{RUN_UUID}/files/categorize"
```

### Read File Content
```bash
curl "http://localhost:8004/analysis/files/content?run_uuid={RUN_UUID}&file_path=annot/sample.csv&preview_lines=100"
```

### Download File
```bash
curl -O "http://localhost:8004/analysis/files/download?run_uuid={RUN_UUID}&file_path=annot/sample.csv"
```

### Parse CSV
```bash
curl "http://localhost:8004/analysis/files/parse/csv?run_uuid={RUN_UUID}&file_path=annot/sample.csv&max_rows=50"
```

### Parse BED
```bash
curl "http://localhost:8004/analysis/files/parse/bed?run_uuid={RUN_UUID}&file_path=bedMethyl/sample.bed&max_records=50"
```

### Get Analysis Summary
```bash
curl "http://localhost:8004/analysis/summary/{RUN_UUID}"
```

## Python Client Examples

### Using REST API
```python
import requests

run_uuid = "72f9f625-c755-441b-bab4-d91e735b8612"

# Get summary
response = requests.get(f"http://localhost:8004/analysis/summary/{run_uuid}")
summary = response.json()
print(f"Sample: {summary['sample_name']}")

# List CSV files
response = requests.get(
    f"http://localhost:8004/analysis/jobs/{run_uuid}/files",
    params={"extensions": ".csv"}
)
files = response.json()
for file in files['files']:
    print(f"- {file['name']} ({file['size']} bytes)")
```

### Using MCP Client
```python
import asyncio
from server1.server4_mcp_client import Server4MCPClient

async def analyze():
    client = Server4MCPClient()
    await client.connect()
    
    run_uuid = "72f9f625-c755-441b-bab4-d91e735b8612"
    
    # Get summary
    summary = await client.get_analysis_summary(run_uuid)
    print(summary)
    
    # Categorize files
    categories = await client.categorize_job_files(run_uuid)
    print(f"CSV files: {len(categories['csv_files'])}")
    
    # Parse CSV
    if categories['csv_files']:
        csv_path = categories['csv_files'][0]['path']
        data = await client.parse_csv_file(run_uuid, csv_path, max_rows=10)
        print(f"Columns: {data['columns']}")
    
    await client.disconnect()

asyncio.run(analyze())
```

## MCP Tools

All 6 MCP tools available for AI agents:

1. **list_job_files** - `list_job_files(run_uuid, extensions?)`
2. **read_file_content** - `read_file_content(run_uuid, file_path, preview_lines?)`
3. **parse_csv_file** - `parse_csv_file(run_uuid, file_path, max_rows?)`
4. **parse_bed_file** - `parse_bed_file(run_uuid, file_path, max_records?)`
5. **get_analysis_summary** - `get_analysis_summary(run_uuid)`
6. **categorize_job_files** - `categorize_job_files(run_uuid)`

## Environment Variables

```bash
# Server configuration
export SERVER4_HOST=0.0.0.0
export SERVER4_PORT=8004
export SERVER4_MCP_PORT=8005

# Database (shared with server1/server3)
export DATABASE_URL=sqlite:///./data/database/agoutic.db

# Work directory (where job results are)
export AGOUTIC_WORK_DIR=./data/server3_work

# File limits
export MAX_PREVIEW_LINES=1000
export MAX_FILE_SIZE_MB=100
```

## Common Dogme Output Files

| Pattern | Type | Location |
|---------|------|----------|
| `*_qc_summary.csv` | QC metrics | `annot/` |
| `*_final_stats.csv` | Alignment stats | `annot/` |
| `*_dogme_abundance.tsv` | Abundance | `annot/` |
| `*.bed` | Methylation | `bedMethyl/` |
| `*.bam` | Alignments | `bams/` |
| `*.fastq.gz` | Reads | `fastqs/` |
| `report.html` | Nextflow | Root |
| `timeline.html` | Timeline | Root |
| `trace.txt` | Trace | Root |

## Troubleshooting

**"Work directory not found"**
```bash
# Check environment variable
echo $AGOUTIC_WORK_DIR

# Verify directory exists
ls -la $AGOUTIC_WORK_DIR

# Check job UUID exists
ls $AGOUTIC_WORK_DIR/{RUN_UUID}
```

**"File too large"**
```bash
# Increase limit
export MAX_FILE_SIZE_MB=500

# Or use preview_lines
curl "...&preview_lines=100"
```

**Connection refused**
```bash
# Check if server is running
ps aux | grep server4

# Check port is not in use
lsof -i :8004

# Start server
./server4/start.sh rest
```

## Testing

```bash
# Run full test suite
./server4/start.sh test

# Or run directly
python3 server4/test_analysis.py

# Test specific endpoint
curl http://localhost:8004/health
```

## Integration with Server1

In `server1/agent_engine.py`:

```python
from server1.server4_mcp_client import Server4MCPClient

class AgentEngine:
    def __init__(self):
        # ... existing code ...
        self.analysis_client = Server4MCPClient()
    
    async def initialize(self):
        await self.analysis_client.connect()
    
    async def analyze_results(self, run_uuid: str):
        summary = await self.analysis_client.get_analysis_summary(run_uuid)
        return summary
```

## Port Summary

- Server1: 8000 (Agent/REST)
- Server3: 8003 (Execution/REST), 8002 (MCP)
- **Server4: 8004 (Analysis/REST), 8005 (MCP)**
- UI: 8501 (Streamlit)
