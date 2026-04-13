import datetime
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import httpx
from fastapi.concurrency import run_in_threadpool
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from common.logging_config import get_logger
from cortex.config import AGOUTIC_DATA, SERVICE_REGISTRY
from cortex.db_helpers import _create_block_internal, _resolve_project_dir
from cortex.llm_validators import get_block_payload
from cortex.models import Project, ProjectBlock, User
from cortex.routes.projects import _slugify
from cortex.remote_stage_status import (
    _make_stage_part,
    _stage_part_progress,
    _reference_stage_message,
    _initial_stage_parts,
    _final_stage_parts,
    _failed_stage_parts,
    _resuming_stage_parts,
    _cancelled_stage_parts,
)
from cortex.task_service import sync_project_tasks

logger = get_logger(__name__)

_WORKFLOW_PLAN_TYPE = "WORKFLOW_PLAN"
_LOCAL_SAMPLE_WORKFLOW = "local_sample_intake"
_REMOTE_SAMPLE_WORKFLOW = "remote_sample_intake"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _remote_path_fingerprint(remote_path: str) -> str:
    cleaned = str(remote_path or "").strip()
    return hashlib.sha256(f"remote:{cleaned}".encode("utf-8")).hexdigest()


def _extract_remote_input_from_input_directory(input_directory: str | None) -> str:
    raw = str(input_directory or "").strip()
    if not raw.lower().startswith("remote:"):
        return ""
    candidate = raw[len("remote:"):].strip()
    if not candidate.startswith("/"):
        return ""
    return candidate.rstrip('.,;:!?')


def _launchpad_internal_headers() -> dict[str, str]:
    from cortex.config import INTERNAL_API_SECRET

    headers: dict[str, str] = {}
    if INTERNAL_API_SECRET:
        headers["X-Internal-Secret"] = INTERNAL_API_SECRET
    return headers


def _launchpad_rest_base_url() -> str:
    return SERVICE_REGISTRY.get("launchpad", {}).get(
        "rest_url", os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
    )


async def _list_user_ssh_profiles(user_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_launchpad_rest_base_url()}/ssh-profiles",
            params={"user_id": user_id},
            headers=_launchpad_internal_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def _resolve_ssh_profile_reference(
    user_id: str,
    ssh_profile_id: str | None,
    ssh_profile_nickname: str | None,
) -> tuple[str | None, str | None]:
    if ssh_profile_id and not ssh_profile_nickname:
        return ssh_profile_id, None
    if not ssh_profile_id and not ssh_profile_nickname:
        return None, None

    profiles = await _list_user_ssh_profiles(user_id)

    if ssh_profile_id:
        for profile in profiles:
            if profile.get("id") == ssh_profile_id:
                return ssh_profile_id, profile.get("nickname")
        return ssh_profile_id, ssh_profile_nickname

    nickname = (ssh_profile_nickname or "").strip().lower()
    if not nickname:
        return None, None

    exact_matches = [
        profile for profile in profiles
        if (profile.get("nickname") or "").strip().lower() == nickname
    ]
    if len(exact_matches) == 1:
        profile = exact_matches[0]
        return profile.get("id"), profile.get("nickname")

    host_matches = [
        profile for profile in profiles
        if nickname in (profile.get("ssh_host") or "").strip().lower()
        or (profile.get("nickname") or "").strip().lower().startswith(nickname)
    ]
    if len(host_matches) == 1:
        profile = host_matches[0]
        return profile.get("id"), profile.get("nickname")

    available_profiles = ", ".join(
        sorted(
            {
                profile.get("nickname") or profile.get("ssh_host") or ""
                for profile in profiles
                if profile.get("nickname") or profile.get("ssh_host")
            }
        )
    ) or "none"
    raise ValueError(
        f"SSH profile {ssh_profile_nickname!r} was not found. Available profiles: {available_profiles}."
    )


async def _get_ssh_profile_auth_session(user_id: str, profile_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_launchpad_rest_base_url()}/ssh-profiles/{profile_id}/auth-session",
            params={"user_id": user_id},
            headers=_launchpad_internal_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


async def _ensure_gate_remote_profile_unlocked(user_id: str, gate_payload: dict) -> None:
    job_params = gate_payload.get("edited_params") or gate_payload.get("extracted_params") or {}
    requested_mode = (job_params.get("requested_execution_mode") or "").strip().lower()
    effective_mode = (job_params.get("execution_mode") or "local").strip().lower()
    if requested_mode and requested_mode in {"local", "slurm"} and effective_mode != requested_mode:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested execution_mode '{requested_mode}' does not match extracted execution_mode "
                f"'{effective_mode}'. Regenerate approval parameters before approving."
            ),
        )

    requested_input_path = str(job_params.get("requested_input_directory") or "").strip()
    effective_input_path = str(job_params.get("input_directory") or "").strip()
    if requested_input_path and requested_input_path.startswith("/") and effective_input_path != requested_input_path:
        raise HTTPException(
            status_code=400,
            detail=(
                "Input path mismatch detected between requested and extracted values. "
                "Regenerate approval parameters before approving."
            ),
        )

    if effective_input_path.startswith("/") and "/media/" in effective_input_path:
        if effective_input_path.count("/media/") > 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Detected rewritten absolute input path (duplicated mount segment). "
                    "Regenerate approval parameters before approving."
                ),
            )

    if (job_params.get("execution_mode") or "local") != "slurm":
        return

    ssh_profile_id, ssh_profile_nickname = await _resolve_ssh_profile_reference(
        user_id,
        job_params.get("ssh_profile_id"),
        job_params.get("ssh_profile_nickname"),
    )
    if not ssh_profile_id:
        raise HTTPException(
            status_code=400,
            detail="SLURM execution requires a saved SSH profile. Select one in the approval gate, then unlock it if needed.",
        )

    if not (job_params.get("remote_base_path") or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "SLURM execution requires a remote base path before approval. "
                "Fill in Remote Base Path or configure it on the saved SSH profile."
            ),
        )

    profiles = await _list_user_ssh_profiles(user_id)
    profile = next((item for item in profiles if item.get("id") == ssh_profile_id), None)
    if not profile:
        raise HTTPException(status_code=400, detail="Selected SSH profile no longer exists.")

    if not profile.get("local_username") or profile.get("auth_method") != "key_file":
        return

    session_info = await _get_ssh_profile_auth_session(user_id, ssh_profile_id)
    if session_info.get("active"):
        return

    profile_label = profile.get("nickname") or ssh_profile_nickname or profile.get("ssh_host") or ssh_profile_id
    raise HTTPException(
        status_code=400,
        detail=(
            f"Remote profile {profile_label!r} is locked. Unlock it in Remote Connection Profiles before approving this SLURM run."
        ),
    )


