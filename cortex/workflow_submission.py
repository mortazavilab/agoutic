import asyncio
import json
import os
import shlex
import uuid
from pathlib import Path

from sqlalchemy import select

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.config import AGOUTIC_DATA, default_haplotype_vcf_for_reference, get_service_url
from cortex.db import SessionLocal
from cortex.db_helpers import _create_block_internal
from cortex.job_parameters import normalize_dogme_batch_params
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
from launchpad.config import MAX_CONCURRENT_JOBS, resolve_dogme_accuracy

REMOTE_STAGE_MCP_TIMEOUT = float(os.getenv("LAUNCHPAD_STAGE_TIMEOUT", "3600"))
SCRIPT_SUBMISSION_TIMEOUT_BUFFER_SECONDS = 30.0

logger = get_logger(__name__)


def _record_batch_sample_submission(
    session,
    *,
    batch_parent_gate_id: str | None,
    batch_sample_id: str | None,
    run_uuid: str | None,
    execution_block_id: str | None,
    status: str,
    error: str | None = None,
) -> None:
    if not batch_parent_gate_id or not batch_sample_id:
        return

    batch_gate = session.query(ProjectBlock).filter(ProjectBlock.id == batch_parent_gate_id).first()
    if batch_gate is None:
        return

    payload = get_block_payload(batch_gate)
    samples = payload.get("batch_samples")
    if not isinstance(samples, list):
        return

    for sample in samples:
        if not isinstance(sample, dict) or str(sample.get("sample_id")) != str(batch_sample_id):
            continue
        sample["status"] = status
        if run_uuid:
            sample["run_uuid"] = run_uuid
        if execution_block_id:
            sample["execution_block_id"] = execution_block_id
        if error:
            sample["error"] = error
        break

    payload["batch_samples"] = samples
    payload["batch_status"] = "RUNNING"
    batch_gate.payload_json = json.dumps(payload)
    session.commit()


async def submit_dogme_batch_after_approval(project_id: str, gate_block_id: str) -> None:
    """Submit approved DOGME batch entries through the single-job pipeline."""
    session = SessionLocal()
    try:
        batch_gate = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if batch_gate is None:
            logger.error("Batch gate block not found", gate_block_id=gate_block_id)
            return

        gate_payload = get_block_payload(batch_gate)
        raw_params = gate_payload.get("edited_params") or gate_payload.get("extracted_params")
        normalized, errors = normalize_dogme_batch_params(raw_params)
        if errors:
            gate_payload["batch_validation_errors"] = errors
            gate_payload["batch_status"] = "FOLLOW_UP"
            batch_gate.payload_json = json.dumps(gate_payload)
            session.commit()
            logger.warning("Batch submission blocked by validation", gate_block_id=gate_block_id, errors=errors)
            return

        batch_id = normalized["batch_id"] or gate_block_id
        shared_params = normalized["shared_params"]
        batch_samples = normalized["batch_samples"]
        requested_max_parallel = normalized["requested_max_parallel"] or MAX_CONCURRENT_JOBS
        gate_payload.update({
            "batch_id": batch_id,
            "batch_samples": batch_samples,
            "shared_params": shared_params,
            "requested_max_parallel": requested_max_parallel,
            "batch_status": "RUNNING",
        })

        child_gate_ids: list[str] = []
        for sample in batch_samples:
            if sample.get("run_uuid") or sample.get("status") not in {"PENDING", "VALIDATING", "FAILED"}:
                continue
            sample_params = {
                **shared_params,
                "sample_name": sample["sample_name"],
                "input_directory": sample["input_directory"],
                "batch_id": batch_id,
                "batch_sample_id": sample["sample_id"],
                "batch_parent_gate_id": batch_gate.id,
            }
            child_block = _create_block_internal(
                session,
                project_id,
                "BATCH_JOB_SUBMISSION",
                {
                    "gate_action": "job",
                    "extracted_params": sample_params,
                    "batch_id": batch_id,
                    "batch_sample_id": sample["sample_id"],
                    "batch_parent_gate_id": batch_gate.id,
                    "model": gate_payload.get("model", "default"),
                },
                status="APPROVED",
                owner_id=batch_gate.owner_id,
            )
            sample["status"] = "SUBMITTING"
            sample["submission_block_id"] = child_block.id
            child_gate_ids.append(child_block.id)

        gate_payload["batch_samples"] = batch_samples
        batch_gate.payload_json = json.dumps(gate_payload)
        session.commit()

        semaphore = asyncio.Semaphore(requested_max_parallel)

        async def submit_one(child_gate_id: str) -> None:
            async with semaphore:
                await submit_job_after_approval(project_id, child_gate_id)

        await asyncio.gather(*(submit_one(child_gate_id) for child_gate_id in child_gate_ids))
    finally:
        session.close()


