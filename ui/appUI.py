import time
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
from appui_state import (
    _auto_refresh_is_suppressed,
    _is_help_intent,
    _job_status_updated_at,
    _pause_auto_refresh,
    _render_local_help_response,
    _render_profile_path_template,
    _slugify_project_name,
)
from appui_tasks import (
    apply_task_action as _apply_task_action,
    prepare_project_task_sections_for_dock as _prepare_project_task_sections_for_dock_impl,
    get_project_tasks as _get_project_tasks,
    get_sanitized_blocks as _get_sanitized_blocks,
    render_project_tasks as _render_project_tasks,
    _count_project_tasks as _count_project_tasks_impl,
)
from appui_renderers import (
    _build_plotly_figure as _build_plotly_figure_impl,
    _render_embedded_dataframes as _render_embedded_dataframes_impl,
    _render_md_with_dataframes as _render_md_with_dataframes_impl,
    _render_plot_block as _render_plot_block_impl,
    _render_workflow_plot_payload as _render_workflow_plot_payload_impl,
    _resolve_df_by_id as _resolve_df_by_id_impl,
    _resolve_payload_df_by_id as _resolve_payload_df_by_id_impl,
)
from appui_services import (
    _block_requires_full_refresh as _block_requires_full_refresh_impl,
    _find_related_workflow_plan as _find_related_workflow_plan_impl,
    _workflow_highlight_steps as _workflow_highlight_steps_impl,
    create_project_server_side as _create_project_server_side_impl,
    get_cached_job_status as _get_cached_job_status_impl,
    get_job_debug_info as _get_job_debug_info_impl,
    load_user_ssh_profiles as _load_user_ssh_profiles_impl,
    launchpad_headers as _launchpad_headers_impl,
)
from appui_sidebar import render_sidebar
from appui_chat_runtime import handle_active_chat, launch_chat_request, render_file_upload
from appui_block_part1 import render_block_part1
from appui_block_part2 import render_block_part2

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
def _create_project_server_side(name: str = None) -> dict:
    return _create_project_server_side_impl(name, API_URL, make_authenticated_request)


def _launchpad_headers() -> dict:
    return _launchpad_headers_impl(INTERNAL_API_SECRET)


def _load_user_ssh_profiles(user_id: str) -> list[dict]:
    return _load_user_ssh_profiles_impl(
        user_id,
        launchpad_url=LAUNCHPAD_URL,
        request_fn=make_authenticated_request,
        internal_api_secret=INTERNAL_API_SECRET,
    )


def _active_project_slug() -> str:
    active_id = st.session_state.get("active_project_id", "")
    for project in st.session_state.get("_cached_projects", []):
        if project.get("id") == active_id:
            # Prefer the real slug from the server; fall back to slugifying name
            return project.get("slug") or _slugify_project_name(project.get("name") or active_id)
    return _slugify_project_name(active_id)


def get_sanitized_blocks(target_project_id: str):
    return _get_sanitized_blocks(target_project_id, api_url=API_URL, request_fn=make_authenticated_request)


def get_project_tasks(project_id: str):
    return _get_project_tasks(project_id, api_url=API_URL, request_fn=make_authenticated_request)


def apply_task_action(project_id: str, task_id: str, action: str) -> bool:
    return _apply_task_action(project_id, task_id, action, api_url=API_URL, request_fn=make_authenticated_request)


def _count_project_tasks(sections: dict) -> int:
    return _count_project_tasks_impl(sections, TASK_SECTION_ORDER)


def render_project_tasks(project_id: str, *, sections: dict | None = None, docked: bool = False) -> int:
    return _render_project_tasks(
        project_id,
        API_URL,
        make_authenticated_request,
        TASK_SECTION_ORDER,
        sections=sections,
        docked=docked,
    )


def prepare_project_task_sections_for_dock(
    sections: dict,
    section_order: list[tuple[str, str]] | None = None,
    *,
    stale_hide_hours: float | None = None,
) -> tuple[dict, int]:
    return _prepare_project_task_sections_for_dock_impl(
        sections,
        section_order or TASK_SECTION_ORDER,
        stale_hide_hours=stale_hide_hours,
    )

