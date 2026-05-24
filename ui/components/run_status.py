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


def _format_usage_duration(seconds) -> str:
    try:
        total_seconds = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return ""
    if total_seconds <= 0:
        return ""
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_usage_memory_mb(megabytes) -> str:
    try:
        value = float(megabytes or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value >= 1024.0:
        return f"{value / 1024.0:.1f} GB"
    return f"{value:.0f} MB"


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
    workflow_usage = status.get("workflow_usage") or {}
    usage_message = ""
    if isinstance(workflow_usage, dict):
        cpu_time = _format_usage_duration(workflow_usage.get("cpu_seconds"))
        if cpu_time:
            _common_meta["CPU Time"] = cpu_time
        actual_gpu_seconds = workflow_usage.get("gpu_seconds")
        gpu_time = _format_usage_duration(actual_gpu_seconds if actual_gpu_seconds not in (None, "") else workflow_usage.get("estimated_gpu_task_seconds"))
        if gpu_time:
            _common_meta["GPU Time" if actual_gpu_seconds not in (None, "") else "GPU Task Time"] = gpu_time
        peak_rss = _format_usage_memory_mb(workflow_usage.get("max_rss_mb"))
        if peak_rss:
            _common_meta["Peak RSS"] = peak_rss
        billing_entries = workflow_usage.get("billing_entries")
        billing_by_account = workflow_usage.get("billing_hours_by_account")
        if isinstance(billing_entries, list) and billing_entries:
            for entry in billing_entries:
                if not isinstance(entry, dict):
                    continue
                billing_hours = entry.get("billing_hours")
                if billing_hours in (None, ""):
                    continue
                resource_type = str(entry.get("resource_type") or "").strip().upper()
                account_name = str(entry.get("account") or "").strip()
                prefix = "GPU" if resource_type == "GPU" else "CPU" if resource_type == "CPU" else ""
                label = f"{prefix} Billing Hours".strip() or "Billing Hours"
                if account_name:
                    label = f"{label} ({account_name})"
                _common_meta[label] = billing_hours
        elif isinstance(billing_by_account, dict) and billing_by_account:
            for account_name, billing_hours in sorted(billing_by_account.items()):
                if billing_hours in (None, ""):
                    continue
                label = f"Billing Hours ({str(account_name).strip() or 'unknown'})"
                _common_meta[label] = billing_hours
        else:
            billing_units = workflow_usage.get("billing_units")
            if billing_units not in (None, ""):
                label = str(workflow_usage.get("billing_label") or "Billing Units").strip() or "Billing Units"
                _common_meta[label] = billing_units
        usage_message = str(workflow_usage.get("usage_message") or "").strip()
    metadata_row(_common_meta)
    if usage_message:
        st.caption(usage_message)
