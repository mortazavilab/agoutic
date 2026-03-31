import time
import threading
import uuid
import requests
import datetime
import json
import os
from datetime import timedelta
from pathlib import Path as _Path
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from auth import require_auth, logout_button, make_authenticated_request, get_session_cookie
from theme import inject_global_css, get_plotly_template
from components.cards import section_header, status_chip, metadata_row, stat_tile, info_callout
from components.progress import stepper, segmented_progress, timeline, progress_stats
from components.forms import review_panel, grouped_section

# --- VERSION (single source of truth: VERSION file at repo root) ---
_VERSION_FILE = _Path(__file__).resolve().parent.parent / "VERSION"
_version_raw = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"
AGOUTIC_VERSION = _version_raw[1:] if _version_raw.lower().startswith("v") else _version_raw

# --- CONFIG ---
# Use environment variable or default to localhost
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")
LAUNCHPAD_URL = os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")
LIVE_JOB_STATUS_TIMEOUT_SECONDS = float(os.getenv("LIVE_JOB_STATUS_TIMEOUT_SECONDS", "60"))

st.set_page_config(page_title=f"AGOUTIC v{AGOUTIC_VERSION}", layout="wide")
inject_global_css()
PLOTLY_TEMPLATE = get_plotly_template()

TASK_SECTION_ORDER = [
    ("pending", "📝 Pending"),
    ("running", "🏃 Running"),
    ("follow_up", "🔎 Follow-up"),
    ("completed", "✅ Completed"),
]
TASK_DOCK_HEIGHT_PX = 320

