"""
ENCODE Tool Schemas — Machine-readable contracts for all ENCODE MCP tools.

These schemas tell the LLM exactly what parameters each tool accepts,
their types, and critical warnings (e.g., ENCSR vs ENCFF).
Cortex fetches these at startup and injects a compact version into
the system prompt so the LLM always has an authoritative reference.
"""

TOOL_SCHEMAS: dict[str, dict] = {
    "get_experiment": {
        "description": "Get details for a single ENCODE experiment by accession.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Experiment accession (ENCSR format, e.g. ENCSR160HKZ). NEVER pass ENCFF file accessions here — use get_file_metadata instead.",
                    "pattern": "^ENCSR[0-9A-Z]{6}$",
                },
            },
            "required": ["accession"],
        },
    },
    "search_by_biosample": {
        "description": "Search for experiments by biosample (cell line, tissue, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Biosample name (e.g. 'K562', 'liver', 'HepG2'). NOT an ENCSR accession.",
                },
                "organism": {
                    "type": "string",
                    "description": "Species filter: 'Homo sapiens' or 'Mus musculus'. Omit to search both.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "assay_title": {
                    "type": "string",
                    "description": "Optional assay filter (e.g. 'total RNA-seq', 'ATAC-seq').",
                },
            },
            "required": ["search_term"],
        },
    },
    "search_by_assay": {
        "description": "Search for experiments by assay type across organisms.",
        "parameters": {
            "type": "object",
            "properties": {
                "assay_title": {
                    "type": "string",
                    "description": "Assay type (e.g. 'total RNA-seq', 'ATAC-seq', 'TF ChIP-seq', 'long read RNA-seq').",
                },
                "organism": {
                    "type": "string",
                    "description": "Species filter: 'Homo sapiens' or 'Mus musculus'. Omit to get both.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "target": {
                    "type": "string",
                    "description": "Optional target protein or histone mark filter.",
                },
            },
            "required": ["assay_title"],
        },
    },
    "search_by_target": {
        "description": "Search for experiments by target protein or histone modification.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target name (e.g. 'CTCF', 'H3K27ac', 'POLR2A').",
                },
                "organism": {
                    "type": "string",
                    "description": "Species filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
            },
            "required": ["target"],
        },
    },
    "search_by_organism": {
        "description": "Search for all experiments in a specific organism.",
        "parameters": {
            "type": "object",
            "properties": {
                "organism": {
                    "type": "string",
                    "description": "Species name.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
            },
            "required": ["organism"],
        },
    },
    "get_files_by_type": {
        "description": "Get files for an experiment filtered by file type.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Experiment accession (ENCSR format). NEVER pass ENCFF here.",
                    "pattern": "^ENCSR[0-9A-Z]{6}$",
                },
                "file_type": {
                    "type": "string",
                    "description": "File format filter (e.g. 'bam', 'bigwig', 'bed', 'fastq').",
                },
            },
            "required": ["accession"],
        },
    },
    "get_files_summary": {
        "description": "Get a summary of available files for an experiment (counts by type).",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Experiment accession (ENCSR format).",
                    "pattern": "^ENCSR[0-9A-Z]{6}$",
                },
            },
            "required": ["accession"],
        },
    },
    "get_file_metadata": {
        "description": "Get full metadata for a specific ENCODE file. Requires BOTH the experiment and the file accession.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Parent experiment accession (ENCSR format).",
                    "pattern": "^ENCSR[0-9A-Z]{6}$",
                },
                "file_accession": {
                    "type": "string",
                    "description": "File accession (ENCFF format, e.g. ENCFF921XAH).",
                    "pattern": "^ENCFF[0-9A-Z]{6}$",
                },
            },
            "required": ["accession", "file_accession"],
        },
    },
    "get_file_url": {
        "description": "Resolve the download URL for a specific ENCODE file.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Parent experiment accession (ENCSR format).",
                    "pattern": "^ENCSR[0-9A-Z]{6}$",
                },
                "file_accession": {
                    "type": "string",
                    "description": "File accession (ENCFF format).",
                    "pattern": "^ENCFF[0-9A-Z]{6}$",
                },
            },
            "required": ["accession", "file_accession"],
        },
    },
    "get_available_output_types": {
        "description": "List all available output types for an experiment.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Experiment accession (ENCSR format).",
                    "pattern": "^ENCSR[0-9A-Z]{6}$",
                },
            },
            "required": ["accession"],
        },
    },
    "get_all_metadata": {
        "description": "Get the complete raw metadata dump for an experiment.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Experiment accession (ENCSR format).",
                    "pattern": "^ENCSR[0-9A-Z]{6}$",
                },
            },
            "required": ["accession"],
        },
    },
    "list_experiments": {
        "description": "List recently updated or released experiments.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of experiments to return (default: 25).",
                },
            },
            "required": [],
        },
    },
    "get_cache_stats": {
        "description": "Get ENCODE server cache statistics.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "get_server_info": {
        "description": "Get ENCODE server info and version.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
