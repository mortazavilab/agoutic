"""
Tests for additional pure/near-pure helper functions in cortex/app.py:
  - _emit_progress  (progress tracking dict)
  - _find_experiment_for_file  (ENCSR/ENCFF lookup)
  - _validate_analyzer_params  (param sanitisation)
  - _build_auto_analysis_context  (markdown builder)
  - _build_static_analysis_summary  (fallback markdown)
"""

import re
import time
from unittest.mock import MagicMock

import pytest

from cortex.app import (
    _emit_progress,
    _chat_progress,
    _validate_analyzer_params,
)
from cortex.encode_helpers import _find_experiment_for_file
from cortex.analysis_helpers import (
    _build_auto_analysis_context,
    _build_static_analysis_summary,
)


# ======================================================================
# _emit_progress
# ======================================================================
class TestEmitProgress:
    """Tests for the in-memory chat progress tracker."""

    @pytest.fixture(autouse=True)
    def _clear_progress(self):
        """Ensure a clean progress dict for each test."""
        _chat_progress.clear()
        yield
        _chat_progress.clear()

    def test_basic_entry(self):
        _emit_progress("req-1", "parsing", "started")
        assert "req-1" in _chat_progress
        entry = _chat_progress["req-1"]
        assert entry["stage"] == "parsing"
        assert entry["detail"] == "started"
        assert isinstance(entry["ts"], float)

    def test_empty_request_id_noop(self):
        _emit_progress("", "parsing")
        assert _chat_progress == {}

    def test_none_request_id_noop(self):
        _emit_progress(None, "parsing")
        assert _chat_progress == {}

    def test_overwrites_previous_stage(self):
        _emit_progress("req-1", "stage-1")
        _emit_progress("req-1", "stage-2", "done")
        assert _chat_progress["req-1"]["stage"] == "stage-2"
        assert _chat_progress["req-1"]["detail"] == "done"

    def test_default_detail_empty(self):
        _emit_progress("req-1", "s")
        assert _chat_progress["req-1"]["detail"] == ""

    def test_stale_entries_cleaned(self):
        """Entries older than 5 minutes should be evicted."""
        # Inject a stale entry manually
        _chat_progress["old"] = {"stage": "x", "detail": "", "ts": time.time() - 400}
        _emit_progress("new", "fresh")
        assert "old" not in _chat_progress
        assert "new" in _chat_progress

    def test_recent_entries_kept(self):
        _emit_progress("req-a", "a")
        _emit_progress("req-b", "b")
        assert "req-a" in _chat_progress
        assert "req-b" in _chat_progress


# ======================================================================
# _find_experiment_for_file
# ======================================================================
class TestFindExperimentForFile:
    """Tests for ENCFF → ENCSR lookup in conversation history."""

    def test_none_history(self):
        assert _find_experiment_for_file("ENCFF123ABC", None) is None

    def test_empty_history(self):
        assert _find_experiment_for_file("ENCFF123ABC", []) is None

    def test_empty_accession(self):
        history = [{"role": "assistant", "content": "ENCSR999XYZ and ENCFF123ABC"}]
        assert _find_experiment_for_file("", history) is None

    def test_direct_match(self):
        history = [
            {"role": "user", "content": "download ENCFF123ABC"},
            {"role": "assistant", "content": "File ENCFF123ABC from experiment ENCSR456DEF"},
        ]
        result = _find_experiment_for_file("ENCFF123ABC", history)
        assert result == "ENCSR456DEF"

    def test_case_insensitive_file_match(self):
        history = [
            {"role": "assistant", "content": "File encff123abc from ENCSR789GHI"},
        ]
        result = _find_experiment_for_file("ENCFF123ABC", history)
        assert result == "ENCSR789GHI"

    def test_fallback_most_recent_experiment(self):
        """If no message mentions the file AND an experiment, fall back to most recent ENCSR."""
        history = [
            {"role": "assistant", "content": "Experiment ENCSR111AAA has 5 files."},
            {"role": "assistant", "content": "Experiment ENCSR222BBB has 3 files."},
            {"role": "user", "content": "download ENCFF999ZZZ"},
        ]
        result = _find_experiment_for_file("ENCFF999ZZZ", history)
        # Should return the MOST RECENT experiment (last in reversed order → ENCSR222BBB)
        assert result == "ENCSR222BBB"

    def test_no_experiments_at_all(self):
        history = [
            {"role": "user", "content": "show files"},
            {"role": "assistant", "content": "Here are some files."},
        ]
        assert _find_experiment_for_file("ENCFF000AAA", history) is None

    def test_prefers_message_mentioning_file(self):
        """When multiple messages have ENCSR, pick the one that also mentions the file."""
        history = [
            {"role": "assistant", "content": "ENCSR111AAA has ENCFF999ZZZ and ENCFF888YYY"},
            {"role": "assistant", "content": "ENCSR222BBB also has interesting data"},
        ]
        result = _find_experiment_for_file("ENCFF999ZZZ", history)
        assert result == "ENCSR111AAA"

    def test_returns_uppercase(self):
        history = [
            {"role": "assistant", "content": "encsr456def includes encff123abc"},
        ]
        result = _find_experiment_for_file("encff123abc", history)
        assert result == "ENCSR456DEF"


