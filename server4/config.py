"""Configuration for Server4 (Analysis Server)."""

import os
from pathlib import Path

# --- ROOT PATH CONFIGURATION (same as server1) ---
# AGOUTIC_CODE: Where the source code lives (this repository)
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))

# AGOUTIC_DATA: Where data, databases, and job outputs live
# Can be local (agoutic_code/data) or a dedicated storage location
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))

# Server configuration
SERVER4_HOST = os.getenv("SERVER4_HOST", "0.0.0.0")
SERVER4_PORT = int(os.getenv("SERVER4_PORT", "8004"))
SERVER4_MCP_PORT = int(os.getenv("SERVER4_MCP_PORT", "8005"))

# --- DATA & STORAGE CONFIG (derived from AGOUTIC_DATA) ---
DB_FOLDER = AGOUTIC_DATA / "database"
DB_FILE = DB_FOLDER / "agoutic_v24.sqlite"

# Database configuration (shared with server1/server3)
# Server4 uses SYNC SQLAlchemy (not async like server1), so use plain sqlite://
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DB_FILE}"
)

# Server3 configuration (for job data access)
SERVER3_URL = os.getenv("SERVER3_URL", "http://127.0.0.1:8003")

# Work directory configuration (where job results are stored)
AGOUTIC_WORK_DIR = Path(os.getenv("AGOUTIC_WORK_DIR", AGOUTIC_DATA / "server3_work"))

# File parsing limits
MAX_PREVIEW_LINES = int(os.getenv("MAX_PREVIEW_LINES", "1000"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Supported file types
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".csv", ".tsv", ".bed", ".gtf", ".gff", ".html"}
SUPPORTED_ANALYSIS_EXTENSIONS = {".csv", ".tsv", ".bed"}

# Analysis configuration
BED_COLUMNS = ["chrom", "chromStart", "chromEnd", "name", "score", "strand", 
               "thickStart", "thickEnd", "itemRgb", "blockCount", "blockSizes", "blockStarts"]
