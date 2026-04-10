import asyncio
import datetime
import json
import re

from fastapi.concurrency import run_in_threadpool

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.agent_engine import AgentEngine
from cortex.analysis_helpers import _build_auto_analysis_context, _build_static_analysis_summary
from cortex.config import get_service_url
from cortex.db import SessionLocal
from cortex.db_helpers import _create_block_internal, save_conversation_message
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock
from cortex.remote_orchestration import _find_workflow_plan, _resolve_workflow_step_id, _set_workflow_step_status
from cortex.task_service import sync_project_tasks

logger = get_logger(__name__)

_TRAILING_PATH_JUNK = re.compile(r'(?:\\n|[^a-zA-Z0-9/_.\-~])+$')


def _sanitize_work_directory(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _TRAILING_PATH_JUNK.sub('', str(value).strip())
    return cleaned or None


async def poll_job_status(project_id: str, block_id: str, run_uuid: str):
    """
    Background task to poll Launchpad for job status via MCP and update the EXECUTION_JOB block.
    Continues until job is completed or failed.
    """

    # Adaptive polling: fast at first (3 s) then slow down to 30 s.
    # Total coverage: ~10 h, which is enough for the longest pipelines.
    _POLL_SCHEDULE = [
        (120, 3),    # first 6 min  -> every 3 s  (120 polls)
        (120, 10),   # next 20 min  -> every 10 s
        (120, 30),   # next 60 min  -> every 30 s
        (960, 30),   # next ~8 h    -> every 30 s  (960 polls)
    ]
    _job_done = False

    for _batch_polls, _interval in _POLL_SCHEDULE:
        if _job_done:
            break
        for _ in range(_batch_polls):
            if _job_done:
                break
            await asyncio.sleep(_interval)

            session = SessionLocal()
            try:
                # Get current status from Launchpad via MCP
                launchpad_url = get_service_url("launchpad")
                client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
                await client.connect()
                try:
                    status_data = await client.call_tool("check_nextflow_status", run_uuid=run_uuid)
                    logs_data = await client.call_tool("get_job_logs", run_uuid=run_uuid, limit=50)
                finally:
                    await client.disconnect()

                if not isinstance(status_data, dict):
                    logger.warning("Failed to get status", run_uuid=run_uuid)
                    continue

                logs = logs_data.get("logs", []) if isinstance(logs_data, dict) else []

                # Update the block with new data
                block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
                if block:
                    # Create new payload dict to ensure SQLAlchemy detects the change
                    payload = get_block_payload(block)
                    resolved_work_directory = _resolved_job_work_directory(payload.get("work_directory"), status_data)
                    if resolved_work_directory:
                        status_data = dict(status_data)
                        status_data["work_directory"] = resolved_work_directory
                    payload["job_status"] = status_data
                    if resolved_work_directory:
                        payload["work_directory"] = resolved_work_directory
                    payload["logs"] = logs
                    payload["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"

                    # Update block status based on job status
                    job_status = status_data.get("status", "UNKNOWN")
                    completed_ready_for_analysis = _completed_job_results_ready(status_data)
                    if job_status == "COMPLETED" and completed_ready_for_analysis:
                        block.status = "DONE"
                    elif job_status == "FAILED":
                        block.status = "FAILED"
                    else:
                        block.status = "RUNNING"

                    # Reassign payload_json to trigger SQLAlchemy update
                    block.payload_json = json.dumps(payload)
                    session.commit()
                    session.refresh(block)
                    sync_project_tasks(session, project_id)

                    logger.info("Job status updated", run_uuid=run_uuid, job_status=job_status, progress=status_data.get("progress_percent", 0))

                    # Stop polling if job is done
                    if job_status == "FAILED" or (job_status == "COMPLETED" and completed_ready_for_analysis):
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
                                "COMPLETED" if job_status == "COMPLETED" else "FAILED",
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
    project_id: str, run_uuid: str, job_payload: dict, owner_id: str | None
):
    """
    Automatically analyse a just-completed Dogme job.

    1. Fetches the analysis summary (file listing) from Analyzer.
    2. Parses key CSV result files (final_stats, qc_summary) via Analyzer MCP.
    3. Passes everything to the LLM for an intelligent first interpretation.
    4. Saves the LLM response as an AGENT_PLAN block with token tracking.

    Falls back to a static template if the LLM call fails.
    """
    sample_name = job_payload.get("sample_name", "Unknown")
    mode = job_payload.get("mode", "DNA")
    model_key = job_payload.get("model", "default")
    work_directory = job_payload.get("work_directory", "")

    logger.info("Auto-triggering analysis", run_uuid=run_uuid,
                sample_name=sample_name, mode=mode, model=model_key)

    session = SessionLocal()
    try:
        workflow_block = _find_workflow_plan(session, project_id, run_uuid=run_uuid)
        if workflow_block is not None:
            workflow_payload = get_block_payload(workflow_block)
            if workflow_payload.get("next_step") != "analyze_results":
                logger.info("Skipping auto-analysis because analysis is not the next todo", run_uuid=run_uuid)
                return
            _set_workflow_step_status(
                session,
                workflow_block,
                "analyze_results",
                "RUNNING",
                extra={"run_uuid": run_uuid},
            )

        # 1. Create a system message announcing the transition
        _create_block_internal(
            session,
            project_id,
            "USER_MESSAGE",
            {"text": f"Job \"{sample_name}\" completed. Analyze the results."},
            owner_id=owner_id,
        )

        # Also save to conversation history so the LLM sees it
        if owner_id:
            await save_conversation_message(
                session, project_id, owner_id, "user",
                f"Job \"{sample_name}\" completed. Analyze the results."
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

        # 3. Map mode -> Dogme analysis skill
        mode_skill_map = {
            "DNA": "run_dogme_dna",
            "RNA": "run_dogme_rna",
            "CDNA": "run_dogme_cdna",
        }
        analysis_skill = mode_skill_map.get(mode.upper(), "analyze_job_results")

        # 4. Build data context string for the LLM
        data_context = _build_auto_analysis_context(
            sample_name, mode, run_uuid, summary_data, parsed_csvs
        )

        # 5. Call the LLM for an intelligent interpretation
        llm_md = ""
        llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        engine = None
        try:
            engine = AgentEngine(model_key=model_key)
            user_prompt = (
                f"A Dogme {mode} job for sample \"{sample_name}\" just completed.\n"
                f"Work directory: {work_directory}\n\n"
                f"Here is the analysis summary and key result data:\n\n"
                f"{data_context}\n\n"
                f"Provide a concise interpretation of these results. "
                f"Highlight key metrics, any QC concerns, and suggest "
                f"next steps the user could explore."
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
            )
            _model_name = "system"
            llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 7. Create AGENT_PLAN block with the analysis
        _token_payload = {
            **llm_usage,
            "model": _model_name,
        }
        _create_block_internal(
            session,
            project_id,
            "AGENT_PLAN",
            {
                "markdown": final_md,
                "skill": analysis_skill,
                "model": _model_name,
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

                # Derive a human-friendly progress message
                pct = progress.get("file_percent", 0)
                speed = progress.get("speed", "")
                xfr = progress.get("files_transferred", 0)
                total = progress.get("files_total", 0)

                if task_status == "running":
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

                    logger.info("Staging completed", task_id=task_id, project_id=project_id)
                    _done = True
                    break

                elif task_status == "failed":
                    stage_parts = _failed_stage_parts(stage_parts)
                    fail_msg = error_msg or "Remote staging failed"
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

                    logger.error("Staging failed", task_id=task_id, error=fail_msg)
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
                            "Partial files are preserved on the remote profile (rsync --partial). "
                            "Re-submit the staging request to resume."
                        )
                        stage_parts = _failed_stage_parts(stage_parts)
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
            stage_parts = _failed_stage_parts(stage_parts)
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
