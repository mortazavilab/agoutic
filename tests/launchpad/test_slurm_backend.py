import pytest
from types import SimpleNamespace

from launchpad.backends.slurm_backend import SlurmBackend
from launchpad.backends.ssh_manager import SSHProfileData


class _FakeConn:
    def __init__(self):
        self.paths = []
        self.closed = False

    async def list_dir(self, path):
        self.paths.append(path)
        return [{"name": "data", "type": "dir", "size": 0}]

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_list_remote_files_resolves_relative_path_under_remote_base(monkeypatch):
    backend = SlurmBackend()
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    fake_conn = _FakeConn()

    async def fake_load_profile(profile_id, user_id):
        return profile

    async def fake_connect(profile_obj):
        assert profile_obj is profile
        return fake_conn

    monkeypatch.setattr(backend, "_load_profile", fake_load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", fake_connect)

    result = await backend.list_remote_files(
        ssh_profile_id="profile-1",
        user_id="user-1",
        path="data",
    )

    assert fake_conn.paths == ["/remote/agoutic/data"]
    assert result["path"] == "/remote/agoutic/data"
    assert fake_conn.closed is True


@pytest.mark.asyncio
async def test_list_remote_files_preserves_absolute_path(monkeypatch):
    backend = SlurmBackend()
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    fake_conn = _FakeConn()

    async def fake_load_profile(profile_id, user_id):
        return profile

    async def fake_connect(profile_obj):
        return fake_conn

    monkeypatch.setattr(backend, "_load_profile", fake_load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", fake_connect)

    result = await backend.list_remote_files(
        ssh_profile_id="profile-1",
        user_id="user-1",
        path="/scratch/shared",
    )

    assert fake_conn.paths == ["/scratch/shared"]
    assert result["path"] == "/scratch/shared"


def test_resolve_remote_browse_path_rejects_parent_traversal():
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )

    with pytest.raises(ValueError, match="cannot contain '..'"):
        SlurmBackend._resolve_remote_browse_path(profile, "../other")
