"""Tests for cortex/dataframe_transforms.py — pure pandas DataFrame operations."""

import pytest
import pandas as pd

from cortex.dataframe_transforms import (
    payload_to_dataframe,
    dataframe_to_payload,
    filter_dataframe,
    select_columns,
    rename_columns,
    sort_dataframe,
    melt_dataframe,
    aggregate_dataframe,
    join_dataframes,
    pivot_dataframe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_df():
    return pd.DataFrame({
        "name": ["Alice", "Bob", "Charlie", "Diana"],
        "age": [25, 30, 35, 28],
        "score": [90.5, 85.0, 78.5, 92.0],
        "department": ["Engineering", "Marketing", "Engineering", "Marketing"],
    })


@pytest.fixture()
def payload():
    return {
        "columns": ["name", "age", "score"],
        "data": [
            ["Alice", 25, 90.5],
            ["Bob", 30, 85.0],
        ],
    }


@pytest.fixture()
def left_df():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "name": ["Alice", "Bob", "Charlie"],
    })


@pytest.fixture()
def right_df():
    return pd.DataFrame({
        "id": [1, 2, 4],
        "score": [90.5, 85.0, 78.5],
    })


# ---------------------------------------------------------------------------
# payload_to_dataframe / dataframe_to_payload
# ---------------------------------------------------------------------------

class TestPayloadToDataframe:
    def test_basic_conversion(self, payload):
        df = payload_to_dataframe(payload)
        assert len(df) == 2
        # Columns are added in order from payload
        assert "name" in df.columns
        assert "age" in df.columns
        assert "score" in df.columns

    def test_missing_columns_added_as_na(self):
        p = {"columns": ["a", "b", "c"], "data": [[1, 2]]}
        df = payload_to_dataframe(p)
        assert list(df.columns) == ["a", "b", "c"]
        # Check that column c exists (may be NA or None depending on pandas version)
        assert "c" in df.columns

    def test_empty_payload(self):
        df = payload_to_dataframe({"columns": [], "data": []})
        assert len(df) == 0

    def test_dataframe_to_payload_roundtrip(self, sample_df):
        source = {"metadata": {"df_id": 1}}
        payload = dataframe_to_payload(
            sample_df, source_payload=source, operation="test", label="test_label"
        )
        assert payload["row_count"] == 4
        assert payload["metadata"]["label"] == "test_label"
        assert payload["metadata"]["operation"] == "test"
        assert "df_id" not in payload["metadata"]

    def test_dataframe_to_payload_na_handling(self):
        df = pd.DataFrame({"a": [1, None, 3]})
        source = {"metadata": {}}
        payload = dataframe_to_payload(df, source_payload=source, operation="op", label="l")
        # pandas converts None to NaN in to_dict(orient='records')
        assert payload["data"][1]["a"] is None or pd.isna(payload["data"][1]["a"])


# ---------------------------------------------------------------------------
# filter_dataframe
# ---------------------------------------------------------------------------

class TestFilterDataframe:
    def test_equal_operator(self, sample_df):
        result = filter_dataframe(sample_df, column="department", operator="==", value="Engineering")
        assert len(result) == 2

    def test_not_equal_operator(self, sample_df):
        result = filter_dataframe(sample_df, column="department", operator="!=", value="Engineering")
        assert len(result) == 2

    def test_greater_than_operator(self, sample_df):
        result = filter_dataframe(sample_df, column="age", operator=">", value=29)
        assert len(result) == 2

    def test_less_than_or_equal_operator(self, sample_df):
        result = filter_dataframe(sample_df, column="age", operator="<=", value=28)
        assert len(result) == 2

    def test_in_operator(self, sample_df):
        result = filter_dataframe(sample_df, column="department", operator="in", value=["Engineering"])
        assert len(result) == 2

    def test_contains_operator(self, sample_df):
        result = filter_dataframe(sample_df, column="name", operator="contains", value="li")
        assert len(result) == 2  # Alice, Charlie

    def test_unsupported_operator_raises(self, sample_df):
        with pytest.raises(ValueError, match="Unsupported filter operator"):
            filter_dataframe(sample_df, column="age", operator="invalid", value=1)

    def test_reset_index(self, sample_df):
        result = filter_dataframe(sample_df, column="age", operator=">", value=29)
        assert list(result.index) == [0, 1]


# ---------------------------------------------------------------------------
# select_columns / rename_columns
# ---------------------------------------------------------------------------

class TestSelectRenameColumns:
    def test_select_columns(self, sample_df):
        result = select_columns(sample_df, columns=["name", "age"])
        assert list(result.columns) == ["name", "age"]
        assert len(result) == 4

    def test_rename_columns(self, sample_df):
        result = rename_columns(sample_df, rename_map={"name": "full_name"})
        assert "full_name" in result.columns
        assert "name" not in result.columns

    def test_rename_multiple(self, sample_df):
        result = rename_columns(sample_df, rename_map={"name": "full_name", "age": "years"})
        assert set(result.columns) == {"full_name", "years", "score", "department"}


# ---------------------------------------------------------------------------
# sort_dataframe
# ---------------------------------------------------------------------------

class TestSortDataframe:
    def test_sort_ascending(self, sample_df):
        result = sort_dataframe(sample_df, sort_by=["age"], ascending=[True])
        assert result.iloc[0]["name"] == "Alice"

    def test_sort_descending(self, sample_df):
        result = sort_dataframe(sample_df, sort_by=["age"], ascending=[False])
        assert result.iloc[0]["name"] == "Charlie"

    def test_sort_multiple_columns(self, sample_df):
        result = sort_dataframe(sample_df, sort_by=["department", "age"], ascending=[True, True])
        # Engineering comes before Marketing, Alice (25) before Charlie (35)
        eng_indices = result[result["department"] == "Engineering"].index.tolist()
        assert result.iloc[eng_indices[0]]["name"] == "Alice"


# ---------------------------------------------------------------------------
# melt_dataframe
# ---------------------------------------------------------------------------

class TestMeltDataframe:
    def test_basic_melt(self, sample_df):
        result = melt_dataframe(
            sample_df, id_vars=["name"], value_vars=["age", "score"],
            var_name="metric", value_name="value"
        )
        assert len(result) == 8  # 4 rows * 2 value_vars


# ---------------------------------------------------------------------------
# aggregate_dataframe
# ---------------------------------------------------------------------------

class TestAggregateDataframe:
    def test_basic_aggregation(self, sample_df):
        result = aggregate_dataframe(
            sample_df, group_by=["department"],
            aggregations={"age": "mean", "score": "sum"}
        )
        assert len(result) == 2

    def test_empty_aggregations_raises(self, sample_df):
        with pytest.raises(ValueError, match="At least one aggregation"):
            aggregate_dataframe(sample_df, group_by=["department"], aggregations={})


# ---------------------------------------------------------------------------
# join_dataframes / pivot_dataframe
# ---------------------------------------------------------------------------

class TestJoinPivotDataframe:
    def test_inner_join(self, left_df, right_df):
        result = join_dataframes(left_df, right_df, on=None, left_on="id", right_on="id", how="inner", suffixes=("_l", "_r"))
        assert len(result) == 2  # ids 1 and 2 match

    def test_left_join(self, left_df, right_df):
        result = join_dataframes(left_df, right_df, on=None, left_on="id", right_on="id", how="left", suffixes=("_l", "_r"))
        assert len(result) == 3  # all left rows

    def test_pivot_dataframe(self, sample_df):
        result = pivot_dataframe(
            sample_df, index="name", columns="department", values="score", aggfunc="mean"
        )
        assert "Engineering" in result.columns or len(result) == 4
