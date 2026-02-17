"""
Database connection and utilities for Server 3.
"""
import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func
from server3.config import DATABASE_URL
from server3.models import Base, DogmeJob, JobLog

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

def job_to_dict(job: DogmeJob) -> dict:
    """Convert DogmeJob row to dictionary."""
    return {
        "run_uuid": job.run_uuid,
        "project_id": job.project_id,
        "sample_name": job.sample_name,
        "mode": job.mode,
        "input_directory": job.input_directory,
        "reference_genome": job.reference_genome,
        "modifications": job.modifications,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "submitted_at": job.submitted_at.isoformat() if job.submitted_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "output_directory": job.output_directory,
        "report": json.loads(job.report_json) if job.report_json else None,
        "error_message": job.error_message,
    }

async def create_job(
    session: AsyncSession,
    run_uuid: str,
    project_id: str,
    sample_name: str,
    mode: str,
    input_directory: str,
    reference_genome: str | list | None = None,
    modifications: str | None = None,
    parent_block_id: str | None = None,
    user_id: str | None = None,
) -> DogmeJob:
    """Create a new job record."""
    import json
    
    # Serialize reference_genome list to JSON string for storage
    if isinstance(reference_genome, list):
        reference_genome_str = json.dumps(reference_genome)
    else:
        reference_genome_str = reference_genome
    
    job = DogmeJob(
        run_uuid=run_uuid,
        project_id=project_id,
        user_id=user_id,
        sample_name=sample_name,
        mode=mode,
        input_directory=input_directory,
        reference_genome=reference_genome_str,
        modifications=modifications,
        parent_block_id=parent_block_id,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job

async def get_job(session: AsyncSession, run_uuid: str) -> DogmeJob | None:
    """Retrieve a job by UUID."""
    result = await session.execute(
        select(DogmeJob).where(DogmeJob.run_uuid == run_uuid)
    )
    return result.scalar_one_or_none()

async def update_job_status(
    session: AsyncSession,
    run_uuid: str,
    status: str,
    progress: int | None = None,
    error_message: str | None = None,
) -> DogmeJob | None:
    """Update job status and optionally progress."""
    job = await get_job(session, run_uuid)
    if job:
        job.status = status
        if progress is not None:
            job.progress_percent = progress
        if error_message:
            job.error_message = error_message
        await session.commit()
        await session.refresh(job)
    return job

async def add_log_entry(
    session: AsyncSession,
    run_uuid: str,
    level: str,
    message: str,
    source: str | None = None,
) -> JobLog:
    """Add a log entry for a job."""
    log = JobLog(
        run_uuid=run_uuid,
        level=level,
        message=message,
        source=source,
    )
    session.add(log)
    await session.commit()
    return log

async def get_job_logs(
    session: AsyncSession,
    run_uuid: str,
    limit: int = 100,
) -> list[dict]:
    """Retrieve logs for a job."""
    result = await session.execute(
        select(JobLog)
        .where(JobLog.run_uuid == run_uuid)
        .order_by(JobLog.timestamp.asc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "timestamp": log.timestamp.isoformat(),
            "level": log.level,
            "message": log.message,
            "source": log.source,
        }
        for log in logs
    ]
