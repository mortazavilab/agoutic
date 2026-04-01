import json

import streamlit as st


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


def _handle_task_action(task: dict, project_id: str, api_url: str, request_fn):
    target_raw = task.get("action_target") or ""
    try:
        target = json.loads(target_raw) if target_raw else {}
    except (TypeError, ValueError):
        target = {}

    target_type = target.get("type")
    run_uuid = target.get("run_uuid")
    block_id = target.get("block_id")

    if target_type == "results" and run_uuid:
        if task.get("kind") == "result_review":
            apply_task_action(project_id, task["id"], "complete", api_url, request_fn)
        st.session_state["selected_job_run_uuid"] = run_uuid
        st.switch_page("pages/results.py")
        return

    if target_type == "block" and block_id:
        st.session_state["_max_visible_blocks"] = max(
            len(st.session_state.get("blocks", [])),
            st.session_state.get("_max_visible_blocks", 30),
        )
        st.session_state["_task_focus_block_id"] = block_id
        st.rerun()


def _render_single_task(
    task: dict,
    project_id: str,
    api_url: str,
    request_fn,
    *,
    is_child: bool = False,
):
    meta = task.get("metadata", {})
    extra = []
    if meta.get("sample_name"):
        extra.append(meta["sample_name"])
    if meta.get("progress_percent") not in (None, "") and task.get("status") == "RUNNING":
        extra.append(f"{meta['progress_percent']}%")
    if meta.get("total_files") and task.get("kind") == "download":
        extra.append(f"{meta.get('downloaded', 0)}/{meta['total_files']} files")

    title = task.get("title", "Task")
    if is_child:
        title = f"↳ {title}"

    left, action_col, extra_col = st.columns([5, 1, 1])
    with left:
        st.write(f"**{title}**")
        if extra:
            st.caption(" · ".join(str(part) for part in extra if part not in (None, "")))
    with action_col:
        action_label = task.get("action_label")
        if action_label and task.get("action_target"):
            if st.button(action_label, key=f"task_action_{task['id']}"):
                _handle_task_action(task, project_id, api_url, request_fn)
    with extra_col:
        status = task.get("status")
        if task.get("kind") in {"result_review", "recovery"} and status in {"PENDING", "FOLLOW_UP"}:
            if st.button("Done", key=f"task_done_{task['id']}"):
                if apply_task_action(project_id, task["id"], "complete", api_url, request_fn):
                    st.rerun()
        elif status == "COMPLETED" and task.get("kind") in {"result_review", "recovery"}:
            if st.button("Reopen", key=f"task_reopen_{task['id']}"):
                if apply_task_action(project_id, task["id"], "reopen", api_url, request_fn):
                    st.rerun()
        elif status in {"COMPLETED", "FOLLOW_UP", "FAILED"}:
            if st.button("Hide", key=f"task_hide_{task['id']}"):
                if apply_task_action(project_id, task["id"], "archive", api_url, request_fn):
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

    st.subheader("Task List")
    summary_cols = st.columns(4)
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
                _render_single_task(task, project_id, api_url, request_fn)
                for child in task.get("children", []):
                    _render_single_task(child, project_id, api_url, request_fn, is_child=True)

    if not docked:
        st.write("---")
    return total_tasks
