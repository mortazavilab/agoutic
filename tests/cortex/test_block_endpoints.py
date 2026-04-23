"""
Tests for cortex/app.py — Block endpoints and project management helpers.

Covers:
  - POST /block — create block
  - GET /blocks — list blocks with pagination (normal + overflow)
  - POST /conversations/{id}/jobs — link job to conversation
  - track_project_access — project access tracking helper
  - _slugify — text to slug conversion
  - _dedup_slug — slug deduplication
  - POST /projects — project creation
  - GET /projects — project listing
  - GET /projects/{id}/jobs — job listing
  - GET /chat/status/{id} — chat progress
"""

import asyncio
import datetime
import json
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.config import AGOUTIC_DATA
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage, JobResult, ProjectTask,
)
from cortex.app import (
    app, _create_block_internal,
    track_project_access, _slugify, _dedup_slug,
    _chat_progress,
)
from cortex.llm_validators import get_block_payload
from cortex.task_service import sync_project_tasks


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

    user = User(id="u-blk", email="blk@example.com", role="user",
                username="blkuser", is_active=True)
    sess.add(user)

    session_obj = SessionModel(id="blk-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)

    proj = Project(id="proj-blk", name="Block Proj", owner_id="u-blk",
                   slug="block-proj")
    sess.add(proj)
    sess.flush()

    access = ProjectAccess(id=str(uuid.uuid4()), project_id="proj-blk",
                           user_id="u-blk", project_name="Block Proj",
                           role="owner")
    sess.add(access)
    sess.commit()
    sess.close()

    return {"user_id": "u-blk", "project_id": "proj-blk", "session_id": "blk-session"}


@pytest.fixture()
def client(session_factory, seed_data):
    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.admin.SessionLocal", session_factory), \
         patch("cortex.auth.SessionLocal", session_factory):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", seed_data["session_id"])
        yield c


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert _slugify("My Project") == "my-project"

    def test_special_chars(self):
        assert _slugify("Test!@#$%^&*()123") == "test-123"

    def test_max_len(self):
        result = _slugify("a" * 100, max_len=10)
        assert len(result) <= 10

    def test_empty_returns_project(self):
        assert _slugify("!@#$%") == "project"

    def test_leading_trailing_hyphens(self):
        assert _slugify("---hello---") == "hello"

    def test_consecutive_hyphens(self):
        assert _slugify("a   b   c") == "a-b-c"


# ---------------------------------------------------------------------------
# _dedup_slug
# ---------------------------------------------------------------------------

class TestDedupSlug:
    def test_unique_slug(self, session_factory, seed_data):
        sess = session_factory()
        result = _dedup_slug(sess, "u-blk", "new-slug")
        assert result == "new-slug"
        sess.close()

    def test_duplicate_slug(self, session_factory, seed_data):
        """When slug already exists, appends -1."""
        sess = session_factory()
        # block-proj already exists for u-blk
        result = _dedup_slug(sess, "u-blk", "block-proj")
        assert result == "block-proj-1"
        sess.close()

    def test_exclude_self(self, session_factory, seed_data):
        """Excluding own project ID allows reuse of own slug."""
        sess = session_factory()
        result = _dedup_slug(sess, "u-blk", "block-proj", exclude_project_id="proj-blk")
        assert result == "block-proj"
        sess.close()


# ---------------------------------------------------------------------------
# track_project_access
# ---------------------------------------------------------------------------

