"""
Launchpad: FastAPI application for Dogme/Nextflow job execution.
Receives job submissions from Cortex and manages pipeline execution.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Path as FastAPIPath, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
from launchpad.config import (
    AGOUTIC_DATA,
    LAUNCHPAD_LOGS_DIR,
    JobStatus,
    MAX_CONCURRENT_JOBS,
    DEFAULT_MAX_GPU_TASKS,
    JOB_POLL_INTERVAL,
    REFERENCE_GENOMES,
    INTERNAL_API_SECRET,
)
from launchpad.db import (
    init_db,
    SessionLocal,
    create_job,
    get_job,
    update_job_status,
    add_log_entry,
    get_job_logs,
    job_to_dict,
)
from launchpad.models import DogmeJob, SSHProfile
from launchpad.nextflow_executor import NextflowExecutor
from launchpad.schemas import (
    SubmitJobRequest,
    StageRemoteSampleRequest,
    StageRemoteSampleResponse,
    JobStatusResponse,
    JobStatusExtendedResponse,
    JobDetailsResponse,
    JobSubmitResponse,
    JobListResponse,
    JobLogsResponse,
    LogEntry,
    HealthCheckResponse,
    SSHProfileCreate,
    SSHProfileUpdate,
    SSHProfileOut,
    SSHProfileTestResult,
    SSHProfileAuthSessionResult,
)
from launchpad.backends import get_backend, SubmitParams
from launchpad.backends.local_auth_sessions import get_local_auth_session_manager
from launchpad.backends.ssh_manager import SSHProfileData
from launchpad.script_execution import (
    normalize_script_args,
    resolve_allowlisted_script,
    validate_script_working_directory,
)

# --- LOGGING ---
setup_logging("launchpad-rest")
logger = get_logger(__name__)

# --- APP SETUP ---
app = FastAPI(
    title="AGOUTIC Launchpad",
    description="Dogme/Nextflow Job Execution Engine",
    version="0.3.0",
)

# Add request logging middleware FIRST (outermost)
app.add_middleware(RequestLoggingMiddleware)


def _effective_job_work_directory(job) -> str | None:
    result_destination = (getattr(job, "result_destination", "") or "").strip().lower()
    local_work_dir = getattr(job, "nextflow_work_dir", None)
    remote_work_dir = getattr(job, "remote_work_dir", None)
    if result_destination in {"local", "both"} and local_work_dir:
        return local_work_dir
    return remote_work_dir or local_work_dir


def _job_timing_payload(job) -> dict[str, str | int | None]:
    submitted_at = job.submitted_at.isoformat() if job.submitted_at else None
    started_at = job.started_at.isoformat() if job.started_at else None
    completed_at = job.completed_at.isoformat() if job.completed_at else None

    duration_seconds = None
    start_dt = job.started_at or job.submitted_at
    end_dt = job.completed_at or datetime.utcnow()
    if start_dt:
        try:
            duration_seconds = max(0, int((end_dt - start_dt).total_seconds()))
        except Exception:
            duration_seconds = None

    return {
        "submitted_at": submitted_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
    }

# Internal API secret validation middleware
class InternalSecretMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate the X-Internal-Secret header on all requests.
    Only /health is exempted.
    """
    
    EXEMPT_PATHS = ["/health", "/docs", "/openapi.json"]
    
    async def dispatch(self, request: Request, call_next):
        # Check if path is exempted
        if any(request.url.path.startswith(path) for path in self.EXEMPT_PATHS):
            return await call_next(request)
        
        # If INTERNAL_API_SECRET is not configured, allow all (dev mode)
        if not INTERNAL_API_SECRET:
            logger.warning("INTERNAL_API_SECRET not set - Launchpad is unprotected")
            return await call_next(request)
        
        # Check for secret header
        provided_secret = request.headers.get("X-Internal-Secret")
        
        if not provided_secret:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing X-Internal-Secret header"}
            )
        
        if provided_secret != INTERNAL_API_SECRET:
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid X-Internal-Secret"}
            )
        
        # Secret is valid, continue
        return await call_next(request)

# Add internal secret middleware FIRST
app.add_middleware(InternalSecretMiddleware)

# Enable CORS for communication with Cortex
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GLOBALS ---
executor = NextflowExecutor()
job_monitors: dict = {}  # Maps run_uuid -> monitoring task
script_processes: dict[str, asyncio.subprocess.Process] = {}


def _describe_exception(exc: Exception) -> str:
    """Return a stable, non-empty exception string for API error details."""
    msg = str(exc).strip()
    if msg:
        return msg
    return f"{type(exc).__name__}: {exc!r}"


def _read_log_tail(path: str, *, max_chars: int = 4000) -> str:
    """Read a safe tail snippet from a log file for status/error surfacing."""
    try:
        text = Path(path).read_text(errors="replace")
    except Exception:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


async def _monitor_script_job(
    run_uuid: str,
    process: asyncio.subprocess.Process,
    stdout_log_path: str,
    stderr_log_path: str,
) -> None:
    """Monitor a standalone local script process and sync final status to DB."""
    session = SessionLocal()
    try:
        return_code = await process.wait()
        stdout_tail = _read_log_tail(stdout_log_path)
        stderr_tail = _read_log_tail(stderr_log_path)

        final_status = JobStatus.COMPLETED if return_code == 0 else JobStatus.FAILED
        progress = 100 if return_code == 0 else 0
        error_message = None if return_code == 0 else (stderr_tail.strip() or f"Script exited with code {return_code}")

        await update_job_status(
            session,
            run_uuid,
            final_status,
            progress=progress,
            error_message=error_message,
        )

        job = await get_job(session, run_uuid)
        if job:
            job.completed_at = datetime.utcnow()
            job.run_stage = "SCRIPT_COMPLETED" if return_code == 0 else "SCRIPT_FAILED"
            job.report_json = json.dumps(
                {
                    "run_type": "script",
                    "return_code": return_code,
                    "stdout_log": stdout_log_path,
                    "stderr_log": stderr_log_path,
                }
            )
            await session.commit()

        await add_log_entry(
            session,
            run_uuid,
            "INFO" if return_code == 0 else "ERROR",
            f"Standalone script finished with exit code {return_code}",
            source="script-monitor",
        )

        if stdout_tail.strip():
            await add_log_entry(
                session,
                run_uuid,
                "INFO",
                f"stdout (tail):\n{stdout_tail}",
                source="script-stdout",
            )

        if stderr_tail.strip():
            await add_log_entry(
                session,
                run_uuid,
                "ERROR" if return_code != 0 else "INFO",
                f"stderr (tail):\n{stderr_tail}",
                source="script-stderr",
            )
    except Exception as exc:
        logger.error("Script monitor failed", run_uuid=run_uuid, error=_describe_exception(exc), exc_info=True)
    finally:
        script_processes.pop(run_uuid, None)
        job_monitors.pop(run_uuid, None)
        await session.close()