async def cancel_dogme_batch(project_id: str, gate_block_id: str) -> dict:
    """Cancel a DOGME batch while preserving completed sample outputs."""
    session = SessionLocal()
    try:
        batch_gate = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if batch_gate is None or batch_gate.project_id != project_id:
            raise ValueError("DOGME batch was not found.")

        payload = get_block_payload(batch_gate)
        batch_samples = payload.get("batch_samples")
        if not isinstance(batch_samples, list):
            raise ValueError("The approval gate does not contain a DOGME batch.")

        active_samples: list[dict] = []
        for sample in batch_samples:
            if not isinstance(sample, dict):
                continue
            status = str(sample.get("status") or "PENDING").upper()
            if status in {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"}:
                continue
            if sample.get("run_uuid"):
                sample["status"] = "CANCELLING"
                active_samples.append(sample)
            else:
                sample["status"] = "CANCELLED"

        payload["cancellation_requested"] = True
        payload["batch_samples"] = batch_samples
        payload["batch_status"] = "RUNNING" if active_samples else "CANCELLED"
        batch_gate.payload_json = json.dumps(payload)
        session.commit()

        async def cancel_one(sample: dict) -> tuple[dict, dict | Exception]:
            try:
                client = MCPHttpClient(name="launchpad", base_url=get_service_url("launchpad"))
                await client.connect()
                try:
                    result = await client.call_tool("cancel_job", run_uuid=sample["run_uuid"])
                finally:
                    await client.disconnect()
                return sample, result if isinstance(result, dict) else {}
            except Exception as exc:
                return sample, exc

        results = await asyncio.gather(*(cancel_one(sample) for sample in active_samples))
        for sample, result in results:
            if isinstance(result, Exception):
                sample["status"] = "FAILED"
                sample["error"] = f"Cancellation failed: {result}"
            else:
                sample["status"] = "CANCELLED"
                sample["error"] = None

        statuses = [str(sample.get("status") or "PENDING").upper() for sample in batch_samples if isinstance(sample, dict)]
        if statuses and all(status == "CANCELLED" for status in statuses):
            payload["batch_status"] = "CANCELLED"
        elif statuses and all(status in {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"} for status in statuses):
            payload["batch_status"] = "COMPLETED_WITH_FAILURES"
        else:
            payload["batch_status"] = "RUNNING"
        payload["batch_samples"] = batch_samples
        batch_gate.payload_json = json.dumps(payload)
        session.commit()

        from cortex.task_service import sync_project_tasks

        sync_project_tasks(session, project_id)
        return {
            "batch_id": payload.get("batch_id") or gate_block_id,
            "batch_status": payload["batch_status"],
            "cancelled_samples": sum(sample.get("status") == "CANCELLED" for sample in batch_samples if isinstance(sample, dict)),
        }
    finally:
        session.close()


async def retry_dogme_batch(
    project_id: str,
    gate_block_id: str,
    *,
    review_before_submit: bool = True,
) -> dict:
    """Clone terminal failed or cancelled samples into a linked retry batch."""
    session = SessionLocal()
    try:
        source_gate = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if source_gate is None or source_gate.project_id != project_id:
            raise ValueError("DOGME batch was not found.")

        source_payload = get_block_payload(source_gate)
        source_samples = source_payload.get("batch_samples")
        if not isinstance(source_samples, list):
            raise ValueError("The approval gate does not contain a DOGME batch.")

        terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"}
        statuses = [str(sample.get("status") or "PENDING").upper() for sample in source_samples if isinstance(sample, dict)]
        if not statuses or not all(status in terminal_statuses for status in statuses):
            raise ValueError("The DOGME batch must finish before failed samples can be retried.")

        retry_samples = []
        retry_attempt = int(source_payload.get("retry_attempt") or 0) + 1
        for sample in source_samples:
            if not isinstance(sample, dict):
                continue
            source_status = str(sample.get("status") or "PENDING").upper()
            if source_status not in {"FAILED", "CANCELLED"}:
                continue
            source_sample_id = str(sample.get("sample_id") or len(retry_samples) + 1)
            retry_samples.append({
                "sample_id": f"{source_sample_id}-retry-{retry_attempt}",
                "sample_name": sample.get("sample_name"),
                "input_directory": sample.get("input_directory"),
                "status": "PENDING",
                "retry_of_sample_id": source_sample_id,
            })

        if not retry_samples:
            raise ValueError("The DOGME batch has no failed or cancelled samples to retry.")

        source_batch_id = source_payload.get("batch_id") or source_gate.id
        retry_batch_id = uuid.uuid4().hex
        shared_params = dict(source_payload.get("shared_params") or {})
        retry_payload = {
            "label": f"Retry DOGME for {len(retry_samples)} failed sample{'s' if len(retry_samples) != 1 else ''}?",
            "extracted_params": {
                "batch_id": retry_batch_id,
                "batch_samples": retry_samples,
                "shared_params": shared_params,
                "requested_max_parallel": source_payload.get("requested_max_parallel"),
                "retry_of_batch_id": source_batch_id,
            },
            "batch_id": retry_batch_id,
            "batch_samples": retry_samples,
            "shared_params": shared_params,
            "requested_max_parallel": source_payload.get("requested_max_parallel"),
            "retry_of_batch_id": source_batch_id,
            "retry_attempt": retry_attempt,
            "batch_status": "PENDING",
            "gate_action": "job",
            "attempt_number": 1,
            "rejection_history": [],
            "skill": source_payload.get("skill") or "analyze_local_sample",
            "model": source_payload.get("model", "default"),
        }
        retry_gate = _create_block_internal(
            session,
            project_id,
            "APPROVAL_GATE",
            retry_payload,
            status="PENDING" if review_before_submit else "APPROVED",
            owner_id=source_gate.owner_id,
        )

        source_payload.setdefault("retry_batch_gate_ids", []).append(retry_gate.id)
        source_gate.payload_json = json.dumps(source_payload)
        session.commit()

        if not review_before_submit:
            asyncio.create_task(submit_dogme_batch_after_approval(project_id, retry_gate.id))

        return {
            "status": "approval_required" if review_before_submit else "submitting",
            "batch_id": retry_batch_id,
            "block_id": retry_gate.id,
            "retry_count": len(retry_samples),
        }
    finally:
        session.close()


def _is_reconcile_script_submission(job_data: dict) -> bool:
    script_id = str(job_data.get("script_id") or "").strip()
    if script_id in {"reconcile_bams/reconcile_bams", "reconcile_bams/reconcileBams"}:
        return True
    script_path = str(job_data.get("script_path") or "").strip()
    return Path(script_path).name in {"reconcile_bams.py", "reconcileBams.py"}


def _is_haplotype_script_submission(job_data: dict) -> bool:
    script_id = str(job_data.get("script_id") or "").strip()
    if script_id == "haplotype_with_vcf/haplotype_with_vcf":
        return True
    script_path = str(job_data.get("script_path") or "").strip()
    return Path(script_path).name == "haplotype_with_vcf.py"


def _submission_client_timeout_seconds(run_type: str, submission_payload: dict) -> float:
    if run_type != "script":
        return 900.0
    raw_timeout = submission_payload.get("timeout_seconds")
    if raw_timeout in (None, ""):
        return 900.0
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError):
        return 900.0
    if timeout_seconds <= 0:
        return 900.0
    return max(900.0, timeout_seconds + SCRIPT_SUBMISSION_TIMEOUT_BUFFER_SECONDS)


def _is_ambiguous_submit_timeout(error: Exception) -> bool:
    message = str(error).lower()
    return "timed out" in message and "submit" in message and "launchpad" in message


async def _find_accepted_submission(
    project_id: str,
    parent_block_id: str,
) -> dict | None:
    launchpad_url = get_service_url("launchpad")
    client = MCPHttpClient(name="launchpad", base_url=launchpad_url, timeout=30.0)
    await client.connect()
    try:
        result = await client.call_tool(
            "find_job_by_parent_block",
            project_id=project_id,
            parent_block_id=parent_block_id,
        )
    finally:
        await client.disconnect()
    return result if isinstance(result, dict) and result.get("run_uuid") else None


def _should_submit_script_as_job(job_data: dict) -> bool:
    return _is_reconcile_script_submission(job_data) or _is_haplotype_script_submission(job_data)


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


def _build_script_submission_payload(job_data: dict) -> dict:
    script_id = str(job_data.get("script_id") or "").strip()
    script_path = str(job_data.get("script_path") or "").strip()
    if not script_id and not script_path:
        raise ValueError("Script approval is missing both script_id and script_path.")

    submission_payload: dict = {}
    if script_id:
        submission_payload["script_id"] = script_id
    if script_path:
        submission_payload["script_path"] = script_path

    script_args = job_data.get("script_args")
    if isinstance(script_args, list):
        submission_payload["script_args"] = list(script_args)

    script_working_directory = str(job_data.get("script_working_directory") or "").strip()
    # The BED overlap script already receives absolute input/output paths and
    # Launchpad only accepts allowlisted roots for custom working directories.
    if script_id == "analyze_job_results/compare_bed_region_overlaps":
        script_working_directory = ""
    if script_working_directory:
        submission_payload["script_working_directory"] = script_working_directory

    output_directory = str(job_data.get("output_directory") or "").strip()
    if output_directory:
        submission_payload["output_directory"] = output_directory

    raw_timeout = job_data.get("timeout_seconds")
    if raw_timeout not in (None, ""):
        submission_payload["timeout_seconds"] = raw_timeout

    return submission_payload


def _build_haplotype_script_args(job_params: dict) -> list[str]:
    bam_inputs = job_params.get("bam_inputs") or []
    bam_paths = [
        item.get("path")
        for item in bam_inputs
        if isinstance(item, dict) and item.get("path")
    ]
    if not bam_paths:
        raise ValueError("Haplotype approval is missing BAM inputs.")

    vcf_path = str(job_params.get("vcf_path") or "").strip()
    if not vcf_path:
        vcf_path = str(
            default_haplotype_vcf_for_reference(
                job_params.get("reference_genome") or job_params.get("reference")
            )
            or ""
        ).strip()
    if not vcf_path:
        raise ValueError("Haplotype approval is missing the VCF path.")

    mode = str(job_params.get("mode") or "").strip()
    if not mode:
        raise ValueError("Haplotype approval is missing the assay mode.")

    output_dir = str(job_params.get("output_directory") or job_params.get("input_directory") or ".").strip() or "."
    script_args: list[str] = ["--json", "--output-dir", output_dir, "--vcf", vcf_path, "--mode", mode]
    for bam_path in bam_paths:
        script_args.extend(["--input-bam", bam_path])

    for sample_name in job_params.get("vcf_selected_samples") or []:
        if sample_name not in (None, ""):
            script_args.extend(["--vcf-sample", str(sample_name)])

    scalar_flags = [
        ("min_informative_sites", "--min-informative-sites"),
        ("min_mapq", "--min-mapq"),
        ("progress_read_interval", "--progress-read-interval"),
    ]
    if str(job_params.get("assignment_mode") or "").strip() != "founder_panel":
        scalar_flags = [("label_a", "--label-a"), ("label_b", "--label-b"), *scalar_flags]
    for field, flag in scalar_flags:
        value = job_params.get(field)
        if value is None or value == "":
            continue
        script_args.extend([flag, str(value)])

    return script_args


def _apply_workflow_specific_step_updates(workflow_payload: dict, job_params: dict) -> None:
    if not isinstance(workflow_payload, dict):
        return

    if str(workflow_payload.get("plan_type") or "").strip() != "compare_region_overlaps":
        return

    workflow_payload["sample_a_label"] = job_params.get("sample_a_label") or workflow_payload.get("sample_a_label")
    workflow_payload["sample_b_label"] = job_params.get("sample_b_label") or workflow_payload.get("sample_b_label")
    workflow_payload["plot_title"] = job_params.get("plot_title") or workflow_payload.get("plot_title")

    steps = workflow_payload.get("steps") or []
    locate_step = next(
        (step for step in steps if isinstance(step, dict) and step.get("id") == "locate_overlap"),
        None,
    )
    if locate_step is None:
        locate_step = next(
            (step for step in steps if isinstance(step, dict) and step.get("kind") == "LOCATE_DATA"),
            None,
        )
    if isinstance(locate_step, dict):
        locate_step["title"] = (
            f"Identify region files for {workflow_payload.get('sample_a_label') or 'Sample A'} "
            f"and {workflow_payload.get('sample_b_label') or 'Sample B'}"
        )

    if job_params.get("plot_title"):
        plot_step = next(
            (step for step in steps if isinstance(step, dict) and step.get("id") == "plot_overlap"),
            None,
        )
        if plot_step is None:
            plot_step = next(
                (step for step in steps if isinstance(step, dict) and step.get("kind") == "GENERATE_PLOT"),
                None,
            )
        if isinstance(plot_step, dict):
            plot_step["plot_title"] = job_params.get("plot_title")


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


def _wf_pore_c_output_flags(job_params: dict) -> dict[str, bool]:
    raw_flags = job_params.get("output_flags") if isinstance(job_params.get("output_flags"), dict) else {}
    flags = {
        "pairs": bool(raw_flags.get("pairs", True)),
        "mcool": bool(raw_flags.get("mcool", True)),
        "hi_c": bool(raw_flags.get("hi_c", False)),
        "bed": bool(raw_flags.get("bed", False)),
        "chromunity": bool(raw_flags.get("chromunity", False)),
        "coverage": bool(raw_flags.get("coverage", False)),
        "paired_end": bool(raw_flags.get("paired_end", False)),
    }
    if flags["bed"]:
        flags["paired_end"] = True
    return flags


def _wf_pore_c_preview_work_dir(output_directory: str, input_path: str, sample_name: str) -> str:
    if output_directory:
        output_path = Path(output_directory).expanduser()
        work_root = output_path.parent / ".nextflow-work" / "wf-pore-c"
        return str(work_root / sample_name)

    normalized_input = str(input_path or "").strip()
    if normalized_input:
        input_path_obj = Path(normalized_input).expanduser()
        parent = input_path_obj if input_path_obj.is_dir() else input_path_obj.parent
        return str(parent / ".nextflow-work" / "wf-pore-c" / sample_name)

    return str(Path(".").resolve() / ".nextflow-work" / "wf-pore-c" / sample_name)


def _build_wf_pore_c_dry_run_preview(job_params: dict) -> dict:
    sample_name = str(job_params.get("sample_name") or job_params.get("sample") or "pore_c_sample").strip() or "pore_c_sample"
    workflow_repo = str(job_params.get("workflow_repo") or "epi2me-labs/wf-pore-c").strip() or "epi2me-labs/wf-pore-c"
    workflow_version = str(job_params.get("workflow_version") or "v1.3.1").strip() or "v1.3.1"
    input_path = str(job_params.get("file_path") or job_params.get("input_directory") or "").strip()
    input_type = str(job_params.get("input_type") or "").strip().lower() or ("bam" if input_path.lower().endswith(".bam") else "fastq")
    reference_fasta = str(job_params.get("reference_fasta") or "").strip()
    vcf = str(job_params.get("vcf") or "").strip()
    sample_sheet = str(job_params.get("sample_sheet") or "").strip()
    cutter = str(job_params.get("cutter") or "NlaIII").strip() or "NlaIII"
    output_directory = str(job_params.get("output_directory") or "").strip()
    report_filename = str(job_params.get("report_filename") or "wf-pore-c-report.html").strip() or "wf-pore-c-report.html"
    output_flags = _wf_pore_c_output_flags(job_params)
    work_dir = _wf_pore_c_preview_work_dir(output_directory, input_path, sample_name)

    command_parts: list[str] = [
        "nextflow",
        "run",
        workflow_repo,
        "-r",
        workflow_version,
        f"--{input_type}",
        input_path,
        "--ref",
        reference_fasta,
        "--out_dir",
        output_directory,
        "-work-dir",
        work_dir,
    ]
    if sample_name and not sample_sheet:
        command_parts.extend(["--sample", sample_name])
    if sample_sheet:
        command_parts.extend(["--sample_sheet", sample_sheet])
    if vcf:
        command_parts.extend(["--vcf", vcf])
    if cutter:
        command_parts.extend(["--cutter", cutter])
    for flag_name in ("pairs", "mcool", "hi_c", "bed", "chromunity", "coverage", "paired_end"):
        if output_flags[flag_name]:
            command_parts.append(f"--{flag_name}")
    command_parts.extend(["-profile", "standard"])

    expected_outputs = ["bams/{alias}.cs.bam", report_filename]
    if output_flags["pairs"]:
        expected_outputs.append("pairs/{alias}.pairs.gz")
    if output_flags["mcool"]:
        expected_outputs.append("cooler/{alias}.mcool")
    if output_flags["hi_c"]:
        expected_outputs.append("hi-c/{alias}.hic")
    if output_flags["chromunity"]:
        expected_outputs.append("chromunity/")
    if output_flags["coverage"]:
        expected_outputs.append("coverage/")

    return {
        "workflow_key": "wf_pore_c",
        "workflow_repo": workflow_repo,
        "workflow_version": workflow_version,
        "sample_name": sample_name,
        "input_type": input_type,
        "input_path": input_path,
        "reference_fasta": reference_fasta,
        "vcf": vcf or None,
        "sample_sheet": sample_sheet or None,
        "cutter": cutter,
        "output_directory": output_directory,
        "work_dir": work_dir,
        "report_filename": report_filename,
        "output_flags": output_flags,
        "expected_outputs": expected_outputs,
        "command": " \\\n    ".join(shlex.quote(part) for part in command_parts),
        "notes": [
            "Phase 1 preview only: no Launchpad job has been submitted.",
            "Keep -work-dir outside --out_dir to avoid Nextflow work/output collisions.",
            "Preflight the reference sidecars before real submission: .fai and chromsizes.",
            "Large BAM/FASTQ staging is planned as symlink-first with copy fallback only when required.",
        ],
    }


def _wf_pore_c_preview_markdown(preview: dict) -> str:
    output_flags = preview.get("output_flags") or {}
    enabled_outputs = [name for name, enabled in output_flags.items() if enabled]
    outputs_text = ", ".join(enabled_outputs) if enabled_outputs else "none"
    expected_outputs = preview.get("expected_outputs") or []
    expected_outputs_text = "\n".join(f"- `{item}`" for item in expected_outputs)
    notes = preview.get("notes") or []
    notes_text = "\n".join(f"- {item}" for item in notes)
    optional_lines = []
    if preview.get("vcf"):
        optional_lines.append(f"- VCF: `{preview['vcf']}`")
    if preview.get("sample_sheet"):
        optional_lines.append(f"- Sample sheet: `{preview['sample_sheet']}`")
    optional_text = "\n".join(optional_lines) if optional_lines else "- Optional inputs: none"
    return (
        "### wf-pore-c Dry-Run Preview\n\n"
        "No workflow was submitted. This card shows the Phase 1 execution draft that AGOUTIC would hand to Launchpad later.\n\n"
        f"- Sample: `{preview['sample_name']}`\n"
        f"- Input type: `{preview['input_type']}`\n"
        f"- Input path: `{preview['input_path']}`\n"
        f"- Reference FASTA: `{preview['reference_fasta']}`\n"
        f"{optional_text}\n"
        f"- Cutter: `{preview['cutter']}`\n"
        f"- Output directory: `{preview['output_directory']}`\n"
        f"- Work directory: `{preview['work_dir']}`\n"
        f"- Enabled outputs: `{outputs_text}`\n"
        f"- Report filename: `{preview['report_filename']}`\n\n"
        "Expected outputs:\n"
        f"{expected_outputs_text}\n\n"
        "```bash\n"
        f"{preview['command']}\n"
        "```\n\n"
        "Notes:\n"
        f"{notes_text}\n"
    )


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

        if isinstance(job_params, dict) and job_params.get("batch_samples"):
            await submit_dogme_batch_after_approval(project_id, gate_block_id)
            return

        gate_action = gate_payload.get("gate_action") or job_params.get("gate_action") or "job"
        workflow_key = str(job_params.get("workflow_key") or "").strip().lower()
        if gate_action == "workflow_dry_run_preview" and workflow_key == "wf_pore_c":
            preview_payload = _build_wf_pore_c_dry_run_preview(job_params)
            preview_block = _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": _wf_pore_c_preview_markdown(preview_payload),
                    "skill": "run_wf_pore_c",
                    "model": gate_payload.get("model", "default"),
                    "workflow_preview": preview_payload,
                },
                status="DONE",
                owner_id=owner_id,
            )
            gate_payload["preview_block_id"] = preview_block.id
            gate_payload["workflow_preview"] = preview_payload
            gate_block.payload_json = json.dumps(gate_payload)
            session.commit()
            return

        run_type = (job_params.get("run_type") or "dogme").strip().lower()
        if run_type not in {"dogme", "script"}:
            run_type = "dogme"

        if run_type == "script" and gate_action == "reconcile_bams":
            job_params = dict(job_params)
            job_params["script_id"] = "reconcile_bams/reconcile_bams"
            job_params["script_args"] = _build_reconcile_script_args(job_params)
        elif run_type == "script" and gate_action == "haplotype_with_vcf":
            job_params = dict(job_params)
            job_params["script_id"] = "haplotype_with_vcf/haplotype_with_vcf"
            job_params["script_args"] = _build_haplotype_script_args(job_params)

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
        selected_slurm_gpu_account = job_params.get("slurm_gpu_account")
        selected_slurm_gpu_partition = job_params.get("slurm_gpu_partition")

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
            "accuracy": resolve_dogme_accuracy(job_params.get("mode"), job_params.get("accuracy")),
            "max_gpu_tasks": job_params.get("max_gpu_tasks") if "max_gpu_tasks" in job_params else None,
            "local_max_task_cpus": job_params.get("local_max_task_cpus"),
            "local_max_task_memory_gb": job_params.get("local_max_task_memory_gb"),
            "custom_dogme_profile": job_params.get("custom_dogme_profile"),
            "custom_dogme_bind_paths": job_params.get("custom_dogme_bind_paths") or [],
            "execution_mode": execution_mode,
            "ssh_profile_id": ssh_profile_id,
            "slurm_account": selected_slurm_account,
            "slurm_partition": selected_slurm_partition,
            "slurm_gpu_account": selected_slurm_gpu_account,
            "slurm_gpu_partition": selected_slurm_gpu_partition,
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

            workflow_payload = get_block_payload(workflow_block)
            _apply_workflow_specific_step_updates(workflow_payload, job_params)
            workflow_block.payload_json = json.dumps(workflow_payload)
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

        # For BAM remap and fastqCDNA: if no valid input_directory was found, resolve to project data/.
        # Uploaded files already appear in project data/ as symlinks to the user's central data folder.
        _input_dir = job_data["input_directory"]
        if _username and _project_slug:
            _project_data_dir = Path(AGOUTIC_DATA) / "users" / _username / _project_slug / "data"
        else:
            _project_data_dir = Path(AGOUTIC_DATA) / "users" / owner_id / project_id / "data"

        if (job_data.get("input_type") == "bam"
            and job_data.get("entry_point") == "remap"
            and (_input_dir == "/data/samples/test" or not Path(_input_dir).exists())):
            if _project_data_dir.exists():
                job_data["input_directory"] = str(_project_data_dir)
                logger.info("Resolved BAM input to project data dir", input_directory=str(_project_data_dir))

        if (job_data.get("input_type") == "fastq"
            and job_data.get("entry_point") == "fastqCDNA"
            and (_input_dir == "/data/samples/test" or not Path(_input_dir).exists())):
            if _project_data_dir.exists():
                _fastq_candidates = sorted(
                    path for path in _project_data_dir.iterdir()
                    if path.is_file() and path.name.lower().endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz"))
                )
                if len(_fastq_candidates) == 1:
                    job_data["input_directory"] = str(_fastq_candidates[0])
                    logger.info("Resolved fastqCDNA input to single project FASTQ", input_directory=str(_fastq_candidates[0]))
                else:
                    job_data["input_directory"] = str(_project_data_dir)
                    logger.info("Resolved fastqCDNA input to project data dir", input_directory=str(_project_data_dir), fastq_candidates=len(_fastq_candidates))

        logger.info("Job parameters prepared", source="edited" if gate_payload.get('edited_params') else "extracted",
                    job_data=job_data)

        if run_type == "script":
            if _should_submit_script_as_job(job_data):
                submission_tool = "submit_dogme_job"
                submission_payload = dict(job_data)
                submission_payload.pop("staged_input_directory", None)
            else:
                submission_tool = "run_allowlisted_script"
                submission_payload = _build_script_submission_payload(job_data)
        else:
            submission_tool = "submit_dogme_job"
            submission_payload = dict(job_data)
            submission_payload.pop("staged_input_directory", None)

        if workflow_block is not None:
            submission_payload["parent_block_id"] = workflow_block.id

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

        if execution_mode == "slurm" and workflow_block is not None and stage_input_step_id:
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
            elif not remote_stage_only:
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
                    "local_workflow_directory": job_params.get("local_workflow_directory"),
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

        if remote_stage_only and job_data.get("staged_remote_input_path"):
            if workflow_block is not None and complete_stage_only_step_id:
                _set_workflow_step_status(
                    session,
                    workflow_block,
                    complete_stage_only_step_id,
                    "COMPLETED",
                    extra={
                        "staged_input_directory": job_data.get("staged_remote_input_path"),
                        "data_action": data_action,
                    },
                )
            logger.info(
                "Remote stage-only request already satisfied by remote path",
                project_id=project_id,
                sample_name=job_data["sample_name"],
                remote_input_path=job_data.get("staged_remote_input_path"),
            )
            return

        if gate_action == "remote_stage" or remote_action == "stage_only":
            raise RuntimeError(
                "Refusing to submit a job for a remote stage-only request; the request must stop at remote staging."
            )

        # Submit job to Launchpad via MCP (single call - Nextflow handles multi-genome)
        try:
            launchpad_url = get_service_url("launchpad")
            client = MCPHttpClient(
                name="launchpad",
                base_url=launchpad_url,
                timeout=_submission_client_timeout_seconds(run_type, submission_payload),
            )
            await client.connect()
            try:
                result = await client.call_tool(submission_tool, **submission_payload)
            finally:
                await client.disconnect()
        except Exception as e:
            _err = str(e).strip() or f"{type(e).__name__}: {e!r}"
            if workflow_block is None or not _is_ambiguous_submit_timeout(e):
                raise Exception(f"MCP call to Launchpad failed: {_err}")
            try:
                result = await _find_accepted_submission(project_id, workflow_block.id)
            except Exception as recovery_error:
                recovery_detail = str(recovery_error).strip() or type(recovery_error).__name__
                raise Exception(
                    f"MCP call to Launchpad failed: {_err}. "
                    f"Timed-out submission could not be reconciled: {recovery_detail}"
                ) from recovery_error
            if result is None:
                raise Exception(
                    f"MCP call to Launchpad failed: {_err}. "
                    "Launchpad did not register a job for this workflow."
                )
            logger.warning(
                "Recovered Launchpad submission after response timeout",
                project_id=project_id,
                parent_block_id=workflow_block.id,
                run_uuid=result["run_uuid"],
            )

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

        script_completed_without_run_uuid = (
            run_type == "script"
            and not run_uuid
            and isinstance(result, dict)
            and bool(result.get("success"))
            and int(result.get("exit_code") or 0) == 0
        )

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
                    "output_directory": _work_directory,
                    "sample_name": job_data["sample_name"],
                    "mode": job_data["mode"],
                    "run_type": run_type,
                    "batch_id": job_params.get("batch_id"),
                    "batch_sample_id": job_params.get("batch_sample_id"),
                    "batch_parent_gate_id": job_params.get("batch_parent_gate_id"),
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

            _record_batch_sample_submission(
                session,
                batch_parent_gate_id=job_params.get("batch_parent_gate_id"),
                batch_sample_id=job_params.get("batch_sample_id"),
                run_uuid=run_uuid,
                execution_block_id=job_block.id,
                status="QUEUED",
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
                        extra={
                            "run_uuid": run_uuid,
                            "block_id": job_block.id,
                            **({"work_directory": _work_directory, "output_directory": _work_directory} if _work_directory else {}),
                        },
                    )

            logger.info("Job submitted", run_uuid=run_uuid, project_id=project_id)

            # Start polling job status in background
            asyncio.create_task(job_polling.poll_job_status(project_id, job_block.id, run_uuid))
        elif script_completed_without_run_uuid:
            _gate_model = gate_payload.get("model", "default")
            job_block = _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "run_uuid": None,
                    "work_directory": _work_directory,
                    "output_directory": _work_directory,
                    "sample_name": job_data["sample_name"],
                    "mode": job_data["mode"],
                    "run_type": run_type,
                    "workflow_plan_block_id": workflow_block.id if workflow_block is not None else None,
                    "model": _gate_model,
                    "status": "COMPLETED",
                    "message": "Script completed successfully",
                    "script_id": job_data.get("script_id"),
                    "script_path": job_data.get("script_path"),
                    "script_args": job_data.get("script_args") if isinstance(job_data.get("script_args"), list) else None,
                    "stdout": result.get("stdout") if isinstance(result, dict) else None,
                    "stderr": result.get("stderr") if isinstance(result, dict) else None,
                    "exit_code": result.get("exit_code") if isinstance(result, dict) else None,
                    "job_status": {
                        "status": "COMPLETED",
                        "progress_percent": 100,
                        "message": "Script completed successfully",
                        "tasks": {},
                    },
                    "logs": [],
                },
                status="DONE",
                owner_id=owner_id,
            )

            if workflow_block is not None:
                run_step_id = _resolve_workflow_step_id(
                    get_block_payload(workflow_block),
                    "run_dogme",
                    kinds=("RUN_SCRIPT",),
                )
                if run_step_id:
                    step_result_payload = dict(result)
                    if _work_directory:
                        step_result_payload["work_directory"] = _work_directory
                        step_result_payload["output_directory"] = _work_directory
                    _set_workflow_step_status(
                        session,
                        workflow_block,
                        run_step_id,
                        "COMPLETED",
                        extra={
                            "block_id": job_block.id,
                            **({"work_directory": _work_directory, "output_directory": _work_directory} if _work_directory else {}),
                            "result": [
                                {
                                    "tool": submission_tool,
                                    "result": step_result_payload,
                                }
                            ],
                        },
                    )

                from cortex.app import _auto_execute_plan_steps

                user_stub = type("UserStub", (), {"id": owner_id})()
                asyncio.create_task(
                    _auto_execute_plan_steps(
                        project_id,
                        workflow_block.id,
                        user_stub,
                        gate_payload.get("model", "default"),
                    )
                )

            logger.info(
                "Script completed without run_uuid",
                project_id=project_id,
                script_id=job_data.get("script_id"),
                work_directory=_work_directory,
            )
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
                        "local_workflow_directory": locals().get("job_params", {}).get("local_workflow_directory"),
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
