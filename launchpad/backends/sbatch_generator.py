"""
SLURM batch script generator.
"""
from __future__ import annotations

from textwrap import dedent


def generate_sbatch_script(
    *,
    job_name: str,
    account: str,
    partition: str,
    cpus: int = 4,
    memory_gb: int = 16,
    walltime: str = "04:00:00",
    gpus: int = 0,
    gpu_type: str | None = None,
    output_log: str = "slurm-%j.out",
    error_log: str = "slurm-%j.err",
    work_dir: str | None = None,
    nextflow_command: str = "",
    module_loads: list[str] | None = None,
    conda_env: str | None = None,
    extra_sbatch_lines: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Generate an sbatch submission script.

    Returns the full script content as a string.
    """
    lines: list[str] = ["#!/bin/bash"]

    # SBATCH directives
    lines.append(f"#SBATCH --job-name={job_name}")
    lines.append(f"#SBATCH --account={account}")
    lines.append(f"#SBATCH --partition={partition}")
    lines.append(f"#SBATCH --cpus-per-task={cpus}")
    lines.append(f"#SBATCH --mem={memory_gb}G")
    lines.append(f"#SBATCH --time={walltime}")
    lines.append(f"#SBATCH --output={output_log}")
    lines.append(f"#SBATCH --error={error_log}")

    if gpus > 0:
        gres = f"gpu:{gpu_type}:{gpus}" if gpu_type else f"gpu:{gpus}"
        lines.append(f"#SBATCH --gres={gres}")

    if work_dir:
        lines.append(f"#SBATCH --chdir={work_dir}")

    if extra_sbatch_lines:
        for line in extra_sbatch_lines:
            lines.append(f"#SBATCH {line}")

    lines.append("")
    lines.append("# --- Environment setup ---")
    lines.append("set -euo pipefail")
    lines.append("")

    # Module loads
    if module_loads:
        for mod in module_loads:
            lines.append(f"module load {mod}")
        lines.append("")

    # Conda activation
    if conda_env:
        lines.append(f"conda activate {conda_env}")
        lines.append("")

    # Extra environment variables
    if extra_env:
        for key, val in extra_env.items():
            lines.append(f"export {key}={val!r}")
        lines.append("")

    lines.append("# --- Run pipeline ---")
    lines.append(f"echo \"AGOUTIC SLURM job started: $(date)\"")
    lines.append(f"echo \"Job ID: $SLURM_JOB_ID\"")
    lines.append(f"echo \"Node: $SLURM_NODELIST\"")
    lines.append("")

    if nextflow_command:
        lines.append(nextflow_command)
    else:
        lines.append("# nextflow_command not provided")

    lines.append("")
    lines.append(f"echo \"AGOUTIC SLURM job finished: $(date)\"")

    return "\n".join(lines) + "\n"
