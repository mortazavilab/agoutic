"""Tests for cortex/tag_parser.py — LLM response tag parsing and correction."""

import pytest
from unittest.mock import patch, MagicMock

from cortex.tag_parser import (
    ParsedLLMResponse,
    apply_response_corrections,
    parse_data_tags,
    parse_approval_tag,
    parse_plot_tags,
    parse_pending_action_tags,
    override_hallucinated_df_refs,
    _convert_tool_call_to_data_call,
    _convert_mistral_tool_call,
    _convert_mistral_inline,
    _convert_mistral_inline_plot,
    _detect_x_column,
)


# ---------------------------------------------------------------------------
# ParsedLLMResponse dataclass
# ---------------------------------------------------------------------------

class TestParsedLLMResponse:
    def test_default_values(self):
        resp = ParsedLLMResponse(corrected_response="hello", clean_markdown="hello")
        assert resp.data_call_matches == []
        assert resp.legacy_encode_matches == []
        assert resp.plot_specs == []
        assert resp.needs_approval is False
        assert resp.fallback_fixes_applied == 0

    def test_custom_values(self):
        resp = ParsedLLMResponse(
            corrected_response="corrected",
            clean_markdown="clean",
            needs_approval=True,
            fallback_fixes_applied=3,
        )
        assert resp.needs_approval is True
        assert resp.fallback_fixes_applied == 3


# ---------------------------------------------------------------------------
# apply_response_corrections
# ---------------------------------------------------------------------------

class TestApplyResponseCorrections:
    @patch("cortex.tag_parser.get_all_fallback_patterns")
    def test_no_corrections_needed(self, mock_fallback):
        mock_fallback.return_value = {}
        corrected, count = apply_response_corrections("no tags here")
        assert corrected == "no tags here"
        assert count == 0

    @patch("cortex.tag_parser.get_all_fallback_patterns")
    def test_rest_tool_call_converted(self, mock_fallback):
        mock_fallback.return_value = {}
        response = "[[TOOL_CALL: GET /analysis/summary?work_dir=/data]]"
        corrected, count = apply_response_corrections(response)
        assert "DATA_CALL" in corrected
        assert count >= 1

    @patch("cortex.tag_parser.get_all_fallback_patterns")
    def test_mistral_tool_call_converted(self, mock_fallback):
        mock_fallback.return_value = {}
        response = '[TOOL_CALLS] DATA_CALL [ARGS] {"service": "analyzer", "tool": "list_files"}'
        corrected, count = apply_response_corrections(response)
        assert "DATA_CALL" in corrected
        assert count >= 1

    @patch("cortex.tag_parser.get_all_fallback_patterns")
    def test_mistral_inline_converted(self, mock_fallback):
        mock_fallback.return_value = {}
        response = "[TOOL_CALLS] DATA_CALL: source=analyzer, tool=list_files"
        corrected, count = apply_response_corrections(response)
        assert "[[DATA_CALL:" in corrected
        assert count >= 1

    @patch("cortex.tag_parser.get_all_fallback_patterns")
    def test_mistral_inline_plot_converted(self, mock_fallback):
        mock_fallback.return_value = {}
        response = "[TOOL_CALLS] PLOT: type=scatter, df_id=1"
        corrected, count = apply_response_corrections(response)
        assert "[[PLOT:" in corrected
        assert count >= 1


# ---------------------------------------------------------------------------
# parse_data_tags
# ---------------------------------------------------------------------------

