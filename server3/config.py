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
SERVER3_WORK_DIR = AGOUTIC_DATA / "server3_work"
SERVER3_LOGS_DIR = AGOUTIC_DATA / "server3_logs"
DB_FOLDER = AGOUTIC_DATA / "database"
DB_FILE = DB_FOLDER / "agoutic_v23.sqlite"

# Create directories
SERVER3_WORK_DIR.mkdir(parents=True, exist_ok=True)
SERVER3_LOGS_DIR.mkdir(parents=True, exist_ok=True)
DB_FOLDER.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"

# --- NEXTFLOW / DOGME CONFIG (derived from AGOUTIC_CODE) ---
# Path to Dogme pipeline repository
DOGME_REPO = Path(os.getenv("DOGME_REPO", AGOUTIC_CODE / "dogme"))

# Path to Nextflow executable
NEXTFLOW_BIN = Path(os.getenv("NEXTFLOW_BIN", "/usr/local/bin/nextflow"))

# Max concurrent jobs
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))

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

# --- REFERENCE GENOMES ---
REFERENCE_GENOMES = {
    "GRCh38": {
        "fasta": "/data/references/GRCh38/GRCh38.fa",
        "gtf": "/data/references/GRCh38/gencode.v42.annotation.gtf",
    },
    "mm39": {
        "fasta": "/data/references/mm39/mm39.fa",
        "gtf": "/data/references/mm39/gencode.vM33.annotation.gtf",
    },
    "default": "GRCh38",
}

# --- MODIFICATION MOTIFS ---
DEFAULT_DNA_MODS = "5mCG_5hmCG,6mA"
DEFAULT_RNA_MODS = "inosine_m6A,pseU,m5C"
DEFAULT_CDNA_MODS = ""  # cDNA does not call modifications
