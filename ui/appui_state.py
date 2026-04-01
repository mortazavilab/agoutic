import datetime
import time

import streamlit as st

from components.cards import metadata_row, section_header


def _slugify_project_name(text: str) -> str:
    """Convert arbitrary text to a slug-friendly project name."""
    import re as _re

    text = text.lower().strip()
    text = _re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:40] or "project"


def _job_status_updated_at(
    persisted_timestamp: str | None,
    live_poll_succeeded: bool,
    now: datetime.datetime | None = None,
) -> str | None:
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
