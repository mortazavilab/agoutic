import asyncio
import datetime
import uuid
import json
import os
import re
import httpx
from typing import Optional

from fastapi import FastAPI, HTTPException, Path as FastAPIPath, Request
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool  # To run LLM without blocking
from pydantic import BaseModel
from sqlalchemy import select, desc, func, text

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
from cortex.context_injection import _inject_job_context
from cortex.data_call_generator import _auto_generate_data_calls, _validate_analyzer_params
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

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db_sync()
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
                            _auto_trigger_analysis(
                                _blk.project_id, _run_uuid,
                                _pl, _blk.owner_id,
                            )
                        )
                        logger.info("Startup recovery: triggered auto-analysis",
                                     run_uuid=_run_uuid)
                else:
                    asyncio.create_task(
                        poll_job_status(_blk.project_id, _blk.id, _run_uuid)
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

# --- CHAT PROGRESS TRACKING ---
# Tracks processing stages for active chat requests so the UI can poll for updates.
# Entries are cleaned up after 5 minutes.
import time as _time
_chat_progress: dict[str, dict] = {}  # request_id -> {"stage": str, "detail": str, "ts": float}

class ChatCancelled(Exception):
    """Raised when user cancels an in-progress chat request."""
    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        self.detail = detail
        super().__init__(f"Chat cancelled at stage={stage}: {detail}")


def _detect_prompt_request(message: str) -> str | None:
    """Detect whether the user is asking to inspect the current system prompt."""
    msg = (message or "").lower()
    prompt_phrases = [
        "system prompt",
        "current prompt",
        "your prompt",
        "show prompt",
        "show me the prompt",
        "show me your prompt",
        "show me the system prompt",
        "what is your system prompt",
        "report your system prompt",
    ]
    if not any(phrase in msg for phrase in prompt_phrases):
        return None

    if any(phrase in msg for phrase in ["first pass", "first-pass", "planning prompt", "planning system prompt"]):
        return "first_pass"
    if any(phrase in msg for phrase in ["second pass", "second-pass", "analysis prompt", "summary prompt"]):
        return "second_pass"
    return "ambiguous"


def _format_prompt_report(prompt_type: str, skill_key: str, model_name: str, prompt_text: str) -> str:
    """Format a rendered prompt for chat display without losing its exact text."""
    label = "First-pass system prompt" if prompt_type == "first_pass" else "Second-pass system prompt"
    return (
        f"### {label}\n\n"
        f"- Skill: **{skill_key}**\n"
        f"- Model: **{model_name}**\n\n"
        "`````text\n"
        f"{prompt_text}\n"
        "`````"
    )


async def _create_prompt_response(
    session,
    req: ChatRequest,
    user_block,
    user_id: str,
    active_skill: str,
    model_name: str,
    markdown: str,
    prompt_type: str | None = None,
):
    """Create a deterministic AGENT_PLAN response for prompt inspection or clarification."""
    payload = {
        "markdown": markdown,
        "skill": active_skill or req.skill or "welcome",
        "model": model_name,
        "tokens": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "model": model_name,
        },
        "_debug": {
            "prompt_inspection": True,
            "prompt_type": prompt_type,
            "requested_skill": req.skill,
            "active_skill": active_skill,
        },
    }
    agent_block = _create_block_internal(
        session,
        req.project_id,
        "AGENT_PLAN",
        payload,
        status="DONE",
        owner_id=user_id,
    )
    await save_conversation_message(
        session,
        req.project_id,
        user_id,
        "assistant",
        markdown,
        token_data=payload["tokens"],
        model_name=model_name,
    )
    return {
        "status": "ok",
        "user_block": row_to_dict(user_block),
        "agent_block": row_to_dict(agent_block),
        "gate_block": None,
    }

def _emit_progress(request_id: str, stage: str, detail: str = ""):
    """Record a processing stage for a chat request."""
    if not request_id:
        return
    # Preserve the cancelled flag across progress updates
    _cancelled = _chat_progress.get(request_id, {}).get("cancelled", False)
    _chat_progress[request_id] = {
        "stage": stage,
        "detail": detail,
        "ts": _time.time(),
        "cancelled": _cancelled,
    }
    # Housekeeping: remove entries older than 5 minutes
    cutoff = _time.time() - 300
    stale = [k for k, v in _chat_progress.items() if v["ts"] < cutoff]
    for k in stale:
        del _chat_progress[k]

