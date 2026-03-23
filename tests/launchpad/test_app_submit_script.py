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
async def test_submit_script_job_success(monkeypatch, tmp_path):
    script_path = tmp_path / "run.py"
    script_path.write_text("print('ok')\n")

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
