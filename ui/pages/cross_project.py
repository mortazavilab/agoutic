"""
Cross-Project Browser Page

Provides a secure, additive cross-project experience:
1. Browse accessible projects and their files
2. Search across multiple projects
3. Select files from multiple projects
4. Stage selected files into a destination workspace
5. View staging results with provenance

This is NOT a replacement for project-local browsing. It is an additive layer
for cross-project workflows.
"""

import streamlit as st
import pandas as pd
import sys
import os
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request
from components.cards import section_header, stat_tile, empty_state, status_chip

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Cross-Project Browser", page_icon="🔗", layout="wide")

# Require authentication
user = require_auth(API_URL)


def _format_size(size_bytes: int | None) -> str:
    """Format file size in human-readable form."""
    if size_bytes is None:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_timestamp(raw_value: str | None) -> str:
    """Format ISO timestamp to readable form."""
    if not raw_value:
        return "—"
    try:
        return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(raw_value)[:16] or "—"


# Session state initialization
if "cross_project_selection" not in st.session_state:
    st.session_state.cross_project_selection = []  # List of {project_id, relative_path, name, ...}

if "current_browse_project" not in st.session_state:
    st.session_state.current_browse_project = None

if "current_subpath" not in st.session_state:
    st.session_state.current_subpath = ""


# Header
section_header(
    "Cross-Project Browser",
    "Browse, search, and select files across your projects. Stage files into a workspace before analysis.",
    icon="🔗"
)


# Layer 1: Project List
section_header("Accessible Projects", "Your projects available for cross-project browsing", icon="📁")

try:
    proj_resp = make_authenticated_request(
        "GET", f"{API_URL}/cross-project/projects", timeout=10
    )
    if proj_resp.status_code != 200:
        st.error(f"Failed to load projects: {proj_resp.status_code}")
        st.stop()
    projects = proj_resp.json()
except Exception as e:
    st.error(f"Cannot connect to API: {e}")
    st.stop()

if not projects:
    empty_state("No projects found", "Create a project from the main chat to get started.", icon="📁")
    st.stop()

# Filter out archived projects by default
archived_toggle = st.checkbox("Show archived projects", value=False)
visible_projects = [p for p in projects if archived_toggle or not p.get("is_archived")]

# Quick stats
c1, c2, c3 = st.columns(3)
with c1:
    stat_tile("Total Projects", len(visible_projects), icon="📂")
with c2:
    stat_tile("Selected Files", len(st.session_state.cross_project_selection), icon="✅")
with c3:
    unique_selected_projects = len(set(s["project_id"] for s in st.session_state.cross_project_selection))
    stat_tile("From Projects", unique_selected_projects, icon="🔗")

st.divider()


# Tabs for Browse vs Search vs Selection
tab_browse, tab_search, tab_selection = st.tabs(["📂 Browse", "🔍 Search", "✅ Selection"])


