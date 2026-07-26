import datetime
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.app import app
from cortex.models import Project, ProjectAccess, Session as SessionModel, User


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
    user = User(id="u-proxy", email="proxy@example.com", role="user", username="proxyuser", is_active=True)
    sess.add(user)
    sess.add(
        SessionModel(
            id="proxy-session",
            user_id=user.id,
            is_valid=True,
            expires_at=datetime.datetime(2099, 1, 1),
        )
    )
    project = Project(id="proj-proxy", name="Proxy Project", owner_id=user.id, slug="proxy-project")
    sess.add(project)
    sess.add(
        ProjectAccess(
            id=str(uuid.uuid4()),
            project_id=project.id,
            user_id=user.id,
            project_name=project.name,
            role="owner",
        )
    )
    sess.commit()
    sess.close()
    return {"user_id": user.id, "session_id": "proxy-session"}


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


class _FakeResponse:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, calls, response):
        self.calls = calls
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


class TestRemoteProfileProxy:
    def test_list_profiles_proxies_authenticated_user(self, client):
        calls = []
        response = _FakeResponse(200, [{"id": "prof-1", "ssh_host": "host"}])

        with patch("cortex.app._launchpad_rest_base_url", return_value="http://launchpad.test"), \
             patch("cortex.app._launchpad_internal_headers", return_value={"X-Internal-Secret": "secret"}), \
             patch("cortex.app.httpx.AsyncClient", side_effect=lambda timeout: _FakeAsyncClient(calls, response)):
            resp = client.get("/remote-profiles")

        assert resp.status_code == 200
        assert resp.json() == [{"id": "prof-1", "ssh_host": "host"}]
        assert calls == [
            (
                "GET",
                "http://launchpad.test/ssh-profiles",
                {
                    "params": {"user_id": "u-proxy"},
                    "headers": {"X-Internal-Secret": "secret"},
                    "json": None,
                },
            )
        ]

    def test_admin_lists_profiles_for_all_users(self, client, session_factory, seed_data):
        session = session_factory()
        admin = User(id="admin-proxy", email="admin@example.com", role="admin", username="admin", is_active=True)
        session.add(admin)
        session.add(
            SessionModel(
                id="admin-proxy-session",
                user_id=admin.id,
                is_valid=True,
                expires_at=datetime.datetime(2099, 1, 1),
            )
        )
        session.commit()
        session.close()
        client.cookies.set("session", "admin-proxy-session")
        calls = []
        response = _FakeResponse(200, [{"id": "prof-1", "user_id": seed_data["user_id"], "ssh_host": "host"}])

        with patch("cortex.app._launchpad_rest_base_url", return_value="http://launchpad.test"), \
             patch("cortex.app._launchpad_internal_headers", return_value={"X-Internal-Secret": "secret"}), \
             patch("cortex.app.httpx.AsyncClient", side_effect=lambda timeout: _FakeAsyncClient(calls, response)):
            resp = client.get("/remote-profiles")

        assert resp.status_code == 200
        assert resp.json() == [{"id": "prof-1", "user_id": seed_data["user_id"], "ssh_host": "host", "owner_email": "proxy@example.com"}]
        assert calls[0][2]["params"] is None

    def test_create_profile_injects_authenticated_user(self, client):
        calls = []
        response = _FakeResponse(201, {"id": "prof-2", "user_id": "u-proxy", "ssh_host": "gpu1"})

        with patch("cortex.app._launchpad_rest_base_url", return_value="http://launchpad.test"), \
             patch("cortex.app._launchpad_internal_headers", return_value={"X-Internal-Secret": "secret"}), \
             patch("cortex.app.httpx.AsyncClient", side_effect=lambda timeout: _FakeAsyncClient(calls, response)):
            resp = client.post(
                "/remote-profiles",
                json={
                    "nickname": "cluster",
                    "ssh_host": "gpu1",
                    "ssh_port": 22,
                    "ssh_username": "alice",
                    "auth_method": "ssh_agent",
                },
            )

        assert resp.status_code == 201
        assert resp.json()["user_id"] == "u-proxy"
        assert len(calls) == 1
        method, url, kwargs = calls[0]
        assert method == "POST"
        assert url == "http://launchpad.test/ssh-profiles"
        assert kwargs["params"] is None
        assert kwargs["json"]["user_id"] == "u-proxy"
        assert kwargs["json"]["ssh_host"] == "gpu1"

    def test_auth_session_create_forwards_local_password(self, client):
        calls = []
        response = _FakeResponse(200, {"active": True, "message": "Local auth session started"})

        with patch("cortex.app._launchpad_rest_base_url", return_value="http://launchpad.test"), \
             patch("cortex.app._launchpad_internal_headers", return_value={"X-Internal-Secret": "secret"}), \
             patch("cortex.app.httpx.AsyncClient", side_effect=lambda timeout: _FakeAsyncClient(calls, response)):
            resp = client.post(
                "/remote-profiles/prof-1/auth-session",
                json={"local_password": "pw123"},
            )

        assert resp.status_code == 200
        assert resp.json()["active"] is True
        assert len(calls) == 1
        method, url, kwargs = calls[0]
        assert method == "POST"
        assert url == "http://launchpad.test/ssh-profiles/prof-1/auth-session"
        assert kwargs["params"] == {"user_id": "u-proxy"}
        assert kwargs["json"] == {"local_password": "pw123"}