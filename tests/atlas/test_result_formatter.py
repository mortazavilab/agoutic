"""
Tests for atlas/result_formatter.py.

Covers: _human_size, _compact_dict, _format_data, format_results
"""

import pytest
from atlas.result_formatter import (
    _human_size,
    _compact_dict,
    _format_data,
    format_results,
    _format_files_by_type,
)


# =========================================================================
# _human_size
# =========================================================================

class TestHumanSize:
    def test_bytes(self):
        assert _human_size(500) == "500.0 B"

    def test_kilobytes(self):
        result = _human_size(1024)
        assert "KB" in result

    def test_megabytes(self):
        result = _human_size(1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = _human_size(1024 ** 3)
        assert "GB" in result

    def test_terabytes(self):
        result = _human_size(1024 ** 4)
        assert "TB" in result

    def test_zero(self):
        assert _human_size(0) == "0.0 B"

    def test_large_value(self):
        result = _human_size(1024 ** 5)
        assert "PB" in result


# =========================================================================
# _compact_dict
# =========================================================================

class TestCompactDict:
    def test_shallow_dict_unchanged(self):
        d = {"a": 1, "b": "hello"}
        assert _compact_dict(d) == d

    def test_deep_nesting_truncated(self):
        d = {"a": {"b": {"c": {"d": "deep"}}}}
        result = _compact_dict(d, max_depth=2)
        # At depth 2, the dict {"c": {"d": "deep"}} is truncated — "c" is the key, value replaced
        assert result["a"]["b"] == {"c": "..."}

    def test_long_list_truncated(self):
        d = {"items": list(range(20))}
        result = _compact_dict(d, max_depth=3)
        assert len(result["items"]) == 11  # 10 items + "... +10 more"
        assert "more" in result["items"][-1]

    def test_short_list_kept(self):
        d = {"items": [1, 2, 3]}
        result = _compact_dict(d, max_depth=3)
        assert result["items"] == [1, 2, 3]

    def test_scalar_passthrough(self):
        assert _compact_dict("hello", max_depth=0) == "hello"
        assert _compact_dict(42, max_depth=0) == 42


# =========================================================================
# _format_data
# =========================================================================

class TestFormatData:
    def test_list_of_dicts_with_columns(self):
        data = [
            {"accession": "ENCSR123", "assay": "RNA-seq", "biosample": "K562"},
            {"accession": "ENCSR456", "assay": "ChIP-seq", "biosample": "HeLa"},
        ]
        columns = [("Accession", "accession"), ("Assay", "assay"), ("Biosample", "biosample")]
        result = _format_data(data, columns, "assay", "assay")
        assert "Found 2 result(s)" in result
        assert "ENCSR123" in result
        assert "| Accession |" in result

    def test_list_with_count_field(self):
        data = [
            {"accession": "A1", "type": "bam"},
            {"accession": "A2", "type": "bam"},
            {"accession": "A3", "type": "fastq"},
        ]
        columns = [("Accession", "accession"), ("Type", "type")]
        result = _format_data(data, columns, "type", "file type")
        assert "Summary" in result
        assert "bam" in result
        assert "fastq" in result

    def test_empty_list(self):
        result = _format_data([], [], None, "type")
        assert "Found 0 result(s)" in result

    def test_dict_with_columns_and_data(self):
        """CSV-style parsed data."""
        data = {
            "columns": ["gene", "count"],
            "data": [{"gene": "BRCA1", "count": 42}],
            "file_path": "test.csv",
            "row_count": 1,
        }
        result = _format_data(data, [], None, "type")
        assert "Parsed file" in result
        assert "BRCA1" in result

    def test_dict_with_files_listing(self):
        data = {
            "files": [{"name": "test.bam", "path": "/work/test.bam", "size": 1024}],
            "file_count": 1,
            "work_dir": "/work",
        }
        result = _format_data(data, [], None, "type")
        assert "Directory" in result
        assert "test.bam" in result

    def test_scalar_data(self):
        result = _format_data("just a string", [], None, "type")
        assert "just a string" in result


# =========================================================================
# _format_files_by_type
# =========================================================================

class TestFormatFilesByType:
    def test_files_by_type(self):
        data = {
            "bam": [
                {"accession": "ENCFF001", "output_type": "alignments",
                 "file_size": 1073741824, "status": "released",
                 "biological_replicates": [1]},
            ],
            "fastq": [
                {"accession": "ENCFF002", "output_type": "reads",
                 "file_size": 524288000, "status": "released",
                 "biological_replicates": [1, 2]},
            ],
        }
        result = _format_files_by_type(data)
        assert "2 files" in result
        assert "BAM" in result
        assert "FASTQ" in result
        assert "ENCFF001" in result
        assert "ENCFF002" in result


# =========================================================================
# format_results (integration)
# =========================================================================

class TestFormatResults:
    def test_empty_results(self):
        assert format_results("encode", []) == ""

    def test_error_result(self):
        results = [{"tool": "get_experiment", "params": {}, "error": "Not found"}]
        result = format_results("encode", results)
        assert "Not found" in result
        assert "❌" in result

    def test_data_result_with_custom_entry(self):
        entry = {
            "display_name": "Test Source",
            "emoji": "🧪",
            "table_columns": [("Name", "name")],
            "count_field": None,
            "count_label": "type",
        }
        results = [{"tool": "search", "params": {"q": "test"}, "data": [{"name": "item1"}]}]
        result = format_results("test", results, registry_entry=entry)
        assert "🧪 Test Source" in result
        assert "item1" in result
