"""
Tests for cortex/app.py — Conversation, user, and statistics endpoints.

Uses TestClient with in-memory SQLite + StaticPool to test:
  - GET  /projects/{id}/conversations
  - GET  /conversations/{id}/messages
  - GET  /user/last-project
  - GET  /user/projects
  - POST /projects/{id}/access
  - GET  /user/token-usage
  - GET  /projects/{id}/stats
  - GET  /projects/{id}/files
"""

import datetime
import json
import uuid

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex.models import (
    Base, User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage,
)
from cortex.app import app

# Also import launchpad models so dogme_jobs table gets created
try:
    from launchpad.models import Base as LaunchpadBase
    _LAUNCHPAD_AVAILABLE = True
except ImportError:
    _LAUNCHPAD_AVAILABLE = False


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
    if _LAUNCHPAD_AVAILABLE:
        LaunchpadBase.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture()
def seed_data(test_session_factory, tmp_path):
    """Create user + session + project + project access."""
    sess = test_session_factory()

    user = User(
        id="user-1",
        email="conv@example.com",
        role="user",
        username="convuser",
        is_active=True,
    )
    sess.add(user)

    session_obj = SessionModel(
        id="conv-session",
        user_id=user.id,
        is_valid=True,
        expires_at=datetime.datetime(2099, 1, 1),
    )
    sess.add(session_obj)

    project = Project(
        id="proj-1",
        name="Test Project",
        owner_id="user-1",
        slug="test-project",
    )
    sess.add(project)
    sess.flush()

    access = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id="user-1",
        project_id="proj-1",
        project_name="Test Project",
        role="owner",
        last_accessed=datetime.datetime.utcnow(),
    )
    sess.add(access)
    sess.commit()
    sess.close()
    return {"user": user, "project": project}


@pytest.fixture()
def client(test_session_factory, seed_data, tmp_path):
    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "conv-session")
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_conversation(session_factory, conv_id="conv-1", proj_id="proj-1",
                       user_id="user-1", title="Test Conv",
                       messages=None):
    """Seed a conversation with optional messages."""
    sess = session_factory()
    conv = Conversation(
        id=conv_id,
        project_id=proj_id,
        user_id=user_id,
        title=title,
    )
    sess.add(conv)
    sess.flush()

    if messages:
        for seq, (role, content, tokens) in enumerate(messages, 1):
            msg = ConversationMessage(
                id=f"{conv_id}-msg-{seq}",
                conversation_id=conv_id,
                role=role,
                content=content,
                seq=seq,
                prompt_tokens=tokens.get("prompt") if tokens else None,
                completion_tokens=tokens.get("completion") if tokens else None,
                total_tokens=tokens.get("total") if tokens else None,
            )
            sess.add(msg)

    sess.commit()
    sess.close()


# ---------------------------------------------------------------------------
# GET /projects/{id}/conversations
# ---------------------------------------------------------------------------
class TestProjectConversations:
    def test_empty(self, client):
        resp = client.get("/projects/proj-1/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversations"] == []

    def test_with_conversations(self, client, test_session_factory):
        _seed_conversation(test_session_factory, "conv-1", title="First")
        _seed_conversation(test_session_factory, "conv-2", title="Second")

        resp = client.get("/projects/proj-1/conversations")
        assert resp.status_code == 200
        convos = resp.json()["conversations"]
        assert len(convos) == 2
        ids = {c["id"] for c in convos}
        assert "conv-1" in ids

    def test_no_access(self, client):
        """User without access to 'other-proj' gets 403."""
        resp = client.get("/projects/other-proj/conversations")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /conversations/{id}/messages
