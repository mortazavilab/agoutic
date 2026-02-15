import time
import requests
import datetime
import os
import streamlit as st
from auth import require_auth, logout_button, make_authenticated_request

# --- CONFIG ---
# Use environment variable or default to localhost
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="AGOUTIC v3.0", layout="wide")

# --- AUTHENTICATION ---
# Require authentication before showing any UI
user = require_auth(API_URL)

# --- 1. STATE MANAGEMENT ---
# Check if we're creating a new project (flag set by New Project button)
if st.session_state.get("_create_new_project", False):
    # Generate new project ID
    new_id = f"project_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    st.session_state.active_project_id = new_id
    st.session_state.blocks = []
    # Clear project-related data
    for key in ['loaded_conversation', 'selected_job', 'chat_history', 
                'skill_content', 'selected_skill', 'job_status', 'messages',
                '_max_visible_blocks']:
        if key in st.session_state:
            del st.session_state[key]
    # Reset the project ID text input widget so it doesn't hold the old value
    st.session_state["_project_id_input"] = new_id
    # Guard: prevent text_input comparison from reverting the ID on this rerun
    st.session_state["_project_just_switched"] = True
    # Clear the flag
    del st.session_state["_create_new_project"]

# Initialize with user's last project or create new one
if "active_project_id" not in st.session_state:
    # Try to get user's last project
    try:
        resp = make_authenticated_request("GET", f"{API_URL}/user/last-project", timeout=3)
        if resp.status_code == 200:
            last_project = resp.json().get("last_project_id")
            if last_project:
                st.session_state.active_project_id = last_project
            else:
                # No previous project, create new one
                st.session_state.active_project_id = f"project_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        else:
            # Fallback to new project
            st.session_state.active_project_id = f"project_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    except:
        # Error fetching, create new project
        st.session_state.active_project_id = f"project_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
# Initialize other state variables
if "blocks" not in st.session_state:
    st.session_state.blocks = []

# Detect project switch: clear stale blocks immediately so they never render
if st.session_state.get("_last_rendered_project") != st.session_state.active_project_id:
    st.session_state.blocks = []
    st.session_state._last_rendered_project = st.session_state.active_project_id

