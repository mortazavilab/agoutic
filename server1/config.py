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
DB_FILE = DB_FOLDER / "agoutic_v23.sqlite"

DB_FOLDER.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"

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

# --- SKILL REGISTRY ---
# The menu of available workflows.
SKILLS_REGISTRY = {
    # Original ENCODE skill
    "ENCODE_LongRead": "ENCODE_LongRead.md",
    
    # New Dogme Skills
    "run_dogme_dna": "Dogme_DNA.md",
    "run_dogme_rna": "Dogme_RNA.md",
    "run_dogme_cdna": "Dogme_cDNA.md",
    
    # Local Intake / Dispatcher Skill
    "analyze_local_sample": "Local_Sample_Intake.md"
}