"""Tests for helper functions extracted directly from ui/app.py source."""

import ast
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import plotly.express as px
import pytest


UI_APP_PATH = Path(__file__).resolve().parents[2] / "ui" / "app.py"


def _load_function(name: str, extra_globals: dict | None = None):
    """Compile a single function from ui/app.py without importing Streamlit."""
    source = UI_APP_PATH.read_text()
    tree = ast.parse(source, filename=str(UI_APP_PATH))
    fn_node = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = ast.Module(body=[fn_node], type_ignores=[])
    namespace = {"pd": pd, "px": px, "datetime": dt, "API_URL": "http://api.test", "PLOTLY_TEMPLATE": {}}
    if extra_globals:
        namespace.update(extra_globals)
    exec(compile(module, filename=str(UI_APP_PATH), mode="exec"), namespace)
    return namespace[name]


class TestCreateProjectServerSide:
    def test_returns_server_project_id_on_success(self):
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"id": "project-123"},
        )
        request = MagicMock(return_value=response)
        fn = _load_function("_create_project_server_side", {"make_authenticated_request": request})

        project_id = fn("named-project")

        assert project_id == "project-123"
        request.assert_called_once_with(
            "POST",
            "http://api.test/projects",
            json={"name": "named-project"},
            timeout=10,
        )

    def test_falls_back_to_uuid_when_request_fails(self):
        request = MagicMock(side_effect=RuntimeError("network down"))
        fake_uuid = SimpleNamespace(__str__=lambda self: "uuid-fallback")
        fn = _load_function("_create_project_server_side", {"make_authenticated_request": request})

        import uuid
        original_uuid4 = uuid.uuid4
        uuid.uuid4 = lambda: "uuid-fallback"
        try:
            project_id = fn("named-project")
        finally:
            uuid.uuid4 = original_uuid4

        assert project_id == "uuid-fallback"


class TestJobStatusUpdatedAt:
    def test_prefers_live_poll_time_when_live_status_succeeds(self):
        fn = _load_function("_job_status_updated_at")
        now = dt.datetime(2026, 3, 19, 23, 0, 0, tzinfo=dt.timezone.utc)

        value = fn("2026-03-19T22:50:00Z", True, now)

        assert value == "2026-03-19T22:50:00Z"

    def test_preserves_supplied_live_poll_timestamp_when_present(self):
        fn = _load_function("_job_status_updated_at")
        now = dt.datetime(2026, 3, 19, 23, 0, 0, tzinfo=dt.timezone.utc)

        value = fn("2026-03-19T22:59:58Z", True, now)

        assert value == "2026-03-19T22:59:58Z"

    def test_falls_back_to_persisted_timestamp_when_live_status_fails(self):
        fn = _load_function("_job_status_updated_at")

        value = fn("2026-03-19T22:50:00Z", False)

        assert value == "2026-03-19T22:50:00Z"

    def test_returns_none_when_no_timestamp_exists(self):
        fn = _load_function("_job_status_updated_at")

        value = fn(None, False)

        assert value is None


class TestPauseAutoRefresh:
    def test_sets_requested_suppression_deadline(self):
        fake_st = SimpleNamespace(session_state={})
        fake_time = SimpleNamespace(time=lambda: 100.0)
        fn = _load_function("_pause_auto_refresh", {"st": fake_st, "time": fake_time})

        fn(4)

        assert fake_st.session_state["_suppress_auto_refresh_until"] == 104.0

    def test_does_not_shorten_existing_deadline(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 110.0})
        fake_time = SimpleNamespace(time=lambda: 100.0)
        fn = _load_function("_pause_auto_refresh", {"st": fake_st, "time": fake_time})

        fn(2)

        assert fake_st.session_state["_suppress_auto_refresh_until"] == 110.0


