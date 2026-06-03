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
    import re as _re

    q = (message or "").strip().lower()
    if not q:
        return False
    q = _re.sub(r"[?.!,]+$", "", q)
    q = _re.sub(r"\s+", " ", q)
    deterministic = {
        "show local help",
        "open local help",
        "local help",
        "local quick reference",
    }
    return q in deterministic


def _is_share_intent(message: str) -> bool:
    import re as _re

    q = (message or "").strip().lower()
    if not q:
        return False
    q = _re.sub(r"[?.!,]+$", "", q)
    q = _re.sub(r"\s+", " ", q)

    patterns = [
        r"\bshare\b.*\bproject\b",
        r"\badd\b.*\bcollaborator\b",
        r"\bgive\b.*\baccess\b",
        r"\bgrant\b.*\baccess\b",
        r"\bcollaborator\b.*\bproject\b",
    ]
    return any(_re.search(pattern, q) for pattern in patterns)


def _is_list_users_intent(message: str) -> bool:
    import re as _re

    q = (message or "").strip().lower()
    if not q:
        return False
    q = _re.sub(r"[?.!,]+$", "", q)
    q = _re.sub(r"\s+", " ", q)

    deterministic = {
        "list users",
        "list project users",
        "list collaborators",
        "show collaborators",
        "show project users",
        "who is in this project",
        "who is on this project",
        "who has access to this project",
    }
    return q in deterministic


def _project_membership_label(project: dict | None) -> str:
    role = str((project or {}).get("role") or "").strip().lower()
    if role == "owner":
        return "Owned by me"
    if role == "editor":
        return "Shared with me · Editor"
    if role == "viewer":
        return "Shared with me · Viewer"
    return "Shared with me"


def _project_can_mutate(project: dict | None, user: dict | None = None) -> bool:
    user_role = str((user or {}).get("role") or "").strip().lower()
    if user_role == "admin":
        return True
    role = str((project or {}).get("role") or "").strip().lower()
    return role in {"owner", "editor"}


def _project_can_manage_collaborators(project: dict | None, user: dict | None = None) -> bool:
    user_role = str((user or {}).get("role") or "").strip().lower()
    if user_role == "admin":
        return True
    role = str((project or {}).get("role") or "").strip().lower()
    return role == "owner"


def _collaborator_activity_status(collaborator: dict | None, now=None) -> tuple[str, str]:
    import datetime as _dt

    current = now or _dt.datetime.now(_dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_dt.timezone.utc)

    raw_value = str((collaborator or {}).get("last_accessed") or "").strip()
    if not raw_value:
        return "unknown", "No recent activity recorded"

    try:
        parsed = _dt.datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        parsed = parsed.astimezone(_dt.timezone.utc)
    except Exception:
        return "unknown", "No recent activity recorded"

    age_seconds = max((current - parsed).total_seconds(), 0.0)
    if age_seconds <= 300:
        return "active", "Active now"
    if age_seconds <= 3600:
        return "recent", "Active within the last hour"
    if age_seconds <= 86400:
        return "idle", "Active today"
    return "idle", f"Last active {parsed.astimezone().strftime('%Y-%m-%d %H:%M')}"


def _shared_project_activity_warning(collaborators: list[dict] | None, current_user_id: str | None, can_mutate: bool, now=None) -> str | None:
    import datetime as _dt

    current = now or _dt.datetime.now(_dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_dt.timezone.utc)

    active_names: list[str] = []
    for collaborator in collaborators or []:
        collaborator_user_id = str(collaborator.get("user_id") or "").strip()
        if collaborator_user_id and collaborator_user_id == str(current_user_id or "").strip():
            continue

        raw_value = str(collaborator.get("last_accessed") or "").strip()
        if not raw_value:
            continue
        try:
            parsed = _dt.datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            parsed = parsed.astimezone(_dt.timezone.utc)
        except Exception:
            continue
        if max((current - parsed).total_seconds(), 0.0) > 300:
            continue

        label = str(
            collaborator.get("display_name")
            or collaborator.get("username")
            or collaborator.get("email")
            or collaborator_user_id
            or "another collaborator"
        ).strip()
        if label:
            active_names.append(label)

    if not active_names:
        return None

    shown = ", ".join(active_names[:2])
    if len(active_names) > 2:
        shown = f"{shown}, and {len(active_names) - 2} more"

    if can_mutate:
        return f"{shown} active in this project right now. Refresh before renaming, uploading, or continuing shared edits so you do not overwrite each other."
    return f"{shown} active in this project right now. This project is shared, so expect changes from other collaborators."


