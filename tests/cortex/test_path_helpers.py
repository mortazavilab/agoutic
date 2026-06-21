"""Tests for cortex/path_helpers.py — pure path resolution functions."""

import pytest

from cortex.path_helpers import _pick_file_tool, _resolve_workflow_path, _resolve_file_path


# ---------------------------------------------------------------------------
# _pick_file_tool
# ---------------------------------------------------------------------------

class TestPickFileTool:
    def test_csv_returns_parse_csv_file(self):
        assert _pick_file_tool("data.csv") == "parse_csv_file"

    def test_tsv_returns_parse_csv_file(self):
        assert _pick_file_tool("results.tsv") == "parse_csv_file"

    def test_bed_returns_parse_bed_file(self):
        assert _pick_file_tool("annotations.bed") == "parse_bed_file"

    def test_other_extensions_return_read_file_content(self):
        assert _pick_file_tool("data.txt") == "read_file_content"
        assert _pick_file_tool("data.json") == "read_file_content"
        assert _pick_file_tool("data.bam") == "read_file_content"

    def test_case_insensitive(self):
        assert _pick_file_tool("DATA.CSV") == "parse_csv_file"
        assert _pick_file_tool("data.BED") == "parse_bed_file"


# ---------------------------------------------------------------------------
# _resolve_workflow_path
# ---------------------------------------------------------------------------

class TestResolveWorkflowPath:
    def test_empty_subpath_returns_default(self):
        assert _resolve_workflow_path("", "/default/work", []) == "/default/work"

    def test_single_component_not_a_workflow_uses_default(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        result = _resolve_workflow_path("annot", "/default/work", workflows)
        assert result == "/default/work/annot"

    def test_workflow_name_match_returns_that_workdir(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        result = _resolve_workflow_path("workflow1", "/default/work", workflows)
        assert result == "/proj/workflow1"

    def test_workflow_name_with_subpath_appends_to_workdir(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        result = _resolve_workflow_path("workflow1/annot", "/default/work", workflows)
        assert result == "/proj/workflow1/annot"

    def test_case_insensitive_workflow_match(self):
        workflows = [{"work_dir": "/proj/MyWorkflow"}]
        result = _resolve_workflow_path("myworkflow/data", "/default/work", workflows)
        assert result == "/proj/MyWorkflow/data"

    def test_no_workflows_list_uses_default(self):
        result = _resolve_workflow_path("annot", "/default/work", [])
        assert result == "/default/work/annot"

    def test_backslash_normalized_to_forward_slash(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        result = _resolve_workflow_path("workflow1\\annot", "/default/work", workflows)
        assert result == "/proj/workflow1/annot"


# ---------------------------------------------------------------------------
# _resolve_file_path
# ---------------------------------------------------------------------------

class TestResolveFilePath:
    def test_simple_filename_uses_default_workdir(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        work_dir, filename = _resolve_file_path("File.csv", "/default/work", workflows)
        assert work_dir == "/default/work"
        assert filename == "File.csv"

    def test_subdir_filename_uses_default_workdir(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        work_dir, filename = _resolve_file_path("annot/File.csv", "/default/work", workflows)
        assert work_dir == "/default/work"
        assert filename == "File.csv"

    def test_workflow_prefixed_path_resolves_to_that_workflow(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        work_dir, filename = _resolve_file_path("workflow1/annot/File.csv", "/default/work", workflows)
        assert work_dir == "/proj/workflow1"
        assert filename == "File.csv"

    def test_workflow_number_pattern_resolves(self):
        workflows = [{"work_dir": "/proj/project1/workflow1"}]
        work_dir, filename = _resolve_file_path("workflow1/File.csv", "/proj/project1/workflow1", workflows)
        assert work_dir == "/proj/project1/workflow1"
        assert filename == "File.csv"

    def test_sample_name_match_resolves(self):
        workflows = [{"work_dir": "/proj/workflow1", "sample_name": "my_sample"}]
        work_dir, filename = _resolve_file_path("results.csv", "/default/work", workflows)
        # Should match by sample name in filename or default
        assert filename == "results.csv"

    def test_trailing_slashes_stripped(self):
        workflows = [{"work_dir": "/proj/workflow1"}]
        work_dir, filename = _resolve_file_path("/annot/File.csv/", "/default/work", workflows)
        assert work_dir == "/default/work"
        assert filename == "File.csv"