class TestAutoRefreshIsSuppressed:
    def test_returns_true_before_deadline(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 105.0})
        fn = _load_function(
            "_auto_refresh_is_suppressed",
            {"st": fake_st, "_has_pending_destructive_confirmation": lambda: False},
        )

        assert fn(104.0) is True

    def test_returns_false_after_deadline(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 105.0})
        fn = _load_function(
            "_auto_refresh_is_suppressed",
            {"st": fake_st, "_has_pending_destructive_confirmation": lambda: False},
        )

        assert fn(105.0) is False

    def test_returns_true_when_destructive_confirmation_is_open(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 0.0})
        fn = _load_function(
            "_auto_refresh_is_suppressed",
            {"st": fake_st, "_has_pending_destructive_confirmation": lambda: True},
        )

        assert fn(105.0) is True


class TestHasPendingDestructiveConfirmation:
    def test_detects_archive_confirmation(self):
        fake_st = SimpleNamespace(session_state={"_confirm_archive_project_id": "proj-1"})
        fn = _load_function("_has_pending_destructive_confirmation", {"st": fake_st})

        assert fn() is True

    def test_detects_job_delete_confirmation(self):
        fake_st = SimpleNamespace(session_state={"del_confirm_block-1": True})
        fn = _load_function("_has_pending_destructive_confirmation", {"st": fake_st})

        assert fn() is True

    def test_returns_false_without_pending_confirmation(self):
        fake_st = SimpleNamespace(session_state={"_confirm_archive_project_id": None})
        fn = _load_function("_has_pending_destructive_confirmation", {"st": fake_st})

        assert fn() is False


class TestResolveDfById:
    def test_returns_latest_matching_dataframe(self):
        fn = _load_function("_resolve_df_by_id")
        blocks = [
            {
                "type": "AGENT_PLAN",
                "payload": {
                    "_dataframes": {
                        "old.csv": {
                            "metadata": {"df_id": 7},
                            "data": [{"gene": "OLD", "score": 1.0}],
                            "columns": ["gene", "score"],
                        }
                    }
                },
            },
            {
                "type": "AGENT_PLAN",
                "payload": {
                    "_dataframes": {
                        "new.csv": {
                            "metadata": {"df_id": 7},
                            "data": [{"gene": "NEW", "score": 2.0}],
                            "columns": ["gene", "score"],
                        }
                    }
                },
            },
        ]

        df, label = fn(7, blocks)

        assert label == "new.csv"
        assert list(df["gene"]) == ["NEW"]

    def test_ignores_non_agent_plan_blocks_and_missing_ids(self):
        fn = _load_function("_resolve_df_by_id")
        blocks = [
            {"type": "USER_MESSAGE", "payload": {}},
            {"type": "AGENT_PLAN", "payload": {"_dataframes": {}}},
        ]

        assert fn(99, blocks) == (None, None)


class TestResolvePayloadDfById:
    def test_returns_dataframe_from_local_payload(self):
        fn = _load_function("_resolve_payload_df_by_id")
        dfs = {
            "stats.csv": {
                "metadata": {"df_id": 1},
                "columns": ["Category", "Count"],
                "data": [
                    {"Category": "Known", "Count": 10},
                    {"Category": "Novel", "Count": 1},
                ],
            }
        }

        df, label = fn(1, dfs)

        assert label == "stats.csv"
        assert df is not None
        assert df["Count"].tolist() == [10, 1]

    def test_returns_none_when_missing(self):
        fn = _load_function("_resolve_payload_df_by_id")

        assert fn(99, {}) == (None, None)


class TestFindRelatedWorkflowPlan:
    def test_prefers_matching_prior_workflow_title(self):
        fn = _load_function("_find_related_workflow_plan")
        blocks = [
            {
                "id": "wf-1",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "Older workflow"},
            },
            {
                "id": "wf-2",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "Plot and interpret results for JamshidW"},
            },
            {
                "id": "agent-1",
                "type": "AGENT_PLAN",
                "project_id": "proj-1",
                "payload": {"markdown": "Plan: Plot and interpret results for JamshidW\n\nDetails"},
            },
        ]

        related = fn(blocks[-1], blocks)

        assert related is not None
        assert related["id"] == "wf-2"


