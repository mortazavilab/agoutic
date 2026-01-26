"""
Server 3 MCP Server - HTTP Transport via fastMCP
Exposes Server 3 tools via the Model Context Protocol (MCP).

Allows external clients and Server 1's LLM Agent to invoke job management tools
over HTTP from any IP address.
"""
import json
import sys

from fastmcp import FastMCP

from server3.mcp_tools import Server3MCPTools, TOOL_REGISTRY

# Create fastMCP server
mcp = FastMCP("AGOUTIC-Server3-MCP", version="0.3.0")

# Initialize tools
tools = Server3MCPTools()

# Register all tools via decorators
@mcp.tool()
async def submit_dogme_job(
    sample_name: str,
    mode: str,
    reference_genome: str,
    pod5_directory: str,
    modifications: str | None = None,
) -> str:
    """Submit a DOGME job for processing."""
    result = await tools.submit_dogme_job(
        sample_name=sample_name,
        mode=mode,
        reference_genome=reference_genome,
        pod5_directory=pod5_directory,
        modifications=modifications,
    )
    return json.dumps(result, indent=2)

@mcp.tool()
async def check_nextflow_status(job_id: str) -> str:
    """Check the status of a Nextflow job."""
    result = await tools.check_nextflow_status(job_id=job_id)
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_dogme_report(job_id: str) -> str:
    """Get the DOGME report for a completed job."""
    result = await tools.get_dogme_report(job_id=job_id)
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
    
    print(f"🚀 Server 3 MCP server starting on HTTP at {args.host}:{args.port}", file=sys.stderr)
    print("Available tools:", file=sys.stderr)
    for tool_name in TOOL_REGISTRY.keys():
        print(f"  - {tool_name}", file=sys.stderr)
    print(f"📍 Access endpoint: http://<your-ip>:{args.port}", file=sys.stderr)
    
    # Get the FastAPI app from fastMCP
    app = mcp.http_app
    
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
