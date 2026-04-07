from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import launchpad.app as launchpad_app
from launchpad.backends.base import SubmitParams


class _FakeSession:
    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None

    async def close(self):
        return None


class _FakeExecuteResult:
    def __init__(self, scalar_value=None, items=None):
        self._scalar_value = scalar_value
        self._items = items or []

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


@pytest.mark.asyncio
async def test_rerun_job_local_reuses_workflow_identity_and_archives_previous_names(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    rerun_job = SimpleNamespace(
        run_uuid="rerun-1",
        status=launchpad_app.JobStatus.PENDING,
        reference_cache_status=None,
        data_cache_status=None,
        reference_cache_path=None,
        data_cache_path=None,
        cache_preflight_json=None,
        nextflow_process_id=None,
        nextflow_work_dir=None,
        workflow_folder_name="workflow7",
        result_destination="local",
        remote_work_dir=None,
        remote_output_dir=None,
        started_at=None,
    )
    source_job = SimpleNamespace(
        run_uuid="source-1",
        project_id="proj-1",
        workflow_index=7,
        workflow_alias="workflow7",
        workflow_folder_name="workflow7",
        workflow_display_name="tumor-retry",
        sample_name="tumor-original",
        mode="DNA",
        input_directory="/input/pod5",
        reference_genome='["GRCh38", "mm39"]',
        modifications="5mCG_5hmCG,6mA",
        parent_block_id="block-1",
        user_id="user-1",
        status=launchpad_app.JobStatus.COMPLETED,
        execution_mode="local",
        ssh_profile_id=None,
        slurm_account=None,
        slurm_partition=None,
        slurm_cpus=None,
        slurm_memory_gb=None,
        slurm_walltime=None,
        slurm_gpus=None,
        slurm_gpu_type=None,
        result_destination="local",
        cache_preflight_json=None,
        reference_cache_status=None,
        data_cache_status=None,
        reference_cache_path=None,
        data_cache_path=None,
        nextflow_work_dir=str(tmp_path / "workflow7"),
        remote_work_dir=None,
        remote_output_dir=None,
    )
    work_dir = tmp_path / "workflow7"
    work_dir.mkdir()
    (work_dir / ".nextflow_pid").write_text("9876\n", encoding="utf-8")

    create_job_kwargs = {}
    executor_kwargs = {}

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "source-1"
        return source_job

    async def fake_create_job(session, **kwargs):
        assert session is fake_session
        create_job_kwargs.update(kwargs)
        return rerun_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_submit_job(**kwargs):
        executor_kwargs.update(kwargs)
        return ("nextflow run ... -rerun", work_dir)

    async def fake_monitor_job(_run_uuid, _work_dir):
        return None

    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app.executor, "submit_job", fake_submit_job)
    monkeypatch.setattr(launchpad_app, "monitor_job", fake_monitor_job)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "rerun-1")

    result = await launchpad_app.rerun_job("source-1")

    assert create_job_kwargs["workflow_index"] == 7
    assert create_job_kwargs["workflow_alias"] == "workflow7"
    assert create_job_kwargs["workflow_folder_name"] == "workflow7"
    assert create_job_kwargs["workflow_display_name"] == "tumor-retry"
    assert create_job_kwargs["sample_name"] == "tumor-retry"
    assert create_job_kwargs["reference_genome"] == ["GRCh38", "mm39"]
    assert executor_kwargs["rerun_in_place"] is True
    assert executor_kwargs["archive_sample_names"] == ["tumor-original", "tumor-retry"]
    assert executor_kwargs["resume_from_dir"] == str(work_dir)
    assert executor_kwargs["workflow_index"] == 7
    assert rerun_job.status == launchpad_app.JobStatus.RUNNING
    assert rerun_job.nextflow_process_id == 9876
    assert result["run_uuid"] == "rerun-1"
    assert result["sample_name"] == "tumor-retry"
    assert result["work_directory"] == str(work_dir)


