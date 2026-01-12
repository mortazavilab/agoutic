import time
import requests
import streamlit as st

API_URL = "http://127.0.0.1:8000"
st.set_page_config(page_title="AGOUTIC v2.3", layout="wide")

# Sidebar
with st.sidebar:
    st.title("🧬 AGOUTIC")
    project_id = st.text_input("Project ID", value="test_project")

    # --- Project Watcher (Auto-Reset) ---
    # If the project changes, clear cached blocks/seq so the UI doesn't go blank
    if "current_pid" not in st.session_state:
        st.session_state.current_pid = project_id

    if st.session_state.current_pid != project_id:
        st.session_state.blocks = []
        st.session_state.since_seq = 0
        st.session_state.current_pid = project_id
        st.rerun()
    # --- End Project Watcher ---

    auto_refresh = st.toggle("Live Stream", value=True)
    poll_seconds = st.slider("Poll interval (sec)", 1, 5, 1)

    if st.button("Clear View"):
        st.session_state.blocks = []
        st.session_state.since_seq = 0
        st.rerun()

# Session state
if "since_seq" not in st.session_state:
    st.session_state.since_seq = 0
if "blocks" not in st.session_state:
    st.session_state.blocks = []


def fetch_updates() -> bool:
    """Poll new blocks since since_seq. Returns True if any new blocks arrived."""
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
    except Exception as e:
        st.sidebar.error(f"API error: {e}")
    return False


def update_local_block(block_id: str, **updates):
    """Optimistically update a block in local cache for snappy UI."""
    for b in st.session_state.blocks:
        if b.get("id") == block_id:
            b.update(updates)
            break


def render_block(block: dict):
    btype = block["type"]
    content = block.get("payload", {})  # matches your earlier naming request
    block_id = block["id"]

    if btype == "USER_PROMPT":
        with st.chat_message("user"):
            st.write(content.get("text", ""))

    elif btype == "AGENT_PLAN":
        with st.chat_message("assistant", avatar="🤖"):
            st.write("### 📋 Proposed Plan")
            steps = content.get("content", [])
            for i, step in enumerate(steps, 1):
                st.checkbox(
                    f"{i}. {step}",
                    key=f"{block_id}_step_{i}",
                    value=True,
                    disabled=True,
                )

    elif btype == "APPROVAL_GATE":
        with st.chat_message("assistant", avatar="🚦"):
            st.write("### ✅ Approval Required")
            st.write(content.get("label", "Approve this plan?"))

            status = block.get("status", "PENDING")

            # If already decided, show static result (no buttons)
            if status == "APPROVED":
                st.success("✅ Approved")
                return
            if status == "REJECTED":
                st.error("❌ Rejected")
                return

            st.warning(f"Status: **{status}**")

            col1, col2 = st.columns(2)

            # Disable double-click: once user clicks, status changes locally and buttons disappear on rerun.
            if col1.button("✅ Approve", key=f"approve_{block_id}"):
                # 1) optimistic UI update (snappy)
                update_local_block(block_id, status="APPROVED")

                # 2) send to backend
                try:
                    r = requests.patch(
                        f"{API_URL}/block/{block_id}",
                        json={"status": "APPROVED"},
                        timeout=5,
                    )
                    r.raise_for_status()
                except Exception as e:
                    # rollback if backend fails
                    update_local_block(block_id, status="PENDING")
                    st.error(f"Approve failed: {e}")

                st.rerun()

            if col2.button("❌ Reject", key=f"reject_{block_id}"):
                update_local_block(block_id, status="REJECTED")
                try:
                    r = requests.patch(
                        f"{API_URL}/block/{block_id}",
                        json={"status": "REJECTED"},
                        timeout=5,
                    )
                    r.raise_for_status()
                except Exception as e:
                    update_local_block(block_id, status="PENDING")
                    st.error(f"Reject failed: {e}")

                st.rerun()

    else:
        with st.chat_message("assistant"):
            st.code(f"[{btype}]\n{content}")


# Main
st.title(f"Project: {project_id}")
st.caption(f"latest seq = {st.session_state.since_seq}")
st.markdown("---")

# Fetch once per run
fetch_updates()

# Render feed
for blk in st.session_state.blocks:
    render_block(blk)

# Auto-refresh
if auto_refresh:
    time.sleep(poll_seconds)
    st.rerun()
