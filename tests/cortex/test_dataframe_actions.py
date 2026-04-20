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

    def test_build_overlap_dataframe_from_multiple_dataframes(self):
        history_blocks = [
            _df_block(
                1,
                "treated",
                [
                    {"gene_symbol": "TP53"},
                    {"gene_symbol": "EGFR"},
                    {"gene_symbol": "MYC"},
                ],
            ),
            _df_block(
                2,
                "control",
                [
                    {"gene_id": "EGFR"},
                    {"gene_id": "MYC"},
                    {"gene_id": "STAT1"},
                ],
            ),
        ]

        result = execute_local_dataframe_call(
            "build_overlap_dataframe",
            {
                "dfs": "DF1|DF2",
                "match_cols": "gene_symbol|gene_id",
                "labels": "Treated|Control",
                "plot_type": "venn",
            },
            history_blocks=history_blocks,
        )

        assert result["columns"] == ["match_key", "Treated", "Control"]
        assert result["row_count"] == 4
        assert result["metadata"]["operation"] == "build_overlap_dataframe"
        assert result["metadata"]["source_df_ids"] == [1, 2]
        assert result["metadata"]["set_columns"] == ["Treated", "Control"]
        rows = {row["match_key"]: row for row in result["data"]}
        assert rows["EGFR"] == {"match_key": "EGFR", "Treated": True, "Control": True}
        assert rows["TP53"] == {"match_key": "TP53", "Treated": True, "Control": False}
        assert rows["STAT1"] == {"match_key": "STAT1", "Treated": False, "Control": True}

    def test_build_overlap_dataframe_from_single_dataframe_samples(self):
        history_blocks = [
            _df_block(
                3,
                "genes",
                [
                    {"sample": "tumor", "gene": "TP53"},
                    {"sample": "tumor", "gene": "EGFR"},
                    {"sample": "normal", "gene": "EGFR"},
                    {"sample": "normal", "gene": "STAT1"},
                ],
            ),
        ]

        result = execute_local_dataframe_call(
            "build_overlap_dataframe",
            {
                "df_id": 3,
                "sample_col": "sample",
                "sample_values": "tumor|normal",
                "match_on": "gene",
                "labels": "Tumor|Normal",
            },
            history_blocks=history_blocks,
        )

        assert result["columns"] == ["match_key", "Tumor", "Normal"]
        assert result["row_count"] == 3
        assert result["metadata"]["source_df_id"] == 3
        assert result["metadata"]["sample_col"] == "sample"
        assert result["metadata"]["sample_values"] == ["tumor", "normal"]
        rows = {row["match_key"]: row for row in result["data"]}
        assert rows["TP53"] == {"match_key": "TP53", "Tumor": True, "Normal": False}
        assert rows["EGFR"] == {"match_key": "EGFR", "Tumor": True, "Normal": True}
        assert rows["STAT1"] == {"match_key": "STAT1", "Tumor": False, "Normal": True}


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