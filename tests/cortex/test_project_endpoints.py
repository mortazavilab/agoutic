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

from common.database import Base
from cortex.models import User, Session as SessionModel, Project, ProjectAccess, ProjectBlock
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


def _seed_user_with_session(
    test_session_factory,
    *,
    user_id: str,
    email: str,
    username: str,
    session_id: str,
    role: str = "user",
    is_active: bool = True,
):
    session = test_session_factory()
    user = User(
        id=user_id,
        email=email,
        role=role,
        username=username,
        is_active=is_active,
    )
    session.add(user)
    session.add(
        SessionModel(
            id=session_id,
            user_id=user_id,
            is_valid=True,
            expires_at=datetime.datetime(2099, 1, 1),
        )
    )
    session.commit()
    session.close()
    return user


def _grant_project_access(
    test_session_factory,
    *,
    user_id: str,
    project_id: str,
    project_name: str,
    role: str,
    invited_by: str | None = None,
):
    session = test_session_factory()
    now = datetime.datetime.utcnow()
    session.add(
        ProjectAccess(
            id=str(uuid.uuid4()),
            user_id=user_id,
            project_id=project_id,
            project_name=project_name,
            role=role,
            invited_by=invited_by,
            created_at=now,
            updated_at=now,
            last_accessed=now,
        )
    )
    session.commit()
    session.close()


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

    def test_llm_models(self, client):
        resp = client.get("/config/llm-models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["models"] == [
            {"key": "default", "model": "gemma4:31b"},
            {"key": "fast", "model": "devstral-small-2:latest"},
            {"key": "smart", "model": "devstral-2:latest"},
            {"key": "coder", "model": "qwen3-coder:latest"},
            {"key": "heavy", "model": "gpt-oss:120b"},
        ]

    def test_reference_genomes(self, client):
        resp = client.get("/config/reference-genomes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == len(data["genomes"])
        assert {"GRCh38", "mm39", "mad1"}.issubset(set(data["genomes"]))
        assert data["default"] == "GRCh38"
        items_by_id = {item["id"]: item for item in data["items"]}
        assert items_by_id["GRCh38"]["aliases"] == ["hg38", "human"]
        assert items_by_id["mm39"]["aliases"] == ["mm10", "mouse"]
        assert items_by_id["mad1"]["assets"]["fasta"] is True
        assert items_by_id["mad1"]["assets"]["kallisto_index"] is False


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


class TestProjectCollaborators:
    def _create_project(self, client, name: str = "Shared Project"):
        resp = client.post("/projects", json={"name": name})
        assert resp.status_code == 200
        return resp.json()

    def _set_session(self, client, session_id: str):
        client.cookies.set("session", session_id)

    def test_owner_can_add_collaborator_by_email(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )

        resp = client.post(
            f"/projects/{project['id']}/collaborators",
            json={"email": "viewer@example.com", "role": "viewer"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"
        assert resp.json()["email"] == "viewer@example.com"
        assert resp.json()["role"] == "viewer"

    def test_admin_can_add_collaborator_to_any_project(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="admin-uid",
            email="admin@example.com",
            username="adminuser",
            session_id="admin-session",
            role="admin",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )

        self._set_session(client, "admin-session")
        resp = client.post(
            f"/projects/{project['id']}/collaborators",
            json={"email": "editor@example.com", "role": "editor"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"
        assert resp.json()["role"] == "editor"

    def test_editor_viewer_and_unrelated_users_cannot_add_collaborators(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="other-uid",
            email="other@example.com",
            username="otheruser",
            session_id="other-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="target-uid",
            email="target@example.com",
            username="targetuser",
            session_id="target-session",
        )

        _grant_project_access(
            test_session_factory,
            user_id="editor-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="editor",
            invited_by="test-uid",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        for session_id in ("editor-session", "viewer-session", "other-session"):
            self._set_session(client, session_id)
            resp = client.post(
                f"/projects/{project['id']}/collaborators",
                json={"email": "target@example.com", "role": "viewer"},
            )
            assert resp.status_code == 403

    def test_add_collaborator_rejects_unknown_inactive_duplicate_and_owner_role(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="inactive-uid",
            email="inactive@example.com",
            username="inactiveuser",
            session_id="inactive-session",
            is_active=False,
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        unknown = client.post(
            f"/projects/{project['id']}/collaborators",
            json={"email": "missing@example.com", "role": "viewer"},
        )
        assert unknown.status_code == 404

        inactive = client.post(
            f"/projects/{project['id']}/collaborators",
            json={"email": "inactive@example.com", "role": "viewer"},
        )
        assert inactive.status_code == 409

        duplicate = client.post(
            f"/projects/{project['id']}/collaborators",
            json={"email": "viewer@example.com", "role": "editor"},
        )
        assert duplicate.status_code == 409

        owner_role = client.post(
            f"/projects/{project['id']}/collaborators",
            json={"email": "viewer@example.com", "role": "owner"},
        )
        assert owner_role.status_code == 422

    def test_owner_and_admin_get_full_collaborator_list(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="admin-uid",
            email="admin@example.com",
            username="adminuser",
            session_id="admin-session",
            role="admin",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="editor-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="editor",
            invited_by="test-uid",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        owner_resp = client.get(f"/projects/{project['id']}/collaborators")
        assert owner_resp.status_code == 200
        owner_emails = {item["email"] for item in owner_resp.json()["collaborators"]}
        assert owner_emails == {"test@example.com", "editor@example.com", "viewer@example.com"}

        self._set_session(client, "admin-session")
        admin_resp = client.get(f"/projects/{project['id']}/collaborators")
        assert admin_resp.status_code == 200
        admin_emails = {item["email"] for item in admin_resp.json()["collaborators"]}
        assert admin_emails == owner_emails

    def test_non_owner_collaborator_sees_full_roster(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="editor-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="editor",
            invited_by="test-uid",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        self._set_session(client, "editor-session")
        resp = client.get(f"/projects/{project['id']}/collaborators")
        assert resp.status_code == 200
        emails = {item["email"] for item in resp.json()["collaborators"]}
        assert emails == {"test@example.com", "editor@example.com", "viewer@example.com"}

    def test_owner_can_update_collaborator_role_and_others_cannot(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="other-uid",
            email="other@example.com",
            username="otheruser",
            session_id="other-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="editor-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="editor",
            invited_by="test-uid",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        owner_update = client.patch(
            f"/projects/{project['id']}/collaborators/viewer-uid",
            json={"role": "editor"},
        )
        assert owner_update.status_code == 200
        assert owner_update.json()["role"] == "editor"

        for session_id in ("editor-session", "viewer-session", "other-session"):
            self._set_session(client, session_id)
            resp = client.patch(
                f"/projects/{project['id']}/collaborators/editor-uid",
                json={"role": "viewer"},
            )
            assert resp.status_code == 403

    def test_owner_and_admin_can_remove_collaborators_but_not_owner(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="admin-uid",
            email="admin@example.com",
            username="adminuser",
            session_id="admin-session",
            role="admin",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        owner_delete = client.delete(f"/projects/{project['id']}/collaborators/viewer-uid")
        assert owner_delete.status_code == 200
        assert owner_delete.json()["status"] == "removed"

        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )
        self._set_session(client, "admin-session")
        admin_delete = client.delete(f"/projects/{project['id']}/collaborators/viewer-uid")
        assert admin_delete.status_code == 200

        owner_delete = client.delete(f"/projects/{project['id']}/collaborators/test-uid")
        assert owner_delete.status_code == 409

    def test_non_owner_cannot_remove_other_collaborators(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="editor-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="editor",
            invited_by="test-uid",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        self._set_session(client, "viewer-session")
        resp = client.delete(f"/projects/{project['id']}/collaborators/editor-uid")
        assert resp.status_code == 403

    def test_owner_can_transfer_ownership_to_existing_collaborator_and_move_project_dir(self, client, test_session_factory, tmp_path):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="editor-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="editor",
            invited_by="test-uid",
        )

        old_dir = tmp_path / "users" / "testuser" / project["slug"] / "data"
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "reads.fastq").write_text("ACGT")

        transfer_resp = client.post(
            f"/projects/{project['id']}/transfer-ownership",
            json={"user_id": "editor-uid"},
        )

        assert transfer_resp.status_code == 200
        assert transfer_resp.json()["status"] == "transferred"
        assert transfer_resp.json()["role"] == "owner"

        owner_roster = client.get(f"/projects/{project['id']}/collaborators")
        assert owner_roster.status_code == 200
        owner_roles = {item["email"]: item["role"] for item in owner_roster.json()["collaborators"]}
        assert owner_roles["editor@example.com"] == "owner"
        assert owner_roles["test@example.com"] == "editor"

        self._set_session(client, "editor-session")
        new_owner_projects = client.get("/projects")
        assert new_owner_projects.status_code == 200
        new_owner_project = next(item for item in new_owner_projects.json()["projects"] if item["id"] == project["id"])
        assert new_owner_project["role"] == "owner"

        new_dir = tmp_path / "users" / "editoruser" / project["slug"] / "data" / "reads.fastq"
        assert new_dir.read_text() == "ACGT"
        assert not (tmp_path / "users" / "testuser" / project["slug"]).exists()

    def test_non_owner_cannot_transfer_ownership(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="editor-uid",
            email="editor@example.com",
            username="editoruser",
            session_id="editor-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="editor-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="editor",
            invited_by="test-uid",
        )

        self._set_session(client, "editor-session")
        transfer_resp = client.post(
            f"/projects/{project['id']}/transfer-ownership",
            json={"user_id": "test-uid"},
        )
        assert transfer_resp.status_code == 403

    def test_collaborator_can_leave_and_owner_cannot(self, client, test_session_factory):
        project = self._create_project(client)
        _seed_user_with_session(
            test_session_factory,
            user_id="viewer-uid",
            email="viewer@example.com",
            username="vieweruser",
            session_id="viewer-session",
        )
        _grant_project_access(
            test_session_factory,
            user_id="viewer-uid",
            project_id=project["id"],
            project_name=project["name"],
            role="viewer",
            invited_by="test-uid",
        )

        self._set_session(client, "viewer-session")
        leave_resp = client.post(f"/projects/{project['id']}/leave")
        assert leave_resp.status_code == 200
        assert leave_resp.json()["status"] == "left"

        projects_resp = client.get("/projects")
        assert projects_resp.status_code == 200
        assert all(item["id"] != project["id"] for item in projects_resp.json()["projects"])

        self._set_session(client, "test-session-token")
        owner_leave = client.post(f"/projects/{project['id']}/leave")
        assert owner_leave.status_code == 409


# ---------------------------------------------------------------------------
# PATCH /projects/{id} — update project
# ---------------------------------------------------------------------------
class TestUpdateProject:
    def test_rename_project(self, client):
        create_resp = client.post("/projects", json={"name": "Old Name"})
        original = create_resp.json()
        project_id = create_resp.json()["id"]
        update_resp = client.patch(
            f"/projects/{project_id}",
            json={"name": "New Name"},
        )
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data["status"] == "ok"
        assert data["id"] == project_id
        assert data["name"] == "New Name"
        # Slug syncs with name so the local folder matches.
        assert data["slug"] == "new-name"

        list_resp = client.get("/projects")
        assert list_resp.status_code == 200
        projects = list_resp.json()["projects"]
        renamed = next(p for p in projects if p["id"] == project_id)
        assert renamed["name"] == "New Name"

    def test_rename_duplicate_name_rejected(self, client):
        first = client.post("/projects", json={"name": "Alpha Project"}).json()
        second = client.post("/projects", json={"name": "Beta Project"}).json()

        resp = client.patch(
            f"/projects/{second['id']}",
            json={"name": first["name"]},
        )
        assert resp.status_code == 409

    def test_rename_unauthorized_rejected(self, client, test_session_factory):
        project = client.post("/projects", json={"name": "Owner Project"}).json()

        Session = test_session_factory
        session = Session()
        other_user = User(
            id="other-uid",
            email="other@example.com",
            role="user",
            username="otheruser",
            is_active=True,
        )
        session.add(other_user)
        other_sess = SessionModel(
            id="other-session-token",
            user_id=other_user.id,
            is_valid=True,
            expires_at=datetime.datetime(2099, 1, 1),
        )
        session.add(other_sess)
        session.commit()
        session.close()

        client.cookies.set("session", "other-session-token")
        resp = client.patch(
            f"/projects/{project['id']}",
            json={"name": "Hijack Name"},
        )
        assert resp.status_code == 403

    def test_project_id_stable_after_rename(self, client):
        created = client.post("/projects", json={"name": "Stable ID"}).json()
        project_id = created["id"]

        resp = client.patch(
            f"/projects/{project_id}",
            json={"name": "Stable ID Renamed"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == project_id

    def test_blocks_accessible_after_rename(self, client):
        created = client.post("/projects", json={"name": "Flow Project"}).json()
        project_id = created["id"]

        block_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "USER_MESSAGE",
            "payload": {"markdown": "Hello"},
            "status": "DONE",
        })
        assert block_resp.status_code == 200
        block_id = block_resp.json()["id"]

        rename_resp = client.patch(
            f"/projects/{project_id}",
            json={"name": "Flow Project Renamed"},
        )
        assert rename_resp.status_code == 200

        blocks_resp = client.get(f"/blocks?project_id={project_id}")
        assert blocks_resp.status_code == 200
        block_ids = {b["id"] for b in blocks_resp.json()["blocks"]}
        assert block_id in block_ids


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


class TestProjectTasks:
    def _create_project(self, client):
        resp = client.post("/projects", json={"name": "Task Test"})
        return resp.json()["id"]

    def test_tasks_include_pending_approval(self, client):
        project_id = self._create_project(client)
        create_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve workflow"},
            "status": "PENDING",
        })
        assert create_resp.status_code == 200

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]
        assert len(sections["pending"]) == 1
        assert sections["pending"][0]["kind"] == "approval"
        assert sections["pending"][0]["title"] == "Approve workflow"

    def test_tasks_include_run_analysis_and_follow_up(self, client):
        project_id = self._create_project(client)

        run_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-123",
                "sample_name": "sample-a",
                "mode": "DNA",
                "job_status": {"progress_percent": 100},
            },
            "status": "DONE",
        })
        assert run_resp.status_code == 200

        analysis_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "AGENT_PLAN",
            "payload": {
                "skill": "analyze_job_results",
                "markdown": "### 📊 Analysis: sample-a\n\nAnalysis complete.",
            },
            "status": "DONE",
        })
        assert analysis_resp.status_code == 200

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]

        completed_tasks = sections["completed"]
        follow_up_tasks = sections["follow_up"]
        completed_kinds = {task["kind"] for task in completed_tasks}
        follow_up_kinds = {task["kind"] for task in follow_up_tasks}

        assert "run" in completed_kinds
        assert "analysis" in completed_kinds
        assert "result_review" in follow_up_kinds

        run_task = next(task for task in completed_tasks if task["kind"] == "run")
        child_kinds = {task["kind"] for task in run_task["children"]}
        assert "analysis" in child_kinds
        assert "result_review" in child_kinds

        review_task = next(task for task in follow_up_tasks if task["kind"] == "result_review")
        assert review_task["parent_task_id"] == run_task["id"]

    def test_task_action_complete_and_reopen(self, client):
        project_id = self._create_project(client)

        client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-456",
                "sample_name": "sample-b",
                "mode": "DNA",
                "job_status": {"progress_percent": 100},
            },
            "status": "DONE",
        })

        client.post("/block", json={
            "project_id": project_id,
            "type": "AGENT_PLAN",
            "payload": {
                "skill": "analyze_job_results",
                "markdown": "### 📊 Analysis: sample-b\n\nAnalysis complete.",
            },
            "status": "DONE",
        })

        initial = client.get(f"/projects/{project_id}/tasks")
        review_task = next(
            task for task in initial.json()["sections"]["follow_up"]
            if task["kind"] == "result_review"
        )

        complete_resp = client.patch(
            f"/projects/{project_id}/tasks/{review_task['id']}",
            json={"action": "complete"},
        )
        assert complete_resp.status_code == 200
        assert complete_resp.json()["status"] == "COMPLETED"

        refreshed = client.get(f"/projects/{project_id}/tasks")
        completed_review = next(
            task for task in refreshed.json()["sections"]["completed"]
            if task["kind"] == "result_review"
        )
        assert completed_review["id"] == review_task["id"]

        reopen_resp = client.patch(
            f"/projects/{project_id}/tasks/{review_task['id']}",
            json={"action": "reopen"},
        )
        assert reopen_resp.status_code == 200
        assert reopen_resp.json()["status"] == "FOLLOW_UP"

    def test_task_action_archive_hides_task(self, client):
        project_id = self._create_project(client)
        client.post("/block", json={
            "project_id": project_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve workflow"},
            "status": "PENDING",
        })

        task_resp = client.get(f"/projects/{project_id}/tasks")
        task_id = task_resp.json()["sections"]["pending"][0]["id"]

        archive_resp = client.patch(
            f"/projects/{project_id}/tasks/{task_id}",
            json={"action": "archive"},
        )
        assert archive_resp.status_code == 200

        refreshed = client.get(f"/projects/{project_id}/tasks")
        sections = refreshed.json()["sections"]
        assert all(task["id"] != task_id for items in sections.values() for task in items)

    def test_download_and_workflow_stage_children(self, client):
        project_id = self._create_project(client)

        client.post("/block", json={
            "project_id": project_id,
            "type": "DOWNLOAD_TASK",
            "payload": {
                "files": [
                    {"filename": "reads_1.fastq.gz", "url": "https://example.test/reads_1.fastq.gz"},
                    {"filename": "reads_2.fastq.gz", "url": "https://example.test/reads_2.fastq.gz"},
                ],
                "downloaded": 1,
                "total_files": 2,
                "current_file": "reads_2.fastq.gz",
            },
            "status": "RUNNING",
        })

        client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-789",
                "sample_name": "sample-c",
                "mode": "DNA",
                "job_status": {
                    "progress_percent": 60,
                    "tasks": {
                        "completed": ["mainWorkflow:basecall (1)"],
                        "running": ["mainWorkflow:align (1)"],
                    },
                },
            },
            "status": "RUNNING",
        })

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]

        running_tasks = sections["running"]
        download_task = next(task for task in running_tasks if task["kind"] == "download")
        run_task = next(task for task in running_tasks if task["kind"] == "run")

        download_child_statuses = {child["title"]: child["status"] for child in download_task["children"]}
        assert download_child_statuses["reads_1.fastq.gz"] == "COMPLETED"
        assert download_child_statuses["reads_2.fastq.gz"] == "RUNNING"

        workflow_child_statuses = {child["title"]: child["status"] for child in run_task["children"]}
        assert workflow_child_statuses["mainWorkflow:basecall (1)"] == "COMPLETED"
        assert workflow_child_statuses["mainWorkflow:align (1)"] == "RUNNING"

    def test_workflow_plan_projects_ordered_steps(self, client):
        project_id = self._create_project(client)

        workflow_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "WORKFLOW_PLAN",
            "payload": {
                "workflow_type": "local_sample_intake",
                "title": "Process local sample Jamshid",
                "sample_name": "Jamshid",
                "status": "RUNNING",
                "next_step": "run_dogme",
                "run_uuid": "run-managed",
                "steps": [
                    {
                        "id": "stage_input",
                        "kind": "copy_sample",
                        "title": "Stage Jamshid into user data",
                        "status": "COMPLETED",
                        "order_index": 0,
                    },
                    {
                        "id": "run_dogme",
                        "kind": "run",
                        "title": "Run Dogme for Jamshid",
                        "status": "RUNNING",
                        "order_index": 1,
                        "run_uuid": "run-managed",
                        "block_id": "job-managed",
                    },
                    {
                        "id": "analyze_results",
                        "kind": "analysis",
                        "title": "Analyze results for Jamshid",
                        "status": "PENDING",
                        "order_index": 2,
                        "run_uuid": "run-managed",
                    },
                ],
            },
            "status": "RUNNING",
        })
        assert workflow_resp.status_code == 200
        workflow_block_id = workflow_resp.json()["id"]

        run_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-managed",
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "workflow_plan_block_id": workflow_block_id,
                "job_status": {"progress_percent": 20},
            },
            "status": "RUNNING",
        })
        assert run_resp.status_code == 200

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]

        workflow_task = next(task for task in sections["running"] if task["kind"] == "workflow_plan")
        child_titles = [child["title"] for child in workflow_task["children"]]
        assert child_titles == [
            "Stage Jamshid into user data",
            "Run Dogme for Jamshid",
            "Analyze results for Jamshid",
        ]
        top_level_running = [task for task in sections["running"] if task["parent_task_id"] is None]
        assert all(task["kind"] != "run" for task in top_level_running if task["id"] != workflow_task["id"])

    def test_stale_running_workflow_moves_to_follow_up(self, client, test_session_factory):
        project_id = self._create_project(client)

        workflow_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "WORKFLOW_PLAN",
            "payload": {
                "workflow_type": "remote_sample_intake",
                "title": "Stage remote sample for Jamshid",
                "sample_name": "Jamshid",
                "status": "RUNNING",
                "steps": [
                    {
                        "id": "stage_input",
                        "kind": "remote_stage",
                        "title": "Stage input for Jamshid",
                        "status": "RUNNING",
                        "order_index": 0,
                    }
                ],
            },
            "status": "RUNNING",
        })
        assert workflow_resp.status_code == 200
        workflow_block_id = workflow_resp.json()["id"]

        Session = test_session_factory
        session = Session()
        workflow_block = session.query(ProjectBlock).filter(ProjectBlock.id == workflow_block_id).one()
        workflow_block.created_at = datetime.datetime.utcnow() - datetime.timedelta(days=3)
        session.commit()
        session.close()

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]

        assert sections["running"] == []
        stale_task = next(task for task in sections["follow_up"] if task["kind"] == "workflow_plan")
        assert stale_task["title"] == "Stage remote sample for Jamshid"
        assert stale_task["metadata"]["is_stale"] is True
        assert stale_task["metadata"]["stale_original_status"] == "RUNNING"

    def test_clear_blocks_clears_tasks(self, client):
        project_id = self._create_project(client)
        client.post("/block", json={
            "project_id": project_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve workflow"},
            "status": "PENDING",
        })

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        assert len(task_resp.json()["sections"]["pending"]) == 1

        clear_resp = client.delete(f"/projects/{project_id}/blocks")
        assert clear_resp.status_code == 200

        refreshed = client.get(f"/projects/{project_id}/tasks")
        assert refreshed.status_code == 200
        sections = refreshed.json()["sections"]
        assert all(len(items) == 0 for items in sections.values())

    def test_all_tasks_aggregates_across_projects(self, client):
        alpha_id = client.post("/projects", json={"name": "Alpha Project"}).json()["id"]
        beta_id = client.post("/projects", json={"name": "Beta Project"}).json()["id"]

        client.post("/block", json={
            "project_id": alpha_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve alpha workflow"},
            "status": "PENDING",
        })
        client.post("/block", json={
            "project_id": beta_id,
            "type": "STAGING_TASK",
            "payload": {
                "sample_name": "beta-sample",
                "mode": "DNA",
                "staging_task_id": "stage-beta-1",
                "progress_percent": 45,
            },
            "status": "RUNNING",
        })

        resp = client.get("/tasks")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total_projects"] == 2
        assert data["projects_with_tasks"] == 2

        pending_task = data["sections"]["pending"][0]
        running_task = data["sections"]["running"][0]

        assert pending_task["project_id"] == alpha_id
        assert pending_task["project_name"] == "Alpha Project"
        assert pending_task["project_is_archived"] is False

        assert running_task["project_id"] == beta_id
        assert running_task["project_name"] == "Beta Project"
        assert running_task["kind"] == "stage_transfer"
        assert running_task["metadata"]["sample_name"] == "beta-sample"
        assert running_task["metadata"]["staging_task_id"] == "stage-beta-1"

    def test_all_tasks_excludes_archived_projects_by_default(self, client):
        project_id = client.post("/projects", json={"name": "Archive Me"}).json()["id"]
        client.post("/block", json={
            "project_id": project_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve archived workflow"},
            "status": "PENDING",
        })

        archive_resp = client.delete(f"/projects/{project_id}")
        assert archive_resp.status_code == 200

        hidden_resp = client.get("/tasks")
        assert hidden_resp.status_code == 200
        hidden_sections = hidden_resp.json()["sections"]
        assert all(len(items) == 0 for items in hidden_sections.values())

        visible_resp = client.get("/tasks", params={"include_archived": True})
        assert visible_resp.status_code == 200
        visible_data = visible_resp.json()
        assert visible_data["total_projects"] == 1
        assert visible_data["projects_with_tasks"] == 1

        archived_task = visible_data["sections"]["pending"][0]
        assert archived_task["project_id"] == project_id
        assert archived_task["project_name"] == "Archive Me"
        assert archived_task["project_is_archived"] is True


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
