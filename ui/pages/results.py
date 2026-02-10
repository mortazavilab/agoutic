"""
Job Results Analysis Page
Displays and analyzes completed job results using Server4 API
"""

import streamlit as st
import requests
import pandas as pd
from typing import Optional

# Server4 configuration
SERVER4_URL = "http://localhost:8004"

st.set_page_config(page_title="Job Results", page_icon="📊", layout="wide")

st.title("📊 Job Results Analysis")
st.markdown("Analyze completed Dogme job results")

# Sidebar configuration
st.sidebar.header("Configuration")
server4_url = st.sidebar.text_input("Server4 URL", SERVER4_URL)

# Main interface
st.header("Select Job")

# Job UUID input
run_uuid = st.text_input(
    "Job UUID",
    placeholder="e.g., 167fd6ce-d1b4-43a0-a267-c5d8d01b5f38",
    help="Enter the UUID of a completed job"
)

if run_uuid:
    try:
        # Get analysis summary
        with st.spinner("Loading job analysis..."):
            response = requests.get(f"{server4_url}/analysis/summary/{run_uuid}")
            
        if response.status_code == 200:
            summary = response.json()
            
            # Display job info
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Sample", summary.get("sample_name", "N/A"))
            with col2:
                st.metric("Workflow", summary.get("workflow_type", "N/A"))
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
                                        parse_response = requests.get(
                                            f"{server4_url}/analysis/files/parse/csv",
                                            params={
                                                "run_uuid": run_uuid,
                                                "file_path": csv_file['path'],
                                                "max_rows": 100
                                            }
                                        )
                                        
                                        if parse_response.status_code == 200:
                                            parsed_data = parse_response.json()
                                            
                                            st.success(f"Parsed {parsed_data['row_count']} rows")
                                            
                                            # Display as DataFrame
                                            if parsed_data.get('data'):
                                                df = pd.DataFrame(parsed_data['data'])
                                                st.dataframe(df, use_container_width=True)
                                            
                                            # Download button
                                            download_url = f"{server4_url}/analysis/files/download?run_uuid={run_uuid}&file_path={csv_file['path']}"
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
                                        parse_response = requests.get(
                                            f"{server4_url}/analysis/files/parse/bed",
                                            params={
                                                "run_uuid": run_uuid,
                                                "file_path": bed_file['path'],
                                                "max_records": 100
                                            }
                                        )
                                        
                                        if parse_response.status_code == 200:
                                            parsed_data = parse_response.json()
                                            
                                            st.success(f"Parsed {parsed_data['record_count']} records")
                                            
                                            # Display records
                                            if parsed_data.get('records'):
                                                records = parsed_data['records']
                                                df = pd.DataFrame(records)
                                                st.dataframe(df, use_container_width=True)
                                            
                                            # Download button
                                            download_url = f"{server4_url}/analysis/files/download?run_uuid={run_uuid}&file_path={bed_file['path']}"
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
                                        read_response = requests.get(
                                            f"{server4_url}/analysis/files/content",
                                            params={
                                                "run_uuid": run_uuid,
                                                "file_path": txt_file['path'],
                                                "preview_lines": 100
                                            }
                                        )
                                        
                                        if read_response.status_code == 200:
                                            content_data = read_response.json()
                                            
                                            st.code(content_data['content'], language="text")
                                            
                                            if content_data.get('is_truncated'):
                                                st.warning(f"Showing first {content_data.get('line_count', 0)} lines")
                                            
                                            # Download button
                                            download_url = f"{server4_url}/analysis/files/download?run_uuid={run_uuid}&file_path={txt_file['path']}"
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
                
                # Get full file listing
                list_response = requests.get(f"{server4_url}/analysis/jobs/{run_uuid}/files")
                
                if list_response.status_code == 200:
                    file_listing = list_response.json()
                    
                    st.metric("Total Files", file_listing['file_count'])
                    st.metric("Total Size", f"{file_listing['total_size_bytes'] / (1024*1024):.2f} MB")
                    
                    # Display all files in a table
                    files_data = []
                    for f in file_listing['files']:
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
    
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to Server4 at {server4_url}. Make sure it's running.")
    except Exception as e:
        st.error(f"Error: {e}")

else:
    st.info("👆 Enter a job UUID above to view results")
    
    # Show example UUIDs
    st.markdown("### Example Job UUIDs")
    st.code("""
167fd6ce-d1b4-43a0-a267-c5d8d01b5f38
23178151-3126-43b7-9831-b9d6c23c2acf
39bc92fa-b592-413d-ac2d-898d1a187d00
""")

# Footer
st.divider()
st.caption(f"Connected to Server4: {server4_url}")
