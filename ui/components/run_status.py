"""
Reusable run-status display component.

Usage:
    from components.run_status import render_run_status
    render_run_status(status_dict)
"""

import streamlit as st

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

    # ── Execution mode badge ─────────────────────────────────────────
    mode_label = "🖥️ Local" if execution_mode == "local" else "🖧 HPC3/SLURM"
    st.markdown(f"**Execution Mode:** `{mode_label}`")

    # ── Stage + progress ─────────────────────────────────────────────
    col1, col2 = st.columns([2, 3])
    with col1:
        st.metric("Stage", f"{emoji} {_human_label(stage)}")
    with col2:
        st.progress(_progress_fraction(stage), text=f"{_human_label(stage)}")

    # ── Remote-specific fields ───────────────────────────────────────
    if execution_mode != "local":
        r_cols = st.columns(3)

        slurm_job_id = status.get("slurm_job_id")
        slurm_state = status.get("slurm_state")
        transfer_state = status.get("transfer_state")

        with r_cols[0]:
            if slurm_job_id:
                st.metric("SLURM Job ID", slurm_job_id)
        with r_cols[1]:
            if slurm_state:
                st.metric("SLURM State", slurm_state)
        with r_cols[2]:
            if transfer_state:
                st.metric("Transfer State", transfer_state)

    # ── Common metadata ──────────────────────────────────────────────
    meta_cols = st.columns(2)
    with meta_cols[0]:
        dest = status.get("result_destination")
        if dest:
            st.markdown(f"**Result Destination:** `{dest}`")
    with meta_cols[1]:
        profile = status.get("ssh_profile_nickname")
        if profile:
            st.markdown(f"**SSH Profile:** `{profile}`")
