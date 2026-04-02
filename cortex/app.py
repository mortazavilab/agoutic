import asyncio
import datetime
import uuid
import json
import hashlib
import os
import re
import shutil
import httpx
from typing import Optional

from fastapi import FastAPI, HTTPException, Path as FastAPIPath, Request
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool  # To run LLM without blocking
from pydantic import BaseModel
from sqlalchemy import select, desc, func, text
from sqlalchemy.exc import OperationalError

# ✅ Import from your package
from cortex.schemas import BlockCreate, BlockOut, BlockStreamOut, BlockUpdate
from cortex.agent_engine import AgentEngine
from cortex.config import SKILLS_REGISTRY, GENOME_ALIASES, AVAILABLE_GENOMES, AGOUTIC_DATA
from cortex.db import SessionLocal, init_db_sync, next_seq_sync, row_to_dict
from cortex.models import ProjectBlock, Conversation, ConversationMessage, JobResult, User, ProjectAccess, Project, UserFile
from cortex.middleware import AuthMiddleware

# --- VERSION (single source of truth: VERSION file at repo root) ---
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
AGOUTIC_VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"
REMOTE_STAGE_MCP_TIMEOUT = float(os.getenv("LAUNCHPAD_STAGE_TIMEOUT", "3600"))
from cortex.auth import router as auth_router
from cortex.admin import router as admin_router
from cortex.dependencies import require_project_access, require_run_uuid_access
from common import MCPHttpClient
from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
from cortex.tool_contracts import fetch_all_tool_schemas, validate_against_schema, invalidate_schema_cache
from atlas import format_results
from atlas.config import (
    CONSORTIUM_REGISTRY,
    get_consortium_entry, get_all_fallback_patterns, get_all_tool_aliases,
    get_all_param_aliases,
)
from cortex.config import SERVICE_REGISTRY, get_service_url
from cortex.encode_helpers import (
    _ENCODE_ASSAY_ALIASES, _looks_like_assay,
    _correct_tool_routing, _validate_encode_params,
    _find_experiment_for_file, _extract_encode_search_term,
)
from cortex.llm_validators import (
    get_block_payload, _parse_tag_params,
    _validate_llm_output, _auto_detect_skill_switch,
)
from cortex.analysis_helpers import (
    _build_auto_analysis_context, _build_static_analysis_summary,
)
from cortex.path_helpers import (
    _pick_file_tool, _resolve_workflow_path, _resolve_file_path,
)
from cortex.conversation_state import (
    _build_conversation_state, _extract_job_context_from_history,
)
from cortex.context_injection import _inject_job_context, inject_memory_context
from cortex.memory_commands import parse_memory_command, detect_memory_intent, execute_memory_command, execute_memory_intent
from cortex.memory_service import get_memory_context as _get_memory_context
from cortex.data_call_generator import _auto_generate_data_calls, _validate_analyzer_params, _validate_edgepython_params
from cortex.tag_parser import (
    apply_response_corrections,
    parse_data_tags,
    parse_approval_tag,
    parse_plot_tags,
    override_hallucinated_df_refs,
    apply_plot_code_fallback,
    suppress_tags_for_plot_command,
    clean_tags_from_markdown,
    fix_hallucinated_accessions,
    user_wants_plot as _user_wants_plot_check,
)
from cortex.tool_dispatch import (
    build_calls_by_source,
    deduplicate_calls,
    validate_call_schemas,
    execute_tool_calls as _execute_tool_calls,
    auto_fetch_missing_encff,
    ChatCancelled,
)
from cortex.chat_dataframes import (
    extract_embedded_dataframes,
    extract_embedded_images,
    assign_df_ids,
    rebind_plots_to_new_df,
    post_df_assignment_plot_fallback,
    deduplicate_plot_specs,
    collapse_competing_specs,
)
from cortex.db_helpers import (
    _resolve_project_dir, _create_block_internal,
    save_conversation_message, track_project_access,
)
from cortex.routes.projects import (
    router as projects_router, _slugify, _dedup_slug,
)
from cortex.routes.conversations import router as conversations_router
from cortex.routes.files import (
    router as files_router, _active_downloads,
    _download_files_background, _update_download_block, _post_download_suggestions,
)
from cortex.routes.analyzer_proxy import (
    router as analyzer_proxy_router,
    _call_analyzer_tool, _call_launchpad_tool,
)
from cortex.routes.user_data import router as user_data_router
from cortex.routes.cross_project import router as cross_project_router
from cortex.routes.memories import router as memories_router
from cortex.task_service import sync_project_tasks, clear_project_tasks
from cortex.remote_orchestration import (
    _WORKFLOW_PLAN_TYPE,
    _LOCAL_SAMPLE_WORKFLOW,
    _REMOTE_SAMPLE_WORKFLOW,
    _launchpad_internal_headers,
    _launchpad_rest_base_url,
    _list_user_ssh_profiles,
    _resolve_ssh_profile_reference,
    _get_ssh_profile_auth_session,
    _ensure_gate_remote_profile_unlocked,
    _extract_remote_browse_request,
    _extract_remote_execution_request,
    _build_remote_stage_approval_context,
    _normalize_reference_id,
    _staged_sample_payload,
    _find_remote_staged_sample,
    _compute_local_input_fingerprint,
    _compute_reference_source_signature,
    _has_remote_stage_intent,
    _build_slurm_cache_preflight,
    _prepare_remote_execution_params,
    _hydrate_request_placeholders,
    _inject_launchpad_context_params,
    _make_stage_part,
    _stage_part_progress,
    _reference_stage_message,
    _initial_stage_parts,
    _final_stage_parts,
    _failed_stage_parts,
    _workflow_step_index,
    _workflow_next_step,
    _workflow_status,
    _persist_workflow_plan,
    _set_workflow_step_status,
    _resolve_workflow_step_id,
    _update_project_block_payload,
    _apply_slurm_cache_preflight_to_workflow,
    _find_workflow_plan,
    _local_sample_dest_dir,
    _should_stage_local_sample,
    _build_local_sample_workflow_payload,
    _build_remote_sample_workflow_payload,
    _ensure_remote_sample_workflow,
    _ensure_local_sample_workflow,
    _copy_local_sample_tree,
    _create_existing_stage_gate,
)
import cortex.job_parameters as job_parameters
import cortex.job_polling as job_polling
import cortex.workflow_submission as workflow_submission

# --- LOGGING ---
setup_logging("cortex")
logger = get_logger(__name__)

# --- APP ---
app = FastAPI()

# Add request logging middleware FIRST (outermost)
app.add_middleware(RequestLoggingMiddleware)

# Add auth middleware (before CORS)
app.add_middleware(AuthMiddleware)

# Add CORS middleware for Streamlit UI cross-origin requests
# Allow both localhost and environment-specified origins
allowed_origins = ["http://localhost:8501"]
if frontend_origin := os.getenv("FRONTEND_URL"):
    if frontend_origin not in allowed_origins:
        allowed_origins.append(frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(projects_router)
app.include_router(conversations_router)
app.include_router(files_router)
app.include_router(analyzer_proxy_router)
app.include_router(user_data_router)
app.include_router(cross_project_router)
app.include_router(memories_router)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    from common.database import is_sqlite
    if is_sqlite():
        init_db_sync()  # Auto-create tables for local SQLite dev
    # For Postgres, tables are managed by Alembic migrations
    # Fetch tool schemas from MCP servers (best-effort, non-blocking on failure)
    try:
        from cortex.config import SERVICE_REGISTRY
        from atlas.config import CONSORTIUM_REGISTRY
        await fetch_all_tool_schemas(SERVICE_REGISTRY, CONSORTIUM_REGISTRY)
    except Exception as e:
        logger.warning("Failed to fetch tool schemas at startup (will retry on first use)", error=str(e))

    # Recovery: re-spawn polling for any EXECUTION_JOB blocks still marked RUNNING.
    # These are orphaned tasks from a previous server instance that was restarted
    # before the job finished.  Without this, the block stays stuck in RUNNING
    # forever, blocking skill switches and suppressing auto-analysis.
    try:
        _recovery_session = SessionLocal()
        try:
            _orphaned = _recovery_session.query(ProjectBlock).filter(
                ProjectBlock.type == "EXECUTION_JOB",
                ProjectBlock.status == "RUNNING",
            ).all()
            for _blk in _orphaned:
                _pl = get_block_payload(_blk)
                _run_uuid = _pl.get("run_uuid", "")
                # Also check inner job_status — if it already says COMPLETED/FAILED
                # the block is just stale; mark it done without re-polling.
                _inner = _pl.get("job_status", {}).get("status", "")
                if not _run_uuid:
                    continue
                if _inner in ("COMPLETED", "FAILED"):
                    _blk.status = "DONE" if _inner == "COMPLETED" else "FAILED"
                    _recovery_session.commit()
                    logger.info("Startup recovery: marked stale RUNNING block as done",
                                 run_uuid=_run_uuid, inner_status=_inner)
                    # Trigger auto-analysis that was missed when polling died
                    if _inner == "COMPLETED":
                        asyncio.create_task(
                            job_polling._auto_trigger_analysis(
                                _blk.project_id, _run_uuid,
                                _pl, _blk.owner_id,
                            )
                        )
                        logger.info("Startup recovery: triggered auto-analysis",
                                     run_uuid=_run_uuid)
                else:
                    asyncio.create_task(
                        job_polling.poll_job_status(_blk.project_id, _blk.id, _run_uuid)
                    )
                    logger.info("Startup recovery: resumed polling for orphaned job",
                                 run_uuid=_run_uuid, block_id=_blk.id)
        finally:
            _recovery_session.close()
    except Exception as _rec_err:
        logger.warning("Startup recovery failed", error=str(_rec_err))

# --- SCHEMAS ---
class ChatRequest(BaseModel):
    project_id: str
    message: str
    skill: str = "welcome" # Default skill
    model: str = "default"         # Default model (devstral-2)
    request_id: str = ""           # Optional: for progress tracking via /chat/status

# --- CHAT HELPERS (extracted to cortex/chat_sync_handler.py) ---
import time as _time
from cortex.chat_sync_handler import (  # noqa: F401 — re-exported for backward compat
    _chat_progress, _detect_prompt_request, _format_prompt_report,
    _detect_df_command, _collect_df_map, _render_list_dfs, _render_head_df,
    _create_prompt_response, _emit_progress, _extract_plot_style_params,
    _is_cancelled, _COMMON_PLOT_COLOR_NAMES,
)

# --- ENDPOINTS ---

@app.get("/health")
async def health_check():
    """Health check endpoint (no auth required)"""
    return {"status": "ok", "service": "cortex", "version": AGOUTIC_VERSION}

@app.get("/skills")
async def get_available_skills():
    """Return the list of all available skills."""
    return {
        "skills": list(SKILLS_REGISTRY.keys()),
        "count": len(SKILLS_REGISTRY)
    }

@app.get("/jobs/{run_uuid}/status")
async def get_job_status_proxy(run_uuid: str, request: Request):
    """
    Proxy endpoint for Launchpad job status via direct REST call.
    Bypasses MCP for reliability — the UI calls this for live job progress.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)

    from cortex.config import INTERNAL_API_SECRET
    launchpad_rest = SERVICE_REGISTRY.get("launchpad", {}).get(
        "rest_url", os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
    )
    headers = {}
    if INTERNAL_API_SECRET:
        headers["X-Internal-Secret"] = INTERNAL_API_SECRET
    status_proxy_timeout = float(os.getenv("CORTEX_JOB_STATUS_PROXY_TIMEOUT_SECONDS", "60"))

    try:
        async with httpx.AsyncClient(timeout=status_proxy_timeout) as client:
            resp = await client.get(
                f"{launchpad_rest}/jobs/{run_uuid}/status",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Failed to proxy job status from Launchpad",
                       run_uuid=run_uuid, error=str(e))
        raise HTTPException(status_code=502, detail=f"Launchpad unreachable: {e}")


@app.post("/jobs/{run_uuid}/cancel")
async def cancel_job_proxy(run_uuid: str, request: Request):
    """
    Cancel a running Nextflow job.
    Proxies to Launchpad REST API (same pattern as /jobs/{run_uuid}/status).
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)

    from cortex.config import INTERNAL_API_SECRET
    launchpad_rest = SERVICE_REGISTRY.get("launchpad", {}).get(
        "rest_url", os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
    )
    headers = {}
    if INTERNAL_API_SECRET:
        headers["X-Internal-Secret"] = INTERNAL_API_SECRET

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{launchpad_rest}/jobs/{run_uuid}/cancel",
                headers=headers,
            )
        if resp.status_code == 400:
            raise HTTPException(status_code=400, detail=resp.json().get("detail", "Cannot cancel job"))
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        resp.raise_for_status()
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to proxy job cancel to Launchpad",
                       run_uuid=run_uuid, error=str(e))
        raise HTTPException(status_code=502, detail=f"Launchpad unreachable: {e}")