def _render_local_help_response() -> None:
    section_header("Help", "Quick operational guide for AGOUTIC", icon="❓")
    with st.container(border=True):
        metadata_row({"Focus": "Workflow + DataFrames + DE + Remote HPC", "Mode": "Deterministic Help"})
        st.divider()
        with st.expander("Getting Started", expanded=True):
            st.markdown("1. Pick or create a project in the sidebar.")
            st.markdown("2. Ask for a workflow run using natural language, or use `/help <topic>` to ask how to phrase a request first.")
            st.markdown("3. Review approval parameters and approve to execute.")
        with st.expander("Prompt Coach", expanded=False):
            st.markdown("- Use `/help` for an overview of AGOUTIC prompting patterns.")
            st.markdown("- Use `/help <topic>` for task-specific guidance such as `/help remote slurm`, `/help /haplotype`, `/help /list files`, or `/help remote_execution`.")
            st.markdown("- Natural language also works: `how do I stage a sample on hpc3`, `how do I prompt you to run Dogme with a staged sample`, `how do I sync workflow12 back from the cluster`, `how do I haplotype workflow7 with a VCF`, `how do I haplotype mouse workflow7 without typing the founder VCF path`.")
            st.markdown("- Prompt-coach answers include what to provide, example prompts, useful slash commands, and what AGOUTIC will do internally.")
        with st.expander("Common Actions", expanded=False):
            st.markdown("- run dna workflow for sample X from /path")
            st.markdown("- stage sample to remote slurm profile hpc3")
            st.markdown("- run Dogme on hpc3 using a staged sample and sync results locally")
            st.markdown("- clean workflow7 or clean remote workflow7; cleanup now runs in the background and job status/logs will show `CLEANING_*`, `CLEANED_*`, or `CLEAN_FAILED`")
            st.markdown("- sync workflow12 back from the cluster or resume a previous sync")
            st.markdown("- clean workflow12 locally or clean remote workflows after a cluster run")
            st.markdown("- show my local samples, staged samples, or imported workflows")
            st.markdown("- list workflows or files for the active project")
            st.markdown("- show job status and next steps")
            st.markdown("- parse results for run UUID")
            st.markdown("- compare reconcile abundance samples with edgePython")
            st.markdown("- haplotype workflow BAMs with an indexed VCF")
        with st.expander("Slash Commands", expanded=False):
            st.markdown("- Help: `/help`, `/help <topic>`, `/commands`")
            st.markdown("- Skills: `/skills`, `/skill <skill_key>`, `/use-skill <skill_key>`")
            st.markdown("- Inventory: `/list samples`, `/list staged [--profile NAME]`, `/list imported`, `/list dfs`, `/list workflows`, `/list files [target] [--project] [--depth N]`")
            st.markdown("- Workflows: `/use <workflow>`, `/rerun <workflow>`, `/rename <workflow> <new_name>`, `/delete <workflow>`, `/clean [remote] [workflow[, workflow2, ...]]`")
            st.markdown("- Haplotyping: `/haplotype <DNA|RNA|cDNA> <workflow> [vcf]`")
            st.markdown("- Differential expression: `/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2`")
            st.markdown("- Memory: `/remember`, `/remember-global`, `/remember-df`, `/memories`, `/forget`, `/pin`, `/unpin`, `/restore`, `/annotate`, `/search-memories`, `/upgrade-to-global`")
            st.markdown("- Hyphenated memory commands also accept underscore variants such as `/remember_global`, `/remember_df`, and `/upgrade_global`")
            st.markdown("- Natural language inventory requests also work: `show my samples`, `show staged samples on hpc3`, `what imported samples do i have`, `show workflows in this project`, `list files in workflow7/annot`")
        with st.expander("Execution Modes", expanded=False):
            st.markdown("- Local: run on AGOUTIC host")
            st.markdown("- SLURM: submit via remote profile and queue")
            st.markdown("- Ask `/help remote slurm` for prompting guidance on stage-only prep, full remote submission, and result sync workflows")
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
