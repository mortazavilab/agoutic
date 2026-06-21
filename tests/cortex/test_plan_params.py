"""Tests for cortex/plan_params.py — parameter extraction helpers."""

import pytest
from unittest.mock import patch

import cortex.plan_params as plan_params
from cortex.plan_params import (
    _resolve_mouse_founder_alias,
    _extract_haplotype_founder_samples,
    _extract_mentioned_reference_genomes,
    _extract_overlap_label_override,
    _extract_overlap_label_overrides,
    _extract_overlap_plot_title,
    _clean_haplotype_founder_token,
    _collapse_mouse_founder_token,
)


# ---------------------------------------------------------------------------
# _resolve_mouse_founder_alias
# ---------------------------------------------------------------------------

class TestResolveMouseFounderAlias:
    def test_ref_alias(self):
        assert _resolve_mouse_founder_alias("ref") == "C57BL_6J"
        assert _resolve_mouse_founder_alias("b6") == "C57BL_6J"
        assert _resolve_mouse_founder_alias("c57bl6j") == "C57BL_6J"

    def test_aj_alias(self):
        assert _resolve_mouse_founder_alias("aj") == "A_J"
        assert _resolve_mouse_founder_alias("a") == "A_J"

    def test_129s1_alias(self):
        assert _resolve_mouse_founder_alias("129s1") == "129S1_SvImJ"

    def test_nod_alias(self):
        assert _resolve_mouse_founder_alias("nod") == "NOD_ShiLtJ"

    def test_cast_alias(self):
        assert _resolve_mouse_founder_alias("cast") == "CAST_EiJ"

    def test_pwk_alias(self):
        assert _resolve_mouse_founder_alias("pwk") == "PWK_PhJ"

    def test_unknown_returns_none(self):
        assert _resolve_mouse_founder_alias("unknown") is None

    def test_empty_string_returns_none(self):
        assert _resolve_mouse_founder_alias("") is None


# ---------------------------------------------------------------------------
# _extract_haplotype_founder_samples
# ---------------------------------------------------------------------------

class TestExtractHaplotypeFounderSamples:
    def test_vcf_sample_flag(self):
        message = "--vcf-sample C57BL_6J,A_J workflow1"
        result = _extract_haplotype_founder_samples(message)
        assert "C57BL_6J" in result
        assert "A_J" in result

    def test_founder_pair(self):
        message = "founders C57BL_6J and A_J workflow1"
        result = _extract_haplotype_founder_samples(message)
        assert "C57BL_6J" in result

    def test_sample_pair(self):
        message = "sample C57BL_6J vs A_J workflow1"
        result = _extract_haplotype_founder_samples(message)
        assert "C57BL_6J" in result

    def test_empty_message(self):
        result = _extract_haplotype_founder_samples("")
        assert result == []


# ---------------------------------------------------------------------------
# _extract_mentioned_reference_genomes
# ---------------------------------------------------------------------------

class TestExtractMentionedReferenceGenomes:
    def test_mm39_alias_resolved(self):
        with patch.object(plan_params, "_GENOME_ALIAS_RE") as mock_re:
            mock_re.findall.return_value = ["mm39"]
            with patch.object(plan_params, "GENOME_ALIASES", {"mm39": "GRCm38", "hg38": "GRCh38"}):
                result = _extract_mentioned_reference_genomes("use mm39 reference")
                assert "GRCm38" in result

    def test_hg38_alias_resolved(self):
        with patch.object(plan_params, "_GENOME_ALIAS_RE") as mock_re:
            mock_re.findall.return_value = ["hg38"]
            with patch.object(plan_params, "GENOME_ALIASES", {"mm39": "GRCm38", "hg38": "GRCh38"}):
                result = _extract_mentioned_reference_genomes("use hg38 reference")
                assert "GRCh38" in result

    def test_no_match_returns_empty(self):
        with patch.object(plan_params, "_GENOME_ALIAS_RE") as mock_re:
            mock_re.findall.return_value = []
            result = _extract_mentioned_reference_genomes("no genome mentioned")
            assert result == []

    def test_duplicate_genomes_not_duplicated(self):
        with patch.object(plan_params, "_GENOME_ALIAS_RE") as mock_re:
            mock_re.findall.return_value = ["mm39", "mm39"]
            with patch.object(plan_params, "GENOME_ALIASES", {"mm39": "GRCm38"}):
                result = _extract_mentioned_reference_genomes("use mm39 and mm39 again")
                assert result.count("GRCm38") == 1


# ---------------------------------------------------------------------------
# _extract_overlap_label_override
# ---------------------------------------------------------------------------

class TestExtractOverlapLabelOverride:
    def test_sample_a_label_assignment(self):
        message = "sample a as MyTreatment"
        result = _extract_overlap_label_override(message, "a")
        assert result == "MyTreatment"

    def test_sample_b_label_assignment(self):
        message = "rename sample b to ControlGroup"
        result = _extract_overlap_label_override(message, "b")
        assert result == "ControlGroup"

    def test_no_label_returns_none(self):
        message = "just compare them"
        result = _extract_overlap_label_override(message, "a")
        assert result is None


# ---------------------------------------------------------------------------
# _extract_overlap_label_overrides
# ---------------------------------------------------------------------------

class TestExtractOverlapLabelOverrides:
    def test_both_samples_labeled(self):
        message = "sample a as Treatment and sample b as Control"
        result = _extract_overlap_label_overrides(message)
        assert result["sample_a_label"] == "Treatment"
        assert result["sample_b_label"] == "Control"

    def test_only_one_sample_labeled(self):
        message = "sample a as Treatment"
        result = _extract_overlap_label_overrides(message)
        assert result["sample_a_label"] == "Treatment"
        assert "sample_b_label" not in result

    def test_no_labels_returns_empty(self):
        result = _extract_overlap_label_overrides("")
        assert result == {}


# ---------------------------------------------------------------------------
# _extract_overlap_plot_title
# ---------------------------------------------------------------------------

class TestExtractOverlapPlotTitle:
    def test_title_it_as(self):
        message = "title it as My Comparison"
        result = _extract_overlap_plot_title(message)
        assert result == "My Comparison"

    def test_with_title(self):
        message = "with title=Venn Diagram"
        result = _extract_overlap_plot_title(message)
        assert result == "Venn Diagram"

    def test_plot_title_is(self):
        message = "plot title is Overlap Analysis"
        result = _extract_overlap_plot_title(message)
        assert result == "Overlap Analysis"

    def test_no_title_returns_none(self):
        result = _extract_overlap_plot_title("no title specified")
        assert result is None


# ---------------------------------------------------------------------------
# _clean_haplotype_founder_token / _collapse_mouse_founder_token
# ---------------------------------------------------------------------------

class TestTokenHelpers:
    def test_clean_removes_quotes_and_punctuation(self):
        assert _clean_haplotype_founder_token('"C57BL_6J"') == "C57BL_6J"
        assert _clean_haplotype_founder_token("A_J,") == "A_J"

    def test_collapse_removes_non_alnum(self):
        result = _collapse_mouse_founder_token("C57 BL 6J")
        assert result == "c57bl6j"

    def test_empty_inputs(self):
        assert _clean_haplotype_founder_token("") == ""
        assert _collapse_mouse_founder_token("") == ""
