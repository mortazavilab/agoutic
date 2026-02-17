import time
import threading
import uuid
import requests
import datetime
import os
import streamlit as st
import streamlit.components.v1 as _st_components
from auth import require_auth, logout_button, make_authenticated_request, get_session_cookie

# --- CONFIG ---
# Use environment variable or default to localhost
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="AGOUTIC v3.0", layout="wide")

# --- AUTHENTICATION ---
# Require authentication before showing any UI
user = require_auth(API_URL)

# --- 1. STATE MANAGEMENT ---
def _create_project_server_side(name: str = None) -> str:
    """Create a project via POST /projects and return the server-generated UUID."""
    project_name = name or f"Project {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    try:
        resp = make_authenticated_request(
            "POST",
            f"{API_URL}/projects",
            json={"name": project_name},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()["id"]
    except Exception:
        pass
    # Fallback: if server is unreachable, generate a local UUID (will be registered on first chat)
    import uuid as _uuid
    return str(_uuid.uuid4())

# Check if we're creating a new project (flag set by New Project button)
if st.session_state.get("_create_new_project", False):
    # Create project via server-side endpoint (server generates UUID)
    new_id = _create_project_server_side()
    st.session_state.active_project_id = new_id
    st.session_state.blocks = []
    # Clear project-related data
    for key in ['loaded_conversation', 'selected_job', 'chat_history', 
                'skill_content', 'selected_skill', 'job_status', 'messages',
                '_max_visible_blocks', '_welcome_sent_for']:
        if key in st.session_state:
            del st.session_state[key]
    # Clear any widget keys left over from old block rendering
    # (form keys, checkbox keys, rejection state, etc.)
    stale_prefixes = ('params_form_', 'logs_', 'rejecting_', 'rejection_reason_',
                      'submit_reject_', 'cancel_reject_')
    for key in list(st.session_state.keys()):
        if any(key.startswith(p) for p in stale_prefixes):
            del st.session_state[key]
    # Reset the project ID text input widget so it doesn't hold the old value
    st.session_state["_project_id_input"] = new_id
    # Guard: prevent text_input comparison from reverting the ID on this rerun.
    # Use a counter (not a bool) so the guard survives multiple rerun cycles
    # while Streamlit's text_input widget syncs to the new value.
    st.session_state["_switch_grace_reruns"] = 3
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
                # No previous project — create one via server
                st.session_state.active_project_id = _create_project_server_side()
        else:
            st.session_state.active_project_id = _create_project_server_side()
    except:
        st.session_state.active_project_id = _create_project_server_side()
    
# Initialize other state variables
if "blocks" not in st.session_state:
    st.session_state.blocks = []

# Detect project switch: clear stale blocks immediately so they never render
if st.session_state.get("_last_rendered_project") != st.session_state.active_project_id:
    st.session_state.blocks = []
    st.session_state._last_rendered_project = st.session_state.active_project_id
    st.session_state.pop("_welcome_sent_for", None)
    # Suppress auto-refresh for a few cycles after switching to avoid
    # Streamlit DOM-reuse artefacts (old messages blinking).
    st.session_state["_suppress_auto_refresh"] = 3

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
    
    # Sync: if user manually edited the field, update active project.
    # Skip while the grace counter is active (prevents the text_input's stale
    # old value from silently reverting active_project_id back).
    grace = st.session_state.get("_switch_grace_reruns", 0)
    if grace > 0:
        st.session_state["_switch_grace_reruns"] = grace - 1
    elif user_input and user_input != st.session_state.active_project_id:
        st.session_state.active_project_id = user_input
        st.session_state.blocks = []
        st.session_state._last_rendered_project = user_input
        st.session_state.pop("_welcome_sent_for", None)
        st.rerun()

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
        # Get user's projects (server-side CRUD endpoint)
        proj_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/projects",
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
                            st.session_state["_switch_grace_reruns"] = 3
                            st.session_state.pop("_welcome_sent_for", None)
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
            else:
                st.caption("No jobs yet")
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


def render_block(block, expected_project_id: str = ""):
    """Render a single block.

    If expected_project_id is provided, silently skip blocks that belong
    to a different project (last line of defence against ghost content).
    """
    b_project = block.get("project_id", "???")
    if expected_project_id and b_project != expected_project_id:
        return  # ghost block – do not render

    btype = block["type"]
    content = block.get("payload", {})
    status = block.get("status", "NEW")
    block_id = block["id"]
    
    # Metadata
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
        with st.chat_message("assistant", avatar="⚙️"):
            run_uuid = content.get("run_uuid", "")
            sample_name = content.get("sample_name", "Unknown")
            mode = content.get("mode", "Unknown")
            
            st.write(f"### 🧬 Nextflow Job: {sample_name} ({mode})")
            st.caption(f"Run UUID: `{run_uuid}`")
            
            # For RUNNING jobs, live-fetch status directly from Server 1
            # (bypasses stale block payload — always fresh from Server 3)
            job_status = content.get("job_status", {})
            block_status_str = block.get("status", "")
            if run_uuid and block_status_str == "RUNNING":
                try:
                    live_resp = make_authenticated_request(
                        "GET",
                        f"{API_URL}/jobs/{run_uuid}/status",
                        timeout=5,
                    )
                    if live_resp.status_code == 200:
                        job_status = live_resp.json()
                except Exception:
                    pass  # Fall back to block payload
            
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
                    
                    # Job timing info
                    _job_created = block.get("created_at", "")
                    _last_upd = content.get("last_updated", "")
                    _timing_parts = []
                    if _job_created:
                        try:
                            _start_dt = datetime.datetime.fromisoformat(_job_created.replace("Z", "+00:00"))
                            _elapsed = datetime.datetime.now(datetime.timezone.utc) - _start_dt
                            _mins, _secs = divmod(int(_elapsed.total_seconds()), 60)
                            _timing_parts.append(f"⏱️ Running for {_mins}m {_secs}s")
                        except (ValueError, TypeError):
                            pass
                    if _last_upd:
                        try:
                            _upd_dt = datetime.datetime.fromisoformat(_last_upd.replace("Z", "+00:00"))
                            _ago = datetime.datetime.now(datetime.timezone.utc) - _upd_dt
                            _ago_secs = int(_ago.total_seconds())
                            _timing_parts.append(f"🔄 Updated {_ago_secs}s ago")
                        except (ValueError, TypeError):
                            pass
                    if _timing_parts:
                        st.caption(" | ".join(_timing_parts))
                    
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

# 1. Fetch & Sanitize
blocks = get_sanitized_blocks(active_id)
# Defensive: drop any block whose project_id drifted (e.g. server cache race)
blocks = [b for b in blocks if b.get("project_id") == active_id]
st.session_state.blocks = blocks  # Update session state with fresh blocks

# 2. Render inside st.empty() – this is critical.
#    st.empty() creates a single-element placeholder whose content is
#    **replaced** on every rerun.  Unlike st.container (keyed or not),
#    there is zero DOM-node reuse across reruns, so old chat messages
#    from a previous project can never linger in the browser.
chat_area = st.empty()
with chat_area.container():
    if not blocks:
        st.session_state["_has_running_job"] = False  # No blocks = no running job
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
        # Use local 'blocks' variable directly (not session_state) to avoid
        # any stale reference.
        max_visible = st.session_state.get("_max_visible_blocks", 30)
        
        if len(blocks) > max_visible:
            hidden_count = len(blocks) - max_visible
            if st.button(f"⬆️ Load {min(hidden_count, 30)} older messages ({hidden_count} hidden)"):
                st.session_state["_max_visible_blocks"] = max_visible + 30
                st.rerun()
            visible_blocks = blocks[-max_visible:]
        else:
            visible_blocks = blocks
        
        # --- Scan ALL blocks (not just visible) for running jobs ---
        _has_running_job = False
        _has_pending_submission = False
        _has_finished_job = False
        for blk in blocks:
            btype = blk.get("type")
            bstatus = blk.get("status")
            if btype == "EXECUTION_JOB" and bstatus == "RUNNING":
                _has_running_job = True
            if btype == "EXECUTION_JOB" and bstatus in ("DONE", "FAILED"):
                _has_finished_job = True
            if btype == "APPROVAL_GATE" and bstatus == "APPROVED":
                _has_pending_submission = True

        # Render visible blocks
        for blk in visible_blocks:
            render_block(blk, expected_project_id=active_id)

        # If approval was given but no EXECUTION_JOB block exists yet,
        # the async job submission is still in-flight — keep refreshing.
        if _has_pending_submission and not _has_running_job and not _has_finished_job:
            _has_running_job = True

        # When a job just finished, keep refreshing for 30s to catch
        # auto-analysis blocks that Server 1 creates after completion.
        if _has_finished_job and not _has_running_job:
            last_finish = st.session_state.get("_job_finished_at")
            if last_finish is None:
                st.session_state["_job_finished_at"] = time.time()
                _has_running_job = True   # keep refreshing
            elif time.time() - last_finish < 30:
                _has_running_job = True   # still within grace window
            # else: grace window expired, stop refreshing
        elif _has_running_job:
            # Job is still running — clear any stale finish timestamp
            st.session_state.pop("_job_finished_at", None)

        # Persist to session_state for reliable access in the auto-refresh section
        st.session_state["_has_running_job"] = _has_running_job

st.write("---")

# 3. Chat Input
if prompt := st.chat_input("Ask Agoutic to do something..."):
    with st.chat_message("user"):
        st.write(prompt)

    # --- Threaded request with real-time progress polling ---
    request_id = str(uuid.uuid4())
    session_token = get_session_cookie()
    _result_holder = {"response": None, "error": None}

    def _send_chat_request():
        try:
            cookies = {"session": session_token} if session_token else {}
            _result_holder["response"] = requests.post(
                f"{API_URL}/chat",
                json={
                    "project_id": active_id,
                    "message": prompt,
                    "skill": "welcome",
                    "model": model_choice,
                    "request_id": request_id,
                },
                cookies=cookies,
                timeout=300,
            )
        except Exception as exc:
            _result_holder["error"] = exc

    thread = threading.Thread(target=_send_chat_request, daemon=True)
    thread.start()

    # Stage emoji map for nice display
    _stage_icons = {
        "waiting": "⏳",
        "thinking": "🧠",
        "switching": "🔄",
        "tools": "🔌",
        "analyzing": "📊",
        "done": "✅",
    }

    with st.chat_message("assistant"):
        status_box = st.status("🧠 Thinking...", expanded=True)
        status_text = status_box.empty()
        start_time = time.time()
        last_detail = ""

        while thread.is_alive():
            elapsed = time.time() - start_time
            # Poll the backend for current processing stage
            try:
                cookies = {"session": session_token} if session_token else {}
                sr = requests.get(
                    f"{API_URL}/chat/status/{request_id}",
                    cookies=cookies,
                    timeout=3,
                )
                if sr.status_code == 200:
                    info = sr.json()
                    stage = info.get("stage", "thinking")
                    detail = info.get("detail", "")
                    icon = _stage_icons.get(stage, "⏳")
                    display = detail or "Processing..."
                    if detail != last_detail:
                        last_detail = detail
                    status_box.update(label=f"{icon} {display}")
                    status_text.caption(f"⏱️ {elapsed:.0f}s elapsed")
            except Exception:
                status_text.caption(f"⏱️ {elapsed:.0f}s elapsed")

            thread.join(timeout=1.5)

        thread.join()
        elapsed = time.time() - start_time

        if _result_holder["error"]:
            status_box.update(label="❌ Error", state="error", expanded=True)
            st.error(f"Failed to send message: {_result_holder['error']}")
        elif _result_holder["response"] is not None and _result_holder["response"].status_code != 200:
            status_box.update(label="❌ Error", state="error", expanded=True)
            st.error(
                f"Chat request failed: {_result_holder['response'].status_code} "
                f"- {_result_holder['response'].text}"
            )
        else:
            status_box.update(
                label=f"✅ Done ({elapsed:.0f}s)",
                state="complete",
                expanded=False,
            )
            time.sleep(0.3)
            st.rerun()

# 4. Auto-Refresh
_suppress = st.session_state.get("_suppress_auto_refresh", 0)
_has_running = st.session_state.get("_has_running_job", False)

# Always decrement suppress counter, but NEVER let it block refresh
# when a job is actively running.
if _suppress > 0:
    st.session_state["_suppress_auto_refresh"] = _suppress - 1

if _has_running:
    _wait = min(poll_seconds, 2)
    _wait_ms = int(_wait * 1000)
    st.caption(
        f"🔄 Auto-refreshing every {_wait}s "
        f"(last: {datetime.datetime.now().strftime('%H:%M:%S')})"
    )
    # --- JavaScript fallback: reload page if st.rerun() somehow fails ---
    # The timer fires after 2× the expected interval; if st.rerun() works
    # normally, the iframe is destroyed on rerun so the timer never fires.
    _st_components.html(
        f"""<script>
        setTimeout(function() {{ window.parent.location.reload(); }},
                   {_wait_ms * 3});
        </script>""",
        height=0,
    )
    # --- Primary: Streamlit-native rerun ---
    time.sleep(_wait)
    st.rerun()
elif auto_refresh and _suppress <= 0:
    time.sleep(poll_seconds)
    st.rerun()