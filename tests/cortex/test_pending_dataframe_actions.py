import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from cortex.pending_dataframe_actions import execute_pending_dataframe_action


class _FakeState:
    def __init__(self):
        self.latest_dataframe = ""
        self.known_dataframes = []

    def to_dict(self):
        return {
            "latest_dataframe": self.latest_dataframe,
            "known_dataframes": self.known_dataframes,
        }


def test_execute_pending_dataframe_action_creates_agent_plan(monkeypatch):
    history_block = SimpleNamespace(
        type="AGENT_PLAN",
        payload_json=json.dumps({
            "_dataframes": {
                "source": {
                    "columns": ["sample", "reads"],
                    "data": [{"sample": "a", "reads": 1}],
                    "metadata": {"df_id": 1, "label": "source", "row_count": 1, "visible": True},
                }
            }
        }),
    )
    pending_block = SimpleNamespace(
        id="pending-1",
        project_id="proj-1",
        owner_id="user-1",
        status="PENDING",
        payload_json=json.dumps({
            "summary": "Rename DF1 columns",
            "skill": "analyze_job_results",
            "model": "test-model",
            "action_call": {
                "tool": "rename_dataframe_columns",
                "params": {"df_id": 1, "rename_map": {"reads": "count"}},
            },
        }),
    )

    session = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [history_block, pending_block]
    session.execute.return_value = execute_result

    monkeypatch.setattr(
        "cortex.pending_dataframe_actions.execute_local_dataframe_call",
        lambda tool_name, params, history_blocks: {
            "columns": ["sample", "count"],
            "data": [{"sample": "a", "count": 1}],
            "row_count": 1,
            "metadata": {"label": "rename_dataframe_columns_DF1", "row_count": 1},
        },
    )

    def _assign_df_ids(embedded_dataframes, _history_blocks, _injected_dfs):
        for payload in embedded_dataframes.values():
            payload.setdefault("metadata", {})["df_id"] = 2
            payload["metadata"].setdefault("label", "rename_dataframe_columns_DF1")
            payload["metadata"].setdefault("row_count", 1)

    monkeypatch.setattr("cortex.pending_dataframe_actions.assign_df_ids", _assign_df_ids)
    monkeypatch.setattr("cortex.pending_dataframe_actions._build_conversation_state", lambda **_kwargs: _FakeState())
    monkeypatch.setattr(
        "cortex.pending_dataframe_actions._create_block_internal",
        lambda *args, **kwargs: SimpleNamespace(id="agent-1", status="DONE", payload_json=json.dumps(kwargs.get("payload") or args[3])),
    )
    monkeypatch.setattr(
        "cortex.pending_dataframe_actions.row_to_dict",
        lambda obj: {"id": obj.id, "status": getattr(obj, "status", None)},
    )

    result = execute_pending_dataframe_action(session, pending_block)

    assert pending_block.status == "COMPLETED"
    assert json.loads(pending_block.payload_json)["result_df_id"] == 2
    assert result["pending_block"]["status"] == "COMPLETED"
    assert result["agent_block"]["id"] == "agent-1"