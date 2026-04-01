"""
Tests for cortex/app.py — Background task functions.

Covers:
  - handle_rejection — rejection handling with LLM revision
  - download_after_approval — file downloading after gate approval
  - submit_job_after_approval — job submission to Launchpad MCP
  - poll_job_status — job status polling and auto-analysis trigger
  - _auto_trigger_analysis — post-completion analysis generation
  - _build_auto_analysis_context — context builder for LLM
  - _build_static_analysis_summary — static fallback summary
  - extract_job_parameters_from_conversation — parameter extraction heuristics
"""

import asyncio
import datetime
import json
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock,
)
from cortex.app import (
    handle_rejection,
    download_after_approval,
    _auto_execute_plan_steps,
    _ensure_workflow_plan_approval_gate,
    _initial_stage_parts,
    _build_auto_analysis_context,
    _build_static_analysis_summary,
    get_block_payload,
    _create_block_internal,
    _active_downloads,
)
from cortex.job_parameters import extract_job_parameters_from_conversation
from cortex.job_polling import (
    poll_job_status,
    _auto_trigger_analysis,
    _completed_job_results_ready,
    _resolved_job_work_directory,
)
from cortex.workflow_submission import submit_job_after_approval
from cortex.planner import _template_remote_stage_workflow
from cortex.remote_orchestration import (
    _find_remote_staged_sample,
    _ensure_remote_sample_workflow,
)
from launchpad.models import RemoteStagedSample


def test_initial_stage_parts_describe_preflight_reference_actions_without_implying_active_transfer():
    parts = _initial_stage_parts(
        {
            "reference_actions": [
                {"reference_id": "mm39", "action": "refresh"},
            ],
            "data_action": {"action": "reuse"},
        }
    )

    references = parts["references"]
    assert references["status"] == "RUNNING"
    assert references["message"] == "Checking and preparing reference assets on the remote profile..."
    details = references.get("details") or []
    assert details
    assert details[0]["message"] == "Planned refresh for mm39 on the remote profile."


def test_find_remote_staged_sample_invalidates_empty_fingerprint(session_factory, seed_data):
    sess = session_factory()
    entry = RemoteStagedSample(
        id=str(uuid.uuid4()),
        user_id="u-bg",
        ssh_profile_id="profile-123",
        ssh_profile_nickname="hpc3",
        sample_name="jamshid",
        sample_slug="jamshid",
        mode="CDNA",
        reference_genome_json=["mm39"],
        source_path="/data/pod5",
        input_fingerprint="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        remote_base_path="/remote/u1/agoutic",
        remote_data_path="/remote/u1/agoutic/data/fp1",
        remote_reference_paths_json={"mm39": "/remote/u1/agoutic/ref/mm39"},
        status="READY",
        last_staged_at=datetime.datetime.utcnow(),
        last_used_at=datetime.datetime.utcnow(),
    )
    sess.add(entry)
    sess.commit()

    result = _find_remote_staged_sample(
        sess,
        owner_id="u-bg",
        ssh_profile_id="profile-123",
        sample_name="jamshid",
        mode="CDNA",
        input_directory="/data/pod5",
        input_directory_explicit=True,
    )

    sess.refresh(entry)
    assert result is None
    assert entry.status == "INVALID_EMPTY_FINGERPRINT"
    sess.close()


def test_find_remote_staged_sample_invalidates_source_mismatch(session_factory, seed_data):
    sess = session_factory()
    entry = RemoteStagedSample(
        id=str(uuid.uuid4()),
        user_id="u-bg",
        ssh_profile_id="profile-123",
        ssh_profile_nickname="hpc3",
        sample_name="jamshid",
        sample_slug="jamshid",
        mode="CDNA",
        reference_genome_json=["mm39"],
        source_path="/data/old_pod5",
        input_fingerprint="abc123deadbeef",
        remote_base_path="/remote/u1/agoutic",
        remote_data_path="/remote/u1/agoutic/data/fp1",
        remote_reference_paths_json={"mm39": "/remote/u1/agoutic/ref/mm39"},
        status="READY",
        last_staged_at=datetime.datetime.utcnow(),
        last_used_at=datetime.datetime.utcnow(),
    )
    sess.add(entry)
    sess.commit()

    result = _find_remote_staged_sample(
        sess,
        owner_id="u-bg",
        ssh_profile_id="profile-123",
        sample_name="jamshid",
        mode="CDNA",
        input_directory="/data/new_pod5",
        input_directory_explicit=True,
    )

    sess.refresh(entry)
    assert result is None
    assert entry.status == "INVALID_SOURCE_MISMATCH"
    sess.close()


def test_ensure_remote_sample_workflow_reuses_active_workflow(session_factory, seed_data):
    sess = session_factory()
    existing = _create_block_internal(
        sess,
        "proj-bg",
        "WORKFLOW_PLAN",
        {
            "workflow_type": "remote_sample_intake",
            "title": "Run remote analysis for jamshid",
            "sample_name": "jamshid",
            "mode": "CDNA",
            "source_path": "/data/pod5",
            "staged_input_directory": "",
            "remote_base_path": "/remote/u1/agoutic",
            "ssh_profile_id": "profile-123",
            "run_uuid": None,
            "status": "RUNNING",
            "steps": [],
        },
        status="PENDING",
        owner_id="u-bg",
    )
    sess.commit()

    workflow_block = _ensure_remote_sample_workflow(
        sess,
        "proj-bg",
        "u-bg",
        gate_block_id="gate-1",
        job_data={
            "sample_name": "jamshid",
            "mode": "CDNA",
            "input_directory": "/data/pod5_new",
            "staged_remote_input_path": "/remote/u1/agoutic/data/fp1",
            "remote_base_path": "/remote/u1/agoutic",
            "ssh_profile_id": "profile-123",
        },
        stage_only=False,
    )

    all_workflows = sess.query(ProjectBlock).filter(ProjectBlock.type == "WORKFLOW_PLAN").all()
    payload = get_block_payload(workflow_block)
    assert workflow_block.id == existing.id
    assert len(all_workflows) == 1
    assert payload["source_path"] == "/data/pod5_new"
    assert payload["staged_input_directory"] == "/remote/u1/agoutic/data/fp1"
    sess.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture()
def seed_data(session_factory):
    sess = session_factory()

    user = User(id="u-bg", email="bg@example.com", role="user",
                username="bguser", is_active=True)
    sess.add(user)

    session_obj = SessionModel(id="bg-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)

    proj = Project(id="proj-bg", name="BG Proj", owner_id="u-bg",
                   slug="bg-proj")
    sess.add(proj)
    sess.flush()

    access = ProjectAccess(id=str(uuid.uuid4()), project_id="proj-bg",
                           user_id="u-bg", project_name="BG Proj",
                           role="owner")
    sess.add(access)
    sess.commit()
    sess.close()

    return {"user_id": "u-bg", "project_id": "proj-bg", "session_id": "bg-session"}


import contextlib

@contextlib.contextmanager
def _patch_session(session_factory):
    """Context manager that redirects SessionLocal to our in-memory DB."""
    with patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.job_polling.SessionLocal", session_factory), \
         patch("cortex.workflow_submission.SessionLocal", session_factory), \
         patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.admin.SessionLocal", session_factory), \
         patch("cortex.auth.SessionLocal", session_factory), \
         patch("cortex.chat_approval.SessionLocal", session_factory), \
         patch("cortex.chat_downloads.SessionLocal", session_factory):
        yield


async def _mock_run_in_threadpool(fn, *args, **kwargs):
    """Replacement for run_in_threadpool that runs synchronously."""
    return fn(*args, **kwargs)


def _create_gate(session_factory, project_id, owner_id, payload, status="PENDING"):
    """Create an APPROVAL_GATE block and return it."""
    sess = session_factory()
    block = _create_block_internal(
        sess, project_id, "APPROVAL_GATE", payload, status=status, owner_id=owner_id,
    )
    sess.close()
    return block


def _create_user_message(session_factory, project_id, owner_id, text):
    """Create a USER_MESSAGE block."""
    sess = session_factory()
    block = _create_block_internal(
        sess, project_id, "USER_MESSAGE", {"text": text}, owner_id=owner_id,
    )
    sess.close()
    return block


def _create_agent_plan(session_factory, project_id, owner_id, markdown, skill=None):
    """Create an AGENT_PLAN block."""
    sess = session_factory()
    payload = {"markdown": markdown}
    if skill:
        payload["skill"] = skill
    block = _create_block_internal(
        sess, project_id, "AGENT_PLAN", payload, owner_id=owner_id,
    )
    sess.close()
    return block


# ---------------------------------------------------------------------------
# handle_rejection
# ---------------------------------------------------------------------------

