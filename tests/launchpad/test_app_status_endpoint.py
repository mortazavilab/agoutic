from types import SimpleNamespace
import json

import pytest

import launchpad.app as launchpad_app


class _FakeSession:
    async def close(self):
        return None


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
    assert payload["tasks"] == {}


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
    assert any("Collecting structures and assigning IDs" in message for message in messages)
    assert any("Initially processed 12 unique transcript structures." in message for message in messages)
    assert any("[WARN] example warning" in message for message in messages)
    assert "script-stdout-live" in sources
    assert "script-stderr-live" in sources