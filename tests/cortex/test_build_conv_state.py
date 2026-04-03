"""
Tests for cortex/app.py — _build_conversation_state() and _extract_job_context_from_history().

_build_conversation_state has two paths:
  - Fast path: last AGENT_PLAN block has cached "state" dict
  - Slow path: reconstruct from all blocks (EXECUTION_JOB, AGENT_PLAN) + history

_extract_job_context_from_history scans blocks/conversation for job context:
  - Primary: EXECUTION_JOB blocks (authoritative)
  - Fallback: parse conversation text for UUID/work_dir (legacy)
"""

import json
import pytest
from unittest.mock import MagicMock

from cortex.conversation_state import _build_conversation_state, _extract_job_context_from_history


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(block_type: str, payload: dict) -> MagicMock:
    blk = MagicMock()
    blk.type = block_type
    blk.payload_json = json.dumps(payload)
    return blk


def _history(messages: list[tuple[str, str]]) -> list[dict]:
    return [{"role": r, "content": c} for r, c in messages]


# ===========================================================================
# _extract_job_context_from_history
# ===========================================================================

class TestExtractJobContext:
    def test_empty_inputs(self):
        ctx = _extract_job_context_from_history(None, None)
        assert ctx == {}

    def test_execution_job_block(self):
        blocks = [_make_block("EXECUTION_JOB", {
            "work_directory": "/data/proj/workflow1",
            "run_uuid": "abc-123",
            "sample_name": "sample1",
            "mode": "DNA",
        })]
        ctx = _extract_job_context_from_history(None, history_blocks=blocks)
        assert ctx["work_dir"] == "/data/proj/workflow1"
        assert ctx["run_uuid"] == "abc-123"
        assert len(ctx["workflows"]) == 1
        assert ctx["workflows"][0]["sample_name"] == "sample1"

    def test_multiple_workflows(self):
        blocks = [
            _make_block("EXECUTION_JOB", {
                "work_directory": "/data/proj/workflow1",
                "run_uuid": "uuid-1",
                "sample_name": "s1",
                "mode": "DNA",
            }),
            _make_block("EXECUTION_JOB", {
                "work_directory": "/data/proj/workflow2",
                "run_uuid": "uuid-2",
                "sample_name": "s2",
                "mode": "RNA",
            }),
        ]
        ctx = _extract_job_context_from_history(None, history_blocks=blocks)
        # Most recent workflow wins
        assert ctx["work_dir"] == "/data/proj/workflow2"
        assert ctx["run_uuid"] == "uuid-2"
        assert len(ctx["workflows"]) == 2

    def test_fallback_text_uuid(self):
        history = _history([
            ("assistant", "Job submitted! Run UUID: 12345678-1234-1234-1234-123456789abc"),
        ])
        ctx = _extract_job_context_from_history(history, history_blocks=None)
        assert ctx.get("run_uuid") == "12345678-1234-1234-1234-123456789abc"

    def test_fallback_text_work_dir(self):
        history = _history([
            ("assistant", "Job started.\nWork Directory: /data/users/eli/proj1/workflow1"),
        ])
        ctx = _extract_job_context_from_history(history, history_blocks=None)
        assert ctx.get("work_dir") == "/data/users/eli/proj1/workflow1"

    def test_blocks_take_priority_over_text(self):
        blocks = [_make_block("EXECUTION_JOB", {
            "work_directory": "/data/block_dir",
            "run_uuid": "block-uuid",
        })]
        history = _history([
            ("assistant", "Work Directory: /data/text_dir"),
        ])
        ctx = _extract_job_context_from_history(history, history_blocks=blocks)
        assert ctx["work_dir"] == "/data/block_dir"

    def test_non_execution_blocks_ignored(self):
        blocks = [
            _make_block("AGENT_PLAN", {"markdown": "Planning..."}),
            _make_block("USER_MESSAGE", {"markdown": "Hello"}),
        ]
        ctx = _extract_job_context_from_history(None, history_blocks=blocks)
        assert ctx == {}

    def test_script_jobs_deprioritised(self):
        """Pipeline (dogme) work_dir should win over script (reconcile_bams) work_dir."""
        blocks = [
            _make_block("EXECUTION_JOB", {
                "work_directory": "/data/proj/workflow2",
                "run_uuid": "uuid-dogme",
                "sample_name": "sample1",
                "mode": "RNA",
                "run_type": "dogme",
            }),
            _make_block("EXECUTION_JOB", {
                "work_directory": "/opt/agoutic_code/skills/reconcile_bams/scripts",
                "run_uuid": "uuid-reconcile",
                "sample_name": "",
                "mode": "",
                "run_type": "script",
            }),
        ]
        ctx = _extract_job_context_from_history(None, history_blocks=blocks)
        # Should pick the dogme workflow, not the reconcile script
        assert ctx["work_dir"] == "/data/proj/workflow2"
        assert ctx["run_uuid"] == "uuid-dogme"
        # Both workflows should still be listed
        assert len(ctx["workflows"]) == 2

    def test_only_script_jobs_still_returns_context(self):
        """When only script jobs exist, fall back to them rather than returning nothing."""
        blocks = [
            _make_block("EXECUTION_JOB", {
                "work_directory": "/opt/scripts/reconcile",
                "run_uuid": "uuid-script",
                "run_type": "script",
            }),
        ]
        ctx = _extract_job_context_from_history(None, history_blocks=blocks)
        assert ctx["work_dir"] == "/opt/scripts/reconcile"
        assert ctx["run_uuid"] == "uuid-script"