class TestHandleRejection:
    """Tests for the handle_rejection background task."""

    @pytest.mark.asyncio
    async def test_missing_gate_block(self, session_factory, seed_data):
        """Returns early when gate block doesn't exist."""
        with _patch_session(session_factory):
            # Should not raise
            await handle_rejection("proj-bg", "nonexistent-id")

    @pytest.mark.asyncio
    async def test_rejection_history_updated(self, session_factory, seed_data):
        """Rejection reason is appended to rejection_history in the gate payload."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "skill": "analyze_local_sample",
            "model": "default",
            "rejection_reason": "Wrong genome",
            "attempt_number": 1,
        })

        mock_engine = MagicMock()
        mock_engine.think = MagicMock(return_value="Revised plan\n[[APPROVAL_NEEDED]]")

        with _patch_session(session_factory), \
             patch("cortex.chat_approval.AgentEngine", return_value=mock_engine), \
             patch("cortex.chat_approval.run_in_threadpool", _mock_run_in_threadpool), \
             patch("cortex.job_parameters.extract_job_parameters_from_conversation",
                   new_callable=AsyncMock, return_value={"sample_name": "test"}):
            await handle_rejection("proj-bg", gate.id)

        # Check the gate block's rejection_history was updated
        sess = session_factory()
        updated = sess.query(ProjectBlock).filter(ProjectBlock.id == gate.id).first()
        payload = get_block_payload(updated)
        assert len(payload["rejection_history"]) == 1
        assert payload["rejection_history"][0]["reason"] == "Wrong genome"
        sess.close()

    @pytest.mark.asyncio
    async def test_max_attempts_creates_manual_form(self, session_factory, seed_data):
        """When attempt_number >= 3, creates manual configuration form."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "skill": "analyze_local_sample",
            "model": "default",
            "rejection_reason": "Still wrong",
            "attempt_number": 3,
        })

        with _patch_session(session_factory), \
             patch("cortex.job_parameters.extract_job_parameters_from_conversation",
                   new_callable=AsyncMock, return_value={"sample_name": "s1", "mode": "DNA"}):
            await handle_rejection("proj-bg", gate.id)

        # Should create an AGENT_PLAN + a new APPROVAL_GATE with manual_mode
        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-bg"
        ).order_by(ProjectBlock.seq.asc()).all()

        # Original gate + AGENT_PLAN (manual form) + APPROVAL_GATE (manual)
        plan_blocks = [b for b in blocks if b.type == "AGENT_PLAN"]
        gate_blocks = [b for b in blocks if b.type == "APPROVAL_GATE"]
        assert len(plan_blocks) >= 1
        assert "Manual Configuration" in get_block_payload(plan_blocks[-1]).get("markdown", "")

        latest_gate = gate_blocks[-1]
        gate_payload = get_block_payload(latest_gate)
        assert gate_payload.get("manual_mode") is True
        assert latest_gate.status == "PENDING"
        sess.close()

    @pytest.mark.asyncio
    async def test_llm_revision_with_approval(self, session_factory, seed_data):
        """Under max attempts, calls LLM and creates revised plan + new approval gate."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "skill": "analyze_local_sample",
            "model": "default",
            "rejection_reason": "Use mm39 instead",
            "attempt_number": 1,
        })
        # Add some conversation context
        _create_user_message(session_factory, "proj-bg", "u-bg", "Run dogme DNA on my sample")
        _create_agent_plan(session_factory, "proj-bg", "u-bg",
                          "I'll run DNA analysis with GRCh38", skill="analyze_local_sample")

        mock_engine = MagicMock()
        mock_engine.think = MagicMock(
            return_value="Here's my revised plan with mm39\n[[APPROVAL_NEEDED]]"
        )

        with _patch_session(session_factory), \
             patch("cortex.chat_approval.AgentEngine", return_value=mock_engine), \
             patch("cortex.chat_approval.run_in_threadpool", _mock_run_in_threadpool), \
             patch("cortex.job_parameters.extract_job_parameters_from_conversation",
                   new_callable=AsyncMock, return_value={"sample_name": "test"}):
            await handle_rejection("proj-bg", gate.id)

        # Should have created a new AGENT_PLAN + new APPROVAL_GATE
        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-bg"
        ).order_by(ProjectBlock.seq.asc()).all()

        agent_plans = [b for b in blocks if b.type == "AGENT_PLAN"]
        new_gates = [b for b in blocks if b.type == "APPROVAL_GATE" and b.id != gate.id]

        # LLM output became an AGENT_PLAN
        assert any("revised plan" in get_block_payload(b).get("markdown", "").lower()
                   for b in agent_plans)

        # New gate was created with attempt_number incremented
        assert len(new_gates) >= 1
        new_gate_payload = get_block_payload(new_gates[-1])
        assert new_gate_payload.get("attempt_number") == 2
        sess.close()

    @pytest.mark.asyncio
    async def test_llm_no_approval_tag(self, session_factory, seed_data):
        """When LLM doesn't include APPROVAL_NEEDED, no new gate is created (info request)."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "skill": "analyze_local_sample",
            "model": "default",
            "rejection_reason": "Clarify genome",
            "attempt_number": 1,
        })
        mock_engine = MagicMock()
        mock_engine.think = MagicMock(
            return_value="Could you clarify which genome you want?"
        )

        with _patch_session(session_factory), \
             patch("cortex.chat_approval.AgentEngine", return_value=mock_engine), \
             patch("cortex.chat_approval.run_in_threadpool", _mock_run_in_threadpool):
            await handle_rejection("proj-bg", gate.id)

        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-bg"
        ).order_by(ProjectBlock.seq.asc()).all()

        # AGENT_PLAN created but no new APPROVAL_GATE
        new_gates = [b for b in blocks if b.type == "APPROVAL_GATE" and b.id != gate.id]
        assert len(new_gates) == 0

        agent_plans = [b for b in blocks if b.type == "AGENT_PLAN"]
        assert any("clarify" in get_block_payload(b).get("markdown", "").lower()
                   for b in agent_plans)
        sess.close()

    @pytest.mark.asyncio
    async def test_llm_failure_creates_error_block(self, session_factory, seed_data):
        """When LLM raises an exception, an error block is created."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "skill": "analyze_local_sample",
            "model": "default",
            "rejection_reason": "Try again",
            "attempt_number": 1,
        })
        mock_engine = MagicMock()
        mock_engine.think = MagicMock(side_effect=RuntimeError("LLM timeout"))

        with _patch_session(session_factory), \
             patch("cortex.chat_approval.AgentEngine", return_value=mock_engine), \
             patch("cortex.chat_approval.run_in_threadpool", _mock_run_in_threadpool):
            await handle_rejection("proj-bg", gate.id)

        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-bg",
            ProjectBlock.type == "AGENT_PLAN",
        ).all()
        error_blocks = [b for b in blocks if get_block_payload(b).get("error")]
        assert len(error_blocks) >= 1
        assert "LLM timeout" in get_block_payload(error_blocks[0]).get("markdown", "")
        sess.close()


# ---------------------------------------------------------------------------
# download_after_approval
# ---------------------------------------------------------------------------

class TestDownloadAfterApproval:
    """Tests for the download_after_approval background task."""

    @pytest.mark.asyncio
    async def test_missing_gate_block(self, session_factory, seed_data):
        """Returns early when gate block doesn't exist."""
        with _patch_session(session_factory):
            await download_after_approval("proj-bg", "nonexistent-id")

    @pytest.mark.asyncio
    async def test_files_from_gate_params(self, session_factory, seed_data, tmp_path):
        """Uses files list from gate extracted_params when available."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "files": [
                    {"url": "https://example.com/file1.bam", "filename": "file1.bam"},
                    {"url": "https://example.com/file2.fastq", "filename": "file2.fastq"},
                ],
                "target_dir": str(tmp_path / "downloads"),
            },
        })

        with _patch_session(session_factory), \
             patch("cortex.chat_downloads.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await download_after_approval("proj-bg", gate.id)

        # Should have created a DOWNLOAD_TASK block
        sess = session_factory()
        dl_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "DOWNLOAD_TASK"
        ).all()
        assert len(dl_blocks) == 1
        dl_payload = get_block_payload(dl_blocks[0])
        assert dl_payload["total_files"] == 2
        assert dl_payload["status"] == "RUNNING"
        sess.close()

        # Clean up _active_downloads
        _active_downloads.clear()

    @pytest.mark.asyncio
    async def test_url_scan_fallback(self, session_factory, seed_data, tmp_path):
        """Falls back to scanning conversation blocks for URLs when no files in params."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {},
        })
        # Add a block with URLs
        _create_agent_plan(session_factory, "proj-bg", "u-bg",
                          "Download: https://files.example.com/data.bam and https://files.example.com/reads.fastq")


        with _patch_session(session_factory), \
             patch("cortex.chat_downloads.asyncio") as mock_aio, \
               patch("cortex.chat_downloads.AGOUTIC_DATA", str(tmp_path)):
            mock_aio.create_task = MagicMock()
            await download_after_approval("proj-bg", gate.id)

        sess = session_factory()
        dl_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "DOWNLOAD_TASK"
        ).all()
        assert len(dl_blocks) == 1
        dl_payload = get_block_payload(dl_blocks[0])
        assert dl_payload["total_files"] == 2
        sess.close()
        _active_downloads.clear()

    @pytest.mark.asyncio
    async def test_no_urls_creates_warning(self, session_factory, seed_data):
        """When no URLs found, creates a warning AGENT_PLAN block."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {},
        })

        with _patch_session(session_factory):
            await download_after_approval("proj-bg", gate.id)

        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "AGENT_PLAN"
        ).all()
        assert len(blocks) >= 1
        assert any("No download URLs" in get_block_payload(b).get("markdown", "")
                   for b in blocks)
        sess.close()

    @pytest.mark.asyncio
    async def test_skips_api_urls(self, session_factory, seed_data, tmp_path):
        """URLs containing /api/ or portal.encode are skipped."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {},
        })
        _create_agent_plan(session_factory, "proj-bg", "u-bg",
                          "See https://portal.encode.org/search and https://api.example.com/api/status")


        with _patch_session(session_factory):
            await download_after_approval("proj-bg", gate.id)

        # Should create warning since all URLs skipped
        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "AGENT_PLAN"
        ).all()
        assert any("No download URLs" in get_block_payload(b).get("markdown", "")
                   for b in blocks)
        sess.close()

    @pytest.mark.asyncio
    async def test_target_dir_from_user_project(self, session_factory, seed_data, tmp_path):
        """When no target_dir in params, resolves from username/project_slug."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "files": [
                    {"url": "https://example.com/f.bam", "filename": "f.bam"},
                ],
            },
        })

        with _patch_session(session_factory), \
             patch("cortex.chat_downloads.AGOUTIC_DATA", str(tmp_path)), \
             patch("cortex.chat_downloads.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await download_after_approval("proj-bg", gate.id)

        # Should have used users/bguser/data as central target
        sess = session_factory()
        dl_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "DOWNLOAD_TASK"
        ).all()
        assert len(dl_blocks) == 1
        dl_payload = get_block_payload(dl_blocks[0])
        assert "bguser" in dl_payload["target_dir"]
        # Central data folder — no project slug in the path
        assert dl_payload["target_dir"].endswith("/data") or "/data" in dl_payload["target_dir"]
        sess.close()
        _active_downloads.clear()


# ---------------------------------------------------------------------------
# submit_job_after_approval
# ---------------------------------------------------------------------------

class TestSubmitJobAfterApproval:
    """Tests for the submit_job_after_approval background task."""

    @pytest.mark.asyncio
    async def test_missing_gate_block(self, session_factory, seed_data):
        """Returns early when gate block doesn't exist."""
        with _patch_session(session_factory):
            await submit_job_after_approval("proj-bg", "nonexistent-id")

    @pytest.mark.asyncio
    async def test_successful_submission(self, session_factory, seed_data):
        """Successful MCP call creates EXECUTION_JOB block with run_uuid."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "my_sample",
                "mode": "DNA",
                "input_directory": "/data/samples/test",
                "reference_genome": ["mm39"],
            },
            "skill": "analyze_local_sample",
            "model": "default",
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "abc-123",
            "work_directory": "/work/abc-123",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission.job_polling.poll_job_status", return_value=MagicMock()), \
             patch("cortex.workflow_submission.asyncio.create_task", return_value=MagicMock()):
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "EXECUTION_JOB"
        ).all()
        assert len(job_blocks) == 1
        payload = get_block_payload(job_blocks[0])
        assert payload["run_uuid"] == "abc-123"
        assert payload["work_directory"] == "/work/abc-123"
        assert payload["status"] == "SUBMITTED"
        assert job_blocks[0].status == "RUNNING"
        sess.close()

    @pytest.mark.asyncio
    async def test_edited_params_preferred(self, session_factory, seed_data):
        """edited_params are used over extracted_params when available."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "original",
                "mode": "DNA",
                "input_directory": "/data/orig",
            },
            "edited_params": {
                "sample_name": "edited_sample",
                "mode": "RNA",
                "input_directory": "/data/edited",
                "reference_genome": ["GRCh38"],
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "edited-uuid",
            "work_directory": "/work/edited",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        # Verify the edited params were sent to MCP
        call_args = mock_client.call_tool.call_args
        assert call_args.kwargs.get("sample_name") == "edited_sample" or \
               (len(call_args.args) > 1 and "edited_sample" in str(call_args))

        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "EXECUTION_JOB"
        ).all()
        assert len(job_blocks) == 1
        assert get_block_payload(job_blocks[0])["sample_name"] == "edited_sample"
        sess.close()

    @pytest.mark.asyncio
    async def test_no_params_creates_error(self, session_factory, seed_data):
        """When no params can be extracted, creates FAILED EXECUTION_JOB block."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {})

        with _patch_session(session_factory), \
             patch("cortex.job_parameters.extract_job_parameters_from_conversation",
                   new_callable=AsyncMock, return_value=None):
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "EXECUTION_JOB"
        ).all()
        assert len(job_blocks) == 1
        assert job_blocks[0].status == "FAILED"
        assert "Failed to extract" in get_block_payload(job_blocks[0]).get("error", "")
        sess.close()

    @pytest.mark.asyncio
    async def test_no_run_uuid_in_response(self, session_factory, seed_data):
        """When MCP returns no run_uuid, creates FAILED EXECUTION_JOB block."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "test", "mode": "DNA",
                "input_directory": "/data/test",
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={"error": "queue full"})

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client):
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "EXECUTION_JOB"
        ).all()
        assert len(job_blocks) == 1
        assert job_blocks[0].status == "FAILED"
        assert "No run_uuid" in get_block_payload(job_blocks[0]).get("error", "")
        sess.close()

    @pytest.mark.asyncio
    async def test_mcp_connection_error(self, session_factory, seed_data):
        """When MCP connection fails, creates FAILED EXECUTION_JOB with error."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "test", "mode": "DNA",
                "input_directory": "/data/test",
            },
        })

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("No launchpad"))

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client):
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "EXECUTION_JOB"
        ).all()
        assert len(job_blocks) == 1
        assert job_blocks[0].status == "FAILED"
        sess.close()

    @pytest.mark.asyncio
    async def test_script_submission_marks_reconcile_run_step_running(self, session_factory, seed_data):
        sess = session_factory()
        workflow_block = _create_block_internal(
            sess,
            "proj-bg",
            "WORKFLOW_PLAN",
            {
                "plan_type": "reconcile_bams",
                "status": "RUNNING",
                "current_step_id": "run_reconcile",
                "steps": [
                    {
                        "id": "approve_reconcile",
                        "kind": "REQUEST_APPROVAL",
                        "status": "COMPLETED",
                    },
                    {
                        "id": "run_reconcile",
                        "kind": "RUN_SCRIPT",
                        "status": "PENDING",
                    },
                ],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        gate = _create_block_internal(
            sess,
            "proj-bg",
            "APPROVAL_GATE",
            {
                "gate_action": "reconcile_bams",
                "extracted_params": {
                    "sample_name": "reconciled",
                    "mode": "RNA",
                    "input_directory": "/proj/reconcile",
                    "output_directory": "/proj/reconcile",
                    "output_prefix": "reconciled",
                    "run_type": "script",
                    "script_id": "reconcile_bams/reconcile_bams",
                    "annotation_gtf": "/refs/GRCh38.annotation.gtf",
                    "bam_inputs": [
                        {"path": "/proj/workflow2/annot/sample1.GRCh38.annotated.bam", "sample": "sample1", "reference": "GRCh38"},
                        {"path": "/proj/workflow3/annot/sample2.GRCh38.annotated.bam", "sample": "sample2", "reference": "GRCh38"},
                    ],
                    "gene_prefix": "CONSG",
                    "tx_prefix": "CONST",
                    "id_tag": "TX",
                    "gene_tag": "GX",
                    "threads": 8,
                    "exon_merge_distance": 5,
                    "min_tpm": 2.5,
                    "min_samples": 3,
                    "filter_known": True,
                    "workflow_block_id": workflow_block.id,
                    "gate_action": "reconcile_bams",
                },
                "model": "default",
            },
            status="APPROVED",
            owner_id="u-bg",
        )
        sess.commit()
        sess.close()

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "script-run-1",
            "work_directory": "/work/script-run-1",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        workflow = sess.query(ProjectBlock).filter(ProjectBlock.id == workflow_block.id).one()
        workflow_payload = get_block_payload(workflow)
        run_step = next(step for step in workflow_payload["steps"] if step["id"] == "run_reconcile")
        assert run_step["status"] == "RUNNING"
        assert run_step["run_uuid"] == "script-run-1"
        submitted = mock_client.call_tool.call_args.kwargs
        assert submitted["script_id"] == "reconcile_bams/reconcile_bams"
        assert submitted["script_args"][:7] == [
            "--json",
            "--output-prefix",
            "reconciled",
            "--output-dir",
            "/proj/reconcile",
            "--annotation-gtf",
            "/refs/GRCh38.annotation.gtf",
        ]
        assert submitted["script_args"].count("--input-bam") == 2
        assert "--filter_known" in submitted["script_args"]
        job_blocks = sess.query(ProjectBlock).filter(ProjectBlock.type == "EXECUTION_JOB").all()
        assert any(get_block_payload(block).get("run_type") == "script" for block in job_blocks)
        sess.close()

    @pytest.mark.asyncio
    async def test_bam_remap_resolves_project_data_dir(self, session_factory, seed_data, tmp_path):
        """BAM remap input_directory resolves to project data/ when path doesn't exist."""
        # Create project data dir
        data_dir = tmp_path / "users" / "bguser" / "bg-proj" / "data"
        data_dir.mkdir(parents=True)

        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "bam_test",
                "mode": "DNA",
                "input_type": "bam",
                "entry_point": "remap",
                "input_directory": "/data/samples/test",  # default placeholder
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "bam-uuid", "work_directory": "/work/bam",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
               patch("cortex.workflow_submission.AGOUTIC_DATA", str(tmp_path)), \
               patch("cortex.remote_orchestration.AGOUTIC_DATA", str(tmp_path)), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        # The call_tool should have received the resolved data dir
        call_args = mock_client.call_tool.call_args
        submitted_dir = call_args.kwargs.get("input_directory", "")
        assert "bguser" in submitted_dir or "bg-proj" in submitted_dir

    @pytest.mark.asyncio
    async def test_local_sample_is_staged_before_submission(self, session_factory, seed_data, tmp_path):
        """Local sample intake copies data into the user's central data folder before Dogme submission."""
        source_dir = tmp_path / "incoming" / "pod5"
        source_dir.mkdir(parents=True)
        (source_dir / "reads.pod5").write_text("pod5-data")

        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": str(source_dir),
                "reference_genome": ["mm39"],
            },
            "skill": "analyze_local_sample",
            "model": "default",
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "stage-uuid",
            "work_directory": "/work/stage-uuid",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
               patch("cortex.workflow_submission.AGOUTIC_DATA", tmp_path), \
               patch("cortex.remote_orchestration.AGOUTIC_DATA", tmp_path), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        staged_dir = tmp_path / "users" / "bguser" / "data" / "jamshid"
        assert staged_dir.exists()
        assert (staged_dir / "reads.pod5").read_text() == "pod5-data"

        submitted = mock_client.call_tool.call_args.kwargs
        assert submitted["input_directory"] == str(staged_dir)
        assert "staged_input_directory" not in submitted

        sess = session_factory()
        workflow_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "WORKFLOW_PLAN").one()
        workflow_payload = get_block_payload(workflow_block)
        assert workflow_payload["steps"][0]["status"] == "COMPLETED"
        assert workflow_payload["steps"][1]["status"] == "RUNNING"
        assert workflow_payload["next_step"] == "run_dogme"
        sess.close()

    @pytest.mark.asyncio
    async def test_existing_staged_dir_creates_decision_gate(self, session_factory, seed_data, tmp_path):
        """When the staged sample folder already exists, submission pauses for a reuse-or-replace decision."""
        source_dir = tmp_path / "incoming" / "pod5"
        source_dir.mkdir(parents=True)
        (source_dir / "reads.pod5").write_text("pod5-data")
        staged_dir = tmp_path / "users" / "bguser" / "data" / "jamshid"
        staged_dir.mkdir(parents=True)
        (staged_dir / "reads.pod5").write_text("old-data")

        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": str(source_dir),
                "reference_genome": ["mm39"],
            },
            "skill": "analyze_local_sample",
            "model": "default",
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "should-not-submit",
            "work_directory": "/work/should-not-submit",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
               patch("cortex.workflow_submission.AGOUTIC_DATA", tmp_path), \
               patch("cortex.remote_orchestration.AGOUTIC_DATA", tmp_path):
            await submit_job_after_approval("proj-bg", gate.id)

        assert mock_client.call_tool.call_count == 0

        sess = session_factory()
        gates = sess.query(ProjectBlock).filter(ProjectBlock.type == "APPROVAL_GATE").all()
        decision_gate = next(block for block in gates if block.id != gate.id)
        decision_payload = get_block_payload(decision_gate)
        assert decision_payload["gate_action"] == "local_sample_existing"
        workflow_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "WORKFLOW_PLAN").one()
        workflow_payload = get_block_payload(workflow_block)
        assert workflow_payload["steps"][0]["status"] == "FOLLOW_UP"
        assert workflow_payload["steps"][0]["decision_gate_id"] == decision_gate.id
        sess.close()

    @pytest.mark.asyncio
    async def test_remote_stage_only_skips_local_sample_staging_even_with_existing_local_path(self, session_factory, seed_data, tmp_path):
        source_dir = tmp_path / "incoming" / "pod5"
        source_dir.mkdir(parents=True)
        (source_dir / "reads.pod5").write_text("pod5-data")

        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "gate_action": "remote_stage",
            "skill": "analyze_local_sample",
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": str(source_dir),
                "reference_genome": ["mm39"],
                "execution_mode": "slurm",
                "ssh_profile_nickname": "hpc3",
                "remote_action": "stage_only",
                "gate_action": "remote_stage",
                "remote_base_path": "/remote/u1/agoutic",
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "remote_data_path": "/remote/u1/agoutic/data/fp1",
            "data_cache_status": "staged",
            "reference_cache_statuses": {"mm39": "reused"},
            "reference_asset_evidence": {
                "mm39": {
                    "requires_kallisto": True,
                    "missing_required_assets": [],
                    "all_required_present": True,
                }
            },
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
               patch("cortex.workflow_submission.AGOUTIC_DATA", tmp_path), \
               patch("cortex.remote_orchestration.AGOUTIC_DATA", tmp_path), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        assert mock_client.call_tool.await_count == 1
        assert mock_client.call_tool.call_args.args[0] == "stage_remote_sample"
        submitted = mock_client.call_tool.call_args.kwargs
        assert submitted["input_directory"] == str(source_dir)

        staged_dir = tmp_path / "users" / "bguser" / "data" / "jamshid"
        assert not staged_dir.exists()

        sess = session_factory()
        assert sess.query(ProjectBlock).filter(ProjectBlock.type == "EXECUTION_JOB").all() == []
        gates = sess.query(ProjectBlock).filter(ProjectBlock.type == "APPROVAL_GATE").all()
        assert len(gates) == 1
        sess.close()

    @pytest.mark.asyncio
    async def test_polls_job_after_submission(self, session_factory, seed_data):
        """After successful submission, creates a poll task."""
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "test", "mode": "DNA",
                "input_directory": "/data/test",
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "poll-uuid", "work_directory": "/work/poll",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        # asyncio.create_task should have been called with poll_job_status coroutine
        assert mock_aio.create_task.called

    @pytest.mark.asyncio
    async def test_resolves_slurm_profile_nickname_before_submission(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "remote-test",
                "mode": "CDNA",
                "input_directory": "/data/test",
                "reference_genome": ["mm39"],
                "execution_mode": "slurm",
                "ssh_profile_nickname": "hpc3",
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "remote-uuid", "work_directory": "/work/remote",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        submitted = mock_client.call_tool.call_args.kwargs
        assert submitted["execution_mode"] == "slurm"
        assert submitted["ssh_profile_id"] == "profile-123"

    @pytest.mark.asyncio
    async def test_stage_only_remote_request_calls_stage_remote_sample(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "gate_action": "remote_stage",
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": "/data/pod5",
                "reference_genome": ["mm39"],
                "execution_mode": "slurm",
                "ssh_profile_nickname": "hpc3",
                "remote_action": "stage_only",
                "gate_action": "remote_stage",
                "remote_base_path": "/remote/u1/agoutic",
                "cache_preflight": {
                    "status": "ready",
                    "reference_actions": [{"reference_id": "mm39", "action": "reuse"}],
                    "data_action": {"action": "stage"},
                },
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "remote_data_path": "/remote/u1/agoutic/data/fp1",
            "data_cache_status": "reused",
            "reference_cache_statuses": {"mm39": "reused"},
            "reference_asset_evidence": {
                "mm39": {
                    "requires_kallisto": True,
                    "missing_required_assets": [],
                    "all_required_present": True,
                }
            },
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        assert mock_client.call_tool.await_count == 1
        assert mock_client.call_tool.call_args.args[0] == "stage_remote_sample"
        submitted = mock_client.call_tool.call_args.kwargs
        assert submitted["ssh_profile_id"] == "profile-123"
        assert submitted["sample_name"] == "Jamshid"

        sess = session_factory()
        workflow_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "WORKFLOW_PLAN").one()
        payload = get_block_payload(workflow_block)
        assert payload["workflow_type"] == "remote_sample_intake"
        assert payload["steps"][1]["status"] == "COMPLETED"
        assert payload["steps"][2]["status"] == "COMPLETED"
        staging_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "STAGING_TASK").one()
        staging_payload = get_block_payload(staging_block)
        assert staging_block.status == "DONE"
        assert staging_payload["remote_data_path"] == "/remote/u1/agoutic/data/fp1"
        assert staging_payload["reference_asset_evidence"]["mm39"]["all_required_present"] is True
        assert staging_payload["stage_parts"]["references"]["status"] == "COMPLETED"
        assert staging_payload["stage_parts"]["references"]["progress_percent"] == 100
        assert "already staged" in staging_payload["stage_parts"]["references"]["message"].lower()
        assert staging_payload["stage_parts"]["data"]["status"] == "COMPLETED"
        assert staging_payload["stage_parts"]["data"]["progress_percent"] == 100
        assert sess.query(ProjectBlock).filter(ProjectBlock.type == "EXECUTION_JOB").all() == []
        sess.close()

    @pytest.mark.asyncio
    async def test_stage_only_remote_request_forces_slurm_even_without_execution_mode(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "gate_action": "remote_stage",
            "skill": "analyze_local_sample",
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": "/media/backup_disk/agoutic_root/testdata/CDNA/pod5",
                "reference_genome": ["mm39"],
                "ssh_profile_nickname": "hpc3",
                "remote_action": "stage_only",
                "gate_action": "remote_stage",
                "remote_base_path": "/remote/u1/agoutic",
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "remote_data_path": "/remote/u1/agoutic/data/fp1",
            "data_cache_status": "staged",
            "reference_cache_statuses": {"mm39": "reused"},
            "reference_asset_evidence": {
                "mm39": {
                    "requires_kallisto": True,
                    "missing_required_assets": [],
                    "all_required_present": True,
                }
            },
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        assert mock_client.call_tool.await_count == 1
        assert mock_client.call_tool.call_args.args[0] == "stage_remote_sample"
        submitted = mock_client.call_tool.call_args.kwargs
        assert submitted["ssh_profile_id"] == "profile-123"
        assert "execution_mode" not in submitted

        sess = session_factory()
        assert sess.query(ProjectBlock).filter(ProjectBlock.type == "EXECUTION_JOB").all() == []
        assert sess.query(ProjectBlock).filter(ProjectBlock.type == "STAGING_TASK").count() == 1
        sess.close()

    @pytest.mark.asyncio
    async def test_remote_stage_only_request_with_profile_is_normalized_before_remote_prep(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "gate_action": "remote_stage",
            "skill": "analyze_local_sample",
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": "/media/backup_disk/agoutic_root/testdata/CDNA/pod5",
                "reference_genome": ["mm39"],
                "ssh_profile_nickname": "hpc3",
                "remote_action": "stage_only",
                "gate_action": "remote_stage",
            },
        })

        seen_execution_modes = []
        real_prepare = __import__("cortex.workflow_submission", fromlist=["_prepare_remote_execution_params"])._prepare_remote_execution_params

        async def _wrapped_prepare(session, project_id, owner_id, params):
            seen_execution_modes.append((params.get("execution_mode") or "local").strip().lower())
            prepared = await real_prepare(session, project_id, owner_id, params)
            seen_execution_modes.append((prepared.get("execution_mode") or "local").strip().lower())
            return prepared

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "remote_data_path": "/remote/u1/agoutic/data/fp1",
            "data_cache_status": "staged",
            "reference_cache_statuses": {"mm39": "reused"},
            "reference_asset_evidence": {"mm39": {"all_required_present": True}},
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission._prepare_remote_execution_params", new=_wrapped_prepare), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[])), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        assert seen_execution_modes[0] == "local"
        assert seen_execution_modes[1] == "slurm"
        assert mock_client.call_tool.call_args.args[0] == "stage_remote_sample"

    @pytest.mark.asyncio
    async def test_remote_stage_only_request_never_falls_through_to_submit_job(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "gate_action": "remote_stage",
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": "/data/pod5",
                "reference_genome": ["mm39"],
                "execution_mode": "local",
                "remote_action": "stage_only",
                "gate_action": "remote_stage",
            },
        })

        async def _broken_prepare(session, project_id, owner_id, params):
            broken = dict(params)
            broken["execution_mode"] = "local"
            return broken

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={
            "run_uuid": "should-not-submit",
            "work_directory": "/work/should-not-submit",
        })

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission._prepare_remote_execution_params", new=_broken_prepare), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        assert mock_client.call_tool.call_count == 0
        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(ProjectBlock.type == "EXECUTION_JOB").all()
        assert job_blocks
        payload = get_block_payload(job_blocks[-1])
        assert "SLURM execution requires an SSH profile" in payload.get("error", "")
        sess.close()


    
    @pytest.mark.asyncio
    async def test_stage_only_remote_failure_creates_failed_staging_task(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "gate_action": "remote_stage",
            "extracted_params": {
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "input_directory": "/data/pod5",
                "reference_genome": ["mm39"],
                "execution_mode": "slurm",
                "ssh_profile_nickname": "hpc3",
                "remote_action": "stage_only",
                "gate_action": "remote_stage",
                "remote_base_path": "/remote/u1/agoutic",
                "cache_preflight": {
                    "status": "ready",
                    "reference_actions": [{"reference_id": "mm39", "action": "reuse"}],
                    "data_action": {"action": "stage"},
                },
            },
        })

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("Failed to stage remote sample: HTTP 400 from http://localhost:8003/remote/stage - {\"detail\":\"Local source path does not exist on the Launchpad server: /data/pod5\"}"))

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        staging_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "STAGING_TASK").one()
        staging_payload = get_block_payload(staging_block)
        assert staging_block.status == "FAILED"
        assert "Local source path does not exist" in staging_payload["error"]
        assert staging_payload["stage_parts"]["references"]["status"] == "COMPLETED"
        assert staging_payload["stage_parts"]["data"]["status"] == "FAILED"
        assert "Sample data staging failed" in staging_payload["stage_parts"]["data"]["message"]
        assert sess.query(ProjectBlock).filter(ProjectBlock.type == "EXECUTION_JOB").all() == []
        workflow_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "WORKFLOW_PLAN").one()
        workflow_payload = get_block_payload(workflow_block)
        assert workflow_payload["steps"][1]["status"] == "FAILED"
        sess.close()

    @pytest.mark.asyncio
    async def test_submit_rejects_requested_execution_mode_mismatch(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "mode-mismatch",
                "mode": "CDNA",
                "input_directory": "/data/pod5",
                "reference_genome": ["mm39"],
                "execution_mode": "slurm",
                "requested_execution_mode": "local",
            },
        })

        async def _passthrough_prepare(session, project_id, owner_id, params):
            return dict(params)

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={"run_uuid": "should-not-run"})

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission._prepare_remote_execution_params", new=_passthrough_prepare), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        assert mock_client.call_tool.call_count == 0
        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(ProjectBlock.type == "EXECUTION_JOB").all()
        assert job_blocks
        payload = get_block_payload(job_blocks[-1])
        assert "Requested execution_mode=local" in payload.get("error", "")
        sess.close()

    @pytest.mark.asyncio
    async def test_slurm_submission_without_run_uuid_marks_stage_input_failed(self, session_factory, seed_data):
        gate = _create_gate(session_factory, "proj-bg", "u-bg", {
            "extracted_params": {
                "sample_name": "no-run-uuid",
                "mode": "CDNA",
                "input_directory": "/data/pod5",
                "reference_genome": ["mm39"],
                "execution_mode": "slurm",
                "ssh_profile_nickname": "hpc3",
                "remote_base_path": "/remote/u1/agoutic",
                "cache_preflight": {
                    "status": "ready",
                    "reference_actions": [{"reference_id": "mm39", "action": "reuse"}],
                    "data_action": {"action": "stage"},
                },
            },
        })

        async def _passthrough_prepare(session, project_id, owner_id, params):
            return dict(params)

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={})

        with _patch_session(session_factory), \
             patch("cortex.workflow_submission._prepare_remote_execution_params", new=_passthrough_prepare), \
             patch("cortex.workflow_submission.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.workflow_submission.MCPHttpClient", return_value=mock_client), \
             patch("cortex.workflow_submission._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.workflow_submission.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        workflow_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "WORKFLOW_PLAN").one()
        workflow_payload = get_block_payload(workflow_block)
        step_status = {step["id"]: step["status"] for step in workflow_payload["steps"]}
        assert step_status["check_remote_stage"] == "COMPLETED"
        assert step_status["stage_input"] == "FAILED"
        assert step_status["run_dogme"] == "FAILED"
        sess.close()


@pytest.mark.asyncio
async def test_auto_execute_plan_steps_creates_gate_for_waiting_remote_stage_plan(session_factory, seed_data):
    sess = session_factory()
    workflow_block = _create_block_internal(
        sess,
        "proj-bg",
        "WORKFLOW_PLAN",
        {
            "title": "Stage remote sample for Jamshid",
            "status": "WAITING_APPROVAL",
            "current_step_id": "approve_remote_stage",
            "steps": [
                {
                    "id": "check_remote_profile_auth",
                    "kind": "CHECK_REMOTE_PROFILE_AUTH",
                    "title": "Check remote profile authorization for Jamshid",
                    "status": "COMPLETED",
                },
                {
                    "id": "approve_remote_stage",
                    "kind": "REQUEST_APPROVAL",
                    "title": "Approve remote staging for Jamshid",
                    "status": "WAITING_APPROVAL",
                    "requires_approval": True,
                }
            ],
        },
        status="PENDING",
        owner_id="u-bg",
    )
    workflow_block_id = workflow_block.id
    sess.commit()
    sess.close()

    user = type("UserStub", (), {"id": "u-bg"})()

    with _patch_session(session_factory), \
         patch("cortex.chat_downloads.AgentEngine") as mock_engine_cls, \
         patch("cortex.plan_executor.execute_plan", new=AsyncMock(return_value=None)), \
         patch(
             "cortex.job_parameters.extract_job_parameters_from_conversation",
             new=AsyncMock(return_value={
                 "sample_name": "Jamshid",
                 "execution_mode": "slurm",
                 "gate_action": "remote_stage",
                 "remote_action": "stage_only",
                 "cache_preflight": {"status": "needs_remote_base_path"},
             }),
         ):
        mock_engine = MagicMock()
        mock_engine.model_name = "test-model"
        mock_engine_cls.return_value = mock_engine
        await _auto_execute_plan_steps("proj-bg", workflow_block_id, user, "test-model")

    sess = session_factory()
    gates = sess.query(ProjectBlock).filter(ProjectBlock.type == "APPROVAL_GATE").all()
    assert len(gates) == 1
    gate_payload = get_block_payload(gates[0])
    assert gates[0].parent_id == workflow_block_id
    assert gate_payload["gate_action"] == "remote_stage"
    assert gate_payload["skill"] == "remote_execution"
    sess.close()


@pytest.mark.asyncio
async def test_auto_execute_plan_steps_executes_remote_stage_plan_to_approval(session_factory, seed_data, tmp_path):
    source_dir = tmp_path / "pod5"
    source_dir.mkdir()
    (source_dir / "read_001.pod5").write_text("pod5")

    sess = session_factory()
    workflow_block = _create_block_internal(
        sess,
        "proj-bg",
        "WORKFLOW_PLAN",
        _template_remote_stage_workflow({
            "sample_name": "Jamshid",
            "input_directory": str(source_dir),
        }),
        status="PENDING",
        owner_id="u-bg",
    )
    workflow_block_id = workflow_block.id
    sess.commit()
    sess.close()

    user = type("UserStub", (), {"id": "u-bg"})()

    with _patch_session(session_factory), \
         patch("cortex.chat_downloads.AgentEngine") as mock_engine_cls, \
         patch(
             "cortex.plan_executor._call_mcp_tool",
             new=AsyncMock(return_value={"success": True, "file_count": 1, "files": [{"name": "read_001.pod5"}]}),
         ), \
         patch(
             "cortex.job_parameters.extract_job_parameters_from_conversation",
             new=AsyncMock(return_value={
                 "sample_name": "Jamshid",
                 "execution_mode": "slurm",
                 "ssh_profile_id": "profile-123",
                 "ssh_profile_nickname": "hpc3",
                 "remote_base_path": "/remote/u1/agoutic",
                 "gate_action": "remote_stage",
             }),
         ), \
         patch("cortex.chat_approval._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
         patch("cortex.chat_approval._list_user_ssh_profiles", new=AsyncMock(return_value=[{
             "id": "profile-123",
             "nickname": "hpc3",
             "ssh_host": "hpc3.example.edu",
             "auth_method": "key_file",
             "local_username": "alim",
         }])), \
         patch("cortex.chat_approval._get_ssh_profile_auth_session", new=AsyncMock(return_value={"active": True})):
        mock_engine = MagicMock()
        mock_engine.model_name = "test-model"
        mock_engine_cls.return_value = mock_engine
        await _auto_execute_plan_steps("proj-bg", workflow_block_id, user, "test-model")

    sess = session_factory()
    workflow = sess.query(ProjectBlock).filter(ProjectBlock.id == workflow_block_id).one()
    workflow_payload = get_block_payload(workflow)
    assert workflow_payload["steps"][0]["status"] == "COMPLETED"
    assert workflow_payload["steps"][1]["status"] == "COMPLETED"
    assert workflow_payload["steps"][2]["status"] == "COMPLETED"
    assert workflow_payload["steps"][3]["status"] == "COMPLETED"
    assert workflow_payload["steps"][4]["status"] == "WAITING_APPROVAL"

    gate = sess.query(ProjectBlock).filter(ProjectBlock.type == "APPROVAL_GATE").one()
    gate_payload = get_block_payload(gate)
    assert gate_payload["gate_action"] == "remote_stage"
    assert gate_payload["skill"] == "remote_execution"
    assert "stage these input files" in gate_payload["label"]
    sess.close()


@pytest.mark.asyncio
async def test_auto_execute_plan_steps_blocks_when_remote_profile_locked(session_factory, seed_data):
    sess = session_factory()
    workflow_block = _create_block_internal(
        sess,
        "proj-bg",
        "WORKFLOW_PLAN",
        {
            "title": "Stage remote sample for Jamshid",
            "status": "RUNNING",
            "current_step_id": "check_remote_profile_auth",
            "steps": [
                {
                    "id": "check_remote_profile_auth",
                    "kind": "CHECK_REMOTE_PROFILE_AUTH",
                    "title": "Check remote profile authorization for Jamshid",
                    "status": "RUNNING",
                },
                {
                    "id": "approve_remote_stage",
                    "kind": "REQUEST_APPROVAL",
                    "title": "Approve remote staging for Jamshid",
                    "status": "PENDING",
                    "requires_approval": True,
                },
            ],
        },
        status="PENDING",
        owner_id="u-bg",
    )
    workflow_block_id = workflow_block.id
    sess.commit()
    sess.close()

    user = type("UserStub", (), {"id": "u-bg"})()

    with _patch_session(session_factory), \
         patch("cortex.chat_downloads.AgentEngine") as mock_engine_cls, \
         patch("cortex.plan_executor.execute_plan", new=AsyncMock(return_value=None)), \
         patch(
             "cortex.job_parameters.extract_job_parameters_from_conversation",
             new=AsyncMock(return_value={
                 "sample_name": "Jamshid",
                 "execution_mode": "slurm",
                 "ssh_profile_id": "profile-123",
                 "ssh_profile_nickname": "hpc3",
                 "remote_base_path": "/remote/u1/agoutic",
                 "gate_action": "remote_stage",
             }),
         ), \
         patch("cortex.chat_approval._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
         patch("cortex.chat_approval._list_user_ssh_profiles", new=AsyncMock(return_value=[{
             "id": "profile-123",
             "nickname": "hpc3",
             "ssh_host": "hpc3.example.edu",
             "auth_method": "key_file",
             "local_username": "alim",
         }])), \
         patch("cortex.chat_approval._get_ssh_profile_auth_session", new=AsyncMock(return_value={"active": False})):
        mock_engine = MagicMock()
        mock_engine.model_name = "test-model"
        mock_engine_cls.return_value = mock_engine
        await _auto_execute_plan_steps("proj-bg", workflow_block_id, user, "test-model")

    sess = session_factory()
    workflow = sess.query(ProjectBlock).filter(ProjectBlock.id == workflow_block_id).one()
    workflow_payload = get_block_payload(workflow)
    assert workflow_payload["status"] == "FOLLOW_UP"
    assert workflow_payload["steps"][0]["status"] == "FOLLOW_UP"
    assert "locked" in (workflow_payload["steps"][0].get("error") or "")

    gates = sess.query(ProjectBlock).filter(ProjectBlock.type == "APPROVAL_GATE").all()
    assert gates == []

    notes = sess.query(ProjectBlock).filter(ProjectBlock.type == "AGENT_PLAN").all()
    assert any("unlock" in (get_block_payload(note).get("markdown", "").lower()) for note in notes)
    sess.close()


@pytest.mark.asyncio
async def test_ensure_workflow_plan_approval_gate_builds_reconcile_specific_payload(session_factory, seed_data):
    preflight_payload = {
        "success": True,
        "status": "preflight_ready",
        "message": "Reconcile preflight validation passed. Ready for approval.",
        "reference": "mm39",
        "gtf": {
            "path": "/refs/mm39.gtf",
            "source": "default",
        },
        "inputs": {
            "count": 2,
            "bams": [
                {"sample": "C2C12r1", "reference": "mm39", "path": "/proj/workflow2/annot/C2C12r1.mm39.annotated.bam"},
                {"sample": "C2C12r3", "reference": "mm39", "path": "/proj/workflow3/annot/C2C12r3.mm39.annotated.bam"},
            ],
        },
        "outputs": {
            "output_prefix": "reconciled",
            "output_root": "/proj/reconcile",
            "artifacts": [],
        },
    }

    sess = session_factory()
    workflow_block = _create_block_internal(
        sess,
        "proj-bg",
        "WORKFLOW_PLAN",
        {
            "plan_type": "reconcile_bams",
            "skill": "reconcile_bams",
            "status": "WAITING_APPROVAL",
            "current_step_id": "approve_reconcile",
            "output_prefix": "reconciled",
            "output_directory": "/proj/reconcile",
            "steps": [
                {
                    "id": "preflight_reconcile",
                    "kind": "CHECK_EXISTING",
                    "title": "Validate reconcile inputs",
                    "status": "COMPLETED",
                    "result": [
                        {
                            "tool": "run_allowlisted_script",
                            "result": {
                                "script_id": "reconcile_bams/reconcile_bams",
                                "stdout": json.dumps(preflight_payload),
                            },
                        }
                    ],
                },
                {
                    "id": "approve_reconcile",
                    "kind": "REQUEST_APPROVAL",
                    "title": "Approve reconcile BAM execution",
                    "status": "WAITING_APPROVAL",
                    "requires_approval": True,
                    "depends_on": ["preflight_reconcile"],
                },
                {
                    "id": "run_reconcile",
                    "kind": "RUN_SCRIPT",
                    "title": "Run reconcile BAM script",
                    "status": "PENDING",
                    "requires_approval": True,
                    "depends_on": ["approve_reconcile"],
                    "tool_calls": [
                        {
                            "source_key": "launchpad",
                            "tool": "run_allowlisted_script",
                            "params": {
                                "script_id": "reconcile_bams/reconcile_bams",
                                "script_args": [
                                    "--workflow-dir", "/proj/workflow2",
                                    "--workflow-dir", "/proj/workflow3",
                                    "--output-dir", "/proj/reconcile",
                                    "--output-prefix", "reconciled",
                                    "--json",
                                ],
                            },
                        }
                    ],
                },
            ],
        },
        status="PENDING",
        owner_id="u-bg",
    )

    gate = await _ensure_workflow_plan_approval_gate(
        sess,
        workflow_block,
        owner_id="u-bg",
        model_name="test-model",
    )

    gate_payload = get_block_payload(gate)
    assert gate_payload["gate_action"] == "reconcile_bams"
    assert gate_payload["skill"] == "reconcile_bams"
    assert gate_payload["extracted_params"]["run_type"] == "script"
    assert gate_payload["extracted_params"]["script_id"] == "reconcile_bams/reconcile_bams"
    assert gate_payload["extracted_params"]["reference"] == "mm39"
    assert gate_payload["extracted_params"]["annotation_gtf"] == "/refs/mm39.gtf"
    assert gate_payload["extracted_params"]["bam_count"] == 2
    sess.close()


# ---------------------------------------------------------------------------
# poll_job_status
# ---------------------------------------------------------------------------

class TestPollJobStatus:
    """Tests for the poll_job_status background task."""

    @pytest.mark.anyio
    async def test_updates_block_on_completion(self, session_factory, seed_data):
        """Polling updates the EXECUTION_JOB block and stops on COMPLETED."""
        # Create an EXECUTION_JOB block
        sess = session_factory()
        job_block = _create_block_internal(
            sess, "proj-bg", "EXECUTION_JOB",
            {
                "run_uuid": "poll-test",
                "job_status": {"status": "PENDING", "progress_percent": 0},
                "logs": [],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        sess.close()


        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[
            # check_nextflow_status
            {"status": "COMPLETED", "progress_percent": 100, "message": "Done"},
            # get_job_logs
            {"logs": [{"message": "All tasks done"}]},
        ])

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_client), \
             patch("cortex.job_polling.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("cortex.job_polling._auto_trigger_analysis", new_callable=AsyncMock):
            # Override sleep to not actually wait
            mock_sleep.return_value = None
            await poll_job_status("proj-bg", job_block.id, "poll-test")

        sess = session_factory()
        updated = sess.query(ProjectBlock).filter(ProjectBlock.id == job_block.id).first()
        payload = get_block_payload(updated)
        assert updated.status == "DONE"
        assert payload["job_status"]["status"] == "COMPLETED"
        sess.close()

    @pytest.mark.anyio
    async def test_updates_block_on_failure(self, session_factory, seed_data):
        """Polling updates the EXECUTION_JOB block and stops on FAILED."""
        sess = session_factory()
        job_block = _create_block_internal(
            sess, "proj-bg", "EXECUTION_JOB",
            {
                "run_uuid": "fail-test",
                "job_status": {"status": "RUNNING", "progress_percent": 50},
                "logs": [],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        sess.close()


        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[
            {"status": "FAILED", "progress_percent": 50, "message": "OOM"},
            {"logs": [{"message": "Killed"}]},
        ])

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_client), \
             patch("cortex.job_polling.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.return_value = None
            await poll_job_status("proj-bg", job_block.id, "fail-test")

        sess = session_factory()
        updated = sess.query(ProjectBlock).filter(ProjectBlock.id == job_block.id).first()
        assert updated.status == "FAILED"
        sess.close()

    @pytest.mark.anyio
    async def test_triggers_auto_analysis_on_completion(self, session_factory, seed_data):
        """On COMPLETED status, _auto_trigger_analysis is called."""
        sess = session_factory()
        job_block = _create_block_internal(
            sess, "proj-bg", "EXECUTION_JOB",
            {
                "run_uuid": "auto-test",
                "work_directory": "/work/auto",
                "sample_name": "sample1",
                "mode": "DNA",
                "model": "default",
                "job_status": {"status": "PENDING"},
                "logs": [],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        sess.close()


        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[
            {"status": "COMPLETED", "progress_percent": 100},
            {"logs": []},
        ])

        mock_auto = AsyncMock()

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_client), \
             patch("cortex.job_polling.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("cortex.job_polling._auto_trigger_analysis", mock_auto):
            mock_sleep.return_value = None
            await poll_job_status("proj-bg", job_block.id, "auto-test")

        assert mock_auto.called
        call_args = mock_auto.call_args
        assert call_args[0][0] == "proj-bg"  # project_id
        assert call_args[0][1] == "auto-test"  # run_uuid

    @pytest.mark.anyio
    async def test_completes_reconcile_run_script_and_resumes_plan(self, session_factory, seed_data):
        sess = session_factory()
        workflow_block = _create_block_internal(
            sess,
            "proj-bg",
            "WORKFLOW_PLAN",
            {
                "plan_type": "reconcile_bams",
                "status": "RUNNING",
                "run_uuid": "script-test",
                "current_step_id": "run_reconcile",
                "steps": [
                    {
                        "id": "approve_reconcile",
                        "kind": "REQUEST_APPROVAL",
                        "status": "COMPLETED",
                    },
                    {
                        "id": "run_reconcile",
                        "kind": "RUN_SCRIPT",
                        "status": "RUNNING",
                    },
                    {
                        "id": "write_summary",
                        "kind": "WRITE_SUMMARY",
                        "status": "PENDING",
                        "depends_on": ["run_reconcile"],
                    },
                ],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        job_block = _create_block_internal(
            sess,
            "proj-bg",
            "EXECUTION_JOB",
            {
                "run_uuid": "script-test",
                "work_directory": "/work/script-test",
                "sample_name": "reconciled",
                "mode": "RNA",
                "run_type": "script",
                "model": "default",
                "workflow_plan_block_id": workflow_block.id,
                "job_status": {"status": "PENDING"},
                "logs": [],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        sess.close()

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[
            {"status": "COMPLETED", "progress_percent": 100, "message": "Done"},
            {"logs": []},
        ])

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_client), \
             patch("cortex.job_polling.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("cortex.job_polling._auto_trigger_analysis", new_callable=AsyncMock) as mock_auto, \
             patch("cortex.job_polling.asyncio.create_task") as mock_create_task, \
             patch("cortex.chat_downloads.AgentEngine") as mock_engine_cls, \
             patch("cortex.plan_executor.execute_plan", new=AsyncMock(return_value=None)):
            mock_sleep.return_value = None
            mock_create_task.side_effect = lambda coro: (coro.close(), MagicMock())[1]
            mock_engine = MagicMock()
            mock_engine.model_name = "default"
            mock_engine_cls.return_value = mock_engine
            await poll_job_status("proj-bg", job_block.id, "script-test")

        assert mock_auto.await_count == 0
        assert mock_create_task.called

        sess = session_factory()
        workflow = sess.query(ProjectBlock).filter(ProjectBlock.id == workflow_block.id).one()
        workflow_payload = get_block_payload(workflow)
        run_step = next(step for step in workflow_payload["steps"] if step["id"] == "run_reconcile")
        assert run_step["status"] == "COMPLETED"
        sess.close()

    @pytest.mark.anyio
    async def test_waits_for_local_result_copy_before_auto_analysis(self, session_factory, seed_data):
        sess = session_factory()
        job_block = _create_block_internal(
            sess, "proj-bg", "EXECUTION_JOB",
            {
                "run_uuid": "copyback-test",
                "work_directory": "/work/copyback",
                "sample_name": "sample-copy",
                "mode": "DNA",
                "model": "default",
                "job_status": {"status": "PENDING"},
                "logs": [],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        sess.close()

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[
            {"status": "COMPLETED", "progress_percent": 95, "result_destination": "local", "transfer_state": "downloading_outputs", "work_directory": "/local/work/copyback"},
            {"logs": []},
            {"status": "COMPLETED", "progress_percent": 100, "result_destination": "local", "transfer_state": "outputs_downloaded", "work_directory": "/local/work/copyback"},
            {"logs": []},
        ])

        mock_auto = AsyncMock()

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_client), \
             patch("cortex.job_polling.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("cortex.job_polling._auto_trigger_analysis", mock_auto):
            mock_sleep.return_value = None
            await poll_job_status("proj-bg", job_block.id, "copyback-test")

        assert mock_auto.await_count == 1

        sess = session_factory()
        updated = sess.query(ProjectBlock).filter(ProjectBlock.id == job_block.id).first()
        payload = get_block_payload(updated)
        assert updated.status == "DONE"
        assert payload["job_status"]["transfer_state"] == "outputs_downloaded"
        assert payload["work_directory"] == "/local/work/copyback"
        sess.close()


def test_completed_job_results_ready_requires_outputs_downloaded_for_local_destinations():
    assert _completed_job_results_ready({"status": "COMPLETED", "result_destination": "local", "transfer_state": "outputs_downloaded"}) is True
    assert _completed_job_results_ready({"status": "COMPLETED", "result_destination": "both", "transfer_state": "downloading_outputs"}) is False
    assert _completed_job_results_ready({"status": "COMPLETED", "result_destination": "remote", "transfer_state": "none"}) is True


def test_resolved_job_work_directory_prefers_status_data_over_existing_payload():
    assert _resolved_job_work_directory(
        "/remote/project/workflow1",
        {"work_directory": "/local/project/workflow1"},
    ) == "/local/project/workflow1"


def test_resolved_job_work_directory_falls_back_to_existing_payload():
    assert _resolved_job_work_directory(
        "/local/project/workflow1",
        {"status": "RUNNING"},
    ) == "/local/project/workflow1"

    @pytest.mark.asyncio
    async def test_handles_poll_errors_gracefully(self, session_factory, seed_data):
        """Polling continues after non-fatal errors."""
        sess = session_factory()
        job_block = _create_block_internal(
            sess, "proj-bg", "EXECUTION_JOB",
            {
                "run_uuid": "err-test",
                "job_status": {"status": "PENDING"},
                "logs": [],
            },
            status="RUNNING",
            owner_id="u-bg",
        )
        sess.close()


        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("Network error")
            # Third call onwards: return completed
            if "check_nextflow_status" in str(args):
                return {"status": "COMPLETED", "progress_percent": 100}
            return {"logs": []}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[
            # First poll pair: error
            ConnectionError("Network"),
            # Second poll pair: success
            {"status": "COMPLETED", "progress_percent": 100},
            {"logs": []},
        ])
        # Make connect raise on first call, succeed on second
        connect_count = [0]
        original_connect = AsyncMock()

        async def connect_side_effect():
            connect_count[0] += 1
            if connect_count[0] == 1:
                raise ConnectionError("First connect failure")

        # Actually, let's simplify - just have the first call_tool raise
        mock_client.connect = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[
            ConnectionError("First poll error"),  # Will be caught
        ])

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_client), \
               patch("cortex.job_polling.asyncio") as mock_aio:
            mock_aio.sleep = AsyncMock()
            # This should not raise - errors are caught
            await poll_job_status("proj-bg", job_block.id, "err-test")

        # If we got here without exception, error handling works


# ---------------------------------------------------------------------------
# _auto_trigger_analysis
# ---------------------------------------------------------------------------

class TestAutoTriggerAnalysis:
    """Tests for the _auto_trigger_analysis function."""

    @pytest.mark.asyncio
    async def test_creates_analysis_block_with_llm(self, session_factory, seed_data):
        """Successful LLM call creates an AGENT_PLAN block with analysis."""

        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(side_effect=[
            # get_analysis_summary
            {
                "file_summary": {"csv_files": [{"name": "final_stats.csv", "size": 1024}]},
                "all_file_counts": {"total_files": 5, "csv_count": 2},
            },
            # parse_csv_file (for final_stats)
            {
                "data": [{"sample": "s1", "reads": 1000}],
                "columns": ["sample", "reads"],
                "total_rows": 1,
            },
        ])

        mock_engine = MagicMock()
        mock_engine.model_name = "test-model"
        mock_engine.think = MagicMock(return_value=(
            "The DNA analysis shows 1000 reads.",
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        ))

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://analyzer:8002"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_mcp), \
               patch("cortex.job_polling.AgentEngine", return_value=mock_engine), \
               patch("cortex.job_polling.run_in_threadpool", _mock_run_in_threadpool), \
               patch("cortex.job_polling.save_conversation_message", new_callable=AsyncMock):
            await _auto_trigger_analysis(
                "proj-bg", "uuid-123",
                {"sample_name": "s1", "mode": "DNA", "model": "default",
                 "work_directory": "/work/test"},
                "u-bg",
            )

        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-bg",
            ProjectBlock.type == "AGENT_PLAN",
        ).all()
        assert len(blocks) >= 1
        latest = blocks[-1]
        payload = get_block_payload(latest)
        assert "1000 reads" in payload.get("markdown", "")
        assert payload.get("skill") == "run_dogme_dna"
        assert payload.get("tokens", {}).get("total_tokens") == 150
        sess.close()

    @pytest.mark.asyncio
    async def test_llm_failure_uses_static_template(self, session_factory, seed_data):
        """When LLM fails, falls back to static summary template."""

        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={
            "file_summary": {"csv_files": []},
            "all_file_counts": {"total_files": 3, "csv_count": 0},
        })

        mock_engine = MagicMock()
        mock_engine.model_name = "test-model"
        mock_engine.think = MagicMock(side_effect=RuntimeError("LLM down"))

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://analyzer:8002"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_mcp), \
               patch("cortex.job_polling.AgentEngine", return_value=mock_engine), \
               patch("cortex.job_polling.run_in_threadpool", _mock_run_in_threadpool), \
               patch("cortex.job_polling.save_conversation_message", new_callable=AsyncMock):
            await _auto_trigger_analysis(
                "proj-bg", "uuid-456",
                {"sample_name": "s2", "mode": "RNA", "model": "default",
                 "work_directory": "/work/rna"},
                "u-bg",
            )

        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-bg",
            ProjectBlock.type == "AGENT_PLAN",
        ).all()
        assert len(blocks) >= 1
        payload = get_block_payload(blocks[-1])
        # Static template should mention sample name
        assert "s2" in payload.get("markdown", "")
        assert payload.get("model") == "system"
        sess.close()

    @pytest.mark.asyncio
    async def test_analyzer_connection_failure(self, session_factory, seed_data):
        """When analyzer MCP connection fails, still creates analysis block."""

        mock_mcp = AsyncMock()
        mock_mcp.connect = AsyncMock(side_effect=ConnectionError("No analyzer"))

        mock_engine = MagicMock()
        mock_engine.model_name = "test-model"
        mock_engine.think = MagicMock(side_effect=RuntimeError("No data"))

        with _patch_session(session_factory), \
             patch("cortex.job_polling.get_service_url", return_value="http://analyzer:8002"), \
             patch("cortex.job_polling.MCPHttpClient", return_value=mock_mcp), \
               patch("cortex.job_polling.AgentEngine", return_value=mock_engine), \
               patch("cortex.job_polling.run_in_threadpool", _mock_run_in_threadpool), \
               patch("cortex.job_polling.save_conversation_message", new_callable=AsyncMock):
            await _auto_trigger_analysis(
                "proj-bg", "uuid-789",
                {"sample_name": "s3", "mode": "CDNA", "model": "default",
                 "work_directory": "/work/cdna"},
                "u-bg",
            )

        sess = session_factory()
        blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-bg",
            ProjectBlock.type == "AGENT_PLAN",
        ).all()
        # Should still have created something (static fallback)
        assert len(blocks) >= 1
        sess.close()

    @pytest.mark.asyncio
    async def test_mode_skill_mapping(self, session_factory, seed_data):
        """Mode correctly maps to skill: DNA→run_dogme_dna, RNA→run_dogme_rna, CDNA→run_dogme_cdna."""

        for mode, expected_skill in [("DNA", "run_dogme_dna"), ("RNA", "run_dogme_rna"),
                                      ("CDNA", "run_dogme_cdna")]:
            mock_mcp = AsyncMock()
            mock_mcp.call_tool = AsyncMock(return_value={})

            mock_engine = MagicMock()
            mock_engine.model_name = "test-model"
            mock_engine.think = MagicMock(side_effect=RuntimeError("skip"))

            with _patch_session(session_factory), \
                 patch("cortex.job_polling.get_service_url", return_value="http://analyzer:8002"), \
                 patch("cortex.job_polling.MCPHttpClient", return_value=mock_mcp), \
                  patch("cortex.job_polling.AgentEngine", return_value=mock_engine), \
                  patch("cortex.job_polling.run_in_threadpool", _mock_run_in_threadpool), \
                  patch("cortex.job_polling.save_conversation_message", new_callable=AsyncMock):
                await _auto_trigger_analysis(
                    "proj-bg", f"uuid-{mode}",
                    {"sample_name": "s", "mode": mode, "model": "default"},
                    "u-bg",
                )

            sess = session_factory()
            blocks = sess.query(ProjectBlock).filter(
                ProjectBlock.project_id == "proj-bg",
                ProjectBlock.type == "AGENT_PLAN",
            ).all()
            latest = blocks[-1]
            assert get_block_payload(latest).get("skill") == expected_skill
            sess.close()

    @pytest.mark.asyncio
    async def test_skips_when_analysis_is_not_next_todo(self, session_factory, seed_data):
        """Workflow-managed auto-analysis only runs when analysis is the next ready todo step."""
        sess = session_factory()
        _create_block_internal(
            sess,
            "proj-bg",
            "WORKFLOW_PLAN",
            {
                "workflow_type": "local_sample_intake",
                "title": "Process local sample Jamshid",
                "sample_name": "Jamshid",
                "run_uuid": "uuid-skip",
                "status": "FOLLOW_UP",
                "next_step": "stage_input",
                "steps": [
                    {"id": "stage_input", "kind": "copy_sample", "title": "Stage Jamshid", "status": "FOLLOW_UP", "order_index": 0},
                    {"id": "run_dogme", "kind": "run", "title": "Run Dogme", "status": "COMPLETED", "order_index": 1, "run_uuid": "uuid-skip"},
                    {"id": "analyze_results", "kind": "analysis", "title": "Analyze results", "status": "PENDING", "order_index": 2, "run_uuid": "uuid-skip"},
                ],
            },
            status="FOLLOW_UP",
            owner_id="u-bg",
        )
        sess.close()

        with _patch_session(session_factory), \
             patch("cortex.job_polling.MCPHttpClient") as mock_mcp, \
               patch("cortex.job_polling.AgentEngine") as mock_engine, \
               patch("cortex.job_polling.save_conversation_message", new_callable=AsyncMock):
            await _auto_trigger_analysis(
                "proj-bg",
                "uuid-skip",
                {"sample_name": "Jamshid", "mode": "CDNA", "model": "default"},
                "u-bg",
            )

        assert not mock_mcp.called
        assert not mock_engine.called

        sess = session_factory()
        agent_blocks = sess.query(ProjectBlock).filter(ProjectBlock.type == "AGENT_PLAN").all()
        assert agent_blocks == []
        workflow_block = sess.query(ProjectBlock).filter(ProjectBlock.type == "WORKFLOW_PLAN").one()
        workflow_payload = get_block_payload(workflow_block)
        assert workflow_payload["steps"][2]["status"] == "PENDING"
        sess.close()


# ---------------------------------------------------------------------------
# _build_auto_analysis_context
# ---------------------------------------------------------------------------

class TestBuildAutoAnalysisContext:
    """Tests for the _build_auto_analysis_context helper."""

    def test_empty_data(self):
        result = _build_auto_analysis_context("s1", "DNA", "uuid", {}, {})
        assert result == "No analysis data available."

    def test_file_inventory(self):
        summary = {
            "all_file_counts": {"total_files": 10, "csv_count": 3, "bed_count": 2, "txt_count": 5},
            "file_summary": {"csv_files": [{"name": "f.csv", "size": 2048}]},
        }
        result = _build_auto_analysis_context("s1", "DNA", "uuid", summary, {})
        assert "File Inventory" in result
        assert "10" in result
        assert "f.csv" in result

    def test_parsed_csv_data(self):
        parsed = {
            "final_stats.csv": {
                "data": [{"col1": "a", "col2": "b"}],
                "columns": ["col1", "col2"],
                "total_rows": 1,
            }
        }
        result = _build_auto_analysis_context("s1", "DNA", "uuid", {}, parsed)
        assert "final_stats.csv" in result
        assert "col1" in result
        assert "a" in result

    def test_many_csv_files_truncated(self):
        csv_files = [{"name": f"file{i}.csv", "size": 100} for i in range(20)]
        summary = {
            "all_file_counts": {"total_files": 20},
            "file_summary": {"csv_files": csv_files},
        }
        result = _build_auto_analysis_context("s1", "DNA", "uuid", summary, {})
        assert "more" in result  # "…and X more"


# ---------------------------------------------------------------------------
# _build_static_analysis_summary
# ---------------------------------------------------------------------------

class TestBuildStaticAnalysisSummary:
    """Tests for the _build_static_analysis_summary helper."""

    def test_with_summary_data(self):
        summary = {
            "all_file_counts": {"total_files": 5, "csv_count": 2, "bed_count": 1, "txt_count": 2},
            "file_summary": {"csv_files": [{"name": "stats.csv", "size": 512}]},
            "mode": "DNA",
            "status": "COMPLETED",
        }
        result = _build_static_analysis_summary("sample1", "DNA", "uuid", summary,
                                                 work_directory="/work/test_run")
        assert "sample1" in result
        assert "test_run" in result
        assert "stats.csv" in result
        assert "dive deeper" in result

    def test_without_summary_data(self):
        result = _build_static_analysis_summary("sample2", "RNA", "uuid", {},
                                                 work_directory="")
        assert "sample2" in result
        assert "couldn't fetch" in result
        assert "dive deeper" in result

    def test_many_csv_files_truncated(self):
        csv_files = [{"name": f"f{i}.csv", "size": 100} for i in range(15)]
        summary = {
            "all_file_counts": {"total_files": 15},
            "file_summary": {"csv_files": csv_files},
        }
        result = _build_static_analysis_summary("s", "DNA", "uuid", summary)
        assert "more" in result


# ---------------------------------------------------------------------------
# extract_job_parameters_from_conversation
# ---------------------------------------------------------------------------

class TestExtractJobParameters:
    """Tests for extract_job_parameters_from_conversation."""

    @pytest.mark.asyncio
    async def test_empty_conversation(self, session_factory, seed_data):
        """Returns None when no blocks exist."""
        sess = session_factory()
        result = await extract_job_parameters_from_conversation(sess, "proj-bg")
        assert result is None
        sess.close()

    @pytest.mark.asyncio
    async def test_detects_mode_dna(self, session_factory, seed_data):
        """Detects DNA mode from user messages."""
        _create_user_message(session_factory, "proj-bg", "u-bg",
                            "Run DNA analysis on my sample")
        sess = session_factory()
        result = await extract_job_parameters_from_conversation(sess, "proj-bg")
        assert result is not None
        assert result.get("mode") == "DNA"
        sess.close()

    @pytest.mark.asyncio
    async def test_detects_entry_point_basecall(self, session_factory, seed_data):
        """Detects basecall entry point from keywords."""
        _create_user_message(session_factory, "proj-bg", "u-bg",
                            "I want to only basecall my pod5 files")
        sess = session_factory()
        result = await extract_job_parameters_from_conversation(sess, "proj-bg")
        assert result is not None
        assert result.get("entry_point") == "basecall"
        assert result.get("input_type") == "pod5"
        sess.close()

    @pytest.mark.asyncio
    async def test_detects_remap_from_bam(self, session_factory, seed_data):
        """Detects remap entry point from BAM keywords."""
        _create_user_message(session_factory, "proj-bg", "u-bg",
                            "Process this unmapped bam file")
        sess = session_factory()
        result = await extract_job_parameters_from_conversation(sess, "proj-bg")
        assert result is not None
        assert result.get("entry_point") == "remap"
        assert result.get("input_type") == "bam"
        sess.close()

    @pytest.mark.asyncio
    async def test_scopes_to_recent_cycle(self, session_factory, seed_data):
        """Only considers blocks after the last EXECUTION_JOB or approved gate."""
        # Create old cycle
        _create_user_message(session_factory, "proj-bg", "u-bg", "Run RNA on old_sample")
        sess = session_factory()
        _create_block_internal(sess, "proj-bg", "EXECUTION_JOB",
                              {"run_uuid": "old"}, status="DONE", owner_id="u-bg")
        sess.close()

        # Create new cycle
        _create_user_message(session_factory, "proj-bg", "u-bg",
                            "Now run DNA on new_sample at /data/new")

        sess = session_factory()
        result = await extract_job_parameters_from_conversation(sess, "proj-bg")
        assert result is not None
        # Should detect DNA from the new cycle, not RNA from the old
        assert result.get("mode") == "DNA"
        sess.close()
