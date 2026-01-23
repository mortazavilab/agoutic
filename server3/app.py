"""
Server 3: FastAPI application for Dogme/Nextflow job execution.
Receives job submissions from Server 1 and manages pipeline execution.
"""
import asyncio
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Path as FastAPIPath, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from server3.config import (
    JobStatus,
    MAX_CONCURRENT_JOBS,
    JOB_POLL_INTERVAL,
)
from server3.db import (
    init_db,
    SessionLocal,
    create_job,
    get_job,
    update_job_status,
    add_log_entry,
    get_job_logs,
    job_to_dict,
)
from server3.models import DogmeJob
from server3.nextflow_executor import NextflowExecutor
from server3.schemas import (
    SubmitJobRequest,
    JobStatusResponse,
    JobDetailsResponse,
    JobSubmitResponse,
    JobListResponse,
    JobLogsResponse,
    LogEntry,
    HealthCheckResponse,
)

# --- APP SETUP ---
app = FastAPI(
    title="AGOUTIC Server 3",
    description="Dogme/Nextflow Job Execution Engine",
    version="0.3.0",
)

# Enable CORS for communication with Server 1
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
    print("✅ Server 3 initialized: Database ready")
    print(f"📍 Max concurrent jobs: {MAX_CONCURRENT_JOBS}")
    print(f"⏱️  Job poll interval: {JOB_POLL_INTERVAL}s")

@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    # Cancel all monitoring tasks
    for task in job_monitors.values():
        if isinstance(task, asyncio.Task):
            task.cancel()
    print("👋 Server 3 shutdown complete")

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
                print(f"🛑 Monitoring stopped for job {run_uuid}")
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
            "status": "degraded",
            "version": "0.3.0",
            "running_jobs": 0,
            "database_ok": False,
        }
    finally:
        await session.close()

# --- JOB SUBMISSION ---
@app.post("/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(req: SubmitJobRequest):
    """
    Submit a new Dogme/Nextflow job.
    
    Receives request from Server 1 agent with job parameters.
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
                sample_name=req.sample_name,
                mode=req.mode,
                input_dir=req.input_directory,
                reference_genome=req.reference_genome,
                modifications=req.modifications,
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
        
        return {
            "run_uuid": run_uuid,
            "status": job.status,
            "progress_percent": job.progress_percent,
            "message": job.error_message or f"Status: {job.status}",
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

# --- DEFAULT ROUTE ---
@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "AGOUTIC Server 3",
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
