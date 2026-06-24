from types import SimpleNamespace
from unittest.mock import AsyncMock
import sys
import types
from types import SimpleNamespace

import pytest


class _FakeEncoding:
    def encode(self, text: str):
        return list(text or "")


if "tiktoken" not in sys.modules:
    sys.modules["tiktoken"] = types.SimpleNamespace(get_encoding=lambda _name: _FakeEncoding(), Encoding=_FakeEncoding)

if "pandas" not in sys.modules:
    sys.modules["pandas"] = types.SimpleNamespace(DataFrame=object, Series=object, read_csv=lambda *_args, **_kwargs: None)

from cortex.workflow_commands import (
    WorkflowCommand,
    detect_workflow_intent,
    execute_manual_workflow_analysis,
    execute_use_workflow,
    execute_workflow_command,
    parse_workflow_command,
    resolve_workflow_reference,
)


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


class _FakeSession:
    def __init__(self, jobs):
        self._jobs = jobs
        self.created_blocks = []

    def execute(self, _query):
        return _FakeScalarResult(self._jobs)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, post_response=None, delete_response=None):
        self.post_response = post_response or _FakeResponse()
        self.delete_response = delete_response or _FakeResponse()
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None, params=None):
        self.calls.append(("POST", url, headers, json, params))
        return self.post_response

    async def delete(self, url, headers=None):
        self.calls.append(("DELETE", url, headers, None))
        return self.delete_response


