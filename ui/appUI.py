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
from auth import require_auth, logout_button, make_authenticated_request, build_auth_request_kwargs
from theme import inject_global_css, get_plotly_template
from components.cards import section_header, status_chip, metadata_row, stat_tile, info_callout
from components.progress import stepper, segmented_progress, timeline, progress_stats
from components.forms import review_panel, grouped_section
from appui_state import (
    _auto_refresh_is_suppressed,
    _collaborator_activity_status,
    _is_help_intent,
    _is_list_users_intent,
    _is_share_intent,
    _job_status_updated_at,
    _pause_auto_refresh,
    _project_can_manage_collaborators,
    _project_can_mutate,
    _project_membership_label,
    _render_local_help_response,
    _render_profile_path_template,
    _shared_project_activity_warning,
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
)
from appui_sidebar import render_sidebar
from appui_chat_runtime import handle_active_chat, launch_chat_request, render_file_upload, render_project_collaborator_list, render_project_share_form
from appui_block_part1 import render_block_part1
from appui_block_part2 import render_block_part2

# --- VERSION (standalone for UI-only releases) ---
_VERSION_FILE = _Path(__file__).resolve().parent / "VERSION"
_version_raw = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"
AGOUTIC_VERSION = _version_raw[1:] if _version_raw.lower().startswith("v") else _version_raw

# --- Startup diagnostics (Streamlit runs the script twice on startup, so this prints twice — harmless) ---
_api_url = os.getenv("AGOUTIC_API_URL", "(not set)")
_frontend_url = os.getenv("FRONTEND_URL", "(not set)")
_local_origins = os.getenv("LOCAL_UI_ALLOWED_ORIGINS", "(not set)")

from urllib.parse import urlparse
import socket

_parsed = urlparse(_api_url)
_is_loopback = _parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}

# Also check if the hostname resolves to a local IP (hosted mode on same machine)
_is_local_host = False
if not _is_loopback and _parsed.hostname:
    try:
        addrinfo = socket.getaddrinfo(_parsed.hostname, None)
        for family, _, _, _, sockaddr in addrinfo:
            ip = sockaddr[0]
            if ip.startswith("127.") or ip == "::1":
                _is_local_host = True
                break
    except (socket.gaierror, OSError):
        pass

_mode = "local" if _is_loopback or _is_local_host else "server"

if "startup_diagnostics_printed" not in st.session_state:
    print(f"\n{'='*60}", flush=True)
    print(f"[AGOUTIC] v{AGOUTIC_VERSION}  mode={_mode}", flush=True)
    print(f"[AGOUTIC] AGOUTIC_API_URL={_api_url}", flush=True)
    if _frontend_url != "(not set)":
        print(f"[AGOUTIC] FRONTEND_URL={_frontend_url}", flush=True)
    if _local_origins != "(not set)":
        print(f"[AGOUTIC] LOCAL_UI_ALLOWED_ORIGINS={_local_origins}", flush=True)
    print(f"{'='*60}\n", flush=True)
    st.session_state["startup_diagnostics_printed"] = True

def _resolved_page_project_name() -> str:
    cached_name = str(st.session_state.get("_page_project_name") or "").strip()
    if cached_name:
        return cached_name

    active_id = str(st.session_state.get("active_project_id") or "").strip()
    if not active_id:
        return ""

    for project in st.session_state.get("_cached_projects", []):
        if project.get("id") == active_id:
            return str(project.get("name") or "").strip()
    return ""


def _browser_page_title(project_name: str | None = None) -> str:
    resolved_name = str(project_name if project_name is not None else _resolved_page_project_name()).strip()
    if resolved_name:
        return f"{resolved_name} | AGOUTIC v{AGOUTIC_VERSION}"
    return f"AGOUTIC v{AGOUTIC_VERSION}"

# --- CONFIG ---
# Use environment variable or default to localhost
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")
LIVE_JOB_STATUS_TIMEOUT_SECONDS = float(os.getenv("LIVE_JOB_STATUS_TIMEOUT_SECONDS", "60"))
IDLE_DISCOVERY_POLL_SECONDS = max(int(os.getenv("IDLE_DISCOVERY_POLL_SECONDS", "120") or 120), 5)

