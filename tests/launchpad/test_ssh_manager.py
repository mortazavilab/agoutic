"""Tests for launchpad/backends/ssh_manager.py."""

from datetime import datetime, timezone
import json
import sys
from types import SimpleNamespace

import pytest

from launchpad.backends.ssh_manager import SSHConnectionManager, SSHProfileData, resolve_key_file_path


class TestResolveKeyFilePath:
    def test_expands_home_relative_to_local_user(self, monkeypatch):
        profile = SSHProfileData(
            id="p1",
            user_id="u1",
            nickname="test",
            ssh_host="example.org",
            ssh_port=22,
            ssh_username="alice",
            auth_method="key_file",
            key_file_path="~/.ssh/id_ed25519",
            local_username="alice",
            is_enabled=True,
        )

        monkeypatch.setattr("launchpad.backends.ssh_manager.pwd.getpwnam", lambda username: SimpleNamespace(pw_dir="/home/alice"))

        resolved = resolve_key_file_path(profile)

        assert resolved == "/home/alice/.ssh/id_ed25519"


class TestConnectKeyFile:
    @pytest.mark.asyncio
    async def test_local_username_path_still_uses_asyncssh_when_readable(self, monkeypatch, tmp_path):
        manager = SSHConnectionManager()
        key_path = tmp_path / "id_ed25519"
        key_path.write_text("PRIVATE KEY")
        captured_kwargs: dict = {}
        profile = SSHProfileData(
            id="p1",
            user_id="u1",
            nickname="test",
            ssh_host="example.org",
            ssh_port=22,
            ssh_username="alice",
            auth_method="key_file",
            key_file_path=str(key_path),
            local_username="alice",
            is_enabled=True,
        )

        async def fake_connect(**kwargs):
            captured_kwargs.update(kwargs)
            class _Conn:
                def close(self):
                    return None
                async def wait_closed(self):
                    return None
            return _Conn()

        monkeypatch.setitem(sys.modules, "asyncssh", SimpleNamespace(connect=fake_connect))
        monkeypatch.setattr("launchpad.backends.ssh_manager.pwd.getpwnam", lambda username: SimpleNamespace(pw_dir=str(tmp_path)))

        conn = await manager.connect(profile)

        assert captured_kwargs["client_keys"] == [str(key_path)]
        assert conn.profile.id == "p1"

    @pytest.mark.asyncio
    async def test_local_username_unreadable_key_raises_safe_error(self, monkeypatch, tmp_path):
        manager = SSHConnectionManager()
        key_dir = tmp_path / ".ssh"
        key_dir.mkdir()
        key_path = key_dir / "id_ed25519"
        key_path.write_text("PRIVATE KEY")
        profile = SSHProfileData(
            id="p1",
            user_id="u1",
            nickname="test",
            ssh_host="example.org",
            ssh_port=22,
            ssh_username="alice",
            auth_method="key_file",
            key_file_path="~/.ssh/id_ed25519",
            local_username="alice",
            is_enabled=True,
        )

        monkeypatch.setitem(sys.modules, "asyncssh", SimpleNamespace(connect=lambda **kwargs: None))
        monkeypatch.setattr("launchpad.backends.ssh_manager.pwd.getpwnam", lambda username: SimpleNamespace(pw_dir=str(tmp_path)))
        monkeypatch.setattr("launchpad.backends.ssh_manager.os.access", lambda path, mode: False)

        with pytest.raises(PermissionError, match="Start a local auth session"):
            await manager.connect(profile)

    @pytest.mark.asyncio
    async def test_local_password_starts_broker_session(self, monkeypatch, tmp_path):
        manager = SSHConnectionManager()
        key_dir = tmp_path / ".ssh"
        key_dir.mkdir()
        key_path = key_dir / "id_ed25519"
        key_path.write_text("PRIVATE KEY")
        profile = SSHProfileData(
            id="p1",
            user_id="u1",
            nickname="test",
            ssh_host="example.org",
            ssh_port=22,
            ssh_username="alice",
            auth_method="key_file",
            key_file_path="~/.ssh/id_ed25519",
            local_username="alice",
            is_enabled=True,
        )
        fake_session = SimpleNamespace(session_id="sess-1")

        class FakeSessionManager:
            async def create_or_replace_session(self, profile_obj, local_password):
                assert profile_obj.id == "p1"
                assert local_password == "secret"
                return fake_session

            async def get_active_session(self, profile_obj):
                return fake_session

        monkeypatch.setattr("launchpad.backends.ssh_manager.get_local_auth_session_manager", lambda: FakeSessionManager())
        monkeypatch.setattr("launchpad.backends.ssh_manager.pwd.getpwnam", lambda username: SimpleNamespace(pw_dir=str(tmp_path)))
        monkeypatch.setattr("launchpad.backends.ssh_manager.os.access", lambda path, mode: False)

        conn = await manager.connect(profile, local_password="secret")

        assert conn.profile.id == "p1"

    @pytest.mark.asyncio
    async def test_active_broker_session_is_preferred_over_direct_key_access(self, monkeypatch, tmp_path):
        manager = SSHConnectionManager()
        key_path = tmp_path / "id_ed25519"
        key_path.write_text("PRIVATE KEY")
        profile = SSHProfileData(
            id="p1",
            user_id="u1",
            nickname="test",
            ssh_host="example.org",
            ssh_port=22,
            ssh_username="alice",
            auth_method="key_file",
            key_file_path=str(key_path),
            local_username="alice",
            is_enabled=True,
        )
        fake_session = SimpleNamespace(session_id="sess-1")

        class FakeSessionManager:
            async def create_or_replace_session(self, profile_obj, local_password):
                raise AssertionError("existing session should be reused")

            async def get_active_session(self, profile_obj):
                return fake_session

        monkeypatch.setattr("launchpad.backends.ssh_manager.get_local_auth_session_manager", lambda: FakeSessionManager())

        connect_called = False

        async def fake_connect(**kwargs):
            nonlocal connect_called
            connect_called = True
            class _Conn:
                def close(self):
                    return None
                async def wait_closed(self):
                    return None
            return _Conn()

        monkeypatch.setitem(sys.modules, "asyncssh", SimpleNamespace(connect=fake_connect))

        conn = await manager.connect(profile)

        assert conn.profile.id == "p1"
        assert connect_called is False

    @pytest.mark.asyncio
    async def test_connect_uses_shared_broker_session_metadata(self, monkeypatch, tmp_path):
        manager = SSHConnectionManager()
        profile = SSHProfileData(
            id="p1",
            user_id="u1",
            nickname="test",
            ssh_host="example.org",
            ssh_port=22,
            ssh_username="alice",
            auth_method="key_file",
            key_file_path="~/.ssh/id_ed25519",
            local_username="alice",
            is_enabled=True,
        )
        now = datetime.now(timezone.utc)
        metadata_path = tmp_path / "agoutic-local-auth-session-u1-p1.json"
        metadata_path.write_text(json.dumps({
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
            "created_at": now.isoformat(),
            "last_used_at": now.isoformat(),
        }), encoding="utf-8")

        from launchpad.backends.local_auth_sessions import LocalAuthSessionManager

        session_manager = LocalAuthSessionManager()

        async def fake_invoke(session_obj, payload):
            return {"ok": True}

        monkeypatch.setattr(session_manager, "invoke", fake_invoke)
        monkeypatch.setattr("launchpad.backends.local_auth_sessions.LOCAL_AUTH_SOCKET_DIR", tmp_path)
        monkeypatch.setattr("launchpad.backends.ssh_manager.get_local_auth_session_manager", lambda: session_manager)

        connect_called = False

        async def fake_connect(**kwargs):
            nonlocal connect_called
            connect_called = True
            class _Conn:
                def close(self):
                    return None
                async def wait_closed(self):
                    return None
            return _Conn()

        monkeypatch.setitem(sys.modules, "asyncssh", SimpleNamespace(connect=fake_connect))

        conn = await manager.connect(profile)

        assert conn.profile.id == "p1"
        assert connect_called is False