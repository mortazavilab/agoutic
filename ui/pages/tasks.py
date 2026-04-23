"""
Cross-project task center.

Surfaces running, resumed, and follow-up work across the user's projects.
"""

import os
import sys
from pathlib import Path

import streamlit as st

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import make_authenticated_request, require_auth
from appui_tasks import (
    count_stale_top_level_tasks,
    count_top_level_tasks,
    get_all_tasks,
    group_tasks_by_project,
    render_project_task_groups,
)
from components.cards import empty_state, info_callout, section_header, stat_tile

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

ACTIVE_SECTION_ORDER = [
    ("running", "🏃 Running"),
    ("follow_up", "🔎 Follow-up"),
    ("pending", "📝 Pending"),
]
COMPLETED_SECTION = [("completed", "✅ Completed")]

st.set_page_config(page_title="Tasks", page_icon="🗂️", layout="wide")

require_auth(API_URL)

section_header(
    "Task Center",
    "Running, resumed, and follow-up work across all of your projects",
    icon="🗂️",
)

filter_col, archive_col = st.columns([3, 2])
with filter_col:
    show_completed = st.toggle("Show completed history", value=False)
with archive_col:
    include_archived = st.toggle("Include archived projects", value=False)

task_collection = get_all_tasks(
    API_URL,
    make_authenticated_request,
    include_archived=include_archived,
)
sections = task_collection.get("sections", {})
section_order = ACTIVE_SECTION_ORDER + (COMPLETED_SECTION if show_completed else [])

project_groups = group_tasks_by_project(sections, section_order)
visible_total = count_top_level_tasks(sections, section_order)
stale_total = count_stale_top_level_tasks(sections, section_order)

metric_a, metric_b, metric_c, metric_d = st.columns(4)
with metric_a:
    stat_tile("Projects In View", len(project_groups), icon="📁")
with metric_b:
    stat_tile("Running", sum(len(group["sections"].get("running", [])) for group in project_groups), icon="🏃")
with metric_c:
    stat_tile("Needs Attention", sum(len(group["sections"].get("follow_up", [])) for group in project_groups), icon="🔎")
with metric_d:
    stat_tile("Possibly Dangling", stale_total, icon="⚠️")

if visible_total == 0:
    empty_state(
        "No active tasks",
        "Nothing is running or waiting right now. Turn on completed history to review finished work.",
        icon="🗂️",
    )
else:
    if stale_total:
        info_callout(
            f"{stale_total} running task(s) were moved out of the Running bucket because no source updates were recorded recently.",
            kind="warning",
            icon="⚠️",
        )
    st.caption(
        f"Scanned {task_collection.get('total_projects', 0)} accessible project(s). "
        "Projects are listed first; task actions still switch into the correct project automatically."
    )
    render_project_task_groups(
        API_URL,
        make_authenticated_request,
        section_order,
        sections=sections,
    )