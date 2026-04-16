"""Tests for launchpad/backends/local_auth_sessions.py."""

import asyncio
import errno
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import launchpad.backends.local_user_broker as local_user_broker_module
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

    assert "umask 022 && PYTHONPATH=" in captured["args"][3]
    assert " nohup " in captured["args"][3]


def test_launch_helper_applies_pythonpath_to_broker_process(monkeypatch, tmp_path):
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

    command = captured["args"][3]
    assert "umask 022 && PYTHONPATH=" in command
    assert "PYTHONPATH=" in command and " nohup " in command


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

    async def fake_run_subprocess(cmd, timeout_seconds=None, request_id=None, idle_timeout_seconds=None, stall_message=None, timeout_message=None):
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
    assert "--partial-dir=.rsync-partial" in captured["cmd"]
    assert "--partial" not in captured["cmd"]
    assert any(str(part).startswith("--skip-compress=") for part in captured["cmd"])
    assert "--copy-links" in captured["cmd"]


@pytest.mark.asyncio
async def test_local_broker_rsync_download_preserves_remote_trailing_slash(monkeypatch):
    """Remote rsync sources (user@host:path/) must keep their trailing slash."""
    captured = {}

    async def fake_run_subprocess(cmd, timeout_seconds=None, request_id=None, idle_timeout_seconds=None, stall_message=None, timeout_message=None):
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
    assert "--partial-dir=.rsync-partial" in captured["cmd"]
    assert "--partial" not in captured["cmd"]
    assert any(str(part).startswith("--skip-compress=") for part in captured["cmd"])


@pytest.mark.asyncio
async def test_local_broker_rsync_retries_without_skip_compress_on_incompatible_option(monkeypatch, tmp_path):
    local_file = tmp_path / "ENCFF921XAH.bam"
    local_file.write_text("BAM")
    commands = []

    async def fake_run_subprocess(cmd, timeout_seconds=None, request_id=None, idle_timeout_seconds=None, stall_message=None, timeout_message=None):
        commands.append(cmd)
        if len(commands) == 1:
            return {
                "ok": False,
                "stdout": "",
                "stderr": "rsync: --skip-compress: unknown option\nrsync error: requested action not supported (code 4)",
                "exit_status": 4,
            }
        return {"ok": True, "stdout": "total size is 84\n", "stderr": "", "exit_status": 0}

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
    assert result["bytes_transferred"] == 84
    assert len(commands) == 2
    assert "--partial-dir=.rsync-partial" in commands[0]
    assert "--partial" not in commands[0]
    assert "--partial-dir=.rsync-partial" in commands[1]
    assert "--partial" not in commands[1]
    assert any(str(part).startswith("--skip-compress=") for part in commands[0])
    assert not any(str(part).startswith("--skip-compress=") for part in commands[1])


@pytest.mark.asyncio
async def test_local_broker_rsync_transfer_forwards_idle_timeout(monkeypatch, tmp_path):
    local_file = tmp_path / "ENCFF921XAH.bam"
    local_file.write_text("BAM")
    captured = {}

    async def fake_run_subprocess(cmd, timeout_seconds=None, request_id=None, idle_timeout_seconds=None, stall_message=None, timeout_message=None):
        captured.update(
            {
                "cmd": cmd,
                "timeout_seconds": timeout_seconds,
                "request_id": request_id,
                "idle_timeout_seconds": idle_timeout_seconds,
                "stall_message": stall_message,
                "timeout_message": timeout_message,
            }
        )
        return {"ok": False, "stdout": "", "stderr": stall_message or "stalled", "exit_status": 124}

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
            "idle_timeout_seconds": 600,
            "request_id": "req-123",
        },
        shutdown_event=asyncio.Event(),
        auth_token="token-123",
    )

    assert result["ok"] is False
    assert captured["request_id"] == "req-123"
    assert captured["idle_timeout_seconds"] == 600
    assert ".rsync-partial" in captured["stall_message"]
    assert ".rsync-partial" in captured["timeout_message"]


