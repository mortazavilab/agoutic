"""Tests for cortex/remote_stage_status.py — pure dict-based stage status builders."""

import pytest

from cortex.remote_stage_status import (
    _make_stage_part,
    _stage_part_progress,
    _reference_stage_message,
    _initial_stage_parts,
    _final_stage_parts,
    _failed_stage_parts,
    _resuming_stage_parts,
    _cancelled_stage_parts,
)


# ---------------------------------------------------------------------------
# _make_stage_part
# ---------------------------------------------------------------------------

class TestMakeStagePart:
    def test_basic_part(self):
        part = _make_stage_part("RUNNING", 40, "Processing...")
        assert part["status"] == "RUNNING"
        assert part["progress_percent"] == 40
        assert part["message"] == "Processing..."
        assert "details" not in part

    def test_part_with_details(self):
        details = [{"key": "value"}]
        part = _make_stage_part("COMPLETED", 100, "Done", details)
        assert part["details"] == details


# ---------------------------------------------------------------------------
# _stage_part_progress
# ---------------------------------------------------------------------------

class TestStagePartProgress:
    def test_none_parts_returns_zero(self):
        assert _stage_part_progress(None) == 0

    def test_empty_dict_returns_zero(self):
        assert _stage_part_progress({}) == 0

    def test_references_only(self):
        parts = {"references": {"progress_percent": 80}}
        # Averages over both "references" and "data" keys; missing data returns 0
        assert _stage_part_progress(parts) == 40

    def test_data_only(self):
        parts = {"data": {"progress_percent": 60}}
        # Averages over both keys; missing references returns 0
        assert _stage_part_progress(parts) == 30

    def test_references_and_data_average(self):
        parts = {
            "references": {"progress_percent": 100},
            "data": {"progress_percent": 50},
        }
        assert _stage_part_progress(parts) == 75

    def test_clamped_to_100(self):
        parts = {"references": {"progress_percent": 200}, "data": {"progress_percent": 0}}
        # 200 clamped to 100, data is 0, average is 50
        assert _stage_part_progress(parts) == 50

    def test_negative_clamped_to_zero(self):
        parts = {"references": {"progress_percent": -10}}
        assert _stage_part_progress(parts) == 0

    def test_invalid_value_defaults_to_zero(self):
        parts = {"references": {"progress_percent": "invalid"}}
        assert _stage_part_progress(parts) == 0


# ---------------------------------------------------------------------------
# _reference_stage_message
# ---------------------------------------------------------------------------

class TestReferenceStageMessage:
    def test_empty_statuses_returns_ready(self):
        message, details = _reference_stage_message({})
        assert message == "Reference assets are ready."
        assert details == []

    def test_all_reused_returns_single_message(self):
        statuses = {"ref1": "reused", "ref2": "reused"}
        message, details = _reference_stage_message(statuses)
        assert message == "Reference assets already staged on the remote profile."
        assert len(details) == 2

    def test_mixed_statuses_returns_summary(self):
        statuses = {"ref1": "reused", "ref2": "staged"}
        message, details = _reference_stage_message(statuses)
        assert "already staged" in message or "staged" in message
        assert len(details) == 2

    def test_unknown_status_shows_raw(self):
        statuses = {"ref1": "unknown_status"}
        message, details = _reference_stage_message(statuses)
        assert "unknown_status" in details[0]["message"]

    def test_refreshed_status(self):
        statuses = {"ref1": "refreshed"}
        message, details = _reference_stage_message(statuses)
        assert "refreshed" in details[0]["message"]

    def test_fallback_status(self):
        statuses = {"ref1": "fallback"}
        message, details = _reference_stage_message(statuses)
        assert "fallback" in details[0]["message"]


# ---------------------------------------------------------------------------
# _initial_stage_parts
# ---------------------------------------------------------------------------

