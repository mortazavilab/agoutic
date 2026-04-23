"""
Tests for ConversationState in cortex/schemas.py.
"""

import json
import pytest
from cortex.schemas import ConversationState


class TestConversationStateInit:
    def test_defaults(self):
        state = ConversationState()
        assert state.active_skill == ""
        assert state.work_dir is None
        assert state.sample_name is None
        assert state.known_dataframes == []
        assert state.collected_params == {}

    def test_with_values(self):
        state = ConversationState(
            active_skill="run_dogme_dna",
            sample_name="C2C12r1",
            work_dir="/tmp/work",
        )
        assert state.active_skill == "run_dogme_dna"
        assert state.sample_name == "C2C12r1"


class TestToDict:
    def test_strips_none(self):
        state = ConversationState(active_skill="welcome")
        d = state.to_dict()
        assert "sample_name" not in d  # None values stripped
        assert "active_skill" in d

    def test_strips_empty_string(self):
        state = ConversationState()  # active_skill defaults to ""
        d = state.to_dict()
        assert "active_skill" not in d  # Empty string stripped

    def test_strips_empty_list(self):
        state = ConversationState()
        d = state.to_dict()
        assert "known_dataframes" not in d  # [] stripped

    def test_strips_empty_dict(self):
        state = ConversationState()
        d = state.to_dict()
        assert "collected_params" not in d  # {} stripped

    def test_non_empty_values_kept(self):
        state = ConversationState(
            active_skill="test",
            known_dataframes=["DF1"],
            collected_params={"sample_name": "s1"},
        )
        d = state.to_dict()
        assert d["known_dataframes"] == ["DF1"]
        assert d["collected_params"] == {"sample_name": "s1"}


class TestToJson:
    def test_compact_format(self):
        state = ConversationState(active_skill="welcome", sample_name="test")
        j = state.to_json()
        # Should be compact (no spaces after separators)
        assert ": " not in j
        parsed = json.loads(j)
        assert parsed["active_skill"] == "welcome"
        assert parsed["sample_name"] == "test"


class TestFromDict:
    def test_round_trip(self):
        original = ConversationState(
            active_skill="run_dogme_dna",
            sample_name="C2C12r1",
            work_dir="/tmp",
            reference_genome="mm39",
            known_dataframes=["DF1 (10 files)"],
            latest_dataframe="DF1",
        )
        d = original.to_dict()
        restored = ConversationState.from_dict(d)
        assert restored.active_skill == "run_dogme_dna"
        assert restored.sample_name == "C2C12r1"
        assert restored.latest_dataframe == "DF1"

    def test_ignores_extra_keys(self):
        data = {"active_skill": "test", "unknown_field": "ignored"}
        state = ConversationState.from_dict(data)
        assert state.active_skill == "test"
        assert not hasattr(state, "unknown_field")

    def test_missing_keys_use_defaults(self):
        state = ConversationState.from_dict({"active_skill": "test"})
        assert state.sample_name is None
        assert state.known_dataframes == []

    def test_empty_dict(self):
        state = ConversationState.from_dict({})
        assert state.active_skill == ""
