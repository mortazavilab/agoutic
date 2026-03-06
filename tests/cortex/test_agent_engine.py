"""
Tests for cortex/agent_engine.py — AgentEngine class.

Tests _usage_to_dict (pure), _load_skill_text (file-based), and
construct_system_prompt (integration with skills files).
Think/analyze_results are tested with a mocked OpenAI client.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from cortex.agent_engine import _usage_to_dict, AgentEngine


# ---------------------------------------------------------------------------
# _usage_to_dict — pure function
# ---------------------------------------------------------------------------
class TestUsageToDict:
    def test_none_returns_zeros(self):
        assert _usage_to_dict(None) == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def test_extracts_fields(self):
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        assert _usage_to_dict(usage) == {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

    def test_handles_none_fields(self):
        usage = MagicMock()
        usage.prompt_tokens = None
        usage.completion_tokens = None
        usage.total_tokens = None
        result = _usage_to_dict(usage)
        assert result["prompt_tokens"] == 0
        assert result["completion_tokens"] == 0
        assert result["total_tokens"] == 0


# ---------------------------------------------------------------------------
# AgentEngine.__init__
# ---------------------------------------------------------------------------
class TestAgentEngineInit:
    def test_default_model_key(self):
        engine = AgentEngine("default")
        # "default" is in LLM_MODELS — should resolve to a model name
        assert engine.model_name is not None
        assert "default" in engine.display_name

    def test_raw_model_name(self):
        engine = AgentEngine("my-custom-model")
        # Not in LLM_MODELS — used directly
        assert engine.model_name == "my-custom-model"
        assert engine.display_name == "my-custom-model"


# ---------------------------------------------------------------------------
# AgentEngine._load_skill_text
# ---------------------------------------------------------------------------
class TestLoadSkillText:
    def test_loads_welcome_skill(self):
        """The 'welcome' skill should always exist."""
        engine = AgentEngine("default")
        text = engine._load_skill_text("welcome")
        assert len(text) > 0

    def test_invalid_skill_raises(self):
        engine = AgentEngine("default")
        with pytest.raises(ValueError, match="not found in Registry"):
            engine._load_skill_text("nonexistent_skill_xyz")

    def test_loads_encode_search(self):
        """ENCODE_Search skill should load."""
        engine = AgentEngine("default")
        text = engine._load_skill_text("ENCODE_Search")
        assert "ENCODE" in text or "encode" in text


# ---------------------------------------------------------------------------
# AgentEngine.construct_system_prompt
# ---------------------------------------------------------------------------
class TestConstructSystemPrompt:
    def test_prompt_contains_skill_content(self):
        engine = AgentEngine("default")
        prompt = engine.construct_system_prompt("welcome")
        assert "SKILL DEFINITION START" in prompt
        assert "SKILL DEFINITION END" in prompt

    def test_prompt_lists_all_skills(self):
        engine = AgentEngine("default")
        prompt = engine.construct_system_prompt("welcome")
        assert "AVAILABLE SKILLS:" in prompt
        assert "welcome" in prompt

    def test_encode_skill_has_data_call_block(self):
        """ENCODE skills should include DATA_CALL instructions."""
        engine = AgentEngine("default")
        prompt = engine.construct_system_prompt("ENCODE_Search")
        assert "DATA_CALL" in prompt
        assert "consortium=encode" in prompt

    def test_prompt_has_plot_instructions(self):
        engine = AgentEngine("default")
        prompt = engine.construct_system_prompt("welcome")
        assert "PLOT" in prompt


# ---------------------------------------------------------------------------
# AgentEngine.think — with mocked OpenAI client
# ---------------------------------------------------------------------------
class TestThink:
    @patch("cortex.agent_engine.client")
    def test_think_returns_response_and_usage(self, mock_client):
        """think() should return (text, usage_dict)."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test response"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15
        mock_client.chat.completions.create.return_value = mock_response

        engine = AgentEngine("default")
        text, usage = engine.think("Hello", skill_key="welcome")

        assert text == "Test response"
        assert usage["total_tokens"] == 15
        mock_client.chat.completions.create.assert_called_once()

    @patch("cortex.agent_engine.client")
    def test_think_with_conversation_history(self, mock_client):
        """think() should include conversation history in messages."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        engine = AgentEngine("default")
        history = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First reply"},
        ]
        engine.think("Second message", skill_key="welcome", conversation_history=history)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # system + 2 history + current = 4 messages
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[-1]["content"] == "Second message"

    @patch("cortex.agent_engine.client")
    def test_think_handles_exception(self, mock_client):
        """think() should return error message on exception, not raise."""
        mock_client.chat.completions.create.side_effect = Exception("Connection timeout")

        engine = AgentEngine("default")
        text, usage = engine.think("Hello", skill_key="welcome")

        assert "Brain Freeze" in text
        assert "Connection timeout" in text
        assert usage["total_tokens"] == 0
