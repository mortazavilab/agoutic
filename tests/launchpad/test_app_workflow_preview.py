from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import launchpad.app as launchpad_app
from launchpad import config as launchpad_config
from cortex.models import SystemSetting
from launchpad.schemas import SubmitJobRequest, WorkflowPreviewRequest


class _FakeSession:
    def __init__(self):
        self._settings = {
            "maintenance_mode": SystemSetting(key="maintenance_mode", value="false"),
            "maintenance_message": SystemSetting(key="maintenance_message", value=""),
            "maintenance_starts_at": SystemSetting(key="maintenance_starts_at", value=""),
        }

    async def execute(self, statement):
        descriptions = list(getattr(statement, "column_descriptions", []) or [])
        if descriptions and descriptions[0].get("entity") is SystemSetting:
            return _ExecuteResult(list(self._settings.values()))
        return _ExecuteResult([])

    def add(self, row):
        if isinstance(row, SystemSetting):
            self._settings[row.key] = row

    def add_all(self, rows):
        for row in rows:
            self.add(row)

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def close(self):
        return None


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


@pytest.mark.asyncio
async def test_preview_workflow_returns_wf_pore_c_preview(monkeypatch):
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", False)

    result = await launchpad_app.preview_workflow(
        WorkflowPreviewRequest(
            workflow_key="wf_pore_c",
            sample_name="POREC_A",
            input_type="bam",
            input_path="/data/pore-c.concatemers.bam",
            reference_fasta="/refs/reference.fa",
            output_directory="/projects/demo/workflow4",
        )
    )

    assert result.workflow_key == "wf_pore_c"
    assert result.supports_submission is False
    assert "epi2me-labs/wf-pore-c" in result.command
    assert result.preview_payload["output_flags"]["pairs"] is True
    assert result.preview_payload["output_flags"]["mcool"] is True


@pytest.mark.asyncio
async def test_preview_workflow_reports_submission_enabled_when_flag_on(monkeypatch):
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)

    result = await launchpad_app.preview_workflow(
        WorkflowPreviewRequest(
            workflow_key="wf_pore_c",
            sample_name="POREC_A",
            input_type="bam",
            input_path="/data/pore-c.concatemers.bam",
            reference_fasta="/refs/reference.fa",
            output_directory="/projects/demo/workflow4",
        )
    )

    assert result.workflow_key == "wf_pore_c"
    assert result.supports_submission is True


@pytest.mark.asyncio
async def test_preview_workflow_rejects_unknown_workflow_key():
    with pytest.raises(HTTPException) as exc_info:
        await launchpad_app.preview_workflow(WorkflowPreviewRequest(workflow_key="mystery_workflow"))

    assert exc_info.value.status_code == 400
    assert "Unknown workflow_key 'mystery_workflow'" in exc_info.value.detail


@pytest.mark.asyncio
async def test_submit_job_rejects_wf_pore_c_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", False)

    fake_session = _FakeSession()
    create_called = False

    async def fake_create_job(*_args, **_kwargs):
        nonlocal create_called
        create_called = True
        return SimpleNamespace()

    monkeypatch.setattr(launchpad_app, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(launchpad_app, "create_job", fake_create_job)

    req = SubmitJobRequest(
        project_id="proj-1",
        sample_name="POREC_A",
        workflow_key="wf_pore_c",
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