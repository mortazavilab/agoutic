import asyncio
import copy
import datetime
import json
import re
import time

from fastapi.concurrency import run_in_threadpool

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.agent_engine import AgentEngine
from cortex.analysis_helpers import _build_auto_analysis_context, _build_static_analysis_summary
from cortex.config import WF_PORE_C_ENABLED, get_service_url
from cortex.db import SessionLocal
from cortex.db_helpers import _create_block_internal, save_conversation_message
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock
from cortex.remote_orchestration import _find_workflow_plan, _resolve_workflow_step_id, _set_workflow_step_status
from cortex.task_service import sync_project_tasks

logger = get_logger(__name__)

_TRAILING_PATH_JUNK = re.compile(r'(?:\\n|[^a-zA-Z0-9/_.\-~])+$')
_JOB_STATUS_CACHE_MAX_AGE_SECONDS = 30.0
_ACTIVE_RESULT_SYNC_STATES = {"pending_import", "downloading_outputs"}
_TERMINAL_RESULT_SYNC_STATES = {"outputs_downloaded", "transfer_failed", "sync_cancelled", "stale"}
_latest_job_status_by_run_uuid: dict[str, dict] = {}


def _normalized_workflow_key(value: str | None) -> str:
    normalized = str(value or "dogme").strip().lower()
    return normalized or "dogme"


