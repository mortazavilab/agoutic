"""Tests for cortex/edgepython_plot_params.py — parameter normalization helpers."""

import pytest

from cortex.edgepython_plot_params import (
    coerce_plot_dpi,
    coerce_optional_bool,
    extract_plot_dpi,
    extract_label_transcripts,
    extract_plot_type,
    normalize_bar_mode,
    extract_bar_mode,
    build_svg_companion_path,
    normalize_generate_plot_params,
)


# ---------------------------------------------------------------------------
# coerce_plot_dpi
# ---------------------------------------------------------------------------

class TestCoercePlotDpi:
    def test_none_returns_none(self):
        assert coerce_plot_dpi(None) is None

    def test_empty_string_returns_none(self):
        assert coerce_plot_dpi("") is None

    def test_bool_returns_none(self):
        assert coerce_plot_dpi(True) is None
        assert coerce_plot_dpi(False) is None

    def test_positive_int_returns_int(self):
        assert coerce_plot_dpi(300) == 300
        assert coerce_plot_dpi(600) == 600

    def test_negative_int_returns_none(self):
        assert coerce_plot_dpi(-100) is None
        assert coerce_plot_dpi(0) is None

    def test_float_coerced_to_int(self):
        assert coerce_plot_dpi(300.7) == 300

    def test_digit_string_returns_int(self):
        assert coerce_plot_dpi("600") == 600

    def test_non_digit_string_returns_none(self):
        assert coerce_plot_dpi("high") is None

    def test_preset_web(self):
        assert coerce_plot_dpi("web") == 300

    def test_preset_publication(self):
        assert coerce_plot_dpi("publication") == 600

    def test_preset_high_res(self):
        assert coerce_plot_dpi("high res") == 900

    def test_preset_journal_max(self):
        assert coerce_plot_dpi("journal max") == 1200


# ---------------------------------------------------------------------------
# coerce_optional_bool
# ---------------------------------------------------------------------------

class TestCoerceOptionalBool:
    def test_none_returns_none(self):
        assert coerce_optional_bool(None) is None

    def test_empty_string_returns_none(self):
        assert coerce_optional_bool("") is None

    def test_actual_bool_passed_through(self):
        assert coerce_optional_bool(True) is True
        assert coerce_optional_bool(False) is False

    def test_int_1_is_true(self):
        assert coerce_optional_bool(1) is True

    def test_int_0_is_false(self):
        assert coerce_optional_bool(0) is False

    def test_true_spellings(self):
        for val in ("true", "yes", "y", "on", "1"):
            assert coerce_optional_bool(val) is True, f"Expected True for {val}"

    def test_false_spellings(self):
        for val in ("false", "no", "n", "off", "0"):
            assert coerce_optional_bool(val) is False, f"Expected False for {val}"

    def test_unclear_returns_none(self):
        assert coerce_optional_bool("maybe") is None


# ---------------------------------------------------------------------------
# extract_plot_dpi
# ---------------------------------------------------------------------------

class TestExtractPlotDpi:
    def test_numeric_dpi_extraction(self):
        assert extract_plot_dpi("300 dpi") == 300
        assert extract_plot_dpi("resolution=600") == 600

    def test_preset_phrase_extraction(self):
        assert extract_plot_dpi("publication quality") == 600
        assert extract_plot_dpi("high resolution image") == 900

    def test_empty_string_returns_none(self):
        assert extract_plot_dpi("") is None


# ---------------------------------------------------------------------------
# extract_label_transcripts
# ---------------------------------------------------------------------------

class TestExtractLabelTranscripts:
    def test_show_transcripts(self):
        assert extract_label_transcripts("show transcripts on the plot") is True

    def test_label_isoforms(self):
        assert extract_label_transcripts("label isoform ids") is True

    def test_no_request_returns_false(self):
        assert extract_label_transcripts("just a regular plot") is False

    def test_empty_string_returns_false(self):
        assert extract_label_transcripts("") is False


# ---------------------------------------------------------------------------
# extract_plot_type
# ---------------------------------------------------------------------------

