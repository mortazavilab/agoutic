import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import select

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.config import AGOUTIC_DATA, get_service_url
from cortex.db import SessionLocal
from cortex.db_helpers import _create_block_internal
from cortex.llm_validators import get_block_payload
from cortex.models import Project, ProjectBlock, User
from cortex.remote_orchestration import (
    _apply_slurm_cache_preflight_to_workflow,
    _copy_local_sample_tree,
    _create_existing_stage_gate,
    _ensure_local_sample_workflow,
    _ensure_remote_sample_workflow,
    _failed_stage_parts,
    _final_stage_parts,
    _find_workflow_plan,
    _has_remote_stage_intent,
    _initial_stage_parts,
    _local_sample_dest_dir,
    _make_stage_part,
    _persist_workflow_plan,
    _prepare_remote_execution_params,
    _resolve_ssh_profile_reference,
    _resolve_workflow_step_id,
    _set_workflow_step_status,
    _should_stage_local_sample,
    _stage_part_progress,
    _update_project_block_payload,
)
import cortex.job_parameters as job_parameters
import cortex.job_polling as job_polling

REMOTE_STAGE_MCP_TIMEOUT = float(os.getenv("LAUNCHPAD_STAGE_TIMEOUT", "3600"))

logger = get_logger(__name__)


def _bounded_reconcile_threads(raw_value: int | None = None) -> int:
    """Return a safe thread count, clamped to env-configurable bounds."""
    default = int(os.environ.get("RECONCILE_BAMS_DEFAULT_THREADS", "4"))
    cap = int(os.environ.get("RECONCILE_BAMS_MAX_THREADS", "8"))
    value = raw_value if raw_value is not None else default
    return max(1, min(value, cap))


def _build_reconcile_script_args(job_params: dict) -> list[str]:
    bam_inputs = job_params.get("bam_inputs") or []
    bam_paths = [
        item.get("path")
        for item in bam_inputs
        if isinstance(item, dict) and item.get("path")
    ]
    if not bam_paths:
        raise ValueError("Reconcile approval is missing BAM inputs.")

    annotation_gtf = (job_params.get("annotation_gtf") or "").strip()
    if not annotation_gtf:
        raise ValueError("Reconcile approval is missing the annotation GTF path.")

    output_prefix = (job_params.get("output_prefix") or job_params.get("sample_name") or "reconciled").strip()
    output_dir = (job_params.get("output_directory") or job_params.get("input_directory") or ".").strip()
    script_args: list[str] = ["--json", "--output-prefix", output_prefix, "--output-dir", output_dir, "--annotation-gtf", annotation_gtf]
    for bam_path in bam_paths:
        script_args.extend(["--input-bam", bam_path])

    raw_threads = job_params.get("threads")
    clamped_threads = _bounded_reconcile_threads(int(raw_threads) if raw_threads not in (None, "") else None)
    script_args.extend(["--threads", str(clamped_threads)])

    scalar_flags = [
        ("gene_prefix", "--gene_prefix"),
        ("tx_prefix", "--tx_prefix"),
        ("id_tag", "--id_tag"),
        ("gene_tag", "--gene_tag"),
        ("exon_merge_distance", "--exon_merge_distance"),
        ("min_tpm", "--min_tpm"),
        ("min_samples", "--min_samples"),
    ]
    for field, flag in scalar_flags:
        value = job_params.get(field)
        if value is None or value == "":
            continue
        script_args.extend([flag, str(value)])

    if job_params.get("filter_known"):
        script_args.append("--filter_known")
    return script_args


def _remote_stage_data_action(job_data: dict) -> tuple[dict, str]:
    cache_preflight = job_data.get("cache_preflight")
    if not isinstance(cache_preflight, dict):
        return {}, ""
    data_action = cache_preflight.get("data_action")
    if not isinstance(data_action, dict):
        return {}, ""
    return data_action, str(data_action.get("action") or "").strip().lower()