@app.delete("/jobs/{run_uuid}")
async def delete_job_proxy(run_uuid: str, request: Request):
    """
    Delete workflow folder for a terminal-state job.
    Proxies to Launchpad REST API.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)

    from cortex.config import INTERNAL_API_SECRET
    launchpad_rest = SERVICE_REGISTRY.get("launchpad", {}).get(
        "rest_url", os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
    )
    headers = {}
    if INTERNAL_API_SECRET:
        headers["X-Internal-Secret"] = INTERNAL_API_SECRET

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.delete(
                f"{launchpad_rest}/jobs/{run_uuid}",
                headers=headers,
            )
        if resp.status_code == 400:
            raise HTTPException(status_code=400, detail=resp.json().get("detail", "Cannot delete job"))
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        resp.raise_for_status()
        result = resp.json()

        # Update the EXECUTION_JOB block so the UI shows DELETED immediately
        try:
            session = SessionLocal()
            blocks = session.query(ProjectBlock).filter(
                ProjectBlock.block_type == "EXECUTION_JOB",
            ).all()
            for blk in blocks:
                p = get_block_payload(blk)
                if p.get("run_uuid") == run_uuid:
                    p["job_status"] = {
                        "status": "DELETED",
                        "progress_percent": 0,
                        "message": result.get("message", "Workflow folder deleted."),
                        "tasks": {},
                    }
                    blk.payload = json.dumps(p) if isinstance(p, dict) else p
                    blk.status = "DELETED"
                    session.commit()
                    break
            session.close()
        except Exception as _e:
            logger.warning("Failed to update block after delete", error=str(_e))

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to proxy job delete to Launchpad",
                       run_uuid=run_uuid, error=str(e))
        raise HTTPException(status_code=502, detail=f"Launchpad unreachable: {e}")


@app.post("/jobs/{run_uuid}/resubmit")
async def resubmit_job(run_uuid: str, request: Request):
    """
    Create a pre-populated APPROVAL_GATE block from a cancelled/failed job's parameters.
    The user can then edit and approve to resubmit with the same (or modified) settings.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)

    # Look up the original job params from dogme_jobs
    from launchpad.models import DogmeJob as LaunchpadDogmeJob

    session = SessionLocal()
    try:
        job = session.execute(
            select(LaunchpadDogmeJob).where(LaunchpadDogmeJob.run_uuid == run_uuid)
        ).scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in ("COMPLETED", "FAILED", "CANCELLED", "DELETED"):
            raise HTTPException(status_code=400, detail=f"Cannot resubmit a {job.status} job")

        # Reconstruct the original parameters
        _ref_genome = job.reference_genome
        # DB may store as JSON string like '["mm39"]' — normalize to a plain list
        if _ref_genome and _ref_genome.startswith("["):
            try:
                _ref_genome = json.loads(_ref_genome)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(_ref_genome, str):
            _ref_genome = [_ref_genome]

        extracted_params = {
            "sample_name": job.sample_name,
            "mode": job.mode,
            "input_directory": job.input_directory,
            "reference_genome": _ref_genome,
            "execution_mode": job.execution_mode or "local",
            "ssh_profile_id": job.ssh_profile_id,
            "slurm_account": job.slurm_account,
            "slurm_partition": job.slurm_partition,
            "slurm_cpus": job.slurm_cpus,
            "slurm_memory_gb": job.slurm_memory_gb,
            "slurm_walltime": job.slurm_walltime,
            "slurm_gpus": job.slurm_gpus,
            "slurm_gpu_type": job.slurm_gpu_type,
            "remote_base_path": str(Path(job.remote_work_dir).parent.parent) if job.remote_work_dir else None,
            "result_destination": job.result_destination,
        }
        if job.modifications:
            extracted_params["modifications"] = job.modifications
        # Include the old work directory so Nextflow can resume with -resume
        if job.nextflow_work_dir:
            extracted_params["resume_from_dir"] = job.nextflow_work_dir

        # Find the project this job belongs to
        project_id = job.project_id
        extracted_params = await _prepare_remote_execution_params(
            session,
            project_id=project_id,
            owner_id=user.id,
            params=extracted_params,
        )

        # Create approval gate block in the same project
        gate_block = _create_block_internal(
            session,
            project_id,
            "APPROVAL_GATE",
            {
                "label": f"Resubmit job (previously {job.status.lower()})? Review and edit parameters:",
                "extracted_params": extracted_params,
                "cache_preflight": extracted_params.get("cache_preflight") if isinstance(extracted_params, dict) else None,
                "gate_action": "job",
                "attempt_number": 1,
                "rejection_history": [],
                "skill": "analyze_local_sample",
                "model": "default",
                "resubmit_from": run_uuid,
            },
            status="PENDING",
            owner_id=user.id,
        )
        return {
            "status": "ok",
            "block_id": gate_block.id,
            "message": "Approval gate created. Edit parameters and approve to resubmit.",
        }
    finally:
        session.close()

@app.get("/jobs/{run_uuid}/debug")
async def get_job_debug_info(run_uuid: str, request: Request):
    """
    Proxy endpoint for Launchpad job debug info via MCP.
    This allows the UI to get debug info without calling Launchpad directly.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)
    return await _call_launchpad_tool("get_job_debug", run_uuid=run_uuid)

@app.post("/block", response_model=BlockOut)
async def create_block(block_in: BlockCreate, request: Request):
    # Get current user from middleware
    user = request.state.user
    require_project_access(block_in.project_id, user, min_role="editor")
    
    session = SessionLocal()
    try:
        new_block = _create_block_internal(
            session, 
            block_in.project_id, 
            block_in.type, 
            block_in.payload, 
            block_in.status,
            owner_id=user.id
        )
        return row_to_dict(new_block)
    finally:
        session.close()

@app.get("/blocks", response_model=BlockStreamOut)
async def get_blocks(project_id: str, request: Request, since_seq: int = 0, limit: int = 500):
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")
    session = SessionLocal()
    try:
        # Count total blocks for this project above since_seq
        total_query = select(func.count(ProjectBlock.id))\
            .where(ProjectBlock.project_id == project_id)\
            .where(ProjectBlock.seq > since_seq)
        total_count = session.execute(total_query).scalar() or 0

        if total_count <= limit:
            # Everything fits — simple ascending query
            query = select(ProjectBlock)\
                .where(ProjectBlock.project_id == project_id)\
                .where(ProjectBlock.seq > since_seq)\
                .order_by(ProjectBlock.seq.asc())\
                .limit(limit)
        else:
            # More blocks than the limit — fetch the NEWEST `limit` blocks
            # using a DESC sub-query, then re-sort ASC so callers get
            # chronological order.
            subq = select(ProjectBlock)\
                .where(ProjectBlock.project_id == project_id)\
                .where(ProjectBlock.seq > since_seq)\
                .order_by(ProjectBlock.seq.desc())\
                .limit(limit)\
                .subquery()
            query = select(ProjectBlock)\
                .join(subq, ProjectBlock.id == subq.c.id)\
                .order_by(ProjectBlock.seq.asc())
        
        result = session.execute(query)
        blocks = result.scalars().all()
        
        new_latest = since_seq
        if blocks:
            new_latest = blocks[-1].seq
            
        return {
            "blocks": [row_to_dict(b) for b in blocks],
            "latest_seq": new_latest
        }
    finally:
        session.close()

@app.patch("/block/{block_id}", response_model=BlockOut)
async def update_block(
    request: Request,
    block_id: str = FastAPIPath(..., min_length=1),
    body: BlockUpdate = ...
):
    user = request.state.user
    session = SessionLocal()
    try:
        result = session.execute(select(ProjectBlock).where(ProjectBlock.id == block_id))
        block = result.scalar_one_or_none()
        if not block:
            raise HTTPException(status_code=404, detail="Block not found")

        # Verify user has editor access to this block's project
        require_project_access(block.project_id, user, min_role="editor")
            
        old_status = block.status

        prospective_payload = get_block_payload(block)
        if isinstance(body.payload, dict):
            prospective_payload = {**prospective_payload, **body.payload}

        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and body.status == "APPROVED":
            gate_action = prospective_payload.get("gate_action") or "job"
            if gate_action != "download":
                await _ensure_gate_remote_profile_unlocked(user.id, prospective_payload)
        
        if body.status is not None:
            block.status = body.status
        if body.payload is not None:
            block.payload_json = json.dumps(body.payload)
            
        session.commit()
        session.refresh(block)
        sync_project_tasks(session, block.project_id)
        
        # If an APPROVAL_GATE was just approved, trigger job submission or download
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "APPROVED":
            gate_payload = get_block_payload(block)
            if gate_payload.get("gate_action") == "download":
                asyncio.create_task(download_after_approval(block.project_id, block_id))
            elif gate_payload.get("gate_action") == "reconcile_bams":
                asyncio.create_task(workflow_submission.submit_job_after_approval(block.project_id, block_id))
                if block.parent_id:
                    _plan_block = _find_workflow_plan(session, block.project_id,
                                                      workflow_block_id=block.parent_id)
                    if _plan_block:
                        _mark_workflow_plan_approval_complete(session, _plan_block, block.id)
                        _advance_workflow_plan_to_step_kind(
                            session,
                            _plan_block,
                            kind="RUN_SCRIPT",
                            approval_gate_id=block.id,
                        )
            else:
                asyncio.create_task(workflow_submission.submit_job_after_approval(block.project_id, block_id))
            # If this gate belongs to a plan, resume plan execution after the job
            if block.parent_id and gate_payload.get("gate_action") != "reconcile_bams":
                _plan_block = _find_workflow_plan(session, block.project_id,
                                                  workflow_block_id=block.parent_id)
                if _plan_block:
                    _mark_workflow_plan_approval_complete(session, _plan_block, block.id)
                    asyncio.create_task(
                        _auto_execute_plan_steps(
                            block.project_id, _plan_block.id,
                            user, gate_payload.get("model", "default"),
                        )
                    )
        
        # If an APPROVAL_GATE was rejected, trigger rejection handling
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "REJECTED":
            gate_payload = get_block_payload(block)
            if gate_payload.get("gate_action") == "local_sample_existing":
                asyncio.create_task(workflow_submission.submit_job_after_approval(block.project_id, block_id))
            else:
                asyncio.create_task(handle_rejection(block.project_id, block_id))
        
        return row_to_dict(block)
    finally:
        session.close()


@app.delete("/projects/{project_id}/blocks")
async def clear_project_blocks(project_id: str, request: Request):
    """
    Delete all blocks for a project, effectively clearing the chat history.
    Conversation records are preserved separately.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="owner")
    session = SessionLocal()
    try:
        result = session.execute(
            select(ProjectBlock).where(ProjectBlock.project_id == project_id)
        )
        blocks = result.scalars().all()
        count = len(blocks)
        for b in blocks:
            session.delete(b)
        session.commit()
        clear_project_tasks(session, project_id)
        logger.info("Cleared project blocks", project_id=project_id, count=count, user=user.email)
        return {"status": "ok", "deleted": count}
    finally:
        session.close()


