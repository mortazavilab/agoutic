"""Tests for cortex/chat_context.py — ChatContext dataclass."""

import pytest

from cortex.chat_context import ChatContext


class TestChatContext:
    def test_default_values(self):
        ctx = ChatContext()
        assert ctx.project_id == ""
        assert ctx.message == ""
        assert ctx.skill == "welcome"
        assert ctx.model == "default"
        assert ctx.active_skill == ""
        assert ctx.needs_approval is False
        assert ctx.plot_specs == []
        assert ctx.data_call_matches == []
        assert ctx.all_results == {}
        assert ctx.response is None

    def test_custom_initialization(self):
        ctx = ChatContext(project_id="proj-1", message="hello", skill="run_dogme_dna")
        assert ctx.project_id == "proj-1"
        assert ctx.message == "hello"
        assert ctx.skill == "run_dogme_dna"

    def test_short_circuit_sets_response(self):
        ctx = ChatContext()
        assert ctx.response is None
        ctx.short_circuit({"content": "done"})
        assert ctx.response == {"content": "done"}

    def test_short_circuit_prevents_further_processing(self):
        """Setting response should signal the pipeline to stop."""
        ctx = ChatContext()
        ctx.short_circuit({"markdown": "result"})
        # The response field is set — the pipeline runner checks this
        assert ctx.response is not None
        assert ctx.response["markdown"] == "result"

    def test_history_blocks_is_list(self):
        ctx = ChatContext()
        assert isinstance(ctx.history_blocks, list)
        ctx.history_blocks.append({"type": "block"})
        assert len(ctx.history_blocks) == 1

    def test_conversation_history_is_list(self):
        ctx = ChatContext()
        assert isinstance(ctx.conversation_history, list)

    def test_token_usage_defaults(self):
        ctx = ChatContext()
        assert ctx.think_usage["prompt_tokens"] == 0
        assert ctx.think_usage["completion_tokens"] == 0
        assert ctx.analyze_usage["total_tokens"] == 0

    def test_injected_dfs_is_dict(self):
        ctx = ChatContext()
        assert isinstance(ctx.injected_dfs, dict)
        ctx.injected_dfs["DF1"] = {"data": [1, 2, 3]}
        assert ctx.injected_dfs["DF1"]["data"] == [1, 2, 3]

    def test_embedded_dataframes_is_dict(self):
        ctx = ChatContext()
        assert isinstance(ctx.embedded_dataframes, dict)

    def test_override_flags_default_false(self):
        ctx = ChatContext()
        assert ctx.is_user_data_override is False
        assert ctx.is_browsing_override is False
        assert ctx.is_remote_browsing_override is False
        assert ctx.is_sync_override is False

    def test_skip_flags_default_false(self):
        ctx = ChatContext()
        assert ctx.skip_llm_first_pass is False
        assert ctx.skip_tag_parsing is False
        assert ctx.skip_second_pass is False
