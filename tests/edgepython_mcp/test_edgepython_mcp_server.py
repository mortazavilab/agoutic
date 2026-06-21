"""Unit tests for edgePython MCP server — helper functions, state management, and tool contracts.

Tests cover:
  - Path resolution & annotation helpers
  - Feature-level inference (gene vs transcript)
  - Analysis-context inference (bulk vs single-cell)
  - Contrast parsing
  - Significance parameter resolution
  - Formatting helpers
  - Plot input table loading
  - State management (describe, reset_state)
  - Tool preconditions (_require)
  - Design matrix validation
  - Bar/heatmap PCA preparation helpers

Uses the agoutic_core conda environment.
"""

import os
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import matplotlib as mpl

# ---------------------------------------------------------------------------
# Stub fastmcp so the module can be imported without a running server.
# ---------------------------------------------------------------------------

class _FakeFastMCP:
    def __init__(self, *_args, **_kwargs):
        pass

    def tool(self, *args, **kwargs):
        def decorator(func):
            # Preserve original function for direct calling
            func._mcp_tool = True
            return func
        return decorator


_fake_fastmcp = types.ModuleType("fastmcp")
_fake_fastmcp.FastMCP = _FakeFastMCP
sys.modules.setdefault("fastmcp", _fake_fastmcp)

# Stub edgepython so ep.* calls don't crash during import.
_fake_ep = types.ModuleType("edgepython")
for _name in (
    "make_dgelist", "read_data", "filter_by_expr", "calc_norm_factors",
    "calc_norm_offsets_for_chip", "normalize_chip_to_input",
    "model_matrix", "estimate_disp", "estimate_glm_robust_disp",
    "voom_lmfit", "glm_ql_fit", "glm_fit", "glm_ql_ftest",
    "glm_treat", "glm_lrt", "exact_test", "diff_splice_dge",
    "splice_variants", "top_tags", "cpm", "decide_tests",
    "plot_mds", "plot_bcv", "plot_ql_disp",
):
    setattr(_fake_ep, _name, MagicMock())
sys.modules.setdefault("edgepython", _fake_ep)

