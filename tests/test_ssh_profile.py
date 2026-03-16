"""Tests for SSH profile schema validation and model."""
import pytest
from pydantic import ValidationError

from launchpad.schemas import SSHProfileCreate, SSHProfileUpdate, SSHProfileOut


# ── SSHProfileCreate ─────────────────────────────────────────────


class TestSSHProfileCreate:
    def test_valid_key_file_profile(self):
        profile = SSHProfileCreate(
            user_id="user1",
            ssh_host="hpc3.example.edu",
            ssh_username="jdoe",
            auth_method="key_file",
            key_file_path="/home/jdoe/.ssh/id_rsa",
        )
        assert profile.auth_method == "key_file"
        assert profile.ssh_port == 22

    def test_valid_ssh_agent_profile(self):
        profile = SSHProfileCreate(
            user_id="user1",
            ssh_host="hpc3.example.edu",
            ssh_username="jdoe",
            auth_method="ssh_agent",
        )
        assert profile.auth_method == "ssh_agent"
        assert profile.key_file_path is None

    def test_key_file_without_path_raises(self):
        with pytest.raises(ValidationError):
            SSHProfileCreate(
                user_id="user1",
                ssh_host="hpc3.example.edu",
                ssh_username="jdoe",
                auth_method="key_file",
                # key_file_path intentionally omitted
            )

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            SSHProfileCreate()  # type: ignore[call-arg]


# ── SSHProfileUpdate ─────────────────────────────────────────────


class TestSSHProfileUpdate:
    def test_partial_update_only_nickname(self):
        update = SSHProfileUpdate(nickname="my-cluster")
        assert update.nickname == "my-cluster"
        assert update.ssh_host is None

    def test_full_update(self):
        update = SSHProfileUpdate(
            nickname="new-name",
            ssh_host="new-host.edu",
            ssh_port=2222,
            ssh_username="newuser",
            auth_method="ssh_agent",
            key_file_path=None,
            is_enabled=False,
        )
        assert update.ssh_port == 2222
        assert update.is_enabled is False

    def test_empty_update_is_valid(self):
        update = SSHProfileUpdate()
        assert update.nickname is None
        assert update.ssh_host is None


# ── SSHProfileOut ────────────────────────────────────────────────


class TestSSHProfileOut:
    def test_has_key_file_true(self):
        profile = SSHProfileOut(
            id="prof_1",
            user_id="user1",
            ssh_host="hpc3.example.edu",
            ssh_port=22,
            ssh_username="jdoe",
            auth_method="key_file",
            has_key_file=True,
            is_enabled=True,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        assert profile.has_key_file is True

    def test_has_key_file_false(self):
        profile = SSHProfileOut(
            id="prof_2",
            user_id="user1",
            ssh_host="hpc3.example.edu",
            ssh_port=22,
            ssh_username="jdoe",
            auth_method="ssh_agent",
            has_key_file=False,
            is_enabled=True,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        assert profile.has_key_file is False
