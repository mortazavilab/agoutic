"""Tests for cortex.context_budget — ContextBudgetManager."""

import pytest

from cortex.context_budget import (
    BudgetAllocation,
    BudgetSlot,
    ContextBudgetManager,
    count_message_tokens,
    count_tokens,
)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

class TestCountTokens:
    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_simple_text(self):
        tokens = count_tokens("Hello, world!")
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_longer_text_more_tokens(self):
        short = count_tokens("hi")
        long = count_tokens("This is a substantially longer piece of text that has many tokens")
        assert long > short


class TestCountMessageTokens:
    def test_empty_list(self):
        assert count_message_tokens([]) == 2  # reply priming only

    def test_single_message(self):
        msgs = [{"role": "user", "content": "Hello"}]
        tokens = count_message_tokens(msgs)
        # 4 overhead + content tokens + 2 priming
        assert tokens >= 7

    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        single = count_message_tokens(msgs[:1])
        double = count_message_tokens(msgs)
        assert double > single


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

class TestAllocate:
    def test_everything_fits(self):
        mgr = ContextBudgetManager(context_window=32768)
        alloc = mgr.allocate(
            system_prompt="You are helpful.",
            user_message="What is 2+2?",
        )
        # When everything fits, allocated == measured
        assert alloc.system_prompt == count_tokens("You are helpful.")
        assert alloc.user_message == count_tokens("What is 2+2?")
        assert alloc.conversation_history == 2  # empty history priming
        assert alloc.total_available == 32768 - 4096

    def test_trimming_needed(self):
        """With a tiny window, trimming must kick in."""
        mgr = ContextBudgetManager(context_window=200, completion_reserve=50)
        big_history = [
            {"role": "user", "content": "x " * 500},
            {"role": "assistant", "content": "y " * 500},
        ]
        alloc = mgr.allocate(
            system_prompt="Be helpful.",
            conversation_history=big_history,
            user_message="Hello",
        )
        total_alloc = (
            alloc.system_prompt + alloc.skill_content + alloc.memory
            + alloc.tool_schemas + alloc.conversation_history + alloc.user_message
        )
        assert total_alloc <= mgr.available_tokens

    def test_hard_slots_preserved(self):
        """System prompt and user message (hard slots) keep their full size."""
        mgr = ContextBudgetManager(context_window=500, completion_reserve=50)
        sys_text = "Short system prompt."
        user_text = "Short question."
        big_history = [{"role": "user", "content": "word " * 2000}]
        alloc = mgr.allocate(
            system_prompt=sys_text,
            conversation_history=big_history,
            user_message=user_text,
        )
        assert alloc.system_prompt == count_tokens(sys_text)
        assert alloc.user_message == count_tokens(user_text)


# ---------------------------------------------------------------------------
# Trimming helpers
# ---------------------------------------------------------------------------

class TestTrimText:
    def test_short_text_unchanged(self):
        mgr = ContextBudgetManager()
        result = mgr.trim_text("hello", 100)
        assert result == "hello"

    def test_long_text_truncated(self):
        mgr = ContextBudgetManager()
        long_text = "word " * 5000
        result = mgr.trim_text(long_text, 50)
        assert count_tokens(result) <= 60  # allow indicator overhead
        assert "[...truncated" in result

    def test_empty_text(self):
        mgr = ContextBudgetManager()
        assert mgr.trim_text("", 100) == ""

    def test_zero_budget(self):
        mgr = ContextBudgetManager()
        assert mgr.trim_text("hello", 0) == ""


class TestTrimHistory:
    def test_empty_history(self):
        mgr = ContextBudgetManager()
        assert mgr.trim_history([], 1000) == []

    def test_all_fit(self):
        mgr = ContextBudgetManager()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = mgr.trim_history(msgs, 1000)
        assert len(result) == 2

    def test_oldest_trimmed_first(self):
        mgr = ContextBudgetManager()
        msgs = [
            {"role": "user", "content": "old " * 200},
            {"role": "assistant", "content": "old reply " * 200},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "recent reply"},
        ]
        result = mgr.trim_history(msgs, 30)
        # Should keep the most recent messages, dropping old ones
        assert len(result) < len(msgs)
        assert result[-1]["content"] == "recent reply"

    def test_zero_budget_returns_empty(self):
        mgr = ContextBudgetManager()
        msgs = [{"role": "user", "content": "hi"}]
        assert mgr.trim_history(msgs, 0) == []


# ---------------------------------------------------------------------------
# fit_messages (end-to-end)
# ---------------------------------------------------------------------------

class TestFitMessages:
    def test_returns_openai_format(self):
        mgr = ContextBudgetManager(context_window=32768)
        msgs = mgr.fit_messages(
            system_prompt="You are helpful.",
            user_message="What is 2+2?",
        )
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"

    def test_with_history(self):
        mgr = ContextBudgetManager(context_window=32768)
        history = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ]
        msgs = mgr.fit_messages(
            system_prompt="You are helpful.",
            conversation_history=history,
            user_message="follow-up",
        )
        assert len(msgs) == 4  # system + 2 history + user

    def test_tiny_window_still_works(self):
        """Even with aggressive trimming, should return system + user minimum."""
        mgr = ContextBudgetManager(context_window=200, completion_reserve=50)
        history = [{"role": "user", "content": "x " * 1000}]
        msgs = mgr.fit_messages(
            system_prompt="Be brief.",
            conversation_history=history,
            user_message="Yes?",
        )
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        total = count_message_tokens(msgs)
        assert total <= 150 + 20  # available + small overhead tolerance
