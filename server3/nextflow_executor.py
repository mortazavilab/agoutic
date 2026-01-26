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

from server3.config import (
    DOGME_REPO,
    NEXTFLOW_BIN,
    SERVER3_WORK_DIR,
    SERVER3_LOGS_DIR,
    DogmeMode,
    JobStatus,
    REFERENCE_GENOMES,
    JOB_POLL_INTERVAL,
)

class NextflowConfig:
    """Generates Nextflow configuration for Dogme pipeline."""
    
    @staticmethod
    def generate_config(
        sample_name: str,
        mode: str,  # "DNA", "RNA", "CDNA"
        input_dir: str,
        reference_genome: str = "mm39",
        modifications: Optional[str] = None,
    ) -> str:
        """
        Generate a Nextflow configuration string for Dogme pipeline.
        
        Args:
            sample_name: Name of the sample
            mode: Dogme mode (DNA, RNA, or CDNA)
            input_dir: Path to pod5 input directory (not used in config generation)
            reference_genome: Genome version (GRCh38, mm39, etc.)
            modifications: Modification motifs to call (uses defaults if None)
        
        Returns:
            Groovy configuration string formatted for nextflow.config
        """
        # Get genome references
        genome_config = REFERENCE_GENOMES.get(reference_genome, REFERENCE_GENOMES["mm39"])
        fasta = genome_config.get("fasta", "/home/seyedam/genRefs/IGVFFI9282QLXO.fasta")
        gtf = genome_config.get("gtf", "/home/seyedam/genRefs/IGVFFI4777RDZK.gtf")
        
        # Determine modifications based on mode
        if modifications:
            mods = modifications
        elif mode == "DNA":
            mods = "5mCG_5hmCG,6mA"
        elif mode == "RNA":
            mods = "inosine_m6A_2OmeA,pseU_2OmeU,m5C_2OmeC,2OmeG"
        else:  # CDNA
            mods = None
        
        # Determine minCov based on mode
        min_cov = 1 if mode == "DNA" else 3
        
        # Build config string matching example format
        config_lines = []
        config_lines.append("params {")
        config_lines.append(f"    sample = '{sample_name}'")
        config_lines.append(f"    //readType can either be 'RNA', 'DNA' or 'CDNA'")
        config_lines.append(f"    readType = '{mode}'")
        config_lines.append(f"    // change this value if 0.9 is too strict")
        config_lines.append(f"    // if set to null or '' then modkit will determine its threshold by sampling reads.")
        config_lines.append(f"    modkitFilterThreshold = 0.9")
        
        # Add modifications if applicable
        if mods:
            config_lines.append(f"    // modification type should be set as necessary if different from 'inosine_m6A,pseU,m5C' for RNA and '5mCG_5hmCG,6mA' for DNA.")
            config_lines.append(f"    modifications = '{mods}'")
        
        config_lines.append(f"    //change setting if necessary")
        config_lines.append(f"    //minCov = 3 by default, but changed to 1 for microtest" if mode == "DNA" else f"    //change setting if necessary")
        config_lines.append(f"    minCov = {min_cov}")
        config_lines.append(f"    perMod = 5")
        config_lines.append(f"    // change if the launch directory is not where the pod5 and output directories should go")
        config_lines.append(f'    topDir = "${{launchDir}}"')
        config_lines.append(f"")
        config_lines.append(f'    scriptEnv = "${{launchDir}}/dogme.profile"')
        config_lines.append(f"")
        config_lines.append(f"    // needs to be modified to match the right genomic reference")
        config_lines.append(f"    genome_annot_refs = [")
        config_lines.append(f"        [name: 'mm39', genome: '{fasta}', annot: '{gtf}']")
        config_lines.append(f"    ]")
        config_lines.append(f"    ")
        config_lines.append(f"    kallistoIndex = '/home/seyedam/genRefs/mm39GencM36_k63.idx'")
        config_lines.append(f"    t2g = '/home/seyedam/genRefs/mm39GencM36_k63.t2g'")
        config_lines.append(f"")
        config_lines.append(f"    //default accuracy is sup")
        config_lines.append(f"    accuracy = \"sup\"")
        config_lines.append(f"    // change this value if 0.9 is too strict")
        config_lines.append(f"    // if set to null or '' then modkit will determine its threshold by sampling reads.")
        config_lines.append(f"    modkitFilterThreshold = 0.9 ")
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
        config_lines.append("    containerOptions = \"-v /home/seyedam:/home/seyedam \"")
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
        config_lines.append("        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam \"")
        config_lines.append("    }")
        config_lines.append("    ")
        config_lines.append("    withName: 'openChromatinTaskBg' {")
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        config_lines.append("        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam \"")
        config_lines.append("    }")
        config_lines.append("")
        config_lines.append("    withName: 'openChromatinTaskBed' {")
        config_lines.append("        memory = '9 GB'  // Increase if necessary")
        config_lines.append("        cpus = 4         // dorado is more GPU intensive than CPU intensive")
        config_lines.append("        containerOptions = \"--gpus all -v /home/seyedam:/home/seyedam \"")
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
        config_lines.append("    file = \"${params.sample}_timeline.html\"")
        config_lines.append("}")
        config_lines.append("")
        config_lines.append("report {")
        config_lines.append("    enabled = true")
        config_lines.append("    file = \"${params.sample}_report.html\"")
        config_lines.append("}")
        config_lines.append("")
        config_lines.append("trace {")
        config_lines.append("    enabled = true")
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
        sample_name: str,
        mode: str,
        input_dir: str,
        reference_genome: str = "mm39",
        modifications: Optional[str] = None,
    ) -> tuple[str, Path]:
        """
        Submit a Dogme/Nextflow job.
        
        Args:
            sample_name: Name of the sample
            mode: Dogme mode (DNA, RNA, CDNA)
            input_dir: Path to pod5 files
            reference_genome: Reference genome version (mm39, GRCh38, etc.)
            modifications: Modification motifs
        
        Returns:
            Tuple of (run_uuid, work_directory)
        """
        run_uuid = str(uuid.uuid4())
        work_dir = self.work_dir / run_uuid
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate configuration
        config_string = NextflowConfig.generate_config(
            sample_name=sample_name,
            mode=mode,
            input_dir=input_dir,
            reference_genome=reference_genome,
            modifications=modifications,
        )
        
        config_path = work_dir / "nextflow.config"
        NextflowConfig.write_config_file(config_string, config_path)
        
        # Log the submission
        log_file = self.logs_dir / f"{run_uuid}.log"
        
        # Build Nextflow command
        cmd = [
            str(self.nextflow_bin),
            "run",
            str(DOGME_REPO / "main.nf"),
            "-c", str(config_path),
            "-work-dir", str(work_dir / "work"),
            "-log", str(log_file),
            "-with-report", str(work_dir / "report.html"),
        ]
        
        # Submit job (fire and forget, track separately)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            # Detach and let it run
            # In production, you'd use a job scheduler like SLURM
            print(f"✅ Job submitted: {run_uuid}")
            print(f"   Command: {' '.join(cmd)}")
            
            return run_uuid, work_dir
            
        except Exception as e:
            raise RuntimeError(f"Failed to submit Nextflow job: {e}")
    
    async def check_status(self, run_uuid: str, work_dir: Path) -> dict:
        """
        Check the status of a running job.
        
        Returns dict with keys: status, progress_percent, message
        """
        # Check for completion markers
        success_marker = work_dir / ".nextflow_success"
        failed_marker = work_dir / ".nextflow_failed"
        running_marker = work_dir / ".nextflow_running"
        
        # Check timeline report for progress
        timeline_file = work_dir / "timeline.html"
        report_file = work_dir / "report.html"
        
        if success_marker.exists():
            return {
                "status": JobStatus.COMPLETED,
                "progress_percent": 100,
                "message": "Job completed successfully",
            }
        elif failed_marker.exists():
            error_msg = ""
            error_file = work_dir / ".nextflow_error"
            if error_file.exists():
                with open(error_file) as f:
                    error_msg = f.read()
            return {
                "status": JobStatus.FAILED,
                "progress_percent": 0,
                "message": f"Job failed: {error_msg}",
            }
        elif report_file.exists():
            # Parse progress from report
            try:
                with open(report_file) as f:
                    report_content = f.read()
                    # Simple heuristic: count completed processes
                    completed = report_content.count('<td class="completed')
                    total = max(1, report_content.count('<td class="'))
                    progress = int((completed / total) * 100) if total > 0 else 50
                return {
                    "status": JobStatus.RUNNING,
                    "progress_percent": progress,
                    "message": "Pipeline executing...",
                }
            except Exception:
                pass
        
        # Check if process still running
        log_file = self.logs_dir / f"{run_uuid}.log"
        if log_file.exists():
            with open(log_file) as f:
                logs = f.read()
                if "Completed" in logs or "completed" in logs:
                    return {
                        "status": JobStatus.COMPLETED,
                        "progress_percent": 100,
                        "message": "Job completed",
                    }
                elif "ERROR" in logs or "error" in logs:
                    return {
                        "status": JobStatus.FAILED,
                        "progress_percent": 0,
                        "message": "Job failed - see logs",
                    }
        
        return {
            "status": JobStatus.RUNNING,
            "progress_percent": 50,
            "message": "Checking job status...",
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
