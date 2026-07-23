"""Tests for the early_overrides stage (priority 360)."""

import pytest
from unittest.mock import patch

from cortex.chat_context import ChatContext
from cortex.chat_stages.early_overrides import (
    EarlyOverrideDetectionStage,
    _is_sync_intent,
    _is_browsing_intent,
    _is_multi_encode_file_request,
    _is_user_data_intent,
)
from cortex.chat_stages.overrides import OverrideDetectionStage


class TestSyncIntentDetection:
    """Verify sync intent regex matches the same patterns as data_call_generator."""

    @pytest.mark.parametrize("msg", [
        "sync results locally",
        "sync workflow6 locally",
        "resync results back to local",
        "copy results back locally",
        "retry results download",
        "sync job results locally",
        "resync workflow3",
        "sync run locally",
    ])
    def test_sync_intent_matches(self, msg):
        assert _is_sync_intent(msg.lower()), f"Expected sync intent for: {msg}"

    @pytest.mark.parametrize("msg", [
        "list files",
        "show my data",
        "run workflow",
        "what is the status of my job",
        "hello",
    ])
    def test_sync_intent_no_match(self, msg):
        assert not _is_sync_intent(msg.lower()), f"Unexpected sync intent for: {msg}"


class TestBrowsingIntentDetection:
    @pytest.mark.parametrize("msg", [
        "list workflows",
        "show workflows",
        "list project files",
        "show files",
    ])
    def test_browsing_intent_matches(self, msg):
        assert _is_browsing_intent(msg.lower()), f"Expected browsing intent for: {msg}"


class TestUserDataIntentDetection:
    @pytest.mark.parametrize("msg", [
        "list my data",
        "show my data files",
        "what data files do I have",
        "show my files",
    ])
    def test_user_data_intent_matches(self, msg):
        assert _is_user_data_intent(msg.lower()), f"Expected user data intent for: {msg}"


class TestMultiEncodeFileIntentDetection:
    def test_matches_explicit_multi_experiment_fastq_request(self):
        assert _is_multi_encode_file_request(
            "Download all released FASTQ files from ENCSR432YKA ENCSR476STT ENCSR448TJV"
        )

    @pytest.mark.parametrize("msg", [
        "Download FASTQ files from ENCSR432YKA",
        "Compare ENCSR432YKA ENCSR476STT metadata",
    ])
    def test_does_not_match_unrelated_or_single_experiment_requests(self, msg):
        assert not _is_multi_encode_file_request(msg)


class TestEarlyOverrideStage:
    @pytest.fixture
    def stage(self):
        return EarlyOverrideDetectionStage()

    def _make_ctx(self, message: str) -> ChatContext:
        ctx = ChatContext.__new__(ChatContext)
        ctx.message = message
        ctx.user_msg_lower = message.lower()
        ctx.skip_llm_first_pass = False
        ctx.skip_tag_parsing = False
        ctx.is_multi_encode_file_override = False
        ctx.request_id = "test-req-id"
        return ctx

    @pytest.mark.asyncio
    @patch("cortex.chat_stages.early_overrides._emit_progress")
    async def test_sync_message_skips_llm(self, mock_progress, stage):
        ctx = self._make_ctx("sync workflow6 locally")
        await stage.run(ctx)
        assert ctx.skip_llm_first_pass is True
        assert ctx.skip_tag_parsing is True
        mock_progress.assert_called_once_with("test-req-id", "tools", "Starting result sync...")

    @pytest.mark.asyncio
    @patch("cortex.chat_stages.early_overrides._emit_progress")
    async def test_browsing_message_skips_llm(self, mock_progress, stage):
        ctx = self._make_ctx("list workflows")
        await stage.run(ctx)
        assert ctx.skip_llm_first_pass is True
        assert ctx.skip_tag_parsing is True
        mock_progress.assert_called_once_with("test-req-id", "tools", "Browsing project files...")

    @pytest.mark.asyncio
    @patch("cortex.chat_stages.early_overrides._emit_progress")
    async def test_user_data_message_skips_llm(self, mock_progress, stage):
        ctx = self._make_ctx("show my data files")
        await stage.run(ctx)
        assert ctx.skip_llm_first_pass is True
        assert ctx.skip_tag_parsing is True
        mock_progress.assert_called_once_with("test-req-id", "tools", "Listing your data files...")

    @pytest.mark.asyncio
    @patch("cortex.chat_stages.early_overrides._emit_progress")
    async def test_multi_encode_file_request_skips_llm(self, mock_progress, stage):
        ctx = self._make_ctx(
            "Download all released FASTQ files from ENCSR432YKA ENCSR476STT ENCSR448TJV"
        )
        await stage.run(ctx)
        assert ctx.skip_llm_first_pass is True
        assert ctx.skip_tag_parsing is True
        assert ctx.is_multi_encode_file_override is True
        mock_progress.assert_called_once_with(
            "test-req-id", "tools", "Finding ENCODE files for all requested experiments..."
        )

    @pytest.mark.asyncio
    @patch("cortex.chat_stages.early_overrides._emit_progress")
    @patch("cortex.chat_stages.overrides._auto_generate_data_calls")
    async def test_multi_encode_file_request_generates_every_experiment_call(
        self, mock_auto_generate, mock_progress, stage,
    ):
        message = "Download FASTQ files from ENCSR432YKA ENCSR476STT ENCSR448TJV"
        ctx = ChatContext(message=message, user_msg_lower=message.lower(), active_skill="ENCODE_Search")
        ctx.request_id = "test-req-id"
        mock_auto_generate.return_value = [
            {
                "source_type": "consortium",
                "source_key": "encode",
                "tool": "get_files_by_type",
                "params": {"accession": accession},
            }
            for accession in ("ENCSR432YKA", "ENCSR476STT", "ENCSR448TJV")
        ]

        await stage.run(ctx)
        await OverrideDetectionStage().run(ctx)

        assert ctx.has_any_tags is False
        assert {call["params"]["accession"] for call in ctx.auto_calls} == {
            "ENCSR432YKA", "ENCSR476STT", "ENCSR448TJV",
        }

    @pytest.mark.asyncio
    @patch("cortex.chat_stages.early_overrides._emit_progress")
    async def test_normal_message_no_skip(self, mock_progress, stage):
        ctx = self._make_ctx("analyze the samples in my project")
        await stage.run(ctx)
        assert ctx.skip_llm_first_pass is False
        assert ctx.skip_tag_parsing is False
        mock_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_run_always_true(self, stage):
        ctx = self._make_ctx("anything")
        assert await stage.should_run(ctx) is True
