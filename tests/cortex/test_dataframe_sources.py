import json
from types import SimpleNamespace

from cortex.dataframe_sources import find_dataframe_parse_source, find_dataframe_payload


def _block(payload: dict):
    return SimpleNamespace(payload_json=json.dumps(payload))


def test_find_dataframe_payload_prefers_visible_match_over_hidden_shadow():
    history_blocks = [
        _block({
            "_dataframes": {
                "visible.tsv": {
                    "columns": ["value"],
                    "data": [{"value": 1}],
                    "row_count": 500,
                    "metadata": {
                        "df_id": 9,
                        "label": "visible.tsv",
                        "visible": True,
                        "row_count": 500,
                        "is_truncated": True,
                    },
                }
            }
        }),
        _block({
            "_dataframes": {
                "hidden-helper": {
                    "columns": ["value"],
                    "data": [{"value": 99}],
                    "row_count": 1,
                    "metadata": {
                        "df_id": 9,
                        "label": "hidden-helper",
                        "visible": False,
                        "row_count": 1,
                    },
                }
            }
        }),
    ]

    payload = find_dataframe_payload(9, history_blocks)

    assert payload is not None
    assert payload["metadata"]["label"] == "visible.tsv"
    assert payload["metadata"]["visible"] is True


def test_find_dataframe_parse_source_prefers_visible_match_over_hidden_shadow():
    history_blocks = [
        _block({
            "_dataframes": {
                "visible.tsv": {
                    "columns": ["value"],
                    "data": [{"value": 1}],
                    "row_count": 500,
                    "metadata": {
                        "df_id": 9,
                        "label": "visible.tsv",
                        "visible": True,
                        "row_count": 500,
                        "is_truncated": True,
                    },
                }
            },
            "_provenance": [{
                "source": "analyzer",
                "tool": "parse_csv_file",
                "success": True,
                "params": {
                    "file_path": "visible.tsv",
                    "work_dir": "/tmp/visible-workflow",
                },
            }],
        }),
        _block({
            "_dataframes": {
                "hidden-helper": {
                    "columns": ["value"],
                    "data": [{"value": 99}],
                    "row_count": 1,
                    "metadata": {
                        "df_id": 9,
                        "label": "hidden-helper",
                        "visible": False,
                        "row_count": 1,
                    },
                }
            },
            "_provenance": [{
                "source": "analyzer",
                "tool": "parse_csv_file",
                "success": True,
                "params": {
                    "file_path": "hidden-helper.tsv",
                    "work_dir": "/tmp/hidden-workflow",
                },
            }],
        }),
    ]

    parse_source = find_dataframe_parse_source(9, history_blocks)

    assert parse_source == {
        "file_path": "visible.tsv",
        "work_dir": "/tmp/visible-workflow",
    }