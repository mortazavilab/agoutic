import threading
import time
import uuid

import requests
import streamlit as st


def _buffer_and_close_response(response: requests.Response) -> requests.Response:
    try:
        _ = response.content
    finally:
        response.close()
    return response


def render_file_upload(*, api_url: str, active_id: str, get_session_cookie_fn):
    """Render file upload UI and perform uploads for the active project."""
    with st.expander("📎 Upload files", expanded=False):
        uploaded_files = st.file_uploader(
            "Drop files here to upload to your project's data/ folder",
            accept_multiple_files=True,
            key="file_upload_widget",
        )
        if uploaded_files and st.button("Upload", key="upload_btn"):
            session_token = get_session_cookie_fn()
            cookies = {"session": session_token} if session_token else {}
            files_payload = [
                ("files", (uf.name, uf.getvalue(), uf.type or "application/octet-stream"))
                for uf in uploaded_files
            ]
            try:
                resp = _buffer_and_close_response(requests.post(
                    f"{api_url}/projects/{active_id}/upload",
                    files=files_payload,
                    cookies=cookies,
                ))
                if resp.status_code == 200:
                    result = resp.json()
                    st.success(f"✅ Uploaded {result['count']} file(s)")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error(f"Upload failed: {resp.text}")
            except Exception as e:
                st.error(f"Upload error: {e}")


def handle_active_chat(*, api_url: str, active_project_id: str | None = None):
    """Render and drive in-flight chat status until completion/cancel."""
    active_chat = st.session_state.get("_active_chat")
    if active_chat is None:
        return
    if active_project_id and active_chat.get("project_id") not in {None, "", active_project_id}:
        return

    ac_thread = active_chat["thread"]
    ac_request_id = active_chat["request_id"]
    ac_result = active_chat["result_holder"]
    ac_start = active_chat["start_time"]
    ac_session_token = active_chat["session_token"]
    stage_icons = {
        "waiting": "⏳",
        "thinking": "🧠",
        "switching": "🔄",
        "context": "📋",
        "tools": "🔌",
        "analyzing": "📊",
        "done": "✅",
        "cancelled": "⏹️",
    }

    if ac_thread.is_alive():
        with st.chat_message("assistant"):
            status_box = st.status("🧠 Processing...", expanded=True)
            elapsed = time.time() - ac_start
            try:
                cookies = {"session": ac_session_token} if ac_session_token else {}
                sr = _buffer_and_close_response(requests.get(
                    f"{api_url}/chat/status/{ac_request_id}",
                    cookies=cookies,
                    timeout=3,
                ))
                if sr.status_code == 200:
                    info = sr.json()
                    stage = info.get("stage", "thinking")
                    detail = info.get("detail", "")
                    icon = stage_icons.get(stage, "⏳")
                    display = detail or "Processing..."
                    status_box.update(label=f"{icon} {display}")
                    status_box.caption(f"⏱️ {elapsed:.0f}s elapsed")
            except Exception:
                status_box.caption(f"⏱️ {elapsed:.0f}s elapsed")

            if st.button("⏹️ Stop", key="_stop_chat_btn"):
                try:
                    cookies = {"session": ac_session_token} if ac_session_token else {}
                    _buffer_and_close_response(requests.post(
                        f"{api_url}/chat/cancel/{ac_request_id}",
                        cookies=cookies,
                        timeout=5,
                    ))
                except Exception:
                    pass
                status_box.update(label="⏹️ Stopping...", state="error")

        time.sleep(1.5)
        st.rerun()

    ac_thread.join()
    elapsed = time.time() - ac_start
    del st.session_state["_active_chat"]

    chat_failed = bool(
        ac_result["error"]
        or (ac_result["response"] is not None and ac_result["response"].status_code != 200)
    )
    if chat_failed:
        st.session_state["_last_prompt_failed"] = True

    with st.chat_message("assistant"):
        if ac_result["error"]:
            st.error(f"Failed to send message: {ac_result['error']}")
        elif ac_result["response"] is not None and ac_result["response"].status_code == 429:
            try:
                detail = ac_result["response"].json().get("detail", {})
                used = detail.get("tokens_used", 0)
                limit = detail.get("token_limit", 0)
                st.warning(
                    f"**🪙 Token quota exceeded.**\n\n"
                    f"You have used **{used:,}** of your **{limit:,}** token limit. "
                    "Please contact an admin to increase your quota."
                )
            except Exception:
                st.warning("🪙 You have reached your token limit. Please contact an admin.")
        elif ac_result["response"] is not None and ac_result["response"].status_code != 200:
            st.error(
                f"Chat request failed: {ac_result['response'].status_code} "
                f"- {ac_result['response'].text}"
            )
        else:
            resp_json = ac_result["response"].json() if ac_result["response"] else {}
            status = resp_json.get("status", "")
            if status == "cancelled":
                st.info("⏹️ Stopped by user.")
            else:
                st.empty()
                st.session_state.pop("_last_prompt_failed", None)

        st.caption(f"⏱️ {elapsed:.0f}s elapsed")
        time.sleep(0.3)
        st.rerun()


def launch_chat_request(*, api_url: str, active_id: str, prompt: str, model_choice: str, get_session_cookie_fn):
    """Start a threaded chat request and persist request state in session."""
    request_id = str(uuid.uuid4())
    session_token = get_session_cookie_fn()
    result_holder = {"response": None, "error": None}

    def _send_chat_request():
        try:
            cookies = {"session": session_token} if session_token else {}
            result_holder["response"] = _buffer_and_close_response(requests.post(
                f"{api_url}/chat",
                json={
                    "project_id": active_id,
                    "message": prompt,
                    "skill": "welcome",
                    "model": model_choice,
                    "request_id": request_id,
                },
                cookies=cookies,
                timeout=900,
            ))
        except Exception as exc:
            result_holder["error"] = exc

    thread = threading.Thread(target=_send_chat_request, daemon=True)
    thread.start()

    st.session_state["_last_sent_prompt"] = prompt
    st.session_state.pop("_last_prompt_failed", None)
    st.session_state.pop("_pending_prompt", None)
    st.session_state["_active_chat"] = {
        "thread": thread,
        "request_id": request_id,
        "result_holder": result_holder,
        "start_time": time.time(),
        "session_token": session_token,
        "project_id": active_id,
    }
    time.sleep(0.5)
    st.rerun()
