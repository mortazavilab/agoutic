"""Tests for launchpad/backends/local_auth_sessions.py."""

import errno
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from launchpad.backends.local_auth_sessions import LocalAuthSession, LocalAuthSessionManager, _launch_helper_via_su


class DummyProcess:
    def __init__(self, returncode=0):
        self._returncode = returncode

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return self._returncode


def test_launch_helper_treats_pty_eio_as_eof(monkeypatch, tmp_path):
    log_file = tmp_path / "helper.log"

    monkeypatch.setattr("launchpad.backends.local_auth_sessions._ensure_socket_dir", lambda: None)
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.pty.openpty", lambda: (10, 11))
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.subprocess.Popen", lambda *args, **kwargs: DummyProcess(0))
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.os.close", lambda fd: None)
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.select.select", lambda *args, **kwargs: ([10], [], []))

    def fake_read(fd, count):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr("launchpad.backends.local_auth_sessions.os.read", fake_read)
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.time.time", MagicMock(side_effect=[0, 0, 0]))

    _launch_helper_via_su("alice", "secret", "127.0.0.1", "/tmp/session.port", "/tmp/session.pid", str(log_file), "token-123")


@pytest.mark.asyncio
async def test_close_session_ignores_permission_errors_on_runtime_files(monkeypatch, tmp_path):
    manager = LocalAuthSessionManager()
    now = datetime.now(timezone.utc)
    port_file = tmp_path / "session.port"
    pid_file = tmp_path / "session.pid"
    log_file = tmp_path / "session.log"
    port_file.write_text("12345")
    pid_file.write_text("999")
    log_file.write_text("log")

    session = LocalAuthSession(
        session_id="sess-1",
        profile_id="p1",
        user_id="u1",
        local_username="alice",
        helper_host="127.0.0.1",
        helper_port=12345,
        port_file=str(port_file),
        pid_file=str(pid_file),
        log_file=str(log_file),
        auth_token="token-123",
        helper_pid=None,
        created_at=now,
        last_used_at=now,
    )
    manager._sessions[session.session_id] = session
    manager._by_profile[(session.user_id, session.profile_id)] = session.session_id

    async def fake_invoke(session_obj, payload):
        return {"ok": True}

    monkeypatch.setattr(manager, "invoke", fake_invoke)

    original_unlink = Path.unlink

    def fake_unlink(self, missing_ok=False):
        if self == port_file:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(self))
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert await manager.close_session("sess-1") is True
    assert not pid_file.exists()
    assert not log_file.exists()


@pytest.mark.asyncio
async def test_get_active_session_loads_shared_metadata_from_disk(monkeypatch, tmp_path):
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.LOCAL_AUTH_SOCKET_DIR", tmp_path)

    now = datetime.now(timezone.utc)
    metadata_path = tmp_path / "agoutic-local-auth-session-u1-p1.json"
    metadata_path.write_text(
        """
{
  "session_id": "sess-1",
  "profile_id": "p1",
  "user_id": "u1",
  "local_username": "alice",
  "helper_host": "127.0.0.1",
  "helper_port": 12345,
  "port_file": "/tmp/session.port",
  "pid_file": "/tmp/session.pid",
  "log_file": "/tmp/session.log",
  "auth_token": "token-123",
  "helper_pid": 999,
  "created_at": "%s",
  "last_used_at": "%s"
}
        """.strip() % (now.isoformat(), now.isoformat()),
        encoding="utf-8",
    )

    manager = LocalAuthSessionManager()
    profile = SimpleNamespace(id="p1", user_id="u1")
    calls = []

    async def fake_invoke(session_obj, payload):
        calls.append((session_obj.session_id, payload["op"]))
        return {"ok": True}

    monkeypatch.setattr(manager, "invoke", fake_invoke)

    session = await manager.get_active_session(profile)

    assert session is not None
    assert session.session_id == "sess-1"
    assert calls == [("sess-1", "ping")]


@pytest.mark.asyncio
async def test_close_session_removes_shared_metadata_file(monkeypatch, tmp_path):
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.LOCAL_AUTH_SOCKET_DIR", tmp_path)

    manager = LocalAuthSessionManager()
    now = datetime.now(timezone.utc)
    port_file = tmp_path / "session.port"
    pid_file = tmp_path / "session.pid"
    log_file = tmp_path / "session.log"
    metadata_file = tmp_path / "agoutic-local-auth-session-u1-p1.json"
    port_file.write_text("12345")
    pid_file.write_text("999")
    log_file.write_text("log")
    metadata_file.write_text("{}")

    session = LocalAuthSession(
        session_id="sess-1",
        profile_id="p1",
        user_id="u1",
        local_username="alice",
        helper_host="127.0.0.1",
        helper_port=12345,
        port_file=str(port_file),
        pid_file=str(pid_file),
        log_file=str(log_file),
        auth_token="token-123",
        helper_pid=None,
        created_at=now,
        last_used_at=now,
    )
    manager._sessions[session.session_id] = session
    manager._by_profile[(session.user_id, session.profile_id)] = session.session_id

    async def fake_invoke(session_obj, payload):
        return {"ok": True}

    monkeypatch.setattr(manager, "invoke", fake_invoke)

    assert await manager.close_session("sess-1") is True
    assert not metadata_file.exists()