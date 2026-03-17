"""
Launchpad MCP Tool Definitions
Exposes Dogme/Nextflow job management as Model Context Protocol tools.

This allows Cortex's LLM Agent to directly invoke job functions as part
of its planning and reasoning process.
"""
from typing import Optional
import json
import httpx

class LaunchpadMCPTools:
    """MCP tools for Launchpad job management."""
    
    def __init__(self, server_url: str = None):
        import os
        default_url = os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
        self.server_url = (server_url or default_url).rstrip("/")
        self.timeout = 30.0
        # Internal API secret for Launchpad authentication
        self._api_secret = os.getenv("INTERNAL_API_SECRET", "")
    
    def _headers(self) -> dict:
        """Build request headers including internal auth secret."""
        headers = {}
        if self._api_secret:
            headers["X-Internal-Secret"] = self._api_secret
        return headers
    
    async def submit_dogme_job(
        self,
        project_id: str,
        sample_name: str,
        mode: str,
        input_directory: str,
        reference_genome: str | list[str] = "GRCh38",
        modifications: Optional[str] = None,
        input_type: Optional[str] = None,
        entry_point: Optional[str] = None,
        modkit_filter_threshold: Optional[float] = None,
        min_cov: Optional[int] = None,
        per_mod: Optional[int] = None,
        accuracy: Optional[str] = None,
        max_gpu_tasks: Optional[int] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        project_slug: Optional[str] = None,
        resume_from_dir: Optional[str] = None,
        execution_mode: str = "local",
        ssh_profile_id: Optional[str] = None,
        slurm_account: Optional[str] = None,
        slurm_partition: Optional[str] = None,
        slurm_cpus: Optional[int] = None,
        slurm_memory_gb: Optional[int] = None,
        slurm_walltime: Optional[str] = None,
        slurm_gpus: Optional[int] = None,
        slurm_gpu_type: Optional[str] = None,
        remote_input_path: Optional[str] = None,
        remote_work_path: Optional[str] = None,
        remote_output_path: Optional[str] = None,
        result_destination: Optional[str] = None,
    ) -> dict:
        """
        Submit a Dogme/Nextflow analysis job to Launchpad.
        
        This tool allows the Agent to request genomic analysis of samples
        in DNA, RNA, or cDNA mode.
        
        Args:
            project_id: Unique project identifier
            sample_name: Name/ID of the sample
            mode: Analysis mode - "DNA", "RNA", or "CDNA"
            input_directory: Path to input data (pod5, bam, or fastq files)
            reference_genome: Reference genome(s) — single string or list for
                parallel multi-genome (e.g., "GRCh38" or ["GRCh38", "mm39"])
            modifications: Optional modification motifs to call
                - DNA: "5mCG_5hmCG,6mA"
                - RNA: "inosine_m6A,pseU,m5C"
                - cDNA: None (not supported)
            input_type: Input file type — "pod5", "bam", or "fastq" (default: "pod5")
            entry_point: Optional pipeline entry point
            modkit_filter_threshold: Optional modkit filter threshold (0.0-1.0)
            min_cov: Optional minimum coverage
            per_mod: Optional per-modification threshold
            accuracy: Optional basecalling accuracy level (e.g., "sup", "hac")
            max_gpu_tasks: Optional max concurrent GPU tasks (dorado/openChromatin) per run (default: 1)
        
        Returns:
            {"run_uuid": str, "sample_name": str, "status": str, "work_directory": str}
            
        Raises:
            Exception: If job submission fails
        """
        payload = {
            "project_id": project_id,
            "sample_name": sample_name,
            "mode": mode,
            "input_directory": input_directory,
            "reference_genome": reference_genome,
            "execution_mode": execution_mode,
        }
        # Add optional parameters if provided
        if modifications:
            payload["modifications"] = modifications
        if input_type is not None:
            payload["input_type"] = input_type
        if entry_point is not None:
            payload["entry_point"] = entry_point
        if modkit_filter_threshold is not None:
            payload["modkit_filter_threshold"] = modkit_filter_threshold
        if min_cov is not None:
            payload["min_cov"] = min_cov
        if per_mod is not None:
            payload["per_mod"] = per_mod
        if accuracy is not None:
            payload["accuracy"] = accuracy
        if max_gpu_tasks is not None:
            payload["max_gpu_tasks"] = max_gpu_tasks
        if user_id is not None:
            payload["user_id"] = user_id
        if username is not None:
            payload["username"] = username
        if project_slug is not None:
            payload["project_slug"] = project_slug
        if resume_from_dir is not None:
            payload["resume_from_dir"] = resume_from_dir
        if ssh_profile_id is not None:
            payload["ssh_profile_id"] = ssh_profile_id
        if slurm_account is not None:
            payload["slurm_account"] = slurm_account
        if slurm_partition is not None:
            payload["slurm_partition"] = slurm_partition
        if slurm_cpus is not None:
            payload["slurm_cpus"] = slurm_cpus
        if slurm_memory_gb is not None:
            payload["slurm_memory_gb"] = slurm_memory_gb
        if slurm_walltime is not None:
            payload["slurm_walltime"] = slurm_walltime
        if slurm_gpus is not None:
            payload["slurm_gpus"] = slurm_gpus
        if slurm_gpu_type is not None:
            payload["slurm_gpu_type"] = slurm_gpu_type
        if remote_input_path is not None:
            payload["remote_input_path"] = remote_input_path
        if remote_work_path is not None:
            payload["remote_work_path"] = remote_work_path
        if remote_output_path is not None:
            payload["remote_output_path"] = remote_output_path
        if result_destination is not None:
            payload["result_destination"] = result_destination
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.server_url}/jobs/submit",
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to submit job: {str(e)}")
    
    async def check_nextflow_status(self, run_uuid: str) -> dict:
        """
        Check the current status of a Nextflow job.
        
        This tool allows the Agent to monitor job progress during execution.
        
        Args:
            run_uuid: The job UUID returned from submit_dogme_job
        
        Returns:
            {
                "run_uuid": str,
                "status": "PENDING" | "RUNNING" | "COMPLETED" | "FAILED",
                "progress_percent": int (0-100),
                "message": str
            }
        
        Raises:
            Exception: If job not found or status check fails
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/jobs/{run_uuid}/status",
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                if response.status_code == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to check status: {str(e)}")
    
    async def get_dogme_report(self, run_uuid: str) -> dict:
        """
        Retrieve the complete analysis results and report for a completed job.
        
        This tool is called after a job completes to get the final results,
        including output files, statistics, and analysis summaries.
        
        Args:
            run_uuid: The job UUID
        
        Returns:
            {
                "run_uuid": str,
                "project_id": str,
                "sample_name": str,
                "mode": str,
                "status": str,
                "progress_percent": int,
                "output_directory": str,
                "report": dict (job results),
                "error_message": Optional[str]
            }
        
        Raises:
            Exception: If job not found
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/jobs/{run_uuid}",
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                if response.status_code == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to get report: {str(e)}")
    
    async def submit_dogme_nextflow(
        self,
        sample_name: str,
        input_dir: str,
        mode: str = "DNA",
        reference_genome: str = "GRCh38",
        modifications: Optional[str] = None,
    ) -> str:
        """
        Convenience tool: Submit job and return UUID immediately.
        
        This is a simplified version of submit_dogme_job that generates
        a project ID automatically and focuses on core parameters.
        
        Args:
            sample_name: Sample identifier
            input_dir: Path to input data
            mode: Analysis mode (DNA, RNA, CDNA)
            reference_genome: Reference genome
            modifications: Optional modification motifs
        
        Returns:
            run_uuid: Job identifier for tracking
        """
        import uuid
        project_id = f"auto_{uuid.uuid4().hex[:8]}"
        
        result = await self.submit_dogme_job(
            project_id=project_id,
            sample_name=sample_name,
            mode=mode,
            input_directory=input_dir,
            reference_genome=reference_genome,
            modifications=modifications,
        )
        return result["run_uuid"]
    
    async def find_pod5_directory(self, query: str) -> dict:
        """
        Locate pod5 files for a sample query.
        
        This tool helps the Agent find input data directories.
        In future versions, could search mounted storage or databases.
        
        Args:
            query: Sample name, path, or identifier to search for
        
        Returns:
            {
                "found": bool,
                "path": Optional[str],
                "file_count": int,
                "total_size_gb": float,
                "message": str
            }
        
        Note:
            Currently a placeholder. In production, would search:
            - Local pod5 directories
            - Network storage
            - Sample database
        """
        from pathlib import Path
        
        # Check if query is already a valid path
        query_path = Path(query)
        if query_path.exists() and query_path.is_dir():
            pod5_files = list(query_path.glob("*.pod5"))
            if pod5_files:
                total_size = sum(f.stat().st_size for f in pod5_files)
                return {
                    "found": True,
                    "path": str(query_path),
                    "file_count": len(pod5_files),
                    "total_size_gb": total_size / (1024**3),
                    "message": f"Found {len(pod5_files)} pod5 files in {query}"
                }
        
        # Return not found (in production would search more broadly)
        return {
            "found": False,
            "path": None,
            "file_count": 0,
            "total_size_gb": 0,
            "message": f"No pod5 files found for query: {query}"
        }
    
    async def generate_dogme_config(
        self,
        sample_name: str,
        read_type: str,
        genome: str = "GRCh38",
        modifications: Optional[str] = None,
    ) -> dict:
        """
        Generate Nextflow configuration for a Dogme pipeline run.
        
        This tool allows the Agent to preview the configuration that will
        be used for an analysis job.
        
        Args:
            sample_name: Sample identifier
            read_type: Analysis mode (DNA, RNA, CDNA)
            genome: Reference genome
            modifications: Modification motifs
        
        Returns:
            Configuration dict (Groovy format when written to file)
        
        Note:
            This is informational - actual config is generated by submit_dogme_job
        """
        from launchpad.nextflow_executor import NextflowConfig
        
        config = NextflowConfig.generate_config(
            sample_name=sample_name,
            mode=read_type,
            input_dir="<will be provided on submission>",
            reference_genome=genome,
            modifications=modifications,
        )
        
        return {
            "sample_name": sample_name,
            "read_type": read_type,
            "genome": genome,
            "modifications": modifications,
            "config": json.dumps(config, indent=2),
            "message": f"Config generated for {sample_name} ({read_type} mode)"
        }
    
    async def scaffold_dogme_dir(
        self,
        sample_name: str,
        input_dir: str,
    ) -> dict:
        """
        Create directory structure for Dogme analysis.
        
        Prepares the workspace for a job by creating required directories
        and validating the setup.
        
        Args:
            sample_name: Sample identifier
            input_dir: Path to input data
        
        Returns:
            {
                "success": bool,
                "work_dir": str,
                "message": str
            }
        """
        from pathlib import Path
        
        # Validate input directory
        input_path = Path(input_dir)
        if not input_path.exists():
            return {
                "success": False,
                "work_dir": None,
                "message": f"Input directory not found: {input_dir}"
            }
        
        # In production, would create the actual directory structure
        # For now, return success if input exists
        return {
            "success": True,
            "work_dir": str(input_path),
            "message": f"Workspace validated for {sample_name}"
        }

    async def get_job_logs(
        self,
        run_uuid: str,
        limit: int = 50,
    ) -> dict:
        """
        Get recent log entries for a running or completed job.
        
        Args:
            run_uuid: The job UUID
            limit: Maximum number of log entries to return (default: 50)
        
        Returns:
            {"logs": list[str]} — list of recent log lines
        
        Raises:
            Exception: If job not found or logs unavailable
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/jobs/{run_uuid}/logs",
                    params={"limit": limit},
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                if response.status_code == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to get logs: {str(e)}")

    async def delete_job_data(
        self,
        run_uuid: str,
    ) -> dict:
        """
        Delete the workflow folder and archive the DB record for a completed,
        failed, or cancelled job.

        Args:
            run_uuid: The job UUID

        Returns:
            {"status": "deleted", "run_uuid": str, "message": str, "deleted_path": str|None, "file_count": int}

        Raises:
            Exception: If job not found or still running
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.server_url}/jobs/{run_uuid}",
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                if response.status_code == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                if response.status_code == 400:
                    raise RuntimeError(response.json().get("detail", "Cannot delete job"))
                response.raise_for_status()
                return response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to delete job data: {str(e)}")

    async def get_job_debug(
        self,
        run_uuid: str,
    ) -> dict:
        """
        Get detailed debug information for a job.
        
        Includes work directory contents, Nextflow logs, process details,
        and error traces for troubleshooting failed or stalled jobs.
        
        Args:
            run_uuid: The job UUID
        
        Returns:
            Dict with debug info (work_dir, log files, process state, etc.)
        
        Raises:
            Exception: If job not found
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/jobs/{run_uuid}/debug",
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                if response.status_code == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to get debug info: {str(e)}")


# Tool registry for MCP server
TOOL_REGISTRY = {
    "submit_dogme_job": {
        "description": "Submit a Dogme/Nextflow analysis job for DNA, RNA, or cDNA samples. Input can be pod5, bam, or fastq. Supports multiple reference genomes in parallel.",
        "tool_function": "submit_dogme_job",
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_name": {"type": "string", "description": "Sample name/ID"},
                "mode": {"type": "string", "enum": ["DNA", "RNA", "CDNA"], "description": "Analysis mode"},
                "input_directory": {"type": "string", "description": "Path to input data (pod5, bam, or fastq files)"},
                "reference_genome": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}}
                    ],
                    "description": "Reference genome(s) — single string or list for parallel multi-genome (e.g., ['GRCh38', 'mm39'])"
                },
                "input_type": {"type": "string", "enum": ["pod5", "bam", "fastq"], "description": "Type of input files (default: pod5)"},
                "modifications": {"type": "string", "description": "Modification motifs to call (optional)"},
                "max_gpu_tasks": {"type": "integer", "description": "Max concurrent GPU tasks (dorado/openChromatin) per pipeline run (default: 1)"},
                "execution_mode": {"type": "string", "enum": ["local", "slurm"], "description": "Execution backend to use (default: local)"},
                "ssh_profile_id": {"type": "string", "description": "Saved SSH profile to use for SLURM execution"},
                "slurm_account": {"type": "string", "description": "SLURM account/allocation"},
                "slurm_partition": {"type": "string", "description": "SLURM partition"},
                "slurm_cpus": {"type": "integer", "description": "Requested CPU count"},
                "slurm_memory_gb": {"type": "integer", "description": "Requested memory in GB"},
                "slurm_walltime": {"type": "string", "description": "Requested walltime (HH:MM:SS or D-HH:MM:SS)"},
                "slurm_gpus": {"type": "integer", "description": "Requested GPU count"},
                "slurm_gpu_type": {"type": "string", "description": "Optional GPU type"},
                "remote_input_path": {"type": "string", "description": "Remote path for staged inputs"},
                "remote_work_path": {"type": "string", "description": "Remote Nextflow work directory"},
                "remote_output_path": {"type": "string", "description": "Remote results directory"},
                "result_destination": {"type": "string", "enum": ["local", "remote", "both"], "description": "Where final outputs should be kept"},
            },
            "required": ["sample_name", "mode", "input_directory"],
        }
    },
    "check_nextflow_status": {
        "description": "Check the current status of a running Nextflow job",
        "tool_function": "check_nextflow_status",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_uuid": {"type": "string", "description": "Job UUID"},
            },
            "required": ["run_uuid"],
        }
    },
    "get_dogme_report": {
        "description": "Get the final analysis results and report for a completed job",
        "tool_function": "get_dogme_report",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_uuid": {"type": "string", "description": "Job UUID"},
            },
            "required": ["run_uuid"],
        }
    },
    "submit_dogme_nextflow": {
        "description": "Simplified job submission that generates a project ID automatically",
        "tool_function": "submit_dogme_nextflow",
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_name": {"type": "string", "description": "Sample name"},
                "input_dir": {"type": "string", "description": "Input directory path"},
                "mode": {"type": "string", "enum": ["DNA", "RNA", "CDNA"], "description": "Analysis mode"},
                "reference_genome": {"type": "string", "description": "Reference genome"},
                "modifications": {"type": "string", "description": "Modifications (optional)"},
            },
            "required": ["sample_name", "input_dir"],
        }
    },
    "find_pod5_directory": {
        "description": "Locate pod5 files for a sample",
        "tool_function": "find_pod5_directory",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Sample name or path to search for"},
            },
            "required": ["query"],
        }
    },
    "generate_dogme_config": {
        "description": "Generate and preview Nextflow configuration for a job",
        "tool_function": "generate_dogme_config",
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_name": {"type": "string", "description": "Sample name"},
                "read_type": {"type": "string", "enum": ["DNA", "RNA", "CDNA"], "description": "Read type"},
                "genome": {"type": "string", "description": "Reference genome"},
                "modifications": {"type": "string", "description": "Modifications (optional)"},
            },
            "required": ["sample_name", "read_type"],
        }
    },
    "scaffold_dogme_dir": {
        "description": "Create and validate directory structure for Dogme analysis",
        "tool_function": "scaffold_dogme_dir",
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_name": {"type": "string", "description": "Sample name"},
                "input_dir": {"type": "string", "description": "Input directory path"},
            },
            "required": ["sample_name", "input_dir"],
        }
    },
    "get_job_logs": {
        "description": "Get recent log entries for a job",
        "tool_function": "get_job_logs",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_uuid": {"type": "string", "description": "Job UUID"},
                "limit": {"type": "integer", "description": "Max log entries to return (default: 50)"},
            },
            "required": ["run_uuid"],
        }
    },
    "get_job_debug": {
        "description": "Get detailed debug information for troubleshooting a job",
        "tool_function": "get_job_debug",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_uuid": {"type": "string", "description": "Job UUID"},
            },
            "required": ["run_uuid"],
        }
    },
    "delete_job_data": {
        "description": "Delete the workflow folder and archive a completed, failed, or cancelled job. Use when the user asks to delete or clean up a workflow/job.",
        "tool_function": "delete_job_data",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_uuid": {"type": "string", "description": "Job UUID to delete"},
            },
            "required": ["run_uuid"],
        }
    },
}
