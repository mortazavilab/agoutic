"""
IGVF Tool Schemas — Machine-readable contracts for all IGVF MCP tools.

These schemas tell the LLM exactly what parameters each tool accepts,
their types, and critical warnings (e.g., IGVFDS vs IGVFFI).
Cortex fetches these at startup and injects a compact version into
the system prompt so the LLM always has an authoritative reference.
"""

TOOL_SCHEMAS: dict[str, dict] = {
    "search_measurement_sets": {
        "description": "Search for IGVF MeasurementSets (primary experimental datasets, equivalent to ENCODE Experiments).",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status. Default 'released'.",
                    "enum": ["released", "in progress", "archived", "revoked"],
                },
                "assay": {
                    "type": "string",
                    "description": "Assay term name (e.g. 'ATAC-seq', 'whole genome sequencing', 'CRISPR screen').",
                },
                "sample": {
                    "type": "string",
                    "description": "Sample summary filter (e.g. 'K562', 'liver').",
                },
                "organism": {
                    "type": "string",
                    "description": "Organism/taxa filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "lab": {
                    "type": "string",
                    "description": "Lab name or title filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 25, max 500).",
                },
            },
            "required": [],
        },
    },
    "search_analysis_sets": {
        "description": "Search for IGVF AnalysisSets (computational analysis results derived from MeasurementSets).",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status. Default 'released'.",
                },
                "sample": {
                    "type": "string",
                    "description": "Sample summary filter.",
                },
                "organism": {
                    "type": "string",
                    "description": "Organism/taxa filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
            "required": [],
        },
    },
    "search_prediction_sets": {
        "description": "Search for IGVF PredictionSets (computational predictions, e.g. variant effect predictions).",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status. Default 'released'.",
                },
                "sample": {
                    "type": "string",
                    "description": "Sample summary filter.",
                },
                "organism": {
                    "type": "string",
                    "description": "Organism/taxa filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
            "required": [],
        },
    },
    "search_by_sample": {
        "description": "Search for datasets by sample/biosample (cell line, tissue, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "sample_term": {
                    "type": "string",
                    "description": "Sample name or summary (e.g. 'K562', 'liver', 'iPSC'). NOT a dataset accession.",
                },
                "organism": {
                    "type": "string",
                    "description": "Organism filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "assay": {
                    "type": "string",
                    "description": "Optional assay filter.",
                },
                "status": {
                    "type": "string",
                    "description": "Status filter. Default 'released'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
            "required": ["sample_term"],
        },
    },
    "search_by_assay": {
        "description": "Search for datasets by assay type across all samples.",
        "parameters": {
            "type": "object",
            "properties": {
                "assay_title": {
                    "type": "string",
                    "description": "Assay type (e.g. 'ATAC-seq', 'whole genome sequencing', 'CRISPR screen', 'Hi-C').",
                },
                "organism": {
                    "type": "string",
                    "description": "Optional organism filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "status": {
                    "type": "string",
                    "description": "Status filter. Default 'released'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
            "required": ["assay_title"],
        },
    },
    "search_files": {
        "description": "Search for files in the IGVF portal.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_format": {
                    "type": "string",
                    "description": "File format filter (e.g. 'fastq', 'bam', 'bed', 'tsv', 'vcf').",
                },
                "content_type": {
                    "type": "string",
                    "description": "Content type filter (e.g. 'reads', 'alignments', 'variant calls').",
                },
                "dataset_accession": {
                    "type": "string",
                    "description": "Filter files belonging to a specific dataset (IGVFDS accession).",
                    "pattern": "^IGVFDS[0-9A-Z]{4,8}$",
                },
                "status": {
                    "type": "string",
                    "description": "Status filter. Default 'released'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
            "required": [],
        },
    },
    "get_dataset": {
        "description": "Get full metadata for an IGVF dataset by accession. Works for MeasurementSets, AnalysisSets, PredictionSets.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Dataset accession (IGVFDS format). NEVER pass IGVFFI file accessions here — use get_file_metadata instead.",
                    "pattern": "^IGVFDS[0-9A-Z]{4,8}$",
                },
            },
            "required": ["accession"],
        },
    },
    "get_file_metadata": {
        "description": "Get full metadata for a specific IGVF file.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_accession": {
                    "type": "string",
                    "description": "File accession (IGVFFI format, e.g. IGVFFI1234ABCD).",
                    "pattern": "^IGVFFI[0-9A-Z]{4,8}$",
                },
            },
            "required": ["file_accession"],
        },
    },
    "get_file_download_url": {
        "description": "Get the download URL for an IGVF file.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_accession": {
                    "type": "string",
                    "description": "File accession (IGVFFI format).",
                    "pattern": "^IGVFFI[0-9A-Z]{4,8}$",
                },
            },
            "required": ["file_accession"],
        },
    },
    "get_files_for_dataset": {
        "description": "Get all files associated with a dataset, optionally filtered by format.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Dataset accession (IGVFDS format).",
                    "pattern": "^IGVFDS[0-9A-Z]{4,8}$",
                },
                "file_format": {
                    "type": "string",
                    "description": "Optional file format filter (e.g. 'fastq', 'bam').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max files to return (default 100).",
                },
            },
            "required": ["accession"],
        },
    },
    "search_genes": {
        "description": "Search for genes in the IGVF portal by symbol, name, or ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gene symbol, name, or ID (e.g. 'TP53', 'BRCA1').",
                },
                "organism": {
                    "type": "string",
                    "description": "Optional organism filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10).",
                },
            },
            "required": ["query"],
        },
    },
    "get_gene": {
        "description": "Get detailed information for a specific gene.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_id": {
                    "type": "string",
                    "description": "Gene identifier — Entrez gene ID, gene symbol, or IGVF @id path.",
                },
            },
            "required": ["gene_id"],
        },
    },
    "search_samples": {
        "description": "Search for samples (biosamples) in the IGVF portal.",
        "parameters": {
            "type": "object",
            "properties": {
                "sample_type": {
                    "type": "string",
                    "description": "Sample type (e.g. 'InVitroSystem', 'PrimaryCell', 'Tissue', 'WholeOrganism'). Omit to search all.",
                },
                "organism": {
                    "type": "string",
                    "description": "Organism filter.",
                    "enum": ["Homo sapiens", "Mus musculus"],
                },
                "query": {
                    "type": "string",
                    "description": "Text query for sample summary.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
            "required": [],
        },
    },
    "get_server_info": {
        "description": "Get IGVF MCP server info and portal status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
