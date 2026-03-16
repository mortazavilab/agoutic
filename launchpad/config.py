import os
from pathlib import Path
from enum import Enum

# --- ROOT PATH CONFIGURATION ---
# AGOUTIC_CODE: Where the source code lives (this repository)
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))

# AGOUTIC_DATA: Where data, databases, and job outputs live
# Can be local (agoutic_code/data) or a dedicated storage location
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))

# --- STORAGE & DATABASE CONFIG (derived from AGOUTIC_DATA) ---
LAUNCHPAD_WORK_DIR = AGOUTIC_DATA / "launchpad_work"
LAUNCHPAD_LOGS_DIR = AGOUTIC_DATA / "launchpad_logs"

# Create directories
LAUNCHPAD_WORK_DIR.mkdir(parents=True, exist_ok=True)
LAUNCHPAD_LOGS_DIR.mkdir(parents=True, exist_ok=True)

# DATABASE_URL is centralized in common.database
from common.database import DATABASE_URL  # noqa: E402

# Internal API secret for Cortex <-> Launchpad communication
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")

# --- NEXTFLOW / DOGME CONFIG (derived from AGOUTIC_CODE) ---
# Path to Dogme pipeline repository
DOGME_REPO = Path(os.getenv("DOGME_REPO", AGOUTIC_CODE / "dogme"))

# Path to Nextflow executable
NEXTFLOW_BIN = Path(os.getenv("NEXTFLOW_BIN", "/usr/local/bin/nextflow"))

# Max concurrent jobs
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))

# Default max concurrent GPU tasks (dorado, openChromatin) per pipeline run
# Controls Nextflow maxForks for GPU-bound processes
DEFAULT_MAX_GPU_TASKS = int(os.getenv("DEFAULT_MAX_GPU_TASKS", "1"))

# Job polling interval (seconds)
JOB_POLL_INTERVAL = int(os.getenv("JOB_POLL_INTERVAL", "10"))

# Job timeout (seconds) - 48 hours default
JOB_TIMEOUT = int(os.getenv("JOB_TIMEOUT", "172800"))

# --- DOGME MODES ---
class DogmeMode(str, Enum):
    DNA = "DNA"
    RNA = "RNA"
    CDNA = "CDNA"

# --- JOB STATUS ---
class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"

# --- REFERENCE GENOMES ---
REFERENCE_GENOMES = {
    "GRCh38": {
        "fasta": AGOUTIC_DATA / "references" / "GRCh38" / "GRCh38_no_alt_analysis_set_GCA_000001405.15.fasta",
        "gtf": AGOUTIC_DATA / "references" / "GRCh38" / "gencode.v29.primary_assembly.annotation_UCSC_names.gtf",
    },
    "mm39": {
        "fasta": AGOUTIC_DATA / "references" / "mm39" / "IGVFFI9282QLXO.fasta",
        "gtf": AGOUTIC_DATA / "references" / "mm39" / "IGVFFI4777RDZK.gtf",
    },
    "default": "GRCh38",
}

# --- MODIFICATION MOTIFS ---
DEFAULT_DNA_MODS = "5mCG_5hmCG,6mA"
DEFAULT_RNA_MODS = "inosine_m6A,pseU,m5C"
DEFAULT_CDNA_MODS = ""  # cDNA does not call modifications

# --- SLURM / REMOTE EXECUTION ---
# Default SLURM resource limits (override via environment)
SLURM_MAX_CPUS = int(os.getenv("SLURM_MAX_CPUS", "128"))
SLURM_MAX_MEMORY_GB = int(os.getenv("SLURM_MAX_MEMORY_GB", "1024"))
SLURM_MAX_WALLTIME_MINUTES = int(os.getenv("SLURM_MAX_WALLTIME_MINUTES", "4320"))  # 72h
SLURM_MAX_GPUS = int(os.getenv("SLURM_MAX_GPUS", "8"))
# Comma-separated whitelist (empty = any allowed)
SLURM_ALLOWED_PARTITIONS = [p for p in os.getenv("SLURM_ALLOWED_PARTITIONS", "").split(",") if p] or None
SLURM_ALLOWED_ACCOUNTS = [a for a in os.getenv("SLURM_ALLOWED_ACCOUNTS", "").split(",") if a] or None