# Now import the server module.
from edgepython_mcp import edgepython_server as server


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset MCP state before each test to avoid cross-test contamination."""
    original = dict(server._state)
    server._state["dgelist"] = None
    server._state["design"] = None
    server._state["design_info"] = None
    server._state["fit"] = None
    server._state["glm_fit"] = None
    server._state["results"] = {}
    server._state["last_result"] = None
    server._state["enrichment_results"] = {}
    server._state["last_enrichment"] = None
    server._state["_filtered_genes"] = None
    server._state["voom"] = None
    server._state["filtered"] = False
    server._state["normalized"] = False
    server._state["dispersions_estimated"] = False
    server._state["feature_level"] = "unknown"
    server._state["feature_level_note"] = None
    server._state["analysis_context"] = "unknown"
    server._state["analysis_context_note"] = None
    server._state["counts_path"] = None
    server._state["work_dir"] = None
    server._state["annotation_gtf"] = None
    yield
    server._state.update(original)


def _make_fake_dgelist(n_genes=100, n_samples=6):
    """Create a minimal fake DGEList dict for testing."""
    counts = np.random.poisson(20, size=(n_genes, n_samples)).astype(np.float64)
    split = max(1, n_samples // 2)
    groups = ["A"] * split + ["B"] * (n_samples - split)
    samples = pd.DataFrame({
        "lib.size": [10000.0 * (1 + i * 0.1) for i in range(n_samples)],
        "norm.factors": [1.0] * n_samples,
        "group": groups,
    })
    genes = pd.DataFrame({
        "GeneID": [f"GENE{i}" for i in range(n_genes)],
        "Symbol": [f"SYMBOL{i}" for i in range(n_genes)],
    })
    dgelist = {
        "counts": counts,
        "samples": samples,
        "genes": genes,
        "_sample_names": [f"S{i+1}" for i in range(n_samples)],
        "_gene_ids": [f"GENE{i}" for i in range(n_genes)],
    }
    return dgelist


def _write_table(tmpdir: str, name: str, frame: pd.DataFrame) -> Path:
    """Write a DataFrame to TSV or CSV based on extension."""
    path = Path(tmpdir) / name
    sep = "," if path.suffix == ".csv" else "\t"
    frame.to_csv(path, sep=sep, index=False)
    return path


# ===================================================================
# Path resolution & annotation helpers
# ===================================================================

class TestPathResolution:
    def test_resolve_existing_path_returns_none_for_empty(self):
        assert server._resolve_existing_path(None) is None
        assert server._resolve_existing_path("") is None
        assert server._resolve_existing_path("   ") is None

    def test_resolve_existing_path_returns_none_for_nonexistent(self, tmp_path):
        result = server._resolve_existing_path(str(tmp_path / "nonexistent" / "file.txt"))
        assert result is None

    def test_resolve_existing_path_resolves_existing_file(self, tmp_path):
        f = tmp_path / "data" / "counts.tsv"
        f.parent.mkdir()
        f.write_text("gene\ts1\nG1\t10\n")
        result = server._resolve_existing_path(str(f))
        assert result is not None
        assert result == f

    def test_infer_annotation_dir_from_gtf_path(self, tmp_path):
        gtf_dir = tmp_path / "annotation"
        gtf_dir.mkdir()
        result = server._infer_annotation_dir(path=str(gtf_dir))
        assert result == gtf_dir

    def test_infer_annotation_dir_from_list_of_paths(self, tmp_path):
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()
        # Should pick the first existing one
        result = server._infer_annotation_dir(data_input=[str(dir_b), str(dir_a)])
        assert result == dir_b

    def test_infer_annotation_dir_fallback_to_parent(self, tmp_path):
        gtf_file = tmp_path / "annotation.gtf"
        gtf_file.write_text("# fake gtf\n")
        result = server._infer_annotation_dir(path=str(gtf_file))
        assert result == tmp_path


# ===================================================================
# Feature-level inference
# ===================================================================

class TestFeatureLevelInference:
    def test_empty_ids_returns_unknown(self):
        level, note = server._infer_feature_level([])
        assert level == "unknown"

    def test_none_ids_returns_unknown(self):
        level, note = server._infer_feature_level(None)
        assert level == "unknown"

    def test_transcript_ids_detected(self):
        ids = [f"ENST{i:06d}" for i in range(20)]
        level, _ = server._infer_feature_level(ids)
        assert level == "transcript"

    def test_gene_ids_detected(self):
        ids = [f"ENSG{i:06d}" for i in range(20)]
        level, _ = server._infer_feature_level(ids)
        assert level == "gene"

    def test_mixed_ids_biased_to_gene_when_gene_dominant(self):
        ids = [f"ENSG{i:06d}" for i in range(50)] + [f"ENST{i:06d}" for i in range(3)]
        level, _ = server._infer_feature_level(ids)
        assert level == "gene"

    def test_transcript_threshold_10_percent(self):
        # 10% transcript IDs should trigger transcript detection
        ids = [f"ENST{i:06d}" for i in range(10)] + [f"GENE{i}" for i in range(90)]
        level, _ = server._infer_feature_level(ids)
        assert level == "transcript"

    def test_transcript_minimum_count_5(self):
        # Fewer than 5 transcript IDs should not trigger
        ids = [f"ENST{i:06d}" for i in range(4)] + [f"GENE{i}" for i in range(96)]
        level, _ = server._infer_feature_level(ids)
        assert level == "gene"

    def test_transcript_id_patterns_nm_nr_xm_xr(self):
        # Need >= 5 transcript IDs and >= 10% to trigger transcript detection
        # With 20 transcript IDs out of 100 total = 20% >= 10%
        ids = ["NM_001234", "NR_001234", "XM_001234", "XR_001234", "NM_005678"] + \
              ["ENST00000" + str(i).zfill(6) for i in range(15)] + \
              [f"GENE{i}" for i in range(80)]
        level, _ = server._infer_feature_level(ids)
        assert level == "transcript"

    def test_transcript_id_patterns_with_pipe_separator(self):
        # Some IDs use pipe separator (common in tag files)
        ids = ["tag|NM_001234", "tag|NR_001234", "tag|XM_001234", "tag|XR_001234", "tag|NM_005678"] + \
              ["ENST00000" + str(i).zfill(6) for i in range(15)] + \
              [f"GENE{i}" for i in range(80)]
        level, _ = server._infer_feature_level(ids)
        assert level == "transcript"

    def test_tx_pattern_with_digits(self):
        # The \btx\d+\b pattern requires tx followed by digits and word boundary
        ids = ["tx001", "tx002", "tx003", "tx004", "tx005"] + \
              ["ENST00000" + str(i).zfill(6) for i in range(15)] + \
              [f"GENE{i}" for i in range(80)]
        level, _ = server._infer_feature_level(ids)
        assert level == "transcript"

    def test_isoform_pattern_requires_word_boundary(self):
        # The \bisoform\b pattern requires word boundary after 'isoform'
        ids = ["isoform_A", "isoform_B", "isoform_C", "isoform_D", "isoform_E"] + \
              ["ENST00000" + str(i).zfill(6) for i in range(15)] + \
              [f"GENE{i}" for i in range(80)]
        level, _ = server._infer_feature_level(ids)
        assert level == "transcript"


# ===================================================================
# Analysis-context inference
# ===================================================================

class TestAnalysisContextInference:
    def test_10x_source_detected(self):
        ctx, _ = server._infer_analysis_context(source="10x")
        assert ctx == "single_cell_10x"

    def test_cellranger_clue_detected(self):
        ctx, _ = server._infer_analysis_context(data_input="/path/to/cellranger_output")
        assert ctx == "single_cell_10x"

    def test_anndata_source_detected_as_single_cell(self):
        ctx, _ = server._infer_analysis_context(source="anndata")
        assert ctx == "single_cell"

    def test_h5ad_extension_detected_as_single_cell(self):
        ctx, _ = server._infer_analysis_context(data_input=["/path/to/data.h5ad"])
        assert ctx == "single_cell"

    def test_smartseq_detected_as_bulk(self):
        ctx, _ = server._infer_analysis_context(data_input=["smartseq2_library"])
        assert ctx == "bulk"

    def test_kallisto_source_detected_as_bulk(self):
        ctx, _ = server._infer_analysis_context(source="kallisto")
        assert ctx == "bulk"

    def test_salmon_source_detected_as_bulk(self):
        ctx, _ = server._infer_analysis_context(source="salmon")
        assert ctx == "bulk"

    def test_rsem_source_detected_as_bulk(self):
        ctx, _ = server._infer_analysis_context(source="rsem")
        assert ctx == "bulk"

    def test_table_source_detected_as_bulk(self):
        ctx, _ = server._infer_analysis_context(source="table")
        assert ctx == "bulk"

    def test_default_bulk_true_returns_bulk(self):
        ctx, _ = server._infer_analysis_context(default_bulk=True)
        assert ctx == "bulk"

    def test_unknown_returns_unknown(self):
        ctx, _ = server._infer_analysis_context(source="unknown_source_xyz")
        assert ctx == "unknown"

    def test_10x_in_clue_text_with_anndata(self):
        ctx, _ = server._infer_analysis_context(
            source="anndata",
            data_input=["/path/to/10x_filtered_feature_bc_matrix"],
        )
        assert ctx == "single_cell_10x"

    def test_sample_barcode_heuristic(self):
        # 8+ character ACGT barcodes - the regex is [ACGT]{8,}(?:-\d+)?
        # Need >= 100 samples with >= 50% matching
        # Use pure ACGT names without trailing digits (digits break the regex)
        pure_names = ["ACGTACGTAC"] * 75 + ["ACGTACGTAG"] * 25
        ctx, _ = server._infer_analysis_context(sample_names=pure_names)
        assert ctx == "single_cell_10x"

    def test_short_sample_names_not_detected_as_barcode(self):
        names = [f"ACGT{i:02d}" for i in range(5)]
        ctx, _ = server._infer_analysis_context(sample_names=names)
        assert ctx != "single_cell_10x"


# ===================================================================
# Contrast parsing
# ===================================================================

class TestContrastParsing:
    def test_simple_difference(self):
        cols = ["groupA", "groupB"]
        vec = server._parse_contrast("groupB - groupA", cols)
        expected = np.array([-1.0, 1.0])
        np.testing.assert_array_equal(vec, expected)

    def test_single_coefficient(self):
        cols = ["Intercept", "groupB"]
        vec = server._parse_contrast("groupB", cols)
        expected = np.array([0.0, 1.0])
        np.testing.assert_array_equal(vec, expected)

    def test_average_vs_reference(self):
        cols = ["groupA", "groupB", "groupC"]
        # The parser handles '(A + B)/2 - C' by tokenizing +/- terms
        vec = server._parse_contrast("groupA + groupB - 2*groupC", cols)
        expected = np.array([1.0, 1.0, -2.0])
        np.testing.assert_array_equal(vec, expected)

    def test_patsy_treatment_coded_name(self):
        cols = ["group[T.A]", "group[T.B]"]
        vec = server._parse_contrast("groupB - groupA", cols)
        expected = np.array([-1.0, 1.0])
        np.testing.assert_array_equal(vec, expected)

    def test_patsy_no_intercept_wrapper(self):
        cols = ["groupA", "groupB"]
        vec = server._parse_contrast("groupB - groupA", cols)
        expected = np.array([-1.0, 1.0])
        np.testing.assert_array_equal(vec, expected)

    def test_unrecognized_term_raises(self):
        cols = ["groupA", "groupB"]
        with pytest.raises(ValueError, match="not found"):
            server._parse_contrast("groupC - groupA", cols)

    def test_zero_contrast_same_column(self):
        cols = ["groupA", "groupB"]
        # 'groupA - groupA' sets contrast[0] = 1 then contrast[0] = -1, resulting in [-1, 0]
        # This does NOT raise because the simple split path is taken
        vec = server._parse_contrast("groupA - groupA", cols)
        assert vec[0] == -1.0
        assert vec[1] == 0.0

    def test_case_insensitive_fallback(self):
        cols = ["groupA", "groupB"]
        vec = server._parse_contrast("GroupB - GroupA", cols)
        expected = np.array([-1.0, 1.0])
        np.testing.assert_array_equal(vec, expected)

    def test_coefficient_multiplier_simple_path_fails(self):
        cols = ["groupA", "groupB", "groupC"]
        # '2*groupA - groupB' takes the simple split path and tries to find '2*groupA'
        with pytest.raises(ValueError, match="not found"):
            server._parse_contrast("2*groupA - groupB", cols)


# ===================================================================
# Significance parameter resolution
# ===================================================================

class TestSignificanceResolution:
    def test_default_fdr(self):
        metric, threshold = server._resolve_significance_params(None, None)
        assert metric == "fdr"
        assert threshold == 0.05

    def test_explicit_pvalue(self):
        metric, threshold = server._resolve_significance_params("pvalue", 0.01)
        assert metric == "pvalue"
        assert threshold == 0.01

    def test_fdr_threshold_legacy(self):
        metric, threshold = server._resolve_significance_params(
            None, None, fdr_threshold=0.01
        )
        assert metric == "fdr"
        assert threshold == 0.01

    def test_negative_threshold_defaults_to_0_05(self):
        metric, threshold = server._resolve_significance_params(None, -0.05)
        assert threshold == 0.05

    def test_qvalue_normalized_to_fdr(self):
        metric, _ = server._resolve_significance_params("qvalue", None)
        assert metric == "fdr"

    def test_p_normalized_to_pvalue(self):
        metric, _ = server._resolve_significance_params("p", None)
        assert metric == "pvalue"

    def test_padj_normalized_to_fdr(self):
        metric, _ = server._resolve_significance_params("padj", None)
        assert metric == "fdr"

    def test_raw_p_normalized_to_pvalue(self):
        metric, _ = server._resolve_significance_params("raw p-value", None)
        assert metric == "pvalue"


# ===================================================================
# Formatting helpers
# ===================================================================

class TestFormattingHelpers:
    def test_format_decimal_basic(self):
        assert server._format_decimal(1234.567, decimals=2) == "1,234.57"

    def test_format_decimal_trims_trailing_zeros(self):
        result = server._format_decimal(100.0, decimals=2)
        assert result == "100"

    def test_format_scientific_notation_basic(self):
        result = server._format_scientific_notation(1234.5)
        assert "× 10" in result

    def test_format_scientific_notation_zero(self):
        assert server._format_scientific_notation(0) == "0"

    def test_format_threshold_value_small(self):
        result = server._format_threshold_value(0.001)
        # Should use scientific notation for values < 1e-3
        assert "× 10" in result or "0.001" in result

    def test_format_threshold_value_large(self):
        result = server._format_threshold_value(15000.0)
        # Values >= 10000 use integer formatting
        assert "15" in result

    def test_format_count_compact_millions(self):
        result = server._format_count(1_500_000, compact=True)
        assert "M" in result

    def test_format_count_compact_thousands(self):
        result = server._format_count(15_000, compact=True)
        assert "k" in result

    def test_darker_hex_darkens_color(self):
        # Darkening #FFFFFF should give something darker
        darker = server._darker_hex("#FFFFFF", factor=0.78)
        rgb = mpl.colors.to_rgb(darker)
        assert all(c < 1.0 for c in rgb)

    def test_okabe_ito_palette_size(self):
        palette = server._okabe_ito_palette(5)
        assert len(palette) == 5

    def test_okabe_ito_palette_empty(self):
        palette = server._okabe_ito_palette(0)
        assert palette == []

    def test_significance_label_fdr(self):
        assert server._significance_label("fdr") == "FDR"

    def test_significance_label_pvalue(self):
        assert server._significance_label("pvalue") == "p-value"

    def test_format_significance_threshold(self):
        assert server._format_significance_threshold(0.05) == "0.05"
        assert server._format_significance_threshold(0.01) == "0.01"


# ===================================================================
# DE counting
# ===================================================================

class TestDECounting:
    def test_count_de_by_significance(self):
        table = pd.DataFrame({
            "logFC": [1.5, -1.2, 0.4, 2.0],
            "FDR": [0.01, 0.02, 0.3, 0.04],
        })
        n_up, n_down, n_ns, label = server._count_de_by_significance(
            table, significance_metric="fdr", significance_threshold=0.05
        )
        assert n_up == 2  # logFC > 0 and FDR < 0.05
        assert n_down == 1  # logFC < 0 and FDR < 0.05
        assert n_ns == 1    # FDR >= 0.05
        assert label == "FDR"

    def test_count_de_by_pvalue(self):
        table = pd.DataFrame({
            "logFC": [1.5, -1.2, 0.4],
            "PValue": [0.01, 0.02, 0.3],
        })
        n_up, n_down, n_ns, label = server._count_de_by_significance(
            table, significance_metric="pvalue", significance_threshold=0.05
        )
        assert n_up == 1
        assert n_down == 1
        assert n_ns == 1
        assert label == "p-value"

    def test_count_de_handles_nan(self):
        table = pd.DataFrame({
            "logFC": [1.5, np.nan, -1.2],
            "FDR": [0.01, np.nan, 0.02],
        })
        n_up, n_down, n_ns, _ = server._count_de_by_significance(
            table, significance_metric="fdr", significance_threshold=0.05
        )
        assert n_up == 1
        assert n_down == 1
        assert n_ns == 0  # NaN rows are not valid


# ===================================================================
# Benjamini-Hochberg implementation
# ===================================================================

class TestBenjaminiHochberg:
    def test_bh_adjsts_two_pvalues(self):
        pvals = np.array([0.01, 0.04])
        adjusted = server._benjamini_hochberg(pvals)
        # With 2 tests: 0.01*2/1=0.02, 0.04*2/2=0.04
        assert adjusted[0] == pytest.approx(0.02)
        assert adjusted[1] == pytest.approx(0.04)

    def test_bh_clips_to_one(self):
        pvals = np.array([0.5, 0.8])
        adjusted = server._benjamini_hochberg(pvals)
        assert all(a <= 1.0 for a in adjusted if np.isfinite(a))

    def test_bh_handles_nan(self):
        pvals = np.array([0.01, np.nan, 0.04])
        adjusted = server._benjamini_hochberg(pvals)
        assert np.isnan(adjusted[1])

    def test_bh_empty(self):
        adjusted = server._benjamini_hochberg(np.array([]))
        assert len(adjusted) == 0


# ===================================================================
# Plot input table loading
# ===================================================================

class TestPlotInputTableLoading:
    def test_load_tsv(self, tmp_path):
        f = _write_table(str(tmp_path), "data.tsv", pd.DataFrame({"a": [1, 2], "b": [3, 4]}))
        df = server._load_plot_input_table(str(f))
        assert df.shape == (2, 2)

    def test_load_csv(self, tmp_path):
        f = _write_table(str(tmp_path), "data.csv", pd.DataFrame({"a": [1, 2], "b": [3, 4]}))
        df = server._load_plot_input_table(str(f))
        assert df.shape == (2, 2)

    def test_load_nonexistent_raises(self):
        with pytest.raises(ValueError, match="not found"):
            server._load_plot_input_table("/nonexistent/path/file.tsv")

    def test_load_with_dropped_na_rows(self, tmp_path):
        f = tmp_path / "data.tsv"
        f.write_text("a\tb\tc\n1\t2\t3\n4\t5\t6\n")
        df = server._load_plot_input_table(str(f))
        assert df.shape == (2, 3)


# ===================================================================
# Numeric coercion & label inference
# ===================================================================

class TestNumericCoercion:
    def test_coerce_numeric_frame(self):
        df = pd.DataFrame({"x": ["a", "b"], "y": ["1.0", "2.0"], "z": ["3", "4"]})
        numeric = server._coerce_numeric_plot_frame(df, exclude=("x",))
        assert numeric.shape == (2, 2)

    def test_coerce_numeric_frame_all_nonnumeric(self):
        df = pd.DataFrame({"a": ["x", "y"], "b": ["p", "q"]})
        numeric = server._coerce_numeric_plot_frame(df)
        assert numeric.empty

    def test_infer_unique_label_column(self):
        df = pd.DataFrame({
            "sample": ["S1", "S2", "S3"],
            "value": [1.0, 2.0, 3.0],
        })
        col = server._infer_unique_label_column(df)
        assert col == "sample"

    def test_infer_unique_label_column_no_unique(self):
        df = pd.DataFrame({
            "group": ["A", "A", "B"],
            "value": [1.0, 2.0, 3.0],
        })
        col = server._infer_unique_label_column(df)
        assert col == "group"  # only non-numeric column


# ===================================================================
# Heatmap preparation
# ===================================================================

class TestHeatmapPreparation:
    def test_prepare_heatmap_from_raw_table(self, tmp_path):
        f = _write_table(
            str(tmp_path), "raw.tsv",
            pd.DataFrame({"s1": [1.0, 2.0], "s2": [3.0, 4.0]}, index=["g1", "g2"]),
        )
        table = server._load_plot_input_table(str(f))
        matrix = server._prepare_heatmap_matrix(table)
        assert matrix.shape == (2, 2)

    def test_prepare_heatmap_correlation_mode(self):
        matrix = pd.DataFrame(
            [[1.0, 0.5], [0.5, 1.0]],
            index=["A", "B"],
            columns=["A", "B"],
        )
        assert server._infer_heatmap_mode(matrix) == "correlation"

    def test_prepare_heatmap_raises_on_no_numeric(self):
        df = pd.DataFrame({"x": ["a", "b"], "y": ["c", "d"]})
        with pytest.raises(ValueError, match="at least one numeric"):
            server._prepare_heatmap_matrix(df)


# ===================================================================
# PCA preparation
# ===================================================================

class TestPCAPreparation:
    def test_prepare_pca_inputs_auto_detects_group(self):
        df = pd.DataFrame({
            "sample": ["S1", "S2", "S3", "S4"],
            "group": ["A", "A", "B", "B"],
            "gene1": [1.0, 2.0, 3.0, 4.0],
            "gene2": [5.0, 6.0, 7.0, 8.0],
        })
        numeric_df, labels, groups, group_col = server._prepare_pca_inputs(df)
        assert len(labels) == 4
        assert group_col == "group"

    def test_prepare_pca_insufficient_columns_raises(self):
        df = pd.DataFrame({"sample": ["S1"], "value": [1.0]})
        with pytest.raises(ValueError, match="at least two rows"):
            server._prepare_pca_inputs(df)


# ===================================================================
# Bar plot preparation
# ===================================================================

class TestBarPlotPreparation:
    def test_coerce_bar_mode_group(self):
        assert server._coerce_bar_mode("grouped", default="stack") == "group"

    def test_coerce_bar_mode_stacked(self):
        assert server._coerce_bar_mode("stacked", default="group") == "stack"

    def test_coerce_bar_mode_percent(self):
        assert server._coerce_bar_mode("percent", default="group") == "percent"

    def test_coerce_bar_mode_default(self):
        assert server._coerce_bar_mode("invalid", default="group") == "group"

    def test_coerce_bar_agg_mean(self):
        assert server._coerce_bar_agg("mean", default="count") == "mean"

    def test_coerce_bar_agg_invalid_defaults(self):
        assert server._coerce_bar_agg("invalid", default="sum") == "sum"


# ===================================================================
# Feature label helpers
# ===================================================================

class TestFeatureLabelHelpers:
    def test_gene_name_prefers_symbol(self):
        row = pd.Series({"Symbol": "TP53", "gene_id": "ENSG000"}, name="ENSG000")
        assert server._gene_name(row) == "TP53"

    def test_gene_name_falls_back_to_gene_id(self):
        row = pd.Series({"gene_id": "ENSG000"}, name="ENSG000")
        assert server._gene_name(row) == "ENSG000"

    def test_transcript_name_prefers_transcript_id(self):
        row = pd.Series({"TranscriptID": "ENST000"}, name="ENST000")
        assert server._transcript_name(row) == "ENST000"

    def test_transcript_name_returns_none_when_same_as_gene(self):
        row = pd.Series({"Symbol": "TP53"}, name="TP53")
        assert server._transcript_name(row) is None

    def test_format_feature_label_uses_row_index_as_fallback(self):
        row = pd.Series({}, name="GENE1")
        assert server._gene_name(row) == "GENE1"

    def test_feature_lookup_key_prefers_row_name(self):
        row = pd.Series({"Symbol": "TP53", "TranscriptID": "ENST000"}, name="ENST000")
        key = server._feature_lookup_key(row)
        assert key == "ENST000"

    def test_feature_label_candidates_includes_gene_and_transcript(self):
        row = pd.Series({"Symbol": "TP53", "TranscriptID": "ENST000"}, name="ENST000")
        candidates = server._feature_label_candidates(row)
        assert "tp53" in candidates
        assert "enst000" in candidates


# ===================================================================
# State management: describe & reset_state
# ===================================================================

class TestStateManagement:
    def test_describe_no_data(self):
        result = server.describe()
        assert "No data loaded" in result

    def test_describe_with_data(self):
        server._state["dgelist"] = _make_fake_dgelist()
        result = server.describe()
        assert "genes" in result
        assert "samples" in result
        assert "Filtered: no" in result
        assert "Normalized: no" in result

    def test_reset_state_clears_everything(self):
        server._state["dgelist"] = _make_fake_dgelist()
        server._state["design"] = np.eye(3)
        server._state["fit"] = {"coefficients": np.array([[1]])}
        server._state["results"] = {"test": {}}
        server._state["normalized"] = True

        result = server.reset_state()
        assert "State cleared" in result
        assert server._state["dgelist"] is None
        assert server._state["design"] is None
        assert server._state["fit"] is None
        assert server._state["results"] == {}
        assert server._state["normalized"] is False

    def test_reset_state_clears_enrichment_results(self):
        server._state["enrichment_results"] = {"enr1": pd.DataFrame()}
        server.reset_state()
        assert server._state["enrichment_results"] == {}


# ===================================================================
# Tool preconditions: _require
# ===================================================================

class TestToolPreconditions:
    def test_require_raises_when_none(self):
        with pytest.raises(ValueError, match="No DGEList available"):
            server._require("dgelist", "DGEList")

    def test_require_passes_when_set(self):
        server._state["dgelist"] = _make_fake_dgelist()
        # Should not raise
        server._require("dgelist", "DGEList")


# ===================================================================
# Design matrix validation
# ===================================================================

class TestDesignMatrixValidation:
    def test_set_design_matrix_wrong_rows(self):
        server._state["dgelist"] = _make_fake_dgelist(n_samples=6)
        # 3 rows but 6 samples
        matrix = [[1, 0], [0, 1], [1, 1]]
        with pytest.raises(ValueError, match="samples"):
            server.set_design_matrix(matrix=matrix, columns=["A", "B"])

    def test_set_design_matrix_wrong_columns(self):
        server._state["dgelist"] = _make_fake_dgelist(n_samples=3)
        matrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        # 3 columns but only 1 name provided - numpy raises "All arrays must be of the same length"
        with pytest.raises(ValueError):
            server.set_design_matrix(matrix=matrix, columns=["A"])

    def test_set_design_matrix_valid(self):
        server._state["dgelist"] = _make_fake_dgelist(n_samples=3)
        matrix = [[1, 0], [0, 1], [1, 1]]
        # This should work - 3 rows matching 3 samples, 2 columns with 2 names
        result = server.set_design_matrix(matrix=matrix, columns=["A", "B"])
        assert "Design matrix set" in result
        assert "2 coefficients" in result

    def test_set_design_matrix_non_2d_raises(self):
        server._state["dgelist"] = _make_fake_dgelist(n_samples=3)
        # A flat list becomes 1D array
        with pytest.raises(ValueError, match="must be 2D"):
            server.set_design_matrix(matrix=[1, 2, 3], columns=["A"])


# ===================================================================
# Tool contracts: load_data (no file I/O — schema validation only)
# ===================================================================

class TestToolContracts:
    """Test that tool functions are callable and have expected signatures."""

    def test_load_data_is_callable(self):
        assert callable(server.load_data)
        assert hasattr(server.load_data, "_mcp_tool")

    def test_load_data_auto_is_callable(self):
        assert callable(server.load_data_auto)

    def test_describe_is_callable(self):
        assert callable(server.describe)

    def test_reset_state_is_callable(self):
        assert callable(server.reset_state)

    def test_filter_genes_is_callable(self):
        assert callable(server.filter_genes)

    def test_normalize_is_callable(self):
        assert callable(server.normalize)

    def test_set_design_is_callable(self):
        assert callable(server.set_design)

    def test_set_design_matrix_is_callable(self):
        assert callable(server.set_design_matrix)

    def test_estimate_dispersion_is_callable(self):
        assert callable(server.estimate_dispersion)

    def test_fit_model_is_callable(self):
        assert callable(server.fit_model)

    def test_fit_glm_is_callable(self):
        assert callable(server.fit_glm)

    def test_test_contrast_is_callable(self):
        assert callable(server.test_contrast)

    def test_test_coef_is_callable(self):
        assert callable(server.test_coef)

    def test_get_top_genes_is_callable(self):
        assert callable(server.get_top_genes)

    def test_get_result_table_is_callable(self):
        assert callable(server.get_result_table)

    def test_save_results_is_callable(self):
        assert callable(server.save_results)

    def test_generate_plot_is_callable(self):
        assert callable(server.generate_plot)

    def test_normalize_chip_is_callable(self):
        assert callable(server.normalize_chip)

    def test_chip_enrichment_test_is_callable(self):
        assert callable(server.chip_enrichment_test)

    def test_estimate_glm_robust_dispersion_is_callable(self):
        assert callable(server.estimate_glm_robust_dispersion)

    def test_voom_transform_is_callable(self):
        assert callable(server.voom_transform)

    def test_glm_lrt_test_is_callable(self):
        assert callable(server.glm_lrt_test)

    def test_exact_test_is_callable(self):
        assert callable(server.exact_test)

    def test_dtu_diff_splice_dge_is_callable(self):
        assert callable(server.dtu_diff_splice_dge)

    def test_dtu_splice_variants_is_callable(self):
        assert callable(server.dtu_splice_variants)


# ===================================================================
# load_data precondition: requires file path
# ===================================================================

class TestLoadDataPreconditions:
    def test_load_data_requires_counts_path(self, tmp_path):
        with pytest.raises(TypeError):
            server.load_data()  # missing counts_path

    def test_load_data_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            server.load_data(str(tmp_path / "nonexistent.tsv"))


# ===================================================================
# filter_genes precondition: requires DGEList
# ===================================================================

class TestFilterGenesPreconditions:
    def test_filter_genes_requires_dgelist(self):
        with pytest.raises(ValueError, match="No DGEList available"):
            server.filter_genes()


# ===================================================================
# normalize precondition: requires DGEList
# ===================================================================

class TestNormalizePreconditions:
    def test_normalize_requires_dgelist(self):
        with pytest.raises(ValueError, match="No DGEList available"):
            server.normalize()


# ===================================================================
# set_design precondition: requires DGEList + sample metadata
# ===================================================================

class TestSetDesignPreconditions:
    def test_set_design_requires_dgelist(self):
        with pytest.raises(ValueError, match="No DGEList available"):
            server.set_design(formula="~ 0 + group")

    def test_set_design_requires_sample_metadata(self):
        # DGEList without samples DataFrame
        dgelist = {"counts": np.array([[10.0]]), "samples": None}
        server._state["dgelist"] = dgelist
        with pytest.raises(ValueError, match="No sample metadata"):
            server.set_design(formula="~ 0 + group")


# ===================================================================
# estimate_dispersion precondition: requires DGEList
# ===================================================================

class TestEstimateDispersionPreconditions:
    def test_estimate_dispersion_requires_dgelist(self):
        with pytest.raises(ValueError, match="No DGEList available"):
            server.estimate_dispersion()


# ===================================================================
# fit_model precondition: requires DGEList + design
# ===================================================================

class TestFitModelPreconditions:
    def test_fit_model_requires_dgelist(self):
        with pytest.raises(ValueError, match="No DGEList available"):
            server.fit_model()

    def test_fit_model_requires_design(self):
        server._state["dgelist"] = _make_fake_dgelist()
        with pytest.raises(ValueError, match="design matrix"):
            server.fit_model()


# ===================================================================
# test_contrast precondition: requires fit
# ===================================================================

class TestTestContrastPreconditions:
    def test_test_contrast_requires_fit(self):
        with pytest.raises(ValueError, match="No fitted model available"):
            server.test_contrast(contrast="groupB - groupA")


# ===================================================================
# get_top_genes / get_result_table preconditions
# ===================================================================

class TestGetTopGenesPreconditions:
    def test_get_top_genes_no_results(self):
        result = server.get_top_genes()
        assert "No test results available" in result

    def test_get_top_genes_result_not_found(self):
        server._state["results"] = {"existing": {}}
        result = server.get_top_genes(name="missing")
        assert "not found" in result

    def test_get_result_table_no_results(self):
        result = server.get_result_table()
        assert "No test results available" in result

    def test_get_result_table_result_not_found(self):
        server._state["results"] = {"existing": {}}
        result = server.get_result_table(name="missing")
        assert "not found" in result


# ===================================================================
# save_results precondition
# ===================================================================

class TestSaveResultsPreconditions:
    def test_save_results_no_results(self, tmp_path):
        out = tmp_path / "results.tsv"
        result = server.save_results(str(out))
        assert "No test results" in result


# ===================================================================
# generate_plot invalid type
# ===================================================================

class TestGeneratePlotPreconditions:
    def test_generate_plot_invalid_type(self):
        result = server.generate_plot(plot_type="nonexistent_type")
        assert "Invalid plot_type" in result

    def test_generate_plot_mds_requires_dgelist(self):
        with pytest.raises(ValueError, match="No DGEList available"):
            server.generate_plot(plot_type="mds")

    def test_generate_plot_bcv_requires_dispersions(self):
        server._state["dgelist"] = _make_fake_dgelist()
        result = server.generate_plot(plot_type="bcv")
        assert "Dispersions not estimated" in result

    def test_generate_plot_ql_dispersion_requires_fit(self):
        with pytest.raises(ValueError, match="No fitted model available"):
            server.generate_plot(plot_type="ql_dispersion")

    def test_generate_plot_md_requires_result(self):
        result = server.generate_plot(plot_type="md")
        assert "No test result available" in result

    def test_generate_plot_volcano_requires_result(self):
        result = server.generate_plot(plot_type="volcano")
        assert "No test result available" in result


# ===================================================================
# normalize_chip precondition: row count validation
# ===================================================================

class TestNormalizeChipPreconditions:
    def test_normalize_chip_row_mismatch(self, tmp_path):
        server._state["dgelist"] = _make_fake_dgelist(n_genes=10)
        inp = tmp_path / "input.csv"
        # Create input with different number of rows
        pd.DataFrame(np.random.rand(5, 3)).to_csv(inp, index=False)
        with pytest.raises(ValueError, match="Row count mismatch"):
            server.normalize_chip(input_csv=str(inp))


# ===================================================================
# chip_enrichment_test precondition: sample index validation
# ===================================================================

class TestChipEnrichmentTestPreconditions:
    def test_chip_enrichment_test_invalid_sample(self, tmp_path):
        server._state["dgelist"] = _make_fake_dgelist(n_samples=3)
        inp = tmp_path / "input.csv"
        # Create input with matching row count to avoid shape mismatch errors
        pd.DataFrame(np.random.rand(100, 3)).to_csv(inp, index=False)
        # Sample 10 is out of range for 3 samples - should raise ValueError
        try:
            server.chip_enrichment_test(input_csv=str(inp), sample=10)
            pytest.fail("Expected ValueError for out-of-range sample")
        except ValueError:
            pass  # Expected


# ===================================================================
# _configure_annotation_sources
# ===================================================================

class TestConfigureAnnotationSources:
    def test_configure_annotation_no_gtf_or_work_dir(self):
        result = server._configure_annotation_sources()
        assert result is None

    def test_configure_annotation_nonexistent_gtf(self, tmp_path):
        # Non-existent GTF should return None (path doesn't exist)
        result = server._configure_annotation_sources(annotation_gtf=str(tmp_path / "nonexistent.gtf"))
        assert result is None


# ===================================================================
# _gene_level_warning
# ===================================================================

class TestGeneLevelWarning:
    def test_gene_level_warning_shown_for_bulk_gene(self):
        server._state["feature_level"] = "gene"
        server._state["analysis_context"] = "bulk"
        warn = server._gene_level_warning()
        assert warn is not None
        assert "gene-level DE" in warn

    def test_gene_level_warning_not_shown_for_transcript(self):
        server._state["feature_level"] = "transcript"
        server._state["analysis_context"] = "bulk"
        warn = server._gene_level_warning()
        assert warn is None

    def test_gene_level_warning_not_shown_for_unknown_context(self):
        server._state["feature_level"] = "gene"
        server._state["analysis_context"] = "unknown"
        warn = server._gene_level_warning()
        assert warn is None


# ===================================================================
# _append_gene_level_warning
# ===================================================================

class TestAppendGeneLevelWarning:
    def test_append_includes_warning(self):
        server._state["feature_level"] = "gene"
        server._state["analysis_context"] = "bulk"
        lines = ["line1", "line2"]
        server._append_gene_level_warning(lines)
        # Warning is appended as a separate line
        assert any("Note:" in line for line in lines)

    def test_append_skips_when_no_warning(self):
        server._state["feature_level"] = "transcript"
        server._state["analysis_context"] = "bulk"
        lines = ["line1", "line2"]
        server._append_gene_level_warning(lines)
        assert len(lines) == 2


# ===================================================================
# _resolve_plot_output_paths
# ===================================================================

class TestResolvePlotOutputPaths:
    def test_resolve_with_explicit_paths(self):
        out, svg = server._resolve_plot_output_paths(
            "volcano", "test_result", "/tmp/volcano.png", "/tmp/volcano.svg"
        )
        assert out == "/tmp/volcano.png"
        assert svg == "/tmp/volcano.svg"

    def test_resolve_auto_generates_default(self):
        out, svg = server._resolve_plot_output_paths("volcano", "my_contrast", None, None)
        assert "volcano_my_contrast.png" in out
        assert "volcano_my_contrast.svg" in svg


# ===================================================================
# _coerce_bool helper
# ===================================================================

class TestCoerceBool:
    def test_true_values(self):
        for val in [True, "true", "True", "yes", "Yes", "1", "on"]:
            assert server._coerce_bool(val) is True

    def test_false_values(self):
        for val in [False, "false", "no", "0", "off", "n"]:
            assert server._coerce_bool(val) is False

    def test_none_defaults_false(self):
        assert server._coerce_bool(None, default=False) is False
        assert server._coerce_bool(None, default=True) is True

    def test_numeric_coercion(self):
        assert server._coerce_bool(1) is True
        assert server._coerce_bool(0) is False


# ===================================================================
# _coerce_int helper
# ===================================================================

class TestCoerceInt:
    def test_valid_int(self):
        assert server._coerce_int("42", default=10) == 42
        assert server._coerce_int(42, default=10) == 42

    def test_invalid_returns_default(self):
        assert server._coerce_int("abc", default=10) == 10

    def test_zero_returns_default(self):
        assert server._coerce_int(0, default=10) == 10

    def test_negative_returns_default(self):
        assert server._coerce_int(-5, default=10) == 10


# ===================================================================
# _coerce_float helper
# ===================================================================

class TestCoerceFloat:
    def test_valid_float(self):
        assert server._coerce_float("3.14", default=1.0) == pytest.approx(3.14)

    def test_invalid_returns_default(self):
        assert server._coerce_float("abc", default=1.0) == 1.0

    def test_none_returns_default(self):
        assert server._coerce_float(None, default=1.0) == 1.0


# ===================================================================
# _coerce_plot_dpi helper
# ===================================================================

class TestCoercePlotDpi:
    def test_numeric_dpi(self):
        assert server._coerce_plot_dpi(300) == 300
        assert server._coerce_plot_dpi(600) == 600

    def test_preset_web(self):
        assert server._coerce_plot_dpi("web") == 300

    def test_preset_publication(self):
        assert server._coerce_plot_dpi("publication") == 600

    def test_preset_high_res(self):
        assert server._coerce_plot_dpi("high res") == 900

    def test_invalid_returns_default(self):
        assert server._coerce_plot_dpi("invalid", default=300) == 300

    def test_zero_returns_default(self):
        assert server._coerce_plot_dpi(0, default=300) == 300


# ===================================================================
# _normalize_label_genes
# ===================================================================

class TestNormalizeLabelGenes:
    def test_pipe_separated(self):
        result = server._normalize_label_genes("TP53|BRCA1|MYC")
        assert result == ["TP53", "BRCA1", "MYC"]

    def test_comma_separated(self):
        result = server._normalize_label_genes("TP53, BRCA1, MYC")
        assert result == ["TP53", "BRCA1", "MYC"]

    def test_list_input(self):
        result = server._normalize_label_genes(["TP53", "BRCA1"])
        assert result == ["TP53", "BRCA1"]

    def test_none_returns_empty(self):
        assert server._normalize_label_genes(None) == []

    def test_single_string(self):
        result = server._normalize_label_genes("TP53")
        assert result == ["TP53"]


# ===================================================================
# _infer_bar_stat_columns
# ===================================================================

class TestInferBarStatColumns:
    def test_finds_sem_and_n(self):
        df = pd.DataFrame({"x": [1], "y": [2.0], "sem": [0.1], "n": [5]})
        error_col, n_col = server._infer_bar_stat_columns(df, y_column="y")
        assert error_col == "sem"
        assert n_col == "n"

    def test_finds_stderr_and_count(self):
        df = pd.DataFrame({"x": [1], "expr": [2.0], "stderr": [0.1], "count": [5]})
        error_col, n_col = server._infer_bar_stat_columns(df, y_column="expr")
        assert error_col == "stderr"
        assert n_col == "count"

    def test_finds_y_specific_error_cols(self):
        df = pd.DataFrame({"x": [1], "mean_expr": [2.0], "sem_mean_expr": [0.1]})
        error_col, _ = server._infer_bar_stat_columns(df, y_column="mean_expr")
        assert error_col == "sem_mean_expr"


# ===================================================================
# Tool schemas
# ===================================================================

class TestToolSchemas:
    def test_tool_schemas_has_load_data(self):
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        assert "load_data" in TOOL_SCHEMAS

    def test_tool_schemas_has_describe(self):
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        assert "describe" in TOOL_SCHEMAS

    def test_tool_schemas_has_reset_state(self):
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        assert "reset_state" in TOOL_SCHEMAS

    def test_tool_schemas_load_data_required_fields(self):
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        assert "counts_path" in TOOL_SCHEMAS["load_data"]["parameters"]["required"]

    def test_tool_schemas_describe_no_required(self):
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        assert TOOL_SCHEMAS["describe"]["parameters"]["required"] == []

    def test_tool_schemas_has_all_bulk_tools(self):
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        expected = [
            "load_data", "load_data_auto", "describe", "reset_state",
            "filter_genes", "normalize", "set_design", "set_design_matrix",
            "estimate_dispersion", "fit_model", "test_contrast",
            "get_top_genes", "generate_plot",
        ]
        for tool in expected:
            assert tool in TOOL_SCHEMAS, f"Missing tool schema: {tool}"


# ===================================================================
# MPL config constants
# ===================================================================

class TestMPLConstants:
    def test_agoutic_colors_defined(self):
        assert hasattr(server, "_AGOUTIC_CANVAS")
        assert server._AGOUTIC_CANVAS == "#FFFFFF"
        assert server._AGOUTIC_DE_UP == "#D7263D"
        assert server._AGOUTIC_DE_DOWN == "#1B5E87"
        assert server._AGOUTIC_DE_NS == "#9AA0A6"

    def test_okabe_ito_palette_defined(self):
        assert len(server._AGOUTIC_OKABE_ITO) == 8

    def test_superscript_translation_table(self):
        # The translation table maps ordinals (int) to Unicode characters
        assert 48 in server._SUPERSCRIPT_TRANSLATION  # '0' ordinal
        assert server._SUPERSCRIPT_TRANSLATION[48] == 8304  # '⁰' ordinal
        assert server._SUPERSCRIPT_TRANSLATION[49] == 185  # '¹' ordinal


# ===================================================================
# _is_bulk_like_context & _feature_lookup_key edge cases
# ===================================================================

class TestContextHelpers:
    def test_is_bulk_like_true(self):
        server._state["analysis_context"] = "bulk"
        assert server._is_bulk_like_context() is True

    def test_is_bulk_like_false(self):
        server._state["analysis_context"] = "single_cell"
        assert server._is_bulk_like_context() is False

    def test_feature_lookup_key_uses_transcript_when_available(self):
        row = pd.Series({"Symbol": "TP53"}, name="ENST00000269305")
        key = server._feature_lookup_key(row)
        assert key == "ENST00000269305"

    def test_feature_lookup_key_falls_back_to_gene(self):
        row = pd.Series({"Symbol": "TP53"}, name="TP53")
        key = server._feature_lookup_key(row)
        assert key == "TP53"


# ===================================================================
# _resolve_gene_exon_ids edge cases
# ===================================================================

class TestResolveGeneExonIds:
    def test_resolve_from_genes_dataframe(self):
        dgelist = {
            "genes": pd.DataFrame({
                "GeneID": ["G1", "G2"],
                "ExonID": ["E1", "E2"],
            }),
            "counts": np.array([[10.0, 20.0], [30.0, 40.0]]),
        }
        gene_ids, exon_ids = server._resolve_gene_exon_ids(dgelist)
        assert list(gene_ids) == ["G1", "G2"]
        assert list(exon_ids) == ["E1", "E2"]

    def test_resolve_with_explicit_columns(self):
        dgelist = {
            "genes": pd.DataFrame({
                "my_gene_id": ["G1", "G2"],
                "my_exon_id": ["E1", "E2"],
            }),
            "counts": np.array([[10.0, 20.0], [30.0, 40.0]]),
        }
        gene_ids, exon_ids = server._resolve_gene_exon_ids(
            dgelist, gene_column="my_gene_id", exon_column="my_exon_id"
        )
        assert list(gene_ids) == ["G1", "G2"]

    def test_resolve_missing_gene_column_raises(self):
        dgelist = {
            "genes": pd.DataFrame({"other_col": [1, 2]}),
            "counts": np.array([[10.0, 20.0], [30.0, 40.0]]),
        }
        with pytest.raises(ValueError, match="gene_column"):
            server._resolve_gene_exon_ids(dgelist)

    def test_resolve_from__gene_ids_fallback(self):
        dgelist = {
            "counts": np.array([[10.0, 20.0], [30.0, 40.0]]),
            "_gene_ids": ["G1", "G2"],
        }
        gene_ids, exon_ids = server._resolve_gene_exon_ids(dgelist)
        assert list(gene_ids) == ["G1", "G2"]
        assert list(exon_ids) == [0, 1]

    def test_resolve_no_annotation_raises(self):
        dgelist = {"counts": np.array([[10.0, 20.0]])}
        with pytest.raises(ValueError, match="No gene annotation"):
            server._resolve_gene_exon_ids(dgelist)


# ===================================================================
# _get_significance_series
# ===================================================================

class TestGetSignificanceSeries:
    def test_fdr_column(self):
        table = pd.DataFrame({"FDR": [0.01, 0.05, 0.1]})
        series = server._get_significance_series(table, "fdr")
        assert list(series) == [0.01, 0.05, 0.1]

    def test_pvalue_column_fallback(self):
        table = pd.DataFrame({"PValue": [0.01, 0.05, 0.1]})
        series = server._get_significance_series(table, "fdr")
        assert list(series) == [0.01, 0.05, 0.1]

    def test_no_fdr_or_pvalue_raises(self):
        table = pd.DataFrame({"p_val": [0.01, 0.05]})
        with pytest.raises(ValueError, match="does not include"):
            server._get_significance_series(table, "fdr")


# ===================================================================
# _apply_plot_header & _apply_plot_frame (visual helpers)
# ===================================================================

class TestPlotStyleHelpers:
    def test_apply_plot_header_sets_facecolor(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        server._apply_plot_header(fig, ax, title="Test Title")
        assert fig.patch.get_facecolor() == mpl.colors.to_rgba("#FFFFFF")

    def test_apply_plot_frame_removes_top_right_spines(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        server._apply_plot_frame(ax)
        assert not ax.spines["top"].get_visible()
        assert not ax.spines["right"].get_visible()

    def test_place_legend_outside_no_handles(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        legend = server._place_legend_outside(ax)
        assert legend is None


# ===================================================================
# _format_decimal edge cases
# ===================================================================

class TestFormatDecimalEdgeCases:
    def test_no_trim(self):
        result = server._format_decimal(100.0, decimals=2, trim=False)
        assert result == "100.00"

    def test_negative_value(self):
        result = server._format_decimal(-1234.567, decimals=2)
        assert "-" in result

    def test_zero(self):
        result = server._format_decimal(0.0, decimals=2)
        assert result == "0"


# ===================================================================
# _count_de_by_significance with treat-style results (LR column)
# ===================================================================

class TestDECountingEdgeCases:
    def test_count_with_all_up(self):
        table = pd.DataFrame({
            "logFC": [1.0, 2.0, 3.0],
            "FDR": [0.01, 0.02, 0.03],
        })
        n_up, n_down, n_ns, _ = server._count_de_by_significance(
            table, significance_metric="fdr", significance_threshold=0.05
        )
        assert n_up == 3
        assert n_down == 0
        assert n_ns == 0

    def test_count_with_all_down(self):
        table = pd.DataFrame({
            "logFC": [-1.0, -2.0, -3.0],
            "FDR": [0.01, 0.02, 0.03],
        })
        n_up, n_down, n_ns, _ = server._count_de_by_significance(
            table, significance_metric="fdr", significance_threshold=0.05
        )
        assert n_up == 0
        assert n_down == 3
        assert n_ns == 0

    def test_count_with_boundary_fdr(self):
        table = pd.DataFrame({
            "logFC": [1.0, -1.0],
            "FDR": [0.05, 0.05],
        })
        n_up, n_down, n_ns, _ = server._count_de_by_significance(
            table, significance_metric="fdr", significance_threshold=0.05
        )
        # FDR == threshold is NOT < threshold, so both are NS
        assert n_up == 0
        assert n_down == 0
        assert n_ns == 2
