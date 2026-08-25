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


def _share_success_message(project_name: str, email: str, role: str) -> str:
    target_name = (project_name or "this project").strip() or "this project"
    return f"Shared **{target_name}** with **{email}** as **{role}**."


def _response_error_detail(response: requests.Response) -> str:
    error_detail = response.text
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error_detail = payload.get("detail") or error_detail
    except Exception:
        pass
    return error_detail


def _matching_collaborator_user_id(collaborators: list[dict] | None, email: str) -> str | None:
    normalized = (email or "").strip().lower()
    if not normalized:
        return None

    for collaborator in collaborators or []:
        candidate = str(collaborator.get("email") or "").strip().lower()
        if candidate == normalized:
            user_id = str(collaborator.get("user_id") or "").strip()
            return user_id or None
    return None


def _submit_share_request(*, api_url: str, project_id: str, email: str, role: str, request_kwargs: dict, timeout: int = 10) -> tuple[requests.Response, str]:
    create_resp = _buffer_and_close_response(
        requests.post(
            f"{api_url}/projects/{project_id}/collaborators",
            json={"email": email, "role": role},
            timeout=timeout,
            **request_kwargs,
        )
    )
    error_detail = _response_error_detail(create_resp).lower()
    if create_resp.status_code != 409 or "already has project access" not in error_detail:
        return create_resp, "created"

    collaborators_resp = _buffer_and_close_response(
        requests.get(
            f"{api_url}/projects/{project_id}/collaborators",
            timeout=timeout,
            **request_kwargs,
        )
    )
    if collaborators_resp.status_code != 200:
        return create_resp, "created"

    collaborators_payload = collaborators_resp.json() if hasattr(collaborators_resp, "json") else {}
    collaborator_user_id = _matching_collaborator_user_id(
        collaborators_payload.get("collaborators", []) if isinstance(collaborators_payload, dict) else [],
        email,
    )
    if not collaborator_user_id:
        return create_resp, "created"

    update_resp = _buffer_and_close_response(
        requests.patch(
            f"{api_url}/projects/{project_id}/collaborators/{collaborator_user_id}",
            json={"role": role},
            timeout=timeout,
            **request_kwargs,
        )
    )
    return update_resp, "updated"


def _format_project_collaborator_roster(project_name: str, collaborators: list[dict] | None, current_user_id: str | None, activity_status_fn) -> list[str]:
    target_name = (project_name or "this project").strip() or "this project"
    roster = collaborators or []
    lines = [f"Users in **{target_name}** ({len(roster)}):"]
    if not roster:
        lines.append("- No collaborators found.")
        return lines

    current_user_token = str(current_user_id or "").strip()
    for collaborator in roster:
        collaborator_user_id = str(collaborator.get("user_id") or "").strip()
        label = str(
            collaborator.get("display_name")
            or collaborator.get("username")
            or collaborator.get("email")
            or collaborator_user_id
            or "Unknown user"
        ).strip()
        email = str(collaborator.get("email") or "").strip()
        if email and email.lower() != label.lower():
            label = f"{label} ({email})"

        role_label = "Owner" if collaborator.get("is_owner") else str(collaborator.get("role") or "viewer").title()
        if collaborator_user_id and collaborator_user_id == current_user_token:
            role_label = f"{role_label} · You"

        _activity_state, activity_label = activity_status_fn(collaborator)
        lines.append(f"- **{label}** — {role_label} · {activity_label}")
    return lines


def render_project_collaborator_list(*, prompt: str, active_project: dict, collaborators: list[dict] | None, current_user_id: str | None, activity_status_fn):
    with st.chat_message("user"):
        st.write(prompt or "list users")

    with st.chat_message("assistant"):
        for line in _format_project_collaborator_roster(
            active_project.get("name") or "this project",
            collaborators,
            current_user_id,
            activity_status_fn,
        ):
            st.markdown(line)


