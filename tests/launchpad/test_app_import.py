import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import launchpad.app as launchpad_app
from launchpad.import_workflows import WorkflowImportMetadata
from launchpad.schemas import ImportWorkflowRequest, StageRemoteSampleRequest


class _FakeSession:
    async def commit(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_import_existing_workflow_local_uses_config_metadata(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    source_dir = tmp_path / "completed-workflow"
    source_dir.mkdir()
    destination_root = tmp_path / "project-root"
    copied_targets = []
    create_job_kwargs = {}
    fake_job = SimpleNamespace(
        run_uuid="import-run-1",
        sample_name="sample-a",
        mode="DNA",
        status=launchpad_app.JobStatus.PENDING,
        progress_percent=0,
        execution_mode="local",
        result_destination="local",
        nextflow_work_dir=None,
        transfer_state=None,
        error_message=None,
        completed_at=None,
        started_at=None,
        imported_source_complete=None,
    )

    async def fake_create_job(session, **kwargs):
        assert session is fake_session
        create_job_kwargs.update(kwargs)
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_maintenance_state_async", AsyncMock(return_value={"mode": False}))
    monkeypatch.setattr(launchpad_app, "normalize_local_workflow_source", lambda _path: str(source_dir))
    monkeypatch.setattr(launchpad_app, "find_job_by_workflow_path", AsyncMock(return_value=None))
    monkeypatch.setattr(launchpad_app, "find_import_duplicate", AsyncMock(return_value=None))
    monkeypatch.setattr(
        launchpad_app,
        "infer_local_workflow_metadata",
        lambda _path: WorkflowImportMetadata(
            workflow_key="dogme",
            sample_name="sample-a",
            mode="DNA",
            reference_genome=["GRCh38", "mm39"],
            modifications="5mCG_5hmCG,6mA",
            config_path=str(source_dir / "custom-run.config"),
            source_complete=True,
            input_directory="/input/pod5",
        ),
    )
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", AsyncMock(return_value=3))
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "_resolve_project_workflow_root", lambda **_kwargs: destination_root)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "import-run-1")

    def fake_copy_local_results(source: Path, destination: Path, *, full_copy: bool, workflow_key: str | None = None):
        copied_targets.append((source, destination, full_copy))
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "custom-run.config").write_text("ok\n", encoding="utf-8")

    monkeypatch.setattr(launchpad_app, "copy_local_results_to_workflow", fake_copy_local_results)

    request = ImportWorkflowRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-a",
        source_path=str(source_dir),
        source_kind="local",
    )

    response = await launchpad_app.import_existing_workflow(request)

    assert create_job_kwargs["sample_name"] == "sample-a"
    assert create_job_kwargs["reference_genome"] == ["GRCh38", "mm39"]
    assert create_job_kwargs["modifications"] == "5mCG_5hmCG,6mA"
    assert create_job_kwargs["workflow_index"] == 3
    assert create_job_kwargs["workflow_alias"] == "workflow3"
    assert copied_targets == [(source_dir, destination_root / "workflow3", False)]
    assert response.run_uuid == "import-run-1"
    assert response.sample_name == "sample-a"
    assert response.mode == "DNA"
    assert response.status == launchpad_app.JobStatus.COMPLETED
    assert response.work_directory == str(destination_root / "workflow3")


