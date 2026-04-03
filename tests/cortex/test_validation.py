"""
Tests for _validate_llm_output in cortex/app.py.

Covers the output contract validator that checks LLM responses for:
- Malformed DATA_CALL tags
- Duplicate APPROVAL_NEEDED tags
- SKILL_SWITCH during running jobs
- Unknown tool names
- Mixed sources in tags
"""

import json
import pytest
from cortex.llm_validators import _validate_llm_output
from tests.conftest import FakeBlock


class TestMalformedDataCall:
    def test_no_tool_in_data_call_stripped(self):
        response = "Hello [[DATA_CALL: service=analyzer]] world"
        cleaned, violations = _validate_llm_output(response, "welcome")
        assert "[[DATA_CALL:" not in cleaned
        assert len(violations) >= 1
        assert any("Malformed DATA_CALL" in v for v in violations)

    def test_valid_data_call_kept(self):
        response = "Here: [[DATA_CALL: service=analyzer, tool=list_job_files, work_dir=/tmp]]"
        cleaned, violations = _validate_llm_output(response, "welcome")
        assert "[[DATA_CALL:" in cleaned

    def test_no_data_calls_no_violations(self):
        response = "Just a normal response with no tags."
        cleaned, violations = _validate_llm_output(response, "welcome")
        assert cleaned == response
        assert violations == []


class TestDuplicateApproval:
    def test_multiple_approval_tags_deduplicated(self):
        response = "Plan A [[APPROVAL_NEEDED]] and Plan B [[APPROVAL_NEEDED]]"
        cleaned, violations = _validate_llm_output(response, "run_dogme_dna")
        assert cleaned.count("[[APPROVAL_NEEDED]]") == 1
        assert any("Duplicate APPROVAL_NEEDED" in v for v in violations)

    def test_single_approval_kept(self):
        response = "Here is the plan [[APPROVAL_NEEDED]]"
        cleaned, violations = _validate_llm_output(response, "run_dogme_dna")
        assert cleaned.count("[[APPROVAL_NEEDED]]") == 1
        assert not any("Duplicate" in v for v in violations)


class TestSkillSwitchDuringJob:
    def test_skill_switch_stripped_during_running_job(self):
        """SKILL_SWITCH should be stripped when a job is genuinely running and no user message after."""
        block = FakeBlock(
            type="EXECUTION_JOB",
            status="RUNNING",
            payload={"job_status": {"status": "RUNNING"}, "run_uuid": "abc"},
        )
        block.seq = 10
        response = "Switching [[SKILL_SWITCH_TO: welcome]] now"
        cleaned, violations = _validate_llm_output(response, "run_dogme_dna", [block])
        assert "[[SKILL_SWITCH_TO:" not in cleaned
        assert any("SKILL_SWITCH" in v for v in violations)

    def test_skill_switch_allowed_when_job_completed(self):
        """SKILL_SWITCH should be allowed when inner status is COMPLETED (stale block)."""
        block = FakeBlock(
            type="EXECUTION_JOB",
            status="RUNNING",  # Block status is stale
            payload={"job_status": {"status": "COMPLETED"}, "run_uuid": "abc"},
        )
        response = "Switching [[SKILL_SWITCH_TO: welcome]] now"
        cleaned, violations = _validate_llm_output(response, "run_dogme_dna", [block])
        assert "[[SKILL_SWITCH_TO:" in cleaned  # Not stripped

    def test_skill_switch_allowed_when_no_running_job(self):
        """SKILL_SWITCH is fine when no jobs are running."""
        response = "Switching [[SKILL_SWITCH_TO: welcome]] now"
        cleaned, violations = _validate_llm_output(response, "welcome", [])
        assert "[[SKILL_SWITCH_TO:" in cleaned

    def test_skill_switch_allowed_when_user_message_after_job(self):
        """SKILL_SWITCH should be allowed when user sent a message after the running job."""
        job_block = FakeBlock(
            type="EXECUTION_JOB",
            status="RUNNING",
            payload={"job_status": {"status": "RUNNING"}, "run_uuid": "abc"},
        )
        job_block.seq = 5
        user_msg = FakeBlock(type="USER_MESSAGE", status="DONE", payload={"text": "parse my csv"})
        user_msg.seq = 10
        response = "Switching [[SKILL_SWITCH_TO: analyze_job_results]] now"
        cleaned, violations = _validate_llm_output(
            response, "analyze_local_sample", [job_block, user_msg],
        )
        assert "[[SKILL_SWITCH_TO:" in cleaned  # Not stripped
        assert not any("SKILL_SWITCH" in v for v in violations)

    def test_skill_switch_blocked_when_user_message_before_job(self):
        """SKILL_SWITCH should be blocked when user message was before the running job."""
        user_msg = FakeBlock(type="USER_MESSAGE", status="DONE", payload={"text": "run pipeline"})
        user_msg.seq = 3
        job_block = FakeBlock(
            type="EXECUTION_JOB",
            status="RUNNING",
            payload={"job_status": {"status": "RUNNING"}, "run_uuid": "abc"},
        )
        job_block.seq = 5
        response = "Switching [[SKILL_SWITCH_TO: welcome]] now"
        cleaned, violations = _validate_llm_output(
            response, "run_dogme_dna", [user_msg, job_block],
        )
        assert "[[SKILL_SWITCH_TO:" not in cleaned
        assert any("SKILL_SWITCH" in v for v in violations)


class TestUnknownTools:
    def test_unknown_tool_flagged(self):
        response = "[[DATA_CALL: service=analyzer, tool=nonexistent_tool]]"
        cleaned, violations = _validate_llm_output(response, "welcome")
        assert any("Unknown tool" in v for v in violations)

    def test_known_tool_not_flagged(self):
        response = "[[DATA_CALL: service=analyzer, tool=list_job_files]]"
        cleaned, violations = _validate_llm_output(response, "welcome")
        assert not any("Unknown tool" in v for v in violations)

    def test_search_alias_not_flagged(self):
        """'search' is a valid alias that gets resolved downstream."""
        response = "[[DATA_CALL: consortium=encode, tool=search]]"
        cleaned, violations = _validate_llm_output(response, "ENCODE_Search")
        assert not any("Unknown tool" in v for v in violations)

    def test_show_bam_details_alias_not_flagged(self):
        """show_bam_details is a compatibility alias resolved downstream."""
        response = "[[DATA_CALL: service=analyzer, tool=show_bam_details, file_path=ENCFF032XPV.bam]]"
        cleaned, violations = _validate_llm_output(response, "analyze_job_results")
        assert not any("Unknown tool" in v for v in violations)

    def test_remote_launchpad_tool_not_flagged(self):
        response = "[[DATA_CALL: service=launchpad, tool=list_ssh_profiles, user_id=<user_id>]]"
        cleaned, violations = _validate_llm_output(response, "remote_execution")
        assert not any("Unknown tool" in v for v in violations)


class TestMixedSources:
    def test_mixed_unrelated_sources_flagged(self):
        response = (
            "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]] "
            "[[DATA_CALL: service=launchpad, tool=submit_dogme_job]]"
        )
        cleaned, violations = _validate_llm_output(response, "welcome")
        assert any("Mixed sources" in v for v in violations)

    def test_encode_plus_analyzer_not_flagged(self):
        """ENCODE + analyzer is a legitimate browsing combo."""
        response = (
            "[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]] "
            "[[DATA_CALL: service=analyzer, tool=list_job_files]]"
        )
        cleaned, violations = _validate_llm_output(response, "welcome")
        assert not any("Mixed sources" in v for v in violations)
