"""My Data inventory and central data management."""

import streamlit as st
import pandas as pd
import sys
import os
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request
from components.cards import section_header, stat_tile, empty_state, status_chip

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="My Data", page_icon="🗄️", layout="wide")

# Require authentication
user = require_auth(API_URL)

section_header(
    "My Data",
    "Inspect local, staged, imported, workflow, and project file inventories alongside your shared central data folder.",
    icon="🗄️",
)


# ---------------------------------------------------------------------------
# Fetch data
# ---------------------------------------------------------------------------

@st.cache_data(ttl=10)
def _fetch_user_files(include_untracked: bool = True):
    q = "true" if include_untracked else "false"
    resp = make_authenticated_request("GET", f"{API_URL}/user/data?include_untracked={q}")
    if resp and resp.status_code == 200:
        return resp.json()
    return {"files": [], "count": 0}


@st.cache_data(ttl=30)
def _fetch_projects():
    resp = make_authenticated_request("GET", f"{API_URL}/projects")
    if resp and resp.status_code == 200:
        return resp.json()
    return []


@st.cache_data(ttl=15)
def _fetch_inventory(endpoint: str, params: tuple[tuple[str, object], ...] = ()):
    query_params = {key: value for key, value in params if value not in (None, "")}
    resp = make_authenticated_request("GET", f"{API_URL}/inventory/{endpoint}", params=query_params or None)
    if resp and resp.status_code == 200:
        return resp.json()
    detail = resp.text if resp is not None else "no response"
    return {"items": [], "count": 0, "error": detail}


def _normalize_projects(project_payload) -> list[dict]:
    if isinstance(project_payload, list):
        return [item for item in project_payload if isinstance(item, dict)]
    if isinstance(project_payload, dict):
        items = project_payload.get("projects", [])
        return [item for item in items if isinstance(item, dict)]
    return []


def _display_value(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _render_inventory_table(
    rows: list[dict],
    columns: list[tuple[str, str]],
    *,
    empty_title: str,
    empty_text: str,
    icon: str,
):
    if not rows:
        empty_state(empty_title, empty_text, icon=icon)
        return

    table_rows = []
    for row in rows:
        table_rows.append({label: _display_value(row.get(key)) for key, label in columns})

    frame = pd.DataFrame(table_rows)
    st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        height=min(420, 40 + 35 * len(frame)),
    )


project_rows = _normalize_projects(_fetch_projects())
project_name_by_id = {
    str(project.get("id") or ""): str(project.get("name") or project.get("id") or "")
    for project in project_rows
    if project.get("id")
}
active_project_id = str(st.session_state.get("active_project_id") or "").strip()
active_project_name = project_name_by_id.get(active_project_id) or active_project_id

local_samples_payload = _fetch_inventory("samples")
staged_payload = _fetch_inventory("staged")
imported_payload = _fetch_inventory("imported")
workflow_payload = (
    _fetch_inventory("workflows", params=(("project_id", active_project_id),))
    if active_project_id
    else {"items": [], "count": 0}
)

summary_col1, summary_col2, summary_col3, summary_col4, summary_col5 = st.columns(5)
with summary_col1:
    stat_tile("Local Samples", int(local_samples_payload.get("count") or 0), icon="🧬")
with summary_col2:
    stat_tile("Staged Samples", int(staged_payload.get("count") or 0), icon="🌐")
with summary_col3:
    stat_tile("Imported", int(imported_payload.get("count") or 0), icon="📥")
with summary_col4:
    stat_tile("Workflows", int(workflow_payload.get("count") or 0), icon="🧪")
with summary_col5:
    stat_tile("Active Project", active_project_name or "None", icon="📁")

status_col1, status_col2 = st.columns(2)
with status_col1:
    if active_project_id:
        status_chip("success", label=f"Project inventory scoped to {active_project_name}", icon="📁")
    else:
        status_chip("warning", label="Select an active project to browse workflows and project files", icon="📁")
with status_col2:
    status_chip("info", label="Chat `/list ...` and this page use the same Cortex inventory backend", icon="🧭")

