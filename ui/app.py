import time
import requests
import datetime
import streamlit as st

# --- CONFIG ---
API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="AGOUTIC v3.0", layout="wide")

# --- 1. STATE MANAGEMENT ---
# We use a single source of truth for the Active Project ID
if "active_project_id" not in st.session_state:
    st.session_state.active_project_id = "test_project_001"

# --- 2. SIDEBAR ---
with st.sidebar:
    st.title("🧬 AGOUTIC")
    
    # [A] NEW PROJECT (Generates Random ID)
    if st.button("✨ New Project", use_container_width=True):
        # Create a unique ID
        new_id = f"project_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        st.session_state.active_project_id = new_id
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
    else:
        with st.chat_message("system", avatar="⚙️"):
            st.code(f"[{btype}] {content}")


# --- 4. MAIN RENDER LOOP ---

# Capture the ID once for this entire run
active_id = st.session_state.active_project_id

st.title(f"Project: {active_id}")

# 1. Fetch & Sanitize
# We only get blocks that survive the filter
blocks = get_sanitized_blocks(active_id)

# 2. Render
if not blocks:
    st.info(f"👋 **Project `{active_id}` is empty.**\n\nAsk Agoutic to start a task!")
else:
    for blk in blocks:
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