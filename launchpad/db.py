"""
Database connection and utilities for Launchpad.

Delegates to common.database for engine/session creation.
Retains Launchpad-specific CRUD helpers.
"""
import json
import uuid
import re
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from common.database import (
    AsyncSessionLocal as SessionLocal,
    init_db_async as init_db,
)
from launchpad.models import (
    DogmeJob,
    JobLog,
    SSHProfile,
    RemoteReferenceCache,
    RemoteInputCache,
    RemoteStagedSample,
)
from launchpad.backends.ssh_manager import SSHProfileData


def job_to_dict(job: DogmeJob) -> dict:
    """Convert DogmeJob row to dictionary."""
    return {
        "run_uuid": job.run_uuid,
        "project_id": job.project_id,
        "workflow_index": job.workflow_index,
        "workflow_alias": job.workflow_alias,
        "workflow_folder_name": job.workflow_folder_name,
        "workflow_display_name": job.workflow_display_name,
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
    workflow_index: int | None = None,
    workflow_alias: str | None = None,
    workflow_folder_name: str | None = None,
    workflow_display_name: str | None = None,
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
        workflow_index=workflow_index,
        workflow_alias=workflow_alias,
        workflow_folder_name=workflow_folder_name,
        workflow_display_name=workflow_display_name,
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


_WORKFLOW_SUFFIX_RE = re.compile(r"^workflow(\d+)$", re.IGNORECASE)


def workflow_alias_for_index(index: int) -> str:
    return f"workflow{index}"


def infer_workflow_index_from_path(path: str | None) -> int | None:
    if not path:
        return None
    folder_name = str(path).rstrip("/").rsplit("/", 1)[-1]
    match = _WORKFLOW_SUFFIX_RE.fullmatch(folder_name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


async def get_next_workflow_index(session: AsyncSession, project_id: str) -> int:
    """Return the next monotonic workflow index for a project.

    Falls back to parsing legacy workflow folder names when older rows do not yet
    have workflow_index populated.
    """
    result = await session.execute(
        select(func.max(DogmeJob.workflow_index)).where(DogmeJob.project_id == project_id)
    )
    current_max = result.scalar_one_or_none()
    if current_max:
        return int(current_max) + 1

    legacy_rows = await session.execute(
        select(DogmeJob.nextflow_work_dir, DogmeJob.remote_work_dir).where(DogmeJob.project_id == project_id)
    )
    legacy_max = 0
    for local_work_dir, remote_work_dir in legacy_rows.all():
        for candidate in (local_work_dir, remote_work_dir):
            inferred = infer_workflow_index_from_path(candidate)
            if inferred and inferred > legacy_max:
                legacy_max = inferred
    return legacy_max + 1 if legacy_max else 1


async def get_workflow_identity_for_path(
    session: AsyncSession,
    project_id: str,
    workflow_path: str | None,
) -> tuple[int | None, str | None, str | None]:
    """Resolve a workflow's persisted identity from a local or remote work path."""
    if not workflow_path:
        return None, None, None

    result = await session.execute(
        select(DogmeJob.workflow_index, DogmeJob.workflow_alias, DogmeJob.workflow_folder_name)
        .where(DogmeJob.project_id == project_id)
        .where(or_(DogmeJob.nextflow_work_dir == workflow_path, DogmeJob.remote_work_dir == workflow_path))
        .order_by(DogmeJob.submitted_at.desc())
        .limit(1)
    )
    row = result.first()
    if row:
        return row[0], row[1], row[2]

    inferred_index = infer_workflow_index_from_path(workflow_path)
    if inferred_index is None:
        return None, None, None
    alias = workflow_alias_for_index(inferred_index)
    folder_name = str(workflow_path).rstrip("/").rsplit("/", 1)[-1]
    return inferred_index, alias, folder_name

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
            remote_base_path=row.remote_base_path,
            is_enabled=row.is_enabled,
            default_slurm_account=row.default_slurm_account,
            default_slurm_partition=row.default_slurm_partition,
            default_slurm_gpu_account=row.default_slurm_gpu_account,
            default_slurm_gpu_partition=row.default_slurm_gpu_partition,
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


async def get_remote_staged_sample(
    user_id: str,
    ssh_profile_id: str,
    sample_name: str,
    *,
    mode: str | None = None,
) -> RemoteStagedSample | None:
    """Fetch one staged sample by user/profile/sample name, optionally filtered by mode."""
    async with SessionLocal() as session:
        query = select(RemoteStagedSample).where(
            RemoteStagedSample.user_id == user_id,
            RemoteStagedSample.ssh_profile_id == ssh_profile_id,
            RemoteStagedSample.sample_name == sample_name,
        )
        if mode:
            query = query.where(RemoteStagedSample.mode == mode)
        query = query.order_by(RemoteStagedSample.updated_at.desc())
        result = await session.execute(query)
        return result.scalar_one_or_none()


async def list_remote_staged_samples(
    user_id: str,
    ssh_profile_id: str,
    *,
    mode: str | None = None,
    reference_genome: list[str] | None = None,
) -> list[RemoteStagedSample]:
    """List staged samples for a user/profile, optionally filtered by mode and genome overlap."""
    async with SessionLocal() as session:
        query = select(RemoteStagedSample).where(
            RemoteStagedSample.user_id == user_id,
            RemoteStagedSample.ssh_profile_id == ssh_profile_id,
        ).order_by(RemoteStagedSample.sample_name.asc())
        if mode:
            query = query.where(RemoteStagedSample.mode == mode)
        result = await session.execute(query)
        rows = list(result.scalars().all())
        if not reference_genome:
            return rows
        wanted = {ref.strip().lower() for ref in reference_genome if ref}
        filtered: list[RemoteStagedSample] = []
        for row in rows:
            row_refs = row.reference_genome_json or []
            normalized = {str(ref).strip().lower() for ref in row_refs}
            if normalized & wanted:
                filtered.append(row)
        return filtered


async def upsert_remote_staged_sample(
    *,
    user_id: str,
    ssh_profile_id: str,
    ssh_profile_nickname: str | None,
    sample_name: str,
    sample_slug: str,
    mode: str,
    reference_genome: list[str],
    source_path: str,
    input_fingerprint: str,
    remote_base_path: str,
    remote_data_path: str,
    remote_reference_paths: dict[str, str],
    status: str = "READY",
    mark_used: bool = False,
) -> RemoteStagedSample:
    """Create or update user-visible staged sample metadata."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        result = await session.execute(
            select(RemoteStagedSample).where(
                RemoteStagedSample.user_id == user_id,
                RemoteStagedSample.ssh_profile_id == ssh_profile_id,
                RemoteStagedSample.sample_slug == sample_slug,
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            entry = RemoteStagedSample(
                id=str(uuid.uuid4()),
                user_id=user_id,
                ssh_profile_id=ssh_profile_id,
                ssh_profile_nickname=ssh_profile_nickname,
                sample_name=sample_name,
                sample_slug=sample_slug,
                mode=mode,
                reference_genome_json=reference_genome,
                source_path=source_path,
                input_fingerprint=input_fingerprint,
                remote_base_path=remote_base_path,
                remote_data_path=remote_data_path,
                remote_reference_paths_json=remote_reference_paths,
                status=status,
                last_staged_at=now,
                last_used_at=now if mark_used else None,
                updated_at=now,
            )
            session.add(entry)
        else:
            entry.ssh_profile_nickname = ssh_profile_nickname
            entry.sample_name = sample_name
            entry.mode = mode
            entry.reference_genome_json = reference_genome
            entry.source_path = source_path
            entry.input_fingerprint = input_fingerprint
            entry.remote_base_path = remote_base_path
            entry.remote_data_path = remote_data_path
            entry.remote_reference_paths_json = remote_reference_paths
            entry.status = status
            entry.last_staged_at = now
            entry.updated_at = now
            if mark_used:
                entry.last_used_at = now
        await session.commit()
        await session.refresh(entry)
        return entry
