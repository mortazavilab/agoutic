import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import launchpad.app as launchpad_app
from launchpad.schemas import SubmitJobRequest


class _FakeSession:
    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None

    async def close(self):
        return None


class _FakeProcess:
    def __init__(self, pid: int = 4321):
        self.pid = pid


@pytest.mark.asyncio
async def test_monitor_script_job_commits_completed_status_with_final_output_directory(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow10"
    workflow_dir.mkdir()
    stdout_log = tmp_path / "run-1.stdout.log"
    stderr_log = tmp_path / "run-1.stderr.log"
    stdout_log.write_text(
        json.dumps(
            {
                "success": True,
                "workflow": {
                    "directory": str(workflow_dir),
                    "output_directory": str(workflow_dir),
                },
            }
        ),
        encoding="utf-8",
    )
    stderr_log.write_text("", encoding="utf-8")

    fake_job = SimpleNamespace(
        run_uuid="run-1",
        status="RUNNING",
        progress_percent=0,
        error_message=None,
        completed_at=None,
        run_stage="SCRIPT_RUNNING",
        report_json=None,
        nextflow_work_dir=str(tmp_path),
        workflow_index=None,
        workflow_alias=None,
        workflow_folder_name=None,
    )

    class _CommitTrackingSession:
        def __init__(self, tracked_job):
            self.tracked_job = tracked_job
            self.commit_snapshots = []

        async def commit(self):
            self.commit_snapshots.append(
                {
                    "status": self.tracked_job.status,
                    "progress_percent": self.tracked_job.progress_percent,
                    "error_message": self.tracked_job.error_message,
                    "nextflow_work_dir": self.tracked_job.nextflow_work_dir,
                    "run_stage": self.tracked_job.run_stage,
                }
            )

        async def close(self):
            return None

    class _CompletingProcess:
        async def wait(self):
            return 0

    fake_session = _CommitTrackingSession(fake_job)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-1"
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "infer_workflow_index_from_path", lambda _path: 10)
    monkeypatch.setattr(launchpad_app, "workflow_alias_for_index", lambda index: f"workflow{index}")
    monkeypatch.setattr(launchpad_app, "script_processes", {"run-1": object()})
    monkeypatch.setattr(launchpad_app, "job_monitors", {"run-1": object()})

    await launchpad_app._monitor_script_job(
        "run-1",
        _CompletingProcess(),
        str(stdout_log),
        str(stderr_log),
    )

    assert fake_job.status == launchpad_app.JobStatus.COMPLETED
    assert fake_job.progress_percent == 100
    assert fake_job.run_stage == "SCRIPT_COMPLETED"
    assert fake_job.nextflow_work_dir == str(workflow_dir)
    assert fake_job.workflow_index == 10
    assert fake_job.workflow_alias == "workflow10"
    assert fake_job.workflow_folder_name == "workflow10"
    assert fake_session.commit_snapshots == [
        {
            "status": launchpad_app.JobStatus.COMPLETED,
            "progress_percent": 100,
            "error_message": None,
            "nextflow_work_dir": str(workflow_dir),
            "run_stage": "SCRIPT_COMPLETED",
        }
    ]
    assert "run-1" not in launchpad_app.script_processes
    assert "run-1" not in launchpad_app.job_monitors


