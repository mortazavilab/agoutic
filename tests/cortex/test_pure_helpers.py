"""
Tests for pure helper functions in cortex/app.py.

Covers: _slugify, _parse_tag_params, _pick_file_tool, _looks_like_assay,
        _resolve_workflow_path, _resolve_file_path
"""

import pytest
from cortex.app import (
    _slugify,
)
from cortex.encode_helpers import _looks_like_assay
from cortex.llm_validators import _parse_tag_params
from cortex.path_helpers import (
    _pick_file_tool,
    _resolve_workflow_path,
    _resolve_file_path,
)
from cortex.chat_dataframes import (
    _build_specialized_dataframe_plot_spec,
    apply_overlap_message_hints,
    materialize_direct_set_overlap_plot_dataframes,
)
from cortex.tag_parser import parse_plot_tags, user_wants_plot


# =========================================================================
# _slugify
# =========================================================================

class TestSlugify:
    def test_basic(self):
        assert _slugify("My Project") == "my-project"

    def test_special_chars(self):
        assert _slugify("Hello! World@2024") == "hello-world-2024"

    def test_multiple_hyphens_collapsed(self):
        assert _slugify("a---b") == "a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert _slugify("--hello--") == "hello"

    def test_max_length(self):
        result = _slugify("a" * 100, max_len=10)
        assert len(result) <= 10

    def test_empty_string_returns_project(self):
        assert _slugify("") == "project"

    def test_only_special_chars(self):
        assert _slugify("!!!") == "project"

    def test_unicode_stripped(self):
        result = _slugify("café résumé")
        assert "é" not in result  # Becomes caf-r-sum after regex

    def test_default_max_len_is_40(self):
        result = _slugify("a" * 50)
        assert len(result) <= 40


# =========================================================================
# _parse_tag_params
# =========================================================================

class TestParseTagParams:
    def test_basic_params(self):
        result = _parse_tag_params("search_term=K562, organism=Homo sapiens")
        assert result == {"search_term": "K562", "organism": "Homo sapiens"}

    def test_single_param(self):
        result = _parse_tag_params("tool=get_experiment")
        assert result == {"tool": "get_experiment"}

    def test_empty_string(self):
        assert _parse_tag_params("") == {}

    def test_none(self):
        assert _parse_tag_params(None) == {}

    def test_quoted_values_stripped(self):
        result = _parse_tag_params('name="hello"')
        assert result == {"name": "hello"}

    def test_single_quoted_values_stripped(self):
        result = _parse_tag_params("name='hello'")
        assert result == {"name": "hello"}

    def test_value_with_equals_sign(self):
        result = _parse_tag_params("formula=a=b")
        assert result == {"formula": "a=b"}

    def test_whitespace_handling(self):
        result = _parse_tag_params("  key = value , key2 = value2  ")
        assert result == {"key": "value", "key2": "value2"}

    def test_no_equals_sign_ignored(self):
        result = _parse_tag_params("search_term=K562, badparam")
        assert result == {"search_term": "K562"}

    def test_python_style_array_is_deserialized(self):
        result = _parse_tag_params("sets=['c2c12r1', 'c2c12r2', 'c2c12r3']")
        assert result == {"sets": ["c2c12r1", "c2c12r2", "c2c12r3"]}


# =========================================================================
# _pick_file_tool
# =========================================================================

class TestPickFileTool:
    def test_csv_file(self):
        assert _pick_file_tool("data.csv") == "parse_csv_file"

    def test_tsv_file(self):
        assert _pick_file_tool("data.tsv") == "parse_csv_file"

    def test_bed_file(self):
        assert _pick_file_tool("output.bed") == "parse_bed_file"

    def test_other_file(self):
        assert _pick_file_tool("readme.txt") == "read_file_content"

    def test_uppercase_csv(self):
        assert _pick_file_tool("DATA.CSV") == "parse_csv_file"

    def test_no_extension(self):
        assert _pick_file_tool("Makefile") == "read_file_content"


# =========================================================================
# _looks_like_assay
# =========================================================================

