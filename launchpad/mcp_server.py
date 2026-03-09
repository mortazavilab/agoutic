"""
Launchpad MCP Server - HTTP Transport via fastMCP
Exposes Launchpad tools via the Model Context Protocol (MCP).

Allows external clients and Cortex's LLM Agent to invoke job management tools
over HTTP from any IP address.
"""
import json
import sys

from fastmcp import FastMCP

from starlette.responses import JSONResponse
from starlette.routing import Route

from common.logging_config import setup_logging, get_logger
from launchpad.mcp_tools import LaunchpadMCPTools, TOOL_REGISTRY

# --- LOGGING ---
setup_logging("launchpad-mcp")
logger = get_logger(__name__)

# Create fastMCP server
mcp = FastMCP("AGOUTIC-Launchpad-MCP", version="0.3.0")


# --- Schema endpoint (fetched by Cortex at startup) ---
def _get_tool_schemas() -> dict:
    """Build schemas dict from TOOL_REGISTRY."""
    return {
        name: {
            "description": entry.get("description", ""),
            "parameters": entry.get("input_schema", {}),
        }
        for name, entry in TOOL_REGISTRY.items()
        if isinstance(entry, dict)  # skip function references
    }


async def _tools_schema_endpoint(request):
    return JSONResponse(_get_tool_schemas())

# Initialize tools
tools = LaunchpadMCPTools()

# Register all tools via decorators
@mcp.tool()
async def submit_dogme_job(
    sample_name: str,
    mode: str,
    input_directory: str,
    reference_genome: str | list[str] = "mm39",
    project_id: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    project_slug: str | None = None,
    modifications: str | None = None,
    input_type: str | None = None,
    entry_point: str | None = None,
    modkit_filter_threshold: float | None = None,
    min_cov: int | None = None,
    per_mod: int | None = None,
    accuracy: str | None = None,
    max_gpu_tasks: int | None = None,
    resume_from_dir: str | None = None,
) -> str:
    """Submit a DOGME job for processing. Input can be pod5, bam, or fastq files. Supports multiple reference genomes in parallel."""
    import uuid as _uuid
    _project_id = project_id or f"mcp_{_uuid.uuid4().hex[:12]}"
    result = await tools.submit_dogme_job(
        project_id=_project_id,
        user_id=user_id,
        username=username,
        project_slug=project_slug,
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
        max_gpu_tasks=max_gpu_tasks,
        resume_from_dir=resume_from_dir,
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

@mcp.tool()
async def delete_job_data(
    run_uuid: str,
) -> str:
    """Delete the workflow folder and archive a completed, failed, or cancelled job."""
    result = await tools.delete_job_data(
        run_uuid=run_uuid,
    )
    return json.dumps(result, indent=2)

if __name__ == "__main__":
    import argparse
    import uvicorn
    
    parser = argparse.ArgumentParser(description="AGOUTIC Launchpad MCP Server")
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
    
    logger.info("Launchpad MCP server starting", host=args.host, port=args.port,
                tools=list(TOOL_REGISTRY.keys()))
    
    # Get the Starlette app from fastMCP and add schema endpoint
    app = mcp.http_app()
    app.routes.insert(0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"]))
    
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
