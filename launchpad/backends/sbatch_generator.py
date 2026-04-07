"""
SLURM batch script generator.
"""
from __future__ import annotations

def generate_sbatch_script(
    *,
    job_name: str,
    account: str,
    partition: str,
    cpus: int = 4,
    memory_gb: int = 16,
    walltime: str = "48:00:00",
    gpus: int = 0,
    gpu_type: str | None = None,
    output_log: str = "slurm-%j.out",
    error_log: str = "slurm-%j.err",
    work_dir: str | None = None,
    container_cache_dir: str | None = None,
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
    lines.append('echo "AGOUTIC bootstrap start: $(date)"')
    lines.append("")

    lines.append('export PATH="$HOME/bin:$HOME/.local/bin:$PATH"')
    lines.append("")

    # Cluster-agnostic runtime bootstrap:
    # - Tries common module names when the module system is available
    # - Falls back to whatever is already on PATH
    # - Provides compatibility shims when only one of singularity/apptainer exists
    lines.append("if [[ -n \"${LMOD_CMD:-}\" ]] || command -v module >/dev/null 2>&1; then")
    lines.append("    module load java/17 >/dev/null 2>&1 || module load java >/dev/null 2>&1 || true")
    lines.append("    module load singularity/3.11.3 >/dev/null 2>&1 || module load singularity >/dev/null 2>&1 || true")
    lines.append("    module load apptainer/1.4.5 >/dev/null 2>&1 || module load apptainer >/dev/null 2>&1 || true")
    lines.append("fi")
    lines.append("")

    lines.append("if ! command -v java >/dev/null 2>&1; then")
    lines.append('    echo "ERROR: java is not available on PATH. Load a Java module or configure PATH before running Nextflow."')
    lines.append("    exit 127")
    lines.append("fi")
    lines.append("")

    lines.append('AGOUTIC_NEXTFLOW_BIN=""')
    lines.append('if [[ -x "$HOME/bin/nextflow" ]]; then')
    lines.append('    AGOUTIC_NEXTFLOW_BIN="$HOME/bin/nextflow"')
    lines.append('elif [[ -x "$HOME/.local/bin/nextflow" ]]; then')
    lines.append('    AGOUTIC_NEXTFLOW_BIN="$HOME/.local/bin/nextflow"')
    lines.append('elif command -v nextflow >/dev/null 2>&1; then')
    lines.append('    AGOUTIC_NEXTFLOW_BIN="$(command -v nextflow)"')
    lines.append('else')
    lines.append('    echo "ERROR: nextflow is not available on PATH. SLURM jobs run in a non-login shell; add nextflow to a shared PATH, install it via modules, or expose it from $HOME/bin or $HOME/.local/bin."')
    lines.append("    exit 127")
    lines.append("fi")
    lines.append("")

    lines.append("if ! command -v singularity >/dev/null 2>&1 && command -v apptainer >/dev/null 2>&1; then")
    lines.append('    echo "INFO: singularity not found; creating local shim to apptainer"')
    lines.append('    mkdir -p .agoutic-bin')
    lines.append("    cat > .agoutic-bin/singularity <<'AGOUTIC_APPTAINER_SHIM'")
    lines.append("#!/bin/bash")
    lines.append("for env_name in ${!SINGULARITYENV_@}; do")
    lines.append("    suffix=${env_name#SINGULARITYENV_}")
    lines.append("    appt_name=APPTAINERENV_${suffix}")
    lines.append("    if [[ -z \"${!appt_name:-}\" ]]; then")
    lines.append("        export \"${appt_name}=${!env_name}\"")
    lines.append("    fi")
    lines.append("    unset \"$env_name\"")
    lines.append("done")
    lines.append("exec apptainer \"$@\"")
    lines.append("AGOUTIC_APPTAINER_SHIM")
    lines.append("    chmod +x .agoutic-bin/singularity")
    lines.append('    export PATH="$(pwd)/.agoutic-bin:$PATH"')
    lines.append("fi")
    lines.append("")

    lines.append("if ! command -v apptainer >/dev/null 2>&1 && command -v singularity >/dev/null 2>&1; then")
    lines.append('    echo "INFO: apptainer not found; creating local shim to singularity"')
    lines.append('    mkdir -p .agoutic-bin')
    lines.append("    cat > .agoutic-bin/apptainer <<'AGOUTIC_SINGULARITY_SHIM'")
    lines.append("#!/bin/bash")
    lines.append("for env_name in ${!APPTAINERENV_@}; do")
    lines.append("    suffix=${env_name#APPTAINERENV_}")
    lines.append("    sing_name=SINGULARITYENV_${suffix}")
    lines.append("    if [[ -z \"${!sing_name:-}\" ]]; then")
    lines.append("        export \"${sing_name}=${!env_name}\"")
    lines.append("    fi")
    lines.append("    unset \"$env_name\"")
    lines.append("done")
    lines.append("exec singularity \"$@\"")
    lines.append("AGOUTIC_SINGULARITY_SHIM")
    lines.append("    chmod +x .agoutic-bin/apptainer")
    lines.append('    export PATH="$(pwd)/.agoutic-bin:$PATH"')
    lines.append("fi")
    lines.append("")

    lines.append("if ! command -v singularity >/dev/null 2>&1 && ! command -v apptainer >/dev/null 2>&1; then")
    lines.append('    echo "ERROR: neither singularity nor apptainer is available on PATH."')
    lines.append("    exit 127")
    lines.append("fi")
    lines.append("")

    # Keep the container cache in a stable shared location when one is provided.
    cache_dir = (container_cache_dir or work_dir)
    if cache_dir:
        lines.append(f"export NXF_APPTAINER_CACHEDIR={cache_dir}/.nxf-apptainer-cache")
        lines.append('export NXF_SINGULARITY_CACHEDIR="$NXF_APPTAINER_CACHEDIR"')
        lines.append('mkdir -p "$NXF_APPTAINER_CACHEDIR"')
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
    lines.append('echo "Nextflow: $AGOUTIC_NEXTFLOW_BIN"')
    lines.append('echo "Java: $(command -v java)"')
    lines.append('if command -v singularity >/dev/null 2>&1; then echo "Container runtime: $(command -v singularity)"; else echo "Container runtime: $(command -v apptainer)"; fi')
    lines.append("")

    if nextflow_command:
        lines.append("set +e")
        lines.append(nextflow_command)
        lines.append("nf_exit=$?")
        lines.append("set -e")
        lines.append('if [[ "$nf_exit" -ne 0 ]]; then')
        lines.append('    echo "Nextflow failed with exit code: $nf_exit"')
        lines.append('    if [[ -f .nextflow.log ]]; then')
        lines.append('        echo "----- .nextflow.log (error scan) -----"')
        lines.append('        grep -nEi "error|exception|caused by|singularity|apptainer|docker://|permission denied|unauthorized|denied|timeout|network|no space left" .nextflow.log | tail -n 200 || true')
        lines.append('        echo "----- .nextflow.log (first 200 lines) -----"')
        lines.append('        sed -n "1,200p" .nextflow.log || true')
        lines.append('        echo "----- .nextflow.log (last 200 lines) -----"')
        lines.append('        tail -n 200 .nextflow.log || true')
        lines.append('    else')
        lines.append('        echo "No .nextflow.log found in working directory"')
        lines.append('    fi')
        lines.append('    exit "$nf_exit"')
        lines.append('fi')
    else:
        lines.append("# nextflow_command not provided")

    lines.append("")
    lines.append(f"echo \"AGOUTIC SLURM job finished: $(date)\"")

    return "\n".join(lines) + "\n"