class _CapturingAsyncClient(_FakeAsyncClient):
    def __init__(self, captured_timeouts, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._captured_timeouts = captured_timeouts

    async def __aenter__(self):
        return self


class TestParseWorkflowCommand:
    def test_parse_list_tracked_slash_command(self):
        cmd = parse_workflow_command("/list-launchpad-workflows")
        assert cmd == WorkflowCommand(action="list_tracked")

    def test_parse_rerun_slash_command(self):
        cmd = parse_workflow_command("/rerun workflow7")
        assert cmd == WorkflowCommand(action="rerun", workflow_ref="workflow7", workflow_refs=["workflow7"], new_name="")

    def test_parse_rerun_slash_command_without_target(self):
        cmd = parse_workflow_command("/rerun")
        assert cmd == WorkflowCommand(action="rerun")

    def test_parse_reanalyze_slash_command(self):
        cmd = parse_workflow_command("/reanalyze workflow7")
        assert cmd == WorkflowCommand(action="reanalyze", workflow_ref="workflow7", workflow_refs=["workflow7"], new_name="")

    def test_parse_analyze_slash_command_alias(self):
        cmd = parse_workflow_command("/analyze workflow7")
        assert cmd == WorkflowCommand(action="reanalyze", workflow_ref="workflow7", workflow_refs=["workflow7"], new_name="")

    def test_parse_reanalyze_slash_command_multiple_targets(self):
        cmd = parse_workflow_command("/reanalyze workflow7, workflow8 workflow9")
        assert cmd == WorkflowCommand(
            action="reanalyze",
            workflow_ref="workflow7",
            workflow_refs=["workflow7", "workflow8", "workflow9"],
        )

    def test_parse_summarize_slash_command_multiple_targets_with_focus(self):
        cmd = parse_workflow_command("/summarize workflow7, workflow8 -- mapping and QC")
        assert cmd == WorkflowCommand(
            action="summarize",
            workflow_ref="workflow7",
            workflow_refs=["workflow7", "workflow8"],
            focus_text="mapping and QC",
        )

    def test_parse_summarize_slash_command_without_targets(self):
        cmd = parse_workflow_command("/summarize")
        assert cmd == WorkflowCommand(action="summarize")

    def test_parse_rename_slash_command(self):
        cmd = parse_workflow_command("/rename workflow7 tumor-retry")
        assert cmd == WorkflowCommand(action="rename", workflow_ref="workflow7", new_name="tumor-retry")

    def test_parse_import_workflow_slash_command(self):
        cmd = parse_workflow_command(
            '/import-workflow "/tmp/workflow run" --remote --full-copy --sample-name tumor-a --mode DNA --reference GRCh38,mm39 --modifications 5mC'
        )
        assert cmd.action == "import"
        assert cmd.source_path == "/tmp/workflow run"
        assert cmd.source_kind == "slurm"
        assert cmd.full_copy is True
        assert cmd.sample_name == "tumor-a"
        assert cmd.mode == "DNA"
        assert cmd.reference_genome == ["GRCh38", "mm39"]
        assert cmd.modifications == "5mC"

    def test_parse_import_workflow_slash_command_with_profile(self):
        cmd = parse_workflow_command(
            "/import-workflow /dfs9/seyedam-lab/share/dogme_drna/ad015_25182 --remote --profile hpc3"
        )
        assert cmd.action == "import"
        assert cmd.source_path == "/dfs9/seyedam-lab/share/dogme_drna/ad015_25182"
        assert cmd.source_kind == "slurm"
        assert cmd.ssh_profile_nickname == "hpc3"

    def test_parse_sync_workflow_slash_command(self):
        cmd = parse_workflow_command("/sync-workflow workflow7 --force")
        assert cmd == WorkflowCommand(action="sync", workflow_ref="workflow7", workflow_refs=["workflow7"], force=True)

    def test_parse_cancel_sync_slash_command(self):
        cmd = parse_workflow_command("/cancel-sync workflow7")
        assert cmd == WorkflowCommand(action="cancel_sync", workflow_ref="workflow7", workflow_refs=["workflow7"])

    def test_parse_clean_slash_command(self):
        cmd = parse_workflow_command("/clean workflow7")
        assert cmd == WorkflowCommand(action="clean", workflow_ref="workflow7", workflow_refs=["workflow7"])

    def test_parse_clean_remote_slash_command(self):
        cmd = parse_workflow_command("/clean remote workflow7, workflow8")
        assert cmd == WorkflowCommand(
            action="clean",
            workflow_ref="workflow7",
            workflow_refs=["workflow7", "workflow8"],
            remote=True,
        )


class TestDetectWorkflowIntent:
    def test_detect_natural_language_list_tracked_workflows(self):
        cmd = detect_workflow_intent("list launchpad workflows for this project")
        assert cmd == WorkflowCommand(action="list_tracked")

    def test_detect_natural_language_rerun(self):
        cmd = detect_workflow_intent("rerun workflow3")
        assert cmd == WorkflowCommand(action="rerun", workflow_ref="workflow3", workflow_refs=["workflow3"], new_name="")

    def test_detect_natural_language_rerun_without_target(self):
        cmd = detect_workflow_intent("rerun")
        assert cmd == WorkflowCommand(action="rerun")

    def test_detect_natural_language_reanalyze(self):
        cmd = detect_workflow_intent("reanalyze workflow3")
        assert cmd == WorkflowCommand(action="reanalyze", workflow_ref="workflow3", workflow_refs=["workflow3"], new_name="")

    def test_detect_natural_language_reanalyze_without_target(self):
        cmd = detect_workflow_intent("reanalyze")
        assert cmd == WorkflowCommand(action="reanalyze")

    def test_detect_natural_language_reanalyze_multiple_targets(self):
        cmd = detect_workflow_intent("reanalyze workflow3, workflow4 and workflow5")
        assert cmd == WorkflowCommand(
            action="reanalyze",
            workflow_ref="workflow3",
            workflow_refs=["workflow3", "workflow4", "workflow5"],
        )

    def test_detect_natural_language_rerun_automatic_analysis(self):
        cmd = detect_workflow_intent("rerun automatic analysis for workflow3")
        assert cmd == WorkflowCommand(action="reanalyze", workflow_ref="workflow3", workflow_refs=["workflow3"], new_name="")

    def test_detect_natural_language_analyze_workflow_routes_to_reanalyze(self):
        cmd = detect_workflow_intent("analyze workflow3")
        assert cmd == WorkflowCommand(action="reanalyze", workflow_ref="workflow3", workflow_refs=["workflow3"], new_name="")

    def test_detect_natural_language_summarize_with_focus(self):
        cmd = detect_workflow_intent("summarize workflow3, workflow4 focusing on mapping and QC")
        assert cmd == WorkflowCommand(
            action="summarize",
            workflow_ref="workflow3",
            workflow_refs=["workflow3", "workflow4"],
            focus_text="mapping and QC",
        )

    def test_detect_natural_language_summarize_without_targets(self):
        cmd = detect_workflow_intent("summarize")
        assert cmd == WorkflowCommand(action="summarize")

    def test_detect_natural_language_generic_analyze_does_not_become_workflow_command(self):
        assert detect_workflow_intent("analyze the results") is None

    def test_detect_natural_language_rename(self):
        cmd = detect_workflow_intent("rename workflow3 to tumor-fix")
        assert cmd == WorkflowCommand(action="rename", workflow_ref="workflow3", new_name="tumor-fix")

    def test_detect_natural_language_remote_import_with_profile_suffix(self):
        cmd = detect_workflow_intent(
            "import remote workflow from /dfs9/seyedam-lab/share/dogme_drna/ad015_25182 on hpc3"
        )
        assert cmd == WorkflowCommand(
            action="import",
            source_kind="slurm",
            source_path="/dfs9/seyedam-lab/share/dogme_drna/ad015_25182",
            ssh_profile_nickname="hpc3",
        )

    def test_detect_natural_language_sync_shorthand(self):
        cmd = detect_workflow_intent("sync workflow2")
        assert cmd == WorkflowCommand(action="sync", workflow_ref="workflow2", workflow_refs=["workflow2"])

    def test_detect_natural_language_sync_without_target(self):
        cmd = detect_workflow_intent("sync workflow")
        assert cmd == WorkflowCommand(action="sync")

    def test_detect_natural_language_cancel_sync(self):
        cmd = detect_workflow_intent("cancel sync workflow2")
        assert cmd == WorkflowCommand(action="cancel_sync", workflow_ref="workflow2", workflow_refs=["workflow2"])

    def test_detect_natural_language_clean(self):
        cmd = detect_workflow_intent("clean workflow3")
        assert cmd == WorkflowCommand(action="clean", workflow_ref="workflow3", workflow_refs=["workflow3"])

    def test_detect_natural_language_clean_remote(self):
        cmd = detect_workflow_intent("clean remote workflow3 workflow4")
        assert cmd == WorkflowCommand(
            action="clean",
            workflow_ref="workflow3",
            workflow_refs=["workflow3", "workflow4"],
            remote=True,
        )


class TestResolveWorkflowReference:
    def test_prefers_alias_match_after_rename(self):
        jobs = [
            SimpleNamespace(
                run_uuid="run-2",
                workflow_alias="workflow7",
                workflow_folder_name="tumor-retry",
                workflow_display_name="tumor-retry",
                sample_name="tumor-retry",
                status="COMPLETED",
                submitted_at=2,
            )
        ]

        resolved = resolve_workflow_reference(_FakeSession(jobs), "proj-1", "workflow7")

        assert resolved.run_uuid == "run-2"

    def test_matches_legacy_work_dir_suffix_when_identity_fields_missing(self):
        jobs = [
            SimpleNamespace(
                run_uuid="run-legacy",
                workflow_alias=None,
                workflow_folder_name=None,
                workflow_display_name=None,
                sample_name="sample-legacy",
                nextflow_work_dir="/data/proj/workflow13",
                remote_work_dir=None,
                status="FAILED",
                submitted_at=13,
            )
        ]

        resolved = resolve_workflow_reference(_FakeSession(jobs), "proj-1", "workflow13")

        assert resolved.run_uuid == "run-legacy"


@pytest.mark.asyncio
async def test_execute_workflow_command_lists_tracked_workflows():
    jobs = [
        SimpleNamespace(
            run_uuid="run-2",
            workflow_alias="workflow7",
            workflow_folder_name="tumor-retry",
            workflow_display_name="tumor-retry",
            sample_name="tumor-retry",
            nextflow_work_dir="/data/proj/tumor-retry",
            remote_work_dir=None,
            status="COMPLETED",
            submitted_at=2,
        ),
        SimpleNamespace(
            run_uuid="run-legacy",
            workflow_alias=None,
            workflow_folder_name=None,
            workflow_display_name=None,
            sample_name="sample-legacy",
            nextflow_work_dir="/data/proj/workflow13",
            remote_work_dir=None,
            status="FAILED",
            submitted_at=13,
        ),
        SimpleNamespace(
            run_uuid="run-deleted",
            workflow_alias="workflow9",
            workflow_folder_name="workflow9",
            workflow_display_name="sample-9",
            sample_name="sample-9",
            nextflow_work_dir="/data/proj/workflow9",
            remote_work_dir=None,
            status="DELETED",
            submitted_at=9,
        ),
    ]

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="list_tracked"),
        project_id="proj-1",
    )

    assert "Tracked Launchpad workflows for this project (2)" in message
    assert "tumor-retry" in message
    assert "workflow13" in message
    assert "run-deleted" not in message


