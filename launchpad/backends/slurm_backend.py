"""
SLURM execution backend — SSH + sbatch for remote HPC execution.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
import shlex
import uuid
from datetime import datetime, timezone

from common.logging_config import get_logger
from launchpad.backends.base import ExecutionBackend, SubmitParams, JobStatus, LogEntry
from launchpad.backends.ssh_manager import SSHConnectionManager, SSHProfileData
from launchpad.backends.sbatch_generator import generate_sbatch_script
from launchpad.backends.slurm_states import map_slurm_state, explain_pending_reason, explain_failure
from launchpad.backends.stage_machine import RunStage
from launchpad.backends.resource_validator import validate_resources
from launchpad.backends.path_validator import validate_remote_paths, check_all_paths_ok
from launchpad.backends.file_transfer import FileTransferManager
from launchpad.config import REFERENCE_GENOMES
from launchpad.nextflow_executor import NextflowConfig

logger = get_logger(__name__)


class SlurmBackend:
    """Remote execution via SSH + SLURM sbatch."""

    _result_sync_tasks: dict[str, asyncio.Task] = {}
    _transfer_progress: dict[str, dict] = {}
    _RSYNC_PARTIAL_DIR = ".rsync-partial"
    _RESULT_SYNC_DIRS = ("annot", "bams", "bedMethyl", "kallisto", "openChromatin", "stats")
    _RESULT_SYNC_FILE_PATTERNS = ("*.config", "*.html", "*.txt", "*.csv", "*.tsv")

    @staticmethod
    def _effective_work_directory(job) -> str | None:
        result_destination = (getattr(job, "result_destination", "") or "").strip().lower()
        local_work_dir = getattr(job, "nextflow_work_dir", None)
        remote_work_dir = getattr(job, "remote_work_dir", None)
        if result_destination in {"local", "both"} and local_work_dir:
            return local_work_dir
        return remote_work_dir or local_work_dir

    def __init__(self):
        self._ssh_manager = SSHConnectionManager()
        self._transfer_manager = FileTransferManager()

    @staticmethod
    def _parse_scheduler_queue_text(
        *,
        queue_output: str,
        parent_job_id: str,
        remote_work: str,
    ) -> dict[str, int]:
        scheduler_running_count = 0
        scheduler_pending_count = 0
        workflow_root = str(remote_work or "").rstrip("/")

        for line in (queue_output or "").splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|", 3)]
            if len(parts) < 4:
                continue
            job_id, raw_state, job_name, work_dir = parts
            if job_id == str(parent_job_id):
                continue

            normalized_job_name = str(job_name or "").strip()
            normalized_work_dir = str(work_dir or "").rstrip("/")
            if normalized_job_name and not normalized_job_name.startswith("nf-"):
                continue

            if workflow_root:
                in_workflow_tree = (
                    normalized_work_dir == workflow_root
                    or normalized_work_dir.startswith(f"{workflow_root}/")
                )
                if not in_workflow_tree:
                    continue

            state = raw_state.upper().split()[0] if raw_state else "UNKNOWN"
            if state in {"R", "RUNNING", "CG", "COMPLETING", "CF", "CONFIGURING", "STAGE_OUT"}:
                scheduler_running_count += 1
            elif state in {"PD", "PENDING"}:
                scheduler_pending_count += 1

        return {
            "scheduler_running_count": scheduler_running_count,
            "scheduler_pending_count": scheduler_pending_count,
        }

    async def _read_scheduler_job_counts(
        self,
        *,
        conn,
        remote_work: str,
        slurm_job_id: str,
        ssh_username: str | None,
    ) -> dict[str, int] | None:
        if not remote_work or not ssh_username:
            return None

        quoted_username = shlex.quote(str(ssh_username))
        quoted_work = shlex.quote(str(remote_work))
        result = await conn.run(
            "squeue "
            f"-u {quoted_username} "
            "--noheader "
            "--format='%i|%T|%j|%Z' "
            "2>/dev/null"
        )
        return self._parse_scheduler_queue_text(
            queue_output=result.stdout or "",
            parent_job_id=slurm_job_id,
            remote_work=remote_work,
        )

    @staticmethod
    def _build_pipeline_message(*, tasks: dict, scheduler_status: str) -> str | None:
        total = int(tasks.get("total", 0) or 0)
        completed_count = int(tasks.get("completed_count", 0) or 0)
        failed_count = int(tasks.get("failed_count", 0) or 0)
        remaining_count = int(tasks.get("remaining_count", 0) or 0)
        retried_count = int(tasks.get("retried_count", 0) or 0)
        observed_running_count = int(
            tasks.get("observed_running_count", len(tasks.get("running", []) or [])) or 0
        )
        scheduler_running_count = tasks.get("scheduler_running_count")
        scheduler_pending_count = tasks.get("scheduler_pending_count")

        parts: list[str] = []
        if total > 0:
            parts.append(f"{completed_count}/{total} completed")

        if remaining_count > 0:
            parts.append(f"{remaining_count} remaining")
        elif observed_running_count > 0 and scheduler_status == "RUNNING":
            parts.append(f"{observed_running_count} active")

        if scheduler_running_count is not None:
            scheduler_running_count = int(scheduler_running_count or 0)
            if scheduler_running_count > 0 or scheduler_pending_count:
                parts.append(f"{scheduler_running_count} SLURM running")

        if scheduler_pending_count is not None:
            scheduler_pending_count = int(scheduler_pending_count or 0)
            if scheduler_pending_count > 0:
                parts.append(f"{scheduler_pending_count} SLURM pending")

        if failed_count > 0:
            parts.append(f"{failed_count} failed")

        if retried_count > 0:
            parts.append(f"{retried_count} retries")

        return f"Pipeline: {', '.join(parts)}" if parts else None

    @staticmethod
    def _compute_remote_input_fingerprint(remote_path: str) -> str:
        return hashlib.sha256(f"remote:{str(remote_path or '').strip()}".encode("utf-8")).hexdigest()

    @staticmethod
    def _is_user_remote_input(params: SubmitParams) -> bool:
        explicit_remote_path = str(params.remote_input_path or "").strip()
        if explicit_remote_path:
            return True
        cache_preflight = params.cache_preflight if isinstance(params.cache_preflight, dict) else {}
        data_action = cache_preflight.get("data_action") if isinstance(cache_preflight, dict) else {}
        return str((data_action or {}).get("action") or "").strip().lower() == "use_remote_path"

    @staticmethod
    def _infer_input_type_from_names(names: list[str]) -> str | None:
        lowered = [str(name or "").strip().lower() for name in names]
        if any(name.endswith(".pod5") or name.endswith(".fast5") for name in lowered):
            return "pod5"
        if any(name.endswith(".bam") for name in lowered):
            return "bam"
        if any(name.endswith(".fastq") or name.endswith(".fq") for name in lowered):
            return "fastq"
        return None

    async def _detect_remote_input_type(self, conn, remote_input: str, *, fallback: str = "pod5") -> str:
        detected = self._infer_input_type_from_names([remote_input])
        if detected:
            return detected

        try:
            entries = await conn.list_dir(remote_input)
        except Exception as exc:
            logger.info(
                "Unable to inspect remote input directory for input type detection; using fallback",
                remote_input=remote_input,
                fallback=fallback,
                error=str(exc),
            )
            return fallback

        detected = self._infer_input_type_from_names([
            entry.get("name")
            for entry in entries
            if isinstance(entry, dict)
        ])
        return detected or fallback

    async def stage_remote_sample(self, params: SubmitParams) -> dict:
        """Stage references and input data on the remote system without submitting a job."""
        profile = await self._load_profile(params.ssh_profile_id, params.user_id)
        remote_roots = self._derive_remote_roots(params, profile)

        conn = await self._ssh_manager.connect(profile)
        try:
            results = await validate_remote_paths(
                conn,
                {
                    "base": remote_roots["remote_base_path"],
                    "ref": remote_roots["ref_root"],
                    "data": remote_roots["data_root"],
                },
            )
            ok, path_errors = check_all_paths_ok(results)
            if not ok:
                raise ValueError(f"Remote path validation failed: {'; '.join(path_errors)}")

            if params.remote_input_path or params.staged_remote_input_path:
                stage_result = await self._reuse_pre_staged_input(None, params, profile=profile, conn=conn)
            else:
                stage_result = await self._stage_sample_inputs(params, profile, conn, run_uuid=None)
            reference_statuses = dict(stage_result.get("reference_cache_statuses") or {})
            reference_asset_evidence, reference_statuses = await self._ensure_reference_assets_present(
                params=params,
                profile=profile,
                conn=conn,
                remote_reference_paths=stage_result["remote_reference_paths"],
                reference_statuses=reference_statuses,
            )
            return {
                "sample_name": params.sample_name,
                "ssh_profile_id": profile.id,
                "ssh_profile_nickname": profile.nickname,
                "remote_base_path": remote_roots["remote_base_path"],
                "remote_data_path": stage_result["remote_input"],
                "remote_reference_paths": stage_result["remote_reference_paths"],
                "data_cache_status": stage_result["data_cache_status"],
                "reference_cache_statuses": reference_statuses,
                "reference_asset_evidence": reference_asset_evidence,
                "detected_input_type": stage_result.get("detected_input_type") or params.input_type,
            }
        finally:
            await conn.close()

    async def submit(self, run_uuid: str, params: SubmitParams) -> str:
        """Submit a job to SLURM via SSH.

        Flow: validate resources → connect SSH → validate paths →
              transfer inputs (if needed) → generate sbatch → submit
        """
        # 1. Validate SLURM resources
        errors = validate_resources(
            cpus=params.slurm_cpus,
            memory_gb=params.slurm_memory_gb,
            walltime=params.slurm_walltime,
            gpus=params.slurm_gpus,
            gpu_type=params.slurm_gpu_type,
            partition=params.slurm_partition,
            account=params.slurm_account,
        )
        if errors:
            raise ValueError(f"Resource validation failed: {'; '.join(errors)}")

        # 2. Load SSH profile
        profile = await self._load_profile(params.ssh_profile_id, params.user_id)
        controller_account, controller_partition = self._resolve_controller_resources(params, profile)
        remote_paths = self._derive_remote_paths(params, profile)

        # 3. Connect and validate
        await self._update_job_stage(run_uuid, RunStage.VALIDATING_CONNECTION)
        conn = await self._ssh_manager.connect(profile)

        try:
            # 4. Validate/create remote paths
            await self._update_job_stage(run_uuid, RunStage.PREPARING_REMOTE_DIRS)
            results = await validate_remote_paths(
                conn,
                {
                    "base": remote_paths["remote_base_path"],
                    "ref": remote_paths["ref_root"],
                    "data": remote_paths["data_root"],
                    "project": remote_paths["project_root"],
                    "work": remote_paths["remote_work"],
                    "output": remote_paths["remote_output"],
                },
            )
            ok, path_errors = check_all_paths_ok(results)
            if not ok:
                raise ValueError(f"Remote path validation failed: {'; '.join(path_errors)}")

            # 5. Resolve reusable staging (reference + input data)
            try:
                if params.staged_remote_input_path:
                    logger.info(f"Reusing pre-staged input: {params.staged_remote_input_path}")
                    cache_resolution = await self._reuse_pre_staged_input(run_uuid, params, profile=profile, conn=conn)
                else:
                    logger.info("Resolving staging cache (fresh stage or reuse)")
                    cache_resolution = await self._resolve_staging_cache(run_uuid, params, profile, conn)
            except Exception as cache_error:
                if self._is_user_remote_input(params):
                    raise RuntimeError(f"Remote input path could not be prepared: {cache_error}") from cache_error
                logger.warning(
                    "Cache resolution degraded; falling back to direct staging",
                    run_uuid=run_uuid,
                    error=str(cache_error),
                )
                cache_resolution = await self._fallback_stage_inputs(run_uuid, params, profile, conn)

            reference_statuses = dict(cache_resolution.get("reference_cache_statuses") or {})
            reference_asset_evidence, reference_statuses = await self._ensure_reference_assets_present(
                params=params,
                profile=profile,
                conn=conn,
                remote_reference_paths=cache_resolution.get("remote_reference_paths") or {},
                reference_statuses=reference_statuses,
                run_uuid=run_uuid,
            )
            cache_resolution["reference_cache_statuses"] = reference_statuses
            cache_resolution["reference_asset_evidence"] = reference_asset_evidence

            # 6. Generate and submit sbatch script
            await self._update_job_stage(run_uuid, RunStage.SUBMITTING_JOB)

            remote_work = remote_paths["remote_work"]
            remote_output = remote_paths["remote_output"]
            remote_cache_root = remote_paths["remote_base_path"]
            remote_input = cache_resolution["remote_input"]
            detected_input_type = str(cache_resolution.get("detected_input_type") or "").strip().lower()
            if detected_input_type:
                params.input_type = detected_input_type

            # Ensure workflow-local input folders point at staged cache paths.
            await self._ensure_workflow_input_links(
                conn=conn,
                params=params,
                remote_work=remote_work,
                remote_input=remote_input,
            )

            # Build and write nextflow.config in remote workflow dir.
            remote_config = await self._write_remote_nextflow_config(
                params=params,
                profile=profile,
                conn=conn,
                remote_work=remote_work,
                cache_resolution=cache_resolution,
            )
            # Build the nextflow command for the batch script
            nf_cmd = self._build_nextflow_command(params, remote_input, remote_output, remote_config)

            script = generate_sbatch_script(
                job_name=f"agoutic-{params.sample_name}-{run_uuid[:8]}",
                account=controller_account,
                partition=controller_partition,
                cpus=params.slurm_cpus or 4,
                memory_gb=params.slurm_memory_gb or 16,
                walltime=params.slurm_walltime or "48:00:00",
                gpus=0,
                gpu_type=None,
                output_log=f"{remote_work}/slurm-%j.out",
                error_log=f"{remote_work}/slurm-%j.err",
                work_dir=remote_work,
                container_cache_dir=remote_cache_root,
                nextflow_command=nf_cmd,
            )

            # Write script to remote
            script_path = f"{remote_work}/submit_{run_uuid[:8]}.sh"
            await conn.mkdir_p(remote_work)
            quoted_script_path = shlex.quote(script_path)
            await conn.run(f"cat > {quoted_script_path} << 'AGOUTIC_EOF'\n{script}AGOUTIC_EOF", check=True)
            await conn.run(f"chmod +x {quoted_script_path}", check=True)

            # Submit via sbatch
            result = await conn.run_checked(f"sbatch --parsable {quoted_script_path}")
            slurm_job_id = result.strip()

            # Update job record with SLURM info
            await self._update_job_slurm_info(run_uuid, slurm_job_id, remote_work, remote_output)
            await self._update_job_stage(run_uuid, RunStage.QUEUED)

            logger.info(f"Submitted SLURM job {slurm_job_id} for run {run_uuid}")
            return run_uuid

        finally:
            await conn.close()

    async def rerun_existing(
        self,
        run_uuid: str,
        params: SubmitParams,
        *,
        remote_work: str,
        remote_output: str,
        local_work_dir: str | None = None,
        archive_sample_names: list[str] | None = None,
    ) -> str:
        """Rerun an existing remote workflow directory in place with Nextflow -rerun."""
        profile = await self._load_profile(params.ssh_profile_id, params.user_id)
        controller_account, controller_partition = self._resolve_controller_resources(params, profile)

        if local_work_dir:
            local_path = Path(local_work_dir)
            if local_path.exists():
                from launchpad.nextflow_executor import NextflowExecutor

                NextflowExecutor.prepare_rerun_directory(local_path, archive_sample_names or [params.sample_name])

        conn = await self._ssh_manager.connect(profile)
        try:
            if not await conn.path_exists(remote_work):
                raise FileNotFoundError(f"Remote workflow directory not found: {remote_work}")

            await self._archive_remote_workflow_artifacts(conn, remote_work, archive_sample_names or [params.sample_name])

            remote_config = str(PurePosixPath(remote_work) / "nextflow.config")
            if not await conn.path_exists(remote_config):
                raise FileNotFoundError(f"Remote nextflow.config not found: {remote_config}")

            remote_input = str(params.data_cache_path or "").strip() or self._workflow_input_link_path(params, remote_work)
            nf_cmd = self._build_nextflow_command(
                params,
                remote_input,
                remote_output,
                remote_config,
                rerun_in_place=True,
            )

            remote_cache_root = (profile.remote_base_path or "").strip() or remote_work
            script = generate_sbatch_script(
                job_name=f"agoutic-{params.sample_name}-{run_uuid[:8]}",
                account=controller_account,
                partition=controller_partition,
                cpus=params.slurm_cpus or 4,
                memory_gb=params.slurm_memory_gb or 16,
                walltime=params.slurm_walltime or "48:00:00",
                gpus=0,
                gpu_type=None,
                output_log=f"{remote_work}/slurm-%j.out",
                error_log=f"{remote_work}/slurm-%j.err",
                work_dir=remote_work,
                container_cache_dir=f"{remote_cache_root}/.nxf-apptainer-cache",
                nextflow_command=nf_cmd,
            )

            script_path = f"{remote_work}/submit_{run_uuid[:8]}.sh"
            quoted_script_path = shlex.quote(script_path)
            await conn.run(f"cat > {quoted_script_path} << 'AGOUTIC_EOF'\n{script}AGOUTIC_EOF", check=True)
            await conn.run(f"chmod +x {quoted_script_path}", check=True)

            slurm_job_id = (await conn.run_checked(f"sbatch --parsable {quoted_script_path}")).strip()
            await self._update_job_slurm_info(run_uuid, slurm_job_id, remote_work, remote_output)
            await self._update_job_stage(run_uuid, RunStage.QUEUED)
            return run_uuid
        finally:
            await conn.close()

    async def rename_workflow(
        self,
        *,
        ssh_profile_id: str | None,
        user_id: str | None,
        old_remote_work: str,
        old_sample_names: list[str] | None,
        new_folder_name: str,
        new_sample_name: str,
    ) -> tuple[str, str]:
        profile = await self._load_profile(ssh_profile_id, user_id)
        conn = await self._ssh_manager.connect(profile)
        try:
            old_remote_path = PurePosixPath(old_remote_work)
            new_remote_work = str(old_remote_path.parent / new_folder_name)
            if new_remote_work != old_remote_work and await conn.path_exists(new_remote_work):
                raise FileExistsError(f"Remote workflow folder already exists: {new_remote_work}")
            if new_remote_work != old_remote_work:
                await conn.run(
                    f"mv {shlex.quote(old_remote_work)} {shlex.quote(new_remote_work)}",
                    check=True,
                )

            await self._rename_remote_workflow_artifacts(
                conn,
                remote_work=new_remote_work,
                sample_names=old_sample_names,
                new_sample_name=new_sample_name,
            )
            return new_remote_work, str(PurePosixPath(new_remote_work) / "output")
        finally:
            await conn.close()

    async def check_status(self, run_uuid: str) -> JobStatus:
        """Check SLURM job status via sacct/squeue."""
        from launchpad.db import get_job_by_uuid as get_job
        job = await get_job(run_uuid)
        if not job:
            return JobStatus(run_uuid=run_uuid, status="UNKNOWN", message="Job not found")

        if not job.slurm_job_id:
            return JobStatus(
                run_uuid=run_uuid,
                status=job.status,
                execution_mode="slurm",
                run_stage=job.run_stage,
                message="No SLURM job ID — job may not be submitted yet",
            )

        # Query SLURM
        profile = await self._load_profile(job.ssh_profile_id, job.user_id)
        try:
            conn = await self._ssh_manager.connect(profile)
            try:
                # Try sacct first (works for completed/failed jobs too)
                result = await conn.run(
                    f"sacct -j {shlex.quote(str(job.slurm_job_id))} --format=State,ExitCode,Reason --noheader --parsable2 | head -1"
                )
                output = (result.stdout or "").strip()

                if output:
                    parts = output.split("|")
                    raw_state = parts[0] if parts else "UNKNOWN"
                    exit_code = parts[1] if len(parts) > 1 else ""
                    reason = parts[2] if len(parts) > 2 else ""
                else:
                    # Fallback to squeue for pending/running jobs
                    result = await conn.run(
                        f"squeue -j {shlex.quote(str(job.slurm_job_id))} --format=%T --noheader"
                    )
                    raw_state = (result.stdout or "").strip() or "UNKNOWN"
                    reason = ""

                agoutic_status, message = map_slurm_state(raw_state)
                base_state = raw_state.split()[0].upper() if raw_state else "UNKNOWN"

                if base_state == "PENDING" and reason:
                    message += f" — {explain_pending_reason(reason)}"
                elif agoutic_status == "FAILED":
                    details: list[str] = []
                    if exit_code and exit_code != "0:0":
                        details.append(f"exit code {exit_code}")
                    if reason:
                        details.append(explain_failure(reason))
                    if details:
                        message += f" — {'; '.join(details)}"
                elif agoutic_status == "CANCELLED" and reason:
                    message += f" — {reason}"

                progress_percent = 100 if agoutic_status == "COMPLETED" else 0
                tasks: dict | None = None
                remote_work_dir = getattr(job, "remote_work_dir", None)
                if remote_work_dir:
                    progress_percent, tasks, task_message = await self._read_remote_task_status(
                        conn=conn,
                        remote_work=remote_work_dir,
                        slurm_job_id=str(job.slurm_job_id),
                        scheduler_status=agoutic_status,
                    )
                    if tasks and agoutic_status == "RUNNING":
                        scheduler_counts = await self._read_scheduler_job_counts(
                            conn=conn,
                            remote_work=remote_work_dir,
                            slurm_job_id=str(job.slurm_job_id),
                            ssh_username=getattr(profile, "ssh_username", None),
                        )
                        if scheduler_counts:
                            tasks.update(scheduler_counts)
                            task_message = self._build_pipeline_message(
                                tasks=tasks,
                                scheduler_status=agoutic_status,
                            )
                    if task_message and agoutic_status == "RUNNING":
                        message = task_message
                    elif task_message and agoutic_status == "FAILED":
                        message = f"{message} — {task_message}"

                if agoutic_status == "COMPLETED":
                    agoutic_status, copyback_message = await self._ensure_local_results_ready(
                        run_uuid=run_uuid,
                        job=job,
                        profile=profile,
                    )
                    if copyback_message:
                        message = copyback_message
                    if agoutic_status == "RUNNING":
                        progress_percent = min(max(progress_percent, 95), 99)
                    elif agoutic_status == "FAILED":
                        progress_percent = 0

                # Update DB with latest state
                await self._update_job_slurm_state(
                    run_uuid,
                    raw_state,
                    agoutic_status,
                    error_message=message if agoutic_status in {"FAILED", "CANCELLED"} else None,
                )

                return JobStatus(
                    run_uuid=run_uuid,
                    status=agoutic_status,
                    progress_percent=progress_percent,
                    tasks=tasks,
                    execution_mode="slurm",
                    run_stage=job.run_stage,
                    slurm_job_id=job.slurm_job_id,
                    slurm_state=raw_state,
                    transfer_state=job.transfer_state,
                    transfer_detail=self.get_transfer_detail(run_uuid),
                    result_destination=job.result_destination,
                    message=message,
                    ssh_profile_nickname=profile.nickname,
                    work_directory=self._effective_work_directory(job),
                )
            finally:
                await conn.close()

        except Exception as e:
            logger.warning(f"Failed to poll SLURM status for {run_uuid}: {e}")
            return JobStatus(
                run_uuid=run_uuid,
                status=job.status,
                progress_percent=getattr(job, "progress_percent", 0),
                execution_mode="slurm",
                run_stage=job.run_stage,
                slurm_job_id=job.slurm_job_id,
                slurm_state=job.slurm_state,
                message=f"Failed to poll scheduler: {e}",
                work_directory=self._effective_work_directory(job),
            )

    async def _read_remote_task_status(
        self,
        *,
        conn,
        remote_work: str,
        slurm_job_id: str,
        scheduler_status: str,
    ) -> tuple[int, dict | None, str | None]:
        """Read remote trace/stdout files and derive task-level progress."""
        quoted_remote_work = shlex.quote(remote_work)
        # Read only the tail of the trace file. Large Nextflow runs can make
        # the full trace too large for a single SSH stdout payload.
        trace_content = ""
        try:
            trace_result = await conn.run(
                f"tail -n 5000 {quoted_remote_work}/*_trace.txt 2>/dev/null "
                f"|| tail -n 5000 {quoted_remote_work}/trace.txt 2>/dev/null "
                f"|| true"
            )
            trace_content = trace_result.stdout or ""
        except Exception as exc:
            logger.warning(
                "Failed to read remote Nextflow trace tail; falling back to stdout-only task progress",
                remote_work=remote_work,
                slurm_job_id=slurm_job_id,
                error=str(exc),
            )

        stdout_path = f"{remote_work}/slurm-{slurm_job_id}.out"
        stdout_result = await conn.run(
            f"tail -n 500 {shlex.quote(stdout_path)} 2>/dev/null || true"
        )
        stdout_content = stdout_result.stdout or ""

        logger.info(
            "Remote task status raw content",
            trace_lines=len(trace_content.splitlines()) if trace_content else 0,
            stdout_lines=len(stdout_content.splitlines()) if stdout_content else 0,
            trace_preview=trace_content[:500] if trace_content else "(empty)",
            stdout_tail=stdout_content[-300:] if stdout_content else "(empty)",
        )

        return self._parse_task_status_texts(
            trace_content=trace_content,
            stdout_content=stdout_content,
            scheduler_status=scheduler_status,
        )

    @staticmethod
    def _parse_task_status_texts(
        *,
        trace_content: str,
        stdout_content: str,
        scheduler_status: str,
    ) -> tuple[int, dict | None, str | None]:
        def _task_suffix(name: str) -> str:
            """Return the last task segment for cross-prefix matching."""
            parts = name.rsplit(":", 1)
            return parts[-1] if parts else name

        def _task_family_key(name: str) -> str:
            """Return a family key that collapses numbered task instances."""
            suffix = _task_suffix(name).strip()
            return re.sub(r"\s*\(\d+\)$", "", suffix)

        completed_tasks: list[str] = []
        failed_tasks: list[str] = []
        running_tasks: list[str] = []
        submitted_count = 0
        retried_count = 0

        trace_lines = trace_content.splitlines() if trace_content else []
        terminal_status_by_task: dict[str, tuple[int, str]] = {}
        if len(trace_lines) > 1:
            for line_index, line in enumerate(trace_lines[1:], start=1):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 5:
                    continue
                task_name = parts[3].strip()
                task_status = parts[4].strip()
                if ":" not in task_name:
                    continue
                if task_status in {"COMPLETED", "FAILED", "ABORTED"}:
                    terminal_status_by_task[task_name] = (line_index, task_status)

        if terminal_status_by_task:
            for task_name, (_line_index, task_status) in sorted(
                terminal_status_by_task.items(),
                key=lambda item: item[1][0],
            ):
                if task_status == "COMPLETED":
                    completed_tasks.append(task_name)
                elif task_status in {"FAILED", "ABORTED"}:
                    failed_tasks.append(task_name)

        # Strip ANSI escape codes so cursor-control sequences from
        # Nextflow's default ANSI log mode don't break line parsing.
        _ansi_escape = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?[0-9;]*[A-Za-z]|\r')
        clean_stdout = _ansi_escape.sub('', stdout_content)
        family_progress: dict[str, dict[str, int]] = {}

        task_events_by_hash: dict[str, tuple[str, bool]] = {}
        for line in clean_stdout.splitlines():
            if "executor >" in line and "(" in line:
                try:
                    count_str = line.split("(", 1)[1].split(")", 1)[0]
                    submitted_count = max(submitted_count, int(count_str))
                except Exception:
                    pass

            retry_match = re.search(r'retries:\s*(\d+)\b', line, flags=re.IGNORECASE)
            if retry_match:
                try:
                    retried_count = max(retried_count, int(retry_match.group(1)))
                except Exception:
                    pass

            if not line.startswith("[") or "]" not in line or ":" not in line.split("]", 1)[-1]:
                continue
            hash_part = line.split("]", 1)[0] + "]"
            if "/" not in hash_part:
                continue
            rest = line.split("]", 1)[1].strip()
            # Handle "Submitted process > taskName" and "process > taskName"
            if ">" in rest:
                rest = rest.split(">", 1)[1].strip()
            task_name = rest.split()[0] if rest else ""
            match = re.search(r"(\(\d+\))", rest)
            if match and match.group(1) not in task_name:
                task_name = f"{task_name} {match.group(1)}"
            if not task_name:
                continue

            progress_match = re.search(r'\|\s*(\d+)\s+of\s+(\d+)\b', line)
            if progress_match:
                try:
                    family_key = _task_family_key(task_name)
                    progress_state = family_progress.setdefault(
                        family_key,
                        {"completed": 0, "total": 0},
                    )
                    progress_state["completed"] = max(progress_state["completed"], int(progress_match.group(1)))
                    progress_state["total"] = max(progress_state["total"], int(progress_match.group(2)))
                except Exception:
                    pass

            is_completed_stdout = "✔" in line
            is_failed_stdout = not is_completed_stdout and "FAILED" in line.upper()
            task_events_by_hash[hash_part] = (task_name, is_completed_stdout, is_failed_stdout)

        # Use trace file as authoritative source for completed/failed.
        # Only fall back to stdout ✔/FAILED when trace has no results.
        trace_has_results = bool(completed_tasks or failed_tasks)

        terminal_suffixes = {
            _task_suffix(t) for t in completed_tasks + failed_tasks
        }

        for task_name, is_completed_stdout, is_failed_stdout in task_events_by_hash.values():
            if is_completed_stdout:
                if not trace_has_results and task_name not in completed_tasks:
                    completed_tasks.append(task_name)
                continue
            if is_failed_stdout:
                if not trace_has_results and task_name not in failed_tasks:
                    failed_tasks.append(task_name)
                continue
            if task_name in completed_tasks or task_name in failed_tasks:
                continue
            if _task_suffix(task_name) in terminal_suffixes:
                continue
            if task_name not in running_tasks:
                running_tasks.append(task_name)

        completed_count = len(completed_tasks)
        failed_count = len(failed_tasks)

        completed_by_family: dict[str, int] = {}
        for task_name in completed_tasks:
            family_key = _task_family_key(task_name)
            completed_by_family[family_key] = completed_by_family.get(family_key, 0) + 1

        failed_by_family: dict[str, int] = {}
        for task_name in failed_tasks:
            family_key = _task_family_key(task_name)
            failed_by_family[family_key] = failed_by_family.get(family_key, 0) + 1

        running_by_family: dict[str, int] = {}
        for task_name in running_tasks:
            family_key = _task_family_key(task_name)
            running_by_family[family_key] = running_by_family.get(family_key, 0) + 1

        family_keys = set(family_progress) | set(completed_by_family) | set(failed_by_family) | set(running_by_family)
        aggregate_completed = 0
        aggregate_failed = 0
        aggregate_total = 0
        for family_key in family_keys:
            progress_state = family_progress.get(family_key, {"completed": 0, "total": 0})
            completed_terminal = completed_by_family.get(family_key, 0)
            failed_terminal = failed_by_family.get(family_key, 0)
            running_terminal = running_by_family.get(family_key, 0)
            aggregate_completed += max(progress_state["completed"], completed_terminal)
            aggregate_failed += failed_terminal
            aggregate_total += max(
                progress_state["total"],
                completed_terminal + failed_terminal + running_terminal,
            )

        unique_total = 0
        if family_progress:
            unique_total = aggregate_total
        elif aggregate_total > 0:
            unique_total = max(submitted_count, aggregate_total)

        if unique_total <= 0:
            unique_total = max(submitted_count, completed_count + len(running_tasks) + failed_count)

        if aggregate_completed > 0:
            completed_count = max(completed_count, aggregate_completed)
        if aggregate_failed > 0:
            failed_count = max(failed_count, aggregate_failed)

        inferred_running_count = 0
        if unique_total > 0:
            inferred_running_count = max(unique_total - completed_count - failed_count, 0)

        total = unique_total
        if total <= 0 and not completed_tasks and not running_tasks and not failed_tasks:
            if scheduler_status == "RUNNING":
                return 10, None, "Pipeline starting..."
            if scheduler_status == "COMPLETED":
                return 100, None, None
            return 0, None, None

        tasks = {
            "completed": completed_tasks,
            "running": running_tasks[-5:] if len(running_tasks) > 5 else running_tasks,
            "total": total,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "retried_count": retried_count,
            "remaining_count": inferred_running_count,
            "observed_running_count": len(running_tasks),
        }

        if scheduler_status == "COMPLETED":
            return 100, tasks, f"Pipeline: {completed_count}/{max(total, completed_count)} completed"

        progress = int((completed_count / total) * 90) if total > 0 else 10
        progress = max(progress, 10 if scheduler_status == "RUNNING" else progress)

        message = SlurmBackend._build_pipeline_message(
            tasks=tasks,
            scheduler_status=scheduler_status,
        )
        return progress, tasks, message

    @classmethod
    def _build_result_sync_include_patterns(cls) -> list[str]:
        return [*(f"{name}/***" for name in cls._RESULT_SYNC_DIRS), *cls._RESULT_SYNC_FILE_PATTERNS]

    @staticmethod
    def _needs_local_result_copy(job) -> bool:
        destination = (getattr(job, "result_destination", "") or "").strip().lower()
        return (
            destination in {"local", "both"}
            and bool(getattr(job, "remote_work_dir", None))
            and bool(getattr(job, "nextflow_work_dir", None))
        )

    async def _ensure_local_results_ready(
        self,
        *,
        run_uuid: str,
        job,
        profile: SSHProfileData,
    ) -> tuple[str, str | None]:
        from launchpad.db import get_job_by_uuid as get_job

        current_job = await get_job(run_uuid) or job
        if not self._needs_local_result_copy(current_job):
            return "COMPLETED", None

        transfer_state = (getattr(current_job, "transfer_state", "") or "").strip().lower()
        if transfer_state == "outputs_downloaded":
            return "COMPLETED", None
        if transfer_state == "transfer_failed":
            return "FAILED", "Remote job completed, but copying results back to the local workflow failed."

        task = self._result_sync_tasks.get(run_uuid)
        if task is None or task.done():
            if task is not None:
                try:
                    task.result()
                except Exception as exc:  # pragma: no cover - defensive task cleanup
                    logger.warning("Background result sync task ended with error", run_uuid=run_uuid, error=str(exc))
            self._result_sync_tasks[run_uuid] = asyncio.create_task(
                self._copy_selected_results_to_local(run_uuid=run_uuid, job=current_job, profile=profile)
            )
        return "RUNNING", "Copying results back to the local workflow..."

    def get_transfer_detail(self, run_uuid: str) -> str | None:
        """Return a human-readable summary of in-flight transfer progress."""
        info = self._transfer_progress.get(run_uuid)
        if not info:
            return None
        if info.get("error"):
            return str(info.get("error"))
        parts: list[str] = []
        # Folder context: "bams/ (2/5 folders)"
        current_folder = info.get("current_folder", "")
        folder_done = info.get("folders_done", 0)
        folder_total = info.get("folders_total", 0)
        if current_folder:
            folder_label = f"{current_folder}/"
            if folder_total:
                folder_label += f" ({folder_done + 1}/{folder_total} folders)"
            parts.append(folder_label)
        if info.get("current_file"):
            parts.append(info["current_file"])
        xfr = info.get("files_transferred")
        total = info.get("files_total")
        if xfr is not None and total is not None:
            parts.append(f"{xfr}/{total} files")
        if info.get("speed"):
            parts.append(info["speed"])
        # Completed folders log
        done_folders = info.get("done_folders", [])
        if done_folders:
            parts.append("✓ " + ", ".join(done_folders))
        return " — ".join(parts) if parts else None

    async def _copy_selected_results_to_local(self, *, run_uuid: str, job, profile: SSHProfileData) -> None:
        local_work_dir = getattr(job, "nextflow_work_dir", None)
        remote_work_dir = getattr(job, "remote_work_dir", None)
        input_directory = getattr(job, "input_directory", None)
        # Nextflow writes results to remote_output_dir (remote_work_dir/output).
        # Pre-migration jobs may have NULL remote_output_dir; derive from remote_work_dir.
        remote_output_dir = getattr(job, "remote_output_dir", None) or (
            str(PurePosixPath(remote_work_dir) / "output") if remote_work_dir else None
        )
        try:
            if not local_work_dir or not remote_work_dir:
                raise RuntimeError("Missing local or remote workflow directory for result copy-back")

            await self._update_job_transfer_state(run_uuid, "downloading_outputs")
            self._transfer_progress[run_uuid] = {
                "current_folder": "discovering artifacts",
                "folders_done": 0,
                "folders_total": 0,
                "done_folders": [],
                "current_file": "",
            }
            artifact_root = remote_output_dir
            artifacts = await self._discover_remote_result_artifacts(
                profile=profile,
                remote_output_dir=remote_output_dir,
            )
            if (
                not artifacts.get("directories")
                and not artifacts.get("files")
                and remote_work_dir
                and remote_output_dir
                and PurePosixPath(remote_work_dir) != PurePosixPath(remote_output_dir)
            ):
                logger.warning(
                    "No result artifacts found in remote output dir; falling back to workflow root",
                    run_uuid=run_uuid,
                    remote_output_dir=remote_output_dir,
                    remote_work_dir=remote_work_dir,
                )
                self._transfer_progress[run_uuid] = {
                    "current_folder": "discovering artifacts in workflow root",
                    "folders_done": 0,
                    "folders_total": 0,
                    "done_folders": [],
                    "current_file": "",
                }
                fallback_artifacts = await self._discover_remote_result_artifacts(
                    profile=profile,
                    remote_output_dir=remote_work_dir,
                )
                if fallback_artifacts.get("directories") or fallback_artifacts.get("files"):
                    artifacts = fallback_artifacts
                    artifact_root = remote_work_dir

            if not artifacts.get("directories") and not artifacts.get("files"):
                searched_locations = [remote_output_dir]
                if artifact_root != remote_work_dir and remote_work_dir:
                    searched_locations.append(remote_work_dir)
                searched_locations = [path for path in searched_locations if path]
                raise RuntimeError(
                    f"No result artifacts found on remote at {' or '.join(searched_locations)}. "
                    "The remote job may not have produced output yet."
                )
            logger.info(
                "Starting result sync (per-directory)",
                run_uuid=run_uuid,
                remote_output_dir=remote_output_dir,
                artifact_root=artifact_root,
                local_work_dir=local_work_dir,
                remote_artifacts=artifacts,
            )

            def _on_rsync_progress(info: dict) -> None:
                current = self._transfer_progress.get(run_uuid, {})
                if "current_file" in info:
                    current["current_file"] = info["current_file"]
                if "files_transferred" in info:
                    current["files_transferred"] = info["files_transferred"]
                    current["files_total"] = info.get("files_total", current.get("files_total"))
                if "speed" in info:
                    current["speed"] = info["speed"]
                self._transfer_progress[run_uuid] = current

            total_bytes = 0
            errors: list[str] = []
            result_dirs = artifacts.get("directories", [])
            done_folders: list[str] = []

            # 1. Sync each subdirectory individually (no --copy-links so
            #    remote symlinks are transferred as symlinks, not dereferenced).
            for dir_idx, dirname in enumerate(result_dirs):
                self._transfer_progress[run_uuid] = {
                    "current_folder": dirname,
                    "folders_done": dir_idx,
                    "folders_total": len(result_dirs),
                    "done_folders": list(done_folders),
                    "current_file": "",
                }
                transfer = await self._transfer_manager.download_outputs(
                    profile=profile,
                    remote_path=str(PurePosixPath(artifact_root) / dirname),
                    local_path=str(Path(local_work_dir) / dirname),
                    on_progress=_on_rsync_progress,
                )
                logger.info(
                    "rsync dir transfer finished",
                    run_uuid=run_uuid,
                    dirname=dirname,
                    ok=transfer.get("ok"),
                    bytes_transferred=transfer.get("bytes_transferred"),
                )
                if transfer.get("ok"):
                    total_bytes += transfer.get("bytes_transferred", 0)
                    done_folders.append(dirname)
                else:
                    errors.append(f"{dirname}: {transfer.get('message', 'unknown error')}")

            # 2. Sync root-level result files (*.config, *.html, etc.)
            root_files = artifacts.get("files", [])
            if root_files:
                self._transfer_progress[run_uuid] = {
                    "current_folder": "root files",
                    "folders_done": len(result_dirs),
                    "folders_total": len(result_dirs) + 1,
                    "done_folders": list(done_folders),
                    "current_file": "",
                }
                transfer = await self._transfer_manager.download_outputs(
                    profile=profile,
                    remote_path=artifact_root,
                    local_path=local_work_dir,
                    include_patterns=list(self._RESULT_SYNC_FILE_PATTERNS),
                    exclude_patterns=["*"],
                    on_progress=_on_rsync_progress,
                )
                if transfer.get("ok"):
                    total_bytes += transfer.get("bytes_transferred", 0)
                else:
                    errors.append(f"root files: {transfer.get('message', 'unknown error')}")

            if errors:
                raise RuntimeError(
                    f"rsync transfer failed for: {'; '.join(errors)}"
                )

            # 3. Resolve symlinks: replace broken remote symlinks with
            #    links pointing to matching files in the local input dir.
            self._resolve_local_symlinks(
                local_work_dir=local_work_dir,
                input_directory=input_directory,
                directories=artifacts.get("directories", []),
            )

            self._verify_local_result_artifacts(local_work_dir=local_work_dir, artifacts=artifacts)
            await self._update_job_transfer_state(run_uuid, "outputs_downloaded")
            return {
                "success": True,
                "status": "outputs_downloaded",
                "message": "Remote outputs synchronized to local workflow directory.",
                "run_uuid": run_uuid,
                "remote_work_dir": remote_work_dir,
                "local_work_dir": local_work_dir,
            }
        except Exception as exc:
            _error_message = str(exc)
            self._transfer_progress[run_uuid] = {
                "error": _error_message,
                "current_folder": "sync failed",
                "folders_done": 0,
                "folders_total": 0,
                "done_folders": [],
                "current_file": "",
            }
            await self._update_job_transfer_state(
                run_uuid,
                "transfer_failed",
                error_message=_error_message,
            )
            logger.error("Selective result copy-back failed", run_uuid=run_uuid, error=_error_message)
            return {
                "success": False,
                "status": "transfer_failed",
                "message": _error_message,
                "run_uuid": run_uuid,
                "remote_work_dir": remote_work_dir,
                "local_work_dir": local_work_dir,
            }
        finally:
            _progress = self._transfer_progress.get(run_uuid)
            if not (_progress and _progress.get("error")):
                self._transfer_progress.pop(run_uuid, None)
            self._result_sync_tasks.pop(run_uuid, None)

    async def sync_results_to_local(self, *, run_uuid: str, force: bool = False) -> dict:
        """Manually trigger remote->local result synchronization for a SLURM run."""
        from launchpad.db import get_job_by_uuid as get_job

        job = await get_job(run_uuid)
        if not job:
            raise ValueError(f"Job not found: {run_uuid}")

        if not self._needs_local_result_copy(job):
            return {
                "success": False,
                "status": "not_applicable",
                "message": "This job is not configured for local/both result copy-back.",
                "run_uuid": run_uuid,
                "remote_work_dir": getattr(job, "remote_work_dir", None),
                "local_work_dir": getattr(job, "nextflow_work_dir", None),
                "transfer_state": getattr(job, "transfer_state", None),
            }

        transfer_state = (getattr(job, "transfer_state", "") or "").strip().lower()
        if transfer_state == "outputs_downloaded" and not force:
            return {
                "success": True,
                "status": "already_synced",
                "message": "Results are already synced locally. Use force=true to retry copy-back.",
                "run_uuid": run_uuid,
                "remote_work_dir": getattr(job, "remote_work_dir", None),
                "local_work_dir": getattr(job, "nextflow_work_dir", None),
                "transfer_state": transfer_state,
            }

        running_task = self._result_sync_tasks.get(run_uuid)
        if running_task and not running_task.done() and not force:
            return {
                "success": False,
                "status": "sync_in_progress",
                "message": "A result synchronization task is already in progress for this run.",
                "run_uuid": run_uuid,
                "remote_work_dir": getattr(job, "remote_work_dir", None),
                "local_work_dir": getattr(job, "nextflow_work_dir", None),
                "transfer_state": transfer_state,
            }

        if running_task and not running_task.done() and force:
            running_task.cancel()

        profile = await self._load_profile(job.ssh_profile_id, job.user_id)

        # Fire the sync as a background task and return immediately.
        # Large result sets (e.g. BAM runs) can take many minutes; keeping
        # the HTTP request open blocks the caller and risks MCP/httpx
        # timeouts that kill rsync mid-transfer (leaving empty directories).
        self._result_sync_tasks[run_uuid] = asyncio.create_task(
            self._copy_selected_results_to_local(
                run_uuid=run_uuid, job=job, profile=profile,
            )
        )
        return {
            "success": True,
            "status": "sync_started",
            "message": "Result synchronization started. Monitor progress via job status polling.",
            "run_uuid": run_uuid,
            "remote_work_dir": getattr(job, "remote_work_dir", None),
            "local_work_dir": getattr(job, "nextflow_work_dir", None),
            "transfer_state": "downloading_outputs",
        }

    async def _discover_remote_result_artifacts(self, *, profile: SSHProfileData, remote_output_dir: str) -> dict[str, list[str]]:
        conn = await self._ssh_manager.connect(profile)
        try:
            existing_dirs: list[str] = []
            for dirname in self._RESULT_SYNC_DIRS:
                if await conn.path_exists(str(PurePosixPath(remote_output_dir) / dirname)):
                    existing_dirs.append(dirname)

            file_predicate = " -o ".join(f"-name {shlex.quote(pattern)}" for pattern in self._RESULT_SYNC_FILE_PATTERNS)
            file_cmd = (
                f"find {shlex.quote(remote_output_dir)} -maxdepth 1 -type f \\( {file_predicate} \\) "
                "-exec basename {} \\; | sort"
            )
            result = await conn.run(f"{file_cmd} 2>/dev/null || true")
            existing_files = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
            return {"directories": existing_dirs, "files": existing_files}
        finally:
            await conn.close()

    @staticmethod
    def _verify_local_result_artifacts(*, local_work_dir: str, artifacts: dict[str, list[str]]) -> None:
        local_root = Path(local_work_dir)
        missing: list[str] = []
        for dirname in artifacts.get("directories", []):
            if not (local_root / dirname).exists():
                missing.append(dirname)
        for filename in artifacts.get("files", []):
            if not (local_root / filename).exists():
                missing.append(filename)
        if missing:
            raise RuntimeError(f"Local result copy-back is missing expected artifacts: {', '.join(sorted(missing))}")

    @staticmethod
    def _resolve_local_symlinks(
        *,
        local_work_dir: str,
        input_directory: str | None,
        directories: list[str],
    ) -> None:
        """Replace broken/remote symlinks with links to the local input dir.

        After per-directory rsync (without --copy-links), symlinks from the
        remote workflow (e.g. bams/sample.unmapped.bam -> /share/.../file.bam)
        arrive as literal symlinks whose targets don't exist locally.

        For each such symlink we look for a file with the **same name** inside
        *input_directory* (recursively) and replace the symlink with one
        pointing to the local copy.  If no match is found the symlink is
        removed so it doesn't leave a broken link.
        """
        if not input_directory:
            return
        input_root = Path(input_directory)
        if not input_root.is_dir():
            return
        # Build a name→path index of files in the input directory (one level
        # deep is enough — input dirs are typically flat hash dirs).
        input_index: dict[str, Path] = {}
        for entry in input_root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                input_index[entry.name] = entry

        local_root = Path(local_work_dir)
        for dirname in directories:
            subdir = local_root / dirname
            if not subdir.is_dir():
                continue
            for entry in subdir.iterdir():
                if not entry.is_symlink():
                    continue
                # Symlink exists — check if target is reachable
                if entry.exists():
                    continue  # target resolves fine, nothing to do
                # Broken symlink — try to resolve to local input file
                local_match = input_index.get(entry.name)
                if local_match:
                    entry.unlink()
                    entry.symlink_to(local_match)
                    logger.info(
                        "Resolved remote symlink to local input file",
                        link=str(entry),
                        target=str(local_match),
                    )
                else:
                    entry.unlink()
                    logger.info(
                        "Removed broken remote symlink (no local match)",
                        link=str(entry),
                    )

    async def cancel(self, run_uuid: str) -> bool:
        """Cancel a SLURM job via scancel."""
        from launchpad.db import get_job_by_uuid as get_job
        job = await get_job(run_uuid)
        if not job or not job.slurm_job_id:
            return False

        profile = await self._load_profile(job.ssh_profile_id, job.user_id)
        try:
            conn = await self._ssh_manager.connect(profile)
            try:
                await conn.run_checked(f"scancel {shlex.quote(str(job.slurm_job_id))}")
                await self._update_job_stage(run_uuid, RunStage.CANCELLED)
                logger.info(f"Cancelled SLURM job {job.slurm_job_id} for run {run_uuid}")
                return True
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"Failed to cancel SLURM job: {e}")
            return False

    async def get_logs(self, run_uuid: str, limit: int = 50) -> list[LogEntry]:
        """Get log entries from remote SLURM output files."""
        from launchpad.db import get_job_by_uuid as get_job
        job = await get_job(run_uuid)
        if not job or not job.slurm_job_id:
            return []

        profile = await self._load_profile(job.ssh_profile_id, job.user_id)
        logs: list[LogEntry] = []

        try:
            conn = await self._ssh_manager.connect(profile)
            try:
                work_dir = job.remote_work_dir or ""
                # Read stdout log
                for suffix, level in [("out", "INFO"), ("err", "ERROR")]:
                    log_path = f"{work_dir}/slurm-{job.slurm_job_id}.{suffix}"
                    result = await conn.run(f"tail -n {int(limit)} {shlex.quote(log_path)} 2>/dev/null || true")
                    if result.stdout:
                        for line in result.stdout.strip().split("\n"):
                            if line.strip():
                                logs.append(LogEntry(
                                    timestamp=datetime.now(timezone.utc).isoformat(),
                                    level=level,
                                    message=line,
                                    source=f"slurm-{suffix}",
                                ))
            finally:
                await conn.close()
        except Exception as e:
            logs.append(LogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                level="ERROR",
                message=f"Failed to read remote logs: {e}",
                source="agoutic",
            ))

        return logs[-limit:]

    async def cleanup(self, run_uuid: str) -> bool:
        """Clean up remote job artifacts."""
        from launchpad.db import get_job_by_uuid as get_job
        job = await get_job(run_uuid)
        if not job or not job.remote_work_dir:
            return False

        profile = await self._load_profile(job.ssh_profile_id, job.user_id)
        try:
            conn = await self._ssh_manager.connect(profile)
            try:
                await conn.run_checked(f"rm -rf {job.remote_work_dir!r}")
                return True
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"Failed to clean up remote job: {e}")
            return False

    async def list_remote_files(
        self,
        *,
        ssh_profile_id: str,
        user_id: str,
        path: str | None = None,
    ) -> dict:
        """List files on the remote host, defaulting to the profile's remote base path."""
        profile = await self._load_profile(ssh_profile_id, user_id)
        remote_path = self._resolve_remote_browse_path(profile, path)
        if not remote_path:
            raise ValueError("No path provided and the SSH profile is missing remote_base_path")

        conn = await self._ssh_manager.connect(profile)
        try:
            entries = await conn.list_dir(remote_path)
            return {
                "ssh_profile_id": profile.id,
                "ssh_profile_nickname": profile.nickname,
                "path": remote_path,
                "entries": entries,
            }
        finally:
            await conn.close()

    # --- Internal helpers ---

    async def _load_profile(self, profile_id: str | None, user_id: str | None = None) -> SSHProfileData:
        """Load an SSH profile from the database."""
        if not profile_id:
            raise ValueError("ssh_profile_id is required for SLURM execution")

        from launchpad.db import get_ssh_profile
        profile = await get_ssh_profile(profile_id, user_id=user_id)
        if not profile:
            raise ValueError(f"SSH profile not found: {profile_id}")
        return profile

    def _build_nextflow_command(
        self,
        params: SubmitParams,
        remote_input: str,
        remote_output: str,
        config_path: str,
        *,
        rerun_in_place: bool = False,
    ) -> str:
        """Build the Nextflow command line for the sbatch script."""
        genome_list = ",".join(params.reference_genome)
        cmd_parts = [
            '"${AGOUTIC_NEXTFLOW_BIN:-nextflow}" run mortazavilab/dogme',
            f"--sample_name {shlex.quote(params.sample_name)}",
            f"--mode {shlex.quote(params.mode)}",
            f"--input {shlex.quote(remote_input)}",
            f"--outdir {shlex.quote(remote_output)}",
            f"--reference_genome {shlex.quote(genome_list)}",
            f"-c {shlex.quote(config_path)}",
        ]
        if params.modifications:
            cmd_parts.append(f"--modifications {shlex.quote(params.modifications)}")
        if params.entry_point:
            cmd_parts.append(f"-entry {shlex.quote(params.entry_point)}")
        if rerun_in_place:
            cmd_parts.append("-rerun")
        return " \\\n    ".join(cmd_parts)

    @staticmethod
    def _workflow_input_link_path(params: SubmitParams, remote_work: str) -> str:
        input_type = (params.input_type or "pod5").strip().lower()
        link_dir_name = {
            "pod5": "pod5",
            "bam": "bams",
            "fastq": "fastqs",
            "fq": "fastqs",
        }.get(input_type, "pod5")
        return str(PurePosixPath(remote_work) / link_dir_name)

    @staticmethod
    async def _archive_remote_workflow_artifacts(conn, remote_work: str, sample_names: list[str] | None = None) -> None:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        candidate_names = ["report.html"]
        for sample_name in sample_names or []:
            cleaned = str(sample_name or "").strip()
            if not cleaned:
                continue
            candidate_names.extend(
                [
                    f"{cleaned}_report.html",
                    f"{cleaned}_timeline.html",
                    f"{cleaned}_trace.txt",
                ]
            )

        seen: set[str] = set()
        for file_name in candidate_names:
            if file_name in seen:
                continue
            seen.add(file_name)
            file_path = str(PurePosixPath(remote_work) / file_name)
            if not await conn.path_exists(file_path):
                continue
            path_obj = PurePosixPath(file_path)
            suffix = path_obj.suffix
            stem = path_obj.name[:-len(suffix)] if suffix else path_obj.name
            archive_name = f"{stem}.rerun_{timestamp}{suffix}"
            target_path = str(path_obj.with_name(archive_name))
            await conn.run(f"mv {shlex.quote(file_path)} {shlex.quote(target_path)}", check=True)

        for marker_name in (
            ".nextflow_success",
            ".nextflow_failed",
            ".nextflow_cancelled",
            ".nextflow_running",
            ".nextflow_error",
            ".launch_error",
            ".nextflow_pid",
        ):
            marker_path = str(PurePosixPath(remote_work) / marker_name)
            if await conn.path_exists(marker_path):
                await conn.run(f"rm -f {shlex.quote(marker_path)}", check=True)

    @staticmethod
    async def _rename_remote_workflow_artifacts(
        conn,
        remote_work: str,
        sample_names: list[str] | None,
        new_sample_name: str,
    ) -> None:
        seen: set[str] = set()
        for sample_name in sample_names or []:
            cleaned = str(sample_name or "").strip()
            if not cleaned or cleaned == new_sample_name or cleaned in seen:
                continue
            seen.add(cleaned)
            for suffix in ("report.html", "timeline.html", "trace.txt"):
                file_path = str(PurePosixPath(remote_work) / f"{cleaned}_{suffix}")
                target_path = str(PurePosixPath(remote_work) / f"{new_sample_name}_{suffix}")
                if not await conn.path_exists(file_path):
                    continue
                if await conn.path_exists(target_path):
                    raise FileExistsError(f"Remote workflow artifact already exists: {target_path}")
                await conn.run(f"mv {shlex.quote(file_path)} {shlex.quote(target_path)}", check=True)

    def _resolve_controller_resources(
        self,
        params: SubmitParams,
        profile: SSHProfileData,
    ) -> tuple[str, str]:
        """Choose CPU resources for the Nextflow controller job.

        The controller only orchestrates the workflow; GPU requests belong to
        the individual pipeline tasks configured in nextflow.config.
        """
        cpu_account = (profile.default_slurm_account or params.slurm_account or "default").strip() or "default"
        cpu_partition = (profile.default_slurm_partition or params.slurm_partition or "standard").strip() or "standard"
        return cpu_account, cpu_partition

    async def _write_remote_nextflow_config(
        self,
        *,
        params: SubmitParams,
        profile: SSHProfileData,
        conn,
        remote_work: str,
        cache_resolution: dict,
    ) -> str:
        """Generate and write Nextflow config in the remote workflow directory."""
        cpu_account = (profile.default_slurm_account or params.slurm_account or "default").strip() or "default"
        cpu_partition = (profile.default_slurm_partition or params.slurm_partition or "standard").strip() or "standard"
        gpu_account = (profile.default_slurm_gpu_account or params.slurm_account or cpu_account).strip() or cpu_account
        gpu_partition = (profile.default_slurm_gpu_partition or params.slurm_partition or cpu_partition).strip() or cpu_partition

        # Prefer staged remote reference cache paths so config does not point at local host paths.
        ref_overrides: dict[str, dict[str, str]] = {}
        remote_reference_paths = {
            str(k).strip().lower(): str(v)
            for k, v in (cache_resolution.get("remote_reference_paths") or {}).items()
            if k and v
        }
        logger.info(
            "Building reference overrides",
            cache_resolution_keys=list(cache_resolution.keys()) if cache_resolution else None,
            remote_reference_paths=remote_reference_paths,
            requested_genomes=params.reference_genome,
        )
        
        if not remote_reference_paths and params.reference_cache_path and params.reference_genome:
            first_ref = self._normalize_reference_id((params.reference_genome or ["default"])[0])
            remote_reference_paths[first_ref] = params.reference_cache_path
            logger.info(f"Using fallback reference_cache_path for {first_ref}: {params.reference_cache_path}")

        # Final safety net: derive reference roots from remote base path when cache metadata is absent.
        if not remote_reference_paths and params.reference_genome:
            derived_ref_root = self._derive_remote_roots(params, profile)["ref_root"]
            for genome_name in params.reference_genome or []:
                ref_id = self._normalize_reference_id(genome_name)
                remote_reference_paths[ref_id] = str(PurePosixPath(derived_ref_root) / ref_id)
            logger.info(
                "Derived remote reference paths from remote base root",
                remote_reference_paths=remote_reference_paths,
            )

        lower_map = {k.lower(): k for k in REFERENCE_GENOMES.keys()}
        for genome_name in params.reference_genome or []:
            ref_id = self._normalize_reference_id(genome_name)
            remote_ref_root = remote_reference_paths.get(ref_id)
            if not remote_ref_root:
                logger.warning(f"No remote reference path found for {genome_name} (normalized: {ref_id}); will use defaults")
                continue

            canonical_name = lower_map.get(str(genome_name).lower(), genome_name)
            ref_cfg = REFERENCE_GENOMES.get(canonical_name, REFERENCE_GENOMES.get("mm39", {}))
            fasta_src = ref_cfg.get("fasta")
            gtf_src = ref_cfg.get("gtf")
            if not fasta_src or not gtf_src:
                continue

            ref_overrides[str(genome_name)] = {
                "fasta": str(PurePosixPath(remote_ref_root) / Path(fasta_src).name),
                "gtf": str(PurePosixPath(remote_ref_root) / Path(gtf_src).name),
            }
            kallisto_src = ref_cfg.get("kallisto_index")
            t2g_src = ref_cfg.get("kallisto_t2g")
            if kallisto_src:
                ref_overrides[str(genome_name)]["kallisto_index"] = str(
                    PurePosixPath(remote_ref_root) / Path(kallisto_src).name
                )
            if t2g_src:
                ref_overrides[str(genome_name)]["kallisto_t2g"] = str(
                    PurePosixPath(remote_ref_root) / Path(t2g_src).name
                )
            logger.info(
                f"Override reference paths for {genome_name}",
                override=ref_overrides[str(genome_name)],
            )

        logger.info(
            "Final reference_overrides passed to config generator",
            reference_overrides=ref_overrides,
        )
        remote_roots = self._derive_remote_roots(params, profile)

        bind_paths: list[str] = [remote_work]
        remote_input = str(cache_resolution.get("remote_input") or "").strip()
        if remote_input:
            bind_paths.append(remote_input)
        for remote_ref_root in remote_reference_paths.values():
            cleaned = str(remote_ref_root or "").strip()
            if cleaned:
                bind_paths.append(cleaned)

        config = NextflowConfig.generate_config(
            sample_name=params.sample_name,
            mode=params.mode,
            input_dir=params.input_directory,
            reference_genome=params.reference_genome,
            reference_overrides=ref_overrides,
            modifications=params.modifications,
            modkit_filter_threshold=params.modkit_filter_threshold,
            min_cov=params.min_cov,
            per_mod=params.per_mod,
            accuracy=params.accuracy,
            max_gpu_tasks=params.max_gpu_tasks,
            execution_mode="slurm",
            slurm_cpu_partition=cpu_partition,
            slurm_gpu_partition=gpu_partition,
            slurm_cpu_account=cpu_account,
            slurm_gpu_account=gpu_account,
            slurm_bind_paths=bind_paths,
            apptainer_cache_dir=f"{remote_roots['remote_base_path']}/.nxf-apptainer-cache",
        )

        config_path = f"{remote_work}/nextflow.config"
        quoted_config_path = shlex.quote(config_path)
        profile_path = f"{remote_work}/dogme.profile"
        quoted_profile_path = shlex.quote(profile_path)
        await conn.mkdir_p(remote_work)
        await conn.run(f"cat > {quoted_config_path} << 'AGOUTIC_EOF'\n{config}\nAGOUTIC_EOF", check=True)
        await conn.run(f"cat > {quoted_profile_path} << 'AGOUTIC_EOF'\nAGOUTIC_EOF", check=True)
        return config_path

    async def _ensure_workflow_input_links(
        self,
        *,
        conn,
        params: SubmitParams,
        remote_work: str,
        remote_input: str,
    ) -> None:
        """Create workflow-local symlink for the staged input folder expected by the Dogme pipeline."""
        input_type = (params.input_type or "pod5").strip().lower()
        link_dir_name = {
            "pod5": "pod5",
            "bam": "bams",
            "fastq": "fastqs",
            "fq": "fastqs",
        }.get(input_type, "pod5")

        link_path = str(PurePosixPath(remote_work) / link_dir_name)
        await conn.mkdir_p(remote_work)

        # Dogme remap expects an `*.unmapped.bam` basename in the workflow-local bams folder.
        # Keep cache paths immutable by creating a local alias link rather than renaming staged files.
        if input_type == "bam" and (params.entry_point or "").strip().lower() == "remap":
            sample_slug = self._slugify(params.sample_name or "sample")
            alias_name = f"{sample_slug}.unmapped.bam"
            quoted_link_path = shlex.quote(link_path)
            quoted_remote_input = shlex.quote(remote_input)
            quoted_alias_name = shlex.quote(alias_name)
            await conn.run(f"rm -rf {quoted_link_path}", check=True)
            await conn.run(f"mkdir -p {quoted_link_path}", check=True)
            await conn.run(
                (
                    "set -euo pipefail; "
                    f"src={quoted_remote_input}; "
                    "if [ -d \"$src\" ]; then "
                    "  bam_src=$(find \"$src\" -maxdepth 1 -type f -name '*.bam' | sort | head -n1); "
                    "  if [ -n \"$bam_src\" ]; then "
                    f"    ln -sfn \"$bam_src\" {quoted_link_path}/{quoted_alias_name}; "
                    "  else "
                    f"    ln -sfn \"$src\" {quoted_link_path}; "
                    "  fi; "
                    "else "
                    f"  ln -sfn \"$src\" {quoted_link_path}/{quoted_alias_name}; "
                    "fi"
                ),
                check=True,
            )
            return

        await conn.run(
            f"ln -sfn {shlex.quote(remote_input)} {shlex.quote(link_path)}",
            check=True,
        )

    async def _update_job_stage(self, run_uuid: str, stage: RunStage) -> None:
        """Update the run_stage on the DogmeJob record."""
        from launchpad.db import update_job_field
        await update_job_field(run_uuid, "run_stage", stage.value)

    async def _update_job_transfer_state(
        self,
        run_uuid: str,
        state: str,
        *,
        error_message: str | None = None,
    ) -> None:
        """Update transfer state and optional transfer error detail."""
        from launchpad.db import update_job_fields

        fields = {"transfer_state": state}
        if error_message is not None:
            fields["error_message"] = error_message
        elif state in {"downloading_outputs", "outputs_downloaded", "inputs_uploaded"}:
            fields["error_message"] = None
        await update_job_fields(run_uuid, fields)

    async def _update_job_slurm_info(
        self, run_uuid: str, slurm_job_id: str, remote_work: str, remote_output: str
    ) -> None:
        """Update SLURM-specific fields on the job record."""
        from launchpad.db import update_job_fields
        await update_job_fields(run_uuid, {
            "slurm_job_id": slurm_job_id,
            "remote_work_dir": remote_work,
            "remote_output_dir": remote_output,
        })

    async def _update_job_slurm_state(
        self,
        run_uuid: str,
        raw_state: str,
        agoutic_status: str,
        *,
        error_message: str | None = None,
    ) -> None:
        """Update SLURM state and map to AGOUTIC status."""
        from launchpad.db import update_job_fields
        fields = {
            "slurm_state": raw_state,
            "status": agoutic_status,
        }
        if error_message is not None:
            fields["error_message"] = error_message
        elif agoutic_status == "COMPLETED":
            fields["error_message"] = None
        await update_job_fields(run_uuid, fields)

    async def _resolve_staging_cache(
        self,
        run_uuid: str,
        params: SubmitParams,
        profile: SSHProfileData,
        conn,
    ) -> dict:
        """Resolve per-user reusable cache paths for references and input data."""
        return await self._stage_sample_inputs(params, profile, conn, run_uuid=run_uuid)

    async def _stage_sample_inputs(
        self,
        params: SubmitParams,
        profile: SSHProfileData,
        conn,
        *,
        run_uuid: str | None,
        on_progress: Callable[[dict], None] | None = None,
        transfer_id: str | None = None,
    ) -> dict:
        """Stage or reuse remote references and sample data, optionally updating a job record."""
        from launchpad.db import (
            get_remote_reference_cache_entry,
            get_remote_input_cache_entry,
            upsert_remote_staged_sample,
            upsert_remote_reference_cache_entry,
            upsert_remote_input_cache_entry,
            update_job_fields,
        )

        user_key = self._cache_user_key(params, profile)
        remote_roots = self._derive_remote_roots(params, profile)
        ref_root = remote_roots["ref_root"]
        data_root = remote_roots["data_root"]
        primary_ref = self._normalize_reference_id((params.reference_genome or ["default"])[0])

        reference_cache_path = None
        reference_status = "skipped"
        reference_statuses: dict[str, str] = {}
        remote_reference_paths: dict[str, str] = {}

        for ref_raw in params.reference_genome or []:
            ref_id = self._normalize_reference_id(ref_raw)
            ref_source_dir = self._resolve_reference_source_dir(ref_raw)
            if not ref_source_dir:
                logger.warning(f"Reference source directory is unknown for {ref_raw}; skipping reference cache stage")
                continue

            source_signature = await self._compute_directory_signature_async(ref_source_dir)
            source_uri = str(ref_source_dir)
            target_remote_path = str(PurePosixPath(ref_root) / ref_id)

            cache_entry = await get_remote_reference_cache_entry(params.user_id or user_key, profile.id, ref_id)
            cache_path = cache_entry.remote_path if cache_entry else target_remote_path
            cache_exists = await conn.path_exists(cache_path)
            needs_refresh = (
                cache_entry is None
                or cache_entry.source_signature != source_signature
                or not cache_exists
            )

            if needs_refresh:
                if run_uuid:
                    await self._update_job_stage(run_uuid, RunStage.TRANSFERRING_INPUTS)
                    await self._update_job_transfer_state(run_uuid, "uploading_inputs")
                await conn.mkdir_p(cache_path)
                upload_kwargs = {
                    "profile": profile,
                    "local_path": str(ref_source_dir),
                    "remote_path": cache_path,
                    "on_progress": on_progress,
                }
                if transfer_id:
                    upload_kwargs["transfer_id"] = transfer_id
                result = await self._transfer_manager.upload_inputs(**upload_kwargs)
                if not result["ok"]:
                    if run_uuid:
                        await self._update_job_transfer_state(run_uuid, "transfer_failed")
                    raise RuntimeError(f"Reference cache stage failed for {ref_id}: {result['message']}")
                reference_status = "refreshed" if cache_entry else "staged"
            else:
                reference_status = "reused"

            reference_statuses[ref_id] = reference_status
            remote_reference_paths[ref_id] = cache_path

            await upsert_remote_reference_cache_entry(
                user_id=params.user_id or user_key,
                ssh_profile_id=profile.id,
                reference_id=ref_id,
                source_signature=source_signature,
                source_uri=source_uri,
                remote_path=cache_path,
                status="READY",
                increment_use_count=True,
            )

            if ref_id == primary_ref:
                reference_cache_path = cache_path

        input_fingerprint = await self._compute_input_fingerprint_async(params.input_directory)
        input_size_bytes = await self._compute_input_size_async(params.input_directory)
        data_cache_key = input_fingerprint[:16]
        target_data_remote = str(PurePosixPath(data_root) / data_cache_key)
        data_entry = await get_remote_input_cache_entry(
            params.user_id or user_key,
            profile.id,
            primary_ref,
            input_fingerprint,
        )

        data_cache_path = data_entry.remote_path if data_entry else target_data_remote
        data_exists = await conn.path_exists(data_cache_path)
        data_cache_info: dict[str, object] | None = None
        data_refresh_reason: str | None = None
        if data_exists:
            if data_entry is None:
                data_refresh_reason = "uncataloged remote cache path already exists"
            data_cache_info = await self._inspect_remote_cache_dir(conn, data_cache_path)
            if data_cache_info is None:
                data_refresh_reason = data_refresh_reason or "remote cache inspection failed"
            elif data_cache_info.get("has_symlinks"):
                data_refresh_reason = "cached input contains symlinks"
            elif data_cache_info.get("has_partial_dir"):
                data_refresh_reason = f"cached input contains {self._RSYNC_PARTIAL_DIR}"
            elif int(data_cache_info.get("size_bytes") or 0) != input_size_bytes:
                data_refresh_reason = (
                    "cached input size mismatch "
                    f"(remote={int(data_cache_info.get('size_bytes') or 0)}, expected={input_size_bytes})"
                )
        elif data_entry is not None:
            data_refresh_reason = "cached input path is missing"

        if data_entry is not None and data_exists and data_refresh_reason is None:
            data_status = "reused"
        else:
            if run_uuid:
                await self._update_job_stage(run_uuid, RunStage.TRANSFERRING_INPUTS)
                await self._update_job_transfer_state(run_uuid, "uploading_inputs")
            if data_exists:
                logger.warning(
                    "Refreshing remote input cache",
                    sample_name=params.sample_name,
                    remote_path=data_cache_path,
                    reason=data_refresh_reason or "stale remote cache path",
                    remote_size_bytes=int((data_cache_info or {}).get("size_bytes") or 0),
                    expected_size_bytes=input_size_bytes,
                )
                await conn.run(f"rm -rf {shlex.quote(data_cache_path)}", check=True)
                data_status = "refreshed"
            else:
                data_status = "staged"
            await conn.mkdir_p(data_cache_path)
            upload_kwargs = {
                "profile": profile,
                "local_path": params.input_directory,
                "remote_path": data_cache_path,
                "on_progress": on_progress,
            }
            if transfer_id:
                upload_kwargs["transfer_id"] = transfer_id
            result = await self._transfer_manager.upload_inputs(**upload_kwargs)
            if not result["ok"]:
                if run_uuid:
                    await self._update_job_transfer_state(run_uuid, "transfer_failed")
                raise RuntimeError(f"Input transfer failed: {result['message']}")

        await upsert_remote_input_cache_entry(
            user_id=params.user_id or user_key,
            ssh_profile_id=profile.id,
            reference_id=primary_ref,
            input_fingerprint=input_fingerprint,
            remote_path=data_cache_path,
            size_bytes=input_size_bytes,
            status="READY",
            increment_use_count=True,
        )
        sample_slug = self._slugify(params.sample_name or "sample")
        await upsert_remote_staged_sample(
            user_id=params.user_id or user_key,
            ssh_profile_id=profile.id,
            ssh_profile_nickname=profile.nickname,
            sample_name=params.sample_name,
            sample_slug=sample_slug,
            mode=params.mode,
            reference_genome=list(params.reference_genome or []),
            source_path=params.input_directory,
            input_fingerprint=input_fingerprint,
            remote_base_path=remote_roots["remote_base_path"],
            remote_data_path=data_cache_path,
            remote_reference_paths=remote_reference_paths,
            status="READY",
            mark_used=True,
        )

        if run_uuid:
            await self._update_job_transfer_state(run_uuid, "inputs_uploaded")
            await update_job_fields(
                run_uuid,
                {
                    "reference_cache_status": reference_status,
                    "data_cache_status": data_status,
                    "reference_cache_path": reference_cache_path,
                    "data_cache_path": data_cache_path,
                },
            )

        logger.info(
            "Stage_sample_inputs completed",
            reference_statuses=reference_statuses,
            remote_reference_paths=remote_reference_paths,
        )
        
        return {
            "reference_cache_path": reference_cache_path,
            "data_cache_path": data_cache_path,
            "remote_input": data_cache_path,
            "reference_cache_status": reference_status,
            "data_cache_status": data_status,
            "reference_cache_statuses": reference_statuses,
            "remote_reference_paths": remote_reference_paths,
        }

    @staticmethod
    async def _remote_dir_contains_symlinks(conn, path: str) -> bool:
        """Return True when a staged cache directory contains symlink entries."""
        try:
            entries = await conn.list_dir(path)
        except Exception as exc:
            logger.warning("Failed to inspect remote cache directory for symlinks", path=path, error=str(exc))
            return False
        return any((entry.get("type") or "") == "symlink" for entry in entries)

    async def _inspect_remote_cache_dir(self, conn, path: str) -> dict[str, object] | None:
        """Collect recursive cache integrity evidence for a staged input directory."""
        script = (
            "python3 - <<'PY'\n"
            "import json, os\n"
            f"path = {path!r}\n"
            "summary = {'size_bytes': 0, 'has_symlinks': False, 'has_partial_dir': False}\n"
            "for root, dirs, files in os.walk(path, followlinks=False):\n"
            f"    if {self._RSYNC_PARTIAL_DIR!r} in dirs:\n"
            "        summary['has_partial_dir'] = True\n"
            "    for name in list(dirs) + list(files):\n"
            "        candidate = os.path.join(root, name)\n"
            "        if os.path.islink(candidate):\n"
            "            summary['has_symlinks'] = True\n"
            "    for name in files:\n"
            "        candidate = os.path.join(root, name)\n"
            "        if os.path.islink(candidate):\n"
            "            continue\n"
            "        try:\n"
            "            summary['size_bytes'] += int(os.stat(candidate, follow_symlinks=False).st_size)\n"
            "        except OSError:\n"
            "            pass\n"
            "print(json.dumps(summary))\n"
            "PY"
        )
        try:
            result = await conn.run(script, check=True)
        except Exception as exc:
            logger.warning("Failed to inspect remote cache directory", path=path, error=str(exc))
            return None
        try:
            payload = json.loads((result.stdout or "").strip() or "{}")
        except json.JSONDecodeError as exc:
            logger.warning("Remote cache inspection returned invalid JSON", path=path, error=str(exc))
            return None
        return {
            "size_bytes": int(payload.get("size_bytes") or 0),
            "has_symlinks": bool(payload.get("has_symlinks")),
            "has_partial_dir": bool(payload.get("has_partial_dir")),
        }

    @staticmethod
    def _reference_paths_from_cache_preflight(params: SubmitParams) -> dict[str, str]:
        cache_preflight = params.cache_preflight if isinstance(params.cache_preflight, dict) else {}
        reference_actions = cache_preflight.get("reference_actions") if isinstance(cache_preflight, dict) else []
        reference_paths: dict[str, str] = {}
        for action in reference_actions or []:
            if not isinstance(action, dict):
                continue
            ref_id = (action.get("reference_id") or "").strip().lower()
            cache_path = str(action.get("cache_path") or "").strip()
            if ref_id and cache_path:
                reference_paths[ref_id] = cache_path
        return reference_paths

    async def _collect_reference_asset_evidence(
        self,
        params: SubmitParams,
        conn,
        remote_reference_paths: dict[str, str],
    ) -> dict[str, dict[str, object]]:
        """Collect remote file evidence for staged reference directories."""
        evidence: dict[str, dict[str, object]] = {}
        seen_refs: set[str] = set()

        for ref_raw in params.reference_genome or []:
            ref_id = self._normalize_reference_id(ref_raw)
            if ref_id in seen_refs:
                continue
            seen_refs.add(ref_id)

            ref_cfg = self._resolve_reference_config(ref_raw)
            remote_ref_path = remote_reference_paths.get(ref_id)
            requires_kallisto = (params.mode or "").strip().upper() in {"RNA", "CDNA"}

            required_assets: dict[str, str] = {}
            optional_assets: dict[str, str] = {}
            for asset_name in ("fasta", "gtf"):
                asset_path = ref_cfg.get(asset_name)
                if asset_path:
                    required_assets[asset_name] = Path(asset_path).name
            for asset_name in ("kallisto_index", "kallisto_t2g"):
                asset_path = ref_cfg.get(asset_name)
                if not asset_path:
                    continue
                asset_filename = Path(asset_path).name
                if requires_kallisto:
                    required_assets[asset_name] = asset_filename
                else:
                    optional_assets[asset_name] = asset_filename

            listed_files: list[str] = []
            listing_error: str | None = None
            if remote_ref_path:
                try:
                    entries = await conn.list_dir(remote_ref_path)
                    listed_files = sorted(
                        entry["name"]
                        for entry in entries
                        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
                    )
                except Exception as exc:
                    listing_error = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
            else:
                listing_error = "Remote reference path unavailable"

            listed_file_set = set(listed_files)
            present_assets = {
                asset_name: str(PurePosixPath(remote_ref_path) / filename)
                for asset_name, filename in {**required_assets, **optional_assets}.items()
                if remote_ref_path and filename in listed_file_set
            }
            missing_required_assets = [
                asset_name
                for asset_name, filename in required_assets.items()
                if filename not in listed_file_set
            ]

            evidence[ref_id] = {
                "mode": (params.mode or "").strip().upper(),
                "remote_path": remote_ref_path,
                "requires_kallisto": requires_kallisto,
                "required_assets": required_assets,
                "optional_assets": optional_assets,
                "present_assets": present_assets,
                "missing_required_assets": missing_required_assets,
                "all_required_present": not missing_required_assets and listing_error is None,
                "listed_files": listed_files,
                "listing_error": listing_error,
            }

        return evidence

    async def _ensure_reference_assets_present(
        self,
        *,
        params: SubmitParams,
        profile: SSHProfileData,
        conn,
        remote_reference_paths: dict[str, str],
        reference_statuses: dict[str, str] | None = None,
        run_uuid: str | None = None,
    ) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
        from launchpad.db import upsert_remote_reference_cache_entry

        remote_reference_paths = dict(remote_reference_paths or {})
        reference_statuses = dict(reference_statuses or {})
        evidence = await self._collect_reference_asset_evidence(params, conn, remote_reference_paths)
        missing_by_ref = {
            ref_id: list((ref_evidence or {}).get("missing_required_assets") or [])
            for ref_id, ref_evidence in evidence.items()
            if (ref_evidence or {}).get("missing_required_assets")
        }
        if not missing_by_ref:
            return evidence, reference_statuses

        logger.warning(
            "Remote reference verification found missing required assets; forcing refresh",
            sample_name=params.sample_name,
            missing_by_ref=missing_by_ref,
        )

        remote_roots = self._derive_remote_roots(params, profile)
        for ref_id in missing_by_ref:
            ref_source_dir = self._resolve_reference_source_dir(ref_id)
            if not ref_source_dir:
                raise RuntimeError(f"Reference source directory is unavailable for {ref_id}")

            remote_ref_path = remote_reference_paths.get(ref_id) or str(PurePosixPath(remote_roots["ref_root"]) / ref_id)
            remote_reference_paths[ref_id] = remote_ref_path

            if run_uuid:
                await self._update_job_stage(run_uuid, RunStage.TRANSFERRING_INPUTS)
                await self._update_job_transfer_state(run_uuid, "uploading_inputs")

            await conn.mkdir_p(remote_ref_path)
            result = await self._transfer_manager.upload_inputs(
                profile=profile,
                local_path=str(ref_source_dir),
                remote_path=remote_ref_path,
            )
            if not result["ok"]:
                if run_uuid:
                    await self._update_job_transfer_state(run_uuid, "transfer_failed")
                raise RuntimeError(f"Reference cache repair failed for {ref_id}: {result['message']}")

            source_signature = await self._compute_directory_signature_async(ref_source_dir)
            await upsert_remote_reference_cache_entry(
                user_id=params.user_id or self._cache_user_key(params, profile),
                ssh_profile_id=profile.id,
                reference_id=ref_id,
                source_signature=source_signature,
                source_uri=str(ref_source_dir),
                remote_path=remote_ref_path,
                status="READY",
                increment_use_count=True,
            )
            reference_statuses[ref_id] = "refreshed"

        if run_uuid:
            await self._update_job_transfer_state(run_uuid, "inputs_uploaded")

        evidence = await self._collect_reference_asset_evidence(params, conn, remote_reference_paths)
        remaining_missing = {
            ref_id: list((ref_evidence or {}).get("missing_required_assets") or [])
            for ref_id, ref_evidence in evidence.items()
            if (ref_evidence or {}).get("missing_required_assets")
        }
        if remaining_missing:
            details = "; ".join(
                f"{ref_id}: {', '.join(missing_assets)}"
                for ref_id, missing_assets in remaining_missing.items()
            )
            raise RuntimeError(f"Remote reference cache verification failed after refresh: {details}")

        return evidence, reference_statuses

    async def _reuse_pre_staged_input(
        self,
        run_uuid: str | None,
        params: SubmitParams,
        *,
        profile: SSHProfileData,
        conn,
    ) -> dict:
        """Use a previously staged remote data path without restaging local inputs."""
        from launchpad.db import update_job_fields, upsert_remote_staged_sample

        remote_input = (params.staged_remote_input_path or "").strip()
        if not remote_input:
            raise ValueError("staged_remote_input_path is required when reusing a staged remote sample")

        if not await conn.path_exists(remote_input):
            raise FileNotFoundError(f"Pre-staged remote input path no longer exists: {remote_input}")

        user_remote_input = self._is_user_remote_input(params)
        if await self._remote_dir_contains_symlinks(conn, remote_input):
            if user_remote_input:
                logger.info(
                    "User-specified remote input path contains symlinks; leaving them in place",
                    remote_input=remote_input,
                )
            else:
                await conn.run(f"rm -rf {shlex.quote(remote_input)}", check=True)
                raise RuntimeError(f"Pre-staged remote input cache contained symlinks and was removed: {remote_input}")

        fallback_input_type = (params.input_type or "pod5").strip().lower() or "pod5"
        detected_input_type = await self._detect_remote_input_type(conn, remote_input, fallback=fallback_input_type)

        if run_uuid:
            await update_job_fields(
                run_uuid,
                {
                    "reference_cache_status": "reused",
                    "data_cache_status": "reused",
                    "data_cache_path": remote_input,
                    "reference_cache_path": params.reference_cache_path,
                },
            )

        remote_reference_paths = self._reference_paths_from_cache_preflight(params)
        if not remote_reference_paths and params.reference_cache_path and params.reference_genome:
            first_ref = self._normalize_reference_id((params.reference_genome or ["default"])[0])
            remote_reference_paths[first_ref] = params.reference_cache_path

        if user_remote_input:
            remote_roots = self._derive_remote_roots(params, profile)
            sample_slug = self._slugify(params.sample_name or "sample")
            await upsert_remote_staged_sample(
                user_id=params.user_id or self._cache_user_key(params, profile),
                ssh_profile_id=profile.id,
                ssh_profile_nickname=profile.nickname,
                sample_name=params.sample_name,
                sample_slug=sample_slug,
                mode=params.mode,
                reference_genome=list(params.reference_genome or []),
                source_path=params.remote_input_path or remote_input,
                input_fingerprint=self._compute_remote_input_fingerprint(remote_input),
                remote_base_path=remote_roots["remote_base_path"],
                remote_data_path=remote_input,
                remote_reference_paths=remote_reference_paths,
                status="READY",
                mark_used=True,
            )

        return {
            "reference_cache_path": params.reference_cache_path,
            "data_cache_path": remote_input,
            "remote_input": remote_input,
            "reference_cache_status": "reused",
            "data_cache_status": "reused",
            "reference_cache_statuses": {},
            "remote_reference_paths": remote_reference_paths,
            "detected_input_type": detected_input_type,
        }

    async def _fallback_stage_inputs(
        self,
        run_uuid: str,
        params: SubmitParams,
        profile: SSHProfileData,
        conn,
    ) -> dict:
        """Fallback path when cache metadata/services are unavailable."""
        from launchpad.db import update_job_fields

        remote_paths = self._derive_remote_paths(params, profile)
        ref_root = remote_paths["ref_root"]
        input_fingerprint = await self._compute_input_fingerprint_async(params.input_directory)
        remote_input = str(PurePosixPath(remote_paths["data_root"]) / input_fingerprint[:16])

        reference_status = "fallback"
        reference_cache_path = None
        first_ref = (params.reference_genome or ["default"])[0]
        first_ref_id = self._normalize_reference_id(first_ref)
        ref_source_dir = self._resolve_reference_source_dir(first_ref)
        if ref_source_dir is not None:
            reference_cache_path = str(PurePosixPath(ref_root) / first_ref_id)
            await conn.mkdir_p(reference_cache_path)
            await self._update_job_stage(run_uuid, RunStage.TRANSFERRING_INPUTS)
            await self._update_job_transfer_state(run_uuid, "uploading_inputs")
            ref_result = await self._transfer_manager.upload_inputs(
                profile=profile,
                local_path=str(ref_source_dir),
                remote_path=reference_cache_path,
            )
            if not ref_result["ok"]:
                raise RuntimeError(f"Fallback reference stage failed: {ref_result['message']}")

        await conn.mkdir_p(remote_input)
        await self._update_job_stage(run_uuid, RunStage.TRANSFERRING_INPUTS)
        await self._update_job_transfer_state(run_uuid, "uploading_inputs")
        result = await self._transfer_manager.upload_inputs(
            profile=profile,
            local_path=params.input_directory,
            remote_path=remote_input,
        )
        if not result["ok"]:
            await self._update_job_transfer_state(run_uuid, "transfer_failed")
            raise RuntimeError(f"Fallback input stage failed: {result['message']}")

        await self._update_job_transfer_state(run_uuid, "inputs_uploaded")
        await update_job_fields(
            run_uuid,
            {
                "reference_cache_status": reference_status,
                "data_cache_status": "fallback",
                "reference_cache_path": reference_cache_path,
                "data_cache_path": remote_input,
            },
        )

        # Build remote_reference_paths for all genomes (fallback case)
        remote_reference_paths: dict[str, str] = {}
        for ref_raw in params.reference_genome or []:
            ref_id = self._normalize_reference_id(ref_raw)
            remote_reference_paths[ref_id] = str(PurePosixPath(ref_root) / ref_id)

        return {
            "reference_cache_path": reference_cache_path,
            "data_cache_path": remote_input,
            "remote_input": remote_input,
            "reference_cache_status": reference_status,
            "data_cache_status": "fallback",
            "remote_reference_paths": remote_reference_paths,
        }

    @staticmethod
    def _cache_user_key(params: SubmitParams, profile: SSHProfileData) -> str:
        return (params.user_id or params.username or profile.ssh_username or "user").strip()

    @staticmethod
    def _normalize_reference_id(reference_id: str) -> str:
        return (reference_id or "default").strip().lower()

    @staticmethod
    def _derive_remote_roots(params: SubmitParams, profile: SSHProfileData) -> dict[str, str]:
        remote_base_path = (params.remote_base_path or profile.remote_base_path or "").strip()
        if not remote_base_path:
            raise ValueError("SLURM execution requires remote_base_path on the request or SSH profile")

        base_path = PurePosixPath(remote_base_path)
        return {
            "remote_base_path": str(base_path),
            "ref_root": str(base_path / "ref"),
            "data_root": str(base_path / "data"),
        }

    @staticmethod
    def _resolve_remote_browse_path(profile: SSHProfileData, path: str | None) -> str:
        requested = (path or "").strip()
        base = (profile.remote_base_path or "").strip()
        if not requested:
            return base
        requested_path = PurePosixPath(requested)
        if requested_path.is_absolute():
            return str(requested_path)
        if any(part == ".." for part in requested_path.parts):
            raise ValueError("Relative remote browse paths cannot contain '..'")
        if not base:
            return str(requested_path)
        return str(PurePosixPath(base) / requested_path)

    @classmethod
    def _derive_remote_paths(cls, params: SubmitParams, profile: SSHProfileData) -> dict[str, str]:
        remote_roots = cls._derive_remote_roots(params, profile)

        workflow_number = params.workflow_number
        if workflow_number is None or workflow_number < 1:
            raise ValueError("SLURM execution requires a positive workflow_number")

        project_slug = (params.project_slug or params.project_id or "project").strip()
        base_path = PurePosixPath(remote_roots["remote_base_path"])
        project_root = base_path / project_slug
        remote_work = project_root / f"workflow{workflow_number}"
        remote_output = remote_work / "output"
        return {
            **remote_roots,
            "project_root": str(project_root),
            "remote_work": str(remote_work),
            "remote_output": str(remote_output),
        }

    @staticmethod
    def _slugify(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in (value or ""))
        while "--" in cleaned:
            cleaned = cleaned.replace("--", "-")
        return cleaned.strip("-") or "sample"

    @staticmethod
    def _resolve_reference_config(reference_id: str) -> dict:
        if not reference_id:
            return {}

        requested = reference_id.strip()
        ref_cfg = REFERENCE_GENOMES.get(requested)
        if ref_cfg is None:
            lower_map = {k.lower(): k for k in REFERENCE_GENOMES.keys()}
            mapped_key = lower_map.get(requested.lower())
            ref_cfg = REFERENCE_GENOMES.get(mapped_key) if mapped_key else None
        return ref_cfg if isinstance(ref_cfg, dict) else {}

    @staticmethod
    def _resolve_reference_source_dir(reference_id: str) -> Path | None:
        ref_cfg = SlurmBackend._resolve_reference_config(reference_id)
        if not ref_cfg:
            return None

        fasta_path = ref_cfg.get("fasta")
        if not fasta_path:
            return None
        return Path(fasta_path).parent

    @staticmethod
    def _compute_directory_signature(directory: Path) -> str:
        if not directory.exists() or not directory.is_dir():
            return "missing"

        hasher = hashlib.sha256()
        for root, _, files in os.walk(directory):
            root_path = Path(root)
            for filename in sorted(files):
                file_path = root_path / filename
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                rel_path = str(file_path.relative_to(directory))
                hasher.update(rel_path.encode("utf-8"))
                hasher.update(str(stat.st_size).encode("utf-8"))
                hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
        return hasher.hexdigest()

    @staticmethod
    async def _compute_directory_signature_async(directory: Path) -> str:
        """Run directory signature scans off the main event loop."""
        return await asyncio.to_thread(SlurmBackend._compute_directory_signature, directory)

    @staticmethod
    def _compute_input_fingerprint(local_path: str) -> str:
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

    @staticmethod
    def _compute_input_size(local_path: str) -> int:
        path = Path(local_path)
        if not path.exists():
            return 0

        if path.is_file():
            try:
                return int(path.stat().st_size)
            except OSError:
                return 0

        total_size = 0
        for root, _, files in os.walk(path):
            root_path = Path(root)
            for filename in files:
                file_path = root_path / filename
                try:
                    total_size += int(file_path.stat().st_size)
                except OSError:
                    continue
        return total_size

    @staticmethod
    async def _compute_input_size_async(local_path: str) -> int:
        """Run input size scans off the main event loop."""
        return await asyncio.to_thread(SlurmBackend._compute_input_size, local_path)

    @staticmethod
    async def _compute_input_fingerprint_async(local_path: str) -> str:
        """Run input fingerprint scans off the main event loop."""
        return await asyncio.to_thread(SlurmBackend._compute_input_fingerprint, local_path)