def _extract_remote_browse_request(user_message: str) -> dict | None:
    msg = (user_message or "").strip()
    if not re.search(
        r"\b(?:list|show|browse|what)\s+(?:the\s+)?(?:top\s+)?(?:files?|folders?|directories?)\b",
        msg,
        re.IGNORECASE,
    ):
        return None

    profile_pattern = re.compile(
        r"\b(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)([a-zA-Z0-9_-]+)(?:\s+profile)?(?:[?.!,]|$)|(?:using|via)\s+(?:the\s+)?([a-zA-Z0-9_-]+)\s+profile)\b",
        re.IGNORECASE,
    )
    profile_match = profile_pattern.search(msg)
    nickname = None
    if profile_match:
        nickname = profile_match.group(1) or profile_match.group(2)

    msg_wo_profile = profile_pattern.sub("", msg).strip()
    path_match = re.search(
        r"\b(?:list|show|browse|what)\s+(?:the\s+)?(?:top\s+)?(?:files?|folders?|directories?)\s+(?:in|under|at|of)\s+(.+?)\s*$",
        msg_wo_profile,
        re.IGNORECASE,
    )
    path = None
    if path_match:
        path = path_match.group(1).strip().strip('"\'`')

    if not nickname:
        return None
    return {
        "ssh_profile_nickname": nickname,
        "path": path or None,
    }


def _extract_remote_execution_request(user_message: str) -> dict | None:
    msg = (user_message or "").strip()
    if not msg:
        return None

    has_remote_intent = bool(re.search(
        r"\b(?:stage|staging|run|submit|launch|analy[sz]e|analyse|process)\b",
        msg,
        re.IGNORECASE,
    ))
    if not has_remote_intent:
        return None

    profile_pattern = re.compile(
        r"\b(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)([a-zA-Z0-9_-]+)(?:\s+profile)?(?:[?.!,]|$)|(?:using|via)\s+(?:the\s+)?([a-zA-Z0-9_-]+)\s+profile)\b",
        re.IGNORECASE,
    )
    profile_match = profile_pattern.search(msg)
    if not profile_match:
        return None

    nickname = profile_match.group(1) or profile_match.group(2)
    if not nickname:
        return None

    return {
        "ssh_profile_nickname": nickname,
        "stage_only": bool(re.search(r"\bstage(?:\s+only)?\b", msg, re.IGNORECASE)),
    }


