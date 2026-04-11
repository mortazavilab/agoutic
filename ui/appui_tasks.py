import datetime
import json
import os

import streamlit as st

from components.cards import status_chip


_PROJECT_TASK_HIDE_STALE_HOURS = float(os.getenv("PROJECT_TASK_HIDE_STALE_HOURS", "48"))


def get_sanitized_blocks(target_project_id: str, api_url: str, request_fn):
    """
    Fetch blocks from server, but scrub any block that doesn't match the target ID.

    Returns (blocks_list, fetch_ok) so callers can distinguish an empty project
    from a transient server failure and avoid wiping session-state blocks.
    """
    try:
        resp = request_fn(
            "GET",
            f"{api_url}/blocks",
            params={"project_id": target_project_id, "since_seq": 0, "limit": 500},
            timeout=10,
        )
        if resp.status_code == 200:
            raw_blocks = resp.json().get("blocks", [])
            clean_blocks = [
                b for b in raw_blocks if b.get("project_id") == target_project_id
            ]
            return clean_blocks, True

        import logging

        logging.getLogger("agoutic.ui").warning(
            "Block fetch returned status %s for project %s",
            resp.status_code,
            target_project_id,
        )
        return [], False
    except Exception as exc:
        import logging

        logging.getLogger("agoutic.ui").warning(
            "Block fetch failed for project %s: %s", target_project_id, exc
        )
        return [], False


def get_project_tasks(project_id: str, api_url: str, request_fn):
    """Fetch grouped persistent project tasks for the active project."""
    try:
        resp = request_fn(
            "GET",
            f"{api_url}/projects/{project_id}/tasks",
            timeout=8,
        )
        if resp.status_code == 200:
            return resp.json().get("sections", {})
    except Exception:
        pass
    return {}


def get_all_tasks(api_url: str, request_fn, *, include_archived: bool = False):
    """Fetch grouped persistent tasks across all accessible projects."""
    try:
        resp = request_fn(
            "GET",
            f"{api_url}/tasks",
            params={"include_archived": include_archived},
            timeout=8,
        )
        if resp.status_code == 200:
            payload = resp.json()
            payload.setdefault("sections", {})
            payload.setdefault("total_projects", 0)
            payload.setdefault("projects_with_tasks", 0)
            return payload
    except Exception:
        pass
    return {
        "sections": {},
        "total_projects": 0,
        "projects_with_tasks": 0,
    }


