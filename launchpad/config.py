import os
import json
from pathlib import Path
from enum import Enum


def _optional_int_env(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None:
        return default

    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "null", "unlimited"}:
        return default

    return int(cleaned)

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

# Container-internal modkit paths used by DNA mode dogme.profile.
DOGME_DNA_MODKITBASE = Path((os.getenv("DOGME_DNA_MODKITBASE") or "/usr/local/lib/modkit-cpu").strip())
DOGME_DNA_MODKITMODEL = Path(
    (
        os.getenv("DOGME_DNA_MODKITMODEL")
        or str(DOGME_DNA_MODKITBASE / "models" / "r1041_e82_400bps_hac_v5.2.0@v0.1.0")
    ).strip()
)

# Container-internal OpenChromatin GPU modkit paths used only for DNA OpenChromatin tasks.
DOGME_DNA_OPENCHROM_MODKITBASE = Path(
    (os.getenv("DOGME_DNA_OPENCHROM_MODKITBASE") or "/usr/local/lib/modkit-gpu").strip()
)
DOGME_DNA_OPENCHROM_BINARY_DIR = Path(
    (
        os.getenv("DOGME_DNA_OPENCHROM_BINARY_DIR")
        or str(DOGME_DNA_OPENCHROM_MODKITBASE / "dist_modkit_v0.5.0_5120ef7_tch")
    ).strip()
)
DOGME_DNA_OPENCHROM_MODEL = Path(
    (
        os.getenv("DOGME_DNA_OPENCHROM_MODEL")
        or str(DOGME_DNA_OPENCHROM_BINARY_DIR / "models" / "r1041_e82_400bps_hac_v5.2.0@v0.1.0")
    ).strip()
)
DOGME_DNA_OPENCHROM_LIBTORCH = Path(
    (
        os.getenv("DOGME_DNA_OPENCHROM_LIBTORCH")
        or str(DOGME_DNA_OPENCHROM_MODKITBASE / "libtorch")
    ).strip()
)

# Shared Apptainer image for SLURM DNA runs.
DOGME_DNA_SLURM_CONTAINER = (
    os.getenv("DOGME_DNA_SLURM_CONTAINER")
    or "/share/crsp/lab/seyedam/share/agoutic/container/dogme-pipeline-openchrom-gpu-bedtools.sif"
).strip()

# Path to Nextflow executable
NEXTFLOW_BIN = Path(os.getenv("NEXTFLOW_BIN", "/usr/local/bin/nextflow"))

# Max concurrent jobs
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))