@pytest.mark.asyncio
async def test_execute_workflow_command_uses_active_workflow_when_target_missing(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-7",
            workflow_alias="workflow7",
            workflow_folder_name="workflow7",
            workflow_display_name="sample-7",
            sample_name="sample-7",
            status="FAILED",
            submitted_at=7,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, payload={"sample_name": "sample-7", "run_uuid": "rerun-7"})
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.setattr("cortex.workflow_commands._load_active_workflow_ref", lambda _session, _project_id: "workflow7")

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="rerun"),
        project_id="proj-1",
    )

    assert "rerun-7" in message
    assert fake_client.calls[0][1] == "http://launchpad/jobs/run-7/rerun"


@pytest.mark.asyncio
async def test_execute_workflow_command_batches_multiple_sync_targets(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-7",
            workflow_alias="workflow7",
            workflow_folder_name="workflow7",
            workflow_display_name="sample-7",
            sample_name="sample-7",
            imported_source_kind="slurm",
            transfer_state="outputs_downloaded",
            status="COMPLETED",
            submitted_at=7,
        ),
        SimpleNamespace(
            run_uuid="run-8",
            workflow_alias="workflow8",
            workflow_folder_name="workflow8",
            workflow_display_name="sample-8",
            sample_name="sample-8",
            imported_source_kind="slurm",
            transfer_state="outputs_downloaded",
            status="COMPLETED",
            submitted_at=8,
        ),
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, payload={"message": "Result synchronization started."})
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="sync", workflow_ref="workflow7", workflow_refs=["workflow7", "workflow8"]),
        project_id="proj-1",
    )

    assert "Workflow sync results:" in message
    assert len(fake_client.calls) == 2
    assert fake_client.calls[0][1] == "http://launchpad/jobs/run-7/sync-results"
    assert fake_client.calls[1][1] == "http://launchpad/jobs/run-8/sync-results"


@pytest.mark.asyncio
async def test_execute_workflow_command_posts_rename(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-3",
            workflow_alias="workflow3",
            workflow_folder_name="workflow3",
            workflow_display_name="sample-3",
            sample_name="sample-3",
            status="FAILED",
            submitted_at=3,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, payload={"new_name": "sample-3-renamed"})
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="rename", workflow_ref="workflow3", new_name="sample-3-renamed"),
        project_id="proj-1",
    )

    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/run-3/rename",
            {"X-Internal-Secret": "secret"},
            {"new_name": "sample-3-renamed"},
            None,
        )
    ]


