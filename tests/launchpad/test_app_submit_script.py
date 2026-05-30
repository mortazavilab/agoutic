import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import launchpad.app as launchpad_app
from launchpad import config as launchpad_config
from common.database import Base
from cortex.models import SystemSetting, User
from launchpad.models import DogmeJob
from launchpad.schemas import SubmitJobRequest


class _FakeSession:
    def __init__(self, *, maintenance_mode: bool = False, user_roles: dict[str, str] | None = None):
        self.close_calls = 0
        self._user_roles = dict(user_roles or {})
        self._settings = {
            "maintenance_mode": SystemSetting(key="maintenance_mode", value="true" if maintenance_mode else "false"),
            "maintenance_message": SystemSetting(key="maintenance_message", value=""),
            "maintenance_starts_at": SystemSetting(key="maintenance_starts_at", value=""),
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, _obj):
        return None

    def add(self, row):
        if isinstance(row, SystemSetting):
            self._settings[row.key] = row

    def add_all(self, rows):
        for row in rows:
            self.add(row)

    async def execute(self, statement):
        descriptions = list(getattr(statement, "column_descriptions", []) or [])
        if not descriptions:
            return _ExecuteResult([])

        first = descriptions[0]
        entity = first.get("entity")
        expr = first.get("expr")
        expr_key = getattr(expr, "key", None)

        if entity is SystemSetting:
            return _ExecuteResult(list(self._settings.values()))
        if entity is DogmeJob:
            return _ExecuteResult([])
        if entity is User and expr_key == "role":
            roles = list(self._user_roles.values())
            return _ExecuteResult(roles[:1])
        if entity is User and expr_key == "email":
            return _ExecuteResult([])
        return _ExecuteResult([])

    async def close(self):
        self.close_calls += 1
        return None


class _FakeProcess:
    def __init__(self, pid: int = 4321):
        self.pid = pid


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


@pytest.fixture()
async def async_session_factory(tmp_path):
    db_path = tmp_path / "launchpad-app-submit.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    yield session_factory

    await engine.dispose()


async def _seed_user_and_maintenance(
    async_session_factory,
    *,
    user_id: str,
    role: str,
    mode: bool,
    message: str = "",
    starts_at: str = "",
):
    async with async_session_factory() as session:
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                role=role,
                username=user_id,
                is_active=True,
            )
        )
        session.add_all(
            [
                SystemSetting(key="maintenance_mode", value="true" if mode else "false"),
                SystemSetting(key="maintenance_message", value=message),
                SystemSetting(key="maintenance_starts_at", value=starts_at),
            ]
        )
        await session.commit()


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
async def test_submit_job_returns_503_for_non_admin_during_maintenance(monkeypatch, async_session_factory):
    await _seed_user_and_maintenance(
        async_session_factory,
        user_id="maint-user",
        role="user",
        mode=True,
        message="Launchpad maintenance window.",
    )

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: async_session_factory())

    req = SubmitJobRequest(
        project_id="proj-1",
        user_id="maint-user",
        sample_name="blocked-job",
        mode="DNA",
        input_directory="/tmp/input",
        run_type="script",
        script_id="demo",
        execution_mode="local",
    )

    result = await launchpad_app.submit_job(req)

    assert result.status_code == 503
    assert json.loads(result.body) == {
        "error": "maintenance_mode",
        "message": "Launchpad maintenance window.",
    }