# ===========================================================================
# _build_conversation_state — slow path
# ===========================================================================

class TestBuildConvStateSlowPath:
    def test_empty_returns_default_state(self):
        state = _build_conversation_state("welcome", None)
        assert state.active_skill == "welcome"
        assert state.workflows == []
        assert state.known_dataframes == []

    def test_extracts_workflows_from_blocks(self):
        blocks = [
            _make_block("EXECUTION_JOB", {
                "work_directory": "/data/proj/workflow1",
                "run_uuid": "uuid-1",
                "sample_name": "s1",
                "mode": "DNA",
            }),
        ]
        state = _build_conversation_state("run_dogme_dna", None, history_blocks=blocks)
        assert len(state.workflows) == 1
        assert state.work_dir == "/data/proj/workflow1"
        assert state.sample_name == "s1"
        assert state.sample_type == "DNA"

    def test_extracts_accessions_from_history(self):
        history = _history([
            ("user", "Search K562"),
            ("assistant", "Found experiment ENCSR000AAA with file ENCFF001XYZ"),
        ])
        state = _build_conversation_state("ENCODE_Search", history)
        assert state.active_experiment == "ENCSR000AAA"
        assert state.active_file == "ENCFF001XYZ"

    def test_extracts_dataframes_from_blocks(self):
        blocks = [
            _make_block("AGENT_PLAN", {
                "markdown": "Here are results",
                "_dataframes": {
                    "K562 experiments": {
                        "columns": ["accession", "assay"],
                        "data": [["ENCSR000AAA", "RNA-seq"]],
                        "metadata": {"df_id": 1, "label": "K562 experiments",
                                     "row_count": 1, "visible": True},
                    },
                },
            }),
        ]
        state = _build_conversation_state("ENCODE_Search", None, history_blocks=blocks)
        assert len(state.known_dataframes) == 1
        assert "DF1" in state.known_dataframes[0]
        assert state.latest_dataframe == "DF1"

    def test_collected_params_for_local_sample(self):
        history = _history([
            ("user", "I want to analyze a DNA sample called MySample at path /data/pod5"),
            ("assistant", "OK, what reference genome?"),
            ("user", "GRCh38"),
        ])
        state = _build_conversation_state("analyze_local_sample", history)
        params = state.collected_params
        assert params.get("sample_type") == "DNA"
        assert params.get("reference_genome") == "GRCh38"

    def test_multiple_dataframes_tracked(self):
        blocks = [
            _make_block("AGENT_PLAN", {
                "_dataframes": {
                    "K562": {
                        "columns": ["a"],
                        "data": [["x"]],
                        "metadata": {"df_id": 1, "label": "K562",
                                     "row_count": 1, "visible": True},
                    },
                },
            }),
            _make_block("AGENT_PLAN", {
                "_dataframes": {
                    "HepG2": {
                        "columns": ["a"],
                        "data": [["y"]],
                        "metadata": {"df_id": 2, "label": "HepG2",
                                     "row_count": 1, "visible": True},
                    },
                },
            }),
        ]
        state = _build_conversation_state("ENCODE_Search", None, history_blocks=blocks)
        assert len(state.known_dataframes) == 2
        assert state.latest_dataframe == "DF2"


