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

        assert value == "2026-03-19T23:00:00Z"

    def test_falls_back_to_persisted_timestamp_when_live_status_fails(self):
        fn = _load_function("_job_status_updated_at")

        value = fn("2026-03-19T22:50:00Z", False)

        assert value == "2026-03-19T22:50:00Z"

    def test_returns_none_when_no_timestamp_exists(self):
        fn = _load_function("_job_status_updated_at")

        value = fn(None, False)

        assert value is None


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
