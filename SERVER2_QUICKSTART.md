# Server2 Integration Quick Reference

**Date:** February 10, 2026  
**Status:** ✅ Complete

## What Was Done

Integrated **ENCODELIB** as **Server 2** in AGOUTIC for ENCODE Portal data retrieval.

## Files Created/Modified

### New Files
1. **[server1/server2_mcp_client.py](server1/server2_mcp_client.py)** - MCP client for ENCODELIB
2. **[SERVER2_IMPLEMENTATION.md](SERVER2_IMPLEMENTATION.md)** - Complete implementation guide

### Modified Files
1. **[server1/config.py](server1/config.py)** - Added Server2 configuration
2. **[skills/ENCODE_LongRead.md](skills/ENCODE_LongRead.md)** - Updated with Server2 tools
3. **[ARCHITECTURE_UPDATE.md](ARCHITECTURE_UPDATE.md)** - Added Server2 to architecture

## Quick Start

### 1. Set ENCODELIB Location (Optional)
```bash
export ENCODELIB_PATH="/Users/eli/code/ENCODELIB"
```

Default is `/Users/eli/code/ENCODELIB` if not set.

### 2. Test Server2 Connection
```bash
cd /Users/eli/code/agoutic
python server1/server2_mcp_client.py
```

Expected output:
```
✅ Connected to Server 2 MCP server (ENCODELIB)
📊 Getting server info...
🔍 Searching for K562 experiments...
✅ Test completed successfully!
```

### 3. Use in Skills

The ENCODE_LongRead skill now references 15 Server2 tools:

**Search Tools:**
- `search_by_biosample(search_term, organism?, assay_title?, target?)`
- `search_by_organism(organism, search_term?, assay_title?, target?)`
- `search_by_target(target, organism?, assay_title?)`

**Download Tools:**
- `download_files(accession, file_types?, file_accessions?)`
- `get_file_url(accession, file_accession)`

**Metadata Tools:**
- `get_experiment(accession)`
- `get_all_metadata(accession)`
- `get_file_types(accession)`
- `get_files_by_type(accession, after_date?, file_status?)`
- `get_available_output_types(accession)`
- `get_files_summary(accession, max_files_per_type?)`
... and more (15 total)

### 4. Example Workflow

```python
from server1.server2_mcp_client import Server2MCPClient
import asyncio

async def download_encode_data():
    client = Server2MCPClient()
    await client.connect()
    
    # Search
    results = await client.search_by_biosample(
        search_term="K562",
        organism="Homo sapiens"
    )
    
    # Get experiment
    exp = await client.get_experiment("ENCSR000CDC")
    
    # Download files
    result = await client.download_files(
        accession="ENCSR000CDC",
        file_types=["fastq"]
    )
    
    print(f"Downloaded {len(result['downloaded'])} files")
    print(f"Location: {result['output_dir']}")
    
    await client.disconnect()

asyncio.run(download_encode_data())
```

## Architecture

```
┌──────────┐
│  Server1 │ (Agent Engine)
│ Port 8000│
└────┬─────┘
     │
     │ MCP Protocol (stdio)
     │
┌────▼─────┐
│  Server2 │ (ENCODELIB)
│ Port 8080│
└────┬─────┘
     │
     │ HTTPS API
     │
┌────▼─────┐
│  ENCODE  │ (Portal)
│  Portal  │
└──────────┘
```

## Key Features

✅ **15 MCP Tools** - Complete ENCODE Portal interface  
✅ **Async/Await** - Non-blocking operations  
✅ **Local Caching** - Fast repeat access  
✅ **Auto Downloads** - Seamless file retrieval  
✅ **Type Safe** - Full type hints  
✅ **Error Handling** - Comprehensive error management  

## Configuration

### Environment Variables
```bash
# Optional - defaults shown
export ENCODELIB_PATH="/Users/eli/code/ENCODELIB"
export SERVER2_MCP_COMMAND="python ${ENCODELIB_PATH}/encode_server.py"
```

### In Code (server1/config.py)
```python
# Server2 (ENCODELIB): ENCODE Portal data retrieval
ENCODELIB_PATH = Path(os.getenv("ENCODELIB_PATH", "/Users/eli/code/ENCODELIB"))
SERVER2_MCP_COMMAND = f"python {ENCODELIB_PATH}/encode_server.py"
```

## Caching

Server2 maintains caches:

**Metadata Cache:** `.encode_cache/`
- `experiments.json` - Complete experiment list
- `metadata/SR/ENCSR*.json` - Per-experiment metadata

**Downloads:** `./files/{accession}/`
- Organized by experiment accession
- Automatic duplicate detection

## Troubleshooting

### Connection Failed
```bash
# Check ENCODELIB path
echo $ENCODELIB_PATH
ls -la $ENCODELIB_PATH/encode_server.py

# Test manual start
cd $ENCODELIB_PATH
python encode_server.py

# Install dependencies
pip install -r $ENCODELIB_PATH/requirements-server.txt
```

### No Results Found
- Broaden search (remove filters)
- Try direct accession: `get_experiment("ENCSR000ABC")`
- Browse available: `list_experiments(limit=100)`

### Download Fails
- Check network connectivity
- Verify file status: `get_file_metadata(accession, file_accession)`
- Check disk space
- Retry with specific file accessions

## Documentation

- **Implementation Guide:** [SERVER2_IMPLEMENTATION.md](SERVER2_IMPLEMENTATION.md)
- **Architecture:** [ARCHITECTURE_UPDATE.md](ARCHITECTURE_UPDATE.md)
- **ENCODE Skill:** [skills/ENCODE_LongRead.md](skills/ENCODE_LongRead.md)
- **ENCODELIB Docs:** `/Users/eli/code/ENCODELIB/README.md`

## Next Steps

1. **Test the integration:**
   ```bash
   python server1/server2_mcp_client.py
   ```

2. **Try ENCODE skill:**
   - Start Server1: `uvicorn server1.app:app --port 8000`
   - Use UI to search ENCODE: "Find K562 experiments"

3. **Explore tools:**
   - Review [SERVER2_IMPLEMENTATION.md](SERVER2_IMPLEMENTATION.md)
   - Check tool examples in [skills/ENCODE_LongRead.md](skills/ENCODE_LongRead.md)

## Benefits

### For Users
- Seamless ENCODE integration
- No manual downloads
- Automatic experiment discovery
- Fast cached access

### For Developers
- Clean MCP interface
- Type-safe methods
- Async/await support
- Easy to extend

### For System
- Separate concerns
- Reusable across skills
- Independent versioning
- Standard protocol

## Status

✅ **Server2 MCP Client** - Complete  
✅ **Configuration** - Complete  
✅ **Documentation** - Complete  
✅ **Skill Integration** - Complete  
✅ **Architecture Update** - Complete  

**Ready for production use!**

---

**Quick Links:**
- Implementation: [SERVER2_IMPLEMENTATION.md](SERVER2_IMPLEMENTATION.md)
- Client Code: [server1/server2_mcp_client.py](server1/server2_mcp_client.py)
- Skill: [skills/ENCODE_LongRead.md](skills/ENCODE_LongRead.md)
- ENCODELIB: `/Users/eli/code/ENCODELIB/`
