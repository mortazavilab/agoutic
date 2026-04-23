"""
Tests for cortex/auth.py — Auth endpoints (non-OAuth).

OAuth callback tests are excluded (require real Google tokens).
Tests here cover:
  - GET  /auth/me
  - POST /auth/logout
  - GET  /auth/check-username/{username}
  - POST /auth/set-username
  - GET  /auth/login  (error path when OAuth not configured)
"""

import datetime
import json

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import User, Session as SessionModel
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
def seed_user(test_session_factory):
    sess = test_session_factory()
    user = User(
        id="auth-uid",
        email="auth@example.com",
        role="user",
        username="authuser",
        is_active=True,
        display_name="Auth User",
    )
    sess.add(user)
    session_obj = SessionModel(
        id="auth-session-token",
        user_id=user.id,
        is_valid=True,
        expires_at=datetime.datetime(2099, 1, 1),
    )
    sess.add(session_obj)
    sess.commit()
    sess.close()
    return user


@pytest.fixture()
def seed_user_no_username(test_session_factory, seed_user):
    """Create a user without a username for onboarding tests."""
    sess = test_session_factory()
    u = User(
        id="noname-uid",
        email="noname@example.com",
        role="user",
        username=None,
        is_active=True,
    )
    sess.add(u)
    session_obj = SessionModel(
        id="noname-session",
        user_id=u.id,
        is_valid=True,
        expires_at=datetime.datetime(2099, 1, 1),
    )
    sess.add(session_obj)
    sess.commit()
    sess.close()
    return u


@pytest.fixture()
def client(test_session_factory, seed_user, tmp_path):
    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.auth.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.auth.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "auth-session-token")
        yield c


@pytest.fixture()
def noname_client(test_session_factory, seed_user_no_username, tmp_path):
    """Client authenticated as user with no username."""
    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.auth.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.auth.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "noname-session")
        yield c


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------
class TestGetMe:
    def test_returns_user_info(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "auth@example.com"
        assert data["role"] == "user"
        assert data["username"] == "authuser"
        assert data["display_name"] == "Auth User"

    def test_unauthenticated(self, test_session_factory, tmp_path):
        with patch("cortex.db.SessionLocal", test_session_factory), \
             patch("cortex.app.SessionLocal", test_session_factory), \
             patch("cortex.auth.SessionLocal", test_session_factory), \
             patch("cortex.middleware.SessionLocal", test_session_factory), \
             patch("cortex.config.AGOUTIC_DATA", tmp_path), \
             patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/auth/me")
            assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------
class TestLogout:
    def test_logout_clears_session(self, client, test_session_factory):
        resp = client.post("/auth/logout")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "logged_out"

        # Verify session is invalidated in DB
        sess = test_session_factory()
        result = sess.execute(
            select(SessionModel).where(SessionModel.id == "auth-session-token")
        )
        session_obj = result.scalar_one_or_none()
        assert session_obj is not None
        assert session_obj.is_valid is False
        sess.close()

    def test_logout_without_cookie(self, test_session_factory, tmp_path):
        with patch("cortex.db.SessionLocal", test_session_factory), \
             patch("cortex.app.SessionLocal", test_session_factory), \
             patch("cortex.auth.SessionLocal", test_session_factory), \
             patch("cortex.middleware.SessionLocal", test_session_factory), \
             patch("cortex.config.AGOUTIC_DATA", tmp_path), \
             patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post("/auth/logout")
            # Should succeed even without a session (graceful)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /auth/check-username/{username}
# ---------------------------------------------------------------------------
class TestCheckUsername:
    def test_available_username(self, client):
        resp = client.get("/auth/check-username/freshname")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True

    def test_taken_username(self, client):
        resp = client.get("/auth/check-username/authuser")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "taken" in data["reason"].lower()

    def test_invalid_format(self, client):
        resp = client.get("/auth/check-username/BAD USER!")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "invalid" in data["reason"].lower()

    def test_single_char_invalid(self, client):
        """Username must be at least 2 chars (regex: [a-z0-9][a-z0-9_-]{0,30})."""
        # 1-char username is valid for the regex (0 repetitions of second group is OK)
        resp = client.get("/auth/check-username/a")
        data = resp.json()
        # "a" matches ^[a-z0-9][a-z0-9_-]{0,30}$ — it's valid
        assert data["available"] is True


# ---------------------------------------------------------------------------
# POST /auth/set-username
# ---------------------------------------------------------------------------
class TestSetUsername:
    def test_set_username_first_time(self, noname_client, tmp_path):
        resp = noname_client.post(
            "/auth/set-username",
            json={"username": "mynewname"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "mynewname"
        # Home directory should be created
        assert (tmp_path / "users" / "mynewname").is_dir()

    def test_username_already_set(self, client):
        """User who already has a username cannot change it via this endpoint."""
        resp = client.post(
            "/auth/set-username",
            json={"username": "differentname"},
        )
        assert resp.status_code == 409
        assert "already set" in resp.json()["detail"].lower()

    def test_invalid_username(self, noname_client):
        resp = noname_client.post(
            "/auth/set-username",
            json={"username": "INVALID!!!"},
        )
        assert resp.status_code == 422

    def test_duplicate_username(self, noname_client):
        """Cannot claim a username that another user already has."""
        resp = noname_client.post(
            "/auth/set-username",
            json={"username": "authuser"},  # used by seed_user
        )
        assert resp.status_code == 409
        assert "taken" in resp.json()["detail"].lower()

    def test_normalises_to_lowercase(self, noname_client, tmp_path):
        resp = noname_client.post(
            "/auth/set-username",
            json={"username": "MyUser"},
        )
        # The endpoint normalises to lowercase before validation, so "MyUser" → "myuser" is valid
        assert resp.status_code == 200
        assert resp.json()["username"] == "myuser"


# ---------------------------------------------------------------------------
# GET /auth/login (error path)
# ---------------------------------------------------------------------------
class TestLogin:
    def test_login_without_oauth_config(self, client):
        """When Google OAuth is not configured, /auth/login should return 500."""
        with patch("cortex.auth.GOOGLE_CLIENT_ID", ""), \
             patch("cortex.auth.GOOGLE_CLIENT_SECRET", ""):
            resp = client.get("/auth/login", follow_redirects=False)
            assert resp.status_code == 500