def render_file_upload(*, api_url: str, active_id: str, build_auth_request_kwargs_fn, disabled: bool = False, disabled_reason: str | None = None):
    """Render file upload UI and perform uploads for the active project."""
    with st.expander("📎 Upload files", expanded=False):
        if disabled:
            st.info(disabled_reason or "Uploads are disabled for this project.")
            return

        uploaded_files = st.file_uploader(
            "Drop files here to upload to your project's data/ folder",
            accept_multiple_files=True,
            key="file_upload_widget",
        )
        if uploaded_files and st.button("Upload", key="upload_btn"):
            request_kwargs = build_auth_request_kwargs_fn()
            files_payload = [
                ("files", (uf.name, uf.getvalue(), uf.type or "application/octet-stream"))
                for uf in uploaded_files
            ]
            try:
                resp = _buffer_and_close_response(requests.post(
                    f"{api_url}/projects/{active_id}/upload",
                    files=files_payload,
                    **request_kwargs,
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


def render_project_share_form(
    *,
    api_url: str,
    active_project: dict,
    user: dict,
    build_auth_request_kwargs_fn,
):
    feedback = st.session_state.get("_share_form_feedback")
    if feedback and feedback.get("project_id") == active_project.get("id"):
        with st.chat_message("assistant"):
            if feedback.get("status") == "success":
                st.success(feedback.get("message", "Project shared."))
            else:
                st.error(feedback.get("message", "Sharing failed."))

    pending = st.session_state.get("_pending_share_form")
    if not pending or pending.get("project_id") != active_project.get("id"):
        return

    with st.chat_message("user"):
        st.write(pending.get("prompt") or "Share this project")

    with st.chat_message("assistant"):
        project_name = active_project.get("name") or "this project"
        st.markdown(f"Share **{project_name}** by entering the collaborator email and access level below.")

        error_message = pending.get("error")
        if error_message:
            st.error(error_message)

        with st.form(key=f"share_project_form_{active_project.get('id')}"):
            email = st.text_input(
                "Collaborator email",
                value=pending.get("email") or "",
                placeholder="name@example.com",
            )
            role = st.selectbox(
                "Role",
                ["viewer", "editor"],
                index=0 if (pending.get("role") or "viewer") == "viewer" else 1,
            )
            submitted = st.form_submit_button("Share project")

        if not submitted:
            return

        request_kwargs = build_auth_request_kwargs_fn()
        try:
            resp, action = _submit_share_request(
                api_url=api_url,
                project_id=active_project["id"],
                email=email,
                role=role,
                request_kwargs=request_kwargs,
                timeout=10,
            )
        except Exception as exc:
            pending.update({"email": email, "role": role, "error": str(exc)})
            st.session_state["_pending_share_form"] = pending
            st.session_state["_share_form_feedback"] = {
                "project_id": active_project.get("id"),
                "status": "error",
                "message": str(exc),
            }
            st.rerun()

        if resp.status_code == 200:
            st.session_state.pop("_pending_share_form", None)
            st.session_state["_share_form_feedback"] = {
                "project_id": active_project.get("id"),
                "status": "success",
                "message": (
                    f"Updated **{email}** on **{project_name}** to **{role}**."
                    if action == "updated"
                    else _share_success_message(project_name, email, role)
                ),
            }
            st.rerun()

        error_detail = _response_error_detail(resp)

        pending.update({"email": email, "role": role, "error": error_detail})
        st.session_state["_pending_share_form"] = pending
        st.session_state["_share_form_feedback"] = {
            "project_id": active_project.get("id"),
            "status": "error",
            "message": error_detail,
        }
        st.rerun()


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
    ac_request_kwargs = dict(active_chat.get("auth_request_kwargs") or {})
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
                sr = _buffer_and_close_response(requests.get(
                    f"{api_url}/chat/status/{ac_request_id}",
                    timeout=3,
                    **ac_request_kwargs,
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
                    _buffer_and_close_response(requests.post(
                        f"{api_url}/chat/cancel/{ac_request_id}",
                        timeout=5,
                        **ac_request_kwargs,
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

    should_rerun = False

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
                should_rerun = True

        st.caption(f"⏱️ {elapsed:.0f}s elapsed")
        if should_rerun:
            time.sleep(0.3)
            st.rerun()


def launch_chat_request(*, api_url: str, active_id: str, prompt: str, model_choice: str, build_auth_request_kwargs_fn):
    """Start a threaded chat request and persist request state in session."""
    request_id = str(uuid.uuid4())
    auth_request_kwargs = build_auth_request_kwargs_fn()
    result_holder = {"response": None, "error": None}

    def _send_chat_request():
        try:
            result_holder["response"] = _buffer_and_close_response(requests.post(
                f"{api_url}/chat",
                json={
                    "project_id": active_id,
                    "message": prompt,
                    "skill": "welcome",
                    "model": model_choice,
                    "request_id": request_id,
                },
                timeout=900,
                **auth_request_kwargs,
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
        "auth_request_kwargs": auth_request_kwargs,
        "project_id": active_id,
    }
    time.sleep(0.5)
    st.rerun()