st.markdown(
    """
    <style>
    .st-key-task_dock [data-testid="stVerticalBlockBorderWrapper"] {
        background: color-mix(in srgb, var(--background-color) 90%, var(--secondary-background-color) 10%);
        border-radius: 0.9rem;
        box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16);
        overflow: hidden;
    }

    .st-key-task_dock [data-testid="stMetric"] {
        background: color-mix(in srgb, var(--secondary-background-color) 82%, transparent);
        border-radius: 0.65rem;
        padding: 0.35rem 0.5rem;
    }

    .st-key-task_dock [data-testid="stExpander"] {
        border-radius: 0.65rem;
        overflow: hidden;
    }

    .st-key-task_dock [data-testid="stVerticalBlock"] > div {
        gap: 0.6rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- AUTHENTICATION ---
# Require authentication before showing any UI
user = require_auth(API_URL)

# --- USERNAME ONBOARDING ---
# If the user has no username, show a one-time picker
if not user.get("username"):
    st.title("🧬 Welcome to AGOUTIC!")
    st.markdown("Before we get started, please choose a **username**. "
                "This will be used to organize your files on disk.")
    st.markdown("Requirements: lowercase letters, numbers, hyphens, underscores. "
                "2–31 characters. Must start with a letter or number.")

    chosen = st.text_input("Choose a username", key="_onboarding_username",
                           max_chars=31, placeholder="e.g. jsmith")
    if chosen:
        import re as _re
        if not _re.match(r'^[a-z0-9][a-z0-9_-]{0,30}$', chosen):
            st.error("Invalid username. Use lowercase letters, numbers, hyphens, or underscores.")
        else:
            # Check availability
            try:
                check = make_authenticated_request(
                    "GET", f"{API_URL}/auth/check-username/{chosen}", timeout=5)
                if check.status_code == 200 and check.json().get("available"):
                    if st.button("Confirm username", key="_confirm_username"):
                        resp = make_authenticated_request(
                            "POST", f"{API_URL}/auth/set-username",
                            json={"username": chosen}, timeout=5)
                        if resp.status_code == 200:
                            st.success(f"Username set to **{chosen}**!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(f"Failed: {resp.text}")
                else:
                    st.warning(f"Username **{chosen}** is not available.")
            except Exception as e:
                st.error(f"Error checking username: {e}")
    st.stop()  # Don't render the rest of the UI until username is set

# --- 1. STATE MANAGEMENT ---
def _slugify_project_name(text: str) -> str:
    """Convert arbitrary text to a slug-friendly project name."""
    import re as _re
    text = text.lower().strip()
    text = _re.sub(r'[^a-z0-9]+', '-', text)   # replace non-alphanumeric runs with hyphen
    text = text.strip('-')                       # trim leading/trailing hyphens
    return text[:40] or 'project'


def _create_project_server_side(name: str = None) -> str:
    """Create a project via POST /projects and return the server-generated UUID."""
    # Default name is slug-friendly: project-YYYY-MM-DD
    project_name = name or f"project-{datetime.datetime.now().strftime('%Y-%m-%d')}"
    try:
        resp = make_authenticated_request(
            "POST",
            f"{API_URL}/projects",
            json={"name": project_name},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["id"]
    except Exception:
        pass
    # Fallback: if server is unreachable, generate a local UUID.
    # The /chat endpoint will auto-register it on first message.
    import uuid as _uuid
    return str(_uuid.uuid4())


def _launchpad_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if INTERNAL_API_SECRET:
        headers["X-Internal-Secret"] = INTERNAL_API_SECRET
    return headers


def _job_status_updated_at(persisted_timestamp: str | None, live_poll_succeeded: bool, now: datetime.datetime | None = None) -> str | None:
    if live_poll_succeeded:
        if persisted_timestamp:
            return persisted_timestamp
        current = now or datetime.datetime.now(datetime.timezone.utc)
        return current.isoformat().replace("+00:00", "Z")
    return persisted_timestamp or None


def _pause_auto_refresh(reruns: int = 4) -> None:
    """Pause auto-refresh for a short time-based window."""
    try:
        desired = max(float(reruns), 0.5)
    except Exception:
        desired = 1.0
    current = st.session_state.get("_suppress_auto_refresh_until", 0.0)
    try:
        current_float = float(current)
    except Exception:
        current_float = 0.0
    st.session_state["_suppress_auto_refresh_until"] = max(current_float, time.time() + desired)


def _has_pending_destructive_confirmation() -> bool:
    """Return True when a destructive-action confirmation is currently open."""
    if st.session_state.get("_confirm_archive_project_id"):
        return True
    if st.session_state.get("_confirm_bulk_delete"):
        return True
    for key, value in st.session_state.items():
        if isinstance(key, str) and key.startswith("del_confirm_") and value:
            return True
    return False


def _auto_refresh_is_suppressed(now: float | None = None) -> bool:
    """Return True while the time-based suppression window is active."""
    if _has_pending_destructive_confirmation():
        return True
    current = now if now is not None else time.time()
    until = st.session_state.get("_suppress_auto_refresh_until", 0.0)
    try:
        until_float = float(until)
    except Exception:
        until_float = 0.0
    return until_float > current


def _load_user_ssh_profiles(user_id: str) -> list[dict]:
    if not user_id:
        return []
    try:
        resp = make_authenticated_request(
            "GET",
            f"{LAUNCHPAD_URL}/ssh-profiles",
            params={"user_id": user_id},
            headers=_launchpad_headers(),
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            profiles = data.get("profiles")
            return profiles if isinstance(profiles, list) else []
    except Exception:
        return []
    return []


def _active_project_slug() -> str:
    active_id = st.session_state.get("active_project_id", "")
    for project in st.session_state.get("_cached_projects", []):
        if project.get("id") == active_id:
            return _slugify_project_name(project.get("name") or active_id)
    return _slugify_project_name(active_id)


def _render_profile_path_template(template: str | None, context: dict[str, str]) -> str | None:
    if not template:
        return None
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace(f"{{{key}}}", value)
        rendered = rendered.replace(f"<{key}>", value)
    return rendered


def _is_help_intent(message: str) -> bool:
    q = (message or "").strip().lower()
    if not q:
        return False
    deterministic = {
        "help",
        "what can you do",
        "show commands",
        "how do i run a workflow",
        "how do i use remote slurm",
    }
    return q in deterministic


def _render_local_help_response() -> None:
    section_header("Help", "Quick operational guide for AGOUTIC", icon="❓")
    with st.container(border=True):
        metadata_row({"Focus": "Workflow + Remote HPC", "Mode": "Deterministic Help"})
        st.divider()
        with st.expander("Getting Started", expanded=True):
            st.markdown("1. Pick or create a project in the sidebar.")
            st.markdown("2. Ask for a workflow run using natural language.")
            st.markdown("3. Review approval parameters and approve to execute.")
        with st.expander("Common Actions", expanded=False):
            st.markdown("- run dna workflow for sample X from /path")
            st.markdown("- stage sample to remote slurm profile hpc3")
            st.markdown("- show job status and next steps")
            st.markdown("- parse results for run UUID")
        with st.expander("Execution Modes", expanded=False):
            st.markdown("- Local: run on AGOUTIC host")
            st.markdown("- SLURM: submit via remote profile and queue")
        with st.expander("Status Guide", expanded=False):
            st.markdown("- pending: waiting for action")
            st.markdown("- running: task active")
            st.markdown("- failed: inspect logs + retry")
            st.markdown("- complete: outputs available in results")
        with st.expander("Troubleshooting", expanded=False):
            st.markdown("- Verify input path and sample name")
            st.markdown("- Confirm reference genome and execution mode")
            st.markdown("- For remote mode, verify SSH profile and SLURM fields")
        with st.expander("Power User Tips", expanded=False):
            st.markdown("- Use precise prompts including sample, mode, and destination")
            st.markdown("- Review approval edits carefully before submission")
            st.markdown("- Use Results page tabs for fast parse/preview")

# Check if we're creating a new project (flag set by New Project button)
if st.session_state.get("_create_new_project", False):
    # Create project via server-side endpoint (server generates UUID)
    _pending_name = st.session_state["_create_new_project"]
    _pending_name = _pending_name if isinstance(_pending_name, str) else None
    new_id = _create_project_server_side(name=_pending_name)
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
    _pause_auto_refresh(3)

# --- 2. SIDEBAR ---
with st.sidebar:
    st.title("🧬 AGOUTIC")
    st.caption(f"v{AGOUTIC_VERSION}")
    
    # User info
    st.caption(f"👤 {user.get('display_name', user.get('email'))}")
    if user.get('role') == 'admin':
        st.caption("🔑 Admin")

    with st.expander("❓ Help", expanded=False):
        st.caption("Quick command suggestions")
        if st.button("help", key="help_prompt_help", width="stretch"):
            st.session_state["_help_prompt"] = "help"
            st.rerun()
        if st.button("what can you do", key="help_prompt_capabilities", width="stretch"):
            st.session_state["_help_prompt"] = "what can you do"
            st.rerun()
        if st.button("how do I run a workflow", key="help_prompt_workflow", width="stretch"):
            st.session_state["_help_prompt"] = "how do i run a workflow"
            st.rerun()
        if st.button("how do I use remote slurm", key="help_prompt_slurm", width="stretch"):
            st.session_state["_help_prompt"] = "how do i use remote slurm"
            st.rerun()
    
    logout_button(API_URL)
    
    st.divider()
    # [A] NEW PROJECT
    with st.expander("✨ New Project", expanded=False):
        _default_slug = f"project-{datetime.datetime.now().strftime('%Y-%m-%d')}"
        _new_name = st.text_input(
            "Project name",
            value=_default_slug,
            key="_new_project_name_input",
            max_chars=40,
            help="Lowercase letters, numbers, hyphens. Will be auto-slugified.",
        )
        if st.button("Create", key="_create_project_btn", width="stretch"):
            _slug = _slugify_project_name(_new_name or _default_slug)
            st.session_state["_create_new_project"] = _slug
            st.rerun()

    # [B] PROJECT ID INPUT
    # Initialize the widget key if not set
    if "_project_id_input" not in st.session_state:
        st.session_state["_project_id_input"] = st.session_state.active_project_id

    def _on_project_id_change():
        """Fires ONLY when the user explicitly edits the text field.

        Programmatic updates via st.session_state["_project_id_input"] = X
        (e.g. from the sidebar button handler) do NOT trigger this callback,
        which eliminates the stale-widget-value revert bug entirely.
        """
        new_val = st.session_state.get("_project_id_input", "")
        if new_val and new_val != st.session_state.get("active_project_id"):
            st.session_state.active_project_id = new_val
            st.session_state.blocks = []
            st.session_state._last_rendered_project = new_val
            st.session_state.pop("_welcome_sent_for", None)

    st.text_input(
        "Project ID",
        key="_project_id_input",
        on_change=_on_project_id_change,
    )

    st.divider()
    
    # [C] CHAT CONTROLS
    col_clear, col_refresh = st.columns(2)
    with col_clear:
        if st.button("🗑️ Clear Chat", width="stretch"):
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
        if st.button("🔄 Refresh", width="stretch"):
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
            # Cache for title display
            st.session_state["_cached_projects"] = user_projects
            if user_projects:
                st.caption(f"{len(user_projects)} project(s)")

                # Search/filter when many projects
                if len(user_projects) > 4:
                    _proj_filter = st.text_input(
                        "🔍 Filter", key="_proj_filter", placeholder="Search projects…",
                        label_visibility="collapsed",
                    )
                else:
                    _proj_filter = ""

                filtered = user_projects
                if _proj_filter:
                    _pf = _proj_filter.lower()
                    filtered = [p for p in user_projects if _pf in p.get("name", "").lower()]

                for proj in filtered:
                    proj_id = proj.get("id", "")
                    proj_name = proj.get("name", proj_id)[:30]
                    is_current = proj_id == st.session_state.active_project_id
                    archive_confirm_id = st.session_state.get("_confirm_archive_project_id")

                    # Build label with job count if available
                    job_count = proj.get("job_count")
                    label_extra = f" · {job_count} job{'s' if job_count != 1 else ''}" if job_count else ""

                    if is_current:
                        st.info(f"📌 **{proj_name}**{label_extra}")
                    else:
                        col_name, col_archive = st.columns([5, 1])
                        with col_name:
                            if archive_confirm_id == proj_id:
                                st.warning(f"Archive {proj_name}?")
                            elif st.button(f"📂 {proj_name}{label_extra}", key=f"proj_{proj_id}", width="stretch"):
                                # Switch to this project
                                st.session_state.active_project_id = proj_id
                                st.session_state.blocks = []
                                st.session_state._last_rendered_project = proj_id
                                st.session_state["_project_id_input"] = proj_id
                                st.session_state.pop("_welcome_sent_for", None)
                                # Update server-side last project so session
                                # recovery lands on the correct project.
                                try:
                                    make_authenticated_request(
                                        "PUT", f"{API_URL}/user/last-project",
                                        json={"project_id": proj_id}, timeout=3,
                                    )
                                except Exception:
                                    pass
                                st.rerun()
                        with col_archive:
                            if archive_confirm_id == proj_id:
                                if st.button("✅", key=f"arch_yes_{proj_id}", help=f"Confirm archive '{proj_name}'"):
                                    _pause_auto_refresh(4)
                                    try:
                                        del_resp = make_authenticated_request(
                                            "DELETE",
                                            f"{API_URL}/projects/{proj_id}",
                                            timeout=5,
                                        )
                                        if del_resp.status_code == 200:
                                            st.session_state.pop("_confirm_archive_project_id", None)
                                            st.toast(f"Archived: {proj_name}")
                                            st.rerun()
                                        else:
                                            st.error(f"Archive failed: {del_resp.status_code}")
                                    except Exception as e:
                                        st.error(f"Error: {e}")
                                if st.button("✖", key=f"arch_no_{proj_id}", help="Cancel archive"):
                                    st.session_state.pop("_confirm_archive_project_id", None)
                                    st.rerun()
                            elif st.button("🗑", key=f"arch_{proj_id}", help=f"Archive '{proj_name}'"):
                                st.session_state["_confirm_archive_project_id"] = proj_id
                                _pause_auto_refresh(4)
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
                    if st.button(f"📝 {conv_title}...", key=f"conv_{conv['id']}", width="stretch"):
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
                    if st.button(f"{status_emoji} {job_name}", key=f"job_{job['id']}", width="stretch"):
                        st.session_state.selected_job = job
                        st.rerun()
            else:
                st.caption("No jobs yet")
    except Exception:
        pass  # Silently fail if history not available
    
    st.divider()

    # [E] TOKEN USAGE SUMMARY FOR THIS USER
    # Fetch OUTSIDE the expander so the data refreshes on every rerun,
    # regardless of whether the expander is open or closed.
    _tok_data = None
    try:
        _tok_resp = make_authenticated_request("GET", f"{API_URL}/user/token-usage", timeout=5)
        if _tok_resp.status_code == 200:
            _tok_data = _tok_resp.json()
    except Exception:
        pass

    _lt = (_tok_data or {}).get("lifetime", {})
    _tok_total = _lt.get("total_tokens", 0)
    _tok_limit = (_tok_data or {}).get("token_limit")
    if _tok_limit:
        _pct = min(100, round(_tok_total / _tok_limit * 100))
        _expander_label = f"🪙 {_tok_total:,} / {_tok_limit:,} tokens ({_pct}%)"
    else:
        _expander_label = f"🪙 Tokens: {_tok_total:,}" if _tok_total else "🪙 Token Usage"

    with st.expander(_expander_label, expanded=False):
        if _tok_data and _tok_total:
            if _tok_limit:
                _pct_val = min(100, round(_tok_total / _tok_limit * 100))
                st.progress(_pct_val / 100, text=f"{_tok_total:,} / {_tok_limit:,} ({_pct_val}%)")
                if _pct_val >= 90:
                    st.warning("⚠️ Approaching token limit — contact an admin.")
            else:
                tcol1, tcol2 = st.columns(2)
                tcol1.metric("Total", f"{_tok_total:,}")
                tcol2.metric("Completion", f"{_lt.get('completion_tokens', 0):,}")
            _daily = _tok_data.get("daily", [])
            if len(_daily) > 1:
                import pandas as _pd
                _df_tok = _pd.DataFrame(_daily)
                _df_tok["date"] = _pd.to_datetime(_df_tok["date"])
                _df_tok = _df_tok.set_index("date")
                st.line_chart(_df_tok["total_tokens"], width="stretch")
            _since = _tok_data.get("tracking_since")
            if _since:
                st.caption(f"Tracking since {_since[:10]}")
        else:
            st.caption("No token data yet — starts with next LLM call.")

    st.divider()
    
    model_choice = st.selectbox("Brain Model", ["default", "fast", "smart"], index=0)
    auto_refresh = st.toggle("Live Stream", value=True)
    poll_seconds = st.slider("Poll interval (sec)", 1, 5, 2)
    debug_mode = st.toggle("🐛 Debug", value=False)
    st.session_state["_debug_mode"] = debug_mode
    
    # DEBUG DISPLAY: Show exactly what ID the UI thinks it is using
    st.caption(f"**System ID:** `{st.session_state.active_project_id}`")


# --- 3. LOGIC ---

def get_sanitized_blocks(target_project_id):
    """
    Fetch blocks from server, but SCRUB any block that doesn't match the target ID.
    This guarantees no 'ghost' messages from other projects can appear.

    Returns (blocks_list, fetch_ok) so callers can distinguish an empty project
    from a transient server failure and avoid wiping session-state blocks.
    """
    try:
        # 1. Ask Server with authentication
        #    Fetch up to 500 blocks (was 100 — caused silent truncation in
        #    long conversations) and use a generous timeout.
        resp = make_authenticated_request(
            "GET",
            f"{API_URL}/blocks",
            params={"project_id": target_project_id, "since_seq": 0, "limit": 500},
            timeout=10,
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
            return clean_blocks, True
        else:
            # Non-200 response — treat as transient failure
            import logging
            logging.getLogger("agoutic.ui").warning(
                "Block fetch returned status %s for project %s",
                resp.status_code, target_project_id,
            )
            return [], False
    except Exception as exc:
        import logging
        logging.getLogger("agoutic.ui").warning(
            "Block fetch failed for project %s: %s", target_project_id, exc
        )
        return [], False


def get_project_tasks(project_id: str):
    """Fetch grouped persistent project tasks for the active project."""
    try:
        resp = make_authenticated_request(
            "GET",
            f"{API_URL}/projects/{project_id}/tasks",
            timeout=8,
        )
        if resp.status_code == 200:
            return resp.json().get("sections", {})
    except Exception:
        pass
    return {}


def apply_task_action(project_id: str, task_id: str, action: str) -> bool:
    """Apply a backend task action and report success."""
    try:
        resp = make_authenticated_request(
            "PATCH",
            f"{API_URL}/projects/{project_id}/tasks/{task_id}",
            json={"action": action},
            timeout=8,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _handle_task_action(task: dict):
    target_raw = task.get("action_target") or ""
    try:
        target = json.loads(target_raw) if target_raw else {}
    except (TypeError, ValueError):
        target = {}

    target_type = target.get("type")
    run_uuid = target.get("run_uuid")
    block_id = target.get("block_id")

    if target_type == "results" and run_uuid:
        if task.get("kind") == "result_review":
            apply_task_action(st.session_state.active_project_id, task["id"], "complete")
        st.session_state["selected_job_run_uuid"] = run_uuid
        st.switch_page("pages/results.py")
        return

    if target_type == "block" and block_id:
        st.session_state["_max_visible_blocks"] = max(
            len(st.session_state.get("blocks", [])),
            st.session_state.get("_max_visible_blocks", 30),
        )
        st.session_state["_task_focus_block_id"] = block_id
        st.rerun()


def _render_single_task(task: dict, project_id: str, *, is_child: bool = False):
    meta = task.get("metadata", {})
    extra = []
    if meta.get("sample_name"):
        extra.append(meta["sample_name"])
    if meta.get("progress_percent") not in (None, "") and task.get("status") == "RUNNING":
        extra.append(f"{meta['progress_percent']}%")
    if meta.get("total_files") and task.get("kind") == "download":
        extra.append(f"{meta.get('downloaded', 0)}/{meta['total_files']} files")

    title = task.get("title", "Task")
    if is_child:
        title = f"↳ {title}"

    left, action_col, extra_col = st.columns([5, 1, 1])
    with left:
        st.write(f"**{title}**")
        if extra:
            st.caption(" · ".join(str(part) for part in extra if part not in (None, "")))
    with action_col:
        action_label = task.get("action_label")
        if action_label and task.get("action_target"):
            if st.button(action_label, key=f"task_action_{task['id']}"):
                _handle_task_action(task)
    with extra_col:
        status = task.get("status")
        if task.get("kind") in {"result_review", "recovery"} and status in {"PENDING", "FOLLOW_UP"}:
            if st.button("Done", key=f"task_done_{task['id']}"):
                if apply_task_action(project_id, task["id"], "complete"):
                    st.rerun()
        elif status == "COMPLETED" and task.get("kind") in {"result_review", "recovery"}:
            if st.button("Reopen", key=f"task_reopen_{task['id']}"):
                if apply_task_action(project_id, task["id"], "reopen"):
                    st.rerun()
        elif status in {"COMPLETED", "FOLLOW_UP", "FAILED"}:
            if st.button("Hide", key=f"task_hide_{task['id']}"):
                if apply_task_action(project_id, task["id"], "archive"):
                    st.rerun()


def _count_project_tasks(sections: dict) -> int:
    return sum(len(sections.get(name, [])) for name, _ in TASK_SECTION_ORDER)


def render_project_tasks(project_id: str, *, sections: dict | None = None, docked: bool = False) -> int:
    """Render grouped project tasks and return the number of visible tasks."""
    sections = sections if sections is not None else get_project_tasks(project_id)
    total_tasks = _count_project_tasks(sections)
    if total_tasks == 0:
        return 0

    all_tasks = []
    for name, _label in TASK_SECTION_ORDER:
        all_tasks.extend(sections.get(name, []))
    child_ids = {task["id"] for task in all_tasks for task in task.get("children", [])}

    st.subheader("Task List")
    summary_cols = st.columns(4)
    for idx, (name, label) in enumerate(TASK_SECTION_ORDER):
        summary_cols[idx].metric(label, len(sections.get(name, [])))

    for name, label in TASK_SECTION_ORDER:
        tasks = sections.get(name, [])
        if not tasks:
            continue
        expanded = name != "completed"
        with st.expander(f"{label} ({len(tasks)})", expanded=expanded):
            for task in tasks:
                if task["id"] in child_ids:
                    continue
                _render_single_task(task, project_id)
                for child in task.get("children", []):
                    _render_single_task(child, project_id, is_child=True)

    if not docked:
        st.write("---")
    return total_tasks

def get_job_debug_info(run_uuid):
    """Fetch detailed debug information for a failed job via Cortex proxy."""
    try:
        # Use Cortex proxy instead of calling Launchpad directly
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


def _render_md_with_dataframes(md: str, block_id: str, section: str):
    """Render a markdown string, replacing any pipe tables with st.dataframe.

    The function splits the markdown on table blocks (contiguous lines that
    start with '|') and renders non-table text via st.markdown while each
    table block becomes an interactive dataframe.
    """
    import re as _re

    lines = md.splitlines(keepends=True)
    buf_text: list[str] = []
    buf_table: list[str] = []
    table_index = [0]

    def flush_text():
        chunk = "".join(buf_text).strip()
        if chunk:
            st.markdown(chunk)
        buf_text.clear()

    def flush_table():
        raw = "".join(buf_table)
        rows = [l for l in buf_table if l.strip() and not _re.match(r"^\s*\|[-| :]+\|\s*$", l)]
        if not rows:
            buf_table.clear()
            return
        try:
            import io
            import pandas as _pd
            # Parse header and data rows
            all_rows = [l.strip() for l in buf_table if l.strip()]
            # Header is first row, separator is second, rest are data
            header_row = all_rows[0]
            data_rows = [r for r in all_rows[2:] if r.startswith("|")]
            parse_row = lambda r: [c.strip() for c in r.strip("|").split("|")]
            headers = parse_row(header_row)
            records = [parse_row(r) for r in data_rows]
            # Pad short rows
            records = [r + [""] * (len(headers) - len(r)) for r in records]
            df = _pd.DataFrame(records, columns=headers)
            idx = table_index[0]
            table_index[0] += 1
            st.dataframe(
                df,
                width="stretch",
                hide_index=True,
                height=min(400, 35 * len(df) + 38),
                key=f"_mdtbl_{block_id}_{section}_{idx}",
            )
        except Exception:
            # Fallback to plain markdown if parsing fails
            st.markdown(raw)
        buf_table.clear()

    in_code_block = False
    for line in lines:
        stripped = line.strip()
        # Track fenced code blocks — don't parse tables inside them
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            if buf_table:
                flush_table()
            buf_text.append(line)
            continue

        if in_code_block:
            buf_text.append(line)
            continue

        is_table_line = stripped.startswith("|")
        if is_table_line:
            if buf_text:
                flush_text()
            buf_table.append(line)
        else:
            if buf_table:
                flush_table()
            buf_text.append(line)

    if buf_table:
        flush_table()
    if buf_text:
        flush_text()


def _render_embedded_dataframes(dfs: dict, block_id: str, *, only_visible: bool = True):
    """Render embedded dataframes (search results, CSV/BED files) from a block's _dataframes dict.

    If ``only_visible=True`` (default), only renders dataframes that are
    visible (have a ``df_id``).  Call with ``only_visible=False`` to render
    the non-visible (supplementary) dataframes inside the details section.
    """
    import re as _re
    for _df_idx, (_fname, _fdata) in enumerate(dfs.items()):
        _meta = _fdata.get("metadata", {})
        _df_id = _meta.get("df_id")
        _has_id = _df_id is not None
        # Filter: visible mode shows only DFs with IDs;
        # non-visible mode shows only DFs without IDs.
        if only_visible and not _has_id:
            continue
        if not only_visible and _has_id:
            continue
        _is_visible = _has_id
        _rows = _fdata.get("data", [])
        _cols = _fdata.get("columns", [])
        _total = _fdata.get("row_count", len(_rows))
        _meta = _fdata.get("metadata", {})
        _df = pd.DataFrame(_rows)
        if _df.empty:
            st.info(f"📊 **{_fname}** — empty table")
            continue
        # Sanitize key: strip non-alphanumeric chars to avoid Streamlit widget key issues
        _safe_key = _re.sub(r"[^a-zA-Z0-9_]", "_", f"{block_id}_{_df_idx}")
        _df_id = _meta.get("df_id")
        _df_prefix = f"DF{_df_id}: " if _df_id else ""
        with st.expander(f"📊 {_df_prefix}**{_fname}** — {_total:,} rows × {len(_cols)} columns", expanded=_is_visible):
            # Column statistics popover
            _col_stats = _meta.get("column_stats", {})
            if _col_stats:
                with st.popover("📈 Column statistics"):
                    for _cn, _ci in _col_stats.items():
                        _dt = _ci.get("dtype", "")
                        _nl = _ci.get("nulls", 0)
                        _ln = f"**{_cn}** ({_dt})"
                        if _nl:
                            _ln += f" — {_nl} nulls"
                        if "mean" in _ci and _ci["mean"] is not None:
                            _ln += (
                                f"  \nmin={_ci.get('min')}  max={_ci.get('max')}  "
                                f"mean={_ci.get('mean'):.4g}  median={_ci.get('median'):.4g}"
                            )
                        elif "unique" in _ci:
                            _ln += f"  \n{_ci['unique']} unique values"
                        st.markdown(_ln)
            # Column filter
            if len(_cols) > 4:
                _sel_cols = st.multiselect(
                    "Columns to display",
                    _cols,
                    default=_cols,
                    key=f"dfchat_{_safe_key}",
                )
            else:
                _sel_cols = _cols
            _disp = _df[_sel_cols] if _sel_cols else _df
            st.dataframe(
                _disp,
                width="stretch",
                hide_index=True,
                height=min(400, 35 * len(_disp) + 38),
            )
            if _meta.get("is_truncated"):
                st.caption(f"Showing {len(_rows):,} of {_total:,} rows")
            _csv = _disp.to_csv(index=False).encode("utf-8")
            st.download_button(
                f"⬇️ Download {_fname}",
                data=_csv,
                file_name=_fname,
                mime="text/csv",
                key=f"dldfc_{_safe_key}",
            )


def _resolve_df_by_id(df_id: int, all_blocks: list):
    """Look up an embedded dataframe by its DF ID across all blocks.

    Scans all AGENT_PLAN blocks in ``all_blocks`` for a dataframe whose
    ``metadata.df_id`` matches *df_id*.

    Returns a tuple ``(pd.DataFrame, label_str)`` or ``(None, None)`` if
    not found.
    """
    for blk in reversed(all_blocks):
        if blk.get("type") != "AGENT_PLAN":
            continue
        dfs = blk.get("payload", {}).get("_dataframes", {})
        for fname, fdata in dfs.items():
            meta = fdata.get("metadata", {})
            if meta.get("df_id") == df_id:
                rows = fdata.get("data", [])
                cols = fdata.get("columns") or None
                df = pd.DataFrame(rows, columns=cols)
                return df, fname
    return None, None


def _resolve_payload_df_by_id(df_id: int, dfs: dict):
    """Look up an embedded dataframe by DF ID inside a single payload dict."""
    for fname, fdata in (dfs or {}).items():
        meta = fdata.get("metadata", {}) if isinstance(fdata, dict) else {}
        if meta.get("df_id") == df_id:
            rows = fdata.get("data", []) if isinstance(fdata, dict) else []
            cols = fdata.get("columns") if isinstance(fdata, dict) else None
            return pd.DataFrame(rows, columns=cols or None), fname
    return None, None


def _find_related_workflow_plan(agent_block: dict, all_blocks: list):
    """Return the nearest prior workflow block that matches an agent plan summary."""
    if not isinstance(agent_block, dict):
        return None

    block_id = agent_block.get("id")
    project_id = agent_block.get("project_id")
    markdown = str(agent_block.get("payload", {}).get("markdown") or "")

    import re as _re
    title_match = _re.search(r"^Plan:\s*(.+)$", markdown, _re.MULTILINE)
    target_title = title_match.group(1).strip() if title_match else ""

    block_index = None
    for idx, candidate in enumerate(all_blocks):
        if isinstance(candidate, dict) and candidate.get("id") == block_id:
            block_index = idx
            break
    if block_index is None:
        return None

    fallback = None
    for candidate in reversed(all_blocks[:block_index]):
        if not isinstance(candidate, dict):
            continue
        if candidate.get("type") != "WORKFLOW_PLAN":
            continue
        if project_id and candidate.get("project_id") != project_id:
            continue
        payload = candidate.get("payload", {})
        candidate_title = str(payload.get("title") or payload.get("summary") or "").strip()
        if target_title and candidate_title == target_title:
            return candidate
        if fallback is None:
            fallback = candidate
    return fallback


def _workflow_highlight_steps(workflow_block: dict) -> list[dict]:
    """Return completed workflow steps worth surfacing in the main chat flow."""
    if not isinstance(workflow_block, dict):
        return []
    payload = workflow_block.get("payload", {})
    steps = payload.get("steps", []) if isinstance(payload, dict) else []
    if not isinstance(steps, list):
        return []

    highlight_kinds = {"GENERATE_PLOT", "INTERPRET_RESULTS", "WRITE_SUMMARY", "RECOMMEND_NEXT"}
    highlights: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("kind") not in highlight_kinds:
            continue
        if step.get("status") != "COMPLETED":
            continue
        result = step.get("result")
        if result in (None, "", [], {}):
            continue
        highlights.append(step)
    return highlights


def _block_requires_full_refresh(block: dict) -> bool:
    """Return True when a block needs whole-page reruns, not just fragment refresh."""
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    bstatus = block.get("status")
    if btype == "DOWNLOAD_TASK":
        return bstatus == "RUNNING"
    if btype == "EXECUTION_JOB":
        payload = block.get("payload", {}) if isinstance(block.get("payload"), dict) else {}
        job_status = payload.get("job_status", {}) if isinstance(payload.get("job_status"), dict) else {}
        nested_status = str(job_status.get("status") or "").upper()
        transfer_state = str(job_status.get("transfer_state") or "").strip().lower()
        return (
            bstatus == "RUNNING"
            or nested_status in {"RUNNING", "PENDING"}
            or transfer_state in {"downloading_outputs"}
        )
    if btype == "AGENT_PLAN":
        payload = block.get("payload", {}) if isinstance(block.get("payload"), dict) else {}
        _sru = payload.get("_sync_run_uuid", "")
        if _sru:
            _cached = (st.session_state.get(f"_transfer_state_{_sru}") or "").strip().lower()
            if _cached in {"downloading_outputs"}:
                return True
    return False


def _build_plotly_figure(chart_spec: dict, df: pd.DataFrame, df_label: str):
    """Build a single Plotly figure from a chart spec and dataframe.

    Returns a plotly Figure or None on error.
    """
    import re

    chart_type = chart_spec.get("type", "histogram")
    x_col = chart_spec.get("x")
    y_col = chart_spec.get("y")
    color_col = chart_spec.get("color")
    palette = chart_spec.get("palette")
    title = chart_spec.get("title", "")
    agg = chart_spec.get("agg")
    literal_color = None
    palette_values = []

    def _looks_like_literal_color(value):
        if not value:
            return False
        value = str(value).strip().strip('"').strip("'")
        if not value:
            return False
        if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value):
            return True
        if re.fullmatch(r"(?:rgb|rgba|hsl|hsla)\([^\)]*\)", value, re.IGNORECASE):
            return True
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value))

    def _parse_palette_values(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            candidates = value
        else:
            candidates = re.split(r"[|;]", str(value))
        return [
            str(item).strip().strip('"').strip("'")
            for item in candidates
            if str(item).strip().strip('"').strip("'")
        ]

    palette_values = _parse_palette_values(palette)
    if palette_values and len(palette_values) == 1 and _looks_like_literal_color(palette_values[0]):
        literal_color = palette_values[0]

    px_color_kwargs = {}

    # Validate columns exist
    available = list(df.columns)
    if x_col and x_col not in available:
        # Try case-insensitive match
        match = [c for c in available if c.lower() == x_col.lower()]
        x_col = match[0] if match else None
    if y_col and y_col not in available:
        match = [c for c in available if c.lower() == y_col.lower()]
        y_col = match[0] if match else None
    if color_col and color_col not in available:
        match = [c for c in available if c.lower() == color_col.lower()]
        if match:
            color_col = match[0]
        elif _looks_like_literal_color(color_col):
            literal_color = str(color_col).strip().strip('"').strip("'")
            color_col = None
        else:
            color_col = None

    if color_col:
        px_color_kwargs["color"] = color_col
    if palette_values:
        px_color_kwargs["color_discrete_sequence"] = palette_values

    try:
        # Convert numeric-looking columns to numeric for plotting
        df_plot = df.copy()
        for col in [x_col, y_col]:
            if col and col in df_plot.columns:
                try:
                    df_plot[col] = pd.to_numeric(df_plot[col])
                except (ValueError, TypeError):
                    pass  # leave non-numeric columns as-is

        if chart_type == "histogram":
            if not x_col:
                # Auto-pick first numeric column
                num_cols = df_plot.select_dtypes(include="number").columns
                x_col = num_cols[0] if len(num_cols) > 0 else available[0]
            # If x_col is categorical (non-numeric), px.histogram would count
            # occurrences per category (each row = 1), which is wrong for
            # pre-aggregated tables like Category/Count.
            # Detect: x_col is non-numeric AND there is exactly one numeric
            # column → treat as a bar chart using that numeric column as y.
            x_is_numeric = pd.api.types.is_numeric_dtype(df_plot[x_col])
            if not x_is_numeric:
                num_cols = df_plot.select_dtypes(include="number").columns.tolist()
                if num_cols:
                    # Prefer a column named "count", "value", "n", "total", etc.
                    _count_names = {"count", "counts", "value", "values",
                                    "n", "total", "freq", "frequency", "amount"}
                    _y_col = next(
                        (c for c in num_cols if c.lower() in _count_names),
                        num_cols[0]
                    )
                    fig = px.bar(df_plot, x=x_col, y=_y_col,
                                 **px_color_kwargs,
                                 title=title or f"{_y_col} by {x_col}")
                else:
                    # Truly categorical with no numeric column — count rows
                    counts = df_plot[x_col].value_counts().reset_index()
                    counts.columns = [x_col, "Count"]
                    fig = px.bar(counts, x=x_col, y="Count",
                                 **px_color_kwargs,
                                 title=title or f"Count by {x_col}")
            else:
                fig = px.histogram(df_plot, x=x_col,
                                   **px_color_kwargs,
                                   title=title or f"Distribution of {x_col}")

        elif chart_type == "scatter":
            if not x_col or not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                if len(num_cols) >= 2:
                    x_col = x_col or num_cols[0]
                    y_col = y_col or num_cols[1]
                else:
                    return None
            fig = px.scatter(df_plot, x=x_col, y=y_col,
                             **px_color_kwargs,
                             title=title or f"{x_col} vs {y_col}")

        elif chart_type == "bar":
            if not x_col:
                # Auto-pick first non-numeric column
                cat_cols = df_plot.select_dtypes(exclude="number").columns
                x_col = cat_cols[0] if len(cat_cols) > 0 else available[0]
            # If y_col is absent and x is categorical, prefer a numeric companion
            # column (pre-aggregated table) over counting rows (which gives 1 each).
            x_is_cat = not pd.api.types.is_numeric_dtype(df_plot[x_col])
            if not y_col and x_is_cat and agg != "count":
                num_cols = df_plot.select_dtypes(include="number").columns.tolist()
                if num_cols:
                    _count_names = {"count", "counts", "value", "values",
                                    "n", "total", "freq", "frequency", "amount"}
                    y_col = next(
                        (c for c in num_cols if c.lower() in _count_names),
                        num_cols[0]
                    )
            if agg == "count" or not y_col:
                # Count occurrences of each x value
                counts = df_plot[x_col].value_counts().reset_index()
                counts.columns = [x_col, "Count"]
                fig = px.bar(counts, x=x_col, y="Count",
                             **px_color_kwargs,
                             title=title or f"Count by {x_col}")
            else:
                if agg == "mean":
                    agg_df = df_plot.groupby(x_col, as_index=False)[y_col].mean()
                elif agg == "sum":
                    agg_df = df_plot.groupby(x_col, as_index=False)[y_col].sum()
                else:
                    agg_df = df_plot
                fig = px.bar(agg_df, x=x_col, y=y_col,
                             **px_color_kwargs,
                             title=title or f"{y_col} by {x_col}")

        elif chart_type == "box":
            if not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                y_col = num_cols[0] if len(num_cols) > 0 else None
            if not y_col:
                return None
            fig = px.box(df_plot, x=x_col, y=y_col,
                         **px_color_kwargs,
                         title=title or f"Distribution of {y_col}" + (f" by {x_col}" if x_col else ""))

        elif chart_type == "heatmap":
            num_df = df_plot.select_dtypes(include="number")
            if num_df.shape[1] < 2:
                return None
            corr = num_df.corr()
            fig = px.imshow(corr, text_auto=".2f",
                            title=title or "Correlation Matrix",
                            color_continuous_scale="RdBu_r",
                            zmin=-1, zmax=1)

        elif chart_type == "pie":
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns
                x_col = cat_cols[0] if len(cat_cols) > 0 else available[0]
            if y_col:
                fig = px.pie(df_plot, names=x_col, values=y_col,
                             **({"color": color_col} if color_col else {}),
                             **({"color_discrete_sequence": palette_values} if palette_values else {}),
                             title=title or f"{x_col} Proportions")
            else:
                counts = df_plot[x_col].value_counts().reset_index()
                counts.columns = [x_col, "Count"]
                fig = px.pie(counts, names=x_col, values="Count",
                             **({"color": color_col} if color_col else {}),
                             **({"color_discrete_sequence": palette_values} if palette_values else {}),
                             title=title or f"{x_col} Distribution")

        else:
            return None

        if literal_color:
            if chart_type == "pie":
                for trace in fig.data:
                    labels = getattr(trace, "labels", None)
                    trace.marker.colors = [literal_color] * len(labels or [1])
            else:
                fig.update_traces(marker_color=literal_color)
                for trace in fig.data:
                    if hasattr(trace, "line"):
                        trace.line.color = literal_color
                    if hasattr(trace, "fillcolor"):
                        trace.fillcolor = literal_color

        fig.update_layout(template=PLOTLY_TEMPLATE)
        return fig

    except Exception:
        return None


def _render_plot_block(payload: dict, all_blocks: list, block_id: str):
    """Render an AGENT_PLOT block's charts using Plotly.

    ``payload["charts"]`` is a list of chart specs each with:
      type, df_id, x, y, color, title, agg

    DataFrames are resolved from prior AGENT_PLAN blocks via ``_resolve_df_by_id``.
    """
    import re as _re
    charts = payload.get("charts", [])
    if not charts:
        st.info("No chart specifications found in this plot block.")
        return

    # Check if multiple charts target the same df and type (multi-trace overlay)
    # Group by (df_id, type) for overlay rendering
    from collections import defaultdict
    groups = defaultdict(list)
    for chart in charts:
        key = (chart.get("df_id"), chart.get("type"))
        groups[key].append(chart)

    chart_idx = 0
    for (df_id, chart_type), chart_group in groups.items():
        if df_id is None:
            st.warning("Chart missing DataFrame reference (df=DFN).")
            continue

        df, df_label = _resolve_df_by_id(df_id, all_blocks)
        if df is None or df.empty:
            st.warning(f"DataFrame DF{df_id} not found in conversation history.")
            continue

        if len(chart_group) == 1:
            # Single chart
            fig = _build_plotly_figure(chart_group[0], df, df_label)
            if fig:
                _safe_key = _re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                st.plotly_chart(fig, width="stretch", key=_safe_key)
            else:
                st.warning(f"Could not render {chart_type} chart for DF{df_id}. "
                           "Check that the specified columns exist.")
        else:
            # Multi-trace overlay: build a combined figure
            combined = go.Figure()
            title_parts = []
            for spec in chart_group:
                fig = _build_plotly_figure(spec, df, df_label)
                if fig:
                    for trace in fig.data:
                        combined.add_trace(trace)
                    if spec.get("title"):
                        title_parts.append(spec["title"])
            if combined.data:
                combined.update_layout(
                    template=PLOTLY_TEMPLATE,
                    title=" / ".join(title_parts) if title_parts else f"DF{df_id} — {chart_type}",
                )
                _safe_key = _re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                st.plotly_chart(combined, width="stretch", key=_safe_key)
            else:
                st.warning(f"Could not render multi-trace {chart_type} chart for DF{df_id}.")
        chart_idx += 1


def _render_workflow_plot_payload(payload: dict, block_id: str, step_suffix: str):
    """Render workflow-local plots embedded in a step result payload."""
    import re as _re

    charts = payload.get("charts", []) if isinstance(payload, dict) else []
    dfs = payload.get("_dataframes", {}) if isinstance(payload, dict) else {}
    if not charts or not isinstance(dfs, dict):
        return

    for chart_idx, chart in enumerate(charts):
        if not isinstance(chart, dict):
            continue
        df_id = chart.get("df_id")
        if df_id is None:
            st.warning("Workflow chart is missing a dataframe reference.")
            continue
        df, df_label = _resolve_payload_df_by_id(df_id, dfs)
        if df is None or df.empty:
            st.warning(f"Workflow chart source DF{df_id} is missing.")
            continue
        fig = _build_plotly_figure(chart, df, df_label)
        if fig is None:
            st.warning(f"Could not render workflow chart for DF{df_id}.")
            continue
        _safe_key = _re.sub(r"[^a-zA-Z0-9_]", "_", f"wf_plot_{block_id}_{step_suffix}_{chart_idx}")
        st.plotly_chart(fig, width="stretch", key=_safe_key)


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
    
    # Metadata — resolve project name from cached list (fall back to short UUID)
    b_skill = content.get("skill", "N/A")
    b_model = content.get("model", "N/A")
    b_project_display = b_project[:12] + "…"  # default: truncated UUID
    for _cp in st.session_state.get("_cached_projects", []):
        if _cp.get("id") == b_project:
            b_project_display = _cp.get("name", b_project_display)
            break

    def show_metadata():
        metadata_row({"Project": b_project_display, "Model": b_model, "Skill": b_skill})

    def _block_timestamp() -> str:
        raw_ts = block.get("created_at") or ""
        if not raw_ts:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%b %d, %H:%M")
        except Exception:
            return ""

    def _parse_timestamp(raw_value):
        if not raw_value:
            return None
        try:
            if isinstance(raw_value, str):
                dt = datetime.datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            else:
                dt = raw_value
            # Convert to local time for display
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt
        except Exception:
            return None

    def _format_timestamp(raw_value) -> str:
        parsed = _parse_timestamp(raw_value)
        if parsed is None:
            return ""
        return parsed.strftime("%b %d, %H:%M:%S")

    def _format_duration(start_value, end_value=None) -> str:
        start_dt = _parse_timestamp(start_value)
        if start_dt is None:
            return ""
        end_dt = _parse_timestamp(end_value) or datetime.datetime.now(datetime.timezone.utc)
        try:
            total_seconds = max(int((end_dt - start_dt).total_seconds()), 0)
        except Exception:
            return ""
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _workflow_label_from_path(path_value: str) -> str:
        if not path_value:
            return ""
        try:
            import pathlib as _pathlib
            return _pathlib.PurePosixPath(path_value).name
        except Exception:
            return ""

    def _run_status_label(raw_status: str) -> tuple[str, str, str]:
        normalized = (raw_status or "UNKNOWN").strip().upper()
        mapping = {
            "COMPLETED": ("complete", "Succeeded", "✅"),
            "FAILED": ("failed", "Failed", "❌"),
            "RUNNING": ("running", "Running", "🔄"),
            "PENDING": ("pending", "Pending", "⏳"),
            "CANCELLED": ("warning", "Cancelled", "🛑"),
            "DELETED": ("pending", "Deleted", "🗑️"),
        }
        return mapping.get(normalized, ("pending", normalized.title(), "❓"))

    def _workflow_status_presentation(raw_status: str) -> tuple[str, str, str]:
        normalized = (raw_status or "pending").strip().lower()
        if normalized in {"completed", "complete", "done", "approved"}:
            return "complete", raw_status.replace("_", " ").title(), "✅"
        if normalized in {"failed", "rejected", "cancelled"}:
            return "failed", raw_status.replace("_", " ").title(), "❌"
        if normalized in {"running", "active"}:
            return "running", raw_status.replace("_", " ").title(), "🔄"
        if normalized in {"follow_up", "waiting_approval", "blocked"}:
            return "warning", raw_status.replace("_", " ").title(), "⏸️"
        return "pending", raw_status.replace("_", " ").title(), "📝"

    def _format_plan_timestamp(raw_value) -> str:
        formatted = _format_timestamp(raw_value)
        return formatted or (str(raw_value) if raw_value else "")

    def _render_step_payload(value) -> None:
        if isinstance(value, (dict, list)):
            st.json(value)
        elif value not in (None, ""):
            st.code(str(value), language="text")

    if btype == "USER_MESSAGE":
        with st.chat_message("user"):
            with st.container(border=True):
                _ts = _block_timestamp()
                _c1, _c2 = st.columns([3, 2])
                with _c1:
                    status_chip("pending", label="User", icon="👤")
                with _c2:
                    if _ts:
                        st.caption(_ts)
                st.write(content.get("text", ""))

    elif btype == "AGENT_PLAN":
        with st.chat_message("assistant", avatar="🤖"):
            section_header("Agent Response", "Summary first, details on demand", icon="🤖")
            show_metadata()
            st.divider()
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
                        _render_md_with_dataframes(main_part, block_id, "main")
                        # ── Render visible DataFrames (with DF IDs) between answer and raw details ──
                        _dfs = content.get("_dataframes")
                        if _dfs and isinstance(_dfs, dict):
                            _render_embedded_dataframes(_dfs, block_id, only_visible=True)
                        with st.expander(summary_text, expanded=False):
                            # Non-visible (supplementary) DFs go inside raw details
                            if _dfs and isinstance(_dfs, dict):
                                _render_embedded_dataframes(_dfs, block_id, only_visible=False)
                            _render_md_with_dataframes(details_body, block_id, "det")
                    else:
                        _render_md_with_dataframes(md, block_id, "main")
                        _dfs = content.get("_dataframes")
                        if _dfs and isinstance(_dfs, dict):
                            _render_embedded_dataframes(_dfs, block_id)
                else:
                    _render_md_with_dataframes(md, block_id, "main")
                    # ── Render embedded DataFrames after plain markdown ──
                    _dfs = content.get("_dataframes")
                    if _dfs and isinstance(_dfs, dict):
                        _render_embedded_dataframes(_dfs, block_id)

            # ── Inline sync progress (visible right in the agent response) ──
            _sync_run_uuid = content.get("_sync_run_uuid", "")
            if _sync_run_uuid:
                _sync_ts = ""
                _sync_detail = ""
                _sync_state = "unknown"
                try:
                    _sync_resp = make_authenticated_request(
                        "GET",
                        f"{API_URL}/jobs/{_sync_run_uuid}/status",
                        timeout=LIVE_JOB_STATUS_TIMEOUT_SECONDS,
                    )
                    if _sync_resp.status_code == 200:
                        _sj = _sync_resp.json()
                        _sync_state = (_sj.get("transfer_state") or "").strip().lower()
                        _sync_detail = (_sj.get("transfer_detail") or "").strip()
                        # Cache so auto-refresh keeps running
                        st.session_state[f"_transfer_state_{_sync_run_uuid}"] = _sync_state
                except Exception:
                    pass
                if _sync_state == "downloading_outputs":
                    st.info(f"📥 **Sync in progress** — {_sync_detail or 'transferring files…'}", icon="⏳")
                elif _sync_state == "outputs_downloaded":
                    st.success("✅ Results synced successfully.")
                elif _sync_state == "transfer_failed":
                    st.error("❌ Sync failed. You can retry with **sync results locally with force**.")

            _all_blocks = st.session_state.get("blocks", [])
            _related_workflow = _find_related_workflow_plan(block, _all_blocks)
            _workflow_highlights = _workflow_highlight_steps(_related_workflow)
            if _workflow_highlights:
                st.divider()
                st.markdown("**Workflow Results**")
                for _wf_idx, _wf_step in enumerate(_workflow_highlights, start=1):
                    _wf_title = _wf_step.get("title") or _wf_step.get("kind") or f"Workflow step {_wf_idx}"
                    st.caption(_wf_title)
                    _wf_result = _wf_step.get("result")
                    if isinstance(_wf_result, dict):
                        _render_workflow_plot_payload(_wf_result, block_id, f"agent_{_wf_idx}")
                        _wf_markdown = _wf_result.get("markdown")
                        if isinstance(_wf_markdown, str) and _wf_markdown.strip():
                            st.markdown(_wf_markdown)

            # ── Render embedded images (DE plots, etc.) ──
            _images = content.get("_images")
            if _images and isinstance(_images, list):
                import base64
                for _img_idx, _img in enumerate(_images):
                    _b64 = _img.get("data_b64", "")
                    _label = _img.get("label", "Plot")
                    if _b64:
                        _img_bytes = base64.b64decode(_b64)
                        st.image(_img_bytes, caption=_label, use_container_width=True)

            # ── Per-message token count ──
            _msg_tokens = content.get("tokens")
            if _msg_tokens and _msg_tokens.get("total_tokens"):
                _tt = _msg_tokens["total_tokens"]
                _pt = _msg_tokens.get("prompt_tokens", 0)
                _ct = _msg_tokens.get("completion_tokens", 0)
                _mn = _msg_tokens.get("model", "")
                _tok_label = f"🪙 {_tt:,} tokens  (↑{_pt:,} prompt · ↓{_ct:,} completion)"
                if _mn:
                    _tok_label += f"  ·  `{_mn}`"
                st.caption(_tok_label)

            # ── Debug panel (only when debug toggle is on) ──
            _debug_info = content.get("_debug")
            if _debug_info and st.session_state.get("_debug_mode"):
                with st.expander("🐛 Debug Info", expanded=False):
                    import json as _json
                    st.code(_json.dumps(_debug_info, indent=2, default=str), language="json")

    elif btype == "APPROVAL_GATE":
        with st.chat_message("assistant", avatar="🚦"):
            # Get extracted parameters and metadata
            extracted_params = content.get("extracted_params", {})
            approved_params = content.get("edited_params") or extracted_params
            manual_mode = content.get("manual_mode", False)
            attempt_number = content.get("attempt_number", 1)
            rejection_history = content.get("rejection_history", [])
            
            section_header("Approval Review", "Review and edit parameters before submission", icon="🚦")
            _chip = "pending"
            if status == "APPROVED":
                _chip = "complete"
            elif status == "REJECTED":
                _chip = "failed"
            status_chip(_chip, label=status.title(), icon="🧾")
            metadata_row({"Attempt": f"{attempt_number}/3", "Mode": "Manual" if manual_mode else "AI Extracted", "Block": block_id[:8]})
            st.divider()

            # Title based on mode
            if manual_mode:
                info_callout(
                    "The AI could not confidently extract parameters after multiple attempts. Please verify manually.",
                    kind="warning",
                    icon="⚠️",
                )
            else:
                st.caption(f"Approval required (attempt {attempt_number}/3)")
            
            st.write(content.get("label", "Approve this plan?"))
            st.caption(f"Block ID: `{block_id}`")

            _summary = {}
            _src_params = approved_params if isinstance(approved_params, dict) else extracted_params
            if isinstance(_src_params, dict):
                for _k in [
                    "sample_name",
                    "mode",
                    "input_type",
                    "input_directory",
                    "execution_mode",
                    "entry_point",
                    "result_destination",
                    "ssh_profile_nickname",
                    "remote_base_path",
                ]:
                    _v = _src_params.get(_k)
                    if _v not in (None, "", [], {}):
                        _summary[_k.replace("_", " ").title()] = _v
                _summary["Gate Action"] = extracted_params.get("gate_action") or content.get("gate_action", "job")
            if _summary:
                with st.expander("Plan Summary", expanded=True):
                    review_panel(_summary, title="Ready-to-Run Parameters")
            
            # Show rejection history if exists
            if rejection_history:
                with st.expander(f"📜 Rejection History ({len(rejection_history)} previous attempts)", expanded=False):
                    for i, hist in enumerate(rejection_history, 1):
                        st.text(f"Attempt {hist.get('attempt', i)}: {hist.get('reason', 'No reason')}")
                        st.caption(f"at {hist.get('timestamp', 'unknown time')}")

            if status == "APPROVED":
                st.success("✅ Approved")
                # Show what parameters were used
                if approved_params:
                    with st.expander("📋 Parameters Used", expanded=False):
                        st.json(approved_params)
                        
            elif status == "REJECTED":
                st.error("❌ Rejected")
                # Show rejection reason if available
                reason = content.get("rejection_reason", "No reason provided")
                st.caption(f"Reason: {reason}")
                
            else:
                # Pending approval - show editable parameter form
                _gate_action = extracted_params.get("gate_action") or content.get("gate_action", "job")

                if _gate_action == "local_sample_existing":
                    _src = extracted_params.get("input_directory", "")
                    _dst = extracted_params.get("staged_input_directory", "")
                    if _src:
                        st.caption(f"Source: `{_src}`")
                    if _dst:
                        st.caption(f"Staged folder: `{_dst}`")

                    col1, col2 = st.columns(2)
                    if col1.button("✅ Reuse Existing Copy", key=f"reuse_stage_{block_id}"):
                        payload_update = dict(content)
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "APPROVED", "payload": payload_update}
                        )
                        st.rerun()
                    if col2.button("♻️ Replace With Fresh Copy", key=f"replace_stage_{block_id}"):
                        payload_update = dict(content)
                        payload_update["rejection_reason"] = "Replace existing staged sample folder"
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "REJECTED", "payload": payload_update}
                        )
                        st.rerun()

                elif _gate_action == "download" and extracted_params.get("files"):
                    # ── Download approval form ──
                    _dl_files = extracted_params["files"]
                    _dl_total = extracted_params.get("total_size_bytes", 0)
                    _dl_target = extracted_params.get("target_dir", "data/")
                    _dl_mb = round(_dl_total / (1024 * 1024), 1) if _dl_total else "?"

                    st.write(f"**📥 Download Plan** — {len(_dl_files)} file(s), ~{_dl_mb} MB → `{_dl_target}`")
                    for _f in _dl_files:
                        _fname = _f.get("filename", "?")
                        _fsize = _f.get("size_bytes")
                        _fmb = f" ({round(_fsize / (1024 * 1024), 1)} MB)" if _fsize else ""
                        st.markdown(f"- `{_fname}`{_fmb}")

                    col1, col2 = st.columns(2)
                    if col1.button("✅ Approve Download", key=f"dl_approve_{block_id}"):
                        payload_update = dict(content)
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "APPROVED", "payload": payload_update}
                        )
                        st.rerun()
                    if col2.button("❌ Cancel", key=f"dl_reject_{block_id}"):
                        payload_update = dict(content)
                        payload_update["rejection_reason"] = "User cancelled download"
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "REJECTED", "payload": payload_update}
                        )
                        st.rerun()

                elif _gate_action == "remote_stage":
                    st.write("**📤 Remote Staging Plan**")

                    _current_user_id = user.get("id") or user.get("user_id", "")
                    _saved_profiles = _load_user_ssh_profiles(_current_user_id)
                    _profile_by_id = {
                        profile.get("id"): profile
                        for profile in _saved_profiles
                        if profile.get("id")
                    }

                    with st.form(key=f"remote_stage_form_{block_id}"):
                        grouped_section("Sample & Input")
                        sample_name = st.text_input(
                            "Sample Name",
                            value=extracted_params.get("sample_name", ""),
                            help="Name to register for the staged sample."
                        )

                        mode_options = ["DNA", "RNA", "CDNA"]
                        current_mode = extracted_params.get("mode", "DNA")
                        mode_index = mode_options.index(current_mode) if current_mode in mode_options else 0
                        mode = st.selectbox("Analysis Mode", mode_options, index=mode_index)

                        input_directory = st.text_input(
                            "Input Directory",
                            value=extracted_params.get("input_directory", ""),
                            help="Local source folder that will be staged to the remote data cache."
                        )

                        genome_options = ["GRCh38", "mm39"]
                        current_genomes = extracted_params.get("reference_genome", ["mm39"])
                        if isinstance(current_genomes, str):
                            if current_genomes.startswith("["):
                                try:
                                    import json as _json
                                    current_genomes = _json.loads(current_genomes)
                                except (ValueError, TypeError):
                                    current_genomes = [current_genomes]
                            else:
                                current_genomes = [current_genomes]
                        current_genomes = [g for g in current_genomes if g in genome_options] or ["mm39"]
                        reference_genomes = st.multiselect(
                            "Reference Genome(s)",
                            genome_options,
                            default=current_genomes,
                            help="Reference assets that should be available under the remote ref/ cache."
                        )

                        input_type = extracted_params.get("input_type", "pod5")
                        st.caption(f"Input type: `{input_type}`")

                        grouped_section("Remote Target")
                        ssh_profile_id = extracted_params.get("ssh_profile_id") or ""
                        ssh_profile_nickname = extracted_params.get("ssh_profile_nickname", "") or ""
                        remote_base_path = extracted_params.get("remote_base_path", "") or ""

                        _selected_profile_id = ssh_profile_id if ssh_profile_id in _profile_by_id else ""
                        if not _selected_profile_id and ssh_profile_nickname:
                            _nickname = ssh_profile_nickname.strip().lower()
                            _match = next(
                                (
                                    profile
                                    for profile in _saved_profiles
                                    if (
                                        (profile.get("nickname") or "").strip().lower() == _nickname
                                        or (profile.get("ssh_host") or "").strip().lower() == _nickname
                                    )
                                ),
                                None,
                            )
                            _selected_profile_id = (_match or {}).get("id") or ""
                        if not _selected_profile_id and len(_saved_profiles) == 1:
                            _selected_profile_id = _saved_profiles[0].get("id") or ""

                        _profile_options = [""] + list(_profile_by_id.keys())
                        _selected_profile_id = st.selectbox(
                            "Saved SSH Profile",
                            options=_profile_options,
                            index=_profile_options.index(_selected_profile_id) if _selected_profile_id in _profile_options else 0,
                            format_func=lambda profile_id: (
                                "(manual entry)"
                                if not profile_id
                                else (
                                    _profile_by_id[profile_id].get("nickname")
                                    or _profile_by_id[profile_id].get("ssh_host")
                                    or profile_id
                                )
                            ),
                            key=f"remote_stage_profile_{block_id}",
                            help="Choose the remote profile that will receive the staged sample data.",
                        )

                        _selected_profile = _profile_by_id.get(_selected_profile_id) if _selected_profile_id else None
                        if _selected_profile:
                            ssh_profile_id = _selected_profile_id
                            ssh_profile_nickname = (
                                _selected_profile.get("nickname")
                                or _selected_profile.get("ssh_host")
                                or ssh_profile_nickname
                            )
                            if not remote_base_path:
                                _project_slug = _active_project_slug()
                                _template_context = {
                                    "user_id": _current_user_id,
                                    "project_id": st.session_state.get("active_project_id", ""),
                                    "project_slug": _project_slug,
                                    "sample_name": sample_name or "sample",
                                    "workflow_slug": _slugify_project_name(sample_name or "sample"),
                                    "ssh_username": _selected_profile.get("ssh_username") or "agoutic",
                                }
                                remote_base_path = (
                                    _render_profile_path_template(
                                        _selected_profile.get("remote_base_path"),
                                        _template_context,
                                    )
                                    or ""
                                )

                        ssh_profile_nickname = st.text_input(
                            "SSH Profile Nickname",
                            value=ssh_profile_nickname,
                            help="Saved Remote Profile nickname, such as hpc3."
                        )
                        if ssh_profile_id:
                            st.caption(f"Existing SSH profile ID will be reused unless you change the nickname: {ssh_profile_id}")

                        remote_base_path = st.text_input(
                            "Remote Base Path",
                            value=remote_base_path,
                            help="Top-level remote folder that contains ref/, data/, and project workflow folders."
                        )

                        local_workflow_directory = extracted_params.get("local_workflow_directory", "") or ""
                        if local_workflow_directory:
                            st.caption(f"Local workflow folder: {local_workflow_directory}")

                        st.divider()
                        col1, col2 = st.columns(2)
                        submit_approve = col1.form_submit_button("✅ Approve Staging", width="stretch")
                        submit_reject = col2.form_submit_button("❌ Cancel Staging", width="stretch")

                        if submit_approve:
                            edited_params = {
                                "sample_name": sample_name,
                                "mode": mode,
                                "input_type": input_type,
                                "input_directory": input_directory,
                                "reference_genome": reference_genomes,
                                "execution_mode": "slurm",
                                "remote_action": "stage_only",
                                "gate_action": "remote_stage",
                                "ssh_profile_id": ssh_profile_id or None,
                                "ssh_profile_nickname": ssh_profile_nickname or None,
                                "remote_base_path": remote_base_path or None,
                                "local_workflow_directory": local_workflow_directory or None,
                            }
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            if resp.status_code == 200:
                                st.rerun()
                            else:
                                try:
                                    error_detail = resp.json().get("detail") or resp.text
                                except Exception:
                                    error_detail = resp.text
                                st.error(f"Approval failed: {error_detail}")

                        if submit_reject:
                            payload_update = dict(content)
                            payload_update["rejection_reason"] = "User cancelled remote staging"
                            make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "REJECTED", "payload": payload_update}
                            )
                            st.rerun()

                elif _gate_action == "reconcile_bams" and extracted_params:
                    preflight_summary = extracted_params.get("preflight_summary") or {}
                    bam_inputs = extracted_params.get("bam_inputs") or []
                    reference = extracted_params.get("reference") or "unknown"
                    annotation_gtf = extracted_params.get("annotation_gtf") or "not resolved"
                    annotation_gtf_source = extracted_params.get("annotation_gtf_source") or "unknown"
                    annotation_evidence = extracted_params.get("annotation_evidence") or []
                    output_directory = extracted_params.get("output_directory") or extracted_params.get("input_directory") or ""
                    output_prefix = extracted_params.get("output_prefix") or extracted_params.get("sample_name") or "reconciled"
                    gene_prefix_default = extracted_params.get("gene_prefix") or "CONSG"
                    tx_prefix_default = extracted_params.get("tx_prefix") or "CONST"
                    id_tag_default = extracted_params.get("id_tag") or "TX"
                    gene_tag_default = extracted_params.get("gene_tag") or "GX"
                    threads_default = int(extracted_params.get("threads") or (os.cpu_count() or 1))
                    exon_merge_distance_default = int(extracted_params.get("exon_merge_distance") or 5)
                    min_tpm_default = float(extracted_params.get("min_tpm") if extracted_params.get("min_tpm") is not None else 1.0)
                    min_samples_default = int(extracted_params.get("min_samples") or 2)
                    filter_known_default = bool(extracted_params.get("filter_known"))
                    underlying_script_id = extracted_params.get("underlying_script_id") or "reconcile_bams/reconcileBams"

                    with st.form(key=f"reconcile_form_{block_id}"):
                        grouped_section("Reconcile Summary")
                        st.write(f"**Reference**: `{reference}`")
                        st.write(f"**Execution Script**: `{underlying_script_id}`")

                        grouped_section("Reconcile Settings")
                        output_directory = st.text_input("Workflow Root", value=output_directory, help="Parent project directory where the next workflowN folder will be created.")
                        output_prefix = st.text_input("Output Prefix", value=output_prefix)
                        annotation_gtf = st.text_input("Annotation GTF", value=annotation_gtf)
                        st.caption(f"GTF source: `{annotation_gtf_source}`")
                        col1, col2 = st.columns(2)
                        gene_prefix = col1.text_input("Gene Prefix", value=gene_prefix_default)
                        tx_prefix = col2.text_input("Transcript Prefix", value=tx_prefix_default)
                        col3, col4 = st.columns(2)
                        id_tag = col3.text_input("Transcript ID Tag", value=id_tag_default)
                        gene_tag = col4.text_input("Gene ID Tag", value=gene_tag_default)
                        col5, col6 = st.columns(2)
                        threads = col5.number_input("Threads", min_value=1, value=threads_default, step=1)
                        exon_merge_distance = col6.number_input("Exon Merge Distance", min_value=0, value=exon_merge_distance_default, step=1)
                        col7, col8 = st.columns(2)
                        min_tpm = col7.number_input("Min TPM", min_value=0.0, value=min_tpm_default, step=0.1, format="%.3f")
                        min_samples = col8.number_input("Min Samples", min_value=1, value=min_samples_default, step=1)
                        filter_known = st.checkbox("Filter Known Transcripts", value=filter_known_default)

                        grouped_section("Validated Inputs")
                        st.write(f"{len(bam_inputs)} annotated BAM(s) passed preflight validation.")
                        for bam in bam_inputs:
                            if not isinstance(bam, dict):
                                continue
                            sample_label = bam.get("sample") or "sample"
                            bam_path = bam.get("path") or ""
                            st.caption(f"{sample_label}: `{bam_path}`")

                        if annotation_evidence:
                            grouped_section("Annotation Provenance")
                            for item in annotation_evidence:
                                if not isinstance(item, dict):
                                    continue
                                evidence_file = item.get("file") or "config"
                                evidence_line = item.get("line")
                                evidence_gtf = item.get("annotation_gtf") or ""
                                st.caption(f"{evidence_file}:{evidence_line} -> `{evidence_gtf}`")

                        message = preflight_summary.get("message") if isinstance(preflight_summary, dict) else None
                        if message:
                            st.info(message)

                        st.divider()
                        col1, col2 = st.columns(2)
                        submit_approve = col1.form_submit_button("✅ Approve Reconcile", width="stretch")
                        submit_reject = col2.form_submit_button("❌ Reject", width="stretch")

                        if submit_approve:
                            edited_params = dict(extracted_params)
                            edited_params.update(
                                {
                                    "output_directory": output_directory,
                                    "input_directory": output_directory,
                                    "output_prefix": output_prefix,
                                    "sample_name": output_prefix,
                                    "annotation_gtf": annotation_gtf,
                                    "gene_prefix": gene_prefix,
                                    "tx_prefix": tx_prefix,
                                    "id_tag": id_tag,
                                    "gene_tag": gene_tag,
                                    "threads": int(threads),
                                    "exon_merge_distance": int(exon_merge_distance),
                                    "min_tpm": float(min_tpm),
                                    "min_samples": int(min_samples),
                                    "filter_known": bool(filter_known),
                                    "script_id": "reconcile_bams/reconcile_bams",
                                }
                            )
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            if resp.status_code == 200:
                                st.rerun()
                            else:
                                try:
                                    error_detail = resp.json().get("detail") or resp.text
                                except Exception:
                                    error_detail = resp.text
                                st.error(f"Approval failed: {error_detail}")

                        if submit_reject:
                            st.session_state[f"rejecting_{block_id}"] = True
                            st.rerun()

                elif extracted_params:
                    st.write("**📋 Extracted Parameters** (edit if needed):")
                    
                    with st.form(key=f"params_form_{block_id}"):
                        grouped_section("Core Run Settings")
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
                        # Handle stringified JSON lists from DB (e.g. '["mm39"]')
                        if isinstance(current_genomes, str):
                            if current_genomes.startswith("["):
                                try:
                                    import json as _json
                                    current_genomes = _json.loads(current_genomes)
                                except (ValueError, TypeError):
                                    current_genomes = [current_genomes]
                            else:
                                current_genomes = [current_genomes]
                        # Filter to only valid options
                        current_genomes = [g for g in current_genomes if g in genome_options]
                        if not current_genomes:
                            current_genomes = ["mm39"]
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
                        
                        # Max concurrent GPU tasks — visible at top level (not hidden in Advanced)
                        _gpu_raw = extracted_params.get("max_gpu_tasks")
                        _gpu_val = int(_gpu_raw) if _gpu_raw is not None else 1
                        if _gpu_val < 1:
                            _gpu_val = 1
                        _gpu_options = [1, 2, 3, 4, 5, 6, 7, 8]
                        _gpu_idx = _gpu_options.index(_gpu_val) if _gpu_val in _gpu_options else 0
                        max_gpu_tasks = st.selectbox(
                            "🖥️ Max Concurrent GPU Tasks",
                            options=_gpu_options,
                            index=_gpu_idx,
                            help="Maximum simultaneous dorado/GPU tasks within a pipeline run (default: 1)",
                        )

                        grouped_section("Execution")
                        execution_mode_options = ["local", "slurm"]
                        current_execution_mode = extracted_params.get("execution_mode", "local")
                        execution_mode_index = execution_mode_options.index(current_execution_mode) if current_execution_mode in execution_mode_options else 0
                        execution_mode = st.selectbox(
                            "Execution Mode",
                            execution_mode_options,
                            index=execution_mode_index,
                            format_func=lambda value: "Local" if value == "local" else "HPC3 / SLURM",
                            help="Choose whether this run stays on the AGOUTIC host or is submitted through a remote SLURM profile."
                        )

                        ssh_profile_id = extracted_params.get("ssh_profile_id")
                        ssh_profile_nickname = extracted_params.get("ssh_profile_nickname", "") or ""
                        slurm_account = extracted_params.get("slurm_account", "") or ""
                        slurm_partition = extracted_params.get("slurm_partition", "") or ""
                        slurm_gpu_account = extracted_params.get("slurm_gpu_account", "") or ""
                        slurm_gpu_partition = extracted_params.get("slurm_gpu_partition", "") or ""
                        slurm_cpus = int(extracted_params.get("slurm_cpus") or 4)
                        slurm_memory_gb = int(extracted_params.get("slurm_memory_gb") or 16)
                        slurm_walltime = extracted_params.get("slurm_walltime", "04:00:00") or "04:00:00"
                        slurm_gpus = max(int(extracted_params.get("slurm_gpus") or 1), 1)
                        slurm_gpu_type = extracted_params.get("slurm_gpu_type", "") or ""
                        remote_base_path = extracted_params.get("remote_base_path", "") or ""
                        local_workflow_directory = extracted_params.get("local_workflow_directory", "") or ""
                        result_destination_default = extracted_params.get("result_destination") or "local"

                        if execution_mode == "slurm":
                            grouped_section("Remote Profile & SLURM")
                            st.caption("Remote execution uses one of your saved SSH profiles. You can refer to it by nickname, for example hpc3.")
                            _current_user_id = user.get("id") or user.get("user_id", "")
                            _saved_profiles = _load_user_ssh_profiles(_current_user_id)
                            _profile_by_id = {
                                profile.get("id"): profile
                                for profile in _saved_profiles
                                if profile.get("id")
                            }

                            _selected_profile_id = ssh_profile_id if ssh_profile_id in _profile_by_id else ""
                            if not _selected_profile_id and ssh_profile_nickname:
                                _nickname = ssh_profile_nickname.strip().lower()
                                _match = next(
                                    (
                                        profile
                                        for profile in _saved_profiles
                                        if (
                                            (profile.get("nickname") or "").strip().lower() == _nickname
                                            or (profile.get("ssh_host") or "").strip().lower() == _nickname
                                        )
                                    ),
                                    None,
                                )
                                _selected_profile_id = (_match or {}).get("id") or ""
                            if not _selected_profile_id and len(_saved_profiles) == 1:
                                _selected_profile_id = _saved_profiles[0].get("id") or ""

                            _profile_options = [""] + list(_profile_by_id.keys())
                            _selected_profile_id = st.selectbox(
                                "Saved SSH Profile",
                                options=_profile_options,
                                index=_profile_options.index(_selected_profile_id) if _selected_profile_id in _profile_options else 0,
                                format_func=lambda profile_id: (
                                    "(manual entry)"
                                    if not profile_id
                                    else (
                                        _profile_by_id[profile_id].get("nickname")
                                        or _profile_by_id[profile_id].get("ssh_host")
                                        or profile_id
                                    )
                                ),
                                key=f"slurm_profile_{block_id}",
                                help="Choose a saved profile to reuse its SLURM defaults and remote path templates.",
                            )

                            _selected_profile = _profile_by_id.get(_selected_profile_id) if _selected_profile_id else None
                            if _selected_profile:
                                ssh_profile_id = _selected_profile_id
                                ssh_profile_nickname = (
                                    _selected_profile.get("nickname")
                                    or _selected_profile.get("ssh_host")
                                    or ssh_profile_nickname
                                )

                                slurm_account = slurm_account or (_selected_profile.get("default_slurm_account") or "")
                                slurm_partition = slurm_partition or (_selected_profile.get("default_slurm_partition") or "")
                                slurm_gpu_account = slurm_gpu_account or (_selected_profile.get("default_slurm_gpu_account") or "")
                                slurm_gpu_partition = slurm_gpu_partition or (_selected_profile.get("default_slurm_gpu_partition") or "")

                                _ssh_username = _selected_profile.get("ssh_username") or "agoutic"
                                _project_slug = _active_project_slug()
                                _workflow_slug = _slugify_project_name(sample_name or "workflow")
                                _remote_root = f"/scratch/{_ssh_username}/agoutic/{_project_slug}/{_workflow_slug}"
                                _template_context = {
                                    "user_id": _current_user_id,
                                    "project_id": st.session_state.get("active_project_id", ""),
                                    "project_slug": _project_slug,
                                    "sample_name": sample_name or "workflow",
                                    "workflow_slug": _workflow_slug,
                                    "ssh_username": _ssh_username,
                                }

                                if not remote_base_path:
                                    remote_base_path = (
                                        _render_profile_path_template(
                                            _selected_profile.get("remote_base_path"),
                                            _template_context,
                                        )
                                        or ""
                                    )
                            elif not _saved_profiles:
                                st.caption("No saved SSH profiles found. Create one from Remote Profiles to avoid re-entering SLURM settings.")

                            if local_workflow_directory:
                                st.caption(f"Local workflow folder: {local_workflow_directory}")
                            ssh_profile_nickname = st.text_input(
                                "SSH Profile Nickname",
                                value=ssh_profile_nickname,
                                help="Saved Remote Profile nickname, such as hpc3."
                            )
                            if ssh_profile_id:
                                st.caption(f"Existing SSH profile ID will be reused unless you change the nickname: {ssh_profile_id}")

                            col_slurm_1, col_slurm_2 = st.columns(2)
                            with col_slurm_1:
                                slurm_account = st.text_input("SLURM Account", value=slurm_account)
                                slurm_gpu_account = st.text_input("GPU Account Override", value=slurm_gpu_account, help="Optional account to use when GPUs are requested.")
                                slurm_cpus = st.number_input("SLURM CPUs", min_value=1, max_value=256, value=slurm_cpus)
                                slurm_walltime = st.text_input("SLURM Walltime", value=slurm_walltime, help="Format HH:MM:SS or D-HH:MM:SS")
                                remote_base_path = st.text_input("Remote Base Path", value=remote_base_path)
                            with col_slurm_2:
                                slurm_partition = st.text_input("SLURM Partition", value=slurm_partition)
                                slurm_gpu_partition = st.text_input("GPU Partition Override", value=slurm_gpu_partition, help="Optional partition to use when GPUs are requested.")
                                slurm_memory_gb = st.number_input("SLURM Memory (GB)", min_value=1, max_value=2048, value=slurm_memory_gb)
                                slurm_gpus = st.number_input("SLURM GPUs", min_value=1, max_value=32, value=slurm_gpus)

                            slurm_gpu_type = st.text_input("GPU Type (optional)", value=slurm_gpu_type)
                            result_destination = st.selectbox(
                                "Result Destination",
                                ["local", "remote", "both"],
                                index=["local", "remote", "both"].index(result_destination_default) if result_destination_default in ["local", "remote", "both"] else 0,
                                help="Choose whether results stay remote, sync back locally, or both."
                            )
                        else:
                            result_destination = None
                        
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
                        
                        submit_approve = col1.form_submit_button("✅ Approve", width="stretch")
                        submit_reject = col2.form_submit_button("❌ Reject", width="stretch")
                        
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
                                "max_gpu_tasks": max_gpu_tasks,
                                "execution_mode": execution_mode,
                            }
                            if execution_mode == "slurm":
                                edited_params.update({
                                    "ssh_profile_id": ssh_profile_id,
                                    "ssh_profile_nickname": ssh_profile_nickname or None,
                                    "local_workflow_directory": local_workflow_directory or None,
                                    "slurm_account": slurm_account or None,
                                    "slurm_partition": slurm_partition or None,
                                    "slurm_gpu_account": slurm_gpu_account or None,
                                    "slurm_gpu_partition": slurm_gpu_partition or None,
                                    "slurm_cpus": int(slurm_cpus),
                                    "slurm_memory_gb": int(slurm_memory_gb),
                                    "slurm_walltime": slurm_walltime or None,
                                    "slurm_gpus": int(slurm_gpus),
                                    "slurm_gpu_type": slurm_gpu_type or None,
                                    "remote_base_path": remote_base_path or None,
                                    "result_destination": result_destination,
                                })
                            else:
                                edited_params.update({
                                    "local_workflow_directory": None,
                                    "ssh_profile_id": None,
                                    "ssh_profile_nickname": None,
                                    "slurm_account": None,
                                    "slurm_partition": None,
                                    "slurm_gpu_account": None,
                                    "slurm_gpu_partition": None,
                                    "slurm_cpus": None,
                                    "slurm_memory_gb": None,
                                    "slurm_walltime": None,
                                    "slurm_gpus": None,
                                    "slurm_gpu_type": None,
                                    "remote_base_path": None,
                                    "result_destination": None,
                                })
                            # Preserve resume_from_dir for resubmit-resume flow
                            if extracted_params.get("resume_from_dir"):
                                edited_params["resume_from_dir"] = extracted_params["resume_from_dir"]
                            
                            # Update block with edited params and approved status
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            if resp.status_code == 200:
                                st.rerun()
                            else:
                                try:
                                    error_detail = resp.json().get("detail") or resp.text
                                except Exception:
                                    error_detail = resp.text
                                st.error(f"Approval failed: {error_detail}")
                        
                        if submit_reject:
                            st.session_state[f"rejecting_{block_id}"] = True
                            st.rerun()
                
                # Show rejection feedback form if user clicked reject
                if _gate_action != "local_sample_existing" and st.session_state.get(f"rejecting_{block_id}", False):
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

    
    elif btype == "WORKFLOW_PLAN":
        with st.chat_message("assistant", avatar="🗺️"):
            section_header("Workflow Plan", "Structured execution outline", icon="🗺️")
            show_metadata()
            st.divider()

            plan_status = str(content.get("status") or status or "PENDING")
            # Mark plan failures so "try again" can replay the prompt.
            if plan_status.strip().upper() == "FAILED":
                st.session_state["_last_prompt_failed"] = True
            chip_kind, chip_label, chip_icon = _workflow_status_presentation(plan_status)
            status_chip(chip_kind, label=chip_label, icon=chip_icon)

            summary = content.get("summary") or content.get("title") or content.get("label")
            steps = content.get("steps")
            planner_meta = content.get("planner_metadata") if isinstance(content.get("planner_metadata"), dict) else {}
            step_count = len(steps) if isinstance(steps, list) else 0
            current_step_id = content.get("current_step_id")
            current_step = None
            if isinstance(steps, list) and isinstance(current_step_id, str):
                current_step = next(
                    (step for step in steps if isinstance(step, dict) and step.get("id") == current_step_id),
                    None,
                )

            metadata = {"Steps": step_count}
            if planner_meta:
                planning_mode = planner_meta.get("planning_mode") or "deterministic"
                metadata["Planner"] = str(planning_mode).replace("_", " ").title()
                if planner_meta.get("hybrid_attempted"):
                    metadata["Hybrid"] = "Succeeded" if planner_meta.get("hybrid_succeeded") else "Fallback"
            if current_step is not None:
                metadata["Current Step"] = current_step.get("title") or current_step.get("id") or current_step_id
            plan_started = _format_plan_timestamp(content.get("started_at"))
            plan_completed = _format_plan_timestamp(content.get("completed_at"))
            plan_duration = _format_duration(content.get("started_at"), content.get("completed_at"))
            if plan_started:
                metadata["Started"] = plan_started
            if plan_completed:
                metadata["Completed"] = plan_completed
            if plan_duration:
                metadata["Duration"] = plan_duration
            created_at = _block_timestamp()
            if created_at:
                metadata["Created"] = created_at
            metadata_row(metadata)

            fallback_reason = planner_meta.get("reason_code") if planner_meta else ""
            if fallback_reason:
                st.caption(f"Planner fallback reason: {fallback_reason}")

            if summary:
                st.markdown(f"**{summary}**")

            if current_step is not None and plan_status.strip().upper() in {"RUNNING", "FOLLOW_UP", "WAITING_APPROVAL"}:
                st.caption(
                    f"Current step: {current_step.get('title') or current_step.get('id') or current_step_id}"
                )

            if isinstance(steps, list) and steps:
                for idx, step in enumerate(steps, start=1):
                    if isinstance(step, dict):
                        title = step.get("title") or step.get("name") or f"Step {idx}"
                        detail = step.get("description") or step.get("details") or ""
                        step_id = step.get("id")
                        step_status = str(step.get("status") or "PENDING")
                        step_kind, step_label, step_icon = _workflow_status_presentation(step_status)
                        is_current = isinstance(step_id, str) and step_id == current_step_id
                        expander_title = f"{idx}. {title}"
                        if is_current:
                            expander_title += " · current"
                        expanded = is_current or step_status.strip().upper() in {"RUNNING", "FAILED", "FOLLOW_UP", "WAITING_APPROVAL"}

                        with st.expander(expander_title, expanded=expanded):
                            status_chip(step_kind, label=step_label, icon=step_icon)

                            step_meta = {"Kind": step.get("kind") or "workflow_step"}
                            if step_id:
                                step_meta["Step ID"] = step_id
                            step_meta["Execution"] = "Approval Required" if step.get("requires_approval") else "Safe Auto-Execute"

                            started_at = _format_plan_timestamp(step.get("started_at"))
                            completed_at = _format_plan_timestamp(step.get("completed_at"))
                            duration = _format_duration(step.get("started_at"), step.get("completed_at"))
                            if started_at:
                                step_meta["Started"] = started_at
                            if completed_at:
                                step_meta["Completed"] = completed_at
                            if duration:
                                step_meta["Duration"] = duration
                            metadata_row(step_meta)

                            depends_on = step.get("depends_on")
                            if isinstance(depends_on, list) and depends_on:
                                st.caption(f"Depends on: {', '.join(str(dep) for dep in depends_on)}")

                            if detail:
                                st.caption(detail)

                            error = step.get("error")
                            if error not in (None, ""):
                                info_callout(str(error), kind="error", icon="❌")

                            result = step.get("result")
                            if result not in (None, "", [], {}):
                                st.markdown("**Result**")
                                if isinstance(result, dict):
                                    _render_workflow_plot_payload(result, block_id, f"step_{idx}")
                                    result_markdown = result.get("markdown")
                                    if isinstance(result_markdown, str) and result_markdown.strip():
                                        st.markdown(result_markdown)
                                _render_step_payload(result)
                    else:
                        st.markdown(f"{idx}. {step}")

            markdown = content.get("markdown")
            if markdown:
                st.markdown(markdown)

            if isinstance(steps, list) and steps:
                with st.expander("Plan Payload", expanded=False):
                    st.json(content)

            if not summary and not markdown and not (isinstance(steps, list) and steps):
                with st.expander("Plan Payload", expanded=False):
                    st.json(content)

    elif btype == "STAGING_TASK":
        with st.chat_message("assistant", avatar="📤"):
            sample_name = content.get("sample_name", "Unknown")
            mode = content.get("mode", "Unknown")
            message = content.get("message", "Preparing remote staging...")
            progress = int(content.get("progress_percent", 0) or 0)
            remote_profile = content.get("ssh_profile_nickname") or content.get("ssh_profile_id") or "remote profile"
            remote_data_path = content.get("remote_data_path", "")
            stage_parts = content.get("stage_parts") or {}
            refs_part = stage_parts.get("references") or {}
            data_part = stage_parts.get("data") or {}
            refs_state = (refs_part.get("status") or "").upper()
            data_state = (data_part.get("status") or "").upper()

            _step_current = 0
            _step_completed = []
            _step_failed = []
            if refs_state == "COMPLETED":
                _step_completed.append(0)
            if data_state == "COMPLETED":
                _step_completed.append(1)

            if refs_state == "FAILED":
                _step_current = 0
                _step_failed.append(0)
            elif data_state == "FAILED":
                _step_current = 1
                _step_failed.append(1)
            elif data_state == "RUNNING":
                _step_current = 1
            elif refs_state == "RUNNING":
                _step_current = 0
            elif block.get("status") == "DONE":
                _step_current = 1
                _step_completed = [0, 1]
            elif block.get("status") == "FAILED":
                _step_current = 1 if 0 in _step_completed else 0
                if 0 in _step_completed and 1 not in _step_failed:
                    _step_failed.append(1)
                elif 0 not in _step_failed:
                    _step_failed.append(0)
            elif progress >= 50:
                _step_current = 1

            section_header(f"Remote Staging · {sample_name}", "Reference and data staging progress", icon="📤")
            metadata_row({"Mode": mode, "Target": remote_profile, "Progress": f"{max(min(progress, 100), 0)}%"})
            stepper(["References", "Sample Data"], current=_step_current, completed=_step_completed, failed=_step_failed)

            def _render_stage_part(label: str, part: dict):
                state = (part.get("status") or "PENDING").upper()
                part_progress = max(min(int(part.get("progress_percent", 0) or 0), 100), 0)
                part_message = part.get("message", "")
                details = part.get("details") or []

                st.write(f"**{label}**")
                st.progress(part_progress / 100)
                if state == "COMPLETED":
                    st.caption(f"✅ {part_message}")
                elif state == "FAILED":
                    st.error(part_message)
                elif state == "RUNNING":
                    st.caption(f"🔄 {part_message}")
                else:
                    st.caption(f"⏳ {part_message}")

                if details:
                    for detail in details:
                        detail_message = detail.get("message") or ""
                        if detail_message:
                            st.caption(f"• {detail_message}")

            if block.get("status") == "RUNNING":
                status_chip("running", label="Staging Running", icon="🔄")
                st.caption(message)
                st.progress(max(min(progress, 100), 0) / 100)
                _render_stage_part("References", stage_parts.get("references") or {
                    "status": "RUNNING",
                    "progress_percent": 40,
                    "message": "Staging reference assets on the remote profile...",
                })
                _render_stage_part("Data", stage_parts.get("data") or {
                    "status": "PENDING",
                    "progress_percent": 0,
                    "message": "Waiting for reference staging to finish.",
                })
            elif block.get("status") == "DONE":
                status_chip("complete", label="Staging Complete", icon="✅")
                st.caption(message)
                _render_stage_part("References", stage_parts.get("references") or {
                    "status": "COMPLETED",
                    "progress_percent": 100,
                    "message": "Reference assets are ready.",
                })
                _render_stage_part("Data", stage_parts.get("data") or {
                    "status": "COMPLETED",
                    "progress_percent": 100,
                    "message": "Sample data is ready.",
                })
                if remote_data_path:
                    st.caption(f"Remote data path: `{remote_data_path}`")
                ref_statuses = content.get("reference_cache_statuses") or {}
                if ref_statuses:
                    with st.expander("Reference cache status", expanded=False):
                        st.json(ref_statuses)
            else:
                status_chip("failed", label="Staging Failed", icon="❌")
                st.error(content.get("error") or message)
                _render_stage_part("References", stage_parts.get("references") or {
                    "status": "FAILED",
                    "progress_percent": 0,
                    "message": content.get("error") or message,
                })
                _render_stage_part("Data", stage_parts.get("data") or {
                    "status": "FAILED",
                    "progress_percent": 0,
                    "message": content.get("error") or message,
                })
                if remote_data_path:
                    st.caption(f"Remote data path: `{remote_data_path}`")
                traceback_text = content.get("traceback")
                if traceback_text:
                    with st.expander("Traceback", expanded=False):
                        st.code(traceback_text)

    elif btype == "EXECUTION_JOB":
        # Job execution monitoring with Nextflow progress visualization
        with st.chat_message("assistant", avatar="⚙️"):
            run_uuid = content.get("run_uuid", "")
            sample_name = content.get("sample_name", "Unknown")
            mode = content.get("mode", "Unknown")
            work_directory = content.get("work_directory", "")
            has_identity = bool(run_uuid) or bool(content.get("sample_name"))
            block_status_str = block.get("status", "")
            
            section_header(
                f"Nextflow Job · {sample_name if has_identity else 'Submission'}",
                "Live execution monitoring and controls",
                icon="🧬",
            )

            # Show human-readable folder name (e.g. "workflow2") when available
            if not run_uuid and block_status_str == "FAILED":
                st.caption("Run UUID not assigned (submission failed before launch)")
            
            # Live-fetch status for RUNNING jobs and DONE jobs with an
            # active transfer (manual sync on a completed job).
            job_status = content.get("job_status", {})
            live_status_poll_succeeded = False
            _persisted_transfer = (content.get("job_status", {}).get("transfer_state") or "").strip().lower()
            # Also check session state — the block payload may be stale but a
            # previous poll already saw the transfer become active.
            _cached_transfer = (st.session_state.get(f"_transfer_state_{run_uuid}") or "").strip().lower()
            _needs_live_poll = (
                block_status_str == "RUNNING"
                or _persisted_transfer in {"downloading_outputs"}
                or _cached_transfer in {"downloading_outputs"}
            )
            if run_uuid and _needs_live_poll:
                try:
                    live_resp = make_authenticated_request(
                        "GET",
                        f"{API_URL}/jobs/{run_uuid}/status",
                        timeout=LIVE_JOB_STATUS_TIMEOUT_SECONDS,
                    )
                    if live_resp.status_code == 200:
                        job_status = live_resp.json()
                        live_status_poll_succeeded = True
                        st.session_state[f"_job_polled_at_{run_uuid}"] = (
                            datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                        )
                        # Cache transfer_state so auto-refresh keeps polling
                        _live_ts = (job_status.get("transfer_state") or "").strip().lower()
                        st.session_state[f"_transfer_state_{run_uuid}"] = _live_ts
                except Exception:
                    pass  # Fall back to block payload
            
            if job_status:
                status_str = job_status.get("status", content.get("status", "UNKNOWN"))
                progress = job_status.get("progress_percent", 0)
                message = job_status.get("message", content.get("message", ""))
                tasks = job_status.get("tasks", {})
                submitted_at = job_status.get("submitted_at") or content.get("submitted_at") or block.get("created_at")
                started_at = job_status.get("started_at") or content.get("started_at")
                completed_at = job_status.get("completed_at") or content.get("completed_at")
                workflow_label = _workflow_label_from_path(work_directory)
                run_stage = (job_status.get("run_stage") or "").strip().lower()
                current_step = (job_status.get("current_step") or "").strip()
                current_step_detail = (job_status.get("current_step_detail") or "").strip()
                transfer_state = (job_status.get("transfer_state") or "").strip().lower()
                result_destination = (job_status.get("result_destination") or "").strip().lower()
                try:
                    progress_pct = max(0, min(100, int(progress or 0)))
                except (TypeError, ValueError):
                    progress_pct = 0
                _chip, _status_label, _status_icon = _run_status_label(status_str)
                status_chip(_chip, label=_status_label, icon=_status_icon)

                run_meta = {"Mode": mode, "Run UUID": run_uuid}
                if workflow_label:
                    run_meta["Workflow"] = workflow_label
                started_label = _format_timestamp(started_at or submitted_at)
                completed_label = _format_timestamp(completed_at)
                duration_label = _format_duration(started_at or submitted_at, completed_at)
                if started_label:
                    run_meta["Started"] = started_label
                if completed_label:
                    run_meta["Completed"] = completed_label
                if duration_label:
                    run_meta["Duration"] = duration_label
                metadata_row(run_meta)

                if current_step:
                    current_step_message = current_step
                    if current_step_detail and current_step_detail != current_step:
                        current_step_message = f"{current_step}: {current_step_detail}"
                    info_callout(current_step_message, kind="info", icon="🧭")

                is_running = status_str == "RUNNING"
                is_failed = status_str in ("FAILED", "CANCELLED")
                is_done = status_str in ("COMPLETED", "DELETED")

                queue_state = "pending"
                execute_state = "pending"
                finalize_state = "pending"

                if run_stage:
                    pre_queue_stages = {
                        "awaiting_details",
                        "awaiting_approval",
                        "validating_connection",
                        "preparing_remote_dirs",
                        "transferring_inputs",
                        "submitting_job",
                    }
                    queue_stages = {"queued", "running", "collecting_outputs", "syncing_results", "completed", "failed", "cancelled"}
                    run_stages = {"running", "collecting_outputs", "syncing_results", "completed", "failed", "cancelled"}

                    if run_stage in queue_stages:
                        queue_state = "complete"
                    elif run_stage in pre_queue_stages:
                        queue_state = "running"

                    if run_stage in {"running", "collecting_outputs"}:
                        execute_state = "running"
                    elif run_stage in {"syncing_results", "completed"}:
                        execute_state = "complete"
                    elif run_stage in {"failed", "cancelled"}:
                        execute_state = "failed"
                    elif run_stage in run_stages:
                        execute_state = "running"

                    if run_stage in {"syncing_results"} or transfer_state in {"downloading_outputs"}:
                        finalize_state = "running"
                    elif run_stage in {"completed"} or transfer_state in {"outputs_downloaded"}:
                        finalize_state = "complete"
                    elif run_stage in {"failed", "cancelled"} or transfer_state in {"transfer_failed"}:
                        finalize_state = "failed"

                elif is_done:
                    queue_state = "complete"
                    execute_state = "complete"
                    finalize_state = "complete"
                elif is_failed:
                    queue_state = "complete" if progress_pct >= 10 else "failed"
                    execute_state = "failed"
                    finalize_state = "failed"
                elif is_running:
                    queue_state = "complete" if progress_pct >= 10 else "running"
                    execute_state = "running" if progress_pct < 95 else "complete"
                    finalize_state = "running" if progress_pct >= 95 else "pending"

                timeline([
                    {"name": "Submit", "status": "complete"},
                    {"name": "Queue / Provision", "status": queue_state},
                    {"name": "Execute", "status": execute_state, "details": message or ""},
                    {"name": "Finalize", "status": finalize_state},
                ])

                if run_stage:
                    validation_state = "complete" if run_stage not in {"awaiting_details", "awaiting_approval"} else "active"
                    staging_state = "complete" if run_stage not in {"validating_connection", "preparing_remote_dirs", "transferring_inputs"} else "active"
                    run_state = "active" if run_stage in {"queued", "running", "collecting_outputs"} else ("complete" if run_stage in {"syncing_results", "completed"} else ("failed" if run_stage in {"failed", "cancelled"} else "pending"))
                    if transfer_state in {"outputs_downloaded"}:
                        sync_state = "complete"
                    elif transfer_state in {"downloading_outputs"} or run_stage == "syncing_results":
                        sync_state = "active"
                    elif transfer_state in {"transfer_failed"}:
                        sync_state = "failed"
                    elif run_stage in {"completed"} and result_destination in {"remote", ""}:
                        sync_state = "complete"
                    elif run_stage in {"failed", "cancelled"}:
                        sync_state = "failed"
                    else:
                        sync_state = "pending"
                else:
                    validation_state = "complete" if (progress_pct >= 10 or is_done or is_failed or is_running) else "pending"
                    staging_state = "complete" if (progress_pct >= 30 or is_done) else ("failed" if is_failed and progress_pct < 30 else ("active" if is_running else "pending"))
                    run_state = "complete" if is_done else ("failed" if is_failed else ("active" if is_running and progress_pct >= 30 else "pending"))
                    sync_state = "complete" if is_done else ("failed" if is_failed and progress_pct >= 95 else ("active" if is_running and progress_pct >= 95 else "pending"))

                segmented_progress([
                    {"name": "Validation", "percentage": 15, "status": validation_state},
                    {"name": "Staging", "percentage": 20, "status": staging_state},
                    {"name": "Run", "percentage": 55, "status": run_state},
                    {"name": "Sync", "percentage": 10, "status": sync_state},
                ])

                # Show rsync transfer detail during active sync
                _transfer_detail = (job_status.get("transfer_detail") or "").strip()
                if _transfer_detail and sync_state == "active":
                    info_callout(f"📥 {_transfer_detail}", kind="info")
                
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
                    info_callout(message or "Job failed during execution.", kind="error", icon="❌")
                    if workflow_label:
                        st.caption(f"Failed run: {workflow_label}")
                    with st.expander("Recommended Next Steps", expanded=True):
                        st.markdown("1. Review Nextflow error lines below for first root cause.")
                        st.markdown("2. Verify input path, genome, and execution mode parameters.")
                        st.markdown("3. For remote mode, verify SSH profile and SLURM account/partition.")
                        st.markdown("4. Resubmit after edits.")
                    
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
                                    timestamp = _format_timestamp(log.get("timestamp", "")) or log.get("timestamp", "")
                                    level = log.get("level", "INFO")
                                    msg = log.get("message", "")
                                    
                                    if level == "ERROR":
                                        st.error(f"[{timestamp}] {msg}")
                                    elif level == "WARN":
                                        st.warning(f"[{timestamp}] {msg}")
                                    else:
                                        st.text(f"[{timestamp}] {msg}")
                    
                elif status_str == "CANCELLED":
                    st.warning(f"🛑 {message}")
                    # Show what was running at cancel time
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        completed_count = tasks.get("completed_count", len(completed))
                        total = tasks.get("total", 0)
                        if completed_count or total:
                            st.caption(f"Completed {completed_count}/{total} tasks before cancellation.")

                    # Post-cancel actions: Delete folder / Resubmit
                    st.divider()
                    _work_dir_name = ""
                    if work_directory:
                        import pathlib as _pl
                        _work_dir_name = _pl.PurePosixPath(work_directory).name
                    _del_col, _resub_col = st.columns(2)
                    with _del_col:
                        _del_key = f"del_{block_id}"
                        _confirm_key = f"del_confirm_{block_id}"
                        if st.session_state.get(_confirm_key):
                            st.warning(f"⚠️ This will permanently delete `{_work_dir_name or 'workflow folder'}` and all its files.")
                            _yes_col, _no_col = st.columns(2)
                            with _yes_col:
                                if st.button("Yes, delete", key=f"del_yes_{block_id}", type="primary"):
                                    _pause_auto_refresh(4)
                                    try:
                                        _del_resp = make_authenticated_request(
                                            "DELETE", f"{API_URL}/jobs/{run_uuid}", timeout=30
                                        )
                                        if _del_resp.status_code == 200:
                                            _del_data = _del_resp.json()
                                            st.success(_del_data.get("message", "Deleted."))
                                            st.session_state.pop(_confirm_key, None)
                                            st.rerun()
                                        else:
                                            st.error(f"Delete failed: {_del_resp.status_code} — {_del_resp.text[:200]}")
                                    except Exception as _e:
                                        st.error(f"Error: {_e}")
                            with _no_col:
                                if st.button("No, keep it", key=f"del_no_{block_id}"):
                                    st.session_state.pop(_confirm_key, None)
                                    st.rerun()
                        else:
                            if st.button(f"🗑️ Delete {_work_dir_name or 'Workflow Folder'}", key=_del_key):
                                st.session_state[_confirm_key] = True
                                _pause_auto_refresh(4)
                                st.rerun()
                    with _resub_col:
                        if st.button("🔄 Resubmit Job", key=f"resub_{block_id}"):
                            try:
                                _resub_resp = make_authenticated_request(
                                    "POST", f"{API_URL}/jobs/{run_uuid}/resubmit", timeout=15
                                )
                                if _resub_resp.status_code == 200:
                                    st.success("Approval gate created — scroll down to review and approve.")
                                    st.rerun()
                                else:
                                    st.error(f"Resubmit failed: {_resub_resp.status_code} — {_resub_resp.text[:200]}")
                            except Exception as _e:
                                st.error(f"Error: {_e}")

                elif status_str == "DELETED":
                    st.info(f"🗑️ {message or 'Workflow folder deleted.'}")
                    if work_directory:
                        import pathlib as _pl
                        st.caption(f"Folder `{_pl.PurePosixPath(work_directory).name}` has been removed.")

                elif status_str == "RUNNING":
                    info_callout(message or "Job is running", kind="info", icon="🔄")
                    
                    # Job timing info
                    _job_created = block.get("created_at", "")
                    _last_upd = _job_status_updated_at(
                        st.session_state.get(f"_job_polled_at_{run_uuid}") or content.get("last_updated", ""),
                        live_status_poll_succeeded,
                    )
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
                        progress_stats({
                            "Completed": f"{completed_count}/{total}",
                            "Running": len(running),
                            "Failed": failed_count,
                        })
                        
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
                    # Cancel button
                    st.divider()
                    if st.button("🛑 Cancel Job", type="primary", key=f"cancel_job_{block_id}"):
                        try:
                            _cancel_resp = make_authenticated_request(
                                "POST", f"{API_URL}/jobs/{run_uuid}/cancel", timeout=15
                            )
                            if _cancel_resp.status_code == 200:
                                _cancel_data = _cancel_resp.json()
                                st.success(_cancel_data.get("message", "Job cancelled successfully."))
                                st.rerun()
                            elif _cancel_resp.status_code == 400:
                                st.warning(_cancel_resp.json().get("detail", "Cannot cancel job."))
                            else:
                                st.error(f"Cancel failed: {_cancel_resp.status_code} — {_cancel_resp.text[:200]}")
                        except Exception as _ce:
                            st.error(f"Error cancelling job: {_ce}")
                else:
                    st.warning(f"⏳ Status: {status_str}")
                    if message:
                        st.caption(message)

                if status_str == "FAILED" and run_uuid:
                    st.divider()
                    _work_dir_name = workflow_label or "workflow folder"
                    _delete_col, _resubmit_col = st.columns(2)
                    with _delete_col:
                        _confirm_key = f"del_confirm_{block_id}"
                        if st.session_state.get(_confirm_key):
                            st.warning(f"⚠️ This will permanently delete `{_work_dir_name}` and all its files.")
                            _yes_col, _no_col = st.columns(2)
                            with _yes_col:
                                if st.button("Yes, delete failed run", key=f"failed_del_yes_{block_id}", type="primary"):
                                    _pause_auto_refresh(4)
                                    try:
                                        _del_resp = make_authenticated_request(
                                            "DELETE", f"{API_URL}/jobs/{run_uuid}", timeout=30
                                        )
                                        if _del_resp.status_code == 200:
                                            st.success(_del_resp.json().get("message", "Deleted."))
                                            st.session_state.pop(_confirm_key, None)
                                            st.rerun()
                                        else:
                                            st.error(f"Delete failed: {_del_resp.status_code} — {_del_resp.text[:200]}")
                                    except Exception as _e:
                                        st.error(f"Error: {_e}")
                            with _no_col:
                                if st.button("No, keep it", key=f"failed_del_no_{block_id}"):
                                    st.session_state.pop(_confirm_key, None)
                                    st.rerun()
                        elif st.button(f"🗑️ Delete {_work_dir_name}", key=f"failed_del_btn_{block_id}"):
                            st.session_state[_confirm_key] = True
                            _pause_auto_refresh(4)
                            st.rerun()
                    with _resubmit_col:
                        if st.button("🔄 Resubmit Job", key=f"failed_resub_{block_id}"):
                            try:
                                _resub_resp = make_authenticated_request(
                                    "POST", f"{API_URL}/jobs/{run_uuid}/resubmit", timeout=15
                                )
                                if _resub_resp.status_code == 200:
                                    st.success("Approval gate created — scroll down to review and approve.")
                                    st.rerun()
                                else:
                                    st.error(f"Resubmit failed: {_resub_resp.status_code} — {_resub_resp.text[:200]}")
                            except Exception as _e:
                                st.error(f"Error: {_e}")
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
                            timestamp = _format_timestamp(log.get("timestamp", "")) or log.get("timestamp", "")
                            level = log.get("level", "INFO")
                            msg = log.get("message", "")
                            
                            # Color-code by level
                            if level == "ERROR":
                                st.error(f"[{timestamp}] {msg}")
                            elif level == "WARN":
                                st.warning(f"[{timestamp}] {msg}")
                            else:
                                st.text(f"[{timestamp}] {msg}")
    
    elif btype == "AGENT_PLOT":
        with st.chat_message("assistant", avatar="📊"):
            section_header("Visualization", "Interactive plot generated from analyzed data", icon="📊")
            show_metadata()
            st.divider()
            # Render interactive Plotly charts
            # Pass all currently loaded blocks so the renderer can look up DFs by ID
            all_blocks = st.session_state.get("blocks", [])
            _render_plot_block(content, all_blocks, block_id)

    elif btype == "DOWNLOAD_TASK":
        with st.chat_message("assistant", avatar="⬇️"):
            dl_status = content.get("status", "RUNNING")
            dl_files = content.get("files", [])
            downloaded = content.get("downloaded", 0)
            total = content.get("total_files", len(dl_files))
            total_bytes = content.get("bytes_downloaded", 0)
            expected_total_bytes = content.get("expected_total_bytes", 0)
            source = content.get("source", "url")

            _chip = "pending"
            if dl_status == "RUNNING":
                _chip = "running"
            elif dl_status == "DONE":
                _chip = "complete"
            elif dl_status in ("FAILED", "CANCELLED"):
                _chip = "failed"
            section_header("Download Task", "Track transfer progress and outcomes", icon="⬇️")
            status_chip(_chip, label=dl_status.title(), icon="📦")
            metadata_row({
                "Source": source,
                "Files": f"{downloaded}/{total}",
                "Downloaded": f"{round(total_bytes / (1024 * 1024), 1)} MB" if total_bytes else "0 MB",
            })

            if dl_status == "RUNNING":
                cur = content.get("current_file", "")
                cur_bytes = content.get("current_file_bytes", 0)
                cur_expected = content.get("current_file_expected", 0)

                # Per-file byte progress
                if cur_expected > 0:
                    _pct = min(cur_bytes / cur_expected, 1.0)
                    _cur_mb = round(cur_bytes / (1024 * 1024), 1)
                    _exp_mb = round(cur_expected / (1024 * 1024), 1)
                    st.info(f"⬇️ **Downloading** ({downloaded}/{total} files) — `{cur}` ({_cur_mb}/{_exp_mb} MB)")
                    st.progress(_pct)
                elif expected_total_bytes > 0:
                    # Fallback: overall progress based on total expected bytes
                    _pct = min(total_bytes / expected_total_bytes, 1.0)
                    _dl_mb = round(total_bytes / (1024 * 1024), 1)
                    _exp_mb = round(expected_total_bytes / (1024 * 1024), 1)
                    st.info(f"⬇️ **Downloading** ({downloaded}/{total} files) — `{cur}` ({_dl_mb}/{_exp_mb} MB)")
                    st.progress(_pct)
                else:
                    st.info(f"⬇️ **Downloading** ({downloaded}/{total} files) — `{cur}`")
                    st.progress(downloaded / max(total, 1))

                # Cancel download button
                _dl_id = content.get("download_id")
                if _dl_id:
                    if st.button("🛑 Cancel Download", type="primary", key=f"cancel_dl_{block_id}"):
                        try:
                            _cancel_resp = make_authenticated_request(
                                "DELETE",
                                f"{API_URL}/projects/{active_id}/downloads/{_dl_id}",
                                timeout=15,
                            )
                            if _cancel_resp.status_code == 200:
                                st.success("Download cancellation requested.")
                                st.rerun()
                            elif _cancel_resp.status_code == 404:
                                st.warning("Download not found — it may have already finished or the server was restarted.")
                            else:
                                st.error(f"Cancel failed: {_cancel_resp.status_code} — {_cancel_resp.text[:200]}")
                        except Exception as _ce:
                            st.error(f"Error cancelling download: {_ce}")
            elif dl_status == "DONE":
                mb = round(total_bytes / (1024 * 1024), 2) if total_bytes else 0
                st.success(f"✅ **Download complete** — {downloaded} file(s), {mb} MB")
                # List downloaded files
                for df in content.get("downloaded_files", []):
                    fname = df.get("filename", "?")
                    fsize = df.get("size_bytes")
                    err = df.get("error")
                    if err:
                        st.markdown(f"- ❌ `{fname}` — {err}")
                    elif fsize:
                        st.markdown(f"- ✅ `{fname}` ({round(fsize / (1024*1024), 2)} MB)")
                    else:
                        st.markdown(f"- ✅ `{fname}`")
            elif dl_status == "FAILED":
                msg = content.get("message", "Unknown error")
                st.error(f"❌ **Download failed** — {msg}")
            elif dl_status == "CANCELLED":
                _cancel_msg = content.get("message", f"{downloaded}/{total} files completed")
                _deleted = content.get("deleted_partial")
                _warn_text = f"⚠️ **Download cancelled** — {_cancel_msg}"
                if _deleted:
                    _warn_text += f" Deleted partial file: `{_deleted}`"
                st.warning(_warn_text)

    else:
        with st.chat_message("system", avatar="⚙️"):
            st.code(f"[{btype}] {content}")


# --- 4. MAIN RENDER LOOP ---

# Capture the ID once for this entire run
active_id = st.session_state.active_project_id

# Show project name in title (fall back to truncated UUID)
_active_project_name = active_id[:12] + "…"
for _p in st.session_state.get("_cached_projects", []):
    if _p.get("id") == active_id:
        _active_project_name = _p.get("name", _active_project_name)
        break
st.title(f"🧬 {_active_project_name}")

# Determine if the chat area needs periodic auto-refresh.
# This is evaluated ONCE per full script run; the fragment uses it.
_auto_refresh_suppressed = _auto_refresh_is_suppressed()
_needs_auto_refresh = bool((auto_refresh or st.session_state.get("_has_running_job", False)) and not _auto_refresh_suppressed)
_needs_full_page_auto_refresh = bool(auto_refresh and st.session_state.get("_has_full_refresh_job", False) and not _auto_refresh_suppressed)
_refresh_seconds = min(poll_seconds, 2) if st.session_state.get("_has_running_job", False) else poll_seconds
_refresh_interval = timedelta(seconds=_refresh_seconds) if _needs_auto_refresh else None


@st.fragment(run_every=_refresh_interval)
def _render_chat():
    """Fragment that renders all chat blocks.

    When ``run_every`` is set (job is running), Streamlit re-executes
    ONLY this function on a timer — the sidebar, title, and chat-input
    all stay stable so there is no visible page flash.
    """
    _active_id = st.session_state.active_project_id

    # 1. Fetch & Sanitize
    fetched_blocks, _fetch_ok = get_sanitized_blocks(_active_id)
    fetched_blocks = [b for b in fetched_blocks if b.get("project_id") == _active_id]

    # Only update session-state blocks when the fetch actually succeeded.
    # A transient server error / timeout should NOT wipe the displayed chat.
    if _fetch_ok:
        blocks = fetched_blocks
        st.session_state.blocks = blocks
    else:
        # Keep whatever was previously in session state so the chat stays visible.
        blocks = st.session_state.get("blocks", [])
        if blocks:
            st.caption("⚠️ Could not refresh — showing cached messages")

    if not blocks:
        st.session_state["_has_running_job"] = False
        # Auto-send welcome prompt for empty projects
        if not st.session_state.get("_welcome_sent_for") or st.session_state["_welcome_sent_for"] != _active_id:
            st.session_state["_welcome_sent_for"] = _active_id
            try:
                resp = make_authenticated_request(
                    "POST",
                    f"{API_URL}/chat",
                    json={
                        "project_id": _active_id,
                        "message": "Hello, what can you help me with?",
                        "skill": "welcome",
                        "model": model_choice
                    }
                )
                if resp.status_code == 200:
                    time.sleep(0.5)
                    st.rerun()
            except Exception:
                pass
        st.info(f"👋 **Project `{_active_id}` is empty.**\n\nAsk Agoutic to start a task!")
        return

    # 2. Pagination
    max_visible = st.session_state.get("_max_visible_blocks", 30)
    if len(blocks) > max_visible:
        hidden_count = len(blocks) - max_visible
        st.session_state["_hidden_block_count"] = hidden_count
        visible_blocks = blocks[-max_visible:]
    else:
        st.session_state.pop("_hidden_block_count", None)
        visible_blocks = blocks

    # 3. Scan ALL blocks for running jobs
    _has_running_job = False
    _has_full_refresh_job = False
    _has_pending_submission = False
    _has_finished_job = False
    for blk in blocks:
        btype = blk.get("type")
        bstatus = blk.get("status")
        if _block_requires_full_refresh(blk):
            _has_full_refresh_job = True
        if btype == "EXECUTION_JOB" and bstatus == "RUNNING":
            _has_running_job = True
        if btype == "EXECUTION_JOB" and bstatus in ("DONE", "FAILED"):
            _has_finished_job = True
        # Keep auto-refresh alive while a result transfer is in progress
        # (manual sync on an already-completed job).
        if btype == "EXECUTION_JOB" and bstatus == "DONE":
            _blk_payload = blk.get("payload", {}) if isinstance(blk.get("payload"), dict) else {}
            _blk_js = _blk_payload.get("job_status", {}) if isinstance(_blk_payload.get("job_status"), dict) else {}
            _blk_run_uuid = _blk_payload.get("run_uuid", "")
            _blk_ts = (_blk_js.get("transfer_state") or "").strip().lower()
            _cached_ts = (st.session_state.get(f"_transfer_state_{_blk_run_uuid}") or "").strip().lower() if _blk_run_uuid else ""
            if _blk_ts in {"downloading_outputs"} or _cached_ts in {"downloading_outputs"}:
                _has_running_job = True
        if btype == "STAGING_TASK" and bstatus == "RUNNING":
            _has_running_job = True
        if btype == "STAGING_TASK" and bstatus in ("DONE", "FAILED"):
            _has_finished_job = True
        if btype == "APPROVAL_GATE" and bstatus == "APPROVED":
            _has_pending_submission = True
        if btype == "DOWNLOAD_TASK" and bstatus == "RUNNING":
            _has_running_job = True

    # 4. Render visible blocks
    for blk in visible_blocks:
        render_block(blk, expected_project_id=_active_id)

    # 5. Determine if auto-refresh should stay active
    if _has_pending_submission and not _has_running_job and not _has_finished_job:
        _has_running_job = True

    # Grace window: keep refreshing 30s after completion to catch auto-analysis
    if _has_finished_job and not _has_running_job:
        last_finish = st.session_state.get("_job_finished_at")
        if last_finish is None:
            st.session_state["_job_finished_at"] = time.time()
            _has_running_job = True
        elif time.time() - last_finish < 30:
            _has_running_job = True
    elif _has_running_job:
        st.session_state.pop("_job_finished_at", None)

    st.session_state["_has_running_job"] = _has_running_job
    st.session_state["_has_full_refresh_job"] = _has_full_refresh_job

    # Show refresh indicator inside the fragment
    if _needs_auto_refresh:
        st.caption(
            f"🔄 Live updating "
            f"(last: {datetime.datetime.now().strftime('%H:%M:%S')})"
        )


_render_chat()


@st.fragment(run_every=_refresh_interval)
def _render_task_dock():
    """Render an inline task pane only when the project has tasks."""
    _active_id = st.session_state.active_project_id
    sections = get_project_tasks(_active_id)
    total_tasks = _count_project_tasks(sections)
    st.session_state["_show_task_dock"] = total_tasks > 0
    if total_tasks == 0:
        return

    with st.container(border=True, height=TASK_DOCK_HEIGHT_PX, key="task_dock"):
        render_project_tasks(_active_id, sections=sections, docked=True)


# Pagination button — rendered outside the fragment to avoid duplicate-key
# errors when the fragment's run_every timer overlaps with a full page rerun.
_hbc = st.session_state.get("_hidden_block_count", 0)
if _hbc > 0:
    if st.button(f"⬆️ Load {min(_hbc, 30)} older messages ({_hbc} hidden)"):
        st.session_state["_max_visible_blocks"] = st.session_state.get("_max_visible_blocks", 30) + 30
        st.rerun()

# --- Capture chat input EARLY ---
# st.chat_input is one-shot: its value is only available during the single
# script run triggered by the submit event.  Several code paths below
# (bootstrap rerun, active-chat handler) can call st.rerun() which would
# discard the value before it is processed.  Capture it here — before any
# of those paths — and persist immediately so neither a bootstrap rerun nor
# an active-chat rerun can lose the user's prompt.
_queued_help_prompt = st.session_state.pop("_help_prompt", None)
_captured_prompt = _queued_help_prompt or st.chat_input("Ask Agoutic to do something...")
if _captured_prompt and not st.session_state.get("_pending_prompt"):
    st.session_state["_pending_prompt"] = _captured_prompt

_render_task_dock()

# Bootstrap: if the desired fragment auto-refresh state changed during the
# fragment run, trigger one full rerun so Streamlit re-registers the fragment
# with the new run_every value.
_refresh_now = bool((auto_refresh or st.session_state.get("_has_running_job", False)) and not _auto_refresh_suppressed)
_full_refresh_now = bool(auto_refresh and st.session_state.get("_has_full_refresh_job", False) and not _auto_refresh_suppressed)
if _refresh_now != _needs_auto_refresh or _full_refresh_now != _needs_full_page_auto_refresh:
    time.sleep(0.3)
    st.rerun()

st.write("---")

# 2.5 File Upload (expandable)
with st.expander("📎 Upload files", expanded=False):
    uploaded_files = st.file_uploader(
        "Drop files here to upload to your project's data/ folder",
        accept_multiple_files=True,
        key="file_upload_widget",
    )
    if uploaded_files and st.button("Upload", key="upload_btn"):
        session_token = get_session_cookie()
        cookies = {"session": session_token} if session_token else {}
        files_payload = [
            ("files", (uf.name, uf.getvalue(), uf.type or "application/octet-stream"))
            for uf in uploaded_files
        ]
        try:
            resp = requests.post(
                f"{API_URL}/projects/{active_id}/upload",
                files=files_payload,
                cookies=cookies,
            )
            if resp.status_code == 200:
                result = resp.json()
                st.success(f"✅ Uploaded {result['count']} file(s)")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error(f"Upload failed: {resp.text}")
        except Exception as e:
            st.error(f"Upload error: {e}")

# --- Handle in-flight chat request (non-blocking polling with stop support) ---
_active_chat = st.session_state.get("_active_chat")
if _active_chat is not None:
    _ac_thread = _active_chat["thread"]
    _ac_request_id = _active_chat["request_id"]
    _ac_result = _active_chat["result_holder"]
    _ac_start = _active_chat["start_time"]
    _ac_session_token = _active_chat["session_token"]
    _stage_icons = {
        "waiting": "⏳", "thinking": "🧠", "switching": "🔄",
        "context": "📋", "tools": "🔌", "analyzing": "📊",
        "done": "✅", "cancelled": "⏹️",
    }

    if _ac_thread.is_alive():
        # Thread still running — show progress and stop button
        with st.chat_message("assistant"):
            status_box = st.status("🧠 Processing...", expanded=True)
            elapsed = time.time() - _ac_start
            # Poll backend for stage
            try:
                _cookies = {"session": _ac_session_token} if _ac_session_token else {}
                sr = requests.get(
                    f"{API_URL}/chat/status/{_ac_request_id}",
                    cookies=_cookies, timeout=3,
                )
                if sr.status_code == 200:
                    info = sr.json()
                    stage = info.get("stage", "thinking")
                    detail = info.get("detail", "")
                    icon = _stage_icons.get(stage, "⏳")
                    display = detail or "Processing..."
                    status_box.update(label=f"{icon} {display}")
                    status_box.caption(f"⏱️ {elapsed:.0f}s elapsed")
            except Exception:
                status_box.caption(f"⏱️ {elapsed:.0f}s elapsed")

            if st.button("⏹️ Stop", key="_stop_chat_btn"):
                try:
                    _cookies = {"session": _ac_session_token} if _ac_session_token else {}
                    requests.post(
                        f"{API_URL}/chat/cancel/{_ac_request_id}",
                        cookies=_cookies, timeout=5,
                    )
                except Exception:
                    pass  # Best-effort cancel
                status_box.update(label="⏹️ Stopping...", state="error")

        time.sleep(1.5)
        st.rerun()
    else:
        # Thread finished — process the result
        _ac_thread.join()
        elapsed = time.time() - _ac_start
        del st.session_state["_active_chat"]

        # Detect whether the request failed so "try again" can replay it.
        _chat_failed = bool(
            _ac_result["error"]
            or (_ac_result["response"] is not None and _ac_result["response"].status_code != 200)
        )
        if _chat_failed:
            st.session_state["_last_prompt_failed"] = True

        with st.chat_message("assistant"):
            if _ac_result["error"]:
                st.error(f"Failed to send message: {_ac_result['error']}")
            elif _ac_result["response"] is not None and _ac_result["response"].status_code == 429:
                try:
                    _detail = _ac_result["response"].json().get("detail", {})
                    _used = _detail.get("tokens_used", 0)
                    _limit = _detail.get("token_limit", 0)
                    st.warning(
                        f"**🪙 Token quota exceeded.**\n\n"
                        f"You have used **{_used:,}** of your **{_limit:,}** token limit. "
                        "Please contact an admin to increase your quota."
                    )
                except Exception:
                    st.warning("🪙 You have reached your token limit. Please contact an admin.")
            elif _ac_result["response"] is not None and _ac_result["response"].status_code != 200:
                st.error(
                    f"Chat request failed: {_ac_result['response'].status_code} "
                    f"- {_ac_result['response'].text}"
                )
            else:
                # Success or cancelled — the agent block message will be rendered
                # by the normal block rendering loop after rerun
                _resp_json = _ac_result["response"].json() if _ac_result["response"] else {}
                _status = _resp_json.get("status", "")
                if _status == "cancelled":
                    st.info("⏹️ Stopped by user.")
                else:
                    st.empty()  # Agent block will be rendered by block loop
                    # Clear failure flag — request succeeded.
                    st.session_state.pop("_last_prompt_failed", None)
            time.sleep(0.3)
            st.rerun()

# 3. Chat Input
# The prompt was captured earlier (before bootstrap/active-chat reruns).
# Recover it from the captured variable or from session state if a rerun
# discarded the variable before we got here.
prompt = _captured_prompt
if not prompt and st.session_state.get("_pending_prompt") and "_active_chat" not in st.session_state:
    prompt = st.session_state.get("_pending_prompt")

# "try again" — replay the last prompt that failed.
if prompt and prompt.strip().lower() in ("try again", "retry"):
    _retry_prompt = st.session_state.get("_last_sent_prompt")
    if _retry_prompt and st.session_state.get("_last_prompt_failed"):
        prompt = _retry_prompt
    else:
        # Nothing to retry — show a helpful message and stop.
        with st.chat_message("assistant"):
            st.info("Nothing to retry — there is no recent failed prompt.")
        st.session_state.pop("_pending_prompt", None)
        st.stop()

if prompt:

    with st.chat_message("user"):
        st.write(prompt)

    if _is_help_intent(prompt):
        st.session_state.pop("_pending_prompt", None)
        with st.chat_message("assistant"):
            _render_local_help_response()
        st.stop()

    # --- Start threaded request and store in session state ---
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
                timeout=900,
            )
        except Exception as exc:
            _result_holder["error"] = exc

    thread = threading.Thread(target=_send_chat_request, daemon=True)
    thread.start()

    # Track the last sent prompt so "try again" can replay it on failure.
    st.session_state["_last_sent_prompt"] = prompt
    st.session_state.pop("_last_prompt_failed", None)

    # Clear the pending prompt now that the request is launched.
    st.session_state.pop("_pending_prompt", None)

    st.session_state["_active_chat"] = {
        "thread": thread,
        "request_id": request_id,
        "result_holder": _result_holder,
        "start_time": time.time(),
        "session_token": session_token,
    }
    time.sleep(0.5)
    st.rerun()

# 4. Auto-Refresh suppression bookkeeping
if _auto_refresh_is_suppressed():
    pass
elif auto_refresh and st.session_state.get("_has_full_refresh_job", False):
    # Restore the older double-polling path for active execution/download jobs.
    # This is heavier than fragment-only refresh, but it keeps Nextflow status
    # cards updating reliably.
    time.sleep(min(poll_seconds, 2))
    st.rerun()