# Check if we're creating a new project (flag set by New Project button)
if st.session_state.get("_create_new_project", False):
    # Create project via server-side endpoint (server generates UUID)
    _pending_name = st.session_state["_create_new_project"]
    _pending_name = _pending_name if isinstance(_pending_name, str) else None
    _new_proj = _create_project_server_side(name=_pending_name)
    new_id = _new_proj["id"] if isinstance(_new_proj, dict) else _new_proj
    _new_slug = _new_proj.get("slug", "") if isinstance(_new_proj, dict) else ""
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
    if _new_slug and _new_slug != _slugify_project_name(_pending_name or ""):
        st.toast(f"Created project — folder: {_new_slug}")
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
                _p = _create_project_server_side()
                st.session_state.active_project_id = _p["id"] if isinstance(_p, dict) else _p
        else:
            _p = _create_project_server_side()
            st.session_state.active_project_id = _p["id"] if isinstance(_p, dict) else _p
    except:
        _p = _create_project_server_side()
        st.session_state.active_project_id = _p["id"] if isinstance(_p, dict) else _p
    
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
model_choice, auto_refresh, poll_seconds, debug_mode = render_sidebar(
    user=user,
    api_url=API_URL,
    agoutic_version=AGOUTIC_VERSION,
    request_fn=make_authenticated_request,
    logout_button=logout_button,
    pause_auto_refresh=_pause_auto_refresh,
    slugify_project_name=_slugify_project_name,
)


# --- 3. LOGIC ---

def get_job_debug_info(run_uuid):
    data, error = _get_job_debug_info_impl(
        run_uuid,
        api_url=API_URL,
        request_fn=make_authenticated_request,
    )
    if error:
        st.error(f"Failed to fetch debug info: {error}")
    return data


def get_cached_job_status(run_uuid: str):
    return _get_cached_job_status_impl(
        run_uuid,
        api_url=API_URL,
        request_fn=make_authenticated_request,
        timeout_seconds=LIVE_JOB_STATUS_TIMEOUT_SECONDS,
    )


def _render_md_with_dataframes(md: str, block_id: str, section: str):
    return _render_md_with_dataframes_impl(md, block_id, section)


def _render_embedded_dataframes(dfs: dict, block_id: str, *, only_visible: bool = True):
    return _render_embedded_dataframes_impl(dfs, block_id, only_visible=only_visible)


def _resolve_df_by_id(df_id: int, all_blocks: list):
    return _resolve_df_by_id_impl(df_id, all_blocks)


def _resolve_payload_df_by_id(df_id: int, dfs: dict):
    return _resolve_payload_df_by_id_impl(df_id, dfs)


def _find_related_workflow_plan(agent_block: dict, all_blocks: list):
    return _find_related_workflow_plan_impl(agent_block, all_blocks)


def _workflow_highlight_steps(workflow_block: dict) -> list[dict]:
    return _workflow_highlight_steps_impl(workflow_block)


def _block_requires_full_refresh(block: dict) -> bool:
    return _block_requires_full_refresh_impl(block)


def _build_plotly_figure(chart_spec: dict, df: pd.DataFrame, df_label: str):
    return _build_plotly_figure_impl(chart_spec, df, df_label, PLOTLY_TEMPLATE)


def _render_plot_block(payload: dict, all_blocks: list, block_id: str):
    return _render_plot_block_impl(payload, all_blocks, block_id, PLOTLY_TEMPLATE)


