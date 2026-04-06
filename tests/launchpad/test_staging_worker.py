from __future__ import annotations

from types import SimpleNamespace

import pytest

from launchpad.backends.base import SubmitParams
from launchpad.backends.staging_worker import StagingTaskState, run_staging


class _FakeConn:
    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_staging_uses_current_backend_and_path_validator(monkeypatch):
    conn = _FakeConn()

    class _FakeSlurmBackend:
        def __init__(self):
            self._ssh_manager = SimpleNamespace(connect=self._connect)

        async def _connect(self, profile):
            return conn

        async def _load_profile(self, ssh_profile_id, user_id):
            return SimpleNamespace(id=ssh_profile_id, nickname="hpc3")

        def _derive_remote_roots(self, params, profile):
            return {
                "remote_base_path": "/remote/base",
                "ref_root": "/remote/base/ref",
                "data_root": "/remote/base/data",
            }

        async def _stage_sample_inputs(self, params, profile, conn, run_uuid=None, on_progress=None):
            if on_progress is not None:
                on_progress({"file_percent": 25, "files_transferred": 1, "files_total": 4})
            return {
                "remote_input": "/remote/base/data/fingerprint",
                "remote_reference_paths": {"GRCh38": "/remote/base/ref/GRCh38"},
                "data_cache_status": "staged",
                "reference_cache_statuses": {"GRCh38": "staged"},
            }

        async def _ensure_reference_assets_present(
            self,
            *,
            params,
            profile,
            conn,
            remote_reference_paths,
            reference_statuses,
        ):
            return ({"GRCh38": {"all_required_present": True}}, reference_statuses)

    async def _validate_remote_paths(conn, paths):
        return {name: SimpleNamespace(error=None, exists=True, writable=True) for name in paths}

    from launchpad.backends import path_validator as path_validator_module
    from launchpad.backends import slurm_backend as slurm_backend_module

    monkeypatch.setattr(slurm_backend_module, "SlurmBackend", _FakeSlurmBackend)
    monkeypatch.setattr(slurm_backend_module, "SubmitParams", SubmitParams)
    monkeypatch.setattr(path_validator_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(path_validator_module, "check_all_paths_ok", lambda results: (True, []))

    task = StagingTaskState(
        task_id="task-1",
        params={
            "project_id": "proj-1",
            "user_id": "user-1",
            "username": "ali",
            "project_slug": "proj",
            "sample_name": "ENCFF433WOA",
            "mode": "RNA",
            "input_directory": "/tmp/ENCFF433WOA.bam",
            "reference_genome": ["GRCh38"],
            "ssh_profile_id": "profile-1",
            "remote_base_path": "/remote/base",
        },
    )

    await run_staging(task)

    assert task.status == "completed"
    assert task.progress["file_percent"] == 25
    assert task.result == {
        "sample_name": "ENCFF433WOA",
        "ssh_profile_id": "profile-1",
        "ssh_profile_nickname": "hpc3",
        "remote_base_path": "/remote/base",
        "remote_data_path": "/remote/base/data/fingerprint",
        "remote_reference_paths": {"GRCh38": "/remote/base/ref/GRCh38"},
        "data_cache_status": "staged",
        "reference_cache_statuses": {"GRCh38": "staged"},
        "reference_asset_evidence": {"GRCh38": {"all_required_present": True}},
        "detected_input_type": "pod5",
    }


@pytest.mark.asyncio
async def test_run_staging_routes_remote_input_path_to_reuse(monkeypatch):
    """When remote_input_path is set, the worker must call _reuse_pre_staged_input
    instead of _stage_sample_inputs."""
    conn = _FakeConn()
    reuse_called = False
    stage_called = False

    class _FakeSlurmBackend:
        def __init__(self):
            self._ssh_manager = SimpleNamespace(connect=self._connect)

        async def _connect(self, profile):
            return conn

        async def _load_profile(self, ssh_profile_id, user_id):
            return SimpleNamespace(id=ssh_profile_id, nickname="hpc3")

        def _derive_remote_roots(self, params, profile):
            return {
                "remote_base_path": "/remote/base",
                "ref_root": "/remote/base/ref",
                "data_root": "/remote/base/data",
            }

        async def _reuse_pre_staged_input(self, run_uuid, params, *, profile, conn):
            nonlocal reuse_called
            reuse_called = True
            return {
                "remote_input": "/dfs9/seyedam-lab/share/pod5/Jamshid",
                "remote_reference_paths": {"mm39": "/remote/base/ref/mm39"},
                "data_cache_status": "reused",
                "reference_cache_statuses": {"mm39": "reused"},
                "detected_input_type": "pod5",
            }

        async def _stage_sample_inputs(self, params, profile, conn, run_uuid=None, on_progress=None):
            nonlocal stage_called
            stage_called = True
            raise AssertionError("_stage_sample_inputs should not be called for remote_input_path")

        async def _ensure_reference_assets_present(
            self,
            *,
            params,
            profile,
            conn,
            remote_reference_paths,
            reference_statuses,
        ):
            return ({"mm39": {"all_required_present": True}}, reference_statuses)

    async def _validate_remote_paths(conn, paths):
        return {name: SimpleNamespace(error=None, exists=True, writable=True) for name in paths}

    from launchpad.backends import path_validator as path_validator_module
    from launchpad.backends import slurm_backend as slurm_backend_module

    monkeypatch.setattr(slurm_backend_module, "SlurmBackend", _FakeSlurmBackend)
    monkeypatch.setattr(slurm_backend_module, "SubmitParams", SubmitParams)
    monkeypatch.setattr(path_validator_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(path_validator_module, "check_all_paths_ok", lambda results: (True, []))

    task = StagingTaskState(
        task_id="task-remote-1",
        params={
            "project_id": "proj-1",
            "user_id": "user-1",
            "username": "eli",
            "project_slug": "proj",
            "sample_name": "Jamshid",
            "mode": "DNA",
            "input_directory": "remote:/dfs9/seyedam-lab/share/pod5/Jamshid",
            "reference_genome": ["mm39"],
            "ssh_profile_id": "profile-1",
            "remote_base_path": "/remote/base",
            "remote_input_path": "/dfs9/seyedam-lab/share/pod5/Jamshid",
            "staged_remote_input_path": "/dfs9/seyedam-lab/share/pod5/Jamshid",
        },
    )

    await run_staging(task)

    assert task.status == "completed"
    assert reuse_called is True
    assert stage_called is False
    assert task.result["remote_data_path"] == "/dfs9/seyedam-lab/share/pod5/Jamshid"
    assert task.result["data_cache_status"] == "reused"
    assert task.result["detected_input_type"] == "pod5"