# --- LIFECYCLE ---
@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    from common.database import is_sqlite
    if is_sqlite():
        await init_db()  # Auto-create tables for local SQLite dev
    # For Postgres, tables are managed by Alembic migrations
    logger.info("Launchpad initialized", max_concurrent_jobs=MAX_CONCURRENT_JOBS, poll_interval=JOB_POLL_INTERVAL)

@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    # Cancel all monitoring tasks
    for task in job_monitors.values():
        if isinstance(task, asyncio.Task):
            task.cancel()
    await get_local_auth_session_manager().close_all()
    logger.info("Launchpad shutdown complete")

# --- MONITORING BACKGROUND TASK ---
async def monitor_job(run_uuid: str, work_dir):
    """Background task to monitor a job and update status."""
    session = SessionLocal()
    
    try:
        while True:
            try:
                # Check current status
                status_info = await executor.check_status(run_uuid, work_dir)
                job_status = status_info["status"]
                progress = status_info["progress_percent"]
                message = status_info["message"]
                
                # Update database
                await update_job_status(
                    session,
                    run_uuid,
                    job_status,
                    progress=progress,
                )
                
                # Log the update
                await add_log_entry(
                    session,
                    run_uuid,
                    "INFO",
                    f"Status: {job_status} ({progress}%)",
                    source="monitor",
                )
                
                # If job finished, get results and exit loop
                if job_status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    if job_status == JobStatus.COMPLETED:
                        results = await executor.get_results(run_uuid, work_dir)
                        job = await get_job(session, run_uuid)
                        if job:
                            import json
                            job.report_json = json.dumps(results)
                            job.completed_at = datetime.utcnow()
                            await session.commit()
                        
                        await add_log_entry(
                            session,
                            run_uuid,
                            "INFO",
                            "Job completed successfully",
                            source="monitor",
                        )
                    else:
                        await add_log_entry(
                            session,
                            run_uuid,
                            "ERROR",
                            message,
                            source="monitor",
                        )
                    
                    break
                
                # Wait before next poll
                await asyncio.sleep(JOB_POLL_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info("Monitoring stopped", run_uuid=run_uuid)
                break
            except Exception as e:
                await add_log_entry(
                    session,
                    run_uuid,
                    "ERROR",
                    f"Monitoring error: {str(e)}",
                    source="monitor",
                )
                await asyncio.sleep(JOB_POLL_INTERVAL)
    
    finally:
        await session.close()
        if run_uuid in job_monitors:
            del job_monitors[run_uuid]

# --- HEALTH CHECK ---
@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Health check endpoint."""
    session = SessionLocal()
    try:
        # Count running jobs
        result = await session.execute(
            select(DogmeJob).where(DogmeJob.status == JobStatus.RUNNING)
        )
        running_jobs = len(result.scalars().all())
        
        return {
            "status": "ok",
            "version": "0.3.0",
            "running_jobs": running_jobs,
            "database_ok": True,
        }
    except Exception as e:
        return {
            "status": "error",
            "version": "0.3.0",
            "running_jobs": 0,
            "database_ok": False,
            "error": str(e)
        }


# --- GENOME LIST ENDPOINT ---
@app.get("/genomes")
async def list_genomes():
    """List available reference genomes."""
    return {
        "genomes": list(REFERENCE_GENOMES.keys()),
        "count": len(REFERENCE_GENOMES)
    }


@app.post("/remote/stage", response_model=StageRemoteSampleResponse)
async def stage_remote_sample(req: StageRemoteSampleRequest):
    """Stage references and input data remotely without submitting a scheduler job."""
    backend = get_backend("slurm")
    try:
        return await backend.stage_remote_sample(
            SubmitParams(
                project_id=req.project_id,
                user_id=req.user_id,
                username=req.username,
                project_slug=req.project_slug,
                sample_name=req.sample_name,
                mode=req.mode,
                input_directory=req.input_directory,
                reference_genome=req.reference_genome,
                ssh_profile_id=req.ssh_profile_id,
                remote_base_path=req.remote_base_path,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=_describe_exception(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_describe_exception(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
    except Exception as exc:
        logger.exception("Remote stage failed", sample_name=req.sample_name, ssh_profile_id=req.ssh_profile_id)
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc


@app.get("/remote/files")
async def list_remote_files(
    user_id: str = Query(..., min_length=1),
    ssh_profile_id: str = Query(..., min_length=1),
    path: str | None = Query(None),
):
    """List remote files for an SSH profile, defaulting to its configured remote base path."""
    backend = get_backend("slurm")
    return await backend.list_remote_files(
        user_id=user_id,
        ssh_profile_id=ssh_profile_id,
        path=path,
    )

# --- JOB SUBMISSION ---
@app.post("/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(req: SubmitJobRequest):
    """
    Submit a new Dogme/Nextflow job.
    
    Receives request from Cortex agent with job parameters.
    Returns job UUID for tracking.
    """
    session = SessionLocal()
    
    try:
        # Generate unique run ID
        run_uuid = str(uuid.uuid4())
        
        # Create job record in database
        job = await create_job(
            session,
            run_uuid=run_uuid,
            project_id=req.project_id,
            sample_name=req.sample_name,
            mode=req.mode,
            input_directory=req.input_directory,
            reference_genome=req.reference_genome,
            modifications=req.modifications,
            parent_block_id=req.parent_block_id,
            user_id=req.user_id,
        )
        run_type = (req.run_type or "dogme").strip().lower()
        job.execution_mode = req.execution_mode
        job.ssh_profile_id = req.ssh_profile_id
        job.slurm_account = req.slurm_account
        job.slurm_partition = req.slurm_partition
        job.slurm_cpus = req.slurm_cpus
        job.slurm_memory_gb = req.slurm_memory_gb
        job.slurm_walltime = req.slurm_walltime
        job.slurm_gpus = req.slurm_gpus
        job.slurm_gpu_type = req.slurm_gpu_type
        job.result_destination = req.result_destination
        job.cache_preflight_json = req.cache_preflight
        await session.commit()
        
        # Log submission
        await add_log_entry(
            session,
            run_uuid,
            "INFO",
            f"Job submitted: {req.sample_name} ({req.mode} mode, run_type={run_type})",
            source="api",
        )
        
        # Submit to selected execution backend
        try:
            # Validate and normalize execution_mode
            exec_mode = (req.execution_mode or "local").strip().lower()
            if exec_mode not in ("local", "slurm"):
                logger.warning(
                    "Invalid execution_mode, defaulting to local",
                    provided_mode=exec_mode,
                    run_uuid=run_uuid,
                    sample_name=req.sample_name,
                )
                exec_mode = "local"
            
            if run_type == "script":
                if exec_mode != "local":
                    raise ValueError("Standalone script execution only supports local execution_mode")

                resolved_script = resolve_allowlisted_script(
                    script_id=req.script_id,
                    script_path=req.script_path,
                )
                script_args = normalize_script_args(req.script_args)
                script_cwd = validate_script_working_directory(req.script_working_directory)
                if script_cwd is None:
                    script_cwd = resolved_script.script_path.parent

                stdout_log = (LAUNCHPAD_LOGS_DIR / f"{run_uuid}.script.stdout.log").resolve()
                stderr_log = (LAUNCHPAD_LOGS_DIR / f"{run_uuid}.script.stderr.log").resolve()

                with open(stdout_log, "ab") as stdout_handle, open(stderr_log, "ab") as stderr_handle:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(resolved_script.script_path),
                        *script_args,
                        cwd=str(script_cwd),
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                    )

                script_processes[run_uuid] = process

                job.status = JobStatus.RUNNING
                job.started_at = datetime.utcnow()
                job.run_stage = "SCRIPT_RUNNING"
                job.nextflow_process_id = process.pid
                job.nextflow_work_dir = str(script_cwd)
                job.log_file = str(stdout_log)
                job.stderr_log = str(stderr_log)
                job.report_json = json.dumps(
                    {
                        "run_type": "script",
                        "script_id": resolved_script.script_id,
                        "script_path": str(resolved_script.script_path),
                        "script_args": script_args,
                        "script_working_directory": str(script_cwd),
                    }
                )
                await session.commit()

                await add_log_entry(
                    session,
                    run_uuid,
                    "INFO",
                    f"Started standalone script {resolved_script.script_path.name} (pid={process.pid})",
                    source="api",
                )

                monitor_task = asyncio.create_task(
                    _monitor_script_job(
                        run_uuid,
                        process,
                        str(stdout_log),
                        str(stderr_log),
                    )
                )
                job_monitors[run_uuid] = monitor_task

                work_directory = str(script_cwd)
                response_status = JobStatus.RUNNING
            elif exec_mode == "slurm":
                workflow_number = None
                local_workflow_dir = None
                if req.resume_from_dir:
                    local_workflow_dir = Path(req.resume_from_dir)
                    if local_workflow_dir.name.startswith("workflow"):
                        try:
                            workflow_number = int(local_workflow_dir.name[len("workflow"):])
                        except ValueError:
                            workflow_number = None

                if workflow_number is None:
                    user_key = req.username or req.user_id or "user"
                    project_key = req.project_slug or req.project_id
                    project_dir = AGOUTIC_DATA / "users" / user_key / project_key
                    project_dir.mkdir(parents=True, exist_ok=True)
                    workflow_number = NextflowExecutor._next_workflow_number(project_dir)
                    local_workflow_dir = project_dir / f"workflow{workflow_number}"
                    local_workflow_dir.mkdir(parents=True, exist_ok=True)

                job.nextflow_work_dir = str(local_workflow_dir) if local_workflow_dir else None
                await session.commit()

                backend = get_backend("slurm")
                await backend.submit(
                    run_uuid,
                    SubmitParams(
                        project_id=req.project_id,
                        user_id=req.user_id,
                        username=req.username,
                        project_slug=req.project_slug,
                        sample_name=req.sample_name,
                        mode=req.mode,
                        input_type=req.input_type,
                        input_directory=req.input_directory,
                        reference_genome=req.reference_genome,
                        modifications=req.modifications,
                        entry_point=req.entry_point,
                        modkit_filter_threshold=req.modkit_filter_threshold,
                        min_cov=req.min_cov,
                        per_mod=req.per_mod,
                        accuracy=req.accuracy,
                        max_gpu_tasks=req.max_gpu_tasks or DEFAULT_MAX_GPU_TASKS,
                        resume_from_dir=req.resume_from_dir,
                        parent_block_id=req.parent_block_id,
                        ssh_profile_id=req.ssh_profile_id,
                        slurm_account=req.slurm_account,
                        slurm_partition=req.slurm_partition,
                        slurm_cpus=req.slurm_cpus,
                        slurm_memory_gb=req.slurm_memory_gb,
                        slurm_walltime=req.slurm_walltime,
                        slurm_gpus=req.slurm_gpus,
                        slurm_gpu_type=req.slurm_gpu_type,
                        remote_base_path=req.remote_base_path,
                        workflow_number=workflow_number,
                        staged_remote_input_path=req.staged_remote_input_path,
                        cache_preflight=req.cache_preflight,
                        result_destination=req.result_destination or "local",
                    ),
                )
                await session.refresh(job)
                job.started_at = datetime.utcnow()
                await session.commit()
                work_directory = _effective_job_work_directory(job) or ""
                response_status = job.status or JobStatus.PENDING
                logger.info("Job submitted to SLURM backend", run_uuid=run_uuid,
                           sample_name=req.sample_name, remote_work=work_directory)
            else:
                # Local execution
                logger.info("Using local NextflowExecutor", run_uuid=run_uuid,
                           sample_name=req.sample_name, exec_mode=exec_mode)
                run_uuid_returned, work_dir = await executor.submit_job(
                    run_uuid=run_uuid,
                    sample_name=req.sample_name,
                    mode=req.mode,
                    input_type=req.input_type,
                    input_dir=req.input_directory,
                    reference_genome=req.reference_genome,  # Now normalized to list by validator
                    modifications=req.modifications,
                    entry_point=req.entry_point,
                    modkit_filter_threshold=req.modkit_filter_threshold,
                    min_cov=req.min_cov,
                    per_mod=req.per_mod,
                    accuracy=req.accuracy,
                    max_gpu_tasks=req.max_gpu_tasks or DEFAULT_MAX_GPU_TASKS,
                    user_id=req.user_id,
                    project_id=req.project_id,
                    username=req.username,
                    project_slug=req.project_slug,
                    resume_from_dir=req.resume_from_dir,
                )
                
                # Update job with work directory info
                job.nextflow_work_dir = str(work_dir)
                job.status = JobStatus.RUNNING
                job.started_at = datetime.utcnow()
                # Persist the Nextflow process PID for cancellation support
                _pid_file = work_dir / ".nextflow_pid"
                if _pid_file.exists():
                    try:
                        job.nextflow_process_id = int(_pid_file.read_text().strip())
                    except (ValueError, OSError):
                        pass
                await session.commit()
                
                # Start background monitoring task
                monitor_task = asyncio.create_task(monitor_job(run_uuid, work_dir))
                job_monitors[run_uuid] = monitor_task
                work_directory = str(work_dir)
                response_status = JobStatus.RUNNING
            
            await add_log_entry(
                session,
                run_uuid,
                "INFO",
                f"{req.execution_mode.upper()} submission completed successfully",
                source="api",
            )
            
            return {
                "run_uuid": run_uuid,
                "sample_name": req.sample_name,
                "status": response_status,
                "work_directory": work_directory,
                "cache_actions": {
                    "reference_status": job.reference_cache_status,
                    "data_status": job.data_cache_status,
                    "reference_cache_path": job.reference_cache_path,
                    "data_cache_path": job.data_cache_path,
                    "cache_preflight": job.cache_preflight_json,
                },
            }
        
        except Exception as e:
            # Job submission failed
            import traceback
            error_trace = traceback.format_exc()
            error_detail = _describe_exception(e)
            logger.error("Nextflow submission error", run_uuid=run_uuid, error=error_detail, exc_info=True)
            
            job.status = JobStatus.FAILED
            job.error_message = error_detail
            await session.commit()
            
            await add_log_entry(
                session,
                run_uuid,
                "ERROR",
                f"Failed to submit to Nextflow: {error_detail}",
                source="api",
            )
            
            raise HTTPException(
                status_code=500,
                detail=f"Failed to submit job: {error_detail}"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_detail = _describe_exception(e)
        logger.error("Submit endpoint error", error=error_detail, exc_info=True)
        raise HTTPException(status_code=500, detail=error_detail)
    finally:
        await session.close()

# --- JOB STATUS ---
@app.get("/jobs/{run_uuid}", response_model=JobDetailsResponse)
async def get_job_details(run_uuid: str = FastAPIPath(..., min_length=1)):
    """Get detailed status of a specific job."""
    session = SessionLocal()
    
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return JobDetailsResponse(**job_to_dict(job))
    
    finally:
        await session.close()

@app.get("/jobs/{run_uuid}/status", response_model=JobStatusExtendedResponse)
async def get_job_status(run_uuid: str = FastAPIPath(..., min_length=1)):
    """Get quick status of a job."""
    session = SessionLocal()
    
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if (job.run_stage or "").startswith("SCRIPT_"):
            return {
                "run_uuid": run_uuid,
                "status": job.status,
                "progress_percent": 100 if job.status == JobStatus.COMPLETED else (50 if job.status == JobStatus.RUNNING else 0),
                "message": job.error_message or f"Script job {str(job.status).lower()}.",
                "tasks": {},
                "execution_mode": "local",
                "run_stage": job.run_stage,
                "work_directory": job.nextflow_work_dir,
                **_job_timing_payload(job),
            }
        
        terminal_statuses = (JobStatus.DELETED, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        pending_local_result_sync = (
            job.execution_mode == "slurm"
            and job.status == JobStatus.COMPLETED
            and (job.result_destination or "").strip().lower() in {"local", "both"}
            and (job.transfer_state or "") != "outputs_downloaded"
        )
        if job.status in terminal_statuses and not pending_local_result_sync:
            work_directory = _effective_job_work_directory(job)
            base_payload = {
                "run_uuid": run_uuid,
                "status": job.status,
                "progress_percent": 100 if job.status == JobStatus.COMPLETED else 0,
                "message": job.error_message or f"Job {job.status.lower()}.",
                "tasks": {},
                "work_directory": work_directory,
                **_job_timing_payload(job),
            }
            if job.execution_mode == "slurm":
                base_payload.update(
                    {
                        "execution_mode": "slurm",
                        "run_stage": job.run_stage,
                        "slurm_job_id": job.slurm_job_id,
                        "slurm_state": job.slurm_state,
                        "transfer_state": job.transfer_state,
                        "result_destination": job.result_destination,
                    }
                )
            return base_payload
        
        if job.execution_mode == "slurm":
            backend = get_backend("slurm")
            status_data = await backend.check_status(run_uuid)
            return {
                "run_uuid": run_uuid,
                "status": status_data.status,
                "progress_percent": status_data.progress_percent if status_data.progress_percent is not None else (100 if status_data.status == JobStatus.COMPLETED else job.progress_percent),
                "message": status_data.message or job.error_message or f"Status: {job.status}",
                "tasks": status_data.tasks or {},
                "execution_mode": status_data.execution_mode,
                "run_stage": status_data.run_stage,
                "slurm_job_id": status_data.slurm_job_id,
                "slurm_state": status_data.slurm_state,
                "transfer_state": status_data.transfer_state,
                "result_destination": status_data.result_destination,
                "ssh_profile_nickname": status_data.ssh_profile_nickname,
                "work_directory": status_data.work_directory,
                **_job_timing_payload(job),
            }

        # Get detailed status including tasks from check_status
        executor = NextflowExecutor()
        # Use the actual work directory stored in DB (may be jailed path)
        work_dir = Path(job.nextflow_work_dir) if job.nextflow_work_dir else executor.work_dir / run_uuid
        status_data = await executor.check_status(run_uuid, work_dir)
        
        return {
            "run_uuid": run_uuid,
            "status": status_data.get("status", job.status),
            "progress_percent": status_data.get("progress_percent", job.progress_percent),
            "message": status_data.get("message", job.error_message or f"Status: {job.status}"),
            "tasks": status_data.get("tasks", {}),
            "execution_mode": "local",
            "work_directory": job.nextflow_work_dir,
            **_job_timing_payload(job),
        }
    
    finally:
        await session.close()

# --- JOB LOGS ---
@app.get("/jobs/{run_uuid}/logs", response_model=JobLogsResponse)
async def get_job_logs_endpoint(
    run_uuid: str = FastAPIPath(..., min_length=1),
    limit: int = Query(100, ge=1, le=1000),
):
    """Get logs for a specific job."""
    session = SessionLocal()
    
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.execution_mode == "slurm":
            backend = get_backend("slurm")
            logs = await backend.get_logs(run_uuid, limit=limit)
            return {
                "run_uuid": run_uuid,
                "logs": [
                    LogEntry(
                        timestamp=log.timestamp,
                        level=log.level,
                        message=log.message,
                        source=log.source,
                    )
                    for log in logs
                ],
            }
        
        logs = await get_job_logs(session, run_uuid, limit=limit)
        
        return {
            "run_uuid": run_uuid,
            "logs": [LogEntry(**log) for log in logs],
        }
    
    finally:
        await session.close()

# --- JOB LIST ---
@app.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """List jobs, optionally filtered by project or status."""
    session = SessionLocal()
    
    try:
        query = select(DogmeJob)
        
        if project_id:
            query = query.where(DogmeJob.project_id == project_id)
        
        if status:
            query = query.where(DogmeJob.status == status)
        
        result = await session.execute(query.limit(limit))
        jobs = result.scalars().all()
        
        running = await session.execute(
            select(DogmeJob).where(DogmeJob.status == JobStatus.RUNNING)
        )
        running_count = len(running.scalars().all())
        
        return {
            "jobs": [JobDetailsResponse(**job_to_dict(j)) for j in jobs],
            "total_count": len(jobs),
            "running_count": running_count,
        }
    
    finally:
        await session.close()

# --- CANCEL JOB ---
@app.post("/jobs/{run_uuid}/cancel")
async def cancel_job(run_uuid: str = FastAPIPath(..., min_length=1)):
    """Cancel a running job."""
    import signal
    session = SessionLocal()
    
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel job with status {job.status}"
            )

        if job.execution_mode == "slurm":
            backend = get_backend("slurm")
            if not await backend.cancel(run_uuid):
                raise HTTPException(status_code=500, detail="Failed to cancel SLURM job")
            await update_job_status(session, run_uuid, JobStatus.CANCELLED)
            await add_log_entry(
                session,
                run_uuid,
                "WARNING",
                "Job cancelled via SLURM backend.",
                source="api",
            )
            return {
                "status": "cancelled",
                "run_uuid": run_uuid,
                "message": "Job cancelled via SLURM backend.",
                "process_killed": True,
            }
        
        # Cancel monitoring task if running
        if run_uuid in job_monitors:
            task = job_monitors[run_uuid]
            if isinstance(task, asyncio.Task):
                task.cancel()
        
        # Kill the Nextflow process (SIGTERM for graceful cleanup)
        _pid = job.nextflow_process_id
        _process_killed = False
        _kill_message = ""
        if not _pid and job.nextflow_work_dir:
            # Fallback: read PID from file
            _pid_file = Path(job.nextflow_work_dir) / ".nextflow_pid"
            if _pid_file.exists():
                try:
                    _pid = int(_pid_file.read_text().strip())
                except (ValueError, OSError):
                    _pid = None

        if _pid:
            try:
                os.kill(_pid, signal.SIGTERM)
                _process_killed = True
                _kill_message = f"Job cancelled. Nextflow process (PID {_pid}) terminated."
                logger.info("Killed Nextflow process", run_uuid=run_uuid, pid=_pid)
            except ProcessLookupError:
                _kill_message = "Job cancelled. Nextflow process had already exited."
                logger.info("Nextflow process already exited", run_uuid=run_uuid, pid=_pid)
            except PermissionError:
                _kill_message = f"Job cancelled. Could not terminate process (PID {_pid}): permission denied."
                logger.warning("Permission denied killing Nextflow process", run_uuid=run_uuid, pid=_pid)
        else:
            _kill_message = "Job cancelled. Could not find process to terminate (monitoring stopped)."

        # Write cancellation marker so the process monitor doesn't
        # overwrite this with .nextflow_failed when it sees exit code 143
        if job.nextflow_work_dir:
            _cancel_marker = Path(job.nextflow_work_dir) / ".nextflow_cancelled"
            _cancel_marker.write_text(f"Cancelled at {datetime.utcnow().isoformat()}\n{_kill_message}\n")

        # Update status
        await update_job_status(session, run_uuid, JobStatus.CANCELLED)
        
        await add_log_entry(
            session,
            run_uuid,
            "WARNING",
            _kill_message,
            source="api",
        )
        
        return {
            "status": "cancelled",
            "run_uuid": run_uuid,
            "message": _kill_message,
            "process_killed": _process_killed,
        }
    
    finally:
        await session.close()

# --- DELETE JOB ---
@app.delete("/jobs/{run_uuid}")
async def delete_job(run_uuid: str = FastAPIPath(..., min_length=1)):
    """Delete workflow folder for a terminal-state job and archive the DB record."""
    import shutil
    session = SessionLocal()

    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        _terminal = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        if job.status not in _terminal:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete job with status {job.status}. "
                       f"Only {', '.join(s.value for s in _terminal)} jobs can be deleted.",
            )

        _work_dir = Path(job.nextflow_work_dir) if job.nextflow_work_dir else None
        _deleted_path = None
        _folder_name = None
        _file_count = 0

        if _work_dir and _work_dir.exists():
            _folder_name = _work_dir.name  # e.g. "workflow6"
            # Count files before deleting
            for _root, _dirs, _files in os.walk(_work_dir):
                _file_count += len(_files)
            shutil.rmtree(_work_dir)
            _deleted_path = str(_work_dir)
            logger.info("Deleted workflow folder", run_uuid=run_uuid,
                        path=_deleted_path, files=_file_count)

        # Archive the DB record
        job.status = JobStatus.DELETED
        job.report_json = None  # Free space
        await session.commit()

        await add_log_entry(
            session, run_uuid, "WARNING",
            f"Job data deleted. Folder {_folder_name or '(none)'} removed ({_file_count} files).",
            source="api",
        )

        _msg = (
            f"Workflow folder `{_folder_name}` deleted ({_file_count} files removed)."
            if _deleted_path
            else "Job archived (no workflow folder found on disk)."
        )
        return {
            "status": "deleted",
            "run_uuid": run_uuid,
            "message": _msg,
            "deleted_path": _deleted_path,
            "file_count": _file_count,
        }

    finally:
        await session.close()

# --- DEBUG JOB ---
@app.get("/jobs/{run_uuid}/debug")
async def debug_job(run_uuid: str = FastAPIPath(..., min_length=1)):
    """Get detailed debugging information about a job."""
    session = SessionLocal()
    
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        from pathlib import Path
        import os
        
        work_dir = Path(job.nextflow_work_dir) if job.nextflow_work_dir else None
        debug_info = {
            "run_uuid": run_uuid,
            "status": job.status,
            "work_directory": str(work_dir) if work_dir else None,
            "markers": {},
            "files": {},
            "process_info": {},
            "logs_preview": {},
            "workdir_listing": [],
        }
        
        if work_dir and work_dir.exists():
            # Check marker files
            markers = [".nextflow_running", ".nextflow_success", ".nextflow_failed", ".nextflow_pid", 
                      ".launch_command", ".launch_error", ".nextflow_error"]
            for marker in markers:
                marker_path = work_dir / marker
                debug_info["markers"][marker] = {
                    "exists": marker_path.exists(),
                    "content": marker_path.read_text()[:200] if marker_path.exists() else None
                }
            
            # Check if PID is alive
            pid_file = work_dir / ".nextflow_pid"
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    debug_info["process_info"]["pid"] = pid
                    try:
                        os.kill(pid, 0)
                        debug_info["process_info"]["alive"] = True
                    except OSError:
                        debug_info["process_info"]["alive"] = False
                except:
                    debug_info["process_info"]["error"] = "Could not parse PID"
            
            # Check key files
            files_to_check = ["nextflow.config", ".nextflow.log", "report.html", "timeline.html", "trace.txt", "pod5", "work"]
            for filename in files_to_check:
                filepath = work_dir / filename
                if filepath.exists():
                    if filepath.is_symlink():
                        debug_info["files"][filename] = {
                            "type": "symlink",
                            "target": str(filepath.resolve()),
                            "target_exists": filepath.resolve().exists()
                        }
                    elif filepath.is_dir():
                        try:
                            num_files = len(list(filepath.rglob("*")))
                            debug_info["files"][filename] = {
                                "type": "directory",
                                "num_files": num_files
                            }
                        except:
                            debug_info["files"][filename] = {"type": "directory", "error": "Could not count"}
                    else:
                        debug_info["files"][filename] = {
                            "type": "file",
                            "size": filepath.stat().st_size
                        }
                else:
                    debug_info["files"][filename] = {"exists": False}

            # Include a compact top-level directory listing for quick triage.
            try:
                entries = []
                for p in sorted(work_dir.iterdir(), key=lambda x: x.name.lower()):
                    entry = {
                        "name": p.name,
                        "type": "dir" if p.is_dir() else ("symlink" if p.is_symlink() else "file"),
                    }
                    if p.is_file():
                        try:
                            entry["size"] = p.stat().st_size
                        except Exception:
                            entry["size"] = None
                    entries.append(entry)
                debug_info["workdir_listing"] = entries[:200]
            except Exception as e:
                debug_info["workdir_listing"] = [{"error": str(e)}]
            
            # Get log previews
            from launchpad.config import LAUNCHPAD_LOGS_DIR
            log_files = [f"{run_uuid}_stdout.log", f"{run_uuid}_stderr.log", f"{run_uuid}.log"]
            for log_name in log_files:
                log_path = LAUNCHPAD_LOGS_DIR / log_name
                if log_path.exists():
                    try:
                        content = log_path.read_text()
                        lines = content.split('\n')
                        debug_info["logs_preview"][log_name] = {
                            "size": log_path.stat().st_size,
                            "lines": len(lines),
                            "last_10_lines": lines[-10:] if len(lines) > 10 else lines
                        }
                    except Exception as e:
                        debug_info["logs_preview"][log_name] = {"error": str(e)}
                else:
                    debug_info["logs_preview"][log_name] = {"exists": False}
            
            # Also get .nextflow.log from work directory (this has the real errors)
            nextflow_log = work_dir / ".nextflow.log"
            if nextflow_log.exists():
                try:
                    content = nextflow_log.read_text()
                    lines = content.split('\n')
                    # Extract ERROR and WARN lines
                    error_lines = [l for l in lines if 'ERROR' in l or 'WARN' in l]
                    debug_info["logs_preview"][".nextflow.log"] = {
                        "size": nextflow_log.stat().st_size,
                        "lines": len(lines),
                        "last_20_lines": lines[-20:] if len(lines) > 20 else lines,
                        "errors_and_warnings": error_lines[-10:] if len(error_lines) > 10 else error_lines
                    }
                except Exception as e:
                    debug_info["logs_preview"][".nextflow.log"] = {"error": str(e)}

            # Capture SLURM logs produced by remote submissions.
            for slurm_log in sorted(work_dir.glob("slurm-*.out")) + sorted(work_dir.glob("slurm-*.err")):
                try:
                    content = slurm_log.read_text()
                    lines = content.split("\n")
                    debug_info["logs_preview"][slurm_log.name] = {
                        "size": slurm_log.stat().st_size,
                        "lines": len(lines),
                        "last_40_lines": lines[-40:] if len(lines) > 40 else lines,
                    }
                except Exception as e:
                    debug_info["logs_preview"][slurm_log.name] = {"error": str(e)}

            # Parse .nextflow.log error-focused snippets to surface root causes quickly.
            if nextflow_log.exists() and ".nextflow.log" in debug_info["logs_preview"]:
                try:
                    content = nextflow_log.read_text().split("\n")
                    patterns = ("ERROR", "WARN", "Exception", "Caused by", "singularity", "apptainer", "denied", "timeout")
                    focused = [line for line in content if any(p in line for p in patterns)]
                    debug_info["logs_preview"][".nextflow.log"]["focused_lines"] = focused[-80:] if len(focused) > 80 else focused
                    debug_info["logs_preview"][".nextflow.log"]["first_60_lines"] = content[:60]
                except Exception:
                    pass
        
        return debug_info
    
    finally:
        await session.close()

# --- DEFAULT ROUTE ---
@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "AGOUTIC Launchpad",
        "description": "Dogme/Nextflow Job Execution Engine",
        "version": "0.3.0",
        "endpoints": {
            "health": "GET /health",
            "submit_job": "POST /jobs/submit",
            "get_job": "GET /jobs/{run_uuid}",
            "get_status": "GET /jobs/{run_uuid}/status",
            "get_logs": "GET /jobs/{run_uuid}/logs",
            "list_jobs": "GET /jobs",
            "cancel_job": "POST /jobs/{run_uuid}/cancel",
            "ssh_profiles": "GET /ssh-profiles",
            "create_ssh_profile": "POST /ssh-profiles",
            "test_ssh_profile": "POST /ssh-profiles/{id}/test",
        }
    }


# ==================== SSH Profile CRUD ====================


@app.post("/ssh-profiles", response_model=SSHProfileOut)
async def create_ssh_profile(req: SSHProfileCreate):
    """Create a new SSH connection profile."""
    import uuid as _uuid
    profile_id = _uuid.uuid4().hex

    async with SessionLocal() as session:
        # Check for duplicate
        existing = await session.execute(
            select(SSHProfile).where(
                SSHProfile.user_id == req.user_id,
                SSHProfile.ssh_host == req.ssh_host,
                SSHProfile.ssh_username == req.ssh_username,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(400, "A profile for this host/username already exists")

        profile = SSHProfile(
            id=profile_id,
            user_id=req.user_id,
            nickname=req.nickname,
            ssh_host=req.ssh_host,
            ssh_port=req.ssh_port,
            ssh_username=req.ssh_username,
            auth_method=req.auth_method,
            key_file_path=req.key_file_path,
            local_username=req.local_username,
            default_slurm_account=req.default_slurm_account,
            default_slurm_partition=req.default_slurm_partition,
            default_slurm_gpu_account=req.default_slurm_gpu_account,
            default_slurm_gpu_partition=req.default_slurm_gpu_partition,
            remote_base_path=req.remote_base_path,
        )
        session.add(profile)
        try:
            await session.commit()
            await session.refresh(profile)
            return _profile_to_out(profile)
        except IntegrityError as exc:
            await session.rollback()
            # Race-safe duplicate handling for unique(user_id, ssh_host, ssh_username)
            if "uq_ssh_profile_user_host" in str(exc) or "UNIQUE constraint failed" in str(exc):
                raise HTTPException(400, "A profile for this host/username already exists") from exc
            raise
        except OperationalError as exc:
            await session.rollback()
            # Surface schema drift clearly instead of a generic 500.
            if "no such column" in str(exc) or "no such table" in str(exc):
                raise HTTPException(
                    503,
                    "Launchpad database schema is out of date. Run `alembic upgrade head` and restart Launchpad.",
                ) from exc
            raise


@app.get("/ssh-profiles", response_model=list[SSHProfileOut])
async def list_ssh_profiles(user_id: str = Query(..., min_length=1)):
    """List SSH profiles for a user."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(SSHProfile).where(SSHProfile.user_id == user_id).order_by(SSHProfile.created_at.desc())
        )
        profiles = result.scalars().all()
        return [_profile_to_out(p) for p in profiles]