@pytest.mark.asyncio
async def test_execute_workflow_command_deletes_legacy_workflow_by_folder_suffix(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-13",
            workflow_alias=None,
            workflow_folder_name=None,
            workflow_display_name=None,
            sample_name="sample-13",
            nextflow_work_dir="/data/proj/workflow13",
            remote_work_dir=None,
            status="FAILED",
            submitted_at=13,
        )
    ]
    fake_client = _FakeAsyncClient(
        delete_response=_FakeResponse(
            status_code=200,
            payload={"message": "Workflow folder `workflow13` deleted (12 files removed)."},
        )
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="delete", workflow_ref="workflow13"),
        project_id="proj-1",
    )

    assert fake_client.calls == [
        (
            "DELETE",
            "http://launchpad/jobs/run-13",
            {"X-Internal-Secret": "secret"},
            None,
        )
    ]
    assert "workflow13" in message


@pytest.mark.asyncio
async def test_execute_workflow_command_deletes_untracked_workflow_folder(tmp_path):
    workflow_dir = tmp_path / "workflow21"
    workflow_dir.mkdir()
    (workflow_dir / "result.txt").write_text("ok", encoding="utf-8")
    nested = workflow_dir / "nested"
    nested.mkdir()
    (nested / "file.tsv").write_text("data", encoding="utf-8")

    message = await execute_workflow_command(
        _FakeSession([]),
        WorkflowCommand(action="delete", workflow_ref="workflow21"),
        project_id="proj-1",
        project_dir=str(tmp_path),
    )

    assert not workflow_dir.exists()
    assert "Deleted untracked workflow folder `workflow21`" in message
    assert "not tracked by Launchpad" in message


@pytest.mark.asyncio
async def test_execute_workflow_command_cleans_tracked_workflows(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-22",
            workflow_alias="workflow22",
            workflow_folder_name="workflow22",
            workflow_display_name="sample-22",
            sample_name="sample-22",
            status="COMPLETED",
            submitted_at=22,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={"message": "Cleaned `workflow22`: gzipped 2 `bedMethyl` BED files; removed `dor-run` (4 files)."},
        )
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="clean", workflow_ref="workflow22"),
        project_id="proj-1",
    )

    assert "Workflow clean results:" not in message
    assert "Cleaned `workflow22`" in message
    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/run-22/clean",
            {"X-Internal-Secret": "secret"},
            None,
            {"remote": "false"},
        )
    ]


@pytest.mark.asyncio
async def test_execute_workflow_command_cleans_untracked_workflow_folder_and_gzips_each_bed(tmp_path):
    workflow_dir = tmp_path / "workflow23"
    workflow_dir.mkdir()
    work_dir = workflow_dir / "work"
    work_dir.mkdir()
    (work_dir / "trace.txt").write_text("ok", encoding="utf-8")
    dor_dir = workflow_dir / "dor-scratch"
    dor_dir.mkdir()
    (dor_dir / "report.tsv").write_text("data", encoding="utf-8")
    bedmethyl_dir = workflow_dir / "bedMethyl"
    bedmethyl_dir.mkdir()
    (bedmethyl_dir / "a.bed").write_text("a", encoding="utf-8")
    (bedmethyl_dir / "b.bed").write_text("b", encoding="utf-8")

    message = await execute_workflow_command(
        _FakeSession([]),
        WorkflowCommand(action="clean", workflow_ref="workflow23"),
        project_id="proj-1",
        project_dir=str(tmp_path),
    )

    assert not work_dir.exists()
    assert not dor_dir.exists()
    assert not (bedmethyl_dir / "a.bed").exists()
    assert not (bedmethyl_dir / "b.bed").exists()
    assert (bedmethyl_dir / "a.bed.gz").exists()
    assert (bedmethyl_dir / "b.bed.gz").exists()
    assert "gzipped 2 `bedMethyl` BED files" in message
    assert "untracked workflow folder" in message


@pytest.mark.asyncio
async def test_execute_workflow_command_clean_workflows_targets_tracked_and_untracked(monkeypatch, tmp_path):
    jobs = [
        SimpleNamespace(
            run_uuid="run-24",
            workflow_alias="workflow24",
            workflow_folder_name="workflow24",
            workflow_display_name="sample-24",
            sample_name="sample-24",
            status="COMPLETED",
            submitted_at=24,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, payload={"message": "Cleaned `workflow24`."})
    )
    workflow_dir = tmp_path / "workflow25"
    workflow_dir.mkdir()
    (workflow_dir / "work").mkdir()

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="clean", workflow_ref="workflows", workflow_refs=["workflows"]),
        project_id="proj-1",
        project_dir=str(tmp_path),
    )

    assert "Workflow clean results:" in message
    assert "workflow24" in message
    assert "workflow25" in message
    assert fake_client.calls[0][1] == "http://launchpad/jobs/run-24/clean"