@pytest.mark.asyncio
async def test_monitor_script_job_recovers_output_directory_from_large_stdout_payload(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow11"
    workflow_dir.mkdir()
    stdout_log = tmp_path / "run-2.stdout.log"
    stderr_log = tmp_path / "run-2.stderr.log"
    stdout_log.write_text(
        json.dumps(
            {
                "success": True,
                "workflow": {
                    "directory": str(workflow_dir),
                    "output_directory": str(workflow_dir),
                },
                "details": "x" * 6000,
            }
        ),
        encoding="utf-8",
    )
    stderr_log.write_text("", encoding="utf-8")

    fake_job = SimpleNamespace(
        run_uuid="run-2",
        status="RUNNING",
        progress_percent=0,
        error_message=None,
        completed_at=None,
        run_stage="SCRIPT_RUNNING",
        report_json=None,
        nextflow_work_dir=str(tmp_path),
        workflow_index=None,
        workflow_alias=None,
        workflow_folder_name=None,
    )

    class _CommitTrackingSession:
        def __init__(self, tracked_job):
            self.tracked_job = tracked_job

        async def commit(self):
            return None

        async def close(self):
            return None

    class _CompletingProcess:
        async def wait(self):
            return 0

    fake_session = _CommitTrackingSession(fake_job)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-2"
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "infer_workflow_index_from_path", lambda _path: 11)
    monkeypatch.setattr(launchpad_app, "workflow_alias_for_index", lambda index: f"workflow{index}")
    monkeypatch.setattr(launchpad_app, "script_processes", {"run-2": object()})
    monkeypatch.setattr(launchpad_app, "job_monitors", {"run-2": object()})

    await launchpad_app._monitor_script_job(
        "run-2",
        _CompletingProcess(),
        str(stdout_log),
        str(stderr_log),
    )

    assert fake_job.status == launchpad_app.JobStatus.COMPLETED
    assert fake_job.nextflow_work_dir == str(workflow_dir)
    assert fake_job.workflow_index == 11
    assert fake_job.workflow_alias == "workflow11"
    assert fake_job.workflow_folder_name == "workflow11"


def _fake_dogme_job():
    return SimpleNamespace(
        run_uuid="run-dogme",
        status="PENDING",
        execution_mode="local",
        ssh_profile_id=None,
        slurm_account=None,
        slurm_partition=None,
        slurm_cpus=None,
        slurm_memory_gb=None,
        slurm_walltime=None,
        slurm_gpus=None,
        slurm_gpu_type=None,
        result_destination=None,
        cache_preflight_json=None,
        reference_cache_status=None,
        data_cache_status=None,
        reference_cache_path=None,
        data_cache_path=None,
        run_stage=None,
        nextflow_process_id=None,
        nextflow_work_dir=None,
        log_file=None,
        stderr_log=None,
        report_json=None,
        started_at=None,
        workflow_folder_name=None,
    )


