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
