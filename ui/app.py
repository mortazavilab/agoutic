import time
import requests
import streamlit as st

# --- CONFIG ---
API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="AGOUTIC v2.3", layout="wide")

# --- SIDEBAR ---
with st.sidebar:
    st.title("🧬 AGOUTIC")
    project_id = st.text_input("Project ID", value="test_project_001")
    
    # Model Selection (New!)
    model_choice = st.selectbox("Brain Model", ["default", "fast", "smart"], index=0)

    # --- Project Watcher (Auto-Reset) ---
    if "current_pid" not in st.session_state:
        st.session_state.current_pid = project_id

    if st.session_state.current_pid != project_id:
        st.session_state.blocks = []
        st.session_state.since_seq = 0
        st.session_state.current_pid = project_id
        st.rerun()

    auto_refresh = st.toggle("Live Stream", value=True)
    poll_seconds = st.slider("Poll interval (sec)", 1, 5, 1)

    if st.button("Clear View"):
        st.session_state.blocks = []
        st.session_state.since_seq = 0
        st.rerun()

# --- SESSION STATE ---
if "since_seq" not in st.session_state:
    st.session_state.since_seq = 0
if "blocks" not in st.session_state:
    st.session_state.blocks = []

# --- FUNCTIONS ---
def fetch_updates() -> bool:
    """Poll new blocks since since_seq."""
    try:
        resp = requests.get(
            f"{API_URL}/blocks",
            params={"project_id": project_id, "since_seq": st.session_state.since_seq},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        new_blocks = data.get("blocks", [])
        if new_blocks:
            st.session_state.blocks.extend(new_blocks)
            st.session_state.since_seq = data.get("latest_seq", st.session_state.since_seq)
            return True
    except Exception:
        pass
    return False

def render_block(block: dict):
    """Render a single block based on its type."""
    btype = block["type"]
    content = block.get("payload", {})
    status = block.get("status", "NEW")
    block_id = block["id"]

    # 1. User Message (Fixed Type Name)
    if btype == "USER_MESSAGE" or btype == "USER_PROMPT":
        with st.chat_message("user"):
            st.write(content.get("text", ""))

    # 2. Agent Plan (Now supports Markdown from LLM)
    elif btype == "AGENT_PLAN":
        with st.chat_message("assistant", avatar="🤖"):
            # Show the skill used if available
            if "skill" in content:
                st.caption(f"Skill: `{content['skill']}` | Model: `{content.get('model', 'unknown')}`")
            
            # The LLM returns a markdown string, so we render it directly
            if "markdown" in content:
                st.markdown(content["markdown"])
            else:
                # Fallback for old list-style plans
                st.write("### 📋 Proposed Plan")
                steps = content.get("content", [])
                for i, step in enumerate(steps, 1):
                    st.write(f"{i}. {step}")

    # 3. Approval Gate (Interactive)
    elif btype == "APPROVAL_GATE":
        with st.chat_message("assistant", avatar="🚦"):
            st.write("### ✅ Approval Required")
            st.write(content.get("label", "Approve this plan?"))

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

    # 4. Fallback for other system messages
    else:
        with st.chat_message("system", avatar="⚙️"):
            st.code(f"[{btype}] {content}")

# --- MAIN LAYOUT ---
st.title(f"Project: {project_id}")

# 1. Render History
fetch_updates()
for blk in st.session_state.blocks:
    render_block(blk)

# 2. Chat Input (The Critical New Feature)
if prompt := st.chat_input("Ask Agoutic to do something..."):
    # Render user message immediately (optimistic UI)
    with st.chat_message("user"):
        st.write(prompt)
    
    # Send to Server
    try:
        requests.post(
            f"{API_URL}/chat",
            json={
                "project_id": project_id, 
                "message": prompt,
                "skill": "ENCODE_LongRead", # Default for now
                "model": model_choice
            }
        )
        # Force a refresh to see the "Thinking..." status or result
        time.sleep(0.5) 
        st.rerun()
        
    except Exception as e:
        st.error(f"Failed to send message: {e}")

# 3. Auto-Refresh Loop
if auto_refresh:
    time.sleep(poll_seconds)
    st.rerun()