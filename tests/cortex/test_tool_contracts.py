"""
Tests for cortex/tool_contracts.py — format_tool_contract() and validate_against_schema().

format_tool_contract: reads from the module-level _SCHEMA_CACHE and produces
compact prompt text.

validate_against_schema: checks params against cached schema — strips unknown
params, checks required fields, validates enum values (case-insensitive).
"""

import pytest
from unittest.mock import patch

import cortex.tool_contracts as tc
from cortex.tool_contracts import (
    format_tool_contract,
    validate_against_schema,
    invalidate_schema_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset schema cache before each test."""
    invalidate_schema_cache()
    yield
    invalidate_schema_cache()


def _inject_schema(source_key: str, tools: dict):
    """Inject a schema dict directly into the module-level cache."""
    tc._SCHEMA_CACHE[source_key] = tools
    tc._CACHE_LOADED = True


# ---------------------------------------------------------------------------
# format_tool_contract
# ---------------------------------------------------------------------------

class TestFormatToolContract:
    def test_empty_cache_returns_empty(self):
        result = format_tool_contract("encode", "consortium")
        assert result == ""

    def test_local_cortex_contract_uses_builtin_schemas(self):
        result = format_tool_contract("cortex", "service")
        assert "filter_dataframe" in result
        assert "melt_dataframe" in result

    def test_single_tool_with_required_and_optional(self):
        _inject_schema("encode", {
            "get_experiment": {
                "description": "Fetch experiment metadata",
                "parameters": {
                    "properties": {
                        "accession": {
                            "type": "string",
                            "pattern": "ENCSR[0-9A-Z]{6}",
                            "description": "Experiment accession",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["json", "tsv"],
                            "description": "Output format",
                        },
                    },
                    "required": ["accession"],
                },
            },
        })
        result = format_tool_contract("encode", "consortium")
        assert "TOOL: get_experiment (consortium=encode)" in result
        assert "REQUIRED:" in result
        assert "accession" in result
        assert "OPTIONAL:" in result
        assert "format" in result

    def test_enum_values_as_pipe_separated(self):
        _inject_schema("encode", {
            "get_files": {
                "description": "",
                "parameters": {
                    "properties": {
                        "file_type": {
                            "type": "string",
                            "enum": ["bam", "fastq", "bed"],
                        },
                    },
                    "required": [],
                },
            },
        })
        result = format_tool_contract("encode", "consortium")
        assert "bam|fastq|bed" in result

    def test_warning_from_never_description(self):
        _inject_schema("encode", {
            "get_file_metadata": {
                "description": "",
                "parameters": {
                    "properties": {
                        "accession": {
                            "type": "string",
                            "description": "NEVER pass ENCFF file accessions here",
                        },
                    },
                    "required": ["accession"],
                },
            },
        })
        result = format_tool_contract("encode", "consortium")
        assert "⚠️" in result or "NEVER" in result

    def test_multiple_tools(self):
        _inject_schema("analyzer", {
            "list_job_files": {
                "description": "List files in work dir",
                "parameters": {"properties": {}, "required": []},
            },
            "parse_csv_file": {
                "description": "Parse a CSV",
                "parameters": {"properties": {}, "required": []},
            },
        })
        result = format_tool_contract("analyzer", "service")
        assert "list_job_files" in result
        assert "parse_csv_file" in result


# ---------------------------------------------------------------------------
# validate_against_schema
# ---------------------------------------------------------------------------

class TestValidateAgainstSchema:
    def _setup_encode_schema(self):
        _inject_schema("encode", {
            "get_experiment": {
                "parameters": {
                    "properties": {
                        "accession": {"type": "string"},
                        "format": {"type": "string", "enum": ["json", "tsv"]},
                    },
                    "required": ["accession"],
                },
            },
            "search_by_biosample": {
                "parameters": {
                    "properties": {
                        "search_term": {"type": "string"},
                        "organism": {"type": "string", "enum": ["Homo sapiens", "Mus musculus"]},
                    },
                    "required": ["search_term"],
                },
            },
        })

    def test_no_schema_passthrough(self):
        cleaned, violations = validate_against_schema(
            "unknown_tool", {"foo": "bar"}, "nonexistent_source",
        )
        assert cleaned == {"foo": "bar"}
        assert violations == []

    def test_valid_params_unchanged(self):
        self._setup_encode_schema()
        cleaned, violations = validate_against_schema(
            "get_experiment", {"accession": "ENCSR000AAA"}, "encode",
        )
        assert cleaned == {"accession": "ENCSR000AAA"}
        assert violations == []

    def test_strips_unknown_params(self):
        self._setup_encode_schema()
        cleaned, violations = validate_against_schema(
            "get_experiment",
            {"accession": "ENCSR000AAA", "bogus_param": "junk"},
            "encode",
        )
        assert "bogus_param" not in cleaned
        assert any("Stripped unknown" in v for v in violations)

    def test_missing_required_params(self):
        self._setup_encode_schema()
        cleaned, violations = validate_against_schema(
            "get_experiment", {"format": "json"}, "encode",
        )
        assert any("Missing required" in v for v in violations)
        assert "accession" in str(violations)

    def test_enum_case_insensitive_correction(self):
        self._setup_encode_schema()
        cleaned, violations = validate_against_schema(
            "search_by_biosample",
            {"search_term": "K562", "organism": "homo sapiens"},
            "encode",
        )
        # Should correct to canonical case
        assert cleaned["organism"] == "Homo sapiens"
        assert not any("Invalid value" in v for v in violations)

    def test_invalid_enum_value(self):
        self._setup_encode_schema()
        cleaned, violations = validate_against_schema(
            "search_by_biosample",
            {"search_term": "K562", "organism": "Alien species"},
            "encode",
        )
        assert any("Invalid value" in v for v in violations)

    def test_combined_violations(self):
        self._setup_encode_schema()
        cleaned, violations = validate_against_schema(
            "search_by_biosample",
            {"organism": "wrong", "extra_key": "drop_me"},
            "encode",
        )
        # Missing required + invalid enum + stripped unknown
        assert len(violations) >= 2


# ---------------------------------------------------------------------------
# invalidate_schema_cache
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def test_invalidate_clears_cache(self):
        _inject_schema("test", {"tool": {"parameters": {"properties": {}, "required": []}}})
        invalidate_schema_cache()
        assert tc._SCHEMA_CACHE == {}
        assert tc._CACHE_LOADED is False
