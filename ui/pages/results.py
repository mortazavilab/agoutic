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
        # Get analysis summary via Server 1 proxy
        with st.spinner("Loading job analysis..."):
            response = make_authenticated_request(
                "GET",
                f"{API_URL}/analysis/jobs/{run_uuid}/summary",
                timeout=15
            )
            
        if response.status_code == 200:
            summary = response.json()
            
            # Guard against MCP error responses that slipped through
            if summary.get("success") is False:
                st.error(f"Analysis error: {summary.get('error', 'Unknown')} — {summary.get('detail', '')}")
                st.stop()
            
            # Display job info
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
            
            # File categories tabs
            tab1, tab2, tab3, tab4 = st.tabs(["📄 CSV Files", "🧬 BED Files", "📝 Text Files", "📁 All Files"])
            
            file_summary = summary.get("file_summary", {})
            
            # CSV Files Tab
            with tab1:
                csv_files = file_summary.get("csv_files", [])
                st.subheader(f"CSV Files ({len(csv_files)})")
                
                if csv_files:
                    for csv_file in csv_files:
                        with st.expander(f"📊 {csv_file['name']} ({csv_file['size']} bytes)"):
                            col1, col2 = st.columns([3, 1])
                            
                            with col1:
                                st.text(f"Path: {csv_file['path']}")
                            
                            with col2:
                                if st.button("Parse CSV", key=f"parse_{csv_file['path']}"):
                                    try:
                                        parse_response = make_authenticated_request(
                                            "GET",
                                            f"{API_URL}/analysis/files/parse/csv",
                                            params={
                                                "run_uuid": run_uuid,
                                                "file_path": csv_file['path'],
                                                "max_rows": 100
                                            },
                                            timeout=15
                                        )
                                        
                                        if parse_response.status_code == 200:
                                            parsed_data = parse_response.json()
                                            
                                            st.success(f"Parsed {parsed_data['row_count']} rows")
                                            
                                            # Display as DataFrame
                                            if parsed_data.get('data'):
                                                df = pd.DataFrame(parsed_data['data'])
                                                st.dataframe(df, use_container_width=True)
                                            
                                            # Download button via Server 1 proxy
                                            download_url = f"{API_URL}/analysis/files/download?run_uuid={run_uuid}&file_path={csv_file['path']}"
                                            st.markdown(f"[⬇️ Download File]({download_url})")
                                        else:
                                            st.error(f"Error parsing CSV: {parse_response.text}")
                                    except Exception as e:
                                        st.error(f"Error: {e}")
                else:
                    st.info("No CSV files found")
            
            # BED Files Tab
            with tab2:
                bed_files = file_summary.get("bed_files", [])
                st.subheader(f"BED Files ({len(bed_files)})")
                
                if bed_files:
                    for bed_file in bed_files:
                        with st.expander(f"🧬 {bed_file['name']} ({bed_file['size']} bytes)"):
                            col1, col2 = st.columns([3, 1])
                            
                            with col1:
                                st.text(f"Path: {bed_file['path']}")
                            
                            with col2:
                                if st.button("Parse BED", key=f"parse_{bed_file['path']}"):
                                    try:
                                        parse_response = make_authenticated_request(
                                            "GET",
                                            f"{API_URL}/analysis/files/parse/bed",
                                            params={
                                                "run_uuid": run_uuid,
                                                "file_path": bed_file['path'],
                                                "max_records": 100
                                            },
                                            timeout=15
                                        )
                                        
                                        if parse_response.status_code == 200:
                                            parsed_data = parse_response.json()
                                            
                                            st.success(f"Parsed {parsed_data['record_count']} records")
                                            
                                            # Display records
                                            if parsed_data.get('records'):
                                                records = parsed_data['records']
                                                df = pd.DataFrame(records)
                                                st.dataframe(df, use_container_width=True)
                                            
                                            # Download button via Server 1 proxy
                                            download_url = f"{API_URL}/analysis/files/download?run_uuid={run_uuid}&file_path={bed_file['path']}"
                                            st.markdown(f"[⬇️ Download File]({download_url})")
                                        else:
                                            st.error(f"Error parsing BED: {parse_response.text}")
                                    except Exception as e:
                                        st.error(f"Error: {e}")
                else:
                    st.info("No BED files found")
            
            # Text Files Tab
            with tab3:
                txt_files = file_summary.get("txt_files", [])
                st.subheader(f"Text Files ({len(txt_files)})")
                
                if txt_files:
                    for txt_file in txt_files:
                        with st.expander(f"📝 {txt_file['name']} ({txt_file['size']} bytes)"):
                            col1, col2 = st.columns([3, 1])
                            
                            with col1:
                                st.text(f"Path: {txt_file['path']}")
                            
                            with col2:
                                if st.button("Read File", key=f"read_{txt_file['path']}"):
                                    try:
                                        read_response = make_authenticated_request(
                                            "GET",
                                            f"{API_URL}/analysis/files/content",
                                            params={
                                                "run_uuid": run_uuid,
                                                "file_path": txt_file['path'],
                                                "preview_lines": 100
                                            },
                                            timeout=15
                                        )
                                        
                                        if read_response.status_code == 200:
                                            content_data = read_response.json()
                                            
                                            st.code(content_data['content'], language="text")
                                            
                                            if content_data.get('is_truncated'):
                                                st.warning(f"Showing first {content_data.get('line_count', 0)} lines")
                                            
                                            # Download button via Server 1 proxy
                                            download_url = f"{API_URL}/analysis/files/download?run_uuid={run_uuid}&file_path={txt_file['path']}"
                                            st.markdown(f"[⬇️ Download File]({download_url})")
                                        else:
                                            st.error(f"Error reading file: {read_response.text}")
                                    except Exception as e:
                                        st.error(f"Error: {e}")
                else:
                    st.info("No text files found")
            
            # All Files Tab
            with tab4:
                st.subheader("All Files")
                
                # Get full file listing via Server 1 proxy
                list_response = make_authenticated_request(
                    "GET",
                    f"{API_URL}/analysis/jobs/{run_uuid}/files",
                    timeout=15
                )
                
                if list_response.status_code == 200:
                    file_listing = list_response.json()
                    
                    st.metric("Total Files", file_listing.get('file_count', 0))
                    st.metric("Total Size", f"{file_listing.get('total_size_bytes', 0) / (1024*1024):.2f} MB")
                    
                    # Display all files in a table
                    files_data = []
                    for f in file_listing.get('files', []):
                        files_data.append({
                            'Name': f['name'],
                            'Path': f['path'],
                            'Size (KB)': f['size'] / 1024,
                            'Extension': f['extension']
                        })
                    
                    if files_data:
                        df = pd.DataFrame(files_data)
                        st.dataframe(df, use_container_width=True)
                else:
                    st.error("Could not load file listing")
            
            # Parsed reports section
            st.divider()
            st.subheader("📋 Parsed Reports")
            
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