# --- 2. SIDEBAR ---
with st.sidebar:
    st.title("🧬 AGOUTIC")
    
    # User info
    st.caption(f"👤 {user.get('display_name', user.get('email'))}")
    if user.get('role') == 'admin':
        st.caption("🔑 Admin")
    
    logout_button(API_URL)
    
    st.divider()
    # [A] NEW PROJECT (Generates Random ID)
    if st.button("✨ New Project", use_container_width=True):
        # Set flag to create new project on next rerun
        st.session_state["_create_new_project"] = True
        st.rerun()

    # [B] PROJECT ID INPUT
    # Initialize the widget key if not set
    if "_project_id_input" not in st.session_state:
        st.session_state["_project_id_input"] = st.session_state.active_project_id
    
    user_input = st.text_input(
        "Project ID", 
        key="_project_id_input",
    )
    
    # Sync: if user manually edited the field, update active project
    # Skip if we just did a project switch (flag prevents revert)
    if not st.session_state.get("_project_just_switched") and user_input and user_input != st.session_state.active_project_id:
        st.session_state.active_project_id = user_input
        st.session_state.blocks = []
        st.session_state._last_rendered_project = user_input
        st.rerun()
    # Clear the switch guard after it's served its purpose
    st.session_state.pop("_project_just_switched", None)

    st.divider()
    
    # [C] CHAT CONTROLS
    col_clear, col_refresh = st.columns(2)
    with col_clear:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            try:
                resp = make_authenticated_request(
                    "DELETE",
                    f"{API_URL}/projects/{st.session_state.active_project_id}/blocks",
                    timeout=5,
                )
                if resp.status_code == 200:
                    st.session_state.blocks = []
                    # Reset welcome flag so agent re-introduces itself
                    st.session_state.pop("_welcome_sent_for", None)
                    st.toast(f"Chat cleared ({resp.json().get('deleted', 0)} messages removed)")
                    st.rerun()
                else:
                    st.error(f"Failed to clear: {resp.status_code}")
            except Exception as e:
                st.error(f"Error: {e}")
    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    st.divider()
    
    # [D] PROJECT SWITCHER
    st.subheader("📁 Projects")
    try:
        # Get user's projects
        proj_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/user/projects",
            timeout=3
        )
        if proj_resp.status_code == 200:
            user_projects = proj_resp.json().get("projects", [])
            if user_projects and len(user_projects) > 1:
                st.caption(f"{len(user_projects)} project(s)")
                for proj in user_projects[:5]:  # Show last 5 projects
                    proj_id = proj.get("id", "")
                    proj_name = proj.get("name", proj_id)[:30]
                    is_current = proj_id == st.session_state.active_project_id
                    
                    if is_current:
                        st.info(f"📌 {proj_name}")
                    else:
                        if st.button(f"📂 {proj_name}", key=f"proj_{proj_id}", use_container_width=True):
                            # Switch to this project
                            st.session_state.active_project_id = proj_id
                            st.session_state.blocks = []
                            st.session_state._last_rendered_project = proj_id
                            st.session_state["_project_id_input"] = proj_id
                            st.session_state["_project_just_switched"] = True
                            st.rerun()
    except Exception:
        pass  # Silently fail if projects not available
    
    st.divider()
    
    # [E] CONVERSATION HISTORY
    st.subheader("💬 History")
    try:
        # Get conversations for this project
        conv_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/projects/{st.session_state.active_project_id}/conversations",
            timeout=3
        )
        if conv_resp.status_code == 200:
            conversations = conv_resp.json().get("conversations", [])
            if conversations:
                st.caption(f"{len(conversations)} conversation(s)")
                for conv in conversations[:5]:  # Show last 5
                    conv_title = conv.get("title", "Untitled")[:30]
                    if st.button(f"📝 {conv_title}...", key=f"conv_{conv['id']}", use_container_width=True):
                        # Load this conversation
                        msg_resp = make_authenticated_request(
                            "GET",
                            f"{API_URL}/conversations/{conv['id']}/messages",
                            timeout=3
                        )
                        if msg_resp.status_code == 200:
                            st.session_state.loaded_conversation = msg_resp.json()
                            st.rerun()
            else:
                st.caption("No history yet")
        
        # Get previous jobs
        job_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/projects/{st.session_state.active_project_id}/jobs",
            timeout=3
        )
        if job_resp.status_code == 200:
            jobs = job_resp.json().get("jobs", [])
            if jobs:
                st.caption(f"📊 {len(jobs)} job(s)")
                for job in jobs[:3]:  # Show last 3
                    status_emoji = "✅" if job.get("status") == "COMPLETED" else "⏳"
                    job_name = job.get("sample_name", "Unknown")[:20]
                    if st.button(f"{status_emoji} {job_name}", key=f"job_{job['id']}", use_container_width=True):
                        st.session_state.selected_job = job
                        st.rerun()
    except Exception:
        pass  # Silently fail if history not available
    
    st.divider()
    
    model_choice = st.selectbox("Brain Model", ["default", "fast", "smart"], index=0)
    auto_refresh = st.toggle("Live Stream", value=True)
    poll_seconds = st.slider("Poll interval (sec)", 1, 5, 2)
    
    # DEBUG DISPLAY: Show exactly what ID the UI thinks it is using
    st.caption(f"**System ID:** `{st.session_state.active_project_id}`")


# --- 3. LOGIC ---

def get_sanitized_blocks(target_project_id):
    """
    Fetch blocks from server, but SCRUB any block that doesn't match the target ID.
    This guarantees no 'ghost' messages from other projects can appear.
    """
    try:
        # 1. Ask Server with authentication
        resp = make_authenticated_request(
            "GET",
            f"{API_URL}/blocks",
            params={"project_id": target_project_id, "since_seq": 0, "limit": 100},
            timeout=5,
        )
        if resp.status_code == 200:
            raw_blocks = resp.json().get("blocks", [])
            
            # 2. CLIENT-SIDE SANITATION (The Fix)
            # We explicitly filter the list. If the block's 'project_id' is not exactly
            # equal to our target, we throw it away.
            clean_blocks = [
                b for b in raw_blocks 
                if b.get("project_id") == target_project_id
            ]
            return clean_blocks
    except Exception:
        pass
    return []

