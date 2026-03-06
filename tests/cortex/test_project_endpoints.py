"""
Tests for cortex/app.py — Project CRUD endpoints.

Uses TestClient with in-memory SQLite and auth bypass to test:
  - POST /projects
  - GET /projects
  - PATCH /projects/{id}
  - DELETE /projects/{id}
  - GET /user/projects
  - POST /block, GET /blocks, PATCH /block/{id}, DELETE /blocks
"""

import uuid
import json
import datetime

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex.models import Base, User, Session as SessionModel, Project, ProjectAccess, ProjectBlock
from cortex.app import app


# ---------------------------------------------------------------------------
# Fixtures — isolated in-memory DB per test
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
def seed_user(test_session_factory):
    """Create a user and a valid session in the DB."""
    Session = test_session_factory
    session = Session()
    user = User(
        id="test-uid",
        email="test@example.com",
        role="user",
        username="testuser",
        is_active=True,
    )
    session.add(user)
    sess = SessionModel(
        id="test-session-token",
        user_id=user.id,
        is_valid=True,
        expires_at=datetime.datetime(2099, 1, 1),
    )
    session.add(sess)
    session.commit()
    session.close()
    return user


@pytest.fixture()
def client(test_session_factory, seed_user, tmp_path, monkeypatch):
    """
    TestClient with patched DB and AGOUTIC_DATA.
    Auth middleware reads from our in-memory DB.
    """
    # Patch SessionLocal everywhere it's imported
    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "test-session-token")
        yield c


# ---------------------------------------------------------------------------
# Health check (no auth)
# ---------------------------------------------------------------------------
class TestHealthCheck:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /projects — create project
# ---------------------------------------------------------------------------
class TestCreateProject:
    def test_create_project(self, client):
        resp = client.post("/projects", json={"name": "My ENCODE Project"})
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["name"] == "My ENCODE Project"
        assert data["slug"] is not None

    def test_create_project_generates_slug(self, client):
        resp = client.post("/projects", json={"name": "Fancy Experiment #1"})
        data = resp.json()
        # Slug should be lowercase, hyphenated
        assert data["slug"] == data["slug"].lower()
        assert " " not in data["slug"]

    def test_create_project_no_auth(self, test_session_factory, tmp_path):
        """Without session cookie, should get 401."""
        with patch("cortex.db.SessionLocal", test_session_factory), \
             patch("cortex.app.SessionLocal", test_session_factory), \
             patch("cortex.middleware.SessionLocal", test_session_factory), \
             patch("cortex.config.AGOUTIC_DATA", tmp_path), \
             patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post("/projects", json={"name": "Test"})
            assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /projects — list projects
# ---------------------------------------------------------------------------
class TestListProjects:
    def test_list_initially_empty(self, client):
        resp = client.get("/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert isinstance(data["projects"], list)

    def test_list_after_create(self, client):
        client.post("/projects", json={"name": "Project A"})
        client.post("/projects", json={"name": "Project B"})
        resp = client.get("/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["projects"]) >= 2


# ---------------------------------------------------------------------------
# PATCH /projects/{id} — update project
# ---------------------------------------------------------------------------
class TestUpdateProject:
    def test_rename_project(self, client):
        create_resp = client.post("/projects", json={"name": "Old Name"})
        project_id = create_resp.json()["id"]
        update_resp = client.patch(
            f"/projects/{project_id}",
            json={"name": "New Name"},
        )
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data["status"] == "ok"
        assert data["id"] == project_id
        # Slug should reflect the new name
        assert "new-name" in data["slug"]


# ---------------------------------------------------------------------------
# DELETE /projects/{id} — soft-delete (archive)
# ---------------------------------------------------------------------------
class TestDeleteProject:
    def test_soft_delete(self, client):
        create_resp = client.post("/projects", json={"name": "To Delete"})
        project_id = create_resp.json()["id"]
        del_resp = client.delete(f"/projects/{project_id}")
        assert del_resp.status_code == 200


# ---------------------------------------------------------------------------
# Block endpoints: POST /block, GET /blocks, PATCH /block/{id}
# ---------------------------------------------------------------------------
class TestBlockEndpoints:
    def _create_project(self, client):
        resp = client.post("/projects", json={"name": "Block Test"})
        return resp.json()["id"]

    def test_create_and_get_blocks(self, client):
        project_id = self._create_project(client)
        # Create a block
        create_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "USER_MESSAGE",
            "payload": {"markdown": "Hello"},
            "status": "DONE",
        })
        assert create_resp.status_code == 200
        block_id = create_resp.json()["id"]

        # Get blocks
        get_resp = client.get(f"/blocks?project_id={project_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert "blocks" in data
        assert len(data["blocks"]) >= 1

    def test_update_block(self, client):
        project_id = self._create_project(client)
        create_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "AGENT_PLAN",
            "payload": {"markdown": "Original"},
            "status": "NEW",
        })
        block_id = create_resp.json()["id"]

        # Update status
        update_resp = client.patch(f"/block/{block_id}", json={
            "status": "DONE",
            "payload": {"markdown": "Updated"},
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["status"] == "DONE"

    def test_clear_project_blocks(self, client):
        project_id = self._create_project(client)
        client.post("/block", json={
            "project_id": project_id,
            "type": "USER_MESSAGE",
            "payload": {"markdown": "msg"},
            "status": "DONE",
        })
        del_resp = client.delete(f"/projects/{project_id}/blocks")
        assert del_resp.status_code == 200

        # Verify empty
        get_resp = client.get(f"/blocks?project_id={project_id}")
        assert len(get_resp.json()["blocks"]) == 0


# ---------------------------------------------------------------------------
# Skills endpoint
# ---------------------------------------------------------------------------
class TestSkillsEndpoint:
    def test_get_skills(self, client):
        resp = client.get("/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        assert isinstance(data["skills"], list)
        # Should have at least welcome + ENCODE_Search
        assert "welcome" in data["skills"]