class TestTrackProjectAccess:
    @pytest.mark.asyncio
    async def test_creates_new_access(self, session_factory, seed_data):
        """Creates a new access record for a new project."""
        sess = session_factory()
        await track_project_access(sess, "u-blk", "proj-new", "New Project", "editor")
        access = sess.execute(
            select(ProjectAccess).where(ProjectAccess.project_id == "proj-new")
        ).scalar_one_or_none()
        assert access is not None
        assert access.role == "editor"
        assert access.project_name == "New Project"
        sess.close()

    @pytest.mark.asyncio
    async def test_updates_existing_access(self, session_factory, seed_data):
        """Updates last_accessed on existing access record."""
        sess = session_factory()
        await track_project_access(sess, "u-blk", "proj-blk", "Updated Name")
        access = sess.execute(
            select(ProjectAccess).where(ProjectAccess.project_id == "proj-blk")
        ).scalar_one_or_none()
        assert access is not None
        assert access.project_name == "Updated Name"
        sess.close()

    @pytest.mark.asyncio
    async def test_updates_user_last_project(self, session_factory, seed_data):
        """Sets user.last_project_id."""
        sess = session_factory()
        await track_project_access(sess, "u-blk", "proj-new2")
        user = sess.execute(select(User).where(User.id == "u-blk")).scalar_one()
        assert user.last_project_id == "proj-new2"
        sess.close()

    @pytest.mark.asyncio
    async def test_default_role_is_owner(self, session_factory, seed_data):
        """When no role specified, defaults to 'owner'."""
        sess = session_factory()
        await track_project_access(sess, "u-blk", "proj-default")
        access = sess.execute(
            select(ProjectAccess).where(ProjectAccess.project_id == "proj-default")
        ).scalar_one_or_none()
        assert access.role == "owner"
        sess.close()


# ---------------------------------------------------------------------------
# POST /block
# ---------------------------------------------------------------------------

