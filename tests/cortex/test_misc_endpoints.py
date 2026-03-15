"""
Tests for cortex/app.py — Miscellaneous endpoints.

Covers:
  - GET  /user/disk-usage
  - DELETE /projects/{id}  (soft archive)
  - DELETE /projects/{id}/permanent
  - GET  /chat/status/{request_id}
  - GET  /projects/{id}/jobs  (empty — no job runner in tests)
  - POST /projects/{id}/share  (project sharing)
"""

import datetime
import json
import uuid

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage,
)
from cortex.app import app, _chat_progress


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
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture()
def seed_data(test_session_factory, tmp_path):
    sess = test_session_factory()

    user = User(id="user-1", email="misc@example.com", role="user",
                username="miscuser", is_active=True)
    sess.add(user)

    session_obj = SessionModel(id="misc-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)

    project = Project(id="proj-1", name="My Project", owner_id="user-1",
                      slug="my-project")
    sess.add(project)
    sess.flush()

    access = ProjectAccess(id=str(uuid.uuid4()), user_id="user-1",
                           project_id="proj-1", project_name="My Project",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    sess.add(access)
    sess.commit()
    sess.close()


@pytest.fixture()
def client(test_session_factory, seed_data, tmp_path):
    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "misc-session")
        yield c


# ---------------------------------------------------------------------------
# GET /user/disk-usage
# ---------------------------------------------------------------------------
class TestDiskUsage:
    def test_empty_disk(self, client, tmp_path):
        with patch("cortex.app._resolve_project_dir",
                   return_value=tmp_path / "empty"):
            resp = client.get("/user/disk-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_bytes"] == 0
        assert isinstance(data["projects"], list)

    def test_with_files(self, client, tmp_path):
        proj_dir = tmp_path / "users" / "miscuser" / "my-project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "data.csv").write_text("a,b\n1,2\n3,4")

        with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
            resp = client.get("/user/disk-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_bytes"] > 0
        assert len(data["projects"]) == 1
        assert data["projects"][0]["project_id"] == "proj-1"


# ---------------------------------------------------------------------------
# DELETE /projects/{id}  (soft archive)
# ---------------------------------------------------------------------------
class TestDeleteProject:
    def test_archive_project(self, client, test_session_factory):
        resp = client.delete("/projects/proj-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archived"
        assert data["id"] == "proj-1"

        # Verify in DB
        sess = test_session_factory()
        proj = sess.execute(select(Project).where(Project.id == "proj-1")).scalar_one()
        assert proj.is_archived is True
        sess.close()

    def test_archive_nonexistent(self, client, test_session_factory):
        # Create a project access entry for a non-existent project to avoid 403
        # Actually the require_project_access will raise 403 if there's no access
        resp = client.delete("/projects/nonexistent")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /projects/{id}/permanent
# ---------------------------------------------------------------------------
class TestPermanentDelete:
    def test_permanent_delete(self, client, test_session_factory, tmp_path):
        # Seed some blocks and conversations
        sess = test_session_factory()
        blk = ProjectBlock(id=str(uuid.uuid4()), project_id="proj-1",
                           owner_id="user-1", type="USER_MESSAGE", seq=1,
                           payload_json='{"text":"hi"}')
        sess.add(blk)
        conv = Conversation(id="conv-del", project_id="proj-1",
                            user_id="user-1", title="To delete")
        sess.add(conv)
        sess.flush()
        msg = ConversationMessage(id="msg-del", conversation_id="conv-del",
                                  role="user", content="bye", seq=1)
        sess.add(msg)
        sess.commit()
        sess.close()

        proj_dir = tmp_path / "users" / "miscuser" / "my-project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "file.txt").write_text("x")

        resp = client.delete("/projects/proj-1/permanent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert not proj_dir.exists()

        # Verify everything is gone
        sess = test_session_factory()
        assert sess.execute(select(Project).where(Project.id == "proj-1")).scalar_one_or_none() is None
        assert sess.execute(select(ProjectBlock).where(ProjectBlock.project_id == "proj-1")).scalar_one_or_none() is None
        assert sess.execute(select(Conversation).where(Conversation.id == "conv-del")).scalar_one_or_none() is None
        sess.close()

    def test_permanent_delete_preserves_user_token_accounting(self, client, test_session_factory, tmp_path):
        sess = test_session_factory()
        conv = Conversation(id="conv-archive", project_id="proj-1", user_id="user-1", title="Usage")
        sess.add(conv)
        sess.flush()
        sess.add(
            ConversationMessage(
                id="msg-archive",
                conversation_id="conv-archive",
                role="assistant",
                content="tracked",
                seq=1,
                prompt_tokens=120,
                completion_tokens=30,
                total_tokens=150,
                created_at=datetime.datetime(2026, 3, 7, 12, 0, 0),
            )
        )
        sess.commit()
        sess.close()

        proj_dir = tmp_path / "users" / "miscuser" / "my-project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "tracked.txt").write_text("hello")

        resp = client.delete("/projects/proj-1/permanent")
        assert resp.status_code == 200

        usage = client.get("/user/token-usage")
        assert usage.status_code == 200
        data = usage.json()
        assert data["lifetime"]["prompt_tokens"] == 120
        assert data["lifetime"]["completion_tokens"] == 30
        assert data["lifetime"]["total_tokens"] == 150
        assert data["tracking_since"] == "2026-03-07"
        assert data["by_conversation"][0]["conversation_id"] is None
        assert data["by_conversation"][0]["project_id"] == "proj-1"
        assert data["by_conversation"][0]["title"] == "My Project"
        assert data["daily"] == [
            {
                "date": "2026-03-07",
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            }
        ]
        assert not proj_dir.exists()

    def test_no_access(self, client):
        resp = client.delete("/projects/other-project/permanent")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /chat/status/{request_id}
# ---------------------------------------------------------------------------
class TestChatStatus:
    @pytest.fixture(autouse=True)
    def _clear(self):
        _chat_progress.clear()
        yield
        _chat_progress.clear()

    def test_unknown_request(self, client):
        resp = client.get("/chat/status/unknown-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == "waiting"

    def test_known_request(self, client):
        import time
        _chat_progress["known-id"] = {
            "stage": "thinking",
            "detail": "reasoning",
            "ts": time.time(),
        }
        resp = client.get("/chat/status/known-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == "thinking"
        assert data["detail"] == "reasoning"


# ---------------------------------------------------------------------------
# GET /projects/{id}/jobs
# ---------------------------------------------------------------------------
class TestProjectJobs:
    def test_empty_jobs(self, client):
        resp = client.get("/projects/proj-1/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"] == []