# --- APPROVAL / DOWNLOAD BACKGROUND TASKS (extracted to cortex/chat_approval.py, cortex/chat_downloads.py) ---
from cortex.chat_approval import (  # noqa: F401 — re-exported for backward compat
    handle_rejection, _ensure_workflow_plan_approval_gate,
    _handle_workflow_plan_remote_profile_auth,
    _mark_workflow_plan_approval_complete,
    _advance_workflow_plan_to_step_kind,
    _build_reconcile_plan_approval_context,
)
from cortex.chat_downloads import (  # noqa: F401 — re-exported for backward compat
    download_after_approval, _auto_execute_plan_steps,
)


# --- CHAT PROGRESS STATUS ---
@app.get("/chat/status/{request_id}")
async def chat_status(request_id: str):
    """Return the current processing stage for a chat request (used by UI polling)."""
    entry = _chat_progress.get(request_id)
    if entry:
        return {"stage": entry["stage"], "detail": entry["detail"], "cancelled": entry.get("cancelled", False)}
    return {"stage": "waiting", "detail": "", "cancelled": False}


@app.post("/chat/cancel/{request_id}")
async def cancel_chat(request_id: str):
    """Cancel an in-progress chat request."""
    entry = _chat_progress.get(request_id)
    if entry:
        entry["cancelled"] = True
        return {"status": "cancelling", "request_id": request_id}
    # Even if we don't have a progress entry yet, create one with the cancelled flag
    _chat_progress[request_id] = {
        "stage": "cancelling",
        "detail": "",
        "ts": _time.time(),
        "cancelled": True,
    }
    return {"status": "cancelling", "request_id": request_id}


