"""
Remote Connection Profiles Page
Manage SSH connection profiles used for HPC/SLURM remote execution.
All requests go through the Launchpad REST API.
"""

import streamlit as st
import pandas as pd
import sys
import os
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request

# Launchpad REST URL for SSH profile management
LAUNCHPAD_URL = os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
# Cortex API URL for authentication
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")
# Internal API secret for service-to-service auth with Launchpad
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")

st.set_page_config(page_title="Remote Profiles", page_icon="🔌", layout="wide")

# Require authentication
user = require_auth(API_URL)
user_id = user.get("id") or user.get("user_id", "")

st.title("🔌 Remote Connection Profiles")
st.caption("Manage SSH connection profiles for remote HPC/SLURM execution")
st.caption("Remote base path may use placeholders like {ssh_username}, {project_slug}, {sample_name}, and {workflow_slug}.")


# ── Helpers ──────────────────────────────────────────────────────────

def _launchpad_headers() -> dict:
    """Return headers required for Launchpad service-to-service auth."""
    h = {"Content-Type": "application/json"}
    if INTERNAL_API_SECRET:
        h["X-Internal-Secret"] = INTERNAL_API_SECRET
    return h


def _fmt_datetime(val: str) -> str:
    """Format an ISO datetime string for display."""
    if not val or val == "None":
        return "—"
    try:
        return datetime.fromisoformat(val).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return val[:16] if len(val) > 16 else val