@pytest.mark.asyncio
async def test_rerun_job_slurm_uses_backend_rerun_existing(monkeypatch):
    fake_session = _FakeSession()
    rerun_job = SimpleNamespace(
        run_uuid="rerun-2",
        status=launchpad_app.JobStatus.PENDING,
        reference_cache_status="ready",
        data_cache_status="ready",
        reference_cache_path="/cache/ref",
        data_cache_path="/cache/data",
        cache_preflight_json={"reference": "ready"},
        nextflow_work_dir=None,
        workflow_folder_name="workflow3",
        result_destination="local",
        remote_work_dir=None,
        remote_output_dir=None,
        started_at=None,
    )
    source_job = SimpleNamespace(
        run_uuid="source-2",
        project_id="proj-2",
        workflow_index=3,
        workflow_alias="workflow3",
        workflow_folder_name="workflow3",
        workflow_display_name=None,
        sample_name="sample-3",
        mode="RNA",
        input_directory="/input/pod5",
        reference_genome="GRCh38",
        modifications=None,
        parent_block_id=None,
        user_id="user-2",
        status=launchpad_app.JobStatus.FAILED,
        execution_mode="slurm",
        ssh_profile_id="ssh-1",
        slurm_account="acct",
        slurm_partition="gpu",
        slurm_cpus=12,
        slurm_memory_gb=64,
        slurm_walltime="48:00:00",
        slurm_gpus=1,
        slurm_gpu_type="a100",
        result_destination="local",
        cache_preflight_json={"reference": "ready"},
        reference_cache_status="ready",
        data_cache_status="ready",
        reference_cache_path="/cache/ref",
        data_cache_path="/cache/data",
        nextflow_work_dir="/local/project/workflow3",
        remote_work_dir="/remote/project/workflow3",
        remote_output_dir="/remote/project/workflow3/output",
    )
    backend_calls = {}

    class _FakeBackend:
        async def rerun_existing(self, run_uuid, params, remote_work, remote_output, local_work_dir, archive_sample_names):
            backend_calls["run_uuid"] = run_uuid
            backend_calls["params"] = params
            backend_calls["remote_work"] = remote_work
            backend_calls["remote_output"] = remote_output
            backend_calls["local_work_dir"] = local_work_dir
            backend_calls["archive_sample_names"] = archive_sample_names

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "source-2"
        return source_job

    async def fake_create_job(session, **kwargs):
        assert session is fake_session
        return rerun_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: _FakeBackend())
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "rerun-2")

    result = await launchpad_app.rerun_job("source-2")

    assert backend_calls["run_uuid"] == "rerun-2"
    assert isinstance(backend_calls["params"], SubmitParams)
    assert backend_calls["params"].rerun_in_place is True
    assert backend_calls["params"].workflow_number == 3
    assert backend_calls["params"].reference_genome == ["GRCh38"]
    assert backend_calls["remote_work"] == "/remote/project/workflow3"
    assert backend_calls["remote_output"] == "/remote/project/workflow3/output"
    assert backend_calls["local_work_dir"] == "/local/project/workflow3"
    assert backend_calls["archive_sample_names"] == ["sample-3"]
    assert rerun_job.nextflow_work_dir == "/local/project/workflow3"
    assert rerun_job.remote_work_dir == "/remote/project/workflow3"
    assert result["run_uuid"] == "rerun-2"
    assert result["sample_name"] == "sample-3"
    assert result["work_directory"] == "/local/project/workflow3"


@pytest.mark.asyncio
async def test_rerun_job_rejects_non_terminal_status(monkeypatch):
    fake_session = _FakeSession()

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        return SimpleNamespace(status=launchpad_app.JobStatus.RUNNING)

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)

    with pytest.raises(HTTPException, match="Cannot rerun job with status"):
        await launchpad_app.rerun_job("source-3")


@pytest.mark.asyncio
async def test_rename_job_workflow_local_updates_all_attempts_and_files(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow7"
    workflow_dir.mkdir()
    (workflow_dir / "tumor-old_report.html").write_text("report", encoding="utf-8")
    (workflow_dir / "tumor-old_timeline.html").write_text("timeline", encoding="utf-8")
    (workflow_dir / "tumor-old_trace.txt").write_text("trace", encoding="utf-8")

    workflow_jobs = [
        SimpleNamespace(
            run_uuid="source-4",
            project_id="proj-4",
            workflow_index=7,
            workflow_alias="workflow7",
            workflow_folder_name="workflow7",
            workflow_display_name="tumor-old",
            sample_name="tumor-old",
            nextflow_work_dir=str(workflow_dir),
            nextflow_config_path=str(workflow_dir / "nextflow.config"),
            output_directory=str(workflow_dir / "output"),
            log_file=str(workflow_dir / "run.log"),
            stderr_log=str(workflow_dir / "run.err"),
            remote_work_dir=None,
            remote_output_dir=None,
        ),
        SimpleNamespace(
            run_uuid="rerun-old",
            project_id="proj-4",
            workflow_index=7,
            workflow_alias="workflow7",
            workflow_folder_name="workflow7",
            workflow_display_name="tumor-old",
            sample_name="tumor-old",
            nextflow_work_dir=str(workflow_dir),
            nextflow_config_path=str(workflow_dir / "nextflow.config"),
            output_directory=str(workflow_dir / "output"),
            log_file=str(workflow_dir / "run.log"),
            stderr_log=str(workflow_dir / "run.err"),
            remote_work_dir=None,
            remote_output_dir=None,
        ),
    ]
    source_job = SimpleNamespace(
        run_uuid="source-4",
        project_id="proj-4",
        workflow_index=7,
        workflow_alias="workflow7",
        workflow_folder_name="workflow7",
        workflow_display_name="tumor-old",
        sample_name="tumor-old",
        nextflow_work_dir=str(workflow_dir),
        remote_work_dir=None,
        remote_output_dir=None,
        ssh_profile_id=None,
        user_id="user-4",
        status=launchpad_app.JobStatus.COMPLETED,
    )

    class _RenameSession(_FakeSession):
        def __init__(self):
            self.calls = 0

        async def execute(self, _query):
            self.calls += 1
            if self.calls == 1:
                return _FakeExecuteResult(scalar_value=None)
            return _FakeExecuteResult(items=workflow_jobs)

    fake_session = _RenameSession()

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "source-4"
        return source_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)

    result = await launchpad_app.rename_job_workflow(
        launchpad_app.WorkflowRenameRequest(new_name="tumor-renamed"),
        "source-4",
    )

    renamed_dir = tmp_path / "tumor-renamed"
    assert renamed_dir.exists()
    assert not workflow_dir.exists()
    assert (renamed_dir / "tumor-renamed_report.html").exists()
    assert (renamed_dir / "tumor-renamed_timeline.html").exists()
    assert (renamed_dir / "tumor-renamed_trace.txt").exists()
    assert all(job.sample_name == "tumor-renamed" for job in workflow_jobs)
    assert all(job.workflow_folder_name == "tumor-renamed" for job in workflow_jobs)
    assert all(job.workflow_display_name == "tumor-renamed" for job in workflow_jobs)
    assert all(job.nextflow_work_dir == str(renamed_dir) for job in workflow_jobs)
    assert result["status"] == "renamed"
    assert result["new_name"] == "tumor-renamed"
    assert result["work_directory"] == str(renamed_dir)