# ---------------------------------------------------------------------------
# Browse Tab
# ---------------------------------------------------------------------------
with tab_browse:
    # Project selection
    project_options = {
        f"{p['project_name']} ({p['role']})": p['project_id']
        for p in visible_projects
    }

    col_proj, col_nav = st.columns([2, 1])

    with col_proj:
        selected_label = st.selectbox(
            "Select project to browse",
            list(project_options.keys()),
            key="browse_project_select"
        )
        selected_project_id = project_options[selected_label]
        st.session_state.current_browse_project = selected_project_id

    with col_nav:
        subpath = st.text_input(
            "Path within project",
            value=st.session_state.current_subpath,
            placeholder="e.g., workflow1/results",
            key="browse_subpath"
        )
        st.session_state.current_subpath = subpath

    # Controls row
    ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 2])
    with ctrl1:
        recursive = st.checkbox("Recursive", value=False)
    with ctrl2:
        limit = st.number_input("Limit", min_value=10, max_value=1000, value=100)

    # Load files
    try:
        params = {
            "subpath": subpath,
            "recursive": str(recursive).lower(),
            "limit": limit,
        }
        browse_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/cross-project/projects/{selected_project_id}/browse",
            params=params,
            timeout=30
        )

        if browse_resp.status_code == 200:
            browse_data = browse_resp.json()
            items = browse_data.get("items", [])
            total = browse_data.get("total_count", 0)
            truncated = browse_data.get("truncated", False)

            if truncated:
                st.warning(f"Results truncated: showing {len(items)} of {total} items")
            else:
                st.caption(f"Found {total} items")

            if items:
                # Build selection checkboxes
                file_rows = []
                for item in items:
                    is_selected = any(
                        s["project_id"] == item["project_id"] and s["relative_path"] == item["relative_path"]
                        for s in st.session_state.cross_project_selection
                    )
                    file_rows.append({
                        "Select": is_selected,
                        "Name": item["name"],
                        "Type": item["file_type"],
                        "Category": item["category"],
                        "Workflow": item.get("workflow_folder") or "—",
                        "Size": _format_size(item.get("size")),
                        "Modified": _format_timestamp(item.get("modified_time")),
                        "Path": item["relative_path"],
                        "_is_dir": item["is_dir"],
                        "_project_id": item["project_id"],
                        "_project_name": item["project_name"],
                    })

                df = pd.DataFrame(file_rows)

                edited_df = st.data_editor(
                    df.drop(columns=["_is_dir", "_project_id", "_project_name"]),
                    hide_index=True,
                    column_config={
                        "Select": st.column_config.CheckboxColumn("✅", default=False),
                        "Name": st.column_config.TextColumn("Name", disabled=True),
                        "Type": st.column_config.TextColumn("Type", disabled=True),
                        "Category": st.column_config.TextColumn("Category", disabled=True),
                        "Workflow": st.column_config.TextColumn("Workflow", disabled=True),
                        "Size": st.column_config.TextColumn("Size", disabled=True),
                        "Modified": st.column_config.TextColumn("Modified", disabled=True),
                        "Path": st.column_config.TextColumn("Path", disabled=True),
                    },
                    key="browse_table",
                )

                # Update selection state
                if st.button("Update Selection", key="browse_update_selection"):
                    # Remove deselected items
                    for idx, row in df.iterrows():
                        was_selected = row["Select"]
                        now_selected = edited_df.iloc[idx]["Select"]

                        item_key = (row["_project_id"], row["Path"])

                        if was_selected and not now_selected:
                            # Remove from selection
                            st.session_state.cross_project_selection = [
                                s for s in st.session_state.cross_project_selection
                                if not (s["project_id"] == row["_project_id"] and s["relative_path"] == row["Path"])
                            ]

                        elif not was_selected and now_selected and not row["_is_dir"]:
                            # Add to selection (files only)
                            already_in = any(
                                s["project_id"] == row["_project_id"] and s["relative_path"] == row["Path"]
                                for s in st.session_state.cross_project_selection
                            )
                            if not already_in:
                                st.session_state.cross_project_selection.append({
                                    "project_id": row["_project_id"],
                                    "project_name": row["_project_name"],
                                    "relative_path": row["Path"],
                                    "name": row["Name"],
                                    "file_type": row["Type"],
                                })

                    st.rerun()
            else:
                st.info("No files found in this location.")

        elif browse_resp.status_code == 403:
            st.error("You don't have access to this project.")
        elif browse_resp.status_code == 400:
            st.error(f"Invalid path: {browse_resp.json().get('detail', 'Unknown error')}")
        else:
            st.error(f"Failed to browse: {browse_resp.status_code}")

    except Exception as e:
        st.error(f"Error browsing project: {e}")


