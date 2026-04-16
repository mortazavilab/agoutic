"""
Nextflow pipeline wrapper and executor for Launchpad.
Handles job submission, monitoring, and result parsing.
"""
import asyncio
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional
import uuid

from common.logging_config import get_logger
from common.workflow_paths import next_workflow_number, workflow_dir_name
from launchpad.config import (
    DOGME_REPO,
    DOGME_DNA_MODKITBASE,
    DOGME_DNA_MODKITMODEL,
    NEXTFLOW_BIN,
    LAUNCHPAD_WORK_DIR,
    LAUNCHPAD_LOGS_DIR,
    AGOUTIC_DATA,
    AGOUTIC_CODE,
    DogmeMode,
    JobStatus,
    REFERENCE_GENOMES,
    JOB_POLL_INTERVAL,
)

logger = get_logger(__name__)

_MINIMAL_DOGME_PROFILE = "# Dogme environment profile\n# Add environment variables here if needed\n"
_DEFAULT_PROCESS_BEFORE_SCRIPT = "export PATH=/opt/conda/bin:$PATH"
_PROFILE_EXPORT_REFERENCE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _ensure_trailing_newline(content: str) -> str:
    if content.endswith("\n"):
        return content
    return f"{content}\n"


def _default_dna_dogme_profile_content() -> str:
    return (
        "# Dogme environment profile\n"
        "# DNA mode modkit paths inside the dogme container image\n"
        f"export MODKITBASE=${{MODKITBASE:-{DOGME_DNA_MODKITBASE}}}\n"
        "export MODKITMODEL=${MODKITMODEL:-${MODKITBASE}/models/"
        f"{DOGME_DNA_MODKITMODEL.name}}}\n"
    )


def _resolve_task_scoped_dogme_exports(profile_content: str) -> list[str]:
    task_scoped_exports: list[str] = []
    known_exports: dict[str, str] = {}

    for line in _ensure_trailing_newline(profile_content).splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            export_name, has_equals, raw_value = stripped[len("export ") :].partition("=")
            if has_equals:
                resolved_value = _PROFILE_EXPORT_REFERENCE_RE.sub(
                    lambda match: known_exports.get(match.group(1), match.group(0)),
                    raw_value.strip(),
                )
                export_name = export_name.strip()
                known_exports[export_name] = resolved_value
                task_scoped_exports.append(f"export {export_name}={resolved_value}")

    return task_scoped_exports


def resolve_dogme_profile_parts(mode: str, custom_profile: str | None = None) -> tuple[str, list[str]]:
    """Return the staged dogme.profile content plus OpenChromatin-only runtime exports."""
    normalized_mode = str(mode or "").strip().upper()
    if normalized_mode == DogmeMode.DNA.value and str(custom_profile or "").strip():
        return _default_dna_dogme_profile_content(), _resolve_task_scoped_dogme_exports(str(custom_profile))

    if normalized_mode == DogmeMode.DNA.value:
        return (_default_dna_dogme_profile_content(), [])

    dogme_profile_src = DOGME_REPO / "dogme.profile"
    if dogme_profile_src.exists():
        return _ensure_trailing_newline(dogme_profile_src.read_text()), []

    return _MINIMAL_DOGME_PROFILE, []


def _quote_nextflow_single_quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _render_before_script(commands: list[str]) -> str:
    normalized: list[str] = []
    for command in commands:
        cleaned = str(command or "").strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return _quote_nextflow_single_quoted("; ".join(normalized))


def _parse_export_statement(command: str) -> tuple[str, str] | None:
    cleaned = str(command or "").strip()
    if not cleaned.startswith("export "):
        return None
    name, has_equals, value = cleaned[len("export ") :].partition("=")
    variable_name = name.strip()
    if not has_equals or not variable_name:
        return None
    return variable_name, value.strip()