@pytest.mark.asyncio
async def test_admin_submit_job_succeeds_during_maintenance(monkeypatch, async_session_factory, tmp_path):
    script_path = tmp_path / "run.py"
    script_path.write_text("print('ok')\n")
    subprocess_kwargs = {}

    await _seed_user_and_maintenance(
        async_session_factory,
        user_id="maint-admin",
        role="admin",
        mode=True,
        message="Admins only window.",
    )

    fake_job = SimpleNamespace(
        run_uuid="run-maint-admin",
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

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: async_session_factory())
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-maint-admin")
    monkeypatch.setattr(launchpad_app, "resolve_allowlisted_script", lambda **_kwargs: SimpleNamespace(script_id="demo", script_path=script_path.resolve()))
    monkeypatch.setattr(launchpad_app, "normalize_script_args", lambda args: args or [])
    monkeypatch.setattr(launchpad_app, "validate_script_working_directory", lambda _path: script_path.parent.resolve())
    monkeypatch.setattr(launchpad_app.asyncio, "create_subprocess_exec", fake_subprocess_exec)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)

    req = SubmitJobRequest(
        project_id="proj-1",
        user_id="maint-admin",
        sample_name="admin-script-job",
        mode="DNA",
        input_directory=str(script_path.parent),
        run_type="script",
        script_id="demo",
        script_args=["--dry-run"],
        script_working_directory=str(script_path.parent),
        execution_mode="local",
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-maint-admin"
    assert result["status"] == launchpad_app.JobStatus.RUNNING
    assert subprocess_kwargs["start_new_session"] is True


@pytest.mark.asyncio
async def test_get_job_status_still_returns_running_status_during_maintenance(monkeypatch, async_session_factory):
    await _seed_user_and_maintenance(
        async_session_factory,
        user_id="status-user",
        role="user",
        mode=True,
        message="Status remains readable.",
    )

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: async_session_factory())

    async def fake_get_job(session, run_uuid):
        assert run_uuid == "run-live"
        return SimpleNamespace(
            run_uuid="run-live",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.RUNNING,
            progress_percent=42,
            error_message=None,
            run_stage="running",
            slurm_job_id="50070001",
            slurm_state="RUNNING",
            transfer_state=None,
            result_destination="local",
            nextflow_work_dir="/local/project/workflow1",
            remote_work_dir="/remote/project/workflow1",
            workflow_usage_json=None,
            workflow_usage_synced_at=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    fake_backend = SimpleNamespace(
        check_status=AsyncMock(
            return_value=SimpleNamespace(
                run_uuid="run-live",
                status="RUNNING",
                progress_percent=42,
                message="Still running",
                tasks={"total": 10, "completed_count": 4, "failed_count": 0, "remaining_count": 6},
                execution_mode="slurm",
                run_stage="running",
                slurm_job_id="50070001",
                slurm_state="RUNNING",
                transfer_state=None,
                transfer_detail=None,
                result_destination="local",
                ssh_profile_nickname="hpc3",
                work_directory="/remote/project/workflow1",
                workflow_usage=None,
                workflow_usage_synced_at=None,
            )
        )
    )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: fake_backend)

    payload = await launchpad_app.get_job_status("run-live")

    assert payload["status"] == "RUNNING"
    assert payload["progress_percent"] == 42
    assert payload["tasks"]["remaining_count"] == 6


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
async def test_submit_wf_pore_c_job_persists_workflow_key_and_null_mode(
    monkeypatch,
    tmp_path,
    async_session_factory,
):
    work_dir = tmp_path / "workflow1"
    work_dir.mkdir()
    input_path = tmp_path / "inputs" / "sample.fastq.gz"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("@read\nACGT\n+\n!!!!\n", encoding="utf-8")
    reference_fasta = tmp_path / "refs" / "reference.fa"
    reference_fasta.parent.mkdir(parents=True, exist_ok=True)
    reference_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    create_task_calls = []
    executor_kwargs = {}

    async def fake_submit_job(**kwargs):
        executor_kwargs.update(kwargs)
        return (kwargs["run_uuid"], work_dir)

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    def fake_create_task(coro):
        create_task_calls.append(coro)
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", async_session_factory)
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app.executor, "submit_job", fake_submit_job)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-pore-c")

    req = SubmitJobRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-1",
        sample_name="POREC_A",
        workflow_key="wf_pore_c",
        mode=None,
        input_directory=str(input_path),
        input_type="fastq",
        reference_fasta=str(reference_fasta),
        reference_genome=["GRCh38"],
        execution_mode="local",
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-pore-c"
    assert result["status"] == launchpad_app.JobStatus.RUNNING
    assert result["work_directory"] == str(work_dir)
    assert executor_kwargs["workflow_key"] == "wf_pore_c"
    assert executor_kwargs["mode"] is None
    assert executor_kwargs["sample_name"] == "POREC_A"
    assert len(create_task_calls) == 1

    session = async_session_factory()
    try:
        row = await session.scalar(select(DogmeJob).where(DogmeJob.run_uuid == "run-pore-c"))
    finally:
        await session.close()

    assert row is not None
    assert row.workflow_key == "wf_pore_c"
    assert row.mode is None
    assert row.workflow_index == 1
    assert row.workflow_alias == "workflow1"
    assert row.workflow_folder_name == "workflow1"