st.set_page_config(page_title=_browser_page_title(), layout="wide")
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
    [class*="st-key-task_dock_scope_project_scope_"] [data-testid="stVerticalBlockBorderWrapper"] {
        background: color-mix(in srgb, var(--background-color) 90%, var(--secondary-background-color) 10%);
        border-radius: 0.9rem;
        box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16);
        overflow: hidden;
    }

    [class*="st-key-task_dock_scope_project_scope_"] [data-testid="stMetric"] {
        background: color-mix(in srgb, var(--secondary-background-color) 82%, transparent);
        border-radius: 0.65rem;
        padding: 0.35rem 0.5rem;
    }

    [class*="st-key-task_dock_scope_project_scope_"] [data-testid="stExpander"] {
        border-radius: 0.65rem;
        overflow: hidden;
    }

    [class*="st-key-task_dock_scope_project_scope_"] [data-testid="stVerticalBlock"] > div {
        gap: 0.6rem;
    }

    .maintenance-banner-fixed {
        position: fixed;
        top: 4.15rem;
        left: 20rem;
        right: 1.5rem;
        z-index: 70;
        background: #f0b44d;
        background-color: #f0b44d;
        color: #2f2413;
        opacity: 1;
        isolation: isolate;
        border: 1px solid #c78e2e;
        border-radius: 0.85rem;
        box-shadow: 0 14px 32px rgba(15, 23, 42, 0.22);
        font-size: 0.9rem;
        line-height: 1.35;
        font-weight: 700;
        padding: 0.8rem 1rem;
    }

    .maintenance-banner-spacer {
        width: 100%;
    }

    .project-shared-status-banner-fixed {
        position: fixed;
        top: 4.15rem;
        left: 20rem;
        right: 1.5rem;
        max-width: 52rem;
        z-index: 60;
        background: #181a1b;
        background-color: #181a1b;
        opacity: 1;
        isolation: isolate;
        border: 1px solid #2e3336;
        border-radius: 0.85rem;
        box-shadow: 0 14px 32px rgba(15, 23, 42, 0.28);
        overflow: hidden;
    }

    .project-shared-status-banner-fixed--with-maintenance {
        top: 8.35rem;
    }

    .project-shared-status-banner__summary {
        font-size: 0.82rem;
        line-height: 1.3;
        color: color-mix(in srgb, var(--text-color) 88%, transparent);
        font-weight: 600;
        list-style: none;
        cursor: pointer;
        padding: 0.55rem 0.8rem 0.65rem;
    }

    .project-shared-status-banner__summary::-webkit-details-marker {
        display: none;
    }

    .project-shared-status-banner__summary::after {
        content: "Show collaborators";
        float: right;
        font-size: 0.72rem;
        font-weight: 500;
        color: color-mix(in srgb, var(--text-color) 62%, transparent);
    }

    .project-shared-status-banner-fixed[open] .project-shared-status-banner__summary {
        border-bottom: 1px solid #2e3336;
        margin-bottom: 0;
    }

    .project-shared-status-banner-fixed[open] .project-shared-status-banner__summary::after {
        content: "Hide collaborators";
    }

    .project-shared-status-banner__body {
        padding: 0.45rem 0.8rem 0.7rem;
    }

    .project-shared-status-banner__line {
        font-size: 0.78rem;
        line-height: 1.25;
        color: color-mix(in srgb, var(--text-color) 82%, transparent);
        margin-top: 0.12rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .project-shared-status-banner__warning {
        margin-top: 0.45rem;
        font-size: 0.76rem;
        line-height: 1.3;
        color: #f4d7a1;
        border-top: 1px solid color-mix(in srgb, var(--secondary-background-color) 75%, transparent);
        padding-top: 0.4rem;
    }

    .project-shared-status-banner-spacer {
        width: 100%;
    }

    @media (max-width: 768px) {
        .maintenance-banner-fixed {
            top: 3.7rem;
            left: 0.85rem;
            right: 0.85rem;
        }

        .project-shared-status-banner-fixed {
            top: 3.7rem;
            left: 0.85rem;
            right: 0.85rem;
            max-width: none;
        }

        .project-shared-status-banner-fixed--with-maintenance {
            top: 7.5rem;
        }

        .project-shared-status-banner__summary {
            font-size: 0.76rem;
            padding: 0.42rem 0.6rem 0.5rem;
        }

        .project-shared-status-banner__body {
            padding: 0.35rem 0.6rem 0.55rem;
        }

        .project-shared-status-banner__line,
        .project-shared-status-banner__warning {
            font-size: 0.72rem;
        }
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


def _load_user_ssh_profiles(user_id: str) -> list[dict]:
    return _load_user_ssh_profiles_impl(
        user_id,
        api_url=API_URL,
        request_fn=make_authenticated_request,
    )


def _active_project_slug() -> str:
    active_id = st.session_state.get("active_project_id", "")
    for project in st.session_state.get("_cached_projects", []):
        if project.get("id") == active_id:
            # Prefer the real slug from the server; fall back to slugifying name
            return project.get("slug") or _slugify_project_name(project.get("name") or active_id)
    return _slugify_project_name(active_id)


def get_sanitized_blocks(target_project_id: str, *, before_seq: int = 0, limit: int = 500):
    return _get_sanitized_blocks(
        target_project_id,
        api_url=API_URL,
        request_fn=make_authenticated_request,
        before_seq=before_seq,
        limit=limit,
    )


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


def _project_scope_mount_key(scope_name: str, project_id: str) -> str:
    scope_token = (scope_name or "scope").strip() or "scope"
    project_token = (project_id or "none").strip() or "none"
    return f"{scope_token}_project_scope_{project_token}"


def _task_dock_cache_keys(project_id: str) -> tuple[str, str]:
    project_token = (project_id or "none").strip() or "none"
    return (
        f"_cached_task_sections_{project_token}",
        f"_last_task_check_{project_token}",
    )


def _task_sections_have_active_items(sections: dict) -> bool:
    if not isinstance(sections, dict):
        return False

    for section_name in ("running", "pending"):
        tasks = sections.get(section_name, [])
        if any(isinstance(task, dict) and not task.get("parent_task_id") for task in tasks):
            return True
    return False


def _render_task_dock_sections(project_id: str, sections: dict) -> None:
    sections, hidden_stale = prepare_project_task_sections_for_dock(sections)
    total_tasks = _count_project_tasks(sections)
    st.session_state["_show_task_dock"] = total_tasks > 0
    if total_tasks == 0:
        return

    with st.container(border=True, height=TASK_DOCK_HEIGHT_PX):
        if hidden_stale:
            st.caption(
                f"Hidden {hidden_stale} stale task(s) older than 48h from this project view."
            )
        render_project_tasks(project_id, sections=sections, docked=True)


def _current_project_record(project_id: str) -> dict:
    for project in st.session_state.get("_cached_projects", []):
        if project.get("id") == project_id:
            return project
    return {
        "id": project_id,
        "name": _resolved_page_project_name() or project_id,
        "role": "viewer",
    }


def _finish_project_switch_loading(active_project_id: str) -> None:
    if st.session_state.get("_project_switch_loading_for") == active_project_id:
        st.session_state.pop("_project_switch_loading_for", None)
        st.rerun()


def _project_refresh_interval(
    *,
    auto_refresh: bool,
    poll_seconds: int,
    auto_refresh_suppressed: bool,
    project_switch_loading: bool,
    has_running_job: bool,
    has_full_refresh_job: bool = False,
):
    if project_switch_loading:
        return timedelta(milliseconds=100)

    refresh_seconds = max(int(poll_seconds or 30), 1)
    if has_full_refresh_job:
        refresh_seconds = min(refresh_seconds, 5)
    if has_running_job and auto_refresh_suppressed:
        return timedelta(seconds=refresh_seconds)
    if not (auto_refresh or has_running_job) or auto_refresh_suppressed:
        return None

    return timedelta(seconds=refresh_seconds)


def _project_discovery_interval(
    *,
    auto_refresh: bool,
    auto_refresh_suppressed: bool,
    project_switch_loading: bool,
):
    if project_switch_loading:
        return timedelta(milliseconds=100)

    if auto_refresh_suppressed or not auto_refresh:
        return None

    return timedelta(seconds=IDLE_DISCOVERY_POLL_SECONDS)


def _block_should_render_in_live_fragment(block: dict) -> bool:
    btype = block.get("type")
    bstatus = str(block.get("status") or "").upper()
    content = block.get("payload", {}) if isinstance(block.get("payload"), dict) else {}

    if btype in {"DOWNLOAD_TASK", "STAGING_TASK"}:
        return bstatus == "RUNNING"

    if btype != "EXECUTION_JOB":
        return False

    job_status = content.get("job_status", {}) if isinstance(content.get("job_status"), dict) else {}
    status = str(job_status.get("status") or "").upper()
    transfer_state = str(
        job_status.get("transfer_state")
        or content.get("transfer_state")
        or ""
    ).strip().lower()
    imported_source_kind = str(
        job_status.get("imported_source_kind")
        or content.get("imported_source_kind")
        or ""
    ).strip().lower()

    if bstatus == "RUNNING" or status in {"RUNNING", "PENDING"}:
        return True
    if transfer_state in {"pending_import", "downloading_outputs"}:
        return True
    if (
        imported_source_kind == "slurm"
        and bstatus in {"RUNNING", "DONE"}
        and transfer_state not in {"outputs_downloaded", "transfer_failed", "sync_cancelled", "stale"}
    ):
        return True
    return False


def _project_shared_status_banner_payload(
    *,
    can_manage_collaborators: bool,
    collaborators: list[dict] | None,
    current_user_id: str | None,
    activity_status_fn,
    shared_warning: str | None,
) -> dict | None:
    if not can_manage_collaborators:
        return None

    roster = collaborators or []
    non_owner_count = sum(1 for collaborator in roster if not collaborator.get("is_owner"))
    if non_owner_count <= 0:
        return None

    active_now = sum(
        1
        for collaborator in roster
        if str(collaborator.get("user_id") or "").strip() != str(current_user_id or "").strip()
        and activity_status_fn(collaborator)[0] == "active"
    )

    summary = f"Project collaborators: {len(roster)} total"
    if active_now:
        summary += f" · {active_now} other active now"

    lines = []
    current_user_token = str(current_user_id or "").strip()
    for collaborator in roster:
        label = str(
            collaborator.get("display_name")
            or collaborator.get("username")
            or collaborator.get("email")
            or collaborator.get("user_id")
            or "Unknown user"
        ).strip()
        role_label = "Owner" if collaborator.get("is_owner") else str(collaborator.get("role") or "viewer").title()
        if str(collaborator.get("user_id") or "").strip() == current_user_token:
            role_label = f"{role_label} · You"
        _activity_state, activity_label = activity_status_fn(collaborator)
        lines.append(f"{label} · {role_label} · {activity_label}")

    return {
        "summary": summary,
        "lines": lines,
        "warning": shared_warning,
    }


def _render_project_shared_status_banner(banner_payload: dict | None) -> None:
    if not banner_payload:
        return

    import html as _html

    summary_text = _html.escape(str(banner_payload.get("summary") or "").strip())
    line_markup = "".join(
        f'<div class="project-shared-status-banner__line">{_html.escape(str(line))}</div>'
        for line in (banner_payload.get("lines") or [])
        if str(line or "").strip()
    )
    warning_text = str(banner_payload.get("warning") or "").strip()
    warning_markup = ""
    if warning_text:
        warning_markup = (
            f'<div class="project-shared-status-banner__warning">{_html.escape(warning_text)}</div>'
        )

    spacer_rem = 3.55
    banner_class = "project-shared-status-banner-fixed"
    if banner_payload.get("maintenance_visible"):
        banner_class = f"{banner_class} project-shared-status-banner-fixed--with-maintenance"
    st.markdown(
        (
            f'<div class="project-shared-status-banner-spacer" aria-hidden="true" '
            f'style="height: {spacer_rem:.2f}rem;"></div>'
            f'<details class="{banner_class}">'
            f'<summary class="project-shared-status-banner__summary">{summary_text}</summary>'
            f'<div class="project-shared-status-banner__body">'
            f'{line_markup}'
            f'{warning_markup}'
            f'</div>'
            f'</details>'
        ),
        unsafe_allow_html=True,
    )


def _parse_maintenance_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _maintenance_banner_text(state: dict | None, *, now=None) -> str:
    data = state or {}
    if not data.get("mode"):
        return ""
    message = str(data.get("message") or "").strip() or "AGOUTIC is currently in maintenance mode."
    reference_now = now or datetime.datetime.now(datetime.timezone.utc)
    starts_at = _parse_maintenance_datetime(data.get("starts_at"))
    if starts_at is not None and starts_at > reference_now:
        remaining = max(int((starts_at - reference_now).total_seconds()), 0)
        hours, remainder = divmod(remaining, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{message} Maintenance starts in {hours:02d}:{minutes:02d}:{seconds:02d}."
    return message


def _maintenance_banner_payload(api_url: str, *, now=None) -> dict | None:
    try:
        response = make_authenticated_request("GET", f"{api_url}/admin/maintenance", timeout=3)
    except Exception:
        return None
    if getattr(response, "status_code", 0) != 200:
        return None
    payload = response.json() if callable(getattr(response, "json", None)) else None
    if not isinstance(payload, dict) or not payload.get("mode"):
        return None
    return {
        "text": _maintenance_banner_text(payload, now=now),
        "mode": True,
    }


def _render_maintenance_banner(banner_payload: dict | None) -> None:
    if not banner_payload:
        return

    import html as _html

    text = _html.escape(str(banner_payload.get("text") or "").strip())
    spacer_rem = 4.15
    st.markdown(
        (
            f'<div class="maintenance-banner-spacer" aria-hidden="true" style="height: {spacer_rem:.2f}rem;"></div>'
            f'<div class="maintenance-banner-fixed" role="status" aria-live="polite">{text}</div>'
        ),
        unsafe_allow_html=True,
    )


def _should_bootstrap_suppressed_monitoring(
    *,
    auto_refresh_suppressed: bool,
    project_switch_loading: bool,
    refresh_interval,
    has_running_job: bool,
) -> bool:
    return bool(
        auto_refresh_suppressed
        and not project_switch_loading
        and refresh_interval is None
        and has_running_job
    )


def _should_use_incremental_block_fetch(
    *,
    has_existing_blocks: bool,
    project_switch_loading_for: str | None,
    active_project_id: str,
    has_full_refresh_job: bool,
) -> bool:
    if not has_existing_blocks:
        return False
    if project_switch_loading_for == active_project_id:
        return False
    if has_full_refresh_job:
        return False
    return True


def _refresh_live_download_block(block: dict, project_id: str) -> dict:
    refreshed_block = dict(block or {})
    content = refreshed_block.get("payload", {}) if isinstance(refreshed_block.get("payload"), dict) else {}
    download_id = str(content.get("download_id") or "").strip()
    if not download_id or not project_id:
        return refreshed_block

    try:
        response = make_authenticated_request(
            "GET",
            f"{API_URL}/projects/{project_id}/downloads/{download_id}",
            timeout=10,
        )
    except Exception:
        return refreshed_block

    if getattr(response, "status_code", 0) != 200:
        return refreshed_block

    live_payload = response.json() if callable(getattr(response, "json", None)) else None
    if not isinstance(live_payload, dict):
        return refreshed_block

    merged_payload = dict(content)
    merged_payload.update(live_payload)
    refreshed_block["payload"] = merged_payload
    if live_payload.get("status") is not None:
        refreshed_block["status"] = str(live_payload.get("status") or refreshed_block.get("status") or "")
    return refreshed_block

# Check if we're creating a new project (flag set by New Project button)
if st.session_state.get("_create_new_project", False):
    # Create project via server-side endpoint (server generates UUID)
    _pending_name = st.session_state["_create_new_project"]
    _pending_name = _pending_name if isinstance(_pending_name, str) else None
    _new_proj = _create_project_server_side(name=_pending_name)
    new_id = _new_proj["id"] if isinstance(_new_proj, dict) else _new_proj
    _new_slug = _new_proj.get("slug", "") if isinstance(_new_proj, dict) else ""
    _new_name = _new_proj.get("name", "") if isinstance(_new_proj, dict) else ""
    _creation_error = _new_proj.get("error", "") if isinstance(_new_proj, dict) else ""
    if new_id:
        st.session_state.pop("_project_creation_error", None)
        st.session_state.active_project_id = new_id
        st.session_state["_page_project_name"] = _new_name or (_pending_name or "")
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
    elif _creation_error:
        st.session_state["_project_creation_error"] = f"Could not create project: {_creation_error}"
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
    st.session_state.pop("_max_visible_blocks", None)
    st.session_state.pop("_hidden_block_count", None)
    # Clear project-scoped caches (active-state index, dataframe index, rehydrated DFs)
    try:
        from appui_active_state import clear_active_state, clear_df_index, clear_rehydrated_df_cache
        old_project = st.session_state.get("_last_rendered_project")
        if old_project:
            clear_active_state(old_project)
            clear_df_index(old_project)
            clear_rehydrated_df_cache(old_project)
    except (ImportError, ModuleNotFoundError):
        pass
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
    return _render_md_with_dataframes_impl(
        md,
        block_id,
        section,
        api_url=API_URL,
        project_id=str(st.session_state.get("active_project_id") or "").strip(),
        request_fn=make_authenticated_request,
    )


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


def render_block(block, expected_project_id: str = "", live_job_status_map: dict | None = None):
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
            "STALE": ("warning", "Stale", "⚠️"),
            "CANCELLED": ("warning", "Cancelled", "🛑"),
            "DELETED": ("pending", "Deleted", "🗑️"),
        }
        return mapping.get(normalized, ("pending", normalized.title(), "❓"))

    def _workflow_status_presentation(raw_status: str) -> tuple[str, str, str]:
        normalized = (raw_status or "pending").strip().lower()
        if normalized in {"completed", "complete", "done", "approved"}:
            return "complete", raw_status.replace("_", " ").title(), "✅"
        if normalized == "deleted":
            return "pending", raw_status.replace("_", " ").title(), "🗑️"
        if normalized == "stale":
            return "warning", raw_status.replace("_", " ").title(), "⚠️"
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
        get_cached_job_status=get_cached_job_status,
        show_metadata=show_metadata,
        _workflow_status_presentation=_workflow_status_presentation,
        _format_plan_timestamp=_format_plan_timestamp,
        _format_duration=_format_duration,
        _block_timestamp=_block_timestamp,
        _render_workflow_plot_payload=_render_workflow_plot_payload,
        _render_md_with_dataframes=_render_md_with_dataframes,
        _render_embedded_dataframes=_render_embedded_dataframes,
        _render_step_payload=_render_step_payload,
        _job_status_updated_at=_job_status_updated_at,
        _run_status_label=_run_status_label,
        _format_timestamp=_format_timestamp,
        _workflow_label_from_path=_workflow_label_from_path,
        _pause_auto_refresh=_pause_auto_refresh,
        get_job_debug_info=get_job_debug_info,
        _render_plot_block=_render_plot_block,
        live_job_status_map=live_job_status_map,
    )

    if not handled_part1 and not handled_part2:
        with st.chat_message("system", avatar="⚙️"):
            st.code(f"[{btype}] {content}")

# --- 4. MAIN RENDER LOOP ---

# Capture the ID once for this entire run
active_id = st.session_state.active_project_id

# Show project name in title (fall back to truncated UUID)
_known_project_name = _resolved_page_project_name()
if _known_project_name:
    st.session_state["_page_project_name"] = _known_project_name
_active_project_name = _known_project_name or (active_id[:12] + "…")
_active_project = _current_project_record(active_id)
_can_mutate_active_project = _project_can_mutate(_active_project, user)
_can_manage_active_collaborators = _project_can_manage_collaborators(_active_project, user)
_active_project_access_label = _project_membership_label(_active_project)

# Determine whether the page is in a transient project-switch state.
_auto_refresh_suppressed = _auto_refresh_is_suppressed()
_project_switch_loading = st.session_state.get("_project_switch_loading_for") == active_id
_needs_auto_refresh = bool((auto_refresh or st.session_state.get("_has_running_job", False)) and not _auto_refresh_suppressed)
_refresh_interval = _project_refresh_interval(
    auto_refresh=auto_refresh,
    poll_seconds=poll_seconds,
    auto_refresh_suppressed=_auto_refresh_suppressed,
    project_switch_loading=_project_switch_loading,
    has_running_job=bool(st.session_state.get("_has_running_job", False)),
    has_full_refresh_job=bool(st.session_state.get("_has_full_refresh_job", False)),
)
_discovery_refresh_interval = _project_discovery_interval(
    auto_refresh=auto_refresh,
    auto_refresh_suppressed=_auto_refresh_suppressed,
    project_switch_loading=_project_switch_loading,
)
project_loading_slot = None


@st.fragment(run_every=_refresh_interval)
def _render_live_block_fragment(block: dict, expected_project_id: str = ""):
    project_id = expected_project_id or st.session_state.active_project_id
    live_block = block
    block_id = str(block.get("id") or "")
    btype = block.get("type")
    content = block.get("payload", {}) if isinstance(block.get("payload"), dict) else {}
    live_job_status_map = None

    if btype == "EXECUTION_JOB":
        run_uuid = str(content.get("run_uuid") or "").strip()
        if run_uuid:
            live_job_status, _ = get_cached_job_status(run_uuid)
            if isinstance(live_job_status, dict) and live_job_status:
                live_job_status_map = {run_uuid: live_job_status}
                try:
                    from appui_active_state import update_run_status

                    update_run_status(st.session_state.active_project_id, run_uuid, live_job_status)
                except (ImportError, ModuleNotFoundError):
                    pass
    elif btype == "DOWNLOAD_TASK":
        live_block = _refresh_live_download_block(block, project_id)
        live_content = live_block.get("payload", {}) if isinstance(live_block.get("payload"), dict) else {}
        try:
            from appui_active_state import update_download_status

            update_download_status(project_id, block_id, live_content)
        except (ImportError, ModuleNotFoundError):
            pass

    with st.container():
        render_block(
            live_block,
            expected_project_id=expected_project_id,
            live_job_status_map=live_job_status_map,
        )


@st.fragment(run_every=_discovery_refresh_interval)
def _render_chat():
    """Render all chat blocks for the active project.

    Uses incremental block streaming and active-state tracking to avoid
    refetching and rescanning large project histories on every refresh cycle.
    Only active jobs/downloads/staging tasks are polled; completed/failed/synced
    tasks render from cached data until reactivated.
    """
    _active_id = st.session_state.active_project_id
    with st.container(key=_project_scope_mount_key("chat", _active_id)):
        if st.session_state.get("_project_switch_loading_for") == _active_id:
            st.session_state["_has_running_job"] = False
            st.session_state["_has_full_refresh_job"] = False
            st.session_state.pop("_hidden_block_count", None)
            return

        if project_loading_slot is not None:
            project_loading_slot.empty()

        # Import active-state helpers
        from appui_active_state import (
            get_active_runs_to_poll,
            get_active_downloads_to_poll,
            has_any_active_items,
            merge_blocks_incremental,
            should_block_require_poll,
            update_run_status,
            update_download_status,
            register_active_run,
            register_active_download,
            is_job_active,
            is_download_active,
        )
        from appui_cache_ttl import periodic_cache_maintenance

        # Run periodic cache maintenance to evict expired download/export caches
        periodic_cache_maintenance()

        # 1. Fetch & Sanitize (incremental streaming)
        # Use incremental fetch on subsequent refreshes to avoid refetching large histories.
        # On initial load or project switch, fall back to full fetch for correctness.
        _use_incremental_fetch = _should_use_incremental_block_fetch(
            has_existing_blocks=bool(st.session_state.get("blocks")),
            project_switch_loading_for=st.session_state.get("_project_switch_loading_for"),
            active_project_id=_active_id,
            has_full_refresh_job=bool(st.session_state.get("_has_full_refresh_job", False)),
        )

        if not _use_incremental_fetch:
            # Full fetch on initial load to establish baseline
            fetched_blocks, _fetch_ok, _pagination = get_sanitized_blocks(_active_id)
        else:
            # Incremental fetch for subsequent refreshes (only new blocks since last poll)
            from appui_tasks import get_sanitized_blocks_incremental
            fetched_blocks, _fetch_ok = get_sanitized_blocks_incremental(_active_id, API_URL, make_authenticated_request)
            _pagination = {}
        
        fetched_blocks = [b for b in fetched_blocks if b.get("project_id") == _active_id]

        # Only update session-state blocks when the fetch actually succeeded.
        # A transient server error / timeout should NOT wipe the displayed chat.
        if _fetch_ok:
            existing_blocks = st.session_state.get("blocks", [])
            blocks, _has_new_blocks = merge_blocks_incremental(
                _active_id, existing_blocks, fetched_blocks,
                max_visible=st.session_state.get("_max_visible_blocks", 30),
            )
            st.session_state.blocks = blocks
            if blocks:
                st.session_state[f"_oldest_block_seq_{_active_id}"] = min(
                    int(block.get("seq") or 0) for block in blocks
                )
            if _pagination:
                st.session_state[f"_has_older_blocks_{_active_id}"] = bool(
                    _pagination.get("has_older", False)
                )
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

        # 3. Scan visible blocks for active state updates (lightweight pass)
        _has_running_job = False
        _has_full_refresh_job = False
        _has_pending_submission = False
        _has_finished_job = False

        for blk in visible_blocks:
            btype = blk.get("type")
            bstatus = str(blk.get("status") or "").upper()
            block_id = blk.get("id", "")
            content = blk.get("payload", {}) if isinstance(blk.get("payload"), dict) else {}

            # Check for full refresh requirements (only for active blocks)
            if _block_requires_full_refresh(blk):
                _has_full_refresh_job = True

            # EXECUTION_JOB handling with rerun-aware tracking
            if btype == "EXECUTION_JOB":
                run_uuid = content.get("run_uuid", "")
                job_status = content.get("job_status", {})

                # Prefer the latest fragment-populated status cache so the parent
                # discovery loop doesn't re-activate cards from stale block payloads.
                if run_uuid:
                    cached_status = st.session_state.get(f"_job_status_cache_{run_uuid}")
                    if isinstance(cached_status, dict) and isinstance(cached_status.get("job_status"), dict):
                        job_status = cached_status["job_status"]

                # Update active-state index for this run
                if run_uuid:
                    is_active = update_run_status(_active_id, run_uuid, job_status)
                    if is_active:
                        _has_running_job = True
                elif bstatus == "RUNNING":
                    _has_running_job = True

                if bstatus in ("DONE", "FAILED"):
                    _has_finished_job = True

            # DOWNLOAD_TASK handling
            elif btype == "DOWNLOAD_TASK":
                if is_download_active(content):
                    update_download_status(_active_id, block_id, content)
                    _has_running_job = True

            # STAGING_TASK handling
            elif btype == "STAGING_TASK":
                if bstatus == "RUNNING":
                    _has_running_job = True
                elif bstatus in ("DONE", "FAILED"):
                    _has_finished_job = True

            # APPROVAL_GATE handling
            elif btype == "APPROVAL_GATE" and bstatus == "APPROVED":
                _has_pending_submission = True

        # Also check for any active items not currently visible (e.g., in hidden history)
        if has_any_active_items(_active_id):
            _has_running_job = True

        # 4. Render visible blocks
        for blk in visible_blocks:
            if _block_should_render_in_live_fragment(blk):
                _render_live_block_fragment(blk, expected_project_id=_active_id)
            else:
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

        if _has_running_job and _needs_auto_refresh:
            st.caption(
                f"🔄 Live updating "
                f"(last: {datetime.datetime.now().strftime('%H:%M:%S')})"
            )
        elif _has_running_job:
            refresh_col, detail_col = st.columns([1, 4])
            with refresh_col:
                st.button("Refresh now", key=f"_manual_project_refresh_{_active_id}")
            with detail_col:
                st.caption(
                    f"Live auto-refresh is temporarily paused. "
                    f"Last update: {datetime.datetime.now().strftime('%H:%M:%S')}"
                )

with st.container(key=_project_scope_mount_key("project_panel", active_id)):
    st.html(
        f"<script>window.parent.document.title = {json.dumps(_browser_page_title(_known_project_name))};</script>"
    )
    _active_project_collaborators = []
    try:
        _collab_resp = make_authenticated_request(
            "GET", f"{API_URL}/projects/{active_id}/collaborators", timeout=5
        )
        if _collab_resp.status_code == 200:
            _payload = _collab_resp.json() or {}
            if isinstance(_payload, dict):
                _active_project_collaborators = _payload.get("collaborators", []) or []
    except Exception:
        _active_project_collaborators = []

    _shared_activity_warning = _shared_project_activity_warning(
        _active_project_collaborators,
        user.get("id"),
        _can_mutate_active_project,
    )
    _shared_status_banner = _project_shared_status_banner_payload(
        can_manage_collaborators=_can_manage_active_collaborators,
        collaborators=_active_project_collaborators,
        current_user_id=user.get("id"),
        activity_status_fn=_collaborator_activity_status,
        shared_warning=_shared_activity_warning,
    )
    _maintenance_banner = _maintenance_banner_payload(API_URL)
    _render_maintenance_banner(_maintenance_banner)
    st.title(f"🧬 {_active_project_name}")
    status_chip(
        "info" if (_active_project.get("role") == "owner" or user.get("role") == "admin") else "warning",
        label=_active_project_access_label,
        icon="🏠" if _active_project.get("role") == "owner" else "🤝",
    )
    if _shared_status_banner:
        _shared_status_banner = {
            **_shared_status_banner,
            "maintenance_visible": bool(_maintenance_banner),
        }
    _render_project_shared_status_banner(_shared_status_banner)
    project_loading_slot = st.empty()
    if _project_switch_loading:
        with project_loading_slot.container():
            st.info(f"Loading project `{active_id}`...")
    _render_chat()


@st.fragment(run_every=_refresh_interval)
def _render_live_task_dock(project_id: str):
    if st.session_state.get("_project_switch_loading_for") == project_id:
        st.session_state["_show_task_dock"] = False
        return

    cache_key, checked_key = _task_dock_cache_keys(project_id)
    sections = get_project_tasks(project_id)
    st.session_state[cache_key] = sections
    st.session_state[checked_key] = time.time()
    _render_task_dock_sections(project_id, sections)


@st.fragment(run_every=_discovery_refresh_interval)
def _render_task_dock():
    """Render an inline task pane only when the project has tasks.

    Optimized to reduce polling frequency when there are no active tasks,
    avoiding unnecessary API calls on idle projects.
    """
    _active_id = st.session_state.active_project_id
    with st.container(key=_project_scope_mount_key("task_dock_scope", _active_id)):
        if st.session_state.get("_project_switch_loading_for") == _active_id:
            st.session_state["_show_task_dock"] = False
            return

        from appui_active_state import has_any_active_items
        _has_active = has_any_active_items(_active_id)

        cache_key, checked_key = _task_dock_cache_keys(_active_id)
        sections = st.session_state.get(cache_key, {})
        _last_task_check = float(st.session_state.get(checked_key, 0) or 0)
        if not isinstance(sections, dict) or not sections or (time.time() - _last_task_check) >= IDLE_DISCOVERY_POLL_SECONDS or _has_active:
            sections = get_project_tasks(_active_id)
            st.session_state[cache_key] = sections
            st.session_state[checked_key] = time.time()

        if _has_active or _task_sections_have_active_items(sections):
            _render_live_task_dock(_active_id)
            return

        _render_task_dock_sections(_active_id, sections)


def _render_history_pagination(project_id: str) -> None:
    """Render chat history pagination independently of task-dock state."""
    _hbc = st.session_state.get("_hidden_block_count", 0)
    _has_server_history = st.session_state.get(f"_has_older_blocks_{project_id}", False)
    if _hbc > 0 or _has_server_history:
        _load_label = (
            f"⬆️ Load {min(_hbc, 30)} older messages ({_hbc} hidden)"
            if _hbc > 0
            else "⬆️ Load 30 older messages"
        )
        if st.button(_load_label):
            if _hbc > 0:
                st.session_state["_max_visible_blocks"] = st.session_state.get("_max_visible_blocks", 30) + 30
            else:
                _oldest_seq = int(st.session_state.get(f"_oldest_block_seq_{project_id}", 0) or 0)
                _older_blocks, _older_fetch_ok, _older_pagination = get_sanitized_blocks(
                    project_id,
                    before_seq=_oldest_seq,
                )
                if _older_fetch_ok and _older_blocks:
                    _combined_by_id = {
                        block.get("id"): block
                        for block in [*_older_blocks, *st.session_state.get("blocks", [])]
                    }
                    st.session_state.blocks = sorted(
                        _combined_by_id.values(),
                        key=lambda block: int(block.get("seq") or 0),
                    )
                    st.session_state[f"_oldest_block_seq_{project_id}"] = min(
                        int(block.get("seq") or 0)
                        for block in st.session_state.blocks
                    )
                    st.session_state[f"_has_older_blocks_{project_id}"] = bool(
                        _older_pagination.get("has_older", False)
                    )
                    st.session_state["_max_visible_blocks"] = st.session_state.get("_max_visible_blocks", 30) + 30
                elif not _older_fetch_ok:
                    st.warning("Could not load older messages. Please try again.")
            st.rerun()

# --- Capture chat input EARLY ---
# st.chat_input is one-shot: its value is only available during the single
# script run triggered by the submit event.  Several code paths below
# (bootstrap rerun, active-chat handler) can call st.rerun() which would
# discard the value before it is processed.  Capture it here — before any
# of those paths — and persist immediately so neither a bootstrap rerun nor
# an active-chat rerun can lose the user's prompt.
_queued_help_prompt = st.session_state.pop("_help_prompt", None)
_chat_prompt_placeholder = "Ask Agoutic to do something..." if _can_mutate_active_project else "Viewer access is read-only in this shared project."
_captured_prompt = _queued_help_prompt or st.chat_input(_chat_prompt_placeholder, disabled=not _can_mutate_active_project)
if _captured_prompt and not st.session_state.get("_pending_prompt"):
    st.session_state["_pending_prompt"] = _captured_prompt

_render_task_dock()
_render_history_pagination(active_id)

if _should_bootstrap_suppressed_monitoring(
    auto_refresh_suppressed=_auto_refresh_suppressed,
    project_switch_loading=_project_switch_loading,
    refresh_interval=_refresh_interval,
    has_running_job=bool(st.session_state.get("_has_running_job", False)),
):
    time.sleep(0.3)
    st.rerun()

_refresh_now = bool((auto_refresh or st.session_state.get("_has_running_job", False)) and not _auto_refresh_suppressed)
if _refresh_now != _needs_auto_refresh:
    time.sleep(0.3)
    st.rerun()

_finish_project_switch_loading(active_id)

st.write("---")

# 2.5 File Upload (expandable)
render_file_upload(
    api_url=API_URL,
    active_id=active_id,
    build_auth_request_kwargs_fn=build_auth_request_kwargs,
    disabled=not _can_mutate_active_project,
    disabled_reason="Viewer access is read-only in this shared project.",
)

# --- Handle in-flight chat request (non-blocking polling with stop support) ---
handle_active_chat(api_url=API_URL, active_project_id=active_id)
render_project_share_form(
    api_url=API_URL,
    active_project=_active_project,
    user=user,
    build_auth_request_kwargs_fn=build_auth_request_kwargs,
)

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

    if _is_list_users_intent(prompt):
        st.session_state.pop("_pending_prompt", None)
        render_project_collaborator_list(
            prompt=prompt,
            active_project=_active_project,
            collaborators=_active_project_collaborators,
            current_user_id=user.get("id"),
            activity_status_fn=_collaborator_activity_status,
        )
        st.stop()

    if _is_share_intent(prompt):
        st.session_state.pop("_pending_prompt", None)
        st.session_state.pop("_share_form_feedback", None)
        if _can_manage_active_collaborators:
            st.session_state["_pending_share_form"] = {
                "project_id": active_id,
                "project_name": _active_project_name,
                "prompt": prompt,
                "email": "",
                "role": "viewer",
                "error": None,
            }
        else:
            st.session_state["_share_form_feedback"] = {
                "project_id": active_id,
                "status": "error",
                "message": "Only the project owner or an admin can manage collaborators for this project.",
            }
        st.rerun()

    if not _can_mutate_active_project:
        st.session_state.pop("_pending_prompt", None)
        with st.chat_message("assistant"):
            st.info("Viewer access is read-only in this shared project. Chat submission, uploads, and other mutating actions are disabled.")
        st.stop()

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
        build_auth_request_kwargs_fn=build_auth_request_kwargs,
    )
