"""Tool schemas for XgenePy MCP tools."""

TOOL_SCHEMAS = {
    "run_xgenepy_analysis": {
        "description": "Run a local XgenePy cis/trans analysis and write canonical outputs.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_dir": {
                    "type": "string",
                    "description": "Absolute project directory used as execution root.",
                },
                "counts_path": {
                    "type": "string",
                    "description": "Project-relative path to counts CSV/TSV input.",
                },
                "metadata_path": {
                    "type": "string",
                    "description": "Project-relative path to metadata CSV/TSV input.",
                },
                "output_subdir": {
                    "type": "string",
                    "description": "Optional project-relative output subdirectory.",
                },
                "trans_model": {
                    "type": "string",
                    "description": "XgenePy trans model (default: log_additive).",
                },
                "fields_to_test": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional metadata fields for multi-condition testing.",
                },
                "combo": {
                    "type": "string",
                    "description": "Optional condition combination for assignment outputs.",
                },
                "alpha": {
                    "type": "number",
                    "description": "Significance alpha used in assignment classification.",
                },
                "execution_mode": {
                    "type": "string",
                    "description": "Execution mode. Phase 1 supports local only.",
                    "enum": ["local"],
                },
                "project_id": {
                    "type": "string",
                    "description": "Optional project identifier for manifest context.",
                },
                "run_id": {
                    "type": "string",
                    "description": "Optional run identifier for manifest context.",
                },
            },
            "required": ["project_dir", "counts_path", "metadata_path"],
        },
    },
}