# --- CHAT & AGENT LOGIC ---
@app.post("/chat")
async def chat_with_agent(req: ChatRequest, request: Request):
    """
    1. Saves User Message -> DB
    2. Runs Agent Engine (LLM)
    3. Parses output for [[APPROVAL_NEEDED]] tag
    4. Saves Agent Plan -> DB (Clean text)
    5. If tag found -> Saves APPROVAL_GATE -> DB (Interactive buttons)
    """
    # Get current user from middleware
    user = request.state.user

    # Auto-register the project if it doesn't exist yet (handles UI fallback UUIDs
    # created when the POST /projects call timed out or failed).
    _ensure_session = SessionLocal()
    try:
        _proj = _ensure_session.execute(
            select(Project).where(Project.id == req.project_id)
        ).scalar_one_or_none()
        if not _proj:
            from cortex.user_jail import get_user_project_dir
            try:
                get_user_project_dir(user.id, req.project_id)
            except PermissionError:
                pass  # directory creation failed; DB rows still useful
            now = datetime.datetime.utcnow()
            _proj = Project(
                id=req.project_id,
                name=f"Project {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
                owner_id=user.id,
                is_public=False,
                is_archived=False,
                created_at=now,
                updated_at=now,
            )
            _ensure_session.add(_proj)
            _acc = ProjectAccess(
                id=str(uuid.uuid4()),
                user_id=user.id,
                project_id=req.project_id,
                project_name=_proj.name,
                role="owner",
                last_accessed=now,
            )
            _ensure_session.add(_acc)
            _ensure_session.commit()
            logger.info("Auto-registered project from chat", project_id=req.project_id, user=user.email)
    except Exception:
        _ensure_session.rollback()
    finally:
        _ensure_session.close()

    require_project_access(req.project_id, user, min_role="editor")

    # --- TOKEN LIMIT CHECK ---
    # Admins are exempt. For regular users, compare lifetime total against their
    # limit (if set) before spending any compute on the LLM call.
    if user.role != "admin" and user.token_limit is not None:
        _limit_session = SessionLocal()
        try:
            _used_row = _limit_session.execute(
                text("""
                    SELECT COALESCE(SUM(cm.total_tokens), 0)
                    FROM conversation_messages cm
                    JOIN conversations c ON cm.conversation_id = c.id
                    WHERE c.user_id = :uid
                      AND cm.role = 'assistant'
                      AND cm.total_tokens IS NOT NULL
                """),
                {"uid": user.id}
            ).fetchone()
            _tokens_used = _used_row[0] if _used_row else 0
        finally:
            _limit_session.close()

        if _tokens_used >= user.token_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "token_limit_exceeded",
                    "message": f"You have used {_tokens_used:,} of your {user.token_limit:,} token limit. "
                               "Please contact an admin to increase your quota.",
                    "tokens_used": _tokens_used,
                    "token_limit": user.token_limit,
                }
            )

    session = SessionLocal()
    try:
        # Check if there's an active skill in progress (no approval gate yet)
        # Look for the last AGENT_PLAN block to see what skill was being used
        last_agent_query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == req.project_id)\
            .where(ProjectBlock.type == "AGENT_PLAN")\
            .order_by(desc(ProjectBlock.seq))\
            .limit(1)
        
        last_agent_result = session.execute(last_agent_query)
        last_agent_block = last_agent_result.scalar_one_or_none()
        
        # Check if there's a pending approval gate or completed one
        approval_query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == req.project_id)\
            .where(ProjectBlock.type == "APPROVAL_GATE")\
            .order_by(desc(ProjectBlock.seq))\
            .limit(1)
        
        approval_result = session.execute(approval_query)
        approval_block = approval_result.scalar_one_or_none()
        
        # If the last agent block exists and there's no approval gate after it,
        # we're in the middle of a conversation - continue with the same skill
        active_skill = req.skill
        if last_agent_block:
            # Check if the most recent AGENT_PLAN is newer than the most recent
            # APPROVAL_GATE.  If so, the agent has spoken *after* the gate was
            # resolved (e.g. auto-analysis after job completion) and we should
            # continue with whatever skill that AGENT_PLAN set.
            agent_is_latest = (
                not approval_block
                or last_agent_block.seq > approval_block.seq
            )
            if agent_is_latest:
                last_skill = get_block_payload(last_agent_block).get("skill")
                if last_skill:
                    active_skill = last_skill
                    logger.info("Continuing with active skill", skill=active_skill)
            elif approval_block and approval_block.status in ["APPROVED", "REJECTED"]:
                # Approval gate is the most recent interaction — start fresh
                active_skill = req.skill
                logger.info("Starting fresh with skill", skill=active_skill)
        
        # Track project access
        await track_project_access(session, user.id, req.project_id, req.project_id)
        
        # 1. Save USER_MESSAGE
        user_block = _create_block_internal(
            session,
            req.project_id,
            "USER_MESSAGE",
            {"text": req.message},
            owner_id=user.id
        )
        
        # Check if user is asking for capabilities
        user_msg_lower = req.message.lower()
        if any(phrase in user_msg_lower for phrase in ["what can you do", "what are your capabilities", "help", "what can i do", "list features", "show capabilities"]):
            # Return the same welcome menu defined in skills/welcome/SKILL.md
            capabilities_text = """👋 Welcome to **Agoutic** — your autonomous bioinformatics agent for long-read sequencing data.

Here's what I can help you with:

1. **Analyze a new local dataset** — Run the Dogme pipeline on pod5, bam, or fastq files on your machine
2. **Download & analyze ENCODE data** — Search the ENCODE portal for long-read experiments, download files, and process them
3. **Download files from URLs** — Grab files from any URL into your project
4. **Check results from a completed job** — View QC reports, alignment stats, modification calls, and expression data
5. **Differential expression analysis** — Run DE analysis on count matrices using edgePython (bulk, single-cell, DTU, ChIP-seq)

What would you like to do?
"""
            agent_block = _create_block_internal(
                session,
                req.project_id,
                "AGENT_PLAN",
                {
                    "markdown": capabilities_text,
                    "skill": active_skill or req.skill or "welcome",
                    "model": req.model or "default"
                },
                status="DONE",
                owner_id=user.id
            )
            
            return {
                "status": "ok",
                "user_block": row_to_dict(user_block),
                "agent_block": row_to_dict(agent_block),
                "gate_block": None
            }

        prompt_request = _detect_prompt_request(req.message)
        if prompt_request:
            if prompt_request == "ambiguous":
                clarification = (
                    "I can show either the first-pass planning prompt or the second-pass analysis prompt. "
                    "Ask for \"first-pass system prompt\" or \"second-pass system prompt\"."
                )
                return await _create_prompt_response(
                    session,
                    req,
                    user_block,
                    user.id,
                    active_skill,
                    req.model or "default",
                    clarification,
                    prompt_type="ambiguous",
                )

            engine = AgentEngine(model_key=req.model)
            rendered_prompt = engine.render_system_prompt(
                skill_key=active_skill,
                prompt_type=prompt_request,
            )
            markdown = _format_prompt_report(
                prompt_request,
                active_skill,
                engine.model_name,
                rendered_prompt,
            )
            return await _create_prompt_response(
                session,
                req,
                user_block,
                user.id,
                active_skill,
                engine.model_name,
                markdown,
                prompt_type=prompt_request,
            )
        
        # Build conversation history for the LLM
        # Get all USER_MESSAGE and AGENT_PLAN blocks for this project
        history_query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == req.project_id)\
            .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))\
            .order_by(ProjectBlock.seq.asc())
        
        history_result = session.execute(history_query)
        history_blocks = history_result.scalars().all()

        # ── DF INSPECTION QUICK COMMANDS ───────────────────────────────
        # "list dfs" / "head DF3" bypass the LLM entirely and return
        # deterministic responses from the conversation's embedded dataframes.
        _df_cmd = _detect_df_command(user_msg_lower)
        if _df_cmd is not None:
            _df_map = _collect_df_map(history_blocks)
            if _df_cmd["action"] == "list":
                _md = _render_list_dfs(_df_map)
            else:  # head
                _target_id = _df_cmd.get("df_id")
                if _target_id is None:
                    # Default to latest (highest ID)
                    _target_id = max(_df_map.keys()) if _df_map else None
                _n_rows = _df_cmd.get("n", 10)
                _md = _render_head_df(_df_map, _target_id, _n_rows)
            return await _create_prompt_response(
                session, req, user_block, user.id,
                active_skill, req.model or "default", _md,
                prompt_type="df_inspection",
            )

        # ── MEMORY SLASH COMMANDS ──────────────────────────────────────
        # /remember, /forget, /memories, /pin, /annotate etc. bypass the LLM.
        _mem_cmd = parse_memory_command(req.message)
        if _mem_cmd is not None:
            _mem_response = execute_memory_command(session, _mem_cmd, user.id, req.project_id)
            return await _create_prompt_response(
                session, req, user_block, user.id,
                active_skill, req.model or "default", _mem_response,
                prompt_type="memory_command",
            )

        # ── NATURAL LANGUAGE MEMORY INTENT ─────────────────────────────
        # "remember that...", "sample X is AD", etc. — execute and respond.
        _mem_intent = detect_memory_intent(req.message)
        if _mem_intent is not None:
            _mem_ack = execute_memory_intent(session, _mem_intent, user.id, req.project_id)
            if _mem_ack:
                return await _create_prompt_response(
                    session, req, user_block, user.id,
                    active_skill, req.model or "default", _mem_ack,
                    prompt_type="memory_intent",
                )

        # Build conversation history in OpenAI format.
        # IMPORTANT: Strip <details>…</details> blocks from assistant messages.
        # These contain raw tool output (e.g. all 69 file rows) shown to the
        # user via a collapsible widget.  If we leave them in conversation
        # history, the LLM sees the FULL unfiltered data from a previous turn
        # and ignores the server-side filtered [PREVIOUS QUERY DATA:] injected
        # by _inject_job_context — causing wrong follow-up answers.
        _DETAILS_RE = re.compile(
            r'\s*---\s*\n\s*<details>.*?</details>', re.DOTALL
        )
        conversation_history = []
        for block in history_blocks[:-1]:  # Exclude the message we just added
            block_payload = get_block_payload(block)
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block_payload.get("text", "")
                })
            elif block.type == "AGENT_PLAN":
                _md = block_payload.get("markdown", "")
                # Remove raw data <details> blocks — the filtered data
                # will be injected by _inject_job_context when needed.
                _md = _DETAILS_RE.sub("", _md)
                # Fix A: skip bare find_file JSON echo blocks — if the entire
                # assistant turn is just a find_file result JSON (LLM echoed it
                # instead of acting on it), exclude it from history so the LLM
                # doesn't treat it as a "completed" action and loop.
                _md_stripped = re.sub(r'```(?:json)?|```', '', _md).strip()
                if '"primary_path"' in _md_stripped and ('"success": true' in _md_stripped or '"success":true' in _md_stripped):
                    try:
                        _probe = json.loads(_md_stripped)
                        if _probe.get("success") and _probe.get("primary_path"):
                            logger.debug("Skipping bare find_file echo block from history",
                                         primary_path=_probe["primary_path"])
                            continue  # don't add to conversation_history
                    except (json.JSONDecodeError, ValueError):
                        pass  # not clean JSON — keep it
                conversation_history.append({
                    "role": "assistant",
                    "content": _md
                })

        # Trim conversation history to last MAX_HISTORY_TURNS pairs to
        # prevent context overflow causing LLM timeouts on long conversations.
        MAX_HISTORY_TURNS = 20  # 20 user+assistant pairs = 40 messages
        if len(conversation_history) > MAX_HISTORY_TURNS * 2:
            conversation_history = conversation_history[-(MAX_HISTORY_TURNS * 2):]
        
        # 2. Run the Brain (in a thread so it doesn't block the server)
        # Initialize engine with the selected model (from UI request)
        engine = AgentEngine(model_key=req.model)
        _emit_progress(req.request_id, "thinking", f"Thinking using {engine.model_name}...")

        # Resolve the project directory path for file-browsing commands.
        # This is the minimum context needed for list_job_files, list_workflows, etc.
        _project_dir_path = _resolve_project_dir(session, user, req.project_id)
        _project_dir_str = str(_project_dir_path) if _project_dir_path else ""

        # 2a. Pre-LLM skill switch detection — catch obvious mismatches
        # before wasting time on an LLM call with the wrong skill.
        _remote_execution_request = _extract_remote_execution_request(req.message)
        if _remote_execution_request:
            auto_skill = "remote_execution"
        else:
            auto_skill = _auto_detect_skill_switch(req.message, active_skill)
        _pre_llm_skill = active_skill  # Track for debug
        if auto_skill and auto_skill in SKILLS_REGISTRY:
            logger.info("Auto-detected skill switch before LLM call",
                       from_skill=active_skill, to_skill=auto_skill)
            _emit_progress(req.request_id, "switching",
                          f"Switching to {auto_skill} skill...")
            active_skill = auto_skill

        # Inject job context (UUID, work_dir) for Dogme skills so the LLM
        # doesn't waste time searching conversation history for known info.
        # For ENCODE skills, inject full dataframe rows from the previous block.
        augmented_message, _injected_dfs, _inject_debug = _inject_job_context(
            req.message, active_skill, conversation_history,
            history_blocks=history_blocks
        )

        # Inject memory context (project + global memories)
        _mem_ctx = _get_memory_context(session, user.id, req.project_id)
        augmented_message = inject_memory_context(augmented_message, _mem_ctx)

        # Build structured conversation state and prepend as JSON
        _conv_state = _build_conversation_state(
            active_skill, conversation_history,
            history_blocks=history_blocks,
            project_id=req.project_id,
        )
        _state_json = _conv_state.to_json()
        if _state_json and _state_json != "{}":
            augmented_message = f"[STATE]\n{_state_json}\n[/STATE]\n\n{augmented_message}"

        # ── PLAN DETECTION ─────────────────────────────────────────────
        # Classify the request. If it's a multi-step plan, generate a
        # structured plan and return immediately (execution is async).
        # If it's a skill-defined chain (CHAIN_MULTI_STEP), show the plan
        # then fall through to the single-turn flow for execution.
        from cortex.planner import classify_request, generate_plan, render_plan_markdown

        _request_class = classify_request(req.message, active_skill, _conv_state)
        _active_chain = None  # will be set if CHAIN_MULTI_STEP

        if _request_class == "CHAIN_MULTI_STEP":
            # Skill-defined plan chain: show plan, then execute via single-turn flow
            from cortex.plan_chains import load_chains_for_skill, match_chain, render_chain_plan
            _chains = load_chains_for_skill(active_skill)
            _active_chain = match_chain(req.message, _chains) if _chains else None
            if _active_chain:
                _chain_plan_md = render_chain_plan(_active_chain, req.message)
                _emit_progress(req.request_id, "planning", _chain_plan_md)
                # Inject chain context into the augmented message so the LLM
                # knows it must execute ALL steps (data retrieval + plotting).
                _chain_hint = (
                    "\n\n[PLAN CHAIN: This is a multi-step request. "
                    "You must execute ALL of the following steps in order:\n"
                )
                for _cs in _active_chain.steps:
                    _chain_hint += f"  {_cs.order}. {_cs.kind}: {_cs.title}\n"
                if _active_chain.plot_hint:
                    _chain_hint += (
                        f"\nPLOT INSTRUCTION: {_active_chain.plot_hint}. "
                        "After the data is retrieved, you MUST emit a "
                        "[[PLOT:...]] tag to visualize the results. "
                        "If this plot depends on data fetched in this same response, "
                        "do NOT guess or reuse an older DF number. Omit df= from the "
                        "[[PLOT:...]] tag and the system will bind it to the newly "
                        "created dataframe after retrieval. Infer the chart type and "
                        "grouping column from the user's message.]\n"
                    )
                else:
                    _chain_hint += "]\n"
                augmented_message = augmented_message + _chain_hint
                logger.info("Chain plan injected", chain=_active_chain.name,
                            steps=len(_active_chain.steps))
            # Fall through to single-turn flow — do NOT return early

        if _request_class == "MULTI_STEP":
            _emit_progress(req.request_id, "planning", "Generating execution plan...")
            _plan_payload = await run_in_threadpool(
                generate_plan, req.message, active_skill, _conv_state,
                engine, conversation_history,
                project_dir=_project_dir_str,
            )
            if _plan_payload:
                _workflow_block = _create_block_internal(
                    session, req.project_id, _WORKFLOW_PLAN_TYPE,
                    _plan_payload, status="PENDING", owner_id=user.id,
                )
                sync_project_tasks(session, req.project_id)

                _plan_md = render_plan_markdown(_plan_payload)
                _plan_agent_block = _create_block_internal(
                    session, req.project_id, "AGENT_PLAN",
                    {
                        "markdown": _plan_md,
                        "skill": active_skill or req.skill or "welcome",
                        "model": engine.model_name,
                        "plan_block_id": _workflow_block.id,
                        "state": _conv_state.to_dict(),
                        "tokens": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "model": engine.model_name,
                        },
                    },
                    status="DONE", owner_id=user.id,
                )

                await save_conversation_message(
                    session, req.project_id, user.id, "assistant",
                    _plan_md,
                    token_data={"prompt_tokens": 0, "completion_tokens": 0,
                                "total_tokens": 0, "model": engine.model_name},
                    model_name=engine.model_name,
                )

                # Auto-execute safe initial steps in the background
                if _plan_payload.get("auto_execute_safe_steps", True):
                    asyncio.create_task(
                        _auto_execute_plan_steps(
                            req.project_id, _workflow_block.id,
                            user, engine.model_name,
                            request_id=req.request_id,
                        )
                    )

                _emit_progress(req.request_id, "done", "Plan generated")
                return {
                    "status": "ok",
                    "user_block": row_to_dict(user_block),
                    "agent_block": row_to_dict(_plan_agent_block),
                    "gate_block": None,
                    "plan_block": row_to_dict(_workflow_block),
                }
        # ── END PLAN DETECTION ─────────────────────────────────────────

        # Notify user when answering from a previously fetched dataframe
        if _injected_dfs:
            # Show the SOURCE DF IDs (from original dataframes), not the
            # injected copy IDs (which don't have IDs yet).
            _df_ref_match_notify = re.search(r'\bDF\s*(\d+)\b', req.message, re.IGNORECASE)
            if _df_ref_match_notify:
                _notify_ids = [f"DF{_df_ref_match_notify.group(1)}"]
            else:
                # Try to find source df_id from the injected metadata's
                # original context (we stored source_df_id for this purpose)
                _notify_ids = []
                for _d in _injected_dfs.values():
                    _src_id = _d.get("metadata", {}).get("source_df_id")
                    if _src_id:
                        _notify_ids.append(f"DF{_src_id}")
                if not _notify_ids:
                    _notify_ids = list(_injected_dfs.keys())[:2]
            _emit_progress(
                req.request_id, "context",
                f"Answering from previous data ({', '.join(_notify_ids)})..."
            )
        
        # 'think' talks to Ollama using the active skill and conversation history
        # Returns (content, usage_dict) — capture tokens for this turn
        _think_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if _is_cancelled(req.request_id):
            raise ChatCancelled("thinking", "Cancelled before the model started thinking.")
        # If startup schema fetch failed (MCP servers not ready yet), retry now
        # before the sync think() call which can't do async fetches itself.
        from cortex.tool_contracts import _CACHE_LOADED
        if not _CACHE_LOADED:
            try:
                import cortex.config as _cfg
                import atlas.config as _atlas_cfg
                await fetch_all_tool_schemas(_cfg.SERVICE_REGISTRY, _atlas_cfg.CONSORTIUM_REGISTRY)
            except Exception:
                pass  # Best-effort; format_tool_contract will return "" if still empty
        raw_response, _think_usage = await run_in_threadpool(
            engine.think, 
            augmented_message, 
            active_skill,
            conversation_history
        )

        # 2c. Validate LLM output against output contracts
        raw_response, _output_violations = _validate_llm_output(
            raw_response, active_skill, history_blocks=history_blocks
        )
        _pre_switch_skill = active_skill  # Track for debug
        
        # 3. Parse for Skill Switch Tag
        skill_switch_match = re.search(r'\[\[SKILL_SWITCH_TO:\s*(\w+)\]\]', raw_response)
        _remote_browse_request = _extract_remote_browse_request(req.message)
        _skill_switch_debug = {
            "tag_found": bool(skill_switch_match),
            "pre_switch_skill": _pre_switch_skill,
        }
        if skill_switch_match:
            new_skill = skill_switch_match.group(1)
            # Map common LLM-hallucinated skill names to real registry keys
            _SKILL_ALIASES = {
                "run_workflow": "analyze_local_sample",
                "submit_job": "analyze_local_sample",
                "run_dogme": "analyze_local_sample",
                "local_sample": "analyze_local_sample",
                "encode_search": "ENCODE_Search",
                "encode_longread": "ENCODE_LongRead",
                "job_results": "analyze_job_results",
            }
            new_skill = _SKILL_ALIASES.get(new_skill, new_skill)
            _skill_switch_debug["requested_skill"] = new_skill
            _skill_switch_debug["in_registry"] = new_skill in SKILLS_REGISTRY
            if (
                _remote_browse_request
                and new_skill == "analyze_job_results"
            ):
                logger.warning(
                    "Ignoring incorrect skill switch for remote browsing request",
                    from_skill=active_skill,
                    requested_skill=new_skill,
                    message=req.message,
                )
                _skill_switch_debug["ignored"] = True
                _skill_switch_debug["ignore_reason"] = "remote_browse_request"
            elif new_skill in SKILLS_REGISTRY:
                logger.info("Agent switching skill", from_skill=active_skill, to_skill=new_skill)
                _emit_progress(req.request_id, "switching", f"Switching to {new_skill} skill...")
                if _is_cancelled(req.request_id):
                    raise ChatCancelled("thinking", f"Cancelled before re-thinking with {new_skill} skill.")
                # Re-run with the new skill, injecting context for the new skill
                engine = AgentEngine(model_key=req.model)
                augmented_message, _injected_dfs, _inject_debug = _inject_job_context(
                    req.message, new_skill, conversation_history,
                    history_blocks=history_blocks
                )
                raw_response, _think_usage = await run_in_threadpool(
                    engine.think, 
                    augmented_message, 
                    new_skill,
                    conversation_history
                )
                # Validate the re-run output too
                raw_response, _switch_violations = _validate_llm_output(
                    raw_response, new_skill, history_blocks=history_blocks
                )
                _output_violations.extend(_switch_violations)
                active_skill = new_skill  # Update the skill for the response
                _skill_switch_debug["switched"] = True
                _skill_switch_debug["new_response_preview"] = raw_response[:200]
        _skill_switch_debug["post_switch_skill"] = active_skill
        _inject_debug["skill_switch"] = _skill_switch_debug

        
        # 4. Parse approval tag, apply tag corrections, parse DATA_CALL tags
        needs_approval, raw_response = parse_approval_tag(raw_response, active_skill)
        corrected_response, fallback_fixes_applied = apply_response_corrections(raw_response)
        data_call_matches, legacy_encode_matches, legacy_analysis_matches = parse_data_tags(corrected_response)

        # 5a. Parse PLOT tags and fix hallucinated DF references
        plot_tag_pattern = r'\[\[PLOT:\s*(.*?)\]\]'
        plot_specs = parse_plot_tags(corrected_response)
        _user_explicit_df = re.search(r'\bDF\s*(\d+)\b', req.message, re.IGNORECASE)
        override_hallucinated_df_refs(plot_specs, req.message, _conv_state.latest_dataframe)

        # 5a-b. Plot code fallback and overrides
        _user_wants_plot = _user_wants_plot_check(req.message)
        plot_specs, corrected_response = apply_plot_code_fallback(
            plot_specs, req.message, corrected_response,
            _injected_dfs, _conv_state.latest_dataframe,
            _extract_plot_style_params,
        )
        data_call_matches, legacy_encode_matches, legacy_analysis_matches, needs_approval, corrected_response = \
            suppress_tags_for_plot_command(
                req.message, plot_specs,
                data_call_matches, legacy_encode_matches, legacy_analysis_matches,
                needs_approval, corrected_response,
            )
        clean_markdown = clean_tags_from_markdown(corrected_response, plot_specs)
        clean_markdown = fix_hallucinated_accessions(clean_markdown, req.message)


        #     detect patterns in the user message and auto-generate appropriate calls.
        #     ALSO validates accessions when the user uses referential words ("them",
        #     "each of them", etc.) to catch LLM-hallucinated accession numbers.
        #     SKIP auto-generation if we injected previous query data (the LLM was
        #     told to answer from existing data, so no tags is correct behaviour).
        has_any_tags = bool(data_call_matches or legacy_encode_matches or legacy_analysis_matches)
        auto_calls = []
        _injected_previous_data = "[PREVIOUS QUERY DATA:]" in augmented_message
        # If the injected data was row-capped (large dataset), we still want to
        # allow auto-generation for assay-filter follow-ups (e.g. K562 → long read RNA-seq).
        # Detect this by checking if the injected data contained a truncation note.
        _injected_was_capped = _injected_previous_data and "total rows)*" in augmented_message

        # When we actually injected data into context (not capped), suppress
        # ALL DATA_CALL / legacy ENCODE_CALL tags the LLM may have emitted.
        # The injected context already contains the answer — any API call is
        # redundant and often wrong (e.g. the LLM passes "DF1" as a literal
        # search term).  Only suppress when _injected_dfs is non-empty, meaning
        # we found a matching DF.  If the user referenced a DF that doesn't
        # exist (e.g. DF55), _injected_dfs will be empty and we should let the
        # DATA_CALL proceed normally.
        if _injected_dfs and not _injected_was_capped:
            _suppressed = []
            if data_call_matches:
                _suppressed.extend(m.group(3) for m in data_call_matches)
                data_call_matches = []
            if legacy_encode_matches:
                _suppressed.extend(f"legacy:{m.group(1)}" for m in legacy_encode_matches)
                legacy_encode_matches = []
            if _suppressed:
                logger.info(
                    "Suppressed ALL DATA_CALL tags (injected data already present)",
                    suppressed_tools=_suppressed,
                )
                has_any_tags = bool(data_call_matches or legacy_encode_matches or legacy_analysis_matches)
                _inject_debug["suppressed_calls"] = _suppressed
            _inject_debug["injected_was_capped"] = _injected_was_capped

        # Check for conversational references in the user's message
        _ref_words = ["them", "these", "those", "each", "all of them",
                      "each of them", "for those", "the experiments",
                      "the accessions", "same"]
        _msg_lower = req.message.lower()
        _has_referential = any(w in _msg_lower for w in _ref_words)

        # --- User-data listing override ---
        # "list my data", "list my files", "show my data", etc. query the
        # user's central data folder, not job outputs.  Detect this BEFORE
        # the browsing-command override to avoid mis-routing to analyzer.
        _is_user_data_override = False
        _user_data_patterns = [
            r'\b(?:list|show|what)\s+(?:my\s+)?(?:data|data\s+files?)\b',
            r'\b(?:list|show)\s+my\s+files?\b',
            r'\bwhat\s+(?:data\s+)?files?\s+do\s+I\s+have\b',
        ]
        if any(re.search(p, _msg_lower) for p in _user_data_patterns):
            _emit_progress(req.request_id, "tools", "Listing your data files...")
            _ud_session = SessionLocal()
            _ud_file_count = 0
            try:
                _ud_rows = _ud_session.execute(
                    select(UserFile)
                    .where(UserFile.user_id == user.id)
                    .order_by(UserFile.filename)
                ).scalars().all()
                if _ud_rows:
                    _ud_file_count = len(_ud_rows)
                    _ud_lines = [f"📂 **Your Data Files** ({_ud_file_count} file{'s' if _ud_file_count != 1 else ''})\n"]
                    _ud_lines.append("| File | Size | Source | Accession | Sample | Added |")
                    _ud_lines.append("|------|------|--------|-----------|--------|-------| ")
                    for _uf in _ud_rows:
                        _sz = _uf.size_bytes or 0
                        if _sz >= 1024 * 1024 * 1024:
                            _sz_str = f"{_sz / (1024**3):.2f} GB"
                        elif _sz >= 1024 * 1024:
                            _sz_str = f"{_sz / (1024**2):.1f} MB"
                        elif _sz > 0:
                            _sz_str = f"{_sz / 1024:.0f} KB"
                        else:
                            _sz_str = "—"
                        _src = _uf.source or "—"
                        _acc = _uf.encode_accession or "—"
                        _samp = _uf.sample_name or "—"
                        _date = str(_uf.created_at)[:10] if _uf.created_at else "—"
                        _ud_lines.append(
                            f"| `{_uf.filename}` | {_sz_str} | {_src} | {_acc} | {_samp} | {_date} |"
                        )
                    clean_markdown = "\n".join(_ud_lines)
                else:
                    # DB has no records — fall back to scanning disk
                    _disk_files = []
                    if hasattr(user, "username") and user.username:
                        try:
                            from cortex.user_jail import get_user_data_dir
                            _data_dir = get_user_data_dir(user.username)
                            if _data_dir.is_dir():
                                _disk_files = sorted([
                                    f for f in _data_dir.iterdir()
                                    if f.is_file() and not f.name.startswith(".")
                                ])
                        except Exception:
                            pass
                    if _disk_files:
                        _ud_file_count = len(_disk_files)
                        _ud_lines = [f"📂 **Your Data Files** ({_ud_file_count} file{'s' if _ud_file_count != 1 else ''})\n"]
                        _ud_lines.append("| File | Size |")
                        _ud_lines.append("|------|------|")
                        for _df in _disk_files:
                            _sz = _df.stat().st_size
                            if _sz >= 1024 * 1024 * 1024:
                                _sz_str = f"{_sz / (1024**3):.2f} GB"
                            elif _sz >= 1024 * 1024:
                                _sz_str = f"{_sz / (1024**2):.1f} MB"
                            elif _sz > 0:
                                _sz_str = f"{_sz / 1024:.0f} KB"
                            else:
                                _sz_str = "—"
                            _ud_lines.append(f"| `{_df.name}` | {_sz_str} |")
                        clean_markdown = "\n".join(_ud_lines)
                    else:
                        clean_markdown = (
                            "📂 **Your Data Files**\n\n"
                            "No data files found yet. You can download files from ENCODE "
                            "or upload your own to get started."
                        )
            finally:
                _ud_session.close()
            _is_user_data_override = True
            # Suppress LLM-generated tags — we have the answer
            data_call_matches = []
            legacy_encode_matches = []
            legacy_analysis_matches = []
            has_any_tags = False
            needs_approval = False
            plot_specs = []
            _injected_dfs = {}
            _injected_previous_data = False
            auto_calls = []
            logger.info("User-data listing override: showing central data files",
                       file_count=_ud_file_count)

        # --- Browsing-command override ---
        # "list workflows", "list files", etc. must always route to analyzer,
        # regardless of the active skill.  When the skill is ENCODE_Search the
        # LLM may generate an ENCODE tag instead of a analyzer tag.  Detect
        # this and force the correct auto-generated call.
        _is_browsing_override = False
        _browsing_patterns = [
            r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
            r'\b(?:list|show)\s+(?:the\s+)?(?:project\s+)?files?\b',
        ]
        if not _is_user_data_override and any(re.search(p, _msg_lower) for p in _browsing_patterns):
            _browse_calls = _auto_generate_data_calls(
                req.message, active_skill,
                conversation_history, history_blocks=history_blocks,
                project_dir=_project_dir_str,
            )
            if _browse_calls:
                # Only keep analyzer calls from the override
                _browse_calls = [c for c in _browse_calls if c.get("source_key") == "analyzer"]
            if _browse_calls:
                auto_calls = _browse_calls
                _is_browsing_override = True
                # Suppress any LLM-generated tags (they're wrong for browsing)
                data_call_matches = []
                legacy_encode_matches = []
                legacy_analysis_matches = []
                has_any_tags = False
                clean_markdown = ""
                needs_approval = False  # browsing is not a job submission
                plot_specs = []         # no plots for file listings
                # Clear injected ENCODE DFs so they're not rendered alongside
                # the file listing
                _injected_dfs = {}
                _injected_previous_data = False
                logger.warning(
                    "Browsing-command override: replacing LLM tags with analyzer calls",
                    skill=active_skill, calls=len(auto_calls),
                )

        _is_remote_browsing_override = False
        _remote_browse_request = _extract_remote_browse_request(req.message)
        if _remote_browse_request:
            try:
                _ssh_profile_id, _ssh_profile_nickname = await _resolve_ssh_profile_reference(
                    user.id,
                    None,
                    _remote_browse_request.get("ssh_profile_nickname"),
                )
            except Exception as _remote_browse_err:
                logger.warning(
                    "Remote browsing override could not resolve SSH profile",
                    message=req.message,
                    error=str(_remote_browse_err),
                )
            else:
                active_skill = "remote_execution"
                _remote_params = {
                    "user_id": user.id,
                    "ssh_profile_id": _ssh_profile_id,
                }
                if _remote_browse_request.get("path"):
                    _remote_params["path"] = _remote_browse_request["path"]
                auto_calls = [{
                    "source_type": "service",
                    "source_key": "launchpad",
                    "tool": "list_remote_files",
                    "params": _remote_params,
                }]
                _is_remote_browsing_override = True
                data_call_matches = []
                legacy_encode_matches = []
                legacy_analysis_matches = []
                has_any_tags = False
                clean_markdown = ""
                needs_approval = False
                plot_specs = []
                _injected_dfs = {}
                _injected_previous_data = False
                logger.warning(
                    "Remote browsing override: replacing LLM response with Launchpad browse call",
                    skill=active_skill,
                    profile=_ssh_profile_nickname,
                )

        # --- Sync-results override ---
        # "sync workflow5 locally" etc. must route to launchpad sync, never
        # to the dogme pipeline planner.  Must fire before the has_any_tags
        # gate because the LLM may generate dogme run tags for sync requests.
        _is_sync_override = False
        if not _is_user_data_override and not _is_browsing_override and not _is_remote_browsing_override:
            _sync_override_calls = _auto_generate_data_calls(
                req.message, active_skill, conversation_history,
                history_blocks=history_blocks,
                project_dir=_project_dir_str,
            )
            if _sync_override_calls and any(
                c.get("tool") == "sync_job_results" for c in _sync_override_calls
            ):
                auto_calls = _sync_override_calls
                _is_sync_override = True
                data_call_matches = []
                legacy_encode_matches = []
                legacy_analysis_matches = []
                has_any_tags = False
                clean_markdown = ""
                needs_approval = False
                plot_specs = []
                _injected_dfs = {}
                _injected_previous_data = False
                logger.warning(
                    "Sync-results override: replacing LLM tags with sync call",
                    calls=len(auto_calls),
                )

        if not _is_user_data_override and not _is_browsing_override and not _is_remote_browsing_override and not _is_sync_override and not has_any_tags and (not _injected_previous_data or _injected_was_capped):
            # Case 1: LLM produced no tags at all — auto-generate
            auto_calls = _auto_generate_data_calls(req.message, active_skill, conversation_history,
                                                     history_blocks=history_blocks,
                                                     project_dir=_project_dir_str)
            if auto_calls:
                logger.warning("LLM failed to generate DATA_CALL tags, auto-generating",
                              count=len(auto_calls), skill=active_skill)
                # Replace the LLM's unhelpful response with a brief note
                clean_markdown = ""
            elif active_skill in ("ENCODE_Search", "ENCODE_LongRead"):
                # Hallucination guard: the LLM may have confidently stated a
                # count or claimed results exist, but NO tool was actually
                # called.  Detect this and strip the hallucinated response.
                _halluc_patterns = [
                    r'there are [\d,]+ .* experiments',
                    r'found [\d,]+ .* results',
                    r'interactive table below',
                    r'experiment list .* below',
                    r'[\d,]+ experiments in ENCODE',
                ]
                _is_hallucinated = any(
                    re.search(p, clean_markdown, re.IGNORECASE)
                    for p in _halluc_patterns
                )
                if _is_hallucinated:
                    logger.warning(
                        "LLM hallucinated ENCODE results without tool execution, "
                        "stripping response",
                        skill=active_skill,
                        response_preview=clean_markdown[:200],
                    )
                    clean_markdown = (
                        "I wasn't able to find an ENCODE query tool for that "
                        "search term. Could you clarify — is this a **cell line**, "
                        "**tissue**, **target protein**, or **assay type**? "
                        "That will help me pick the right query."
                    )
        elif has_any_tags and _has_referential:
            # Case 2: LLM produced tags BUT user used referential words.
            # Validate that the accessions in the tags actually appeared in
            # conversation history; if not, they are hallucinated.
            _llm_accessions = set()
            for _m in data_call_matches:
                _p = _parse_tag_params(_m.group(4))
                if _p.get("accession"):
                    _llm_accessions.add(_p["accession"].upper())
            for _m in legacy_encode_matches:
                _p = _parse_tag_params(_m.group(2))
                if _p.get("accession"):
                    _llm_accessions.add(_p["accession"].upper())

            _history_accessions = set()
            if conversation_history:
                for _msg in conversation_history:
                    _content = re.sub(r'<details>.*?</details>', '', _msg.get("content", ""), flags=re.DOTALL)
                    _found = re.findall(r'(ENCSR\d{3}[A-Z]{3})', _content, re.IGNORECASE)
                    _history_accessions.update(a.upper() for a in _found)

            if _llm_accessions and _history_accessions and not _llm_accessions & _history_accessions:
                # None of the LLM's accessions appear in history — hallucinated
                logger.warning(
                    "LLM hallucinated accessions in DATA_CALL tags, overriding",
                    llm_accessions=sorted(_llm_accessions),
                    history_accessions=sorted(_history_accessions),
                )
                auto_calls = _auto_generate_data_calls(req.message, active_skill, conversation_history,
                                                         history_blocks=history_blocks,
                                                         project_dir=_project_dir_str)
                if auto_calls:
                    # Discard the LLM-generated tags
                    data_call_matches = []
                    legacy_encode_matches = []
                    legacy_analysis_matches = []
                    has_any_tags = False
                    clean_markdown = ""

        # 6. Build tool calls from parsed tags and auto-generated calls
        calls_by_source = build_calls_by_source(
            data_call_matches=data_call_matches,
            legacy_encode_matches=legacy_encode_matches,
            legacy_analysis_matches=legacy_analysis_matches,
            auto_calls=auto_calls,
            has_any_tags=has_any_tags,
            user_id=user.id,
            project_id=req.project_id,
            user_message=req.message,
            conversation_history=conversation_history,
            history_blocks=history_blocks,
            project_dir=_project_dir_str,
            active_skill=active_skill,
        )

        # 6b. Deduplicate and validate
        calls_by_source = deduplicate_calls(calls_by_source)
        validate_call_schemas(calls_by_source)

        # 6c. Execute all tool calls via MCP
        _exec_result = await _execute_tool_calls(
            calls_by_source,
            user_id=user.id,
            username=getattr(user, 'username', None),
            project_id=req.project_id,
            project_dir_path=_project_dir_path,
            user_message=req.message,
            active_skill=active_skill,
            needs_approval=needs_approval,
            request_id=req.request_id,
            is_cancelled=_is_cancelled,
            emit_progress=_emit_progress,
        )
        all_results = _exec_result.all_results
        _pending_download_files = _exec_result.pending_download_files
        if _exec_result.active_skill != active_skill:
            active_skill = _exec_result.active_skill
        if _exec_result.needs_approval:
            needs_approval = True
        if _exec_result.clean_markdown is not None:
            clean_markdown = _exec_result.clean_markdown

        # Auto-fetch missing ENCFF files for multi-file downloads
        _autofetch_markdown = await auto_fetch_missing_encff(
            _pending_download_files, req.message if hasattr(req, 'message') else "",
        )
        if _autofetch_markdown is not None:
            clean_markdown = _autofetch_markdown

        # 6c. Build provenance records for all tool calls (audit trail)
        _provenance: list[dict] = []
        _now_ts = datetime.datetime.utcnow().isoformat() + "Z"
        for _prov_src, _prov_results in all_results.items():
            for _prov_r in _prov_results:
                _prov_entry = {
                    "source": _prov_src,
                    "tool": _prov_r.get("tool", "?"),
                    "params": {k: v for k, v in _prov_r.get("params", {}).items()
                               if k not in ("__routing_error__",)},
                    "timestamp": _now_ts,
                    "success": "data" in _prov_r,
                }
                if "data" in _prov_r:
                    _data = _prov_r["data"]
                    if isinstance(_data, dict):
                        _rows = (
                            _data.get("total")
                            or _data.get("count")
                            or len(_data.get("experiments", _data.get("files", _data.get("entries", []))))
                        )
                        if not _rows and (_data.get("found") is True or _data.get("ok") is True):
                            _rows = 1
                        _prov_entry["rows"] = _rows
                    elif isinstance(_data, list):
                        _prov_entry["rows"] = len(_data)
                if "error" in _prov_r:
                    _prov_entry["error"] = str(_prov_r["error"])[:200]
                _provenance.append(_prov_entry)

        # 6d. Format results and run second-pass LLM analysis
        _analyze_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # Fix B: Recovery guard — detect when the LLM echoed a find_file JSON
        # result verbatim instead of emitting a [[DATA_CALL:...]] tag.
        # This happens with weaker models (e.g. devstral-2) that have low
        # instruction-following fidelity. When detected:
        #   1. Extract primary_path and run_uuid from the echoed JSON
        #   2. Call the appropriate parse tool directly
        #   3. Inject the result into all_results so the normal second-pass
        #      analyze_results flow handles it as if a DATA_CALL had fired.
        if not all_results and '"primary_path"' in clean_markdown:
            _echo_stripped = re.sub(r'```(?:json)?|```', '', clean_markdown).strip()
            if '"success": true' in _echo_stripped or '"success":true' in _echo_stripped:
                try:
                    _echo_data = json.loads(_echo_stripped)
                    if (_echo_data.get("success")
                            and _echo_data.get("primary_path")
                            and _echo_data.get("run_uuid")):
                        _recovery_path = _echo_data["primary_path"]
                        _recovery_uuid = _echo_data["run_uuid"]
                        _recovery_tool = _pick_file_tool(_recovery_path)
                        logger.info(
                            "[CHAIN-RECOVERY] LLM echoed find_file JSON — auto-chaining",
                            tool=_recovery_tool,
                            file_path=_recovery_path,
                            run_uuid=_recovery_uuid,
                        )
                        _chain_params: dict = {
                            "run_uuid": _recovery_uuid,
                            "file_path": _recovery_path,
                        }
                        if _recovery_tool == "parse_csv_file":
                            _chain_params["max_rows"] = 100
                        elif _recovery_tool == "parse_bed_file":
                            _chain_params["max_records"] = 100
                        elif _recovery_tool == "read_file_content":
                            _chain_params["preview_lines"] = 50
                        try:
                            _recovery_url = get_service_url("analyzer")
                            _recovery_mcp = MCPHttpClient(name="analyzer", base_url=_recovery_url)
                            await _recovery_mcp.connect()
                            _recovery_result = await _recovery_mcp.call_tool(
                                _recovery_tool, **_chain_params
                            )
                            await _recovery_mcp.disconnect()
                            # Replace clean_markdown with a neutral placeholder so
                            # analyze_results writes the real summary, not the echo.
                            clean_markdown = ""
                            all_results["analyzer"] = [{
                                "tool": _recovery_tool,
                                "params": _chain_params,
                                "data": _recovery_result,
                            }]
                            logger.info("[CHAIN-RECOVERY] Parse succeeded",
                                        tool=_recovery_tool)
                        except Exception as _re:
                            logger.error("[CHAIN-RECOVERY] Parse failed",
                                         tool=_recovery_tool, error=str(_re))
                except (json.JSONDecodeError, ValueError):
                    pass  # not clean JSON — leave all_results empty, fall through normally

        remote_stage_approval_context = None
        _sync_run_uuid = ""  # populated by sync handler below (hoisted so always bound)

        if all_results:
            # Check if all results are errors (skip second pass if so)
            has_real_data = any(
                any("data" in r for r in results)
                for results in all_results.values()
            )

            # Format results into compact markdown for the LLM to analyze
            formatted_data_parts = []
            # Collect structured error blocks for the LLM
            _error_blocks = []
            for source_key, results in all_results.items():
                if results:
                    entry = SERVICE_REGISTRY.get(source_key)
                    results_markdown = format_results(source_key, results, registry_entry=entry)
                    formatted_data_parts.append(results_markdown)
                    # Build structured error blocks for any failed tool calls
                    for _r in results:
                        if "error" in _r:
                            _err_msg = _r["error"]
                            _err_type = "unknown"
                            if "not found" in _err_msg.lower() or "404" in _err_msg:
                                _err_type = "not_found"
                            elif "connection" in _err_msg.lower() or "timeout" in _err_msg.lower():
                                _err_type = "connection_failed"
                            elif "permission" in _err_msg.lower() or "403" in _err_msg:
                                _err_type = "permission_denied"
                            elif "routing" in _err_msg.lower():
                                _err_type = "routing_error"
                            _error_blocks.append(
                                f"[TOOL_ERROR: tool={_r.get('tool', '?')}, "
                                f"error_type={_err_type}, "
                                f"message=\"{_err_msg}\"]\n"
                                f"Follow the TOOL FAILURE RULES in your system prompt."
                            )

            formatted_data = "\n".join(formatted_data_parts)
            # Build provenance headers for traceability
            _prov_block = ""
            if _provenance:
                _prov_lines = []
                for _p in _provenance:
                    if _p.get("success"):
                        _prov_lines.append(
                            f"[TOOL_RESULT: source={_p['source']}, tool={_p['tool']}, "
                            f"params={{{', '.join(f'{k}={v}' for k, v in _p.get('params', {}).items())}}}, "
                            f"rows={_p.get('rows', '?')}, timestamp={_p['timestamp']}]"
                        )
                if _prov_lines:
                    _prov_block = "\n".join(_prov_lines)
                    # Prepend to formatted_data so the LLM sees provenance context
                    formatted_data = _prov_block + "\n\n" + formatted_data
            # Append structured errors so the LLM uses the error-handling playbook
            if _error_blocks and not has_real_data:
                formatted_data += "\n\n" + "\n".join(_error_blocks)
            logger.info("Formatted tool results", size=len(formatted_data),
                       has_real_data=has_real_data,
                       preview=formatted_data[:500])

            # --- Skip second-pass LLM for pure browsing commands ---
            # "list files", "list workflows" etc. should show the file table
            # directly — no LLM re-interpretation needed.
            _browsing_tools = {"list_job_files", "categorize_job_files", "list_remote_files"}
            _all_tools_used = set()
            _has_download_chain = False
            for _src_results in all_results.values():
                for _r in _src_results:
                    _all_tools_used.add(_r.get("tool", ""))
                    if _r.get("_chain") == "download":
                        _has_download_chain = True
            _is_browsing = bool(_all_tools_used) and _all_tools_used <= _browsing_tools
            _is_sync = "sync_job_results" in _all_tools_used

            # Also skip second-pass for download workflows — the metadata is
            # used to build the approval gate, not to summarise for the user.
            _is_download = _has_download_chain or active_skill == "download_files"

            remote_stage_approval_context = await _build_remote_stage_approval_context(
                session,
                project_id=req.project_id,
                owner_id=user.id,
                user_message=req.message,
                active_skill=active_skill,
                all_results=all_results,
                extract_params=job_parameters.extract_job_parameters_from_conversation,
            )

            if remote_stage_approval_context and has_real_data:
                needs_approval = True
                _display_data = "\n".join(formatted_data_parts)
                if _prov_block:
                    _display_data = _prov_block + "\n\n" + _display_data
                clean_markdown = str(remote_stage_approval_context.get("summary") or "").rstrip()
                if _display_data.strip():
                    clean_markdown += (
                        "\n\n<details><summary>📋 Raw Query Results (click to expand)</summary>\n\n"
                        + _display_data
                        + "\n\n</details>"
                    )

            elif _is_browsing and has_real_data and formatted_data.strip():
                # Show the formatted file listing directly — no second pass.
                # Use the raw results (without provenance prefix) and append
                # provenance as a collapsed section at the end.
                _display_data = "\n".join(formatted_data_parts)
                if _prov_block:
                    _display_data += (
                        "\n\n<details><summary>📋 Query Details</summary>\n\n"
                        + _prov_block
                        + "\n\n</details>"
                    )
                clean_markdown = _display_data

            elif _is_download and has_real_data:
                # For downloads, keep the Download Plan (already in clean_markdown)
                # and collapse the raw file metadata into a <details> section.
                _display_data = "\n".join(formatted_data_parts)
                if _prov_block:
                    _display_data = _prov_block + "\n\n" + _display_data
                _metadata_details = (
                    "\n\n<details><summary>📋 File Metadata Details</summary>\n\n"
                    + _display_data
                    + "\n\n</details>"
                )
                # Append metadata details after the Download Plan
                clean_markdown = clean_markdown.rstrip() + _metadata_details

            elif _is_browsing and not has_real_data:
                # Browsing command failed (e.g. directory not found).
                # Show the error plus helpful suggestions directly.
                _err_detail = ""
                for _src_results in all_results.values():
                    for _r in _src_results:
                        if "error" in _r:
                            _err_detail = _r.get("error", "")
                            break
                clean_markdown = (
                    f"⚠️ {_err_detail}\n\n"
                    "Try one of these:\n"
                    "- **list files** — files in the current workflow\n"
                    "- **list project files** — top-level project contents\n"
                    "- **list project files in data** — a subfolder under the project root\n"
                    "- **list files in workflow2/annot** — a specific workflow subfolder\n"
                    "- **list workflows** — all workflows in the project"
                )

            elif _is_sync:
                # Sync fires a background task — show a brief acknowledgement
                # and let the task dock handle progress display, like downloads.
                _sync_data = {}
                _sync_error = ""
                for _src_results in all_results.values():
                    for _r in _src_results:
                        if _r.get("tool") == "sync_job_results":
                            if "data" in _r:
                                _sync_data = _r["data"] if isinstance(_r["data"], dict) else {}
                            elif "error" in _r:
                                _sync_error = str(_r["error"])
                            break
                if _sync_error:
                    clean_markdown = f"⚠️ Sync failed: {_sync_error}"
                else:
                    _sync_status = _sync_data.get("status", "")
                    _sync_msg = _sync_data.get("message", "")
                    if _sync_status in ("sync_started", "sync_in_progress"):
                        clean_markdown = f"📥 {_sync_msg or 'Result synchronization started.'} You can track progress in the task dock below."
                        # Update the EXECUTION_JOB block payload so the UI's
                        # auto-refresh picks up the active transfer immediately.
                        _sync_run_uuid = _sync_data.get("run_uuid", "")
                        if _sync_run_uuid:
                            _job_block = (
                                session.query(ProjectBlock)
                                .filter(
                                    ProjectBlock.project_id == req.project_id,
                                    ProjectBlock.type == "EXECUTION_JOB",
                                    ProjectBlock.payload_json.contains(_sync_run_uuid),
                                )
                                .order_by(ProjectBlock.created_at.desc())
                                .first()
                            )
                            if _job_block:
                                _update_project_block_payload(
                                    session,
                                    _job_block.id,
                                    {"job_status": {**get_block_payload(_job_block).get("job_status", {}), "transfer_state": "downloading_outputs"}},
                                )
                    elif _sync_status == "already_synced":
                        clean_markdown = f"✅ {_sync_msg or 'Results are already synced locally.'}"
                    elif _sync_status == "not_applicable":
                        clean_markdown = f"ℹ️ {_sync_msg or 'Manual sync is not applicable for this job.'}"
                    else:
                        clean_markdown = _sync_msg or "Sync request submitted."

            elif has_real_data and formatted_data.strip():
                # Cap data size to avoid overwhelming the LLM context window.
                # For large list results, keep only the header block (tool name,
                # found-count, and the 📊 Summary table) and drop the individual
                # rows — the full table is already stored as an embedded dataframe
                # and will be shown interactively in the UI.
                MAX_DATA_CHARS = 12000
                if len(formatted_data) > MAX_DATA_CHARS:
                    logger.warning("Truncating tool results for LLM",
                                  original_size=len(formatted_data), max_size=MAX_DATA_CHARS)
                    # Try to keep everything up to and including the 📊 Summary block
                    _summary_end = formatted_data.find("\n**Total:")
                    if _summary_end != -1:
                        # Include the Total line itself
                        _nl = formatted_data.find("\n", _summary_end + 1)
                        _cut = _nl if _nl != -1 else _summary_end + 200
                        formatted_data = (
                            formatted_data[:_cut]
                            + "\n\n*(The full result set is shown as an interactive "
                            "dataframe above — only the summary is shown here.)*"
                        )
                    else:
                        formatted_data = (
                            formatted_data[:MAX_DATA_CHARS]
                            + "\n\n*(Results truncated — full data in interactive dataframe above.)*"
                        )

                # Second-pass: send data to LLM for analysis/filtering/summarization
                logger.info("Running second-pass LLM analysis", data_size=len(formatted_data))
                if _is_cancelled(req.request_id):
                    raise ChatCancelled("analyzing", "Tool calls completed but cancelled before generating the summary. You can re-send your message to see the results.")
                _emit_progress(req.request_id, "analyzing", "Analyzing results...")
                analyzed_response, _analyze_usage = await run_in_threadpool(
                    engine.analyze_results,
                    req.message,
                    clean_markdown,
                    formatted_data,
                    active_skill,
                    conversation_history,
                )

                # Append the formatted source data so user can verify
                clean_markdown = analyzed_response + "\n\n---\n\n" \
                    "<details><summary>📋 Raw Query Results (click to expand)</summary>\n\n" \
                    + formatted_data + "\n\n</details>"

                # ── POST-SECOND-PASS PLOT TAG PARSING ──────────────────
                # The second-pass LLM may have emitted [[PLOT:...]] tags
                # (now that it has PLOT instructions). Parse them and
                # merge into plot_specs so the UI renders charts.
                _post_plot_matches = list(re.finditer(
                    plot_tag_pattern, analyzed_response, re.DOTALL
                ))
                if _post_plot_matches:
                    for _ppm in _post_plot_matches:
                        _raw_inner2 = _ppm.group(1)
                        _pp2 = _parse_tag_params(_raw_inner2)
                        # Normalize df_id
                        _df_ref2 = _pp2.get("df", "")
                        _df_id_m2 = re.match(r'(?:DF)?\s*(\d+)', _df_ref2, re.IGNORECASE)
                        if _df_id_m2:
                            _pp2["df_id"] = int(_df_id_m2.group(1))
                        else:
                            _pp2["df_id"] = None
                        if "type" not in _pp2:
                            _pp2["type"] = "bar"
                        plot_specs.append(_pp2)
                    logger.info("Parsed PLOT tags from second-pass",
                                count=len(_post_plot_matches))
                    # Strip PLOT tags from the user-visible clean_markdown
                    clean_markdown = re.sub(
                        plot_tag_pattern, '', clean_markdown, flags=re.DOTALL
                    ).strip()
                # ── END POST-SECOND-PASS PLOT TAG PARSING ──────────────
            else:
                # All errors or empty — show errors directly, no second pass
                clean_markdown += formatted_data
        
        # 7. Extract DataFrames and images from tool results
        _embedded_dataframes = extract_embedded_dataframes(all_results, req.message)

        _embedded_images = extract_embedded_images(all_results)

        # 8. Save AGENT_PLAN (The Text)
        # Status is DONE because the text itself is just informational. 
        # The flow control happens in the gate block below.
        _emit_progress(req.request_id, "done", "Complete")
        # Assign DF IDs (merges injected DFs and assigns sequential IDs)
        _next_df_id = assign_df_ids(_embedded_dataframes, history_blocks, _injected_dfs)

        # Rebind plots to new DFs, update conv state, and deduplicate specs
        rebind_plots_to_new_df(plot_specs, _embedded_dataframes, req.message)

        # Update _conv_state with newly-embedded DFs
        if _embedded_dataframes:
            _max_new_id = 0
            _edf_label = ""
            _edf_rows = "?"
            for _edf_val in _embedded_dataframes.values():
                _edf_meta = _edf_val.get("metadata", {})
                _edf_id = _edf_meta.get("df_id")
                if isinstance(_edf_id, int) and _edf_id > _max_new_id:
                    _max_new_id = _edf_id
                    _edf_label = _edf_meta.get("label", "")
                    _edf_rows = _edf_meta.get("row_count", "?")
            if _max_new_id > 0:
                _new_latest = f"DF{_max_new_id}"
                _conv_state.latest_dataframe = _new_latest
                if not any(k.startswith(_new_latest + " ") or k == _new_latest
                           for k in _conv_state.known_dataframes):
                    _conv_state.known_dataframes.append(
                        f"{_new_latest} ({_edf_label}, {_edf_rows} rows)"
                    )

        post_df_assignment_plot_fallback(
            plot_specs, _embedded_dataframes, req.message, _extract_plot_style_params,
        )
        plot_specs = deduplicate_plot_specs(plot_specs)
        plot_specs = collapse_competing_specs(plot_specs, req.message, _extract_plot_style_params)

        # Consolidate token usage from both LLM passes (think + analyze_results)
        _total_usage = {
            "prompt_tokens": _think_usage["prompt_tokens"] + _analyze_usage["prompt_tokens"],
            "completion_tokens": _think_usage["completion_tokens"] + _analyze_usage["completion_tokens"],
            "total_tokens": _think_usage["total_tokens"] + _analyze_usage["total_tokens"],
            "model": engine.model_name,
        }

        _plan_payload = {
            "markdown": clean_markdown, 
            "skill": active_skill,
            "model": engine.model_name,
            "tokens": _total_usage,
            "state": _conv_state.to_dict(),
        }
        if _embedded_dataframes:
            _plan_payload["_dataframes"] = _embedded_dataframes
        if _embedded_images:
            _plan_payload["_images"] = _embedded_images
        if _provenance:
            _plan_payload["_provenance"] = _provenance
        if _sync_run_uuid:
            _plan_payload["_sync_run_uuid"] = _sync_run_uuid
        # Attach debug info for the UI debug panel
        _debug_payload = dict(_inject_debug)  # copy the injection debug info
        _debug_payload["has_injected_dfs"] = bool(_injected_dfs)
        _debug_payload["injected_previous_data"] = _injected_previous_data
        _debug_payload["auto_calls_count"] = len(auto_calls)
        if auto_calls:
            _debug_payload["auto_calls"] = [str(c) for c in auto_calls[:10]]
        _debug_payload["llm_tags_remaining"] = {
            "data_call": len(data_call_matches),
            "legacy_encode": len(legacy_encode_matches),
            "legacy_analysis": len(legacy_analysis_matches),
        }
        _debug_payload["referential_words_detected"] = _has_referential
        _debug_payload["embedded_df_count"] = len(_embedded_dataframes)
        _debug_payload["active_skill"] = active_skill
        _debug_payload["pre_llm_skill"] = _pre_llm_skill
        _debug_payload["auto_skill_detected"] = auto_skill
        _debug_payload["requested_skill"] = req.skill
        _debug_payload["llm_raw_response_preview"] = raw_response[:800] if raw_response else ""
        if _output_violations:
            _debug_payload["output_violations"] = _output_violations
        _plan_payload["_debug"] = _debug_payload
        agent_block = _create_block_internal(
            session,
            req.project_id,
            "AGENT_PLAN",
            _plan_payload,
            status="DONE",
            owner_id=user.id
        )
        
        # Save assistant's response to conversation history (with token counts)
        await save_conversation_message(
            session, req.project_id, user.id, "assistant", clean_markdown,
            token_data=_total_usage, model_name=engine.model_name
        )

        # 8b. Insert AGENT_PLOT blocks for any [[PLOT:...]] tags
        plot_blocks = []
        if plot_specs:
            # Validate plot specs against available dataframes.
            # Build a map of df_id → {columns, data, metadata} from all
            # history blocks AND the current block's embedded dataframes.
            _all_df_map = {}  # df_id → {columns, data, metadata, label}
            for _hblk in history_blocks:
                if _hblk.type == "AGENT_PLAN":
                    _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                    for _hfname, _hfdata in _hblk_dfs.items():
                        _hid = _hfdata.get("metadata", {}).get("df_id")
                        if _hid is not None:
                            _all_df_map[_hid] = {**_hfdata, "label": _hfname}
            # Also include DFs from the block we just created
            for _efname, _efdata in _embedded_dataframes.items():
                _eid = _efdata.get("metadata", {}).get("df_id")
                if _eid is not None:
                    _all_df_map[_eid] = {**_efdata, "label": _efname}

            # Group plot specs by df_id for multi-trace support.
            # Skip any spec where df_id is None — they can't be rendered.
            from collections import defaultdict
            _plots_by_df = defaultdict(list)
            for _ps in plot_specs:
                _df_id_val = _ps.get("df_id")
                if _df_id_val is None:
                    logger.warning("Skipping PLOT spec with no df_id", spec=_ps)
                    continue
                _plots_by_df[_df_id_val].append(_ps)

            for _df_key, _chart_group in _plots_by_df.items():
                _charts = []
                _df_info = _all_df_map.get(_df_key)
                for _cs in _chart_group:
                    _chart_entry = {
                        "type": _cs.get("type", "histogram"),
                        "df_id": _cs.get("df_id"),
                        "x": _cs.get("x"),
                        "y": _cs.get("y"),
                        "color": _cs.get("color"),
                        "palette": _cs.get("palette"),
                        "title": _cs.get("title"),
                        "agg": _cs.get("agg"),
                    }
                    # Remove None values for cleaner payload
                    _chart_entry = {k: v for k, v in _chart_entry.items() if v is not None}
                    _charts.append(_chart_entry)

                _plot_payload = {
                    "charts": _charts,
                    "skill": active_skill,
                    "model": engine.model_name,
                }
                _plot_block = _create_block_internal(
                    session,
                    req.project_id,
                    "AGENT_PLOT",
                    _plot_payload,
                    status="DONE",
                    owner_id=user.id
                )
                plot_blocks.append(_plot_block)
                logger.info("Created AGENT_PLOT block",
                           block_id=_plot_block.id,
                           chart_count=len(_charts),
                           df_key=_df_key)

        # 5. Insert APPROVAL_GATE (The Buttons) if requested
        gate_block = None
        if needs_approval:
            # Determine gate action based on active skill
            gate_action = "download" if active_skill == "download_files" else "job"
            gate_skill = active_skill

            if gate_action == "download" and _pending_download_files:
                # Download gate: show the files to be downloaded, not Dogme params
                _total_dl_bytes = sum(f.get("size_bytes") or 0 for f in _pending_download_files)
                from cortex.user_jail import get_user_data_dir
                _dl_target = str(get_user_data_dir(user.username))
                extracted_params = {
                    "gate_action": "download",
                    "files": _pending_download_files,
                    "total_size_bytes": _total_dl_bytes,
                    "target_dir": _dl_target,
                }
            else:
                # Job gate: extract Dogme pipeline parameters
                if remote_stage_approval_context and isinstance(remote_stage_approval_context.get("params"), dict):
                    extracted_params = dict(remote_stage_approval_context.get("params") or {})
                else:
                    extracted_params = await job_parameters.extract_job_parameters_from_conversation(session, req.project_id)
                if isinstance(extracted_params, dict):
                    gate_action = extracted_params.get("gate_action") or gate_action
                    if (
                        gate_action == "remote_stage"
                        or (extracted_params.get("remote_action") or "").strip().lower() == "stage_only"
                    ):
                        gate_action = "remote_stage"
                        gate_skill = "remote_execution"
                if (extracted_params.get("execution_mode") or "local") == "slurm":
                    gate_skill = "remote_execution"
            
            gate_block = _create_block_internal(
                session,
                req.project_id,
                "APPROVAL_GATE",
                {
                    "label": "Do you authorize the Agent to proceed with this plan?",
                    "extracted_params": extracted_params,
                    "cache_preflight": extracted_params.get("cache_preflight") if isinstance(extracted_params, dict) else None,
                    "gate_action": gate_action,
                    "attempt_number": 1,
                    "rejection_history": [],
                    "skill": gate_skill,
                    "model": engine.model_name
                },
                status="PENDING",
                owner_id=user.id
            )
        
        # Save conversation message
        await save_conversation_message(session, req.project_id, user.id, "user", req.message)
        
        return {
            "status": "ok", 
            "user_block": row_to_dict(user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": row_to_dict(gate_block) if gate_block else None,
            "plot_blocks": [row_to_dict(pb) for pb in plot_blocks] if plot_blocks else None
        }
        
    except ChatCancelled as cc:
        logger.info("Chat cancelled by user", stage=cc.stage, detail=cc.detail,
                     request_id=req.request_id)
        # Build stage-aware user message
        if cc.stage == "thinking":
            _cancel_msg = "⏹️ **Stopped** — cancelled while the model was thinking. No tools were called."
        elif cc.stage == "tools":
            _cancel_msg = f"⏹️ **Stopped** — {cc.detail} No partial results were saved."
        elif cc.stage == "analyzing":
            _cancel_msg = "⏹️ **Stopped** — tool calls completed but cancelled before generating the summary. You can re-send your message to see the results."
        else:
            _cancel_msg = "⏹️ **Stopped** by user."
        _emit_progress(req.request_id, "cancelled", _cancel_msg)
        # Save a minimal agent block so the conversation shows what happened
        agent_block = _create_block_internal(
            session,
            req.project_id,
            "AGENT_PLAN",
            {"markdown": _cancel_msg, "skill": active_skill, "model": "system", "cancelled": True},
            status="DONE",
            owner_id=user.id,
        )
        return {
            "status": "cancelled",
            "user_block": row_to_dict(user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": None,
            "plot_blocks": None,
        }
    except Exception as e:
        import traceback
        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        logger.error("Chat endpoint error", error=error_detail, exc_info=True)
        raise HTTPException(status_code=500, detail=error_detail)
    finally:
        session.close()


