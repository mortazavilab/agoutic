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
# Server2 (ENCODELIB): ENCODE Portal data retrieval
# Default to ENCODELIB in same directory as agoutic code
_default_encodelib = AGOUTIC_CODE / "ENCODELIB"
ENCODELIB_PATH = Path(os.getenv("ENCODELIB_PATH", str(_default_encodelib)))
SERVER2_MCP_COMMAND = f"python {ENCODELIB_PATH}/encode_server.py"

# Server3 (Job Execution): Nextflow/Dogme pipeline execution
SERVER3_URL = os.getenv("SERVER3_URL", "http://localhost:8001")

# Server4 (Analysis): Results analysis engine
SERVER4_URL = os.getenv("SERVER4_URL", "http://localhost:8002")

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