@pytest.mark.asyncio
async def test_execute_workflow_command_clean_remote_workflows_targets_tracked_only(monkeypatch, tmp_path):
    jobs = [
        SimpleNamespace(
            run_uuid="run-27",
            workflow_alias="workflow27",
            workflow_folder_name="workflow27",
            workflow_display_name="sample-27",
            sample_name="sample-27",
            status="COMPLETED",
            submitted_at=27,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, payload={"message": "Remotely cleaned `workflow27`."})
    )
    (tmp_path / "workflow28").mkdir()

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="clean", workflow_ref="workflows", workflow_refs=["workflows"], remote=True),
        project_id="proj-1",
        project_dir=str(tmp_path),
    )

    assert "workflow27" in message
    assert "workflow28" not in message
    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/run-27/clean",
            {"X-Internal-Secret": "secret"},
            None,
            {"remote": "true"},
        )
    ]


@pytest.mark.asyncio
async def test_execute_workflow_command_cleans_remote_workflows(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-26",
            workflow_alias="workflow26",
            workflow_folder_name="workflow26",
            workflow_display_name="sample-26",
            sample_name="sample-26",
            status="COMPLETED",
            submitted_at=26,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(status_code=200, payload={"message": "Remotely cleaned `workflow26`: removed `work` (3 files)."})
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="clean", workflow_ref="workflow26", remote=True),
        project_id="proj-1",
    )

    assert "Remotely cleaned `workflow26`" in message
    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/run-26/clean",
            {"X-Internal-Secret": "secret"},
            None,
            {"remote": "true"},
        )
    ]


@pytest.mark.asyncio
async def test_execute_workflow_command_remote_clean_uses_extended_timeout(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-29",
            workflow_alias="workflow29",
            workflow_folder_name="workflow29",
            workflow_display_name="sample-29",
            sample_name="sample-29",
            status="COMPLETED",
            submitted_at=29,
        )
    ]
    captured_timeouts = []
    fake_client = _FakeAsyncClient(post_response=_FakeResponse(status_code=200, payload={"message": "Remotely cleaned `workflow29`."}))

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands._WORKFLOW_CLEAN_TIMEOUT_SECONDS", 3600.0)
    monkeypatch.setattr(
        "cortex.workflow_commands.httpx.AsyncClient",
        lambda timeout: (captured_timeouts.append(timeout) or fake_client),
    )

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="clean", workflow_ref="workflow29", remote=True),
        project_id="proj-1",
    )

    assert "workflow29" in message
    assert captured_timeouts == [3600.0]


@pytest.mark.asyncio
async def test_execute_workflow_command_clean_records_tracking_metadata(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-30",
            workflow_alias="workflow30",
            workflow_folder_name="workflow30",
            workflow_display_name="sample-30",
            sample_name="sample-30",
            output_directory="/tmp/workflow30",
            status="COMPLETED",
            submitted_at=30,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={
                "status": "cleaning",
                "run_stage": "CLEANING_REMOTE",
                "message": "Started remote clean for `workflow30`. The workflow clean is running in the background; check job status or logs for completion.",
            },
        )
    )
    clean_tracking = []

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    session = _FakeSession(jobs)
    message = await execute_workflow_command(
        session,
        WorkflowCommand(action="clean", workflow_ref="workflow30", remote=True),
        project_id="proj-1",
        owner_id="user-1",
        model="default",
        clean_tracking=clean_tracking,
    )

    assert "Started remote clean for `workflow30`" in message
    assert clean_tracking == [
        {
            "run_uuid": "run-30",
            "workflow_ref": "workflow30",
            "workflow_label": "workflow30",
            "remote": True,
            "message": "Started remote clean for `workflow30`. The workflow clean is running in the background; check job status or logs for completion.",
        }
    ]


@pytest.mark.asyncio
async def test_execute_workflow_command_posts_import_and_registers_block(monkeypatch):
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={
                "run_uuid": "import-run-1",
                "sample_name": "tumor-a",
                "mode": "DNA",
                "status": "COMPLETED",
                "work_directory": "/data/proj/workflow8",
                "execution_mode": "local",
                "transfer_state": "outputs_downloaded",
                "imported_source_kind": "local",
                "imported_source_complete": True,
                "import_warning_message": None,
                "message": "Imported workflow into workflow8.",
            },
        )
    )
    register_calls = []

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.setattr("cortex.workflow_commands._get_project_import_context", lambda _session, _project_id: ("alice", "proj-a"))
    monkeypatch.setattr(
        "cortex.workflow_commands._register_imported_job_block",
        lambda session, **kwargs: register_calls.append(kwargs),
    )

    message = await execute_workflow_command(
        _FakeSession([]),
        WorkflowCommand(action="import", source_path="/tmp/workflow7", source_kind="local"),
        project_id="proj-1",
        owner_id="user-1",
        model="gpt-test",
    )

    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/import",
            {"X-Internal-Secret": "secret"},
            {
                "project_id": "proj-1",
                "user_id": "user-1",
                "username": "alice",
                "project_slug": "proj-a",
                "source_path": "/tmp/workflow7",
                "source_kind": "local",
                "full_copy": False,
            },
            None,
        )
    ]
    assert register_calls[0]["project_id"] == "proj-1"
    assert register_calls[0]["owner_id"] == "user-1"
    assert register_calls[0]["payload"]["run_uuid"] == "import-run-1"
    assert "workflow8" in message