async def _build_remote_stage_approval_context(
    session,
    *,
    project_id: str,
    owner_id: str,
    user_message: str,
    active_skill: str,
    all_results: dict,
    extract_params,
) -> dict | None:
    if active_skill != "remote_execution":
        return None

    remote_request = _extract_remote_execution_request(user_message)
    if not remote_request:
        return None

    launchpad_results = all_results.get("launchpad") or []
    defaults_data = next(
        (
            result.get("data")
            for result in launchpad_results
            if result.get("tool") == "get_slurm_defaults" and isinstance(result.get("data"), dict)
        ),
        None,
    )
    if not defaults_data:
        return None

    selected_defaults = dict(defaults_data.get("selected_profile_defaults") or {})
    if not selected_defaults:
        profile_defaults = defaults_data.get("ssh_profile_defaults") or []
        if isinstance(profile_defaults, list) and profile_defaults:
            selected_defaults = dict(profile_defaults[0] or {})

    if not selected_defaults:
        listed_profiles = next(
            (
                result.get("data")
                for result in launchpad_results
                if result.get("tool") == "list_ssh_profiles" and isinstance(result.get("data"), list)
            ),
            [],
        )
        wanted_nickname = (remote_request.get("ssh_profile_nickname") or "").strip().lower()
        if wanted_nickname:
            for profile in listed_profiles:
                if not isinstance(profile, dict):
                    continue
                if (profile.get("nickname") or "").strip().lower() != wanted_nickname:
                    continue
                selected_defaults = {
                    "ssh_profile_id": profile.get("id"),
                    "nickname": profile.get("nickname"),
                    "default_slurm_account": profile.get("default_slurm_account"),
                    "default_slurm_partition": profile.get("default_slurm_partition"),
                    "default_slurm_gpu_account": profile.get("default_slurm_gpu_account"),
                    "default_slurm_gpu_partition": profile.get("default_slurm_gpu_partition"),
                    "remote_base_path": profile.get("remote_base_path"),
                }
                break

    if not selected_defaults:
        return None

    params = await extract_params(session, project_id) or {}
    params = dict(params)
    params["execution_mode"] = "slurm"
    if remote_request.get("stage_only"):
        params.setdefault("remote_action", "stage_only")
        params.setdefault("gate_action", "remote_stage")
    else:
        if (params.get("remote_action") or "").strip().lower() == "stage_only":
            params.pop("remote_action", None)
        params.setdefault("gate_action", "job")
    if selected_defaults.get("ssh_profile_id") and not params.get("ssh_profile_id"):
        params["ssh_profile_id"] = selected_defaults.get("ssh_profile_id")
    if selected_defaults.get("nickname") and not params.get("ssh_profile_nickname"):
        params["ssh_profile_nickname"] = selected_defaults.get("nickname")
    if selected_defaults.get("remote_base_path") and not params.get("remote_base_path"):
        params["remote_base_path"] = selected_defaults.get("remote_base_path")

    prepared = await _prepare_remote_execution_params(session, project_id, owner_id, params)
    prepared["execution_mode"] = "slurm"
    if not prepared.get("slurm_account"):
        prepared["slurm_account"] = (
            defaults_data.get("account")
            or selected_defaults.get("default_slurm_account")
        )
    if not prepared.get("slurm_partition"):
        prepared["slurm_partition"] = (
            defaults_data.get("partition")
            or selected_defaults.get("default_slurm_partition")
        )
    if remote_request.get("stage_only"):
        prepared["remote_action"] = "stage_only"
        prepared["gate_action"] = "remote_stage"
    else:
        prepared["gate_action"] = "job"
    prepared = await _build_slurm_cache_preflight(session, project_id, owner_id, prepared)

    sample_name = prepared.get("sample_name") or "sample"
    mode = prepared.get("mode") or "DNA"
    input_directory = prepared.get("input_directory") or ""
    remote_input_path = prepared.get("remote_input_path") or ""
    reference_genome = prepared.get("reference_genome") or ["mm39"]
    if isinstance(reference_genome, str):
        reference_genome = [reference_genome]
    reference_text = ", ".join(reference_genome)
    profile_name = prepared.get("ssh_profile_nickname") or selected_defaults.get("nickname") or remote_request.get("ssh_profile_nickname") or "remote profile"
    account = prepared.get("slurm_account") or defaults_data.get("account") or selected_defaults.get("default_slurm_account") or "(unset)"
    partition = prepared.get("slurm_partition") or defaults_data.get("partition") or selected_defaults.get("default_slurm_partition") or "(unset)"
    remote_base_path = prepared.get("remote_base_path") or selected_defaults.get("remote_base_path") or "(unset)"
    result_destination = prepared.get("result_destination") or ("both" if remote_input_path else "local")
    cpus = int(prepared.get("slurm_cpus") or 4)
    memory_gb = int(prepared.get("slurm_memory_gb") or 16)
    walltime = prepared.get("slurm_walltime") or "04:00:00"
    gpus = int(prepared.get("slurm_gpus") or 1)
    data_path_line = (
        f"📂 **Remote Input Path:** {remote_input_path}\n"
        if remote_input_path
        else f"📁 **Data Path:** {input_directory}\n"
    )
    has_saved_defaults = bool(defaults_data.get("found"))

    if remote_request.get("stage_only"):
        intro = (
            "I found the saved remote defaults needed for staging."
            if has_saved_defaults
            else "I found the SSH profile, but no saved SLURM defaults. Review the staging parameters before approving."
        )
        summary = (
            f"{intro}\n\n"
            f"📋 **Sample Name:** {sample_name}\n"
            f"{data_path_line}"
            f"🧬 **Data Type:** {mode}\n"
            f"🔬 **Reference Genome:** {reference_text}\n"
            f"🖥️ **Execution Mode:** SLURM staging only\n"
            f"🔐 **SSH Profile:** {profile_name}\n"
            f"🧾 **Account:** {account}\n"
            f"🗂️ **Partition:** {partition}\n"
            f"📍 **Remote Base Path:** {remote_base_path}\n\n"
            f"I will stage the sample and reference assets on `{profile_name}`. This will not launch Dogme or Nextflow.\n\n"
            "[[APPROVAL_NEEDED]]"
        )
    else:
        intro = (
            "I found the saved remote defaults needed to submit this run."
            if has_saved_defaults
            else "I found the SSH profile, but no saved SLURM defaults. Review the remote submission parameters before approving."
        )
        summary = (
            f"{intro}\n\n"
            f"📋 **Sample Name:** {sample_name}\n"
            f"{data_path_line}"
            f"🧬 **Data Type:** {mode}\n"
            f"🔬 **Reference Genome:** {reference_text}\n"
            f"🖥️ **Execution Mode:** SLURM\n"
            f"🔐 **SSH Profile:** {profile_name}\n"
            f"🧾 **Account:** {account}\n"
            f"🗂️ **Partition:** {partition}\n"
            f"🧠 **CPUs:** {cpus}\n"
            f"💾 **Memory (GB):** {memory_gb}\n"
            f"⏱️ **Walltime:** {walltime}\n"
            f"🎮 **GPUs:** {gpus}\n"
            f"📦 **Result Destination:** {result_destination}\n"
            f"📍 **Remote Base Path:** {remote_base_path}\n\n"
            f"I am ready to submit Dogme on `{profile_name}` once you approve.\n\n"
            "[[APPROVAL_NEEDED]]"
        )

    return {
        "summary": summary,
        "params": prepared,
    }


def _normalize_reference_id(reference_id: str | None) -> str:
    return (reference_id or "default").strip().lower()


def _staged_sample_payload(entry) -> dict:
    return {
        "id": entry.id,
        "sample_name": entry.sample_name,
        "sample_slug": entry.sample_slug,
        "mode": entry.mode,
        "source_path": entry.source_path,
        "remote_base_path": entry.remote_base_path,
        "remote_data_path": entry.remote_data_path,
        "remote_reference_paths": entry.remote_reference_paths_json or {},
        "reference_genome": entry.reference_genome_json or [],
        "status": entry.status,
        "last_staged_at": entry.last_staged_at.isoformat() if entry.last_staged_at else None,
        "last_used_at": entry.last_used_at.isoformat() if entry.last_used_at else None,
    }


def _find_remote_staged_sample(
    session,
    *,
    owner_id: str,
    ssh_profile_id: str | None,
    sample_name: str | None,
    mode: str | None,
    input_directory: str | None,
    input_directory_explicit: bool,
):
    if not ssh_profile_id or not sample_name:
        return None

    from launchpad.models import RemoteStagedSample

    query = select(RemoteStagedSample).where(
        RemoteStagedSample.user_id == owner_id,
        RemoteStagedSample.ssh_profile_id == ssh_profile_id,
        RemoteStagedSample.sample_name == sample_name,
    )
    if mode:
        query = query.where(RemoteStagedSample.mode == mode)

    try:
        entry = session.execute(query.order_by(RemoteStagedSample.updated_at.desc())).scalar_one_or_none()
    except OperationalError:
        logger.info(
            "Remote staged sample lookup unavailable; continuing without staged reuse metadata",
            owner_id=owner_id,
            ssh_profile_id=ssh_profile_id,
        )
        return None
    if entry is None:
        return None

    entry_fingerprint = str(entry.input_fingerprint or "")
    if not entry_fingerprint or entry_fingerprint == _EMPTY_SHA256 or entry_fingerprint.startswith("e3b0c442"):
        entry.status = "INVALID_EMPTY_FINGERPRINT"
        entry.updated_at = datetime.datetime.utcnow()
        session.commit()
        return None

    if input_directory and input_directory_explicit:
        source_path = str(entry.source_path or "")
        if source_path and source_path != str(input_directory):
            entry.status = "INVALID_SOURCE_MISMATCH"
            entry.updated_at = datetime.datetime.utcnow()
            session.commit()
            return None

    return entry


