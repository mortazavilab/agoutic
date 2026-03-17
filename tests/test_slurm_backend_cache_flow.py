"""Behavioral tests for SLURM cache flow (hit/miss/refresh/fallback)."""

from types import SimpleNamespace

import pytest

from launchpad.backends.base import SubmitParams
from launchpad.backends.slurm_backend import SlurmBackend
from launchpad.backends.ssh_manager import SSHProfileData


class _FakeConn:
    def __init__(self, existing_paths=None):
        self.existing_paths = set(existing_paths or [])

    async def path_exists(self, path: str) -> bool:
        return path in self.existing_paths

    async def mkdir_p(self, path: str) -> None:
        self.existing_paths.add(path)

    async def run(self, command: str, check: bool = False):
        return SimpleNamespace(stdout="", stderr="", exit_status=0)

    async def run_checked(self, command: str) -> str:
        if "sbatch --parsable" in command:
            return "12345\n"
        return ""

    async def close(self) -> None:
        return None


@pytest.fixture()
def profile() -> SSHProfileData:
    return SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc",
        ssh_host="cluster.example.edu",
        ssh_port=22,
        ssh_username="eli",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
    )


@pytest.mark.asyncio
async def test_resolve_staging_cache_reuses_reference_and_data(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(
        existing_paths={
            "/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/references/mm39",
            "/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/data/mm39/fp1234567890abcd",
        }
    )

    params = SubmitParams(
        user_id="user-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
    )

    monkeypatch.setattr(backend, "_resolve_reference_source_dir", lambda _: SimpleNamespace())
    monkeypatch.setattr(backend, "_compute_directory_signature", lambda _: "sig-1")
    monkeypatch.setattr(backend, "_compute_input_fingerprint", lambda _: "fp1234567890abcd")

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_transfer_state", _noop)

    upload_calls = []

    async def _upload_inputs(**kwargs):
        upload_calls.append(kwargs)
        return {"ok": True, "message": "ok", "bytes_transferred": 0}

    monkeypatch.setattr(backend._transfer_manager, "upload_inputs", _upload_inputs)

    from launchpad import db as launchpad_db

    async def _get_ref(*args, **kwargs):
        return SimpleNamespace(
            remote_path="/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/references/mm39",
            source_signature="sig-1",
            last_validated_at=None,
        )

    async def _get_data(*args, **kwargs):
        return SimpleNamespace(
            remote_path="/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/data/mm39/fp1234567890abcd",
            last_used_at=None,
        )

    monkeypatch.setattr(launchpad_db, "get_remote_reference_cache_entry", _get_ref)
    monkeypatch.setattr(launchpad_db, "get_remote_input_cache_entry", _get_data)
    monkeypatch.setattr(launchpad_db, "upsert_remote_reference_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "upsert_remote_input_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "update_job_fields", _noop)

    result = await backend._resolve_staging_cache("run-1", params, profile, conn)

    assert result["reference_cache_status"] == "reused"
    assert result["data_cache_status"] == "reused"
    assert upload_calls == []


@pytest.mark.asyncio
async def test_resolve_staging_cache_refreshes_stale_reference(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(existing_paths={"/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/data/mm39/fpdeadbeefcafebabe"})

    params = SubmitParams(
        user_id="user-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
    )

    monkeypatch.setattr(backend, "_resolve_reference_source_dir", lambda _: SimpleNamespace())
    monkeypatch.setattr(backend, "_compute_directory_signature", lambda _: "new-sig")
    monkeypatch.setattr(backend, "_compute_input_fingerprint", lambda _: "fpdeadbeefcafebabe")

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_transfer_state", _noop)

    upload_calls = []

    async def _upload_inputs(**kwargs):
        upload_calls.append(kwargs)
        return {"ok": True, "message": "ok", "bytes_transferred": 0}

    monkeypatch.setattr(backend._transfer_manager, "upload_inputs", _upload_inputs)

    from launchpad import db as launchpad_db

    async def _get_ref(*args, **kwargs):
        return SimpleNamespace(
            remote_path="/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/references/mm39",
            source_signature="old-sig",
            last_validated_at=None,
        )

    async def _get_data(*args, **kwargs):
        return None

    monkeypatch.setattr(launchpad_db, "get_remote_reference_cache_entry", _get_ref)
    monkeypatch.setattr(launchpad_db, "get_remote_input_cache_entry", _get_data)
    monkeypatch.setattr(launchpad_db, "upsert_remote_reference_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "upsert_remote_input_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "update_job_fields", _noop)

    result = await backend._resolve_staging_cache("run-2", params, profile, conn)

    assert result["reference_cache_status"] == "refreshed"
    assert result["data_cache_status"] == "staged"
    assert len(upload_calls) >= 2


@pytest.mark.asyncio
async def test_submit_uses_fallback_when_cache_resolution_fails(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn()

    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        slurm_account="acc",
        slurm_partition="standard",
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _raise_cache(*args, **kwargs):
        raise RuntimeError("metadata unavailable")

    async def _fallback(*args, **kwargs):
        return {
            "remote_input": "/scratch/eli/agoutic/fallback/input",
            "reference_cache_status": "fallback",
            "data_cache_status": "fallback",
        }

    async def _noop(*args, **kwargs):
        return None

    async def _connect(*args, **kwargs):
        return conn

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_resolve_staging_cache", _raise_cache)
    monkeypatch.setattr(backend, "_fallback_stage_inputs", _fallback)
    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_slurm_info", _noop)
    monkeypatch.setattr(backend, "_build_nextflow_command", lambda *args, **kwargs: "echo ok")

    from launchpad.backends import slurm_backend as slurm_module

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(slurm_module, "generate_sbatch_script", lambda **kwargs: "#!/bin/bash\necho hi\n")

    run_uuid = await backend.submit("run-3", params)

    assert run_uuid == "run-3"