# ======================================================================
# _validate_analyzer_params
# ======================================================================
class TestValidateAnalyzerParams:
    """Tests for param sanitisation before Analyzer MCP calls."""

    @staticmethod
    def _make_block(block_type, payload_json):
        blk = MagicMock()
        blk.type = block_type
        blk.payload_json = payload_json
        return blk

    def test_strips_unknown_params(self):
        result = _validate_analyzer_params(
            tool="list_job_files",
            params={"work_dir": "/w", "sample": "Jamshid", "colour": "red"},
            user_message="list files",
        )
        assert "sample" not in result
        assert "colour" not in result

    def test_keeps_known_params(self):
        result = _validate_analyzer_params(
            tool="list_job_files",
            params={"work_dir": "/w", "extensions": ".csv"},
            user_message="list csv files",
        )
        assert result["extensions"] == ".csv"

    def test_unknown_tool_keeps_all_params(self):
        """Tools not in _KNOWN_PARAMS should keep all params."""
        result = _validate_analyzer_params(
            tool="some_new_tool",
            params={"a": 1, "b": 2, "work_dir": "/x"},
            user_message="do something",
        )
        assert result["a"] == 1
        assert result["b"] == 2

    def test_work_dir_from_history_blocks(self):
        """work_dir should be resolved from conversation history blocks."""
        import json
        blk = self._make_block(
            "EXECUTION_JOB",
            json.dumps({
                "workflow_name": "DNA",
                "work_directory": "/real/path/workflow1",
                "run_uuid": "abc-123",
            }),
        )
        result = _validate_analyzer_params(
            tool="get_analysis_summary",
            params={"work_dir": "/LLM/invented"},
            user_message="show summary",
            history_blocks=[blk],
        )
        assert result["work_dir"] == "/real/path/workflow1"

    def test_work_dir_project_dir_fallback(self):
        """If no workflow-level work_dir, fall back to project_dir."""
        result = _validate_analyzer_params(
            tool="get_analysis_summary",
            params={},
            user_message="show summary",
            project_dir="/proj/dir",
        )
        assert result["work_dir"] == "/proj/dir"

    def test_list_workflows_strips_workflow_suffix(self):
        """'list workflows' command should strip /workflowN suffix."""
        import json
        blk = self._make_block(
            "EXECUTION_JOB",
            json.dumps({"work_directory": "/proj/workflow1"}),
        )
        result = _validate_analyzer_params(
            tool="list_job_files",
            params={},
            user_message="list available workflows",
            history_blocks=[blk],
        )
        assert result["work_dir"] == "/proj"
        assert result["max_depth"] == 1
        assert result.get("name_pattern") == "workflow*"

    def test_subfolder_hint_from_invented_param(self):
        """LLM-invented params like subfolder=annot should be rescued."""
        import json
        blk = self._make_block(
            "EXECUTION_JOB",
            json.dumps({"work_directory": "/proj/workflow1"}),
        )
        result = _validate_analyzer_params(
            tool="list_job_files",
            params={"subfolder": "annot"},
            user_message="list files in annot",
            history_blocks=[blk],
        )
        # work_dir should incorporate the subfolder
        assert "annot" in result["work_dir"]

    def test_valid_subdir_of_context_wd_kept(self):
        """If LLM work_dir is a valid subdir of context work_dir, keep it."""
        import json
        blk = self._make_block(
            "EXECUTION_JOB",
            json.dumps({"work_directory": "/proj/workflow1"}),
        )
        result = _validate_analyzer_params(
            tool="find_file",
            params={"work_dir": "/proj/workflow1/results", "file_name": "out.csv"},
            user_message="find out.csv",
            history_blocks=[blk],
        )
        assert result["work_dir"] == "/proj/workflow1/results"


