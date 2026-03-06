"""
Tests for ENCODE-related helper functions in cortex/app.py.

Covers: _extract_encode_search_term, _validate_encode_params
"""

import pytest
from cortex.app import _extract_encode_search_term, _validate_encode_params


# =========================================================================
# _extract_encode_search_term
# =========================================================================

class TestExtractEncodeSearchTerm:
    def test_how_many_pattern(self):
        result = _extract_encode_search_term("how many K562 experiments are there?")
        assert result == "K562"

    def test_how_many_with_qualifier(self):
        result = _extract_encode_search_term("how many HL-60 experiments")
        assert result == "HL-60"

    def test_search_for_pattern(self):
        result = _extract_encode_search_term("search encode for K562 experiments")
        assert result == "K562"

    def test_search_for_without_encode(self):
        result = _extract_encode_search_term("search for C2C12")
        assert result == "C2C12"

    def test_experiments_in_encode(self):
        result = _extract_encode_search_term("K562 experiments in encode")
        assert result == "K562"

    def test_does_encode_have(self):
        result = _extract_encode_search_term("does encode have HeLa experiments?")
        assert result == "HeLa"

    def test_fallback_tokenizer(self):
        # All stop words stripped, remaining tokens returned
        result = _extract_encode_search_term("tell me about K562")
        assert result is not None
        assert "K562" in result

    def test_all_stop_words_returns_none(self):
        # When everything is a stop word, check if None or empty
        result = _extract_encode_search_term("how many are there")
        # After stripping stop words, nothing is left
        assert result is None or result.strip() == ""

    def test_accessions_are_stop_words(self):
        # "accessions" should not be treated as a search term
        result = _extract_encode_search_term("what are the accessions")
        # Should not contain "accessions" as a biosample name
        if result:
            assert "accession" not in result.lower()

    def test_visualization_words_are_stop_words(self):
        result = _extract_encode_search_term("plot the chart")
        if result:
            assert "plot" not in result.lower()
            assert "chart" not in result.lower()

    def test_multiword_biosample(self):
        result = _extract_encode_search_term("how many SK-N-SH experiments")
        assert result == "SK-N-SH"


# =========================================================================
# _validate_encode_params
# =========================================================================

class TestValidateEncodeParams:
    def test_passthrough_valid_params(self):
        params = {"search_term": "K562"}
        result = _validate_encode_params("search_by_biosample", params, "search for K562")
        assert result["search_term"] == "K562"

    def test_missing_search_term_salvaged_from_assay_title(self):
        """When assay_title has a non-assay value (biosample name), move it to search_term."""
        params = {"assay_title": "MEL"}
        result = _validate_encode_params("search_by_biosample", params, "search for MEL")
        assert result["search_term"] == "MEL"
        assert "assay_title" not in result

    def test_missing_search_term_with_real_assay(self):
        """When assay_title has a real assay name, don't move it."""
        params = {"assay_title": "RNA-seq"}
        result = _validate_encode_params("search_by_biosample", params, "RNA-seq experiments for K562")
        # search_term should be injected from user message, assay_title stays
        assert "search_term" in result

    def test_organism_stripped_when_not_mentioned(self):
        """LLM added organism but user didn't mention species."""
        params = {"search_term": "K562", "organism": "Homo sapiens"}
        result = _validate_encode_params("search_by_biosample", params, "search for K562")
        assert "organism" not in result

    def test_organism_kept_when_mentioned(self):
        params = {"search_term": "K562", "organism": "Homo sapiens"}
        result = _validate_encode_params("search_by_biosample", params,
                                         "search for human K562 experiments")
        assert result["organism"] == "Homo sapiens"

    def test_organism_kept_for_mouse(self):
        params = {"search_term": "C2C12", "organism": "Mus musculus"}
        result = _validate_encode_params("search_by_biosample", params,
                                         "search for mouse C2C12 experiments")
        assert result["organism"] == "Mus musculus"

    def test_hallucinated_encsr_replaced(self):
        """LLM put a different ENCSR than what user said."""
        params = {"accession": "ENCSRXXXXXX"}
        result = _validate_encode_params("get_experiment", params,
                                         "tell me about ENCSR123ABC")
        assert result["accession"] == "ENCSR123ABC"

    def test_correct_encsr_kept(self):
        params = {"accession": "ENCSR123ABC"}
        result = _validate_encode_params("get_experiment", params,
                                         "tell me about ENCSR123ABC")
        assert result["accession"] == "ENCSR123ABC"

    def test_no_encsr_in_message_no_change(self):
        params = {"accession": "ENCSR123ABC"}
        result = _validate_encode_params("get_experiment", params,
                                         "get this experiment details")
        assert result["accession"] == "ENCSR123ABC"
