from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import launchpad.backends.staging_worker as staging_worker_module
from launchpad.backends.base import SubmitParams
from launchpad.backends.staging_worker import StagingTaskState, active_task_count, cleanup_old_tasks, recover_staging_tasks, run_staging


class _FakeConn:
    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def clear_staging_tasks():
    staging_worker_module.get_staging_tasks().clear()
    yield
    staging_worker_module.get_staging_tasks().clear()


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


@pytest.mark.asyncio
async def test_run_staging_persists_state_transitions(monkeypatch):
    conn = _FakeConn()
    persisted_snapshots: list[dict] = []

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

    async def _fake_upsert(task, session=None):
        persisted_snapshots.append(
            {
                "task_id": task.task_id,
                "status": task.status,
                "progress": dict(task.progress),
                "result": task.result,
                "error": task.error,
            }
        )
        return None

    from launchpad.backends import path_validator as path_validator_module
    from launchpad.backends import slurm_backend as slurm_backend_module
    import launchpad.db as launchpad_db_module

    monkeypatch.setattr(slurm_backend_module, "SlurmBackend", _FakeSlurmBackend)
    monkeypatch.setattr(slurm_backend_module, "SubmitParams", SubmitParams)
    monkeypatch.setattr(path_validator_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(path_validator_module, "check_all_paths_ok", lambda results: (True, []))
    monkeypatch.setattr(launchpad_db_module, "upsert_staging_task", _fake_upsert)
    monkeypatch.setattr(staging_worker_module, "_PROGRESS_PERSIST_INTERVAL_SECONDS", 0.0)

    task = StagingTaskState(
        task_id="task-persist-1",
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
    await asyncio.sleep(0)

    assert persisted_snapshots[0]["status"] == "running"
    assert any(snapshot["progress"].get("file_percent") == 25 for snapshot in persisted_snapshots)
    assert persisted_snapshots[-1]["status"] == "completed"
    assert persisted_snapshots[-1]["result"]["remote_data_path"] == "/remote/base/data/fingerprint"


@pytest.mark.asyncio
async def test_recover_staging_tasks_requeues_running_rows_and_respects_capacity(monkeypatch):
    started: list[str] = []
    persisted_statuses: list[tuple[str, str]] = []
    release = asyncio.Event()

    records = [
        SimpleNamespace(
            task_id="stg-1",
            status="running",
            progress_json={"file_percent": 55},
            result_json=None,
            error=None,
            created_at=1.0,
            updated_at=2.0,
            params_json={"sample_name": "sample-1"},
        ),
        SimpleNamespace(
            task_id="stg-2",
            status="queued",
            progress_json={},
            result_json=None,
            error=None,
            created_at=3.0,
            updated_at=4.0,
            params_json={"sample_name": "sample-2"},
        ),
    ]

    async def _fake_get_incomplete(session=None):
        return records

    async def _fake_upsert(task, session=None):
        persisted_statuses.append((task.task_id, task.status))
        return None

    async def _fake_run(task):
        started.append(task.task_id)
        task.status = "running"
        await release.wait()
        task.status = "completed"

    import launchpad.db as launchpad_db_module

    monkeypatch.setattr(launchpad_db_module, "get_incomplete_staging_tasks", _fake_get_incomplete)
    monkeypatch.setattr(launchpad_db_module, "upsert_staging_task", _fake_upsert)
    monkeypatch.setattr(staging_worker_module, "MAX_CONCURRENT_STAGING_TASKS", 1)
    monkeypatch.setattr(staging_worker_module, "run_staging", _fake_run)
    async def _no_block_reason(task):
        return None

    monkeypatch.setattr(staging_worker_module, "get_staging_task_start_block_reason", _no_block_reason)

    recovered = await recover_staging_tasks()
    await asyncio.sleep(0)

    assert [task.task_id for task in recovered] == ["stg-1", "stg-2"]
    assert ("stg-1", "queued") in persisted_statuses
    assert started == ["stg-1"]
    assert recovered[0]._task is not None
    assert recovered[1]._task is None

    release.set()
    await asyncio.gather(*(task._task for task in recovered if task._task is not None), return_exceptions=True)
    await asyncio.sleep(0)

    assert started == ["stg-1", "stg-2"]


@pytest.mark.asyncio
async def test_cleanup_old_tasks_removes_persisted_rows(monkeypatch):
    deleted_ids: list[str] = []
    now = 1_000.0

    async def _fake_delete(task_id, session=None):
        deleted_ids.append(task_id)
        return True

    import launchpad.db as launchpad_db_module

    monkeypatch.setattr(launchpad_db_module, "delete_staging_task", _fake_delete)
    monkeypatch.setattr(staging_worker_module.time, "time", lambda: now)
    monkeypatch.setattr(staging_worker_module, "_CLEANUP_TTL_SECONDS", 10.0)

    finished_task = StagingTaskState(task_id="done-1", status="completed", updated_at=now - 20.0)
    running_task = StagingTaskState(task_id="run-1", status="running", updated_at=now - 20.0)
    staging_worker_module.get_staging_tasks()[finished_task.task_id] = finished_task
    staging_worker_module.get_staging_tasks()[running_task.task_id] = running_task

    removed = await cleanup_old_tasks()

    assert removed == 1
    assert deleted_ids == ["done-1"]
    assert list(staging_worker_module.get_staging_tasks()) == ["run-1"]


@pytest.mark.asyncio
async def test_recover_staging_tasks_waits_for_reauth_when_local_broker_session_is_missing(monkeypatch, tmp_path):
    persisted_progress: list[dict] = []
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")

    record = SimpleNamespace(
        task_id="stg-auth-1",
        status="running",
        progress_json={"file_percent": 67},
        result_json=None,
        error=None,
        created_at=1.0,
        updated_at=2.0,
        params_json={
            "sample_name": "sample-auth",
            "ssh_profile_id": "profile-1",
            "user_id": "user-1",
        },
    )

    async def _fake_get_incomplete(session=None):
        return [record]

    async def _fake_get_profile(profile_id, user_id=None):
        return SimpleNamespace(
            id=profile_id,
            user_id=user_id,
            nickname="hpc3",
            is_enabled=True,
            auth_method="key_file",
            key_file_path=str(key_path),
            local_username="alice",
        )

    async def _fake_upsert(task, session=None):
        persisted_progress.append(dict(task.progress))
        return None

    class _FakeSessionManager:
        async def get_active_session(self, profile):
            return None

    async def _fail_run(task):
        raise AssertionError("Recovered task should not auto-start without auth")

    import launchpad.db as launchpad_db_module

    monkeypatch.setattr(launchpad_db_module, "get_incomplete_staging_tasks", _fake_get_incomplete)
    monkeypatch.setattr(launchpad_db_module, "get_ssh_profile", _fake_get_profile)
    monkeypatch.setattr(launchpad_db_module, "upsert_staging_task", _fake_upsert)
    monkeypatch.setattr("launchpad.backends.local_auth_sessions.get_local_auth_session_manager", lambda: _FakeSessionManager())
    monkeypatch.setattr("launchpad.backends.ssh_manager.resolve_key_file_path", lambda profile: str(key_path))
    monkeypatch.setattr(staging_worker_module.os, "access", lambda path, mode: False)
    monkeypatch.setattr(staging_worker_module, "run_staging", _fail_run)

    recovered = await recover_staging_tasks()

    assert len(recovered) == 1
    assert recovered[0].status == "queued"
    assert recovered[0]._task is None
    assert recovered[0].progress["waiting_for_auth"] is True
    assert "unlock" in recovered[0].progress["wait_reason"].lower()
    assert active_task_count() == 0
    assert any(progress.get("waiting_for_auth") is True for progress in persisted_progress)


@pytest.mark.asyncio
async def test_run_staging_requeues_when_auth_becomes_unavailable_during_start(monkeypatch):
    persisted_snapshots: list[dict] = []

    class _FakeSlurmBackend:
        def __init__(self):
            self._ssh_manager = SimpleNamespace(connect=self._connect)

        async def _connect(self, profile):
            raise PermissionError("No active local auth session for this profile. Unlock the profile in Remote Profiles first.")

        async def _load_profile(self, ssh_profile_id, user_id):
            return SimpleNamespace(id=ssh_profile_id, nickname="hpc3")

        def _derive_remote_roots(self, params, profile):
            return {
                "remote_base_path": "/remote/base",
                "ref_root": "/remote/base/ref",
                "data_root": "/remote/base/data",
            }

    async def _fake_upsert(task, session=None):
        persisted_snapshots.append(
            {
                "status": task.status,
                "progress": dict(task.progress),
                "error": task.error,
            }
        )
        return None

    async def _fake_wait_reason(task):
        return "Waiting for the Remote Profile unlock to be re-established after restart before staging can resume."

    from launchpad.backends import slurm_backend as slurm_backend_module
    import launchpad.db as launchpad_db_module

    monkeypatch.setattr(slurm_backend_module, "SlurmBackend", _FakeSlurmBackend)
    monkeypatch.setattr(slurm_backend_module, "SubmitParams", SubmitParams)
    monkeypatch.setattr(launchpad_db_module, "upsert_staging_task", _fake_upsert)
    monkeypatch.setattr(staging_worker_module, "get_staging_task_start_block_reason", _fake_wait_reason)

    task = StagingTaskState(
        task_id="task-auth-race-1",
        params={
            "project_id": "proj-1",
            "user_id": "user-1",
            "sample_name": "sample-a",
            "mode": "RNA",
            "input_directory": "/tmp/sample-a.bam",
            "reference_genome": ["GRCh38"],
            "ssh_profile_id": "profile-1",
        },
    )

    await run_staging(task)

    assert task.status == "queued"
    assert task.error is None
    assert task.progress["waiting_for_auth"] is True
    assert "unlock" in task.progress["wait_reason"].lower()
    assert persisted_snapshots[-1]["status"] == "queued"