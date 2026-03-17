"""
Database connection and utilities for Launchpad.

Delegates to common.database for engine/session creation.
Retains Launchpad-specific CRUD helpers.
"""
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from common.database import (
    AsyncSessionLocal as SessionLocal,
    init_db_async as init_db,
)
from launchpad.models import DogmeJob, JobLog, SSHProfile, RemoteReferenceCache, RemoteInputCache
from launchpad.backends.ssh_manager import SSHProfileData


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
        "cache_preflight": job.cache_preflight_json,
        "reference_cache_status": job.reference_cache_status,
        "data_cache_status": job.data_cache_status,
        "reference_cache_path": job.reference_cache_path,
        "data_cache_path": job.data_cache_path,
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


# ==================== Session-managed helpers (for backends) ====================

async def get_job_by_uuid(run_uuid: str) -> DogmeJob | None:
    """Retrieve a job by UUID (opens its own session). Used by backends."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(DogmeJob).where(DogmeJob.run_uuid == run_uuid)
        )
        return result.scalar_one_or_none()


async def update_job_field(run_uuid: str, field: str, value) -> None:
    """Update a single field on a DogmeJob (opens its own session)."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(DogmeJob).where(DogmeJob.run_uuid == run_uuid)
        )
        job = result.scalar_one_or_none()
        if job:
            setattr(job, field, value)
            await session.commit()


async def update_job_fields(run_uuid: str, fields: dict) -> None:
    """Update multiple fields on a DogmeJob (opens its own session)."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(DogmeJob).where(DogmeJob.run_uuid == run_uuid)
        )
        job = result.scalar_one_or_none()
        if job:
            for key, value in fields.items():
                setattr(job, key, value)
            await session.commit()


async def get_ssh_profile(profile_id: str, user_id: str | None = None) -> SSHProfileData | None:
    """Load an SSH profile from DB and return as SSHProfileData."""
    async with SessionLocal() as session:
        query = select(SSHProfile).where(SSHProfile.id == profile_id)
        if user_id:
            query = query.where(SSHProfile.user_id == user_id)
        result = await session.execute(query)
        row = result.scalar_one_or_none()
        if not row:
            return None
        return SSHProfileData(
            id=row.id,
            user_id=row.user_id,
            nickname=row.nickname,
            ssh_host=row.ssh_host,
            ssh_port=row.ssh_port,
            ssh_username=row.ssh_username,
            auth_method=row.auth_method,
            key_file_path=row.key_file_path,
            local_username=row.local_username,
            is_enabled=row.is_enabled,
        )


async def get_remote_reference_cache_entry(
    user_id: str,
    ssh_profile_id: str,
    reference_id: str,
) -> RemoteReferenceCache | None:
    """Fetch a reference cache entry for a user/profile/reference tuple."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(RemoteReferenceCache).where(
                RemoteReferenceCache.user_id == user_id,
                RemoteReferenceCache.ssh_profile_id == ssh_profile_id,
                RemoteReferenceCache.reference_id == reference_id,
            )
        )
        return result.scalar_one_or_none()


async def upsert_remote_reference_cache_entry(
    *,
    user_id: str,
    ssh_profile_id: str,
    reference_id: str,
    source_signature: str | None,
    source_uri: str | None,
    remote_path: str,
    status: str = "READY",
    increment_use_count: bool = False,
) -> None:
    """Create or update a reference cache entry."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        result = await session.execute(
            select(RemoteReferenceCache).where(
                RemoteReferenceCache.user_id == user_id,
                RemoteReferenceCache.ssh_profile_id == ssh_profile_id,
                RemoteReferenceCache.reference_id == reference_id,
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            entry = RemoteReferenceCache(
                id=str(uuid.uuid4()),
                user_id=user_id,
                ssh_profile_id=ssh_profile_id,
                reference_id=reference_id,
                source_signature=source_signature,
                source_uri=source_uri,
                remote_path=remote_path,
                status=status,
                use_count=1 if increment_use_count else 0,
                last_validated_at=now,
                last_used_at=now if increment_use_count else None,
                updated_at=now,
            )
            session.add(entry)
        else:
            entry.source_signature = source_signature
            entry.source_uri = source_uri
            entry.remote_path = remote_path
            entry.status = status
            entry.last_validated_at = now
            entry.updated_at = now
            if increment_use_count:
                entry.use_count = (entry.use_count or 0) + 1
                entry.last_used_at = now
        await session.commit()


async def get_remote_input_cache_entry(
    user_id: str,
    ssh_profile_id: str,
    reference_id: str,
    input_fingerprint: str,
) -> RemoteInputCache | None:
    """Fetch an input cache entry for a user/profile/reference/fingerprint tuple."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(RemoteInputCache).where(
                RemoteInputCache.user_id == user_id,
                RemoteInputCache.ssh_profile_id == ssh_profile_id,
                RemoteInputCache.reference_id == reference_id,
                RemoteInputCache.input_fingerprint == input_fingerprint,
            )
        )
        return result.scalar_one_or_none()


async def upsert_remote_input_cache_entry(
    *,
    user_id: str,
    ssh_profile_id: str,
    reference_id: str,
    input_fingerprint: str,
    remote_path: str,
    size_bytes: int | None = None,
    status: str = "READY",
    increment_use_count: bool = False,
) -> None:
    """Create or update an input cache entry."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        result = await session.execute(
            select(RemoteInputCache).where(
                RemoteInputCache.user_id == user_id,
                RemoteInputCache.ssh_profile_id == ssh_profile_id,
                RemoteInputCache.reference_id == reference_id,
                RemoteInputCache.input_fingerprint == input_fingerprint,
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            entry = RemoteInputCache(
                id=str(uuid.uuid4()),
                user_id=user_id,
                ssh_profile_id=ssh_profile_id,
                reference_id=reference_id,
                input_fingerprint=input_fingerprint,
                remote_path=remote_path,
                status=status,
                size_bytes=size_bytes,
                use_count=1 if increment_use_count else 0,
                last_used_at=now if increment_use_count else None,
                updated_at=now,
            )
            session.add(entry)
        else:
            entry.remote_path = remote_path
            entry.status = status
            if size_bytes is not None:
                entry.size_bytes = size_bytes
            entry.updated_at = now
            if increment_use_count:
                entry.use_count = (entry.use_count or 0) + 1
                entry.last_used_at = now
        await session.commit()