def get_job_debug_info(run_uuid):
    """Fetch detailed debug information for a failed job via Server 1 proxy."""
    try:
        # Use Server 1 proxy instead of calling Server 3 directly
        resp = make_authenticated_request(
            "GET",
            f"{API_URL}/jobs/{run_uuid}/debug",
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        st.error(f"Failed to fetch debug info: {e}")
    return None


def render_block(block):
    """Render a single block."""
    btype = block["type"]
    content = block.get("payload", {})
    status = block.get("status", "NEW")
    block_id = block["id"]
    
    # Metadata
    b_project = block.get("project_id", "???")
    b_skill = content.get("skill", "N/A")
    b_model = content.get("model", "N/A")

    def show_metadata():
        st.caption(f"📌 **Proj:** `{b_project}` | 🧠 **Model:** `{b_model}` | 🛠️ **Skill:** `{b_skill}`")

    if btype == "USER_MESSAGE":
        with st.chat_message("user"):
            st.write(content.get("text", ""))

    elif btype == "AGENT_PLAN":
        with st.chat_message("assistant", avatar="🤖"):
            show_metadata()
            if "markdown" in content:
                md = content["markdown"]
                # Split out raw query results into a collapsible expander
                DETAILS_START = "<details><summary>"
                DETAILS_END = "</details>"
                if DETAILS_START in md and DETAILS_END in md:
                    main_part = md[:md.index(DETAILS_START)].rstrip().rstrip("---").rstrip()
                    details_block = md[md.index(DETAILS_START):md.index(DETAILS_END) + len(DETAILS_END)]
                    # Extract the summary text and body
                    import re as _re
                    details_match = _re.search(
                        r'<details><summary>(.*?)</summary>(.*)',
                        details_block, _re.DOTALL
                    )
                    if details_match:
                        summary_text = details_match.group(1).strip()
                        details_body = details_match.group(2).strip()
                        st.markdown(main_part)
                        with st.expander(summary_text, expanded=False):
                            st.markdown(details_body)
                    else:
                        st.markdown(md)
                else:
                    st.markdown(md)

    elif btype == "APPROVAL_GATE":
        with st.chat_message("assistant", avatar="🚦"):
            # Get extracted parameters and metadata
            extracted_params = content.get("extracted_params", {})
            manual_mode = content.get("manual_mode", False)
            attempt_number = content.get("attempt_number", 1)
            rejection_history = content.get("rejection_history", [])
            
            # Title based on mode
            if manual_mode:
                st.write("### ⚠️ Manual Configuration Required")
                st.warning(f"The AI couldn't understand your requirements after 3 attempts. Please verify these parameters manually.")
            else:
                st.write(f"### ✅ Approval Required (Attempt {attempt_number}/3)")
            
            st.write(content.get("label", "Approve this plan?"))
            st.caption(f"Block ID: `{block_id}`")
            
            # Show rejection history if exists
            if rejection_history:
                with st.expander(f"📜 Rejection History ({len(rejection_history)} previous attempts)", expanded=False):
                    for i, hist in enumerate(rejection_history, 1):
                        st.text(f"Attempt {hist.get('attempt', i)}: {hist.get('reason', 'No reason')}")
                        st.caption(f"at {hist.get('timestamp', 'unknown time')}")

            if status == "APPROVED":
                st.success("✅ Approved")
                # Show what parameters were used
                if extracted_params:
                    with st.expander("📋 Parameters Used", expanded=False):
                        st.json(extracted_params)
                        
            elif status == "REJECTED":
                st.error("❌ Rejected")
                # Show rejection reason if available
                reason = content.get("rejection_reason", "No reason provided")
                st.caption(f"Reason: {reason}")
                
            else:
                # Pending approval - show editable parameter form
                if extracted_params:
                    st.write("**📋 Extracted Parameters** (edit if needed):")
                    
                    with st.form(key=f"params_form_{block_id}"):
                        # Sample name
                        sample_name = st.text_input(
                            "Sample Name",
                            value=extracted_params.get("sample_name", ""),
                            help="Name for this sample"
                        )
                        
                        # Mode selection
                        mode_options = ["DNA", "RNA", "CDNA"]
                        current_mode = extracted_params.get("mode", "DNA")
                        mode_index = mode_options.index(current_mode) if current_mode in mode_options else 0
                        mode = st.selectbox("Analysis Mode", mode_options, index=mode_index)
                        
                        # Input type
                        input_type_options = ["pod5", "bam"]
                        current_input_type = extracted_params.get("input_type", "pod5")
                        input_type_index = input_type_options.index(current_input_type) if current_input_type in input_type_options else 0
                        input_type = st.selectbox("Input Type", input_type_options, index=input_type_index)
                        
                        # Entry point (Dogme workflow)
                        entry_point_options = ["(auto)", "basecall", "remap", "modkit", "annotateRNA", "reports"]
                        current_entry = extracted_params.get("entry_point") or "(auto)"
                        entry_index = entry_point_options.index(current_entry) if current_entry in entry_point_options else 0
                        entry_point = st.selectbox(
                            "Pipeline Entry Point",
                            entry_point_options,
                            index=entry_index,
                            help="main=(auto) full pipeline, basecall=only basecalling, remap=from unmapped BAM, modkit=modifications only, annotateRNA=transcript annotation, reports=generate reports"
                        )
                        
                        # Input directory
                        input_directory = st.text_input(
                            "Input Directory",
                            value=extracted_params.get("input_directory", ""),
                            help="Full path to input files"
                        )
                        
                        # Reference genomes (multi-select)
                        genome_options = ["GRCh38", "mm39"]  # TODO: fetch from /genomes endpoint
                        current_genomes = extracted_params.get("reference_genome", ["mm39"])
                        if isinstance(current_genomes, str):
                            current_genomes = [current_genomes]
                        reference_genomes = st.multiselect(
                            "Reference Genome(s)",
                            genome_options,
                            default=current_genomes,
                            help="Select one or more reference genomes"
                        )
                        
                        # Modifications (optional)
                        modifications = st.text_input(
                            "Modifications (optional)",
                            value=extracted_params.get("modifications", "") or "",
                            help="Comma-separated modification motifs (leave empty for auto)"
                        )
                        
                        # Advanced parameters in expander
                        with st.expander("⚙️ Advanced Parameters (optional)"):
                            st.caption("Leave empty to use defaults")
                            
                            # modkit_filter_threshold
                            modkit_threshold = st.number_input(
                                "Modkit Filter Threshold",
                                min_value=0.0,
                                max_value=1.0,
                                value=extracted_params.get("modkit_filter_threshold", 0.9),
                                step=0.05,
                                help="Modification calling threshold (default: 0.9)"
                            )
                            
                            # min_cov
                            min_cov_default = extracted_params.get("min_cov")
                            if min_cov_default is None:
                                # Show placeholder based on mode
                                min_cov_placeholder = 1 if mode == "DNA" else 3
                                st.caption(f"Min Coverage: (auto - will use {min_cov_placeholder} for {mode} mode)")
                                min_cov = None
                            else:
                                min_cov = st.number_input(
                                    "Minimum Coverage",
                                    min_value=1,
                                    max_value=100,
                                    value=min_cov_default,
                                    help="Minimum coverage for modification calls"
                                )
                            
                            # per_mod
                            per_mod = st.number_input(
                                "Per Mod Threshold",
                                min_value=1,
                                max_value=100,
                                value=extracted_params.get("per_mod", 5),
                                help="Percentage threshold for modifications (default: 5)"
                            )
                            
                            # accuracy
                            accuracy_options = ["sup", "hac", "fast"]
                            current_accuracy = extracted_params.get("accuracy", "sup")
                            accuracy_index = accuracy_options.index(current_accuracy) if current_accuracy in accuracy_options else 0
                            accuracy = st.selectbox(
                                "Basecalling Accuracy",
                                accuracy_options,
                                index=accuracy_index,
                                help="Model accuracy: sup=super accurate, hac=high accuracy, fast=fast mode"
                            )
                        
                        st.divider()
                        
                        # Action buttons
                        col1, col2 = st.columns(2)
                        
                        submit_approve = col1.form_submit_button("✅ Approve", use_container_width=True)
                        submit_reject = col2.form_submit_button("❌ Reject", use_container_width=True)
                        
                        if submit_approve:
                            # Build edited params
                            edited_params = {
                                "sample_name": sample_name,
                                "mode": mode,
                                "input_type": input_type,
                                "entry_point": entry_point if entry_point != "(auto)" else None,
                                "input_directory": input_directory,
                                "reference_genome": reference_genomes,
                                "modifications": modifications if modifications else None,
                                # Advanced parameters
                                "modkit_filter_threshold": modkit_threshold,
                                "min_cov": min_cov,
                                "per_mod": per_mod,
                                "accuracy": accuracy,
                            }
                            
                            # Update block with edited params and approved status
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            
                            make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            st.rerun()
                        
                        if submit_reject:
                            st.session_state[f"rejecting_{block_id}"] = True
                            st.rerun()
                
                # Show rejection feedback form if user clicked reject
                if st.session_state.get(f"rejecting_{block_id}", False):
                    st.write("**💬 Why are you rejecting this plan?**")
                    rejection_reason = st.text_area(
                        "Feedback",
                        placeholder="E.g., 'Use GRCh38 instead of mm39' or 'Wrong input path'",
                        key=f"rejection_reason_{block_id}"
                    )
                    
                    col1, col2 = st.columns(2)
                    if col1.button("Submit Rejection", key=f"submit_reject_{block_id}"):
                        # Update block with rejection
                        payload_update = dict(content)
                        payload_update["rejection_reason"] = rejection_reason
                        payload_update["attempt_number"] = attempt_number
                        
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "REJECTED", "payload": payload_update}
                        )
                        
                        # Clear rejection state
                        del st.session_state[f"rejecting_{block_id}"]
                        st.rerun()
                    
                    if col2.button("Cancel", key=f"cancel_reject_{block_id}"):
                        del st.session_state[f"rejecting_{block_id}"]
                        st.rerun()

    
    elif btype == "EXECUTION_JOB":
        # Job execution monitoring with Nextflow progress visualization
        # All data comes from Server1 (which polls Server3 and updates the block)
        with st.chat_message("assistant", avatar="⚙️"):
            run_uuid = content.get("run_uuid", "")
            sample_name = content.get("sample_name", "Unknown")
            mode = content.get("mode", "Unknown")
            
            st.write(f"### 🧬 Nextflow Job: {sample_name} ({mode})")
            st.caption(f"Run UUID: `{run_uuid}`")
            
            # Read job status from block payload (updated by Server1)
            job_status = content.get("job_status", {})
            
            if job_status:
                status_str = job_status.get("status", content.get("status", "UNKNOWN"))
                progress = job_status.get("progress_percent", 0)
                message = job_status.get("message", content.get("message", ""))
                tasks = job_status.get("tasks", {})
                
                # Status indicator
                if status_str == "COMPLETED":
                    st.success(f"✅ {message}")
                    
                    # Show completed tasks summary
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        total = tasks.get("total", 0)
                        completed_count = tasks.get("completed_count", 0)
                        
                        st.metric("✅ Completed Tasks", f"{completed_count}/{total}")
                        
                        # Group and display completed tasks
                        if completed:
                            with st.expander(f"📋 Completed Tasks ({len(completed)} total)", expanded=False):
                                # Group tasks by base name
                                task_groups = {}
                                for task in completed:
                                    if '(' in task and ')' in task:
                                        base_name = task[:task.rfind('(')].strip()
                                        try:
                                            instance = int(task[task.rfind('(')+1:task.rfind(')')].strip())
                                            if base_name not in task_groups:
                                                task_groups[base_name] = []
                                            task_groups[base_name].append(instance)
                                        except ValueError:
                                            task_groups[task] = None
                                    else:
                                        task_groups[task] = None
                                
                                # Display grouped tasks
                                for base_name, instances in task_groups.items():
                                    if instances is None:
                                        st.text(f"✔ {base_name}")
                                    elif len(instances) == 1:
                                        st.text(f"✔ {base_name} ({instances[0]})")
                                    else:
                                        instances_sorted = sorted(instances)
                                        st.text(f"✔ {base_name} ({len(instances)}/{max(instances_sorted)})")
                    
                elif status_str == "FAILED":
                    st.error(f"❌ {message}")
                    
                    # Fetch detailed debug information
                    if run_uuid:
                        debug_info = get_job_debug_info(run_uuid)
                        
                        if debug_info:
                            # Show .nextflow.log errors prominently
                            logs_preview = debug_info.get("logs_preview", {})
                            nextflow_log = logs_preview.get(".nextflow.log", {})
                            
                            if nextflow_log.get("errors_and_warnings"):
                                with st.expander("🔥 Nextflow Errors (from .nextflow.log)", expanded=True):
                                    error_container = st.container(height=400)
                                    with error_container:
                                        for line in nextflow_log["errors_and_warnings"]:
                                            if line.strip():
                                                if "ERROR" in line.upper() or "Exception" in line:
                                                    st.error(line[:150])
                                                elif "WARN" in line.upper():
                                                    st.warning(line[:150])
                                                else:
                                                    st.text(line[:150])
                            
                            # Show failed tasks if available
                            failed_count = tasks.get("failed_count", 0) if tasks else 0
                            if failed_count > 0:
                                st.metric("❌ Failed Tasks", failed_count)
                            
                            # Show work directory and markers
                            with st.expander("🔍 Debug Information", expanded=False):
                                st.write(f"**Work Directory:** `{debug_info.get('work_directory', 'N/A')}`")
                                
                                markers = debug_info.get("markers", {})
                                if markers:
                                    st.write("**Job Markers:**")
                                    for marker_name, marker_info in markers.items():
                                        if marker_info.get("exists"):
                                            st.success(f"✅ {marker_name}: {marker_info.get('content', '')[:60]}")
                                        else:
                                            st.info(f"⬜ {marker_name}: Not present")
                                
                                # Show other log files
                                if logs_preview:
                                    st.write("**Available Log Files:**")
                                    for log_name, log_info in logs_preview.items():
                                        if log_name != ".nextflow.log" and log_info.get("exists"):
                                            size = log_info.get("size", 0)
                                            lines = log_info.get("lines", 0)
                                            st.text(f"📄 {log_name}: {size:,} bytes, {lines} lines")
                                            
                                            # Show last few lines for stderr/stdout
                                            if log_name in [".command.err", ".command.out"]:
                                                last_lines = log_info.get("last_20_lines", [])
                                                if last_lines:
                                                    with st.expander(f"Last lines from {log_name}", expanded=False):
                                                        for line in last_lines[-10:]:
                                                            if line.strip():
                                                                st.text(line[:120])
                    
                    # Also show regular logs from job_status
                    logs = content.get("logs", [])
                    if logs:
                        with st.expander("📋 Job Logs", expanded=False):
                            log_container = st.container(height=300)
                            with log_container:
                                for log in reversed(logs[-50:]):
                                    timestamp = log.get("timestamp", "")
                                    level = log.get("level", "INFO")
                                    msg = log.get("message", "")
                                    
                                    if level == "ERROR":
                                        st.error(f"[{timestamp}] {msg}")
                                    elif level == "WARN":
                                        st.warning(f"[{timestamp}] {msg}")
                                    else:
                                        st.text(f"[{timestamp}] {msg}")
                    
                elif status_str == "RUNNING":
                    st.info(f"🔄 {message}")
                    
                    # Progress bar
                    st.progress(progress / 100.0, text=f"Progress: {progress}%")
                    
                    # Nextflow-style task display
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        running = tasks.get("running", [])
                        total = tasks.get("total", 0)
                        completed_count = tasks.get("completed_count", 0)
                        failed_count = tasks.get("failed_count", 0)
                        
                        # Summary metrics
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("✅ Completed", f"{completed_count}/{total}")
                        with col2:
                            st.metric("🏃 Running", len(running))
                        with col3:
                            if failed_count > 0:
                                st.metric("❌ Failed", failed_count)
                        
                        # Show running tasks in Nextflow style
                        if running:
                            st.write("**🏃 Currently Running:**")
                            for task in running[-5:]:  # Show last 5 running tasks
                                # Format like: [7b/34a1] mainWorkflow:doradoDownloadTask (1) [100%]
                                task_hash = task.split(':')[-1][:4] if ':' in task else "????"
                                st.code(f"[..../{task_hash}] {task}", language="bash")
                        
                        # Show recently completed with grouping
                        if completed:
                            with st.expander(f"✅ Recently Completed ({len(completed)} total)", expanded=False):
                                # Group tasks by base name
                                task_groups = {}
                                for task in completed:
                                    if '(' in task and ')' in task:
                                        base_name = task[:task.rfind('(')].strip()
                                        try:
                                            instance = int(task[task.rfind('(')+1:task.rfind(')')].strip())
                                            if base_name not in task_groups:
                                                task_groups[base_name] = []
                                            task_groups[base_name].append(instance)
                                        except ValueError:
                                            task_groups[task] = None
                                    else:
                                        task_groups[task] = None
                                
                                # Display grouped tasks
                                for base_name, instances in task_groups.items():
                                    if instances is None:
                                        st.text(f"✔ {base_name}")
                                    elif len(instances) == 1:
                                        st.text(f"✔ {base_name} ({instances[0]})")
                                    else:
                                        instances_sorted = sorted(instances)
                                        st.text(f"✔ {base_name} ({len(instances)}/{max(instances_sorted)})")
                else:
                    st.warning(f"⏳ Status: {status_str}")
                    if message:
                        st.caption(message)
            else:
                # Fallback for initial state
                init_message = content.get("message", "Job submitted")
                st.info(f"⏳ {init_message}")
            
            # Show logs toggle for non-failed jobs (failed jobs show logs above)
            if status_str != "FAILED":
                logs = content.get("logs", [])
                if logs and st.checkbox("Show Logs", key=f"logs_{block_id}"):
                    st.write("**Recent Logs:**")
                    log_container = st.container(height=300)
                    with log_container:
                        for log in reversed(logs[-30:]):  # Show last 30 in reverse chronological order
                            timestamp = log.get("timestamp", "")
                            level = log.get("level", "INFO")
                            msg = log.get("message", "")
                            
                            # Color-code by level
                            if level == "ERROR":
                                st.error(f"[{timestamp}] {msg}")
                            elif level == "WARN":
                                st.warning(f"[{timestamp}] {msg}")
                            else:
                                st.text(f"[{timestamp}] {msg}")
    
    else:
        with st.chat_message("system", avatar="⚙️"):
            st.code(f"[{btype}] {content}")