# ---------------------------------------------------------------------------
# Search Tab
# ---------------------------------------------------------------------------
with tab_search:
    col_q, col_filter = st.columns([3, 1])

    with col_q:
        search_query = st.text_input(
            "Search term",
            placeholder="Enter filename pattern (e.g., .bam, results)",
            key="search_query"
        )

    with col_filter:
        file_type_filter = st.selectbox(
            "File type filter",
            ["All", "alignment", "sequence", "variant", "annotation", "tabular", "report"],
            key="file_type_filter"
        )

    # Multi-select for projects
    project_multi = st.multiselect(
        "Limit to specific projects (optional)",
        options=[f"{p['project_name']} ({p['project_id'][:8]}...)" for p in visible_projects],
        key="search_project_filter"
    )

    search_limit = st.number_input("Max results", min_value=10, max_value=1000, value=200)

    if search_query and st.button("Search", key="run_search"):
        try:
            params = {
                "q": search_query,
                "limit": search_limit,
            }

            # Add file type filter
            if file_type_filter != "All":
                params["file_type"] = file_type_filter

            # Add project filter
            if project_multi:
                # Extract project IDs from selection
                filtered_ids = []
                for sel in project_multi:
                    for p in visible_projects:
                        if sel.startswith(p["project_name"]):
                            filtered_ids.append(p["project_id"])
                            break
                if filtered_ids:
                    params["project_ids"] = ",".join(filtered_ids)

            search_resp = make_authenticated_request(
                "GET",
                f"{API_URL}/cross-project/search",
                params=params,
                timeout=60
            )

            if search_resp.status_code == 200:
                search_data = search_resp.json()
                items = search_data.get("items", [])
                total = search_data.get("total_count", 0)
                truncated = search_data.get("truncated", False)

                if truncated:
                    st.warning(f"Results truncated: showing {len(items)} of {total} matches")
                else:
                    st.success(f"Found {total} matching files")

                if items:
                    search_rows = []
                    for item in items:
                        is_selected = any(
                            s["project_id"] == item["project_id"] and s["relative_path"] == item["relative_path"]
                            for s in st.session_state.cross_project_selection
                        )
                        search_rows.append({
                            "Select": is_selected,
                            "Project": item["project_name"],
                            "Name": item["name"],
                            "Type": item["file_type"],
                            "Workflow": item.get("workflow_folder") or "—",
                            "Size": _format_size(item.get("size")),
                            "Path": item["relative_path"],
                            "_project_id": item["project_id"],
                        })

                    sdf = pd.DataFrame(search_rows)
                    edited_sdf = st.data_editor(
                        sdf.drop(columns=["_project_id"]),
                        hide_index=True,
                        column_config={
                            "Select": st.column_config.CheckboxColumn("✅", default=False),
                        },
                        key="search_table",
                    )

                    if st.button("Add Selected to Selection", key="search_add_selection"):
                        for idx, row in sdf.iterrows():
                            was_selected = row["Select"]
                            now_selected = edited_sdf.iloc[idx]["Select"]

                            if now_selected and not was_selected:
                                already_in = any(
                                    s["project_id"] == row["_project_id"] and s["relative_path"] == row["Path"]
                                    for s in st.session_state.cross_project_selection
                                )
                                if not already_in:
                                    st.session_state.cross_project_selection.append({
                                        "project_id": row["_project_id"],
                                        "project_name": row["Project"],
                                        "relative_path": row["Path"],
                                        "name": row["Name"],
                                        "file_type": row["Type"],
                                    })

                        st.rerun()
                else:
                    st.info("No files match your search.")
            else:
                st.error(f"Search failed: {search_resp.status_code}")

        except Exception as e:
            st.error(f"Search error: {e}")