@pytest.mark.asyncio
async def test_local_broker_run_subprocess_stalls_on_idle_timeout(monkeypatch):
    terminated = []

    class _FakeStdout:
        async def read(self, _size):
            await asyncio.Event().wait()

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        def __init__(self):
            self.returncode = None
            self.pid = 12345
            self.stdout = _FakeStdout()
            self.stderr = _FakeStderr()

        async def wait(self):
            self.returncode = -15 if self.returncode is None else self.returncode
            return self.returncode

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    async def fake_terminate(proc, *, grace_seconds=5.0):
        terminated.append((proc, grace_seconds))
        proc.returncode = -15

    monkeypatch.setattr(local_user_broker_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(local_user_broker_module, "_terminate_process_tree", fake_terminate)

    result = await local_user_broker_module._run_subprocess(
        ["rsync", "source", "dest"],
        timeout_seconds=30,
        request_id="req-stall",
        idle_timeout_seconds=0.01,
        stall_message="Rsync transfer stalled - no output for 0.01s.",
    )

    assert result["ok"] is False
    assert result["exit_status"] == 124
    assert "stalled" in result["stderr"]
    assert len(terminated) == 1
    assert "req-stall" not in local_user_broker_module._ACTIVE_PROCESSES


@pytest.mark.asyncio
async def test_local_broker_run_subprocess_treats_carriage_return_progress_as_activity(monkeypatch):
    class _FakeStdout:
        def __init__(self):
            self._chunks = [
                b"sample.pod5\r",
                b"      1048576   5%    8.00MB/s    0:00:10\r",
                b"total size is 20971520  speedup is 1.00\n",
                b"",
            ]

        async def read(self, _size):
            return self._chunks.pop(0)

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        def __init__(self):
            self.returncode = 0
            self.pid = 12345
            self.stdout = _FakeStdout()
            self.stderr = _FakeStderr()

        async def wait(self):
            return self.returncode

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(local_user_broker_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await local_user_broker_module._run_subprocess(
        ["rsync", "source", "dest"],
        timeout_seconds=30,
        request_id="req-progress",
        idle_timeout_seconds=0.01,
        stall_message="Rsync transfer stalled - no output for 0.01s.",
    )

    assert result["ok"] is True
    assert result["exit_status"] == 0
    assert "total size is 20971520" in result["stdout"]
    assert "req-progress" not in local_user_broker_module._ACTIVE_PROCESSES


@pytest.mark.asyncio
async def test_local_broker_cancel_request_terminates_active_process(monkeypatch):
    terminated = []

    class _FakeProc:
        returncode = None
        pid = 12345

    async def fake_terminate(proc, *, grace_seconds=5.0):
        terminated.append((proc, grace_seconds))

    local_user_broker_module._ACTIVE_PROCESSES["req-1"] = _FakeProc()
    monkeypatch.setattr(local_user_broker_module, "_terminate_process_tree", fake_terminate)

    result = await _handle_request(
        {
            "auth_token": "token-123",
            "op": "cancel_request",
            "request_id": "req-1",
        },
        shutdown_event=asyncio.Event(),
        auth_token="token-123",
    )

    assert result == {"ok": True, "request_id": "req-1", "cancelled": True}
    assert len(terminated) == 1
    assert "req-1" not in local_user_broker_module._ACTIVE_PROCESSES


@pytest.mark.asyncio
async def test_local_broker_get_request_status_returns_progress_snapshot():
    local_user_broker_module._ACTIVE_REQUEST_PROGRESS["req-2"] = {
        "current_file": "IGVFFI6571ANCX.bam",
        "file_percent": 67,
    }
    try:
        result = await _handle_request(
            {
                "auth_token": "token-123",
                "op": "get_request_status",
                "request_id": "req-2",
            },
            shutdown_event=asyncio.Event(),
            auth_token="token-123",
        )
    finally:
        local_user_broker_module._ACTIVE_REQUEST_PROGRESS.pop("req-2", None)

    assert result == {
        "ok": True,
        "request_id": "req-2",
        "active": False,
        "status": "finished",
        "progress": {
            "current_file": "IGVFFI6571ANCX.bam",
            "file_percent": 67,
        },
    }


@pytest.mark.asyncio
async def test_local_auth_invoke_reads_large_single_line_broker_response(monkeypatch, tmp_path):
    async def _handle_client(reader, writer):
        await reader.readline()
        payload = {"ok": True, "blob": "x" * 200000}
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(_handle_client, host="127.0.0.1", port=0)
    socket_info = server.sockets[0].getsockname()
    helper_port = int(socket_info[1])
    now = datetime.now(timezone.utc)
    session = LocalAuthSession(
        session_id="sess-large-response",
        profile_id="profile-1",
        user_id="user-1",
        local_username="alice",
        helper_host="127.0.0.1",
        helper_port=helper_port,
        port_file=str(tmp_path / "helper.port"),
        pid_file=str(tmp_path / "helper.pid"),
        log_file=str(tmp_path / "helper.log"),
        auth_token="token-123",
        helper_pid=None,
        created_at=now,
        last_used_at=now,
    )
    monkeypatch.setattr("launchpad.backends.local_auth_sessions._write_session_metadata", lambda _session: None)

    try:
        result = await LocalAuthSessionManager().invoke(session, {"op": "ping", "timeout_seconds": 5})
    finally:
        server.close()
        await server.wait_closed()

    assert result["ok"] is True
    assert len(result["blob"]) == 200000


@pytest.mark.asyncio
async def test_local_broker_rsync_transfer_trims_large_stdout_in_response(monkeypatch, tmp_path):
    local_file = tmp_path / "IGVFFI6571ANCX.bam"
    local_file.write_text("BAM")
    huge_stdout = ("progress\r" * 4000) + "total size is 20971520  speedup is 1.00\n"

    async def fake_run_subprocess(cmd, timeout_seconds=None, request_id=None, idle_timeout_seconds=None, stall_message=None, timeout_message=None):
        return {"ok": True, "stdout": huge_stdout, "stderr": "", "exit_status": 0}

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
    assert result["bytes_transferred"] == 20971520
    assert "total size is 20971520" in result["stdout"]
    assert "[truncated rsync stdout; omitted" in result["stdout"]
    assert len(result["stdout"]) < len(huge_stdout)


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