def _compute_local_input_fingerprint(local_path: str) -> str:
    path = Path(local_path)
    hasher = hashlib.sha256()

    if not path.exists():
        hasher.update(f"missing:{local_path}".encode("utf-8"))
        return hasher.hexdigest()

    if path.is_file():
        stat = path.stat()
        hasher.update(str(path.name).encode("utf-8"))
        hasher.update(str(stat.st_size).encode("utf-8"))
        hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
        return hasher.hexdigest()

    for root, _, files in os.walk(path):
        root_path = Path(root)
        for filename in sorted(files):
            file_path = root_path / filename
            try:
                stat = file_path.stat()
            except OSError:
                continue
            rel_path = str(file_path.relative_to(path))
            hasher.update(rel_path.encode("utf-8"))
            hasher.update(str(stat.st_size).encode("utf-8"))
            hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
    return hasher.hexdigest()


def _compute_reference_source_signature(reference_id: str) -> str | None:
    from launchpad.config import REFERENCE_GENOMES

    ref_cfg = REFERENCE_GENOMES.get(reference_id)
    if ref_cfg is None:
        lower_map = {k.lower(): k for k in REFERENCE_GENOMES.keys()}
        mapped = lower_map.get(reference_id.lower())
        ref_cfg = REFERENCE_GENOMES.get(mapped) if mapped else None
    if not isinstance(ref_cfg, dict):
        return None

    fasta_path = ref_cfg.get("fasta")
    if not fasta_path:
        return None
    source_dir = Path(fasta_path).parent
    if not source_dir.exists() or not source_dir.is_dir():
        return None

    hasher = hashlib.sha256()
    for root, _, files in os.walk(source_dir):
        root_path = Path(root)
        for filename in sorted(files):
            file_path = root_path / filename
            try:
                stat = file_path.stat()
            except OSError:
                continue
            rel_path = str(file_path.relative_to(source_dir))
            hasher.update(rel_path.encode("utf-8"))
            hasher.update(str(stat.st_size).encode("utf-8"))
            hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
    return hasher.hexdigest()


def _has_remote_stage_intent(params: dict | None, gate_payload: dict | None = None) -> bool:
    normalized = dict(params or {})
    gate_payload = gate_payload or {}
    gate_action = gate_payload.get("gate_action") or normalized.get("gate_action") or ""
    remote_action = (normalized.get("remote_action") or "").strip().lower()
    if gate_action == "remote_stage" or remote_action == "stage_only":
        return True
    return bool(normalized.get("ssh_profile_id") or normalized.get("ssh_profile_nickname"))


