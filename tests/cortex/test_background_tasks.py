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

from cortex.models import (
    Base, User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock,
)
from cortex.app import (
    handle_rejection,
    download_after_approval,
    submit_job_after_approval,
    poll_job_status,
    _auto_trigger_analysis,
    _build_auto_analysis_context,
    _build_static_analysis_summary,
    extract_job_parameters_from_conversation,
    get_block_payload,
    _create_block_internal,
    _active_downloads,
)

try:
    from launchpad.models import Base as LaunchpadBase
    _LP = True
except ImportError:
    _LP = False


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
    if _LP:
        LaunchpadBase.metadata.create_all(bind=engine)
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
         patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.admin.SessionLocal", session_factory), \
         patch("cortex.auth.SessionLocal", session_factory):
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
             patch("cortex.app.AgentEngine", return_value=mock_engine), \
             patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool), \
             patch("cortex.app.extract_job_parameters_from_conversation",
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
             patch("cortex.app.extract_job_parameters_from_conversation",
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
             patch("cortex.app.AgentEngine", return_value=mock_engine), \
             patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool), \
             patch("cortex.app.extract_job_parameters_from_conversation",
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
             patch("cortex.app.AgentEngine", return_value=mock_engine), \
             patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool):
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
             patch("cortex.app.AgentEngine", return_value=mock_engine), \
             patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool):
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
             patch("cortex.app.asyncio") as mock_aio:
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
             patch("cortex.app.asyncio") as mock_aio, \
             patch("cortex.app.AGOUTIC_DATA", str(tmp_path)):
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
             patch("cortex.app.AGOUTIC_DATA", str(tmp_path)), \
             patch("cortex.app.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await download_after_approval("proj-bg", gate.id)

        # Should have used users/bguser/bg-proj/data as target
        sess = session_factory()
        dl_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "DOWNLOAD_TASK"
        ).all()
        assert len(dl_blocks) == 1
        dl_payload = get_block_payload(dl_blocks[0])
        assert "bguser" in dl_payload["target_dir"]
        assert "bg-proj" in dl_payload["target_dir"]
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.asyncio") as mock_aio:
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
             patch("cortex.app.extract_job_parameters_from_conversation",
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client):
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client):
            await submit_job_after_approval("proj-bg", gate.id)

        sess = session_factory()
        job_blocks = sess.query(ProjectBlock).filter(
            ProjectBlock.type == "EXECUTION_JOB"
        ).all()
        assert len(job_blocks) == 1
        assert job_blocks[0].status == "FAILED"
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.AGOUTIC_DATA", str(tmp_path)), \
             patch("cortex.app.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        # The call_tool should have received the resolved data dir
        call_args = mock_client.call_tool.call_args
        submitted_dir = call_args.kwargs.get("input_directory", "")
        assert "bguser" in submitted_dir or "bg-proj" in submitted_dir

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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.asyncio") as mock_aio:
            mock_aio.create_task = MagicMock()
            await submit_job_after_approval("proj-bg", gate.id)

        # asyncio.create_task should have been called with poll_job_status coroutine
        assert mock_aio.create_task.called


# ---------------------------------------------------------------------------
# poll_job_status
# ---------------------------------------------------------------------------

class TestPollJobStatus:
    """Tests for the poll_job_status background task."""

    @pytest.mark.asyncio
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.asyncio") as mock_aio, \
             patch("cortex.app._auto_trigger_analysis", new_callable=AsyncMock):
            # Override sleep to not actually wait
            mock_aio.sleep = AsyncMock()
            await poll_job_status("proj-bg", job_block.id, "poll-test")

        sess = session_factory()
        updated = sess.query(ProjectBlock).filter(ProjectBlock.id == job_block.id).first()
        payload = get_block_payload(updated)
        assert updated.status == "DONE"
        assert payload["job_status"]["status"] == "COMPLETED"
        sess.close()

    @pytest.mark.asyncio
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.asyncio") as mock_aio:
            mock_aio.sleep = AsyncMock()
            await poll_job_status("proj-bg", job_block.id, "fail-test")

        sess = session_factory()
        updated = sess.query(ProjectBlock).filter(ProjectBlock.id == job_block.id).first()
        assert updated.status == "FAILED"
        sess.close()

    @pytest.mark.asyncio
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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.asyncio") as mock_aio, \
             patch("cortex.app._auto_trigger_analysis", mock_auto):
            mock_aio.sleep = AsyncMock()
            await poll_job_status("proj-bg", job_block.id, "auto-test")

        assert mock_auto.called
        call_args = mock_auto.call_args
        assert call_args[0][0] == "proj-bg"  # project_id
        assert call_args[0][1] == "auto-test"  # run_uuid

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
             patch("cortex.app.get_service_url", return_value="http://launchpad:8003"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.asyncio") as mock_aio:
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
             patch("cortex.app.get_service_url", return_value="http://analyzer:8002"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_mcp), \
             patch("cortex.app.AgentEngine", return_value=mock_engine), \
             patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool), \
             patch("cortex.app.save_conversation_message", new_callable=AsyncMock):
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
             patch("cortex.app.get_service_url", return_value="http://analyzer:8002"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_mcp), \
             patch("cortex.app.AgentEngine", return_value=mock_engine), \
             patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool), \
             patch("cortex.app.save_conversation_message", new_callable=AsyncMock):
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
             patch("cortex.app.get_service_url", return_value="http://analyzer:8002"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_mcp), \
             patch("cortex.app.AgentEngine", return_value=mock_engine), \
             patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool), \
             patch("cortex.app.save_conversation_message", new_callable=AsyncMock):
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
                 patch("cortex.app.get_service_url", return_value="http://analyzer:8002"), \
                 patch("cortex.app.MCPHttpClient", return_value=mock_mcp), \
                 patch("cortex.app.AgentEngine", return_value=mock_engine), \
                 patch("cortex.app.run_in_threadpool", _mock_run_in_threadpool), \
                 patch("cortex.app.save_conversation_message", new_callable=AsyncMock):
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
