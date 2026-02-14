"""
Server 3 MCP Server - HTTP Transport via fastMCP
Exposes Server 3 tools via the Model Context Protocol (MCP).

Allows external clients and Server 1's LLM Agent to invoke job management tools
over HTTP from any IP address.
"""
import json
import sys

from fastmcp import FastMCP

from common.logging_config import setup_logging, get_logger
from server3.mcp_tools import Server3MCPTools, TOOL_REGISTRY

# --- LOGGING ---
setup_logging("server3-mcp")
logger = get_logger(__name__)

# Create fastMCP server
mcp = FastMCP("AGOUTIC-Server3-MCP", version="0.3.0")

# Initialize tools
tools = Server3MCPTools()

# Register all tools via decorators
@mcp.tool()
async def submit_dogme_job(
    sample_name: str,
    mode: str,
    input_directory: str,
    reference_genome: str | list[str] = "mm39",
    project_id: str | None = None,
    modifications: str | None = None,
    input_type: str | None = None,
    entry_point: str | None = None,
    modkit_filter_threshold: float | None = None,
    min_cov: int | None = None,
    per_mod: int | None = None,
    accuracy: str | None = None,
) -> str:
    """Submit a DOGME job for processing. Input can be pod5, bam, or fastq files. Supports multiple reference genomes in parallel."""
    import uuid as _uuid
    _project_id = project_id or f"mcp_{_uuid.uuid4().hex[:12]}"
    result = await tools.submit_dogme_job(
        project_id=_project_id,
        sample_name=sample_name,
        mode=mode,
        reference_genome=reference_genome,
        input_directory=input_directory,
        modifications=modifications,
        input_type=input_type,
        entry_point=entry_point,
        modkit_filter_threshold=modkit_filter_threshold,
        min_cov=min_cov,
        per_mod=per_mod,
        accuracy=accuracy,
    )
    return json.dumps(result, indent=2)

@mcp.tool()
async def check_nextflow_status(run_uuid: str) -> str:
    """Check the status of a Nextflow job."""
    result = await tools.check_nextflow_status(run_uuid=run_uuid)
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_dogme_report(run_uuid: str) -> str:
    """Get the DOGME report for a completed job."""
    result = await tools.get_dogme_report(run_uuid=run_uuid)
    return json.dumps(result, indent=2)

@mcp.tool()
async def submit_dogme_nextflow(
    config_file: str,
    output_dir: str,
) -> str:
    """Submit a Nextflow workflow directly."""
    result = await tools.submit_dogme_nextflow(
        config_file=config_file,
        output_dir=output_dir,
    )
    return json.dumps(result, indent=2)

@mcp.tool()
async def find_pod5_directory(base_path: str, sample_name: str) -> str:
    """Find POD5 directory for a sample."""
    result = await tools.find_pod5_directory(
        base_path=base_path,
        sample_name=sample_name,
    )
    return json.dumps(result, indent=2)

@mcp.tool()
async def generate_dogme_config(
    mode: str,
    reference_genome: str,
    modifications: str | None = None,
) -> str:
    """Generate a DOGME configuration file."""
    result = await tools.generate_dogme_config(
        mode=mode,
        reference_genome=reference_genome,
        modifications=modifications,
    )
    return result  # Already returns string

@mcp.tool()
async def scaffold_dogme_dir(
    base_path: str,
    sample_name: str,
    mode: str,
) -> str:
    """Scaffold a DOGME working directory."""
    result = await tools.scaffold_dogme_dir(
        base_path=base_path,
        sample_name=sample_name,
        mode=mode,
    )
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_job_logs(
    run_uuid: str,
    limit: int = 50,
) -> str:
    """Get recent log entries for a running or completed job."""
    result = await tools.get_job_logs(
        run_uuid=run_uuid,
        limit=limit,
    )
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_job_debug(
    run_uuid: str,
) -> str:
    """Get detailed debug information for troubleshooting a job."""
    result = await tools.get_job_debug(
        run_uuid=run_uuid,
    )
    return json.dumps(result, indent=2)

if __name__ == "__main__":
    import argparse
    import uvicorn
    
    parser = argparse.ArgumentParser(description="AGOUTIC Server 3 MCP Server")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0 - all interfaces)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8002,
        help="Port to bind to (default: 8002)",
    )
    
    args = parser.parse_args()
    
    logger.info("Server 3 MCP server starting", host=args.host, port=args.port,
                tools=list(TOOL_REGISTRY.keys()))
    
    # Get the FastAPI app from fastMCP
    app = mcp.http_app
    
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
