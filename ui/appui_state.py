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
        "show slash commands",
        "what slash commands are available",
        "how do i run a workflow",
        "how do i use remote slurm",
        "how do i use dataframes",
        "how do i compare reconcile samples",
        "show dataframe commands",
    }
    return q in deterministic


def _render_local_help_response() -> None:
    section_header("Help", "Quick operational guide for AGOUTIC", icon="❓")
    with st.container(border=True):
        metadata_row({"Focus": "Workflow + DataFrames + DE + Remote HPC", "Mode": "Deterministic Help"})
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
            st.markdown("- compare reconcile abundance samples with edgePython")
        with st.expander("Slash Commands", expanded=False):
            st.markdown("- Skills: `/skills`, `/skill <skill_key>`, `/use-skill <skill_key>`")
            st.markdown("- Workflows: `/use <workflow>`, `/rerun <workflow>`, `/rename <workflow> <new_name>`, `/delete <workflow>`")
            st.markdown("- Differential expression: `/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2`")
            st.markdown("- Memory: `/remember`, `/remember-global`, `/remember-df`, `/memories`, `/forget`, `/pin`, `/unpin`, `/restore`, `/annotate`, `/search-memories`, `/upgrade-to-global`")
            st.markdown("- Hyphenated memory commands also accept underscore variants such as `/remember_global`, `/remember_df`, and `/upgrade_global`")
        with st.expander("Execution Modes", expanded=False):
            st.markdown("- Local: run on AGOUTIC host")
            st.markdown("- SLURM: submit via remote profile and queue")
        with st.expander("Dataframe Commands", expanded=False):
            st.markdown("- `list dfs` lists the dataframes currently available in the chat")
            st.markdown("- `head DF5` or `head DF5 20` previews the first rows of a dataframe")
            st.markdown("- `head c2c12DF` works for remembered named dataframes")
            st.markdown("- You can ask for in-memory dataframe actions in natural language: filter, subset, keep columns, rename columns, sort, melt, group, join, and pivot")
            st.markdown("- Example prompts: `subset DF3 to columns sample, modification, reads`, `rename DF2 columns old_reads to reads`, `summarize DF4 by sample and sum reads`")
            st.markdown("- If AGOUTIC saves a dataframe action for confirmation, use the block's Apply or Dismiss buttons in chat")
        with st.expander("Plotting From Dataframes", expanded=False):
            st.markdown("- Ask naturally: `plot DF5 by assay`, `make a bar chart of DF3 by sample`, `color by sample`")
            st.markdown("- Wide sample tables can auto-melt when you ask for grouped plots such as `color by sample`")
            st.markdown("- Histogram, scatter, bar, box, heatmap, and pie charts render inline in chat")
        with st.expander("Differential Expression From Reconcile Outputs", expanded=False):
            st.markdown("- Use the current workflow abundance table directly: `compare the treated samples treated_1 and treated_2 to the control samples ctrl_1 and ctrl_2`")
            st.markdown("- Run the same comparison from a dataframe: `compare treated_1 and treated_2 to ctrl_1 and ctrl_2 from DF1 at transcript level`")
            st.markdown("- Slash form is also supported: `/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2`")
            st.markdown("- If you omit the groups, AGOUTIC will ask which sample columns belong to each side instead of guessing")
            st.markdown("- Default behavior is gene-level aggregation from `reconciled_abundance.tsv`; ask for transcript level when you want transcript-wise testing")
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
