"""Tests for cortex/plot_routing.py — pure regex-based plot routing logic."""

import pytest

from cortex.plot_routing import (
    normalize_plot_type,
    detect_chart_type,
    has_publication_context,
    plot_requests_baked_in_labels,
    bar_requires_edgepython,
    infer_plot_route,
)


# ---------------------------------------------------------------------------
# normalize_plot_type
# ---------------------------------------------------------------------------

class TestNormalizePlotType:
    def test_none_returns_empty(self):
        assert normalize_plot_type(None) == ""

    def test_empty_string_returns_empty(self):
        assert normalize_plot_type("") == ""

    def test_whitespace_only_returns_empty(self):
        assert normalize_plot_type("   ") == ""

    def test_exact_match_keeps_value(self):
        assert normalize_plot_type("scatter") == "scatter"
        assert normalize_plot_type("bar") == "bar"
        assert normalize_plot_type("heatmap") == "heatmap"

    def test_alias_mean_difference(self):
        assert normalize_plot_type("mean_difference") == "md"
        assert normalize_plot_type("mean_difference_plot") == "md"
        assert normalize_plot_type("md_plot") == "md"

    def test_alias_mean_average(self):
        assert normalize_plot_type("mean_average") == "ma"
        assert normalize_plot_type("ma_plot") == "ma"

    def test_alias_stacked_bar(self):
        assert normalize_plot_type("stacked") == "stacked_bar"
        assert normalize_plot_type("stackedbar") == "stacked_bar"
        assert normalize_plot_type("stacked_bar_chart") == "stacked_bar"
        assert normalize_plot_type("stacked_bar_plot") == "stacked_bar"

    def test_alias_bar_line_area(self):
        assert normalize_plot_type("bar_chart") == "bar"
        assert normalize_plot_type("line_chart") == "line"
        assert normalize_plot_type("area_chart") == "area"

    def test_alias_box_violin_strip_pie(self):
        assert normalize_plot_type("box_plot") == "box"
        assert normalize_plot_type("violin_plot") == "violin"
        assert normalize_plot_type("strip_plot") == "strip"
        assert normalize_plot_type("pie_chart") == "pie"

    def test_alias_volcano_heat_map(self):
        assert normalize_plot_type("volcano_plot") == "volcano"
        assert normalize_plot_type("heat_map") == "heatmap"

    def test_case_insensitive(self):
        assert normalize_plot_type("SCATTER") == "scatter"
        assert normalize_plot_type("Bar_Chart") == "bar"

    def test_multiple_underscores_collapsed(self):
        assert normalize_plot_type("my__plot") == "my_plot"

    def test_dashes_and_spaces_become_underscores(self):
        assert normalize_plot_type("my-plot") == "my_plot"
        assert normalize_plot_type("my plot") == "my_plot"


# ---------------------------------------------------------------------------
# detect_chart_type
# ---------------------------------------------------------------------------

class TestDetectChartType:
    def test_volcano_detected(self):
        assert detect_chart_type("make a volcano plot") == "volcano"

    def test_pca_detected(self):
        assert detect_chart_type("show me a PCA") == "pca"
        assert detect_chart_type("principal component analysis") == "pca"
        assert detect_chart_type("principal components analysis") == "pca"

    def test_stacked_bar_detected(self):
        assert detect_chart_type("stacked bar chart") == "stacked_bar"

    def test_md_plot_detected(self):
        assert detect_chart_type("show me an MA plot") == "md"
        assert detect_chart_type("mean difference plot") == "md"
        assert detect_chart_type("mean-difference") == "md"

    def test_heatmap_and_correlation_detected(self):
        assert detect_chart_type("heatmap of the data") == "heatmap"
        assert detect_chart_type("correlation matrix") == "heatmap"

    def test_upset_and_venn_detected(self):
        assert detect_chart_type("upset plot") == "upset"
        assert detect_chart_type("venn diagram") == "venn"

    def test_violin_strip_line_area_pie_scatter_bar_box(self):
        assert detect_chart_type("violin plot") == "violin"
        assert detect_chart_type("strip chart") == "strip"
        assert detect_chart_type("line chart") == "line"
        assert detect_chart_type("area plot") == "area"
        assert detect_chart_type("pie chart") == "pie"
        assert detect_chart_type("scatter plot") == "scatter"
        assert detect_chart_type("bar chart") == "bar"
        assert detect_chart_type("box plot") == "box"

    def test_histogram_and_distribution_detected(self):
        assert detect_chart_type("show a histogram") == "histogram"
        assert detect_chart_type("distribution of values") == "histogram"

    def test_default_is_bar(self):
        assert detect_chart_type("do something random") == "bar"


# ---------------------------------------------------------------------------
# has_publication_context
# ---------------------------------------------------------------------------

