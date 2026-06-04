from datetime import datetime, timezone
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import launchpad.app as launchpad_app


class _FakeSession:
    async def commit(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_startup_schedules_background_staging_recovery(monkeypatch):
    cleanup_sentinel = object()
    recovery_sentinel = object()
    mock_create_task = MagicMock()

    monkeypatch.setattr("common.database.is_sqlite", lambda: False)
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.periodic_cleanup",
        lambda: cleanup_sentinel,
    )
    monkeypatch.setattr(
        launchpad_app,
        "_recover_staging_tasks_background",
        lambda: recovery_sentinel,
    )
    monkeypatch.setattr(launchpad_app.asyncio, "create_task", mock_create_task)

    await launchpad_app.startup()

    assert [call.args[0] for call in mock_create_task.call_args_list] == [
        cleanup_sentinel,
        recovery_sentinel,
    ]


@pytest.mark.asyncio
async def test_get_job_status_returns_terminal_slurm_db_state_without_repoll(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-1"
        return SimpleNamespace(
            run_uuid="run-1",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
            progress_percent=0,
            error_message=None,
            run_stage="completed",
            slurm_job_id="50052939",
            slurm_state="COMPLETED",
            transfer_state="outputs_downloaded",
            result_destination="local",
            nextflow_work_dir="/local/project/workflow1",
            remote_work_dir="/remote/project/workflow1",
            workflow_usage_json={"source": "slurm_sacct+nextflow_trace", "cpu_seconds": 120.0},
            workflow_usage_synced_at=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    def fail_get_backend(mode):
        raise AssertionError(f"backend polling should be skipped for terminal SLURM jobs, got {mode}")

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", fail_get_backend)

    payload = await launchpad_app.get_job_status("run-1")

    assert payload["status"] == launchpad_app.JobStatus.COMPLETED
    assert payload["progress_percent"] == 100
    assert payload["execution_mode"] == "slurm"
    assert payload["slurm_job_id"] == "50052939"
    assert payload["slurm_state"] == "COMPLETED"
    assert payload["work_directory"] == "/local/project/workflow1"
    assert payload["workflow_usage"]["cpu_seconds"] == 120.0
    assert payload["tasks"] == {}


@pytest.mark.asyncio
async def test_get_job_status_returns_stale_db_state_without_repoll(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-stale"
        return SimpleNamespace(
            run_uuid="run-stale",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.STALE,
            progress_percent=0,
            error_message="Marked stale by maintenance cleanup.",
            run_stage="completed",
            slurm_job_id="50052940",
            slurm_state="UNKNOWN",
            transfer_state=None,
            result_destination="local",
            nextflow_work_dir="/local/project/workflow9",
            remote_work_dir="/remote/project/workflow9",
            workflow_usage_json=None,
            workflow_usage_synced_at=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    def fail_get_backend(mode):
        raise AssertionError(f"backend polling should be skipped for stale jobs, got {mode}")

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", fail_get_backend)

    payload = await launchpad_app.get_job_status("run-stale")

    assert payload["status"] == launchpad_app.JobStatus.STALE
    assert payload["progress_percent"] == 0
    assert payload["message"] == "Marked stale by maintenance cleanup."
    assert payload["execution_mode"] == "slurm"
    assert payload["work_directory"] == "/local/project/workflow9"


@pytest.mark.asyncio
async def test_get_job_status_reports_sync_completion_message(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-sync-complete"
        return SimpleNamespace(
            run_uuid="run-sync-complete",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
            progress_percent=100,
            error_message=None,
            run_stage="completed",
            slurm_job_id="50052941",
            slurm_state="COMPLETED",
            transfer_state="outputs_downloaded",
            result_destination="local",
            nextflow_work_dir="/local/project/workflow10",
            remote_work_dir="/remote/project/workflow10",
            workflow_usage_json=None,
            workflow_usage_synced_at=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
            imported_source_complete=None,
        )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "_backfill_terminal_slurm_workflow_usage", AsyncMock())

    payload = await launchpad_app.get_job_status("run-sync-complete")

    assert payload["status"] == launchpad_app.JobStatus.COMPLETED
    assert payload["message"] == "Results synchronized to the local workflow directory."


@pytest.mark.asyncio
async def test_get_job_status_reports_stale_transfer_message_without_repoll(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-transfer-stale"
        return SimpleNamespace(
            run_uuid="run-transfer-stale",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
            progress_percent=100,
            error_message=None,
            run_stage="completed",
            slurm_job_id="50052942",
            slurm_state="COMPLETED",
            transfer_state="stale",
            result_destination="local",
            nextflow_work_dir="/local/project/workflow11",
            remote_work_dir="/remote/project/workflow11",
            workflow_usage_json=None,
            workflow_usage_synced_at=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
            imported_source_complete=None,
        )

    def fail_get_backend(mode):
        raise AssertionError(f"backend polling should be skipped for stale transfers, got {mode}")

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", fail_get_backend)
    monkeypatch.setattr(launchpad_app, "_backfill_terminal_slurm_workflow_usage", AsyncMock())

    payload = await launchpad_app.get_job_status("run-transfer-stale")

    assert payload["status"] == launchpad_app.JobStatus.COMPLETED
    assert payload["transfer_state"] == "stale"
    assert payload["message"] == "Result transfer marked stale by maintenance cleanup. Run sync again to retry copy-back."


@pytest.mark.asyncio
async def test_get_job_status_reports_cleaning_remote_message_without_repoll(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-cleaning-remote"
        return SimpleNamespace(
            run_uuid="run-cleaning-remote",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.FAILED,
            progress_percent=0,
            error_message="old rsync failure",
            run_stage="CLEANING_REMOTE",
            slurm_job_id="50052943",
            slurm_state="FAILED",
            transfer_state="transfer_failed",
            result_destination="local",
            nextflow_work_dir="/local/project/workflow12",
            remote_work_dir="/remote/project/workflow12",
            submitted_at=None,
            started_at=None,
            completed_at=None,
            imported_source_complete=None,
        )

    def fail_get_backend(mode):
        raise AssertionError(f"backend polling should be skipped for clean status, got {mode}")

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", fail_get_backend)

    payload = await launchpad_app.get_job_status("run-cleaning-remote")

    assert payload["status"] == "RUNNING"
    assert payload["run_stage"] == "CLEANING_REMOTE"
    assert payload["message"] == "Cleaning remote workflow artifacts in the background."


@pytest.mark.asyncio
async def test_get_job_status_reports_cleaned_local_message_without_old_error(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-cleaned-local"
        return SimpleNamespace(
            run_uuid="run-cleaned-local",
            execution_mode="local",
            status=launchpad_app.JobStatus.FAILED,
            progress_percent=0,
            error_message="Job failed — exit code 1",
            run_stage="CLEANED_LOCAL",
            slurm_job_id=None,
            slurm_state=None,
            transfer_state=None,
            result_destination=None,
            nextflow_work_dir="/local/project/workflow13",
            remote_work_dir=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
            imported_source_complete=None,
        )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)

    payload = await launchpad_app.get_job_status("run-cleaned-local")

    assert payload["status"] == "COMPLETED"
    assert payload["run_stage"] == "CLEANED_LOCAL"
    assert payload["message"] == "Local workflow cleanup completed."


@pytest.mark.asyncio
async def test_get_job_clean_status_returns_mode_specific_payload(monkeypatch):
    launchpad_app._workflow_clean_status.clear()
    launchpad_app._workflow_clean_status[("run-clean-status", True)] = {
        "run_uuid": "run-clean-status",
        "remote": True,
        "status": "RUNNING",
        "run_stage": "CLEANING_REMOTE",
        "message": "Cleaning remote workflow artifacts in the background.",
        "updated_at": "2026-06-02T00:00:00+00:00",
    }

    payload = await launchpad_app.get_job_clean_status("run-clean-status", remote=True)

    assert payload["run_uuid"] == "run-clean-status"
    assert payload["remote"] is True
    assert payload["run_stage"] == "CLEANING_REMOTE"


@pytest.mark.asyncio
async def test_get_job_clean_status_recovers_completed_remote_status_from_job(monkeypatch):
    fake_session = _FakeSession()
    launchpad_app._workflow_clean_status.clear()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-cleaned-remote"
        return SimpleNamespace(
            run_uuid="run-cleaned-remote",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.FAILED,
            progress_percent=0,
            error_message="old failure text should not leak",
            run_stage="CLEANED_REMOTE",
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)

    payload = await launchpad_app.get_job_clean_status("run-cleaned-remote", remote=True)

    assert payload["run_uuid"] == "run-cleaned-remote"
    assert payload["remote"] is True
    assert payload["status"] == "COMPLETED"
    assert payload["run_stage"] == "CLEANED_REMOTE"
    assert payload["message"] == "Remote workflow cleanup completed."


@pytest.mark.asyncio
async def test_get_job_status_repolls_terminal_failed_slurm_job_for_task_summary(monkeypatch):
    fake_session = _FakeSession()
    synced_at = datetime(2026, 5, 25, 5, 12, 12, tzinfo=timezone.utc)
    job = SimpleNamespace(
        run_uuid="run-failed-terminal",
        execution_mode="slurm",
        status=launchpad_app.JobStatus.FAILED,
        progress_percent=0,
        error_message="Job failed — exit code 1:0; Failure reason: None — Pipeline: 0/48 completed, 48 remaining",
        run_stage="failed",
        slurm_job_id="50052999",
        slurm_state="FAILED",
        transfer_state=None,
        result_destination="local",
        nextflow_work_dir=None,
        remote_work_dir="/remote/project/workflow1",
        workflow_usage_json=None,
        workflow_usage_synced_at=None,
        submitted_at=None,
        started_at=None,
        completed_at=None,
    )

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-failed-terminal"
        return job

    fake_backend = SimpleNamespace(
        check_status=AsyncMock(
            return_value=SimpleNamespace(
                run_uuid="run-failed-terminal",
                status="FAILED",
                progress_percent=99,
                message="Job failed — exit code 1:0; Failure reason: None — Pipeline: 742/744 completed, 1 remaining, 1 failed",
                tasks={
                    "total": 744,
                    "completed_count": 742,
                    "failed_count": 1,
                    "remaining_count": 1,
                },
                execution_mode="slurm",
                run_stage="failed",
                slurm_job_id="50052999",
                slurm_state="FAILED",
                transfer_state=None,
                transfer_detail=None,
                result_destination="local",
                ssh_profile_nickname="hpc3",
                work_directory="/remote/project/workflow1",
                workflow_usage={"source": "slurm_sacct+nextflow_trace", "cpu_seconds": 120.0},
                workflow_usage_synced_at=synced_at.isoformat(),
            )
        )
    )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: fake_backend)

    payload = await launchpad_app.get_job_status("run-failed-terminal")

    fake_backend.check_status.assert_awaited_once_with("run-failed-terminal")
    assert payload["status"] == "FAILED"
    assert payload["progress_percent"] == 99
    assert payload["tasks"]["completed_count"] == 742
    assert payload["tasks"]["failed_count"] == 1
    assert payload["tasks"]["total"] == 744
    assert "742/744 completed" in payload["message"]
    assert payload["workflow_usage"]["cpu_seconds"] == 120.0
    assert payload["workflow_usage_synced_at"] == synced_at.replace(tzinfo=None).isoformat()
    assert job.workflow_usage_json["cpu_seconds"] == 120.0
    assert job.workflow_usage_synced_at == synced_at.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_get_job_status_backfills_terminal_slurm_workflow_usage_from_local_trace(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    workflow_dir = tmp_path / "workflow-trace"
    workflow_dir.mkdir()
    (workflow_dir / "trace.txt").write_text(
        "task_id\thash\tnative_id\tname\tstatus\trealtime\t%cpu\tpeak_rss\tpeak_vmem\texit\n"
        "1\taa/bb\t50060001\tmainWorkflow:doradoTask (1)\tCOMPLETED\t90s\t200%\t4G\t6G\t0\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-old-trace"
        return SimpleNamespace(
            run_uuid="run-old-trace",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
            progress_percent=100,
            error_message=None,
            run_stage="completed",
            slurm_job_id="50060000",
            slurm_state="COMPLETED",
            transfer_state="outputs_downloaded",
            result_destination="local",
            nextflow_work_dir=str(workflow_dir),
            remote_work_dir="/remote/project/workflow-trace",
            workflow_usage_json=None,
            workflow_usage_synced_at=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    def fail_get_backend(mode):
        raise AssertionError(f"terminal backfill should use local trace only, got {mode}")

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", fail_get_backend)

    payload = await launchpad_app.get_job_status("run-old-trace")

    assert payload["status"] == launchpad_app.JobStatus.COMPLETED
    assert payload["workflow_usage"]["accounting_mode"] == "slurm"
    assert payload["workflow_usage"]["usage_status"] == "partial"
    assert payload["workflow_usage"]["usage_message"] == "Using Nextflow trace only; scheduler accounting unavailable."
    assert payload["workflow_usage"]["cpu_seconds"] == 180.0
    assert payload["workflow_usage_synced_at"] is not None


@pytest.mark.asyncio
async def test_get_job_status_returns_usage_unavailable_for_terminal_slurm_job_without_trace(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-old-missing"
        return SimpleNamespace(
            run_uuid="run-old-missing",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
            progress_percent=100,
            error_message=None,
            run_stage="completed",
            slurm_job_id="50060010",
            slurm_state="COMPLETED",
            transfer_state="outputs_downloaded",
            result_destination="local",
            nextflow_work_dir="/local/project/missing-workflow",
            remote_work_dir="/remote/project/missing-workflow",
            workflow_usage_json=None,
            workflow_usage_synced_at=None,
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    def fail_get_backend(mode):
        raise AssertionError(f"terminal fallback should not repoll backend, got {mode}")

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", fail_get_backend)

    payload = await launchpad_app.get_job_status("run-old-missing")

    assert payload["status"] == launchpad_app.JobStatus.COMPLETED
    assert payload["workflow_usage"]["usage_status"] == "unavailable"
    assert payload["workflow_usage"]["usage_message"] == "Usage statistics not available."


@pytest.mark.asyncio
async def test_get_job_status_returns_and_persists_slurm_workflow_usage(monkeypatch):
    fake_session = _FakeSession()
    synced_at = datetime(2026, 5, 24, 20, 0, 0, tzinfo=timezone.utc)
    job = SimpleNamespace(
        run_uuid="run-slurm-live",
        execution_mode="slurm",
        status=launchpad_app.JobStatus.RUNNING,
        progress_percent=15,
        error_message=None,
        run_stage="running",
        slurm_job_id="50070000",
        slurm_state="RUNNING",
        transfer_state=None,
        result_destination="local",
        nextflow_work_dir="/local/project/workflow-live",
        remote_work_dir="/remote/project/workflow-live",
        workflow_usage_json=None,
        workflow_usage_synced_at=None,
        submitted_at=None,
        started_at=None,
        completed_at=None,
    )

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-slurm-live"
        return job

    class _FakeBackend:
        async def check_status(self, run_uuid):
            assert run_uuid == "run-slurm-live"
            return SimpleNamespace(
                status=launchpad_app.JobStatus.RUNNING,
                progress_percent=44,
                message="Pipeline: 2/5 completed, 1 running",
                tasks={"completed_count": 2, "total": 5},
                execution_mode="slurm",
                run_stage="running",
                slurm_job_id="50070000",
                slurm_state="RUNNING",
                transfer_state=None,
                transfer_detail=None,
                result_destination="local",
                ssh_profile_nickname="hpc3",
                work_directory="/local/project/workflow-live",
                workflow_usage_synced_at=synced_at.isoformat(),
                workflow_usage={
                    "source": "slurm_sacct+nextflow_trace",
                    "accounting_mode": "slurm",
                    "cpu_seconds": 123.5,
                    "gpu_seconds": 40.0,
                    "billing_units": 2.5,
                    "billing_entries": [
                        {
                            "resource_type": "CPU",
                            "account": "cpu-default",
                            "billing_hours": 1.0,
                        },
                        {
                            "resource_type": "GPU",
                            "account": "gpu-default",
                            "billing_hours": 1.5,
                        },
                    ],
                    "billing_hours_by_account": {
                        "cpu-default": 1.0,
                        "gpu-default": 1.5,
                    },
                },
            )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: _FakeBackend())

    payload = await launchpad_app.get_job_status("run-slurm-live")

    assert payload["status"] == launchpad_app.JobStatus.RUNNING
    assert payload["progress_percent"] == 44
    assert payload["workflow_usage"]["cpu_seconds"] == 123.5
    assert payload["workflow_usage"]["gpu_seconds"] == 40.0
    assert payload["workflow_usage"]["billing_entries"][1]["resource_type"] == "GPU"
    assert payload["workflow_usage"]["billing_hours_by_account"]["gpu-default"] == 1.5
    assert payload["workflow_usage_synced_at"] == synced_at.replace(tzinfo=None).isoformat()
    assert job.workflow_usage_json["billing_units"] == 2.5
    assert job.workflow_usage_json["billing_hours_by_account"]["cpu-default"] == 1.0
    assert job.workflow_usage_synced_at == synced_at.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_get_staging_task_status_falls_back_to_persisted_row(monkeypatch):
    monkeypatch.setattr("launchpad.backends.staging_worker.get_staging_tasks", lambda: {})
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.hydrate_incomplete_staging_tasks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        launchpad_app,
        "get_staging_task_record",
        AsyncMock(
            return_value=SimpleNamespace(
                task_id="stg-1",
                status="running",
                progress_json={"file_percent": 42},
                result_json=None,
                error=None,
                created_at=100.0,
                updated_at=105.0,
                params_json={"sample_name": "sample-a"},
            )
        ),
    )

    payload = await launchpad_app.get_staging_task_status("stg-1")

    assert payload.task_id == "stg-1"
    assert payload.status == "running"
    assert payload.progress["file_percent"] == 42


@pytest.mark.asyncio
async def test_get_staging_task_status_preserves_wf_pore_c_result_metadata(monkeypatch):
    monkeypatch.setattr("launchpad.backends.staging_worker.get_staging_tasks", lambda: {})
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.hydrate_incomplete_staging_tasks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        launchpad_app,
        "get_staging_task_record",
        AsyncMock(
            return_value=SimpleNamespace(
                task_id="stg-wf-pore-c",
                status="completed",
                progress_json={"file_percent": 100},
                result_json={
                    "workflow_key": "wf_pore_c",
                    "reference_fasta_remote_path": "/remote/base/ref/wf-pore-c/abc123/reference.fa",
                    "workflow_remote_input": "/remote/base/proj/workflow1/.agoutic/wf-pore-c/staged-inputs/input/sample.bam",
                },
                error=None,
                created_at=100.0,
                updated_at=105.0,
                params_json={"sample_name": "POREC_A"},
            )
        ),
    )

    payload = await launchpad_app.get_staging_task_status("stg-wf-pore-c")

    assert payload.status == "completed"
    assert payload.result["workflow_key"] == "wf_pore_c"
    assert payload.result["reference_fasta_remote_path"].endswith("reference.fa")


@pytest.mark.asyncio
async def test_get_staging_task_status_attempts_resume_for_queued_task(monkeypatch):
    queued_task = SimpleNamespace(
        task_id="stg-queued",
        status="queued",
        progress={"wait_reason": "Waiting for the Remote Profile unlock to be re-established after restart."},
        result=None,
        error=None,
        created_at=100.0,
        updated_at=105.0,
        params={"ssh_profile_id": "profile-1"},
        _task=None,
        to_dict=lambda: {
            "task_id": "stg-queued",
            "status": queued_task.status,
            "progress": dict(queued_task.progress),
            "result": queued_task.result,
            "error": queued_task.error,
            "created_at": queued_task.created_at,
            "updated_at": queued_task.updated_at,
        },
    )
    tasks = {"stg-queued": queued_task}

    async def _fake_resume(profile_id=None):
        assert profile_id == "profile-1"
        queued_task.status = "running"
        queued_task.progress = {"file_percent": 12}
        return 1

    monkeypatch.setattr("launchpad.backends.staging_worker.get_staging_tasks", lambda: tasks)
    monkeypatch.setattr("launchpad.backends.staging_worker.resume_queued_staging_tasks", _fake_resume)

    payload = await launchpad_app.get_staging_task_status("stg-queued")

    assert payload.status == "running"
    assert payload.progress["file_percent"] == 12


@pytest.mark.asyncio
async def test_get_staging_task_status_hydrates_persisted_incomplete_task_before_resuming(monkeypatch):
    tasks: dict[str, object] = {}

    def _task_to_dict(task):
        return {
            "task_id": task.task_id,
            "status": task.status,
            "progress": dict(task.progress),
            "result": task.result,
            "error": task.error,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    async def _fake_hydrate(task_id=None, profile_id=None):
        assert task_id == "stg-db"
        task = SimpleNamespace(
            task_id="stg-db",
            status="queued",
            progress={"wait_reason": "waiting"},
            result=None,
            error=None,
            created_at=100.0,
            updated_at=105.0,
            params={"ssh_profile_id": "profile-1"},
            _task=None,
        )
        task.to_dict = lambda task=task: _task_to_dict(task)
        tasks[task.task_id] = task
        return [task]

    async def _fake_resume(profile_id=None):
        assert profile_id == "profile-1"
        task = tasks["stg-db"]
        task.status = "running"
        task.progress = {"file_percent": 9}
        return 1

    monkeypatch.setattr("launchpad.backends.staging_worker.get_staging_tasks", lambda: tasks)
    monkeypatch.setattr("launchpad.backends.staging_worker.hydrate_incomplete_staging_tasks", _fake_hydrate)
    monkeypatch.setattr("launchpad.backends.staging_worker.resume_queued_staging_tasks", _fake_resume)
    monkeypatch.setattr(launchpad_app, "get_staging_task_record", AsyncMock(return_value=None))

    payload = await launchpad_app.get_staging_task_status("stg-db")

    assert payload.task_id == "stg-db"
    assert payload.status == "running"
    assert payload.progress["file_percent"] == 9


@pytest.mark.asyncio
async def test_retry_staging_task_uses_persisted_failed_row(monkeypatch):
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.requeue_staging_task",
        AsyncMock(return_value=SimpleNamespace(task_id="stg-new", status="queued")),
    )

    response = await launchpad_app.retry_staging_task("stg-old")

    assert response.task_id == "stg-new"
    assert response.status == "queued"


@pytest.mark.asyncio
async def test_cancel_staging_task_endpoint_returns_cancelled_response(monkeypatch):
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.cancel_staging_task",
        AsyncMock(
            return_value=SimpleNamespace(
                task_id="stg-1",
                status="cancelled",
                error="Remote staging cancelled by user.",
            )
        ),
    )

    payload = await launchpad_app.cancel_staging_task_endpoint("stg-1")

    assert payload.task_id == "stg-1"
    assert payload.status == "cancelled"
    assert payload.message == "Remote staging cancelled by user."


@pytest.mark.asyncio
async def test_cancel_staging_task_endpoint_maps_terminal_conflict(monkeypatch):
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.cancel_staging_task",
        AsyncMock(side_effect=ValueError("Cannot cancel staging task with status completed")),
    )

    with pytest.raises(launchpad_app.HTTPException) as exc_info:
        await launchpad_app.cancel_staging_task_endpoint("stg-done")

    assert exc_info.value.status_code == 409
    assert "completed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_resume_staging_task_endpoint_returns_replacement_task(monkeypatch):
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.resume_staging_task",
        AsyncMock(return_value=SimpleNamespace(task_id="stg-2", status="queued")),
    )

    payload = await launchpad_app.resume_staging_task_endpoint("stg-1")

    assert payload.task_id == "stg-2"
    assert payload.status == "queued"
    assert payload.resumed_from_task_id == "stg-1"


@pytest.mark.asyncio
async def test_resume_staging_task_endpoint_maps_capacity_limit(monkeypatch):
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.resume_staging_task",
        AsyncMock(side_effect=RuntimeError("Too many concurrent staging tasks")),
    )

    with pytest.raises(launchpad_app.HTTPException) as exc_info:
        await launchpad_app.resume_staging_task_endpoint("stg-1")

    assert exc_info.value.status_code == 429
    assert "Too many concurrent staging tasks" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_create_ssh_profile_auth_session_resumes_queued_staging_for_profile(monkeypatch):
    profile = SimpleNamespace(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="key_file",
        key_file_path="~/.ssh/id_ed25519",
        local_username="alice",
        is_enabled=True,
    )

    class _FakeExecuteResult:
        def scalar_one_or_none(self):
            return profile

    class _FakeAsyncSessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, query):
            return _FakeExecuteResult()

    class _FakeAuthManager:
        async def create_or_replace_session(self, profile_data, local_password):
            assert profile_data.id == "profile-1"
            assert local_password == "secret"
            return SimpleNamespace(session_id="sess-1")

        def _serialize_session(self, session_obj):
            return {
                "active": True,
                "session_id": session_obj.session_id,
                "local_username": "alice",
                "created_at": "2026-04-11T00:00:00+00:00",
                "last_used_at": "2026-04-11T00:00:00+00:00",
                "expires_at": "2026-04-11T01:00:00+00:00",
            }

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: _FakeAsyncSessionContext())
    monkeypatch.setattr(launchpad_app, "get_local_auth_session_manager", lambda: _FakeAuthManager())
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.hydrate_incomplete_staging_tasks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "launchpad.backends.staging_worker.resume_queued_staging_tasks",
        AsyncMock(return_value=1),
    )

    result = await launchpad_app.create_ssh_profile_auth_session(
        profile_id="profile-1",
        user_id="user-1",
        local_password="secret",
    )

    assert result.active is True
    assert result.message == "Local auth session started; resumed 1 queued staging task(s)"


