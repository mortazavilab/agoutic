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
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage, JobResult,
)
from cortex.app import (
    app, _create_block_internal,
    track_project_access, _slugify, _dedup_slug,
    _chat_progress,
)
from cortex.llm_validators import get_block_payload


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
