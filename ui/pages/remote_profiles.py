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
                value="~/.ssh/id_rsa",
                help="Path to the private key file on the server",
            )

        local_username = st.text_input(
            "Local OS Username (optional)",
            help="If the key file is owned by a different OS user, enter their username for sudo access",
        )

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
            pwd_key = f"_sudo_pwd_{idx}"
            if needs_password:
                sudo_pwd = st.text_input(
                    "Local password", type="password", key=pwd_key,
                    help=f"Password for OS user '{profile.get('local_username')}' (not stored)",
                )
            else:
                sudo_pwd = ""

            if st.button("🔌 Test Connection", key=f"test_{idx}"):
                if needs_password and not sudo_pwd:
                    st.warning("Enter your local OS password to test (required for sudo key access)")
                else:
                    with st.spinner("Testing connection…"):
                        try:
                            resp = make_authenticated_request(
                                "POST",
                                f"{LAUNCHPAD_URL}/ssh-profiles/{pid}/test",
                                params={"user_id": user_id},
                                json={"local_password": sudo_pwd},
                                headers=_launchpad_headers(),
                                timeout=30,
                            )
                            if resp.status_code == 200:
                                result = resp.json()
                                if result.get("success", result.get("ok", False)):
                                    st.success(f"✅ Connection successful! {result.get('message', '')}")
                                else:
                                    st.warning(f"⚠️ Test returned: {result.get('message', result)}")
                            else:
                                st.error(f"Test failed ({resp.status_code}): {resp.text[:300]}")
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
                            value=profile.get("key_file_path", "~/.ssh/id_rsa"),
                            key=f"ek_{idx}",
                        )

                    new_local_user = st.text_input(
                        "Local OS Username (optional)",
                        value=profile.get("local_username", ""),
                        key=f"elu_{idx}",
                        help="OS user who owns the key file (for sudo access)",
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