@pytest.mark.asyncio
async def test_get_job_status_repolls_completed_slurm_job_while_local_copyback_pending(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        return SimpleNamespace(
            run_uuid="run-2",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
            progress_percent=100,
            error_message=None,
            run_stage="completed",
            slurm_job_id="50052940",
            slurm_state="COMPLETED",
            transfer_state="downloading_outputs",
            result_destination="local",
            nextflow_work_dir="/local/project/workflow2",
            remote_work_dir="/remote/project/workflow2",
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    class _FakeBackend:
        async def check_status(self, run_uuid):
            assert run_uuid == "run-2"
            return SimpleNamespace(
                status="RUNNING",
                progress_percent=95,
                message="Copying results back to the local workflow...",
                tasks={},
                execution_mode="slurm",
                run_stage="completed",
                slurm_job_id="50052940",
                slurm_state="COMPLETED",
                transfer_state="downloading_outputs",
                transfer_detail="bams/sample1.bam — 1/5 files — 12.3MB/s",
                result_destination="local",
                ssh_profile_nickname="hpc3",
                work_directory="/local/project/workflow2",
            )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: _FakeBackend())

    payload = await launchpad_app.get_job_status("run-2")

    assert payload["status"] == "RUNNING"
    assert payload["transfer_state"] == "downloading_outputs"
    assert payload["message"] == "Copying results back to the local workflow..."
    assert payload["work_directory"] == "/local/project/workflow2"


@pytest.mark.asyncio
async def test_get_job_status_reports_transfer_failure_detail_for_completed_slurm_job(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        return SimpleNamespace(
            run_uuid="run-fail",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
            progress_percent=100,
            error_message="No result artifacts found on remote at /remote/workflow5/output.",
            run_stage="completed",
            slurm_job_id="50052941",
            slurm_state="COMPLETED",
            transfer_state="transfer_failed",
            result_destination="local",
            nextflow_work_dir="/local/project/workflow5",
            remote_work_dir="/remote/project/workflow5",
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    class _FakeBackend:
        def get_transfer_detail(self, run_uuid):
            assert run_uuid == "run-fail"
            return None

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: _FakeBackend())

    payload = await launchpad_app.get_job_status("run-fail")

    assert payload["status"] == "FAILED"
    assert payload["transfer_state"] == "transfer_failed"
    assert payload["transfer_detail"] == "No result artifacts found on remote at /remote/workflow5/output."
    assert "No result artifacts found" in payload["message"]


@pytest.mark.asyncio
async def test_get_job_status_reports_cancelled_sync_for_slurm_job(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        return SimpleNamespace(
            run_uuid="run-cancel",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.CANCELLED,
            progress_percent=100,
            error_message="Result synchronization cancelled. Run sync again to resume copying outputs.",
            run_stage="completed",
            slurm_job_id="50052942",
            slurm_state="COMPLETED",
            transfer_state="sync_cancelled",
            result_destination="local",
            nextflow_work_dir="/local/project/workflow6",
            remote_work_dir="/remote/project/workflow6",
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    def fail_get_backend(mode):
        raise AssertionError(f"backend polling should be skipped for cancelled sync jobs, got {mode}")

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", fail_get_backend)

    payload = await launchpad_app.get_job_status("run-cancel")

    assert payload["status"] == launchpad_app.JobStatus.CANCELLED
    assert payload["transfer_state"] == "sync_cancelled"
    assert "Result synchronization cancelled" in payload["message"]


@pytest.mark.asyncio
async def test_cancel_job_dispatches_to_active_result_sync(monkeypatch):
    fake_session = _FakeSession()
    add_log_entry = AsyncMock()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        return SimpleNamespace(
            run_uuid="run-sync",
            execution_mode="slurm",
            status=launchpad_app.JobStatus.COMPLETED,
        )

    class _FakeBackend:
        def is_result_sync_active(self, run_uuid):
            assert run_uuid == "run-sync"
            return True

        async def cancel_result_sync(self, *, run_uuid):
            assert run_uuid == "run-sync"
            return {
                "success": True,
                "status": "sync_cancelled",
                "message": "Result synchronization cancelled. Run sync again to resume copying outputs.",
                "run_uuid": run_uuid,
                "transfer_state": "sync_cancelled",
            }

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_backend", lambda mode: _FakeBackend())
    monkeypatch.setattr(launchpad_app, "add_log_entry", add_log_entry)

    payload = await launchpad_app.cancel_job("run-sync")

    assert payload["status"] == "sync_cancelled"
    add_log_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_job_terminates_script_process_group(monkeypatch):
    fake_session = _FakeSession()
    add_log_entry = AsyncMock()
    update_status = AsyncMock()
    script_process = SimpleNamespace(pid=4321)
    terminate_calls = []

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "add_log_entry", add_log_entry)
    monkeypatch.setattr(launchpad_app, "update_job_status", update_status)
    monkeypatch.setattr(launchpad_app, "script_processes", {"run-script": script_process})
    monkeypatch.setattr(
        launchpad_app,
        "terminate_script_process_tree",
        lambda **kwargs: terminate_calls.append(kwargs) or True,
    )

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-script"
        return SimpleNamespace(
            run_uuid="run-script",
            execution_mode="local",
            status=launchpad_app.JobStatus.RUNNING,
            nextflow_process_id=4321,
            nextflow_work_dir=None,
            report_json=json.dumps({"run_type": "script", "script_id": "reconcile_bams/reconcile_bams"}),
            run_stage="SCRIPT_RUNNING",
        )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)

    payload = await launchpad_app.cancel_job("run-script")

    assert payload["status"] == "cancelled"
    assert payload["process_killed"] is True
    assert "process group" in payload["message"]
    assert terminate_calls == [{"process": script_process, "pid": 4321, "sig": launchpad_app.signal.SIGTERM}]
    update_status.assert_awaited_once_with(fake_session, "run-script", launchpad_app.JobStatus.CANCELLED)
    add_log_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_job_status_reports_live_reconcile_step_for_running_script(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    stdout_log = tmp_path / "run-3.stdout.log"
    stderr_log = tmp_path / "run-3.stderr.log"
    stdout_log.write_text(
        "reconcileBams.py version 1.1.5\n"
        "=== Step 2: Rewriting BAM files (using 4 threads)... ===\n"
        "  - Finished rewriting sample1.GRCh38.annotated.bam: changed IDs for 10 / 10 reads. Mapping file created.\n",
        encoding="utf-8",
    )
    stderr_log.write_text("", encoding="utf-8")

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-3"
        return SimpleNamespace(
            run_uuid="run-3",
            execution_mode="local",
            status=launchpad_app.JobStatus.RUNNING,
            progress_percent=0,
            error_message=None,
            run_stage="SCRIPT_RUNNING",
            nextflow_work_dir="/local/project/workflow3",
            log_file=str(stdout_log),
            stderr_log=str(stderr_log),
            report_json=json.dumps(
                {
                    "run_type": "script",
                    "script_id": "reconcile_bams/reconcile_bams",
                    "script_path": "/tmp/reconcile_bams.py",
                }
            ),
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)

    payload = await launchpad_app.get_job_status("run-3")

    assert payload["status"] == launchpad_app.JobStatus.RUNNING
    assert payload["progress_percent"] == 82
    assert payload["current_step"] == "Rewriting BAM files"
    assert "Finished rewriting sample1.GRCh38.annotated.bam" in payload["current_step_detail"]
    assert "Finished rewriting sample1.GRCh38.annotated.bam" in payload["message"]


@pytest.mark.asyncio
async def test_get_job_status_returns_and_persists_local_workflow_usage(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    workflow_dir = tmp_path / "workflow1"
    workflow_dir.mkdir()
    synced_at = datetime(2026, 5, 25, 14, 30, 0, tzinfo=timezone.utc)
    job = SimpleNamespace(
        run_uuid="run-local",
        execution_mode="local",
        status=launchpad_app.JobStatus.RUNNING,
        progress_percent=25,
        error_message=None,
        run_stage="running",
        nextflow_work_dir=str(workflow_dir),
        submitted_at=None,
        started_at=None,
        completed_at=None,
        workflow_usage_json=None,
        workflow_usage_synced_at=None,
    )

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-local"
        return job

    async def fake_check_status(run_uuid, work_dir):
        assert run_uuid == "run-local"
        assert str(work_dir) == str(workflow_dir)
        return {
            "status": launchpad_app.JobStatus.RUNNING,
            "progress_percent": 42,
            "message": "Pipeline: 2/5 completed, 1 running",
            "tasks": {"completed_count": 2, "total": 5, "failed_count": 0},
            "workflow_usage": {
                "source": "nextflow_trace",
                "accounting_mode": "local",
                "cpu_seconds": 123.5,
                "estimated_gpu_task_seconds": 40.0,
                "max_rss_mb": 2048.0,
            },
            "workflow_usage_synced_at": synced_at.isoformat(),
        }

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(
        launchpad_app,
        "NextflowExecutor",
        lambda: SimpleNamespace(work_dir=tmp_path, check_status=fake_check_status),
    )

    payload = await launchpad_app.get_job_status("run-local")

    assert payload["status"] == launchpad_app.JobStatus.RUNNING
    assert payload["progress_percent"] == 42
    assert payload["workflow_usage"]["cpu_seconds"] == 123.5
    assert payload["workflow_usage"]["estimated_gpu_task_seconds"] == 40.0
    assert payload["workflow_usage_synced_at"] == synced_at.replace(tzinfo=None).isoformat()
    assert job.workflow_usage_json["max_rss_mb"] == 2048.0
    assert job.workflow_usage_synced_at == synced_at.replace(tzinfo=None)


def test_parse_script_output_directory_prefers_workflow_directory_from_json_stdout():
    stdout_text = json.dumps(
        {
            "message": "Outputs are in: /local/project/workflow5\\n\",",
            "workflow": {
                "directory": "/local/project/workflow5",
                "output_directory": "/local/project/workflow5",
            },
        },
        indent=2,
    )

    assert launchpad_app._parse_script_output_directory(stdout_text) == "/local/project/workflow5"


@pytest.mark.asyncio
async def test_get_job_status_sanitizes_work_directory_for_running_script(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-5"
        return SimpleNamespace(
            run_uuid="run-5",
            execution_mode="local",
            status=launchpad_app.JobStatus.RUNNING,
            progress_percent=0,
            error_message=None,
            run_stage="SCRIPT_RUNNING",
            nextflow_work_dir="/local/project/workflow5\\n\",",
            log_file=None,
            stderr_log=None,
            report_json=json.dumps(
                {
                    "run_type": "script",
                    "script_id": "reconcile_bams/reconcile_bams",
                }
            ),
            submitted_at=None,
            started_at=None,
            completed_at=None,
        )

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)

    payload = await launchpad_app.get_job_status("run-5")

    assert payload["work_directory"] == "/local/project/workflow5"


@pytest.mark.asyncio
async def test_get_job_logs_returns_live_script_output_while_running(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    stdout_log = tmp_path / "run-4.stdout.log"
    stderr_log = tmp_path / "run-4.stderr.log"
    stdout_log.write_text(
        "=== Step 1: Collecting structures and assigning IDs (using 4 threads)... ===\n"
        "Initially processed 12 unique transcript structures.\n",
        encoding="utf-8",
    )
    stderr_log.write_text("[WARN] example warning\n", encoding="utf-8")

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)

    async def fake_get_job(session, run_uuid):
        assert session is fake_session
        assert run_uuid == "run-4"
        return SimpleNamespace(
            run_uuid="run-4",
            execution_mode="local",
            status=launchpad_app.JobStatus.RUNNING,
            run_stage="SCRIPT_RUNNING",
            log_file=str(stdout_log),
            stderr_log=str(stderr_log),
        )

    async def fake_get_job_logs(_session, _run_uuid, limit=100):
        assert limit == 10
        return []

    monkeypatch.setattr(launchpad_app, "get_job", fake_get_job)
    monkeypatch.setattr(launchpad_app, "get_job_logs", fake_get_job_logs)

    payload = await launchpad_app.get_job_logs_endpoint("run-4", limit=10)

    messages = [entry.message for entry in payload["logs"]]
    sources = [entry.source for entry in payload["logs"]]
    levels_by_message = {entry.message: entry.level for entry in payload["logs"]}
    assert any("Collecting structures and assigning IDs" in message for message in messages)
    assert any("Initially processed 12 unique transcript structures." in message for message in messages)
    assert any("[WARN] example warning" in message for message in messages)
    assert "script-stdout-live" in sources
    assert "script-stderr-live" in sources
    assert levels_by_message["[WARN] example warning"] == "WARN"


def test_classify_live_script_log_level_treats_generic_stderr_progress_as_info():
    assert launchpad_app._classify_live_script_log_level(
        "script-stderr-live",
        "=== Step 1: Collecting structures and assigning IDs (using 4 threads)... ===",
    ) == "INFO"


def test_classify_live_script_log_level_keeps_real_stderr_errors_red():
    assert launchpad_app._classify_live_script_log_level(
        "script-stderr-live",
        "Traceback (most recent call last):",
    ) == "ERROR"