@app.get("/ssh-profiles/{profile_id}", response_model=SSHProfileOut)
async def get_ssh_profile_detail(
    profile_id: str = FastAPIPath(..., min_length=1),
    user_id: str = Query(..., min_length=1),
):
    """Get a single SSH profile."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(SSHProfile).where(SSHProfile.id == profile_id, SSHProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(404, "SSH profile not found")
        return _profile_to_out(profile)


@app.put("/ssh-profiles/{profile_id}", response_model=SSHProfileOut)
async def update_ssh_profile(
    req: SSHProfileUpdate,
    profile_id: str = FastAPIPath(..., min_length=1),
    user_id: str = Query(..., min_length=1),
):
    """Update an SSH profile."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(SSHProfile).where(SSHProfile.id == profile_id, SSHProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(404, "SSH profile not found")

        for field, value in req.model_dump(exclude_unset=True).items():
            setattr(profile, field, value)

        await session.commit()
        await session.refresh(profile)
        return _profile_to_out(profile)


@app.delete("/ssh-profiles/{profile_id}")
async def delete_ssh_profile(
    profile_id: str = FastAPIPath(..., min_length=1),
    user_id: str = Query(..., min_length=1),
):
    """Delete an SSH profile."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(SSHProfile).where(SSHProfile.id == profile_id, SSHProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(404, "SSH profile not found")

        await session.delete(profile)
        await session.commit()
        return {"ok": True, "message": "Profile deleted"}


@app.post("/ssh-profiles/{profile_id}/test", response_model=SSHProfileTestResult)
async def test_ssh_profile(
    profile_id: str = FastAPIPath(..., min_length=1),
    user_id: str = Query(..., min_length=1),
    local_password: str = Body("", embed=True),
):
    """Test SSH connectivity for a profile. Password is transient — never stored."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(SSHProfile).where(SSHProfile.id == profile_id, SSHProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(404, "SSH profile not found")

    from launchpad.backends.ssh_manager import SSHConnectionManager, SSHProfileData
    manager = SSHConnectionManager()
    profile_data = SSHProfileData(
        id=profile.id,
        user_id=profile.user_id,
        nickname=profile.nickname,
        ssh_host=profile.ssh_host,
        ssh_port=profile.ssh_port,
        ssh_username=profile.ssh_username,
        auth_method=profile.auth_method,
        key_file_path=profile.key_file_path,
        local_username=profile.local_username,
        is_enabled=profile.is_enabled,
    )
    test_result = await manager.test_connection(profile_data, local_password)
    if local_password and profile.local_username:
        session_meta = await get_local_auth_session_manager().get_session_metadata(profile_data)
        if session_meta is not None:
            test_result["session_started"] = True
            test_result["session_expires_at"] = session_meta["expires_at"]
    return SSHProfileTestResult(**test_result)


