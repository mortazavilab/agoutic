import time
import requests
import datetime
import streamlit as st

# --- CONFIG ---
API_URL = "http://127.0.0.1:8000"
SERVER3_URL = "http://127.0.0.1:8003"

st.set_page_config(page_title="AGOUTIC v3.0", layout="wide")

# --- 1. STATE MANAGEMENT ---
# Initialize with a random project ID if none exists
if "active_project_id" not in st.session_state:
    st.session_state.active_project_id = f"project_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
# Initialize other state variables
if "blocks" not in st.session_state:
    st.session_state.blocks = []

# --- 2. SIDEBAR ---
with st.sidebar:
    st.title("🧬 AGOUTIC")
    
    # [A] NEW PROJECT (Generates Random ID)
    if st.button("✨ New Project", use_container_width=True):
        # Create a unique ID
        new_id = f"project_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # Clear ALL state variables for fresh start
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        # Set the new project ID
        st.session_state.active_project_id = new_id
        st.session_state.blocks = []
        st.rerun()

    # [B] PROJECT ID INPUT
    # We display the current state. If user types here, we update state & rerun.
    user_input = st.text_input(
        "Project ID", 
        value=st.session_state.active_project_id
    )
    
    # Handle manual renaming
    if user_input != st.session_state.active_project_id:
        st.session_state.active_project_id = user_input
        st.rerun()

    st.divider()
    
    # [C] DEBUG TOOLS
    # If things get weird, this button forces a hard reload
    if st.button("🧹 Force Clear / Refresh"):
        st.rerun()

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
        # 1. Ask Server
        resp = requests.get(
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
    """Fetch detailed debug information for a failed job."""
    try:
        resp = requests.get(f"{SERVER3_URL}/jobs/{run_uuid}/debug", timeout=10)
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
                st.markdown(content["markdown"])

    elif btype == "APPROVAL_GATE":
        with st.chat_message("assistant", avatar="🚦"):
            st.write("### ✅ Approval Required")
            st.write(content.get("label", "Approve this plan?"))
            st.caption(f"Block ID: `{block_id}`")

            if status == "APPROVED":
                st.success("✅ Approved")
            elif status == "REJECTED":
                st.error("❌ Rejected")
            else:
                col1, col2 = st.columns(2)
                if col1.button("✅ Approve", key=f"app_{block_id}"):
                    requests.patch(f"{API_URL}/block/{block_id}", json={"status": "APPROVED"})
                    st.rerun()
                if col2.button("❌ Reject", key=f"rej_{block_id}"):
                    requests.patch(f"{API_URL}/block/{block_id}", json={"status": "REJECTED"})
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
    st.info(f"👋 **Project `{active_id}` is empty.**\n\nAsk Agoutic to start a task!")
else:
    for blk in st.session_state.blocks:
        render_block(blk)

st.write("---")

# 3. Chat Input
if prompt := st.chat_input("Ask Agoutic to do something..."):
    with st.chat_message("user"):
        st.write(prompt)
    
    try:
        # Send using the ACTIVE ID
        requests.post(
            f"{API_URL}/chat",
            json={
                "project_id": active_id, 
                "message": prompt,
                "skill": "ENCODE_LongRead",
                "model": model_choice
            }
        )
        time.sleep(0.5) 
        st.rerun()
    except Exception as e:
        st.error(f"Failed to send message: {e}")

# 4. Auto-Refresh
if auto_refresh:
    time.sleep(poll_seconds)
    st.rerun()