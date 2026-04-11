"""
Atlas Configuration - Consortium Registry

Defines external consortium MCP servers (ENCODE, GEO, SRA, etc.)
that Cortex can route data queries to.

Internal service servers (Launchpad, Analyzer) are configured in cortex/config.py.

To add a new consortium:
  1. Add an entry to CONSORTIUM_REGISTRY below
  2. Create a skill markdown file in skills/
  3. Register the skill in cortex/config.py SKILLS_REGISTRY
  4. Start the consortium's MCP server (add to agoutic_servers.sh)
"""

import os
from pathlib import Path

# --- ROOT PATH (same convention as cortex/config.py) ---
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
            ("Assay", "assay"),
            ("Biosample", "biosample"),
            ("Target", "targets"),
        ],
        # Field for auto-summary counting (e.g., count by assay type)
        "count_field": "assay",
        "count_label": "assay type",

        # Skills that belong to this consortium
        "skills": ["ENCODE_Search", "ENCODE_LongRead"],

        # Tool name aliases: fix LLM-hallucinated tool names inside DATA_CALL tags
        # Maps wrong_name -> correct_name
        "tool_aliases": {
            "get_files_types": "get_files_by_type",
            "get_file_by_type": "get_files_by_type",
            "get_files_type": "get_files_by_type",
            "files_by_type": "get_files_by_type",
            "get_file_types_by_type": "get_files_by_type",
            # get_file_types only returns type-name strings, not file objects.
            # Redirect to get_files_by_type which returns full metadata.
            "get_file_types": "get_files_by_type",
            "file_types": "get_files_by_type",
            "get_filetypes": "get_files_by_type",
            "file_summary": "get_files_summary",
            "get_file_summary": "get_files_summary",
            "files_summary": "get_files_summary",
            "experiment": "get_experiment",
            "get_experiments": "get_experiment",
            "search_biosample": "search_by_biosample",
            "biosample_search": "search_by_biosample",
            "search_experiments": "search_by_biosample",
            "search_experiment": "search_by_biosample",
            "search_encode": "search_by_biosample",
            "search_data": "search_by_biosample",
            "encode_search": "search_by_biosample",
            "search": "search_by_biosample",
            "search_target": "search_by_target",
            "target_search": "search_by_target",
            "search_organism": "search_by_organism",
            "organism_search": "search_by_organism",
            "available_output_types": "get_available_output_types",
            "output_types": "get_available_output_types",
            "all_metadata": "get_all_metadata",
            "metadata": "get_all_metadata",
            "experiments": "list_experiments",
            "cache_stats": "get_cache_stats",
            "server_info": "get_server_info",
            "file_metadata": "get_file_metadata",
        },

        # Parameter name aliases: fix LLM-hallucinated parameter names
        # Maps {tool_name: {wrong_param: correct_param}}
        # NOTE: get_file_metadata and get_file_url both need TWO params:
        #   accession (experiment ENCSR) + file_accession (file ENCFF)
        #   Don't alias accession→file_accession here; routing is handled
        #   by _correct_tool_routing() in cortex/app.py
        "param_aliases": {
            "search_by_biosample": {
                "biosample": "search_term",
                "biosample_name": "search_term",
                "cell_line": "search_term",
                "sample": "search_term",
                "biosample_term_name": "search_term",
                "cell_type": "search_term",
                "tissue": "search_term",
            },
            "search_by_target": {
                "target_name": "target",
            },
        },

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
            r'Search Encode\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=search_by_biosample, \1]]',
            r'Search\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=search_by_biosample, \1]]',
            r'Search By Target\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=search_by_target, \1]]',
            r'Search By Organism\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=search_by_organism, \1]]',
            r'List Experiments\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=encode, tool=list_experiments, \1]]',
            r'Get Cache Stats\s*\(\)': r'[[DATA_CALL: consortium=encode, tool=get_cache_stats]]',
            r'Get Server Info\s*\(\)': r'[[DATA_CALL: consortium=encode, tool=get_server_info]]',
        },
    },

    "igvf": {
        # Connection
        "url": os.getenv("IGVF_MCP_URL", "http://localhost:8009"),

        # Display
        "display_name": "IGVF Portal",
        "emoji": "🔬",

        # Result formatting
        "table_columns": [
            ("Accession", "accession"),
            ("Assay", "assay"),
            ("Summary", "summary"),
            ("Status", "status"),
        ],
        "count_field": "assay",
        "count_label": "assay type",

        # Skills that belong to this consortium
        "skills": ["IGVF_Search"],

        # Tool name aliases: fix LLM-hallucinated tool names
        "tool_aliases": {
            "search_datasets": "search_measurement_sets",
            "search_experiments": "search_measurement_sets",
            "search_igvf": "search_measurement_sets",
            "search": "search_measurement_sets",
            "get_experiment": "get_dataset",
            "get_measurement_set": "get_dataset",
            "file_metadata": "get_file_metadata",
            "file_download": "get_file_download_url",
            "download_file": "get_file_download_url",
            "get_files": "get_files_for_dataset",
            "files_for_dataset": "get_files_for_dataset",
            "gene_search": "search_genes",
            "find_gene": "search_genes",
            "sample_search": "search_samples",
            "find_samples": "search_samples",
            "server_info": "get_server_info",
        },

        # Parameter name aliases
        "param_aliases": {
            "search_by_sample": {
                "biosample": "sample_term",
                "biosample_name": "sample_term",
                "cell_line": "sample_term",
                "sample": "sample_term",
                "sample_name": "sample_term",
                "search_term": "sample_term",
                "tissue": "sample_term",
            },
            "search_by_assay": {
                "assay": "assay_title",
                "assay_name": "assay_title",
                "assay_type": "assay_title",
            },
            "search_measurement_sets": {
                "biosample": "sample",
                "cell_line": "sample",
                "sample_name": "sample",
                "search_term": "sample",
                "tissue": "sample",
                "assay_title": "assay",
                "assay_type": "assay",
            },
            "search_genes": {
                "gene_symbol": "query",
                "gene_name": "query",
                "gene": "query",
                "symbol": "query",
            },
            "get_gene": {
                "query": "gene_id",
                "gene_symbol": "gene_id",
                "symbol": "gene_id",
                "gene_name": "gene_id",
            },
            "get_files_for_dataset": {
                "dataset_accession": "accession",
                "dataset": "accession",
                "file_set": "accession",
            },
            "get_file_metadata": {
                "accession": "file_accession",
                "file_id": "file_accession",
            },
            "get_file_download_url": {
                "accession": "file_accession",
                "file_id": "file_accession",
            },
            # Entries below ensure these canonical tool names are protected
            # from base-alias shadowing (search_files → find_file, etc.)
            "search_files": {
                "format": "file_format",
                "type": "content_type",
            },
            "search_analysis_sets": {
                "biosample": "sample",
                "cell_line": "sample",
                "tissue": "sample",
            },
            "search_prediction_sets": {
                "biosample": "sample",
                "cell_line": "sample",
                "tissue": "sample",
            },
        },

        # Fallback patterns
        "fallback_patterns": {
            r'Get Dataset\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=get_dataset, \1]]',
            r'Search Measurement Sets\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, \1]]',
            r'Search IGVF\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, \1]]',
            r'Search By Sample\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=search_by_sample, \1]]',
            r'Search By Assay\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=search_by_assay, \1]]',
            r'Search Genes\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=search_genes, \1]]',
            r'Get Gene\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=get_gene, \1]]',
            r'Get File Metadata\s*\(([^)]+)\)': r'[[DATA_CALL: consortium=igvf, tool=get_file_metadata, \1]]',
            r'Get Server Info\s*\(\)': r'[[DATA_CALL: consortium=igvf, tool=get_server_info]]',
        },
    },

    # --- TEMPLATE FOR ADDING NEW CONSORTIA ---
    # "geo": {
    #     "url": os.getenv("GEO_MCP_URL", "http://localhost:8010"),
    #     "display_name": "GEO (Gene Expression Omnibus)",
    #     "emoji": "📊",
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


def get_all_tool_aliases() -> dict[str, str]:
    """
    Collect all tool name aliases from all registered consortia.
    Returns a merged dict of wrong_name -> correct_name.
    Used to fix LLM-hallucinated tool names inside DATA_CALL tags.
    """
    aliases = {}
    for entry in CONSORTIUM_REGISTRY.values():
        aliases.update(entry.get("tool_aliases", {}))
    return aliases


def get_all_param_aliases() -> dict[str, dict[str, str]]:
    """
    Collect all parameter name aliases from all registered consortia.
    Returns a merged dict of {tool_name: {wrong_param: correct_param}}.
    Used to fix LLM-hallucinated parameter names in DATA_CALL tags.
    """
    aliases: dict[str, dict[str, str]] = {}
    for entry in CONSORTIUM_REGISTRY.values():
        for tool_name, mappings in entry.get("param_aliases", {}).items():
            aliases.setdefault(tool_name, {}).update(mappings)
    return aliases