# ======================================================================
# _build_auto_analysis_context
# ======================================================================
class TestBuildAutoAnalysisContext:
    """Tests for the markdown context builder."""

    def test_empty_inputs(self):
        result = _build_auto_analysis_context("s1", "DNA", "uuid1", {}, {})
        assert result == "No analysis data available."

    def test_file_inventory(self):
        summary = {
            "all_file_counts": {"total_files": 42, "csv_count": 10, "bed_count": 5, "txt_count": 27},
            "file_summary": {"csv_files": [{"name": "results.csv", "size": 2048}]},
        }
        result = _build_auto_analysis_context("sample1", "DNA", "u1", summary, {})
        assert "## File Inventory" in result
        assert "Total files: 42" in result
        assert "CSV/TSV: 10" in result
        assert "BED: 5" in result
        assert "results.csv" in result

    def test_csv_list_capped_at_12(self):
        csv_files = [{"name": f"file{i}.csv", "size": 1024} for i in range(20)]
        summary = {
            "all_file_counts": {"total_files": 20},
            "file_summary": {"csv_files": csv_files},
        }
        result = _build_auto_analysis_context("s", "DNA", "u", summary, {})
        assert "…and 8 more" in result

    def test_parsed_csv_data_table(self):
        parsed_csvs = {
            "results.csv": {
                "columns": ["gene", "score"],
                "data": [{"gene": "BRCA1", "score": "0.95"}, {"gene": "TP53", "score": "0.87"}],
                "total_rows": 2,
            }
        }
        result = _build_auto_analysis_context("s", "DNA", "u", {}, parsed_csvs)
        assert "## Data: results.csv" in result
        assert "gene" in result
        assert "BRCA1" in result
        assert "0.95" in result

    def test_csv_data_capped_at_30_rows(self):
        rows = [{"x": str(i)} for i in range(50)]
        parsed_csvs = {
            "big.csv": {"columns": ["x"], "data": rows, "total_rows": 50}
        }
        result = _build_auto_analysis_context("s", "DNA", "u", {}, parsed_csvs)
        assert "showing 30 of 50 rows" in result

    def test_list_rows(self):
        """Rows can be lists/tuples instead of dicts."""
        parsed_csvs = {
            "f.csv": {"columns": ["a", "b"], "data": [[1, 2], [3, 4]], "total_rows": 2}
        }
        result = _build_auto_analysis_context("s", "DNA", "u", {}, parsed_csvs)
        assert "1" in result
        assert "4" in result


# ======================================================================
# _build_static_analysis_summary
# ======================================================================
class TestBuildStaticAnalysisSummary:
    """Tests for the fallback markdown summary."""

    def test_with_summary_data(self):
        summary = {
            "mode": "DNA",
            "status": "COMPLETED",
            "all_file_counts": {"total_files": 15, "csv_count": 3, "bed_count": 2, "txt_count": 10},
            "file_summary": {
                "csv_files": [{"name": "out.csv", "size": 4096}],
            },
        }
        result = _build_static_analysis_summary("sampleX", "DNA", "uuid", summary, "/proj/workflow1")
        assert "sampleX" in result
        assert "workflow1" in result
        assert "COMPLETED" in result
        assert "CSV / TSV" in result
        assert "out.csv" in result
        assert "Show me the modification summary" in result

    def test_csv_list_capped_at_8(self):
        csv_files = [{"name": f"f{i}.csv", "size": 512} for i in range(15)]
        summary = {
            "all_file_counts": {"total_files": 15},
            "file_summary": {"csv_files": csv_files},
        }
        result = _build_static_analysis_summary("s", "DNA", "u", summary)
        assert "…and 7 more" in result

    def test_no_summary_data(self):
        result = _build_static_analysis_summary("mysample", "RNA", "uu", {})
        assert "mysample" in result
        assert "RNA" in result
        assert "couldn't fetch" in result.lower() or "couldn" in result.lower()
        # Should still have follow-up prompts
        assert "Parse the CSV" in result

    def test_follow_up_prompts_always_present(self):
        result = _build_static_analysis_summary("s", "DNA", "u", {})
        assert "QC report" in result