# ---------------------------------------------------------------------------
# Selection Tab
# ---------------------------------------------------------------------------
with tab_selection:
    selection = st.session_state.cross_project_selection

    if not selection:
        empty_state(
            "No files selected",
            "Use the Browse or Search tabs to select files from your projects.",
            icon="✅"
        )
    else:
        st.subheader(f"Selected Files ({len(selection)})")

        # Group by project
        by_project = {}
        for s in selection:
            pname = s.get("project_name", s["project_id"][:8])
            if pname not in by_project:
                by_project[pname] = []
            by_project[pname].append(s)

        for pname, files in by_project.items():
            with st.expander(f"**{pname}** ({len(files)} files)", expanded=True):
                for f in files:
                    col_info, col_action = st.columns([4, 1])
                    with col_info:
                        st.text(f"{f['name']} ({f.get('file_type', 'unknown')})")
                        st.caption(f.get("relative_path", ""))
                    with col_action:
                        if st.button("❌", key=f"remove_{f['project_id']}_{f['relative_path']}"):
                            st.session_state.cross_project_selection = [
                                s for s in selection
                                if not (s["project_id"] == f["project_id"] and s["relative_path"] == f["relative_path"])
                            ]
                            st.rerun()

        st.divider()

        # Clear all
        if st.button("Clear All Selections", type="secondary"):
            st.session_state.cross_project_selection = []
            st.rerun()

        st.divider()

        # --- Staging Action ---
        section_header("Stage to Workspace", "Create a staging workspace with selected files", icon="📦")

        action_type = st.radio(
            "Action type",
            ["stage_workspace", "analyze_together", "compare_together"],
            format_func=lambda x: {
                "stage_workspace": "Stage to workspace (no analysis)",
                "analyze_together": "Stage for combined analysis",
                "compare_together": "Stage for comparison analysis",
            }.get(x, x),
            horizontal=True
        )

        dest_method = st.radio(
            "Destination",
            ["existing", "new"],
            format_func=lambda x: {
                "existing": "Use existing project",
                "new": "Create new project",
            }.get(x, x),
            horizontal=True
        )

        if dest_method == "existing":
            dest_options = {
                f"{p['project_name']}": p['project_id']
                for p in visible_projects
                if p.get("role") in ("owner", "editor")
            }
            if not dest_options:
                st.warning("No projects available where you have editor access.")
            else:
                dest_label = st.selectbox("Destination project", list(dest_options.keys()))
                dest_project_id = dest_options[dest_label]
                dest_project_name = None
        else:
            dest_project_id = None
            dest_project_name = st.text_input(
                "New project name",
                placeholder="e.g., Cross-Project Analysis 2026-03"
            )

        # Stage button
        if st.button("Stage Selected Files", type="primary", disabled=len(selection) == 0):
            if dest_method == "new" and not dest_project_name:
                st.error("Please enter a name for the new project.")
            else:
                try:
                    payload = {
                        "selected_files": [
                            {"source_project_id": s["project_id"], "relative_path": s["relative_path"]}
                            for s in selection
                        ],
                        "action_type": action_type,
                    }

                    if dest_project_id:
                        payload["destination_project_id"] = dest_project_id
                    else:
                        payload["destination_project_name"] = dest_project_name

                    stage_resp = make_authenticated_request(
                        "POST",
                        f"{API_URL}/cross-project/stage",
                        json=payload,
                        timeout=120
                    )

                    if stage_resp.status_code == 200:
                        result = stage_resp.json()
                        st.success(f"Staging complete! Stage ID: {result['stage_id']}")

                        st.info(f"""
                        **Staging Summary:**
                        - Destination: {result['destination_project_name']}
                        - Files staged: {result['item_count']}
                        - Staging mode: {result['staging_mode']}
                        - Manifest: {result['manifest_path']}
                        - Action type: {result['action_type']}
                        """)

                        # Clear selection after successful staging
                        if st.button("Clear selection and continue"):
                            st.session_state.cross_project_selection = []
                            st.rerun()

                    elif stage_resp.status_code == 422:
                        detail = stage_resp.json().get("detail", "Validation error")
                        st.error(f"Incompatible selection: {detail}")
                    elif stage_resp.status_code == 403:
                        st.error("You don't have access to stage to the selected destination.")
                    else:
                        st.error(f"Staging failed: {stage_resp.status_code} - {stage_resp.text}")

                except Exception as e:
                    st.error(f"Staging error: {e}")

# Footer
st.divider()
st.caption(f"Connected to AGOUTIC API: {API_URL}")
