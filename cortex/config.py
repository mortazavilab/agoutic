import os
from pathlib import Path

from launchpad.config import REFERENCE_GENOMES  # noqa: E402

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
SESSION_EXPIRES_HOURS = int(os.getenv("SESSION_EXPIRES_HOURS", "72"))

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
WF_PORE_C_ENABLED = os.getenv("WF_PORE_C_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

# --- LLM CONFIGURATION ---
# Check environment variable first; fallback to localhost default if missing
LLM_URL = os.getenv("LLM_URL", "http://localhost:11434/v1")

# Context window size for LLM calls (tokens).
# Ollama defaults to a tiny 2048-4096 unless told otherwise.
# devstral supports 256k; we default to 131072 (128k) for safety.
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "32768"))

# The available models on your local machine.
# Keys are the friendly aliases surfaced in the UI/API; values are the exact
# Ollama tags used for inference.
LLM_MODELS = {
    "default": "gemma4:31b-it-qat",              # Default model (31B parameters, 20 GB RAM)
    "fast": "gemma4:12b-it-qat",    # Faster, lighter checks (12GB)
    "coder": "qwen3.6:35b-a3b-mtp-q8_0",        # Specialized for writing code
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
        "skills": [],
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
        "skills": [],
        "fallback_patterns": {},
    },
    "edgepython": {
        "url": os.getenv("EDGEPYTHON_MCP_URL", "http://localhost:8007"),
        "display_name": "Differential Expression (edgePython)",
        "emoji": "\U0001f4c8",
        "table_columns": [],
        "count_field": None,
        "count_label": None,
        "skills": [],
        "fallback_patterns": {},
    },
    "xgenepy": {
        "url": os.getenv("XGENEPY_MCP_URL", "http://localhost:8008"),
        "display_name": "Cis/Trans Analysis (XgenePy)",
        "emoji": "\U0001f9ec",
        "table_columns": [],
        "count_field": None,
        "count_label": None,
        "skills": [],
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
    from cortex.skill_manifest import get_manifest

    manifest = get_manifest(skill_key)
    if manifest and manifest.source_key and manifest.source_type:
        return (manifest.source_key, manifest.source_type)

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
# Derive canonical genomes from the Launchpad reference catalog so newly added
# references appear automatically in the UI and intake parser.
AVAILABLE_GENOMES = [
    str(genome)
    for genome, entry in REFERENCE_GENOMES.items()
    if genome != "default" and isinstance(entry, dict)
]

GENOME_ALIASES = {genome.lower(): genome for genome in AVAILABLE_GENOMES}
if "GRCh38" in AVAILABLE_GENOMES:
    GENOME_ALIASES.update({
        "human": "GRCh38",
        "hg38": "GRCh38",
        "grch38": "GRCh38",
    })
if "mm39" in AVAILABLE_GENOMES:
    GENOME_ALIASES.update({
        "mouse": "mm39",
        "mm10": "mm39",
    })

DEFAULT_MM39_HAPLOTYPE_FOUNDER_VCF_NAME = "mgp_REL2021_snps_founders.vcf.gz"


def canonical_reference_genome(reference_id: str | None) -> str | None:
    raw_value = str(reference_id or "").strip()
    if not raw_value:
        return None
    return GENOME_ALIASES.get(raw_value.lower()) or (raw_value if raw_value in AVAILABLE_GENOMES else None)


def default_haplotype_vcf_for_reference(reference_id: str | None) -> str | None:
    canonical = canonical_reference_genome(reference_id)
    if canonical != "mm39":
        return None

    entry = REFERENCE_GENOMES.get(canonical)
    if not isinstance(entry, dict):
        return None

    fasta_path = entry.get("fasta")
    if not fasta_path:
        return None

    return str(Path(fasta_path).expanduser().resolve().parent / DEFAULT_MM39_HAPLOTYPE_FOUNDER_VCF_NAME)


def get_reference_genome_catalog() -> dict:
    """Return canonical genome ids plus alias and asset metadata."""
    aliases_by_genome: dict[str, list[str]] = {genome: [] for genome in AVAILABLE_GENOMES}
    for alias, genome in GENOME_ALIASES.items():
        if genome not in aliases_by_genome:
            continue
        if alias.lower() == genome.lower():
            continue
        if alias not in aliases_by_genome[genome]:
            aliases_by_genome[genome].append(alias)

    default_genome = str(REFERENCE_GENOMES.get("default") or "").strip() or None
    items: list[dict[str, object]] = []
    for genome in AVAILABLE_GENOMES:
        entry = REFERENCE_GENOMES.get(genome)
        if not isinstance(entry, dict):
            entry = {}
        aliases = sorted(aliases_by_genome.get(genome, []))
        assets = {
            "fasta": bool(entry.get("fasta")),
            "gtf": bool(entry.get("gtf")),
            "kallisto_index": bool(entry.get("kallisto_index")),
            "kallisto_t2g": bool(entry.get("kallisto_t2g")),
        }
        label = genome if not aliases else f"{genome} (aliases: {', '.join(aliases)})"
        items.append(
            {
                "id": genome,
                "label": label,
                "aliases": aliases,
                "is_default": genome == default_genome,
                "assets": assets,
            }
        )

    return {
        "default": default_genome if default_genome in AVAILABLE_GENOMES else None,
        "genomes": list(AVAILABLE_GENOMES),
        "items": items,
    }

# --- SKILL REGISTRY ---
# Authoritative manifests live in skills/<skill_key>/manifest.yaml and are loaded
# by cortex.skill_manifest.py.
# SKILLS_REGISTRY is re-exported here for backward compatibility (key → path).
from cortex.skill_manifest import SKILLS_REGISTRY, skills_for_source  # noqa: E402


for source_key, entry in SERVICE_REGISTRY.items():
    entry["skills"] = [manifest.key for manifest in skills_for_source(source_key, "service")]