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

        if block.type == "PENDING_ACTION" and old_status == "PENDING" and block.status == "APPROVED":
            execute_pending_dataframe_action(session, block)

        if block.type == "PENDING_ACTION" and old_status == "PENDING" and block.status == "REJECTED":
            reject_pending_dataframe_action(
                session,
                block,
                reason=(get_block_payload(block).get("rejection_reason") or "User dismissed saved dataframe action"),
            )
        
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
from cortex.pending_dataframe_actions import (  # noqa: E402
    execute_pending_dataframe_action,
    reject_pending_dataframe_action,
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
from cortex.chat_context import ChatContext  # noqa: E402
from cortex.chat_pipeline import run_chat_pipeline  # noqa: E402

@app.post("/chat")
async def chat_with_agent(req: ChatRequest, request: Request):
    """Dispatch a user chat message through the registered pipeline stages."""
    user = request.state.user
    ctx = ChatContext(
        project_id=req.project_id,
        message=req.message,
        skill=req.skill,
        model=req.model,
        request_id=req.request_id,
        user=user,
    )
    try:
        return await run_chat_pipeline(ctx)
    except ChatCancelled as cc:
        logger.info("Chat cancelled by user", stage=cc.stage, detail=cc.detail,
                     request_id=req.request_id)
        if cc.stage == "thinking":
            _cancel_msg = "⏹️ **Stopped** — cancelled while the model was thinking. No tools were called."
        elif cc.stage == "tools":
            _cancel_msg = f"⏹️ **Stopped** — {cc.detail} No partial results were saved."
        elif cc.stage == "analyzing":
            _cancel_msg = (
                "⏹️ **Stopped** — tool calls completed but cancelled before generating "
                "the summary. You can re-send your message to see the results."
            )
        else:
            _cancel_msg = "⏹️ **Stopped** by user."
        _emit_progress(req.request_id, "cancelled", _cancel_msg)
        session = ctx.session or SessionLocal()
        try:
            agent_block = _create_block_internal(
                session, req.project_id, "AGENT_PLAN",
                {"markdown": _cancel_msg, "skill": ctx.active_skill, "model": "system", "cancelled": True},
                status="DONE", owner_id=user.id,
            )
            return {
                "status": "cancelled",
                "user_block": row_to_dict(ctx.user_block) if ctx.user_block else None,
                "agent_block": row_to_dict(agent_block),
                "gate_block": None,
                "plot_blocks": None,
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        logger.error("Chat endpoint error", error=error_detail, exc_info=True)
        raise HTTPException(status_code=500, detail=error_detail)
    finally:
        if ctx.session:
            ctx.session.close()
        
