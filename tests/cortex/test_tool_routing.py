"""
Tests for _correct_tool_routing in cortex/app.py.

Covers the tool routing logic that fixes common LLM misrouting mistakes:
- get_experiment called with biosample names → search_by_biosample
- get_experiment called with assay params → search_by_assay
- ENCFF accession sent to get_experiment → get_file_metadata
- search_by_biosample without search_term → search_by_assay
"""

import pytest
from cortex.app import _correct_tool_routing


class TestCorrectToolRouting:
    """Tests for _correct_tool_routing."""

    # --- get_experiment → search_by_biosample ---

    def test_biosample_name_to_search_by_biosample(self):
        """Cell line name as accession should route to search_by_biosample."""
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "K562"}, "search for K562"
        )
        assert tool == "search_by_biosample"
        assert params["search_term"] == "K562"

    def test_search_params_to_search_by_biosample(self):
        """Explicit search_term param should route to search_by_biosample."""
        tool, params = _correct_tool_routing(
            "get_experiment", {"search_term": "HeLa"}, "search for HeLa"
        )
        assert tool == "search_by_biosample"
        assert params["search_term"] == "HeLa"

    def test_valid_encsr_not_rerouted(self):
        """Valid ENCSR accession should NOT be rerouted."""
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "ENCSR123ABC"}, "get experiment ENCSR123ABC"
        )
        assert tool == "get_experiment"

    # --- get_experiment → search_by_assay ---

    def test_assay_only_to_search_by_assay(self):
        """Assay name as accession (no biosample) → search_by_assay."""
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "RNA-seq"}, "how many RNA-seq experiments"
        )
        assert tool == "search_by_assay"
        assert "assay_title" in params

    def test_assay_with_organism(self):
        """Assay + organism → search_by_assay with organism preserved."""
        tool, params = _correct_tool_routing(
            "get_experiment",
            {"accession": "ATAC-seq", "organism": "Mus musculus"},
            "mouse ATAC-seq experiments"
        )
        assert tool == "search_by_assay"
        assert params.get("organism") == "Mus musculus"

    # --- get_experiment → get_file_metadata (ENCFF) ---

    def test_encff_accession_rerouted(self):
        """ENCFF accession in get_experiment → get_file_metadata."""
        history = [
            {"role": "assistant", "content": "Experiment ENCSR123ABC has file ENCFF789GHI"}
        ]
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "ENCFF789GHI"},
            "tell me about ENCFF789GHI",
            conversation_history=history,
        )
        assert tool == "get_file_metadata"
        assert params.get("file_accession") == "ENCFF789GHI"

    def test_encff_without_experiment_returns_routing_error(self):
        """ENCFF with no experiment in history → routing error."""
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "ENCFF789GHI"},
            "tell me about ENCFF789GHI",
            conversation_history=[],
        )
        assert tool == "get_file_metadata"
        assert "__routing_error__" in params

    def test_mangled_encff_to_encsr_caught(self):
        """LLM changed ENCFF prefix to ENCSR (same suffix) → catch and fix."""
        history = [
            {"role": "assistant", "content": "Found ENCFF921XAH in experiment ENCSR160HKZ"}
        ]
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "ENCSR921XAH"},
            "tell me about ENCFF921XAH",
            conversation_history=history,
        )
        assert tool == "get_file_metadata"
        assert params.get("file_accession") == "ENCFF921XAH"

    # --- search_by_biosample → search_by_assay ---

    def test_search_by_biosample_no_search_term_with_assay(self):
        """search_by_biosample w/o search_term but with assay_title → search_by_assay."""
        tool, params = _correct_tool_routing(
            "search_by_biosample",
            {"assay_title": "ATAC-seq"},
            "how many ATAC-seq experiments are there"
        )
        assert tool == "search_by_assay"
        assert params["assay_title"] == "ATAC-seq"

    def test_search_by_biosample_with_search_term_stays(self):
        """search_by_biosample WITH search_term should not be rerouted."""
        tool, params = _correct_tool_routing(
            "search_by_biosample",
            {"search_term": "K562", "assay_title": "RNA-seq"},
            "K562 RNA-seq experiments"
        )
        assert tool == "search_by_biosample"

    # --- get_file_metadata fix ---

    def test_get_file_metadata_missing_experiment(self):
        """get_file_metadata without experiment → find from history."""
        history = [
            {"role": "assistant", "content": "Experiment ENCSR123ABC files: ENCFF456DEF"}
        ]
        tool, params = _correct_tool_routing(
            "get_file_metadata",
            {"file_accession": "ENCFF456DEF"},
            "get metadata for ENCFF456DEF",
            conversation_history=history,
        )
        assert tool == "get_file_metadata"
        assert params["accession"] == "ENCSR123ABC"
        assert params["file_accession"] == "ENCFF456DEF"

    def test_get_file_metadata_accession_is_encff(self):
        """When accession field has an ENCFF value, it should become file_accession."""
        history = [
            {"role": "assistant", "content": "From ENCSR999ZZZ: ENCFF111AAA"}
        ]
        tool, params = _correct_tool_routing(
            "get_file_metadata",
            {"accession": "ENCFF111AAA"},
            "metadata for ENCFF111AAA",
            conversation_history=history,
        )
        assert params["file_accession"] == "ENCFF111AAA"
        assert params["accession"] == "ENCSR999ZZZ"

    # --- Human biosample organism defaulting ---

    def test_human_biosample_defaults_organism(self):
        """Well-known human cell lines get organism=Homo sapiens."""
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "K562"},
            "K562 experiments"
        )
        assert tool == "search_by_biosample"
        assert params.get("organism") == "Homo sapiens"

    def test_unknown_biosample_no_default_organism(self):
        """Unknown biosample should NOT get a default organism."""
        tool, params = _correct_tool_routing(
            "get_experiment", {"accession": "MyCustomCells"},
            "MyCustomCells experiments"
        )
        assert tool == "search_by_biosample"
        assert "organism" not in params

    # --- Passthrough ---

    def test_unrelated_tool_passes_through(self):
        """Tools that don't need routing fixes should pass through unchanged."""
        tool, params = _correct_tool_routing(
            "list_job_files", {"work_dir": "/tmp/work"},
            "list files"
        )
        assert tool == "list_job_files"
        assert params == {"work_dir": "/tmp/work"}
