"""
SLURM execution backend — SSH + sbatch for remote HPC execution.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import uuid
from datetime import datetime, timezone

from common.logging_config import get_logger
from launchpad.backends.base import ExecutionBackend, SubmitParams, JobStatus, LogEntry
from launchpad.backends.ssh_manager import SSHConnectionManager, SSHProfileData
from launchpad.backends.sbatch_generator import generate_sbatch_script
from launchpad.backends.slurm_states import map_slurm_state, explain_pending_reason
from launchpad.backends.stage_machine import RunStage
from launchpad.backends.resource_validator import validate_resources
from launchpad.backends.path_validator import validate_remote_paths, check_all_paths_ok
from launchpad.backends.file_transfer import FileTransferManager
from launchpad.config import REFERENCE_GENOMES

logger = get_logger(__name__)


class SlurmBackend:
    """Remote execution via SSH + SLURM sbatch."""

    def __init__(self):
        self._ssh_manager = SSHConnectionManager()
        self._transfer_manager = FileTransferManager()

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

            stage_result = await self._stage_sample_inputs(params, profile, conn, run_uuid=None)
            return {
                "sample_name": params.sample_name,
                "ssh_profile_id": profile.id,
                "ssh_profile_nickname": profile.nickname,
                "remote_base_path": remote_roots["remote_base_path"],
                "remote_data_path": stage_result["remote_input"],
                "remote_reference_paths": stage_result["remote_reference_paths"],
                "data_cache_status": stage_result["data_cache_status"],
                "reference_cache_statuses": stage_result["reference_cache_statuses"],
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
                    cache_resolution = await self._reuse_pre_staged_input(run_uuid, params)
                else:
                    cache_resolution = await self._resolve_staging_cache(run_uuid, params, profile, conn)
            except Exception as cache_error:
                logger.warning(
                    "Cache resolution degraded; falling back to direct staging",
                    run_uuid=run_uuid,
                    error=str(cache_error),
                )
                cache_resolution = await self._fallback_stage_inputs(run_uuid, params, profile, conn)

            # 6. Generate and submit sbatch script
            await self._update_job_stage(run_uuid, RunStage.SUBMITTING_JOB)

            remote_work = remote_paths["remote_work"]
            remote_output = remote_paths["remote_output"]
            remote_input = cache_resolution["remote_input"]

            # Build the nextflow command for the batch script
            nf_cmd = self._build_nextflow_command(params, remote_input, remote_output)

            script = generate_sbatch_script(
                job_name=f"agoutic-{params.sample_name}-{run_uuid[:8]}",
                account=params.slurm_account or "default",
                partition=params.slurm_partition or "standard",
                cpus=params.slurm_cpus or 4,
                memory_gb=params.slurm_memory_gb or 16,
                walltime=params.slurm_walltime or "04:00:00",
                gpus=params.slurm_gpus or 0,
                gpu_type=params.slurm_gpu_type,
                output_log=f"{remote_work}/slurm-%j.out",
                error_log=f"{remote_work}/slurm-%j.err",
                work_dir=remote_work,
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

                if raw_state == "PENDING" and reason:
                    message += f" — {explain_pending_reason(reason)}"

                # Update DB with latest state
                await self._update_job_slurm_state(run_uuid, raw_state, agoutic_status)

                return JobStatus(
                    run_uuid=run_uuid,
                    status=agoutic_status,
                    execution_mode="slurm",
                    run_stage=job.run_stage,
                    slurm_job_id=job.slurm_job_id,
                    slurm_state=raw_state,
                    transfer_state=job.transfer_state,
                    result_destination=job.result_destination,
                    message=message,
                    ssh_profile_nickname=profile.nickname,
                )
            finally:
                await conn.close()

        except Exception as e:
            logger.warning(f"Failed to poll SLURM status for {run_uuid}: {e}")
            return JobStatus(
                run_uuid=run_uuid,
                status=job.status,
                execution_mode="slurm",
                run_stage=job.run_stage,
                slurm_job_id=job.slurm_job_id,
                slurm_state=job.slurm_state,
                message=f"Failed to poll scheduler: {e}",
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
        self, params: SubmitParams, remote_input: str, remote_output: str
    ) -> str:
        """Build the Nextflow command line for the sbatch script."""
        genome_list = ",".join(params.reference_genome)
        cmd_parts = [
            "nextflow run main.nf",
            f"--sample_name {shlex.quote(params.sample_name)}",
            f"--mode {shlex.quote(params.mode)}",
            f"--input {shlex.quote(remote_input)}",
            f"--outdir {shlex.quote(remote_output)}",
            f"--reference_genome {shlex.quote(genome_list)}",
        ]
        if params.modifications:
            cmd_parts.append(f"--modifications {shlex.quote(params.modifications)}")
        if params.entry_point:
            cmd_parts.append(f"-entry {shlex.quote(params.entry_point)}")
        return " \\\n    ".join(cmd_parts)

    async def _update_job_stage(self, run_uuid: str, stage: RunStage) -> None:
        """Update the run_stage on the DogmeJob record."""
        from launchpad.db import update_job_field
        await update_job_field(run_uuid, "run_stage", stage.value)

    async def _update_job_transfer_state(self, run_uuid: str, state: str) -> None:
        """Update the transfer_state on the DogmeJob record."""
        from launchpad.db import update_job_field
        await update_job_field(run_uuid, "transfer_state", state)

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
        self, run_uuid: str, raw_state: str, agoutic_status: str
    ) -> None:
        """Update SLURM state and map to AGOUTIC status."""
        from launchpad.db import update_job_fields
        await update_job_fields(run_uuid, {
            "slurm_state": raw_state,
            "status": agoutic_status,
        })

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

            source_signature = self._compute_directory_signature(ref_source_dir)
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
                result = await self._transfer_manager.upload_inputs(
                    profile=profile,
                    local_path=str(ref_source_dir),
                    remote_path=cache_path,
                )
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

        input_fingerprint = self._compute_input_fingerprint(params.input_directory)
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

        if data_entry is not None and data_exists:
            data_status = "reused"
        else:
            if run_uuid:
                await self._update_job_stage(run_uuid, RunStage.TRANSFERRING_INPUTS)
                await self._update_job_transfer_state(run_uuid, "uploading_inputs")
            await conn.mkdir_p(data_cache_path)
            result = await self._transfer_manager.upload_inputs(
                profile=profile,
                local_path=params.input_directory,
                remote_path=data_cache_path,
            )
            if not result["ok"]:
                if run_uuid:
                    await self._update_job_transfer_state(run_uuid, "transfer_failed")
                raise RuntimeError(f"Input transfer failed: {result['message']}")
            data_status = "staged"

        await upsert_remote_input_cache_entry(
            user_id=params.user_id or user_key,
            ssh_profile_id=profile.id,
            reference_id=primary_ref,
            input_fingerprint=input_fingerprint,
            remote_path=data_cache_path,
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

        return {
            "reference_cache_path": reference_cache_path,
            "data_cache_path": data_cache_path,
            "remote_input": data_cache_path,
            "reference_cache_status": reference_status,
            "data_cache_status": data_status,
            "reference_cache_statuses": reference_statuses,
            "remote_reference_paths": remote_reference_paths,
        }

    async def _reuse_pre_staged_input(self, run_uuid: str, params: SubmitParams) -> dict:
        """Use a previously staged remote data path without restaging local inputs."""
        from launchpad.db import update_job_fields

        remote_input = (params.staged_remote_input_path or "").strip()
        if not remote_input:
            raise ValueError("staged_remote_input_path is required when reusing a staged remote sample")

        await update_job_fields(
            run_uuid,
            {
                "reference_cache_status": "reused",
                "data_cache_status": "reused",
                "data_cache_path": remote_input,
                "reference_cache_path": params.reference_cache_path,
            },
        )
        return {
            "reference_cache_path": params.reference_cache_path,
            "data_cache_path": remote_input,
            "remote_input": remote_input,
            "reference_cache_status": "reused",
            "data_cache_status": "reused",
            "reference_cache_statuses": {},
            "remote_reference_paths": {},
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
        remote_input = str(PurePosixPath(remote_paths["data_root"]) / self._compute_input_fingerprint(params.input_directory)[:16])

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

        return {
            "reference_cache_path": reference_cache_path,
            "data_cache_path": remote_input,
            "remote_input": remote_input,
            "reference_cache_status": reference_status,
            "data_cache_status": "fallback",
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
    def _resolve_reference_source_dir(reference_id: str) -> Path | None:
        if not reference_id:
            return None

        requested = reference_id.strip()
        ref_cfg = REFERENCE_GENOMES.get(requested)
        if ref_cfg is None:
            lower_map = {k.lower(): k for k in REFERENCE_GENOMES.keys()}
            mapped_key = lower_map.get(requested.lower())
            ref_cfg = REFERENCE_GENOMES.get(mapped_key) if mapped_key else None
        if not isinstance(ref_cfg, dict):
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
