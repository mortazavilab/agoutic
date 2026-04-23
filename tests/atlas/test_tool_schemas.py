"""
Tests for atlas/tool_schemas.py — TOOL_SCHEMAS integrity.

Ensures every tool schema is well-formed with the right structure,
patterns are valid regex, required fields are present, and enum
values are consistent.
"""

import re
import pytest

from atlas.tool_schemas import TOOL_SCHEMAS


# ---------------------------------------------------------------------------
# Catalogue completeness
# ---------------------------------------------------------------------------

class TestToolSchemasCatalogue:
    """Verify the expected set of tools is present."""

    EXPECTED_TOOLS = {
        "get_experiment",
        "search_by_biosample",
        "search_by_assay",
        "search_by_target",
        "search_by_organism",
        "get_files_by_type",
        "get_files_summary",
        "get_file_metadata",
        "get_file_url",
        "get_available_output_types",
        "get_all_metadata",
        "list_experiments",
        "get_cache_stats",
        "get_server_info",
    }

    def test_all_expected_tools_present(self):
        assert self.EXPECTED_TOOLS == set(TOOL_SCHEMAS.keys())

    def test_tool_count(self):
        assert len(TOOL_SCHEMAS) == 14


# ---------------------------------------------------------------------------
# Per-tool structure
# ---------------------------------------------------------------------------

class TestToolSchemaStructure:
    """Every tool must have a description and parameters object."""

    @pytest.mark.parametrize("tool_name", list(TOOL_SCHEMAS.keys()))
    def test_has_description(self, tool_name):
        schema = TOOL_SCHEMAS[tool_name]
        assert "description" in schema
        assert isinstance(schema["description"], str)
        assert len(schema["description"]) > 10  # meaningful description

    @pytest.mark.parametrize("tool_name", list(TOOL_SCHEMAS.keys()))
    def test_has_parameters_object(self, tool_name):
        schema = TOOL_SCHEMAS[tool_name]
        assert "parameters" in schema
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        assert "required" in params

    @pytest.mark.parametrize("tool_name", list(TOOL_SCHEMAS.keys()))
    def test_required_subset_of_properties(self, tool_name):
        """Required fields must also appear in properties."""
        params = TOOL_SCHEMAS[tool_name]["parameters"]
        prop_keys = set(params["properties"].keys())
        required = set(params["required"])
        assert required <= prop_keys, f"Required {required - prop_keys} not in properties"


# ---------------------------------------------------------------------------
# ENCSR / ENCFF pattern validation
# ---------------------------------------------------------------------------

class TestAccessionPatterns:
    """Tools that accept accession fields must have valid regex patterns."""

    ENCSR_TOOLS = [
        "get_experiment",
        "get_files_by_type",
        "get_files_summary",
        "get_file_metadata",
        "get_file_url",
        "get_available_output_types",
        "get_all_metadata",
    ]

    @pytest.mark.parametrize("tool_name", ENCSR_TOOLS)
    def test_encsr_pattern(self, tool_name):
        pattern = TOOL_SCHEMAS[tool_name]["parameters"]["properties"]["accession"]["pattern"]
        assert re.match(pattern, "ENCSR160HKZ")
        assert not re.match(pattern, "ENCFF921XAH")  # ENCFF must NOT match ENCSR pattern

    def test_encff_pattern_in_get_file_metadata(self):
        fa = TOOL_SCHEMAS["get_file_metadata"]["parameters"]["properties"]["file_accession"]
        pattern = fa["pattern"]
        assert re.match(pattern, "ENCFF921XAH")
        assert not re.match(pattern, "ENCSR160HKZ")

    def test_encff_pattern_in_get_file_url(self):
        fa = TOOL_SCHEMAS["get_file_url"]["parameters"]["properties"]["file_accession"]
        pattern = fa["pattern"]
        assert re.match(pattern, "ENCFF000AAA")
        assert not re.match(pattern, "ENCSR000AAA")


# ---------------------------------------------------------------------------
# Organism enum consistency
# ---------------------------------------------------------------------------

class TestOrganismEnum:
    """All organism fields must offer the same enum values."""

    TOOLS_WITH_ORGANISM = [
        "search_by_biosample",
        "search_by_assay",
        "search_by_target",
        "search_by_organism",
    ]

    @pytest.mark.parametrize("tool_name", TOOLS_WITH_ORGANISM)
    def test_organism_enum_values(self, tool_name):
        org_prop = TOOL_SCHEMAS[tool_name]["parameters"]["properties"]["organism"]
        assert set(org_prop["enum"]) == {"Homo sapiens", "Mus musculus"}


# ---------------------------------------------------------------------------
# Parameter types
# ---------------------------------------------------------------------------

class TestParameterTypes:
    """Spot-check that parameter types are correctly declared."""

    def test_list_experiments_limit_is_integer(self):
        props = TOOL_SCHEMAS["list_experiments"]["parameters"]["properties"]
        assert props["limit"]["type"] == "integer"

    def test_search_by_biosample_search_term_is_string(self):
        props = TOOL_SCHEMAS["search_by_biosample"]["parameters"]["properties"]
        assert props["search_term"]["type"] == "string"

    def test_get_files_by_type_file_type_is_string(self):
        props = TOOL_SCHEMAS["get_files_by_type"]["parameters"]["properties"]
        assert props["file_type"]["type"] == "string"

    def test_no_tools_with_zero_properties(self):
        """Every tool should have at least one property (or be a no-arg tool)."""
        no_arg_tools = {"get_cache_stats", "get_server_info"}
        for name, schema in TOOL_SCHEMAS.items():
            props = schema["parameters"]["properties"]
            if name not in no_arg_tools:
                assert len(props) > 0, f"{name} has no parameters"
