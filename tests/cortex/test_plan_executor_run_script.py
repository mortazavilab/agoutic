import pytest

from cortex.plan_executor import STEP_TOOL_DEFAULTS, execute_step, should_auto_execute


class _FakeSession:
    def commit(self):
        return None

    def refresh(self, _obj):
        return None


class _FakeBlock:
    id = "wf-1"
    project_id = "proj-1"
    status = "PENDING"
    payload_json = "{}"


@pytest.mark.asyncio
async def test_execute_plan_rejects_invalid_plan_before_dispatch(monkeypatch):
    from cortex.plan_executor import execute_plan

    payload = {
        "title": "Invalid plan",
        "project_id": "proj-1",
        "steps": [
            {
                "id": "bad_1",
                "kind": "UNKNOWN_KIND",
                "title": "Unknown",
                "status": "PENDING",
                "depends_on": [],
                "requires_approval": False,
            }
        ],
    }
    persisted = {}

    def _fake_get_block_payload(_workflow_block):
        return payload

    def _fake_persist(_session, _workflow_block, plan_payload):
        persisted.update(plan_payload)

    async def _unexpected_execute_step(*_args, **_kwargs):
        raise AssertionError("execute_step should not run for invalid plans")

    monkeypatch.setattr("cortex.llm_validators.get_block_payload", _fake_get_block_payload)
    monkeypatch.setattr("cortex.plan_executor._persist_step_update", _fake_persist)
    monkeypatch.setattr("cortex.plan_executor.execute_step", _unexpected_execute_step)

    await execute_plan(_FakeSession(), _FakeBlock(), project_id="proj-1")

    assert persisted.get("status") == "FAILED"
    assert persisted.get("validation_error", {}).get("error") == "plan_validation_failed"


@pytest.mark.asyncio
async def test_execute_plan_rejects_project_scope_mismatch(monkeypatch):
    from cortex.plan_executor import execute_plan

    payload = {
        "title": "Mismatched plan",
        "project_id": "proj-2",
        "steps": [
            {
                "id": "s1",
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "status": "PENDING",
                "depends_on": [],
                "requires_approval": False,
            }
        ],
    }
    persisted = {}

    def _fake_get_block_payload(_workflow_block):
        return payload

    def _fake_persist(_session, _workflow_block, plan_payload):
        persisted.update(plan_payload)

    monkeypatch.setattr("cortex.llm_validators.get_block_payload", _fake_get_block_payload)
    monkeypatch.setattr("cortex.plan_executor._persist_step_update", _fake_persist)

    await execute_plan(_FakeSession(), _FakeBlock(), project_id="proj-1")

    assert persisted.get("status") == "FAILED"
    issues = persisted.get("validation_error", {}).get("issues", [])
    assert any(issue.get("code") == "project_id_mismatch" for issue in issues)


@pytest.mark.asyncio
async def test_execute_step_run_script_returns_special_handling(monkeypatch):
    payload = {
        "steps": [
            {
                "id": "step1",
                "kind": "RUN_SCRIPT",
                "title": "Run script",
                "status": "PENDING",
                "requires_approval": True,
            }
        ]
    }

    # Keep this test focused on dispatch behavior only.
    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "step1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    assert result.data == {"action": "special_handling", "kind": "RUN_SCRIPT"}
    assert payload["steps"][0]["status"] == "WAITING_APPROVAL"


def test_run_script_is_not_auto_executed_by_default():
    step = {"kind": "RUN_SCRIPT", "requires_approval": False}
    assert should_auto_execute(step) is False
    assert STEP_TOOL_DEFAULTS["RUN_SCRIPT"] is None
