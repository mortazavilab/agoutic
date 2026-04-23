"""
Memories Page — Browser with create, delete, and upgrade-to-global actions.
All requests go through Cortex — the UI never contacts backend servers directly.
"""

import streamlit as st
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request
from components.cards import section_header, empty_state

# Cortex API URL (the only server the UI talks to)
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Memories", page_icon="🧠", layout="wide")

# Require authentication
user = require_auth(API_URL)

section_header("Memories", "Project notes, sample annotations, and captured results", icon="🧠")


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


def _category_badge(category: str) -> str:
    badges = {
        "result": "🏆 Result",
        "sample_annotation": "🏷️ Sample",
        "pipeline_step": "⚙️ Step",
        "preference": "⚙️ Preference",
        "finding": "💡 Finding",
        "custom": "📝 Note",
        "dataframe": "📊 DataFrame",
    }
    return badges.get(category, f"📝 {category}")


def _source_badge(source: str) -> str:
    badges = {
        "user_manual": "👤 Manual",
        "auto_step": "🤖 Auto",
        "auto_result": "🤖 Auto",
        "system": "⚙️ System",
    }
    return badges.get(source, source)


# ── Project selector ───────────────────────────────────────────────────

# Get user's projects for selector
_projects_resp = make_authenticated_request("GET", f"{API_URL}/projects", timeout=10)
_projects_raw = _projects_resp.json() if _projects_resp and _projects_resp.status_code == 200 else {}
_projects = _projects_raw.get("projects", []) if isinstance(_projects_raw, dict) else _projects_raw

if not _projects:
    empty_state("No projects found", "Create a project to start using memories.")
    st.stop()

# Build project lookup: id → display name (prefer slug, fall back to name)
_project_id_to_name: dict[str | None, str] = {}
_project_options = {"— All memories —": "__all__"}
for p in _projects:
    if isinstance(p, dict) and "name" in p:
        display = p.get("slug") or p["name"]
        _project_options[display] = p["id"]
        _project_id_to_name[p["id"]] = display
_project_options["— Global memories only —"] = None

col1, col2 = st.columns([3, 1])
with col1:
    selected_project_name = st.selectbox(
        "Project",
        options=list(_project_options.keys()),
        index=0,
    )
selected_project_id = _project_options.get(selected_project_name)
_show_all = selected_project_id == "__all__"

# ── Filters ────────────────────────────────────────────────────────────

with col2:
    category_filter = st.selectbox(
        "Category",
        options=["All", "result", "sample_annotation", "pipeline_step", "preference", "finding", "custom", "dataframe"],
        index=0,
    )

filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    pinned_only = st.checkbox("Pinned only", value=False)
with filter_col2:
    show_deleted = st.checkbox("Show deleted", value=False)
with filter_col3:
    include_global = st.checkbox("Include global", value=True)

# ── Add Memory ─────────────────────────────────────────────────────────

with st.expander("➕ Add a memory"):
    add_col1, add_col2 = st.columns([3, 1])
    with add_col1:
        new_content = st.text_area("Content", placeholder="Enter memory text...", key="new_mem_content", height=80)
    with add_col2:
        new_category = st.selectbox(
            "Category ",  # trailing space to avoid key clash
            options=["custom", "result", "sample_annotation", "pipeline_step", "preference", "finding", "dataframe"],
            index=0,
            key="new_mem_category",
        )
        # Build scope choices: global + each project
        _scope_labels = {"🌐 Global": None}
        for p in _projects:
            if isinstance(p, dict) and "name" in p:
                display = p.get("slug") or p["name"]
                _scope_labels[f"📁 {display}"] = p["id"]
        # Default to current project selection if a specific project is selected
        _scope_keys = list(_scope_labels.keys())
        _default_scope = 0
        if selected_project_id and selected_project_id != "__all__":
            for i, lbl in enumerate(_scope_keys):
                if _scope_labels[lbl] == selected_project_id:
                    _default_scope = i
                    break
        new_scope_label = st.selectbox("Scope", options=_scope_keys, index=_default_scope, key="new_mem_scope")
        new_project_id = _scope_labels[new_scope_label]

    if st.button("Save memory", type="primary", disabled=not new_content):
        body = {"content": new_content, "category": new_category}
        if new_project_id is not None:
            body["project_id"] = new_project_id
        resp = make_authenticated_request("POST", f"{API_URL}/memories", json=body, timeout=10)
        if resp and resp.status_code == 201:
            st.success("Memory saved!")
            st.rerun()
        else:
            detail = ""
            try:
                detail = resp.json().get("detail", "") if resp else ""
            except Exception:
                pass
            st.error(f"Failed to save memory. {detail}")