# --- 4. MAIN RENDER LOOP ---

# Capture the ID once for this entire run
active_id = st.session_state.active_project_id

st.title(f"Project: {active_id}")

# 1. Fetch & Sanitize (only if we need to refresh)
blocks = get_sanitized_blocks(active_id)
st.session_state.blocks = blocks  # Update session state with fresh blocks

# 2. Render
if not st.session_state.blocks:
    # Auto-send welcome prompt for empty projects so the LLM introduces itself
    if not st.session_state.get("_welcome_sent_for") or st.session_state["_welcome_sent_for"] != active_id:
        st.session_state["_welcome_sent_for"] = active_id
        try:
            resp = make_authenticated_request(
                "POST",
                f"{API_URL}/chat",
                json={
                    "project_id": active_id,
                    "message": "Hello, what can you help me with?",
                    "skill": "welcome",
                    "model": model_choice
                }
            )
            if resp.status_code == 200:
                time.sleep(0.5)
                st.rerun()
        except Exception:
            pass  # Fall through to empty state if request fails
    st.info(f"👋 **Project `{active_id}` is empty.**\n\nAsk Agoutic to start a task!")
else:
    all_blocks = st.session_state.blocks
    # Default: show last 30 blocks. User can load more.
    max_visible = st.session_state.get("_max_visible_blocks", 30)
    
    if len(all_blocks) > max_visible:
        hidden_count = len(all_blocks) - max_visible
        if st.button(f"⬆️ Load {min(hidden_count, 30)} older messages ({hidden_count} hidden)"):
            st.session_state["_max_visible_blocks"] = max_visible + 30
            st.rerun()
        visible_blocks = all_blocks[-max_visible:]
    else:
        visible_blocks = all_blocks
    
    for blk in visible_blocks:
        render_block(blk)

st.write("---")

# 3. Chat Input
if prompt := st.chat_input("Ask Agoutic to do something..."):
    with st.chat_message("user"):
        st.write(prompt)
    
    try:
        # Send using the ACTIVE ID
        resp = make_authenticated_request(
            "POST",
            f"{API_URL}/chat",
            json={
                "project_id": active_id, 
                "message": prompt,
                "skill": "welcome",
                "model": model_choice
            }
        )
        if resp.status_code != 200:
            st.error(f"Chat request failed: {resp.status_code} - {resp.text}")
        else:
            time.sleep(0.5) 
            st.rerun()
    except Exception as e:
        st.error(f"Failed to send message: {e}")

# 4. Auto-Refresh
if auto_refresh:
    time.sleep(poll_seconds)
    st.rerun()