class TestExtractPlotType:
    def test_pca_detected(self):
        assert extract_plot_type("show me a PCA plot") == "pca"

    def test_stacked_bar_detected(self):
        assert extract_plot_type("stacked bar chart") == "stacked_bar"

    def test_heatmap_detected(self):
        assert extract_plot_type("heatmap visualization") == "heatmap"

    def test_volcano_detected(self):
        assert extract_plot_type("volcano plot") == "volcano"

    def test_ma_plot_detected(self):
        assert extract_plot_type("MA plot") == "ma"

    def test_md_plot_detected(self):
        assert extract_plot_type("mean difference plot") == "md"

    def test_bar_detected(self):
        assert extract_plot_type("bar chart") == "bar"

    def test_no_match_returns_none(self):
        assert extract_plot_type("random text") is None

    def test_empty_string_returns_none(self):
        assert extract_plot_type("") is None


# ---------------------------------------------------------------------------
# normalize_bar_mode
# ---------------------------------------------------------------------------

class TestNormalizeBarMode:
    def test_none_returns_none(self):
        assert normalize_bar_mode(None) is None

    def test_group_aliases(self):
        assert normalize_bar_mode("group") == "group"
        assert normalize_bar_mode("grouped") == "group"

    def test_stacked_aliases(self):
        assert normalize_bar_mode("stack") == "stack"
        assert normalize_bar_mode("stacked") == "stack"

    def test_percent_aliases(self):
        assert normalize_bar_mode("percent") == "percent"
        assert normalize_bar_mode("percentage") == "percent"
        assert normalize_bar_mode("normalized") == "percent"
        assert normalize_bar_mode("normalised") == "percent"

    def test_unknown_returns_none(self):
        assert normalize_bar_mode("unknown") is None


# ---------------------------------------------------------------------------
# extract_bar_mode
# ---------------------------------------------------------------------------

class TestExtractBarMode:
    def test_percent_text_detected(self):
        assert extract_bar_mode("show percent bars") == "percent"

    def test_stacked_text_detected(self):
        assert extract_bar_mode("stacked bars") == "stack"

    def test_grouped_text_detected(self):
        assert extract_bar_mode("grouped by category") == "group"

    def test_stacked_bar_plot_type_defaults_to_stack(self):
        assert extract_bar_mode("", plot_type="stacked_bar") == "stack"

    def test_empty_no_plot_type_returns_none(self):
        assert extract_bar_mode("") is None


# ---------------------------------------------------------------------------
# build_svg_companion_path
# ---------------------------------------------------------------------------

class TestBuildSvgCompanionPath:
    def test_png_to_svg(self):
        assert build_svg_companion_path("/path/to/plot.png") == "/path/to/plot.svg"

    def test_jpg_to_svg(self):
        assert build_svg_companion_path("/path/to/plot.jpg") == "/path/to/plot.svg"

    def test_pdf_to_svg(self):
        assert build_svg_companion_path("/path/to/plot.pdf") == "/path/to/plot.svg"


# ---------------------------------------------------------------------------
# normalize_generate_plot_params
# ---------------------------------------------------------------------------

class TestNormalizeGeneratePlotParams:
    def test_plot_type_normalized(self):
        result = normalize_generate_plot_params({"plot_type": "mean_difference"})
        assert result["plot_type"] == "md"

    def test_inferred_plot_type_from_text_pool(self):
        result = normalize_generate_plot_params({}, text_pool="show a PCA plot")
        assert result.get("plot_type") == "pca"

    def test_bar_with_stacked_hint_becomes_stacked_bar(self):
        result = normalize_generate_plot_params(
            {"plot_type": "bar"}, text_pool="stacked bar chart"
        )
        assert result["plot_type"] == "stacked_bar"

    def test_df_ref_converted_to_df_id(self):
        result = normalize_generate_plot_params({"df": "DF42"})
        assert result.get("df_id") == 42

    def test_resolution_moved_to_dpi(self):
        result = normalize_generate_plot_params({"resolution": 600})
        assert result["dpi"] == 600
        assert "resolution" not in result

    def test_svg_output_path_generated(self):
        result = normalize_generate_plot_params({"output_path": "/plot.png"})
        assert result["svg_output_path"] == "/plot.svg"
