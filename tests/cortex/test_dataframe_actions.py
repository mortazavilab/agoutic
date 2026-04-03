import json
from types import SimpleNamespace

from cortex.dataframe_actions import build_auto_dataframe_call, execute_local_dataframe_call


def _df_block(df_id: int, label: str, rows: list[dict]):
    return SimpleNamespace(
        type="AGENT_PLAN",
        payload_json=json.dumps({
            "_dataframes": {
                label: {
                    "columns": list(rows[0].keys()) if rows else [],
                    "data": rows,
                    "metadata": {
                        "df_id": df_id,
                        "label": label,
                        "row_count": len(rows),
                        "visible": True,
                    },
                },
            },
        }),
    )


class TestExecuteLocalDataframeCall:
    def test_melt_dataframe_returns_long_payload(self):
        history_blocks = [
            _df_block(
                1,
                "novelty",
                [{"sample": "s1", "KNOWN": 10, "NIC": 5}, {"sample": "s2", "KNOWN": 11, "NIC": 6}],
            ),
        ]
        result = execute_local_dataframe_call(
            "melt_dataframe",
            {"df_id": 1, "id_vars": ["sample"], "var_name": "modification", "value_name": "reads"},
            history_blocks=history_blocks,
        )

        assert result["columns"] == ["sample", "modification", "reads"]
        assert result["row_count"] == 4
        assert result["metadata"]["operation"] == "melt_dataframe"
        assert result["metadata"]["source_df_id"] == 1

    def test_filter_dataframe_matches_columns_case_insensitively(self):
        history_blocks = [
            _df_block(
                2,
                "counts",
                [{"Sample": "a", "Reads": 10}, {"Sample": "b", "Reads": 50}],
            ),
        ]
        result = execute_local_dataframe_call(
            "filter_dataframe",
            {"df_id": 2, "column": "reads", "operator": ">", "value": 20},
            history_blocks=history_blocks,
        )

        assert result["row_count"] == 1
        assert result["data"][0]["Sample"] == "b"


class TestBuildAutoDataframeCall:
    def test_builds_melt_call_from_reshape_request(self):
        call = build_auto_dataframe_call(
            "reshape DF1 into long format with columns sample, modification, reads",
        )

        assert call is not None
        assert call["source_key"] == "cortex"
        assert call["tool"] == "melt_dataframe"
        assert call["params"]["df_id"] == 1
        assert call["params"]["var_name"] == "modification"
        assert call["params"]["value_name"] == "reads"