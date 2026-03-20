from types import SimpleNamespace

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