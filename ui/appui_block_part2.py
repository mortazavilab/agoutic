import datetime
import os

import streamlit as st
from components.cards import info_callout, metadata_row, section_header, status_chip
from components.progress import progress_stats, segmented_progress, stepper, timeline


_STAGING_UI_STALE_SECONDS = float(os.getenv("STAGING_UI_STALE_SECONDS", "90"))
_STAGING_UI_ACTIVE_REFRESH_SECONDS = float(os.getenv("STAGING_UI_ACTIVE_REFRESH_SECONDS", "8"))
_STAGING_UI_REFRESH_RETRY_SECONDS = float(os.getenv("STAGING_UI_REFRESH_RETRY_SECONDS", "5"))


def _parse_stage_timestamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)
    raw_value = str(value).strip()
    if not raw_value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _staging_block_needs_refresh(block: dict, content: dict) -> bool:
    staging_task_id = content.get("staging_task_id")
    if not staging_task_id:
        return False
    updated_at = _parse_stage_timestamp(
        content.get("last_updated") or content.get("updated_at") or block.get("created_at")
    )
    if updated_at is None:
        return False
    thresholds: list[float] = []
    if _STAGING_UI_STALE_SECONDS > 0:
        thresholds.append(_STAGING_UI_STALE_SECONDS)

    block_status = str(block.get("status") or "").upper()
    transfer_progress = content.get("transfer_progress")
    has_transfer_progress = isinstance(transfer_progress, dict) and bool(transfer_progress)
    if block_status == "RUNNING" and _STAGING_UI_ACTIVE_REFRESH_SECONDS > 0:
        thresholds.append(_STAGING_UI_ACTIVE_REFRESH_SECONDS)
        if not has_transfer_progress:
            thresholds.append(min(_STAGING_UI_ACTIVE_REFRESH_SECONDS, 5.0))

    if not thresholds:
        return False
    age_seconds = (datetime.datetime.now(datetime.timezone.utc) - updated_at).total_seconds()
    return age_seconds >= min(thresholds)


def _refresh_staging_block_status(*, staging_task_id, block_id, project_id, api_url, request_fn, pause_refresh):
    refresh_key = f"_staging_refresh_attempt_{staging_task_id}"
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    last_attempt = float(st.session_state.get(refresh_key, 0.0) or 0.0)
    if now_ts - last_attempt < _STAGING_UI_REFRESH_RETRY_SECONDS:
        return False, None

    st.session_state[refresh_key] = now_ts
    pause_refresh(4)
    try:
        resp = request_fn(
            "POST",
            f"{api_url}/remote/stage/{staging_task_id}/refresh",
            json={"project_id": project_id, "block_id": block_id},
            timeout=15,
        )
    except Exception as exc:
        return False, f"Error refreshing staging status: {exc}"

    if resp.status_code == 200:
        return True, None

    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    return False, detail or getattr(resp, "text", "")[:200] or "Unable to refresh staging status."


def _format_stage_bytes(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit_index = 0
    while amount >= 1024 and unit_index < len(units) - 1:
        amount /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(amount)} {units[unit_index]}"
    if amount >= 100:
        return f"{amount:.0f} {units[unit_index]}"
    if amount >= 10:
        return f"{amount:.1f} {units[unit_index]}"
    return f"{amount:.2f} {units[unit_index]}"


def _render_stage_transfer_snapshot(transfer_progress: dict | None):
    if not isinstance(transfer_progress, dict) or not transfer_progress:
        return

    current_file = str(transfer_progress.get("current_file") or "").strip()
    current_bytes = _format_stage_bytes(transfer_progress.get("current_file_bytes_transferred"))
    current_total = _format_stage_bytes(transfer_progress.get("current_file_total_bytes_estimate"))
    overall_transferred = _format_stage_bytes(transfer_progress.get("overall_bytes_transferred"))
    overall_total = _format_stage_bytes(transfer_progress.get("overall_bytes_total"))
    speed = str(transfer_progress.get("speed") or "").strip()

    files_transferred = transfer_progress.get("files_transferred")
    files_total = transfer_progress.get("files_total")
    try:
        files_label = (
            f"{int(files_transferred or 0)}/{int(files_total)}"
            if files_total not in (None, "") else None
        )
    except (TypeError, ValueError):
        files_label = None

    stats = {}
    if overall_transferred and overall_total:
        stats["Transferred"] = f"{overall_transferred} / {overall_total}"
    elif overall_total:
        stats["Total Input"] = overall_total

    if current_bytes and current_total:
        stats["Current File"] = f"{current_bytes} / ~{current_total}"
    elif current_bytes:
        stats["Current File"] = current_bytes
    elif current_total:
        stats["Current File"] = f"~{current_total}"

    if files_label:
        stats["Files"] = files_label
    if speed:
        stats["Speed"] = speed

    if stats:
        progress_stats(stats)
    if current_file:
        st.caption(f"Current file: `{current_file}`")


