"""
Launchpad: FastAPI application for Dogme/Nextflow job execution.
Receives job submissions from Cortex and manages pipeline execution.
"""
import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Path as FastAPIPath, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select

from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
from launchpad.config import (
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
from launchpad.models import DogmeJob
from launchpad.nextflow_executor import NextflowExecutor
from launchpad.schemas import (
    SubmitJobRequest,
    JobStatusResponse,
    JobDetailsResponse,
    JobSubmitResponse,
    JobListResponse,
    JobLogsResponse,
    LogEntry,
    HealthCheckResponse,
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

# --- LIFECYCLE ---
@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    await init_db()
    logger.info("Launchpad initialized", max_concurrent_jobs=MAX_CONCURRENT_JOBS, poll_interval=JOB_POLL_INTERVAL)

@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    # Cancel all monitoring tasks
    for task in job_monitors.values():
        if isinstance(task, asyncio.Task):
            task.cancel()
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
        
        # Log submission
        await add_log_entry(
            session,
            run_uuid,
            "INFO",
            f"Job submitted: {req.sample_name} ({req.mode} mode)",
            source="api",
        )
        
        # Submit to Nextflow
        try:
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
            )
            
            # Update job with work directory info
            job.nextflow_work_dir = str(work_dir)
            job.status = JobStatus.RUNNING
            job.started_at = datetime.utcnow()
            await session.commit()
            
            # Start background monitoring task
            monitor_task = asyncio.create_task(monitor_job(run_uuid, work_dir))
            job_monitors[run_uuid] = monitor_task
            
            await add_log_entry(
                session,
                run_uuid,
                "INFO",
                f"Nextflow submitted successfully",
                source="api",
            )
            
            return {
                "run_uuid": run_uuid,
                "sample_name": req.sample_name,
                "status": JobStatus.RUNNING,
                "work_directory": str(work_dir),
            }
        
        except Exception as e:
            # Job submission failed
            import traceback
            error_trace = traceback.format_exc()
            logger.error("Nextflow submission error", run_uuid=run_uuid, error=str(e), exc_info=True)
            
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await session.commit()
            
            await add_log_entry(
                session,
                run_uuid,
                "ERROR",
                f"Failed to submit to Nextflow: {str(e)}",
                source="api",
            )
            
            raise HTTPException(
                status_code=500,
                detail=f"Failed to submit job: {str(e)}"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error("Submit endpoint error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
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

@app.get("/jobs/{run_uuid}/status", response_model=JobStatusResponse)
async def get_job_status(run_uuid: str = FastAPIPath(..., min_length=1)):
    """Get quick status of a job."""
    session = SessionLocal()
    
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
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
        
        # Cancel monitoring task if running
        if run_uuid in job_monitors:
            task = job_monitors[run_uuid]
            if isinstance(task, asyncio.Task):
                task.cancel()
        
        # Update status
        await update_job_status(session, run_uuid, JobStatus.CANCELLED)
        
        await add_log_entry(
            session,
            run_uuid,
            "WARNING",
            "Job cancelled by user",
            source="api",
        )
        
        return {"status": "cancelled", "run_uuid": run_uuid}
    
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
            files_to_check = ["nextflow.config", ".nextflow.log", "report.html", "pod5"]
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
        }
    }