# Default max concurrent GPU tasks (dorado, openChromatin) per pipeline run.
# None means no explicit Nextflow maxForks limit; Nextflow manages concurrency.
DEFAULT_MAX_GPU_TASKS = _optional_int_env("DEFAULT_MAX_GPU_TASKS")
MAX_GPU_TASKS_LIMIT = int(os.getenv("MAX_GPU_TASKS_LIMIT", "16"))
if DEFAULT_MAX_GPU_TASKS is not None and not 1 <= DEFAULT_MAX_GPU_TASKS <= MAX_GPU_TASKS_LIMIT:
    raise ValueError(
        f"DEFAULT_MAX_GPU_TASKS must be between 1 and {MAX_GPU_TASKS_LIMIT}, or unset for no maximum"
    )

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
        "kallisto_index": AGOUTIC_DATA / "references" / "GRCh38" / "hg38Genc47_k63.idx",
        "kallisto_t2g": AGOUTIC_DATA / "references" / "GRCh38" / "hg38Genc47_k63.t2g",
    },
    "mm39": {
        "fasta": AGOUTIC_DATA / "references" / "mm39" / "IGVFFI9282QLXO.fasta",
        "gtf": AGOUTIC_DATA / "references" / "mm39" / "IGVFFI4777RDZK.gtf",
        "kallisto_index": AGOUTIC_DATA / "references" / "mm39" / "mm39GencM36_k63.idx",
        "kallisto_t2g": AGOUTIC_DATA / "references" / "mm39" / "mm39GencM36_k63.t2g",
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
SLURM_DEFAULT_CPU_MEMORY_GB = int(os.getenv("SLURM_DEFAULT_CPU_MEMORY_GB", "64"))
SLURM_MAX_MEMORY_GB = int(os.getenv("SLURM_MAX_MEMORY_GB", "1024"))
SLURM_MIN_WALLTIME_MINUTES = int(os.getenv("SLURM_MIN_WALLTIME_MINUTES", "2880"))  # 48h
SLURM_MAX_WALLTIME_MINUTES = int(os.getenv("SLURM_MAX_WALLTIME_MINUTES", "4320"))  # 72h
SLURM_MAX_GPUS = int(os.getenv("SLURM_MAX_GPUS", "8"))
if SLURM_DEFAULT_CPU_MEMORY_GB > SLURM_MAX_MEMORY_GB:
    raise ValueError("SLURM_DEFAULT_CPU_MEMORY_GB cannot exceed SLURM_MAX_MEMORY_GB")
if SLURM_MIN_WALLTIME_MINUTES > SLURM_MAX_WALLTIME_MINUTES:
    raise ValueError("SLURM_MIN_WALLTIME_MINUTES cannot exceed SLURM_MAX_WALLTIME_MINUTES")
# Comma-separated whitelist (empty = any allowed)
SLURM_ALLOWED_PARTITIONS = [p for p in os.getenv("SLURM_ALLOWED_PARTITIONS", "").split(",") if p] or None
SLURM_ALLOWED_ACCOUNTS = [a for a in os.getenv("SLURM_ALLOWED_ACCOUNTS", "").split(",") if a] or None

# SSH transport hardening
# If unset, underlying SSH clients use their normal system defaults.
SSH_KNOWN_HOSTS = os.getenv("SSH_KNOWN_HOSTS", "").strip() or None
SSH_STRICT_HOST_KEY_CHECKING = os.getenv("SSH_STRICT_HOST_KEY_CHECKING", "true").strip().lower() not in {"0", "false", "no"}
SSH_AGENT_FORWARDING = os.getenv("SSH_AGENT_FORWARDING", "false").strip().lower() in {"1", "true", "yes"}
SSH_CONNECT_TIMEOUT_SECONDS = int(os.getenv("SSH_CONNECT_TIMEOUT_SECONDS", "600"))
SSH_CONNECTION_ATTEMPTS = int(os.getenv("SSH_CONNECTION_ATTEMPTS", "1"))

# Local auth broker sessions for per-user SSH access via `su`
LOCAL_AUTH_SESSION_TTL_SECONDS = int(os.getenv("LOCAL_AUTH_SESSION_TTL_SECONDS", "3600"))
LOCAL_AUTH_HELPER_START_TIMEOUT_SECONDS = int(os.getenv("LOCAL_AUTH_HELPER_START_TIMEOUT_SECONDS", "20"))
LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS = int(os.getenv("LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS", "3600"))
LOCAL_AUTH_SOCKET_DIR = Path(os.getenv("LOCAL_AUTH_SOCKET_DIR", AGOUTIC_DATA / "runtime" / "local_auth"))


# --- LOCAL SCRIPT EXECUTION SAFETY ---
# Deny-by-default allowlist for standalone script execution.
# Only explicit script_id entries from SCRIPT_ALLOWLIST_IDS and explicit script_path
# under SCRIPT_ALLOWLIST_ROOTS are runnable.
_script_roots_raw = [
    item.strip()
    for item in os.getenv("LAUNCHPAD_SCRIPT_ALLOWLIST_ROOTS", "").split(os.pathsep)
    if item.strip()
]
SCRIPT_ALLOWLIST_ROOTS = [Path(item).expanduser().resolve() for item in _script_roots_raw]

_script_ids_raw = os.getenv("LAUNCHPAD_SCRIPT_ALLOWLIST_IDS", "{}")
try:
    _script_id_map = json.loads(_script_ids_raw)
except json.JSONDecodeError:
    _script_id_map = {}

if not isinstance(_script_id_map, dict):
    _script_id_map = {}

SCRIPT_ALLOWLIST_IDS = {
    str(key): Path(str(value)).expanduser().resolve()
    for key, value in _script_id_map.items()
    if str(key).strip() and str(value).strip()
}

# --- AUTO-DISCOVER SKILL SCRIPTS ---
# Scan skills/<skill_key>/scripts/ directories for runnable Python scripts.
# Discovered scripts are merged into SCRIPT_ALLOWLIST_IDS (keyed as
# "<skill_key>/<script_stem>") and their parent directories are added to
# SCRIPT_ALLOWLIST_ROOTS.  Env-var entries always take precedence.
_SKILLS_DIR = AGOUTIC_CODE / "skills"
if _SKILLS_DIR.is_dir():
    for _skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not _skill_dir.is_dir():
            continue
        _scripts_dir = _skill_dir / "scripts"
        if not _scripts_dir.is_dir():
            continue
        _resolved_scripts_dir = _scripts_dir.resolve()
        if _resolved_scripts_dir not in SCRIPT_ALLOWLIST_ROOTS:
            SCRIPT_ALLOWLIST_ROOTS.append(_resolved_scripts_dir)
        for _script_file in sorted(_scripts_dir.glob("*.py")):
            if _script_file.name.startswith("_"):
                continue
            _script_id = f"{_skill_dir.name}/{_script_file.stem}"
            if _script_id not in SCRIPT_ALLOWLIST_IDS:
                SCRIPT_ALLOWLIST_IDS[_script_id] = _script_file.resolve()