tab_samples, tab_staged, tab_imported, tab_workflows, tab_files = st.tabs(
    ["Local Samples", "Staged Samples", "Imported Samples", "Workflows", "Files"]
)

with tab_samples:
    _render_inventory_table(
        local_samples_payload.get("items", []),
        [
            ("sample_name", "Sample"),
            ("file_count", "Files"),
            ("total_size", "Total Size"),
            ("sources", "Sources"),
            ("projects", "Projects"),
            ("added", "Added"),
        ],
        empty_title="No local samples yet",
        empty_text="Local samples appear here when files are downloaded or uploaded into your central data folder.",
        icon="🧬",
    )

with tab_staged:
    staged_rows = list(staged_payload.get("items", []))
    profile_options = ["All profiles"] + sorted(
        {
            str(item.get("profile") or "").strip()
            for item in staged_rows
            if str(item.get("profile") or "").strip() and str(item.get("profile") or "").strip() != "—"
        }
    )
    selected_profile = st.selectbox("Profile", profile_options, key="my_data_staged_profile")
    if selected_profile != "All profiles":
        staged_rows = [row for row in staged_rows if row.get("profile") == selected_profile]
    _render_inventory_table(
        staged_rows,
        [
            ("sample_name", "Sample"),
            ("mode", "Mode"),
            ("profile", "Profile"),
            ("status", "Status"),
            ("remote_data_path", "Remote Data Path"),
            ("last_staged", "Last Staged"),
            ("last_used", "Last Used"),
        ],
        empty_title="No staged samples found",
        empty_text="Remote staged samples will appear here after you stage data onto a remote profile.",
        icon="🌐",
    )

with tab_imported:
    _render_inventory_table(
        imported_payload.get("items", []),
        [
            ("sample_name", "Sample"),
            ("project", "Project"),
            ("workflow", "Workflow"),
            ("source_kind", "Source"),
            ("source_path", "Imported From"),
            ("status", "Status"),
            ("completed", "Completed"),
        ],
        empty_title="No imported workflows found",
        empty_text="Imported workflows will appear here across all projects you can access.",
        icon="📥",
    )

with tab_workflows:
    if not active_project_id:
        empty_state(
            "No active project selected",
            "Set an active project in the sidebar or Projects page to inspect tracked and on-disk workflows here.",
            icon="📁",
        )
    else:
        st.caption(f"Active project: {active_project_name}")
        _render_inventory_table(
            workflow_payload.get("items", []),
            [
                ("workflow", "Workflow"),
                ("display_name", "Display Name"),
                ("tracked", "Tracked"),
                ("on_disk", "On Disk"),
                ("status", "Status"),
                ("run_uuid", "Run UUID"),
            ],
            empty_title="No workflows found",
            empty_text="Tracked workflows and workflow folders for the active project will appear here.",
            icon="🧪",
        )