@pytest.mark.asyncio
async def test_import_existing_workflow_local_recognizes_wf_pore_c_metadata(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    source_dir = tmp_path / "completed-wf-pore-c"
    source_dir.mkdir()
    destination_root = tmp_path / "project-root"
    copied_targets = []
    create_job_kwargs = {}
    fake_job = SimpleNamespace(
        run_uuid="import-run-pore-c",
        sample_name="POREC_A",
        mode=None,
        status=launchpad_app.JobStatus.PENDING,
        progress_percent=0,
        execution_mode="local",
        result_destination="local",
        nextflow_work_dir=None,
        transfer_state=None,
        error_message=None,
        completed_at=None,
        started_at=None,
        imported_source_complete=None,
    )

    async def fake_create_job(session, **kwargs):
        assert session is fake_session
        create_job_kwargs.update(kwargs)
        return fake_job

    async def fake_add_log_entry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_maintenance_state_async", AsyncMock(return_value={"mode": False}))
    monkeypatch.setattr(launchpad_app, "normalize_local_workflow_source", lambda _path: str(source_dir))
    monkeypatch.setattr(launchpad_app, "find_job_by_workflow_path", AsyncMock(return_value=None))
    monkeypatch.setattr(launchpad_app, "find_import_duplicate", AsyncMock(return_value=None))
    monkeypatch.setattr(
        launchpad_app,
        "infer_local_workflow_metadata",
        lambda _path: WorkflowImportMetadata(
            workflow_key="wf_pore_c",
            sample_name="POREC_A",
            mode=None,
            reference_genome=["GRCh38"],
            modifications=None,
            config_path=str(source_dir / ".agoutic.workflow.json"),
            source_complete=True,
            input_directory="/input/fastq",
        ),
    )
    monkeypatch.setattr(launchpad_app, "get_next_workflow_index", AsyncMock(return_value=4))
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)
    monkeypatch.setattr(launchpad_app, "add_log_entry", fake_add_log_entry)
    monkeypatch.setattr(launchpad_app, "_resolve_project_workflow_root", lambda **_kwargs: destination_root)
    monkeypatch.setattr(launchpad_app.uuid, "uuid4", lambda: "import-run-pore-c")

    def fake_copy_local_results(source: Path, destination: Path, *, full_copy: bool, workflow_key: str | None = None):
        copied_targets.append((source, destination, full_copy))
        destination.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(launchpad_app, "copy_local_results_to_workflow", fake_copy_local_results)

    request = ImportWorkflowRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-a",
        source_path=str(source_dir),
        source_kind="local",
    )

    response = await launchpad_app.import_existing_workflow(request)

    assert create_job_kwargs["workflow_key"] == "wf_pore_c"
    assert create_job_kwargs["sample_name"] == "POREC_A"
    assert create_job_kwargs["mode"] is None
    assert create_job_kwargs["reference_genome"] == ["GRCh38"]
    assert create_job_kwargs["modifications"] is None
    assert create_job_kwargs["workflow_index"] == 4
    assert create_job_kwargs["workflow_alias"] == "workflow4"
    assert copied_targets == [(source_dir, destination_root / "workflow4", False)]
    assert response.run_uuid == "import-run-pore-c"
    assert response.sample_name == "POREC_A"
    assert response.mode is None
    assert response.status == launchpad_app.JobStatus.COMPLETED
    assert response.work_directory == str(destination_root / "workflow4")


@pytest.mark.asyncio
async def test_import_existing_workflow_blocks_non_admin_during_maintenance(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        launchpad_app,
        "get_maintenance_state_async",
        AsyncMock(return_value={"mode": True, "message": "Drain mode"}),
    )
    monkeypatch.setattr(launchpad_app, "_submitter_is_admin", AsyncMock(return_value=False))

    request = ImportWorkflowRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-a",
        source_path="/tmp/workflow",
        source_kind="local",
    )

    response = await launchpad_app.import_existing_workflow(request)

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "error": "maintenance_mode",
        "message": "Drain mode",
    }


@pytest.mark.asyncio
async def test_stage_remote_sample_blocks_non_admin_during_maintenance(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        launchpad_app,
        "get_maintenance_state_async",
        AsyncMock(return_value={"mode": True, "message": "Transfers paused"}),
    )
    monkeypatch.setattr(launchpad_app, "_submitter_is_admin", AsyncMock(return_value=False))

    request = StageRemoteSampleRequest(
        project_id="proj-1",
        user_id="user-1",
        username="alice",
        project_slug="proj-a",
        sample_name="sample-a",
        mode="DNA",
        input_directory="/input/pod5",
        reference_genome=["mm39"],
        ssh_profile_id="ssh-1",
    )

    response = await launchpad_app.stage_remote_sample(request)

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "error": "maintenance_mode",
        "message": "Transfers paused",
    }