def _is_cancelled(request_id: str) -> bool:
    """Check whether the user has requested cancellation for this chat request."""
    if not request_id:
        return False
    return _chat_progress.get(request_id, {}).get("cancelled", False)

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

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
    from cortex.config import DATABASE_URL
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession

    sync_url = DATABASE_URL.replace("sqlite+aiosqlite://", "sqlite://")
    engine = create_engine(sync_url, connect_args={"check_same_thread": False})

    with SyncSession(engine) as db:
        job = db.execute(
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
        }
        if job.modifications:
            extracted_params["modifications"] = job.modifications
        # Include the old work directory so Nextflow can resume with -resume
        if job.nextflow_work_dir:
            extracted_params["resume_from_dir"] = job.nextflow_work_dir

        # Find the project this job belongs to
        project_id = job.project_id

    # Create approval gate block in the same project
    session = SessionLocal()
    try:
        gate_block = _create_block_internal(
            session,
            project_id,
            "APPROVAL_GATE",
            {
                "label": f"Resubmit job (previously {job.status.lower()})? Review and edit parameters:",
                "extracted_params": extracted_params,
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
        
        if body.status is not None:
            block.status = body.status
        if body.payload is not None:
            block.payload_json = json.dumps(body.payload)
            
        session.commit()
        session.refresh(block)
        
        # If an APPROVAL_GATE was just approved, trigger job submission or download
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "APPROVED":
            gate_payload = get_block_payload(block)
            if gate_payload.get("gate_action") == "download":
                asyncio.create_task(download_after_approval(block.project_id, block_id))
            else:
                asyncio.create_task(submit_job_after_approval(block.project_id, block_id))
        
        # If an APPROVAL_GATE was rejected, trigger rejection handling
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "REJECTED":
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
        logger.info("Cleared project blocks", project_id=project_id, count=count, user=user.email)
        return {"status": "ok", "deleted": count}
    finally:
        session.close()


async def extract_job_parameters_from_conversation(session, project_id: str) -> dict:
    """
    Extract job parameters from conversation history using heuristics.
    
    Scopes to the **most recent submission cycle** — only blocks after the
    last EXECUTION_JOB or APPROVAL_GATE (approved).  This prevents stale
    parameters from earlier jobs (e.g. a previous sample name) bleeding
    into a new submission.
    
    Analyzes USER_MESSAGE and AGENT_PLAN blocks to determine:
    - sample_name
    - mode (DNA/RNA/CDNA)
    - input_directory (path to pod5 files)
    - reference_genome (GRCh38, mm39, etc.)
    - modifications (optional)
    """
    # Fetch all blocks for this project
    query = select(ProjectBlock)\
        .where(ProjectBlock.project_id == project_id)\
        .order_by(ProjectBlock.seq.asc())
    
    result = session.execute(query)
    blocks = result.scalars().all()
    
    # --- Scope to the most recent submission cycle ---
    # Find the last EXECUTION_JOB or approved APPROVAL_GATE block.
    # Only consider blocks AFTER that point for parameter extraction.
    last_boundary_idx = -1
    for i, block in enumerate(blocks):
        if block.type == "EXECUTION_JOB":
            last_boundary_idx = i
        elif block.type == "APPROVAL_GATE" and block.status == "APPROVED":
            last_boundary_idx = i
    
    recent_blocks = blocks[last_boundary_idx + 1:] if last_boundary_idx >= 0 else blocks
    
    # Build conversation context from recent blocks only
    conversation = []
    user_messages = []
    for block in recent_blocks:
        if block.type == "USER_MESSAGE":
            text = get_block_payload(block).get('text', '')
            conversation.append(f"User: {text}")
            user_messages.append(text)
        elif block.type == "AGENT_PLAN":
            conversation.append(f"Agent: {get_block_payload(block).get('markdown', '')}")
    
    if not conversation:
        return None
    
    conversation_text = "\n".join(conversation)
    all_user_text_original = " ".join(user_messages)  # Keep original case for path extraction
    all_user_text = all_user_text_original.lower()  # Lowercase for keyword matching
    
    # First, use simple heuristics for quick detection
    params = {
        "sample_name": None,
        "mode": None,
        "input_directory": None,
        "input_type": "pod5",  # Default to pod5
        "entry_point": None,  # Dogme entry point
        "reference_genome": [],  # Now a list for multi-genome support
        "modifications": None,
        # Advanced parameters (optional)
        "modkit_filter_threshold": None,  # Will use default 0.9 if not specified
        "min_cov": None,  # Will default based on mode if not specified
        "per_mod": None,  # Will use default 5 if not specified
        "accuracy": None,  # Will use default "sup" if not specified
        "max_gpu_tasks": None,  # Will use default 1 if not specified
    }
    
    # Detect Dogme entry point from conversation
    if "only basecall" in all_user_text or "just basecalling" in all_user_text or "basecall only" in all_user_text:
        params["entry_point"] = "basecall"
        params["input_type"] = "pod5"
    elif "call modifications" in all_user_text or "run modkit" in all_user_text or "extract modifications" in all_user_text:
        params["entry_point"] = "modkit"
        params["input_type"] = "bam"  # modkit needs mapped BAM
    elif "generate report" in all_user_text or "create summary" in all_user_text or "reports only" in all_user_text:
        params["entry_point"] = "reports"
    elif "annotate" in all_user_text and ("transcripts" in all_user_text or "rna" in all_user_text):
        params["entry_point"] = "annotateRNA"
        params["input_type"] = "bam"  # annotateRNA needs mapped BAM
    elif "unmapped bam" in all_user_text or "remap" in all_user_text:
        params["entry_point"] = "remap"
        params["input_type"] = "bam"
    elif "downloaded bam" in all_user_text or "from bam" in all_user_text or ("bam" in all_user_text and "from data" in all_user_text):
        # User wants to run Dogme from downloaded BAM files in project data/
        params["entry_point"] = "remap"
        params["input_type"] = "bam"
    elif ".bam" in all_user_text_original:
        # Detect if BAM is mapped or unmapped based on context
        if "mapped" in all_user_text and "unmapped" not in all_user_text:
            params["input_type"] = "bam"
            # Will need to determine entry point based on other keywords
        else:
            params["entry_point"] = "remap"
            params["input_type"] = "bam"
    elif ".fastq" in all_user_text_original or ".fq" in all_user_text_original or "fastq" in all_user_text:
        params["input_type"] = "fastq"
    
    # Detect genome from keywords - support multiple genomes
    genome_keywords = ["human", "mouse", "hg38", "mm39", "mm10", "grch38"]
    found_genomes = set()
    
    # Find all genome mentions
    for keyword in genome_keywords:
        if keyword in all_user_text:
            canonical = GENOME_ALIASES.get(keyword, keyword)
            found_genomes.add(canonical)
    
    # Check for "both X and Y" pattern
    multi_genome_pattern = r'both\s+(\w+)\s+and\s+(\w+)'
    multi_match = re.search(multi_genome_pattern, all_user_text)
    if multi_match:
        g1 = GENOME_ALIASES.get(multi_match.group(1), multi_match.group(1))
        g2 = GENOME_ALIASES.get(multi_match.group(2), multi_match.group(2))
        found_genomes.add(g1)
        found_genomes.add(g2)
    
    # Convert to list, default to mouse if none found
    # For modkit/annotateRNA, do NOT auto-default genome — the user must specify
    # the genome the BAM was actually mapped to.
    if found_genomes:
        params["reference_genome"] = list(found_genomes)
    elif params["entry_point"] in ("modkit", "annotateRNA"):
        params["reference_genome"] = []  # Force the intake skill to ask
    else:
        params["reference_genome"] = ["mm39"]
    
    # Detect mode from keywords
    if "rna" in all_user_text and "cdna" not in all_user_text:
        params["mode"] = "RNA"
    elif "cdna" in all_user_text:
        params["mode"] = "CDNA"
    elif "dna" in all_user_text or "genomic" in all_user_text or "fiber" in all_user_text:
        params["mode"] = "DNA"
    else:
        params["mode"] = "DNA"  # Default to DNA
    
    # Look for paths in user messages (use ORIGINAL case to preserve path)
    # First try: relative paths with known sequencing extensions (data/ENCFF921XAH.bam)
    _rel_path_pattern = r'\b([\w.-]+/[\w./-]+\.(?:bam|pod5|fastq|fq|fast5))\b'
    _rel_paths = re.findall(_rel_path_pattern, all_user_text_original)
    # Second try: absolute paths
    _abs_path_pattern = r'(/[^\s,]+(?:/[^\s,]+)*)'
    _abs_paths = re.findall(_abs_path_pattern, all_user_text_original)
    
    if _rel_paths:
        cleaned_path = _rel_paths[0].rstrip('.,;:!?')
        # Resolve relative path against project directory
        _proj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        _owner_id = None
        # Find the project owner from blocks
        for block in blocks:
            if block.owner_id:
                _owner_id = block.owner_id
                break
        if _owner_id:
            _owner_user = session.execute(select(User).where(User.id == _owner_id)).scalar_one_or_none()
            _uname = getattr(_owner_user, 'username', None) if _owner_user else None
            _slug = _proj.slug if _proj else None
            if _uname and _slug:
                _project_dir = AGOUTIC_DATA / "users" / _uname / _slug
            else:
                _project_dir = AGOUTIC_DATA / "users" / _owner_id / project_id
            params["input_directory"] = str(_project_dir / cleaned_path)
        else:
            params["input_directory"] = cleaned_path
    elif _abs_paths:
        cleaned_path = _abs_paths[0].rstrip('.,;:!?')
        params["input_directory"] = cleaned_path
    else:
        params["input_directory"] = "/data/samples/test"
    
    # Extract sample name from context
    # Search user messages in REVERSE order (most recent first) so a new
    # submission request wins over older ones in the same cycle.
    explicit_patterns = [
        r'([a-zA-Z0-9_-]+)\s+is\s+(?:the\s+)?sample\s+name',  # "Jamshid is sample name"
        r'sample\s+name\s+is\s+([a-zA-Z0-9_-]+)',  # "sample name is Jamshid"
        r'named\s+([a-zA-Z0-9_-]+)',  # "named Ali1"
        r'called\s+([a-zA-Z0-9_-]+)',  # "called Ali1"
        r'(?:the\s+)?sample\s+([a-zA-Z0-9_-]+)',  # "the sample c2c12r1" / "sample c2c12r1"
        r'analyze\s+(?:the\s+)?(?:sample\s+)?([a-zA-Z0-9_-]+)\s+using',  # "analyze c2c12r1 using"
    ]
    _skip_words = {'is', 'the', 'a', 'an', 'this', 'that', 'it', 'at', 'in', 'on',
                   'mm39', 'grch38', 'hg38', 'mm10', 'name', 'type', 'data', 'file',
                   'using', 'with', 'from', 'for', 'my', 'new', 'rna', 'dna', 'cdna'}
    for msg in reversed(user_messages):
        for pattern in explicit_patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                if candidate.lower() not in _skip_words:
                    params["sample_name"] = candidate
                    break
        if params["sample_name"]:
            break
    
    # If not found via explicit pattern, check standalone answers (but filter more carefully)
    if not params["sample_name"]:
        for msg in reversed(user_messages):
            msg_lower = msg.lower().strip()
            # Check if this is a short, standalone response (likely an answer to a question)
            if len(msg.split()) <= 5 and len(msg) < 50:
                # Extract just the name part if it says "X is sample name"
                name_match = re.search(r'^([a-zA-Z0-9_-]+)(?:\s+is\s+sample\s+name)?$', msg, re.IGNORECASE)
                if name_match:
                    potential_name = name_match.group(1)
                    # Not a path, not a common word, not a genome name
                    if (potential_name.lower() not in ['dna', 'rna', 'cdna', 'mm39', 'grch38', 'hg38', 'mm10', 'human', 'mouse', 'yes', 'no', 'sup', 'hac', 'fast']
                        and '/' not in potential_name):
                        params["sample_name"] = potential_name
                        break
    
    if not params["sample_name"]:
        # Use genome type + project timestamp as default
        genome_type = "mouse" if "mm39" in params["reference_genome"] else "human"
        params["sample_name"] = f"{genome_type}_sample_{project_id.split('_')[-1]}"
    
    # Extract advanced parameters if mentioned
    # modkit_filter_threshold (handle "threshold of 0.85" or "threshold: 0.85")
    threshold_pattern = r'(?:modkit\s+)?(?:threshold|filter)(?:[:\s]+of\s+|[:\s]+)([0-9.]+)'
    threshold_match = re.search(threshold_pattern, all_user_text)
    if threshold_match:
        try:
            params["modkit_filter_threshold"] = float(threshold_match.group(1))
        except ValueError:
            pass
    
    # min_cov (handle "coverage of 10" or "min cov: 10")
    mincov_pattern = r'(?:min[_\s]*cov|minimum[_\s]*coverage)(?:[:\s]+of\s+|[:\s]+)(\d+)'
    mincov_match = re.search(mincov_pattern, all_user_text)
    if mincov_match:
        params["min_cov"] = int(mincov_match.group(1))
    
    # per_mod (handle "per mod of 8" or "per_mod: 8")
    permod_pattern = r'(?:per[_\s]*mod|percentage)(?:[:\s]+of\s+|[:\s]+)(\d+)'
    permod_match = re.search(permod_pattern, all_user_text)
    if permod_match:
        params["per_mod"] = int(permod_match.group(1))
    
    # accuracy (sup, hac, fast)
    if "accuracy" in all_user_text:
        if "hac" in all_user_text:
            params["accuracy"] = "hac"
        elif "fast" in all_user_text:
            params["accuracy"] = "fast"
        elif "sup" in all_user_text:
            params["accuracy"] = "sup"
    
    # max_gpu_tasks (handle "max gpu tasks 2", "limit dorado to 3", "run 2 gpu tasks at a time")
    gpu_task_patterns = [
        r'max[_\s]*gpu[_\s]*tasks?[:\s]+(?:of\s+)?(\d+)',
        r'limit\s+(?:dorado|gpu)\s+(?:tasks?\s+)?to\s+(\d+)',
        r'(\d+)\s+(?:concurrent|simultaneous|parallel)\s+(?:dorado|gpu)\s+tasks?',
        r'(?:run|allow)\s+(\d+)\s+(?:dorado|gpu)\s+tasks?',
    ]
    for gp in gpu_task_patterns:
        gpu_match = re.search(gp, all_user_text)
        if gpu_match:
            params["max_gpu_tasks"] = int(gpu_match.group(1))
            break
    
    logger.info("Extracted parameters", method="heuristics", params=params)
    return params


async def handle_rejection(project_id: str, gate_block_id: str):
    """
    Background task to handle rejection of an approval gate.
    Passes feedback to LLM to regenerate plan. Limits to 3 attempts.
    """
    session = SessionLocal()
    
    try:
        # Get the rejected gate block
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if not gate_block:
            logger.error("Gate block not found", gate_block_id=gate_block_id)
            return
        
        # Get owner_id from the gate block
        owner_id = gate_block.owner_id
        
        # Get rejection details from payload
        gate_payload = get_block_payload(gate_block)
        rejection_reason = gate_payload.get("rejection_reason", "No reason provided")
        attempt_number = gate_payload.get("attempt_number", 1)
        rejection_history = gate_payload.get("rejection_history", [])
        
        # Add to rejection history
        rejection_history.append({
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "reason": rejection_reason,
            "attempt": attempt_number
        })
        
        # Update gate block with history
        gate_payload["rejection_history"] = rejection_history
        gate_block.payload_json = json.dumps(gate_payload)
        session.commit()
        
        logger.info("Handling rejection", project_id=project_id, attempt=attempt_number, reason=rejection_reason)
        
        # Check attempt limit
        if attempt_number >= 3:
            logger.warning("Max attempts reached, creating manual parameter form", project_id=project_id)
            
            # Extract parameters for manual form
            params = await extract_job_parameters_from_conversation(session, project_id)
            
            # Create a manual parameter form block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": f"""### ⚠️ Manual Configuration Required (Attempt 3/3)

I've tried 3 times but couldn't meet your requirements. Please verify and edit these parameters manually:

**Extracted Parameters:**
- Sample Name: `{params.get('sample_name', 'unknown')}`
- Mode: `{params.get('mode', 'DNA')}`
- Input Type: `{params.get('input_type', 'pod5')}`
- Input Directory: `{params.get('input_directory', '/path/to/data')}`
- Reference Genome(s): `{', '.join(params.get('reference_genome', ['mm39']))}`
- Modifications: `{params.get('modifications', 'auto')}`

Please use the parameter editing form below to make corrections.""",
                    "skill": get_block_payload(gate_block).get("skill", "analyze_local_sample"),
                    "model": get_block_payload(gate_block).get("model", "default")
                },
                status="NEW",
                owner_id=owner_id
            )
            
            # Create a new approval gate with manual mode flag
            _create_block_internal(
                session,
                project_id,
                "APPROVAL_GATE",
                {
                    "label": "Manual Configuration - Verify Parameters",
                    "extracted_params": params,
                    "manual_mode": True,
                    "attempt_number": 3,
                    "rejection_history": rejection_history
                },
                status="PENDING",
                owner_id=owner_id
            )
            
            return
        
        # Build conversation history for LLM
        query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == project_id)\
            .order_by(ProjectBlock.seq.asc())
        
        result = session.execute(query)
        blocks = result.scalars().all()
        
        conversation_history = []
        active_skill = None
        
        for block in blocks:
            block_payload = get_block_payload(block)
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block_payload.get("text", "")
                })
            elif block.type == "AGENT_PLAN":
                conversation_history.append({
                    "role": "assistant",
                    "content": block_payload.get("markdown", "")
                })
                active_skill = block_payload.get("skill")
        
        # Add rejection feedback as user message
        conversation_history.append({
            "role": "user",
            "content": f"I'm rejecting your plan (attempt {attempt_number}/3). Please revise it.\n\nFeedback: {rejection_reason}\n\nPlease generate a new plan that addresses my concerns."
        })
        
        # Get the active skill and model
        gate_payload = get_block_payload(gate_block)
        skill_name = active_skill or gate_payload.get("skill", "analyze_local_sample")
        model_name = gate_payload.get("model", "default")
        
        # Call LLM to regenerate plan
        agent_engine = AgentEngine(model_name=model_name)
        
        try:
            llm_response = await run_in_threadpool(
                agent_engine.think,
                skill_name=skill_name,
                user_message=f"[REVISION REQUEST] {rejection_reason}",
                conversation_history=conversation_history
            )
            
            # Check if response needs approval or is asking for more info
            needs_approval = "[[APPROVAL_NEEDED]]" in llm_response
            clean_response = llm_response.replace("[[APPROVAL_NEEDED]]", "").strip()
            
            # Create new AGENT_PLAN block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": clean_response,
                    "skill": skill_name,
                    "model": model_name,
                    "revision_attempt": attempt_number + 1
                },
                status="DONE" if needs_approval else "NEW",
                owner_id=owner_id
            )
            
            # Only create approval gate if the agent explicitly requested it
            if needs_approval:
                # Extract parameters for new approval gate
                params = await extract_job_parameters_from_conversation(session, project_id)
                
                # Create new APPROVAL_GATE
                _create_block_internal(
                    session,
                    project_id,
                    "APPROVAL_GATE",
                    {
                        "label": f"Revised Plan (Attempt {attempt_number + 1}/3)",
                        "extracted_params": params,
                        "attempt_number": attempt_number + 1,
                        "rejection_history": rejection_history,
                        "skill": skill_name,
                        "model": model_name
                    },
                    status="PENDING",
                    owner_id=owner_id
                )
                logger.info("Generated revised plan", project_id=project_id, attempt=attempt_number + 1)
            else:
                logger.info("Agent requesting more information", project_id=project_id, attempt=attempt_number + 1)
            
        except Exception as e:
            logger.error("LLM failed during revision", error=str(e), project_id=project_id)
            # Create error block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": f"### ⚠️ Error\n\nFailed to generate revised plan: {str(e)}\n\nPlease try again or use manual configuration.",
                    "error": str(e)
                },
                status="ERROR",
                owner_id=owner_id
            )
    
    finally:
        session.close()


async def download_after_approval(project_id: str, gate_block_id: str):
    """Background task to start downloading files after approval.

    Downloads are written to the user's **central data folder** and symlinked
    into the project.  If the gate extracted_params contains a 'files' list
    (set by the download chain), use those directly.  Otherwise falls back to
    scanning conversation blocks for HTTP/HTTPS URLs.
    """
    session = SessionLocal()
    try:
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if not gate_block:
            logger.error("Gate block not found for download", gate_block_id=gate_block_id)
            return

        owner_id = gate_block.owner_id
        gate_payload = get_block_payload(gate_block)
        gate_params = gate_payload.get("extracted_params", {})

        # --- Preferred path: files list from download chain ---
        urls: list[dict] = []
        _target_dir_str: str | None = gate_params.get("target_dir")

        if gate_params.get("files"):
            for f in gate_params["files"]:
                urls.append({
                    "url": f["url"],
                    "filename": f.get("filename", f["url"].rsplit("/", 1)[-1].split("?")[0] or "file"),
                    "size_bytes": f.get("size_bytes"),
                })

        # --- Fallback: scan conversation blocks for URLs ---
        if not urls:
            query = select(ProjectBlock)\
                .where(ProjectBlock.project_id == project_id)\
                .order_by(ProjectBlock.seq.asc())
            blocks = session.execute(query).scalars().all()

            url_pattern = re.compile(r'https?://[^\s\)\"\'>\]]+')

            for blk in blocks:
                if blk.type not in ("AGENT_PLAN", "USER_MESSAGE"):
                    continue
                payload = get_block_payload(blk)
                text = payload.get("markdown", "") or payload.get("text", "")
                for match in url_pattern.finditer(text):
                    url = match.group(0).rstrip(".,;:")
                    # Skip obvious non-file URLs (API endpoints, portal pages)
                    if any(skip in url for skip in ["/api/", "/search/", "portal.encode"]):
                        continue
                    filename = url.rsplit("/", 1)[-1].split("?")[0] or "file"
                    # Avoid duplicates
                    if not any(u["url"] == url for u in urls):
                        urls.append({"url": url, "filename": filename})

        if not urls:
            _create_block_internal(
                session, project_id, "AGENT_PLAN",
                {
                    "markdown": "⚠️ No download URLs found in the conversation. "
                                "Please provide URLs or ENCODE file accessions and try again.",
                    "skill": "download_files",
                    "model": "system",
                },
                status="DONE",
                owner_id=owner_id,
            )
            return

        # Resolve target directory — prefer user's central data folder
        owner_user = session.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
        project_obj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        _username = getattr(owner_user, "username", None) if owner_user else None
        _slug = project_obj.slug if project_obj else None

        if _username:
            from cortex.user_jail import get_user_data_dir
            target_dir = get_user_data_dir(_username)
        elif _target_dir_str:
            target_dir = Path(_target_dir_str)
        else:
            if _username and _slug:
                project_dir = Path(AGOUTIC_DATA) / "users" / _username / _slug
            else:
                project_dir = Path(AGOUTIC_DATA) / "users" / owner_id / project_id
            target_dir = project_dir / "data"
        target_dir.mkdir(parents=True, exist_ok=True)

        download_id = str(uuid.uuid4())

        block = _create_block_internal(
            session, project_id, "DOWNLOAD_TASK",
            {
                "download_id": download_id,
                "source": "conversation",
                "files": [{"filename": u["filename"], "url": u["url"]} for u in urls],
                "target_dir": str(target_dir),
                "downloaded": 0,
                "total_files": len(urls),
                "bytes_downloaded": 0,
                "status": "RUNNING",
            },
            status="RUNNING",
            owner_id=owner_id,
        )

        _active_downloads[download_id] = {
            "block_id": block.id,
            "project_id": project_id,
            "cancelled": False,
        }

        asyncio.create_task(
            _download_files_background(
                download_id=download_id,
                project_id=project_id,
                block_id=block.id,
                owner_id=owner_id,
                files=urls,
                target_dir=target_dir,
                username=_username,
                project_slug=_slug,
                source="conversation",
            )
        )

    except Exception as e:
        logger.error("download_after_approval failed", error=str(e), exc_info=True)
    finally:
        session.close()


async def submit_job_after_approval(project_id: str, gate_block_id: str):
    """
    Background task to submit a job to Launchpad after approval.
    Uses edited_params if available, otherwise falls back to extracted_params.
    """
    session = SessionLocal()
    
    try:
        # Get the gate block to check for edited params
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        
        if not gate_block:
            logger.error("Gate block not found", gate_block_id=gate_block_id)
            return
        
        # Get owner_id from the gate block
        owner_id = gate_block.owner_id
        
        # Prefer edited_params over extracted_params
        gate_payload = get_block_payload(gate_block)
        job_params = gate_payload.get("edited_params") or gate_payload.get("extracted_params")
        
        if not job_params:
            # Fallback: extract from conversation
            job_params = await extract_job_parameters_from_conversation(session, project_id)
        
        if not job_params:
            # Failed to extract parameters, create error block
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "error": "Failed to extract job parameters from conversation",
                    "message": "Could not determine sample name, data type, or input directory",
                    "job_status": {
                        "status": "FAILED",
                        "progress_percent": 0,
                        "message": "Parameter extraction failed",
                        "tasks": {}
                    }
                },
                status="FAILED",
                owner_id=owner_id
            )
            logger.error("Failed to extract parameters", project_id=project_id)
            return
        
        # Normalize reference_genome to list for Launchpad (Nextflow handles multi-genome in parallel)
        ref_genome = job_params.get("reference_genome", ["mm39"])
        if isinstance(ref_genome, str):
            ref_genome = [ref_genome]

        # Look up username and project_slug for human-readable work directories
        owner_user = session.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
        project_obj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        _username = owner_user.username if owner_user else None
        _project_slug = project_obj.slug if project_obj else None
        
        job_data = {
            "project_id": project_id,
            "user_id": owner_id,  # Pass owner for jailed file paths and job ownership
            "username": _username,  # Human-readable dir name (may be None for legacy users)
            "project_slug": _project_slug,  # Human-readable dir name (may be None for legacy projects)
            "sample_name": job_params.get("sample_name", f"sample_{project_id.split('_')[-1]}"),
            "mode": job_params.get("mode", "DNA"),
            "input_directory": job_params.get("input_directory", "/data/samples/test"),
            "reference_genome": ref_genome,  # List — Nextflow parallelizes across genomes
            "modifications": job_params.get("modifications"),
            "input_type": job_params.get("input_type", "pod5"),
            "entry_point": job_params.get("entry_point"),
            # Advanced parameters - use Launchpad defaults if None
            "modkit_filter_threshold": job_params.get("modkit_filter_threshold") or 0.9,
            "min_cov": job_params.get("min_cov"),  # Let Launchpad handle None (mode-dependent default)
            "per_mod": job_params.get("per_mod") or 5,
            "accuracy": job_params.get("accuracy") or "sup",
            "max_gpu_tasks": job_params.get("max_gpu_tasks") or 1,
        }

        # If this is a resume (resubmit from cancelled/failed), pass the old work directory
        # Check both edited_params and extracted_params since resume_from_dir is internal
        _resume_dir = job_params.get("resume_from_dir") or gate_payload.get("extracted_params", {}).get("resume_from_dir")
        if _resume_dir:
            job_data["resume_from_dir"] = _resume_dir

        # For BAM remap: if no valid input_directory was found, resolve to project data/ dir
        # This handles "run dogme from downloaded BAM" where BAMs are in projectdir/data/
        _input_dir = job_data["input_directory"]
        if (job_data.get("input_type") == "bam"
            and job_data.get("entry_point") == "remap"
            and (_input_dir == "/data/samples/test" or not Path(_input_dir).exists())):
            if _username and _project_slug:
                _project_data_dir = str(Path(AGOUTIC_DATA) / "users" / _username / _project_slug / "data")
            else:
                _project_data_dir = str(Path(AGOUTIC_DATA) / "users" / owner_id / project_id / "data")
            if Path(_project_data_dir).exists():
                job_data["input_directory"] = _project_data_dir
                logger.info("Resolved BAM input to project data dir",
                           input_directory=_project_data_dir)
        
        logger.info("Job parameters prepared", source="edited" if gate_payload.get('edited_params') else "extracted",
                    job_data=job_data)
        
        # Submit job to Launchpad via MCP (single call — Nextflow handles multi-genome)
        try:
            launchpad_url = get_service_url("launchpad")
            client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
            await client.connect()
            try:
                result = await client.call_tool("submit_dogme_job", **job_data)
            finally:
                await client.disconnect()
        except Exception as e:
            raise Exception(f"MCP call to Launchpad failed: {e}")
        
        run_uuid = result.get("run_uuid") if isinstance(result, dict) else None
        _work_directory = result.get("work_directory", "") if isinstance(result, dict) else ""
        
        if run_uuid:
            
            # Create EXECUTION_JOB block
            # Include model_name so _auto_trigger_analysis can call the same LLM
            _gate_model = gate_payload.get("model", "default")
            job_block = _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "run_uuid": run_uuid,
                    "work_directory": _work_directory,
                    "sample_name": job_data["sample_name"],
                    "mode": job_data["mode"],
                    "model": _gate_model,
                    "status": "SUBMITTED",
                    "message": f"Job submitted: {run_uuid}",
                    "job_status": {
                        "status": "PENDING",
                        "progress_percent": 0,
                        "message": "Job submitted, waiting to start...",
                        "tasks": {}
                    },
                    "logs": []
                },
                status="RUNNING",
                owner_id=owner_id
            )
            
            logger.info("Job submitted", run_uuid=run_uuid, project_id=project_id)
            
            # Start polling job status in background
            asyncio.create_task(poll_job_status(project_id, job_block.id, run_uuid))
        else:
            # MCP call succeeded but no run_uuid in response
            # Show the actual result for debugging
            error_detail = str(result) if result else "empty response"
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "error": f"No run_uuid in Launchpad response: {error_detail}",
                    "message": error_detail,
                    "job_data": {k: str(v) for k, v in job_data.items()},  # Include params for debugging
                    "job_status": {
                        "status": "FAILED",
                        "progress_percent": 0,
                        "message": f"Submission failed: {error_detail}",
                        "tasks": {}
                    }
                },
                status="FAILED",
                owner_id=owner_id
            )
            logger.error("Job submission failed: no run_uuid in response", result=result, result_type=type(result).__name__, job_data=job_data)
    
    except Exception as e:
        # Create error block with full details
        import traceback
        error_trace = traceback.format_exc()
        error_msg = str(e)
        _create_block_internal(
            session,
            project_id,
            "EXECUTION_JOB",
            {
                "error": error_msg,
                "message": f"Failed to submit job to Launchpad: {error_msg}",
                "job_status": {
                    "status": "FAILED",
                    "progress_percent": 0,
                    "message": f"Error: {error_msg}",
                    "tasks": {}
                }
            },
            status="FAILED",
            owner_id=owner_id
        )
        logger.error("Job submission error", error=error_msg, traceback=error_trace, project_id=project_id)
    finally:
        session.close()

async def poll_job_status(project_id: str, block_id: str, run_uuid: str):
    """
    Background task to poll Launchpad for job status via MCP and update the EXECUTION_JOB block.
    Continues until job is completed or failed.
    """
    
    # Adaptive polling: fast at first (3 s) then slow down to 30 s.
    # Total coverage: ~10 h, which is enough for the longest pipelines.
    _POLL_SCHEDULE = [
        (120,   3),    # first 6 min  → every 3 s  (120 polls)
        (120,  10),    # next 20 min  → every 10 s
        (120,  30),    # next 60 min  → every 30 s
        (960,  30),    # next ~8 h    → every 30 s  (960 polls)
    ]
    _job_done = False
    
    for _batch_polls, _interval in _POLL_SCHEDULE:
        if _job_done:
            break
        for _ in range(_batch_polls):
            if _job_done:
                break
            await asyncio.sleep(_interval)
        
            session = SessionLocal()
            try:
                # Get current status from Launchpad via MCP
                launchpad_url = get_service_url("launchpad")
                client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
                await client.connect()
                try:
                    status_data = await client.call_tool("check_nextflow_status", run_uuid=run_uuid)
                    logs_data = await client.call_tool("get_job_logs", run_uuid=run_uuid, limit=50)
                finally:
                    await client.disconnect()
            
                if not isinstance(status_data, dict):
                    logger.warning("Failed to get status", run_uuid=run_uuid)
                    continue
            
                logs = logs_data.get("logs", []) if isinstance(logs_data, dict) else []
            
                # Update the block with new data
                block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
                if block:
                    # Create new payload dict to ensure SQLAlchemy detects the change
                    payload = get_block_payload(block)
                    payload["job_status"] = status_data
                    payload["logs"] = logs
                    payload["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
                
                    # Update block status based on job status
                    job_status = status_data.get("status", "UNKNOWN")
                    if job_status == "COMPLETED":
                        block.status = "DONE"
                    elif job_status == "FAILED":
                        block.status = "FAILED"
                    else:
                        block.status = "RUNNING"
                
                    # Reassign payload_json to trigger SQLAlchemy update
                    block.payload_json = json.dumps(payload)
                    session.commit()
                    session.refresh(block)
                
                    logger.info("Job status updated", run_uuid=run_uuid, job_status=job_status, progress=status_data.get('progress_percent', 0))
                
                    # Stop polling if job is done
                    if job_status in ("COMPLETED", "FAILED"):
                        logger.info("Job finished", run_uuid=run_uuid, job_status=job_status)
                    
                        # On completion, auto-trigger analysis
                        if job_status == "COMPLETED":
                            await _auto_trigger_analysis(
                                project_id, run_uuid, payload, block.owner_id
                            )
                    
                        _job_done = True
                        break
        
            except Exception as e:
                logger.warning("Error polling job", run_uuid=run_uuid, error=str(e))
            finally:
                session.close()
    
    logger.info("Stopped polling job", run_uuid=run_uuid)


async def _auto_trigger_analysis(
    project_id: str, run_uuid: str, job_payload: dict, owner_id: str | None
):
    """
    Automatically analyse a just-completed Dogme job.

    1. Fetches the analysis summary (file listing) from Analyzer.
    2. Parses key CSV result files (final_stats, qc_summary) via Analyzer MCP.
    3. Passes everything to the LLM for an intelligent first interpretation.
    4. Saves the LLM response as an AGENT_PLAN block with token tracking.

    Falls back to a static template if the LLM call fails.
    """
    sample_name = job_payload.get("sample_name", "Unknown")
    mode = job_payload.get("mode", "DNA")
    model_key = job_payload.get("model", "default")
    work_directory = job_payload.get("work_directory", "")

    logger.info("Auto-triggering analysis", run_uuid=run_uuid,
                sample_name=sample_name, mode=mode, model=model_key)

    session = SessionLocal()
    try:
        # 1. Create a system message announcing the transition
        _create_block_internal(
            session,
            project_id,
            "USER_MESSAGE",
            {"text": f"Job \"{sample_name}\" completed. Analyze the results."},
            owner_id=owner_id,
        )

        # Also save to conversation history so the LLM sees it
        if owner_id:
            await save_conversation_message(
                session, project_id, owner_id, "user",
                f"Job \"{sample_name}\" completed. Analyze the results."
            )

        # 2. Fetch analysis summary + key CSV data from Analyzer
        summary_data = {}  # structured summary from get_analysis_summary
        parsed_csvs = {}   # filename → parsed rows from key CSVs
        try:
            analyzer_url = get_service_url("analyzer")
            client = MCPHttpClient(name="analyzer", base_url=analyzer_url)
            await client.connect()
            try:
                summary_data = await client.call_tool(
                    "get_analysis_summary", run_uuid=run_uuid,
                    work_dir=work_directory or None,
                )
                if not isinstance(summary_data, dict):
                    summary_data = {}

                # Parse key CSV files for the LLM
                # Prioritise small, high-value files: final_stats, qc_summary
                csv_files = (
                    summary_data
                    .get("file_summary", {})
                    .get("csv_files", [])
                )
                _KEY_PATTERNS = ("final_stats", "qc_summary")
                for finfo in csv_files:
                    fname = finfo.get("name", "")
                    fsize = finfo.get("size", 0)
                    if fsize > 500_000:  # skip files > 500 KB
                        continue
                    if any(pat in fname.lower() for pat in _KEY_PATTERNS):
                        try:
                            _csv_params: dict = {
                                "file_path": fname,
                                "max_rows": 50,
                            }
                            if work_directory:
                                _csv_params["work_dir"] = work_directory
                            else:
                                _csv_params["run_uuid"] = run_uuid
                            parse_result = await client.call_tool(
                                "parse_csv_file",
                                **_csv_params,
                            )
                            if isinstance(parse_result, dict) and parse_result.get("data"):
                                parsed_csvs[fname] = parse_result
                        except Exception as csv_err:
                            logger.debug("Failed to parse CSV for auto-analysis",
                                         filename=fname, error=str(csv_err))
            finally:
                await client.disconnect()
        except Exception as e:
            logger.warning("Failed to fetch analysis summary", run_uuid=run_uuid, error=str(e))

        # 3. Map mode → Dogme analysis skill
        mode_skill_map = {
            "DNA": "run_dogme_dna",
            "RNA": "run_dogme_rna",
            "CDNA": "run_dogme_cdna",
        }
        analysis_skill = mode_skill_map.get(mode.upper(), "analyze_job_results")

        # 4. Build data context string for the LLM
        data_context = _build_auto_analysis_context(
            sample_name, mode, run_uuid, summary_data, parsed_csvs
        )

        # 5. Call the LLM for an intelligent interpretation
        llm_md = ""
        llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        engine = None
        try:
            engine = AgentEngine(model_key=model_key)
            user_prompt = (
                f"A Dogme {mode} job for sample \"{sample_name}\" just completed.\n"
                f"Work directory: {work_directory}\n\n"
                f"Here is the analysis summary and key result data:\n\n"
                f"{data_context}\n\n"
                f"Provide a concise interpretation of these results. "
                f"Highlight key metrics, any QC concerns, and suggest "
                f"next steps the user could explore."
            )
            llm_md, llm_usage = await run_in_threadpool(
                engine.think,
                user_prompt,
                analysis_skill,
                None,  # no conversation history needed — data is self-contained
            )
            # Strip any tags the LLM might emit (it shouldn't, but be safe)
            llm_md = re.sub(r'\[\[DATA_CALL:.*?\]\]', '', llm_md)
            llm_md = re.sub(r'\[\[SKILL_SWITCH_TO:.*?\]\]', '', llm_md)
            llm_md = re.sub(r'\[\[APPROVAL_NEEDED\]\]', '', llm_md)
            llm_md = llm_md.strip()
            logger.info("Auto-analysis LLM call succeeded",
                        run_uuid=run_uuid, tokens=llm_usage.get("total_tokens", 0))
        except Exception as llm_err:
            logger.warning("Auto-analysis LLM call failed, using static template",
                           run_uuid=run_uuid, error=str(llm_err))
            llm_md = ""  # fall through to static template below

        # 6. Build the final markdown
        if llm_md:
            # LLM succeeded — prepend a header and append exploration hints
            _wf_name = work_directory.rstrip('/').rsplit('/', 1)[-1] if work_directory else ''
            final_md = (
                f"### 📊 Analysis: {sample_name}\n"
                f"**Workflow:** {_wf_name} &nbsp;|&nbsp; "
                f"**Mode:** {mode} &nbsp;|&nbsp; "
                f"**Status:** COMPLETED\n\n"
                f"{llm_md}\n\n"
                f"💡 *You can ask me to dive deeper — for example:*\n"
                f"- \"Show me the modification summary\"\n"
                f"- \"Parse the CSV results\"\n"
                f"- \"Give me a QC report\"\n"
            )
            _model_name = engine.model_name if engine else "system"
        else:
            # Fallback: static template (same as before)
            final_md = _build_static_analysis_summary(
                sample_name, mode, run_uuid, summary_data,
                work_directory=work_directory,
            )
            _model_name = "system"
            llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 7. Create AGENT_PLAN block with the analysis
        _token_payload = {
            **llm_usage,
            "model": _model_name,
        }
        _create_block_internal(
            session,
            project_id,
            "AGENT_PLAN",
            {
                "markdown": final_md,
                "skill": analysis_skill,
                "model": _model_name,
                "tokens": _token_payload,
            },
            status="DONE",
            owner_id=owner_id,
        )

        # Save assistant response to conversation history with token tracking
        if owner_id:
            await save_conversation_message(
                session, project_id, owner_id, "assistant", final_md,
                token_data=_token_payload, model_name=_model_name
            )

        logger.info("Auto-analysis block created", run_uuid=run_uuid,
                    skill=analysis_skill, model=_model_name,
                    tokens=llm_usage.get("total_tokens", 0))

    except Exception as e:
        logger.error("Auto-trigger analysis failed", run_uuid=run_uuid, error=str(e))
    finally:
        session.close()


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
            # Return capabilities list
            capabilities_text = """### 🧬 AGOUTIC Capabilities

I can help you analyze nanopore sequencing data using the Dogme pipeline. Here's what I can do:

#### **📊 Full Analysis Workflows**
- **DNA Analysis**: Genomic DNA with modification calling (5mCG, 5hmCG, 6mA)
- **RNA Analysis**: Direct RNA with modification detection (m6A, pseudouridine, m5C)
- **cDNA Analysis**: cDNA sequencing with transcript quantification

#### **🔄 Flexible Entry Points**
- **Full Pipeline** (pod5 → results): Complete analysis from raw data
- **Basecalling Only**: Convert pod5 files to unmapped BAM
- **Remap**: Start from unmapped BAM files
- **Modification Calling**: Run modkit on mapped BAMs
- **Transcript Annotation**: Annotate RNA/cDNA BAMs with transcript info
- **Report Generation**: Create summary reports from existing outputs

#### **🧬 Multi-Genome Support**
- Map to multiple reference genomes in one run
- Supported genomes: **GRCh38** (human), **mm39** (mouse)
- Example: "Analyze against both human and mouse genomes"

#### **📁 Input Flexibility**
- **pod5 files**: Raw nanopore data
- **Unmapped BAM**: Already basecalled data
- **Mapped BAM**: For modification calling or annotation

#### **🎛️ Parameter Control**
- Interactive parameter editing before job submission
- Specify sample names, reference genomes, modifications
- Advanced settings: modkit threshold, min coverage, accuracy mode
- Reject and revise plans up to 3 times
- Manual parameter override available

#### **💬 Example Requests**
```
"Analyze mouse cDNA sample named Ali1 at /path/to/pod5"
"Remap this unmapped BAM file: /path/to/sample.bam"
"Only basecall pod5 files in /path/to/data"
"Call modifications on mapped BAM at /path/to/aligned.bam"
"Generate report for job in /path/to/work_dir"
"Map sample to both human and mouse genomes"
```

**Ready to start? Just describe what you want to analyze!**
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

        # Build structured conversation state and prepend as JSON
        _conv_state = _build_conversation_state(
            active_skill, conversation_history,
            history_blocks=history_blocks,
            project_id=req.project_id,
        )
        _state_json = _conv_state.to_json()
        if _state_json and _state_json != "{}":
            augmented_message = f"[STATE]\n{_state_json}\n[/STATE]\n\n{augmented_message}"

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
            if new_skill in SKILLS_REGISTRY:
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

        
        # 4. Parse the "Approval Gate Tag"
        trigger_tag = "[[APPROVAL_NEEDED]]"
        needs_approval = trigger_tag in raw_response

        # Suppress spurious approval gates for skills that never submit jobs.
        # The LLM sometimes echoes [[APPROVAL_NEEDED]] from conversation
        # history even when the current skill is a search/browse skill.
        _APPROVAL_SKILLS = {
            "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
            "analyze_local_sample", "download_files",
        }
        if needs_approval and active_skill not in _APPROVAL_SKILLS:
            logger.warning(
                "Suppressing spurious APPROVAL_NEEDED for non-job skill",
                skill=active_skill,
            )
            needs_approval = False
            raw_response = raw_response.replace(trigger_tag, "").strip()
        
        # 5. Parse DATA_CALL Tags (unified format for all consortia and services)
        # Format: [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]
        #     or: [[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=...]]
        
        # FALLBACK: Fix common LLM mistakes by converting plain text patterns to proper tags
        all_fallback_patterns = get_all_fallback_patterns()
        
        corrected_response = raw_response
        fallback_fixes_applied = 0
        for pattern, replacement in all_fallback_patterns.items():
            before = corrected_response
            corrected_response = re.sub(pattern, replacement, corrected_response, flags=re.IGNORECASE)
            if before != corrected_response:
                fallback_fixes_applied += 1

        # FALLBACK: Convert [[TOOL_CALL: GET /analysis/...]] REST-style tags
        # the LLM sometimes emits instead of proper DATA_CALL tags.
        # Pattern: [[TOOL_CALL: GET /analysis/jobs/{work_dir}/summary?work_dir=...]]
        _tool_call_pattern = r'\[\[TOOL_CALL:\s*(?:GET\s+)?/analysis/[^?]*\?([^\]]+)\]\]'
        def _convert_tool_call_to_data_call(m: re.Match) -> str:
            query_string = m.group(1)
            # Parse query params: "work_dir=/path&foo=bar" → {work_dir: /path, foo: bar}
            params = {}
            for part in query_string.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k.strip()] = v.strip()
            # Determine tool from the URL path
            url_path = m.group(0).lower()
            if "summary" in url_path:
                tool = "get_analysis_summary"
            elif "categori" in url_path:
                tool = "categorize_job_files"
            elif "file" in url_path:
                tool = "list_job_files"
            else:
                tool = "get_analysis_summary"  # default
            param_str = ", ".join(f"{k}={v}" for k, v in params.items())
            return f"[[DATA_CALL: service=analyzer, tool={tool}, {param_str}]]"

        _before_tc = corrected_response
        corrected_response = re.sub(_tool_call_pattern, _convert_tool_call_to_data_call, corrected_response)
        if corrected_response != _before_tc:
            fallback_fixes_applied += 1
            logger.warning("Converted [[TOOL_CALL:...]] to [[DATA_CALL:...]]")
        
        if fallback_fixes_applied > 0:
            logger.warning("Applied fallback tag fixes to LLM response", count=fallback_fixes_applied)
        
        # Parse unified DATA_CALL tags
        # Groups: (1) source_type [consortium|service], (2) source_key, (3) tool_name, (4) remaining params
        data_call_pattern = r'\[\[DATA_CALL:\s*(?:(consortium|service)=(\w+)),\s*tool=(\w+)(?:,\s*([^\]]+))?\]\]'
        data_call_matches = list(re.finditer(data_call_pattern, corrected_response))
        
        # Also support legacy ENCODE_CALL and ANALYSIS_CALL tags for backward compatibility
        legacy_encode_pattern = r'\[\[ENCODE_CALL:\s*([\w_]+)(?:,\s*([^\]]+))?\]\]'
        legacy_analysis_pattern = r'\[\[ANALYSIS_CALL:\s*(\w+),\s*run_uuid=([a-f0-9-]+)\]\]'
        legacy_encode_matches = list(re.finditer(legacy_encode_pattern, corrected_response))
        legacy_analysis_matches = list(re.finditer(legacy_analysis_pattern, corrected_response))

        # 5a. Parse [[PLOT:...]] tags for interactive chart generation
        # Format: [[PLOT: type=scatter, df=DF5, x=score, y=enrichment, color=biosample_type, title=My Title]]
        # NOTE: use non-greedy .*? with DOTALL so that ] characters inside the tag
        # (e.g. Columns: ['A', 'B']) don't break the match.
        plot_tag_pattern = r'\[\[PLOT:\s*(.*?)\]\]'
        plot_tag_matches = list(re.finditer(plot_tag_pattern, corrected_response, re.DOTALL))
        plot_specs = []
        for _pm in plot_tag_matches:
            _raw_inner = _pm.group(1)
            _plot_params = _parse_tag_params(_raw_inner)
            # If no key=value pairs were found (LLM wrote natural language inside
            # the tag, e.g. "histogram of DF1 with Category on the x-axis"),
            # attempt to extract parameters from the free-form text.
            if not _plot_params.get("df"):
                _nl = _raw_inner
                # Chart type
                for _ct in ("histogram", "scatter", "bar", "box", "heatmap", "pie"):
                    if _ct in _nl.lower():
                        _plot_params.setdefault("type", _ct)
                        break
                # DF reference: "DF1", "df 2", etc.
                _nl_df_m = re.search(r'\bDF\s*(\d+)\b', _nl, re.IGNORECASE)
                if _nl_df_m:
                    _plot_params["df"] = f"DF{_nl_df_m.group(1)}"
                # x column — "Category on the x-axis", "x=Category", "x: Category"
                _x_m = re.search(
                    r'\b(\w+)\s+(?:on\s+the\s+)?x[- ]axis', _nl, re.IGNORECASE
                ) or re.search(
                    r'\bx\s*[=:]\s*([\w][\w ]*?)(?:,|\.|\band\b|$)', _nl, re.IGNORECASE
                )
                if _x_m:
                    _plot_params.setdefault("x", _x_m.group(1).strip())
                # y column — "Count on the y-axis", "y=Count"
                _y_m = re.search(
                    r'\b(\w+)\s+(?:on\s+the\s+)?y[- ]axis', _nl, re.IGNORECASE
                ) or re.search(
                    r'\by\s*[=:]\s*([\w][\w ]*?)(?:,|\.|\band\b|$)', _nl, re.IGNORECASE
                )
                if _y_m:
                    _plot_params.setdefault("y", _y_m.group(1).strip())
            # Normalize: extract df_id integer from "DF5" or "5"
            _df_ref = _plot_params.get("df", "")
            _df_id_match = re.match(r'(?:DF)?\s*(\d+)', _df_ref, re.IGNORECASE)
            if _df_id_match:
                _plot_params["df_id"] = int(_df_id_match.group(1))
            else:
                _plot_params["df_id"] = None
            # Default chart type to histogram if missing
            if "type" not in _plot_params:
                _plot_params["type"] = "histogram"
            plot_specs.append(_plot_params)
        if plot_specs:
            logger.info("Parsed PLOT tags", count=len(plot_specs),
                       specs=[{"type": s.get("type"), "df_id": s.get("df_id")} for s in plot_specs])

        # 5a-fix. Override hallucinated DF references in PLOT tags.
        # When the user says "plot this" / "plot it" without naming a specific DF,
        # the LLM often defaults to DF1.  Correct to the most recent DF.
        _user_explicit_df = re.search(r'\bDF\s*(\d+)\b', req.message, re.IGNORECASE)
        if plot_specs and not _user_explicit_df and _conv_state.latest_dataframe:
            _latest_id_m = re.match(r'DF(\d+)', _conv_state.latest_dataframe)
            if _latest_id_m:
                _latest_id = int(_latest_id_m.group(1))
                for _ps in plot_specs:
                    if _ps.get("df_id") is not None and _ps["df_id"] != _latest_id:
                        logger.warning(
                            "Overriding PLOT df_id with latest DF",
                            llm_df_id=_ps["df_id"], latest_df_id=_latest_id,
                        )
                        _ps["df_id"] = _latest_id
                        _ps["df"] = f"DF{_latest_id}"

        # 5a-b. FALLBACK: If LLM wrote Python code for plotting instead of [[PLOT:...]] tags,
        #        auto-generate the tag from context clues (user message + DF references).
        _plot_keywords = {"plot", "chart", "pie", "histogram", "scatter", "bar chart",
                          "box plot", "heatmap", "visualize", "graph", "distribution"}
        _user_wants_plot = any(kw in req.message.lower() for kw in _plot_keywords)
        _has_code_plot = bool(re.search(
            r'```python.*?(?:matplotlib|plt\.|plotly|px\.|fig\.|\.pie|\.bar|\.hist|\.scatter)',
            corrected_response, re.DOTALL | re.IGNORECASE
        ))
        # Also trigger the fallback when:
        # (a) the LLM produced [[PLOT:...]] tags but none resolved to a valid df_id, OR
        # (b) no plot specs were parsed at all (regex match failed, e.g. ] inside tag
        #     ate the match before the NL parser could run).
        _plot_specs_invalid = plot_specs and all(s.get("df_id") is None for s in plot_specs)
        _plot_specs_missing = _user_wants_plot and not plot_specs
        if _user_wants_plot and (_has_code_plot or _plot_specs_invalid or _plot_specs_missing) and not (
            plot_specs and any(s.get("df_id") is not None for s in plot_specs)
        ):
            logger.warning("LLM wrote Python plot code instead of [[PLOT:...]] tag — auto-generating")
            # Detect which DF the user or LLM referenced
            _auto_df_id = None
            _df_ref_in_msg = re.search(r'\bDF\s*(\d+)\b', req.message, re.IGNORECASE)
            if _df_ref_in_msg:
                _auto_df_id = int(_df_ref_in_msg.group(1))
            else:
                _df_ref_in_resp = re.search(r'\bDF\s*(\d+)\b', corrected_response, re.IGNORECASE)
                if _df_ref_in_resp:
                    _auto_df_id = int(_df_ref_in_resp.group(1))
                elif _injected_dfs:
                    # Use the first injected DF's ID
                    for _ij_data in _injected_dfs.values():
                        _ij_id = _ij_data.get("metadata", {}).get("df_id")
                        if _ij_id is not None:
                            _auto_df_id = _ij_id
                            break
                # Last resort: use latest_dataframe from conversation state
                if _auto_df_id is None and _conv_state.latest_dataframe:
                    _ld_m = re.match(r'DF(\d+)', _conv_state.latest_dataframe)
                    if _ld_m:
                        _auto_df_id = int(_ld_m.group(1))
            # Detect chart type from user message
            _msg_l = req.message.lower()
            if "pie" in _msg_l:
                _auto_type = "pie"
            elif "scatter" in _msg_l:
                _auto_type = "scatter"
            elif "bar" in _msg_l:
                _auto_type = "bar"
            elif "box" in _msg_l:
                _auto_type = "box"
            elif "heatmap" in _msg_l or "correlation" in _msg_l:
                _auto_type = "heatmap"
            elif "histogram" in _msg_l or "distribution" in _msg_l:
                _auto_type = "histogram"
            else:
                _auto_type = "bar"
            # Detect x column from user message (look for "by <column>", "of <column>")
            # Prefer "by" matches over "of/for" since "by" usually indicates the grouping column.
            # Skip DF references (e.g., "for DF1") — those aren't column names.
            _auto_x = None
            _by_match = re.search(r'\bby\s+(\w+)', req.message, re.IGNORECASE)
            if _by_match and not re.match(r'DF\d+', _by_match.group(1), re.IGNORECASE):
                _auto_x = _by_match.group(1)
            if not _auto_x:
                _of_match = re.search(r'\b(?:of|for)\s+(\w+)', req.message, re.IGNORECASE)
                if _of_match and not re.match(r'DF\d+', _of_match.group(1), re.IGNORECASE):
                    _auto_x = _of_match.group(1)
            if _auto_df_id is not None:
                _auto_spec = {
                    "type": _auto_type,
                    "df_id": _auto_df_id,
                    "df": f"DF{_auto_df_id}",
                }
                if _auto_x:
                    _auto_spec["x"] = _auto_x
                # Do NOT set agg=count for bar by default — if the DF is
                # pre-aggregated (has a numeric value column), _build_plotly_figure
                # will use it automatically via the categorical-x companion-column logic.
                if _auto_type == "pie" and not _auto_spec.get("y"):
                    # Pie charts with only x column count occurrences
                    pass  # UI handles this automatically
                plot_specs.append(_auto_spec)
                logger.info("Auto-generated PLOT spec from code fallback",
                           spec=_auto_spec)
            # Strip the code block from the markdown so user doesn't see code
            corrected_response = re.sub(
                r'```python.*?```', '', corrected_response, flags=re.DOTALL
            ).strip()
            # Also strip any "Explanation:" boilerplate that follows code blocks
            corrected_response = re.sub(
                r'\n*(?:Explanation|Output|Note|Here is|The (?:pie|bar|scatter|histogram|box) chart).*$',
                '', corrected_response, flags=re.DOTALL | re.IGNORECASE
            ).strip()

        # 5a-c. Plot-command override: when the user wants a plot and we have
        # valid plot_specs, suppress any DATA_CALL / APPROVAL tags the LLM
        # emitted.  Without this, "plot this" on Dogme skills triggers
        # list_job_files + a spurious job submission approval gate.
        if _user_wants_plot and plot_specs and any(s.get("df_id") is not None for s in plot_specs):
            if data_call_matches or legacy_encode_matches or legacy_analysis_matches or needs_approval:
                logger.warning(
                    "Plot-command override: suppressing LLM DATA_CALL/APPROVAL tags",
                    data_calls=len(data_call_matches),
                    legacy_encode=len(legacy_encode_matches),
                    needs_approval=needs_approval,
                )
            data_call_matches = []
            legacy_encode_matches = []
            legacy_analysis_matches = []
            needs_approval = False
            # Strip all tags from LLM response so only the plot placeholder remains
            corrected_response = re.sub(data_call_pattern, '', corrected_response).strip()
            corrected_response = re.sub(legacy_encode_pattern, '', corrected_response).strip()
            corrected_response = re.sub(legacy_analysis_pattern, '', corrected_response).strip()
            corrected_response = corrected_response.replace(trigger_tag, "").strip()

        # Clean all tags from user-visible text
        clean_markdown = corrected_response.replace(trigger_tag, "").strip()
        clean_markdown = re.sub(r'\[\[SKILL_SWITCH_TO:\s*\w+\]\]', '', clean_markdown).strip()
        clean_markdown = re.sub(data_call_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(legacy_encode_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(legacy_analysis_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(plot_tag_pattern, '', clean_markdown, flags=re.DOTALL).strip()
        
        # Also remove any remaining plain text patterns that might not have been converted
        for pattern in all_fallback_patterns.keys():
            clean_markdown = re.sub(pattern, '', clean_markdown, flags=re.IGNORECASE).strip()

        # If the LLM only emitted a PLOT tag (no surrounding text), insert a
        # brief placeholder so the AGENT_PLAN block isn't empty in the UI.
        if not clean_markdown and plot_specs:
            _ps0 = plot_specs[0]
            _chart_type = _ps0.get("type", "chart").replace("_", " ")
            _df_label = _ps0.get("df", "")  # string like "DF2"
            if not _df_label and _ps0.get("df_id") is not None:
                _df_label = f"DF{_ps0['df_id']}"
            clean_markdown = f"Here is the {_chart_type} for **{_df_label}**:" if _df_label else f"Here is the {_chart_type}:"

        # Fix hallucinated ENCSR accessions in LLM text response.
        # If the user explicitly stated exactly one ENCSR accession and the LLM
        # mentions a *different* ENCSR in its text, replace the wrong one.
        _encsr_in_user_msg = re.findall(r'(ENCSR[A-Z0-9]{6})', req.message, re.IGNORECASE)
        if len(_encsr_in_user_msg) == 1:
            _correct_encsr = _encsr_in_user_msg[0].upper()
            _encsr_in_reply = re.findall(r'(ENCSR[A-Z0-9]{6})', clean_markdown, re.IGNORECASE)
            _wrong_encsr = [a.upper() for a in _encsr_in_reply if a.upper() != _correct_encsr]
            for _wrong in set(_wrong_encsr):
                logger.warning("Fixing hallucinated ENCSR in LLM text",
                               hallucinated=_wrong, correct=_correct_encsr)
                clean_markdown = re.sub(
                    re.escape(_wrong), _correct_encsr, clean_markdown, flags=re.IGNORECASE
                )


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

        if not _is_user_data_override and not _is_browsing_override and not has_any_tags and (not _injected_previous_data or _injected_was_capped):
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

        # 6. Collect all tool calls into a unified structure
        # Structure: {source_key: [{"tool": str, "params": dict}, ...]}
        calls_by_source = {}
        
        # Load aliases for correcting LLM-hallucinated tool/param names
        _tool_aliases = get_all_tool_aliases()
        # Analyzer tool aliases (not in CONSORTIUM_REGISTRY)
        _tool_aliases.update({
            "list_workflows": "list_job_files",
            "list_files": "list_job_files",
            "browse_files": "list_job_files",
            "browse_workflow": "list_job_files",
            "find_files": "find_file",
            "search_file": "find_file",
            "search_files": "find_file",
            "read_file": "read_file_content",
            "get_file": "read_file_content",
            "parse_csv": "parse_csv_file",
            "parse_bed": "parse_bed_file",
            "analysis_summary": "get_analysis_summary",
            "job_summary": "get_analysis_summary",
        })
        _param_aliases = get_all_param_aliases()
        
        # From auto-generated tags (safety net)
        if not has_any_tags and auto_calls:
            for ac in auto_calls:
                _ac_tool = ac["tool"]
                _ac_params = dict(ac["params"])
                _ac_source = ac["source_key"]

                # Apply the same corrections as LLM-generated tags
                if _ac_source == "encode":
                    _ac_tool, _ac_params = _correct_tool_routing(
                        _ac_tool, _ac_params, req.message, conversation_history)
                    _ac_params = _validate_encode_params(_ac_tool, _ac_params, req.message)
                elif _ac_source == "analyzer":
                    _ac_params = _validate_analyzer_params(
                        _ac_tool, _ac_params, req.message,
                        conversation_history=conversation_history,
                        history_blocks=history_blocks,
                        project_dir=_project_dir_str,
                    )

                entry = {"tool": _ac_tool, "params": _ac_params}
                if "_chain" in ac:
                    entry["_chain"] = ac["_chain"]
                calls_by_source.setdefault(_ac_source, []).append(entry)

        # From new DATA_CALL tags
        for match in data_call_matches:
            source_type = match.group(1)  # "consortium" or "service"
            source_key = match.group(2)   # e.g., "encode", "analyzer"
            tool_name = match.group(3)
            params_str = match.group(4)
            
            # Fix hallucinated tool names using alias map
            corrected_tool = _tool_aliases.get(tool_name, tool_name)
            if corrected_tool != tool_name:
                logger.warning("Corrected hallucinated tool name",
                              original=tool_name, corrected=corrected_tool)
            
            params = _parse_tag_params(params_str)

            # Fix hallucinated parameter names using param alias map
            _p_aliases = _param_aliases.get(corrected_tool, {})
            if _p_aliases:
                params = {_p_aliases.get(k, k): v for k, v in params.items()}

            # Fix wrong tool for accession type (e.g. get_experiment for ENCFF)
            corrected_tool, params = _correct_tool_routing(
                corrected_tool, params, req.message, conversation_history)

            # Validate/fix ENCODE params (missing search_term, wrong organism)
            if source_key == "encode":
                params = _validate_encode_params(corrected_tool, params, req.message)

            # Validate/fix Analyzer params (placeholder work_dir)
            if source_key == "analyzer":
                params = _validate_analyzer_params(
                    corrected_tool, params, req.message,
                    conversation_history=conversation_history,
                    history_blocks=history_blocks,
                    project_dir=_project_dir_str,
                )

            calls_by_source.setdefault(source_key, []).append({
                "tool": corrected_tool,
                "params": params,
            })
        
        # From legacy ENCODE_CALL tags (backward compat)
        for match in legacy_encode_matches:
            tool_name = match.group(1)
            params_str = match.group(2)
            params = _parse_tag_params(params_str)
            
            # Fix hallucinated tool names using alias map
            corrected_tool = _tool_aliases.get(tool_name, tool_name)
            if corrected_tool != tool_name:
                logger.warning("Corrected hallucinated tool name (legacy)",
                              original=tool_name, corrected=corrected_tool)

            # Fix hallucinated parameter names using param alias map
            _p_aliases = _param_aliases.get(corrected_tool, {})
            if _p_aliases:
                params = {_p_aliases.get(k, k): v for k, v in params.items()}

            # Fix wrong tool for accession type (e.g. get_experiment for ENCFF)
            corrected_tool, params = _correct_tool_routing(
                corrected_tool, params, req.message, conversation_history)

            # Validate/fix ENCODE params (missing search_term, wrong organism)
            params = _validate_encode_params(corrected_tool, params, req.message)

            calls_by_source.setdefault("encode", []).append({
                "tool": corrected_tool,
                "params": params,
            })
        
        # From legacy ANALYSIS_CALL tags (backward compat)
        for match in legacy_analysis_matches:
            analysis_type = match.group(1)
            run_uuid = match.group(2)
            
            # Map legacy analysis types to Analyzer MCP tool names
            tool_map = {
                "summary": "get_analysis_summary",
                "categorize_files": "categorize_job_files",
                "list_files": "list_job_files",
            }
            tool_name = tool_map.get(analysis_type, analysis_type)
            
            calls_by_source.setdefault("analyzer", []).append({
                "tool": tool_name,
                "params": {"run_uuid": run_uuid},
            })
        
        # 6b. Deduplicate tool calls within each source.
        # The LLM sometimes emits both a DATA_CALL and a legacy ENCODE_CALL
        # (or two DATA_CALL tags with slightly different casing) for the same
        # logical query.  Deduplicate by (tool, canonical_params) so the same
        # search isn't executed twice.
        for _src_key in list(calls_by_source):
            _seen: set[str] = set()
            _deduped: list[dict] = []
            for _call in calls_by_source[_src_key]:
                # Build a canonical key: tool + sorted params (case-insensitive values)
                _canon_params = tuple(
                    sorted((k, v.lower() if isinstance(v, str) else v)
                           for k, v in _call["params"].items())
                )
                _key = (_call["tool"], _canon_params)
                _key_str = str(_key)
                if _key_str not in _seen:
                    _seen.add(_key_str)
                    _deduped.append(_call)
                else:
                    logger.info("Deduplicated duplicate tool call",
                               tool=_call["tool"], params=_call["params"])
            calls_by_source[_src_key] = _deduped

        # 6b2. Schema validation: validate params against cached tool schemas
        for _src_key, _calls_list in calls_by_source.items():
            for _call in _calls_list:
                if "__routing_error__" in _call.get("params", {}):
                    continue  # will be handled as routing error
                _cleaned, _violations = validate_against_schema(
                    _call["tool"], _call["params"], _src_key
                )
                if _violations:
                    logger.warning("Schema validation issues",
                                  tool=_call["tool"], source=_src_key,
                                  violations=_violations)
                _call["params"] = _cleaned

        # 6c. Execute all tool calls grouped by source
        all_results = {}  # {source_key: [result_dicts]}
        _pending_download_files: list[dict] = []  # populated by download chain
        
        for source_key, calls in calls_by_source.items():
            logger.info("Executing tool calls", source=source_key, count=len(calls))
            _source_label = CONSORTIUM_REGISTRY.get(source_key, {}).get("display_name") \
                or SERVICE_REGISTRY.get(source_key, {}).get("display_name", source_key)
            _emit_progress(req.request_id, "tools", f"Querying {_source_label}...")
            
            try:
                url = get_service_url(source_key)
            except KeyError:
                logger.error("Unknown source", source=source_key)
                all_results[source_key] = [{
                    "tool": c["tool"],
                    "params": c["params"],
                    "error": f"Unknown source: {source_key}",
                } for c in calls]
                continue
            
            mcp_client = MCPHttpClient(name=source_key, base_url=url)
            source_results = []
            
            try:
                await mcp_client.connect()
                
                for call in calls:
                    tool_name = call["tool"]
                    params = call["params"]

                    # Check for user cancellation before each tool call
                    if _is_cancelled(req.request_id):
                        raise ChatCancelled("tools", f"Cancelled before running {tool_name} on {_source_label}.")

                    # Check for routing errors (e.g. missing parent experiment)
                    if "__routing_error__" in params:
                        error_msg = params.pop("__routing_error__")
                        logger.warning("Skipping tool call due to routing error",
                                      tool=tool_name, error=error_msg)
                        source_results.append({
                            "tool": tool_name,
                            "params": params,
                            "error": error_msg,
                        })
                        continue
                    
                    logger.info("Calling tool", source=source_key, tool=tool_name, params=params)
                    
                    try:
                        result_data = await mcp_client.call_tool(tool_name, **params)
                    except (ConnectionError, TimeoutError, OSError) as _conn_err:
                        # Single retry for transient connection errors
                        logger.warning("Transient error, retrying once",
                                      source=source_key, tool=tool_name, error=str(_conn_err))
                        await asyncio.sleep(2)
                        try:
                            result_data = await mcp_client.call_tool(tool_name, **params)
                        except Exception as _retry_err:
                            logger.error("Retry also failed", source=source_key,
                                        tool=tool_name, error=str(_retry_err))
                            source_results.append({
                                "tool": tool_name,
                                "params": params,
                                "error": f"Connection failed after retry: {_retry_err}",
                            })
                            continue

                    # --- ENCODE search retry: biosample ↔ assay swap on 0 results ---
                    # When a search returns zero results, the term might have been
                    # classified wrong (biosample vs assay).  Try the alternate tool.
                    if source_key == "encode" \
                            and isinstance(result_data, dict) \
                            and result_data.get("total", -1) == 0 \
                            and tool_name in ("search_by_biosample", "search_by_assay"):
                        _swap_tool = None
                        _swap_params: dict = {}

                        if tool_name == "search_by_biosample":
                            # The search_term might actually be an assay name
                            _st = params.get("search_term", "")
                            if _st and _looks_like_assay(_st):
                                _swap_tool = "search_by_assay"
                                _swap_params = {"assay_title": _st}
                                if "organism" in params:
                                    _swap_params["organism"] = params["organism"]
                            elif _st:
                                # Even if it doesn't look like an assay, try anyway
                                _swap_tool = "search_by_assay"
                                _swap_params = {"assay_title": _st}
                                if "organism" in params:
                                    _swap_params["organism"] = params["organism"]

                        elif tool_name == "search_by_assay":
                            # The assay_title might actually be a biosample name
                            _at = params.get("assay_title", "")
                            if _at and not _looks_like_assay(_at):
                                _swap_tool = "search_by_biosample"
                                _swap_params = {"search_term": _at}
                                if "organism" in params:
                                    _swap_params["organism"] = params["organism"]

                        if _swap_tool:
                            logger.warning(
                                "ENCODE search returned 0 results — retrying with alternate tool",
                                original_tool=tool_name, swap_tool=_swap_tool,
                                original_params=params, swap_params=_swap_params,
                            )
                            try:
                                _swap_result = await mcp_client.call_tool(_swap_tool, **_swap_params)
                                if isinstance(_swap_result, dict) and _swap_result.get("total", 0) > 0:
                                    result_data = _swap_result
                                    tool_name = _swap_tool
                                    params = _swap_params
                                    logger.info("Alternate tool returned results",
                                               tool=_swap_tool, total=_swap_result.get("total"))
                                else:
                                    logger.info("Alternate tool also returned 0 results",
                                               tool=_swap_tool)
                            except Exception as _swap_err:
                                logger.warning("Alternate tool call failed",
                                              tool=_swap_tool, error=str(_swap_err))

                    try:
                        source_results.append({
                            "tool": tool_name,
                            "params": params,
                            "data": result_data,
                        })
                        logger.info("Tool call successful", source=source_key, tool=tool_name)

                        # --- Chaining: download after get_file_metadata ---
                        # Trigger when _chain=="download" (auto-generated) OR
                        # when active_skill is download_files and LLM generated
                        # the get_file_metadata tag directly (no _chain key),
                        # OR when the user's message has clear download intent
                        # (e.g. "download ENCFF..." on any ENCODE skill).
                        _msg_lower = req.message.lower() if hasattr(req, 'message') else ""
                        _user_wants_download = any(
                            w in _msg_lower for w in ("download", "grab", "fetch", "save")
                        )
                        _is_download_chain = (
                            call.get("_chain") == "download"
                            or active_skill == "download_files"
                            or _user_wants_download
                        )
                        if _is_download_chain \
                                and tool_name == "get_file_metadata" \
                                and isinstance(result_data, dict):
                            _dl_url = None
                            _file_acc = params.get("file_accession", "")
                            _file_size = result_data.get("file_size")
                            _file_fmt = result_data.get("file_format", "")
                            _file_name = f"{_file_acc}.{_file_fmt}" if _file_acc and _file_fmt else _file_acc

                            # Prefer the cloud_metadata URL (direct S3 link)
                            _cloud = result_data.get("cloud_metadata")
                            if isinstance(_cloud, dict) and _cloud.get("url"):
                                _dl_url = _cloud["url"]
                            # Fallback: construct from href
                            elif result_data.get("href"):
                                _dl_url = f"https://www.encodeproject.org{result_data['href']}"

                            if _dl_url:
                                _dl_file_info = {
                                    "url": _dl_url,
                                    "filename": _file_name,
                                    "size_bytes": _file_size,
                                    "accession": _file_acc,
                                }
                                # Append (not replace) so multi-file downloads accumulate
                                _pending_download_files.append(_dl_file_info)
                                logger.info(
                                    "Download chain: resolved URL from file metadata",
                                    file_accession=_file_acc, url=_dl_url,
                                    size_bytes=_file_size,
                                )
                                # Force download_files skill and approval
                                active_skill = "download_files"
                                needs_approval = True
                                # Build a clean confirmation message listing all pending files
                                _plan_lines = ["**Download Plan:**"]
                                _total_mb = 0
                                for _pf in _pending_download_files:
                                    _sz = _pf.get("size_bytes") or 0
                                    _mb = round(_sz / (1024 * 1024), 1)
                                    _total_mb += _mb
                                    _plan_lines.append(f"- **File:** `{_pf['filename']}` ({_mb} MB)")
                                _plan_lines.append(f"- **Total:** {round(_total_mb, 1)} MB")
                                _plan_lines.append(f"- **Destination:** `data/`\n")
                                _plan_lines.append("Proceed with download?")
                                clean_markdown = "\n".join(_plan_lines)

                        # --- Chaining: follow up find_file with parse/read ---
                        # Works whether _chain was set by auto_calls or needs
                        # to be inferred (e.g. LLM generated the find_file tag directly).
                        if tool_name == "find_file" and isinstance(result_data, dict) \
                                and result_data.get("success") and result_data.get("primary_path"):
                            chain_tool = call.get("_chain")
                            primary_path = result_data["primary_path"]
                            # Infer follow-up tool from filename if not explicitly set
                            if not chain_tool:
                                chain_tool = _pick_file_tool(primary_path)
                            # Prefer work_dir over run_uuid for chaining
                            chain_work_dir = result_data.get("work_dir") or params.get("work_dir")
                            chain_uuid = result_data.get("run_uuid") or params.get("run_uuid")
                            if chain_work_dir or chain_uuid:
                                logger.info("Chaining find_file → parse/read",
                                           chain_tool=chain_tool, file_path=primary_path)
                                try:
                                    chain_params: dict = {"file_path": primary_path}
                                    if chain_work_dir:
                                        chain_params["work_dir"] = chain_work_dir
                                    elif chain_uuid:
                                        chain_params["run_uuid"] = chain_uuid
                                    if chain_tool == "parse_csv_file":
                                        chain_params["max_rows"] = 100
                                    elif chain_tool == "parse_bed_file":
                                        chain_params["max_records"] = 100
                                    elif chain_tool == "read_file_content":
                                        chain_params["preview_lines"] = 50
                                    chain_result = await mcp_client.call_tool(chain_tool, **chain_params)
                                    source_results.append({
                                        "tool": chain_tool,
                                        "params": chain_params,
                                        "data": chain_result,
                                    })
                                    logger.info("Chain call successful", tool=chain_tool)
                                except Exception as ce:
                                    logger.error("Chain call failed", tool=chain_tool, error=str(ce))
                                    source_results.append({
                                        "tool": chain_tool,
                                        "params": chain_params,
                                        "error": str(ce),
                                    })

                    except Exception as e:
                        logger.error("Tool call failed", source=source_key, tool=tool_name, error=str(e))
                        source_results.append({
                            "tool": tool_name,
                            "params": params,
                            "error": str(e),
                        })
            except Exception as e:
                logger.error("Failed to connect to source", source=source_key, error=str(e))
                source_results = [{
                    "tool": c["tool"],
                    "params": c["params"],
                    "error": f"Connection failed: {e}",
                } for c in calls]
            finally:
                await mcp_client.disconnect()
            
            all_results[source_key] = source_results
        
        # 6c-auto. Auto-fetch missing ENCFF files for multi-file downloads.
        # The LLM often emits only ONE get_file_metadata tag when the user
        # requests multiple files.  Detect missing accessions and fetch them.
        if _pending_download_files:
            _msg_text = req.message if hasattr(req, 'message') else ""
            _requested_encffs = set(
                m.upper() for m in re.findall(r'ENCFF[A-Z0-9]{6}', _msg_text, re.IGNORECASE)
            )
            _fetched_encffs = set(
                f.get("accession", "").upper() for f in _pending_download_files
            )
            _missing_encffs = _requested_encffs - _fetched_encffs
            if _missing_encffs:
                # We need the experiment accession for the API call
                _exp_match = re.search(r'(ENCSR[A-Z0-9]{6})', _msg_text, re.IGNORECASE)
                _exp_acc = _exp_match.group(1) if _exp_match else None
                if _exp_acc:
                    logger.info("Auto-fetching missing ENCFF files for download",
                               missing=sorted(_missing_encffs), experiment=_exp_acc)
                    try:
                        _autofetch_url = get_service_url("encode")
                        _autofetch_mcp = MCPHttpClient(name="encode", base_url=_autofetch_url)
                        await _autofetch_mcp.connect()
                        for _miss_acc in sorted(_missing_encffs):
                            try:
                                _miss_result = await _autofetch_mcp.call_tool(
                                    "get_file_metadata",
                                    accession=_exp_acc,
                                    file_accession=_miss_acc,
                                )
                                if isinstance(_miss_result, dict):
                                    _miss_dl_url = None
                                    _miss_cloud = _miss_result.get("cloud_metadata")
                                    if isinstance(_miss_cloud, dict) and _miss_cloud.get("url"):
                                        _miss_dl_url = _miss_cloud["url"]
                                    elif _miss_result.get("href"):
                                        _miss_dl_url = f"https://www.encodeproject.org{_miss_result['href']}"
                                    if _miss_dl_url:
                                        _miss_fmt = _miss_result.get("file_format", "")
                                        _miss_fname = f"{_miss_acc}.{_miss_fmt}" if _miss_fmt else _miss_acc
                                        _pending_download_files.append({
                                            "url": _miss_dl_url,
                                            "filename": _miss_fname,
                                            "size_bytes": _miss_result.get("file_size"),
                                            "accession": _miss_acc,
                                        })
                                        logger.info("Auto-fetched missing file metadata",
                                                   accession=_miss_acc, url=_miss_dl_url)
                            except Exception as _mfe:
                                logger.warning("Failed to auto-fetch file metadata",
                                              accession=_miss_acc, error=str(_mfe))
                        await _autofetch_mcp.disconnect()
                        # Rebuild the Download Plan to include all files
                        _plan_lines = ["**Download Plan:**"]
                        _total_mb = 0
                        for _pf in _pending_download_files:
                            _sz = _pf.get("size_bytes") or 0
                            _mb = round(_sz / (1024 * 1024), 1)
                            _total_mb += _mb
                            _plan_lines.append(f"- **File:** `{_pf['filename']}` ({_mb} MB)")
                        _plan_lines.append(f"- **Total:** {round(_total_mb, 1)} MB")
                        _plan_lines.append(f"- **Destination:** `data/`\n")
                        _plan_lines.append("Proceed with download?")
                        clean_markdown = "\n".join(_plan_lines)
                    except Exception as _conn_e:
                        logger.warning("Failed to connect for auto-fetch",
                                      error=str(_conn_e))

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
                        _prov_entry["rows"] = _data.get("total") or _data.get("count") or len(_data.get("experiments", _data.get("files", [])))
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
            _browsing_tools = {"list_job_files", "categorize_job_files"}
            _all_tools_used = set()
            _has_download_chain = False
            for _src_results in all_results.values():
                for _r in _src_results:
                    _all_tools_used.add(_r.get("tool", ""))
                    if _r.get("_chain") == "download":
                        _has_download_chain = True
            _is_browsing = bool(_all_tools_used) and _all_tools_used <= _browsing_tools

            # Also skip second-pass for download workflows — the metadata is
            # used to build the approval gate, not to summarise for the user.
            _is_download = _has_download_chain or active_skill == "download_files"

            if _is_browsing and has_real_data and formatted_data.strip():
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
            else:
                # All errors or empty — show errors directly, no second pass
                clean_markdown += formatted_data
        
        # 7. Extract any parsed DataFrames from tool results so the UI
        #    can render them interactively with st.dataframe.
        #    This covers:
        #      a) File-parsing tools (parse_csv_file, parse_bed_file)
        #      b) Search/list tools that return a plain list of dicts
        #         (search_by_biosample, search_by_target, search_by_organism,
        #          list_experiments) — stored BEFORE markdown truncation so the
        #         full result set is always available in the UI regardless of
        #         how many rows the LLM saw.
        _SEARCH_TOOLS = {
            "search_by_biosample", "search_by_target",
            "search_by_organism", "search_by_assay", "list_experiments",
        }
        _FILE_TOOLS = {"get_files_by_type"}
        _embedded_dataframes = {}
        for _src_key, _src_results in all_results.items():
            _reg_entry = (SERVICE_REGISTRY.get(_src_key)
                         or CONSORTIUM_REGISTRY.get(_src_key)
                         or {})
            _table_cols = _reg_entry.get("table_columns", [])  # [(header, field_key), ...]
            for _r in _src_results:
                _tool = _r.get("tool", "")
                if _tool in ("parse_csv_file", "parse_bed_file") and "data" in _r:
                    _rd = _r["data"]
                    if isinstance(_rd, dict) and _rd.get("data"):
                        _fname = _rd.get("file_path") or _r["params"].get("file_path", "unknown")
                        _fname = _fname.rsplit("/", 1)[-1] if "/" in _fname else _fname
                        _embedded_dataframes[_fname] = {
                            "columns": _rd.get("columns", []),
                            "data": _rd["data"],
                            "row_count": _rd.get("row_count", len(_rd["data"])),
                            "metadata": _rd.get("metadata", {}),
                        }
                elif _tool in _FILE_TOOLS and "data" in _r:
                    # get_files_by_type returns {"bam": [...], "fastq": [...], ...}
                    # Store ONE dataframe per file type so follow-up queries like
                    # "which bam files are methylated reads?" inject ONLY the bam
                    # rows — not a merged table of all 69+ files.
                    _rd = _r["data"]
                    if isinstance(_rd, dict):
                        _exp_acc = _r.get("params", {}).get("accession", "files")
                        _cols = ["Accession", "Output Type", "Replicate", "Size", "Status"]
                        # Detect which file type(s) the user asked about so we
                        # only show those tables prominently in the UI.  All
                        # tables are still stored for follow-up queries.
                        _msg_lower_ft = req.message.lower()
                        _asked_file_types: set[str] = set()
                        for _candidate_ft in _rd.keys():
                            if _candidate_ft.lower().split()[0] in _msg_lower_ft:
                                _asked_file_types.add(_candidate_ft)
                        for _ftype, _flist in _rd.items():
                            if not isinstance(_flist, list) or not _flist:
                                continue
                            _rows = []
                            for _fobj in _flist:
                                if isinstance(_fobj, dict):
                                    _rows.append({
                                        "Accession": _fobj.get("accession", ""),
                                        "Output Type": _fobj.get("output_type", ""),
                                        "Replicate": _fobj.get("biological_replicates_formatted")
                                                     or ", ".join(
                                                         f"Rep {x}" for x in
                                                         _fobj.get("biological_replicates", [])
                                                     ),
                                        "Size": _fobj.get("file_size", ""),
                                        "Status": _fobj.get("status", ""),
                                    })
                            if _rows:
                                _df_label = f"{_exp_acc} {_ftype} files ({len(_rows)})"
                                # Mark as visible if user asked about this type
                                # (or all visible if no specific type mentioned).
                                _is_visible = (not _asked_file_types
                                               or _ftype in _asked_file_types)
                                _embedded_dataframes[_df_label] = {
                                    "columns": _cols,
                                    "data": _rows,
                                    "row_count": len(_rows),
                                    "metadata": {
                                        "file_type": _ftype,
                                        "accession": _exp_acc,
                                        "visible": _is_visible,
                                    },
                                }
                elif _tool in _SEARCH_TOOLS and "data" in _r:
                    _rd = _r["data"]
                    # Normalise to a flat list of experiment dicts.
                    # search_by_assay returns a dict with "experiments",
                    # "human", or "mouse" sub-lists; other search tools
                    # return a plain list directly.
                    _experiment_list: list[dict] = []
                    if isinstance(_rd, list):
                        _experiment_list = _rd
                    elif isinstance(_rd, dict):
                        if "experiments" in _rd and isinstance(_rd["experiments"], list):
                            _experiment_list = _rd["experiments"]
                        else:
                            # Merge human + mouse sub-lists (search_by_assay
                            # without organism filter)
                            for _sub_key in ("human", "mouse"):
                                _sub = _rd.get(_sub_key)
                                if isinstance(_sub, list):
                                    _experiment_list.extend(_sub)

                    if _experiment_list and isinstance(_experiment_list[0], dict):
                        # Derive column list from registry table_columns or first row keys
                        if _table_cols:
                            _cols = [h for h, _ in _table_cols]
                            _rows = [
                                {h: (item.get(k) if not isinstance(item.get(k), list)
                                     else ", ".join(str(v) for v in item.get(k, [])))
                                 for h, k in _table_cols}
                                for item in _experiment_list
                            ]
                        else:
                            _cols = list(_experiment_list[0].keys())
                            _rows = _experiment_list
                        _params = _r.get("params", {})
                        _label = _tool.replace("_", " ").title()
                        if _params.get("search_term"):
                            _label = _params["search_term"]
                        elif _params.get("assay_title"):
                            _label = _params["assay_title"]
                        elif _params.get("target"):
                            _label = _params["target"]
                        elif _params.get("organism"):
                            _label = _params["organism"]
                        _fname = f"{_label} ({len(_experiment_list)} results)"
                        _embedded_dataframes[_fname] = {
                            "columns": _cols,
                            "data": _rows,
                            "row_count": len(_experiment_list),
                            "metadata": {"visible": True},
                        }

        # 8. Save AGENT_PLAN (The Text)
        # Status is DONE because the text itself is just informational. 
        # The flow control happens in the gate block below.
        _emit_progress(req.request_id, "done", "Complete")
        # Merge any dataframes produced by _inject_job_context (server-side
        # filtered follow-up results) into the embedded dataframes dict so
        # the UI renders them as interactive tables.
        if _injected_dfs:
            _embedded_dataframes.update(_injected_dfs)

        # Assign sequential DF IDs (DF1, DF2, ...) to VISIBLE dataframes only.
        # Non-visible DFs (e.g. tar/bed when user asked about bam) don't get
        # numbered IDs — they appear collapsed inside raw query details.
        # Scan history to find the highest existing ID, then continue from there.
        _next_df_id = 1
        for _hblk in history_blocks[:-1]:  # exclude the current USER_MESSAGE
            if _hblk.type == "AGENT_PLAN":
                _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                for _dfd in _hblk_dfs.values():
                    _existing_id = _dfd.get("metadata", {}).get("df_id")
                    if isinstance(_existing_id, int):
                        _next_df_id = max(_next_df_id, _existing_id + 1)
        for _df_data in _embedded_dataframes.values():
            _meta = _df_data.setdefault("metadata", {})
            if _meta.get("df_id"):
                continue  # already has an ID (e.g. injected df)
            if _meta.get("visible", True):  # default True for DFs without flag
                _meta["df_id"] = _next_df_id
                _next_df_id += 1
            # Non-visible DFs intentionally get no df_id

        # Update _conv_state with the newly-embedded DFs so the cached state
        # saved into this block is accurate for future requests' fast path.
        if _embedded_dataframes:
            _max_new_id = 0
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
        if _provenance:
            _plan_payload["_provenance"] = _provenance
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
                extracted_params = await extract_job_parameters_from_conversation(session, req.project_id)
            
            gate_block = _create_block_internal(
                session,
                req.project_id,
                "APPROVAL_GATE",
                {
                    "label": "Do you authorize the Agent to proceed with this plan?",
                    "extracted_params": extracted_params,
                    "gate_action": gate_action,
                    "attempt_number": 1,
                    "rejection_history": [],
                    "skill": active_skill,
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


