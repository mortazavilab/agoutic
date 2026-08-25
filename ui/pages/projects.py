"""
Projects Dashboard Page
Browse projects, view job history, file listings, and disk usage.
All requests go through Cortex — the UI never contacts backend servers directly.
"""

import streamlit as st
import pandas as pd
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request
from components.cards import section_header, stat_tile, empty_state, status_chip
from appui_state import (
    _collaborator_activity_status,
    _project_can_manage_collaborators,
    _project_can_mutate,
    _project_membership_label,
    _shared_project_activity_warning,
)

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Projects", page_icon="📁", layout="wide")

# Require authentication
user = require_auth(API_URL)

section_header("Projects Dashboard", "Project state, activity, jobs, and storage at a glance", icon="📁")


def _set_active_project(project_id: str, project_name: str, *, open_chat: bool = False) -> None:
    """Persist the selected project as the active chat context."""
    st.session_state["_project_switch_loading_for"] = project_id
    st.session_state["active_project_id"] = project_id
    st.session_state["_project_id_input"] = project_id
    st.session_state["blocks"] = []
    st.session_state["_last_rendered_project"] = project_id
    st.session_state.pop("_welcome_sent_for", None)

    try:
        make_authenticated_request(
            "PUT",
            f"{API_URL}/user/last-project",
            json={"project_id": project_id},
            timeout=3,
        )
    except Exception:
        pass

    st.toast(f"Active project set: {project_name}")
    if open_chat:
        st.switch_page("appUI.py")
    st.rerun()


def _format_timestamp(raw_value: str | None) -> str:
    if not raw_value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(raw_value)[:16] or "—"


