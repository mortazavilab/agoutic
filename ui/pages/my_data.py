"""
My Data — Central Data Folder Browser
Browse, search, and manage all downloaded/uploaded files across projects.
Link or unlink files to projects, edit metadata, and force re-downloads.
"""

import streamlit as st
import pandas as pd
import sys
import os
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request
from components.cards import section_header, stat_tile, empty_state, status_chip, metadata_row

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="My Data", page_icon="🗄️", layout="wide")

# Require authentication
user = require_auth(API_URL)

section_header("My Data", "All files in your central data folder shared across projects via symlinks.", icon="🗄️")


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
    st.stop()


# ---------------------------------------------------------------------------
# Filter bar
# ---------------------------------------------------------------------------

col_filter, col_source, col_count = st.columns([3, 2, 1])
with col_filter:
    search_text = st.text_input("🔍 Filter files", placeholder="filename, accession, sample name…")
with col_source:
    source_options = sorted({f["source"] for f in files})
    source_filter = st.multiselect("Source", source_options, default=source_options)
with col_count:
    stat_tile("Total files", len(files), icon="📦")

# Apply filters
filtered = files
if search_text:
    _q = search_text.lower()
    filtered = [
        f for f in filtered
        if _q in (f.get("filename") or "").lower()
        or _q in (f.get("encode_accession") or "").lower()
        or _q in (f.get("sample_name") or "").lower()
        or _q in (f.get("organism") or "").lower()
    ]
if source_filter:
    filtered = [f for f in filtered if f.get("source") in source_filter]


# ---------------------------------------------------------------------------
# File table
# ---------------------------------------------------------------------------

if not filtered:
    empty_state("No files match your filters", "Try broadening search text or source filters.", icon="🔍")
    st.stop()

table_data = []
for f in filtered:
    proj_names = ", ".join(
        p.get("project_name") or p.get("project_id", "?")
        for p in f.get("projects", [])
    ) or "—"
    size_mb = round((f.get("size_bytes") or 0) / 1_048_576, 2)
    table_data.append({
        "Filename": f["filename"],
        "Folder": str(Path(f.get("disk_path") or "").parent),
        "Size (MB)": size_mb,
        "Source": f.get("source", ""),
        "Accession": f.get("encode_accession") or "",
        "Sample": f.get("sample_name") or "",
        "Organism": f.get("organism") or "",
        "Projects": proj_names,
        "Added": (f.get("created_at") or "")[:10],
        "_id": f["id"],
    })

df = pd.DataFrame(table_data)

st.dataframe(
    df.drop(columns=["_id"]),
    use_container_width=True,
    hide_index=True,
    height=min(400, 40 + 35 * len(df)),
)


# ---------------------------------------------------------------------------
# File detail / actions
# ---------------------------------------------------------------------------

st.divider()
section_header("File Details & Actions", "Edit metadata, links, and file actions", icon="🧾")

file_options = {}
for f in filtered:
    filename = f.get("filename") or "(unnamed)"
    parent = str(Path(f.get("disk_path") or "").parent)
    display = f"{filename} ({parent})"
    # Ensure stable uniqueness even if both filename and folder collide
    key = display
    suffix = 2
    while key in file_options:
        key = f"{display} [{suffix}]"
        suffix += 1
    file_options[key] = f

selected_name = st.selectbox("Select a file", list(file_options.keys()))

