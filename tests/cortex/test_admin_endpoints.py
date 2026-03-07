"""
Tests for cortex/admin.py — Admin endpoint integration tests.

Uses TestClient with in-memory SQLite + StaticPool to test:
  - GET  /admin/users
  - PATCH /admin/users/{id}
  - POST  /admin/users/{id}/approve
  - POST  /admin/users/{id}/revoke
  - POST  /admin/users/{id}/promote
  - PATCH /admin/users/{id}/token-limit
  - PATCH /admin/users/{id}/username
  - GET   /admin/token-usage/summary
  - GET   /admin/token-usage
"""

import datetime
import json

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex.models import (
    Base, User, Session as SessionModel,
    Conversation, ConversationMessage,
    DeletedProjectTokenUsage, DeletedProjectTokenDaily,
)
from cortex.app import app


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
def seed_admin(test_session_factory):
    """Create an admin user and a valid session."""
    sess = test_session_factory()
    admin = User(
        id="admin-uid",
        email="admin@example.com",
        role="admin",
        username="admin",
        is_active=True,
    )
    sess.add(admin)
    session_obj = SessionModel(
        id="admin-session-token",
        user_id=admin.id,
        is_valid=True,
        expires_at=datetime.datetime(2099, 1, 1),
    )
    sess.add(session_obj)
    sess.commit()
    sess.close()
    return admin


@pytest.fixture()
def seed_regular_user(test_session_factory):
    """Create a regular (non-admin) user."""
    sess = test_session_factory()
    user = User(
        id="user-uid",
        email="user@example.com",
        role="user",
        username="regularuser",
        is_active=True,
    )
    sess.add(user)
    sess.commit()
    sess.close()
    return user


@pytest.fixture()
def seed_inactive_user(test_session_factory):
    """Create an inactive user for approval tests."""
    sess = test_session_factory()
    user = User(
        id="inactive-uid",
        email="inactive@example.com",
        role="user",
        username="inactiveuser",
        is_active=False,
    )
    sess.add(user)
    sess.commit()
    sess.close()
    return user


@pytest.fixture()
def admin_client(test_session_factory, seed_admin, tmp_path):
    """TestClient authenticated as admin."""
    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.admin.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.admin.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "admin-session-token")
        yield c


@pytest.fixture()
def non_admin_client(test_session_factory, seed_admin, seed_regular_user, tmp_path):
    """TestClient authenticated as a regular (non-admin) user — needs a session."""
    sess = test_session_factory()
    session_obj = SessionModel(
        id="user-session-token",
        user_id="user-uid",
        is_valid=True,
        expires_at=datetime.datetime(2099, 1, 1),
    )
    sess.add(session_obj)
    sess.commit()
    sess.close()

    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.admin.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.admin.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "user-session-token")
        yield c


# ---------------------------------------------------------------------------
# GET /admin/users
# ---------------------------------------------------------------------------
class TestListUsers:
    def test_list_users_as_admin(self, admin_client, seed_regular_user):
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200
        users = resp.json()
        assert isinstance(users, list)
        assert len(users) >= 2  # admin + regular user
        emails = {u["email"] for u in users}
        assert "admin@example.com" in emails
        assert "user@example.com" in emails

    def test_list_users_returns_expected_fields(self, admin_client):
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200
        u = resp.json()[0]
        for key in ("id", "email", "role", "is_active", "created_at"):
            assert key in u

    def test_list_users_non_admin_forbidden(self, non_admin_client):
        resp = non_admin_client.get("/admin/users")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /admin/users/{id}
