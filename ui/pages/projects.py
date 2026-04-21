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

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Projects", page_icon="📁", layout="wide")

# Require authentication
user = require_auth(API_URL)

section_header("Projects Dashboard", "Project state, activity, jobs, and storage at a glance", icon="📁")


def _set_active_project(project_id: str, project_name: str, *, open_chat: bool = False) -> None:
    """Persist the selected project as the active chat context."""
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
        "CANCELLED": "🛑 Cancelled",
        "DELETED": "🗑️ Deleted",
    }.get(normalized, f"❓ {normalized.title()}")

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
        "Jobs": p.get("job_count") if p.get("job_count") is not None else 0,
        "Size (MB)": disk_mb,
        "Messages": msg_count,
        "Files": file_count,
        "Tokens": total_tokens,
        "Created": created or "—",
        "Last Active": last_active or "—",
        "Status": "🗄️ Archived" if p.get("is_archived") else "Active",
        "_id": pid,
    })

# ── Editable project table ───────────────────────────────────────────
if rows:
    import streamlit.column_config as _cc  # noqa: F811

    df = pd.DataFrame(rows)

    edited_df = st.data_editor(
        df.drop(columns=["_id"]),
        width="stretch",
        hide_index=True,
        column_config={
            "Archive": st.column_config.CheckboxColumn("🗄️ Archive", default=False),
            "Delete": st.column_config.CheckboxColumn("🗑️ Delete", default=False),
            "Name": st.column_config.TextColumn("Name", disabled=True),
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
    archive_ids = [rows[i]["_id"] for i in range(len(rows)) if edited_df.iloc[i]["Archive"]]
    delete_ids = [rows[i]["_id"] for i in range(len(rows)) if edited_df.iloc[i]["Delete"]]

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
proj_options = {p.get("name", p.get("id", "?")): p.get("id") for p in filtered}
if not proj_options:
    st.info("No projects match the filter.")
    st.stop()

selected_name = st.selectbox("Select project", list(proj_options.keys()))
selected_id = proj_options[selected_name]
_current_active_project = st.session_state.get("active_project_id")

if _current_active_project == selected_id:
    st.info(f"Active chat project: {selected_name}")
else:
    st.caption(
        "Selected project is not the current chat project. "
        "Use the actions below to switch safely without opening appUI first."
    )

switch_col, chat_col = st.columns(2)
with switch_col:
    if st.button("📌 Set Active Project", width="stretch"):
        _set_active_project(selected_id, selected_name)
with chat_col:
    if st.button("💬 Open This Project In Chat", width="stretch"):
        _set_active_project(selected_id, selected_name, open_chat=True)

# Quick actions row
act1, act2 = st.columns(2)
with act1:
    new_name = st.text_input("Rename", value=selected_name, key="rename_input")
    if new_name != selected_name and st.button("✏️ Save name"):
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
tab_stats, tab_jobs, tab_files, tab_convos = st.tabs(
    ["📈 Stats", "🧪 Jobs", "📂 Files", "💬 Conversations"]
)

# ── Stats Tab ────────────────────────────────────────────────────────
with tab_stats:
    try:
        stats_resp = make_authenticated_request(
            "GET", f"{API_URL}/projects/{selected_id}/stats", timeout=5
        )
        if stats_resp.status_code == 200:
            stats = stats_resp.json()
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Jobs", stats.get("job_count", 0))
            with c2:
                mb = stats.get("disk_usage_bytes", 0) / (1024 * 1024)
                st.metric("Disk", f"{mb:.1f} MB")
            with c3:
                st.metric("Messages", stats.get("message_count", 0))
            with c4:
                st.metric("Conversations", stats.get("conversation_count", 0))
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
                if running_jobs:
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
                if failed_jobs:
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

# Footer
st.divider()
st.caption(f"Connected to AGOUTIC API: {API_URL}")
