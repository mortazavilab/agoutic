"""
Server 2 Configuration - Consortium Registry

Defines external consortium MCP servers (ENCODE, GEO, SRA, etc.)
that Server 1 can route data queries to.

Internal service servers (Server 3, Server 4) are configured in server1/config.py.

To add a new consortium:
  1. Add an entry to CONSORTIUM_REGISTRY below
  2. Create a skill markdown file in skills/
  3. Register the skill in server1/config.py SKILLS_REGISTRY
  4. Start the consortium's MCP server (add to agoutic_servers.sh)
"""

import os
from pathlib import Path

# --- ROOT PATH (same convention as server1/config.py) ---
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))

# =============================================================================
# CONSORTIUM REGISTRY - External data source MCP servers
# =============================================================================
# Each consortium is an external MCP server that provides domain-specific tools.
# The client connects via HTTP/SSE to a running server process.

CONSORTIUM_REGISTRY = {
    "encode": {
        # Connection
        "url": os.getenv("ENCODE_MCP_URL", "http://localhost:8006"),

        # Display
        "display_name": "ENCODE Portal",
        "emoji": "🧬",

        # Result formatting: (column_header, field_key) pairs for table output
        "table_columns": [
            ("Accession", "accession"),
            ("Assay", "assay_title"),
            ("Biosample", "biosample_summary"),
            ("Target", "targets"),
        ],
        # Field for auto-summary counting (e.g., count by assay type)
        "count_field": "assay_title",
        "count_label": "assay type",

        # Skills that belong to this consortium
        "skills": ["ENCODE_Search", "ENCODE_LongRead"],

        # Fallback patterns: fix common LLM mistakes
        # Maps plain-text tool patterns to proper DATA_CALL tags
        "fallback_patterns": {
            r'Get Experiment\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=get_experiment, \1]]',
            r'Get All Metadata\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=get_all_metadata, \1]]',
            r'Get File Types\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=get_file_types, \1]]',
            r'Get Files By Type\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=get_files_by_type, \1]]',
            r'Get Available Output Types\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=get_available_output_types, \1]]',
            r'Get Files Summary\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=get_files_summary, \1]]',
            r'Search By Biosample\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=search_by_biosample, \1]]',
            r'Search By Target\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=search_by_target, \1]]',
            r'Search By Organism\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=search_by_organism, \1]]',
            r'List Experiments\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=list_experiments, \1]]',
            r'Get Cache Stats\s*\(\)': r'[[DATA_CALL: consortium=encode, tool=get_cache_stats]]',
            r'Get Server Info\s*\(\)': r'[[DATA_CALL: consortium=encode, tool=get_server_info]]',
        },
    },

    # --- TEMPLATE FOR ADDING NEW CONSORTIA ---
    # "geo": {
    #     "url": os.getenv("GEO_MCP_URL", "http://localhost:8007"),
    #     "display_name": "GEO (Gene Expression Omnibus)",
    #     "emoji": "🔬",
    #     "table_columns": [
    #         ("Accession", "accession"),
    #         ("Title", "title"),
    #         ("Organism", "organism"),
    #         ("Platform", "platform"),
    #     ],
    #     "count_field": "organism",
    #     "count_label": "organism",
    #     "skills": ["GEO_Search"],
    #     "fallback_patterns": {},
    # },
}


def get_consortium_url(key: str) -> str:
    """Get the MCP URL for a consortium by key."""
    if key in CONSORTIUM_REGISTRY:
        return CONSORTIUM_REGISTRY[key]["url"]
    raise KeyError(f"Unknown consortium: {key}")


def get_consortium_entry(key: str) -> dict:
    """Get the full registry entry for a consortium."""
    if key in CONSORTIUM_REGISTRY:
        return CONSORTIUM_REGISTRY[key]
    raise KeyError(f"Unknown consortium: {key}")


def get_all_fallback_patterns() -> dict[str, str]:
    """
    Collect all fallback patterns from all registered consortia.
    Returns a merged dict of regex pattern -> replacement.
    """
    patterns = {}
    for entry in CONSORTIUM_REGISTRY.values():
        patterns.update(entry.get("fallback_patterns", {}))
    return patterns