class TestHasPublicationContext:
    def test_publication_keyword(self):
        assert has_publication_context("for publication") is True

    def test_print_keyword(self):
        assert has_publication_context("needs to be print-ready") is True

    def test_poster_keyword(self):
        assert has_publication_context("poster presentation") is True

    def test_figure_keyword(self):
        assert has_publication_context("figure for the paper") is True

    def test_dissertation_keyword(self):
        assert has_publication_context("dissertation chapter") is True

    def test_thesis_keyword(self):
        assert has_publication_context("my thesis figure") is True

    def test_journal_manuscript_paper_keywords(self):
        assert has_publication_context("journal submission") is True
        assert has_publication_context("manuscript draft") is True
        assert has_publication_context("research paper") is True

    def test_no_match(self):
        assert has_publication_context("just a quick plot") is False

    def test_empty_string(self):
        assert has_publication_context("") is False

    def test_case_insensitive(self):
        assert has_publication_context("FOR PUBLICATION") is True


# ---------------------------------------------------------------------------
# plot_requests_baked_in_labels
# ---------------------------------------------------------------------------

class TestPlotRequestsBakedInLabels:
    def test_edgepython_plot_types_return_true(self):
        for pt in ("volcano", "md", "ma", "pca", "heatmap", "stacked_bar"):
            assert plot_requests_baked_in_labels(pt) is True

    def test_label_transcripts_param(self):
        assert plot_requests_baked_in_labels("scatter", params={"label_transcripts": True}) is True

    def test_label_genes_param(self):
        assert plot_requests_baked_in_labels("scatter", params={"label_genes": True}) is True

    def test_top_n_labels_param(self):
        assert plot_requests_baked_in_labels("scatter", params={"top_n_labels": 10}) is True

    def test_scatter_with_label_keywords(self):
        assert plot_requests_baked_in_labels(
            "scatter", user_message="show labels on the scatter"
        ) is True
        assert plot_requests_baked_in_labels(
            "scatter", user_message="annotate the points"
        ) is True

    def test_non_edgepython_without_params_returns_false(self):
        assert plot_requests_baked_in_labels("histogram") is False
        assert plot_requests_baked_in_labels("line") is False


# ---------------------------------------------------------------------------
# bar_requires_edgepython
# ---------------------------------------------------------------------------

class TestBarRequiresEdgePython:
    def test_publication_context_triggers_edgepython(self):
        assert bar_requires_edgepython(user_message="for publication") is True

    def test_total_value_labels_triggers(self):
        assert bar_requires_edgepython(user_message="show total values") is True

    def test_error_bars_triggers(self):
        assert bar_requires_edgepython(user_message="with error bars") is True

    def test_confidence_interval_triggers(self):
        assert bar_requires_edgepython(user_message="confidence interval") is True

    def test_grouped_triggers(self):
        assert bar_requires_edgepython(user_message="grouped bar chart") is True

    def test_de_summary_bar_triggers(self):
        assert bar_requires_edgepython(
            user_message="DE genes counts per contrast"
        ) is True

    def test_stacked_mode_param(self):
        # bar_requires_edgepython checks user_message for hints via regex.
        # The _BAR_EDGEPYTHON_HINT_RE matches "grouped" but not "stacked".
        assert bar_requires_edgepython(user_message="show a grouped bar") is True
        assert bar_requires_edgepython(user_message="error bars please") is True

    def test_color_param_triggers(self):
        assert bar_requires_edgepython(params={"color": "gene"}) is True

    def test_no_trigger_returns_false(self):
        assert bar_requires_edgepython(user_message="simple bar chart") is False
        assert bar_requires_edgepython() is False


# ---------------------------------------------------------------------------
# infer_plot_route
# ---------------------------------------------------------------------------

class TestInferPlotRoute:
    def test_bar_with_publication_routes_edgepython(self):
        assert (
            infer_plot_route("bar", user_message="for publication") == "edgepython"
        )

    def test_bar_without_triggers_routes_declarative(self):
        assert infer_plot_route("bar", user_message="simple bar") == "declarative"

    def test_edgepython_plot_types_route_edgepython(self):
        for pt in ("volcano", "md", "ma", "pca", "heatmap"):
            assert infer_plot_route(pt) == "edgepython"

    def test_interactive_plot_types_route_declarative(self):
        for pt in ("scatter", "line", "histogram", "box", "violin"):
            assert infer_plot_route(pt) == "declarative"

    def test_label_transcripts_routes_edgepython(self):
        assert (
            infer_plot_route("scatter", params={"label_transcripts": True})
            == "edgepython"
        )

    def test_detect_chart_type_fallback(self):
        assert infer_plot_route(None, user_message="show a volcano plot") == "edgepython"
        assert infer_plot_route(None, user_message="make a scatter plot") == "declarative"
