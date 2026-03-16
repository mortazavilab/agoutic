"""
Reusable SLURM resource configuration form component.

Usage:
    from components.slurm_form import render_slurm_form
    result = render_slurm_form(defaults={"cpus": 8, "memory_gb": 32})
"""

import streamlit as st
from typing import Optional


def render_slurm_form(defaults: Optional[dict] = None) -> Optional[dict]:
    """
    Render a SLURM resource configuration form.

    Args:
        defaults: Optional dict to pre-fill form values.

    Returns:
        dict with SLURM config on submit, or None if not submitted / validation fails.
    """
    if defaults is None:
        defaults = {}

    st.subheader("⚙️ SLURM Resource Configuration")

    account = st.text_input(
        "Account *",
        value=defaults.get("account", ""),
        help="SLURM account/allocation name",
        key="slurm_account",
    )
    partition = st.text_input(
        "Partition *",
        value=defaults.get("partition", ""),
        help="SLURM partition (e.g. standard, gpu)",
        key="slurm_partition",
    )

    col1, col2 = st.columns(2)
    with col1:
        cpus = st.number_input(
            "CPUs",
            min_value=1,
            max_value=128,
            value=defaults.get("cpus", 4),
            key="slurm_cpus",
        )
    with col2:
        memory_gb = st.number_input(
            "Memory (GB)",
            min_value=1,
            max_value=1024,
            value=defaults.get("memory_gb", 16),
            key="slurm_memory_gb",
        )

    walltime = st.text_input(
        "Wall Time (HH:MM:SS)",
        value=defaults.get("walltime", "04:00:00"),
        key="slurm_walltime",
    )

    col3, col4 = st.columns(2)
    with col3:
        gpus = st.number_input(
            "GPUs",
            min_value=0,
            max_value=8,
            value=defaults.get("gpus", 0),
            key="slurm_gpus",
        )
    with col4:
        gpu_type = ""
        if gpus > 0:
            gpu_type = st.text_input(
                "GPU Type",
                value=defaults.get("gpu_type", ""),
                help="e.g. A100, V100",
                key="slurm_gpu_type",
            )

    if st.button("✅ Apply SLURM Config", key="slurm_submit"):
        # Validation
        errors = []
        if not account.strip():
            errors.append("Account is required.")
        if not partition.strip():
            errors.append("Partition is required.")
        if walltime and walltime.count(":") != 2:
            errors.append("Wall time must be in HH:MM:SS format.")

        if errors:
            for err in errors:
                st.error(err)
            return None

        result = {
            "account": account.strip(),
            "partition": partition.strip(),
            "cpus": cpus,
            "memory_gb": memory_gb,
            "walltime": walltime.strip(),
            "gpus": gpus,
            "gpu_type": gpu_type.strip() if gpus > 0 else "",
        }
        return result

    return None
