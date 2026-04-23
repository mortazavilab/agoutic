# Analyzer Quick Reference

## Quick Start

```bash
# Make startup script executable
chmod +x analyzer/start.sh

# Test the installation
./analyzer/start.sh test

# Start REST API
./analyzer/start.sh rest

# Start in development mode (auto-reload)
./analyzer/start.sh dev
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
from cortex.analyzer_mcp_client import AnalyzerMCPClient

async def analyze():
    client = AnalyzerMCPClient()
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

All 7 MCP tools available for AI agents (all accept `work_dir` preferred, `run_uuid` as fallback):

1. **list_job_files** - `list_job_files(work_dir, extensions?, compact?)`
2. **find_file** - `find_file(work_dir, file_name)` — partial match, skips `work/`
3. **read_file_content** - `read_file_content(work_dir, file_path, preview_lines?)`
4. **parse_csv_file** - `parse_csv_file(work_dir, file_path, max_rows?)`
5. **parse_bed_file** - `parse_bed_file(work_dir, file_path, max_records?)`
6. **get_analysis_summary** - `get_analysis_summary(work_dir)`
7. **categorize_job_files** - `categorize_job_files(work_dir)`

## Environment Variables

```bash
# Server configuration
export ANALYZER_HOST=0.0.0.0
export ANALYZER_PORT=8004
export ANALYZER_MCP_PORT=8005

# Database (shared with cortex/launchpad)
export DATABASE_URL=sqlite:///./data/database/agoutic.db

# Work directory (where job results are)
export AGOUTIC_WORK_DIR=./data/launchpad_work

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
ps aux | grep analyzer

# Check port is not in use
lsof -i :8004

# Start server
./analyzer/start.sh rest
```

## Testing

```bash
# Run full test suite
./analyzer/start.sh test

# Or run directly with pytest
python3 -m pytest tests/analyzer -q

# Test specific endpoint
curl http://localhost:8004/health
```

## Integration with Cortex

In `cortex/agent_engine.py`:

```python
from cortex.analyzer_mcp_client import AnalyzerMCPClient

class AgentEngine:
    def __init__(self):
        # ... existing code ...
        self.analysis_client = AnalyzerMCPClient()
    
    async def initialize(self):
        await self.analysis_client.connect()
    
    async def analyze_results(self, run_uuid: str):
        summary = await self.analysis_client.get_analysis_summary(run_uuid)
        return summary
```

## Port Summary

- Cortex: 8000 (Agent/REST)
- Launchpad: 8003 (Execution/REST), 8002 (MCP)
- **Analyzer: 8004 (Analysis/REST), 8005 (MCP)**
- UI: 8501 (Streamlit)
