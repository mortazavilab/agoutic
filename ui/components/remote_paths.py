"""
Reusable remote path configuration form component.

Usage:
    from components.remote_paths import render_remote_paths_form
    paths = render_remote_paths_form(defaults={"remote_work_path": "/scratch/user"})
"""

import streamlit as st
from typing import Optional
from components.cards import section_header
from components.forms import grouped_section, review_panel


def render_remote_paths_form(defaults: Optional[dict] = None) -> Optional[dict]:
    """
    Render a form for configuring remote directory paths.

    Args:
        defaults: Optional dict to pre-fill form values.

    Returns:
        dict with remote path config on submit, or None if not submitted.
    """
    if defaults is None:
        defaults = {}

    section_header("Remote Path Configuration", "Configure execution/storage paths on remote host", icon="📂")

    grouped_section("Storage & Execution Paths")
    remote_input_path = st.text_input(
        "Remote Input Path",
        value=defaults.get("remote_input_path", ""),
        help="Directory on the remote host where input files will be placed",
        key="remote_input_path",
    )
    remote_work_path = st.text_input(
        "Remote Work Path",
        value=defaults.get("remote_work_path", ""),
        help="Working directory on the remote host for job execution",
        key="remote_work_path",
    )
    remote_output_path = st.text_input(
        "Remote Output Path",
        value=defaults.get("remote_output_path", ""),
        help="Directory on the remote host where outputs are written",
        key="remote_output_path",
    )
    remote_log_path = st.text_input(
        "Remote Log Path",
        value=defaults.get("remote_log_path", ""),
        help="Directory on the remote host for log files",
        key="remote_log_path",
    )

    review_panel(
        {
            "Remote Input Path": remote_input_path,
            "Remote Work Path": remote_work_path,
            "Remote Output Path": remote_output_path,
            "Remote Log Path": remote_log_path,
        },
        title="Path Summary",
    )

    if st.button("✅ Apply Remote Paths", key="remote_paths_submit"):
        return {
            "remote_input_path": remote_input_path.strip(),
            "remote_work_path": remote_work_path.strip(),
            "remote_output_path": remote_output_path.strip(),
            "remote_log_path": remote_log_path.strip(),
        }

    return None
