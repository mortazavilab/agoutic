"""Tests for launchpad/backends/file_transfer.py."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    assert payload["include_patterns"] == []
    assert payload["copy_links"] is True
    assert payload["use_skip_compress"] is True
    assert payload["timeout_seconds"] == pytest.approx(28800.0, rel=0.01)
    assert payload["timeout_seconds"] > LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_upload_single_file_prefers_active_broker_session_without_trailing_slash(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_file = tmp_path / "ENCFF921XAH.bam"
    local_file.write_text("BAM")
    fake_session = SimpleNamespace(session_id="sess-1")
    invoke_calls = []

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return fake_session

        async def invoke(self, session, payload):
            invoke_calls.append((session, payload))
            return {"ok": True, "bytes_transferred": 321}

    async def fail_subprocess(*args, **kwargs):
        raise AssertionError("direct rsync subprocess should not be used when broker session is active")

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.create_subprocess_exec", fail_subprocess)

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_file),
        remote_path="/scratch/seyedam/agoutic/data/sample",
    )

    assert result == {"ok": True, "message": "Upload completed", "bytes_transferred": 321}
    _, payload = invoke_calls[0]
    assert payload["source"] == str(local_file)
    assert payload["copy_links"] is True


@pytest.mark.asyncio
async def test_upload_broker_polls_live_progress(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_file = tmp_path / "IGVFFI6571ANCX.bam"
    local_file.write_text("BAM")
    fake_session = SimpleNamespace(session_id="sess-1")
    progress_updates = []
    transfer_released = False

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return fake_session

        async def invoke(self, session, payload):
            nonlocal transfer_released
            if payload["op"] == "get_request_status":
                transfer_released = True
                return {
                    "ok": True,
                    "active": True,
                    "status": "running",
                    "progress": {
                        "current_file": "IGVFFI6571ANCX.bam",
                        "current_file_bytes_transferred": 670,
                        "current_file_total_bytes_estimate": 1000,
                        "file_percent": 67,
                        "files_transferred": 0,
                        "files_total": 1,
                        "speed": "120MB/s",
                    },
                }
            if payload["op"] == "rsync_transfer":
                while not transfer_released:
                    await asyncio.sleep(0)
                return {
                    "ok": True,
                    "bytes_transferred": 1000,
                    "progress": {
                        "current_file": "IGVFFI6571ANCX.bam",
                        "current_file_bytes_transferred": 1000,
                        "current_file_total_bytes_estimate": 1000,
                        "file_percent": 100,
                        "files_transferred": 1,
                        "files_total": 1,
                        "speed": "120MB/s",
                    },
                    "stdout": "",
                }
            raise AssertionError(f"Unexpected broker op: {payload['op']}")

    real_sleep = asyncio.sleep

    async def fake_sleep(_seconds):
        await real_sleep(0)

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.sleep", fake_sleep)

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_file),
        remote_path="/scratch/seyedam/agoutic/data/sample",
        on_progress=progress_updates.append,
        transfer_id="req-live-progress",
    )

    assert result == {"ok": True, "message": "Upload completed", "bytes_transferred": 1000}
    assert any(update.get("file_percent") == 67 for update in progress_updates)
    assert any(update.get("file_percent") == 100 for update in progress_updates)


@pytest.mark.asyncio
async def test_download_passes_include_patterns_to_broker_session(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_dir = tmp_path / "workflow2"
    fake_session = SimpleNamespace(session_id="sess-1")
    invoke_calls = []

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return fake_session

        async def invoke(self, session, payload):
            invoke_calls.append((session, payload))
            return {"ok": True, "bytes_transferred": 2048}

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())

    result = await manager.download_outputs(
        profile=key_file_profile,
        remote_path="/scratch/seyedam/agoutic/project/workflow2",
        local_path=str(local_dir),
        include_patterns=["annot/***", "*.html"],
        exclude_patterns=["*"],
    )

    assert result == {"ok": True, "message": "Download completed", "bytes_transferred": 2048}
    _, payload = invoke_calls[0]
    assert payload["include_patterns"] == ["annot/***", "*.html"]
    assert payload["exclude_patterns"] == ["*"]
    assert payload["copy_links"] is False
    assert payload["copy_dirlinks"] is True
    assert payload["use_skip_compress"] is True
    assert payload["timeout_seconds"] == pytest.approx(28800.0, rel=0.01)
    assert payload["timeout_seconds"] > LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS


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


@pytest.mark.asyncio
async def test_upload_single_file_direct_rsync_omits_trailing_slash(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_file = tmp_path / "ENCFF921XAH.bam"
    local_file.write_text("BAM")
    captured = {}

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return None

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return (b"total size is 123\n", b"")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProcess()

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.os.access", lambda path, mode: True)
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_file),
        remote_path="/scratch/seyedam/agoutic/data/sample",
    )

    assert result == {"ok": True, "message": "Upload completed", "bytes_transferred": 123}
    assert str(local_file) in captured["cmd"]
    assert f"{local_file}/" not in captured["cmd"]
    assert "--partial-dir=.rsync-partial" in captured["cmd"]
    assert "--partial" not in captured["cmd"]
    assert any(str(part).startswith("--skip-compress=") for part in captured["cmd"])
    assert "--copy-links" in captured["cmd"]


@pytest.mark.asyncio
async def test_download_direct_rsync_uses_copy_dirlinks_and_skip_compress(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    captured = {}

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return None

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return (b"total size is 456\n", b"")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProcess()

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.os.access", lambda path, mode: True)
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    result = await manager.download_outputs(
        profile=key_file_profile,
        remote_path="/scratch/seyedam/agoutic/project/workflow2",
        local_path=str(tmp_path / "workflow2"),
    )

    assert result == {"ok": True, "message": "Download completed", "bytes_transferred": 456}
    assert "--partial-dir=.rsync-partial" in captured["cmd"]
    assert "--partial" not in captured["cmd"]
    assert "--copy-dirlinks" in captured["cmd"]
    assert "--copy-links" not in captured["cmd"]
    assert any(str(part).startswith("--skip-compress=") for part in captured["cmd"])


@pytest.mark.asyncio
async def test_direct_rsync_retries_without_skip_compress_on_incompatible_option(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_file = tmp_path / "ENCFF921XAH.bam"
    local_file.write_text("BAM")
    commands = []

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return None

    class FakeProcess:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self):
            return (self._stdout, self._stderr)

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        commands.append(cmd)
        if len(commands) == 1:
            return FakeProcess(4, b"", b"rsync: --skip-compress: unknown option\nrsync error: requested action not supported (code 4)\n")
        return FakeProcess(0, b"total size is 789\n", b"")

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.os.access", lambda path, mode: True)
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_file),
        remote_path="/scratch/seyedam/agoutic/data/sample",
    )

    assert result == {"ok": True, "message": "Upload completed", "bytes_transferred": 789}
    assert len(commands) == 2
    assert "--partial-dir=.rsync-partial" in commands[0]
    assert "--partial" not in commands[0]
    assert "--partial-dir=.rsync-partial" in commands[1]
    assert "--partial" not in commands[1]
    assert any(str(part).startswith("--skip-compress=") for part in commands[0])
    assert not any(str(part).startswith("--skip-compress=") for part in commands[1])


@pytest.mark.asyncio
async def test_upload_direct_rsync_times_out_with_actionable_message(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_dir = tmp_path / "inputs"
    local_dir.mkdir()
    (local_dir / "reads.fastq").write_text("ACGT")

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return None

    class FakeProcess:
        returncode = None
        pid = 12345

        async def communicate(self):
            raise AssertionError("communicate should not be called directly during timeout path")

        def kill(self):
            self.returncode = -9

        def terminate(self):
            self.returncode = -15

        async def wait(self):
            self.returncode = self.returncode if self.returncode is not None else -15
            return self.returncode

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return FakeProcess()

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.file_transfer.os.access", lambda path, mode: True)
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.wait_for", fake_wait_for)

    async def fake_communicate(self):
        return (b"", b"")

    monkeypatch.setattr(FakeProcess, "communicate", fake_communicate, raising=False)

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_dir),
        remote_path="/scratch/seyedam/agoutic/data/sample",
        timeout_seconds=5,
    )

    assert result["ok"] is False
    assert "Upload failed" in result["message"]
    assert "timeout budget" in result["message"]


@pytest.mark.asyncio
async def test_run_rsync_subprocess_treats_carriage_return_progress_as_activity(monkeypatch):
    manager = FileTransferManager()
    progress_updates = []

    class FakeStdout:
        def __init__(self):
            self._chunks = [
                b"sample.pod5\r",
                b"      1048576   5%    8.00MB/s    0:00:10\r",
                b"     20971520 100%   12.00MB/s    0:00:10 (xfr#1, to-chk=0/1)\n",
                b"total size is 20971520  speedup is 1.00\n",
                b"",
            ]

        async def read(self, _size):
            return self._chunks.pop(0)

    class FakeStderr:
        async def read(self):
            return b""

    class FakeProcess:
        def __init__(self):
            self.returncode = 0
            self.pid = 12345
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr("launchpad.backends.file_transfer.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    returncode, stdout_text, stderr_text = await manager._run_rsync_subprocess(
        cmd=["rsync", "source", "dest"],
        direction="upload",
        timeout_seconds=30,
        on_progress=progress_updates.append,
    )

    assert returncode == 0
    assert stderr_text == ""
    assert "total size is 20971520" in stdout_text
    assert any(update.get("current_file") == "sample.pod5" for update in progress_updates)
    assert any(update.get("current_file_bytes_transferred") == 1048576 for update in progress_updates)
    assert any(update.get("current_file_total_bytes_estimate") == 20971520 for update in progress_updates)
    assert any(update.get("file_percent") == 100 for update in progress_updates)


@pytest.mark.asyncio
async def test_upload_broker_uses_shared_timeout_budget_for_retry(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_dir = tmp_path / "inputs"
    local_dir.mkdir()
    (local_dir / "reads.fastq").write_text("ACGT")
    fake_session = SimpleNamespace(session_id="sess-1")
    invoke_calls = []

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return fake_session

        async def invoke(self, session, payload):
            invoke_calls.append(payload)
            return {"ok": False, "stderr": "Operation timed out after 42s", "exit_status": 124}

    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_dir),
        remote_path="/scratch/seyedam/agoutic/data/sample",
        timeout_seconds=42,
    )

    assert result == {
        "ok": False,
        "message": "Upload failed: Operation timed out after 42s",
        "bytes_transferred": 0,
    }
    assert invoke_calls[0]["timeout_seconds"] == pytest.approx(42.0, rel=0.01)


@pytest.mark.asyncio
async def test_upload_prefers_explicit_remote_stage_transfer_timeout(monkeypatch, tmp_path, key_file_profile):
    manager = FileTransferManager()
    local_dir = tmp_path / "inputs"
    local_dir.mkdir()
    (local_dir / "reads.fastq").write_text("ACGT")
    fake_session = SimpleNamespace(session_id="sess-1")
    invoke_calls = []

    class FakeSessionManager:
        async def get_active_session(self, profile):
            return fake_session

        async def invoke(self, session, payload):
            invoke_calls.append(payload)
            return {"ok": True, "bytes_transferred": 1234}

    monkeypatch.setenv("REMOTE_STAGE_TRANSFER_TIMEOUT_SECONDS", "7200")
    monkeypatch.setattr("launchpad.backends.file_transfer.get_local_auth_session_manager", lambda: FakeSessionManager())

    result = await manager.upload_inputs(
        profile=key_file_profile,
        local_path=str(local_dir),
        remote_path="/scratch/seyedam/agoutic/data/sample",
    )

    assert result == {"ok": True, "message": "Upload completed", "bytes_transferred": 1234}
    assert invoke_calls[0]["timeout_seconds"] == pytest.approx(7200.0, rel=0.01)


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