def _job_status_has_useful_progress(status_data: dict | None) -> bool:
    if not isinstance(status_data, dict):
        return False
    tasks = status_data.get("tasks")
    if isinstance(tasks, dict):
        if int(tasks.get("completed_count", 0) or 0) > 0:
            return True
        if int(tasks.get("total", 0) or 0) > 0 and int(tasks.get("remaining_count", 0) or 0) >= 0:
            return True
    try:
        if int(status_data.get("progress_percent", 0) or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    message = str(status_data.get("message") or "")
    return message.startswith("Pipeline:")


def _is_transient_scheduler_poll_failure(status_data: dict | None) -> bool:
    if not isinstance(status_data, dict):
        return False
    message = str(status_data.get("message") or "")
    if not message.startswith("Failed to poll scheduler:"):
        return False
    status = str(status_data.get("status") or "").upper()
    if status and status not in {"RUNNING", "PENDING", "QUEUED"}:
        return False
    tasks = status_data.get("tasks")
    return not isinstance(tasks, dict) or not bool(tasks)


def _prefer_richer_job_status(previous_status: dict | None, incoming_status: dict | None) -> dict | None:
    if not isinstance(incoming_status, dict):
        return None
    if isinstance(previous_status, dict):
        previous_transfer_state = str(previous_status.get("transfer_state") or "").strip().lower()
        incoming_transfer_state = str(incoming_status.get("transfer_state") or "").strip().lower()
        incoming_result_destination = str(incoming_status.get("result_destination") or "").strip().lower()
        incoming_status_value = str(incoming_status.get("status") or "").upper()
        if (
            previous_transfer_state in _ACTIVE_RESULT_SYNC_STATES
            and incoming_status_value == "COMPLETED"
            and incoming_transfer_state not in _TERMINAL_RESULT_SYNC_STATES
        ):
            preserved = copy.deepcopy(incoming_status)
            for key in (
                "transfer_state",
                "result_destination",
                "transfer_detail",
                "imported_source_kind",
                "import_warning_message",
                "work_directory",
            ):
                if preserved.get(key) in (None, "", [], {}):
                    value = previous_status.get(key)
                    if value not in (None, "", [], {}):
                        preserved[key] = copy.deepcopy(value)

            preserved_transfer_state = str(preserved.get("transfer_state") or "").strip().lower()
            preserved_result_destination = str(preserved.get("result_destination") or "").strip().lower()
            if (
                preserved_transfer_state in _ACTIVE_RESULT_SYNC_STATES
                and preserved_result_destination in {"local", "both"}
            ):
                previous_status_value = str(previous_status.get("status") or "").upper()
                if previous_status_value in {"RUNNING", "PENDING", "QUEUED"}:
                    preserved["status"] = previous_status_value
                try:
                    preserved["progress_percent"] = max(
                        int(preserved.get("progress_percent", 0) or 0),
                        int(previous_status.get("progress_percent", 0) or 0),
                        95,
                    )
                except (TypeError, ValueError):
                    preserved["progress_percent"] = previous_status.get("progress_percent", 95)
                if not preserved.get("message"):
                    preserved["message"] = previous_status.get("message")
                return preserved
    if _is_transient_scheduler_poll_failure(incoming_status) and _job_status_has_useful_progress(previous_status):
        preserved = copy.deepcopy(previous_status)
        preserved["last_poll_error"] = incoming_status.get("message")
        for key in ("slurm_state", "transfer_state", "transfer_detail", "ssh_profile_nickname", "work_directory"):
            value = incoming_status.get(key)
            if value not in (None, "", [], {}):
                preserved[key] = value
        return preserved
    return copy.deepcopy(incoming_status)


def cache_job_status(run_uuid: str, status_data: dict | None) -> None:
    if not run_uuid or not isinstance(status_data, dict):
        return
    previous_status = None
    existing = _latest_job_status_by_run_uuid.get(run_uuid)
    if isinstance(existing, dict) and isinstance(existing.get("status_data"), dict):
        previous_status = existing["status_data"]
    resolved_status = _prefer_richer_job_status(previous_status, status_data)
    if not isinstance(resolved_status, dict):
        return
    _latest_job_status_by_run_uuid[run_uuid] = {
        "cached_at": time.time(),
        "status_data": resolved_status,
    }


def get_cached_job_status(run_uuid: str, *, max_age_seconds: float = _JOB_STATUS_CACHE_MAX_AGE_SECONDS) -> dict | None:
    if not run_uuid:
        return None
    entry = _latest_job_status_by_run_uuid.get(run_uuid)
    if not isinstance(entry, dict):
        return None
    cached_at = float(entry.get("cached_at") or 0.0)
    if max_age_seconds > 0 and (time.time() - cached_at) > max_age_seconds:
        return None
    status_data = entry.get("status_data")
    if not isinstance(status_data, dict):
        return None
    return copy.deepcopy(status_data)


def _sanitize_work_directory(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _TRAILING_PATH_JUNK.sub('', str(value).strip())
    return cleaned or None


async def poll_job_status(
    project_id: str,
    block_id: str,
    run_uuid: str,
    *,
    initial_delay_seconds: float | None = None,
):
    """
    Background task to poll Launchpad for job status via MCP and update the EXECUTION_JOB block.
    Continues until job is completed or failed.
    """

    # Poll execution status at a steady 30-second cadence to avoid hammering
    # Launchpad and SQLite-backed state while jobs are active.
    _POLL_SCHEDULE = [
        (1200, 30),  # ~10 h coverage at 30-second intervals
    ]
    _job_done = False
    _next_delay = initial_delay_seconds

    for _batch_polls, _interval in _POLL_SCHEDULE:
        if _job_done:
            break
        for _ in range(_batch_polls):
            if _job_done:
                break
            delay_seconds = _interval if _next_delay is None else max(0.0, float(_next_delay))
            _next_delay = None
            await asyncio.sleep(delay_seconds)

            session = SessionLocal()
            try:
                # Get current status from Launchpad via MCP
                launchpad_url = get_service_url("launchpad")
                client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
                await client.connect()
                try:
                    status_data = await client.call_tool("check_nextflow_status", run_uuid=run_uuid)
                finally:
                    await client.disconnect()

                if not isinstance(status_data, dict):
                    logger.warning("Failed to get status", run_uuid=run_uuid)
                    continue

                cache_job_status(run_uuid, status_data)

                logs: list = []
                logs_fetch_failed = False
                try:
                    launchpad_url = get_service_url("launchpad")
                    client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
                    await client.connect()
                    try:
                        logs_data = await client.call_tool("get_job_logs", run_uuid=run_uuid, limit=50)
                    finally:
                        await client.disconnect()
                    logs = logs_data.get("logs", []) if isinstance(logs_data, dict) else []
                except Exception as log_exc:
                    logs_fetch_failed = True
                    logger.warning("Failed to get job logs during polling", run_uuid=run_uuid, error=str(log_exc))

                # Update the block with new data
                block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
                if block:
                    # Create new payload dict to ensure SQLAlchemy detects the change
                    payload = get_block_payload(block)
                    previous_job_status = payload.get("job_status") if isinstance(payload.get("job_status"), dict) else None
                    status_data = _prefer_richer_job_status(previous_job_status, status_data) or status_data
                    resolved_work_directory = _resolved_job_work_directory(payload.get("work_directory"), status_data)
                    if resolved_work_directory:
                        status_data = dict(status_data)
                        status_data["work_directory"] = resolved_work_directory
                    cache_job_status(run_uuid, status_data)
                    payload["job_status"] = status_data
                    if resolved_work_directory:
                        payload["work_directory"] = resolved_work_directory
                    if not logs_fetch_failed:
                        payload["logs"] = logs
                    payload["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"

                    # Update block status based on job status
                    job_status = status_data.get("status", "UNKNOWN")
                    completed_ready_for_analysis = _completed_job_results_ready(status_data)
                    completed_sync_terminal = _completed_job_result_sync_is_terminal(status_data)
                    if job_status == "COMPLETED" and (completed_ready_for_analysis or completed_sync_terminal):
                        block.status = "DONE"
                    elif job_status == "FAILED":
                        block.status = "FAILED"
                    elif job_status == "STALE":
                        block.status = "FAILED"
                    elif job_status == "CANCELLED":
                        block.status = "CANCELLED"
                    else:
                        block.status = "RUNNING"

                    # Reassign payload_json to trigger SQLAlchemy update
                    block.payload_json = json.dumps(payload)
                    session.commit()
                    session.refresh(block)
                    sync_project_tasks(session, project_id)

                    logger.info("Job status updated", run_uuid=run_uuid, job_status=job_status, progress=status_data.get("progress_percent", 0))

                    # Stop polling if job is done
                    if job_status in {"FAILED", "CANCELLED", "STALE"} or (job_status == "COMPLETED" and (completed_ready_for_analysis or completed_sync_terminal)):
                        logger.info("Job finished", run_uuid=run_uuid, job_status=job_status)

                        workflow_block = _find_workflow_plan(session, project_id, run_uuid=run_uuid)
                        if workflow_block is not None:
                            workflow_payload = get_block_payload(workflow_block)
                            run_step_id = _resolve_workflow_step_id(
                                workflow_payload,
                                "run_dogme",
                                kinds=("RUN_SCRIPT",),
                            )
                            _set_workflow_step_status(
                                session,
                                workflow_block,
                                run_step_id or "run_dogme",
                                (
                                    "COMPLETED" if job_status == "COMPLETED"
                                    else ("CANCELLED" if job_status == "CANCELLED" else "FAILED")
                                ),
                                extra={
                                    "run_uuid": run_uuid,
                                    "block_id": block_id,
                                    **({"work_directory": resolved_work_directory} if resolved_work_directory else {}),
                                },
                            )

                        # On completion, auto-trigger analysis
                        if job_status == "COMPLETED" and payload.get("run_type") != "script":
                            await _auto_trigger_analysis(
                                project_id, run_uuid, payload, block.owner_id
                            )
                        elif job_status == "COMPLETED" and workflow_block is not None and payload.get("run_type") == "script":
                            from cortex.app import _auto_execute_plan_steps

                            user_stub = type("UserStub", (), {"id": block.owner_id})()
                            asyncio.create_task(
                                _auto_execute_plan_steps(
                                    project_id,
                                    workflow_block.id,
                                    user_stub,
                                    payload.get("model", "default"),
                                )
                            )

                        # Auto-capture completed job to memory
                        if job_status == "COMPLETED":
                            try:
                                from cortex.memory_service import auto_capture_result
                                auto_capture_result(
                                    session,
                                    user_id=block.owner_id,
                                    project_id=project_id,
                                    run_uuid=run_uuid,
                                    sample_name=payload.get("sample_name", ""),
                                    workflow_type=payload.get("mode", payload.get("workflow_type", "")),
                                    work_directory=payload.get("work_directory", ""),
                                    block_id=block_id,
                                )
                            except Exception:
                                logger.debug("Memory auto-capture failed for job", exc_info=True)

                        _job_done = True
                        break

            except Exception as e:
                logger.warning("Error polling job", run_uuid=run_uuid, error=str(e))
            finally:
                session.close()

    logger.info("Stopped polling job", run_uuid=run_uuid)


def _completed_job_results_ready(status_data: dict | None) -> bool:
    if not isinstance(status_data, dict):
        return False
    if status_data.get("status") != "COMPLETED":
        return False
    result_destination = (status_data.get("result_destination") or "").strip().lower()
    if result_destination not in {"local", "both"}:
        return True
    return (status_data.get("transfer_state") or "") == "outputs_downloaded"


def _completed_job_result_sync_is_terminal(status_data: dict | None) -> bool:
    if not isinstance(status_data, dict):
        return False
    if status_data.get("status") != "COMPLETED":
        return False
    result_destination = (status_data.get("result_destination") or "").strip().lower()
    if result_destination not in {"local", "both"}:
        return False
    return (status_data.get("transfer_state") or "").strip().lower() in _TERMINAL_RESULT_SYNC_STATES


def _resolved_job_work_directory(existing_work_directory: str | None, status_data: dict | None) -> str | None:
    sanitized_existing = _sanitize_work_directory(existing_work_directory)
    if isinstance(status_data, dict):
        status_work_directory = _sanitize_work_directory(status_data.get("work_directory") or "")
        if status_work_directory:
            # Don't let a script-cwd value from Launchpad overwrite a
            # better output directory that cortex already resolved.
            if (
                sanitized_existing
                and "/skills/" in status_work_directory
                and "/scripts" in status_work_directory
                and "/skills/" not in sanitized_existing
            ):
                return sanitized_existing
            return status_work_directory
    return sanitized_existing or None


async def _auto_trigger_analysis(
    project_id: str,
    run_uuid: str,
    job_payload: dict,
    owner_id: str | None,
    *,
    request_message: str | None = None,
    persist_request_message: bool = True,
    force: bool = False,
):
    """
    Automatically analyse a just-completed workflow.

    1. Fetches the analysis summary (file listing) from Analyzer.
    2. Parses key CSV result files (final_stats, qc_summary) via Analyzer MCP.
    3. Passes everything to the LLM for an intelligent first interpretation.
    4. Saves the LLM response as an AGENT_PLAN block with token tracking.

    Falls back to a static template if the LLM call fails.

    Returns the created AGENT_PLAN block on success, else ``None``.
    """
    sample_name = job_payload.get("sample_name", "Unknown")
    mode = job_payload.get("mode", "DNA")
    model_key = job_payload.get("model", "default")
    work_directory = job_payload.get("work_directory", "")
    workflow_key = _normalized_workflow_key(job_payload.get("workflow_key"))

    logger.info("Auto-triggering analysis", run_uuid=run_uuid,
                sample_name=sample_name, mode=mode, workflow_key=workflow_key, model=model_key)

    session = SessionLocal()
    try:
        workflow_block = _find_workflow_plan(session, project_id, run_uuid=run_uuid)
        if workflow_block is not None:
            workflow_payload = get_block_payload(workflow_block)
            if not force and workflow_payload.get("next_step") != "analyze_results":
                logger.info("Skipping auto-analysis because analysis is not the next todo", run_uuid=run_uuid)
                return None
            _set_workflow_step_status(
                session,
                workflow_block,
                "analyze_results",
                "RUNNING",
                extra={"run_uuid": run_uuid},
            )

        request_text = request_message or f"Job \"{sample_name}\" completed. Analyze the results."
        if persist_request_message:
            _create_block_internal(
                session,
                project_id,
                "USER_MESSAGE",
                {"text": request_text},
                owner_id=owner_id,
            )

            # Also save to conversation history so the LLM sees it
            if owner_id:
                await save_conversation_message(
                    session, project_id, owner_id, "user", request_text
                )

        # 2. Fetch analysis summary + key CSV data from Analyzer
        summary_data = {}  # structured summary from get_analysis_summary
        parsed_csvs = {}   # filename -> parsed rows from key CSVs
        try:
            analyzer_url = get_service_url("analyzer")
            client = MCPHttpClient(name="analyzer", base_url=analyzer_url)
            await client.connect()
            try:
                summary_data = await client.call_tool(
                    "get_analysis_summary", run_uuid=run_uuid,
                    work_dir=work_directory or None,
                )
                if not isinstance(summary_data, dict):
                    summary_data = {}
                workflow_key = _normalized_workflow_key(summary_data.get("workflow_key") or workflow_key)

                # Parse key CSV files for the LLM
                # Prioritise small, high-value files: final_stats, qc_summary
                csv_files = (
                    summary_data
                    .get("file_summary", {})
                    .get("csv_files", [])
                )
                _KEY_PATTERNS = ("final_stats", "qc_summary")
                for finfo in csv_files:
                    fname = finfo.get("name", "")
                    fsize = finfo.get("size", 0)
                    if fsize > 500_000:  # skip files > 500 KB
                        continue
                    if any(pat in fname.lower() for pat in _KEY_PATTERNS):
                        try:
                            _csv_params: dict = {
                                "file_path": fname,
                                "max_rows": 50,
                            }
                            if work_directory:
                                _csv_params["work_dir"] = work_directory
                            else:
                                _csv_params["run_uuid"] = run_uuid
                            parse_result = await client.call_tool(
                                "parse_csv_file",
                                **_csv_params,
                            )
                            if isinstance(parse_result, dict) and parse_result.get("data"):
                                parsed_csvs[fname] = parse_result
                        except Exception as csv_err:
                            logger.debug("Failed to parse CSV for auto-analysis",
                                         filename=fname, error=str(csv_err))
            finally:
                await client.disconnect()
        except Exception as e:
            logger.warning("Failed to fetch analysis summary", run_uuid=run_uuid, error=str(e))

        # 3. Route auto-analysis by workflow family, with wf-pore-c behind its feature flag.
        mode_skill_map = {
            "DNA": "run_dogme_dna",
            "RNA": "run_dogme_rna",
            "CDNA": "run_dogme_cdna",
        }
        wf_pore_c_enabled = WF_PORE_C_ENABLED and workflow_key == "wf_pore_c"
        workflow_family = workflow_key
        if workflow_family == "wf_pore_c" and not wf_pore_c_enabled:
            workflow_family = "dogme"

        if workflow_family in {"reconcile_bams", "haplotype_with_vcf", "wf_pore_c"}:
            analysis_skill = "analyze_job_results"
        else:
            analysis_skill = mode_skill_map.get(str(mode or "").upper(), "analyze_job_results")

        # 4. Build data context string for the LLM
        data_context = _build_auto_analysis_context(
            sample_name, mode, run_uuid, summary_data, parsed_csvs,
            wf_pore_c_enabled=wf_pore_c_enabled,
        )

        # 5. Call the LLM for an intelligent interpretation
        llm_md = ""
        llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        engine = None
        try:
            engine = AgentEngine(model_key=model_key)
            if workflow_family == "wf_pore_c":
                user_prompt = (
                    f"A wf-pore-c job for sample \"{sample_name}\" just completed.\n"
                    f"Work directory: {work_directory}\n\n"
                    f"You are writing the automatic post-run analysis card shown immediately after job completion.\n"
                    f"This should be a substantive first-pass interpretation, not a terse completion note.\n"
                    f"Use only the supplied analysis data, and prefer concrete metrics, artifact presence, warnings, and filenames over generalities.\n\n"
                    f"Here is the analysis summary and key result data:\n\n"
                    f"{data_context}\n\n"
                    f"Write a structured markdown report with these sections when the data support them:\n"
                    f"1. Overall Assessment\n"
                    f"2. Contact Map Outputs\n"
                    f"3. pairs.stats.txt Metrics\n"
                    f"4. Missing Outputs or Warnings\n"
                    f"5. Recommended Next Steps\n"
                    f"6. Notable Output Files\n\n"
                    f"Be explicit about requested outputs that are present or missing, mention the revision, reference, cutter, and sample alias when available, "
                    f"and clearly state when metrics are sparse or incomplete."
                )
            elif workflow_family == "reconcile_bams":
                user_prompt = (
                    f"A reconcile_bams workflow for sample \"{sample_name}\" just completed.\n"
                    f"Work directory: {work_directory}\n\n"
                    f"You are writing the automatic post-run analysis card shown immediately after job completion.\n"
                    f"This should be a substantive first-pass interpretation, not a terse completion note.\n"
                    f"Use only the supplied analysis data, and prefer concrete artifact presence, manifest details, references, warnings, and filenames over generalities.\n\n"
                    f"Here is the analysis summary and key result data:\n\n"
                    f"{data_context}\n\n"
                    f"Write a structured markdown report with these sections when the data support them:\n"
                    f"1. Overall Assessment\n"
                    f"2. Reconcile Outputs\n"
                    f"3. Input Manifest Summary\n"
                    f"4. Warnings or Missing Outputs\n"
                    f"5. Recommended Next Steps\n"
                    f"6. Notable Output Files\n\n"
                    f"Be explicit about how many BAMs were reconciled, whether annotation outputs are present, and whether the references or samples look mixed."
                )
            elif workflow_family == "haplotype_with_vcf":
                user_prompt = (
                    f"A haplotype_with_vcf workflow for sample \"{sample_name}\" just completed.\n"
                    f"Work directory: {work_directory}\n\n"
                    f"You are writing the automatic post-run analysis card shown immediately after job completion.\n"
                    f"This should be a substantive first-pass interpretation, not a terse completion note.\n"
                    f"Use only the supplied analysis data, and prefer concrete artifact presence, assignment labels, warnings, and filenames over generalities.\n\n"
                    f"Here is the analysis summary and key result data:\n\n"
                    f"{data_context}\n\n"
                    f"Write a structured markdown report with these sections when the data support them:\n"
                    f"1. Overall Assessment\n"
                    f"2. Haplotyped BAM Outputs\n"
                    f"3. Haplotype Assignment Summaries\n"
                    f"4. Warnings or Missing Outputs\n"
                    f"5. Recommended Next Steps\n"
                    f"6. Notable Output Files\n\n"
                    f"Be explicit about assignment labels, ambiguous BAM outputs, and which summary TSVs are available for follow-up interpretation."
                )
            else:
                user_prompt = (
                    f"A Dogme {mode} job for sample \"{sample_name}\" just completed.\n"
                    f"Work directory: {work_directory}\n\n"
                    f"You are writing the automatic post-run analysis card shown immediately after job completion.\n"
                    f"This should be a substantive first-pass interpretation, not a terse completion note.\n"
                    f"Use only the supplied analysis data, and prefer concrete metrics and filenames over generalities.\n\n"
                    f"Here is the analysis summary and key result data:\n\n"
                    f"{data_context}\n\n"
                    f"Write a structured markdown report with these sections when the data support them:\n"
                    f"1. Overall Assessment\n"
                    f"2. Key Metrics\n"
                    f"3. Reference-Specific Findings (separate subsections if multiple genomes are present)\n"
                    f"4. QC Concerns or Limitations\n"
                    f"5. Recommended Next Steps\n"
                    f"6. Notable Output Files\n\n"
                    f"Call out whether sequencing depth or yield is adequate for downstream interpretation, "
                    f"name any obvious failure modes, and mention the most relevant QC/statistics files explicitly. "
                    f"If the data are sparse, say that clearly, but still explain what can and cannot be concluded."
                )
            llm_md, llm_usage = await run_in_threadpool(
                engine.think,
                user_prompt,
                analysis_skill,
                None,  # no conversation history needed -- data is self-contained
            )
            # Strip any tags the LLM might emit (it shouldn't, but be safe)
            llm_md = re.sub(r"\[\[DATA_CALL:.*?\]\]", "", llm_md)
            llm_md = re.sub(r"\[\[SKILL_SWITCH_TO:.*?\]\]", "", llm_md)
            llm_md = re.sub(r"\[\[APPROVAL_NEEDED\]\]", "", llm_md)
            llm_md = llm_md.strip()
            logger.info("Auto-analysis LLM call succeeded",
                        run_uuid=run_uuid, tokens=llm_usage.get("total_tokens", 0))
        except Exception as llm_err:
            logger.warning("Auto-analysis LLM call failed, using static template",
                           run_uuid=run_uuid, error=str(llm_err))
            llm_md = ""  # fall through to static template below

        # 6. Build the final markdown
        if llm_md:
            # LLM succeeded -- prepend a header and append exploration hints
            _wf_name = work_directory.rstrip("/").rsplit("/", 1)[-1] if work_directory else ""
            if workflow_family == "wf_pore_c":
                workflow_version = ((summary_data.get("workflow_summary") or {}).get("metadata") or {}).get("workflow_version") or "unknown"
                final_md = (
                    f"### Contact Map Analysis: {sample_name}\n"
                    f"**Workflow:** {_wf_name} &nbsp;|&nbsp; "
                    f"**Workflow key:** wf_pore_c &nbsp;|&nbsp; "
                    f"**Revision:** {workflow_version} &nbsp;|&nbsp; "
                    f"**Status:** COMPLETED\n\n"
                    f"{llm_md}\n\n"
                    f"💡 *You can ask me to dive deeper -- for example:*\n"
                    f"- \"Show me the pairs stats\"\n"
                    f"- \"Summarize the contact map outputs\"\n"
                    f"- \"Which requested outputs are missing?\"\n"
                )
            elif workflow_family == "reconcile_bams":
                final_md = (
                    f"### Reconcile Analysis: {sample_name}\n"
                    f"**Workflow:** {_wf_name} &nbsp;|&nbsp; "
                    f"**Workflow key:** reconcile_bams &nbsp;|&nbsp; "
                    f"**Status:** COMPLETED\n\n"
                    f"{llm_md}\n\n"
                    f"You can ask me to dive deeper, for example:\n"
                    f"- \"Show me the reconcile manifest\"\n"
                    f"- \"Which BAM outputs were produced?\"\n"
                    f"- \"Summarize the reconcile report\"\n"
                )
            elif workflow_family == "haplotype_with_vcf":
                final_md = (
                    f"### Haplotype Analysis: {sample_name}\n"
                    f"**Workflow:** {_wf_name} &nbsp;|&nbsp; "
                    f"**Workflow key:** haplotype_with_vcf &nbsp;|&nbsp; "
                    f"**Status:** COMPLETED\n\n"
                    f"{llm_md}\n\n"
                    f"You can ask me to dive deeper, for example:\n"
                    f"- \"Show me the haplotype summary TSV\"\n"
                    f"- \"Summarize per-chromosome haplotype counts\"\n"
                    f"- \"Which BAMs were assigned ambiguously?\"\n"
                )
            else:
                final_md = (
                    f"### 📊 Analysis: {sample_name}\n"
                    f"**Workflow:** {_wf_name} &nbsp;|&nbsp; "
                    f"**Mode:** {mode} &nbsp;|&nbsp; "
                    f"**Status:** COMPLETED\n\n"
                    f"{llm_md}\n\n"
                    f"💡 *You can ask me to dive deeper -- for example:*\n"
                    f"- \"Show me the modification summary\"\n"
                    f"- \"Parse the CSV results\"\n"
                    f"- \"Give me a QC report\"\n"
                )
            _model_name = engine.model_name if engine else "system"
        else:
            # Fallback: static template (same as before)
            final_md = _build_static_analysis_summary(
                sample_name, mode, run_uuid, summary_data,
                work_directory=work_directory,
                wf_pore_c_enabled=wf_pore_c_enabled,
            )
            _model_name = "system"
            llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 7. Create AGENT_PLAN block with the analysis
        _token_payload = {
            **llm_usage,
            "model": _model_name,
        }
        agent_block = _create_block_internal(
            session,
            project_id,
            "AGENT_PLAN",
            {
                "markdown": final_md,
                "skill": analysis_skill,
                "model": _model_name,
                "workflow_plan_block_id": workflow_block.id if workflow_block is not None else None,
                "tokens": _token_payload,
            },
            status="DONE",
            owner_id=owner_id,
        )

        # Save assistant response to conversation history with token tracking
        if owner_id:
            await save_conversation_message(
                session, project_id, owner_id, "assistant", final_md,
                token_data=_token_payload, model_name=_model_name
            )

        logger.info("Auto-analysis block created", run_uuid=run_uuid,
                    skill=analysis_skill, model=_model_name,
                    tokens=llm_usage.get("total_tokens", 0))

        if workflow_block is not None:
            _set_workflow_step_status(
                session,
                workflow_block,
                "analyze_results",
                "COMPLETED",
                extra={"run_uuid": run_uuid},
            )

        return agent_block

    except Exception as e:
        logger.error("Auto-trigger analysis failed", run_uuid=run_uuid, error=str(e))
        if "workflow_block" in locals() and workflow_block is not None:
            _set_workflow_step_status(
                session,
                workflow_block,
                "analyze_results",
                "FAILED",
                extra={"run_uuid": run_uuid, "error": str(e)},
            )
            return None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Staging task polling
# ---------------------------------------------------------------------------

async def poll_staging_status(
    task_id: str,
    project_id: str,
    block_id: str,
    owner_id: str | None,
    *,
    job_data: dict,
    ssh_profile_id: str,
    ssh_profile_nickname: str | None,
    workflow_block_id: str | None = None,
    gate_block_id: str | None = None,
    stage_input_step_id: str | None = None,
    complete_stage_only_step_id: str | None = None,
    gate_payload: dict | None = None,
    initial_stage_parts: dict | None = None,
):
    """
    Background task to poll Launchpad for staging progress and update the STAGING_TASK block.
    Mirrors poll_job_status() with an adaptive schedule tuned for transfers.
    """
    from cortex.remote_orchestration import (
        _cancelled_stage_parts,
        _failed_stage_parts,
        _final_stage_parts,
        _find_workflow_plan,
        _make_stage_part,
        _resolve_workflow_step_id,
        _set_workflow_step_status,
        _stage_part_progress,
        _update_project_block_payload,
    )

    _POLL_SCHEDULE = [
        (40, 3),     # first 2 min   -> every 3 s
        (60, 10),    # next 10 min   -> every 10 s
        (120, 30),   # next 60 min   -> every 30 s
        (840, 30),   # next ~7 h     -> every 30 s
    ]

    gate_payload = gate_payload or {}
    stage_parts = dict(initial_stage_parts) if initial_stage_parts else {}
    _done = False
    _consecutive_not_found = 0
    _NOT_FOUND_THRESHOLD = 3  # consecutive 404s before declaring task lost

    for _batch_polls, _interval in _POLL_SCHEDULE:
        if _done:
            break
        for _ in range(_batch_polls):
            if _done:
                break
            await asyncio.sleep(_interval)

            session = SessionLocal()
            try:
                launchpad_url = get_service_url("launchpad")
                client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
                await client.connect()
                try:
                    status_data = await client.call_tool(
                        "get_staging_task_status", task_id=task_id,
                    )
                finally:
                    await client.disconnect()

                # Successful response — reset 404 counter
                _consecutive_not_found = 0

                if not isinstance(status_data, dict):
                    logger.warning("Bad staging poll response", task_id=task_id)
                    continue

                task_status = status_data.get("status", "unknown")
                progress = status_data.get("progress") or {}
                stage_result = status_data.get("result")
                error_msg = status_data.get("error")
                transfer_progress = dict(progress) if isinstance(progress, dict) else {}

                # Derive a human-friendly progress message
                pct = progress.get("file_percent", 0)
                speed = progress.get("speed", "")
                xfr = progress.get("files_transferred", 0)
                total = progress.get("files_total", 0)

                if task_status == "queued":
                    wait_reason = str(progress.get("wait_reason") or "").strip()
                    msg = wait_reason or "Staging is queued and waiting to resume."
                    current_progress = stage_parts.get("data", {}).get("progress_percent", 35)
                    try:
                        current_progress = int(current_progress or 35)
                    except (TypeError, ValueError):
                        current_progress = 35

                    if stage_parts.get("data", {}).get("status") != "COMPLETED":
                        stage_parts["data"] = _make_stage_part("PENDING", max(35, current_progress), msg)

                    _update_project_block_payload(
                        session,
                        block_id,
                        {
                            "progress_percent": _stage_part_progress(stage_parts),
                            "message": msg,
                            "stage_parts": stage_parts,
                            "transfer_progress": transfer_progress,
                        },
                        status="RUNNING",
                    )
                    logger.info("Staging queued", task_id=task_id, message=msg)

                elif task_status == "running":
                    if total:
                        msg = f"Uploading {xfr}/{total} files ({pct}% current file) {speed}".strip()
                    elif pct:
                        msg = f"Uploading... {pct}% {speed}".strip()
                    else:
                        msg = "Staging in progress..."

                    if stage_parts.get("data", {}).get("status") != "COMPLETED":
                        stage_parts["data"] = _make_stage_part("RUNNING", max(35, pct), msg)

                    _update_project_block_payload(
                        session,
                        block_id,
                        {
                            "progress_percent": _stage_part_progress(stage_parts),
                            "message": msg,
                            "stage_parts": stage_parts,
                            "transfer_progress": transfer_progress,
                        },
                        status="RUNNING",
                    )
                    logger.debug(
                        "Staging progress", task_id=task_id,
                        pct=pct, files=f"{xfr}/{total}", speed=speed,
                    )

                elif task_status == "completed" and isinstance(stage_result, dict):
                    stage_parts = _final_stage_parts(stage_result, stage_parts)
                    remote_data_path = stage_result.get("remote_data_path", "")

                    _update_project_block_payload(
                        session,
                        block_id,
                        {
                            "status": "COMPLETED",
                            "progress_percent": _stage_part_progress(stage_parts),
                            "message": f"Remote staging complete: {remote_data_path}",
                            "remote_data_path": remote_data_path,
                            "remote_reference_paths": stage_result.get("remote_reference_paths"),
                            "data_cache_status": stage_result.get("data_cache_status"),
                            "reference_cache_statuses": stage_result.get("reference_cache_statuses"),
                            "reference_asset_evidence": stage_result.get("reference_asset_evidence"),
                            "stage_parts": stage_parts,
                            **({"transfer_progress": transfer_progress} if transfer_progress else {}),
                        },
                        status="DONE",
                    )

                    # Update workflow steps
                    if workflow_block_id:
                        workflow_block = session.query(ProjectBlock).filter(
                            ProjectBlock.id == workflow_block_id
                        ).first()
                        if workflow_block and stage_input_step_id:
                            decision = "reuse" if stage_result.get("data_cache_status") == "reused" else "stage"
                            _set_workflow_step_status(
                                session, workflow_block, stage_input_step_id, "COMPLETED",
                                extra={
                                    "decision": decision,
                                    "staged_input_directory": remote_data_path,
                                    "reference_cache_statuses": stage_result.get("reference_cache_statuses"),
                                },
                            )
                        if workflow_block and complete_stage_only_step_id:
                            _set_workflow_step_status(
                                session, workflow_block, complete_stage_only_step_id, "COMPLETED",
                                extra={"staged_input_directory": remote_data_path},
                            )

                    # Completion announcement
                    _create_block_internal(
                        session,
                        project_id,
                        "AGENT_PLAN",
                        {
                            "markdown": (
                                f"### Remote staging complete\n\n"
                                f"Sample `{job_data.get('sample_name', '')}` is staged on "
                                f"`{ssh_profile_nickname or ssh_profile_id}` at `{remote_data_path}`."
                            ),
                            "skill": gate_payload.get("skill", "remote_execution"),
                            "model": gate_payload.get("model", "default"),
                            "stage_result": stage_result,
                            "workflow_plan_block_id": workflow_block_id,
                        },
                        status="DONE",
                        owner_id=owner_id,
                    )

                    if gate_block_id and not complete_stage_only_step_id:
                        gate_block = session.query(ProjectBlock).filter(
                            ProjectBlock.id == gate_block_id
                        ).first()
                        if gate_block is not None:
                            continue_payload = get_block_payload(gate_block)
                            params_key = "edited_params" if isinstance(continue_payload.get("edited_params"), dict) else "extracted_params"
                            continue_params = dict(continue_payload.get(params_key) or {})
                            continue_params["staged_remote_input_path"] = remote_data_path
                            if workflow_block_id:
                                continue_params["workflow_block_id"] = workflow_block_id
                            if not continue_params.get("result_destination"):
                                continue_params["result_destination"] = job_data.get("result_destination") or "both"

                            cache_preflight = continue_params.get("cache_preflight")
                            if isinstance(cache_preflight, dict):
                                updated_preflight = dict(cache_preflight)
                                data_action = dict(updated_preflight.get("data_action") or {})
                                if remote_data_path:
                                    data_action["remote_path"] = remote_data_path
                                updated_preflight["data_action"] = data_action
                                continue_params["cache_preflight"] = updated_preflight

                            continue_payload[params_key] = continue_params
                            gate_block.payload_json = json.dumps(continue_payload)
                            session.commit()

                            from cortex.workflow_submission import submit_job_after_approval

                            asyncio.create_task(submit_job_after_approval(project_id, gate_block_id))
                            logger.info(
                                "Continuing remote workflow after staging",
                                task_id=task_id,
                                project_id=project_id,
                                gate_block_id=gate_block_id,
                                remote_data_path=remote_data_path,
                            )

                    logger.info("Staging completed", task_id=task_id, project_id=project_id)
                    _done = True
                    break

                elif task_status == "failed":
                    fail_msg = error_msg or "Remote staging failed"
                    stage_parts = _failed_stage_parts(stage_parts, fail_msg)
                    _update_project_block_payload(
                        session,
                        block_id,
                        {
                            "status": "FAILED",
                            "progress_percent": _stage_part_progress(stage_parts),
                            "message": fail_msg,
                            "error": fail_msg,
                            "stage_parts": stage_parts,
                            **({"transfer_progress": transfer_progress} if transfer_progress else {}),
                        },
                        status="FAILED",
                    )

                    if workflow_block_id and stage_input_step_id:
                        workflow_block = session.query(ProjectBlock).filter(
                            ProjectBlock.id == workflow_block_id
                        ).first()
                        if workflow_block:
                            _set_workflow_step_status(
                                session, workflow_block, stage_input_step_id, "FAILED",
                                extra={"error": fail_msg},
                            )

                    logger.error("Staging failed", task_id=task_id, error=fail_msg)
                    _done = True
                    break

                elif task_status == "cancelled":
                    cancel_msg = error_msg or "Remote staging cancelled by user."
                    stage_parts = _cancelled_stage_parts(stage_parts, cancel_msg)
                    _update_project_block_payload(
                        session,
                        block_id,
                        {
                            "status": "CANCELLED",
                            "progress_percent": _stage_part_progress(stage_parts),
                            "message": cancel_msg,
                            "error": None,
                            "stage_parts": stage_parts,
                        },
                        status="CANCELLED",
                    )

                    if workflow_block_id:
                        workflow_block = session.query(ProjectBlock).filter(
                            ProjectBlock.id == workflow_block_id
                        ).first()
                        if workflow_block and stage_input_step_id:
                            _set_workflow_step_status(
                                session, workflow_block, stage_input_step_id, "CANCELLED",
                                extra={"error": cancel_msg},
                            )
                        if workflow_block and complete_stage_only_step_id:
                            _set_workflow_step_status(
                                session, workflow_block, complete_stage_only_step_id, "CANCELLED",
                                extra={"error": cancel_msg},
                            )

                    logger.info("Staging cancelled", task_id=task_id, message=cancel_msg)
                    _done = True
                    break

            except Exception as e:
                err_str = str(e)
                # Detect 404 — task_id vanished (Launchpad restart or cleanup)
                if "HTTP 404" in err_str or "not found" in err_str.lower():
                    _consecutive_not_found += 1
                    logger.warning(
                        "Staging task not found",
                        task_id=task_id,
                        consecutive=_consecutive_not_found,
                    )
                    if _consecutive_not_found >= _NOT_FOUND_THRESHOLD:
                        fail_msg = (
                            "Staging task lost — Launchpad may have restarted during the transfer. "
                            "Partial files are preserved on the remote profile in .rsync-partial. "
                            "Re-submit the staging request to resume."
                        )
                        stage_parts = _failed_stage_parts(stage_parts, fail_msg)
                        _update_project_block_payload(
                            session,
                            block_id,
                            {
                                "status": "FAILED",
                                "progress_percent": _stage_part_progress(stage_parts),
                                "message": fail_msg,
                                "error": fail_msg,
                                "stage_parts": stage_parts,
                                **({"transfer_progress": transfer_progress} if transfer_progress else {}),
                            },
                            status="FAILED",
                        )
                        if workflow_block_id and stage_input_step_id:
                            workflow_block = session.query(ProjectBlock).filter(
                                ProjectBlock.id == workflow_block_id
                            ).first()
                            if workflow_block:
                                _set_workflow_step_status(
                                    session, workflow_block, stage_input_step_id, "FAILED",
                                    extra={"error": fail_msg},
                                )
                        logger.error("Staging task lost after consecutive 404s", task_id=task_id)
                        _done = True
                        break
                else:
                    logger.warning("Error polling staging task", task_id=task_id, error=err_str)
            finally:
                session.close()

    if not _done:
        # Schedule exhausted without terminal state — fail the block so it
        # doesn't stay stuck at RUNNING forever.
        session = SessionLocal()
        try:
            fail_msg = (
                "Staging status polling timed out after the maximum polling window. "
                "The transfer may still be running on the remote host. "
                "Check the remote profile or re-submit to resume."
            )
            stage_parts = _failed_stage_parts(stage_parts, fail_msg)
            _update_project_block_payload(
                session,
                block_id,
                {
                    "status": "FAILED",
                    "progress_percent": _stage_part_progress(stage_parts),
                    "message": fail_msg,
                    "error": fail_msg,
                    "stage_parts": stage_parts,
                },
                status="FAILED",
            )
            if workflow_block_id and stage_input_step_id:
                workflow_block = session.query(ProjectBlock).filter(
                    ProjectBlock.id == workflow_block_id
                ).first()
                if workflow_block:
                    _set_workflow_step_status(
                        session, workflow_block, stage_input_step_id, "FAILED",
                        extra={"error": fail_msg},
                    )
        except Exception:
            logger.error("Failed to mark staging block as failed on schedule exhaustion", task_id=task_id, exc_info=True)
        finally:
            session.close()
        logger.warning("Stopped polling staging task (schedule exhausted)", task_id=task_id)
