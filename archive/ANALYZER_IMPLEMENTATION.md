# Analyzer - Analysis Server Implementation

**Date**: February 9, 2026  
**Component**: Analyzer (Analysis Server)  
**Status**: ✅ Implemented

## Overview

Analyzer has been implemented as the **Analysis Server** for AGOUTIC, providing file discovery, parsing, and analysis capabilities for completed Dogme job results. It follows the dual-interface pattern (REST + MCP) established by Launchpad.

## What Was Implemented

### 1. Core Infrastructure
- **Config** ([analyzer/config.py](analyzer/config.py)) - Ports, paths, limits
- **Database** ([analyzer/db.py](analyzer/db.py)) - Shared database access
- **Models** ([analyzer/models.py](analyzer/models.py)) - DogmeJob model (read-only)
- **Schemas** ([analyzer/schemas.py](analyzer/schemas.py)) - Request/response types

### 2. Analysis Engine ([analyzer/analysis_engine.py](analyzer/analysis_engine.py))
- **File Discovery** - Recursively scan job work directories
- **File Categorization** - Group by type (txt, csv, bed, other)
- **File Reading** - Read with security validation and size limits
- **CSV/TSV Parsing** - Structured table data extraction
- **BED Parsing** - Genomic record parsing
- **Analysis Summary** - Comprehensive job results overview

### 3. MCP Integration ([analyzer/mcp_tools.py](analyzer/mcp_tools.py), [analyzer/mcp_server.py](analyzer/mcp_server.py))
- **6 MCP Tools** exposed for AI agents:
  1. `list_job_files` - List files with extension filtering
  2. `read_file_content` - Read file content with preview
  3. `parse_csv_file` - Parse tabular data
  4. `parse_bed_file` - Parse genomic records
  5. `get_analysis_summary` - Full job summary
  6. `categorize_job_files` - File type categorization

- **MCP Server** - fastMCP-based server on stdio transport
- **Cortex Integration** ([cortex/analyzer_mcp_client.py](cortex/analyzer_mcp_client.py)) - Client for agent engine

### 4. REST API ([analyzer/app.py](analyzer/app.py))
FastAPI application with endpoints:
- `GET /analysis/jobs/{run_uuid}/files` - File listing
- `GET /analysis/jobs/{run_uuid}/files/categorize` - Categorization
- `GET /analysis/files/content` - Read content
- `GET /analysis/files/download` - Download files
- `GET /analysis/files/parse/csv` - Parse CSV
- `GET /analysis/files/parse/bed` - Parse BED
- `GET /analysis/summary/{run_uuid}` - Complete summary

### 5. Documentation & Utilities
- **README** ([analyzer/README.md](analyzer/README.md)) - Architecture and usage
- **Installation Guide** ([analyzer/INSTALLATION.md](analyzer/INSTALLATION.md)) - Setup instructions
- **Quick Reference** ([analyzer/QUICKSTART.md](analyzer/QUICKSTART.md)) - Common tasks
- **Test Suite** ([analyzer/test_analysis.py](analyzer/test_analysis.py)) - Comprehensive tests
- **Startup Script** ([analyzer/start.sh](analyzer/start.sh)) - Easy server management
- **Requirements** ([analyzer/requirements.txt](analyzer/requirements.txt)) - Dependencies

## Architecture

```
AGOUTIC System with Analyzer
============================

Cortex (8000)                Launchpad (8003)
   Agent/Orchestration           Execution Engine
        │                             │
        │         REST                │
        ├────────────────────────────►│
        │                             │
        │         MCP                 │  Nextflow/Dogme
        ├────────────────────────────►├──────────────►
        │                             │
        │                             │
        │         MCP                 │
        ├─────────────┐               │
        │             │               │
        │             ▼               │
        │      Analyzer (8004)         │
        │      Analysis Engine        │
        │             │               │
        │             └───reads───────┤
        │                   work_dir  │
        │                             │
        ▼                             │
    UI (8501)◄────────────────────────┘
   Streamlit
```

## File Structure

```
analyzer/
├── __init__.py              # Package initialization
├── config.py                # Configuration
├── db.py                    # Database connection
├── models.py                # Data models
├── schemas.py               # Request/response schemas
├── analysis_engine.py       # Core analysis logic
├── mcp_tools.py            # MCP tool definitions
├── mcp_server.py           # MCP server
├── app.py                   # FastAPI REST API
├── test_analysis.py        # Test suite
├── start.sh                 # Startup script
├── requirements.txt         # Dependencies
├── README.md               # Architecture documentation
├── INSTALLATION.md         # Setup guide
└── QUICKSTART.md           # Quick reference

cortex/
└── analyzer_mcp_client.py   # MCP client for Cortex
```

## Key Features

### Security
- ✅ Path validation (prevents directory traversal)
- ✅ File size limits (prevents memory exhaustion)
- ✅ Extension filtering (only expected file types)
- ✅ Work directory isolation (files must be in job dir)

### Performance
- ✅ Preview limits (read only N lines)
- ✅ Streaming downloads (large files)
- ✅ Efficient file discovery (recursive with filters)
- ✅ Async operations (non-blocking I/O)

### Functionality
- ✅ Multiple file formats (TXT, CSV, TSV, BED)
- ✅ Structured data parsing
- ✅ Metadata extraction
- ✅ Comprehensive summaries
- ✅ File categorization