# ===========================================================================
# _build_conversation_state — fast path (cached state)
# ===========================================================================

class TestBuildConvStateFastPath:
    def _cached_state_block(self, state_dict: dict, extra_payload: dict | None = None):
        payload = {"state": state_dict}
        if extra_payload:
            payload.update(extra_payload)
        return _make_block("AGENT_PLAN", payload)

    def test_fast_path_uses_cached_state(self):
        cached = {
            "active_skill": "old_skill",
            "work_dir": "/data/proj/wf1",
            "sample_name": "cached_sample",
            "workflows": [{"work_dir": "/data/proj/wf1", "sample_name": "cached_sample"}],
        }
        blocks = [self._cached_state_block(cached)]
        state = _build_conversation_state("ENCODE_Search", None, history_blocks=blocks)
        # active_skill should be overridden to the passed-in value
        assert state.active_skill == "ENCODE_Search"
        # Other fields come from cache
        assert state.work_dir == "/data/proj/wf1"
        assert state.sample_name == "cached_sample"

    def test_fast_path_patches_latest_df(self):
        cached = {
            "active_skill": "ENCODE_Search",
            "latest_dataframe": "DF1",
            "known_dataframes": ["DF1 (K562, 5 rows)"],
        }
        extra = {
            "_dataframes": {
                "HepG2": {
                    "columns": ["a"],
                    "data": [["x"]],
                    "metadata": {"df_id": 3, "label": "HepG2",
                                 "row_count": 1, "visible": True},
                },
            },
        }
        blocks = [self._cached_state_block(cached, extra)]
        state = _build_conversation_state("ENCODE_Search", None, history_blocks=blocks)
        assert state.latest_dataframe == "DF3"

    def test_fast_path_refreshes_params_for_local_sample(self):
        cached = {
            "active_skill": "analyze_local_sample",
            "collected_params": {"sample_name": "OldSample", "sample_type": "DNA"},
        }
        history = _history([
            ("user", "I want to analyze a sample called NewSample"),
        ])
        blocks = [self._cached_state_block(cached)]
        state = _build_conversation_state("analyze_local_sample", history, history_blocks=blocks)
        # Should refresh from conversation history
        assert state.collected_params.get("sample_name") == "NewSample"
        assert state.sample_name == "NewSample"

    def test_fast_path_only_checks_last_agent_plan(self):
        """Only the most recent AGENT_PLAN is checked for cached state."""
        old_cached = {"active_skill": "welcome", "work_dir": "/old"}
        new_cached = {"active_skill": "run_dogme_dna", "work_dir": "/new"}
        blocks = [
            self._cached_state_block(old_cached),
            _make_block("USER_MESSAGE", {"markdown": "Next step"}),
            self._cached_state_block(new_cached),
        ]
        state = _build_conversation_state("run_dogme_dna", None, history_blocks=blocks)
        assert state.work_dir == "/new"
