"""Tests for launchpad/backends/local_auth_sessions.py."""

import asyncio
import errno
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from launchpad.backends.local_auth_sessions import LocalAuthSession, LocalAuthSessionManager, _launch_helper_via_su
from launchpad.backends.local_user_broker import _build_ssh_transport, _handle_request


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


def test_launch_helper_sets_readable_umask(monkeypatch, tmp_path):
    log_file = tmp_path / "helper.log"
    captured = {}

    monkeypatch.setattr("launchpad.backends.local_auth_sessions._ensure_socket_dir", lambda: None)
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.pty.openpty", lambda: (10, 11))

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return DummyProcess(0)

    monkeypatch.setattr("launchpad.backends.local_auth_sessions.subprocess.Popen", fake_popen)
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.os.close", lambda fd: None)
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.select.select", lambda *args, **kwargs: ([], [], []))
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.time.time", MagicMock(side_effect=[0, 0, 999]))

    _launch_helper_via_su("alice", "secret", "127.0.0.1", "/tmp/session.port", "/tmp/session.pid", str(log_file), "token-123")

    assert "umask 022 && nohup" in captured["args"][3]


def test_local_broker_transport_uses_fail_fast_publickey_options():
    transport = _build_ssh_transport(
        {
            "ssh_host": "hpc3.example.edu",
            "ssh_port": 22,
            "ssh_username": "seyedam",
            "auth_method": "key_file",
            "key_file_path": "~/.ssh/id_ed25519",
        }
    )

    assert "-o" in transport
    assert "BatchMode=yes" in transport
    assert "PreferredAuthentications=publickey" in transport
    assert "PasswordAuthentication=no" in transport
    assert "KbdInteractiveAuthentication=no" in transport
    assert "GSSAPIAuthentication=no" in transport
    assert "IdentitiesOnly=yes" in transport
    assert "ConnectTimeout=600" in transport
    assert "ConnectionAttempts=1" in transport


@pytest.mark.asyncio
async def test_local_broker_rsync_transfer_keeps_single_file_source(monkeypatch, tmp_path):
    local_file = tmp_path / "ENCFF921XAH.bam"
    local_file.write_text("BAM")
    captured = {}

    async def fake_run_subprocess(cmd, timeout_seconds=None):
        captured["cmd"] = cmd
        return {"ok": True, "stdout": "total size is 42\n", "stderr": "", "exit_status": 0}

    monkeypatch.setattr("launchpad.backends.local_user_broker._run_subprocess", fake_run_subprocess)

    result = await _handle_request(
        {
            "auth_token": "token-123",
            "op": "rsync_transfer",
            "profile": {
                "ssh_host": "hpc3.example.edu",
                "ssh_port": 22,
                "ssh_username": "seyedam",
                "auth_method": "key_file",
                "key_file_path": "~/.ssh/id_ed25519",
            },
            "source": str(local_file),
            "dest": "seyedam@hpc3.example.edu:/scratch/seyedam/agoutic/data/sample",
            "copy_links": True,
            "timeout_seconds": 30,
        },
        shutdown_event=asyncio.Event(),
        auth_token="token-123",
    )

    assert result["ok"] is True
    assert str(local_file) in captured["cmd"]
    assert f"{local_file}/" not in captured["cmd"]
    assert "--copy-links" in captured["cmd"]


@pytest.mark.asyncio
async def test_local_broker_rsync_download_preserves_remote_trailing_slash(monkeypatch):
    """Remote rsync sources (user@host:path/) must keep their trailing slash."""
    captured = {}

    async def fake_run_subprocess(cmd, timeout_seconds=None):
        captured["cmd"] = cmd
        return {"ok": True, "stdout": "total size is 42\n", "stderr": "", "exit_status": 0}

    monkeypatch.setattr("launchpad.backends.local_user_broker._run_subprocess", fake_run_subprocess)

    result = await _handle_request(
        {
            "auth_token": "token-123",
            "op": "rsync_transfer",
            "profile": {
                "ssh_host": "hpc3.example.edu",
                "ssh_port": 22,
                "ssh_username": "seyedam",
                "auth_method": "key_file",
                "key_file_path": "~/.ssh/id_ed25519",
            },
            "source": "seyedam@hpc3.example.edu:/share/crsp/workflow1/",
            "dest": "/local/path/workflow1",
            "copy_links": False,
            "timeout_seconds": 30,
        },
        shutdown_event=asyncio.Event(),
        auth_token="token-123",
    )

    assert result["ok"] is True
    # The trailing slash on the remote source must survive — without it rsync
    # creates a nested directory instead of copying contents into dest.
    assert "seyedam@hpc3.example.edu:/share/crsp/workflow1/" in captured["cmd"]


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