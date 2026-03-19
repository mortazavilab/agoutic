"""Tests for launchpad/backends/file_transfer.py."""

from types import SimpleNamespace

import pytest

from launchpad.backends.file_transfer import FileTransferManager
from launchpad.backends.ssh_manager import SSHProfileData
from launchpad.config import LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS


@pytest.fixture()
def key_file_profile(tmp_path) -> SSHProfileData:
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("PRIVATE KEY")
    return SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="hpc3.example.edu",
        ssh_port=22,
        ssh_username="seyedam",
        auth_method="key_file",
        key_file_path=str(key_path),
        local_username="seyedam",
        remote_base_path="/scratch/seyedam/agoutic",
        is_enabled=True,
    )


@pytest.mark.asyncio
async def test_upload_prefers_active_broker_session(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_dir = tmp_path / "inputs"
    local_dir.mkdir()
    (local_dir / "reads.fastq").write_text("ACGT")
    fake_session = SimpleNamespace(session_id="sess-1")
    invoke_calls = []

    class FakeSessionManager:
        async def get_active_session(self, profile):
            assert profile.id == "profile-1"
            return fake_session

        async def invoke(self, session, payload):
            invoke_calls.append((session, payload))
            return {"ok": True, "bytes_transferred": 1234}

    async def fail_subprocess(*args, **kwargs):
        raise AssertionError("direct rsync subprocess should not be used when broker session is active")

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.create_subprocess_exec", fail_subprocess)

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_dir),
        remote_path="/scratch/seyedam/agoutic/data/sample",
    )

    assert result == {"ok": True, "message": "Upload completed", "bytes_transferred": 1234}
    assert len(invoke_calls) == 1
    _, payload = invoke_calls[0]
    assert payload["op"] == "rsync_transfer"
    assert payload["source"] == f"{local_dir}/"
    assert payload["dest"] == "seyedam@hpc3.example.edu:/scratch/seyedam/agoutic/data/sample"
    assert payload["timeout_seconds"] == LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_upload_requires_unlock_when_key_unreadable_and_no_session(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_dir = tmp_path / "inputs"
    local_dir.mkdir()

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return None

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.os.access", lambda path, mode: False)

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_dir),
        remote_path="/scratch/seyedam/agoutic/data/sample",
    )

    assert result == {
        "ok": False,
        "message": "No active local auth session for this profile. Unlock the profile in Remote Profiles first.",
        "bytes_transferred": 0,
    }


def test_rsync_ssh_command_uses_fail_fast_publickey_transport(key_file_profile):
    manager = FileTransferManager()

    command = manager._build_ssh_command(key_file_profile)

    assert "BatchMode=yes" in command
    assert "PreferredAuthentications=publickey" in command
    assert "PasswordAuthentication=no" in command
    assert "KbdInteractiveAuthentication=no" in command
    assert "GSSAPIAuthentication=no" in command
    assert "IdentitiesOnly=yes" in command
    assert "ConnectTimeout=600" in command
    assert "ConnectionAttempts=1" in command