class TestParseDataTags:
    def test_data_call_tag(self):
        response = "Some text [[DATA_CALL: service=analyzer, tool=list_files, work_dir=/data]] more text"
        data_calls, encode_calls, analysis_calls = parse_data_tags(response)
        assert len(data_calls) == 1

    def test_legacy_encode_call_tag(self):
        response = "[[ENCODE_CALL: search_by_biosample, search_term=K562]]"
        data_calls, encode_calls, analysis_calls = parse_data_tags(response)
        assert len(encode_calls) == 1
        assert len(data_calls) == 0

    def test_legacy_analysis_call_tag(self):
        response = "[[ANALYSIS_CALL: get_summary, run_uuid=abc123]]"
        data_calls, encode_calls, analysis_calls = parse_data_tags(response)
        assert len(analysis_calls) == 1

    def test_multiple_tags(self):
        response = (
            "[[DATA_CALL: service=analyzer, tool=list_files]] "
            "[[ENCODE_CALL: search, term=test]]"
        )
        data_calls, encode_calls, analysis_calls = parse_data_tags(response)
        assert len(data_calls) == 1
        assert len(encode_calls) == 1

    def test_no_tags(self):
        data_calls, encode_calls, analysis_calls = parse_data_tags("just plain text")
        assert len(data_calls) == 0
        assert len(encode_calls) == 0
        assert len(analysis_calls) == 0


# ---------------------------------------------------------------------------
# parse_approval_tag
# ---------------------------------------------------------------------------

class TestParseApprovalTag:
    def test_approval_present_for_job_skill(self):
        needs, cleaned = parse_approval_tag("[[APPROVAL_NEEDED]] some text", "run_dogme_dna")
        assert needs is True

    def test_approval_suppressed_for_non_job_skill(self):
        needs, cleaned = parse_approval_tag("[[APPROVAL_NEEDED]] some text", "welcome")
        assert needs is False
        assert "[[APPROVAL_NEEDED]]" not in cleaned

    def test_no_approval_tag(self):
        needs, cleaned = parse_approval_tag("no approval needed", "run_dogme_dna")
        assert needs is False


# ---------------------------------------------------------------------------
# parse_plot_tags
# ---------------------------------------------------------------------------

class TestParsePlotTags:
    @patch("cortex.tag_parser._parse_tag_params", return_value={"type": "scatter", "df_id": 1})
    def test_single_plot_tag(self, mock_params):
        response = "[[PLOT: type=scatter, df_id=1]]"
        specs = parse_plot_tags(response)
        assert len(specs) == 1
        assert specs[0]["type"] == "scatter"

    @patch("cortex.tag_parser._parse_tag_params", return_value={"type": "bar"})
    def test_multiple_plot_tags(self, mock_params):
        response = "[[PLOT: type=bar]] [[PLOT: type=scatter]]"
        specs = parse_plot_tags(response)
        assert len(specs) == 2

    @patch("cortex.tag_parser._parse_tag_params", return_value={})
    def test_no_plot_tags(self, mock_params):
        specs = parse_plot_tags("no plot tags here")
        assert specs == []


# ---------------------------------------------------------------------------
# parse_pending_action_tags
# ---------------------------------------------------------------------------

class TestParsePendingActionTags:
    @patch("cortex.tag_parser.parse_pending_action_tag", return_value=[{"action": "test"}])
    def test_returns_pending_actions(self, mock_parse):
        result = parse_pending_action_tags("some response")
        assert len(result) == 1

    @patch("cortex.tag_parser.parse_pending_action_tag", return_value=[])
    def test_empty_pending_actions(self, mock_parse):
        result = parse_pending_action_tags("no actions")
        assert result == []


# ---------------------------------------------------------------------------
# override_hallucinated_df_refs
# ---------------------------------------------------------------------------

class TestOverrideHallucinatedDfRefs:
    def test_overrides_stale_df_id(self):
        specs = [{"df_id": 1, "df": "DF1"}]
        override_hallucinated_df_refs(specs, user_message="plot this", latest_dataframe="DF5")
        assert specs[0]["df_id"] == 5
        assert specs[0]["df"] == "DF5"

    def test_no_override_when_user_explicit_df(self):
        specs = [{"df_id": 1, "df": "DF1"}]
        override_hallucinated_df_refs(specs, user_message="plot DF3", latest_dataframe="DF5")
        assert specs[0]["df_id"] == 1

    def test_no_override_when_latest_none(self):
        specs = [{"df_id": 1, "df": "DF1"}]
        override_hallucinated_df_refs(specs, user_message="plot this", latest_dataframe=None)
        assert specs[0]["df_id"] == 1

    def test_empty_specs_noop(self):
        override_hallucinated_df_refs([], user_message="plot this", latest_dataframe="DF5")