def render_block_part2(
    *,
    btype,
    block,
    content,
    block_id,
    status,
    API_URL,
    active_id,
    LIVE_JOB_STATUS_TIMEOUT_SECONDS,
    make_authenticated_request,
    show_metadata,
    _workflow_status_presentation,
    _format_plan_timestamp,
    _format_duration,
    _block_timestamp,
    _render_workflow_plot_payload,
    _render_embedded_dataframes,
    _render_step_payload,
    _job_status_updated_at,
    _run_status_label,
    _format_timestamp,
    _workflow_label_from_path,
    _pause_auto_refresh,
    get_job_debug_info,
    _render_plot_block,
):
    handled = False
    if btype == "WORKFLOW_PLAN":
        handled = True
        with st.chat_message("assistant", avatar="🗺️"):
            section_header("Workflow Plan", "Structured execution outline", icon="🗺️")
            show_metadata()
            st.divider()

            plan_status = str(content.get("status") or status or "PENDING")
            # Mark plan failures so "try again" can replay the prompt.
            if plan_status.strip().upper() == "FAILED":
                st.session_state["_last_prompt_failed"] = True
            chip_kind, chip_label, chip_icon = _workflow_status_presentation(plan_status)
            status_chip(chip_kind, label=chip_label, icon=chip_icon)

            summary = content.get("summary") or content.get("title") or content.get("label")
            steps = content.get("steps")
            planner_meta = content.get("planner_metadata") if isinstance(content.get("planner_metadata"), dict) else {}
            step_count = len(steps) if isinstance(steps, list) else 0
            current_step_id = content.get("current_step_id")
            current_step = None
            if isinstance(steps, list) and isinstance(current_step_id, str):
                current_step = next(
                    (step for step in steps if isinstance(step, dict) and step.get("id") == current_step_id),
                    None,
                )

            metadata = {"Steps": step_count}
            if planner_meta:
                planning_mode = planner_meta.get("planning_mode") or "deterministic"
                metadata["Planner"] = str(planning_mode).replace("_", " ").title()
                if planner_meta.get("hybrid_attempted"):
                    metadata["Hybrid"] = "Succeeded" if planner_meta.get("hybrid_succeeded") else "Fallback"
            if current_step is not None:
                metadata["Current Step"] = current_step.get("title") or current_step.get("id") or current_step_id
            plan_started = _format_plan_timestamp(content.get("started_at"))
            plan_completed = _format_plan_timestamp(content.get("completed_at"))
            plan_duration = _format_duration(content.get("started_at"), content.get("completed_at"))
            if plan_started:
                metadata["Started"] = plan_started
            if plan_completed:
                metadata["Completed"] = plan_completed
            if plan_duration:
                metadata["Duration"] = plan_duration
            created_at = _block_timestamp()
            if created_at:
                metadata["Created"] = created_at
            metadata_row(metadata)

            fallback_reason = planner_meta.get("reason_code") if planner_meta else ""
            if fallback_reason:
                st.caption(f"Planner fallback reason: {fallback_reason}")

            if summary:
                st.markdown(f"**{summary}**")

            if current_step is not None and plan_status.strip().upper() in {"RUNNING", "FOLLOW_UP", "WAITING_APPROVAL"}:
                st.caption(
                    f"Current step: {current_step.get('title') or current_step.get('id') or current_step_id}"
                )

            if isinstance(steps, list) and steps:
                for idx, step in enumerate(steps, start=1):
                    if isinstance(step, dict):
                        title = step.get("title") or step.get("name") or f"Step {idx}"
                        detail = step.get("description") or step.get("details") or ""
                        step_id = step.get("id")
                        step_status = str(step.get("status") or "PENDING")
                        step_kind, step_label, step_icon = _workflow_status_presentation(step_status)
                        is_current = isinstance(step_id, str) and step_id == current_step_id
                        expander_title = f"{idx}. {title}"
                        if is_current:
                            expander_title += " · current"
                        expanded = is_current or step_status.strip().upper() in {"RUNNING", "FAILED", "FOLLOW_UP", "WAITING_APPROVAL"}

                        with st.expander(expander_title, expanded=expanded):
                            status_chip(step_kind, label=step_label, icon=step_icon)

                            step_meta = {"Kind": step.get("kind") or "workflow_step"}
                            if step_id:
                                step_meta["Step ID"] = step_id
                            step_meta["Execution"] = "Approval Required" if step.get("requires_approval") else "Safe Auto-Execute"

                            started_at = _format_plan_timestamp(step.get("started_at"))
                            completed_at = _format_plan_timestamp(step.get("completed_at"))
                            duration = _format_duration(step.get("started_at"), step.get("completed_at"))
                            if started_at:
                                step_meta["Started"] = started_at
                            if completed_at:
                                step_meta["Completed"] = completed_at
                            if duration:
                                step_meta["Duration"] = duration
                            metadata_row(step_meta)

                            depends_on = step.get("depends_on")
                            if isinstance(depends_on, list) and depends_on:
                                st.caption(f"Depends on: {', '.join(str(dep) for dep in depends_on)}")

                            if detail:
                                st.caption(detail)

                            error = step.get("error")
                            if error not in (None, ""):
                                info_callout(str(error), kind="error", icon="❌")

                            result = step.get("result")
                            if result not in (None, "", [], {}):
                                st.markdown("**Result**")
                                rendered_result = result
                                if isinstance(result, dict):
                                    _render_workflow_plot_payload(result, block_id, f"step_{idx}")
                                    result_markdown = result.get("markdown")
                                    if isinstance(result_markdown, str) and result_markdown.strip():
                                        st.markdown(result_markdown)
                                    result_dfs = result.get("_dataframes")
                                    if isinstance(result_dfs, dict) and result_dfs:
                                        _render_embedded_dataframes(result_dfs, f"{block_id}_step_{idx}")
                                    post_dataframe_markdown = result.get("post_dataframe_markdown")
                                    if isinstance(post_dataframe_markdown, str) and post_dataframe_markdown.strip():
                                        st.markdown(post_dataframe_markdown)
                                    rendered_result = {
                                        key: value for key, value in result.items()
                                        if key not in {"markdown", "_dataframes", "post_dataframe_markdown"}
                                    }
                                if rendered_result not in (None, "", [], {}):
                                    _render_step_payload(rendered_result)
                    else:
                        st.markdown(f"{idx}. {step}")

            markdown = content.get("markdown")
            if markdown:
                st.markdown(markdown)

            if isinstance(steps, list) and steps:
                with st.expander("Plan Payload", expanded=False):
                    st.json(content)

            if not summary and not markdown and not (isinstance(steps, list) and steps):
                with st.expander("Plan Payload", expanded=False):
                    st.json(content)

    elif btype == "STAGING_TASK":
        handled = True
        with st.chat_message("assistant", avatar="📤"):
            sample_name = content.get("sample_name", "Unknown")
            mode = content.get("mode", "Unknown")
            message = content.get("message", "Preparing remote staging...")
            progress = int(content.get("progress_percent", 0) or 0)
            remote_profile = content.get("ssh_profile_nickname") or content.get("ssh_profile_id") or "remote profile"
            remote_data_path = content.get("remote_data_path", "")
            stage_parts = content.get("stage_parts") or {}
            refs_part = stage_parts.get("references") or {}
            data_part = stage_parts.get("data") or {}
            refs_state = (refs_part.get("status") or "").upper()
            data_state = (data_part.get("status") or "").upper()

            _step_current = 0
            _step_completed = []
            _step_failed = []
            if refs_state == "COMPLETED":
                _step_completed.append(0)
            if data_state == "COMPLETED":
                _step_completed.append(1)

            if refs_state in {"FAILED", "CANCELLED"}:
                _step_current = 0
                _step_failed.append(0)
            elif data_state in {"FAILED", "CANCELLED"}:
                _step_current = 1
                _step_failed.append(1)
            elif data_state == "RUNNING":
                _step_current = 1
            elif refs_state == "RUNNING":
                _step_current = 0
            elif block.get("status") == "DONE":
                _step_current = 1
                _step_completed = [0, 1]
            elif block.get("status") in {"FAILED", "CANCELLED"}:
                _step_current = 1 if 0 in _step_completed else 0
                if 0 in _step_completed and 1 not in _step_failed:
                    _step_failed.append(1)
                elif 0 not in _step_failed:
                    _step_failed.append(0)
            elif progress >= 50:
                _step_current = 1

            section_header(f"Remote Staging · {sample_name}", "Reference and data staging progress", icon="📤")
            metadata_row({"Mode": mode, "Target": remote_profile, "Progress": f"{max(min(progress, 100), 0)}%"})
            stepper(["References", "Sample Data"], current=_step_current, completed=_step_completed, failed=_step_failed)

            def _render_stage_part(label: str, part: dict):
                state = (part.get("status") or "PENDING").upper()
                part_progress = max(min(int(part.get("progress_percent", 0) or 0), 100), 0)
                part_message = part.get("message", "")
                details = part.get("details") or []

                st.write(f"**{label}**")
                st.progress(part_progress / 100)
                if state == "COMPLETED":
                    st.caption(f"✅ {part_message}")
                elif state == "FAILED":
                    st.error(part_message)
                elif state == "CANCELLED":
                    st.warning(part_message)
                elif state == "RUNNING":
                    st.caption(f"🔄 {part_message}")
                else:
                    st.caption(f"⏳ {part_message}")

                if details:
                    for detail in details:
                        detail_message = detail.get("message") or ""
                        if detail_message:
                            st.caption(f"• {detail_message}")

            if block.get("status") == "RUNNING":
                status_chip("running", label="Staging Running", icon="🔄")
                st.caption(message)
                st.progress(max(min(progress, 100), 0) / 100)
                _render_stage_transfer_snapshot(content.get("transfer_progress"))
                staging_task_id = content.get("staging_task_id")
                if _staging_block_needs_refresh(block, content):
                    st.info("Refreshing status before showing staging actions...")
                    _refreshed, _refresh_error = _refresh_staging_block_status(
                        staging_task_id=staging_task_id,
                        block_id=block_id,
                        project_id=active_id,
                        api_url=API_URL,
                        request_fn=make_authenticated_request,
                        pause_refresh=_pause_auto_refresh,
                    )
                    if _refreshed:
                        st.rerun()
                    if _refresh_error:
                        st.caption(_refresh_error)
                elif staging_task_id and st.button("🛑 Cancel Staging", type="primary", key=f"cancel_stage_{block_id}"):
                    _pause_auto_refresh(4)
                    try:
                        _cancel_resp = make_authenticated_request(
                            "POST",
                            f"{API_URL}/remote/stage/{staging_task_id}/cancel",
                            json={"project_id": active_id, "block_id": block_id},
                            timeout=15,
                        )
                        if _cancel_resp.status_code == 200:
                            _cancel_data = _cancel_resp.json()
                            _cancel_status = str(_cancel_data.get("status") or "").strip().lower()
                            _cancel_message = _cancel_data.get("message", "Staging cancelled.")
                            if _cancel_status == "failed":
                                st.warning(_cancel_message)
                            elif _cancel_status == "completed":
                                st.info(_cancel_message)
                            else:
                                st.success(_cancel_message)
                            st.rerun()
                        elif _cancel_resp.status_code == 404:
                            st.warning("Staging task not found. It may have already finished or the server was restarted.")
                        elif _cancel_resp.status_code == 409:
                            st.warning(_cancel_resp.json().get("detail", "Cannot cancel staging task."))
                        else:
                            st.error(f"Cancel failed: {_cancel_resp.status_code} — {_cancel_resp.text[:200]}")
                    except Exception as _ce:
                        st.error(f"Error cancelling staging task: {_ce}")
                _render_stage_part("References", stage_parts.get("references") or {
                    "status": "RUNNING",
                    "progress_percent": 40,
                    "message": "Staging reference assets on the remote profile...",
                })
                _render_stage_part("Data", stage_parts.get("data") or {
                    "status": "PENDING",
                    "progress_percent": 0,
                    "message": "Waiting for reference staging to finish.",
                })
            elif block.get("status") == "DONE":
                status_chip("complete", label="Staging Complete", icon="✅")
                st.caption(message)
                _render_stage_part("References", stage_parts.get("references") or {
                    "status": "COMPLETED",
                    "progress_percent": 100,
                    "message": "Reference assets are ready.",
                })
                _render_stage_part("Data", stage_parts.get("data") or {
                    "status": "COMPLETED",
                    "progress_percent": 100,
                    "message": "Sample data is ready.",
                })
                if remote_data_path:
                    st.caption(f"Remote data path: `{remote_data_path}`")
                ref_statuses = content.get("reference_cache_statuses") or {}
                if ref_statuses:
                    with st.expander("Reference cache status", expanded=False):
                        st.json(ref_statuses)
            elif block.get("status") == "CANCELLED":
                status_chip("cancelled", label="Staging Cancelled", icon="🛑")
                st.warning(message)
                _render_stage_transfer_snapshot(content.get("transfer_progress"))
                staging_task_id = content.get("staging_task_id")
                if staging_task_id and st.button("🔄 Resume Staging", type="primary", key=f"resume_stage_{block_id}"):
                    _pause_auto_refresh(4)
                    try:
                        _resume_resp = make_authenticated_request(
                            "POST",
                            f"{API_URL}/remote/stage/{staging_task_id}/resume",
                            json={"project_id": active_id, "block_id": block_id},
                            timeout=15,
                        )
                        if _resume_resp.status_code == 200:
                            st.success("Resuming staging from the existing cache path.")
                            st.rerun()
                        elif _resume_resp.status_code == 404:
                            st.warning("Staging task not found. It may have already been cleaned up.")
                        elif _resume_resp.status_code in {409, 429}:
                            st.warning(_resume_resp.json().get("detail", "Cannot resume staging task."))
                        else:
                            st.error(f"Resume failed: {_resume_resp.status_code} — {_resume_resp.text[:200]}")
                    except Exception as _re:
                        st.error(f"Error resuming staging task: {_re}")
                _render_stage_part("References", stage_parts.get("references") or {
                    "status": "CANCELLED",
                    "progress_percent": 0,
                    "message": "Reference staging cancelled.",
                })
                _render_stage_part("Data", stage_parts.get("data") or {
                    "status": "CANCELLED",
                    "progress_percent": 0,
                    "message": message,
                })
                if remote_data_path:
                    st.caption(f"Remote data path: `{remote_data_path}`")
            elif block.get("status") == "DELETED":
                status_chip("pending", label="Workflow Deleted", icon="🗑️")
                st.info(message or "Workflow folder deleted.")
                deleted_path = content.get("deleted_path")
                if deleted_path:
                    st.caption(f"Deleted workflow path: `{deleted_path}`")
            else:
                status_chip("failed", label="Staging Failed", icon="❌")
                st.error(content.get("error") or message)
                _render_stage_transfer_snapshot(content.get("transfer_progress"))
                staging_task_id = content.get("staging_task_id")
                _failed_workflow_name = ""
                _local_workflow_dir = str(content.get("local_workflow_directory") or "").strip()
                if _local_workflow_dir:
                    try:
                        import pathlib as _pl
                        _failed_workflow_name = _pl.PurePosixPath(_local_workflow_dir).name
                    except Exception:
                        _failed_workflow_name = ""
                _failed_has_delete_target = bool(
                    _local_workflow_dir or content.get("workflow_plan_block_id") or content.get("gate_block_id")
                )
                _resume_col, _delete_col = st.columns(2)
                with _resume_col:
                    if staging_task_id and st.button("🔄 Resume Staging", type="primary", key=f"resume_failed_stage_{block_id}"):
                        _pause_auto_refresh(4)
                        try:
                            _resume_resp = make_authenticated_request(
                                "POST",
                                f"{API_URL}/remote/stage/{staging_task_id}/resume",
                                json={"project_id": active_id, "block_id": block_id},
                                timeout=15,
                            )
                            if _resume_resp.status_code == 200:
                                st.success("Resuming staging from the existing cache path.")
                                st.rerun()
                            elif _resume_resp.status_code == 404:
                                st.warning("Staging task not found. It may have already been cleaned up.")
                            elif _resume_resp.status_code in {409, 429}:
                                st.warning(_resume_resp.json().get("detail", "Cannot resume staging task."))
                            else:
                                st.error(f"Resume failed: {_resume_resp.status_code} — {_resume_resp.text[:200]}")
                        except Exception as _re:
                            st.error(f"Error resuming staging task: {_re}")
                with _delete_col:
                    _confirm_key = f"del_confirm_stage_{block_id}"
                    if _failed_has_delete_target:
                        if st.session_state.get(_confirm_key):
                            st.warning(
                                f"⚠️ This will permanently delete `{_failed_workflow_name or 'workflow folder'}` from the local project workspace. "
                                "Remote staged files will not be removed."
                            )
                            _yes_col, _no_col = st.columns(2)
                            with _yes_col:
                                if st.button("Yes, delete workflow", key=f"failed_stage_del_yes_{block_id}", type="primary"):
                                    _pause_auto_refresh(4)
                                    try:
                                        _delete_resp = make_authenticated_request(
                                            "POST",
                                            f"{API_URL}/remote/stage/{staging_task_id}/delete-workflow",
                                            json={"project_id": active_id, "block_id": block_id},
                                            timeout=30,
                                        )
                                        if _delete_resp.status_code == 200:
                                            st.success(_delete_resp.json().get("message", "Workflow deleted."))
                                            st.session_state.pop(_confirm_key, None)
                                            st.rerun()
                                        else:
                                            st.error(f"Delete failed: {_delete_resp.status_code} — {_delete_resp.text[:200]}")
                                    except Exception as _de:
                                        st.error(f"Error deleting workflow: {_de}")
                            with _no_col:
                                if st.button("No, keep it", key=f"failed_stage_del_no_{block_id}"):
                                    st.session_state.pop(_confirm_key, None)
                                    st.rerun()
                        elif st.button(f"🗑️ Delete {_failed_workflow_name or 'Workflow Folder'}", key=f"failed_stage_del_btn_{block_id}"):
                            st.session_state[_confirm_key] = True
                            _pause_auto_refresh(4)
                            st.rerun()
                _render_stage_part("References", stage_parts.get("references") or {
                    "status": "FAILED",
                    "progress_percent": 0,
                    "message": content.get("error") or message,
                })
                _render_stage_part("Data", stage_parts.get("data") or {
                    "status": "FAILED",
                    "progress_percent": 0,
                    "message": content.get("error") or message,
                })
                if remote_data_path:
                    st.caption(f"Remote data path: `{remote_data_path}`")
                traceback_text = content.get("traceback")
                if traceback_text:
                    with st.expander("Traceback", expanded=False):
                        st.code(traceback_text)

    elif btype == "EXECUTION_JOB":
        handled = True
        # Job execution monitoring with progress visualization
        with st.chat_message("assistant", avatar="⚙️"):
            run_uuid = content.get("run_uuid", "")
            sample_name = content.get("sample_name", "Unknown")
            mode = content.get("mode", "Unknown")
            work_directory = content.get("work_directory", "")
            has_identity = bool(run_uuid) or bool(content.get("sample_name"))
            block_status_str = block.get("status", "")

            # Show human-readable folder name (e.g. "workflow2") when available
            if not run_uuid and block_status_str == "FAILED":
                st.caption("Run UUID not assigned (submission failed before launch)")
            
            # Live-fetch status for RUNNING jobs and DONE jobs with an
            # active transfer (manual sync on a completed job).
            job_status = content.get("job_status", {})
            live_status_poll_succeeded = False
            _persisted_transfer = (content.get("job_status", {}).get("transfer_state") or "").strip().lower()
            # Also check session state — the block payload may be stale but a
            # previous poll already saw the transfer become active.
            _cached_transfer = (st.session_state.get(f"_transfer_state_{run_uuid}") or "").strip().lower()
            _needs_live_poll = (
                block_status_str == "RUNNING"
                or _persisted_transfer in {"downloading_outputs"}
                or _cached_transfer in {"downloading_outputs"}
            )
            if run_uuid and _needs_live_poll:
                try:
                    live_resp = make_authenticated_request(
                        "GET",
                        f"{API_URL}/jobs/{run_uuid}/status",
                        timeout=LIVE_JOB_STATUS_TIMEOUT_SECONDS,
                    )
                    if live_resp.status_code == 200:
                        job_status = live_resp.json()
                        live_status_poll_succeeded = True
                        run_stage_hint = str(job_status.get("run_stage") or run_stage_hint).strip().upper()
                        is_script_job = is_script_job or run_stage_hint.startswith("SCRIPT_")
                        st.session_state[f"_job_polled_at_{run_uuid}"] = (
                            datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                        )
                        # Cache transfer_state so auto-refresh keeps polling
                        _live_ts = (job_status.get("transfer_state") or "").strip().lower()
                        st.session_state[f"_transfer_state_{run_uuid}"] = _live_ts
                except Exception:
                    pass  # Fall back to block payload

            run_type = str(content.get("run_type") or "").strip().lower()
            script_id = str(content.get("script_id") or "").strip()
            run_stage_hint = str(
                job_status.get("run_stage")
                or content.get("run_stage")
                or content.get("job_status", {}).get("run_stage")
                or ""
            ).strip().upper()
            is_script_job = run_type == "script" or bool(script_id) or run_stage_hint.startswith("SCRIPT_")
            job_title = "Skill Script" if is_script_job else "Nextflow Job"
            job_subtitle = (
                "Live script execution monitoring and controls"
                if is_script_job
                else "Live execution monitoring and controls"
            )

            section_header(
                f"{job_title} · {sample_name if has_identity else 'Submission'}",
                job_subtitle,
                icon="🧬",
            )
            
            if job_status:
                status_str = job_status.get("status", content.get("status", "UNKNOWN"))
                progress = job_status.get("progress_percent", 0)
                message = job_status.get("message", content.get("message", ""))
                tasks = job_status.get("tasks", {})
                submitted_at = job_status.get("submitted_at") or content.get("submitted_at") or block.get("created_at")
                started_at = job_status.get("started_at") or content.get("started_at")
                completed_at = job_status.get("completed_at") or content.get("completed_at")
                workflow_label = _workflow_label_from_path(work_directory)
                run_stage = (job_status.get("run_stage") or "").strip().lower()
                current_step = (job_status.get("current_step") or "").strip()
                current_step_detail = (job_status.get("current_step_detail") or "").strip()
                transfer_state = (job_status.get("transfer_state") or "").strip().lower()
                result_destination = (job_status.get("result_destination") or "").strip().lower()
                try:
                    progress_pct = max(0, min(100, int(progress or 0)))
                except (TypeError, ValueError):
                    progress_pct = 0
                _chip, _status_label, _status_icon = _run_status_label(status_str)
                status_chip(_chip, label=_status_label, icon=_status_icon)

                run_meta = {"Mode": mode, "Run UUID": run_uuid}
                if is_script_job and script_id:
                    run_meta["Script"] = script_id
                if workflow_label:
                    run_meta["Workflow"] = workflow_label
                started_label = _format_timestamp(started_at or submitted_at)
                completed_label = _format_timestamp(completed_at)
                duration_label = _format_duration(started_at or submitted_at, completed_at)
                if started_label:
                    run_meta["Started"] = started_label
                if completed_label:
                    run_meta["Completed"] = completed_label
                if duration_label:
                    run_meta["Duration"] = duration_label
                metadata_row(run_meta)

                if current_step:
                    current_step_message = current_step
                    if current_step_detail and current_step_detail != current_step:
                        current_step_message = f"{current_step}: {current_step_detail}"
                    info_callout(current_step_message, kind="info", icon="🧭")

                is_running = status_str == "RUNNING"
                is_failed = status_str in ("FAILED", "CANCELLED")
                is_done = status_str in ("COMPLETED", "DELETED")

                queue_state = "pending"
                execute_state = "pending"
                finalize_state = "pending"

                if run_stage:
                    pre_queue_stages = {
                        "awaiting_details",
                        "awaiting_approval",
                        "validating_connection",
                        "preparing_remote_dirs",
                        "transferring_inputs",
                        "submitting_job",
                    }
                    queue_stages = {"queued", "running", "collecting_outputs", "syncing_results", "completed", "failed", "cancelled"}
                    run_stages = {"running", "collecting_outputs", "syncing_results", "completed", "failed", "cancelled"}

                    if run_stage in queue_stages:
                        queue_state = "complete"
                    elif run_stage in pre_queue_stages:
                        queue_state = "running"

                    if run_stage in {"running", "collecting_outputs"}:
                        execute_state = "running"
                    elif run_stage in {"syncing_results", "completed"}:
                        execute_state = "complete"
                    elif run_stage in {"failed", "cancelled"}:
                        execute_state = "failed"
                    elif run_stage in run_stages:
                        execute_state = "running"

                    if run_stage in {"syncing_results"} or transfer_state in {"downloading_outputs"}:
                        finalize_state = "running"
                    elif run_stage in {"completed"} or transfer_state in {"outputs_downloaded"}:
                        finalize_state = "complete"
                    elif run_stage in {"failed", "cancelled"} or transfer_state in {"transfer_failed"}:
                        finalize_state = "failed"

                elif is_done:
                    queue_state = "complete"
                    execute_state = "complete"
                    finalize_state = "complete"
                elif is_failed:
                    queue_state = "complete" if progress_pct >= 10 else "failed"
                    execute_state = "failed"
                    finalize_state = "failed"
                elif is_running:
                    queue_state = "complete" if progress_pct >= 10 else "running"
                    execute_state = "running" if progress_pct < 95 else "complete"
                    finalize_state = "running" if progress_pct >= 95 else "pending"

                timeline([
                    {"name": "Submit", "status": "complete"},
                    {"name": "Queue / Provision", "status": queue_state},
                    {"name": "Execute", "status": execute_state, "details": message or ""},
                    {"name": "Finalize", "status": finalize_state},
                ])

                if run_stage:
                    validation_state = "complete" if run_stage not in {"awaiting_details", "awaiting_approval"} else "active"
                    staging_state = "complete" if run_stage not in {"validating_connection", "preparing_remote_dirs", "transferring_inputs"} else "active"
                    run_state = "active" if run_stage in {"queued", "running", "collecting_outputs"} else ("complete" if run_stage in {"syncing_results", "completed"} else ("failed" if run_stage in {"failed", "cancelled"} else "pending"))
                    if transfer_state in {"outputs_downloaded"}:
                        sync_state = "complete"
                    elif transfer_state in {"downloading_outputs"} or run_stage == "syncing_results":
                        sync_state = "active"
                    elif transfer_state in {"transfer_failed"}:
                        sync_state = "failed"
                    elif run_stage in {"completed"} and result_destination in {"remote", ""}:
                        sync_state = "complete"
                    elif run_stage in {"failed", "cancelled"}:
                        sync_state = "failed"
                    else:
                        sync_state = "pending"
                else:
                    validation_state = "complete" if (progress_pct >= 10 or is_done or is_failed or is_running) else "pending"
                    staging_state = "complete" if (progress_pct >= 30 or is_done) else ("failed" if is_failed and progress_pct < 30 else ("active" if is_running else "pending"))
                    run_state = "complete" if is_done else ("failed" if is_failed else ("active" if is_running and progress_pct >= 30 else "pending"))
                    sync_state = "complete" if is_done else ("failed" if is_failed and progress_pct >= 95 else ("active" if is_running and progress_pct >= 95 else "pending"))

                segmented_progress([
                    {"name": "Validation", "percentage": 15, "status": validation_state},
                    {"name": "Staging", "percentage": 20, "status": staging_state},
                    {"name": "Run", "percentage": 55, "status": run_state},
                    {"name": "Sync", "percentage": 10, "status": sync_state},
                ])

                # Show rsync transfer detail during active sync
                _transfer_detail = (job_status.get("transfer_detail") or "").strip()
                if _transfer_detail and sync_state == "active":
                    info_callout(f"📥 {_transfer_detail}", kind="info")
                elif _transfer_detail and sync_state == "failed":
                    info_callout(f"❌ {_transfer_detail}", kind="error")
                
                # Status indicator
                if status_str == "COMPLETED":
                    st.success(f"✅ {message}")
                    
                    # Show completed tasks summary
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        total = tasks.get("total", 0)
                        completed_count = tasks.get("completed_count", 0)
                        
                        st.metric("✅ Completed Tasks", f"{completed_count}/{total}")
                        
                        # Group and display completed tasks
                        if completed:
                            with st.expander(f"📋 Completed Tasks ({len(completed)} total)", expanded=False):
                                # Group tasks by base name
                                task_groups = {}
                                for task in completed:
                                    if '(' in task and ')' in task:
                                        base_name = task[:task.rfind('(')].strip()
                                        try:
                                            instance = int(task[task.rfind('(')+1:task.rfind(')')].strip())
                                            if base_name not in task_groups:
                                                task_groups[base_name] = []
                                            task_groups[base_name].append(instance)
                                        except ValueError:
                                            task_groups[task] = None
                                    else:
                                        task_groups[task] = None
                                
                                # Display grouped tasks
                                for base_name, instances in task_groups.items():
                                    if instances is None:
                                        st.text(f"✔ {base_name}")
                                    elif len(instances) == 1:
                                        st.text(f"✔ {base_name} ({instances[0]})")
                                    else:
                                        instances_sorted = sorted(instances)
                                        st.text(f"✔ {base_name} ({len(instances)}/{max(instances_sorted)})")

                    st.divider()
                    _completed_workflow_name = workflow_label or "workflow"
                    _delete_col, _rerun_col = st.columns(2)
                    with _delete_col:
                        _confirm_key = f"completed_del_confirm_{block_id}"
                        if st.session_state.get(_confirm_key):
                            st.warning(f"⚠️ This will permanently delete `{_completed_workflow_name}` and all its files.")
                            _yes_col, _no_col = st.columns(2)
                            with _yes_col:
                                if st.button("Yes, delete", key=f"completed_del_yes_{block_id}", type="primary"):
                                    _pause_auto_refresh(4)
                                    try:
                                        _del_resp = make_authenticated_request(
                                            "DELETE", f"{API_URL}/jobs/{run_uuid}", timeout=30
                                        )
                                        if _del_resp.status_code == 200:
                                            st.success(_del_resp.json().get("message", "Deleted."))
                                            st.session_state.pop(_confirm_key, None)
                                            st.rerun()
                                        else:
                                            st.error(f"Delete failed: {_del_resp.status_code} — {_del_resp.text[:200]}")
                                    except Exception as _e:
                                        st.error(f"Error: {_e}")
                            with _no_col:
                                if st.button("No, keep it", key=f"completed_del_no_{block_id}"):
                                    st.session_state.pop(_confirm_key, None)
                                    st.rerun()
                        elif st.button(f"🗑️ Delete {_completed_workflow_name}", key=f"completed_del_btn_{block_id}"):
                            st.session_state[_confirm_key] = True
                            _pause_auto_refresh(4)
                            st.rerun()
                    with _rerun_col:
                        if st.button("↻ Rerun Workflow", key=f"completed_rerun_{block_id}"):
                            try:
                                _rerun_resp = make_authenticated_request(
                                    "POST", f"{API_URL}/jobs/{run_uuid}/rerun", timeout=20
                                )
                                if _rerun_resp.status_code == 200:
                                    st.success("Workflow rerun submitted.")
                                    st.rerun()
                                else:
                                    st.error(f"Rerun failed: {_rerun_resp.status_code} — {_rerun_resp.text[:200]}")
                            except Exception as _e:
                                st.error(f"Error: {_e}")

                    _rename_default = sample_name or _completed_workflow_name
                    _rename_value = st.text_input(
                        "Rename workflow",
                        value=_rename_default,
                        key=f"completed_rename_input_{block_id}",
                    )
                    if _rename_value.strip() and _rename_value.strip() != _rename_default and st.button("✏️ Rename Workflow", key=f"completed_rename_btn_{block_id}"):
                        try:
                            _rename_resp = make_authenticated_request(
                                "POST",
                                f"{API_URL}/jobs/{run_uuid}/rename",
                                json={"new_name": _rename_value.strip()},
                                timeout=20,
                            )
                            if _rename_resp.status_code == 200:
                                st.success(f"Workflow renamed to {_rename_value.strip()}.")
                                st.rerun()
                            else:
                                st.error(f"Rename failed: {_rename_resp.status_code} — {_rename_resp.text[:200]}")
                        except Exception as _e:
                            st.error(f"Error: {_e}")
                    
                elif status_str == "FAILED":
                    info_callout(message or "Job failed during execution.", kind="error", icon="❌")
                    if workflow_label:
                        st.caption(f"Failed run: {workflow_label}")
                    with st.expander("Recommended Next Steps", expanded=True):
                        st.markdown("1. Review Nextflow error lines below for first root cause.")
                        st.markdown("2. Verify input path, genome, and execution mode parameters.")
                        st.markdown("3. For remote mode, verify SSH profile and SLURM account/partition.")
                        st.markdown("4. Resubmit after edits.")
                    
                    # Fetch detailed debug information
                    if run_uuid:
                        debug_info = get_job_debug_info(run_uuid)
                        
                        if debug_info:
                            # Show .nextflow.log errors prominently
                            logs_preview = debug_info.get("logs_preview", {})
                            nextflow_log = logs_preview.get(".nextflow.log", {})
                            
                            if nextflow_log.get("errors_and_warnings"):
                                with st.expander("🔥 Nextflow Errors (from .nextflow.log)", expanded=True):
                                    error_container = st.container(height=400)
                                    with error_container:
                                        for line in nextflow_log["errors_and_warnings"]:
                                            if line.strip():
                                                if "ERROR" in line.upper() or "Exception" in line:
                                                    st.error(line[:150])
                                                elif "WARN" in line.upper():
                                                    st.warning(line[:150])
                                                else:
                                                    st.text(line[:150])
                            
                            # Show failed tasks if available
                            failed_count = tasks.get("failed_count", 0) if tasks else 0
                            if failed_count > 0:
                                st.metric("❌ Failed Tasks", failed_count)
                            
                            # Show work directory and markers
                            with st.expander("🔍 Debug Information", expanded=False):
                                st.write(f"**Work Directory:** `{debug_info.get('work_directory', 'N/A')}`")
                                
                                markers = debug_info.get("markers", {})
                                if markers:
                                    st.write("**Job Markers:**")
                                    for marker_name, marker_info in markers.items():
                                        if marker_info.get("exists"):
                                            st.success(f"✅ {marker_name}: {marker_info.get('content', '')[:60]}")
                                        else:
                                            st.info(f"⬜ {marker_name}: Not present")
                                
                                # Show other log files
                                if logs_preview:
                                    st.write("**Available Log Files:**")
                                    for log_name, log_info in logs_preview.items():
                                        if log_name != ".nextflow.log" and log_info.get("exists"):
                                            size = log_info.get("size", 0)
                                            lines = log_info.get("lines", 0)
                                            st.text(f"📄 {log_name}: {size:,} bytes, {lines} lines")
                                            
                                            # Show last few lines for stderr/stdout
                                            if log_name in [".command.err", ".command.out"]:
                                                last_lines = log_info.get("last_20_lines", [])
                                                if last_lines:
                                                    with st.expander(f"Last lines from {log_name}", expanded=False):
                                                        for line in last_lines[-10:]:
                                                            if line.strip():
                                                                st.text(line[:120])
                    
                    # Also show regular logs from job_status
                    logs = content.get("logs", [])
                    if logs:
                        with st.expander("📋 Job Logs", expanded=False):
                            log_container = st.container(height=300)
                            with log_container:
                                for log in reversed(logs[-50:]):
                                    timestamp = _format_timestamp(log.get("timestamp", "")) or log.get("timestamp", "")
                                    level = log.get("level", "INFO")
                                    msg = log.get("message", "")
                                    
                                    if level == "ERROR":
                                        st.error(f"[{timestamp}] {msg}")
                                    elif level == "WARN":
                                        st.warning(f"[{timestamp}] {msg}")
                                    else:
                                        st.text(f"[{timestamp}] {msg}")
                    
                elif status_str == "CANCELLED":
                    st.warning(f"🛑 {message}")
                    # Show what was running at cancel time
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        completed_count = tasks.get("completed_count", len(completed))
                        total = tasks.get("total", 0)
                        if completed_count or total:
                            st.caption(f"Completed {completed_count}/{total} tasks before cancellation.")

                    # Post-cancel actions: Delete folder / Resubmit
                    st.divider()
                    _work_dir_name = ""
                    if work_directory:
                        import pathlib as _pl
                        _work_dir_name = _pl.PurePosixPath(work_directory).name
                    _del_col, _rerun_col, _resub_col = st.columns(3)
                    with _del_col:
                        _del_key = f"del_{block_id}"
                        _confirm_key = f"del_confirm_{block_id}"
                        if st.session_state.get(_confirm_key):
                            st.warning(f"⚠️ This will permanently delete `{_work_dir_name or 'workflow folder'}` and all its files.")
                            _yes_col, _no_col = st.columns(2)
                            with _yes_col:
                                if st.button("Yes, delete", key=f"del_yes_{block_id}", type="primary"):
                                    _pause_auto_refresh(4)
                                    try:
                                        _del_resp = make_authenticated_request(
                                            "DELETE", f"{API_URL}/jobs/{run_uuid}", timeout=30
                                        )
                                        if _del_resp.status_code == 200:
                                            _del_data = _del_resp.json()
                                            st.success(_del_data.get("message", "Deleted."))
                                            st.session_state.pop(_confirm_key, None)
                                            st.rerun()
                                        else:
                                            st.error(f"Delete failed: {_del_resp.status_code} — {_del_resp.text[:200]}")
                                    except Exception as _e:
                                        st.error(f"Error: {_e}")
                            with _no_col:
                                if st.button("No, keep it", key=f"del_no_{block_id}"):
                                    st.session_state.pop(_confirm_key, None)
                                    st.rerun()
                        else:
                            if st.button(f"🗑️ Delete {_work_dir_name or 'Workflow Folder'}", key=_del_key):
                                st.session_state[_confirm_key] = True
                                _pause_auto_refresh(4)
                                st.rerun()
                    with _rerun_col:
                        if st.button("↻ Rerun Workflow", key=f"cancel_rerun_{block_id}"):
                            try:
                                _rerun_resp = make_authenticated_request(
                                    "POST", f"{API_URL}/jobs/{run_uuid}/rerun", timeout=20
                                )
                                if _rerun_resp.status_code == 200:
                                    st.success("Workflow rerun submitted.")
                                    st.rerun()
                                else:
                                    st.error(f"Rerun failed: {_rerun_resp.status_code} — {_rerun_resp.text[:200]}")
                            except Exception as _e:
                                st.error(f"Error: {_e}")
                    with _resub_col:
                        if st.button("🔄 Resubmit Job", key=f"resub_{block_id}"):
                            try:
                                _resub_resp = make_authenticated_request(
                                    "POST", f"{API_URL}/jobs/{run_uuid}/resubmit", timeout=15
                                )
                                if _resub_resp.status_code == 200:
                                    st.success("Approval gate created — scroll down to review and approve.")
                                    st.rerun()
                                else:
                                    st.error(f"Resubmit failed: {_resub_resp.status_code} — {_resub_resp.text[:200]}")
                            except Exception as _e:
                                st.error(f"Error: {_e}")

                    _cancelled_rename = st.text_input(
                        "Rename workflow",
                        value=sample_name or _work_dir_name,
                        key=f"cancelled_rename_input_{block_id}",
                    )
                    if _cancelled_rename.strip() and _cancelled_rename.strip() != (sample_name or _work_dir_name) and st.button("✏️ Rename Workflow", key=f"cancelled_rename_btn_{block_id}"):
                        try:
                            _rename_resp = make_authenticated_request(
                                "POST",
                                f"{API_URL}/jobs/{run_uuid}/rename",
                                json={"new_name": _cancelled_rename.strip()},
                                timeout=20,
                            )
                            if _rename_resp.status_code == 200:
                                st.success(f"Workflow renamed to {_cancelled_rename.strip()}.")
                                st.rerun()
                            else:
                                st.error(f"Rename failed: {_rename_resp.status_code} — {_rename_resp.text[:200]}")
                        except Exception as _e:
                            st.error(f"Error: {_e}")

                elif status_str == "DELETED":
                    st.info(f"🗑️ {message or 'Workflow folder deleted.'}")
                    if work_directory:
                        import pathlib as _pl
                        st.caption(f"Folder `{_pl.PurePosixPath(work_directory).name}` has been removed.")

                elif status_str == "RUNNING":
                    info_callout(message or "Job is running", kind="info", icon="🔄")
                    
                    # Job timing info
                    _job_created = block.get("created_at", "")
                    _last_upd = _job_status_updated_at(
                        st.session_state.get(f"_job_polled_at_{run_uuid}") or content.get("last_updated", ""),
                        live_status_poll_succeeded,
                    )
                    _timing_parts = []
                    if _job_created:
                        try:
                            _start_dt = datetime.datetime.fromisoformat(_job_created.replace("Z", "+00:00"))
                            if _start_dt.tzinfo is None:
                                _start_dt = _start_dt.replace(tzinfo=datetime.timezone.utc)
                            _elapsed = datetime.datetime.now(datetime.timezone.utc) - _start_dt.astimezone(datetime.timezone.utc)
                            _mins, _secs = divmod(int(_elapsed.total_seconds()), 60)
                            _timing_parts.append(f"⏱️ Running for {_mins}m {_secs}s")
                        except (ValueError, TypeError):
                            pass
                    if _last_upd:
                        try:
                            _upd_dt = datetime.datetime.fromisoformat(_last_upd.replace("Z", "+00:00"))
                            if _upd_dt.tzinfo is None:
                                _upd_dt = _upd_dt.replace(tzinfo=datetime.timezone.utc)
                            _ago = datetime.datetime.now(datetime.timezone.utc) - _upd_dt.astimezone(datetime.timezone.utc)
                            _ago_secs = int(_ago.total_seconds())
                            _timing_parts.append(f"🔄 Updated {_ago_secs}s ago")
                        except (ValueError, TypeError):
                            pass
                    if _timing_parts:
                        st.caption(" | ".join(_timing_parts))
                    
                    # Progress bar
                    st.progress(progress / 100.0, text=f"Progress: {progress}%")
                    
                    # Nextflow-style task display
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        running = tasks.get("running", [])
                        total = tasks.get("total", 0)
                        completed_count = tasks.get("completed_count", 0)
                        failed_count = tasks.get("failed_count", 0)
                        retried_count = tasks.get("retried_count", 0)
                        remaining_count = tasks.get("remaining_count", 0)
                        scheduler_running_count = tasks.get("scheduler_running_count")
                        scheduler_pending_count = tasks.get("scheduler_pending_count")
                        observed_running_count = tasks.get("observed_running_count", len(running))
                        
                        # Summary metrics
                        summary_metrics = {
                            "Completed": f"{completed_count}/{total}",
                        }
                        if scheduler_running_count is not None:
                            summary_metrics["SLURM Running"] = scheduler_running_count
                        if scheduler_pending_count is not None:
                            summary_metrics["SLURM Pending"] = scheduler_pending_count
                        if remaining_count:
                            summary_metrics["Remaining"] = remaining_count
                        elif observed_running_count:
                            summary_metrics["Observed Active"] = observed_running_count
                        if retried_count:
                            summary_metrics["Retries"] = retried_count
                        summary_metrics["Failed"] = failed_count
                        progress_stats(summary_metrics)
                        
                        # Show the last few active task names observed in stdout.
                        if running:
                            st.write("**🏃 Recently Observed Active Tasks:**")
                            if scheduler_running_count is not None or scheduler_pending_count is not None:
                                st.caption("Task names below come from the recent Nextflow stdout tail; the metrics above reflect actual SLURM job counts.")
                            for task in running[-5:]:  # Show last 5 running tasks
                                # Format like: [7b/34a1] mainWorkflow:doradoDownloadTask (1) [100%]
                                task_hash = task.split(':')[-1][:4] if ':' in task else "????"
                                st.code(f"[..../{task_hash}] {task}", language="bash")
                        
                        # Show recently completed with grouping
                        if completed:
                            with st.expander(f"✅ Recently Completed ({len(completed)} shown)", expanded=False):
                                # Group tasks by base name
                                task_groups = {}
                                for task in completed:
                                    if '(' in task and ')' in task:
                                        base_name = task[:task.rfind('(')].strip()
                                        try:
                                            instance = int(task[task.rfind('(')+1:task.rfind(')')].strip())
                                            if base_name not in task_groups:
                                                task_groups[base_name] = []
                                            task_groups[base_name].append(instance)
                                        except ValueError:
                                            task_groups[task] = None
                                    else:
                                        task_groups[task] = None
                                
                                # Display grouped tasks
                                for base_name, instances in task_groups.items():
                                    if instances is None:
                                        st.text(f"✔ {base_name}")
                                    elif len(instances) == 1:
                                        st.text(f"✔ {base_name} ({instances[0]})")
                                    else:
                                        instances_sorted = sorted(instances)
                                        st.text(f"✔ {base_name} ({len(instances)}/{max(instances_sorted)})")
                    # Cancel button
                    st.divider()
                    if st.button("🛑 Cancel Job", type="primary", key=f"cancel_job_{block_id}"):
                        try:
                            _cancel_resp = make_authenticated_request(
                                "POST", f"{API_URL}/jobs/{run_uuid}/cancel", timeout=15
                            )
                            if _cancel_resp.status_code == 200:
                                _cancel_data = _cancel_resp.json()
                                st.success(_cancel_data.get("message", "Job cancelled successfully."))
                                st.rerun()
                            elif _cancel_resp.status_code == 400:
                                st.warning(_cancel_resp.json().get("detail", "Cannot cancel job."))
                            else:
                                st.error(f"Cancel failed: {_cancel_resp.status_code} — {_cancel_resp.text[:200]}")
                        except Exception as _ce:
                            st.error(f"Error cancelling job: {_ce}")
                else:
                    st.warning(f"⏳ Status: {status_str}")
                    if message:
                        st.caption(message)

                if status_str == "FAILED" and run_uuid:
                    st.divider()
                    _work_dir_name = workflow_label or "workflow folder"
                    _delete_col, _rerun_col, _resubmit_col = st.columns(3)
                    with _delete_col:
                        _confirm_key = f"del_confirm_{block_id}"
                        if st.session_state.get(_confirm_key):
                            st.warning(f"⚠️ This will permanently delete `{_work_dir_name}` and all its files.")
                            _yes_col, _no_col = st.columns(2)
                            with _yes_col:
                                if st.button("Yes, delete failed run", key=f"failed_del_yes_{block_id}", type="primary"):
                                    _pause_auto_refresh(4)
                                    try:
                                        _del_resp = make_authenticated_request(
                                            "DELETE", f"{API_URL}/jobs/{run_uuid}", timeout=30
                                        )
                                        if _del_resp.status_code == 200:
                                            st.success(_del_resp.json().get("message", "Deleted."))
                                            st.session_state.pop(_confirm_key, None)
                                            st.rerun()
                                        else:
                                            st.error(f"Delete failed: {_del_resp.status_code} — {_del_resp.text[:200]}")
                                    except Exception as _e:
                                        st.error(f"Error: {_e}")
                            with _no_col:
                                if st.button("No, keep it", key=f"failed_del_no_{block_id}"):
                                    st.session_state.pop(_confirm_key, None)
                                    st.rerun()
                        elif st.button(f"🗑️ Delete {_work_dir_name}", key=f"failed_del_btn_{block_id}"):
                            st.session_state[_confirm_key] = True
                            _pause_auto_refresh(4)
                            st.rerun()
                    with _rerun_col:
                        if st.button("↻ Rerun Workflow", key=f"failed_rerun_{block_id}"):
                            try:
                                _rerun_resp = make_authenticated_request(
                                    "POST", f"{API_URL}/jobs/{run_uuid}/rerun", timeout=20
                                )
                                if _rerun_resp.status_code == 200:
                                    st.success("Workflow rerun submitted.")
                                    st.rerun()
                                else:
                                    st.error(f"Rerun failed: {_rerun_resp.status_code} — {_rerun_resp.text[:200]}")
                            except Exception as _e:
                                st.error(f"Error: {_e}")
                    with _resubmit_col:
                        if st.button("🔄 Resubmit Job", key=f"failed_resub_{block_id}"):
                            try:
                                _resub_resp = make_authenticated_request(
                                    "POST", f"{API_URL}/jobs/{run_uuid}/resubmit", timeout=15
                                )
                                if _resub_resp.status_code == 200:
                                    st.success("Approval gate created — scroll down to review and approve.")
                                    st.rerun()
                                else:
                                    st.error(f"Resubmit failed: {_resub_resp.status_code} — {_resub_resp.text[:200]}")
                            except Exception as _e:
                                st.error(f"Error: {_e}")

                    _failed_rename = st.text_input(
                        "Rename workflow",
                        value=sample_name or _work_dir_name,
                        key=f"failed_rename_input_{block_id}",
                    )
                    if _failed_rename.strip() and _failed_rename.strip() != (sample_name or _work_dir_name) and st.button("✏️ Rename Workflow", key=f"failed_rename_btn_{block_id}"):
                        try:
                            _rename_resp = make_authenticated_request(
                                "POST",
                                f"{API_URL}/jobs/{run_uuid}/rename",
                                json={"new_name": _failed_rename.strip()},
                                timeout=20,
                            )
                            if _rename_resp.status_code == 200:
                                st.success(f"Workflow renamed to {_failed_rename.strip()}.")
                                st.rerun()
                            else:
                                st.error(f"Rename failed: {_rename_resp.status_code} — {_rename_resp.text[:200]}")
                        except Exception as _e:
                            st.error(f"Error: {_e}")
            else:
                # Fallback for initial state
                init_message = content.get("message", "Job submitted")
                st.info(f"⏳ {init_message}")
            
            # Show logs toggle for non-failed jobs (failed jobs show logs above)
            if status_str != "FAILED":
                logs = content.get("logs", [])
                if logs and st.checkbox("Show Logs", key=f"logs_{block_id}"):
                    st.write("**Recent Logs:**")
                    log_container = st.container(height=300)
                    with log_container:
                        for log in reversed(logs[-30:]):  # Show last 30 in reverse chronological order
                            timestamp = _format_timestamp(log.get("timestamp", "")) or log.get("timestamp", "")
                            level = log.get("level", "INFO")
                            msg = log.get("message", "")
                            
                            # Color-code by level
                            if level == "ERROR":
                                st.error(f"[{timestamp}] {msg}")
                            elif level == "WARN":
                                st.warning(f"[{timestamp}] {msg}")
                            else:
                                st.text(f"[{timestamp}] {msg}")
    
    elif btype == "AGENT_PLOT":
        handled = True
        with st.chat_message("assistant", avatar="📊"):
            section_header("Visualization", "Interactive plot generated from analyzed data", icon="📊")
            show_metadata()
            st.divider()
            # Render interactive Plotly charts
            # Pass all currently loaded blocks so the renderer can look up DFs by ID
            all_blocks = st.session_state.get("blocks", [])
            _render_plot_block(content, all_blocks, block_id)

    elif btype == "DOWNLOAD_TASK":
        handled = True
        with st.chat_message("assistant", avatar="⬇️"):
            dl_status = content.get("status", "RUNNING")
            dl_files = content.get("files", [])
            downloaded = content.get("downloaded", 0)
            total = content.get("total_files", len(dl_files))
            total_bytes = content.get("bytes_downloaded", 0)
            expected_total_bytes = content.get("expected_total_bytes", 0)
            source = content.get("source", "url")

            _chip = "pending"
            if dl_status == "RUNNING":
                _chip = "running"
            elif dl_status == "DONE":
                _chip = "complete"
            elif dl_status in ("FAILED", "CANCELLED"):
                _chip = "failed"
            section_header("Download Task", "Track transfer progress and outcomes", icon="⬇️")
            status_chip(_chip, label=dl_status.title(), icon="📦")
            metadata_row({
                "Source": source,
                "Files": f"{downloaded}/{total}",
                "Downloaded": f"{round(total_bytes / (1024 * 1024), 1)} MB" if total_bytes else "0 MB",
            })

            if dl_status == "RUNNING":
                cur = content.get("current_file", "")
                cur_bytes = content.get("current_file_bytes", 0)
                cur_expected = content.get("current_file_expected", 0)

                # Per-file byte progress
                if cur_expected > 0:
                    _pct = min(cur_bytes / cur_expected, 1.0)
                    _cur_mb = round(cur_bytes / (1024 * 1024), 1)
                    _exp_mb = round(cur_expected / (1024 * 1024), 1)
                    st.info(f"⬇️ **Downloading** ({downloaded}/{total} files) — `{cur}` ({_cur_mb}/{_exp_mb} MB)")
                    st.progress(_pct)
                elif expected_total_bytes > 0:
                    # Fallback: overall progress based on total expected bytes
                    _pct = min(total_bytes / expected_total_bytes, 1.0)
                    _dl_mb = round(total_bytes / (1024 * 1024), 1)
                    _exp_mb = round(expected_total_bytes / (1024 * 1024), 1)
                    st.info(f"⬇️ **Downloading** ({downloaded}/{total} files) — `{cur}` ({_dl_mb}/{_exp_mb} MB)")
                    st.progress(_pct)
                else:
                    st.info(f"⬇️ **Downloading** ({downloaded}/{total} files) — `{cur}`")
                    st.progress(downloaded / max(total, 1))

                # Cancel download button
                _dl_id = content.get("download_id")
                if _dl_id:
                    if st.button("🛑 Cancel Download", type="primary", key=f"cancel_dl_{block_id}"):
                        try:
                            _cancel_resp = make_authenticated_request(
                                "DELETE",
                                f"{API_URL}/projects/{active_id}/downloads/{_dl_id}",
                                timeout=15,
                            )
                            if _cancel_resp.status_code == 200:
                                st.success("Download cancellation requested.")
                                st.rerun()
                            elif _cancel_resp.status_code == 404:
                                st.warning("Download not found — it may have already finished or the server was restarted.")
                            else:
                                st.error(f"Cancel failed: {_cancel_resp.status_code} — {_cancel_resp.text[:200]}")
                        except Exception as _ce:
                            st.error(f"Error cancelling download: {_ce}")
            elif dl_status == "DONE":
                mb = round(total_bytes / (1024 * 1024), 2) if total_bytes else 0
                st.success(f"✅ **Download complete** — {downloaded} file(s), {mb} MB")
                # List downloaded files
                for df in content.get("downloaded_files", []):
                    fname = df.get("filename", "?")
                    fsize = df.get("size_bytes")
                    err = df.get("error")
                    if err:
                        st.markdown(f"- ❌ `{fname}` — {err}")
                    elif fsize:
                        st.markdown(f"- ✅ `{fname}` ({round(fsize / (1024*1024), 2)} MB)")
                    else:
                        st.markdown(f"- ✅ `{fname}`")
            elif dl_status == "FAILED":
                msg = content.get("message", "Unknown error")
                st.error(f"❌ **Download failed** — {msg}")
            elif dl_status == "CANCELLED":
                _cancel_msg = content.get("message", f"{downloaded}/{total} files completed")
                _deleted = content.get("deleted_partial")
                _warn_text = f"⚠️ **Download cancelled** — {_cancel_msg}"
                if _deleted:
                    _warn_text += f" Deleted partial file: `{_deleted}`"
                st.warning(_warn_text)

    return handled