def apply_task_action(project_id: str, task_id: str, action: str, api_url: str, request_fn) -> bool:
    """Apply a backend task action and report success."""
    try:
        resp = request_fn(
            "PATCH",
            f"{api_url}/projects/{project_id}/tasks/{task_id}",
            json={"action": action},
            timeout=8,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _parse_timestamp(value):
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


def _task_activity_at(task: dict):
    meta = task.get("metadata", {}) if isinstance(task.get("metadata", {}), dict) else {}
    for candidate in (
        meta.get("source_updated_at"),
        task.get("updated_at"),
        task.get("created_at"),
    ):
        parsed = _parse_timestamp(candidate)
        if parsed is not None:
            return parsed
    return None


def _format_relative_time(value) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return ""
    delta = datetime.datetime.now(datetime.timezone.utc) - parsed
    total_seconds = max(int(delta.total_seconds()), 0)
    if total_seconds < 60:
        return "updated just now"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"updated {minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"updated {hours}h ago"
    days = hours // 24
    return f"updated {days}d ago"


def count_top_level_tasks(sections: dict, section_order: list[tuple[str, str]]) -> int:
    total = 0
    for name, _label in section_order:
        total += sum(1 for task in sections.get(name, []) if not task.get("parent_task_id"))
    return total


def count_stale_top_level_tasks(sections: dict, section_order: list[tuple[str, str]]) -> int:
    total = 0
    for name, _label in section_order:
        total += sum(
            1
            for task in sections.get(name, [])
            if not task.get("parent_task_id") and task.get("metadata", {}).get("is_stale")
        )
    return total


def prepare_project_task_sections_for_dock(
    sections: dict,
    section_order: list[tuple[str, str]],
    *,
    stale_hide_hours: float | None = None,
):
    """Hide old stale root tasks from the per-project dock.

    The global task center remains the audit view; the inline dock stays focused on
    currently actionable work.
    """
    def _local_parse_timestamp(value):
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

    def _local_task_age_seconds(task: dict) -> int | None:
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata", {}), dict) else {}
        age_seconds = metadata.get("stale_age_seconds")
        if age_seconds not in (None, ""):
            try:
                return max(int(age_seconds), 0)
            except (TypeError, ValueError):
                pass

        for candidate in (
            metadata.get("source_updated_at"),
            task.get("updated_at"),
            task.get("created_at"),
        ):
            activity_at = _local_parse_timestamp(candidate)
            if activity_at is not None:
                return max(
                    int((datetime.datetime.now(datetime.timezone.utc) - activity_at).total_seconds()),
                    0,
                )
        return None

    hide_after_hours = _PROJECT_TASK_HIDE_STALE_HOURS if stale_hide_hours is None else stale_hide_hours
    if hide_after_hours <= 0:
        return sections, 0

    all_tasks = []
    for name, _label in section_order:
        all_tasks.extend(sections.get(name, []))

    children_by_parent: dict[str, list[str]] = {}
    for task in all_tasks:
        parent_id = task.get("parent_task_id")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(task.get("id"))

    hide_ids: set[str] = set()
    hidden_root_count = 0
    cutoff_seconds = hide_after_hours * 3600

    def _hide_subtree(task_id: str) -> None:
        if task_id in hide_ids:
            return
        hide_ids.add(task_id)
        for child_id in children_by_parent.get(task_id, []):
            _hide_subtree(child_id)

    for task in all_tasks:
        if task.get("parent_task_id"):
            continue
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata", {}), dict) else {}
        if not metadata.get("is_stale"):
            continue
        age_seconds = _local_task_age_seconds(task)
        if age_seconds is None or age_seconds < cutoff_seconds:
            continue
        hidden_root_count += 1
        _hide_subtree(task.get("id"))

    if not hide_ids:
        return sections, 0

    filtered_sections = {
        name: [task for task in sections.get(name, []) if task.get("id") not in hide_ids]
        for name, _label in section_order
    }
    return filtered_sections, hidden_root_count


def group_tasks_by_project(sections: dict, section_order: list[tuple[str, str]]):
    grouped: dict[str, dict] = {}
    for section_name, _label in section_order:
        for task in sections.get(section_name, []):
            if task.get("parent_task_id"):
                continue
            project_id = task.get("project_id") or ""
            group = grouped.setdefault(
                project_id,
                {
                    "project_id": project_id,
                    "project_name": task.get("project_name") or project_id or "Project",
                    "project_slug": task.get("project_slug"),
                    "project_is_archived": bool(task.get("project_is_archived")),
                    "sections": {name: [] for name, _ in section_order},
                    "latest_activity": None,
                    "stale_count": 0,
                },
            )
            group["sections"].setdefault(section_name, []).append(task)
            activity_at = _task_activity_at(task)
            if activity_at is not None and (
                group["latest_activity"] is None or activity_at > group["latest_activity"]
            ):
                group["latest_activity"] = activity_at
            if task.get("metadata", {}).get("is_stale"):
                group["stale_count"] += 1

    def _sort_key(item: dict):
        sections_map = item.get("sections", {})
        latest_activity = item.get("latest_activity")
        latest_ts = latest_activity.timestamp() if latest_activity is not None else 0.0
        return (
            len(sections_map.get("running", [])),
            len(sections_map.get("follow_up", [])),
            len(sections_map.get("pending", [])),
            item.get("stale_count", 0),
            latest_ts,
            (item.get("project_name") or "").lower(),
        )

    return sorted(grouped.values(), key=_sort_key, reverse=True)


def _task_status_badge(task: dict) -> tuple[str, str]:
    metadata = task.get("metadata", {}) if isinstance(task.get("metadata", {}), dict) else {}
    if metadata.get("is_stale"):
        return "warning", "Possibly dangling"

    status = str(task.get("status") or "").upper()
    if status == "RUNNING":
        return "running", "Running"
    if status == "FOLLOW_UP":
        return "warning", "Needs attention"
    if status == "FAILED":
        return "error", "Failed"
    if status == "COMPLETED":
        return "success", "Completed"
    if status == "CANCELLED":
        return "cancelled", "Cancelled"
    return "pending", "Pending"


def _handle_task_action(
    task: dict,
    project_id: str,
    api_url: str,
    request_fn,
    *,
    navigate_to_chat_on_block: bool = False,
):
    target_raw = task.get("action_target") or ""
    try:
        target = json.loads(target_raw) if target_raw else {}
    except (TypeError, ValueError):
        target = {}

    task_project_id = task.get("project_id") or project_id
    if task_project_id:
        st.session_state["active_project_id"] = task_project_id
        st.session_state["_project_id_input"] = task_project_id

    target_type = target.get("type")
    run_uuid = target.get("run_uuid")
    block_id = target.get("block_id")

    if target_type == "results" and run_uuid:
        if task.get("kind") == "result_review":
            apply_task_action(task_project_id, task["id"], "complete", api_url, request_fn)
        st.session_state["selected_job_run_uuid"] = run_uuid
        st.switch_page("pages/results.py")
        return

    if target_type == "block" and block_id:
        st.session_state["_max_visible_blocks"] = max(
            len(st.session_state.get("blocks", [])),
            st.session_state.get("_max_visible_blocks", 30),
        )
        st.session_state["_task_focus_block_id"] = block_id
        if navigate_to_chat_on_block:
            st.switch_page("appUI.py")
        else:
            st.rerun()


def _render_single_task(
    task: dict,
    project_id: str,
    api_url: str,
    request_fn,
    *,
    is_child: bool = False,
    show_project_context: bool = False,
    navigate_to_chat_on_block: bool = False,
    show_activity: bool = False,
):
    meta = task.get("metadata", {})
    extra = []
    project_label = ""
    if show_project_context and not is_child and task.get("project_name"):
        project_label = task["project_name"]
        if task.get("project_is_archived"):
            project_label = f"{project_label} · archived"
    if meta.get("sample_name"):
        extra.append(meta["sample_name"])
    if meta.get("progress_percent") not in (None, "") and (
        task.get("status") == "RUNNING" or meta.get("stale_original_status") == "RUNNING"
    ):
        extra.append(f"{meta['progress_percent']}%")
    if meta.get("total_files") and task.get("kind") == "download":
        extra.append(f"{meta.get('downloaded', 0)}/{meta['total_files']} files")
    if meta.get("is_stale"):
        extra.append("possibly dangling")
    if show_activity:
        activity_label = _format_relative_time(meta.get("source_updated_at") or task.get("updated_at") or task.get("created_at"))
        if activity_label:
            extra.append(activity_label)

    title = task.get("title", "Task")
    if is_child:
        title = f"↳ {title}"

    left, action_col, extra_col = st.columns([5, 1, 1])
    with left:
        st.write(f"**{title}**")
        subtitle_parts = []
        if project_label:
            subtitle_parts.append(project_label)
        subtitle_parts.extend(str(part) for part in extra if part not in (None, ""))
        if subtitle_parts:
            st.caption(" · ".join(subtitle_parts))
        if show_activity and meta.get("is_stale") and meta.get("stale_reason") and not is_child:
            st.caption(meta.get("stale_reason"))
    with action_col:
        action_label = task.get("action_label")
        if action_label and task.get("action_target"):
            if st.button(action_label, key=f"task_action_{task['id']}"):
                _handle_task_action(
                    task,
                    project_id,
                    api_url,
                    request_fn,
                    navigate_to_chat_on_block=navigate_to_chat_on_block,
                )
    with extra_col:
        task_project_id = task.get("project_id") or project_id
        status = task.get("status")
        if task.get("kind") in {"result_review", "recovery"} and status in {"PENDING", "FOLLOW_UP"}:
            if st.button("Done", key=f"task_done_{task['id']}"):
                if apply_task_action(task_project_id, task["id"], "complete", api_url, request_fn):
                    st.rerun()
        elif status == "COMPLETED" and task.get("kind") in {"result_review", "recovery"}:
            if st.button("Reopen", key=f"task_reopen_{task['id']}"):
                if apply_task_action(task_project_id, task["id"], "reopen", api_url, request_fn):
                    st.rerun()
        elif status in {"COMPLETED", "FOLLOW_UP", "FAILED"}:
            if st.button("Hide", key=f"task_hide_{task['id']}"):
                if apply_task_action(task_project_id, task["id"], "archive", api_url, request_fn):
                    st.rerun()


def _count_project_tasks(sections: dict, section_order: list[tuple[str, str]]) -> int:
    return sum(len(sections.get(name, [])) for name, _ in section_order)


def render_project_tasks(
    project_id: str,
    api_url: str,
    request_fn,
    section_order: list[tuple[str, str]],
    *,
    sections: dict | None = None,
    docked: bool = False,
    title: str = "Task List",
    show_project_context: bool = False,
    navigate_to_chat_on_block: bool = False,
    show_activity: bool = False,
) -> int:
    """Render grouped project tasks and return the number of visible tasks."""
    sections = sections if sections is not None else get_project_tasks(project_id, api_url, request_fn)
    total_tasks = _count_project_tasks(sections, section_order)
    if total_tasks == 0:
        return 0

    all_tasks = []
    for name, _label in section_order:
        all_tasks.extend(sections.get(name, []))
    child_ids = {task["id"] for task in all_tasks for task in task.get("children", [])}

    st.subheader(title)
    summary_cols = st.columns(max(len(section_order), 1))
    for idx, (name, label) in enumerate(section_order):
        summary_cols[idx].metric(label, len(sections.get(name, [])))

    for name, label in section_order:
        tasks = sections.get(name, [])
        if not tasks:
            continue
        expanded = name != "completed"
        with st.expander(f"{label} ({len(tasks)})", expanded=expanded):
            for task in tasks:
                if task["id"] in child_ids:
                    continue
                _render_single_task(
                    task,
                    project_id,
                    api_url,
                    request_fn,
                    show_project_context=show_project_context,
                    navigate_to_chat_on_block=navigate_to_chat_on_block,
                    show_activity=show_activity,
                )
                for child in task.get("children", []):
                    _render_single_task(
                        child,
                        project_id,
                        api_url,
                        request_fn,
                        is_child=True,
                        show_project_context=show_project_context,
                        navigate_to_chat_on_block=navigate_to_chat_on_block,
                        show_activity=show_activity,
                    )

    if not docked:
        st.write("---")
    return total_tasks


def render_project_task_groups(api_url: str, request_fn, section_order: list[tuple[str, str]], *, sections: dict) -> int:
    groups = group_tasks_by_project(sections, section_order)
    if not groups:
        return 0

    total_visible = 0
    for group in groups:
        group_sections = group.get("sections", {})
        running_count = len(group_sections.get("running", []))
        follow_up_count = len(group_sections.get("follow_up", []))
        pending_count = len(group_sections.get("pending", []))
        completed_count = len(group_sections.get("completed", []))
        visible_count = sum(len(group_sections.get(name, [])) for name, _ in section_order)
        if visible_count == 0:
            continue
        total_visible += visible_count

        summary_parts = []
        if running_count:
            summary_parts.append(f"{running_count} running")
        if follow_up_count:
            summary_parts.append(f"{follow_up_count} follow-up")
        if pending_count:
            summary_parts.append(f"{pending_count} pending")
        if completed_count:
            summary_parts.append(f"{completed_count} completed")
        if group.get("stale_count"):
            summary_parts.append(f"{group['stale_count']} stale")

        project_title = group.get("project_name") or group.get("project_id") or "Project"
        if group.get("project_is_archived"):
            project_title = f"{project_title} · archived"
        expander_title = project_title
        if summary_parts:
            expander_title = f"{project_title}  ({' · '.join(summary_parts)})"

        with st.expander(expander_title, expanded=running_count > 0 or group.get("stale_count", 0) > 0):
            latest_label = _format_relative_time(group.get("latest_activity")) if group.get("latest_activity") else ""
            if latest_label:
                st.caption(f"Latest source activity: {latest_label}")

            for section_name, section_label in section_order:
                tasks = group_sections.get(section_name, [])
                if not tasks:
                    continue
                st.markdown(f"**{section_label}**")
                for task in tasks:
                    with st.container(border=True):
                        badge_kind, badge_label = _task_status_badge(task)
                        status_chip(badge_kind, label=badge_label)
                        _render_single_task(
                            task,
                            group.get("project_id", ""),
                            api_url,
                            request_fn,
                            navigate_to_chat_on_block=True,
                            show_activity=True,
                        )
                        children = task.get("children", [])
                        if children:
                            with st.expander(f"Steps ({len(children)})", expanded=False):
                                for child in children:
                                    _render_single_task(
                                        child,
                                        group.get("project_id", ""),
                                        api_url,
                                        request_fn,
                                        is_child=True,
                                        navigate_to_chat_on_block=True,
                                        show_activity=True,
                                    )

    return total_visible