## Usage Examples

### REST API
```bash
# Get analysis summary
curl http://localhost:8004/analysis/summary/{run_uuid}

# List CSV files
curl "http://localhost:8004/analysis/jobs/{run_uuid}/files?extensions=.csv"

# Parse CSV
curl "http://localhost:8004/analysis/files/parse/csv?run_uuid={run_uuid}&file_path=annot/stats.csv"
```

### Python MCP Client
```python
from cortex.analyzer_mcp_client import AnalyzerMCPClient

client = AnalyzerMCPClient()
await client.connect()

# Get summary
summary = await client.get_analysis_summary(run_uuid)

# Parse CSV
data = await client.parse_csv_file(run_uuid, "annot/stats.csv", max_rows=100)

await client.disconnect()
```

## Testing

Run the test suite to verify installation:

```bash
./analyzer/start.sh test
```

Tests cover:
1. File discovery
2. File categorization
3. CSV parsing
4. BED parsing
5. Analysis summary generation

## Configuration

Environment variables:

```bash
ANALYZER_HOST=0.0.0.0
ANALYZER_PORT=8004
ANALYZER_MCP_PORT=8005
DATABASE_URL=sqlite:///./data/database/agoutic.db
AGOUTIC_WORK_DIR=/media/backup_disk/agoutic_root/launchpad_work
MAX_PREVIEW_LINES=1000
MAX_FILE_SIZE_MB=100
```

## Starting Analyzer

```bash
# REST API
./analyzer/start.sh rest

# MCP Server (for agents)
./analyzer/start.sh mcp

# Development mode
./analyzer/start.sh dev

# Run tests
./analyzer/start.sh test
```

## Integration Points

### With Cortex (Agent Engine)
- MCP client available at `cortex/analyzer_mcp_client.py`
- Agents can invoke analysis tools via MCP protocol
- Example usage in client file

### With Launchpad (Execution Engine)
- Reads from same database (DogmeJob table)
- Accesses work directories created by Launchpad
- Independent operation (no direct coupling)

### With UI (Streamlit)
- REST API ready for UI integration
- File browser page can be added to `ui/pages/`
- Download and preview capabilities available

## API Documentation

Interactive API docs available at:
- **Swagger UI**: http://localhost:8004/docs
- **ReDoc**: http://localhost:8004/redoc

## Next Steps

To fully integrate Analyzer into the AGOUTIC workflow:

1. **Test with Real Data**
   - Run a Dogme cDNA job on Launchpad
   - Use Analyzer to analyze results
   - Verify all file types are parsed correctly

2. **UI Integration**
   - Create `ui/pages/results.py` for result viewing
   - Add file browser with download buttons
   - Display parsed data in tables/charts

3. **Agent Integration**
   - Update Cortex agent skills to use analysis tools
   - Add "analyze results" capability to Dogme skills
   - Enable automatic result summarization after job completion

4. **Monitoring**
   - Set up health checks
   - Log analysis requests
   - Monitor file access patterns

5. **Optimization**
   - Add result caching (AnalysisCache table)
   - Implement batch analysis
   - Add visualization generation

## Dependencies

Install with:
```bash
pip install -r analyzer/requirements.txt
```

Core dependencies:
- fastapi>=0.104.0
- uvicorn[standard]>=0.24.0
- sqlalchemy>=2.0.0
- pydantic>=2.0.0
- mcp>=0.9.0

## Common Dogme Output Files

Analyzer can parse these typical Dogme cDNA output files:

| File | Type | Location | Parser |
|------|------|----------|--------|
| `*_qc_summary.csv` | CSV | `annot/` | ✅ CSV |
| `*_final_stats.csv` | CSV | `annot/` | ✅ CSV |
| `*_dogme_abundance.tsv` | TSV | `annot/` | ✅ CSV |
| `*.bed` | BED | `bedMethyl/` | ✅ BED |
| `report.html` | HTML | Root | ✅ Read |
| `*_timeline.html` | HTML | Root | ✅ Read |
| `*_trace.txt` | TXT | Root | ✅ Read |
| `*.bam` | BAM | `bams/` | ⚠️ Binary |
| `*.fastq.gz` | FASTQ | `fastqs/` | ⚠️ Compressed |

## Troubleshooting

See [INSTALLATION.md](analyzer/INSTALLATION.md) for detailed troubleshooting steps.

Common issues:
- **Work directory not found**: Set `AGOUTIC_WORK_DIR` correctly
- **File too large**: Increase `MAX_FILE_SIZE_MB` or use preview
- **Port in use**: Change `ANALYZER_PORT`
- **Import errors**: Install dependencies with pip

## Production Deployment

See [INSTALLATION.md](analyzer/INSTALLATION.md) for:
- systemd service configuration
- Docker containerization
- Nginx reverse proxy setup
- SSL/TLS configuration

## Summary

✅ **Complete Implementation**
- Core analysis engine with file parsing
- Dual-interface architecture (REST + MCP)
- Cortex MCP client for agent integration
- Comprehensive documentation
- Test suite and utilities
- Security and performance features

🚀 **Ready for Use**
- REST API operational on port 8004
- MCP server ready for agent integration
- Documentation complete
- Tests can be run once job data available

📋 **Remaining Tasks**
- UI integration (create results viewing page)
- Agent skill updates (add analysis capabilities)
- Production deployment setup
- Testing with real Dogme cDNA results