@pytest.mark.asyncio
async def test_rename_job_workflow_slurm_uses_backend_and_updates_remote_paths(monkeypatch):
    workflow_jobs = [
        SimpleNamespace(
            run_uuid="source-5",
            project_id="proj-5",
            workflow_index=3,
            workflow_alias="workflow3",
            workflow_folder_name="workflow3",
            workflow_display_name="sample-3",
            sample_name="sample-3",
            nextflow_work_dir="/local/project/workflow3",
            nextflow_config_path="/local/project/workflow3/nextflow.config",
            output_directory="/local/project/workflow3/output",
            log_file="/local/project/workflow3/run.log",
            stderr_log="/local/project/workflow3/run.err",
            remote_work_dir="/remote/project/workflow3",
            remote_output_dir="/remote/project/workflow3/output",
        )
    ]
    source_job = SimpleNamespace(
        run_uuid="source-5",
        project_id="proj-5",
        workflow_index=3,
        workflow_alias="workflow3",
        workflow_folder_name="workflow3",
        workflow_display_name="sample-3",
        sample_name="sample-3",
        nextflow_work_dir=None,
        remote_work_dir="/remote/project/workflow3",
        remote_output_dir="/remote/project/workflow3/output",
        ssh_profile_id="ssh-5",
        user_id="user-5",
        status=launchpad_app.JobStatus.FAILED,
    )
    backend_calls = {}

    class _RenameSession(_FakeSession):
        def __init__(self):
            self.calls = 0

        async def execute(self, _query):
            self.calls += 1
            if self.calls == 1:
                return _FakeExecuteResult(scalar_value=None)
            return _FakeExecuteResult(items=workflow_jobs)

    class _FakeBackend:
        async def rename_workflow(self, **kwargs):
            backend_calls.update(kwargs)
            return "/remote/project/sample-3-renamed", "/remote/project/sample-3-renamed/output"

    fake_session = _RenameSession()

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "source-5"
        return source_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: _FakeBackend())

    result = await launchpad_app.rename_job_workflow(
        launchpad_app.WorkflowRenameRequest(new_name="sample-3-renamed"),
        "source-5",
    )

    assert backend_calls["ssh_profile_id"] == "ssh-5"
    assert backend_calls["old_remote_work"] == "/remote/project/workflow3"
    assert backend_calls["new_folder_name"] == "sample-3-renamed"
    assert backend_calls["new_sample_name"] == "sample-3-renamed"
    assert workflow_jobs[0].remote_work_dir == "/remote/project/sample-3-renamed"
    assert workflow_jobs[0].remote_output_dir == "/remote/project/sample-3-renamed/output"
    assert workflow_jobs[0].sample_name == "sample-3-renamed"
    assert result["work_directory"] == "/remote/project/sample-3-renamed"


@pytest.mark.asyncio
async def test_rename_job_workflow_rejects_reserved_alias_style_name(monkeypatch):
    fake_session = _FakeSession()

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        return SimpleNamespace(
            workflow_index=9,
            status=launchpad_app.JobStatus.COMPLETED,
            sample_name="sample-9",
            workflow_display_name="sample-9",
            workflow_folder_name="workflow9",
            nextflow_work_dir=None,
            project_id="proj-9",
        )

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)

    with pytest.raises(HTTPException, match="reserved"):
        await launchpad_app.rename_job_workflow(
            launchpad_app.WorkflowRenameRequest(new_name="workflow12"),
            "source-9",
        )