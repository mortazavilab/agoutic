import os
from pathlib import Path

# --- DATA & STORAGE CONFIG ---
# This points to where heavy data and the DB live
AGOUTIC_ROOT = Path(os.getenv("AGOUTIC_ROOT", "/media/backup_disk/agoutic_root"))

DB_FOLDER = AGOUTIC_ROOT / "database"
DB_FILE = DB_FOLDER / "agoutic_v23.sqlite"

DB_FOLDER.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"

# --- SKILLS & CODE CONFIG ---
# This finds the root code folder (agoutic_code) based on where this file is.
CODE_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = CODE_ROOT / "skills"

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
    "ENCODE_LongRead": "ENCODE_LongRead.md"
}