# ---------------------------------------------------------------------------
class TestUpdateUser:
    def test_deactivate_user(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid",
            json={"is_active": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is False

    def test_change_role_to_admin(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    def test_invalid_role(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid",
            json={"role": "superuser"},
        )
        assert resp.status_code == 400

    def test_cannot_deactivate_self(self, admin_client):
        resp = admin_client.patch(
            "/admin/users/admin-uid",
            json={"is_active": False},
        )
        assert resp.status_code == 400
        assert "own account" in resp.json()["detail"].lower()

    def test_cannot_demote_self(self, admin_client):
        resp = admin_client.patch(
            "/admin/users/admin-uid",
            json={"role": "user"},
        )
        assert resp.status_code == 400
        assert "own account" in resp.json()["detail"].lower()

    def test_update_nonexistent_user(self, admin_client):
        resp = admin_client.patch(
            "/admin/users/no-such-id",
            json={"is_active": True},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/approve
# ---------------------------------------------------------------------------
class TestApproveUser:
    def test_approve_inactive_user(self, admin_client, seed_inactive_user):
        resp = admin_client.post("/admin/users/inactive-uid/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is True
        assert data["id"] == "inactive-uid"

    def test_approve_nonexistent_user(self, admin_client):
        resp = admin_client.post("/admin/users/no-such-id/approve")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/revoke
# ---------------------------------------------------------------------------
class TestRevokeUser:
    def test_revoke_active_user(self, admin_client, seed_regular_user):
        resp = admin_client.post("/admin/users/user-uid/revoke")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is False

    def test_cannot_revoke_self(self, admin_client):
        resp = admin_client.post("/admin/users/admin-uid/revoke")
        assert resp.status_code == 400
        assert "own access" in resp.json()["detail"].lower()

    def test_revoke_nonexistent_user(self, admin_client):
        resp = admin_client.post("/admin/users/no-such-id/revoke")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/promote
# ---------------------------------------------------------------------------
class TestPromoteUser:
    def test_promote_regular_user(self, admin_client, seed_regular_user):
        resp = admin_client.post("/admin/users/user-uid/promote")
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "admin"

    def test_promote_nonexistent_user(self, admin_client):
        resp = admin_client.post("/admin/users/no-such-id/promote")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /admin/users/{id}/token-limit
# ---------------------------------------------------------------------------
class TestTokenLimit:
    def test_set_token_limit(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid/token-limit",
            json={"token_limit": 500000},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_limit"] == 500000

    def test_remove_token_limit(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid/token-limit",
            json={"token_limit": None},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_limit"] is None

    def test_missing_field(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid/token-limit",
            json={},
        )
        assert resp.status_code == 422

    def test_negative_limit(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid/token-limit",
            json={"token_limit": -100},
        )
        assert resp.status_code == 422

    def test_nonexistent_user(self, admin_client):
        resp = admin_client.patch(
            "/admin/users/no-such-id/token-limit",
            json={"token_limit": 100},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /admin/users/{id}/username
# ---------------------------------------------------------------------------
class TestSetUsername:
    def test_set_first_username(self, admin_client, test_session_factory, tmp_path):
        """Create user without username, then set one — home dir should be created."""
        sess = test_session_factory()
        u = User(id="noname-uid", email="noname@example.com", role="user",
                 username=None, is_active=True)
        sess.add(u)
        sess.commit()
        sess.close()

        resp = admin_client.patch(
            "/admin/users/noname-uid/username",
            json={"username": "freshuser"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "freshuser"
        assert data["old_username"] is None
        # Home dir should have been created
        assert (tmp_path / "users" / "freshuser").is_dir()

    def test_invalid_username_format(self, admin_client, seed_regular_user):
        resp = admin_client.patch(
            "/admin/users/user-uid/username",
            json={"username": "BAD USER!"},
        )
        assert resp.status_code == 422

    def test_username_already_taken(self, admin_client, seed_regular_user, test_session_factory):
        """Second user can't claim an existing username."""
        sess = test_session_factory()
        u2 = User(id="dup-uid", email="dup@example.com", role="user",
                   username=None, is_active=True)
        sess.add(u2)
        sess.commit()
        sess.close()

        resp = admin_client.patch(
            "/admin/users/dup-uid/username",
            json={"username": "regularuser"},  # already used by seed_regular_user
        )
        assert resp.status_code == 409

    def test_nonexistent_user(self, admin_client):
        resp = admin_client.patch(
            "/admin/users/no-such-id/username",
            json={"username": "newname"},
        )
        assert resp.status_code == 404

    def test_rename_existing_username(self, admin_client, seed_regular_user, tmp_path):
        """Changing from one username to another should trigger rename_user_dir."""
        # Create old user directory
        old_dir = tmp_path / "users" / "regularuser"
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "test.txt").write_text("data")

        with patch("cortex.admin.rename_user_dir") as mock_rename:
            resp = admin_client.patch(
                "/admin/users/user-uid/username",
                json={"username": "newname"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["username"] == "newname"
            assert data["old_username"] == "regularuser"
            mock_rename.assert_called_once_with("regularuser", "newname")


# ---------------------------------------------------------------------------
# GET /admin/token-usage/summary
# ---------------------------------------------------------------------------
class TestTokenUsageSummary:
    def test_empty_summary(self, admin_client):
        resp = admin_client.get("/admin/token-usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert "daily" in data
        assert isinstance(data["users"], list)
        assert isinstance(data["daily"], list)

    def test_summary_with_messages(self, admin_client, test_session_factory, seed_admin):
        """Seed a conversation with token-tracked messages and verify summary."""
        sess = test_session_factory()
        conv = Conversation(
            id="conv-1",
            user_id="admin-uid",
            project_id="proj-1",
            title="Test conv",
        )
        sess.add(conv)
        sess.flush()
        msg = ConversationMessage(
            id="msg-1",
            conversation_id="conv-1",
            role="assistant",
            content="Hello",
            seq=1,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        sess.add(msg)
        sess.commit()
        sess.close()

        resp = admin_client.get("/admin/token-usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        admin_entry = [u for u in data["users"] if u["user_id"] == "admin-uid"]
        assert len(admin_entry) == 1
        assert admin_entry[0]["total_tokens"] >= 150

    def test_summary_includes_deleted_project_archives(self, admin_client, test_session_factory, seed_admin):
        sess = test_session_factory()
        sess.add(
            DeletedProjectTokenUsage(
                id="archived-usage-1",
                user_id="admin-uid",
                project_id="deleted-proj",
                project_name="Deleted Project",
                conversation_count=1,
                assistant_message_count=3,
                prompt_tokens=90,
                completion_tokens=10,
                total_tokens=100,
                deleted_at=datetime.datetime(2026, 3, 7, 9, 0, 0),
            )
        )
        sess.add(
            DeletedProjectTokenDaily(
                id="archived-daily-1",
                user_id="admin-uid",
                project_id="deleted-proj",
                usage_date="2026-03-07",
                prompt_tokens=90,
                completion_tokens=10,
                total_tokens=100,
                assistant_message_count=3,
            )
        )
        sess.commit()
        sess.close()

        resp = admin_client.get("/admin/token-usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        admin_entry = [u for u in data["users"] if u["user_id"] == "admin-uid"]
        assert len(admin_entry) == 1
        assert admin_entry[0]["total_tokens"] == 100
        assert data["daily"] == [
            {
                "date": "2026-03-07",
                "prompt_tokens": 90,
                "completion_tokens": 10,
                "total_tokens": 100,
            }
        ]


# ---------------------------------------------------------------------------
# GET /admin/token-usage
# ---------------------------------------------------------------------------
class TestTokenUsageDetailed:
    def test_no_filters(self, admin_client):
        resp = admin_client.get("/admin/token-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "filters" in data
        assert "by_conversation" in data
        assert "daily" in data

    def test_filter_by_user(self, admin_client, test_session_factory, seed_admin):
        sess = test_session_factory()
        conv = Conversation(id="conv-2", user_id="admin-uid", project_id="proj-2", title="C2")
        sess.add(conv)
        sess.flush()
        msg = ConversationMessage(
            id="msg-2", conversation_id="conv-2", role="assistant",
            content="x", seq=1, prompt_tokens=10, completion_tokens=5, total_tokens=15,
        )
        sess.add(msg)
        sess.commit()
        sess.close()

        resp = admin_client.get("/admin/token-usage", params={"user_id": "admin-uid"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["filters"]["user_id"] == "admin-uid"
        assert len(data["by_conversation"]) >= 1

    def test_includes_deleted_project_breakdown(self, admin_client, test_session_factory, seed_admin):
        sess = test_session_factory()
        sess.add(
            DeletedProjectTokenUsage(
                id="archived-usage-2",
                user_id="admin-uid",
                project_id="deleted-proj-2",
                project_name="Deleted Project Two",
                conversation_count=2,
                assistant_message_count=4,
                prompt_tokens=40,
                completion_tokens=20,
                total_tokens=60,
                deleted_at=datetime.datetime(2026, 3, 9, 9, 0, 0),
            )
        )
        sess.add(
            DeletedProjectTokenDaily(
                id="archived-daily-2",
                user_id="admin-uid",
                project_id="deleted-proj-2",
                usage_date="2026-03-09",
                prompt_tokens=40,
                completion_tokens=20,
                total_tokens=60,
                assistant_message_count=4,
            )
        )
        sess.commit()
        sess.close()

        resp = admin_client.get("/admin/token-usage", params={"user_id": "admin-uid"})
        assert resp.status_code == 200
        data = resp.json()
        archived_rows = [row for row in data["by_conversation"] if row["project_id"] == "deleted-proj-2"]
        assert len(archived_rows) == 1
        assert archived_rows[0]["conversation_id"] is None
        assert archived_rows[0]["title"] == "Deleted Project Two"
        assert archived_rows[0]["total_tokens"] == 60
        assert data["daily"] == [
            {
                "date": "2026-03-09",
                "prompt_tokens": 40,
                "completion_tokens": 20,
                "total_tokens": 60,
            }
        ]

    def test_non_admin_forbidden(self, non_admin_client):
        resp = non_admin_client.get("/admin/token-usage")
        assert resp.status_code == 403
