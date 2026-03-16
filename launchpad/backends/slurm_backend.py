"""
SLURM execution backend — SSH + sbatch for remote HPC execution.
"""
from __future__ import annotations

import json
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

logger = get_logger(__name__)


class SlurmBackend:
    """Remote execution via SSH + SLURM sbatch."""

    def __init__(self):
        self._ssh_manager = SSHConnectionManager()
        self._transfer_manager = FileTransferManager()

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
        profile = await self._load_profile(params.ssh_profile_id)

        # 3. Connect and validate
        await self._update_job_stage(run_uuid, RunStage.VALIDATING_CONNECTION)
        conn = await self._ssh_manager.connect(profile)

        try:
            # 4. Validate/create remote paths
            await self._update_job_stage(run_uuid, RunStage.PREPARING_REMOTE_DIRS)
            paths_to_validate = {}
            if params.remote_work_path:
                paths_to_validate["work"] = params.remote_work_path
            if params.remote_output_path:
                paths_to_validate["output"] = params.remote_output_path

            if paths_to_validate:
                results = await validate_remote_paths(conn, paths_to_validate)
                ok, path_errors = check_all_paths_ok(results)
                if not ok:
                    raise ValueError(f"Remote path validation failed: {'; '.join(path_errors)}")

            # 5. Transfer inputs if needed
            if params.remote_input_path and params.input_directory:
                await self._update_job_stage(run_uuid, RunStage.TRANSFERRING_INPUTS)
                await self._update_job_transfer_state(run_uuid, "uploading_inputs")

                result = await self._transfer_manager.upload_inputs(
                    profile=profile,
                    local_path=params.input_directory,
                    remote_path=params.remote_input_path,
                )
                if not result["ok"]:
                    await self._update_job_transfer_state(run_uuid, "transfer_failed")
                    raise RuntimeError(f"Input transfer failed: {result['message']}")

                await self._update_job_transfer_state(run_uuid, "inputs_uploaded")

            # 6. Generate and submit sbatch script
            await self._update_job_stage(run_uuid, RunStage.SUBMITTING_JOB)

            remote_work = params.remote_work_path or f"/scratch/{profile.ssh_username}/agoutic/{run_uuid}"
            remote_output = params.remote_output_path or f"{remote_work}/output"
            remote_input = params.remote_input_path or params.input_directory

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
            await conn.run(f"cat > {script_path} << 'AGOUTIC_EOF'\n{script}AGOUTIC_EOF", check=True)
            await conn.run(f"chmod +x {script_path}", check=True)

            # Submit via sbatch
            result = await conn.run_checked(f"sbatch --parsable {script_path}")
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
        profile = await self._load_profile(job.ssh_profile_id)
        try:
            conn = await self._ssh_manager.connect(profile)
            try:
                # Try sacct first (works for completed/failed jobs too)
                result = await conn.run(
                    f"sacct -j {job.slurm_job_id} --format=State,ExitCode,Reason --noheader --parsable2 | head -1"
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
                        f"squeue -j {job.slurm_job_id} --format=%T --noheader"
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

        profile = await self._load_profile(job.ssh_profile_id)
        try:
            conn = await self._ssh_manager.connect(profile)
            try:
                await conn.run_checked(f"scancel {job.slurm_job_id}")
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

        profile = await self._load_profile(job.ssh_profile_id)
        logs: list[LogEntry] = []

        try:
            conn = await self._ssh_manager.connect(profile)
            try:
                work_dir = job.remote_work_dir or ""
                # Read stdout log
                for suffix, level in [("out", "INFO"), ("err", "ERROR")]:
                    log_path = f"{work_dir}/slurm-{job.slurm_job_id}.{suffix}"
                    result = await conn.run(f"tail -n {limit} {log_path} 2>/dev/null || true")
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

        profile = await self._load_profile(job.ssh_profile_id)
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

    # --- Internal helpers ---

    async def _load_profile(self, profile_id: str | None) -> SSHProfileData:
        """Load an SSH profile from the database."""
        if not profile_id:
            raise ValueError("ssh_profile_id is required for SLURM execution")

        from launchpad.db import get_ssh_profile
        profile = await get_ssh_profile(profile_id)
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
            f"--sample_name {params.sample_name!r}",
            f"--mode {params.mode}",
            f"--input {remote_input!r}",
            f"--outdir {remote_output!r}",
            f"--reference_genome {genome_list!r}",
        ]
        if params.modifications:
            cmd_parts.append(f"--modifications {params.modifications!r}")
        if params.entry_point:
            cmd_parts.append(f"-entry {params.entry_point}")
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