# ---------------------------------------------------------------------------
class TestConversationMessages:
    def test_get_messages(self, client, test_session_factory):
        _seed_conversation(test_session_factory, messages=[
            ("user", "Hello", None),
            ("assistant", "Hi there!", {"prompt": 10, "completion": 5, "total": 15}),
        ])

        resp = client.get("/conversations/conv-1/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == "conv-1"
        msgs = data["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[0]["seq"] < msgs[1]["seq"]

    def test_nonexistent_conversation(self, client):
        resp = client.get("/conversations/no-such-conv/messages")
        assert resp.status_code == 404

    def test_access_denied_other_user(self, client, test_session_factory):
        """Cannot read messages from another user's conversation."""
        sess = test_session_factory()
        other = User(id="other-uid", email="other@x.com", role="user",
                     username="other", is_active=True)
        sess.add(other)
        sess.flush()
        conv = Conversation(id="other-conv", project_id="proj-1",
                            user_id="other-uid", title="X")
        sess.add(conv)
        sess.commit()
        sess.close()

        resp = client.get("/conversations/other-conv/messages")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /user/last-project
# ---------------------------------------------------------------------------
class TestLastProject:
    def test_no_last_project(self, client):
        resp = client.get("/user/last-project")
        assert resp.status_code == 200
        assert resp.json()["last_project_id"] is None

    def test_with_last_project(self, client, test_session_factory):
        sess = test_session_factory()
        from sqlalchemy import select
        user = sess.execute(select(User).where(User.id == "user-1")).scalar_one()
        user.last_project_id = "proj-1"
        sess.commit()
        sess.close()

        resp = client.get("/user/last-project")
        assert resp.status_code == 200
        assert resp.json()["last_project_id"] == "proj-1"


# ---------------------------------------------------------------------------
# GET /user/projects
# ---------------------------------------------------------------------------
class TestUserProjects:
    def test_returns_accessed_projects(self, client):
        resp = client.get("/user/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert len(data["projects"]) >= 1
        assert data["projects"][0]["id"] == "proj-1"


# ---------------------------------------------------------------------------
# POST /projects/{id}/access
# ---------------------------------------------------------------------------
class TestRecordAccess:
    def test_record_access(self, client):
        resp = client.post("/projects/proj-1/access")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /user/token-usage
# ---------------------------------------------------------------------------
class TestUserTokenUsage:
    def test_empty(self, client):
        resp = client.get("/user/token-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lifetime"]["total_tokens"] == 0
        assert data["by_conversation"] == []
        assert data["daily"] == []

    def test_with_messages(self, client, test_session_factory):
        _seed_conversation(test_session_factory, messages=[
            ("user", "Hi", None),
            ("assistant", "Hello!", {"prompt": 50, "completion": 20, "total": 70}),
            ("assistant", "More.", {"prompt": 30, "completion": 10, "total": 40}),
        ])

        resp = client.get("/user/token-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lifetime"]["total_tokens"] >= 110
        assert len(data["by_conversation"]) >= 1


# ---------------------------------------------------------------------------
# GET /projects/{id}/stats
# ---------------------------------------------------------------------------
class TestProjectStats:
    def test_basic_stats(self, client, test_session_factory, tmp_path):
        # Create a block
        sess = test_session_factory()
        blk = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-1",
            owner_id="user-1",
            type="USER_MESSAGE",
            seq=1,
            payload_json=json.dumps({"text": "hello"}),
        )
        sess.add(blk)
        sess.commit()
        sess.close()

        # Create project dir so disk usage scan works
        proj_dir = tmp_path / "users" / "convuser" / "test-project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "test.txt").write_text("data")

        with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
            resp = client.get("/projects/proj-1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "proj-1"
        assert data["message_count"] >= 1
        assert "token_usage" in data
        assert data["disk_usage_bytes"] > 0

    def test_no_access(self, client):
        resp = client.get("/projects/other-proj/stats")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /projects/{id}/files
# ---------------------------------------------------------------------------
class TestProjectFiles:
    def test_empty_project(self, client, tmp_path):
        from pathlib import Path
        # Create project dir but no files
        proj_dir = tmp_path / "users" / "convuser" / "test-project"
        proj_dir.mkdir(parents=True, exist_ok=True)

        with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
            resp = client.get("/projects/proj-1/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_count"] == 0

    def test_with_files(self, client, tmp_path, test_session_factory):
        from pathlib import Path
        proj_dir = tmp_path / "users" / "convuser" / "test-project"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "results.csv").write_text("a,b\n1,2")
        (proj_dir / "data").mkdir()
        (proj_dir / "data" / "reads.fastq").write_text("@SEQ")

        with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
            resp = client.get("/projects/proj-1/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_count"] == 2
        assert data["total_size_bytes"] > 0
        names = {f["name"] for f in data["files"]}
        assert "results.csv" in names
        assert "reads.fastq" in names
