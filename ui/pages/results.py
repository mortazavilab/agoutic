"""
Job Results Analysis Page
Displays and analyzes completed job results via Server 1 proxy endpoints.
All requests go through Server 1 — the UI never contacts backend servers directly.
"""

import streamlit as st
import pandas as pd
import sys
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request

# Server 1 API URL (the only server the UI talks to)
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Job Results", page_icon="📊", layout="wide")

# Require authentication
user = require_auth(API_URL)

st.title("📊 Job Results Analysis")
st.markdown("Analyze completed Dogme job results")

# Main interface
st.header("Select Job")

# Auto-list jobs from the user's active project (if available)
run_uuid = None
_active_pid = st.session_state.get("active_project_id")

try:
    if _active_pid:
        _stats = make_authenticated_request(
            "GET", f"{API_URL}/projects/{_active_pid}/stats", timeout=5
        )
        if _stats.status_code == 200:
            _jobs = _stats.json().get("jobs", [])
            if _jobs:
                _options = {}
                for j in _jobs:
                    _uuid = j.get("run_uuid", "")
                    _status = j.get("status", "?")
                    _emoji = {"COMPLETED": "✅", "RUNNING": "⏳", "FAILED": "❌"}.get(_status, "❓")
                    _sample = j.get("sample_name", "Unknown")
                    _label = f"{_emoji} {_sample} — {_status} ({_uuid[:8]}…)"
                    _options[_label] = _uuid
                if _options:
                    st.caption(f"Jobs in current project ({len(_options)})")
                    _sel = st.selectbox("Pick a job", list(_options.keys()), key="job_pick")
                    run_uuid = _options[_sel]
except Exception:
    pass

# Fallback: manual UUID input
manual_uuid = st.text_input(
    "Or enter Job UUID manually",
    value=run_uuid or "",
    placeholder="e.g., 167fd6ce-d1b4-43a0-a267-c5d8d01b5f38",
    help="Enter the UUID of a completed job",
)
if manual_uuid:
    run_uuid = manual_uuid

