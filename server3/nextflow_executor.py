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
        reference_genome: str = "GRCh38",
        modifications: Optional[str] = None,
    ) -> dict:
        """
        Generate a Nextflow configuration dict for Dogme pipeline.
        
        Args:
            sample_name: Name of the sample
            mode: Dogme mode (DNA, RNA, or CDNA)
            input_dir: Path to pod5 input directory
            reference_genome: Genome version (GRCh38, mm39, etc.)
            modifications: Modification motifs to call (or empty string for cDNA)
        
        Returns:
            Dictionary representing nextflow.config parameters
        """
        genome_config = REFERENCE_GENOMES.get(reference_genome, {})
        fasta = genome_config.get("fasta", "")
        gtf = genome_config.get("gtf", "")
        
        config = {
            "params": {
                "sample_name": sample_name,
                "input": input_dir,
                "readType": mode,
                "genome": reference_genome,
                "fasta": fasta,
                "gtf": gtf if mode in ("RNA", "CDNA") else None,
                "outdir": str(SERVER3_WORK_DIR / f"{sample_name}_output"),
            },
            "process": {
                "maxRetries": 1,
                "errorStrategy": "finish",
                "withName": {
                    "basecall": {
                        "container": "nanopore/guppy:6.0.1",
                    },
                    "align": {
                        "container": "biocontainers/minimap2:2.24",
                    },
                }
            },
            "executor": {
                "name": "local",
                "cpus": 16,
                "memory": "64 GB",
            }
        }
        
        # Add modification calling only for DNA and RNA
        if mode == "DNA" and modifications:
            config["params"]["modifications"] = modifications
            config["params"]["modkit_enabled"] = True
        elif mode == "RNA" and modifications:
            config["params"]["modifications"] = modifications
        elif mode == "CDNA":
            config["params"]["modkit_enabled"] = False
        
        return config
    
    @staticmethod
    def write_config_file(
        config: dict,
        output_path: Path,
    ) -> Path:
        """Write configuration to a nextflow.config file."""
        # Convert dict to Groovy-like format
        groovy_config = NextflowConfig._dict_to_groovy(config)
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(groovy_config)
        
        return output_path
    
    @staticmethod
    def _dict_to_groovy(d: dict, indent: int = 0) -> str:
        """Convert Python dict to Nextflow Groovy config format."""
        lines = []
        ind = "    " * indent
        
        for key, value in d.items():
            if isinstance(value, dict):
                lines.append(f"{ind}{key} {{")
                lines.append(NextflowConfig._dict_to_groovy(value, indent + 1))
                lines.append(f"{ind}}}")
            elif isinstance(value, bool):
                lines.append(f"{ind}{key} = {str(value).lower()}")
            elif isinstance(value, (int, float)):
                lines.append(f"{ind}{key} = {value}")
            elif value is None:
                continue  # Skip None values
            else:
                # String
                lines.append(f'{ind}{key} = "{value}"')
        
        return "\n".join(lines)


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
        reference_genome: str = "GRCh38",
        modifications: Optional[str] = None,
    ) -> tuple[str, Path]:
        """
        Submit a Dogme/Nextflow job.
        
        Args:
            sample_name: Name of the sample
            mode: Dogme mode (DNA, RNA, CDNA)
            input_dir: Path to pod5 files
            reference_genome: Reference genome version
            modifications: Modification motifs
        
        Returns:
            Tuple of (run_uuid, work_directory)
        """
        run_uuid = str(uuid.uuid4())
        work_dir = self.work_dir / run_uuid
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate configuration
        config_dict = NextflowConfig.generate_config(
            sample_name=sample_name,
            mode=mode,
            input_dir=input_dir,
            reference_genome=reference_genome,
            modifications=modifications,
        )
        
        config_path = work_dir / "nextflow.config"
        NextflowConfig.write_config_file(config_dict, config_path)
        
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