def _render_workflow_plot_payload(payload: dict, block_id: str, step_suffix: str):
    return _render_workflow_plot_payload_impl(payload, block_id, step_suffix, PLOTLY_TEMPLATE)


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
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
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
            # Treat naive values from the API as UTC, then convert to local time.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
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
        end_dt = _parse_timestamp(end_value) or datetime.datetime.now().astimezone()
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

    handled_part1 = render_block_part1(
        btype=btype,
        block=block,
        content=content,
        status=status,
        block_id=block_id,
        user=user,
        API_URL=API_URL,
        LIVE_JOB_STATUS_TIMEOUT_SECONDS=LIVE_JOB_STATUS_TIMEOUT_SECONDS,
        make_authenticated_request=make_authenticated_request,
        get_cached_job_status=get_cached_job_status,
        _render_md_with_dataframes=_render_md_with_dataframes,
        _render_embedded_dataframes=_render_embedded_dataframes,
        _find_related_workflow_plan=_find_related_workflow_plan,
        _workflow_highlight_steps=_workflow_highlight_steps,
        _render_workflow_plot_payload=_render_workflow_plot_payload,
        show_metadata=show_metadata,
        _load_user_ssh_profiles=_load_user_ssh_profiles,
        _active_project_slug=_active_project_slug,
        _slugify_project_name=_slugify_project_name,
        _render_profile_path_template=_render_profile_path_template,
        _block_timestamp=_block_timestamp,
    )

    handled_part2 = render_block_part2(
        btype=btype,
        block=block,
        content=content,
        block_id=block_id,
        status=status,
        API_URL=API_URL,
        active_id=st.session_state.active_project_id,
        LIVE_JOB_STATUS_TIMEOUT_SECONDS=LIVE_JOB_STATUS_TIMEOUT_SECONDS,
        make_authenticated_request=make_authenticated_request,
        show_metadata=show_metadata,
        _workflow_status_presentation=_workflow_status_presentation,
        _format_plan_timestamp=_format_plan_timestamp,
        _format_duration=_format_duration,
        _block_timestamp=_block_timestamp,
        _render_workflow_plot_payload=_render_workflow_plot_payload,
        _render_step_payload=_render_step_payload,
        _job_status_updated_at=_job_status_updated_at,
        _run_status_label=_run_status_label,
        _format_timestamp=_format_timestamp,
        _workflow_label_from_path=_workflow_label_from_path,
        _pause_auto_refresh=_pause_auto_refresh,
        get_job_debug_info=get_job_debug_info,
        _render_plot_block=_render_plot_block,
    )

    if not handled_part1 and not handled_part2:
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

    # Keep session alive while jobs are running (heartbeat every 5 min)
    if _has_running_job:
        _last_hb = st.session_state.get("_last_heartbeat", 0)
        if time.time() - _last_hb > 300:
            try:
                make_authenticated_request("POST", f"{API_URL}/auth/heartbeat", timeout=5)
                st.session_state["_last_heartbeat"] = time.time()
            except Exception:
                pass

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
    sections, hidden_stale = prepare_project_task_sections_for_dock(sections)
    total_tasks = _count_project_tasks(sections)
    st.session_state["_show_task_dock"] = total_tasks > 0
    if total_tasks == 0:
        return

    with st.container(border=True, height=TASK_DOCK_HEIGHT_PX, key="task_dock"):
        if hidden_stale:
            st.caption(
                f"Hidden {hidden_stale} stale task(s) older than 48h from this project view."
            )
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
render_file_upload(api_url=API_URL, active_id=active_id, get_session_cookie_fn=get_session_cookie)

# --- Handle in-flight chat request (non-blocking polling with stop support) ---
handle_active_chat(api_url=API_URL)

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

    launch_chat_request(
        api_url=API_URL,
        active_id=active_id,
        prompt=prompt,
        model_choice=model_choice,
        get_session_cookie_fn=get_session_cookie,
    )

# 4. Auto-Refresh suppression bookkeeping
if _auto_refresh_is_suppressed():
    pass
elif auto_refresh and st.session_state.get("_has_full_refresh_job", False):
    # Restore the older double-polling path for active execution/download jobs.
    # This is heavier than fragment-only refresh, but it keeps Nextflow status
    # cards updating reliably.
    time.sleep(min(poll_seconds, 2))
    st.rerun()