if run_uuid:
    try:
        # ── Job Summary ──────────────────────────────────────────────
        with st.spinner("Loading job analysis..."):
            response = make_authenticated_request(
                "GET",
                f"{API_URL}/analysis/jobs/{run_uuid}/summary",
                timeout=15
            )
            
        if response.status_code == 200:
            summary = response.json()
            
            if summary.get("success") is False:
                st.error(f"Analysis error: {summary.get('error', 'Unknown')} — {summary.get('detail', '')}")
                st.stop()
            
            # Job metrics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Sample", summary.get("sample_name", "N/A"))
            with col2:
                st.metric("Workflow", summary.get("mode", "N/A"))
            with col3:
                st.metric("Status", summary.get("status", "N/A"))
            with col4:
                total_files = summary.get("key_results", {}).get("total_files", 0)
                st.metric("Total Files", total_files)
            
            st.divider()

            # ── Tabs ─────────────────────────────────────────────────
            tab_df, tab_bed, tab_txt, tab_all, tab_reports = st.tabs(
                ["📊 DataFrames", "🧬 BED Files", "📝 Text Files", "📁 All Files", "📋 Reports"]
            )
            
            file_summary = summary.get("file_summary", {})

            # ── DataFrames Tab (on-demand per-file) ────────────────
            with tab_df:
                st.subheader("CSV / TSV DataFrames")

                csv_files = file_summary.get("csv_files", [])
                tabular_files = csv_files  # includes both .csv and .tsv

                if not tabular_files:
                    st.info("No CSV/TSV files found in this job")
                else:
                    st.caption(f"{len(tabular_files)} tabular file(s) available — click **Parse** to load")

                    for tf in tabular_files:
                        _fp = tf.get("path", tf.get("name", "unknown"))
                        _nm = tf.get("name", _fp.rsplit("/", 1)[-1])
                        _sz = tf.get("size", tf.get("size_bytes", 0))
                        _cache_key = f"_df_{run_uuid}_{_fp}"

                        with st.expander(f"📊 **{_nm}** ({_sz:,} bytes)", expanded=(_cache_key in st.session_state)):
                            if _cache_key not in st.session_state:
                                if st.button("Parse CSV/TSV", key=f"parse_csv_{_fp}"):
                                    with st.spinner(f"Parsing {_nm}…"):
                                        try:
                                            _resp = make_authenticated_request(
                                                "GET",
                                                f"{API_URL}/analysis/files/parse/csv",
                                                params={
                                                    "run_uuid": run_uuid,
                                                    "file_path": _fp,
                                                    "max_rows": 5000,
                                                },
                                                timeout=30,
                                            )
                                            if _resp.status_code == 200:
                                                st.session_state[_cache_key] = _resp.json()
                                                st.rerun()
                                            else:
                                                st.error(f"Error: {_resp.status_code} — {_resp.text[:200]}")
                                        except Exception as e:
                                            st.error(f"Error: {e}")
                            else:
                                fdata = st.session_state[_cache_key]
                                rows = fdata.get("data", [])
                                cols = fdata.get("columns", [])
                                row_count = fdata.get("row_count", len(rows))
                                meta = fdata.get("metadata", {})

                                df = pd.DataFrame(rows)
                                if df.empty:
                                    st.info("Empty table")
                                    continue

                                # Column Stats popover
                                col_stats = meta.get("column_stats", {})
                                if col_stats:
                                    with st.popover("📈 Column statistics"):
                                        for cname, cinfo in col_stats.items():
                                            dtype = cinfo.get("dtype", "")
                                            nulls = cinfo.get("nulls", 0)
                                            line = f"**{cname}** ({dtype})"
                                            if nulls:
                                                line += f" — {nulls} nulls"
                                            if "mean" in cinfo and cinfo["mean"] is not None:
                                                line += (
                                                    f"  \nmin={cinfo.get('min')}  max={cinfo.get('max')}  "
                                                    f"mean={cinfo.get('mean'):.4g}  median={cinfo.get('median'):.4g}"
                                                )
                                            elif "unique" in cinfo:
                                                line += f"  \n{cinfo['unique']} unique values"
                                            st.markdown(line)

                                # Column filter
                                if len(cols) > 3:
                                    selected_cols = st.multiselect(
                                        "Columns to display",
                                        cols,
                                        default=cols,
                                        key=f"cols_{_fp}",
                                    )
                                else:
                                    selected_cols = cols

                                display_df = df[selected_cols] if selected_cols else df

                                st.dataframe(
                                    display_df,
                                    width="stretch",
                                    hide_index=True,
                                    height=min(400, 35 * len(display_df) + 38),
                                )

                                if meta.get("is_truncated"):
                                    st.caption(f"Showing {len(rows):,} of {row_count:,} rows")

                                csv_bytes = display_df.to_csv(index=False).encode("utf-8")
                                st.download_button(
                                    f"⬇️ Download {_nm}",
                                    data=csv_bytes,
                                    file_name=_nm,
                                    mime="text/csv",
                                    key=f"dl_{_fp}",
                                )

            # ── BED Files Tab ────────────────────────────────────────
            with tab_bed:
                bed_files = file_summary.get("bed_files", [])
                st.subheader(f"BED Files ({len(bed_files)})")
                
                if bed_files:
                    for bed_file in bed_files:
                        with st.expander(f"🧬 {bed_file['name']} ({bed_file['size']} bytes)"):
                            st.text(f"Path: {bed_file['path']}")
                            if st.button("Parse BED", key=f"parse_{bed_file['path']}"):
                                try:
                                    parse_response = make_authenticated_request(
                                        "GET",
                                        f"{API_URL}/analysis/files/parse/bed",
                                        params={
                                            "run_uuid": run_uuid,
                                            "file_path": bed_file['path'],
                                            "max_records": 500
                                        },
                                        timeout=15
                                    )
                                    if parse_response.status_code == 200:
                                        parsed_data = parse_response.json()
                                        st.success(f"Parsed {parsed_data['record_count']} records")
                                        if parsed_data.get('records'):
                                            bed_df = pd.DataFrame(parsed_data['records'])
                                            st.dataframe(bed_df, width="stretch", hide_index=True)
                                            csv_bytes = bed_df.to_csv(index=False).encode("utf-8")
                                            st.download_button(
                                                f"⬇️ Download as CSV",
                                                data=csv_bytes,
                                                file_name=bed_file['name'].rsplit('.', 1)[0] + '.csv',
                                                mime="text/csv",
                                                key=f"dl_bed_{bed_file['path']}",
                                            )
                                    else:
                                        st.error(f"Error: {parse_response.text}")
                                except Exception as e:
                                    st.error(f"Error: {e}")
                else:
                    st.info("No BED files found")
            
            # ── Text Files Tab ───────────────────────────────────────
            with tab_txt:
                txt_files = file_summary.get("txt_files", [])
                st.subheader(f"Text Files ({len(txt_files)})")
                
                if txt_files:
                    for txt_file in txt_files:
                        with st.expander(f"📝 {txt_file['name']} ({txt_file['size']} bytes)"):
                            st.text(f"Path: {txt_file['path']}")
                            if st.button("Read File", key=f"read_{txt_file['path']}"):
                                try:
                                    read_response = make_authenticated_request(
                                        "GET",
                                        f"{API_URL}/analysis/files/content",
                                        params={
                                            "run_uuid": run_uuid,
                                            "file_path": txt_file['path'],
                                            "preview_lines": 200
                                        },
                                        timeout=15
                                    )
                                    if read_response.status_code == 200:
                                        content_data = read_response.json()
                                        st.code(content_data['content'], language="text")
                                        if content_data.get('is_truncated'):
                                            st.warning(f"Showing first {content_data.get('line_count', 0)} lines")
                                    else:
                                        st.error(f"Error: {read_response.text}")
                                except Exception as e:
                                    st.error(f"Error: {e}")
                else:
                    st.info("No text files found")
            
            # ── All Files Tab ────────────────────────────────────────
            with tab_all:
                st.subheader("All Files")
                list_response = make_authenticated_request(
                    "GET",
                    f"{API_URL}/analysis/jobs/{run_uuid}/files",
                    timeout=15
                )
                if list_response.status_code == 200:
                    file_listing = list_response.json()
                    c1, c2 = st.columns(2)
                    with c1:
                        st.metric("Total Files", file_listing.get('file_count', 0))
                    with c2:
                        st.metric("Total Size", f"{file_listing.get('total_size_bytes', 0) / (1024*1024):.2f} MB")
                    
                    files_data = []
                    for f in file_listing.get('files', []):
                        files_data.append({
                            'Name': f['name'],
                            'Path': f['path'],
                            'Size (KB)': round((f.get('size') or f.get('size_bytes', 0)) / 1024, 1),
                            'Extension': f['extension']
                        })
                    if files_data:
                        st.dataframe(pd.DataFrame(files_data), width="stretch", hide_index=True)
                else:
                    st.error("Could not load file listing")
            
            # ── Parsed Reports Tab ───────────────────────────────────
            with tab_reports:
                st.subheader("Parsed Reports")
                parsed_reports = summary.get("parsed_reports", {})
                if parsed_reports:
                    for report_name, report_data in parsed_reports.items():
                        with st.expander(f"📊 {report_name}"):
                            st.json(report_data)
                else:
                    st.info("No parsed reports available")
        
        elif response.status_code == 404:
            st.error(f"Job not found: {run_uuid}")
        else:
            st.error(f"Error loading job: {response.status_code} - {response.text}")
    
    except Exception as e:
        st.error(f"Cannot connect to AGOUTIC API at {API_URL}. Make sure the servers are running.\n\nError: {e}")

else:
    st.info("👆 Select a job above or enter a UUID to view results")

# Footer
st.divider()
st.caption(f"Connected to AGOUTIC API: {API_URL}")