class TestLooksLikeAssay:
    def test_rnaseq(self):
        assert _looks_like_assay("RNA-seq") is True

    def test_chipseq(self):
        assert _looks_like_assay("ChIP-seq") is True

    def test_atacseq(self):
        assert _looks_like_assay("ATAC-seq") is True

    def test_long_read_rna(self):
        assert _looks_like_assay("long read RNA-seq") is True

    def test_wgbs(self):
        assert _looks_like_assay("WGBS") is True

    def test_eclip(self):
        assert _looks_like_assay("eCLIP") is True

    def test_biosample_not_assay(self):
        assert _looks_like_assay("K562") is False

    def test_cell_line_not_assay(self):
        assert _looks_like_assay("HeLa") is False

    def test_organism_not_assay(self):
        assert _looks_like_assay("Homo sapiens") is False

    def test_empty_string(self):
        assert _looks_like_assay("") is False

    def test_dnase_seq(self):
        assert _looks_like_assay("DNase-seq") is True

    def test_alias_key_match(self):
        assert _looks_like_assay("mirna-seq") is True

    def test_alias_value_match(self):
        assert _looks_like_assay("total RNA-seq") is True


# =========================================================================
# _resolve_workflow_path
# =========================================================================

class TestResolveWorkflowPath:
    WORKFLOWS = [
        {"work_dir": "/data/users/eli/proj/workflow1", "sample_name": "s1"},
        {"work_dir": "/data/users/eli/proj/workflow2", "sample_name": "s2"},
    ]
    DEFAULT_WD = "/data/users/eli/proj/workflow1"

    def test_empty_subpath_returns_default(self):
        assert _resolve_workflow_path("", self.DEFAULT_WD, self.WORKFLOWS) == self.DEFAULT_WD

    def test_workflow_name_resolves(self):
        result = _resolve_workflow_path("workflow2", self.DEFAULT_WD, self.WORKFLOWS)
        assert result == "/data/users/eli/proj/workflow2"

    def test_workflow_with_subdir(self):
        result = _resolve_workflow_path("workflow2/annot", self.DEFAULT_WD, self.WORKFLOWS)
        assert result == "/data/users/eli/proj/workflow2/annot"

    def test_unknown_subpath_appended_to_default(self):
        result = _resolve_workflow_path("annot", self.DEFAULT_WD, self.WORKFLOWS)
        assert result == "/data/users/eli/proj/workflow1/annot"

    def test_no_workflows_appends_to_default(self):
        result = _resolve_workflow_path("annot", self.DEFAULT_WD, [])
        assert result == "/data/users/eli/proj/workflow1/annot"

    def test_empty_default_wd(self):
        result = _resolve_workflow_path("annot", "", [])
        assert result == ""  # Can't resolve without a default


# =========================================================================
# _resolve_file_path
# =========================================================================

class TestResolveFilePath:
    WORKFLOWS = [
        {"work_dir": "/data/proj/workflow1", "sample_name": "sample1"},
        {"work_dir": "/data/proj/workflow2", "sample_name": "sample2"},
    ]
    DEFAULT_WD = "/data/proj/workflow1"

    def test_simple_filename(self):
        wd, fn = _resolve_file_path("File.csv", self.DEFAULT_WD, self.WORKFLOWS)
        assert wd == self.DEFAULT_WD
        assert fn == "File.csv"

    def test_subdir_filename(self):
        wd, fn = _resolve_file_path("annot/File.csv", self.DEFAULT_WD, self.WORKFLOWS)
        assert fn == "File.csv"  # basename only for recursive search

    def test_workflow_prefix(self):
        wd, fn = _resolve_file_path("workflow2/annot/File.csv", self.DEFAULT_WD, self.WORKFLOWS)
        assert wd == "/data/proj/workflow2"
        assert fn == "File.csv"

    def test_backslash_normalized(self):
        wd, fn = _resolve_file_path("workflow2\\annot\\File.csv", self.DEFAULT_WD, self.WORKFLOWS)
        assert wd == "/data/proj/workflow2"
        assert fn == "File.csv"

    def test_leading_trailing_slashes_stripped(self):
        wd, fn = _resolve_file_path("/File.csv/", self.DEFAULT_WD, self.WORKFLOWS)
        assert fn == "File.csv"

    def test_explicit_workflow_prefix_without_known_workflows_uses_project_root(self):
        wd, fn = _resolve_file_path("workflow2/annot/File.csv", "/data/proj", [])
        assert wd == "/data/proj/workflow2"
        assert fn == "File.csv"

    def test_explicit_workflow_prefix_without_known_workflows_from_workflow_default(self):
        wd, fn = _resolve_file_path("workflow2/annot/File.csv", "/data/proj/workflow1", [])
        assert wd == "/data/proj/workflow2"
        assert fn == "File.csv"


