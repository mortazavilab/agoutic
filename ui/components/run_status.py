"""
Reusable run-status display component.

Usage:
    from components.run_status import render_run_status
    render_run_status(status_dict)
"""

import streamlit as st
from components.cards import status_chip, metadata_row, section_header
from components.progress import stepper

# Stage → emoji mapping
STAGE_INDICATORS: dict[str, str] = {
    "awaiting_details": "⏳",
    "awaiting_approval": "✋",
    "validating_connection": "🔌",
    "preparing_remote_dirs": "📁",
    "transferring_inputs": "📤",
    "submitting_job": "🚀",
    "queued": "⏳",
    "running": "🏃",
    "collecting_outputs": "📥",
    "syncing_results": "🔄",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}

# Ordered list of stages for progress calculation
_STAGE_ORDER = list(STAGE_INDICATORS.keys())


def _human_label(stage: str) -> str:
    """Convert a snake_case stage name to a human-readable label."""
    return stage.replace("_", " ").title()


def _progress_fraction(stage: str) -> float:
    """Return a 0.0–1.0 progress value based on stage position."""
    try:
        idx = _STAGE_ORDER.index(stage)
    except ValueError:
        return 0.0
    return (idx + 1) / len(_STAGE_ORDER)


def render_run_status(status: dict) -> None:
    """
    Render an enhanced run-status display.

    Args:
        status: Dict containing run status fields such as
                execution_mode, stage, slurm_job_id, slurm_state,
                transfer_state, result_destination, ssh_profile_nickname.
    """
    if not status:
        st.info("No status information available.")
        return

    execution_mode = status.get("execution_mode", "local")
    stage = status.get("stage", "")
    emoji = STAGE_INDICATORS.get(stage, "❓")

    section_header("Run Status", "Live stage overview", icon="🧭")

    mode_label = "Local" if execution_mode == "local" else "HPC3/SLURM"
    _state = "running" if stage in ("running", "submitting_job", "transferring_inputs") else ("failed" if stage == "failed" else ("complete" if stage == "completed" else "pending"))
    status_chip(_state, label=_human_label(stage), icon=emoji)
    metadata_row({"Execution Mode": mode_label})

    # ── Stage + progress ─────────────────────────────────────────────
    cur = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else 0
    stepper([_human_label(s) for s in _STAGE_ORDER[:5]], current=min(cur, 4), completed=[i for i in range(min(cur, 5))])
    st.progress(_progress_fraction(stage), text=f"{_human_label(stage)}")

    # ── Remote-specific fields ───────────────────────────────────────
    if execution_mode != "local":
        slurm_job_id = status.get("slurm_job_id")
        slurm_state = status.get("slurm_state")
        transfer_state = status.get("transfer_state")
        _remote_meta = {}
        if slurm_job_id:
            _remote_meta["SLURM Job ID"] = slurm_job_id
        if slurm_state:
            _remote_meta["SLURM State"] = slurm_state
        if transfer_state:
            _remote_meta["Transfer State"] = transfer_state
        metadata_row(_remote_meta)

    # ── Common metadata ──────────────────────────────────────────────
    _common_meta = {}
    dest = status.get("result_destination")
    if dest:
        _common_meta["Result Destination"] = dest
    profile = status.get("ssh_profile_nickname")
    if profile:
        _common_meta["SSH Profile"] = profile
    metadata_row(_common_meta)