@pytest.mark.asyncio
async def test_sync_job_results_to_local_dispatches_imported_local_job(monkeypatch):
    fake_session = _FakeSession()
    job = SimpleNamespace(run_uuid="run-2", execution_mode="local", imported_source_kind="local")
    sync_mock = AsyncMock(
        return_value={
            "success": True,
            "status": "outputs_downloaded",
            "message": "Workflow outputs synchronized into the project workflow directory.",
            "run_uuid": "run-2",
            "transfer_state": "outputs_downloaded",
            "import_warning_message": None,
        }
    )

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-2"
        return job

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "_sync_imported_or_remote_job", sync_mock)

    response = await launchpad_app.sync_job_results_to_local("run-2", force=False)

    assert response["success"] is True
    assert response["status"] == "outputs_downloaded"
    sync_mock.assert_awaited_once_with(job, force=False)


@pytest.mark.asyncio
async def test_sync_imported_local_job_refreshes_partial_warning_before_copy(monkeypatch, tmp_path):
    source_dir = tmp_path / "completed-source"
    destination_dir = tmp_path / "workflow5"
    source_dir.mkdir()
    destination_dir.mkdir()
    job = SimpleNamespace(
        run_uuid="run-3",
        imported_source_kind="local",
        imported_source_path=str(source_dir),
        nextflow_work_dir=str(destination_dir),
        imported_copy_mode="subset",
        imported_source_complete=False,
        imported_config_path=None,
        transfer_state=None,
        status=launchpad_app.JobStatus.COMPLETED,
        progress_percent=100,
        completed_at=None,
        error_message=None,
    )

    async def fake_commit_job(_job):
        return None

    monkeypatch.setattr(launchpad_app, "sessionless_commit_job", fake_commit_job)
    monkeypatch.setattr(
        launchpad_app,
        "infer_local_workflow_metadata",
        lambda _path: WorkflowImportMetadata(
            workflow_key="dogme",
            sample_name="sample-a",
            mode="DNA",
            reference_genome=["GRCh38"],
            modifications="5mC",
            config_path=str(source_dir / "nextflow.config"),
            source_complete=True,
            input_directory="/input/pod5",
        ),
    )
    monkeypatch.setattr(launchpad_app, "copy_local_results_to_workflow", lambda *_args, **_kwargs: None)

    response = await launchpad_app._sync_imported_or_remote_job(job, force=True)

    assert job.imported_source_complete is True
    assert response["success"] is True
    assert response["import_warning_message"] is None


@pytest.mark.asyncio
async def test_sync_imported_local_wf_pore_c_job_passes_workflow_key_to_copy(monkeypatch, tmp_path):
    source_dir = tmp_path / "completed-pore-c-source"
    destination_dir = tmp_path / "workflow6"
    source_dir.mkdir()
    destination_dir.mkdir()
    job = SimpleNamespace(
        run_uuid="run-pore-c-sync",
        workflow_key="wf_pore_c",
        imported_source_kind="local",
        imported_source_path=str(source_dir),
        nextflow_work_dir=str(destination_dir),
        imported_copy_mode="subset",
        imported_source_complete=True,
        imported_config_path=None,
        transfer_state=None,
        status=launchpad_app.JobStatus.COMPLETED,
        progress_percent=100,
        completed_at=None,
        error_message=None,
    )
    captured = {}

    async def fake_commit_job(_job):
        return None

    monkeypatch.setattr(launchpad_app, "sessionless_commit_job", fake_commit_job)
    monkeypatch.setattr(
        launchpad_app,
        "infer_local_workflow_metadata",
        lambda _path: WorkflowImportMetadata(
            workflow_key="wf_pore_c",
            sample_name="POREC_A",
            mode=None,
            reference_genome=["GRCh38"],
            modifications=None,
            config_path=str(source_dir / ".agoutic.workflow.json"),
            source_complete=True,
            input_directory="/input/fastq",
        ),
    )

    def fake_copy_local_results(source: Path, destination: Path, *, full_copy: bool, workflow_key: str | None):
        captured.update(
            {
                "source": source,
                "destination": destination,
                "full_copy": full_copy,
                "workflow_key": workflow_key,
            }
        )

    monkeypatch.setattr(launchpad_app, "copy_local_results_to_workflow", fake_copy_local_results)

    response = await launchpad_app._sync_imported_or_remote_job(job, force=True)

    assert response["success"] is True
    assert captured == {
        "source": source_dir,
        "destination": destination_dir,
        "full_copy": False,
        "workflow_key": "wf_pore_c",
    }