# =========================================================================
# plot intent
# =========================================================================

class TestUserWantsPlot:
    def test_immediate_plot_request_detected(self):
        assert user_wants_plot("plot a histogram of DF1") is True

    def test_deferred_plot_request_not_detected(self):
        assert user_wants_plot("I want a DF so the data can be plotted later") is False

    def test_venn_request_detected(self):
        assert user_wants_plot("make a venn diagram of DF1") is True


class TestParsePlotTags:
    def test_venn_tag_keeps_set_columns(self):
        specs = parse_plot_tags("[[PLOT: type=venn, df=DF2, sets=treated|control|rescue]]")

        assert specs == [{"type": "venn", "df": "DF2", "sets": "treated|control|rescue", "df_id": 2}]

    def test_upset_tag_keeps_overlap_build_params(self):
        specs = parse_plot_tags(
            "[[PLOT: type=upset, dfs=DF1|DF2, match_cols=gene_symbol|gene_id, labels=Treated|Control, max_intersections=5]]"
        )

        assert specs == [{
            "type": "upset",
            "dfs": "DF1|DF2",
            "match_cols": "gene_symbol|gene_id",
            "labels": "Treated|Control",
            "max_intersections": "5",
            "df_id": None,
        }]

    def test_upset_tag_accepts_python_list_sets(self):
        specs = parse_plot_tags(
            "[[PLOT: type=upset, df=DF3, sets=['c2c12r1', 'c2c12r2', 'c2c12r3']]]"
        )

        assert specs == [{
            "type": "upset",
            "df": "DF3",
            "sets": ["c2c12r1", "c2c12r2", "c2c12r3"],
            "df_id": 3,
        }]


def test_direct_overlap_rewrite_preserves_label_override_for_existing_membership_df():
    plot_specs = [{"type": "venn", "df": "DF1", "df_id": 1, "labels": "Control|Treatment"}]
    current_dataframes = {
        "overlap_membership_components.csv": {
            "columns": ["match_key", "Alpha", "Beta"],
            "data": [
                {"match_key": "C1", "Alpha": True, "Beta": False},
                {"match_key": "C2", "Alpha": True, "Beta": True},
                {"match_key": "C3", "Alpha": False, "Beta": True},
            ],
            "row_count": 3,
            "metadata": {
                "df_id": 1,
                "kind": "overlap_membership",
                "set_columns": ["Alpha", "Beta"],
                "visible": True,
            },
        }
    }

    apply_overlap_message_hints(plot_specs, "rename the venn labels", [], current_dataframes=current_dataframes)
    materialize_direct_set_overlap_plot_dataframes(
        plot_specs,
        current_dataframes,
        [],
        current_dataframes=current_dataframes,
    )

    assert plot_specs[0]["sets"] == "Alpha|Beta"
    assert plot_specs[0]["labels"] == "Control|Treatment"


class TestSpecializedDataframePlotSpec:
    def test_bed_count_dataframe_prefers_bar_by_chromosome_and_genome(self):
        df_data = {
            "columns": ["Sample", "Genome", "Modification", "Chromosome", "Count"],
            "data": [
                {"Sample": "JamshidP", "Genome": "mm39", "Modification": "inosine", "Chromosome": "chr1", "Count": 7},
                {"Sample": "JamshidP", "Genome": "hg38", "Modification": "inosine", "Chromosome": "chr1", "Count": 2},
            ],
            "metadata": {"kind": "bed_chromosome_counts"},
        }

        spec = _build_specialized_dataframe_plot_spec(df_data)

        assert spec == {
            "type": "bar",
            "x": "Chromosome",
            "y": "Count",
            "color": "Genome",
            "title": "Regions per Chromosome by Genome",
        }