with tab_files:
    section_header("Project File Inventory", "Browse the active project root or a specific workflow directory.", icon="📂")

    workflow_rows = list(workflow_payload.get("items", [])) if active_project_id else []
    workflow_map = {str(item.get("workflow") or ""): item for item in workflow_rows if item.get("workflow")}
    workflow_options = list(workflow_map.keys())

    if not active_project_id:
        empty_state(
            "No active project selected",
            "Set an active project to browse workflow and project files here.",
            icon="📂",
        )
    else:
        default_scope_index = 0 if workflow_options else 1
        browse_col1, browse_col2, browse_col3 = st.columns([1.2, 2.2, 1])
        with browse_col1:
            browse_scope = st.radio(
                "Scope",
                ["Workflow", "Project"],
                horizontal=True,
                index=default_scope_index,
                key="my_data_file_scope",
            )
        with browse_col2:
            if browse_scope == "Workflow":
                if workflow_options:
                    selected_workflow = st.selectbox(
                        "Workflow",
                        workflow_options,
                        format_func=lambda value: (
                            f"{value} - {workflow_map[value].get('display_name') or value}"
                            f" ({workflow_map[value].get('status') or 'UNKNOWN'})"
                        ),
                        key="my_data_workflow_ref",
                    )
                    target_label = "Folder inside workflow"
                    target_placeholder = "annot or results/qc"
                else:
                    selected_workflow = ""
                    st.caption("No workflows found for the active project. Switch to Project scope to browse the project root.")
                    target_label = "Folder inside project"
                    target_placeholder = "workflow7/annot or shared"
            else:
                selected_workflow = ""
                target_label = "Folder inside project"
                target_placeholder = "workflow7/annot or shared"
            browse_target = st.text_input(target_label, placeholder=target_placeholder, key="my_data_file_target")
        with browse_col3:
            browse_depth = st.selectbox("Depth", [1, 2, 3, 5, 10], index=1, key="my_data_file_depth")

        file_inventory_payload = {"items": [], "count": 0}
        if browse_scope == "Workflow" and not selected_workflow:
            file_inventory_payload = {
                "items": [],
                "count": 0,
                "error": "Choose a workflow or switch to Project scope.",
            }
        else:
            file_params = [("project_id", active_project_id), ("max_depth", browse_depth)]
            if browse_scope == "Project":
                file_params.append(("project_scope", True))
            elif selected_workflow:
                file_params.append(("workflow_ref", selected_workflow))
            if browse_target.strip():
                file_params.append(("target", browse_target.strip()))
            file_inventory_payload = _fetch_inventory("files", params=tuple(file_params))

        st.caption("File inventory loads through the Analyzer file lister for the selected scope.")
        inventory_error = str(file_inventory_payload.get("error") or "").strip()
        if inventory_error:
            st.warning(inventory_error)
        resolved_work_dir = str(file_inventory_payload.get("work_dir") or "").strip()
        if resolved_work_dir:
            st.caption(f"Resolved path: {resolved_work_dir}")

        _render_inventory_table(
            file_inventory_payload.get("items", []),
            [("path", "Path"), ("size", "Size"), ("modified", "Modified")],
            empty_title="No files found for this scope",
            empty_text="Adjust the scope, workflow, folder, or depth to inspect another location in the active project.",
            icon="📂",
        )

    st.divider()
    section_header("Central Data Folder", "Manage shared files, metadata, and project links.", icon="🧾")

    include_untracked = st.toggle(
        "Show files found on disk (not in database)",
        value=True,
        help="Includes files physically present in your central data directory even if they were not registered via upload/download flows.",
    )

    data = _fetch_user_files(include_untracked=include_untracked)
    files = data.get("files", [])

    if not files:
        empty_state(
            "No files in your central data folder yet",
            "Files will appear here when you download or upload data in any project.",
            icon="🗄️",
        )
    else:
        filter_col, source_col, count_col = st.columns([3, 2, 1])
        with filter_col:
            search_text = st.text_input("🔍 Filter files", placeholder="filename, accession, sample name…")
        with source_col:
            source_options = sorted({str(item.get("source") or "unknown") for item in files})
            source_filter = st.multiselect("Source", source_options, default=source_options)
        with count_col:
            stat_tile("Total files", len(files), icon="📦")

        filtered = list(files)
        if search_text:
            query = search_text.lower()
            filtered = [
                item for item in filtered
                if query in (item.get("filename") or "").lower()
                or query in (item.get("encode_accession") or "").lower()
                or query in (item.get("sample_name") or "").lower()
                or query in (item.get("organism") or "").lower()
            ]
        filtered = [item for item in filtered if str(item.get("source") or "unknown") in source_filter]

        if not filtered:
            empty_state("No files match your filters", "Try broadening search text or source filters.", icon="🔍")
        else:
            table_data = []
            for item in filtered:
                proj_names = ", ".join(
                    project.get("project_name") or project.get("project_id", "?")
                    for project in item.get("projects", [])
                ) or "—"
                size_mb = round((item.get("size_bytes") or 0) / 1_048_576, 2)
                table_data.append(
                    {
                        "Filename": item["filename"],
                        "Folder": str(Path(item.get("disk_path") or "").parent),
                        "Size (MB)": size_mb,
                        "Source": item.get("source", ""),
                        "Accession": item.get("encode_accession") or "",
                        "Sample": item.get("sample_name") or "",
                        "Organism": item.get("organism") or "",
                        "Projects": proj_names,
                        "Added": (item.get("created_at") or "")[:10],
                        "_id": item["id"],
                    }
                )

            df = pd.DataFrame(table_data)
            st.dataframe(
                df.drop(columns=["_id"]),
                width="stretch",
                hide_index=True,
                height=min(400, 40 + 35 * len(df)),
            )

            st.divider()
            section_header("File Details & Actions", "Edit metadata, links, and file actions", icon="🧾")

            file_options = {}
            for item in filtered:
                filename = item.get("filename") or "(unnamed)"
                parent = str(Path(item.get("disk_path") or "").parent)
                display = f"{filename} ({parent})"
                option_key = display
                suffix = 2
                while option_key in file_options:
                    option_key = f"{display} [{suffix}]"
                    suffix += 1
                file_options[option_key] = item

            selected_name = st.selectbox("Select a file", list(file_options.keys()), key="my_data_selected_file")

            if selected_name:
                selected_file = file_options[selected_name]
                is_tracked = selected_file.get("tracked", True)
                detail_col, action_col = st.columns([2, 1])

                with detail_col:
                    st.markdown(
                        f"**{selected_file['filename']}** — {round((selected_file.get('size_bytes') or 0) / 1_048_576, 2)} MB"
                    )
                    st.caption(
                        f"MD5: `{selected_file.get('md5_hash', 'N/A')}` | "
                        f"Source: {selected_file.get('source', '?')} | "
                        f"Added: {(selected_file.get('created_at') or '')[:10]}"
                    )
                    st.code(str(selected_file.get("disk_path") or ""), language="text")
                    st.caption(f"Folder: {str(Path(selected_file.get('disk_path') or '').parent)}")
                    if not is_tracked:
                        status_chip("warning", label="Untracked", icon="⚠️")
                        st.info(
                            "This file exists on disk but is not in the AGOUTIC database catalog. "
                            "Metadata, linking, and deletion are disabled for this entry."
                        )
                    if selected_file.get("source_url"):
                        st.caption(f"URL: {selected_file['source_url']}")

                    with st.form(key=f"meta_{selected_file['id']}"):
                        new_sample = st.text_input(
                            "Sample name",
                            value=selected_file.get("sample_name") or "",
                            disabled=not is_tracked,
                        )
                        new_organism = st.text_input(
                            "Organism",
                            value=selected_file.get("organism") or "",
                            disabled=not is_tracked,
                        )
                        new_tissue = st.text_input(
                            "Tissue",
                            value=selected_file.get("tissue") or "",
                            disabled=not is_tracked,
                        )
                        raw_tags = st.text_area(
                            "Tags (JSON)",
                            value=json.dumps(selected_file.get("tags") or {}, indent=2),
                            height=80,
                            disabled=not is_tracked,
                        )
                        submitted = st.form_submit_button("💾 Save metadata", disabled=not is_tracked)

                    if submitted:
                        try:
                            tags_dict = json.loads(raw_tags) if raw_tags.strip() else {}
                        except json.JSONDecodeError:
                            st.error("Invalid JSON in tags field")
                            tags_dict = None

                        if tags_dict is not None:
                            payload = {
                                "sample_name": new_sample or None,
                                "organism": new_organism or None,
                                "tissue": new_tissue or None,
                                "tags": tags_dict,
                            }
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/user/data/{selected_file['id']}",
                                json=payload,
                            )
                            if resp and resp.status_code == 200:
                                st.success("Metadata saved!")
                                _fetch_user_files.clear()
                                st.rerun()
                            else:
                                st.error(f"Failed to save: {resp.text if resp else 'no response'}")

                with action_col:
                    linked = selected_file.get("projects", [])
                    if not is_tracked:
                        st.caption("Actions unavailable for untracked filesystem entries")
                    else:
                        st.markdown("**Linked projects**")
                        if linked:
                            for project in linked:
                                pcol1, pcol2 = st.columns([3, 1])
                                with pcol1:
                                    st.write(f"📁 {project.get('project_name') or project.get('project_id')}")
                                with pcol2:
                                    if st.button("Unlink", key=f"unlink_{selected_file['id']}_{project['project_id']}"):
                                        resp = make_authenticated_request(
                                            "POST",
                                            f"{API_URL}/user/data/{selected_file['id']}/unlink",
                                            json={"project_id": project["project_id"]},
                                        )
                                        if resp and resp.status_code == 200:
                                            st.success("Unlinked!")
                                            _fetch_user_files.clear()
                                            st.rerun()
                                        else:
                                            st.error("Unlink failed")
                        else:
                            st.caption("Not linked to any project")

                        st.markdown("**Link to project**")
                        project_map = {
                            project["name"]: project["id"]
                            for project in project_rows
                            if project.get("name") and project.get("id")
                        }
                        already_linked_ids = {project["project_id"] for project in linked}
                        available_projects = {
                            name: project_id
                            for name, project_id in project_map.items()
                            if project_id not in already_linked_ids
                        }

                        if available_projects:
                            link_choice = st.selectbox(
                                "Project",
                                list(available_projects.keys()),
                                key=f"linksel_{selected_file['id']}",
                            )
                            if st.button("🔗 Link", key=f"linkbtn_{selected_file['id']}"):
                                resp = make_authenticated_request(
                                    "POST",
                                    f"{API_URL}/user/data/{selected_file['id']}/link",
                                    json={"project_id": available_projects[link_choice]},
                                )
                                if resp and resp.status_code == 200:
                                    st.success(f"Linked to {link_choice}!")
                                    _fetch_user_files.clear()
                                    st.rerun()
                                else:
                                    st.error("Link failed")
                        else:
                            st.caption("All projects already linked")

                        st.divider()
                        if selected_file.get("source_url"):
                            if st.button("🔄 Re-download", key=f"redl_{selected_file['id']}"):
                                st.session_state[f"confirm_redl_{selected_file['id']}"] = True

                            if st.session_state.get(f"confirm_redl_{selected_file['id']}"):
                                st.warning("This will replace the file with a fresh download. Continue?")
                                c1, c2 = st.columns(2)
                                with c1:
                                    if st.button("✅ Yes, re-download", key=f"redl_yes_{selected_file['id']}"):
                                        resp = make_authenticated_request(
                                            "POST",
                                            f"{API_URL}/user/data/{selected_file['id']}/redownload",
                                            json={"force": True},
                                        )
                                        st.session_state.pop(f"confirm_redl_{selected_file['id']}", None)
                                        if resp and resp.status_code == 200:
                                            st.success("Re-download started!")
                                            _fetch_user_files.clear()
                                        else:
                                            st.error("Re-download failed")
                                with c2:
                                    if st.button("❌ Cancel", key=f"redl_no_{selected_file['id']}"):
                                        st.session_state.pop(f"confirm_redl_{selected_file['id']}", None)
                                        st.rerun()

                        st.divider()
                        if st.button("🗑️ Delete file", key=f"del_{selected_file['id']}", type="secondary"):
                            st.session_state[f"confirm_del_{selected_file['id']}"] = True

                        if st.session_state.get(f"confirm_del_{selected_file['id']}"):
                            affected = [project.get("project_name") or project.get("project_id") for project in linked]
                            if affected:
                                st.warning(
                                    f"This will remove the file and break symlinks in: {', '.join(affected)}"
                                )
                            else:
                                st.warning("Permanently delete this file?")
                            c1, c2 = st.columns(2)
                            with c1:
                                if st.button("✅ Yes, delete", key=f"del_yes_{selected_file['id']}"):
                                    resp = make_authenticated_request(
                                        "DELETE",
                                        f"{API_URL}/user/data/{selected_file['id']}",
                                    )
                                    st.session_state.pop(f"confirm_del_{selected_file['id']}", None)
                                    if resp and resp.status_code == 200:
                                        st.success("Deleted!")
                                        _fetch_user_files.clear()
                                        st.rerun()
                                    else:
                                        st.error("Delete failed")
                            with c2:
                                if st.button("❌ Cancel", key=f"del_no_{selected_file['id']}"):
                                    st.session_state.pop(f"confirm_del_{selected_file['id']}", None)
                                    st.rerun()


st.caption(f"Connected to AGOUTIC API: {API_URL}")