def _load_profiles() -> list[dict]:
    """Fetch SSH profiles for the current user from Launchpad."""
    try:
        resp = make_authenticated_request(
            "GET",
            f"{LAUNCHPAD_URL}/ssh-profiles",
            params={"user_id": user_id},
            headers=_launchpad_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("profiles", [])
        else:
            st.error(f"Failed to load profiles: {resp.status_code}")
            return []
    except Exception as e:
        st.error(f"Cannot connect to Launchpad API: {e}")
        return []


def _load_auth_session(profile_id: str) -> dict:
    """Fetch active local auth session metadata for a profile."""
    try:
        resp = make_authenticated_request(
            "GET",
            f"{LAUNCHPAD_URL}/ssh-profiles/{profile_id}/auth-session",
            params={"user_id": user_id},
            headers=_launchpad_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {"active": False, "message": "Session status unavailable"}


def _response_error_text(resp) -> str:
    """Extract the most useful error text from an HTTP response."""
    try:
        payload = resp.json()
    except Exception:
        return resp.text[:300]

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail[:300]
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message[:300]
    return resp.text[:300]


# ── Create Profile ───────────────────────────────────────────────────

with st.expander("➕ Create Profile", expanded=False):
    with st.form("create_profile_form"):
        st.subheader("New SSH Profile")

        nickname = st.text_input("Nickname *", help="Friendly name for this connection")
        cp_col1, cp_col2 = st.columns(2)
        with cp_col1:
            ssh_host = st.text_input("SSH Host *", help="Hostname or IP address")
            ssh_username = st.text_input("SSH Username *")
        with cp_col2:
            ssh_port = st.number_input("SSH Port", min_value=1, max_value=65535, value=22)
            auth_method = st.selectbox("Auth Method", ["key_file", "ssh_agent"])

        key_file_path = ""
        if auth_method == "key_file":
            key_file_path = st.text_input(
                "Key File Path",
                value="",
                help="Path to the private key file on the server",
            )

        local_username = st.text_input(
            "Local OS Username (optional)",
            help="Local Unix account used to unlock a per-session SSH broker with that user's password.",
        )

        st.markdown("**Remote Execution Defaults**")
        def_col1, def_col2 = st.columns(2)
        with def_col1:
            default_slurm_account = st.text_input("Default CPU Account")
            default_slurm_gpu_account = st.text_input("Default GPU Account")
            remote_base_path = st.text_input("Remote Base Path")
        with def_col2:
            default_slurm_partition = st.text_input("Default CPU Partition")
            default_slurm_gpu_partition = st.text_input("Default GPU Partition")

        submitted = st.form_submit_button("🔧 Create Profile", type="primary")

        if submitted:
            # Validation
            errors = []
            if not nickname.strip():
                errors.append("Nickname is required.")
            if not ssh_host.strip():
                errors.append("SSH Host is required.")
            if not ssh_username.strip():
                errors.append("SSH Username is required.")

            if errors:
                for err in errors:
                    st.error(err)
            else:
                payload = {
                    "user_id": user_id,
                    "nickname": nickname.strip(),
                    "ssh_host": ssh_host.strip(),
                    "ssh_port": ssh_port,
                    "ssh_username": ssh_username.strip(),
                    "auth_method": auth_method,
                }
                if auth_method == "key_file" and key_file_path.strip():
                    payload["key_file_path"] = key_file_path.strip()
                if local_username.strip():
                    payload["local_username"] = local_username.strip()
                if default_slurm_account.strip():
                    payload["default_slurm_account"] = default_slurm_account.strip()
                if default_slurm_partition.strip():
                    payload["default_slurm_partition"] = default_slurm_partition.strip()
                if default_slurm_gpu_account.strip():
                    payload["default_slurm_gpu_account"] = default_slurm_gpu_account.strip()
                if default_slurm_gpu_partition.strip():
                    payload["default_slurm_gpu_partition"] = default_slurm_gpu_partition.strip()
                if remote_base_path.strip():
                    payload["remote_base_path"] = remote_base_path.strip()

                try:
                    resp = make_authenticated_request(
                        "POST",
                        f"{LAUNCHPAD_URL}/ssh-profiles",
                        json=payload,
                        headers=_launchpad_headers(),
                        timeout=10,
                    )
                    if resp.status_code in (200, 201):
                        st.success(f"Profile '{nickname}' created!")
                        st.rerun()
                    else:
                        st.error(f"Create failed ({resp.status_code}): {resp.text[:300]}")
                except Exception as e:
                    st.error(f"Error creating profile: {e}")

st.divider()

# ── Profile List ─────────────────────────────────────────────────────

profiles = _load_profiles()

if not profiles:
    st.info("No SSH profiles yet. Create one above to get started.")
    st.stop()

# Display profiles as a table
rows = []
for p in profiles:
    rows.append({
        "Nickname": p.get("nickname", "—"),
        "Host": p.get("ssh_host", p.get("host", "—")),
        "Username": p.get("ssh_username", p.get("username", "—")),
        "Port": p.get("ssh_port", p.get("port", 22)),
        "Auth Method": p.get("auth_method", "—"),
        "Enabled": "✅" if p.get("is_enabled", True) else "❌",
        "Created": _fmt_datetime(p.get("created_at", "")),
    })

st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── Per-profile actions ──────────────────────────────────────────────

for idx, profile in enumerate(profiles):
    pid = profile.get("id", "")
    nick = profile.get("nickname", f"Profile {idx}")

    with st.container():
        st.markdown(f"---\n**{nick}** (`{profile.get('ssh_host', profile.get('host', ''))}`)")

        act_col1, act_col2, act_col3 = st.columns(3)

        # ── Test Connection ──────────────────────────────────────────
        with act_col1:
            needs_password = bool(profile.get("local_username"))
            pwd_key = f"_local_auth_pwd_{idx}"
            session_info = _load_auth_session(pid) if needs_password else {"active": False}
            if needs_password:
                local_auth_pwd = st.text_input(
                    "Local account password", type="password", key=pwd_key,
                    help=f"Password used once to start a session broker as local Unix user '{profile.get('local_username')}'; not stored",
                )
            else:
                local_auth_pwd = ""

            if needs_password:
                if session_info.get("active"):
                    st.success(f"Unlocked until {_fmt_datetime(session_info.get('expires_at', ''))}")
                unlock_col, lock_col = st.columns(2)
                with unlock_col:
                    if st.button("🔓 Unlock Session", key=f"unlock_{idx}"):
                        if not local_auth_pwd:
                            st.warning("Enter the local account password to start a session")
                        else:
                            with st.spinner("Starting local auth session…"):
                                try:
                                    resp = make_authenticated_request(
                                        "POST",
                                        f"{LAUNCHPAD_URL}/ssh-profiles/{pid}/auth-session",
                                        params={"user_id": user_id},
                                        json={"local_password": local_auth_pwd},
                                        headers=_launchpad_headers(),
                                        timeout=30,
                                    )
                                    if resp.status_code == 200:
                                        result = resp.json()
                                        st.success(f"Session unlocked until {_fmt_datetime(result.get('expires_at', ''))}")
                                        st.rerun()
                                    else:
                                        st.error(f"Unlock failed ({resp.status_code}): {_response_error_text(resp)}")
                                except Exception as e:
                                    st.error(f"Unlock error: {e}")
                with lock_col:
                    if st.button("🔒 End Session", key=f"lock_{idx}"):
                        try:
                            resp = make_authenticated_request(
                                "DELETE",
                                f"{LAUNCHPAD_URL}/ssh-profiles/{pid}/auth-session",
                                params={"user_id": user_id},
                                headers=_launchpad_headers(),
                                timeout=10,
                            )
                            if resp.status_code == 200:
                                st.info("Local auth session closed")
                                st.rerun()
                            else:
                                st.error(f"Close failed ({resp.status_code}): {_response_error_text(resp)}")
                        except Exception as e:
                            st.error(f"Error closing session: {e}")

            if st.button("🔌 Test Connection", key=f"test_{idx}"):
                if needs_password and not (local_auth_pwd or session_info.get("active")):
                    st.warning("Unlock the local auth session first, or enter the password for a one-off test")
                else:
                    with st.spinner("Testing connection…"):
                        try:
                            resp = make_authenticated_request(
                                "POST",
                                f"{LAUNCHPAD_URL}/ssh-profiles/{pid}/test",
                                params={"user_id": user_id},
                                json={"local_password": local_auth_pwd},
                                headers=_launchpad_headers(),
                                timeout=30,
                            )
                            if resp.status_code == 200:
                                result = resp.json()
                                if result.get("success", result.get("ok", False)):
                                    msg = result.get("message", "")
                                    if result.get("session_started") and result.get("session_expires_at"):
                                        msg = f"{msg} Session unlocked until {_fmt_datetime(result['session_expires_at'])}".strip()
                                    st.success(f"✅ Connection successful! {msg}")
                                else:
                                    st.warning(f"⚠️ Test returned: {result.get('message', result)}")
                            else:
                                st.error(f"Test failed ({resp.status_code}): {_response_error_text(resp)}")
                        except Exception as e:
                            st.error(f"Connection test error: {e}")

        # ── Delete ───────────────────────────────────────────────────
        with act_col3:
            confirm_key = f"_confirm_delete_{idx}"
            if st.session_state.get(confirm_key):
                st.warning(f"Delete '{nick}'?")
                y_col, n_col = st.columns(2)
                with y_col:
                    if st.button("✅ Yes", key=f"del_yes_{idx}"):
                        try:
                            resp = make_authenticated_request(
                                "DELETE",
                                f"{LAUNCHPAD_URL}/ssh-profiles/{pid}",
                                params={"user_id": user_id},
                                headers=_launchpad_headers(),
                                timeout=10,
                            )
                            if resp.status_code in (200, 204):
                                st.success(f"Deleted '{nick}'")
                                st.session_state.pop(confirm_key, None)
                                st.rerun()
                            else:
                                st.error(f"Delete failed ({resp.status_code}): {resp.text[:300]}")
                        except Exception as e:
                            st.error(f"Error deleting profile: {e}")
                with n_col:
                    if st.button("❌ Cancel", key=f"del_no_{idx}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
            else:
                if st.button("🗑️ Delete", key=f"delete_{idx}"):
                    st.session_state[confirm_key] = True
                    st.rerun()

        # ── Edit ─────────────────────────────────────────────────────
        with act_col2:
            edit_expanded = st.session_state.get(f"_edit_open_{idx}", False)
            if st.button("✏️ Edit", key=f"edit_btn_{idx}"):
                st.session_state[f"_edit_open_{idx}"] = not edit_expanded
                st.rerun()

        if st.session_state.get(f"_edit_open_{idx}", False):
            with st.expander(f"Edit '{nick}'", expanded=True):
                with st.form(f"edit_form_{idx}"):
                    new_nickname = st.text_input(
                        "Nickname", value=profile.get("nickname", ""), key=f"en_{idx}"
                    )
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        new_host = st.text_input(
                            "SSH Host",
                            value=profile.get("ssh_host", profile.get("host", "")),
                            key=f"eh_{idx}",
                        )
                        new_username = st.text_input(
                            "SSH Username",
                            value=profile.get("ssh_username", profile.get("username", "")),
                            key=f"eu_{idx}",
                        )
                    with ec2:
                        new_port = st.number_input(
                            "SSH Port",
                            min_value=1,
                            max_value=65535,
                            value=profile.get("ssh_port", profile.get("port", 22)),
                            key=f"ep_{idx}",
                        )
                        new_auth = st.selectbox(
                            "Auth Method",
                            ["key_file", "ssh_agent"],
                            index=0 if profile.get("auth_method") == "key_file" else 1,
                            key=f"ea_{idx}",
                        )

                    new_key_path = ""
                    if new_auth == "key_file":
                        new_key_path = st.text_input(
                            "Key File Path",
                            value=profile.get("key_file_path", "") or "",
                            key=f"ek_{idx}",
                        )

                    new_local_user = st.text_input(
                        "Local OS Username (optional)",
                        value=profile.get("local_username", ""),
                        key=f"elu_{idx}",
                        help="Local Unix account used to unlock a per-session SSH broker with that user's password.",
                    )

                    st.markdown("**Remote Execution Defaults**")
                    ed1, ed2 = st.columns(2)
                    with ed1:
                        new_default_slurm_account = st.text_input(
                            "Default CPU Account",
                            value=profile.get("default_slurm_account", ""),
                            key=f"edcpuacct_{idx}",
                        )
                        new_default_slurm_gpu_account = st.text_input(
                            "Default GPU Account",
                            value=profile.get("default_slurm_gpu_account", ""),
                            key=f"edgpuacct_{idx}",
                        )
                        new_remote_base_path = st.text_input(
                            "Remote Base Path",
                            value=profile.get("remote_base_path", ""),
                            key=f"edrbase_{idx}",
                        )
                    with ed2:
                        new_default_slurm_partition = st.text_input(
                            "Default CPU Partition",
                            value=profile.get("default_slurm_partition", ""),
                            key=f"edcpupart_{idx}",
                        )
                        new_default_slurm_gpu_partition = st.text_input(
                            "Default GPU Partition",
                            value=profile.get("default_slurm_gpu_partition", ""),
                            key=f"edgpupart_{idx}",
                        )

                    new_enabled = st.checkbox(
                        "Enabled",
                        value=profile.get("is_enabled", True),
                        key=f"ee_{idx}",
                    )

                    if st.form_submit_button("💾 Save Changes"):
                        update_payload = {
                            "nickname": new_nickname.strip(),
                            "ssh_host": new_host.strip(),
                            "ssh_port": new_port,
                            "ssh_username": new_username.strip(),
                            "auth_method": new_auth,
                            "default_slurm_account": new_default_slurm_account.strip() or None,
                            "default_slurm_partition": new_default_slurm_partition.strip() or None,
                            "default_slurm_gpu_account": new_default_slurm_gpu_account.strip() or None,
                            "default_slurm_gpu_partition": new_default_slurm_gpu_partition.strip() or None,
                            "remote_base_path": new_remote_base_path.strip() or None,
                            "is_enabled": new_enabled,
                        }
                        if new_auth == "key_file" and new_key_path.strip():
                            update_payload["key_file_path"] = new_key_path.strip()
                        if new_local_user.strip():
                            update_payload["local_username"] = new_local_user.strip()

                        try:
                            resp = make_authenticated_request(
                                "PUT",
                                f"{LAUNCHPAD_URL}/ssh-profiles/{pid}",
                                params={"user_id": user_id},
                                json=update_payload,
                                headers=_launchpad_headers(),
                                timeout=10,
                            )
                            if resp.status_code == 200:
                                st.success(f"Profile '{new_nickname}' updated!")
                                st.session_state.pop(f"_edit_open_{idx}", None)
                                st.rerun()
                            else:
                                st.error(f"Update failed ({resp.status_code}): {resp.text[:300]}")
                        except Exception as e:
                            st.error(f"Error updating profile: {e}")

# Footer
st.divider()
st.caption(f"Connected to Launchpad API: {LAUNCHPAD_URL}")
