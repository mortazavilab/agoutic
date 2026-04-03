import json
from unittest.mock import MagicMock

from cortex.tool_dispatch import build_calls_by_source


def _make_block(payload: dict):
    blk = MagicMock()
    blk.type = "EXECUTION_JOB"
    blk.payload_json = json.dumps(payload)
    return blk


class TestToolDispatchPathResolution:
    def test_auto_call_find_file_keeps_explicit_workflow_path(self):
        blocks = [
            _make_block(
                {
                    "work_directory": "/media/backup_disk/agoutic_code/skills/reconcile_bams/scripts",
                    "run_uuid": "abc-123",
                }
            )
        ]
        auto_calls = [
            {
                "source_type": "service",
                "source_key": "analyzer",
                "tool": "find_file",
                "params": {
                    "file_name": "reconciled_novelty_by_sample.csv",
                    "work_dir": "/media/backup_disk/agoutic_root/users/elnaz-a/c2c12-reconciled/workflow2",
                },
                "_chain": "parse_csv_file",
            }
        ]

        calls_by_source = build_calls_by_source(
            data_call_matches=[],
            legacy_encode_matches=[],
            legacy_analysis_matches=[],
            auto_calls=auto_calls,
            has_any_tags=False,
            user_id="u-1",
            project_id="proj-1",
            user_message="Parse reconciled_novelty_by_sample.csv",
            conversation_history=[],
            history_blocks=blocks,
            project_dir="",
            active_skill="analyze_job_results",
        )

        analyzer_calls = calls_by_source["analyzer"]
        assert len(analyzer_calls) == 1
        assert analyzer_calls[0]["tool"] == "find_file"
        assert analyzer_calls[0]["params"]["work_dir"] == "/media/backup_disk/agoutic_root/users/elnaz-a/c2c12-reconciled/workflow2"
        assert "run_uuid" not in analyzer_calls[0]["params"]