@pytest.mark.asyncio
async def test_execute_workflow_command_posts_import_with_resolved_profile(monkeypatch):
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={
                "run_uuid": "import-run-2",
                "sample_name": "tumor-a",
                "mode": "DNA",
                "status": "COMPLETED",
                "work_directory": "/data/proj/workflow9",
                "execution_mode": "slurm",
                "transfer_state": "downloading_outputs",
                "imported_source_kind": "slurm",
                "imported_source_complete": True,
                "import_warning_message": None,
                "message": "Import started for workflow9.",
            },
        )
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.setattr("cortex.workflow_commands._get_project_import_context", lambda _session, _project_id: ("alice", "proj-a"))
    monkeypatch.setattr("cortex.workflow_commands._register_imported_job_block", lambda *args, **kwargs: None)
    monkeypatch.setattr("cortex.workflow_commands._resolve_import_ssh_profile_id", lambda _session, _owner_id, _nickname: "profile-123")

    await execute_workflow_command(
        _FakeSession([]),
        WorkflowCommand(
            action="import",
            source_path="/dfs9/seyedam-lab/share/dogme_drna/ad015_25182",
            source_kind="slurm",
            ssh_profile_nickname="hpc3",
        ),
        project_id="proj-1",
        owner_id="user-1",
        model="default",
    )

    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/import",
            {"X-Internal-Secret": "secret"},
            {
                "project_id": "proj-1",
                "user_id": "user-1",
                "username": "alice",
                "project_slug": "proj-a",
                "source_path": "/dfs9/seyedam-lab/share/dogme_drna/ad015_25182",
                "source_kind": "slurm",
                "full_copy": False,
                "ssh_profile_id": "profile-123",
            },
            None,
        )
    ]


@pytest.mark.asyncio
async def test_execute_workflow_command_does_not_force_sync_while_import_is_downloading(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-9",
            workflow_alias="workflow9",
            workflow_folder_name="workflow9",
            workflow_display_name="sample-9",
            sample_name="sample-9",
            imported_source_kind="slurm",
            transfer_state="downloading_outputs",
            status="COMPLETED",
            submitted_at=9,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={
                "message": "Result synchronization started. Monitor progress via job status polling.",
                "transfer_state": "downloading_outputs",
                "import_warning_message": "Imported workflow is incomplete at the source. Run sync again later to pick up newly produced outputs.",
            },
        )
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="sync", workflow_ref="workflow9"),
        project_id="proj-1",
    )

    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/run-9/sync-results",
            {"X-Internal-Secret": "secret"},
            None,
            {"force": "false"},
        )
    ]
    assert "Result synchronization started" in message


@pytest.mark.asyncio
async def test_execute_workflow_command_forces_sync_after_imported_outputs_downloaded(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-10",
            workflow_alias="workflow10",
            workflow_folder_name="workflow10",
            workflow_display_name="sample-10",
            sample_name="sample-10",
            imported_source_kind="slurm",
            transfer_state="outputs_downloaded",
            status="COMPLETED",
            submitted_at=10,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={
                "message": "Result synchronization started. Monitor progress via job status polling.",
                "transfer_state": "downloading_outputs",
                "import_warning_message": "Imported workflow is incomplete at the source. Run sync again later to pick up newly produced outputs.",
            },
        )
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="sync", workflow_ref="workflow10"),
        project_id="proj-1",
    )

    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/run-10/sync-results",
            {"X-Internal-Secret": "secret"},
            None,
            {"force": "true"},
        )
    ]
    assert "Result synchronization started" in message


@pytest.mark.asyncio
async def test_execute_workflow_command_sync_updates_execution_block_and_starts_polling(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-15",
            workflow_alias="workflow15",
            workflow_folder_name="workflow15",
            workflow_display_name="sample-15",
            sample_name="sample-15",
            imported_source_kind="slurm",
            transfer_state="outputs_downloaded",
            status="COMPLETED",
            submitted_at=15,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={
                "message": "Result synchronization started. Monitor progress via job status polling.",
                "transfer_state": "downloading_outputs",
            },
        )
    )
    apply_calls = []
    fake_poll = AsyncMock()

    def fake_apply(session, *, project_id, run_uuid, transfer_state, message, import_warning):
        apply_calls.append(
            {
                "session": session,
                "project_id": project_id,
                "run_uuid": run_uuid,
                "transfer_state": transfer_state,
                "message": message,
                "import_warning": import_warning,
            }
        )
        return [SimpleNamespace(id="block-15")]

    def fake_create_task(coro):
        coro.close()
        return None

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.setattr("cortex.workflow_commands._apply_result_sync_status_update", fake_apply)
    monkeypatch.setattr("cortex.workflow_commands.poll_job_status", fake_poll)
    monkeypatch.setattr("cortex.workflow_commands.asyncio.create_task", fake_create_task)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="sync", workflow_ref="workflow15"),
        project_id="proj-1",
    )

    assert "Result synchronization started" in message
    assert apply_calls == [
        {
            "session": apply_calls[0]["session"],
            "project_id": "proj-1",
            "run_uuid": "run-15",
            "transfer_state": "downloading_outputs",
            "message": "Result synchronization started. Monitor progress via job status polling.",
            "import_warning": None,
        }
    ]
    fake_poll.assert_called_once_with("proj-1", "block-15", "run-15")


