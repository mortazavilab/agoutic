"""
Memories Page — Read-only browser for project and global memories.
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
_projects = _projects_resp.json() if _projects_resp and _projects_resp.status_code == 200 else []

if not _projects:
    empty_state("No projects found", "Create a project to start using memories.")
    st.stop()

_project_options = {"— All memories —": "__all__"}
for p in _projects:
    if isinstance(p, dict) and "name" in p:
        _project_options[p["name"]] = p["id"]
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
        options=["All", "result", "sample_annotation", "pipeline_step", "preference", "finding", "custom"],
        index=0,
    )

filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    pinned_only = st.checkbox("Pinned only", value=False)
with filter_col2:
    show_deleted = st.checkbox("Show deleted", value=False)
with filter_col3:
    include_global = st.checkbox("Include global", value=True)

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
    scope_icon = "🌐" if mem.get("project_id") is None else "📁"

    with st.container():
        cols = st.columns([0.5, 6, 2, 2])
        with cols[0]:
            st.markdown(f"{scope_icon}")
        with cols[1]:
            content = mem.get("content", "")
            st.markdown(f"{pin_icon}{deleted}{content}{deleted}")
        with cols[2]:
            st.caption(_category_badge(mem.get("category", "custom")))
            st.caption(_source_badge(mem.get("source", "")))
        with cols[3]:
            st.caption(f"`{mem.get('id', '')[:8]}`")
            st.caption(_format_timestamp(mem.get("created_at")))
        st.divider()