async def _build_slurm_cache_preflight(
    session,
    project_id: str,
    owner_id: str,
    params: dict,
) -> dict:
    """Build planner/approval cache preflight metadata for SLURM staging reuse."""
    normalized = dict(params or {})
    if _has_remote_stage_intent(normalized):
        normalized["execution_mode"] = "slurm"
    if (normalized.get("execution_mode") or "local") != "slurm":
        return normalized

    ssh_profile_id = normalized.get("ssh_profile_id")
    ssh_profile_nickname = normalized.get("ssh_profile_nickname")
    ssh_username = "agoutic"
    profile_defaults: dict = {}

    if ssh_profile_id or ssh_profile_nickname:
        try:
            ssh_profile_id, ssh_profile_nickname = await _resolve_ssh_profile_reference(
                owner_id,
                ssh_profile_id,
                ssh_profile_nickname,
            )
            normalized["ssh_profile_id"] = ssh_profile_id
            normalized["ssh_profile_nickname"] = ssh_profile_nickname
            profiles = await _list_user_ssh_profiles(owner_id)
            profile = next((item for item in profiles if item.get("id") == ssh_profile_id), None)
            if profile:
                ssh_username = profile.get("ssh_username") or ssh_username
                profile_defaults = profile
        except Exception:
            logger.info("SLURM cache preflight: profile enrichment unavailable", project_id=project_id)

    normalized["remote_base_path"] = normalized.get("remote_base_path") or profile_defaults.get("remote_base_path")
    if normalized.get("remote_base_path"):
        remote_base_path = str(normalized["remote_base_path"]).rstrip("/")
        normalized["remote_reference_cache_root"] = f"{remote_base_path}/ref"
        normalized["remote_data_cache_root"] = f"{remote_base_path}/data"
    else:
        normalized["cache_preflight"] = {
            "scope": "per_user_cross_project",
            "status": "needs_remote_base_path",
            "project_id": project_id,
            "user_id": owner_id,
            "ssh_profile_id": ssh_profile_id,
            "reference_actions": [],
            "data_action": None,
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        return normalized

    ref_ids_raw = normalized.get("reference_genome") or ["mm39"]
    if isinstance(ref_ids_raw, str):
        ref_ids_raw = [ref_ids_raw]
    ref_ids = [_normalize_reference_id(ref_id) for ref_id in ref_ids_raw]
    primary_ref = ref_ids[0]

    input_directory = str(normalized.get("input_directory") or "")
    remote_input_path = str(normalized.get("remote_input_path") or "").strip()
    input_fingerprint = (
        _remote_path_fingerprint(remote_input_path)
        if remote_input_path
        else _compute_local_input_fingerprint(input_directory)
    )
    data_key = input_fingerprint[:16]

    preflight = {
        "scope": "per_user_cross_project",
        "status": "ready",
        "project_id": project_id,
        "user_id": owner_id,
        "ssh_profile_id": ssh_profile_id,
        "cache_roots": {
            "reference_root": normalized["remote_reference_cache_root"],
            "data_root": normalized["remote_data_cache_root"],
        },
        "reference_actions": [],
        "data_action": {
            "reference_id": primary_ref,
            "input_fingerprint": input_fingerprint,
            "cache_path": remote_input_path or f"{normalized['remote_data_cache_root']}/{data_key}",
            "action": "use_remote_path" if remote_input_path else "stage",
            "reason": "user_specified_remote_path" if remote_input_path else "cache_miss",
        },
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
    }

    try:
        from launchpad.models import RemoteReferenceCache, RemoteInputCache

        for ref_id in ref_ids:
            expected_path = f"{normalized['remote_reference_cache_root']}/{ref_id}"
            source_signature = _compute_reference_source_signature(ref_id)
            if source_signature is None:
                preflight["reference_actions"].append(
                    {
                        "reference_id": ref_id,
                        "cache_path": expected_path,
                        "action": "fallback",
                        "reason": "source_signature_unavailable",
                    }
                )
                continue

            ref_entry = session.execute(
                select(RemoteReferenceCache).where(
                    RemoteReferenceCache.user_id == owner_id,
                    RemoteReferenceCache.ssh_profile_id == (ssh_profile_id or ""),
                    RemoteReferenceCache.reference_id == ref_id,
                )
            ).scalar_one_or_none()

            if ref_entry is None:
                action = "stage"
                reason = "cache_miss"
            elif ref_entry.source_signature != source_signature:
                action = "refresh"
                reason = "source_changed"
            else:
                action = "reuse"
                reason = "cache_hit_validate_remote"

            preflight["reference_actions"].append(
                {
                    "reference_id": ref_id,
                    "cache_path": ref_entry.remote_path if ref_entry else expected_path,
                    "action": action,
                    "reason": reason,
                    "last_validated_at": ref_entry.last_validated_at.isoformat() if ref_entry and ref_entry.last_validated_at else None,
                }
            )

        if remote_input_path:
            pass
        elif input_fingerprint == _EMPTY_SHA256 or input_fingerprint.startswith("e3b0c442"):
            preflight["data_action"].update(
                {
                    "action": "stage",
                    "reason": "invalid_empty_input_fingerprint",
                }
            )
        else:
            data_entry = session.execute(
                select(RemoteInputCache).where(
                    RemoteInputCache.user_id == owner_id,
                    RemoteInputCache.ssh_profile_id == (ssh_profile_id or ""),
                    RemoteInputCache.reference_id == primary_ref,
                    RemoteInputCache.input_fingerprint == input_fingerprint,
                )
            ).scalar_one_or_none()
            if data_entry is not None:
                preflight["data_action"].update(
                    {
                        "action": "reuse",
                        "reason": "cache_hit_validate_remote",
                        "cache_path": data_entry.remote_path,
                        "last_used_at": data_entry.last_used_at.isoformat() if data_entry.last_used_at else None,
                    }
                )

    except Exception as cache_err:
        preflight["status"] = "degraded"
        preflight["degraded_reason"] = f"cache_metadata_unavailable: {cache_err}"
        preflight["fallback_action"] = "stage_inputs_without_cache_metadata"
        preflight["reference_actions"] = [
            {
                "reference_id": ref_id,
                "cache_path": f"{normalized['remote_reference_cache_root']}/{ref_id}",
                "action": "fallback",
                "reason": "metadata_unavailable",
            }
            for ref_id in ref_ids
        ]

    normalized["cache_preflight"] = preflight
    return normalized


async def _prepare_remote_execution_params(
    session,
    project_id: str,
    owner_id: str,
    params: dict,
) -> dict:
    def _render_profile_default(template: str | None, context: dict[str, str]) -> str | None:
        if not template:
            return None
        rendered = template
        for key, value in context.items():
            rendered = rendered.replace(f"{{{key}}}", value)
            rendered = rendered.replace(f"<{key}>", value)
        return rendered

    normalized = dict(params or {})
    if _has_remote_stage_intent(normalized):
        normalized["execution_mode"] = "slurm"
    if (normalized.get("execution_mode") or "local") != "slurm":
        return normalized

    if not normalized.get("remote_input_path"):
        recovered_remote_input = _extract_remote_input_from_input_directory(
            normalized.get("input_directory")
        )
        if recovered_remote_input:
            normalized["remote_input_path"] = recovered_remote_input

    normalized["slurm_gpus"] = max(int(normalized.get("slurm_gpus") or 0), 1)
    remote_input_path = str(normalized.get("remote_input_path") or "").strip()
    normalized["result_destination"] = normalized.get("result_destination") or ("both" if remote_input_path else "local")

    owner_user = session.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
    project_obj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()

    project_slug = _slugify(project_obj.slug if project_obj and project_obj.slug else project_id)
    workflow_slug = _slugify(normalized.get("sample_name") or "workflow")

    if owner_user:
        local_workflow_dir = _resolve_project_dir(session, owner_user, project_id) / "workflow" / workflow_slug
    else:
        local_workflow_dir = Path(AGOUTIC_DATA) / "users" / owner_id / project_id / "workflow" / workflow_slug

    normalized["local_workflow_directory"] = str(local_workflow_dir)

    ssh_profile_id = normalized.get("ssh_profile_id")
    ssh_profile_nickname = normalized.get("ssh_profile_nickname")
    ssh_username = None
    profile_defaults: dict = {}

    if ssh_profile_id or ssh_profile_nickname:
        try:
            ssh_profile_id, ssh_profile_nickname = await _resolve_ssh_profile_reference(
                owner_id,
                ssh_profile_id,
                ssh_profile_nickname,
            )
            normalized["ssh_profile_id"] = ssh_profile_id
            normalized["ssh_profile_nickname"] = ssh_profile_nickname

            if ssh_profile_id:
                try:
                    profiles = await _list_user_ssh_profiles(owner_id)
                    profile = next((item for item in profiles if item.get("id") == ssh_profile_id), None)
                    if profile:
                        ssh_username = profile.get("ssh_username")
                        profile_defaults = profile
                except (httpx.HTTPError, OSError):
                    logger.info(
                        "Skipping SSH profile enrichment while preparing remote defaults",
                        owner_id=owner_id,
                        ssh_profile_id=ssh_profile_id,
                    )
        except (ValueError, httpx.HTTPError, OSError):
            pass

    normalized["slurm_account"] = normalized.get("slurm_account") or profile_defaults.get("default_slurm_account")
    normalized["slurm_partition"] = normalized.get("slurm_partition") or profile_defaults.get("default_slurm_partition")
    normalized["slurm_gpu_account"] = normalized.get("slurm_gpu_account") or profile_defaults.get("default_slurm_gpu_account")
    normalized["slurm_gpu_partition"] = normalized.get("slurm_gpu_partition") or profile_defaults.get("default_slurm_gpu_partition")

    template_context = {
        "user_id": owner_id,
        "project_id": project_id,
        "project_slug": project_slug,
        "sample_name": normalized.get("sample_name") or "workflow",
        "workflow_slug": workflow_slug,
        "ssh_username": ssh_username or "agoutic",
        "local_workflow_directory": normalized["local_workflow_directory"],
    }
    remote_base_default = _render_profile_default(profile_defaults.get("remote_base_path"), template_context)
    normalized["remote_base_path"] = normalized.get("remote_base_path") or remote_base_default
    if normalized.get("remote_base_path"):
        remote_base_path = str(normalized["remote_base_path"]).rstrip("/")
        normalized["remote_reference_cache_root"] = f"{remote_base_path}/ref"
        normalized["remote_data_cache_root"] = f"{remote_base_path}/data"

    if remote_input_path:
        normalized["staged_remote_input_path"] = remote_input_path
        if not normalized.get("input_directory"):
            normalized["input_directory"] = f"remote:{remote_input_path}"

    normalized = await _build_slurm_cache_preflight(session, project_id, owner_id, normalized)

    if remote_input_path:
        preflight = normalized.get("cache_preflight") or {}
        if isinstance(preflight, dict):
            preflight["status"] = "ready"
            preflight["data_action"] = {
                "reference_id": _normalize_reference_id((normalized.get("reference_genome") or ["default"])[0]),
                "input_fingerprint": _remote_path_fingerprint(remote_input_path),
                "cache_path": remote_input_path,
                "action": "use_remote_path",
                "reason": "user_specified_remote_path",
            }
            normalized["cache_preflight"] = preflight
        return normalized

    if normalized.get("remote_action") != "stage_only":
        staged_entry = _find_remote_staged_sample(
            session,
            owner_id=owner_id,
            ssh_profile_id=normalized.get("ssh_profile_id"),
            sample_name=normalized.get("sample_name"),
            mode=normalized.get("mode"),
            input_directory=normalized.get("input_directory"),
            input_directory_explicit=bool(normalized.get("input_directory_explicit")),
        )
        if staged_entry is not None:
            staged_payload = _staged_sample_payload(staged_entry)
            normalized["staged_remote_input_path"] = staged_payload["remote_data_path"]
            normalized["remote_staged_sample"] = staged_payload
            normalized["remote_base_path"] = normalized.get("remote_base_path") or staged_payload["remote_base_path"]
            preflight = normalized.get("cache_preflight") or {}
            if isinstance(preflight, dict):
                preflight["status"] = "ready"
                preflight["staged_sample"] = staged_payload
                preflight["data_action"] = {
                    "reference_id": _normalize_reference_id((normalized.get("reference_genome") or ["default"])[0]),
                    "input_fingerprint": staged_entry.input_fingerprint,
                    "cache_path": staged_payload["remote_data_path"],
                    "action": "reuse",
                    "reason": "staged_sample_match",
                }
                ref_actions = []
                for ref_id, ref_path in (staged_payload["remote_reference_paths"] or {}).items():
                    ref_actions.append(
                        {
                            "reference_id": ref_id,
                            "cache_path": ref_path,
                            "action": "reuse",
                            "reason": "staged_sample_match",
                        }
                    )
                if ref_actions:
                    preflight["reference_actions"] = ref_actions
                normalized["cache_preflight"] = preflight
    return normalized


def _hydrate_request_placeholders(value, *, user_id: str, project_id: str):
    if isinstance(value, str):
        substitutions = {
            "<user_id>": user_id,
            "{user_id}": user_id,
            "<project_id>": project_id,
            "{project_id}": project_id,
        }
        hydrated = value
        for placeholder, replacement in substitutions.items():
            hydrated = hydrated.replace(placeholder, replacement)
        return hydrated
    if isinstance(value, list):
        return [
            _hydrate_request_placeholders(item, user_id=user_id, project_id=project_id)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _hydrate_request_placeholders(item, user_id=user_id, project_id=project_id)
            for key, item in value.items()
        }
    return value


def _inject_launchpad_context_params(
    tool_name: str,
    params: dict,
    *,
    user_id: str,
    project_id: str,
) -> dict:
    """Best-effort context hydration for Launchpad tools that require identity scope."""
    hydrated = dict(params or {})

    # Remote profile/default/listing tools require user scope in Launchpad MCP.
    if tool_name in {
        "list_ssh_profiles",
        "get_slurm_defaults",
        "list_remote_files",
        "test_ssh_connection",
        "submit_dogme_job",
    }:
        if not hydrated.get("user_id"):
            hydrated["user_id"] = user_id

    # Defaults lookups are usually project-scoped in chat flows.
    if tool_name in {"get_slurm_defaults", "submit_dogme_job"} and project_id and not hydrated.get("project_id"):
        hydrated["project_id"] = project_id

    return hydrated


def _workflow_step_index(payload: dict, step_id: str) -> int | None:
    for idx, step in enumerate(payload.get("steps", [])):
        if step.get("id") == step_id:
            return idx
    return None


def _workflow_next_step(payload: dict) -> str | None:
    for step in payload.get("steps", []):
        if step.get("status") not in {"COMPLETED", "CANCELLED"}:
            return step.get("id")
    return None


def _workflow_status(payload: dict) -> str:
    steps = payload.get("steps", [])
    if any(step.get("status") == "FAILED" for step in steps):
        return "FAILED"
    if any(step.get("status") == "FOLLOW_UP" for step in steps):
        return "FOLLOW_UP"
    if steps and all(step.get("status") in {"COMPLETED", "CANCELLED"} for step in steps if step.get("id")):
        if any(step.get("status") == "CANCELLED" for step in steps):
            return "CANCELLED"
        return "COMPLETED"
    if all(step.get("status") == "COMPLETED" for step in steps if step.get("id")) and steps:
        return "COMPLETED"
    if any(step.get("status") == "RUNNING" for step in steps):
        return "RUNNING"
    return "PENDING"


def _persist_workflow_plan(session, workflow_block: ProjectBlock, payload: dict, *, status: str | None = None) -> None:
    payload["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    payload["next_step"] = _workflow_next_step(payload)
    payload["status"] = _workflow_status(payload)
    workflow_block.payload_json = json.dumps(payload)
    workflow_block.status = status or payload["status"]
    session.commit()
    session.refresh(workflow_block)
    sync_project_tasks(session, workflow_block.project_id)


def _set_workflow_step_status(
    session,
    workflow_block: ProjectBlock,
    step_id: str,
    status: str,
    *,
    extra: dict | None = None,
) -> dict:
    payload = get_block_payload(workflow_block)
    idx = _workflow_step_index(payload, step_id)
    if idx is None:
        return payload
    step = dict(payload["steps"][idx])
    step["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    step["status"] = status
    if extra:
        step.update(extra)
    if status == "COMPLETED":
        step.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
    payload["steps"][idx] = step
    _persist_workflow_plan(session, workflow_block, payload)
    return payload


def _resolve_workflow_step_id(payload: dict, *identifiers: str, kinds: tuple[str, ...] = ()) -> str | None:
    steps = payload.get("steps", [])
    for identifier in identifiers:
        if not identifier:
            continue
        for step in steps:
            if step.get("id") == identifier:
                return identifier
    if kinds:
        for step in steps:
            if step.get("kind") in kinds:
                return step.get("id")
    return None


def _update_project_block_payload(session, block_id: str, updates: dict, *, status: str | None = None) -> ProjectBlock | None:
    block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
    if not block:
        return None
    payload = get_block_payload(block)
    payload.update(updates)
    payload["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    block.payload_json = json.dumps(payload)
    if status is not None:
        block.status = status
    session.commit()
    session.refresh(block)
    sync_project_tasks(session, block.project_id)
    return block


def _apply_slurm_cache_preflight_to_workflow(
    session,
    workflow_block: ProjectBlock | None,
    cache_preflight: dict | None,
) -> None:
    if workflow_block is None or not cache_preflight:
        return

    payload = get_block_payload(workflow_block)
    steps = payload.get("steps", [])
    if not steps:
        return

    reference_actions = cache_preflight.get("reference_actions") or []
    data_action = cache_preflight.get("data_action") or {}
    preflight_status = cache_preflight.get("status") or "ready"

    for idx, step in enumerate(steps):
        kind = step.get("kind")
        if kind == "FIND_REFERENCE_CACHE":
            updated = dict(step)
            updated["status"] = "COMPLETED"
            updated["cache_preflight_status"] = preflight_status
            updated["reference_actions"] = reference_actions
            updated["cache_root"] = (cache_preflight.get("cache_roots") or {}).get("reference_root")
            updated.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
            steps[idx] = updated
        elif kind == "FIND_DATA_CACHE":
            updated = dict(step)
            updated["status"] = "COMPLETED"
            updated["cache_preflight_status"] = preflight_status
            updated["data_action"] = data_action
            updated["cache_root"] = (cache_preflight.get("cache_roots") or {}).get("data_root")
            updated.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
            steps[idx] = updated
        elif kind in {"check_remote_stage", "CHECK_REMOTE_STAGE"}:
            updated = dict(step)
            updated["status"] = "COMPLETED"
            updated["cache_preflight_status"] = preflight_status
            updated["reference_actions"] = reference_actions
            updated["data_action"] = data_action
            updated["cache_root"] = (cache_preflight.get("cache_roots") or {}).get("data_root")
            updated.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
            steps[idx] = updated

    payload["steps"] = steps
    _persist_workflow_plan(session, workflow_block, payload)


def _find_workflow_plan(session, project_id: str, workflow_block_id: str | None = None, run_uuid: str | None = None) -> ProjectBlock | None:
    query = select(ProjectBlock).where(
        ProjectBlock.project_id == project_id,
        ProjectBlock.type == _WORKFLOW_PLAN_TYPE,
    ).order_by(ProjectBlock.seq.desc())
    for block in session.execute(query).scalars().all():
        if workflow_block_id and block.id == workflow_block_id:
            return block
        payload = get_block_payload(block)
        if run_uuid and payload.get("run_uuid") == run_uuid:
            return block
        if not workflow_block_id and not run_uuid and payload.get("workflow_type") == _LOCAL_SAMPLE_WORKFLOW:
            return block
    return None


def _local_sample_dest_dir(*, username: str | None, owner_id: str, sample_name: str) -> Path:
    user_key = username or owner_id
    sample_slug = _slugify(sample_name or "sample")
    return Path(AGOUTIC_DATA) / "users" / user_key / "data" / sample_slug


def _should_stage_local_sample(gate_payload: dict, job_params: dict) -> bool:
    skill = gate_payload.get("skill")
    if skill != "analyze_local_sample":
        return False
    if _has_remote_stage_intent(job_params, gate_payload):
        return False
    if (job_params.get("execution_mode") or "local").strip().lower() == "slurm":
        return False
    if job_params.get("input_type") == "bam":
        return False
    input_dir = str(job_params.get("input_directory") or "").strip()
    if job_params.get("gate_action") == "local_sample_existing":
        return True
    return input_dir.startswith("/") and Path(input_dir).exists()


def _build_local_sample_workflow_payload(job_data: dict, *, gate_block_id: str) -> dict:
    sample_name = job_data["sample_name"]
    source_path = str(job_data["input_directory"])
    staged_path = str(job_data["staged_input_directory"])
    return {
        "workflow_type": _LOCAL_SAMPLE_WORKFLOW,
        "title": f"Process local sample {sample_name}",
        "sample_name": sample_name,
        "mode": job_data.get("mode"),
        "source_path": source_path,
        "staged_input_directory": staged_path,
        "gate_block_id": gate_block_id,
        "run_uuid": None,
        "steps": [
            {
                "id": "stage_input",
                "kind": "copy_sample",
                "title": f"Stage {sample_name} into user data",
                "status": "PENDING",
                "source_path": source_path,
                "staged_input_directory": staged_path,
                "order_index": 0,
            },
            {
                "id": "run_dogme",
                "kind": "run",
                "title": f"Run Dogme for {sample_name}",
                "status": "PENDING",
                "order_index": 1,
            },
            {
                "id": "analyze_results",
                "kind": "analysis",
                "title": f"Analyze results for {sample_name}",
                "status": "PENDING",
                "order_index": 2,
            },
        ],
    }


def _build_remote_sample_workflow_payload(job_data: dict, *, gate_block_id: str, stage_only: bool) -> dict:
    sample_name = job_data["sample_name"]
    source_path = str(job_data.get("input_directory") or "")
    staged_path = str(job_data.get("staged_remote_input_path") or "")
    steps = [
        {
            "id": "check_remote_stage",
            "kind": "check_remote_stage",
            "title": f"Check remote staged data for {sample_name}",
            "status": "PENDING",
            "order_index": 0,
        },
        {
            "id": "stage_input",
            "kind": "remote_stage",
            "title": f"Stage or reuse remote input for {sample_name}",
            "status": "PENDING",
            "source_path": source_path,
            "staged_input_directory": staged_path,
            "order_index": 1,
        },
    ]
    if stage_only:
        steps.append(
            {
                "id": "complete_stage_only",
                "kind": "complete_stage_only",
                "title": f"Finish remote staging for {sample_name}",
                "status": "PENDING",
                "order_index": 2,
            }
        )
    else:
        steps.extend(
            [
                {
                    "id": "run_dogme",
                    "kind": "run",
                    "title": f"Run Dogme for {sample_name}",
                    "status": "PENDING",
                    "order_index": 2,
                },
                {
                    "id": "analyze_results",
                    "kind": "analysis",
                    "title": f"Analyze results for {sample_name}",
                    "status": "PENDING",
                    "order_index": 3,
                },
            ]
        )

    return {
        "workflow_type": _REMOTE_SAMPLE_WORKFLOW,
        "title": f"{'Stage' if stage_only else 'Run remote analysis for'} {sample_name}",
        "sample_name": sample_name,
        "mode": job_data.get("mode"),
        "source_path": source_path,
        "staged_input_directory": staged_path,
        "remote_base_path": job_data.get("remote_base_path"),
        "ssh_profile_id": job_data.get("ssh_profile_id"),
        "gate_block_id": gate_block_id,
        "run_uuid": None,
        "remote_action": "stage_only" if stage_only else "job",
        "steps": steps,
    }


def _ensure_remote_sample_workflow(
    session,
    project_id: str,
    owner_id: str,
    gate_block_id: str,
    job_data: dict,
    *,
    workflow_block_id: str | None = None,
    stage_only: bool,
) -> ProjectBlock:
    workflow_block = _find_workflow_plan(session, project_id, workflow_block_id=workflow_block_id)
    if workflow_block is None and not workflow_block_id:
        query = select(ProjectBlock).where(
            ProjectBlock.project_id == project_id,
            ProjectBlock.type == _WORKFLOW_PLAN_TYPE,
        ).order_by(ProjectBlock.seq.desc())
        for candidate in session.execute(query).scalars().all():
            payload = get_block_payload(candidate)
            if payload.get("workflow_type") != _REMOTE_SAMPLE_WORKFLOW:
                continue
            if payload.get("sample_name") != job_data.get("sample_name"):
                continue
            if payload.get("run_uuid"):
                continue
            if payload.get("status") in {"FAILED", "COMPLETED", "CANCELLED"}:
                continue
            workflow_block = candidate
            break

    if workflow_block is not None:
        payload = get_block_payload(workflow_block)
        payload["sample_name"] = job_data["sample_name"]
        payload["mode"] = job_data.get("mode")
        payload["source_path"] = str(job_data.get("input_directory") or "")
        payload["staged_input_directory"] = str(job_data.get("staged_remote_input_path") or "")
        payload["remote_base_path"] = job_data.get("remote_base_path")
        payload["ssh_profile_id"] = job_data.get("ssh_profile_id")
        payload["remote_action"] = "stage_only" if stage_only else "job"
        _persist_workflow_plan(session, workflow_block, payload)
        return workflow_block

    workflow_block = _create_block_internal(
        session,
        project_id,
        _WORKFLOW_PLAN_TYPE,
        _build_remote_sample_workflow_payload(job_data, gate_block_id=gate_block_id, stage_only=stage_only),
        status="PENDING",
        owner_id=owner_id,
    )
    sync_project_tasks(session, project_id)
    return workflow_block


def _ensure_local_sample_workflow(session, project_id: str, owner_id: str, gate_block_id: str, job_data: dict, workflow_block_id: str | None = None) -> ProjectBlock:
    workflow_block = _find_workflow_plan(session, project_id, workflow_block_id=workflow_block_id)
    if workflow_block is not None:
        payload = get_block_payload(workflow_block)
        payload["sample_name"] = job_data["sample_name"]
        payload["mode"] = job_data.get("mode")
        payload["source_path"] = str(job_data["input_directory"])
        payload["staged_input_directory"] = str(job_data["staged_input_directory"])
        _persist_workflow_plan(session, workflow_block, payload)
        return workflow_block

    workflow_block = _create_block_internal(
        session,
        project_id,
        _WORKFLOW_PLAN_TYPE,
        _build_local_sample_workflow_payload(job_data, gate_block_id=gate_block_id),
        status="PENDING",
        owner_id=owner_id,
    )
    sync_project_tasks(session, project_id)
    return workflow_block


async def _copy_local_sample_tree(source_dir: Path, staged_dir: Path, *, replace_existing: bool) -> None:
    def _copy():
        staged_dir.parent.mkdir(parents=True, exist_ok=True)
        if replace_existing and staged_dir.exists():
            shutil.rmtree(staged_dir)
        if not staged_dir.exists():
            shutil.copytree(source_dir, staged_dir)

    await run_in_threadpool(_copy)


def _create_existing_stage_gate(session, project_id: str, owner_id: str, gate_payload: dict, job_params: dict, workflow_block: ProjectBlock, staged_dir: Path) -> ProjectBlock:
    payload = {
        "label": (
            f"A staged sample folder already exists at `{staged_dir}`. "
            "Approve to reuse the existing staged copy, or choose replace to recopy from the source path."
        ),
        "extracted_params": {
            **job_params,
            "gate_action": "local_sample_existing",
            "workflow_block_id": workflow_block.id,
            "staged_input_directory": str(staged_dir),
        },
        "gate_action": "local_sample_existing",
        "attempt_number": 1,
        "rejection_history": [],
        "skill": gate_payload.get("skill"),
        "model": gate_payload.get("model", "default"),
    }
    gate_block = _create_block_internal(
        session,
        project_id,
        "APPROVAL_GATE",
        payload,
        status="PENDING",
        owner_id=owner_id,
    )
    gate_block.parent_id = workflow_block.id
    session.commit()
    session.refresh(gate_block)
    return gate_block