# ── Fetch memories ─────────────────────────────────────────────────────

params = {
    "include_global": include_global,
    "include_deleted": show_deleted,
    "pinned_only": pinned_only,
    "limit": 200,
}
if _show_all:
    params["show_all"] = True
elif selected_project_id:
    params["project_id"] = selected_project_id
if category_filter != "All":
    params["category"] = category_filter

resp = make_authenticated_request("GET", f"{API_URL}/memories", params=params, timeout=10)

if not resp or resp.status_code != 200:
    st.error("Failed to load memories.")
    st.stop()

data = resp.json()
memories = data.get("memories", [])

if not memories:
    empty_state("No memories yet", "Use `/remember <text>` in the chat or say \"remember that...\" to create one.")
    st.stop()

# ── Display ────────────────────────────────────────────────────────────

st.markdown(f"**{len(memories)}** memories")

for mem in memories:
    pin_icon = "⭐ " if mem.get("is_pinned") else ""
    deleted = "~~" if mem.get("is_deleted") else ""
    is_global = mem.get("project_id") is None
    scope_icon = "🌐" if is_global else "📁"
    if is_global:
        scope_label = "Global"
    else:
        pid = mem.get("project_id", "")
        scope_label = _project_id_to_name.get(pid) or f"Project {pid[:8]}"

    with st.container():
        cols = st.columns([0.5, 5, 2, 2, 1.5])
        with cols[0]:
            st.markdown(f"{scope_icon}")
        with cols[1]:
            content = mem.get("content", "")
            st.markdown(f"{pin_icon}{deleted}{content}{deleted}")
            st.caption(f"{scope_icon} {scope_label}")
            # Show DF preview for dataframe memories
            if mem.get("category") == "dataframe" and mem.get("structured_data"):
                sd = mem["structured_data"]
                if isinstance(sd, dict) and sd.get("columns"):
                    cols_str = ", ".join(sd["columns"][:8])
                    if len(sd.get("columns", [])) > 8:
                        cols_str += f" ... +{len(sd['columns']) - 8} more"
                    st.caption(f"Columns: {cols_str}")
                    tags = mem.get("tags") or {}
                    df_name = tags.get("df_name")
                    if df_name:
                        st.caption(f"Name: **{df_name}**")
                    else:
                        st.caption("⚠️ Unnamed — name it to upgrade globally")
        with cols[2]:
            st.caption(_category_badge(mem.get("category", "custom")))
            st.caption(_source_badge(mem.get("source", "")))
        with cols[3]:
            st.caption(f"`{mem.get('id', '')[:8]}`")
            st.caption(_format_timestamp(mem.get("created_at")))
        with cols[4]:
            mem_id = mem.get("id", "")
            if mem.get("is_deleted"):
                if st.button("♻️", key=f"restore_{mem_id}", help="Restore"):
                    r = make_authenticated_request("POST", f"{API_URL}/memories/{mem_id}/restore", timeout=10)
                    if r and r.status_code == 200:
                        st.rerun()
                    else:
                        st.error("Restore failed")
            else:
                if st.button("🗑️", key=f"del_{mem_id}", help="Delete"):
                    r = make_authenticated_request("DELETE", f"{API_URL}/memories/{mem_id}", timeout=10)
                    if r and r.status_code == 200:
                        st.rerun()
                    else:
                        st.error("Delete failed")
                if not is_global:
                    # Unnamed DF memories cannot be upgraded
                    is_unnamed_df = (
                        mem.get("category") == "dataframe"
                        and not (mem.get("tags") or {}).get("df_name")
                    )
                    if is_unnamed_df:
                        st.button("🌐", key=f"upgrade_{mem_id}", help="Name this DF first", disabled=True)
                    else:
                        if st.button("🌐", key=f"upgrade_{mem_id}", help="Upgrade to global"):
                            r = make_authenticated_request("POST", f"{API_URL}/memories/{mem_id}/upgrade-to-global", timeout=10)
                            if r and r.status_code == 200:
                                st.rerun()
                            else:
                                detail = ""
                                try:
                                    detail = r.json().get("detail", "") if r else ""
                                except Exception:
                                    pass
                                st.error(f"Upgrade failed. {detail}")
        st.divider()
