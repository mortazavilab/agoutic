"""Configuration for Analyzer (Analysis Server)."""

import os
from pathlib import Path

# --- ROOT PATH CONFIGURATION (same as cortex) ---
# AGOUTIC_CODE: Where the source code lives (this repository)
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))

# AGOUTIC_DATA: Where data, databases, and job outputs live
# Can be local (agoutic_code/data) or a dedicated storage location
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))

# Server configuration
ANALYZER_HOST = os.getenv("ANALYZER_HOST", "0.0.0.0")
ANALYZER_PORT = int(os.getenv("ANALYZER_PORT", "8004"))
ANALYZER_MCP_PORT = int(os.getenv("ANALYZER_MCP_PORT", "8005"))

# --- DATA & STORAGE CONFIG (derived from AGOUTIC_DATA) ---
# DATABASE_URL is centralized in common.database
from common.database import DATABASE_URL  # noqa: E402

# Launchpad configuration (for job data access)
LAUNCHPAD_URL = os.getenv("LAUNCHPAD_URL", "http://127.0.0.1:8003")

# Upstream edgePython MCP configuration.
# Analyzer will proxy DE tool calls to this service during the adapter migration.
EDGEPYTHON_UPSTREAM_URL = os.getenv("EDGEPYTHON_UPSTREAM_URL", "http://127.0.0.1:8007")
DE_RESULTS_SUBDIR = os.getenv("DE_RESULTS_SUBDIR", "de_results")
DE_SESSION_TTL_SECONDS = int(os.getenv("DE_SESSION_TTL_SECONDS", "3600"))

# Work directory configuration (where job results are stored)
AGOUTIC_WORK_DIR = Path(os.getenv("AGOUTIC_WORK_DIR", AGOUTIC_DATA / "launchpad_work"))

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