@app.get("/ssh-profiles/{profile_id}/auth-session", response_model=SSHProfileAuthSessionResult)
async def get_ssh_profile_auth_session(
    profile_id: str = FastAPIPath(..., min_length=1),
    user_id: str = Query(..., min_length=1),
):
    """Return the active local auth session for a profile, if any."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(SSHProfile).where(SSHProfile.id == profile_id, SSHProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(404, "SSH profile not found")

    profile_data = SSHProfileData(
        id=profile.id,
        user_id=profile.user_id,
        nickname=profile.nickname,
        ssh_host=profile.ssh_host,
        ssh_port=profile.ssh_port,
        ssh_username=profile.ssh_username,
        auth_method=profile.auth_method,
        key_file_path=profile.key_file_path,
        local_username=profile.local_username,
        is_enabled=profile.is_enabled,
    )
    session_meta = await get_local_auth_session_manager().get_session_metadata(profile_data)
    if session_meta is None:
        return SSHProfileAuthSessionResult(active=False, message="No active local auth session")
    return SSHProfileAuthSessionResult(message="Local auth session active", **session_meta)


@app.post("/ssh-profiles/{profile_id}/auth-session", response_model=SSHProfileAuthSessionResult)
async def create_ssh_profile_auth_session(
    profile_id: str = FastAPIPath(..., min_length=1),
    user_id: str = Query(..., min_length=1),
    local_password: str = Body(..., embed=True),
):
    """Start or replace a local auth session for a profile using the local Unix password."""
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(SSHProfile).where(SSHProfile.id == profile_id, SSHProfile.user_id == user_id)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                raise HTTPException(404, "SSH profile not found")

        profile_data = SSHProfileData(
            id=profile.id,
            user_id=profile.user_id,
            nickname=profile.nickname,
            ssh_host=profile.ssh_host,
            ssh_port=profile.ssh_port,
            ssh_username=profile.ssh_username,
            auth_method=profile.auth_method,
            key_file_path=profile.key_file_path,
            local_username=profile.local_username,
            is_enabled=profile.is_enabled,
        )
        session_obj = await get_local_auth_session_manager().create_or_replace_session(profile_data, local_password)
        payload = get_local_auth_session_manager()._serialize_session(session_obj)
        return SSHProfileAuthSessionResult(message="Local auth session started", **payload)
    except HTTPException:
        raise
    except PermissionError as exc:
        logger.warning("Local auth session rejected", profile_id=profile_id, user_id=user_id, error=str(exc))
        raise HTTPException(401, str(exc))
    except (ValueError, RuntimeError, TimeoutError) as exc:
        logger.error("Local auth session failed", profile_id=profile_id, user_id=user_id, error=str(exc))
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.exception("Unexpected local auth session error", profile_id=profile_id, user_id=user_id)
        raise HTTPException(500, f"Unexpected unlock error: {exc}")


@app.delete("/ssh-profiles/{profile_id}/auth-session", response_model=SSHProfileAuthSessionResult)
async def delete_ssh_profile_auth_session(
    profile_id: str = FastAPIPath(..., min_length=1),
    user_id: str = Query(..., min_length=1),
):
    """Terminate the active local auth session for a profile."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(SSHProfile).where(SSHProfile.id == profile_id, SSHProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(404, "SSH profile not found")

    profile_data = SSHProfileData(
        id=profile.id,
        user_id=profile.user_id,
        nickname=profile.nickname,
        ssh_host=profile.ssh_host,
        ssh_port=profile.ssh_port,
        ssh_username=profile.ssh_username,
        auth_method=profile.auth_method,
        key_file_path=profile.key_file_path,
        local_username=profile.local_username,
        is_enabled=profile.is_enabled,
    )
    closed = await get_local_auth_session_manager().close_session_for_profile(profile_data)
    if not closed:
        return SSHProfileAuthSessionResult(active=False, message="No active local auth session")
    return SSHProfileAuthSessionResult(active=False, message="Local auth session closed")


def _profile_to_out(profile: SSHProfile) -> SSHProfileOut:
    """Convert SSHProfile model to output schema (no secrets)."""
    return SSHProfileOut(
        id=profile.id,
        user_id=profile.user_id,
        nickname=profile.nickname,
        ssh_host=profile.ssh_host,
        ssh_port=profile.ssh_port,
        ssh_username=profile.ssh_username,
        auth_method=profile.auth_method,
        has_key_file=bool(profile.key_file_path),
        key_file_path=profile.key_file_path,
        local_username=profile.local_username,
        default_slurm_account=profile.default_slurm_account,
        default_slurm_partition=profile.default_slurm_partition,
        default_slurm_gpu_account=profile.default_slurm_gpu_account,
        default_slurm_gpu_partition=profile.default_slurm_gpu_partition,
        remote_base_path=profile.remote_base_path,
        is_enabled=profile.is_enabled,
        created_at=profile.created_at.isoformat() if profile.created_at else "",
        updated_at=profile.updated_at.isoformat() if profile.updated_at else "",
    )