@pytest.mark.asyncio
async def test_submit_wf_pore_c_job_rejects_when_flag_disabled(monkeypatch):
    fake_session = _FakeSession()
    create_called = False

    async def fake_create_job(*_args, **_kwargs):
        nonlocal create_called
        create_called = True
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", False)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)

    req = SubmitJobRequest(
        project_id="proj-1",
        sample_name="POREC_A",
        workflow_key="wf_pore_c",
        mode=None,
        input_directory="/data/pore-c.concatemers.bam",
        input_type="bam",
        reference_fasta="/refs/reference.fa",
        reference_genome=["GRCh38"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await launchpad_app.submit_job(req)

    assert exc_info.value.status_code == 400
    assert "WF_PORE_C_ENABLED" in exc_info.value.detail
    assert create_called is False


@pytest.mark.asyncio
async def test_submit_wf_pore_c_job_closes_session_on_success(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    fake_job = _fake_dogme_job()
    work_dir = tmp_path / "workflow1"
    work_dir.mkdir(parents=True, exist_ok=True)

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_get_workflow_identity_for_path(*_args, **_kwargs):
        return (None, None, None)

    async def fake_get_next_workflow_index(*_args, **_kwargs):
        return 1

    async def fake_submit_job(**_kwargs):
        return ("run-pore-c", work_dir)

    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_workflow_identity_for_path", fake_get_workflow_identity_for_path)
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", fake_get_next_workflow_index)
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)
    monkeypatch.setattr(launchpad_app.executor, "submit_job", fake_submit_job)
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-pore-c")

    req = SubmitJobRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-1",
        sample_name="POREC_A",
        workflow_key="wf_pore_c",
        mode=None,
        input_directory=str(tmp_path / "input.fastq"),
        input_type="fastq",
        reference_fasta=str(tmp_path / "reference.fa"),
        reference_genome=["GRCh38"],
        execution_mode="local",
    )

    result = await launchpad_app.submit_job(req)

    assert result["run_uuid"] == "run-pore-c"
    assert fake_session.close_calls == 1


@pytest.mark.asyncio
async def test_submit_wf_pore_c_job_closes_session_on_failure(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    fake_job = _fake_dogme_job()

    async def fake_create_job(*_args, **_kwargs):
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    async def fake_get_workflow_identity_for_path(*_args, **_kwargs):
        return (None, None, None)

    async def fake_get_next_workflow_index(*_args, **_kwargs):
        return 1

    async def failing_submit_job(**_kwargs):
        raise RuntimeError("launch failed")

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "get_workflow_identity_for_path", fake_get_workflow_identity_for_path)
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", fake_get_next_workflow_index)
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)
    monkeypatch.setattr(launchpad_app.executor, "submit_job", failing_submit_job)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "run-pore-c")

    req = SubmitJobRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-1",
        sample_name="POREC_A",
        workflow_key="wf_pore_c",
        mode=None,
        input_directory=str(tmp_path / "input.fastq"),
        input_type="fastq",
        reference_fasta=str(tmp_path / "reference.fa"),
        reference_genome=["GRCh38"],
        execution_mode="local",
    )

    with pytest.raises(HTTPException, match="Failed to submit job"):
        await launchpad_app.submit_job(req)

    assert fake_job.status == launchpad_app.JobStatus.FAILED
    assert "launch failed" in (fake_job.error_message or "")
    assert fake_session.close_calls == 1


@pytest.mark.asyncio
async def test_health_check_closes_session(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    result = await launchpad_app.health_check()

    assert result["database_ok"] is True
    assert result["running_jobs"] == 0
    assert fake_session.close_calls == 1


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