def _render_apptainer_env_options(exports: list[str]) -> str:
    assignments: list[str] = []
    for command in exports:
        parsed = _parse_export_statement(command)
        if not parsed:
            continue
        variable_name, value = parsed
        if variable_name == "PATH":
            if value.endswith(":${PATH}"):
                assignments.append(f"PREPEND_PATH={value[:-len(':${PATH}')]}")
            elif value.endswith(":$PATH"):
                assignments.append(f"PREPEND_PATH={value[:-len(':$PATH')]}")
            else:
                assignments.append(f"PATH={value.replace('$', r'\\$')}")
            continue
        if variable_name in {"LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}:
            default_suffix = f":${{{variable_name}:-}}"
            plain_suffix = f":${variable_name}"
            if value.endswith(default_suffix):
                assignments.append(f"{variable_name}={value[:-len(default_suffix)]}:\\${variable_name}")
                continue
            if value.endswith(plain_suffix):
                assignments.append(f"{variable_name}={value[:-len(plain_suffix)]}:\\${variable_name}")
                continue
        assignments.append(f"{variable_name}={value.replace('$', r'\\$')}")
    if not assignments:
        return ""
    return "--env '" + ",".join(assignments).replace("'", "'\\''") + "'"


def resolve_dogme_profile_content(mode: str, custom_profile: str | None = None) -> str:
    """Return the staged dogme.profile content for a workflow mode."""
    return resolve_dogme_profile_parts(mode, custom_profile=custom_profile)[0]


def resolve_dogme_profile_task_runtime_exports(mode: str, custom_profile: str | None = None) -> list[str]:
    """Return exports that should only apply to OpenChromatin tasks."""
    return resolve_dogme_profile_parts(mode, custom_profile=custom_profile)[1]

class NextflowConfig:
    """Generates Nextflow configuration for Dogme pipeline."""
    
    @staticmethod
    def generate_config(
        sample_name: str,
        mode: str,  # "DNA", "RNA", "CDNA"
        input_dir: str,
        reference_genome: list,  # Now a list of genome names
        reference_overrides: Optional[dict[str, dict[str, str]]] = None,
        modifications: Optional[str] = None,
        modkit_filter_threshold: float = 0.9,
        min_cov: Optional[int] = None,
        per_mod: int = 5,
        accuracy: str = "sup",
        max_gpu_tasks: Optional[int] = None,
        execution_mode: str = "local",
        slurm_cpu_partition: str | None = None,
        slurm_gpu_partition: str | None = None,
        slurm_cpu_account: str | None = None,
        slurm_gpu_account: str | None = None,
        slurm_bind_paths: Optional[list[str]] = None,
        slurm_modkit_bind_paths: Optional[list[str]] = None,
        modkit_task_runtime_exports: Optional[list[str]] = None,
        apptainer_cache_dir: Optional[str] = None,
    ) -> str:
        """
        Generate a Nextflow configuration string for Dogme pipeline.
        
        Args:
            sample_name: Name of the sample
            mode: Dogme mode (DNA, RNA, or CDNA)
            input_dir: Path to pod5 input directory (not used in config generation)
            reference_genome: List of genome versions (["GRCh38"], ["mm39"], or ["GRCh38", "mm39"])
            modifications: Modification motifs to call (uses defaults if None)
        
        Returns:
            Groovy configuration string formatted for nextflow.config
        """
        # Ensure reference_genome is a list
        if isinstance(reference_genome, str):
            reference_genome = [reference_genome]
        
        # Build genome_annot_refs list for all specified genomes
        genome_refs_lines = []
        for genome_name in reference_genome:
            genome_config = REFERENCE_GENOMES.get(genome_name, REFERENCE_GENOMES["mm39"])
            override = (reference_overrides or {}).get(genome_name, {})
            fasta = override.get("fasta") or genome_config.get("fasta", "/home/seyedam/genRefs/IGVFFI9282QLXO.fasta")
            gtf = override.get("gtf") or genome_config.get("gtf", "/home/seyedam/genRefs/IGVFFI4777RDZK.gtf")
            logger.debug(
                f"Config paths for {genome_name}",
                has_override=bool(override),
                using_fasta=fasta,
                using_gtf=gtf,
            )
            genome_refs_lines.append(f"        [name: '{genome_name}', genome: '{fasta}', annot: '{gtf}']")
        
        # Use first genome for kallisto index (TODO: support per-genome kallisto)
        primary_genome = reference_genome[0]
        primary_config = REFERENCE_GENOMES.get(primary_genome, REFERENCE_GENOMES["mm39"])
        primary_override = (reference_overrides or {}).get(primary_genome, {})
        
        # Determine modifications based on mode
        if modifications:
            mods = modifications
        elif mode == "DNA":
            mods = "5mCG_5hmCG,6mA"
        elif mode == "RNA":
            mods = "inosine_m6A_2OmeA,pseU_2OmeU,m5C_2OmeC,2OmeG"
        else:  # CDNA
            mods = ""  # Empty string for CDNA (no modifications)
        
        # Determine minCov based on mode if not explicitly provided
        if min_cov is None:
            min_cov = 1 if mode == "DNA" else 3

        is_slurm = (execution_mode or "local").strip().lower() == "slurm"
        cpu_partition = (slurm_cpu_partition or "standard").strip() or "standard"
        gpu_partition = (slurm_gpu_partition or cpu_partition).strip() or cpu_partition
        cpu_account = (slurm_cpu_account or "default").strip() or "default"
        gpu_account = (slurm_gpu_account or cpu_account).strip() or cpu_account
        normalized_bind_paths: list[str] = []
        for bind_path in slurm_bind_paths or []:
            cleaned = str(bind_path or "").strip()
            if cleaned and cleaned not in normalized_bind_paths:
                normalized_bind_paths.append(cleaned)
        normalized_modkit_bind_paths: list[str] = []
        for bind_path in (slurm_modkit_bind_paths or normalized_bind_paths):
            cleaned = str(bind_path or "").strip()
            if cleaned and cleaned not in normalized_modkit_bind_paths:
                normalized_modkit_bind_paths.append(cleaned)
        slurm_bind_args = ""
        if normalized_bind_paths:
            slurm_bind_args = f"--bind {','.join(normalized_bind_paths)}"
        slurm_modkit_bind_args = ""
        if normalized_modkit_bind_paths:
            slurm_modkit_bind_args = f"--bind {','.join(normalized_modkit_bind_paths)}"
        slurm_container_base_options = "--no-mount hostfs"
        if slurm_bind_args:
            slurm_container_base_options = f"{slurm_container_base_options} {slurm_bind_args}"
        slurm_openchromatin_container_base_options = "--no-mount hostfs"
        if slurm_modkit_bind_args:
            slurm_openchromatin_container_base_options = f"{slurm_openchromatin_container_base_options} {slurm_modkit_bind_args}"
        resolved_apptainer_cache_dir = str(apptainer_cache_dir or "").strip() or "${launchDir}/.nxf-apptainer-cache"
        base_before_script = _render_before_script([_DEFAULT_PROCESS_BEFORE_SCRIPT])
        has_openchromatin_task_runtime_exports = bool(modkit_task_runtime_exports)
        openchromatin_env_options = _render_apptainer_env_options(modkit_task_runtime_exports or [])
        if openchromatin_env_options:
            slurm_openchromatin_container_base_options = (
                f"{slurm_openchromatin_container_base_options} {openchromatin_env_options}"
            )
        
        # Build config string matching example format
        config_lines = []
        config_lines.append("params {")
        config_lines.append(f"    sample = '{sample_name}'")
        config_lines.append(f"    //readType can either be 'RNA', 'DNA' or 'CDNA'")
        config_lines.append(f"    readType = '{mode}'")
        config_lines.append(f"    // change this value if 0.9 is too strict")
        config_lines.append(f"    // if set to null or '' then modkit will determine its threshold by sampling reads.")
        config_lines.append(f"    modkitFilterThreshold = {modkit_filter_threshold}")
        
        # Always include modifications parameter (empty for CDNA)
        if mods:
            config_lines.append(f"    // modification type should be set as necessary if different from 'inosine_m6A,pseU,m5C' for RNA and '5mCG_5hmCG,6mA' for DNA.")
            config_lines.append(f"    modifications = '{mods}'")
        else:
            config_lines.append(f"    // No modifications for CDNA mode")
            config_lines.append(f"    modifications = ''")
        
        config_lines.append(f"    //change setting if necessary")
        config_lines.append(f"    //minCov = 3 by default, but changed to 1 for microtest" if mode == "DNA" else f"    //change setting if necessary")
        config_lines.append(f"    minCov = {min_cov}")
        config_lines.append(f"    perMod = {per_mod}")
        config_lines.append(f"    // change if the launch directory is not where the pod5 and output directories should go")
        config_lines.append(f'    topDir = "${{launchDir}}"')
        config_lines.append(f"")
        config_lines.append(f'    scriptEnv = "${{launchDir}}/dogme.profile"')
        config_lines.append(f"")
        config_lines.append(f"    // needs to be modified to match the right genomic reference")
        config_lines.append(f"    genome_annot_refs = [")
        # Add all genome references
        config_lines.extend(genome_refs_lines)
        config_lines.append("    ]")
        config_lines.append("")
        # Use primary genome for kallisto
        kallisto_index = primary_override.get("kallisto_index") or primary_config.get("kallisto_index", "/home/seyedam/genRefs/mm39GencM36_k63.idx")
        kallisto_t2g = primary_override.get("kallisto_t2g") or primary_config.get("kallisto_t2g", "/home/seyedam/genRefs/mm39GencM36_k63.t2g")
        config_lines.append(f"    kallistoIndex = '{kallisto_index}'")
        config_lines.append(f"    t2g = '{kallisto_t2g}'")
        config_lines.append("")
        config_lines.append("    //default accuracy is sup")
        config_lines.append(f"    accuracy = \"{accuracy}\"")
        config_lines.append("    // change this value if 0.9 is too strict")
        config_lines.append("    // if set to null or '' then modkit will determine its threshold by sampling reads.")
        config_lines.append(f"    modkitFilterThreshold = {modkit_filter_threshold}")
        config_lines.append("")
        config_lines.append("    // these paths are all based on the topDir and sample name")
        config_lines.append("    // dogme will populate all of these folders with its output")
        config_lines.append('    modDir = "${topDir}/dorModels"')
        config_lines.append('    dorDir = "${topDir}/dor12-${sample}"')
        config_lines.append('    podDir = "${topDir}/pod5"')
        config_lines.append('    bamDir = "${topDir}/bams"')
        config_lines.append('    annotDir = "${topDir}/annot"')
        config_lines.append('    bedDir = "${topDir}/bedMethyl"')
        config_lines.append('    fastqDir = "${topDir}/fastqs"')
        config_lines.append('    kallistoDir = "${topDir}/kallisto"')
        config_lines.append("    tmpDir = '/tmp'  // Temporary directory for disk-based sorting")
        config_lines.append("}")
        config_lines.append("")
        config_lines.append("process {")
        config_lines.append("    // <-- Container Settings --->")
        config_lines.append("    container = 'ghcr.io/mortazavilab/dogme-pipeline:latest'")
        if is_slurm:
            config_lines.append("    // Remote SLURM runs bind only workflow-specific remote paths.")
            config_lines.append(f"    containerOptions = \"{slurm_container_base_options}\"")
        else:
            config_lines.append(f"    containerOptions = \"-v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append(f"    beforeScript = {base_before_script}")
        config_lines.append("")
        if is_slurm:
            config_lines.append("    // --- Slurm Executor ---")
            config_lines.append("    executor = 'slurm'")
            config_lines.append(f"    cpuPartition = '{cpu_partition}'")
            config_lines.append(f"    gpuPartition = '{gpu_partition}'")
            config_lines.append(f"    cpuAccount = '{cpu_account}'")
            config_lines.append(f"    gpuAccount = '{gpu_account}'")
            config_lines.append("")
            config_lines.append("    // General default settings - adjust as necessary")
            config_lines.append("    cpus = 12")
            config_lines.append("    memory = '64 GB'")
            config_lines.append("    time = '8:00:00'")
            config_lines.append("    clusterOptions = \"--account=${cpuAccount}\"")
            config_lines.append("    queue = \"${cpuPartition}\"")
        else:
            config_lines.append("    executor = 'local'")
            config_lines.append("")
            config_lines.append("    // General default settings - adjust as necessary")
            config_lines.append("    cpus = 1")
            config_lines.append("    memory = '32 GB'")
            config_lines.append("    time = '8:00:00'")
        config_lines.append("")
        config_lines.append("    withName: 'extractfastqTask' {")
        config_lines.append("        // Matches the script's thread count and gives safe memory buffer")
        config_lines.append("        cpus = 6")
        config_lines.append("        memory = '24 GB'")
        config_lines.append("        time = '4 h'")
        config_lines.append("    }")
        config_lines.append("")
        config_lines.append("    withName: 'doradoTask' {")
        if is_slurm:
            config_lines.append("        clusterOptions = \"--account=${gpuAccount} --gres=gpu:1\"")
            config_lines.append("        queue = \"${gpuPartition}\"")
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        if max_gpu_tasks is not None:
            config_lines.append(f"        maxForks = {max_gpu_tasks}  // Limit concurrent GPU tasks")
        if is_slurm:
            gpu_container_options = f"--nv {slurm_container_base_options}"
            config_lines.append(f"        containerOptions = \"{gpu_container_options}\"")
        else:
            config_lines.append(f"        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append("    }")
        config_lines.append("    ")
        config_lines.append("    withName: 'modkitTask' {")
        config_lines.append("        memory = '32 GB'")
        config_lines.append("        cpus = 12")
        if is_slurm:
            config_lines.append(f"        containerOptions = \"{slurm_container_base_options}\"")
        config_lines.append("    }")
        config_lines.append("    ")
        config_lines.append("    withName: 'openChromatinTaskBg' {")
        if is_slurm:
            config_lines.append("        clusterOptions = \"--account=${gpuAccount} --gres=gpu:1\"")
            config_lines.append("        queue = \"${gpuPartition}\"")
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        if max_gpu_tasks is not None:
            config_lines.append(f"        maxForks = {max_gpu_tasks}  // Limit concurrent GPU tasks")
        if is_slurm:
            gpu_container_options = f"--nv {slurm_openchromatin_container_base_options}"
            config_lines.append(f"        containerOptions = \"{gpu_container_options}\"")
        else:
            config_lines.append(f"        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append("    }")
        config_lines.append("")
        config_lines.append("    withName: 'openChromatinTaskBed' {")
        if is_slurm:
            config_lines.append("        clusterOptions = \"--account=${gpuAccount} --gres=gpu:1\"")
            config_lines.append("        queue = \"${gpuPartition}\"")
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        if max_gpu_tasks is not None:
            config_lines.append(f"        maxForks = {max_gpu_tasks}  // Limit concurrent GPU tasks")
        if is_slurm:
            gpu_container_options = f"--nv {slurm_openchromatin_container_base_options}"
            config_lines.append(f"        containerOptions = \"{gpu_container_options}\"")
        else:
            config_lines.append(f"        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append("    }")
        config_lines.append("")
        config_lines.append("    withName: 'minimapTask' {")
        config_lines.append("        memory = '64 GB'  // Increase memory for minimap2")
        config_lines.append("    }")
        config_lines.append("}")
        config_lines.append("")
        if is_slurm:
            config_lines.append("apptainer {")
            config_lines.append("    enabled = true")
            config_lines.append("    autoMounts = false")
            config_lines.append(f"    cacheDir = \"{resolved_apptainer_cache_dir}\"")
            config_lines.append("}")
        else:
            config_lines.append("docker {")
            config_lines.append("    enabled = true")
            config_lines.append("    runOptions = '-u $(id -u):$(id -g)'")
            config_lines.append("}")
        config_lines.append("")
        config_lines.append("timeline {")
        config_lines.append("    enabled = true")
        config_lines.append("    overwrite = true")
        config_lines.append("    file = \"${params.sample}_timeline.html\"")
        config_lines.append("}")
        config_lines.append("")
        config_lines.append("report {")
        config_lines.append("    enabled = true")
        config_lines.append("    overwrite = true")
        config_lines.append("    file = \"${params.sample}_report.html\"")
        config_lines.append("}")
        config_lines.append("")
        config_lines.append("trace {")
        config_lines.append("    enabled = true")
        config_lines.append("    overwrite = true")
        config_lines.append("    file = \"${params.sample}_trace.txt\"")
        config_lines.append("}")
        
        return "\n".join(config_lines)
    
    @staticmethod
    def write_config_file(
        config: str,
        output_path: Path,
    ) -> Path:
        """Write configuration string to a nextflow.config file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(config)
        
        return output_path


class NextflowExecutor:
    """Manages Nextflow job execution and monitoring."""
    
    def __init__(self):
        self.nextflow_bin = NEXTFLOW_BIN
        self.work_dir = LAUNCHPAD_WORK_DIR
        self.logs_dir = LAUNCHPAD_LOGS_DIR

    @staticmethod
    def _next_workflow_number(project_dir: Path) -> int:
        """Compute the next workflow number by scanning existing workflow* dirs."""
        return next_workflow_number(project_dir)

    @staticmethod
    def workflow_dir_name(workflow_index: int) -> str:
        return workflow_dir_name(workflow_index)

    @staticmethod
    def prepare_rerun_directory(work_dir: Path, sample_names: list[str] | None = None) -> dict[str, str]:
        """Archive prior summary artifacts and clear stale Nextflow markers."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        archived: dict[str, str] = {}
        candidate_names = ["report.html"]
        for sample_name in sample_names or []:
            cleaned = str(sample_name or "").strip()
            if not cleaned:
                continue
            candidate_names.extend(
                [
                    f"{cleaned}_report.html",
                    f"{cleaned}_timeline.html",
                    f"{cleaned}_trace.txt",
                ]
            )

        seen: set[str] = set()
        for file_name in candidate_names:
            if file_name in seen:
                continue
            seen.add(file_name)
            source_path = work_dir / file_name
            if not source_path.exists():
                continue

            suffix = source_path.suffix
            stem = source_path.name[:-len(suffix)] if suffix else source_path.name
            archived_name = f"{stem}.rerun_{timestamp}{suffix}"
            target_path = work_dir / archived_name
            counter = 1
            while target_path.exists():
                archived_name = f"{stem}.rerun_{timestamp}_{counter}{suffix}"
                target_path = work_dir / archived_name
                counter += 1
            source_path.rename(target_path)
            archived[file_name] = archived_name

        for marker_name in (
            ".nextflow_success",
            ".nextflow_failed",
            ".nextflow_cancelled",
            ".nextflow_running",
            ".nextflow_error",
            ".launch_error",
            ".nextflow_pid",
        ):
            marker_path = work_dir / marker_name
            if marker_path.exists():
                marker_path.unlink()

        return archived
    
    async def submit_job(
        self,
        run_uuid: str,
        sample_name: str,
        mode: str,
        input_type: str,
        input_dir: str,
        reference_genome: list,
        modifications: Optional[str] = None,
        entry_point: Optional[str] = None,
        modkit_filter_threshold: float = 0.9,
        min_cov: Optional[int] = None,
        per_mod: int = 5,
        accuracy: str = "sup",
        max_gpu_tasks: Optional[int] = None,
        custom_dogme_profile: str | None = None,
        workflow_index: Optional[int] = None,
        rerun_in_place: bool = False,
        archive_sample_names: Optional[list[str]] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        username: Optional[str] = None,
        project_slug: Optional[str] = None,
        resume_from_dir: Optional[str] = None,
    ) -> tuple[str, Path]:
        """
        Submit a Dogme/Nextflow job.
        
        Args:
            run_uuid: Unique identifier for this job run
            sample_name: Name of the sample
            mode: Dogme mode (DNA, RNA, CDNA)
            input_type: Type of input ("pod5" or "bam")
            input_dir: Path to input files
            reference_genome: List of reference genome versions
            modifications: Modification motifs
            entry_point: Dogme entry point workflow (None for main, or "remap", "basecall", etc.)
            username: Human-readable username for directory naming
            project_slug: Human-readable project slug for directory naming
        
        Returns:
            Tuple of (run_uuid, work_directory)
        """
        # Determine work directory:
        #   Resume: reuse the previous workflow directory (with -resume flag)
        #   New: AGOUTIC_DATA/users/{username}/{project_slug}/workflow{N}/
        #   Fallback (legacy): AGOUTIC_DATA/users/{user_id}/{project_id}/{run_uuid}/
        #   Fallback (flat):   LAUNCHPAD_WORK_DIR/{run_uuid}/
        is_resume = False
        if resume_from_dir:
            resume_path = Path(resume_from_dir)
            if resume_path.exists() and resume_path.is_dir():
                work_dir = resume_path
                is_resume = True
                if rerun_in_place:
                    archived = self.prepare_rerun_directory(work_dir, archive_sample_names or [sample_name])
                    logger.info(
                        "Preparing rerun in existing workflow directory",
                        work_dir=str(work_dir),
                        run_uuid=run_uuid,
                        archived=list(archived.items()),
                    )
                else:
                    # Clean up old cancellation/failure markers so the run starts fresh
                    for marker in (".nextflow_cancelled", ".nextflow_failed", ".nextflow_running"):
                        marker_file = work_dir / marker
                        if marker_file.exists():
                            marker_file.unlink()
                logger.info("Resuming in existing workflow directory",
                           work_dir=str(work_dir), run_uuid=run_uuid)
            else:
                logger.warning("resume_from_dir does not exist, creating new workflow dir",
                              resume_from_dir=resume_from_dir)

        if not is_resume:
            if username and project_slug:
                # Human-readable path — compute next workflow number
                project_dir = AGOUTIC_DATA / "users" / username / project_slug
                project_dir.mkdir(parents=True, exist_ok=True)
                next_n = workflow_index or self._next_workflow_number(project_dir)
                work_dir = project_dir / self.workflow_dir_name(next_n)
                work_dir.mkdir(parents=True, exist_ok=True)
                logger.info("Using workflow directory", work_dir=str(work_dir),
                           username=username, project_slug=project_slug, workflow_n=next_n)
            elif user_id and project_id:
                work_dir = AGOUTIC_DATA / "users" / user_id / project_id / run_uuid
                work_dir.mkdir(parents=True, exist_ok=True)
                logger.info("Using jailed work directory (legacy UUID)", work_dir=str(work_dir),
                           user_id=user_id, project_id=project_id)
            else:
                work_dir = self.work_dir / run_uuid
                work_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup input files based on entry point and input type
        if entry_point == "basecall":
            # basecall: pod5 → unmapped BAM
            pod5_link = work_dir / "pod5"
            try:
                if pod5_link.exists():
                    pod5_link.unlink()
                pod5_link.symlink_to(Path(input_dir).resolve())
                logger.info("Created pod5 symlink for basecalling", link=str(pod5_link), target=input_dir)
            except Exception as e:
                raise RuntimeError(f"Failed to create pod5 symlink: {e}")
        
        elif entry_point == "remap":
            # remap: unmapped BAM → mapped BAM
            # Dogme expects bams/{sample_name}.unmapped.bam for remap entry
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            input_path = Path(input_dir)
            if input_path.is_dir():
                # input_dir is a directory (e.g. projectdir/data/) — find BAM files
                bam_files = list(input_path.glob("*.bam"))
                if not bam_files:
                    raise RuntimeError(f"No BAM files found in directory: {input_dir}")
                # Try to match by sample name first
                matched = [f for f in bam_files if sample_name.lower() in f.stem.lower()]
                bam_file = matched[0] if matched else bam_files[0]
                logger.info("Found BAM in directory", directory=input_dir,
                           bam_file=str(bam_file), total_bams=len(bam_files))
            else:
                bam_file = input_path
                if not bam_file.exists():
                    raise RuntimeError(f"Unmapped BAM file not found: {input_dir}")
            
            bam_link = bams_dir / f"{sample_name}.unmapped.bam"
            try:
                if bam_link.exists():
                    bam_link.unlink()
                bam_link.symlink_to(bam_file.resolve())
                logger.info("Created unmapped BAM symlink for remapping",
                           link=str(bam_link), target=str(bam_file))
            except Exception as e:
                raise RuntimeError(f"Failed to create BAM symlink: {e}")
        
        elif entry_point == "modkit":
            # modkit: mapped BAM → modification calls
            # Dogme expects bams/{sample_name}.{genome_ref}.bam for mapped BAM entries
            # genome_ref MUST match the genome the BAM was actually mapped to
            if not reference_genome:
                raise RuntimeError(
                    "reference_genome is required for modkit entry point. "
                    "Please specify the genome the BAM was mapped to (e.g., GRCh38, mm39)."
                )
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            bam_file = Path(input_dir)
            if not bam_file.exists():
                raise RuntimeError(f"Mapped BAM file not found: {input_dir}")
            
            genome_ref = reference_genome[0]
            bam_link = bams_dir / f"{sample_name}.{genome_ref}.bam"
            try:
                if bam_link.exists():
                    bam_link.unlink()
                bam_link.symlink_to(bam_file.resolve())
                logger.info("Created mapped BAM symlink for modkit", link=str(bam_link), target=input_dir)
            except Exception as e:
                raise RuntimeError(f"Failed to create BAM symlink: {e}")
        
        elif entry_point == "annotateRNA":
            # annotateRNA: mapped RNA/cDNA BAM → transcript annotation
            # Dogme expects bams/{sample_name}.{genome_ref}.bam for mapped BAM entries
            # genome_ref MUST match the genome the BAM was actually mapped to
            if not reference_genome:
                raise RuntimeError(
                    "reference_genome is required for annotateRNA entry point. "
                    "Please specify the genome the BAM was mapped to (e.g., GRCh38, mm39)."
                )
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            bam_file = Path(input_dir)
            if not bam_file.exists():
                raise RuntimeError(f"Mapped BAM file not found: {input_dir}")
            
            genome_ref = reference_genome[0]
            bam_link = bams_dir / f"{sample_name}.{genome_ref}.bam"
            try:
                if bam_link.exists():
                    bam_link.unlink()
                bam_link.symlink_to(bam_file.resolve())
                logger.info("Created mapped BAM symlink for annotation", link=str(bam_link), target=input_dir)
            except Exception as e:
                raise RuntimeError(f"Failed to create BAM symlink: {e}")
        
        elif entry_point == "reports":
            # reports: existing work directory → generate reports
            # Input_dir should point to existing work directory with outputs
            logger.info("Report generation using existing outputs", input_dir=input_dir)
            # No symlink needed, just use the directory as-is
        
        elif input_type == "bam":
            # Default BAM handling (assume unmapped, use remap)
            # Dogme expects bams/{sample_name}.unmapped.bam for remap entry
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            input_path = Path(input_dir)
            if input_path.is_dir():
                # input_dir is a directory (e.g. projectdir/data/) — find BAM files
                bam_files = list(input_path.glob("*.bam"))
                if not bam_files:
                    raise RuntimeError(f"No BAM files found in directory: {input_dir}")
                matched = [f for f in bam_files if sample_name.lower() in f.stem.lower()]
                bam_file = matched[0] if matched else bam_files[0]
                logger.info("Found BAM in directory", directory=input_dir,
                           bam_file=str(bam_file), total_bams=len(bam_files))
            else:
                bam_file = input_path
                if not bam_file.exists():
                    raise RuntimeError(f"BAM file not found: {input_dir}")
            
            bam_link = bams_dir / f"{sample_name}.unmapped.bam"
            try:
                if bam_link.exists():
                    bam_link.unlink()
                bam_link.symlink_to(bam_file.resolve())
                logger.info("Created BAM symlink", link=str(bam_link), target=str(bam_file))
            except Exception as e:
                raise RuntimeError(f"Failed to create BAM symlink: {e}")
            
            # Default to remap if no entry point specified
            if entry_point is None:
                entry_point = "remap"
        else:
            # Default: pod5 input for full pipeline (main workflow)
            pod5_link = work_dir / "pod5"
            try:
                if pod5_link.exists():
                    pod5_link.unlink()
                pod5_link.symlink_to(Path(input_dir).resolve())
                logger.info("Created pod5 symlink", link=str(pod5_link), target=input_dir)
            except Exception as e:
                raise RuntimeError(f"Failed to create pod5 symlink: {e}")
        
        dogme_profile_content, modkit_task_runtime_exports = resolve_dogme_profile_parts(
            mode,
            custom_profile=custom_dogme_profile,
        )
        dogme_profile_dst = work_dir / "dogme.profile"
        dogme_profile_dst.write_text(dogme_profile_content)
        logger.debug("Staged dogme.profile", mode=mode, path=str(dogme_profile_dst))
        
        # Generate configuration
        config_string = NextflowConfig.generate_config(
            sample_name=sample_name,
            mode=mode,
            input_dir=input_dir,
            reference_genome=reference_genome,
            modifications=modifications,
            modkit_filter_threshold=modkit_filter_threshold,
            min_cov=min_cov,
            per_mod=per_mod,
            accuracy=accuracy,
            max_gpu_tasks=max_gpu_tasks,
            modkit_task_runtime_exports=modkit_task_runtime_exports,
        )
        
        config_path = work_dir / "nextflow.config"
        NextflowConfig.write_config_file(config_string, config_path)
        
        # Log files for stdout/stderr
        stdout_file = self.logs_dir / f"{run_uuid}_stdout.log"
        stderr_file = self.logs_dir / f"{run_uuid}_stderr.log"
        
        # Create running marker
        running_marker = work_dir / ".nextflow_running"
        running_marker.write_text(f"Started at {datetime.utcnow().isoformat()}\n")
        
        # Build Nextflow command
        # Note: Nextflow automatically creates .nextflow.log in the working directory
        cmd = [
            str(self.nextflow_bin),
            "run",
            str(DOGME_REPO / "dogme.nf"),
        ]
        
        # Add entry point if specified
        if entry_point:
            cmd.extend(["-entry", entry_point])
            logger.info("Using Dogme entry point", entry_point=entry_point)
            
            # Add -CDNA flag for annotateRNA with cDNA samples
            if entry_point == "annotateRNA" and mode == "CDNA":
                cmd.append("-CDNA")
                logger.info("Adding -CDNA flag for cDNA annotation")
        
        cmd.extend([
            "-c", str(config_path),
            "-work-dir", str(work_dir / "work"),
            "-with-report", str(work_dir / "report.html"),
            "-with-timeline", str(work_dir / f"{sample_name}_timeline.html"),
            "-with-trace", str(work_dir / f"{sample_name}_trace.txt"),
        ])
        
        # Add rerun/resume flag when reusing an existing workflow directory
        if is_resume:
            rerun_flag = "-rerun" if rerun_in_place else "-resume"
            cmd.append(rerun_flag)
            logger.info("Adding workflow reuse flag", flag=rerun_flag, work_dir=str(work_dir))
        
        # Validate prerequisites before attempting launch
        if not self.nextflow_bin.exists():
            raise RuntimeError(f"Nextflow binary not found at: {self.nextflow_bin}")
        
        if not (DOGME_REPO / "dogme.nf").exists():
            raise RuntimeError(f"Dogme dogme.nf not found at: {DOGME_REPO / 'dogme.nf'}")
        
        # Submit job as background process
        try:
            # Create log files and open for writing
            stdout_file.parent.mkdir(parents=True, exist_ok=True)
            stderr_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Open file descriptors
            stdout_fd = open(stdout_file, "w", buffering=1)  # Line buffered
            stderr_fd = open(stderr_file, "w", buffering=1)
            
            # Also write a launch marker with the command
            launch_marker = work_dir / ".launch_command"
            launch_marker.write_text(" ".join(cmd) + "\n")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=stdout_fd,
                stderr=stderr_fd,
                cwd=work_dir,  # Run in work directory so launchDir is correct
            )
            
            # Store PID for tracking
            pid_file = work_dir / ".nextflow_pid"
            pid_file.write_text(str(process.pid))
            
            logger.info("Job submitted", run_uuid=run_uuid, pid=process.pid,
                        command=" ".join(cmd), work_dir=str(work_dir),
                        stdout_log=str(stdout_file), stderr_log=str(stderr_file))
            
            # Start background task to monitor process completion
            asyncio.create_task(self._monitor_process(process, run_uuid, work_dir, stdout_fd, stderr_fd))
            
            return run_uuid, work_dir
            
        except Exception as e:
            # Clean up running marker on failure
            if running_marker.exists():
                running_marker.unlink()
            
            # Write error to marker file
            error_marker = work_dir / ".launch_error"
            error_marker.write_text(f"Launch failed: {str(e)}\n{type(e).__name__}")
            
            raise RuntimeError(f"Failed to submit Nextflow job: {e}")
    
    async def _monitor_process(self, process, run_uuid: str, work_dir: Path, stdout_f, stderr_f):
        """Background task to monitor process completion and create marker files."""
        try:
            returncode = await process.wait()
            
            # Close log files
            stdout_f.close()
            stderr_f.close()
            
            # Remove running marker
            running_marker = work_dir / ".nextflow_running"
            if running_marker.exists():
                running_marker.unlink()
            
            # Create completion marker
            if returncode == 0:
                success_marker = work_dir / ".nextflow_success"
                success_marker.write_text(f"Completed at {datetime.utcnow().isoformat()}\n")
                logger.info("Job completed successfully", run_uuid=run_uuid)
            elif (work_dir / ".nextflow_cancelled").exists():
                # Job was cancelled via the API — don't overwrite with FAILED
                logger.info("Job was cancelled (SIGTERM), skipping failed marker",
                            run_uuid=run_uuid, returncode=returncode)
            else:
                failed_marker = work_dir / ".nextflow_failed"
                error_file = work_dir / ".nextflow_error"
                
                # Try to extract error from stderr log (strip ANSI codes
                # and harmless "update available" nag messages)
                stderr_file = self.logs_dir / f"{run_uuid}_stderr.log"
                error_msg = f"Process exited with code {returncode}"
                if stderr_file.exists():
                    with open(stderr_file) as f:
                        stderr_content = f.read()
                        if stderr_content:
                            import re as _re
                            stderr_content = _re.sub(r'\x1b\[[0-9;]*m', '', stderr_content)
                            _lines = [
                                l for l in stderr_content.splitlines()
                                if l.strip()
                                and 'is available' not in l
                                and 'consider updating' not in l.lower()
                            ]
                            if _lines:
                                error_msg = '\n'.join(_lines)[-500:]
                
                failed_marker.write_text(f"Failed at {datetime.utcnow().isoformat()}\n")
                error_file.write_text(error_msg)
                logger.error("Job failed", run_uuid=run_uuid, returncode=returncode)
                
        except Exception as e:
            logger.error("Error monitoring process", run_uuid=run_uuid, error=str(e))
    
    async def check_status(self, run_uuid: str, work_dir: Path) -> dict:
        """
        Check the status of a running job.
        
        Returns dict with keys: status, progress_percent, message, tasks (optional)
        """
        # If work directory was deleted (e.g. by delete-job API), report as DELETED
        if not work_dir.exists():
            return {
                "status": JobStatus.DELETED,
                "progress_percent": 0,
                "message": "Workflow folder has been deleted.",
                "tasks": {},
            }

        # Check for completion markers
        success_marker = work_dir / ".nextflow_success"
        failed_marker = work_dir / ".nextflow_failed"
        running_marker = work_dir / ".nextflow_running"
        pid_file = work_dir / ".nextflow_pid"
        
        cancelled_marker = work_dir / ".nextflow_cancelled"

        # Check completion markers first
        if cancelled_marker.exists():
            _cancel_detail = ""
            try:
                _cancel_detail = cancelled_marker.read_text().strip()
            except OSError:
                pass
            # Second line of the marker contains the kill message
            _cancel_lines = _cancel_detail.splitlines()
            _cancel_msg = _cancel_lines[1] if len(_cancel_lines) > 1 else "Job was cancelled by user."
            return {
                "status": JobStatus.CANCELLED,
                "progress_percent": 0,
                "message": _cancel_msg,
                "tasks": {},
            }
        elif success_marker.exists():
            # Parse final task summary from trace file
            completed_tasks = []
            total = 0
            trace_files = list(work_dir.glob("*_trace.txt"))
            if trace_files:
                try:
                    with open(trace_files[0]) as f:
                        lines = f.readlines()
                        if len(lines) > 1:
                            headers = lines[0].strip().split('\t')
                            try:
                                name_idx = headers.index('name')
                            except ValueError:
                                name_idx = 0
                            
                            for line in lines[1:]:
                                parts = line.strip().split('\t')
                                if len(parts) > name_idx:
                                    completed_tasks.append(parts[name_idx])
                            total = len(lines) - 1
                except:
                    pass
            
            return {
                "status": JobStatus.COMPLETED,
                "progress_percent": 100,
                "message": f"Job completed successfully - {total} tasks completed",
                "tasks": {
                    "completed": completed_tasks,  # All completed tasks
                    "running": [],
                    "total": total,
                    "completed_count": total,
                    "failed_count": 0
                } if total > 0 else {}
            }
        elif failed_marker.exists():
            error_msg = ""
            error_file = work_dir / ".nextflow_error"
            if error_file.exists():
                with open(error_file) as f:
                    error_msg = f.read()[:200]  # First 200 chars
            return {
                "status": JobStatus.FAILED,
                "progress_percent": 0,
                "message": f"Job failed: {error_msg}" if error_msg else "Job failed",
                "tasks": {}
            }
        
        # Check if process is still running
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                # Check if PID is alive
                import os
                import signal
                try:
                    os.kill(pid, 0)  # Signal 0 checks if process exists
                    is_running = True
                except OSError:
                    is_running = False
                
                if not is_running and running_marker.exists():
                    # Process is gone. But did it actually succeed?
                    # After a server restart we lose the process handle, so
                    # _monitor_process never writes .nextflow_success.  Check
                    # the trace file: if every task status is COMPLETED (and
                    # there is at least one task), the job finished fine.
                    _job_actually_succeeded = False
                    _trace_files = list(work_dir.glob("*_trace.txt"))
                    if not _trace_files and (work_dir / "trace.txt").exists():
                        _trace_files = [work_dir / "trace.txt"]
                    if _trace_files:
                        try:
                            with open(_trace_files[0], 'r', encoding='utf-8', errors='ignore') as _tf:
                                _tlines = _tf.readlines()
                            if len(_tlines) > 1:
                                _all_done = True
                                for _tl in _tlines[1:]:
                                    if not _tl.strip():
                                        continue
                                    _parts = _tl.split('\t')
                                    if len(_parts) >= 5:
                                        _st = _parts[4].strip()
                                        if _st not in ('COMPLETED', 'CACHED'):
                                            _all_done = False
                                            break
                                _task_count = len([l for l in _tlines[1:] if l.strip()])
                                if _all_done and _task_count > 0:
                                    _job_actually_succeeded = True
                        except Exception:
                            pass

                    running_marker.unlink()

                    if _job_actually_succeeded:
                        # Recover: write the success marker that _monitor_process
                        # would have written had the server not been restarted.
                        success_marker = work_dir / ".nextflow_success"
                        success_marker.write_text(
                            f"Recovered at {datetime.utcnow().isoformat()} "
                            f"(process exited while server was down)\n"
                        )
                        logger.info("Recovered successful job after server restart",
                                    run_uuid=run_uuid)
                        # Re-enter check_status — it will now find .nextflow_success
                        return await self.check_status(run_uuid, work_dir)

                    # Genuinely failed — write markers
                    failed_marker = work_dir / ".nextflow_failed"
                    failed_marker.write_text(f"Process died unexpectedly at {datetime.utcnow().isoformat()}\n")
                    
                    # Try to get error from stderr (filter out ANSI and
                    # Nextflow version-update nag messages)
                    stderr_file = self.logs_dir / f"{run_uuid}_stderr.log"
                    error_msg = "Process died unexpectedly"
                    if stderr_file.exists():
                        with open(stderr_file) as f:
                            content = f.read()
                            if content:
                                import re as _re
                                # Strip ANSI escape codes
                                content = _re.sub(r'\x1b\[[0-9;]*m', '', content)
                                # Drop harmless "update available" lines
                                _lines = [
                                    l for l in content.splitlines()
                                    if l.strip()
                                    and 'is available' not in l
                                    and 'consider updating' not in l.lower()
                                ]
                                if _lines:
                                    error_msg = '\n'.join(_lines)[-200:]
                                # If only version nag remains, keep default msg
                    
                    error_file = work_dir / ".nextflow_error"
                    error_file.write_text(error_msg)
                    
                    return {
                        "status": JobStatus.FAILED,
                        "progress_percent": 0,
                        "message": f"Process died: {error_msg[:100]}",
                        "tasks": {}
                    }
            except Exception as e:
                pass
        
        # Job is running - parse detailed task information from trace.txt
        progress = 10  # Default
        message = "Job starting..."
        tasks = {
            "completed": [],
            "running": [],
            "total": 0,
            "completed_count": 0,
            "failed_count": 0
        }
        
        # Parse trace file for completed tasks - tab-separated with columns:
        # task_id hash native_id name status exit submit duration realtime %cpu peak_rss peak_vmem rchar wchar
        # Nextflow writes {sample}_trace.txt (via -with-trace flag).
        # Prefer the sample-specific file; fall back to generic trace.txt.
        trace_file = None
        sample_traces = list(work_dir.glob("*_trace.txt"))
        if sample_traces:
            trace_file = sample_traces[0]
        elif (work_dir / "trace.txt").exists():
            trace_file = work_dir / "trace.txt"
        
        completed_tasks = []
        failed_tasks = []
        
        if trace_file and trace_file.exists():
            try:
                with open(trace_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    
                    # Skip header line
                    for line in lines[1:]:
                        if not line.strip():
                            continue
                        
                        # Split by tabs
                        parts = line.split('\t')
                        if len(parts) < 5:
                            continue
                        
                        # Extract task name (column 3, index 3) and status (column 4, index 4)
                        task_name = parts[3].strip()
                        status = parts[4].strip()
                        
                        # Only track workflow tasks (contain ':' in the name,
                        # e.g. mainWorkflow:doradoTask, remap:minimapTask).
                        # This filters out Nextflow internal tasks while
                        # supporting all entry points (remap, basecall, modkit,
                        # annotateRNA, reports).
                        if ':' not in task_name:
                            continue
                        
                        # Categorize by status
                        if status == 'COMPLETED':
                            if task_name not in completed_tasks:
                                completed_tasks.append(task_name)
                        elif status == 'FAILED':
                            if task_name not in failed_tasks:
                                failed_tasks.append(task_name)
                        
            except Exception as e:
                pass
        
        # Now parse stdout for currently running tasks (not yet in trace.txt)
        stdout_file = self.logs_dir / f"{run_uuid}_stdout.log"
        running_tasks = []
        submitted_count = 0
        
        if stdout_file.exists():
            try:
                with open(stdout_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    lines = content.split('\n')
                    
                    task_events_by_hash = {}
                    
                    for line in lines:
                        # Look for executor count to get total submitted
                        if 'executor >' in line and '(' in line:
                            try:
                                count_str = line.split('(')[1].split(')')[0]
                                submitted_count = max(submitted_count, int(count_str))
                            except:
                                pass
                        
                        # Look for task lines: [hash] workflowName:taskName
                        # Accept any workflow prefix (mainWorkflow, remap,
                        # basecall, modkit, annotateRNA, reports).
                        if line.startswith('[') and ']' in line and ':' in line.split(']', 1)[-1]:
                            # Extract hash
                            hash_part = line.split(']')[0] + ']'
                            
                            # Skip placeholder lines [-        ]
                            if '/' not in hash_part:
                                continue
                            
                            # Extract task name
                            rest = line.split(']', 1)[1] if ']' in line else ''
                            task_name = rest.strip().split()[0] if rest.strip() else ''
                            
                            # Add number if present: workflow:taskName (1)
                            if '(' in rest and ')' in rest:
                                task_num = rest[rest.find('('):rest.find(')')+1]
                                if task_num not in task_name:
                                    task_name = task_name + ' ' + task_num
                            
                            if not task_name:
                                continue
                            
                            is_terminal_stdout_event = '✔' in line or 'FAILED' in line.upper()
                            task_events_by_hash[hash_part] = (task_name, is_terminal_stdout_event)

                    for task_name, is_terminal_stdout_event in task_events_by_hash.values():
                        if is_terminal_stdout_event:
                            continue
                        if task_name in completed_tasks or task_name in failed_tasks:
                            continue
                        if task_name not in running_tasks:
                            running_tasks.append(task_name)
                            
            except Exception as e:
                pass
        
        # Calculate totals
        total = max(submitted_count, len(completed_tasks) + len(running_tasks) + len(failed_tasks))
        
        if total > 0 or completed_tasks or running_tasks:
            tasks['completed'] = completed_tasks  # All completed tasks
            tasks['running'] = running_tasks[-5:] if len(running_tasks) > 5 else running_tasks  # Last 5 running
            tasks['total'] = total
            tasks['completed_count'] = len(completed_tasks)
            tasks['failed_count'] = len(failed_tasks)
            
            # Calculate progress
            progress = int((len(completed_tasks) / total) * 90) if total > 0 else 10
            
            # Build message
            msg_parts = []
            if total > 0:
                msg_parts.append(f"{len(completed_tasks)}/{total} completed")
            if running_tasks:
                msg_parts.append(f"{len(running_tasks)} running")
            if failed_tasks:
                msg_parts.append(f"{len(failed_tasks)} failed")
            
            message = "Pipeline: " + ", ".join(msg_parts) if msg_parts else "Pipeline starting..."
        
        # Fallback: Check .nextflow.log for recent activity if we still have no data
        if progress == 10 and not completed_tasks:
            nextflow_log = work_dir / ".nextflow.log"
            if nextflow_log.exists():
                try:
                    with open(nextflow_log) as f:
                        log_content = f.read()
                        # Look for executor messages about submitted tasks
                        submitted = log_content.count('Submitted process')
                        if submitted > 0:
                            progress = min(30 + (submitted * 3), 80)
                            message = f"Pipeline executing ({submitted} tasks submitted)..."
                except Exception:
                    pass
        
        return {
            "status": JobStatus.RUNNING,
            "progress_percent": progress,
            "message": message,
            "tasks": tasks
        }
    
    async def get_results(self, run_uuid: str, work_dir: Path) -> dict:
        """
        Retrieve results from a completed job.
        
        Returns dictionary with result paths and summary stats.
        """
        output_dir = work_dir / "results"
        report_data = {}
        
        # Parse the Nextflow report
        report_file = work_dir / "report.html"
        if report_file.exists():
            try:
                with open(report_file) as f:
                    report_data["raw_report"] = f.read()[:1000]  # First 1KB
            except Exception:
                pass
        
        # Look for key output files
        results = {
            "output_directory": str(output_dir),
            "files": [],
            "summary": {
                "run_uuid": run_uuid,
                "status": "completed",
            }
        }
        
        # Scan for important output files
        if output_dir.exists():
            for pattern in ["*.bam", "*.vcf", "*.bed", "*_report.tsv", "*.json"]:
                for file in output_dir.glob(pattern):
                    results["files"].append({
                        "path": str(file),
                        "name": file.name,
                        "size": file.stat().st_size,
                    })
        
        # Add timeline and reports
        for report_type in ["timeline.html", "report.html", "trace.txt"]:
            report_path = work_dir / report_type
            if report_path.exists():
                results["files"].append({
                    "path": str(report_path),
                    "name": report_type,
                    "size": report_path.stat().st_size,
                })
        
        return results