class TestInitialStageParts:
    def test_empty_preflight(self):
        parts = _initial_stage_parts({})
        assert "references" in parts
        assert "data" in parts
        assert parts["references"]["status"] == "RUNNING"
        assert parts["data"]["status"] == "PENDING"

    def test_reference_reuse_only(self):
        cache = {
            "reference_actions": [{"action": "reuse", "reference_id": "ref1"}],
            "data_action": {"action": "reuse"},
        }
        parts = _initial_stage_parts(cache)
        assert parts["references"]["status"] == "COMPLETED"
        assert parts["data"]["status"] == "COMPLETED"

    def test_reference_pending_data_pending(self):
        cache = {
            "reference_actions": [{"action": "stage", "reference_id": "ref1"}],
            "data_action": {"action": "stage"},
        }
        parts = _initial_stage_parts(cache)
        assert parts["references"]["status"] == "RUNNING"
        assert parts["data"]["status"] == "PENDING"

    def test_reference_completed_data_running(self):
        cache = {
            "reference_actions": [{"action": "refresh", "reference_id": "ref1"}],
            "data_action": {"action": "stage"},
        }
        parts = _initial_stage_parts(cache)
        # Reference is RUNNING, data should be PENDING since ref not done
        assert parts["references"]["status"] == "RUNNING"


# ---------------------------------------------------------------------------
# _final_stage_parts
# ---------------------------------------------------------------------------

class TestFinalStageParts:
    def test_successful_stage(self):
        result = {
            "reference_cache_statuses": {"ref1": "staged"},
            "data_cache_status": "staged",
        }
        parts = _final_stage_parts(result)
        assert parts["references"]["status"] == "COMPLETED"
        assert parts["data"]["status"] == "COMPLETED"

    def test_reused_data(self):
        result = {
            "reference_cache_statuses": {},
            "data_cache_status": "reused",
        }
        parts = _final_stage_parts(result)
        assert "already staged" in parts["data"]["message"]

    def test_resumed_data(self):
        result = {
            "reference_cache_statuses": {},
            "data_cache_status": "resumed",
        }
        parts = _final_stage_parts(result)
        assert "resumed" in parts["data"]["message"]

    def test_fallback_data(self):
        result = {
            "reference_cache_statuses": {},
            "data_cache_status": "fallback",
        }
        parts = _final_stage_parts(result)
        assert "fallback" in parts["data"]["message"]


# ---------------------------------------------------------------------------
# _failed_stage_parts
# ---------------------------------------------------------------------------

class TestFailedStageParts:
    def test_reference_cache_failure(self):
        error = "Reference cache stage failed: timeout"
        parts = _failed_stage_parts(None, error)
        assert parts["references"]["status"] == "FAILED"
        assert parts["data"]["status"] == "PENDING"

    def test_input_transfer_failure(self):
        error = "Input transfer failed: local source path does not exist"
        parts = _failed_stage_parts(None, error)
        assert parts["references"]["status"] == "COMPLETED"
        assert parts["data"]["status"] == "FAILED"

    def test_generic_failure(self):
        error = "Something went wrong"
        parts = _failed_stage_parts(None, error)
        for part in parts.values():
            if part.get("status") != "COMPLETED":
                assert part["status"] == "FAILED"


# ---------------------------------------------------------------------------
# _resuming_stage_parts
# ---------------------------------------------------------------------------

class TestResumingStageParts:
    def test_resume_with_completed_references(self):
        parts = {"references": {"status": "COMPLETED", "progress_percent": 80}}
        result = _resuming_stage_parts(parts)
        assert result["references"]["progress_percent"] == 100
        assert result["data"]["status"] == "RUNNING"

    def test_resume_with_pending_references(self):
        parts = {"references": {"status": "RUNNING"}}
        result = _resuming_stage_parts(parts)
        assert result["references"]["status"] == "RUNNING"
        assert result["data"]["status"] == "PENDING"


# ---------------------------------------------------------------------------
# _cancelled_stage_parts
# ---------------------------------------------------------------------------

class TestCancelledStageParts:
    def test_cancelled_stages(self):
        parts = {"references": {"status": "RUNNING"}}
        result = _cancelled_stage_parts(parts, "User cancelled")
        assert result["references"]["status"] == "CANCELLED"
        assert result["data"]["status"] == "CANCELLED"

    def test_cancelled_with_completed_references(self):
        parts = {"references": {"status": "COMPLETED"}}
        result = _cancelled_stage_parts(parts, "User cancelled")
        # References stay COMPLETED, data gets cancelled
        assert result["data"]["status"] == "CANCELLED"
