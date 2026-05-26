"""
Launchpad: FastAPI application for Dogme/Nextflow job execution.
Receives job submissions from Cortex and manages pipeline execution.
"""
import asyncio
import json
import os
import re
import signal
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
    find_import_duplicate,
    find_job_by_workflow_path,
    get_default_ssh_profile_id,
    get_next_workflow_index,
    get_job,
    get_ssh_profile,
    infer_workflow_index_from_path,
    get_workflow_identity_for_path,
    resolve_job_by_workflow_label,
    update_job_status,
    add_log_entry,
    get_job_logs,
    job_to_dict,
    workflow_alias_for_index,
    get_staging_task_record,
    staging_task_record_to_dict,
)
from launchpad.models import DogmeJob, SSHProfile
from launchpad.nextflow_executor import NextflowExecutor, _find_nextflow_trace_file
from launchpad.schemas import (
    ImportWorkflowRequest,
    ImportWorkflowResponse,
    SubmitJobRequest,
    WorkflowPreviewRequest,
    WorkflowPreviewResponse,
    StageRemoteSampleRequest,
    StageRemoteSampleResponse,
    StageTaskAcceptedResponse,
    StageTaskCancelResponse,
    StageTaskResumeResponse,
    StagingTaskStatusResponse,
    JobStatusResponse,
    JobStatusExtendedResponse,
    JobResultSyncResponse,
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
    WorkflowRenameRequest,
)
from launchpad.backends import get_backend, SubmitParams
from launchpad.backends.local_auth_sessions import get_local_auth_session_manager
from launchpad.backends.ssh_manager import SSHConnectionManager, SSHProfileData
from launchpad.workflow_accounting import build_unavailable_workflow_usage, summarize_nextflow_trace_file
from launchpad.workflow_executors import get_workflow_executor
from launchpad.workflow_executors.base import UnknownWorkflowKeyError
from launchpad.import_workflows import (
    copy_local_results_to_workflow,
    import_warning_message,
    infer_local_workflow_metadata,
    infer_remote_workflow_metadata,
    normalize_local_workflow_source,
    normalize_remote_workflow_source,
)
from launchpad.script_execution import (
    normalize_script_args,
    resolve_allowlisted_script,
    script_subprocess_session_kwargs,
    terminate_script_process_tree,
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

_TRAILING_PATH_JUNK = re.compile(r'(?:\\n|[^a-zA-Z0-9/_.\-~])+$')
_WORKFLOW_FOLDER_RE = re.compile(r"^workflow\d+$", re.IGNORECASE)


def _sanitize_work_directory(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _TRAILING_PATH_JUNK.sub('', str(value).strip())
    return cleaned or None


def _effective_job_work_directory(job) -> str | None:
    result_destination = (getattr(job, "result_destination", "") or "").strip().lower()
    output_directory = _sanitize_work_directory(getattr(job, "output_directory", None))
    local_work_dir = _sanitize_work_directory(getattr(job, "nextflow_work_dir", None))
    remote_work_dir = _sanitize_work_directory(getattr(job, "remote_work_dir", None))
    if result_destination in {"local", "both"}:
        if output_directory:
            return output_directory
        if local_work_dir:
            return local_work_dir
    return remote_work_dir or local_work_dir


def _resolve_deletable_workflow_dir(job) -> Path | None:
    expected_folder_name = str(getattr(job, "workflow_folder_name", "") or "").strip()
    candidate_values = [
        getattr(job, "output_directory", None),
        _effective_job_work_directory(job),
        getattr(job, "nextflow_work_dir", None),
    ]
    seen: set[str] = set()
    for raw_value in candidate_values:
        cleaned = _sanitize_work_directory(raw_value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        candidate = Path(cleaned).expanduser()
        candidate_name = candidate.name
        if expected_folder_name:
            if candidate_name != expected_folder_name:
                continue
        elif not _WORKFLOW_FOLDER_RE.fullmatch(candidate_name):
            continue
        return candidate
    return None


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


def _workflow_usage_payload(job) -> dict[str, object | None]:
    synced_at = getattr(job, "workflow_usage_synced_at", None)
    return {
        "workflow_usage": getattr(job, "workflow_usage_json", None),
        "workflow_usage_synced_at": synced_at.isoformat() if synced_at else None,
    }


def _coerce_workflow_usage_synced_at(raw_value) -> datetime | None:
    if raw_value in (None, ""):
        return None
    parsed = raw_value
    if isinstance(raw_value, str):
        cleaned = str(raw_value).strip()
        if not cleaned:
            return None
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(parsed, datetime):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _slurm_status_poll_failed(status_data) -> bool:
    message = str(getattr(status_data, "message", "") or "")
    tasks = getattr(status_data, "tasks", None)
    return message.startswith("Failed to poll scheduler:") and not tasks


async def _slurm_status_payload(session, job, status_data) -> dict[str, object]:
    workflow_usage = getattr(status_data, "workflow_usage", None)
    workflow_usage_synced_at = getattr(status_data, "workflow_usage_synced_at", None)
    if workflow_usage is not None:
        resolved_synced_at = _coerce_workflow_usage_synced_at(workflow_usage_synced_at) or datetime.utcnow()
        existing_synced_at = _coerce_workflow_usage_synced_at(getattr(job, "workflow_usage_synced_at", None))
        workflow_usage_changed = getattr(job, "workflow_usage_json", None) != workflow_usage
        synced_at_changed = existing_synced_at != resolved_synced_at
        if workflow_usage_changed or synced_at_changed:
            job.workflow_usage_json = workflow_usage
            job.workflow_usage_synced_at = resolved_synced_at
            await session.commit()
        workflow_usage_synced_at = resolved_synced_at.isoformat()
    return {
        "run_uuid": str(getattr(status_data, "run_uuid", None) or getattr(job, "run_uuid", "") or ""),
        "status": getattr(status_data, "status", None) or job.status,
        "progress_percent": (
            getattr(status_data, "progress_percent", None)
            if getattr(status_data, "progress_percent", None) is not None
            else (100 if getattr(status_data, "status", None) == JobStatus.COMPLETED else job.progress_percent)
        ),
        "message": getattr(status_data, "message", None) or job.error_message or f"Status: {job.status}",
        "tasks": getattr(status_data, "tasks", None) or {},
        "execution_mode": getattr(status_data, "execution_mode", None) or "slurm",
        "run_stage": getattr(status_data, "run_stage", None) or job.run_stage,
        "slurm_job_id": getattr(status_data, "slurm_job_id", None) or job.slurm_job_id,
        "slurm_state": getattr(status_data, "slurm_state", None) or job.slurm_state,
        "transfer_state": getattr(status_data, "transfer_state", None),
        "transfer_detail": getattr(status_data, "transfer_detail", None),
        "result_destination": getattr(status_data, "result_destination", None) or job.result_destination,
        "ssh_profile_nickname": getattr(status_data, "ssh_profile_nickname", None),
        "work_directory": getattr(status_data, "work_directory", None) or _effective_job_work_directory(job),
        "workflow_usage": workflow_usage if workflow_usage is not None else getattr(job, "workflow_usage_json", None),
        "workflow_usage_synced_at": workflow_usage_synced_at or (job.workflow_usage_synced_at.isoformat() if getattr(job, "workflow_usage_synced_at", None) else None),
        **_import_status_payload(job),
        **_job_timing_payload(job),
    }


async def _backfill_terminal_slurm_workflow_usage(session, job) -> None:
    if getattr(job, "workflow_usage_json", None) is not None:
        return

    workflow_usage = None
    local_work_dir = getattr(job, "nextflow_work_dir", None)
    if local_work_dir:
        try:
            trace_file = _find_nextflow_trace_file(Path(local_work_dir))
        except Exception:
            trace_file = None
        if trace_file:
            workflow_usage = summarize_nextflow_trace_file(trace_file, accounting_mode="slurm")
            if workflow_usage is not None:
                workflow_usage = {
                    **workflow_usage,
                    "usage_status": "partial",
                    "usage_message": "Using Nextflow trace only; scheduler accounting unavailable.",
                }

    if workflow_usage is None:
        workflow_usage = build_unavailable_workflow_usage(accounting_mode="slurm")

    job.workflow_usage_json = workflow_usage
    job.workflow_usage_synced_at = datetime.utcnow()
    await session.commit()


def _normalize_reference_genome(raw_value: str | list[str] | None) -> list[str]:
    value = raw_value
    if isinstance(value, str) and value.startswith("["):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _normalize_workflow_name(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Workflow name is required")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise HTTPException(status_code=400, detail="Workflow name cannot contain path separators")
    if re.fullmatch(r"workflow\d+", value, flags=re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Workflow names like workflowN are reserved")
    return value


def _replace_path_prefix(path_value: str | None, old_prefix: str | None, new_prefix: str | None) -> str | None:
    if not path_value or not old_prefix or not new_prefix:
        return path_value
    if path_value == old_prefix:
        return new_prefix
    normalized_old = old_prefix.rstrip("/")
    if path_value.startswith(f"{normalized_old}/"):
        return f"{new_prefix.rstrip('/')}" + path_value[len(normalized_old):]
    return path_value


def _get_workflow_executor_or_400(workflow_key: str | None):
    try:
        return get_workflow_executor(workflow_key)
    except UnknownWorkflowKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _rename_local_workflow_artifacts(work_dir: Path, old_sample_names: list[str], new_sample_name: str) -> None:
    seen: set[str] = set()
    for sample_name in old_sample_names:
        cleaned = str(sample_name or "").strip()
        if not cleaned or cleaned == new_sample_name or cleaned in seen:
            continue
        seen.add(cleaned)
        for suffix in ("report.html", "timeline.html", "trace.txt"):
            source_path = work_dir / f"{cleaned}_{suffix}"
            target_path = work_dir / f"{new_sample_name}_{suffix}"
            if not source_path.exists():
                continue
            if target_path.exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot rename workflow artifacts because {target_path.name} already exists",
                )
            source_path.rename(target_path)


def _resolve_project_workflow_root(
    *,
    user_id: str,
    project_id: str,
    username: str | None,
    project_slug: str | None,
) -> Path:
    if username and project_slug:
        return AGOUTIC_DATA / "users" / username / project_slug
    return AGOUTIC_DATA / "users" / user_id / project_id


def _allocate_import_workflow_directory(project_dir: Path, workflow_index: int) -> tuple[int, Path]:
    current_index = max(1, int(workflow_index))
    project_dir.mkdir(parents=True, exist_ok=True)
    while True:
        candidate = project_dir / f"workflow{current_index}"
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return current_index, candidate
        except FileExistsError:
            current_index += 1


def _merge_import_metadata(
    req: ImportWorkflowRequest,
    *,
    workflow_key: str,
    inferred_metadata,
    source_job: DogmeJob | None,
) -> tuple[str | None, str | None, list[str], str | None]:
    inferred_sample = getattr(inferred_metadata, "sample_name", None)
    inferred_mode = getattr(inferred_metadata, "mode", None)
    inferred_reference = list(getattr(inferred_metadata, "reference_genome", []) or [])
    inferred_modifications = getattr(inferred_metadata, "modifications", None)

    source_job_reference = _normalize_reference_genome(getattr(source_job, "reference_genome", None)) if source_job else []

    sample_name = (req.sample_name or inferred_sample or getattr(source_job, "sample_name", None) or "").strip() or None
    mode = (req.mode or inferred_mode or getattr(source_job, "mode", None) or "").strip().upper() or None
    reference_genome = list(req.reference_genome or inferred_reference or source_job_reference)

    modifications = None
    if "modifications" in req.model_fields_set:
        modifications = req.modifications
    elif inferred_modifications is not None:
        modifications = inferred_modifications
    elif source_job is not None:
        modifications = source_job.modifications

    if mode == "CDNA" and modifications is None:
        modifications = ""

    if workflow_key == "wf_pore_c":
        mode = None
        modifications = None

    return sample_name, mode, reference_genome, modifications


def _missing_import_fields(
    *,
    workflow_key: str,
    sample_name: str | None,
    mode: str | None,
    reference_genome: list[str],
    modifications: str | None,
) -> list[str]:
    missing: list[str] = []
    if not sample_name:
        missing.append("sample_name")
    if workflow_key == "dogme" and not mode:
        missing.append("mode")
    if not reference_genome:
        missing.append("reference_genome")
    if workflow_key == "dogme" and modifications is None:
        missing.append("modifications")
    return missing


def _copied_config_path(destination_dir: Path, source_dir: Path, source_config_path: str | None) -> str | None:
    if not source_config_path:
        return None
    source_path = Path(source_config_path)
    try:
        relative_path = source_path.relative_to(source_dir)
    except ValueError:
        relative_path = Path(source_path.name)
    candidate = destination_dir / relative_path
    if candidate.exists():
        return str(candidate)
    fallback = destination_dir / source_path.name
    if fallback.exists():
        return str(fallback)
    return None


def _import_status_payload(job) -> dict[str, object]:
    imported_complete = getattr(job, "imported_source_complete", None)
    return {
        "imported_source_kind": getattr(job, "imported_source_kind", None),
        "imported_source_path": getattr(job, "imported_source_path", None),
        "imported_source_run_uuid": getattr(job, "imported_source_run_uuid", None),
        "imported_copy_mode": getattr(job, "imported_copy_mode", None),
        "imported_source_complete": imported_complete,
        "import_warning_message": import_warning_message(imported_complete),
    }


async def _refresh_imported_source_state(job: DogmeJob) -> None:
    imported_source_kind = str(getattr(job, "imported_source_kind", "") or "").strip().lower()
    if imported_source_kind not in {"local", "slurm"}:
        return

    inferred_metadata = None
    try:
        if imported_source_kind == "local":
            source_path = str(getattr(job, "imported_source_path", "") or "").strip()
            if not source_path:
                return
            inferred_metadata = infer_local_workflow_metadata(Path(source_path))
        else:
            source_path = str(getattr(job, "imported_source_path", "") or "").strip()
            ssh_profile_id = getattr(job, "ssh_profile_id", None)
            user_id = getattr(job, "user_id", None)
            if not source_path or not ssh_profile_id or not user_id:
                return
            ssh_profile = await get_ssh_profile(ssh_profile_id, user_id)
            if ssh_profile is None:
                return
            ssh_manager = SSHConnectionManager()
            conn = await ssh_manager.connect(ssh_profile)
            try:
                inferred_metadata = await infer_remote_workflow_metadata(conn, source_path)
            finally:
                await conn.close()
    except Exception as exc:
        logger.warning(
            "Failed to refresh imported source metadata before sync",
            run_uuid=getattr(job, "run_uuid", None),
            error=_describe_exception(exc),
        )
        return

    if inferred_metadata is None:
        return

    new_complete = getattr(inferred_metadata, "source_complete", None)
    new_config_path = getattr(inferred_metadata, "config_path", None)
    if (
        new_complete != getattr(job, "imported_source_complete", None)
        or (new_config_path and new_config_path != getattr(job, "imported_config_path", None))
    ):
        job.imported_source_complete = new_complete
        if new_config_path:
            job.imported_config_path = new_config_path
        await sessionless_commit_job(job)


async def _copy_imported_local_results(job: DogmeJob) -> dict[str, object]:
    source_path = str(getattr(job, "imported_source_path", "") or "").strip()
    destination_path = str(getattr(job, "nextflow_work_dir", "") or "").strip()
    if not source_path or not destination_path:
        raise ValueError("Imported local workflow is missing source or destination paths")

    source_dir = Path(source_path)
    destination_dir = Path(destination_path)
    full_copy = str(getattr(job, "imported_copy_mode", "subset") or "subset").strip().lower() == "full"

    job.transfer_state = "downloading_outputs"
    await sessionless_commit_job(job)
    copy_local_results_to_workflow(
        source_dir,
        destination_dir,
        full_copy=full_copy,
        workflow_key=getattr(job, "workflow_key", None),
    )
    job.transfer_state = "outputs_downloaded"
    job.status = JobStatus.COMPLETED
    job.progress_percent = 100
    job.completed_at = datetime.utcnow()
    job.nextflow_config_path = _copied_config_path(destination_dir, source_dir, getattr(job, "imported_config_path", None))
    await sessionless_commit_job(job)
    return {
        "success": True,
        "status": "outputs_downloaded",
        "message": "Workflow outputs synchronized into the project workflow directory.",
        "run_uuid": job.run_uuid,
        "remote_work_dir": getattr(job, "remote_work_dir", None),
        "local_work_dir": destination_path,
        "transfer_state": job.transfer_state,
        "import_warning_message": import_warning_message(getattr(job, "imported_source_complete", None)),
    }


async def sessionless_commit_job(job: DogmeJob) -> None:
    session = SessionLocal()
    try:
        current = await get_job(session, job.run_uuid)
        if current is None:
            raise ValueError(f"Job not found: {job.run_uuid}")
        tracked_fields = (
            "transfer_state",
            "status",
            "progress_percent",
            "completed_at",
            "error_message",
            "nextflow_config_path",
            "run_stage",
        )
        for field_name in tracked_fields:
            setattr(current, field_name, getattr(job, field_name, None))
        await session.commit()
    finally:
        await session.close()


def _build_import_response(job: DogmeJob, *, message: str, transfer_state: str | None = None) -> ImportWorkflowResponse:
    status = job.status.value if isinstance(job.status, JobStatus) else str(job.status)
    return ImportWorkflowResponse(
        run_uuid=job.run_uuid,
        sample_name=job.sample_name,
        mode=job.mode,
        status=status,
        work_directory=str(getattr(job, "nextflow_work_dir", "") or ""),
        execution_mode=str(getattr(job, "execution_mode", "local") or "local"),
        transfer_state=transfer_state or getattr(job, "transfer_state", None),
        imported_source_kind=str(getattr(job, "imported_source_kind", "") or ""),
        imported_source_complete=getattr(job, "imported_source_complete", None),
        import_warning_message=import_warning_message(getattr(job, "imported_source_complete", None)),
        message=message,
    )


async def _sync_imported_or_remote_job(job: DogmeJob, *, force: bool = False) -> dict[str, object]:
    imported_source_kind = str(getattr(job, "imported_source_kind", "") or "").strip().lower()
    if imported_source_kind in {"local", "slurm"}:
        await _refresh_imported_source_state(job)

    if imported_source_kind == "local":
        try:
            return await _copy_imported_local_results(job)
        except Exception as exc:
            job.transfer_state = "transfer_failed"
            job.error_message = _describe_exception(exc)
            await sessionless_commit_job(job)
            return {
                "success": False,
                "status": "transfer_failed",
                "message": _describe_exception(exc),
                "run_uuid": job.run_uuid,
                "remote_work_dir": getattr(job, "remote_work_dir", None),
                "local_work_dir": getattr(job, "nextflow_work_dir", None),
                "transfer_state": "transfer_failed",
                "import_warning_message": import_warning_message(getattr(job, "imported_source_complete", None)),
            }

    if imported_source_kind == "slurm" or (getattr(job, "execution_mode", "local") or "local").strip().lower() == "slurm":
        backend = get_backend("slurm")
        result = await backend.sync_results_to_local(run_uuid=job.run_uuid, force=force)
        result.setdefault(
            "import_warning_message",
            import_warning_message(getattr(job, "imported_source_complete", None)),
        )
        return result

    return {
        "success": False,
        "status": "not_applicable",
        "message": "This job does not have an import source available for explicit sync.",
        "run_uuid": job.run_uuid,
        "remote_work_dir": getattr(job, "remote_work_dir", None),
        "local_work_dir": getattr(job, "nextflow_work_dir", None),
        "transfer_state": getattr(job, "transfer_state", None),
        "import_warning_message": import_warning_message(getattr(job, "imported_source_complete", None)),
    }


@app.post("/jobs/import", response_model=ImportWorkflowResponse)
async def import_existing_workflow(req: ImportWorkflowRequest):
    session = SessionLocal()

    try:
        source_kind = str(req.source_kind or "local").strip().lower()
        if source_kind not in {"local", "slurm"}:
            raise HTTPException(status_code=400, detail="source_kind must be 'local' or 'slurm'")

        if source_kind == "local":
            normalized_source_path = normalize_local_workflow_source(req.source_path)
            source_dir = Path(normalized_source_path)
            if not source_dir.exists() or not source_dir.is_dir():
                raise HTTPException(status_code=400, detail=f"Local workflow directory not found: {normalized_source_path}")
        else:
            normalized_source_path = normalize_remote_workflow_source(req.source_path)

        existing_source_job = await find_job_by_workflow_path(session, normalized_source_path)
        if existing_source_job is not None and existing_source_job.project_id == req.project_id and str(existing_source_job.status or "").upper() != JobStatus.DELETED.value:
            workflow_name = getattr(existing_source_job, "workflow_folder_name", None) or getattr(existing_source_job, "workflow_alias", None) or existing_source_job.run_uuid
            raise HTTPException(
                status_code=409,
                detail=f"This workflow is already registered in the current project as {workflow_name}.",
            )

        duplicate = await find_import_duplicate(
            session,
            project_id=req.project_id,
            source_kind=source_kind,
            source_path=normalized_source_path,
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail=f"This workflow was already imported into the current project as {duplicate.workflow_folder_name or duplicate.workflow_alias or duplicate.run_uuid}.",
            )

        ssh_profile = None
        if source_kind == "local":
            inferred_metadata = infer_local_workflow_metadata(source_dir)
        else:
            ssh_profile_id = req.ssh_profile_id or await get_default_ssh_profile_id(
                session,
                user_id=req.user_id,
                project_id=req.project_id,
            )
            if not ssh_profile_id:
                raise HTTPException(
                    status_code=400,
                    detail="No default SSH profile is configured for this project. Save SLURM defaults or specify a profile first.",
                )
            ssh_profile = await get_ssh_profile(ssh_profile_id, req.user_id)
            if ssh_profile is None:
                raise HTTPException(status_code=404, detail=f"SSH profile not found: {ssh_profile_id}")

            ssh_manager = SSHConnectionManager()
            conn = await ssh_manager.connect(ssh_profile)
            try:
                if not await conn.path_exists(normalized_source_path):
                    raise HTTPException(status_code=400, detail=f"Remote workflow directory not found: {normalized_source_path}")
                inferred_metadata = await infer_remote_workflow_metadata(conn, normalized_source_path)
            finally:
                await conn.close()

        workflow_key = str(
            getattr(existing_source_job, "workflow_key", None)
            or getattr(inferred_metadata, "workflow_key", None)
            or "dogme"
        ).strip().lower() or "dogme"
        sample_name, mode, reference_genome, modifications = _merge_import_metadata(
            req,
            workflow_key=workflow_key,
            inferred_metadata=inferred_metadata,
            source_job=existing_source_job,
        )
        missing_fields = _missing_import_fields(
            workflow_key=workflow_key,
            sample_name=sample_name,
            mode=mode,
            reference_genome=reference_genome,
            modifications=modifications,
        )
        if missing_fields:
            config_hint = getattr(inferred_metadata, "config_path", None)
            config_suffix = f" Parsed config: {config_hint}." if config_hint else ""
            raise HTTPException(
                status_code=400,
                detail=(
                    "Import could not infer all required metadata. Missing: "
                    f"{', '.join(missing_fields)}.{config_suffix}"
                ),
            )

        next_workflow_index = await get_next_workflow_index(session, req.project_id)
        project_dir = _resolve_project_workflow_root(
            user_id=req.user_id,
            project_id=req.project_id,
            username=req.username,
            project_slug=req.project_slug,
        )
        workflow_index, work_dir = _allocate_import_workflow_directory(project_dir, next_workflow_index)
        workflow_alias = workflow_alias_for_index(workflow_index)

        run_uuid = str(uuid.uuid4())
        job = await create_job(
            session,
            run_uuid=run_uuid,
            project_id=req.project_id,
            workflow_index=workflow_index,
            workflow_alias=workflow_alias,
            workflow_folder_name=workflow_alias,
            workflow_display_name=sample_name,
            workflow_key=workflow_key,
            sample_name=sample_name,
            mode=mode,
            input_directory=getattr(inferred_metadata, "input_directory", None) or normalized_source_path,
            reference_genome=reference_genome,
            modifications=modifications,
            user_id=req.user_id,
        )
        job.execution_mode = "slurm" if source_kind == "slurm" else "local"
        job.nextflow_work_dir = str(work_dir)
        job.result_destination = "local"
        job.workflow_display_name = sample_name
        job.imported_source_kind = source_kind
        job.imported_source_path = normalized_source_path
        job.imported_source_run_uuid = existing_source_job.run_uuid if existing_source_job is not None else None
        job.imported_config_path = getattr(inferred_metadata, "config_path", None)
        job.imported_copy_mode = "full" if req.full_copy else "subset"
        job.imported_source_complete = getattr(inferred_metadata, "source_complete", None)
        job.run_stage = "IMPORTING_RESULTS"
        job.started_at = datetime.utcnow()

        if source_kind == "slurm":
            job.status = JobStatus.COMPLETED
            job.progress_percent = 100
            job.completed_at = datetime.utcnow()
            job.remote_work_dir = normalized_source_path
            job.remote_output_dir = str(PurePosixPath(normalized_source_path) / "output")
            job.ssh_profile_id = ssh_profile.id if ssh_profile is not None else None
            job.transfer_state = "pending_import"

        await session.commit()

        await add_log_entry(
            session,
            run_uuid,
            "INFO",
            f"Import registered from {source_kind} workflow source: {normalized_source_path}",
            source="import",
        )

        warning = import_warning_message(getattr(job, "imported_source_complete", None))
        if source_kind == "local":
            try:
                copy_local_results_to_workflow(
                    source_dir,
                    work_dir,
                    full_copy=req.full_copy,
                    workflow_key=workflow_key,
                )
                job.status = JobStatus.COMPLETED
                job.progress_percent = 100
                job.transfer_state = "outputs_downloaded"
                job.completed_at = datetime.utcnow()
                job.nextflow_config_path = _copied_config_path(work_dir, source_dir, getattr(inferred_metadata, "config_path", None))
                await session.commit()
                message = f"Imported workflow into {work_dir.name}."
                if warning:
                    message = f"{message} {warning}"
                return _build_import_response(job, message=message)
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.transfer_state = "transfer_failed"
                job.error_message = _describe_exception(exc)
                await session.commit()
                raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc

        try:
            backend_result = await _sync_imported_or_remote_job(job, force=True)
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.transfer_state = "transfer_failed"
            job.error_message = _describe_exception(exc)
            await session.commit()
            raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc

        if not backend_result.get("success"):
            refreshed_job = await get_job(session, run_uuid)
            if refreshed_job is not None:
                refreshed_job.status = JobStatus.FAILED
                refreshed_job.transfer_state = str(backend_result.get("transfer_state") or "transfer_failed")
                refreshed_job.error_message = str(backend_result.get("message") or "Import sync failed")
                await session.commit()
            raise HTTPException(status_code=500, detail=str(backend_result.get("message") or "Import sync failed"))

        message = f"Import started for {work_dir.name}."
        if warning:
            message = f"{message} {warning}"
        return _build_import_response(
            job,
            message=message,
            transfer_state=str(backend_result.get("transfer_state") or getattr(job, "transfer_state", None) or ""),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_describe_exception(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
    except Exception as exc:
        logger.exception("Workflow import failed", source_path=req.source_path, source_kind=req.source_kind)
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
    finally:
        await session.close()

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


def _read_log_text(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except Exception:
        return ""


def _tail_text(text: str, *, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _read_log_tail(path: str, *, max_chars: int = 4000) -> str:
    """Read a safe tail snippet from a log file for status/error surfacing."""
    return _tail_text(_read_log_text(path), max_chars=max_chars)


def _tail_log_lines(path: str | None, *, max_lines: int = 40, max_chars: int = 12000) -> list[str]:
    if not path:
        return []
    text = _read_log_tail(path, max_chars=max_chars)
    if not text:
        return []
    return [line for line in text.splitlines() if line.strip()][-max_lines:]


def _classify_live_script_log_level(source: str, message: str) -> str:
    stripped = (message or "").strip()
    lowered = stripped.lower()

    if source == "script-stdout-live":
        return "INFO"

    if not stripped:
        return "INFO"

    if stripped.startswith("[WARN]") or stripped.startswith("WARNING:"):
        return "WARN"

    error_markers = (
        "[error]",
        "error:",
        "traceback",
        "exception",
        "failed",
        "fatal",
    )
    if any(marker in lowered for marker in error_markers):
        return "ERROR"

    return "INFO"


def _parse_job_report(job) -> dict:
    raw = getattr(job, "report_json", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_reconcile_script_job(job, report: dict | None = None) -> bool:
    report = report or _parse_job_report(job)
    script_id = (report.get("script_id") or "").strip()
    script_path = Path(report.get("script_path") or "") if report.get("script_path") else None
    if script_id in {"reconcile_bams/reconcile_bams", "reconcile_bams/reconcileBams"}:
        return True
    if script_path is None:
        return False
    return script_path.name in {"reconcile_bams.py", "reconcileBams.py"}


def _infer_reconcile_progress(stdout_lines: list[str], stderr_lines: list[str]) -> dict[str, str | int | None]:
    markers = [
        ("=== Parsing reference annotation", "Parsing reference annotation", 10),
        ("=== Step 1: Collecting structures and assigning IDs", "Collecting transcript structures", 30),
        ("=== Step 1.5: Consolidating KNOWN/ISM variants", "Consolidating transcript variants", 45),
        ("=== Summary of Findings ===", "Summarizing reconcile findings", 55),
        ("Wrote per-sample novelty counts to:", "Writing novelty summaries", 60),
        ("=== Step 2: Rewriting BAM files", "Rewriting BAM files", 75),
        ("Finished rewriting ", "Rewriting BAM files", 82),
        ("=== Step 3: Generating DOGME annotation files", "Generating annotation outputs", 90),
        ("Writing ", "Writing reconcile output files", 94),
        ("=== ✅ All tasks complete! ===", "Reconcile complete", 100),
    ]

    current_step = None
    current_detail = None
    progress_percent = 5
    for line in [*stdout_lines, *stderr_lines]:
        stripped = line.strip()
        if not stripped:
            continue
        for marker, label, progress in markers:
            if marker in stripped:
                current_step = label
                current_detail = stripped
                progress_percent = progress

    if current_step is None:
        latest_line = next((line.strip() for line in reversed(stdout_lines) if line.strip()), "")
        latest_err = next((line.strip() for line in reversed(stderr_lines) if line.strip()), "")
        if latest_line:
            current_step = "Running reconcile script"
            current_detail = latest_line
            progress_percent = 50
        elif latest_err:
            current_step = "Running reconcile script"
            current_detail = latest_err
            progress_percent = 50
        else:
            current_step = "Preparing reconcile workflow"
            current_detail = "Launching reconcileBams.py"
            progress_percent = 5

    return {
        "current_step": current_step,
        "current_step_detail": current_detail,
        "progress_percent": progress_percent,
        "message": current_detail or current_step,
    }


def _build_live_script_status(job) -> dict[str, str | int | None]:
    stdout_lines = _tail_log_lines(getattr(job, "log_file", None), max_lines=80)
    stderr_lines = _tail_log_lines(getattr(job, "stderr_log", None), max_lines=40)
    report = _parse_job_report(job)

    if _is_reconcile_script_job(job, report=report):
        return _infer_reconcile_progress(stdout_lines, stderr_lines)

    latest_stdout = next((line.strip() for line in reversed(stdout_lines) if line.strip()), "")
    latest_stderr = next((line.strip() for line in reversed(stderr_lines) if line.strip()), "")
    message = latest_stderr or latest_stdout or "Script job running."
    return {
        "current_step": "Running script",
        "current_step_detail": message,
        "progress_percent": 50,
        "message": message,
    }


def _build_live_script_logs(job, *, limit: int) -> list[dict[str, str]]:
    timestamp = datetime.utcnow().isoformat()
    live_logs: list[dict[str, str]] = []
    for source, path in (
        ("script-stdout-live", getattr(job, "log_file", None)),
        ("script-stderr-live", getattr(job, "stderr_log", None)),
    ):
        for line in _tail_log_lines(path, max_lines=max(1, limit)):
            live_logs.append(
                {
                    "timestamp": timestamp,
                    "level": _classify_live_script_log_level(source, line),
                    "message": line,
                    "source": source,
                }
            )
    return live_logs[-limit:]


def _parse_script_output_directory(stdout_text: str) -> str | None:
    """Extract an explicit output directory from script stdout.

    Many agoutic scripts print a line like ``Outputs are in: /path/to/dir``
    when they finish.  If present, the path is more accurate than the script's
    working directory and should be used as the job's work_directory.
    """
    import re as _re

    try:
        parsed = json.loads(stdout_text)
    except Exception:
        parsed = None

    if isinstance(parsed, dict):
        workflow_payload = parsed.get("workflow") if isinstance(parsed.get("workflow"), dict) else {}
        for candidate in (
            workflow_payload.get("directory"),
            workflow_payload.get("output_directory"),
            parsed.get("work_directory"),
            parsed.get("output_directory"),
        ):
            sanitized = _sanitize_work_directory(candidate)
            if sanitized and os.path.isabs(sanitized):
                return sanitized

    for line in stdout_text.splitlines():
        m = _re.search(r'Outputs are in:\s*(.+)', line)
        if not m:
            continue
        candidate = _sanitize_work_directory(m.group(1))
        if candidate and os.path.isabs(candidate):
            return candidate
    return None


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
        stdout_text = _read_log_text(stdout_log_path)
        stderr_text = _read_log_text(stderr_log_path)
        stdout_tail = _tail_text(stdout_text)
        stderr_tail = _tail_text(stderr_text)

        final_status = JobStatus.COMPLETED if return_code == 0 else JobStatus.FAILED
        progress = 100 if return_code == 0 else 0
        error_message = None if return_code == 0 else (stderr_tail.strip() or f"Script exited with code {return_code}")

        job = await get_job(session, run_uuid)
        if job:
            job.status = final_status
            job.progress_percent = progress
            job.error_message = error_message
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
            # If the script printed an explicit output directory, use it as
            # the authoritative work_directory so downstream "list files"
            # calls land in the right place instead of the script cwd.
            if return_code == 0:
                _script_output_dir = _parse_script_output_directory(stdout_text)
                if _script_output_dir:
                    job.nextflow_work_dir = _script_output_dir
                    inferred_index = infer_workflow_index_from_path(_script_output_dir)
                    if inferred_index is not None:
                        job.workflow_index = inferred_index
                        job.workflow_alias = workflow_alias_for_index(inferred_index)
                        job.workflow_folder_name = Path(_script_output_dir).name
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


async def _recover_staging_tasks_background() -> None:
    """Recover durable staging tasks without blocking Launchpad startup."""
    from launchpad.backends.staging_worker import recover_staging_tasks

    try:
        recovered_tasks = await recover_staging_tasks()
    except Exception as exc:
        logger.error(
            "Launchpad staging recovery failed",
            error=_describe_exception(exc),
            exc_info=True,
        )
        return

    logger.info(
        "Launchpad staging recovery completed",
        recovered_staging_tasks=len(recovered_tasks),
    )

# --- LIFECYCLE ---
@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    from common.database import is_sqlite
    if is_sqlite():
        await init_db()  # Auto-create tables for local SQLite dev
    # For Postgres, tables are managed by Alembic migrations

    # Start background staging maintenance only after the service is live.
    from launchpad.backends.staging_worker import periodic_cleanup

    asyncio.create_task(periodic_cleanup())
    asyncio.create_task(_recover_staging_tasks_background())

    logger.info(
        "Launchpad initialized",
        max_concurrent_jobs=MAX_CONCURRENT_JOBS,
        poll_interval=JOB_POLL_INTERVAL,
        staging_recovery="scheduled",
    )

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
                workflow_usage = status_info.get("workflow_usage")
                workflow_usage_synced_at = _coerce_workflow_usage_synced_at(
                    status_info.get("workflow_usage_synced_at")
                )
                
                # Update database
                await update_job_status(
                    session,
                    run_uuid,
                    job_status,
                    progress=progress,
                    workflow_usage=workflow_usage,
                    workflow_usage_synced_at=workflow_usage_synced_at,
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
    async with SessionLocal() as session:
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


@app.post("/remote/stage")
async def stage_remote_sample(
    req: StageRemoteSampleRequest,
    sync: bool = Query(False, description="Run synchronously and return result directly"),
):
    """Stage references and input data remotely without submitting a scheduler job.

    By default the upload runs in the background and a ``task_id`` is returned
    immediately.  Poll ``GET /remote/stage/{task_id}`` for progress.

    Pass ``?sync=true`` to fall back to the legacy synchronous behaviour
    (blocks until the transfer completes or times out).
    """
    from launchpad.backends.staging_worker import (
        StagingTaskState,
        get_staging_tasks,
        active_task_count,
        new_task_id,
        persist_staging_task,
        start_staging_task,
        MAX_CONCURRENT_STAGING_TASKS,
    )

    params_dict = {
        "project_id": req.project_id,
        "user_id": req.user_id,
        "username": req.username,
        "project_slug": req.project_slug,
        "sample_name": req.sample_name,
        "mode": req.mode,
        "input_directory": req.input_directory,
        "reference_genome": req.reference_genome,
        "ssh_profile_id": req.ssh_profile_id,
        "remote_base_path": req.remote_base_path,
        "remote_input_path": req.remote_input_path,
        "staged_remote_input_path": req.remote_input_path,
    }

    # --- Legacy synchronous mode ---
    if sync:
        backend = get_backend("slurm")
        try:
            result = await backend.stage_remote_sample(
                SubmitParams(**params_dict)
            )
            return result
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=_describe_exception(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_describe_exception(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
        except Exception as exc:
            logger.exception("Remote stage failed", sample_name=req.sample_name, ssh_profile_id=req.ssh_profile_id)
            raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc

    # --- Async background mode (default) ---
    if active_task_count() >= MAX_CONCURRENT_STAGING_TASKS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent staging tasks ({MAX_CONCURRENT_STAGING_TASKS}). "
            "Wait for a running transfer to finish before starting another.",
        )

    task = StagingTaskState(task_id=new_task_id(), params=params_dict)
    await persist_staging_task(task, force=True, raise_on_error=True)
    staging_tasks = get_staging_tasks()
    staging_tasks[task.task_id] = task
    start_staging_task(task)
    logger.info("Background staging task created", task_id=task.task_id, sample_name=req.sample_name)

    return StageTaskAcceptedResponse(task_id=task.task_id, status=task.status)


@app.get("/remote/stage/{task_id}", response_model=StagingTaskStatusResponse)
async def get_staging_task_status(task_id: str = FastAPIPath(..., min_length=1)):
    """Poll the status of a background staging task."""
    from launchpad.backends.staging_worker import (
        get_staging_tasks,
        hydrate_incomplete_staging_tasks,
        resume_queued_staging_tasks,
    )

    task = get_staging_tasks().get(task_id)
    if task is None:
        await hydrate_incomplete_staging_tasks(task_id=task_id)
        task = get_staging_tasks().get(task_id)
    if task is not None and task.status == "queued" and (task._task is None or task._task.done()):
        await resume_queued_staging_tasks(profile_id=task.params.get("ssh_profile_id"))
        task = get_staging_tasks().get(task_id)
    if task is not None:
        return StagingTaskStatusResponse(**task.to_dict())

    task_record = await get_staging_task_record(task_id)
    if task_record is None:
        raise HTTPException(status_code=404, detail=f"Staging task {task_id} not found")
    return StagingTaskStatusResponse(**staging_task_record_to_dict(task_record))


@app.post("/remote/stage/{task_id}/cancel", response_model=StageTaskCancelResponse)
async def cancel_staging_task_endpoint(task_id: str = FastAPIPath(..., min_length=1)):
    """Cancel a queued or running background staging task."""
    from launchpad.backends.staging_worker import cancel_staging_task

    try:
        task = await cancel_staging_task(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StageTaskCancelResponse(
        task_id=task.task_id,
        status="cancelled",
        message=task.error or "Remote staging cancelled by user.",
    )


@app.post("/remote/stage/{task_id}/resume", response_model=StageTaskResumeResponse)
async def resume_staging_task_endpoint(task_id: str = FastAPIPath(..., min_length=1)):
    """Resume staging by queueing a replacement task from persisted parameters."""
    from launchpad.backends.staging_worker import resume_staging_task

    try:
        task = await resume_staging_task(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StageTaskResumeResponse(
        task_id=task.task_id,
        status=task.status,
        resumed_from_task_id=task_id,
    )


@app.post("/remote/stage/{task_id}/retry", response_model=StageTaskAcceptedResponse)
async def retry_staging_task(task_id: str = FastAPIPath(..., min_length=1)):
    """Re-queue a failed staging task using its stored parameters."""
    from launchpad.backends.staging_worker import requeue_staging_task

    try:
        task = await requeue_staging_task(
            task_id,
            allowed_statuses={"failed"},
            action_name="retried",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StageTaskAcceptedResponse(task_id=task.task_id, status=task.status)


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


@app.post("/workflows/preview", response_model=WorkflowPreviewResponse)
async def preview_workflow(req: WorkflowPreviewRequest):
    """Build a workflow-family preview without submitting a Launchpad job."""
    workflow_executor = _get_workflow_executor_or_400(req.workflow_key)
    preview = workflow_executor.build_preview(**req.model_dump())
    return WorkflowPreviewResponse(
        workflow_key=preview.workflow_key,
        supports_submission=preview.supports_submission,
        command=preview.command,
        preview_markdown=preview.preview_markdown,
        preview_payload=preview.preview_payload,
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
        run_type = (req.run_type or "dogme").strip().lower()
        workflow_key = "dogme" if run_type == "script" else (req.workflow_key or "dogme")
        workflow_executor = None

        if run_type != "script":
            workflow_executor = _get_workflow_executor_or_400(workflow_key)
            if not workflow_executor.supports_submission:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "workflow_key 'wf_pore_c' requires WF_PORE_C_ENABLED=true for local submission"
                        if workflow_executor.workflow_key == "wf_pore_c"
                        else (
                            f"workflow_key '{workflow_executor.workflow_key}' is preview-only in Phase 1 "
                            "and cannot be submitted yet; use /workflows/preview instead"
                        )
                    ),
                )
            try:
                workflow_executor.validate_submission(mode=req.mode)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        workflow_index = None
        workflow_alias = None
        workflow_folder_name = None
        max_gpu_tasks = (
            req.max_gpu_tasks
            if "max_gpu_tasks" in req.model_fields_set
            else DEFAULT_MAX_GPU_TASKS
        )
        if run_type != "script":
            workflow_index, workflow_alias, workflow_folder_name = await get_workflow_identity_for_path(
                session,
                req.project_id,
                req.resume_from_dir,
            )
            if workflow_index is None:
                workflow_index = await get_next_workflow_index(session, req.project_id)
                workflow_alias = workflow_alias_for_index(workflow_index)
                workflow_folder_name = workflow_alias
        
        # Create job record in database
        job = await create_job(
            session,
            run_uuid=run_uuid,
            project_id=req.project_id,
            workflow_index=workflow_index,
            workflow_alias=workflow_alias,
            workflow_folder_name=workflow_folder_name,
            workflow_display_name=req.sample_name if run_type != "script" else None,
            workflow_key=workflow_key,
            sample_name=req.sample_name,
            mode=req.mode,
            input_directory=req.input_directory,
            reference_genome=req.reference_genome,
            modifications=req.modifications,
            parent_block_id=req.parent_block_id,
            user_id=req.user_id,
        )
        job.execution_mode = req.execution_mode
        job.ssh_profile_id = req.ssh_profile_id
        job.slurm_account = req.slurm_account
        job.slurm_partition = req.slurm_partition
        job.slurm_cpus = req.slurm_cpus
        job.local_max_task_cpus = req.local_max_task_cpus
        job.local_max_task_memory_gb = req.local_max_task_memory_gb
        job.slurm_memory_gb = req.slurm_memory_gb
        job.slurm_walltime = req.slurm_walltime
        job.slurm_gpus = req.slurm_gpus
        job.slurm_gpu_type = req.slurm_gpu_type
        job.result_destination = req.result_destination
        job.cache_preflight_json = req.cache_preflight
        job.output_directory = req.output_directory
        if run_type == "script" and req.output_directory:
            output_name = PurePosixPath(req.output_directory).name
            if output_name:
                job.workflow_folder_name = output_name
        await session.commit()

        # Log submission
        await add_log_entry(
            session,
            run_uuid,
            "INFO",
            f"Job submitted: {req.sample_name} (workflow_key={workflow_key}, mode={req.mode or 'n/a'}, run_type={run_type})",
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
                        **script_subprocess_session_kwargs(),
                    )

                script_processes[run_uuid] = process

                job.status = JobStatus.RUNNING
                job.started_at = datetime.utcnow()
                job.run_stage = "SCRIPT_RUNNING"
                job.nextflow_process_id = process.pid
                job.nextflow_work_dir = str(script_cwd)
                if req.output_directory:
                    job.output_directory = req.output_directory
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
                workflow_number = workflow_index
                local_workflow_dir = None
                if req.resume_from_dir:
                    local_workflow_dir = Path(req.resume_from_dir)
                    job.workflow_folder_name = local_workflow_dir.name

                if local_workflow_dir is None:
                    user_key = req.username or req.user_id or "user"
                    project_key = req.project_slug or req.project_id
                    project_dir = AGOUTIC_DATA / "users" / user_key / project_key
                    project_dir.mkdir(parents=True, exist_ok=True)
                    local_workflow_dir = project_dir / NextflowExecutor.workflow_dir_name(workflow_number)
                    local_workflow_dir.mkdir(parents=True, exist_ok=True)

                job.nextflow_work_dir = str(local_workflow_dir) if local_workflow_dir else None
                job.workflow_folder_name = local_workflow_dir.name if local_workflow_dir else job.workflow_folder_name
                await session.commit()

                backend = get_backend("slurm")
                if workflow_executor is None:
                    raise HTTPException(status_code=500, detail="Workflow executor resolution failed")
                backend_submit_kwargs = workflow_executor.build_backend_submit_params(
                    request=req,
                    workflow_number=workflow_number,
                    max_gpu_tasks=max_gpu_tasks,
                )
                await backend.submit(
                    run_uuid,
                    SubmitParams(**backend_submit_kwargs),
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
                if workflow_executor is None:
                    raise HTTPException(status_code=500, detail="Workflow executor resolution failed")
                local_submit_kwargs = workflow_executor.build_local_submit_kwargs(
                    run_uuid=run_uuid,
                    request=req,
                    workflow_index=workflow_index,
                    max_gpu_tasks=max_gpu_tasks,
                )
                run_uuid_returned, work_dir = await executor.submit_job(**local_submit_kwargs)

                # Update job with work directory info
                job.nextflow_work_dir = str(work_dir)
                job.output_directory = str(work_dir)
                job.workflow_folder_name = work_dir.name
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

        except ValueError as e:
            error_detail = _describe_exception(e)
            logger.warning("Workflow submission validation failed", run_uuid=run_uuid, error=error_detail)

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

            raise HTTPException(status_code=400, detail=error_detail) from e

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
            live_status = _build_live_script_status(job)
            return {
                "run_uuid": run_uuid,
                "status": job.status,
                "progress_percent": 100 if job.status == JobStatus.COMPLETED else (int(live_status.get("progress_percent") or job.progress_percent or 50) if job.status == JobStatus.RUNNING else 0),
                "message": job.error_message or str(live_status.get("message") or f"Script job {str(job.status).lower()}."),
                "tasks": {},
                "execution_mode": "local",
                "run_stage": job.run_stage,
                "current_step": live_status.get("current_step"),
                "current_step_detail": live_status.get("current_step_detail"),
                "work_directory": _effective_job_work_directory(job),
                **_job_timing_payload(job),
            }
        
        terminal_statuses = (JobStatus.DELETED, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        pending_local_result_sync = (
            job.execution_mode == "slurm"
            and job.status == JobStatus.COMPLETED
            and (job.result_destination or "").strip().lower() in {"local", "both"}
            and (job.transfer_state or "") != "outputs_downloaded"
        )
        if pending_local_result_sync:
            # Fast path: job already COMPLETED on the cluster — skip the
            # SSH sacct round-trip and return the in-memory transfer detail
            # immediately so the UI sees per-folder progress without delay.
            backend = get_backend("slurm")
            get_transfer_detail = getattr(backend, "get_transfer_detail", None)
            transfer_detail = get_transfer_detail(run_uuid) if callable(get_transfer_detail) else None
            transfer_state = job.transfer_state
            transfer_failed = (transfer_state or "").strip().lower() == "transfer_failed"
            transfer_message = transfer_detail or job.error_message
            return {
                "run_uuid": run_uuid,
                "status": "FAILED" if transfer_failed else "RUNNING",
                "progress_percent": 0 if transfer_failed else 99,
                "message": transfer_message or (
                    "Remote job completed, but copying results back to the local workflow failed."
                    if transfer_failed
                    else "Copying results back to the local workflow..."
                ),
                "tasks": {},
                "execution_mode": "slurm",
                "run_stage": job.run_stage,
                "slurm_job_id": job.slurm_job_id,
                "slurm_state": job.slurm_state,
                "transfer_state": transfer_state,
                "transfer_detail": transfer_message,
                "result_destination": job.result_destination,
                "work_directory": _effective_job_work_directory(job),
                **_workflow_usage_payload(job),
                **_import_status_payload(job),
                **_job_timing_payload(job),
            }
        if job.status in terminal_statuses:
            warning_message = import_warning_message(getattr(job, "imported_source_complete", None))
            work_directory = _effective_job_work_directory(job)
            base_payload = {
                "run_uuid": run_uuid,
                "status": job.status,
                "progress_percent": 100 if job.status == JobStatus.COMPLETED else 0,
                "message": job.error_message or warning_message or f"Job {job.status.lower()}.",
                "tasks": {},
                "work_directory": work_directory,
                **_workflow_usage_payload(job),
                **_import_status_payload(job),
                **_job_timing_payload(job),
            }
            if job.execution_mode == "slurm":
                if job.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                    backend = get_backend("slurm")
                    status_data = await backend.check_status(run_uuid)
                    if not _slurm_status_poll_failed(status_data):
                        return await _slurm_status_payload(session, job, status_data)
                await _backfill_terminal_slurm_workflow_usage(session, job)
                base_payload.update(
                    {
                        "execution_mode": "slurm",
                        "run_stage": job.run_stage,
                        "slurm_job_id": job.slurm_job_id,
                        "slurm_state": job.slurm_state,
                        "transfer_state": job.transfer_state,
                        "transfer_detail": job.error_message if (job.transfer_state or "").strip().lower() == "transfer_failed" else None,
                        "result_destination": job.result_destination,
                    }
                )
            return base_payload
        
        if job.execution_mode == "slurm":
            backend = get_backend("slurm")
            status_data = await backend.check_status(run_uuid)
            return await _slurm_status_payload(session, job, status_data)

        # Get detailed status including tasks from check_status
        executor = NextflowExecutor()
        # Use the actual work directory stored in DB (may be jailed path)
        effective_work_directory = _effective_job_work_directory(job)
        work_dir = Path(effective_work_directory) if effective_work_directory else executor.work_dir / run_uuid
        status_data = await executor.check_status(run_uuid, work_dir)
        workflow_usage = status_data.get("workflow_usage")
        workflow_usage_synced_at = status_data.get("workflow_usage_synced_at")
        if workflow_usage is not None:
            resolved_synced_at = (
                _coerce_workflow_usage_synced_at(workflow_usage_synced_at)
                or _coerce_workflow_usage_synced_at(getattr(job, "workflow_usage_synced_at", None))
                or datetime.utcnow()
            )
            existing_synced_at = _coerce_workflow_usage_synced_at(getattr(job, "workflow_usage_synced_at", None))
            if getattr(job, "workflow_usage_json", None) != workflow_usage or existing_synced_at != resolved_synced_at:
                job.workflow_usage_json = workflow_usage
                job.workflow_usage_synced_at = resolved_synced_at
                await session.commit()
            workflow_usage_synced_at = resolved_synced_at.isoformat()
        
        return {
            "run_uuid": run_uuid,
            "status": status_data.get("status", job.status),
            "progress_percent": status_data.get("progress_percent", job.progress_percent),
            "message": status_data.get("message", job.error_message or f"Status: {job.status}"),
            "tasks": status_data.get("tasks", {}),
            "execution_mode": "local",
            "work_directory": effective_work_directory,
            "workflow_usage": workflow_usage if workflow_usage is not None else getattr(job, "workflow_usage_json", None),
            "workflow_usage_synced_at": workflow_usage_synced_at or (job.workflow_usage_synced_at.isoformat() if getattr(job, "workflow_usage_synced_at", None) else None),
            **_job_timing_payload(job),
        }
    
    finally:
        await session.close()


@app.post("/jobs/sync-results-by-workflow", response_model=JobResultSyncResponse)
async def sync_job_results_by_workflow_label(
    project_id: str = Query(..., min_length=1),
    workflow_label: str = Query(..., min_length=1),
    force: bool = Query(False),
):
    """Resolve a job by project + workflow folder name, then trigger sync."""
    session = SessionLocal()
    try:
        job = await resolve_job_by_workflow_label(session, project_id, workflow_label)
        if not job:
            raise HTTPException(
                status_code=404,
                detail=f"No job found for project={project_id}, workflow={workflow_label}",
            )
        return await _sync_imported_or_remote_job(job, force=force)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_describe_exception(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
    except Exception as exc:
        logger.exception("Manual result sync by workflow label failed",
                         project_id=project_id, workflow_label=workflow_label)
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
    finally:
        await session.close()


@app.post("/jobs/{run_uuid}/sync-results", response_model=JobResultSyncResponse)
async def sync_job_results_to_local(
    run_uuid: str = FastAPIPath(..., min_length=1),
    force: bool = Query(False),
):
    """Manually trigger imported or remote result synchronization into the local workflow directory."""
    session = SessionLocal()
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return await _sync_imported_or_remote_job(job, force=force)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_describe_exception(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
    except Exception as exc:
        logger.exception("Manual result sync failed", run_uuid=run_uuid)
        raise HTTPException(status_code=500, detail=_describe_exception(exc)) from exc
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
        if (job.run_stage or "").startswith("SCRIPT_") and job.status == JobStatus.RUNNING:
            live_logs = _build_live_script_logs(job, limit=limit)
            if live_logs:
                logs = [*logs, *live_logs][-limit:]
        
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
    """Cancel a running job or an in-flight remote result sync."""
    session = SessionLocal()
    
    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.execution_mode == "slurm":
            backend = get_backend("slurm")
            if getattr(backend, "is_result_sync_active", None) and backend.is_result_sync_active(run_uuid):
                result = await backend.cancel_result_sync(run_uuid=run_uuid)
                if not result.get("success"):
                    raise HTTPException(status_code=409, detail=str(result.get("message") or "Cannot cancel result sync"))
                await add_log_entry(
                    session,
                    run_uuid,
                    "WARNING",
                    str(result.get("message") or "Result synchronization cancelled."),
                    source="api",
                )
                return result
        
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
        report = _parse_job_report(job)
        is_script_job = report.get("run_type") == "script" or str(getattr(job, "run_stage", "")).startswith("SCRIPT_")
        script_process = script_processes.get(run_uuid) if is_script_job else None

        _pid = job.nextflow_process_id or getattr(script_process, "pid", None)
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
                if is_script_job:
                    terminate_script_process_tree(process=script_process, pid=_pid, sig=signal.SIGTERM)
                    _process_killed = True
                    _kill_message = f"Job cancelled. Standalone script process group (PID {_pid}) terminated."
                    logger.info("Killed standalone script process group", run_uuid=run_uuid, pid=_pid)
                else:
                    os.kill(_pid, signal.SIGTERM)
                    _process_killed = True
                    _kill_message = f"Job cancelled. Nextflow process (PID {_pid}) terminated."
                    logger.info("Killed Nextflow process", run_uuid=run_uuid, pid=_pid)
            except ProcessLookupError:
                if is_script_job:
                    _kill_message = "Job cancelled. Standalone script process had already exited."
                    logger.info("Standalone script process already exited", run_uuid=run_uuid, pid=_pid)
                else:
                    _kill_message = "Job cancelled. Nextflow process had already exited."
                    logger.info("Nextflow process already exited", run_uuid=run_uuid, pid=_pid)
            except PermissionError:
                if is_script_job:
                    _kill_message = f"Job cancelled. Could not terminate standalone script process group (PID {_pid}): permission denied."
                    logger.warning("Permission denied killing standalone script process group", run_uuid=run_uuid, pid=_pid)
                else:
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

        _work_dir = _resolve_deletable_workflow_dir(job)
        if _work_dir is None:
            raise HTTPException(
                status_code=409,
                detail="Could not determine a safe workflow directory to delete for this job.",
            )
        _deleted_path = None
        _folder_name = job.workflow_folder_name or _work_dir.name
        _file_count = 0

        if _work_dir and _work_dir.exists():
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


@app.post("/jobs/{run_uuid}/rerun", response_model=JobSubmitResponse)
async def rerun_job(run_uuid: str = FastAPIPath(..., min_length=1)):
    """Create a new job attempt that reruns an existing workflow in the same folder."""
    session = SessionLocal()

    try:
        source_job = await get_job(session, run_uuid)
        if not source_job:
            raise HTTPException(status_code=404, detail="Job not found")

        allowed_statuses = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        if source_job.status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot rerun job with status {source_job.status}. Only {', '.join(s.value for s in allowed_statuses)} jobs can be rerun.",
            )

        reference_genome = _normalize_reference_genome(source_job.reference_genome)
        sample_name = getattr(source_job, "workflow_display_name", None) or source_job.sample_name
        archive_sample_names = [source_job.sample_name]
        if sample_name != source_job.sample_name:
            archive_sample_names.append(sample_name)
        workflow_key = getattr(source_job, "workflow_key", None) or "dogme"
        workflow_executor = _get_workflow_executor_or_400(workflow_key)
        if not workflow_executor.supports_submission:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"workflow_key '{workflow_executor.workflow_key}' is preview-only in Phase 1 "
                    "and cannot be rerun yet"
                ),
            )

        rerun_uuid = str(uuid.uuid4())
        rerun_job = await create_job(
            session,
            run_uuid=rerun_uuid,
            project_id=source_job.project_id,
            workflow_index=source_job.workflow_index,
            workflow_alias=source_job.workflow_alias,
            workflow_folder_name=source_job.workflow_folder_name,
            workflow_display_name=getattr(source_job, "workflow_display_name", None) or sample_name,
            workflow_key=workflow_key,
            sample_name=sample_name,
            mode=source_job.mode,
            input_directory=source_job.input_directory,
            reference_genome=reference_genome,
            modifications=source_job.modifications,
            parent_block_id=source_job.parent_block_id,
            user_id=source_job.user_id,
        )
        rerun_job.execution_mode = source_job.execution_mode
        rerun_job.ssh_profile_id = source_job.ssh_profile_id
        rerun_job.slurm_account = source_job.slurm_account
        rerun_job.slurm_partition = source_job.slurm_partition
        rerun_job.slurm_cpus = source_job.slurm_cpus
        rerun_job.local_max_task_cpus = source_job.local_max_task_cpus
        rerun_job.local_max_task_memory_gb = source_job.local_max_task_memory_gb
        rerun_job.slurm_memory_gb = source_job.slurm_memory_gb
        rerun_job.slurm_walltime = source_job.slurm_walltime
        rerun_job.slurm_gpus = source_job.slurm_gpus
        rerun_job.slurm_gpu_type = source_job.slurm_gpu_type
        rerun_job.result_destination = source_job.result_destination
        rerun_job.cache_preflight_json = source_job.cache_preflight_json
        rerun_job.reference_cache_status = source_job.reference_cache_status
        rerun_job.data_cache_status = source_job.data_cache_status
        rerun_job.reference_cache_path = source_job.reference_cache_path
        rerun_job.data_cache_path = source_job.data_cache_path
        await session.commit()

        await add_log_entry(
            session,
            rerun_uuid,
            "INFO",
            f"Rerun requested from workflow alias {source_job.workflow_alias or source_job.workflow_folder_name or run_uuid}",
            source="api",
        )

        execution_mode = (source_job.execution_mode or "local").strip().lower()
        if execution_mode == "slurm":
            if not source_job.remote_work_dir:
                raise HTTPException(status_code=400, detail="Cannot rerun SLURM job without a remote workflow directory")

            backend = get_backend("slurm")
            params = SubmitParams(
                project_id=source_job.project_id,
                user_id=source_job.user_id,
                workflow_key=workflow_key,
                sample_name=sample_name,
                mode=source_job.mode,
                input_type="pod5",
                input_directory=source_job.input_directory,
                reference_genome=reference_genome,
                modifications=source_job.modifications,
                max_gpu_tasks=None,
                resume_from_dir=source_job.nextflow_work_dir,
                rerun_in_place=True,
                ssh_profile_id=source_job.ssh_profile_id,
                slurm_account=source_job.slurm_account,
                slurm_partition=source_job.slurm_partition,
                slurm_cpus=source_job.slurm_cpus,
                local_max_task_cpus=source_job.local_max_task_cpus,
                local_max_task_memory_gb=source_job.local_max_task_memory_gb,
                slurm_memory_gb=source_job.slurm_memory_gb,
                slurm_walltime=source_job.slurm_walltime,
                slurm_gpus=source_job.slurm_gpus,
                slurm_gpu_type=source_job.slurm_gpu_type,
                workflow_number=source_job.workflow_index,
                result_destination=source_job.result_destination or "local",
                cache_preflight=source_job.cache_preflight_json,
                reference_cache_path=source_job.reference_cache_path,
                data_cache_path=source_job.data_cache_path,
            )
            await backend.rerun_existing(
                rerun_uuid,
                params,
                remote_work=source_job.remote_work_dir,
                remote_output=source_job.remote_output_dir or str(Path(source_job.remote_work_dir) / "output"),
                local_work_dir=source_job.nextflow_work_dir,
                archive_sample_names=archive_sample_names,
            )
            await session.refresh(rerun_job)
            rerun_job.nextflow_work_dir = source_job.nextflow_work_dir
            rerun_job.remote_work_dir = source_job.remote_work_dir
            rerun_job.remote_output_dir = source_job.remote_output_dir
            rerun_job.started_at = datetime.utcnow()
            await session.commit()
            return {
                "run_uuid": rerun_uuid,
                "sample_name": sample_name,
                "status": rerun_job.status or JobStatus.PENDING,
                "work_directory": _effective_job_work_directory(rerun_job) or "",
                "cache_actions": {
                    "reference_status": rerun_job.reference_cache_status,
                    "data_status": rerun_job.data_cache_status,
                    "reference_cache_path": rerun_job.reference_cache_path,
                    "data_cache_path": rerun_job.data_cache_path,
                    "cache_preflight": rerun_job.cache_preflight_json,
                },
            }

        if not source_job.nextflow_work_dir:
            raise HTTPException(status_code=400, detail="Cannot rerun local job without a workflow directory")

        _, work_dir = await executor.submit_job(
            run_uuid=rerun_uuid,
            sample_name=sample_name,
            mode=source_job.mode,
            input_type="pod5",
            input_dir=source_job.input_directory,
            reference_genome=reference_genome,
            modifications=source_job.modifications,
            max_gpu_tasks=None,
            local_max_task_cpus=source_job.local_max_task_cpus,
            local_max_task_memory_gb=source_job.local_max_task_memory_gb,
            workflow_index=source_job.workflow_index,
            rerun_in_place=True,
            archive_sample_names=archive_sample_names,
            user_id=source_job.user_id,
            project_id=source_job.project_id,
            resume_from_dir=source_job.nextflow_work_dir,
        )

        rerun_job.nextflow_work_dir = str(work_dir)
        rerun_job.workflow_folder_name = work_dir.name
        rerun_job.status = JobStatus.RUNNING
        rerun_job.started_at = datetime.utcnow()
        pid_file = work_dir / ".nextflow_pid"
        if pid_file.exists():
            try:
                rerun_job.nextflow_process_id = int(pid_file.read_text().strip())
            except (ValueError, OSError):
                pass
        await session.commit()

        monitor_task = asyncio.create_task(monitor_job(rerun_uuid, work_dir))
        job_monitors[rerun_uuid] = monitor_task

        return {
            "run_uuid": rerun_uuid,
            "sample_name": sample_name,
            "status": JobStatus.RUNNING,
            "work_directory": str(work_dir),
            "cache_actions": {
                "reference_status": rerun_job.reference_cache_status,
                "data_status": rerun_job.data_cache_status,
                "reference_cache_path": rerun_job.reference_cache_path,
                "data_cache_path": rerun_job.data_cache_path,
                "cache_preflight": rerun_job.cache_preflight_json,
            },
        }
    finally:
        await session.close()


@app.post("/jobs/{run_uuid}/rename")
async def rename_job_workflow(
    req: WorkflowRenameRequest,
    run_uuid: str = FastAPIPath(..., min_length=1),
):
    """Rename a workflow folder and display/sample name while keeping its stable alias."""
    session = SessionLocal()

    try:
        job = await get_job(session, run_uuid)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        terminal_statuses = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        if job.status not in terminal_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot rename job with status {job.status}. Only {', '.join(s.value for s in terminal_statuses)} jobs can be renamed.",
            )
        if job.workflow_index is None:
            raise HTTPException(status_code=400, detail="Only workflow-backed jobs can be renamed")

        new_name = _normalize_workflow_name(req.new_name)
        current_display_name = getattr(job, "workflow_display_name", None) or job.sample_name
        current_folder_name = getattr(job, "workflow_folder_name", None) or (
            Path(job.nextflow_work_dir).name if job.nextflow_work_dir else new_name
        )

        existing_result = await session.execute(
            select(DogmeJob.run_uuid).where(
                DogmeJob.project_id == job.project_id,
                DogmeJob.workflow_folder_name == new_name,
                DogmeJob.workflow_index != job.workflow_index,
                DogmeJob.status != JobStatus.DELETED,
            )
        )
        if existing_result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Workflow name already exists: {new_name}")

        old_sample_names = [job.sample_name, getattr(job, "workflow_display_name", None)]
        local_old_dir = str(job.nextflow_work_dir or "").strip() or None
        local_new_dir = None
        if local_old_dir:
            source_local_path = Path(local_old_dir)
            target_local_path = source_local_path.with_name(new_name)
            local_new_dir = str(target_local_path)
            if target_local_path != source_local_path and target_local_path.exists():
                raise HTTPException(status_code=409, detail=f"Local workflow folder already exists: {target_local_path}")

        remote_old_dir = str(job.remote_work_dir or "").strip() or None
        remote_new_dir = None
        remote_old_output = str(job.remote_output_dir or "").strip() or None
        remote_new_output = remote_old_output
        if remote_old_dir and not job.ssh_profile_id:
            raise HTTPException(status_code=400, detail="Cannot rename remote workflow without an SSH profile")

        if local_old_dir:
            source_local_path = Path(local_old_dir)
            target_local_path = source_local_path.with_name(new_name)
            if target_local_path != source_local_path and source_local_path.exists():
                source_local_path.rename(target_local_path)
            if target_local_path.exists():
                _rename_local_workflow_artifacts(target_local_path, old_sample_names, new_name)
            local_new_dir = str(target_local_path)

        if remote_old_dir:
            backend = get_backend("slurm")
            remote_new_dir, remote_new_output = await backend.rename_workflow(
                ssh_profile_id=job.ssh_profile_id,
                user_id=job.user_id,
                old_remote_work=remote_old_dir,
                old_sample_names=old_sample_names,
                new_folder_name=new_name,
                new_sample_name=new_name,
            )

        workflow_result = await session.execute(
            select(DogmeJob).where(
                DogmeJob.project_id == job.project_id,
                DogmeJob.workflow_index == job.workflow_index,
            )
        )
        workflow_jobs = list(workflow_result.scalars().all())
        for workflow_job in workflow_jobs:
            workflow_job.sample_name = new_name
            workflow_job.workflow_folder_name = new_name
            workflow_job.workflow_display_name = new_name
            workflow_job.nextflow_work_dir = _replace_path_prefix(workflow_job.nextflow_work_dir, local_old_dir, local_new_dir)
            workflow_job.nextflow_config_path = _replace_path_prefix(workflow_job.nextflow_config_path, local_old_dir, local_new_dir)
            workflow_job.output_directory = _replace_path_prefix(workflow_job.output_directory, local_old_dir, local_new_dir)
            workflow_job.log_file = _replace_path_prefix(workflow_job.log_file, local_old_dir, local_new_dir)
            workflow_job.stderr_log = _replace_path_prefix(workflow_job.stderr_log, local_old_dir, local_new_dir)
            workflow_job.remote_work_dir = _replace_path_prefix(workflow_job.remote_work_dir, remote_old_dir, remote_new_dir)
            workflow_job.remote_output_dir = _replace_path_prefix(workflow_job.remote_output_dir, remote_old_output, remote_new_output)

        await session.commit()

        await add_log_entry(
            session,
            run_uuid,
            "INFO",
            f"Workflow {job.workflow_alias or job.workflow_index} renamed from {current_display_name}/{current_folder_name} to {new_name}",
            source="api",
        )

        return {
            "status": "renamed",
            "run_uuid": run_uuid,
            "workflow_alias": job.workflow_alias,
            "old_name": current_display_name,
            "new_name": new_name,
            "work_directory": local_new_dir or remote_new_dir,
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
        from launchpad.backends.staging_worker import hydrate_incomplete_staging_tasks, resume_queued_staging_tasks

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
        await hydrate_incomplete_staging_tasks(profile_id=profile_id)
        resumed_count = await resume_queued_staging_tasks(profile_id=profile_id)
        payload = get_local_auth_session_manager()._serialize_session(session_obj)
        message = "Local auth session started"
        if resumed_count:
            message = f"Local auth session started; resumed {resumed_count} queued staging task(s)"
        return SSHProfileAuthSessionResult(message=message, **payload)
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