@pytest.mark.asyncio
async def test_execute_workflow_command_cancels_sync(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-11",
            workflow_alias="workflow11",
            workflow_folder_name="workflow11",
            workflow_display_name="sample-11",
            sample_name="sample-11",
            imported_source_kind="slurm",
            transfer_state="downloading_outputs",
            status="RUNNING",
            submitted_at=11,
        )
    ]
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            status_code=200,
            payload={
                "message": "Result synchronization cancelled. Run sync again to resume copying outputs.",
                "status": "sync_cancelled",
                "transfer_state": "sync_cancelled",
            },
        )
    )

    monkeypatch.setattr("cortex.workflow_commands._launchpad_rest_base_url", lambda: "http://launchpad")
    monkeypatch.setattr("cortex.workflow_commands._launchpad_internal_headers", lambda: {"X-Internal-Secret": "secret"})
    monkeypatch.setattr("cortex.workflow_commands.httpx.AsyncClient", lambda timeout: fake_client)

    message = await execute_workflow_command(
        _FakeSession(jobs),
        WorkflowCommand(action="cancel_sync", workflow_ref="workflow11"),
        project_id="proj-1",
    )

    assert fake_client.calls == [
        (
            "POST",
            "http://launchpad/jobs/run-11/cancel",
            {"X-Internal-Secret": "secret"},
            None,
            None,
        )
    ]
    assert "Result synchronization cancelled" in message


@pytest.mark.asyncio
async def test_execute_manual_workflow_analysis_reuses_auto_trigger(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-12",
            workflow_alias="workflow12",
            workflow_folder_name="workflow12",
            workflow_display_name="sample-12",
            sample_name="sample-12",
            mode="DNA",
            nextflow_work_dir="/data/proj/workflow12",
            status="COMPLETED",
            result_destination="local",
            transfer_state="outputs_downloaded",
            submitted_at=12,
        )
    ]
    sentinel_block = object()
    mock_auto = AsyncMock(return_value=sentinel_block)

    monkeypatch.setattr("cortex.workflow_commands._auto_trigger_analysis", mock_auto)

    agent_block, error = await execute_manual_workflow_analysis(
        _FakeSession(jobs),
        WorkflowCommand(action="reanalyze", workflow_ref="workflow12"),
        project_id="proj-1",
        owner_id="user-1",
        model="gemma-test",
    )

    assert agent_block is sentinel_block
    assert error is None
    mock_auto.assert_awaited_once_with(
        "proj-1",
        "run-12",
        {
            "sample_name": "sample-12",
            "mode": "DNA",
            "model": "gemma-test",
            "work_directory": "/data/proj/workflow12",
            "workflow_key": "",
            "workflow_ref": "workflow12",
        },
        "user-1",
        persist_request_message=False,
        force=True,
    )


@pytest.mark.asyncio
async def test_execute_manual_workflow_analysis_uses_active_workflow_when_target_missing(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-14",
            workflow_alias="workflow14",
            workflow_folder_name="workflow14",
            workflow_display_name="sample-14",
            sample_name="sample-14",
            mode="RNA",
            nextflow_work_dir="/data/proj/workflow14",
            status="COMPLETED",
            result_destination="local",
            transfer_state="outputs_downloaded",
            submitted_at=14,
        )
    ]
    sentinel_block = object()
    mock_auto = AsyncMock(return_value=sentinel_block)

    monkeypatch.setattr("cortex.workflow_commands._auto_trigger_analysis", mock_auto)
    monkeypatch.setattr("cortex.workflow_commands._load_active_workflow_ref", lambda _session, _project_id: "workflow14")

    agent_block, error = await execute_manual_workflow_analysis(
        _FakeSession(jobs),
        WorkflowCommand(action="reanalyze"),
        project_id="proj-1",
        owner_id="user-1",
        model="gemma-test",
    )

    assert agent_block is sentinel_block
    assert error is None