@pytest.mark.asyncio
async def test_submit_script_job_success(monkeypatch, tmp_path):
    script_path = tmp_path / "run.py"
    script_path.write_text("print('ok')\n")
    subprocess_kwargs = {}

    fake_session = _FakeSession()
    fake_job = SimpleNamespace(
        run_uuid="run-1",
        status="PENDING",
        execution_mode="local",
        ssh_profile_id=None,
        slurm_account=None,
        slurm_partition=None,
        slurm_cpus=None,
        slurm_memory_gb=None,
        slurm_walltime=None,
        slurm_gpus=None,
        slurm_gpu_type=None,
        result_destination=None,
        cache_preflight_json=None,
        reference_cache_status=None,
        data_cache_status=None,
        reference_cache_path=None,
        data_cache_path=None,
        run_stage=None,
        nextflow_process_id=None,
        nextflow_work_dir=None,
        log_file=None,
        stderr_log=None,
        report_json=None,
        started_at=None,
    )

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_subprocess_exec(*_args, **_kwargs):
        subprocess_kwargs.update(_kwargs)
        return _FakeProcess()

    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-1")
    monkeypatch.setattr(launchpad_app, "resolve_allowlisted_script", lambda **_kwargs: SimpleNamespace(script_id="demo", script_path=script_path.resolve()))
    monkeypatch.setattr(launchpad_app, "normalize_script_args", lambda args: args or [])
    monkeypatch.setattr(launchpad_app, "validate_script_working_directory", lambda _path: script_path.parent.resolve())
    monkeypatch.setattr(launchpad_app.asyncio, "create_subprocess_exec", fake_subprocess_exec)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)

    req = SubmitJobRequest(
        project_id="proj-1",
        sample_name="script-job",
        mode="DNA",
        input_directory=str(script_path.parent),
        run_type="script",
        script_id="demo",
        script_args=["--dry-run"],
        script_working_directory=str(script_path.parent),
        execution_mode="local",
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-1"
    assert result["status"] == launchpad_app.JobStatus.RUNNING
    assert result["work_directory"] == str(script_path.parent.resolve())
    assert fake_job.run_stage == "SCRIPT_RUNNING"
    assert fake_job.nextflow_process_id == 4321
    assert subprocess_kwargs["start_new_session"] is True


@pytest.mark.asyncio
async def test_submit_script_job_failure_surfaces_error(monkeypatch, tmp_path):
    script_path = tmp_path / "run.py"
    script_path.write_text("print('ok')\n")

    fake_session = _FakeSession()
    fake_job = SimpleNamespace(
        run_uuid="run-2",
        status="PENDING",
        execution_mode="local",
        ssh_profile_id=None,
        slurm_account=None,
        slurm_partition=None,
        slurm_cpus=None,
        slurm_memory_gb=None,
        slurm_walltime=None,
        slurm_gpus=None,
        slurm_gpu_type=None,
        result_destination=None,
        cache_preflight_json=None,
        reference_cache_status=None,
        data_cache_status=None,
        reference_cache_path=None,
        data_cache_path=None,
        error_message=None,
    )

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fail_subprocess(*_args, **_kwargs):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-2")
    monkeypatch.setattr(launchpad_app, "resolve_allowlisted_script", lambda **_kwargs: SimpleNamespace(script_id="demo", script_path=script_path.resolve()))
    monkeypatch.setattr(launchpad_app, "normalize_script_args", lambda args: args or [])
    monkeypatch.setattr(launchpad_app, "validate_script_working_directory", lambda _path: script_path.parent.resolve())
    monkeypatch.setattr(launchpad_app.asyncio, "create_subprocess_exec", fail_subprocess)

    req = SubmitJobRequest(
        project_id="proj-1",
        sample_name="script-job",
        mode="DNA",
        input_directory=str(script_path.parent),
        run_type="script",
        script_id="demo",
        execution_mode="local",
    )

    with pytest.raises(HTTPException, match="Failed to submit job"):
        await launchpad_app.submit_job(req)

    assert fake_job.status == launchpad_app.JobStatus.FAILED
    assert "spawn failed" in (fake_job.error_message or "")


@pytest.mark.asyncio
async def test_submit_dogme_job_explicit_null_gpu_limit_skips_default(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    fake_job = _fake_dogme_job()
    executor_kwargs = {}

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_submit_job(**kwargs):
        executor_kwargs.update(kwargs)
        work_dir = tmp_path / "workflow1"
        work_dir.mkdir(parents=True, exist_ok=True)
        return ("run-dogme", work_dir)

    async def fake_monitor_job(_run_uuid, _work_dir):
        return None

    async def fake_get_workflow_identity_for_path(*_args, **_kwargs):
        return (None, None, None)

    async def fake_get_next_workflow_index(*_args, **_kwargs):
        return 1

    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_workflow_identity_for_path", fake_get_workflow_identity_for_path)
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", fake_get_next_workflow_index)
    monkeypatch.setattr(launchpad_app.executor, "submit_job", fake_submit_job)
    monkeypatch.setattr(launchpad_app, "monitor_job", fake_monitor_job)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-dogme")
    monkeypatch.setattr(launchpad_app, "DEFAULT_MAX_GPU_TASKS", 3)

    req = SubmitJobRequest(
        project_id="proj-1",
        sample_name="sample-a",
        mode="DNA",
        input_directory=str(tmp_path / "input"),
        execution_mode="local",
        max_gpu_tasks=None,
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-dogme"
    assert executor_kwargs["max_gpu_tasks"] is None


@pytest.mark.asyncio
async def test_submit_dogme_job_omitted_gpu_limit_uses_default(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    fake_job = _fake_dogme_job()
    executor_kwargs = {}

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_submit_job(**kwargs):
        executor_kwargs.update(kwargs)
        work_dir = tmp_path / "workflow1"
        work_dir.mkdir(parents=True, exist_ok=True)
        return ("run-dogme", work_dir)

    async def fake_monitor_job(_run_uuid, _work_dir):
        return None

    async def fake_get_workflow_identity_for_path(*_args, **_kwargs):
        return (None, None, None)

    async def fake_get_next_workflow_index(*_args, **_kwargs):
        return 1

    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_workflow_identity_for_path", fake_get_workflow_identity_for_path)
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", fake_get_next_workflow_index)
    monkeypatch.setattr(launchpad_app.executor, "submit_job", fake_submit_job)
    monkeypatch.setattr(launchpad_app, "monitor_job", fake_monitor_job)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-dogme")
    monkeypatch.setattr(launchpad_app, "DEFAULT_MAX_GPU_TASKS", 3)

    req = SubmitJobRequest(
        project_id="proj-1",
        sample_name="sample-a",
        mode="DNA",
        input_directory=str(tmp_path / "input"),
        execution_mode="local",
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-dogme"
    assert executor_kwargs["max_gpu_tasks"] == 3


@pytest.mark.asyncio
async def test_submit_dogme_job_forwards_local_resource_caps(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    fake_job = _fake_dogme_job()
    executor_kwargs = {}

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_submit_job(**kwargs):
        executor_kwargs.update(kwargs)
        work_dir = tmp_path / "workflow1"
        work_dir.mkdir(parents=True, exist_ok=True)
        return ("run-dogme", work_dir)

    async def fake_monitor_job(_run_uuid, _work_dir):
        return None

    async def fake_get_workflow_identity_for_path(*_args, **_kwargs):
        return (None, None, None)

    async def fake_get_next_workflow_index(*_args, **_kwargs):
        return 1

    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_workflow_identity_for_path", fake_get_workflow_identity_for_path)
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", fake_get_next_workflow_index)
    monkeypatch.setattr(launchpad_app.executor, "submit_job", fake_submit_job)
    monkeypatch.setattr(launchpad_app, "monitor_job", fake_monitor_job)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-dogme")

    req = SubmitJobRequest(
        project_id="proj-1",
        sample_name="sample-a",
        mode="DNA",
        input_directory=str(tmp_path / "input"),
        execution_mode="local",
        local_max_task_cpus=8,
        local_max_task_memory_gb=48,
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-dogme"
    assert executor_kwargs["local_max_task_cpus"] == 8
    assert executor_kwargs["local_max_task_memory_gb"] == 48


@pytest.mark.asyncio
async def test_submit_slurm_job_preserves_gpu_account_overrides(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    fake_job = _fake_dogme_job()
    captured = {}

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_get_workflow_identity_for_path(*_args, **_kwargs):
        return (None, None, None)

    async def fake_get_next_workflow_index(*_args, **_kwargs):
        return 1

    class _FakeBackend:
        async def submit(self, _run_uuid, submit_params):
            captured["submit_params"] = submit_params
            return "run-dogme"

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_workflow_identity_for_path", fake_get_workflow_identity_for_path)
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", fake_get_next_workflow_index)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: _FakeBackend())
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-dogme")

    req = SubmitJobRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-1",
        sample_name="sample-a",
        mode="RNA",
        input_directory=str(tmp_path / "input"),
        execution_mode="slurm",
        ssh_profile_id="profile-1",
        slurm_account="BIOD132_CLASS",
        slurm_partition="standard",
        slurm_gpu_account="BIOD132_CLASS_GPU ",
        slurm_gpu_partition="gpu",
        slurm_cpus=4,
        slurm_memory_gb=16,
        slurm_walltime="48:00:00",
        slurm_gpus=1,
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-dogme"
    assert captured["submit_params"].slurm_gpu_account == "BIOD132_CLASS_GPU "
    assert captured["submit_params"].slurm_gpu_partition == "gpu"
