import os
from pathlib import Path

# --- ROOT PATH CONFIGURATION ---
# AGOUTIC_CODE: Where the source code lives (this repository)
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))

# AGOUTIC_DATA: Where data, databases, and job outputs live
# Can be local (agoutic_code/data) or a dedicated storage location
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))

# --- DATA & STORAGE CONFIG (derived from AGOUTIC_DATA) ---
# DATABASE_URL is centralized in common.database
from common.database import DATABASE_URL  # noqa: E402

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

# Environment: "development" or "production"
# Controls security flags like cookie secure attribute
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Internal API secret for Cortex <-> Launchpad communication
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")

# Frontend URL for OAuth redirects - use environment variable for remote access
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")

# --- SKILLS & CODE CONFIG (derived from AGOUTIC_CODE) ---
SKILLS_DIR = AGOUTIC_CODE / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

# --- LLM CONFIGURATION ---
# Check environment variable first; fallback to localhost default if missing
LLM_URL = os.getenv("LLM_URL", "http://localhost:11434/v1")

# Context window size for LLM calls (tokens).
# Ollama defaults to a tiny 2048-4096 unless told otherwise.
# devstral supports 256k; we default to 131072 (128k) for safety.
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "32768"))

# The available models on your local machine
# Keys are the "nicknames" you use in code; Values are the exact Ollama tags.
LLM_MODELS = {
    "heavy": "devstral-2:latest",       # Your main high-IQ brain (74GB)
    "default": "devstral-small-2:latest",    # Faster, lighter checks (15GB)
    "coder": "qwen3-coder:latest",        # Specialized for writing code
    "backup": "gpt-oss:120b",             # Alternative heavy model
}

# --- SERVER INTEGRATION CONFIGURATION ---
# Default ENCODELIB location (for reference / fallback scripts):
_default_encodelib = AGOUTIC_CODE / "ENCODELIB"

# =============================================================================
# SERVICE REGISTRY - Internal MCP servers (Launchpad, Analyzer)
# =============================================================================
# Cortex connects directly to these via MCP over HTTP.
# Consortium MCP servers (ENCODE, etc.) are managed in atlas/config.py.

SERVICE_REGISTRY = {
    "launchpad": {
        "url": os.getenv("LAUNCHPAD_MCP_URL", "http://localhost:8002"),
        "rest_url": os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003"),
        "display_name": "Job Execution (Nextflow/Dogme)",
        "emoji": "\U0001f680",
        "table_columns": [],
        "count_field": None,
        "count_label": None,
        "skills": [
            "analyze_local_sample",
            "remote_execution",
        ],
        "fallback_patterns": {},
    },
    "analyzer": {
        "url": os.getenv("ANALYZER_MCP_URL", "http://localhost:8005"),
        "rest_url": os.getenv("ANALYZER_REST_URL", "http://localhost:8004"),
        "display_name": "Analysis Engine",
        "emoji": "\U0001f4ca",
        "table_columns": [],
        "count_field": None,
        "count_label": None,
        "skills": [
            "analyze_job_results",
            "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
            "enrichment_analysis",
        ],
        "fallback_patterns": {},
    },
    "edgepython": {
        "url": os.getenv("EDGEPYTHON_MCP_URL", "http://localhost:8007"),
        "display_name": "Differential Expression (edgePython)",
        "emoji": "\U0001f4c8",
        "table_columns": [],
        "count_field": None,
        "count_label": None,
        "skills": [
            "differential_expression",
        ],
        "fallback_patterns": {},
    },
}


def get_service_url(key: str) -> str:
    """Get the MCP URL for a service or consortium by key."""
    if key in SERVICE_REGISTRY:
        return SERVICE_REGISTRY[key]["url"]
    # Also check consortium registry for unified dispatch
    from atlas.config import CONSORTIUM_REGISTRY
    if key in CONSORTIUM_REGISTRY:
        return CONSORTIUM_REGISTRY[key]["url"]
    raise KeyError(f"Unknown service/consortium: {key}")


def get_source_for_skill(skill_key: str) -> tuple[str, str] | None:
    """
    Look up which consortium or service a skill belongs to.

    Returns:
        (source_key, source_type) e.g. ("encode", "consortium") or ("analyzer", "service")
        None if skill doesn't belong to any registered source.
    """
    # Check consortia (imported lazily to avoid circular imports)
    from atlas.config import CONSORTIUM_REGISTRY
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
    "welcome": "welcome/SKILL.md",
    
    # ENCODE skills
    "ENCODE_Search": "ENCODE_Search/SKILL.md",
    "ENCODE_LongRead": "ENCODE_LongRead/SKILL.md",
    
    # New Dogme Skills
    "run_dogme_dna": "run_dogme_dna/SKILL.md",
    "run_dogme_rna": "run_dogme_rna/SKILL.md",
    "run_dogme_cdna": "run_dogme_cdna/SKILL.md",
    
    # Local Intake / Dispatcher Skill
    "analyze_local_sample": "analyze_local_sample/SKILL.md",
    
    # Job Results Analysis Skill
    "analyze_job_results": "analyze_job_results/SKILL.md",
    
    # Download / File Intake Skill
    "download_files": "download_files/SKILL.md",
    
    # Differential Expression (edgePython)
    "differential_expression": "differential_expression/SKILL.md",

    # GO & Pathway Enrichment (edgePython)
    "enrichment_analysis": "enrichment_analysis/SKILL.md",

    # Remote Execution (HPC3/SLURM)
    "remote_execution": "remote_execution/SKILL.md",
}