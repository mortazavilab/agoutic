"""Tests for cortex/plan_classifier.py — request classification and plan-type detection."""

import pytest
from unittest.mock import patch, MagicMock

from cortex.plan_classifier import (
    classify_request,
    _detect_plan_type,
    _is_summarize_results_request,
)


# ---------------------------------------------------------------------------
# classify_request
# ---------------------------------------------------------------------------

class TestClassifyRequest:
    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_de_command_classified_as_multi_step(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("/de", "run_dogme_dna", MagicMock())
        assert result == "MULTI_STEP"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_reconcile_bams_classified_as_multi_step(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("reconcile the annotated BAMs", "run_dogme_dna", MagicMock())
        assert result == "MULTI_STEP"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_compare_samples_classified_as_multi_step(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("compare these samples", "run_dogme_dna", MagicMock())
        assert result == "MULTI_STEP"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_download_and_analyze_classified_as_multi_step(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("download data and then run analysis", "run_dogme_dna", MagicMock())
        assert result == "MULTI_STEP"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_informational_question(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("what can you do?", "run_dogme_dna", MagicMock())
        assert result == "INFORMATIONAL"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_show_command_is_informational(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("show me the results", "run_dogme_dna", MagicMock())
        assert result == "INFORMATIONAL"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_default_is_single_tool(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("hello", "run_dogme_dna", MagicMock())
        assert result == "SINGLE_TOOL"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_run_pipeline_classified_as_multi_step(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("run the full pipeline on my sample", "run_dogme_dna", MagicMock())
        assert result == "MULTI_STEP"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_differential_expression_classified_as_multi_step(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("run a differential expression analysis", "run_dogme_dna", MagicMock())
        assert result == "MULTI_STEP"

    @patch("cortex.plan_classifier.compiled_triggers")
    @patch("cortex.plan_chains.match_chain")
    def test_enrichment_analysis_classified_as_multi_step(self, mock_match, mock_triggers):
        mock_match.return_value = False
        mock_triggers.return_value = []
        result = classify_request("run a GO enrichment analysis", "run_dogme_dna", MagicMock())
        assert result == "MULTI_STEP"


# ---------------------------------------------------------------------------
# _detect_plan_type
# ---------------------------------------------------------------------------

class TestDetectPlanType:
    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_de_pipeline_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("run a differential expression analysis") == "run_de_pipeline"
        assert _detect_plan_type("/de") == "run_de_pipeline"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_reconcile_bams_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("reconcile the annotated BAMs") == "reconcile_bams"
        assert _detect_plan_type("merge annotated bams") == "reconcile_bams"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_compare_samples_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("compare these samples") == "compare_samples"
        assert _detect_plan_type("differences between the results") == "compare_samples"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_compare_workflows_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("compare these workflows") == "compare_workflows"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_download_analyze_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("download data and then run analysis") == "download_analyze"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_parse_plot_interpret_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("parse and then plot the results") == "parse_plot_interpret"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_remote_stage_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("stage the sample on slurm") == "remote_stage_workflow"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_run_workflow_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("run the full pipeline on my sample") == "run_workflow"
        assert _detect_plan_type("analyze my local sample") == "run_workflow"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_enrichment_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("run a GO enrichment analysis") == "run_enrichment"
        assert _detect_plan_type("KEGG enrichment pathway") == "run_enrichment"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_xgenepy_detected(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("run xgenepy analysis") == "run_xgenepy_analysis"
        assert _detect_plan_type("cis/trans analysis") == "run_xgenepy_analysis"

    @patch("cortex.plan_classifier._detect_plan_type_from_manifests")
    def test_no_match_returns_none(self, mock_manifest):
        mock_manifest.return_value = None
        assert _detect_plan_type("hello world") is None


# ---------------------------------------------------------------------------
# _is_summarize_results_request
# ---------------------------------------------------------------------------

class TestIsSummarizeResultsRequest:
    def test_summarize_with_work_dir(self):
        cs = MagicMock()
        cs.work_dir = "/proj/workflow1"
        assert _is_summarize_results_request("summarize the results", cs) is True

    def test_interpret_with_work_dir(self):
        cs = MagicMock()
        cs.work_dir = "/proj/workflow1"
        assert _is_summarize_results_request("interpret the output", cs) is True

    def test_explain_with_work_dir(self):
        cs = MagicMock()
        cs.work_dir = "/proj/workflow1"
        assert _is_summarize_results_request("explain the qc", cs) is True

    def test_no_work_dir_returns_false(self):
        cs = MagicMock()
        cs.work_dir = None
        assert _is_summarize_results_request("summarize the results", cs) is False

    def test_no_matching_keywords(self):
        cs = MagicMock()
        cs.work_dir = "/proj/workflow1"
        assert _is_summarize_results_request("what is this?", cs) is False