if selected_name:
    f = file_options[selected_name]
    is_tracked = f.get("tracked", True)

    detail_col, action_col = st.columns([2, 1])

    # ---- Metadata editor ----
    with detail_col:
        st.markdown(f"**{f['filename']}** — {round((f.get('size_bytes') or 0) / 1_048_576, 2)} MB")
        st.caption(f"MD5: `{f.get('md5_hash', 'N/A')}` | Source: {f.get('source', '?')} | Added: {(f.get('created_at') or '')[:10]}")
        st.code(str(f.get("disk_path") or ""), language="text")
        st.caption(f"Folder: {str(Path(f.get('disk_path') or '').parent)}")
        if not is_tracked:
            status_chip("warning", label="Untracked", icon="⚠️")
            st.info("This file exists on disk but is not in the AGOUTIC database catalog. Metadata/link/delete actions are disabled for this entry.")
        if f.get("source_url"):
            st.caption(f"URL: {f['source_url']}")

        with st.form(key=f"meta_{f['id']}"):
            new_sample = st.text_input("Sample name", value=f.get("sample_name") or "", disabled=not is_tracked)
            new_organism = st.text_input("Organism", value=f.get("organism") or "", disabled=not is_tracked)
            new_tissue = st.text_input("Tissue", value=f.get("tissue") or "", disabled=not is_tracked)
            raw_tags = st.text_area(
                "Tags (JSON)",
                value=json.dumps(f.get("tags") or {}, indent=2),
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
                    "PATCH", f"{API_URL}/user/data/{f['id']}", json=payload,
                )
                if resp and resp.status_code == 200:
                    st.success("Metadata saved!")
                    _fetch_user_files.clear()
                    st.rerun()
                else:
                    st.error(f"Failed to save: {resp.text if resp else 'no response'}")

    # ---- Projects & actions ----
    with action_col:
        if not is_tracked:
            st.caption("Actions unavailable for untracked filesystem entries")
            st.stop()

        st.markdown("**Linked projects**")
        linked = f.get("projects", [])
        if linked:
            for p in linked:
                pcol1, pcol2 = st.columns([3, 1])
                with pcol1:
                    st.write(f"📁 {p.get('project_name') or p.get('project_id')}")
                with pcol2:
                    if st.button("Unlink", key=f"unlink_{f['id']}_{p['project_id']}"):
                        resp = make_authenticated_request(
                            "POST",
                            f"{API_URL}/user/data/{f['id']}/unlink",
                            json={"project_id": p["project_id"]},
                        )
                        if resp and resp.status_code == 200:
                            st.success("Unlinked!")
                            _fetch_user_files.clear()
                            st.rerun()
                        else:
                            st.error("Unlink failed")
        else:
            st.caption("Not linked to any project")

        # Link to another project
        st.markdown("**Link to project**")
        all_projects = _fetch_projects()
        if isinstance(all_projects, list):
            proj_map = {p["name"]: p["id"] for p in all_projects if "name" in p and "id" in p}
        elif isinstance(all_projects, dict):
            proj_map = {p["name"]: p["id"] for p in all_projects.get("projects", []) if "name" in p and "id" in p}
        else:
            proj_map = {}

        already_linked_ids = {p["project_id"] for p in linked}
        available = {k: v for k, v in proj_map.items() if v not in already_linked_ids}

        if available:
            link_choice = st.selectbox("Project", list(available.keys()), key=f"linksel_{f['id']}")
            if st.button("🔗 Link", key=f"linkbtn_{f['id']}"):
                resp = make_authenticated_request(
                    "POST",
                    f"{API_URL}/user/data/{f['id']}/link",
                    json={"project_id": available[link_choice]},
                )
                if resp and resp.status_code == 200:
                    st.success(f"Linked to {link_choice}!")
                    _fetch_user_files.clear()
                    st.rerun()
                else:
                    st.error("Link failed")
        else:
            st.caption("All projects already linked")

        # Re-download
        st.divider()
        if f.get("source_url"):
            if st.button("🔄 Re-download", key=f"redl_{f['id']}"):
                st.session_state[f"confirm_redl_{f['id']}"] = True

            if st.session_state.get(f"confirm_redl_{f['id']}"):
                st.warning("This will replace the file with a fresh download. Continue?")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Yes, re-download", key=f"redl_yes_{f['id']}"):
                        resp = make_authenticated_request(
                            "POST",
                            f"{API_URL}/user/data/{f['id']}/redownload",
                            json={"force": True},
                        )
                        st.session_state.pop(f"confirm_redl_{f['id']}", None)
                        if resp and resp.status_code == 200:
                            st.success("Re-download started!")
                            _fetch_user_files.clear()
                        else:
                            st.error("Re-download failed")
                with c2:
                    if st.button("❌ Cancel", key=f"redl_no_{f['id']}"):
                        st.session_state.pop(f"confirm_redl_{f['id']}", None)
                        st.rerun()

        # Delete file
        st.divider()
        if st.button("🗑️ Delete file", key=f"del_{f['id']}", type="secondary"):
            st.session_state[f"confirm_del_{f['id']}"] = True

        if st.session_state.get(f"confirm_del_{f['id']}"):
            affected = [p.get("project_name") or p.get("project_id") for p in linked]
            if affected:
                st.warning(f"This will remove the file and break symlinks in: {', '.join(affected)}")
            else:
                st.warning("Permanently delete this file?")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Yes, delete", key=f"del_yes_{f['id']}"):
                    resp = make_authenticated_request(
                        "DELETE", f"{API_URL}/user/data/{f['id']}",
                    )
                    st.session_state.pop(f"confirm_del_{f['id']}", None)
                    if resp and resp.status_code == 200:
                        st.success("Deleted!")
                        _fetch_user_files.clear()
                        st.rerun()
                    else:
                        st.error("Delete failed")
            with c2:
                if st.button("❌ Cancel", key=f"del_no_{f['id']}"):
                    st.session_state.pop(f"confirm_del_{f['id']}", None)
                    st.rerun()


st.caption(f"Connected to AGOUTIC API: {API_URL}")
