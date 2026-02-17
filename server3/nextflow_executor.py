"""
Nextflow pipeline wrapper and executor for Server 3.
Handles job submission, monitoring, and result parsing.
"""
import asyncio
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional
import uuid

from common.logging_config import get_logger
from server3.config import (
    DOGME_REPO,
    NEXTFLOW_BIN,
    SERVER3_WORK_DIR,
    SERVER3_LOGS_DIR,
    AGOUTIC_DATA,
    AGOUTIC_CODE,
    DogmeMode,
    JobStatus,
    REFERENCE_GENOMES,
    JOB_POLL_INTERVAL,
)

logger = get_logger(__name__)

class NextflowConfig:
    """Generates Nextflow configuration for Dogme pipeline."""
    
    @staticmethod
    def generate_config(
        sample_name: str,
        mode: str,  # "DNA", "RNA", "CDNA"
        input_dir: str,
        reference_genome: list,  # Now a list of genome names
        modifications: Optional[str] = None,
        modkit_filter_threshold: float = 0.9,
        min_cov: Optional[int] = None,
        per_mod: int = 5,
        accuracy: str = "sup",
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
            fasta = genome_config.get("fasta", "/home/seyedam/genRefs/IGVFFI9282QLXO.fasta")
            gtf = genome_config.get("gtf", "/home/seyedam/genRefs/IGVFFI4777RDZK.gtf")
            genome_refs_lines.append(f"        [name: '{genome_name}', genome: '{fasta}', annot: '{gtf}']")
        
        # Use first genome for kallisto index (TODO: support per-genome kallisto)
        primary_genome = reference_genome[0]
        primary_config = REFERENCE_GENOMES.get(primary_genome, REFERENCE_GENOMES["mm39"])
        
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
        config_lines.append(f"    ]")
        config_lines.append(f"    ")
        # Use primary genome for kallisto
        kallisto_index = primary_config.get("kallisto_index", "/home/seyedam/genRefs/mm39GencM36_k63.idx")
        kallisto_t2g = primary_config.get("kallisto_t2g", "/home/seyedam/genRefs/mm39GencM36_k63.t2g")
        config_lines.append(f"    kallistoIndex = '{kallisto_index}'")
        config_lines.append(f"    t2g = '{kallisto_t2g}'")
        config_lines.append(f"")
        config_lines.append(f"    //default accuracy is sup")
        config_lines.append(f"    accuracy = \"{accuracy}\"")
        config_lines.append(f"    // change this value if 0.9 is too strict")
        config_lines.append(f"    // if set to null or '' then modkit will determine its threshold by sampling reads.")
        config_lines.append(f"    modkitFilterThreshold = {modkit_filter_threshold} ")
        config_lines.append(f"")
        config_lines.append(f"")
        config_lines.append(f"    // these paths are all based on the topDir and sample name")
        config_lines.append(f"    // dogme will populate all of these folders with its output")
        config_lines.append(f'    modDir = "${{topDir}}/dorModels"')
        config_lines.append(f'    dorDir = "${{topDir}}/dor12-${{sample}}"')
        config_lines.append(f'    podDir = "${{topDir}}/pod5"')
        config_lines.append(f'    bamDir = "${{topDir}}/bams"')
        config_lines.append(f'    annotDir = "${{topDir}}/annot"')
        config_lines.append(f'    bedDir = "${{topDir}}/bedMethyl"')
        config_lines.append(f'    fastqDir = "${{topDir}}/fastqs"')
        config_lines.append(f'    kallistoDir = "${{topDir}}/kallisto"')
        config_lines.append(f"    tmpDir = '/tmp'  // Temporary directory for disk-based sorting")
        config_lines.append("}")
        config_lines.append("")
        config_lines.append("process {")
        config_lines.append("    // <-- Container Settings --->")
        config_lines.append("    container = 'ghcr.io/mortazavilab/dogme-pipeline:latest'")
        config_lines.append(f"    containerOptions = \"-v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append("    beforeScript = 'export PATH=/opt/conda/bin:$PATH'")
        config_lines.append("")
        config_lines.append("")
        config_lines.append("    executor='local'")
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
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        config_lines.append(f"        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append("    }")
        config_lines.append("    ")
        config_lines.append("    withName: 'openChromatinTaskBg' {")
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        config_lines.append(f"        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append("    }")
        config_lines.append("")
        config_lines.append("    withName: 'openChromatinTaskBed' {")
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        config_lines.append(f"        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam -v {AGOUTIC_DATA}:{AGOUTIC_DATA} -v {AGOUTIC_CODE}:{AGOUTIC_CODE} \"")
        config_lines.append("    }")
        config_lines.append("")
        config_lines.append("    withName: 'minimapTask' {")
        config_lines.append("        memory = '64 GB'  // Increase memory for minimap2")
        config_lines.append("    }")
        config_lines.append("}")
        config_lines.append("")
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
        self.work_dir = SERVER3_WORK_DIR
        self.logs_dir = SERVER3_LOGS_DIR
    
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
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
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
        
        Returns:
            Tuple of (run_uuid, work_directory)
        """
        # If user_id and project_id are available, use jailed directory structure
        # New jobs: AGOUTIC_DATA/users/{user_id}/{project_id}/{run_uuid}/
        # Legacy fallback: SERVER3_WORK_DIR/{run_uuid}/
        if user_id and project_id:
            work_dir = AGOUTIC_DATA / "users" / user_id / project_id / run_uuid
            work_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Using jailed work directory", work_dir=str(work_dir),
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
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            bam_file = Path(input_dir)
            if not bam_file.exists():
                raise RuntimeError(f"Unmapped BAM file not found: {input_dir}")
            
            bam_link = bams_dir / f"{sample_name}.bam"
            try:
                if bam_link.exists():
                    bam_link.unlink()
                bam_link.symlink_to(bam_file.resolve())
                logger.info("Created unmapped BAM symlink for remapping", link=str(bam_link), target=input_dir)
            except Exception as e:
                raise RuntimeError(f"Failed to create BAM symlink: {e}")
        
        elif entry_point == "modkit":
            # modkit: mapped BAM → modification calls
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            bam_file = Path(input_dir)
            if not bam_file.exists():
                raise RuntimeError(f"Mapped BAM file not found: {input_dir}")
            
            bam_link = bams_dir / f"{sample_name}.bam"
            try:
                if bam_link.exists():
                    bam_link.unlink()
                bam_link.symlink_to(bam_file.resolve())
                logger.info("Created mapped BAM symlink for modkit", link=str(bam_link), target=input_dir)
            except Exception as e:
                raise RuntimeError(f"Failed to create BAM symlink: {e}")
        
        elif entry_point == "annotateRNA":
            # annotateRNA: mapped RNA/cDNA BAM → transcript annotation
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            bam_file = Path(input_dir)
            if not bam_file.exists():
                raise RuntimeError(f"Mapped BAM file not found: {input_dir}")
            
            bam_link = bams_dir / f"{sample_name}.bam"
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
            bams_dir = work_dir / "bams"
            bams_dir.mkdir(parents=True, exist_ok=True)
            
            bam_file = Path(input_dir)
            if not bam_file.exists():
                raise RuntimeError(f"BAM file not found: {input_dir}")
            
            bam_link = bams_dir / f"{sample_name}.bam"
            try:
                if bam_link.exists():
                    bam_link.unlink()
                bam_link.symlink_to(bam_file.resolve())
                logger.info("Created BAM symlink", link=str(bam_link), target=input_dir)
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
        
        # Copy or create dogme.profile
        dogme_profile_src = DOGME_REPO / "dogme.profile"
        dogme_profile_dst = work_dir / "dogme.profile"
        
        if dogme_profile_src.exists():
            # Copy from repo
            import shutil
            shutil.copy2(dogme_profile_src, dogme_profile_dst)
            logger.debug("Copied dogme.profile", source=str(dogme_profile_src))
        else:
            # Create a minimal one
            dogme_profile_dst.write_text("# Dogme environment profile\n# Add environment variables here if needed\n")
            logger.debug("Created minimal dogme.profile")
        
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
            else:
                failed_marker = work_dir / ".nextflow_failed"
                error_file = work_dir / ".nextflow_error"
                
                # Try to extract error from stderr log
                stderr_file = self.logs_dir / f"{run_uuid}_stderr.log"
                error_msg = f"Process exited with code {returncode}"
                if stderr_file.exists():
                    with open(stderr_file) as f:
                        stderr_content = f.read()
                        if stderr_content:
                            error_msg = stderr_content[-500:]  # Last 500 chars
                
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
        # Check for completion markers
        success_marker = work_dir / ".nextflow_success"
        failed_marker = work_dir / ".nextflow_failed"
        running_marker = work_dir / ".nextflow_running"
        pid_file = work_dir / ".nextflow_pid"
        
        # Check completion markers first
        if success_marker.exists():
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
                    # Process died unexpectedly
                    running_marker.unlink()
                    failed_marker = work_dir / ".nextflow_failed"
                    failed_marker.write_text(f"Process died unexpectedly at {datetime.utcnow().isoformat()}\n")
                    
                    # Try to get error from stderr
                    stderr_file = self.logs_dir / f"{run_uuid}_stderr.log"
                    error_msg = "Process died unexpectedly"
                    if stderr_file.exists():
                        with open(stderr_file) as f:
                            content = f.read()
                            if content:
                                error_msg = content[-200:]  # Last 200 chars
                    
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
        
        # Parse trace.txt file for completed tasks - tab-separated with columns:
        # task_id hash native_id name status exit submit duration realtime %cpu peak_rss peak_vmem rchar wchar
        # Find trace file - it might be named trace.txt or {sample_name}_trace.txt
        trace_file = work_dir / "trace.txt"
        if not trace_file.exists():
            # Try to find any *_trace.txt file
            trace_files = list(work_dir.glob("*_trace.txt"))
            if trace_files:
                trace_file = trace_files[0]
        
        completed_tasks = []
        failed_tasks = []
        
        if trace_file.exists():
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
                        
                        # Only track mainWorkflow tasks
                        if not task_name.startswith('mainWorkflow:'):
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
                    
                    # Track task hashes we've seen
                    seen_hashes = set()
                    
                    for line in lines:
                        # Look for executor count to get total submitted
                        if 'executor >' in line and '(' in line:
                            try:
                                count_str = line.split('(')[1].split(')')[0]
                                submitted_count = max(submitted_count, int(count_str))
                            except:
                                pass
                        
                        # Look for task lines: [hash] taskName
                        if line.startswith('[') and ']' in line and 'mainWorkflow:' in line:
                            # Extract hash
                            hash_part = line.split(']')[0] + ']'
                            
                            # Skip placeholder lines [-        ]
                            if '/' not in hash_part:
                                continue
                            
                            # Extract task name
                            rest = line.split(']', 1)[1] if ']' in line else ''
                            task_name = rest.strip().split()[0] if rest.strip() else ''
                            
                            # Add number if present: mainWorkflow:doradoTask (1)
                            if '(' in rest and ')' in rest:
                                task_num = rest[rest.find('('):rest.find(')')+1]
                                if task_num not in task_name:
                                    task_name = task_name + ' ' + task_num
                            
                            if not task_name:
                                continue
                            
                            # Check if completed (has ✔)
                            is_completed = '✔' in line
                            
                            # Only add to running if not completed AND not already in completed_tasks
                            if not is_completed and task_name not in completed_tasks:
                                # Use hash to deduplicate
                                if hash_part not in seen_hashes:
                                    seen_hashes.add(hash_part)
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