@pytest.mark.asyncio
async def test_execute_manual_workflow_analysis_supports_untracked_workflow_dir(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow16"
    workflow_dir.mkdir()
    (workflow_dir / "de_inputs").mkdir()
    (workflow_dir / "de_results").mkdir()

    sentinel_block = object()
    mock_auto = AsyncMock(return_value=sentinel_block)

    monkeypatch.setattr("cortex.workflow_commands._auto_trigger_analysis", mock_auto)

    agent_block, error = await execute_manual_workflow_analysis(
        _FakeSession([]),
        WorkflowCommand(action="reanalyze", workflow_ref="workflow16"),
        project_id="proj-1",
        project_dir=str(tmp_path),
        owner_id="user-1",
        model="gemma-test",
    )

    assert agent_block is sentinel_block
    assert error is None
    mock_auto.assert_awaited_once_with(
        "proj-1",
        "",
        {
            "sample_name": "workflow16",
            "mode": None,
            "model": "gemma-test",
            "work_directory": str(workflow_dir),
            "workflow_key": "",
            "workflow_ref": "workflow16",
        },
        "user-1",
        persist_request_message=False,
        force=True,
    )


@pytest.mark.asyncio
async def test_execute_manual_workflow_analysis_requires_ready_results(monkeypatch):
    jobs = [
        SimpleNamespace(
            run_uuid="run-13",
            workflow_alias="workflow13",
            workflow_folder_name="workflow13",
            workflow_display_name="sample-13",
            sample_name="sample-13",
            mode="DNA",
            nextflow_work_dir="/data/proj/workflow13",
            status="RUNNING",
            submitted_at=13,
        )
    ]
    mock_auto = AsyncMock()

    monkeypatch.setattr("cortex.workflow_commands._auto_trigger_analysis", mock_auto)

    agent_block, error = await execute_manual_workflow_analysis(
        _FakeSession(jobs),
        WorkflowCommand(action="reanalyze", workflow_ref="workflow13"),
        project_id="proj-1",
        owner_id="user-1",
        model="gemma-test",
    )

    assert agent_block is None
    assert "only available after the workflow finishes" in error
    mock_auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_manual_workflow_analysis_allows_failed_job_with_local_outputs(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow4"
    workflow_dir.mkdir()
    (workflow_dir / "annot").mkdir()
    (workflow_dir / "qc_summary.csv").write_text("metric,value\nreads,10\n", encoding="utf-8")

    jobs = [
        SimpleNamespace(
            run_uuid="run-4",
            workflow_alias="workflow4",
            workflow_folder_name="workflow4",
            workflow_display_name="sample-4",
            sample_name="sample-4",
            mode="RNA",
            nextflow_work_dir=str(workflow_dir),
            output_directory=str(workflow_dir),
            status="FAILED",
            submitted_at=4,
        )
    ]
    sentinel_block = object()
    mock_auto = AsyncMock(return_value=sentinel_block)

    monkeypatch.setattr("cortex.workflow_commands._auto_trigger_analysis", mock_auto)

    agent_block, error = await execute_manual_workflow_analysis(
        _FakeSession(jobs),
        WorkflowCommand(action="reanalyze", workflow_ref="workflow4"),
        project_id="proj-1",
        owner_id="user-1",
        model="gemma-test",
    )

    assert agent_block is sentinel_block
    assert error is None
    mock_auto.assert_awaited_once()


# ── Parse / detect "use" ──────────────────────────────────────────────────

class TestParseUseCommand:
    def test_slash_use(self):
        cmd = parse_workflow_command("/use workflow10")
        assert cmd == WorkflowCommand(action="use", workflow_ref="workflow10")

    def test_slash_use_case_insensitive(self):
        cmd = parse_workflow_command("/USE Workflow5")
        assert cmd == WorkflowCommand(action="use", workflow_ref="Workflow5")


class TestDetectUseIntent:
    def test_use_natural(self):
        cmd = detect_workflow_intent("use workflow10")
        assert cmd == WorkflowCommand(action="use", workflow_ref="workflow10")

    def test_switch_to(self):
        cmd = detect_workflow_intent("switch to workflow3")
        assert cmd == WorkflowCommand(action="use", workflow_ref="workflow3")

    def test_set_workflow_to(self):
        cmd = detect_workflow_intent("set workflow to workflow5")
        assert cmd == WorkflowCommand(action="use", workflow_ref="workflow5")

    def test_set_active_workflow_to(self):
        cmd = detect_workflow_intent("set active workflow to workflow8")
        assert cmd == WorkflowCommand(action="use", workflow_ref="workflow8")

    def test_please_prefix(self):
        cmd = detect_workflow_intent("please use workflow2")
        assert cmd == WorkflowCommand(action="use", workflow_ref="workflow2")


# ── execute_use_workflow ───────────────────────────────────────────────────

class TestExecuteUseWorkflow:
    def _make_state(self, workflows=None, work_dir="", active_idx=None):
        return SimpleNamespace(
            workflows=workflows or [],
            work_dir=work_dir,
            active_workflow_index=active_idx,
        )

    def test_match_known_workflow(self):
        state = self._make_state(workflows=[
            {"work_dir": "/data/proj/workflow9"},
            {"work_dir": "/data/proj/workflow10"},
        ], work_dir="/data/proj/workflow9", active_idx=0)

        updated, md = execute_use_workflow(state, "/data/proj", "workflow10")
        assert updated.work_dir == "/data/proj/workflow10"
        assert updated.active_workflow_index == 1
        assert "workflow10" in md

    def test_match_case_insensitive(self):
        state = self._make_state(workflows=[
            {"work_dir": "/proj/Workflow5"},
        ])
        updated, md = execute_use_workflow(state, "/proj", "workflow5")
        assert updated.work_dir == "/proj/Workflow5"
        assert updated.active_workflow_index == 0

    def test_fallback_to_disk(self, tmp_path):
        wf_dir = tmp_path / "workflow7"
        wf_dir.mkdir()
        state = self._make_state()

        updated, md = execute_use_workflow(state, str(tmp_path), "workflow7")
        assert updated.work_dir == str(wf_dir)
        assert updated.active_workflow_index is None
        assert "workflow7" in md

    def test_not_found(self):
        state = self._make_state()
        updated, md = execute_use_workflow(state, "/no/such/dir", "workflow99")
        assert "Could not find" in md