def _format_duration(duration_seconds: int | None) -> str:
    if duration_seconds is None:
        return "—"
    total = max(int(duration_seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _status_badge(status: str) -> str:
    normalized = (status or "UNKNOWN").upper()
    return {
        "COMPLETED": "✅ Succeeded",
        "FAILED": "❌ Failed",
        "RUNNING": "⏳ Running",
        "PENDING": "⏳ Pending",
        "STALE": "⚠️ Stale",
        "CANCELLED": "🛑 Cancelled",
        "DELETED": "🗑️ Deleted",
    }.get(normalized, f"❓ {normalized.title()}")


def _project_page_collaborator_summary(*, can_manage: bool, collaborators: list[dict] | None) -> dict | None:
    if not can_manage:
        return None

    visible_collaborators = [
        collaborator
        for collaborator in (collaborators or [])
        if not collaborator.get("is_owner")
    ]
    if not visible_collaborators:
        return None

    count = len(visible_collaborators)
    return {
        "count": count,
        "label": f"Shared with {count} collaborator" + ("s" if count != 1 else ""),
        "lines": [
            f"{str(collaborator.get('email') or '—')} · {str(collaborator.get('role') or 'viewer').title()}"
            for collaborator in visible_collaborators
        ],
    }


def _project_page_collaborator_groups(collaborators: list[dict] | None) -> dict[str, list[str]]:
    grouped = {"owner": [], "editor": [], "viewer": []}
    for collaborator in collaborators or []:
        role_key = "owner" if collaborator.get("is_owner") else str(collaborator.get("role") or "viewer").strip().lower()
        if role_key not in grouped:
            continue
        label = str(collaborator.get("email") or collaborator.get("display_name") or collaborator.get("username") or "—")
        role_label = "Owner" if role_key == "owner" else role_key.title()
        grouped[role_key].append(f"{label} · {role_label}")
    return grouped


def _project_ownership_transfer_candidates(collaborators: list[dict] | None) -> list[dict]:
    candidates = []
    for collaborator in collaborators or []:
        if collaborator.get("is_owner"):
            continue
        role = str(collaborator.get("role") or "viewer").strip().lower()
        if role not in {"viewer", "editor"}:
            continue
        email = str(collaborator.get("email") or collaborator.get("display_name") or collaborator.get("username") or "—")
        user_id = str(collaborator.get("user_id") or "").strip()
        if not user_id:
            continue
        candidates.append({
            "label": f"{email} · {role.title()}",
            "user_id": user_id,
            "role": role,
        })
    candidates.sort(key=lambda item: (0 if item["role"] == "editor" else 1, item["label"].lower()))
    return candidates

# ── Disk Usage Summary ───────────────────────────────────────────────
try:
    disk_resp = make_authenticated_request("GET", f"{API_URL}/user/disk-usage", timeout=5)
    token_resp = make_authenticated_request("GET", f"{API_URL}/user/token-usage", timeout=5)
    col_a, col_b, col_c = st.columns([1, 1, 3])
    if disk_resp.status_code == 200:
        disk = disk_resp.json()
        total_mb = disk.get("total_bytes", 0) / (1024 * 1024)
        with col_a:
            st.metric("Total Disk Usage", f"{total_mb:.1f} MB")
        with col_c:
            breakdown = disk.get("projects", [])
            if breakdown:
                labels = []
                for bp in breakdown[:8]:
                    mb = bp.get("size_bytes", 0) / (1024 * 1024)
                    labels.append(f"**{bp.get('project_id','?')[:8]}…** {mb:.1f} MB")
                st.caption("Per-project: " + " · ".join(labels))
    if token_resp.status_code == 200:
        tok = token_resp.json()
        lifetime = tok.get("lifetime", {})
        with col_b:
            st.metric("Lifetime Tokens Used", f"{lifetime.get('total_tokens', 0):,}")
except Exception:
    pass

st.divider()

# ── Project List ─────────────────────────────────────────────────────
try:
    proj_resp = make_authenticated_request(
        "GET", f"{API_URL}/projects", params={"include_archived": True}, timeout=5
    )
    if proj_resp.status_code != 200:
        st.error(f"Failed to load projects: {proj_resp.status_code}")
        st.stop()
    all_projects = proj_resp.json().get("projects", [])
except Exception as e:
    st.error(f"Cannot connect to API: {e}")
    st.stop()

if not all_projects:
    empty_state("No projects yet", "Create one from the main chat page to get started.", icon="📁")
    st.stop()

# Filter controls
col_filter, col_archived = st.columns([3, 1])
with col_filter:
    search = st.text_input("🔍 Search projects", placeholder="Filter by name…")
with col_archived:
    show_archived = st.checkbox("Show archived", value=False)

filtered = all_projects
if search:
    _s = search.lower()
    filtered = [p for p in filtered if _s in (p.get("name") or "").lower()]
if not show_archived:
    filtered = [p for p in filtered if not p.get("is_archived")]

_visible_count = len(filtered)
_active_count = sum(1 for p in filtered if not p.get("is_archived"))
_archived_count = sum(1 for p in filtered if p.get("is_archived"))
_job_total = sum(int(p.get("job_count") or 0) for p in filtered)

section_header("Overview", "Filtered project footprint and status", icon="📌")
ov1, ov2, ov3, ov4 = st.columns(4)
with ov1:
    stat_tile("Visible Projects", _visible_count, icon="📁")
with ov2:
    stat_tile("Active", _active_count, icon="✅")
with ov3:
    stat_tile("Archived", _archived_count, icon="🗄️")
with ov4:
    stat_tile("Jobs in View", _job_total, icon="🧪")

sc1, sc2 = st.columns(2)
with sc1:
    status_chip("info", label=f"Search: {'On' if bool(search) else 'Off'}", icon="🔍")
with sc2:
    status_chip("warning" if show_archived else "success", label=f"Archived Visible: {'Yes' if show_archived else 'No'}", icon="🧭")

st.divider()

# Build enriched table — fetch per-project stats for disk, messages, files
rows = []
for p in filtered:
    pid = p.get("id", "")
    created = p.get("created_at", "")
    if created and created != "None":
        try:
            dt = datetime.fromisoformat(created)
            created = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    last_active = (p.get("last_accessed") or "")[:16]

    # Fetch stats for this project (disk, messages, files, tokens)
    disk_mb, msg_count, file_count, total_tokens = 0.0, 0, 0, 0
    try:
        sr = make_authenticated_request(
            "GET", f"{API_URL}/projects/{pid}/stats", timeout=5
        )
        if sr.status_code == 200:
            sd = sr.json()
            disk_mb = round(sd.get("disk_usage_bytes", 0) / (1024 * 1024), 2)
            msg_count = sd.get("message_count", 0)
            file_count = sd.get("file_count", 0)
            total_tokens = sd.get("token_usage", {}).get("total_tokens", 0)
    except Exception:
        pass

    rows.append({
        "Archive": False,
        "Delete": False,
        "Name": p.get("name", "—"),
        "Access": _project_membership_label(p),
        "Jobs": p.get("job_count") if p.get("job_count") is not None else 0,
        "Size (MB)": disk_mb,
        "Messages": msg_count,
        "Files": file_count,
        "Tokens": total_tokens,
        "Created": created or "—",
        "Last Active": last_active or "—",
        "Status": "🗄️ Archived" if p.get("is_archived") else "Active",
        "_id": pid,
        "_can_manage": _project_can_manage_collaborators(p, user),
        "_can_mutate": _project_can_mutate(p, user),
    })

# ── Editable project table ───────────────────────────────────────────
if rows:
    import streamlit.column_config as _cc  # noqa: F811

    df = pd.DataFrame(rows)

    edited_df = st.data_editor(
        df.drop(columns=["_id", "_can_manage", "_can_mutate"]),
        width="stretch",
        hide_index=True,
        column_config={
            "Archive": st.column_config.CheckboxColumn("🗄️ Archive", default=False),
            "Delete": st.column_config.CheckboxColumn("🗑️ Delete", default=False),
            "Name": st.column_config.TextColumn("Name", disabled=True),
            "Access": st.column_config.TextColumn("Access", disabled=True),
            "Jobs": st.column_config.NumberColumn("Jobs", disabled=True),
            "Size (MB)": st.column_config.NumberColumn("Size (MB)", format="%.2f", disabled=True),
            "Messages": st.column_config.NumberColumn("Messages", disabled=True),
            "Files": st.column_config.NumberColumn("Files", disabled=True),
            "Tokens": st.column_config.NumberColumn("🪙 Tokens", disabled=True),
            "Created": st.column_config.TextColumn("Created", disabled=True),
            "Last Active": st.column_config.TextColumn("Last Active", disabled=True),
            "Status": st.column_config.TextColumn("Status", disabled=True),
        },
        key="_project_table",
    )

    # Gather selected IDs from the checkboxes
    archive_ids = [rows[i]["_id"] for i in range(len(rows)) if edited_df.iloc[i]["Archive"] and rows[i]["_can_manage"]]
    delete_ids = [rows[i]["_id"] for i in range(len(rows)) if edited_df.iloc[i]["Delete"] and rows[i]["_can_manage"]]
    blocked_archive_ids = [rows[i]["_id"] for i in range(len(rows)) if edited_df.iloc[i]["Archive"] and not rows[i]["_can_manage"]]
    blocked_delete_ids = [rows[i]["_id"] for i in range(len(rows)) if edited_df.iloc[i]["Delete"] and not rows[i]["_can_manage"]]

    if blocked_archive_ids or blocked_delete_ids:
        st.warning("Archive and permanent delete are owner/admin-only project controls. Shared collaborators can view those projects but cannot perform those actions.")

    # Action buttons
    if archive_ids or delete_ids:
        st.caption(
            (f"🗄️ {len(archive_ids)} to archive  " if archive_ids else "")
            + (f"🗑️ {len(delete_ids)} to delete" if delete_ids else "")
        )

        btn1, btn2, btn3 = st.columns(3)
        with btn1:
            if archive_ids and st.button(
                f"🗄️ Archive {len(archive_ids)} project(s)", width="stretch"
            ):
                ok, fail = 0, 0
                for pid in archive_ids:
                    try:
                        r = make_authenticated_request("DELETE", f"{API_URL}/projects/{pid}", timeout=5)
                        ok += 1 if r.status_code == 200 else 0
                        fail += 0 if r.status_code == 200 else 1
                    except Exception:
                        fail += 1
                st.toast(f"Archived {ok}" + (f", {fail} failed" if fail else ""))
                st.rerun()

        with btn2:
            _bulk_del_key = "_confirm_bulk_delete"
            if delete_ids:
                if st.session_state.get(_bulk_del_key):
                    st.warning(f"Permanently delete {len(delete_ids)} project(s)?")
                    cy, cn = st.columns(2)
                    with cy:
                        if st.button("✅ Yes, delete all", key="bulk_yes", type="primary"):
                            ok, fail = 0, 0
                            for pid in delete_ids:
                                try:
                                    r = make_authenticated_request(
                                        "DELETE", f"{API_URL}/projects/{pid}/permanent", timeout=10
                                    )
                                    if r.status_code == 200:
                                        ok += 1
                                        if st.session_state.get("active_project_id") == pid:
                                            st.session_state.pop("active_project_id", None)
                                    else:
                                        fail += 1
                                except Exception:
                                    fail += 1
                            st.session_state.pop(_bulk_del_key, None)
                            st.toast(f"Deleted {ok}" + (f", {fail} failed" if fail else ""))
                            st.rerun()
                    with cn:
                        if st.button("❌ Cancel", key="bulk_no"):
                            st.session_state.pop(_bulk_del_key, None)
                            st.rerun()
                else:
                    if st.button(
                        f"🗑️ Delete {len(delete_ids)} permanently", width="stretch"
                    ):
                        st.session_state[_bulk_del_key] = True
                        st.rerun()
        with btn3:
            pass  # spacer

st.divider()

# ── Project Detail ───────────────────────────────────────────────────
section_header("Project Details", "Inspect project stats, jobs, files, and conversations", icon="🔎")

# Choose which project to inspect
proj_options = {
    f"{p.get('name', p.get('id', '?'))} · {_project_membership_label(p)}": p.get("id")
    for p in filtered
}
if not proj_options:
    st.info("No projects match the filter.")
    st.stop()

selected_name = st.selectbox("Select project", list(proj_options.keys()))
selected_id = proj_options[selected_name]
selected_project = next(
    (project for project in filtered if project.get("id") == selected_id),
    {"id": selected_id, "name": selected_name, "role": "viewer"},
)
selected_project_name = selected_project.get("name", selected_name)
selected_access_label = _project_membership_label(selected_project)
selected_can_manage = _project_can_manage_collaborators(selected_project, user)
selected_can_mutate = _project_can_mutate(selected_project, user)
_current_active_project = st.session_state.get("active_project_id")
selected_collaborators: list[dict] = []
selected_collaborators_error: str | None = None

try:
    selected_collab_resp = make_authenticated_request(
        "GET", f"{API_URL}/projects/{selected_id}/collaborators", timeout=5
    )
    if selected_collab_resp.status_code == 200:
        selected_collaborators = selected_collab_resp.json().get("collaborators", [])
    else:
        selected_collaborators_error = f"Could not load collaborators ({selected_collab_resp.status_code})"
except Exception as e:
    selected_collaborators_error = f"Error: {e}"

selected_collaborator_summary = _project_page_collaborator_summary(
    can_manage=selected_can_manage,
    collaborators=selected_collaborators,
)
selected_collaborator_groups = _project_page_collaborator_groups(selected_collaborators)
selected_transfer_candidates = _project_ownership_transfer_candidates(selected_collaborators)

if _current_active_project == selected_id:
    st.info(f"Active chat project: {selected_project_name}")
else:
    st.caption(
        "Selected project is not the current chat project. "
        "Use the actions below to switch safely without opening appUI first."
    )

status_chip(
    "info" if (selected_project.get("role") == "owner" or user.get("role") == "admin") else "warning",
    label=selected_access_label,
    icon="🏠" if selected_project.get("role") == "owner" else "🤝",
)

if selected_collaborator_summary:
    with st.expander(selected_collaborator_summary["label"], expanded=True):
        st.caption("Visible here so shared-project membership is easy to scan.")
        if selected_collaborator_groups["editor"]:
            st.markdown(f"**Editors ({len(selected_collaborator_groups['editor'])})**")
            for line in selected_collaborator_groups["editor"]:
                st.caption(line)
        if selected_collaborator_groups["viewer"]:
            st.markdown(f"**Viewers ({len(selected_collaborator_groups['viewer'])})**")
            for line in selected_collaborator_groups["viewer"]:
                st.caption(line)
        st.caption("Manage access in the Collaborators tab below.")

switch_col, chat_col = st.columns(2)
with switch_col:
    if st.button("📌 Set Active Project", width="stretch"):
        _set_active_project(selected_id, selected_project_name)
with chat_col:
    if st.button("💬 Open This Project In Chat", width="stretch"):
        _set_active_project(selected_id, selected_project_name, open_chat=True)

# Quick actions row
act1, act2 = st.columns(2)
with act1:
    new_name = st.text_input("Rename", value=selected_project_name, key="rename_input")
    if new_name != selected_project_name and st.button("✏️ Save name", disabled=not selected_can_manage):
        try:
            r = make_authenticated_request(
                "PATCH",
                f"{API_URL}/projects/{selected_id}",
                json={"name": new_name},
                timeout=5,
            )
            if r.status_code == 200:
                result = r.json() or {}
                renamed = result.get("name") or new_name
                new_slug = result.get("slug", "")
                toast_msg = f"Renamed -> {renamed}"
                if new_slug:
                    toast_msg += f" (folder: {new_slug})"
                st.toast(toast_msg)
                st.rerun()
            elif r.status_code == 409:
                detail = "Name already exists"
                try:
                    detail = (r.json() or {}).get("detail") or detail
                except Exception:
                    pass
                st.error(f"Rename failed: {detail}")
            elif r.status_code == 403:
                st.error("Rename failed: owner access required")
            else:
                detail = ""
                try:
                    detail = (r.json() or {}).get("detail") or ""
                except Exception:
                    pass
                suffix = f" ({detail})" if detail else ""
                st.error(f"Rename failed: {r.status_code}{suffix}")
        except Exception as e:
            st.error(str(e))
with act2:
    if st.button("🔄 Refresh"):
        st.rerun()

# Tabs for detail views
tab_stats, tab_jobs, tab_files, tab_convos, tab_collabs = st.tabs(
    ["📈 Stats", "🧪 Jobs", "📂 Files", "💬 Conversations", "🤝 Collaborators"]
)

# ── Stats Tab ────────────────────────────────────────────────────────
with tab_stats:
    try:
        stats_resp = make_authenticated_request(
            "GET", f"{API_URL}/projects/{selected_id}/stats", timeout=5
        )
        if stats_resp.status_code == 200:
            stats = stats_resp.json()
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric("Jobs", stats.get("job_count", 0))
            with c2:
                mb = stats.get("disk_usage_bytes", 0) / (1024 * 1024)
                st.metric("Disk", f"{mb:.1f} MB")
            with c3:
                st.metric("Messages", stats.get("message_count", 0))
            with c4:
                st.metric("Conversations", stats.get("conversation_count", 0))
            with c5:
                st.metric("Stale Jobs", stats.get("stale_count", 0))
        else:
            st.warning(f"Stats unavailable ({stats_resp.status_code})")
    except Exception as e:
        st.warning(f"Could not load stats: {e}")

# ── Jobs Tab ─────────────────────────────────────────────────────────
with tab_jobs:
    try:
        stats_resp = make_authenticated_request(
            "GET", f"{API_URL}/projects/{selected_id}/stats", timeout=5
        )
        if stats_resp.status_code == 200:
            jobs = stats_resp.json().get("jobs", [])
            if jobs:
                job_rows = []
                for j in jobs:
                    status = j.get("status", "UNKNOWN")
                    job_rows.append({
                        "Status": _status_badge(status),
                        "Sample": j.get("sample_name", "—"),
                        "Workflow": j.get("workflow_label", "—") or "—",
                        "Mode": j.get("mode", "—"),
                        "UUID": j.get("run_uuid", "—"),
                        "Submitted": _format_timestamp(j.get("submitted_at")),
                        "Started": _format_timestamp(j.get("started_at")),
                        "Duration": _format_duration(j.get("duration_seconds")),
                    })
                st.dataframe(
                    pd.DataFrame(job_rows),
                    width="stretch",
                    hide_index=True,
                )

                # Quick-view: select a job to analyze
                job_uuids = [j.get("run_uuid") for j in jobs if j.get("run_uuid")]
                if job_uuids:
                    job_options = {}
                    for j in jobs:
                        run_uuid = j.get("run_uuid")
                        if not run_uuid:
                            continue
                        workflow_label = j.get("workflow_label") or "workflow?"
                        label = (
                            f"{_status_badge(j.get('status', 'UNKNOWN'))} · "
                            f"{j.get('sample_name', 'Unknown')} · {workflow_label} · "
                            f"{_format_timestamp(j.get('started_at') or j.get('submitted_at'))} · "
                            f"{run_uuid[:8]}…"
                        )
                        job_options[label] = run_uuid

                    sel_label = st.selectbox("Analyze a job", list(job_options.keys()), key="job_sel")
                    sel_uuid = job_options[sel_label]
                    if st.button("📊 View Results"):
                        # Persist selected UUID so Results pre-fills consistently.
                        st.session_state["selected_job_run_uuid"] = sel_uuid
                        st.switch_page("pages/results.py")

                # Cancel button for RUNNING jobs
                running_jobs = [j for j in jobs if j.get("status") in ("RUNNING", "PENDING")]
                if running_jobs and selected_can_mutate:
                    st.divider()
                    st.subheader("🛑 Cancel a Running Job")
                    cancel_options = {
                        f"⏳ {j.get('sample_name', 'Unknown')} ({j.get('run_uuid', '')[:8]}…)": j.get("run_uuid")
                        for j in running_jobs
                    }
                    cancel_label = st.selectbox("Select job to cancel", list(cancel_options.keys()), key="cancel_sel")
                    cancel_uuid = cancel_options[cancel_label]
                    if st.button("🛑 Cancel Job", type="primary", key="cancel_btn"):
                        try:
                            _resp = make_authenticated_request(
                                "POST", f"{API_URL}/jobs/{cancel_uuid}/cancel", timeout=15
                            )
                            if _resp.status_code == 200:
                                _data = _resp.json()
                                st.success(_data.get("message", "Job cancelled successfully."))
                                st.rerun()
                            else:
                                st.error(f"Cancel failed: {_resp.status_code} — {_resp.text[:200]}")
                        except Exception as _e:
                            st.error(f"Error cancelling job: {_e}")

                failed_jobs = [j for j in jobs if j.get("status") == "FAILED" and j.get("run_uuid")]
                if failed_jobs and selected_can_mutate:
                    st.divider()
                    st.subheader("🗑️ Delete a Failed Run")
                    failed_options = {
                        (
                            f"❌ {j.get('sample_name', 'Unknown')} · "
                            f"{j.get('workflow_label') or 'workflow?'} · "
                            f"{_format_timestamp(j.get('started_at') or j.get('submitted_at'))} · "
                            f"{j.get('run_uuid', '')[:8]}…"
                        ): j.get("run_uuid")
                        for j in failed_jobs
                    }
                    failed_label = st.selectbox("Select failed run to delete", list(failed_options.keys()), key="failed_sel")
                    failed_uuid = failed_options[failed_label]
                    confirm_key = f"delete_failed_confirm_{failed_uuid}"
                    if st.session_state.get(confirm_key):
                        st.warning("This deletes the failed run's workflow folder and archives the job record.")
                        col_yes, col_no = st.columns(2)
                        with col_yes:
                            if st.button("🗑️ Confirm Delete", key=f"failed_del_yes_{failed_uuid}", type="primary"):
                                try:
                                    _resp = make_authenticated_request(
                                        "DELETE", f"{API_URL}/jobs/{failed_uuid}", timeout=30
                                    )
                                    if _resp.status_code == 200:
                                        st.session_state.pop(confirm_key, None)
                                        st.success(_resp.json().get("message", "Failed run deleted."))
                                        st.rerun()
                                    else:
                                        st.error(f"Delete failed: {_resp.status_code} — {_resp.text[:200]}")
                                except Exception as _e:
                                    st.error(f"Error deleting failed run: {_e}")
                        with col_no:
                            if st.button("Keep Run", key=f"failed_del_no_{failed_uuid}"):
                                st.session_state.pop(confirm_key, None)
                                st.rerun()
                    elif st.button("🗑️ Delete Failed Run", key=f"failed_del_btn_{failed_uuid}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
            else:
                st.info("No jobs in this project yet.")
        else:
            st.warning(f"Could not load jobs ({stats_resp.status_code})")
    except Exception as e:
        st.warning(f"Error: {e}")

# ── Files Tab ────────────────────────────────────────────────────────
with tab_files:
    try:
        files_resp = make_authenticated_request(
            "GET", f"{API_URL}/projects/{selected_id}/files", timeout=5
        )
        if files_resp.status_code == 200:
            files = files_resp.json().get("files", [])
            if files:
                file_rows = []
                for f in files:
                    size_kb = (f.get("size_bytes") or f.get("size") or 0) / 1024
                    file_rows.append({
                        "Name": f.get("name", "—"),
                        "Extension": f.get("extension", "—"),
                        "Size (KB)": round(size_kb, 1),
                        "Modified": (f.get("modified") or "")[:16],
                        "Path": f.get("path", ""),
                    })
                st.dataframe(
                    pd.DataFrame(file_rows),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(f"{len(files)} file(s)")
            else:
                st.info("No files in this project directory.")
        elif files_resp.status_code == 404:
            st.info("Project directory does not exist yet (no jobs run).")
        else:
            st.warning(f"Could not load files ({files_resp.status_code})")
    except Exception as e:
        st.warning(f"Error: {e}")

# ── Conversations Tab ────────────────────────────────────────────────
with tab_convos:
    try:
        conv_resp = make_authenticated_request(
            "GET", f"{API_URL}/projects/{selected_id}/conversations", timeout=5
        )
        if conv_resp.status_code == 200:
            convos = conv_resp.json().get("conversations", [])
            if convos:
                for conv in convos:
                    title = conv.get("title", "Untitled")
                    msg_count = conv.get("message_count", "?")
                    created = (conv.get("created_at") or "")[:16]
                    with st.expander(f"💬 {title} ({msg_count} msgs, {created})"):
                        # Load messages
                        msg_resp = make_authenticated_request(
                            "GET",
                            f"{API_URL}/conversations/{conv['id']}/messages",
                            timeout=5,
                        )
                        if msg_resp.status_code == 200:
                            messages = msg_resp.json().get("messages", [])
                            for msg in messages:
                                role = msg.get("role", "user")
                                icon = "🧑" if role == "user" else "🤖"
                                content = msg.get("content", "")[:500]
                                st.markdown(f"{icon} **{role}**: {content}")
                        else:
                            st.warning("Could not load messages")
            else:
                st.info("No conversations in this project yet.")
        else:
            st.warning(f"Could not load conversations ({conv_resp.status_code})")
    except Exception as e:
        st.warning(f"Error: {e}")

# ── Collaborators Tab ───────────────────────────────────────────────
with tab_collabs:
    try:
        if selected_collaborators_error:
            st.warning(selected_collaborators_error)
        else:
            collaborators = selected_collaborators
            active_now_count = sum(
                1
                for collaborator in collaborators
                if _collaborator_activity_status(collaborator)[0] == "active"
            )
            shared_warning = _shared_project_activity_warning(
                collaborators,
                user.get("id"),
                selected_can_mutate,
            )
            st.caption(
                f"Project roster: {len(collaborators)} member(s)"
                + (f" · {active_now_count} active now" if active_now_count else "")
            )
            if shared_warning:
                st.warning(shared_warning)

            if selected_can_manage:
                st.caption("Owner/admin can update roles or remove collaborators from this tab.")
                with st.form(key=f"add_collaborator_{selected_id}"):
                    collaborator_email = st.text_input("Collaborator email", placeholder="name@example.com")
                    collaborator_role = st.selectbox("Role", ["viewer", "editor"], key=f"collab_add_role_{selected_id}")
                    add_submit = st.form_submit_button("Add collaborator")

                if add_submit:
                    add_resp = make_authenticated_request(
                        "POST",
                        f"{API_URL}/projects/{selected_id}/collaborators",
                        json={"email": collaborator_email, "role": collaborator_role},
                        timeout=5,
                    )
                    if add_resp.status_code == 200:
                        st.success(f"Added {collaborator_email} as {collaborator_role}.")
                        st.rerun()
                    else:
                        st.error(add_resp.text)

                if selected_transfer_candidates:
                    st.caption("Transfer ownership to an existing editor or viewer. The current owner becomes an editor.")
                    transfer_options = {item["label"]: item["user_id"] for item in selected_transfer_candidates}
                    with st.form(key=f"transfer_collaborator_owner_{selected_id}"):
                        transfer_target_label = st.selectbox(
                            "Transfer ownership to",
                            list(transfer_options.keys()),
                            key=f"collab_transfer_owner_target_{selected_id}",
                        )
                        transfer_submit = st.form_submit_button("Transfer ownership")

                    if transfer_submit:
                        transfer_resp = make_authenticated_request(
                            "POST",
                            f"{API_URL}/projects/{selected_id}/transfer-ownership",
                            json={"user_id": transfer_options[transfer_target_label]},
                            timeout=10,
                        )
                        if transfer_resp.status_code == 200:
                            st.success(f"Transferred ownership to {transfer_target_label}.")
                            st.rerun()
                        else:
                            st.error(transfer_resp.text)
            else:
                st.caption("Shared collaborators can see the full roster and recent activity here.")

            if selected_collaborator_groups["editor"]:
                st.markdown(f"**Editors ({len(selected_collaborator_groups['editor'])})**")
                for line in selected_collaborator_groups["editor"]:
                    st.caption(line)
            if selected_collaborator_groups["viewer"]:
                st.markdown(f"**Viewers ({len(selected_collaborator_groups['viewer'])})**")
                for line in selected_collaborator_groups["viewer"]:
                    st.caption(line)

            if not collaborators:
                st.info("No collaborators found for this project.")
            else:
                for collaborator in collaborators:
                    collab_role = collaborator.get("role", "viewer")
                    collab_email = collaborator.get("email", "—")
                    collab_user_id = collaborator.get("user_id", "")
                    activity_state, activity_label = _collaborator_activity_status(collaborator)
                    role_label = "Owner" if collaborator.get("is_owner") else collab_role.title()
                    if collab_user_id == user.get("id"):
                        role_label = f"{role_label} · You"
                    col_info, col_edit, col_remove = st.columns([3, 2, 1])
                    with col_info:
                        st.markdown(f"**{collab_email}**")
                        st.caption(f"{role_label} · {activity_label}")
                    if selected_can_manage and collab_role != "owner":
                        with col_edit:
                            next_role = st.selectbox(
                                "Role",
                                ["viewer", "editor"],
                                index=0 if collab_role == "viewer" else 1,
                                key=f"collab_role_{selected_id}_{collab_user_id}",
                            )
                            if st.button("Save", key=f"collab_save_{selected_id}_{collab_user_id}"):
                                update_resp = make_authenticated_request(
                                    "PATCH",
                                    f"{API_URL}/projects/{selected_id}/collaborators/{collab_user_id}",
                                    json={"role": next_role},
                                    timeout=5,
                                )
                                if update_resp.status_code == 200:
                                    st.success(f"Updated {collab_email} to {next_role}.")
                                    st.rerun()
                                else:
                                    st.error(update_resp.text)
                        with col_remove:
                            if st.button("Remove", key=f"collab_remove_{selected_id}_{collab_user_id}"):
                                delete_resp = make_authenticated_request(
                                    "DELETE",
                                    f"{API_URL}/projects/{selected_id}/collaborators/{collab_user_id}",
                                    timeout=5,
                                )
                                if delete_resp.status_code == 200:
                                    st.success(f"Removed {collab_email}.")
                                    st.rerun()
                                else:
                                    st.error(delete_resp.text)
                    else:
                        with col_edit:
                            st.caption(collab_role.title())
                        with col_remove:
                            st.caption("—")
    except Exception as e:
        st.warning(f"Error: {e}")

# Footer
st.divider()
st.caption(f"Connected to AGOUTIC API: {API_URL}")