# ---------------------------------------------------------------------------
# _convert_tool_call_to_data_call
# ---------------------------------------------------------------------------

class TestConvertToolCallToDataCall:
    def test_summary_url(self):
        m = MagicMock()
        m.group.return_value = "/analysis/summary?work_dir=/data"
        m.group.side_effect = lambda i: {1: "work_dir=/data", 0: "[[TOOL_CALL: GET /analysis/summary?work_dir=/data]]"}[i]
        # Simpler: just test the function directly with a real match
        import re
        pattern = re.compile(r'\[\[TOOL_CALL:\s*(?:GET\s+)?/analysis/[^?]*\?([^\]]+)\]\]')
        match = pattern.search('[[TOOL_CALL: GET /analysis/summary?work_dir=/data]]')
        result = _convert_tool_call_to_data_call(match)
        assert "tool=get_analysis_summary" in result

    def test_file_url(self):
        import re
        pattern = re.compile(r'\[\[TOOL_CALL:\s*(?:GET\s+)?/analysis/[^?]*\?([^\]]+)\]\]')
        match = pattern.search('[[TOOL_CALL: GET /analysis/file?file=test.csv]]')
        result = _convert_tool_call_to_data_call(match)
        assert "tool=list_job_files" in result


# ---------------------------------------------------------------------------
# _convert_mistral_tool_call
# ---------------------------------------------------------------------------

class TestConvertMistralToolCall:
    def test_encode_service(self):
        import re
        pattern = re.compile(r'\[TOOL_CALLS\]\s*DATA_CALL\s*\[ARGS\]\s*(\{[^}]+\})')
        match = pattern.search('[TOOL_CALLS] DATA_CALL [ARGS] {"service": "encode", "tool": "search"}')
        result = _convert_mistral_tool_call(match)
        assert "consortium=encode" in result

    def test_analyzer_service(self):
        import re
        pattern = re.compile(r'\[TOOL_CALLS\]\s*DATA_CALL\s*\[ARGS\]\s*(\{[^}]+\})')
        match = pattern.search('[TOOL_CALLS] DATA_CALL [ARGS] {"service": "analyzer", "tool": "list"}')
        result = _convert_mistral_tool_call(match)
        assert "service=analyzer" in result


# ---------------------------------------------------------------------------
# _convert_mistral_inline / _convert_mistral_inline_plot
# ---------------------------------------------------------------------------

class TestConvertMistralInline:
    def test_inline_data_call(self):
        import re
        pattern = re.compile(r'\[TOOL_CALLS\]\s*DATA_CALL:\s*(.+?)(?:\n|$)')
        match = pattern.search('[TOOL_CALLS] DATA_CALL: source=analyzer, tool=list')
        result = _convert_mistral_inline(match)
        assert "[[DATA_CALL:" in result

    def test_inline_plot(self):
        import re
        pattern = re.compile(r'\[TOOL_CALLS\]\s*PLOT:\s*(.+?)(?:\n|$)')
        match = pattern.search('[TOOL_CALLS] PLOT: type=scatter, df_id=1')
        result = _convert_mistral_inline_plot(match)
        assert "[[PLOT:" in result


# ---------------------------------------------------------------------------
# _detect_x_column
# ---------------------------------------------------------------------------

class TestDetectXColumn:
    def test_by_keyword(self):
        assert _detect_x_column("plot by gene") == "gene"

    def test_of_keyword(self):
        assert _detect_x_column("values of sample") == "sample"

    def test_df_number_ignored(self):
        assert _detect_x_column("plot by DF1") is None