class TestCreateBlock:
    def test_create_block(self, client):
        resp = client.post("/block", json={
            "project_id": "proj-blk",
            "type": "USER_MESSAGE",
            "payload": {"text": "Hello"},
            "status": "NEW",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "USER_MESSAGE"
        assert data["payload"]["text"] == "Hello"

    def test_cancel_staging_task_proxy_updates_block_and_workflow(self, client, session_factory, monkeypatch):
        sess = session_factory()
        workflow_block = _create_block_internal(
            sess,
            "proj-blk",
            "WORKFLOW_PLAN",
            {
                "status": "RUNNING",
                "steps": [
                    {"id": "stage_input", "kind": "REMOTE_STAGE", "title": "Stage input", "status": "RUNNING"},
                    {"id": "complete_stage_only", "kind": "COMPLETE_STAGE_ONLY", "title": "Finish stage-only flow", "status": "PENDING"},
                ],
            },
            status="RUNNING",
            owner_id="u-blk",
        )
        stage_block = _create_block_internal(
            sess,
            "proj-blk",
            "STAGING_TASK",
            {
                "sample_name": "ENCFF433WOA",
                "mode": "RNA",
                "staging_task_id": "stg-1",
                "workflow_plan_block_id": workflow_block.id,
                "stage_input_step_id": "stage_input",
                "complete_stage_only_step_id": "complete_stage_only",
                "progress_percent": 70,
                "message": "Uploading sample data",
                "stage_parts": {
                    "references": {"status": "COMPLETED", "progress_percent": 100, "message": "Reference assets are ready."},
                    "data": {"status": "RUNNING", "progress_percent": 40, "message": "Uploading sample data"},
                },
            },
            status="RUNNING",
            owner_id="u-blk",
        )
        sess.commit()
        sess.close()

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "task_id": "stg-1",
                    "status": "cancelled",
                    "message": "Remote staging cancelled by user.",
                }

            def raise_for_status(self):
                return None

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, headers=None):
                assert url.endswith("/remote/stage/stg-1/cancel")
                return _FakeResponse()

        monkeypatch.setattr("cortex.app.httpx.AsyncClient", _FakeAsyncClient)

        resp = client.post(
            "/remote/stage/stg-1/cancel",
            json={"project_id": "proj-blk", "block_id": stage_block.id},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        sess = session_factory()
        refreshed_stage = sess.execute(select(ProjectBlock).where(ProjectBlock.id == stage_block.id)).scalar_one()
        refreshed_workflow = sess.execute(select(ProjectBlock).where(ProjectBlock.id == workflow_block.id)).scalar_one()
        stage_payload = get_block_payload(refreshed_stage)
        workflow_payload = get_block_payload(refreshed_workflow)
        sess.close()

        assert refreshed_stage.status == "CANCELLED"
        assert stage_payload["message"] == "Remote staging cancelled by user."
        assert stage_payload["stage_parts"]["references"]["status"] == "COMPLETED"
        assert stage_payload["stage_parts"]["data"]["status"] == "CANCELLED"
        assert refreshed_workflow.status == "CANCELLED"
        assert workflow_payload["status"] == "CANCELLED"
        assert {step["id"]: step["status"] for step in workflow_payload["steps"]} == {
            "stage_input": "CANCELLED",
            "complete_stage_only": "CANCELLED",
        }

    def test_cancel_staging_task_proxy_refreshes_failed_block_when_launchpad_is_already_terminal(self, client, session_factory, monkeypatch):
        sess = session_factory()
        workflow_block = _create_block_internal(
            sess,
            "proj-blk",
            "WORKFLOW_PLAN",
            {
                "status": "RUNNING",
                "steps": [
                    {"id": "stage_input", "kind": "REMOTE_STAGE", "title": "Stage input", "status": "RUNNING"},
                    {"id": "complete_stage_only", "kind": "COMPLETE_STAGE_ONLY", "title": "Finish stage-only flow", "status": "PENDING"},
                ],
            },
            status="RUNNING",
            owner_id="u-blk",
        )
        stage_block = _create_block_internal(
            sess,
            "proj-blk",
            "STAGING_TASK",
            {
                "sample_name": "IGVFFI6571ANCX",
                "mode": "DNA",
                "staging_task_id": "stg-failed",
                "workflow_plan_block_id": workflow_block.id,
                "stage_input_step_id": "stage_input",
                "complete_stage_only_step_id": "complete_stage_only",
                "progress_percent": 67,
                "message": "Staging in progress...",
                "stage_parts": {
                    "references": {"status": "COMPLETED", "progress_percent": 100, "message": "Reference assets are ready."},
                    "data": {"status": "RUNNING", "progress_percent": 67, "message": "Uploading sample data"},
                },
            },
            status="RUNNING",
            owner_id="u-blk",
        )
        sess.commit()
        sess.close()

        class _FakeResponse:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

            def raise_for_status(self):
                return None

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, headers=None):
                assert url.endswith("/remote/stage/stg-failed/cancel")
                return _FakeResponse(409, {"detail": "Cannot cancel staging task with status failed"})

            async def get(self, url, headers=None):
                assert url.endswith("/remote/stage/stg-failed")
                return _FakeResponse(
                    200,
                    {
                        "task_id": "stg-failed",
                        "status": "failed",
                        "error": "Upload stalled for too long.",
                        "progress": {},
                        "result": None,
                    },
                )

        monkeypatch.setattr("cortex.app.httpx.AsyncClient", _FakeAsyncClient)

        resp = client.post(
            "/remote/stage/stg-failed/cancel",
            json={"project_id": "proj-blk", "block_id": stage_block.id},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

        sess = session_factory()
        refreshed_stage = sess.execute(select(ProjectBlock).where(ProjectBlock.id == stage_block.id)).scalar_one()
        refreshed_workflow = sess.execute(select(ProjectBlock).where(ProjectBlock.id == workflow_block.id)).scalar_one()
        stage_payload = get_block_payload(refreshed_stage)
        workflow_payload = get_block_payload(refreshed_workflow)
        sess.close()

        assert refreshed_stage.status == "FAILED"
        assert stage_payload["message"] == "Upload stalled for too long."
        assert stage_payload["stage_parts"]["data"]["status"] == "FAILED"
        assert refreshed_workflow.status == "FAILED"
        assert {step["id"]: step["status"] for step in workflow_payload["steps"]} == {
            "stage_input": "FAILED",
            "complete_stage_only": "PENDING",
        }

    def test_resume_staging_task_proxy_updates_block_and_workflow(self, client, session_factory, monkeypatch):
        sess = session_factory()
        workflow_block = _create_block_internal(
            sess,
            "proj-blk",
            "WORKFLOW_PLAN",
            {
                "status": "CANCELLED",
                "steps": [
                    {"id": "stage_input", "kind": "REMOTE_STAGE", "title": "Stage input", "status": "CANCELLED"},
                    {"id": "complete_stage_only", "kind": "COMPLETE_STAGE_ONLY", "title": "Finish stage-only flow", "status": "CANCELLED"},
                ],
            },
            status="CANCELLED",
            owner_id="u-blk",
        )
        stage_block = _create_block_internal(
            sess,
            "proj-blk",
            "STAGING_TASK",
            {
                "sample_name": "IGVFFI6571ANCX",
                "mode": "DNA",
                "input_directory": "/tmp/IGVFFI6571ANCX.bam",
                "ssh_profile_id": "profile-1",
                "ssh_profile_nickname": "hpc3",
                "remote_base_path": "/remote/agoutic",
                "staging_task_id": "stg-1",
                "workflow_plan_block_id": workflow_block.id,
                "stage_input_step_id": "stage_input",
                "complete_stage_only_step_id": "complete_stage_only",
                "progress_percent": 70,
                "message": "Remote staging cancelled by user.",
                "stage_parts": {
                    "references": {"status": "COMPLETED", "progress_percent": 100, "message": "Reference assets are ready."},
                    "data": {"status": "CANCELLED", "progress_percent": 67, "message": "Cancelled while uploading sample data."},
                },
            },
            status="CANCELLED",
            owner_id="u-blk",
        )
        sess.commit()
        sess.close()

        scheduled = []

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "task_id": "stg-2",
                    "status": "queued",
                    "resumed_from_task_id": "stg-1",
                }

            def raise_for_status(self):
                return None

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, headers=None):
                assert url.endswith("/remote/stage/stg-1/resume")
                return _FakeResponse()

            async def get(self, url, headers=None):
                assert url.endswith("/remote/stage/stg-2")
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "task_id": "stg-2",
                        "status": "queued",
                        "progress": {
                            "wait_reason": "Waiting for the Remote Profile unlock to be re-established after restart before staging can resume.",
                            "waiting_for_auth": True,
                        },
                    },
                )

        monkeypatch.setattr("cortex.app.httpx.AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr("cortex.app.job_polling.poll_staging_status", lambda **kwargs: scheduled.append(kwargs))
        monkeypatch.setattr("cortex.app.asyncio.create_task", lambda coro: None)

        resp = client.post(
            "/remote/stage/stg-1/resume",
            json={"project_id": "proj-blk", "block_id": stage_block.id},
        )

        assert resp.status_code == 200
        assert resp.json()["task_id"] == "stg-2"

        sess = session_factory()
        refreshed_stage = sess.execute(select(ProjectBlock).where(ProjectBlock.id == stage_block.id)).scalar_one()
        refreshed_workflow = sess.execute(select(ProjectBlock).where(ProjectBlock.id == workflow_block.id)).scalar_one()
        stage_payload = get_block_payload(refreshed_stage)
        workflow_payload = get_block_payload(refreshed_workflow)
        sess.close()

        assert refreshed_stage.status == "RUNNING"
        assert stage_payload["staging_task_id"] == "stg-2"
        assert stage_payload["message"] == "Staging is queued and waiting to resume."
        assert stage_payload["stage_parts"]["references"]["status"] == "COMPLETED"
        assert stage_payload["stage_parts"]["data"]["status"] == "PENDING"
        assert refreshed_workflow.status == "RUNNING"
        assert {step["id"]: step["status"] for step in workflow_payload["steps"]} == {
            "stage_input": "RUNNING",
            "complete_stage_only": "PENDING",
        }
        assert len(scheduled) == 1
        scheduled_kwargs = scheduled[0]
        assert scheduled_kwargs["task_id"] == "stg-2"
        assert scheduled_kwargs["project_id"] == "proj-blk"
        assert scheduled_kwargs["block_id"] == stage_block.id
        assert scheduled_kwargs["owner_id"] == "u-blk"
        assert scheduled_kwargs["job_data"] == {
            "sample_name": "IGVFFI6571ANCX",
            "mode": "DNA",
            "input_directory": "/tmp/IGVFFI6571ANCX.bam",
            "remote_base_path": "/remote/agoutic",
        }
        assert scheduled_kwargs["ssh_profile_id"] == "profile-1"
        assert scheduled_kwargs["ssh_profile_nickname"] == "hpc3"
        assert scheduled_kwargs["workflow_block_id"] == workflow_block.id
        assert scheduled_kwargs["stage_input_step_id"] == "stage_input"
        assert scheduled_kwargs["complete_stage_only_step_id"] == "complete_stage_only"
        assert scheduled_kwargs["gate_payload"] == {"skill": "remote_execution", "model": "default"}
        assert scheduled_kwargs["initial_stage_parts"] == stage_payload["stage_parts"]

    def test_refresh_staging_task_proxy_updates_running_block(self, client, session_factory, monkeypatch):
        sess = session_factory()
        stage_block = _create_block_internal(
            sess,
            "proj-blk",
            "STAGING_TASK",
            {
                "sample_name": "ENCFF433WOA",
                "mode": "RNA",
                "staging_task_id": "stg-refresh",
                "progress_percent": 35,
                "message": "Staging in progress...",
                "stage_parts": {
                    "references": {"status": "COMPLETED", "progress_percent": 100, "message": "Reference assets are ready."},
                    "data": {"status": "RUNNING", "progress_percent": 35, "message": "Staging in progress..."},
                },
            },
            status="RUNNING",
            owner_id="u-blk",
        )
        sess.commit()
        sess.close()

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "task_id": "stg-refresh",
                    "status": "running",
                    "progress": {
                        "current_file": "IGVFFI6571ANCX.bam",
                        "current_file_bytes_transferred": 820,
                        "current_file_total_bytes_estimate": 1000,
                        "overall_bytes_total": 1000,
                        "overall_bytes_transferred": 820,
                        "file_percent": 82,
                        "speed": "120MB/s",
                        "files_transferred": 1,
                        "files_total": 3,
                    },
                }

            def raise_for_status(self):
                return None

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                assert url.endswith("/remote/stage/stg-refresh")
                return _FakeResponse()

        monkeypatch.setattr("cortex.app.httpx.AsyncClient", _FakeAsyncClient)

        resp = client.post(
            "/remote/stage/stg-refresh/refresh",
            json={"project_id": "proj-blk", "block_id": stage_block.id},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

        sess = session_factory()
        refreshed_stage = sess.execute(select(ProjectBlock).where(ProjectBlock.id == stage_block.id)).scalar_one()
        stage_payload = get_block_payload(refreshed_stage)
        sess.close()

        assert refreshed_stage.status == "RUNNING"
        assert stage_payload["message"] == "Uploading 1/3 files (82% current file) 120MB/s"
        assert stage_payload["transfer_progress"]["current_file"] == "IGVFFI6571ANCX.bam"
        assert stage_payload["transfer_progress"]["overall_bytes_transferred"] == 820
        assert stage_payload["transfer_progress"]["overall_bytes_total"] == 1000
        assert stage_payload["stage_parts"]["data"]["status"] == "RUNNING"
        assert stage_payload["stage_parts"]["data"]["progress_percent"] == 82
        assert stage_payload["last_updated"]

    def test_refresh_staging_task_proxy_marks_missing_task_failed(self, client, session_factory, monkeypatch):
        sess = session_factory()
        stage_block = _create_block_internal(
            sess,
            "proj-blk",
            "STAGING_TASK",
            {
                "sample_name": "IGVFFI6571ANCX",
                "mode": "DNA",
                "staging_task_id": "stg-missing",
                "progress_percent": 67,
                "message": "Staging in progress...",
                "stage_parts": {
                    "references": {"status": "COMPLETED", "progress_percent": 100, "message": "Reference assets are ready."},
                    "data": {"status": "RUNNING", "progress_percent": 67, "message": "Uploading sample data"},
                },
            },
            status="RUNNING",
            owner_id="u-blk",
        )
        sess.commit()
        sess.close()

        class _FakeResponse:
            status_code = 404

            def json(self):
                return {"detail": "Not found"}

            def raise_for_status(self):
                return None

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                assert url.endswith("/remote/stage/stg-missing")
                return _FakeResponse()

        monkeypatch.setattr("cortex.app.httpx.AsyncClient", _FakeAsyncClient)

        resp = client.post(
            "/remote/stage/stg-missing/refresh",
            json={"project_id": "proj-blk", "block_id": stage_block.id},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

        sess = session_factory()
        refreshed_stage = sess.execute(select(ProjectBlock).where(ProjectBlock.id == stage_block.id)).scalar_one()
        stage_payload = get_block_payload(refreshed_stage)
        sess.close()

        assert refreshed_stage.status == "FAILED"
        assert "Launchpad no longer has this task" in stage_payload["message"]
        assert stage_payload["stage_parts"]["data"]["status"] == "FAILED"

    def test_delete_failed_staging_workflow_removes_local_folder_and_marks_blocks_deleted(self, client, session_factory):
        sess = session_factory()
        workflow_dir = AGOUTIC_DATA / "users" / "blkuser" / "block-proj" / "workflow" / "failed-stage-delete"
        if workflow_dir.exists():
            import shutil as _shutil
            _shutil.rmtree(workflow_dir)
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "nextflow.config").write_text("process {}\n")
        (workflow_dir / "dogme.profile").write_text("export MODKITBASE=/cluster/modkit\n")

        workflow_block = _create_block_internal(
            sess,
            "proj-blk",
            "WORKFLOW_PLAN",
            {
                "status": "FAILED",
                "sample_name": "IGVFFI6571ANCX",
                "steps": [
                    {"id": "stage_input", "kind": "REMOTE_STAGE", "title": "Stage input", "status": "FAILED"},
                ],
            },
            status="FAILED",
            owner_id="u-blk",
        )
        stage_block = _create_block_internal(
            sess,
            "proj-blk",
            "STAGING_TASK",
            {
                "sample_name": "IGVFFI6571ANCX",
                "mode": "DNA",
                "staging_task_id": "stg-delete-1",
                "workflow_plan_block_id": workflow_block.id,
                "local_workflow_directory": str(workflow_dir),
                "progress_percent": 67,
                "message": "Upload stalled for too long.",
                "stage_parts": {
                    "references": {"status": "COMPLETED", "progress_percent": 100, "message": "Reference assets are ready."},
                    "data": {"status": "FAILED", "progress_percent": 67, "message": "Upload stalled for too long."},
                },
            },
            status="FAILED",
            owner_id="u-blk",
        )
        sync_project_tasks(sess, "proj-blk")
        assert sess.execute(select(ProjectTask)).scalars().all()
        sess.commit()
        sess.close()

        resp = client.post(
            "/remote/stage/stg-delete-1/delete-workflow",
            json={"project_id": "proj-blk", "block_id": stage_block.id},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["deleted_path"] == str(workflow_dir)
        assert body["file_count"] == 2
        assert not workflow_dir.exists()

        sess = session_factory()
        refreshed_stage = sess.execute(select(ProjectBlock).where(ProjectBlock.id == stage_block.id)).scalar_one()
        refreshed_workflow = sess.execute(select(ProjectBlock).where(ProjectBlock.id == workflow_block.id)).scalar_one()
        stage_payload = get_block_payload(refreshed_stage)
        workflow_payload = get_block_payload(refreshed_workflow)
        remaining_tasks = sess.execute(
            select(ProjectTask).where(ProjectTask.archived_at.is_(None))
        ).scalars().all()
        sess.close()

        assert refreshed_stage.status == "DELETED"
        assert refreshed_workflow.status == "DELETED"
        assert stage_payload["deleted_path"] == str(workflow_dir)
        assert stage_payload["deleted_file_count"] == 2
        assert workflow_payload["status"] == "DELETED"
        assert workflow_payload["deleted_file_count"] == 2
        assert remaining_tasks == []

    def test_delete_failed_staging_workflow_rejects_running_block(self, client, session_factory):
        sess = session_factory()
        stage_block = _create_block_internal(
            sess,
            "proj-blk",
            "STAGING_TASK",
            {
                "sample_name": "ENCFF433WOA",
                "mode": "RNA",
                "staging_task_id": "stg-running-delete",
                "local_workflow_directory": str(AGOUTIC_DATA / "users" / "blkuser" / "block-proj" / "workflow" / "running-stage-delete"),
                "progress_percent": 20,
                "message": "Uploading sample data",
                "stage_parts": {
                    "references": {"status": "COMPLETED", "progress_percent": 100, "message": "Reference assets are ready."},
                    "data": {"status": "RUNNING", "progress_percent": 20, "message": "Uploading sample data"},
                },
            },
            status="RUNNING",
            owner_id="u-blk",
        )
        sess.commit()
        sess.close()

        resp = client.post(
            "/remote/stage/stg-running-delete/delete-workflow",
            json={"project_id": "proj-blk", "block_id": stage_block.id},
        )

        assert resp.status_code == 409
        assert "only available after staging has failed or been cancelled" in resp.json()["detail"]

    def test_create_block_unauthenticated(self, session_factory, seed_data):
        with patch("cortex.db.SessionLocal", session_factory), \
             patch("cortex.app.SessionLocal", session_factory), \
             patch("cortex.dependencies.SessionLocal", session_factory), \
             patch("cortex.middleware.SessionLocal", session_factory), \
             patch("cortex.admin.SessionLocal", session_factory), \
             patch("cortex.auth.SessionLocal", session_factory):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post("/block", json={
                "project_id": "proj-blk",
                "type": "USER_MESSAGE",
                "payload": {"text": "Hello"},
            })
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /blocks
# ---------------------------------------------------------------------------

class TestGetBlocks:
    def test_empty_project(self, client):
        resp = client.get("/blocks", params={"project_id": "proj-blk"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["blocks"] == []
        assert data["latest_seq"] == 0

    def test_returns_blocks(self, client, session_factory):
        # Create some blocks
        sess = session_factory()
        for i in range(3):
            _create_block_internal(sess, "proj-blk", "USER_MESSAGE",
                                  {"text": f"msg {i}"}, owner_id="u-blk")
        sess.close()

        resp = client.get("/blocks", params={"project_id": "proj-blk"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["blocks"]) == 3
        assert data["latest_seq"] > 0

    def test_since_seq_filtering(self, client, session_factory):
        sess = session_factory()
        blocks = []
        for i in range(5):
            b = _create_block_internal(sess, "proj-blk", "USER_MESSAGE",
                                      {"text": f"msg {i}"}, owner_id="u-blk")
            blocks.append(b)
        sess.close()

        # Get blocks since seq 3
        resp = client.get("/blocks", params={"project_id": "proj-blk", "since_seq": 3})
        assert resp.status_code == 200
        data = resp.json()
        # Should only have blocks with seq > 3
        assert len(data["blocks"]) == 2

    def test_limit_returns_newest(self, client, session_factory):
        """When more blocks than limit, return the newest ones."""
        sess = session_factory()
        for i in range(10):
            _create_block_internal(sess, "proj-blk", "USER_MESSAGE",
                                  {"text": f"msg {i}"}, owner_id="u-blk")
        sess.close()

        resp = client.get("/blocks", params={"project_id": "proj-blk", "limit": 3})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["blocks"]) == 3
        # Should be the last 3 blocks (newest)
        texts = [b["payload"]["text"] for b in data["blocks"]]
        assert "msg 7" in texts
        assert "msg 8" in texts
        assert "msg 9" in texts


# ---------------------------------------------------------------------------
# GET /chat/status/{request_id}
# ---------------------------------------------------------------------------

class TestChatStatus:
    def test_unknown_request(self, client):
        resp = client.get("/chat/status/unknown-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == "waiting"

    def test_known_request(self, client):
        _chat_progress["test-req-1"] = {"stage": "thinking", "detail": "Processing..."}
        try:
            resp = client.get("/chat/status/test-req-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["stage"] == "thinking"
            assert data["detail"] == "Processing..."
        finally:
            _chat_progress.pop("test-req-1", None)


# ---------------------------------------------------------------------------
# POST /conversations/{id}/jobs
# ---------------------------------------------------------------------------

class TestLinkJobToConversation:
    def test_link_job(self, client, session_factory):
        # Create a conversation owned by the user
        sess = session_factory()
        conv = Conversation(
            id="conv-job-test",
            project_id="proj-blk",
            user_id="u-blk",
            title="Test Conv",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        sess.add(conv)
        sess.commit()
        sess.close()

        resp = client.post(
            "/conversations/conv-job-test/jobs",
            params={"run_uuid": "test-run-uuid"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "job_id" in data

    def test_link_job_wrong_user(self, client, session_factory):
        """Returns 403 when conversation belongs to another user."""
        sess = session_factory()
        conv = Conversation(
            id="conv-other",
            project_id="proj-blk",
            user_id="u-other",  # Different user
            title="Other Conv",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        sess.add(conv)
        sess.commit()
        sess.close()

        resp = client.post(
            "/conversations/conv-other/jobs",
            params={"run_uuid": "test-run"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /projects/{id}/jobs
# ---------------------------------------------------------------------------

class TestProjectJobs:
    def test_no_jobs(self, client):
        resp = client.get("/projects/proj-blk/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"] == []

    def test_with_jobs(self, client, session_factory):
        sess = session_factory()
        conv = Conversation(
            id="conv-with-jobs",
            project_id="proj-blk",
            user_id="u-blk",
            title="Job Conv",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        sess.add(conv)
        sess.flush()

        job = JobResult(
            id=str(uuid.uuid4()),
            conversation_id="conv-with-jobs",
            run_uuid="run-123",
            sample_name="sample1",
            workflow_type="DNA",
            status="COMPLETED",
            created_at=datetime.datetime.utcnow(),
        )
        sess.add(job)
        sess.commit()
        sess.close()

        resp = client.get("/projects/proj-blk/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["run_uuid"] == "run-123"


# ---------------------------------------------------------------------------
# POST /projects
# ---------------------------------------------------------------------------

class TestCreateProject:
    def test_creates_project(self, client, tmp_path):
        proj_dir = tmp_path / "test-project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        with patch("cortex.user_jail.get_user_project_dir", return_value=proj_dir):
            resp = client.post("/projects", json={"name": "My New Project"})

        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["name"] == "My New Project"
        assert data["slug"] == "my-new-project"
        assert "id" in data


# ---------------------------------------------------------------------------
# GET /projects
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_lists_user_projects(self, client):
        resp = client.get("/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["projects"]) >= 1
        proj = data["projects"][0]
        assert proj["id"] == "proj-blk"
        assert proj["name"] == "Block Proj"
        assert proj["role"] == "owner"
