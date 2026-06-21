"""Tests for cortex/analysis_helpers.py — pure analysis context/summary builders."""

import pytest

from cortex.analysis_helpers import (
    _normalized_workflow_key,
    _is_wf_pore_c_summary,
    _effective_workflow_family,
    _build_reconcile_bams_context,
    _build_haplotype_with_vcf_context,
    _build_wf_pore_c_context,
    _build_wf_pore_c_static_summary,
    _build_reconcile_bams_static_summary,
)


# ---------------------------------------------------------------------------
# _normalized_workflow_key / _is_wf_pore_c_summary
# ---------------------------------------------------------------------------

class TestWorkflowKeyHelpers:
    def test_normalized_key_lowercase(self):
        assert _normalized_workflow_key({"workflow_key": "DOGME"}) == "dogme"

    def test_default_is_dogme(self):
        assert _normalized_workflow_key({}) == "dogme"
        assert _normalized_workflow_key(None) == "dogme"

    def test_wf_pore_c_detection_enabled(self):
        data = {"workflow_key": "wf_pore_c"}
        assert _is_wf_pore_c_summary(data, wf_pore_c_enabled=True) is True

    def test_wf_pore_c_detection_disabled(self):
        data = {"workflow_key": "wf_pore_c"}
        assert _is_wf_pore_c_summary(data, wf_pore_c_enabled=False) is False

    def test_non_wf_pore_c_returns_false(self):
        data = {"workflow_key": "dogme"}
        assert _is_wf_pore_c_summary(data, wf_pore_c_enabled=True) is False


# ---------------------------------------------------------------------------
# _effective_workflow_family
# ---------------------------------------------------------------------------

class TestEffectiveWorkflowFamily:
    def test_wf_pore_c_enabled_returns_wf_pore_c(self):
        data = {"workflow_key": "wf_pore_c"}
        assert _effective_workflow_family(data, wf_pore_c_enabled=True) == "wf_pore_c"

    def test_wf_pore_c_disabled_falls_back_to_dogme(self):
        data = {"workflow_key": "wf_pore_c"}
        assert _effective_workflow_family(data, wf_pore_c_enabled=False) == "dogme"

    def test_non_wf_pore_c_returns_key(self):
        data = {"workflow_key": "dogme"}
        assert _effective_workflow_family(data) == "dogme"


# ---------------------------------------------------------------------------
# _build_reconcile_bams_context
# ---------------------------------------------------------------------------

class TestBuildReconcileBamsContext:
    def test_basic_structure(self):
        summary_data = {
            "workflow_summary": {
                "metadata": {
                    "input_bam_count": 3,
                    "reference": "mm39",
                },
                "artifacts": {},
            },
            "parsed_reports": {},
            "warnings": [],
        }
        result = _build_reconcile_bams_context(summary_data)
        assert "## Workflow Summary" in result
        assert "Workflow key: reconcile_bams" in result
        assert "Input BAM count: 3" in result

    def test_includes_transcript_category_counts(self):
        summary_data = {
            "workflow_summary": {
                "metadata": {
                    "input_bam_count": 1,
                    "transcript_category_counts": {"known": 100, "novel": 50},
                },
                "artifacts": {},
            },
            "parsed_reports": {},
            "warnings": [],
        }
        result = _build_reconcile_bams_context(summary_data)
        assert "Isoform Summary" in result
        assert "known: 100" in result

    def test_includes_artifact_presence(self):
        summary_data = {
            "workflow_summary": {
                "metadata": {"input_bam_count": 1},
                "artifacts": {
                    "reconciled_bam": {"present": True, "matches": ["/path/bam"]},
                    "summary_report": {"present": False},
                },
            },
            "parsed_reports": {},
            "warnings": ["warning1"],
        }
        result = _build_reconcile_bams_context(summary_data)
        assert "Artifact Presence" in result
        assert "Reconciled BAM outputs: present" in result
        assert "Reconciled summary report: missing" in result
        assert "## Warnings" in result


# ---------------------------------------------------------------------------
# _build_haplotype_with_vcf_context
# ---------------------------------------------------------------------------

class TestBuildHaplotypeWithVcfContext:
    def test_basic_structure(self):
        summary_data = {
            "workflow_summary": {
                "metadata": {
                    "haplotyped_bam_count": 2,
                    "assignment_labels": ["sample1", "sample2"],
                },
                "artifacts": {},
            },
            "warnings": [],
        }
        result = _build_haplotype_with_vcf_context(summary_data)
        assert "Workflow key: haplotype_with_vcf" in result
        assert "Haplotyped BAM count: 2" in result
        assert "sample1" in result

    def test_includes_artifacts(self):
        summary_data = {
            "workflow_summary": {
                "metadata": {},
                "artifacts": {
                    "haplotyped_bam": {"present": True},
                    "genome_summary": {"present": True, "matches": ["/path.tsv"]},
                },
            },
            "warnings": [],
        }
        result = _build_haplotype_with_vcf_context(summary_data)
        assert "Artifact Presence" in result
        assert "Haplotyped BAM outputs: present" in result


# ---------------------------------------------------------------------------
# _build_wf_pore_c_context
# ---------------------------------------------------------------------------

class TestBuildWfPoreCContext:
    def test_basic_structure(self):
        summary_data = {
            "workflow_summary": {
                "sample_alias": "test_sample",
                "metadata": {
                    "workflow_version": "v1.0",
                    "reference_fasta": "/ref.fa",
                    "cutter": "NlaIII",
                },
                "artifacts": {"report_html": {"present": True, "requested": True}},
            },
            "pairs_stats": {"total_pairs": 1000000, "cis_trans_ratio": 2.5},
            "warnings": [],
        }
        result = _build_wf_pore_c_context(summary_data)
        assert "Workflow key: wf_pore_c" in result
        assert "Sample alias: test_sample" in result
        assert "Revision: v1.0" in result

    def test_includes_pairs_stats(self):
        summary_data = {
            "workflow_summary": {
                "sample_alias": "test",
                "metadata": {"workflow_version": "v1"},
                "artifacts": {},
                "pairs_stats": {"total_pairs": 500, "duplicate_rate": 0.15},
            },
            "warnings": [],
        }
        result = _build_wf_pore_c_context(summary_data)
        assert "Total pairs: 500" in result
        assert "Duplicate rate:" in result


# ---------------------------------------------------------------------------
# Static summary builders
# ---------------------------------------------------------------------------

class TestStaticSummaries:
    def test_wf_pore_c_static_summary(self):
        summary_data = {
            "status": "COMPLETED",
            "workflow_summary": {
                "sample_alias": "test",
                "metadata": {"workflow_version": "v1"},
                "artifacts": {"report_html": {"present": True, "requested": True}},
            },
            "pairs_stats": {},
            "warnings": [],
        }
        result = _build_wf_pore_c_static_summary("test", summary_data, "/proj/workflow1")
        assert "Contact Map Summary: test" in result
        assert "Workflow key:** wf_pore_c" in result
        assert "You can ask me to dive deeper" in result

    def test_reconcile_bams_static_summary(self):
        summary_data = {
            "status": "COMPLETED",
            "workflow_summary": {
                "metadata": {
                    "input_bam_count": 2,
                    "reference": "hg38",
                    "transcript_category_counts": {"known": 50},
                },
                "artifacts": {},
            },
            "warnings": [],
        }
        result = _build_reconcile_bams_static_summary("test", summary_data, "/proj/workflow1")
        assert "Reconcile Summary: test" in result
        assert "**Input BAM count:** 2" in result
        assert "You can ask me to dive deeper" in result
