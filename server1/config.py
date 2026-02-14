import os
from pathlib import Path

# --- ROOT PATH CONFIGURATION ---
# AGOUTIC_CODE: Where the source code lives (this repository)
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))

# AGOUTIC_DATA: Where data, databases, and job outputs live
# Can be local (agoutic_code/data) or a dedicated storage location
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))

# --- DATA & STORAGE CONFIG (derived from AGOUTIC_DATA) ---
DB_FOLDER = AGOUTIC_DATA / "database"
DB_FILE = DB_FOLDER / "agoutic_v24.sqlite"  # Updated to v24 for multi-user support

DB_FOLDER.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"

# --- AUTH CONFIGURATION ---
# Google OAuth
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
# Allow override via environment variable for remote access
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")

# Super admin email (auto-approved on first login)
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "")

# Session configuration
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", os.urandom(32).hex())
SESSION_EXPIRES_HOURS = int(os.getenv("SESSION_EXPIRES_HOURS", "24"))

# Internal API secret for Server 1 <-> Server 3 communication
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")

# Frontend URL for OAuth redirects - use environment variable for remote access
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")

# --- SKILLS & CODE CONFIG (derived from AGOUTIC_CODE) ---
SKILLS_DIR = AGOUTIC_CODE / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

# --- LLM CONFIGURATION ---
# Check environment variable first; fallback to localhost default if missing
LLM_URL = os.getenv("LLM_URL", "http://localhost:11434/v1")

# The available models on your local machine
# Keys are the "nicknames" you use in code; Values are the exact Ollama tags.
LLM_MODELS = {
    "default": "devstral-2:latest",       # Your main high-IQ brain (74GB)
    "fast": "devstral-small-2:latest",    # Faster, lighter checks (15GB)
    "coder": "qwen3-coder:latest",        # Specialized for writing code
    "backup": "gpt-oss:120b",             # Alternative heavy model
}

# --- SERVER INTEGRATION CONFIGURATION ---
# Default ENCODELIB location (for reference / fallback scripts):
_default_encodelib = AGOUTIC_CODE / "ENCODELIB"

# =============================================================================
# SERVICE REGISTRY - Internal MCP servers (Server 3, Server 4)
# =============================================================================
# Server 1 connects directly to these via MCP over HTTP.
# Consortium MCP servers (ENCODE, etc.) are managed in server2/config.py.

SERVICE_REGISTRY = {
    "server3": {
        "url": os.getenv("SERVER3_MCP_URL", "http://localhost:8002"),
        "display_name": "Job Execution (Nextflow/Dogme)",
        "emoji": "\U0001f680",
        "table_columns": [],
        "count_field": None,
        "count_label": None,
        "skills": [
            "analyze_local_sample",
        ],
        "fallback_patterns": {},
    },
    "server4": {
        "url": os.getenv("SERVER4_MCP_URL", "http://localhost:8004"),
        "display_name": "Analysis Engine",
        "emoji": "\U0001f4ca",
        "table_columns": [],
        "count_field": None,
        "count_label": None,
        "skills": [
            "analyze_job_results",
            "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
        ],
        "fallback_patterns": {},
    },
}


def get_service_url(key: str) -> str:
    """Get the MCP URL for a service or consortium by key."""
    if key in SERVICE_REGISTRY:
        return SERVICE_REGISTRY[key]["url"]
    # Also check consortium registry for unified dispatch
    from server2.config import CONSORTIUM_REGISTRY
    if key in CONSORTIUM_REGISTRY:
        return CONSORTIUM_REGISTRY[key]["url"]
    raise KeyError(f"Unknown service/consortium: {key}")


def get_source_for_skill(skill_key: str) -> tuple[str, str] | None:
    """
    Look up which consortium or service a skill belongs to.

    Returns:
        (source_key, source_type) e.g. ("encode", "consortium") or ("server4", "service")
        None if skill doesn't belong to any registered source.
    """
    # Check consortia (imported lazily to avoid circular imports)
    from server2.config import CONSORTIUM_REGISTRY
    for key, entry in CONSORTIUM_REGISTRY.items():
        if skill_key in entry.get("skills", []):
            return (key, "consortium")
    # Check internal services
    for key, entry in SERVICE_REGISTRY.items():
        if skill_key in entry.get("skills", []):
            return (key, "service")
    return None

# --- GENOME ALIASES ---
# Map common genome names to canonical IDs
GENOME_ALIASES = {
    "human": "GRCh38",
    "hg38": "GRCh38",
    "grch38": "GRCh38",
    "mouse": "mm39",
    "mm10": "mm39",
    "mm39": "mm39",
}

# Available reference genomes (for UI display)
AVAILABLE_GENOMES = ["GRCh38", "mm39"]

# --- SKILL REGISTRY ---
# The menu of available workflows.
SKILLS_REGISTRY = {
    # Welcome / entry point
    "welcome": "Welcome.md",
    
    # ENCODE skills
    "ENCODE_Search": "ENCODE_Search.md",
    "ENCODE_LongRead": "ENCODE_LongRead.md",
    
    # New Dogme Skills
    "run_dogme_dna": "Dogme_DNA.md",
    "run_dogme_rna": "Dogme_RNA.md",
    "run_dogme_cdna": "Dogme_cDNA.md",
    
    # Local Intake / Dispatcher Skill
    "analyze_local_sample": "Local_Sample_Intake.md",
    
    # Job Results Analysis Skill
    "analyze_job_results": "Analyze_Job_Results.md"
}