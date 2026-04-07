from types import SimpleNamespace

import pytest

from cortex.workflow_commands import (
    WorkflowCommand,
    detect_workflow_intent,
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

    async def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, headers, json))
        return self.post_response

    async def delete(self, url, headers=None):
        self.calls.append(("DELETE", url, headers, None))
        return self.delete_response


class TestParseWorkflowCommand:
    def test_parse_rerun_slash_command(self):
        cmd = parse_workflow_command("/rerun workflow7")
        assert cmd == WorkflowCommand(action="rerun", workflow_ref="workflow7", new_name="")

    def test_parse_rename_slash_command(self):
        cmd = parse_workflow_command("/rename workflow7 tumor-retry")
        assert cmd == WorkflowCommand(action="rename", workflow_ref="workflow7", new_name="tumor-retry")


class TestDetectWorkflowIntent:
    def test_detect_natural_language_rerun(self):
        cmd = detect_workflow_intent("rerun workflow3")
        assert cmd == WorkflowCommand(action="rerun", workflow_ref="workflow3", new_name="")

    def test_detect_natural_language_rename(self):
        cmd = detect_workflow_intent("rename workflow3 to tumor-fix")
        assert cmd == WorkflowCommand(action="rename", workflow_ref="workflow3", new_name="tumor-fix")


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
        )
    ]
    assert "sample-3-renamed" in message