def _should_background_remote_stage(
    *,
    run_type: str,
    execution_mode: str,
    remote_stage_only: bool,
    job_data: dict,
) -> bool:
    if run_type == "script" or execution_mode != "slurm":
        return False
    if job_data.get("staged_remote_input_path"):
        return False
    _data_action, data_action_name = _remote_stage_data_action(job_data)
    if remote_stage_only:
        return True
    if data_action_name:
        return data_action_name not in {"reuse", "use_remote_path"}
    return True


async def submit_job_after_approval(project_id: str, gate_block_id: str):
    """
    Background task to submit a job to Launchpad after approval.
    Uses edited_params if available, otherwise falls back to extracted_params.
    """
    session = SessionLocal()

    stage_task_block = None
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
            job_params = await job_parameters.extract_job_parameters_from_conversation(session, project_id)

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

        run_type = (job_params.get("run_type") or "dogme").strip().lower()
        if run_type not in {"dogme", "script"}:
            run_type = "dogme"

        gate_action = gate_payload.get("gate_action") or job_params.get("gate_action") or "job"
        if run_type == "script" and gate_action == "reconcile_bams":
            job_params = dict(job_params)
            job_params["script_id"] = "reconcile_bams/reconcile_bams"
            job_params["script_args"] = _build_reconcile_script_args(job_params)

        # Normalize remote execution parameters for Dogme jobs only.
        if run_type != "script":
            job_params = await _prepare_remote_execution_params(session, project_id, owner_id, job_params)
        else:
            job_params = dict(job_params)
            job_params["execution_mode"] = "local"
        remote_input_path = str(job_params.get("remote_input_path") or "").strip()
        if remote_input_path:
            job_params["staged_remote_input_path"] = remote_input_path
            job_params["result_destination"] = job_params.get("result_destination") or "both"
            if not job_params.get("input_directory"):
                job_params["input_directory"] = f"remote:{remote_input_path}"
        if gate_payload.get("edited_params"):
            gate_payload["edited_params"] = job_params
        else:
            gate_payload["extracted_params"] = job_params
        gate_payload["cache_preflight"] = job_params.get("cache_preflight")
        gate_block.payload_json = json.dumps(gate_payload)
        session.commit()

        ref_genome = job_params.get("reference_genome", ["mm39"])
        if isinstance(ref_genome, str):
            ref_genome = [ref_genome]

        # Look up username and project_slug for human-readable work directories
        owner_user = session.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
        project_obj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        _username = owner_user.username if owner_user else None
        _project_slug = project_obj.slug if project_obj else None
        remote_action = (job_params.get("remote_action") or "").strip().lower()
        remote_intent = run_type != "script" and _has_remote_stage_intent(job_params, gate_payload)
        execution_mode = (job_params.get("execution_mode") or "local").strip().lower()
        requested_execution_mode = (job_params.get("requested_execution_mode") or "").strip().lower()
        if remote_intent and execution_mode != "slurm":
            execution_mode = "slurm"
            job_params["execution_mode"] = "slurm"
        elif requested_execution_mode in {"local", "slurm"} and execution_mode != requested_execution_mode:
            raise ValueError(
                f"Requested execution_mode={requested_execution_mode} but extracted execution_mode={execution_mode}. "
                "Regenerate approval parameters."
            )
        if run_type == "script":
            execution_mode = "local"
            job_params["execution_mode"] = "local"
        if execution_mode not in ("local", "slurm"):
            logger.warning(
                "Invalid execution_mode from job_params, defaulting to local",
                provided_mode=execution_mode,
                project_id=project_id,
            )
            execution_mode = "local"
        ssh_profile_id = job_params.get("ssh_profile_id")
        ssh_profile_nickname = job_params.get("ssh_profile_nickname")

        if execution_mode == "slurm":
            if not ssh_profile_id and not ssh_profile_nickname:
                raise ValueError(
                    "SLURM execution requires an SSH profile. Select one in the approval gate or use a saved profile nickname such as 'hpc3'."
                )
            ssh_profile_id, ssh_profile_nickname = await _resolve_ssh_profile_reference(
                owner_id,
                ssh_profile_id,
                ssh_profile_nickname,
            )

        selected_slurm_account = (job_params.get("slurm_resources") or {}).get("account") or job_params.get("slurm_account")
        selected_slurm_partition = (job_params.get("slurm_resources") or {}).get("partition") or job_params.get("slurm_partition")
        if execution_mode == "slurm" and max(int(job_params.get("slurm_gpus") or 0), 0) > 0:
            selected_slurm_account = job_params.get("slurm_gpu_account") or selected_slurm_account
            selected_slurm_partition = job_params.get("slurm_gpu_partition") or selected_slurm_partition

        job_data = {
            "project_id": project_id,
            "user_id": owner_id,  # Pass owner for jailed file paths and job ownership
            "username": _username,  # Human-readable dir name (may be None for legacy users)
            "project_slug": _project_slug,  # Human-readable dir name (may be None for legacy projects)
            "sample_name": job_params.get("sample_name", f"sample_{project_id.split('_')[-1]}"),
            "mode": job_params.get("mode", "DNA"),
            "input_directory": job_params.get("input_directory", "/data/samples/test"),
            "run_type": run_type,
            "reference_genome": ref_genome,  # List - Nextflow parallelizes across genomes
            "modifications": job_params.get("modifications"),
            "input_type": job_params.get("input_type", "pod5"),
            "entry_point": job_params.get("entry_point"),
            # Advanced parameters - use Launchpad defaults if None
            "modkit_filter_threshold": job_params.get("modkit_filter_threshold") or 0.9,
            "min_cov": job_params.get("min_cov"),  # Let Launchpad handle None (mode-dependent default)
            "per_mod": job_params.get("per_mod") or 5,
            "accuracy": job_params.get("accuracy") or "sup",
            "max_gpu_tasks": job_params.get("max_gpu_tasks") if "max_gpu_tasks" in job_params else None,
            "custom_dogme_profile": job_params.get("custom_dogme_profile"),
            "custom_dogme_bind_paths": job_params.get("custom_dogme_bind_paths") or [],
            "execution_mode": execution_mode,
            "ssh_profile_id": ssh_profile_id,
            "slurm_account": selected_slurm_account,
            "slurm_partition": selected_slurm_partition,
            "slurm_cpus": (job_params.get("slurm_resources") or {}).get("cpus") or job_params.get("slurm_cpus"),
            "slurm_memory_gb": (job_params.get("slurm_resources") or {}).get("memory_gb") or job_params.get("slurm_memory_gb"),
            "slurm_walltime": (job_params.get("slurm_resources") or {}).get("walltime") or job_params.get("slurm_walltime"),
            "slurm_gpus": (job_params.get("slurm_resources") or {}).get("gpus") or job_params.get("slurm_gpus"),
            "slurm_gpu_type": (job_params.get("slurm_resources") or {}).get("gpu_type") or job_params.get("slurm_gpu_type"),
            "remote_base_path": (job_params.get("remote_paths") or {}).get("remote_base_path") or job_params.get("remote_base_path"),
            "remote_input_path": remote_input_path or None,
            "staged_remote_input_path": job_params.get("staged_remote_input_path"),
            "cache_preflight": job_params.get("cache_preflight"),
            "result_destination": job_params.get("result_destination") or ("both" if remote_input_path else None),
            "script_id": job_params.get("script_id"),
            "script_path": job_params.get("script_path"),
            "script_args": job_params.get("script_args") if isinstance(job_params.get("script_args"), list) else None,
            "script_working_directory": job_params.get("script_working_directory"),
        }

        workflow_block = None
        remote_stage_only = run_type != "script" and execution_mode == "slurm" and (
            gate_action == "remote_stage" or job_params.get("remote_action") == "stage_only"
        )
        workflow_block_id = job_params.get("workflow_block_id")

        if workflow_block_id:
            workflow_block = _find_workflow_plan(session, project_id, workflow_block_id=workflow_block_id)
        if workflow_block is None and job_params.get("run_uuid"):
            workflow_block = _find_workflow_plan(session, project_id, run_uuid=job_params.get("run_uuid"))

        if run_type != "script" and execution_mode == "slurm":
            workflow_block = _ensure_remote_sample_workflow(
                session,
                project_id,
                owner_id,
                gate_block_id,
                job_data,
                workflow_block_id=workflow_block_id,
                stage_only=remote_stage_only,
            )

        if workflow_block is not None:
            job_params["workflow_block_id"] = workflow_block.id
            if gate_payload.get("edited_params"):
                gate_payload["edited_params"] = job_params
            else:
                gate_payload["extracted_params"] = job_params
            gate_block.payload_json = json.dumps(gate_payload)
            session.commit()

        _apply_slurm_cache_preflight_to_workflow(session, workflow_block, job_data.get("cache_preflight"))

        if run_type != "script" and _should_stage_local_sample(gate_payload, job_params):
            staged_dir = _local_sample_dest_dir(
                username=_username,
                owner_id=owner_id,
                sample_name=job_data["sample_name"],
            )
            job_data["staged_input_directory"] = str(staged_dir)
            workflow_block = _ensure_local_sample_workflow(
                session,
                project_id,
                owner_id,
                gate_block_id,
                job_data,
                workflow_block_id=workflow_block_id,
            )

            source_dir = Path(job_data["input_directory"])
            source_real = source_dir.resolve(strict=False)
            staged_real = staged_dir.resolve(strict=False)
            if source_real == staged_real or staged_real in source_real.parents:
                job_data["input_directory"] = str(staged_dir)
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    "stage_input",
                    "COMPLETED",
                    extra={
                        "decision": "already_staged",
                        "staged_input_directory": str(staged_dir),
                    },
                )
            else:
                if not source_dir.exists():
                    raise FileNotFoundError(f"Input directory does not exist: {source_dir}")

                replace_existing = False
                if gate_action == "local_sample_existing":
                    replace_existing = gate_block.status == "REJECTED"
                elif staged_dir.exists():
                    _set_workflow_step_status(
                        session,
                        workflow_block,
                        "stage_input",
                        "FOLLOW_UP",
                        extra={
                            "decision_gate_id": None,
                            "staged_input_directory": str(staged_dir),
                            "source_path": str(source_dir),
                        },
                    )
                    conflict_gate = _create_existing_stage_gate(
                        session,
                        project_id,
                        owner_id,
                        gate_payload,
                        job_params,
                        workflow_block,
                        staged_dir,
                    )
                    _set_workflow_step_status(
                        session,
                        workflow_block,
                        "stage_input",
                        "FOLLOW_UP",
                        extra={
                            "decision_gate_id": conflict_gate.id,
                            "staged_input_directory": str(staged_dir),
                            "source_path": str(source_dir),
                        },
                    )
                    return

                _set_workflow_step_status(
                    session,
                    workflow_block,
                    "stage_input",
                    "RUNNING",
                    extra={
                        "staged_input_directory": str(staged_dir),
                        "source_path": str(source_dir),
                    },
                )
                await _copy_local_sample_tree(source_dir, staged_dir, replace_existing=replace_existing)
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    "stage_input",
                    "COMPLETED",
                    extra={
                        "decision": "replace" if replace_existing else "copy",
                        "staged_input_directory": str(staged_dir),
                    },
                )
                job_data["input_directory"] = str(staged_dir)

            _set_workflow_step_status(
                session,
                workflow_block,
                "run_dogme",
                "RUNNING",
                extra={"staged_input_directory": job_data["input_directory"]},
            )

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

        submission_payload = dict(job_data)
        submission_payload.pop("staged_input_directory", None)

        workflow_payload = get_block_payload(workflow_block) if workflow_block is not None else None
        check_remote_stage_step_id = None
        stage_input_step_id = None
        complete_stage_only_step_id = None
        if workflow_payload is not None:
            check_remote_stage_step_id = _resolve_workflow_step_id(
                workflow_payload,
                "check_remote_stage",
                kinds=("check_remote_stage", "CHECK_REMOTE_STAGE"),
            )
            stage_input_step_id = _resolve_workflow_step_id(
                workflow_payload,
                "stage_input",
                kinds=("remote_stage", "REMOTE_STAGE"),
            )
            complete_stage_only_step_id = _resolve_workflow_step_id(
                workflow_payload,
                "complete_stage_only",
                kinds=("complete_stage_only", "COMPLETE_STAGE_ONLY"),
            )

        data_action, data_action_name = _remote_stage_data_action(job_data)
        background_remote_stage = (
            workflow_block is not None
            and stage_input_step_id is not None
            and _should_background_remote_stage(
                run_type=run_type,
                execution_mode=execution_mode,
                remote_stage_only=remote_stage_only,
                job_data=job_data,
            )
        )

        if execution_mode == "slurm" and workflow_block is not None and check_remote_stage_step_id:
            _set_workflow_step_status(
                session,
                workflow_block,
                check_remote_stage_step_id,
                "COMPLETED",
                extra={"cache_preflight": job_data.get("cache_preflight")},
            )

        if execution_mode == "slurm" and workflow_block is not None and stage_input_step_id and not remote_stage_only:
            if job_data.get("staged_remote_input_path"):
                decision = "use_remote_path" if data_action_name == "use_remote_path" else (
                    "reuse" if data_action_name == "reuse" else "stage"
                )
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    stage_input_step_id,
                    "COMPLETED",
                    extra={
                        "decision": decision,
                        "staged_input_directory": job_data.get("staged_remote_input_path"),
                        "data_action": data_action,
                    },
                )
            else:
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    stage_input_step_id,
                    "RUNNING",
                    extra={
                        "decision": "stage",
                        "source_path": job_data.get("input_directory"),
                        "staged_input_directory": job_data.get("staged_remote_input_path"),
                        "data_action": data_action,
                    },
                )

        if background_remote_stage:
            stage_parts = _initial_stage_parts(job_data.get("cache_preflight"))
            stage_task_block = _create_block_internal(
                session,
                project_id,
                "STAGING_TASK",
                {
                    "sample_name": job_data["sample_name"],
                    "mode": job_data["mode"],
                    "input_directory": job_data.get("input_directory"),
                    "ssh_profile_id": ssh_profile_id,
                    "ssh_profile_nickname": ssh_profile_nickname,
                    "remote_base_path": job_data.get("remote_base_path"),
                    "gate_block_id": gate_block.id,
                    "workflow_plan_block_id": workflow_block.id if workflow_block is not None else None,
                    "staging_task_id": None,
                    "stage_input_step_id": stage_input_step_id,
                    "complete_stage_only_step_id": complete_stage_only_step_id,
                    "skill": gate_payload.get("skill", "remote_execution"),
                    "model": gate_payload.get("model", "default"),
                    "progress_percent": _stage_part_progress(stage_parts),
                    "message": "Preparing remote staging...",
                    "stage_parts": stage_parts,
                    "status": "RUNNING",
                },
                status="RUNNING",
                owner_id=owner_id,
            )
            if workflow_block is not None and stage_input_step_id:
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    stage_input_step_id,
                    "RUNNING",
                    extra={
                        "source_path": job_data.get("input_directory"),
                        "remote_base_path": job_data.get("remote_base_path"),
                        "block_id": stage_task_block.id if stage_task_block is not None else None,
                    },
                )

            if stage_task_block is not None:
                stage_parts = dict(stage_parts)
                if stage_parts.get("references", {}).get("status") == "COMPLETED" and stage_parts.get("data", {}).get("status") == "PENDING":
                    stage_parts["data"] = _make_stage_part(
                        "RUNNING",
                        35,
                        "Staging sample data on the remote profile...",
                    )
                _update_project_block_payload(
                    session,
                    stage_task_block.id,
                    {
                        "progress_percent": _stage_part_progress(stage_parts),
                        "message": "Uploading input files and reference assets to the remote profile...",
                        "stage_parts": stage_parts,
                    },
                    status="RUNNING",
                )

            try:
                launchpad_url = get_service_url("launchpad")
                client = MCPHttpClient(name="launchpad", base_url=launchpad_url, timeout=REMOTE_STAGE_MCP_TIMEOUT)
                await client.connect()
                try:
                    stage_response = await client.call_tool(
                        "stage_remote_sample",
                        project_id=project_id,
                        user_id=owner_id,
                        username=_username,
                        project_slug=_project_slug,
                        sample_name=job_data["sample_name"],
                        mode=job_data["mode"],
                        input_directory=job_data["input_directory"],
                        reference_genome=job_data["reference_genome"],
                        ssh_profile_id=ssh_profile_id,
                        remote_base_path=job_data.get("remote_base_path"),
                        remote_input_path=job_data.get("remote_input_path"),
                    )
                finally:
                    await client.disconnect()
            except Exception as e:
                _err = str(e).strip() or f"{type(e).__name__}: {e!r}"
                raise Exception(f"MCP call to Launchpad failed: {_err}")

            staging_task_id = stage_response.get("task_id") if isinstance(stage_response, dict) else None
            if not staging_task_id:
                raise RuntimeError(
                    f"Launchpad did not return a staging task_id: {stage_response!r}"
                )

            if stage_task_block is not None:
                _update_project_block_payload(
                    session,
                    stage_task_block.id,
                    {"staging_task_id": staging_task_id},
                    status="RUNNING",
                )

            # Spawn background poller — completion and block updates happen there
            asyncio.create_task(
                job_polling.poll_staging_status(
                    task_id=staging_task_id,
                    project_id=project_id,
                    block_id=stage_task_block.id if stage_task_block else None,
                    owner_id=owner_id,
                    job_data=job_data,
                    ssh_profile_id=ssh_profile_id,
                    ssh_profile_nickname=ssh_profile_nickname,
                    workflow_block_id=workflow_block.id if workflow_block is not None else None,
                    gate_block_id=gate_block.id,
                    stage_input_step_id=stage_input_step_id,
                    complete_stage_only_step_id=complete_stage_only_step_id,
                    gate_payload=gate_payload,
                    initial_stage_parts=stage_parts,
                )
            )
            logger.info(
                "Remote staging dispatched to background",
                project_id=project_id,
                sample_name=job_data["sample_name"],
                staging_task_id=staging_task_id,
                continue_submission=not remote_stage_only,
            )
            return

        if gate_action == "remote_stage" or remote_action == "stage_only":
            raise RuntimeError(
                "Refusing to submit a job for a remote stage-only request; the request must stop at remote staging."
            )

        # Submit job to Launchpad via MCP (single call - Nextflow handles multi-genome)
        try:
            launchpad_url = get_service_url("launchpad")
            client = MCPHttpClient(name="launchpad", base_url=launchpad_url, timeout=900.0)
            await client.connect()
            try:
                result = await client.call_tool("submit_dogme_job", **submission_payload)
            finally:
                await client.disconnect()
        except Exception as e:
            _err = str(e).strip() or f"{type(e).__name__}: {e!r}"
            raise Exception(f"MCP call to Launchpad failed: {_err}")

        run_uuid = result.get("run_uuid") if isinstance(result, dict) else None
        _work_directory = result.get("work_directory", "") if isinstance(result, dict) else ""

        # For script runs the work_directory from Launchpad is the script's
        # cwd (the skills/scripts folder), NOT the actual output directory.
        # Prefer the output_directory known from the job params.
        if run_type == "script":
            _script_output_dir = (
                job_data.get("output_directory")
                or job_params.get("output_directory")
                or ""
            ).strip()
            if _script_output_dir and os.path.isabs(_script_output_dir):
                _work_directory = _script_output_dir

        if run_uuid:
            if workflow_block is not None:
                payload = get_block_payload(workflow_block)
                payload["run_uuid"] = run_uuid
                _persist_workflow_plan(session, workflow_block, payload)

                if execution_mode == "slurm":
                    cache_actions = result.get("cache_actions") if isinstance(result, dict) else {}
                    decision = "reuse" if (cache_actions or {}).get("data_status") == "reused" else "stage"
                    _set_workflow_step_status(
                        session,
                        workflow_block,
                        "stage_input",
                        "COMPLETED",
                        extra={
                            "decision": decision,
                            "staged_input_directory": job_data.get("staged_remote_input_path") or (cache_actions or {}).get("data_cache_path"),
                            "reference_cache_status": (cache_actions or {}).get("reference_status"),
                            "data_cache_status": (cache_actions or {}).get("data_status"),
                        },
                    )

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
                    "run_type": run_type,
                    "workflow_plan_block_id": workflow_block.id if workflow_block is not None else None,
                    "model": _gate_model,
                    "status": "SUBMITTED",
                    "message": f"Job submitted: {run_uuid}",
                    "cache_preflight": job_data.get("cache_preflight"),
                    "cache_actions": result.get("cache_actions") if isinstance(result, dict) else None,
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

            if workflow_block is not None:
                run_step_id = _resolve_workflow_step_id(
                    get_block_payload(workflow_block),
                    "run_dogme",
                    kinds=("RUN_SCRIPT",),
                )
                if run_step_id:
                    _set_workflow_step_status(
                        session,
                        workflow_block,
                        run_step_id,
                        "RUNNING",
                        extra={"run_uuid": run_uuid, "block_id": job_block.id},
                    )

            logger.info("Job submitted", run_uuid=run_uuid, project_id=project_id)

            # Start polling job status in background
            asyncio.create_task(job_polling.poll_job_status(project_id, job_block.id, run_uuid))
        else:
            # MCP call succeeded but no run_uuid in response
            # Show the actual result for debugging
            error_detail = str(result) if result else "empty response"
            if workflow_block is not None:
                if execution_mode == "slurm":
                    _set_workflow_step_status(
                        session,
                        workflow_block,
                        "stage_input",
                        "FAILED",
                        extra={"error": f"Submission did not return run_uuid: {error_detail}"},
                    )
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    "run_dogme",
                    "FAILED",
                    extra={"error": f"No run_uuid in Launchpad response: {error_detail}"},
                )
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "sample_name": job_data.get("sample_name"),
                    "mode": job_data.get("mode"),
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
        if 'workflow_block' in locals() and workflow_block is not None:
            failing_step_id = "run_dogme"
            if locals().get("remote_stage_only"):
                _workflow_payload = get_block_payload(workflow_block)
                failing_step_id = _resolve_workflow_step_id(
                    _workflow_payload,
                    "stage_input",
                    kinds=("remote_stage", "REMOTE_STAGE"),
                ) or "stage_input"
            elif locals().get("execution_mode") == "slurm":
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    "stage_input",
                    "FAILED",
                    extra={"error": error_msg},
                )
            _set_workflow_step_status(
                session,
                workflow_block,
                failing_step_id,
                "FAILED",
                extra={"error": error_msg},
            )
        if locals().get("remote_stage_only"):
            if stage_task_block is not None:
                current_stage_parts = locals().get("stage_parts") or {}
                current_stage_parts = _failed_stage_parts(current_stage_parts, error_msg)
                _update_project_block_payload(
                    session,
                    stage_task_block.id,
                    {
                        "status": "FAILED",
                        "progress_percent": _stage_part_progress(current_stage_parts),
                        "error": error_msg,
                        "message": error_msg,
                        "stage_parts": current_stage_parts,
                        "traceback": error_trace,
                    },
                    status="FAILED",
                )
            else:
                current_stage_parts = _failed_stage_parts({}, error_msg)
                _create_block_internal(
                    session,
                    project_id,
                    "STAGING_TASK",
                    {
                        "sample_name": locals().get("job_data", {}).get("sample_name", ""),
                        "mode": locals().get("job_data", {}).get("mode", ""),
                        "status": "FAILED",
                        "progress_percent": _stage_part_progress(current_stage_parts),
                        "error": error_msg,
                        "message": error_msg,
                        "stage_parts": current_stage_parts,
                    },
                    status="FAILED",
                    owner_id=owner_id
                )
        else:
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "sample_name": locals().get("job_data", {}).get("sample_name", ""),
                    "mode": locals().get("job_data", {}).get("mode", ""),
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
