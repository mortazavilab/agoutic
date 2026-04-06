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
async def run_allowlisted_script(
    script_id: str | None = None,
    script_path: str | None = None,
    script_args: list[str] | None = None,
    script_working_directory: str | None = None,
    timeout_seconds: float | None = 60.0,
) -> str:
    """Run a small allowlisted local Python utility script and return stdout/stderr directly."""
    result = await tools.run_allowlisted_script(
        script_id=script_id,
        script_path=script_path,
        script_args=script_args,
        script_working_directory=script_working_directory,
        timeout_seconds=timeout_seconds,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
async def submit_dogme_job(
    sample_name: str,
    mode: str,
    input_directory: str = "",
    run_type: str = "dogme",
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
    execution_mode: str = "local",
    ssh_profile_id: str | None = None,
    slurm_account: str | None = None,
    slurm_partition: str | None = None,
    slurm_cpus: int | None = None,
    slurm_memory_gb: int | None = None,
    slurm_walltime: str | None = None,
    slurm_gpus: int | None = None,
    slurm_gpu_type: str | None = None,
    remote_base_path: str | None = None,
    remote_input_path: str | None = None,
    staged_remote_input_path: str | None = None,
    cache_preflight: dict | None = None,
    result_destination: str | None = None,
    script_id: str | None = None,
    script_path: str | None = None,
    script_args: list[str] | None = None,
    script_working_directory: str | None = None,
) -> str:
    """Submit a DOGME job for processing. Input can be pod5, bam, or fastq files. Supports multiple reference genomes in parallel."""
    import uuid as _uuid
    _project_id = project_id or f"mcp_{_uuid.uuid4().hex[:12]}"
    result = await tools.submit_dogme_job(
        project_id=_project_id,
        run_type=run_type,
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
        execution_mode=execution_mode,
        ssh_profile_id=ssh_profile_id,
        slurm_account=slurm_account,
        slurm_partition=slurm_partition,
        slurm_cpus=slurm_cpus,
        slurm_memory_gb=slurm_memory_gb,
        slurm_walltime=slurm_walltime,
        slurm_gpus=slurm_gpus,
        slurm_gpu_type=slurm_gpu_type,
        remote_base_path=remote_base_path,
        remote_input_path=remote_input_path,
        staged_remote_input_path=staged_remote_input_path,
        cache_preflight=cache_preflight,
        result_destination=result_destination,
        script_id=script_id,
        script_path=script_path,
        script_args=script_args,
        script_working_directory=script_working_directory,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
async def stage_remote_sample(
    project_id: str,
    user_id: str,
    sample_name: str,
    mode: str,
    ssh_profile_id: str,
    input_directory: str = "",
    reference_genome: str | list[str] = "mm39",
    username: str | None = None,
    project_slug: str | None = None,
    remote_base_path: str | None = None,
    remote_input_path: str | None = None,
) -> str:
    """Stage a sample and references remotely without submitting a SLURM job."""
    result = await tools.stage_remote_sample(
        project_id=project_id,
        user_id=user_id,
        username=username,
        project_slug=project_slug,
        sample_name=sample_name,
        mode=mode,
        input_directory=input_directory,
        reference_genome=reference_genome,
        ssh_profile_id=ssh_profile_id,
        remote_base_path=remote_base_path,
        remote_input_path=remote_input_path,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_staging_task_status(task_id: str) -> str:
    """Poll the status of an async staging task started by stage_remote_sample."""
    result = await tools.get_staging_task_status(task_id=task_id)
    return json.dumps(result, indent=2)


@mcp.tool()
async def check_nextflow_status(run_uuid: str) -> str:
    """Check the status of a Nextflow job."""
    result = await tools.check_nextflow_status(run_uuid=run_uuid)
    return json.dumps(result, indent=2)


@mcp.tool()
async def sync_job_results(run_uuid: str, force: bool = False) -> str:
    """Manually retry remote-to-local result synchronization for a SLURM run."""
    result = await tools.sync_job_results(run_uuid=run_uuid, force=force)
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


# ==================== Remote Execution MCP Tools ====================


@mcp.tool()
async def list_ssh_profiles(
    user_id: str,
) -> str:
    """List saved SSH connection profiles for a user."""
    import httpx
    url = tools.server_url + "/ssh-profiles"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params={"user_id": user_id}, headers=tools._headers())
        return resp.text


@mcp.tool()
async def test_ssh_connection(
    profile_id: str,
    user_id: str,
) -> str:
    """Test SSH connectivity for a saved profile."""
    import httpx
    url = f"{tools.server_url}/ssh-profiles/{profile_id}/test"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, params={"user_id": user_id}, headers=tools._headers())
        return resp.text


@mcp.tool()
async def get_slurm_defaults(
    user_id: str,
    project_id: str | None = None,
    ssh_profile_id: str | None = None,
    profile_nickname: str | None = None,
) -> str:
    """Get saved SLURM resource defaults, with SSH profile defaults as fallback."""
    from sqlalchemy import select
    from common.database import AsyncSessionLocal
    from launchpad.models import SlurmDefaults, SSHProfile
    async with AsyncSessionLocal() as session:
        query = select(SlurmDefaults).where(SlurmDefaults.user_id == user_id)
        if project_id:
            query = query.where(SlurmDefaults.project_id == project_id)
        else:
            query = query.where(SlurmDefaults.project_id.is_(None))
        result = await session.execute(query)
        defaults = result.scalar_one_or_none()

        profile_query = select(SSHProfile).where(SSHProfile.user_id == user_id, SSHProfile.is_enabled.is_(True))
        profile_result = await session.execute(profile_query)
        profiles = list(profile_result.scalars().all())

        if ssh_profile_id:
            profiles = [p for p in profiles if p.id == ssh_profile_id]
        elif profile_nickname:
            wanted = profile_nickname.strip().lower()
            profiles = [p for p in profiles if (p.nickname or "").strip().lower() == wanted]

        profile_defaults = []
        for p in profiles:
            profile_defaults.append({
                "ssh_profile_id": p.id,
                "nickname": p.nickname,
                "default_slurm_account": p.default_slurm_account,
                "default_slurm_partition": p.default_slurm_partition,
                "default_slurm_gpu_account": p.default_slurm_gpu_account,
                "default_slurm_gpu_partition": p.default_slurm_gpu_partition,
                "remote_base_path": p.remote_base_path,
            })

        selected_profile = profile_defaults[0] if profile_defaults else None
        has_profile_defaults = bool(
            selected_profile
            and (
                selected_profile.get("default_slurm_account")
                or selected_profile.get("default_slurm_partition")
                or selected_profile.get("default_slurm_gpu_account")
                or selected_profile.get("default_slurm_gpu_partition")
            )
        )

        if defaults:
            return json.dumps({
                "found": True,
                "source": "slurm_defaults",
                "account": defaults.account,
                "partition": defaults.partition,
                "cpus": defaults.cpus,
                "memory_gb": defaults.memory_gb,
                "walltime": defaults.walltime,
                "gpus": defaults.gpus,
                "gpu_type": defaults.gpu_type,
                "ssh_profile_defaults": profile_defaults,
                "selected_profile_defaults": selected_profile,
            }, indent=2)

        if has_profile_defaults:
            return json.dumps({
                "found": True,
                "source": "ssh_profile_defaults",
                "account": selected_profile.get("default_slurm_account"),
                "partition": selected_profile.get("default_slurm_partition"),
                "cpus": None,
                "memory_gb": None,
                "walltime": None,
                "gpus": None,
                "gpu_type": None,
                "ssh_profile_defaults": profile_defaults,
                "selected_profile_defaults": selected_profile,
                "message": "Using defaults saved on SSH profile.",
            }, indent=2)

        return json.dumps({
            "found": False,
            "source": "none",
            "ssh_profile_defaults": profile_defaults,
            "message": "No saved defaults in slurm_defaults or SSH profile defaults.",
        }, indent=2)


@mcp.tool()
async def cancel_slurm_job(
    run_uuid: str,
) -> str:
    """Cancel a running SLURM job on the remote cluster."""
    from launchpad.backends import get_backend
    backend = get_backend("slurm")
    ok = await backend.cancel(run_uuid)
    return json.dumps({"ok": ok, "run_uuid": run_uuid})


@mcp.tool()
async def list_remote_files(
    user_id: str,
    ssh_profile_id: str,
    path: str | None = None,
) -> str:
    """List files for an SSH profile, defaulting to its configured remote base path."""
    from launchpad.backends import get_backend

    backend = get_backend("slurm")
    result = await backend.list_remote_files(
        user_id=user_id,
        ssh_profile_id=ssh_profile_id,
        path=path,
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