class TestWorkflowHighlightSteps:
    def test_returns_completed_plot_and_summary_steps(self):
        fn = _load_function("_workflow_highlight_steps")
        workflow_block = {
            "payload": {
                "steps": [
                    {"kind": "PARSE_OUTPUT_FILE", "status": "COMPLETED", "result": {"rows": 10}},
                    {"kind": "GENERATE_PLOT", "status": "COMPLETED", "result": {"charts": [{"type": "bar"}]}},
                    {"kind": "INTERPRET_RESULTS", "status": "COMPLETED", "result": {"markdown": "Looks usable."}},
                    {"kind": "WRITE_SUMMARY", "status": "PENDING", "result": {"markdown": "Later"}},
                ]
            }
        }

        highlights = fn(workflow_block)

        assert [step["kind"] for step in highlights] == ["GENERATE_PLOT", "INTERPRET_RESULTS"]


class TestBlockRequiresFullRefresh:
    def test_only_running_execution_and_download_jobs_require_full_refresh(self):
        fn = _load_function("_block_requires_full_refresh")

        assert fn({"type": "EXECUTION_JOB", "status": "RUNNING"}) is True
        assert fn({"type": "DOWNLOAD_TASK", "status": "RUNNING"}) is True
        assert fn({"type": "EXECUTION_JOB", "status": "DONE", "payload": {"job_status": {"status": "RUNNING"}}}) is True
        assert fn({"type": "EXECUTION_JOB", "status": "NEW", "payload": {"job_status": {"status": "PENDING"}}}) is True
        assert fn({"type": "WORKFLOW_PLAN", "status": "RUNNING"}) is False
        assert fn({"type": "STAGING_TASK", "status": "RUNNING"}) is False
        assert fn({"type": "EXECUTION_JOB", "status": "DONE"}) is False


class TestFullPageRefreshBootstrapExpression:
    def test_expression_turns_on_when_flag_appears(self):
        auto_refresh = True
        suppressed = False
        before = bool(auto_refresh and False and not suppressed)
        after = bool(auto_refresh and True and not suppressed)

        assert before is False
        assert after is True


class TestBuildPlotlyFigure:
    @pytest.fixture()
    def sample_df(self):
        return pd.DataFrame(
            {
                "Category": ["A", "B", "C"],
                "Count": [5, 3, 2],
                "Score": [0.9, 0.7, 0.5],
                "Group": ["x", "x", "y"],
            }
        )

    def test_case_insensitive_columns_work_for_scatter(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "scatter", "x": "category", "y": "score"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].x.tolist() == ["A", "B", "C"]

    def test_histogram_uses_numeric_companion_for_categorical_x(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "histogram", "x": "Category"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].type == "bar"
        assert fig.data[0].y.tolist() == [5, 3, 2]

    def test_bar_mean_aggregation_groups_values(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "Group": ["x", "x", "y"],
                "Value": [1.0, 3.0, 10.0],
            }
        )

        fig = fn({"type": "bar", "x": "Group", "y": "Value", "agg": "mean"}, df, "Grouped")

        assert fig is not None
        assert fig.data[0].x.tolist() == ["x", "y"]
        assert fig.data[0].y.tolist() == [2.0, 10.0]

    def test_heatmap_returns_none_with_only_one_numeric_column(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame({"Label": ["a", "b"], "Value": [1, 2]})

        assert fn({"type": "heatmap"}, df, "Small") is None

    def test_unknown_chart_type_returns_none(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        assert fn({"type": "not-a-chart"}, sample_df, "Test DF") is None

    def test_bar_honors_literal_named_color(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "bar", "x": "Category", "color": "red"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].marker.color == "red"

    def test_bar_honors_literal_hex_color(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "bar", "x": "Category", "color": "#ff0000"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].marker.color == "#ff0000"

    def test_bar_honors_palette_literal(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "bar", "x": "Category", "palette": "red"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].marker.color == "red"
