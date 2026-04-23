"""
Tests for cortex/app.py — _inject_job_context().

_inject_job_context augments the user message with conversational context.
Returns (augmented_message, injected_dataframes, debug_info).

Coverage areas:
  - No history → passthrough
  - Dogme skills → [CONTEXT: work_dir=...] injection
  - Dogme DF reference → [NOTE: DFN is in-memory...] injection
  - analyze_local_sample → collected parameter injection
  - ENCODE follow-up → previous DF row injection
  - ENCODE browsing command → no injection
  - ENCODE new query → no injection
"""

import json
import pytest
from unittest.mock import MagicMock

from cortex.context_injection import _inject_job_context


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


# ---------------------------------------------------------------------------
# No history → passthrough
# ---------------------------------------------------------------------------

class TestNoHistory:
    def test_returns_original_message(self):
        msg, dfs, debug = _inject_job_context("Hello", "welcome", None)
        assert msg == "Hello"
        assert dfs == {}

    def test_empty_history(self):
        msg, dfs, debug = _inject_job_context("Hello", "welcome", [])
        assert msg == "Hello"


# ---------------------------------------------------------------------------
# Dogme skills — work_dir injection
# ---------------------------------------------------------------------------

class TestDogmeInjection:
    def _blocks_with_job(self, work_dir="/data/proj/workflow1",
                         sample_name="test1", mode="DNA"):
        return [_make_block("EXECUTION_JOB", {
            "work_directory": work_dir,
            "run_uuid": "abc-123",
            "sample_name": sample_name,
            "mode": mode,
        })]

    def test_single_workflow_injection(self):
        blocks = self._blocks_with_job()
        history = _history([("user", "Run dogme"), ("assistant", "Job done")])
        msg, dfs, debug = _inject_job_context(
            "Analyze the results", "run_dogme_dna", history, blocks,
        )
        assert "[CONTEXT:" in msg
        assert "work_dir=/data/proj/workflow1" in msg
        assert "sample=test1" in msg

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
        history = _history([("user", "check results")])
        msg, dfs, debug = _inject_job_context(
            "Show me the results", "analyze_job_results", history, blocks,
        )
        assert "[CONTEXT:" in msg
        assert "workflows=[" in msg
        assert "workflow1" in msg
        assert "workflow2" in msg

    def test_uuid_fallback(self):
        history = _history([
            ("assistant", "Run UUID: 12345678-1234-1234-1234-123456789abc"),
        ])
        msg, dfs, debug = _inject_job_context(
            "Check results", "run_dogme_dna", history, None,
        )
        assert "run_uuid=" in msg

    def test_df_reference_note(self):
        blocks = [
            _make_block("EXECUTION_JOB", {
                "work_directory": "/data/proj/wf1",
                "run_uuid": "uuid-1",
            }),
            _make_block("AGENT_PLAN", {
                "_dataframes": {
                    "Summary": {
                        "columns": ["col_a", "col_b"],
                        "data": [{"col_a": "x", "col_b": "y"}],
                        "metadata": {"df_id": 2, "label": "Summary",
                                     "row_count": 1, "visible": True},
                    },
                },
            }),
        ]
        history = _history([("user", "check results")])
        msg, dfs, debug = _inject_job_context(
            "plot a histogram of DF2", "analyze_job_results", history, blocks,
        )
        assert "[NOTE: DF2 is an in-memory DataFrame" in msg
        assert "PLOT" in msg
        assert debug.get("df_note_injected") is True

    def test_implicit_viz_finds_latest_df(self):
        blocks = [
            _make_block("EXECUTION_JOB", {
                "work_directory": "/data/proj/wf1",
                "run_uuid": "uuid-1",
            }),
            _make_block("AGENT_PLAN", {
                "_dataframes": {
                    "Results": {
                        "columns": ["size"],
                        "data": [{"size": 100}],
                        "metadata": {"df_id": 5, "label": "Results",
                                     "row_count": 1, "visible": True},
                    },
                },
            }),
        ]
        history = _history([("user", "show results")])
        msg, dfs, debug = _inject_job_context(
            "plot a chart", "analyze_job_results", history, blocks,
        )
        assert "[NOTE: DF5 is an in-memory DataFrame" in msg


# ---------------------------------------------------------------------------
# analyze_local_sample — parameter collection injection
# ---------------------------------------------------------------------------

class TestLocalSampleInjection:
    def test_collects_dna_type(self):
        history = _history([
            ("user", "I want to analyze a DNA sample"),
            ("assistant", "What's the sample name?"),
        ])
        msg, dfs, debug = _inject_job_context(
            "It's called MySample", "analyze_local_sample", history,
        )
        assert "[CONTEXT:" in msg
        assert "sample_type=DNA" in msg

    def test_collects_all_four_params(self):
        history = _history([
            ("user", "Analyze a DNA sample called TestSample at path /data/pod5"),
            ("assistant", "What reference genome?"),
            ("user", "GRCh38"),
        ])
        msg, dfs, debug = _inject_job_context(
            "GRCh38", "analyze_local_sample", history,
        )
        assert "[CONTEXT:" in msg
        assert "ALL 4 parameters are collected" in msg
        assert "sample_name=TestSample" in msg
        assert "path=/data/pod5" in msg
        assert "sample_type=DNA" in msg
        assert "reference_genome=GRCh38" in msg
        assert "APPROVAL_NEEDED" in msg

    def test_cdna_detection(self):
        history = _history([
            ("user", "I have cDNA data"),
        ])
        msg, dfs, debug = _inject_job_context(
            "called my_cdna_sample", "analyze_local_sample", history,
        )
        assert "sample_type=CDNA" in msg

    def test_rna_detection(self):
        history = _history([
            ("user", "I have RNA data to process"),
        ])
        msg, dfs, debug = _inject_job_context(
            "Tell me more", "analyze_local_sample", history,
        )
        assert "sample_type=RNA" in msg

    def test_mouse_genome_inference(self):
        history = _history([
            ("user", "I have mouse DNA data at /home/data"),
        ])
        msg, dfs, debug = _inject_job_context(
            "What next?", "analyze_local_sample", history,
        )
        params = debug.get("collected_params", {})
        assert params.get("reference_genome") == "mm39"

    def test_no_params_no_context(self):
        history = _history([
            ("user", "Hello"),
            ("assistant", "How can I help?"),
        ])
        msg, dfs, debug = _inject_job_context(
            "What can you do?", "analyze_local_sample", history,
        )
        # No parameters detected → message should be unchanged
        assert msg == "What can you do?"


# ---------------------------------------------------------------------------
# ENCODE skills — browsing command bypass
# ---------------------------------------------------------------------------

class TestEncodeBrowsingBypass:
    def test_list_files_bypasses_injection(self):
        history = _history([
            ("user", "Search K562"),
            ("assistant", "Found experiments"),
        ])
        msg, dfs, debug = _inject_job_context(
            "list files", "ENCODE_Search", history,
        )
        assert msg == "list files"
        assert debug.get("context") == "browsing_command"

    def test_show_workflows_bypasses_injection(self):
        history = _history([
            ("user", "Search K562"),
            ("assistant", "Found experiments"),
        ])
        msg, dfs, debug = _inject_job_context(
            "show workflows", "ENCODE_Search", history,
        )
        assert msg == "show workflows"


# ---------------------------------------------------------------------------
# ENCODE skills — follow-up with DF injection
# ---------------------------------------------------------------------------

class TestEncodeFollowUp:
    def _encode_blocks_with_df(self, df_id=1, df_name="K562 experiments",
                                rows=None, cols=None, visible=True):
        if rows is None:
            rows = [
                {"accession": "ENCSR000AAA", "assay": "RNA-seq", "status": "released"},
                {"accession": "ENCSR000BBB", "assay": "ATAC-seq", "status": "released"},
                {"accession": "ENCSR000CCC", "assay": "RNA-seq", "status": "archived"},
            ]
        if cols is None:
            cols = ["accession", "assay", "status"]
        return [_make_block("AGENT_PLAN", {
            "markdown": "Results",
            "_dataframes": {
                df_name: {
                    "columns": cols,
                    "data": rows,
                    "metadata": {
                        "df_id": df_id,
                        "label": df_name,
                        "row_count": len(rows),
                        "visible": visible,
                    },
                },
            },
        })]

    def test_followup_injects_data(self):
        blocks = self._encode_blocks_with_df()
        history = _history([
            ("user", "Search K562"),
            ("assistant", "Found 3 experiments"),
        ])
        msg, dfs, debug = _inject_job_context(
            "How many of them are RNA-seq?", "ENCODE_Search", history, blocks,
        )
        assert "[PREVIOUS QUERY DATA:]" in msg
        assert "K562 experiments" in msg
        assert len(dfs) >= 1

    def test_explicit_df_reference(self):
        blocks = self._encode_blocks_with_df(df_id=3)
        history = _history([("user", "search"), ("assistant", "results")])
        # Use "those" as a referential word to trigger follow-up detection.
        # Note: the \bDF\s*\d+\b pattern in _followup_signals is case-sensitive
        # against msg_lower, so "DF3" alone doesn't trigger follow-up.
        msg, dfs, debug = _inject_job_context(
            "Show me those in DF3", "ENCODE_Search", history, blocks,
        )
        assert "[PREVIOUS QUERY DATA:]" in msg
        assert debug.get("source") == "encode_df_injection"

    def test_new_query_no_injection(self):
        blocks = self._encode_blocks_with_df()
        history = _history([
            ("user", "Search K562"),
            ("assistant", "Found experiments"),
        ])
        msg, dfs, debug = _inject_job_context(
            "Search ENCODE for HepG2 experiments", "ENCODE_Search", history, blocks,
        )
        # For a new query, no previous data should be injected
        assert "[PREVIOUS QUERY DATA:]" not in msg

    def test_explicit_accession_no_injection(self):
        blocks = self._encode_blocks_with_df()
        history = _history([
            ("user", "Search K562"),
            ("assistant", "Found experiments"),
        ])
        msg, dfs, debug = _inject_job_context(
            "Tell me about ENCSR999ZZZ", "ENCODE_Search", history, blocks,
        )
        # Has accession → not a follow-up on previous data
        assert "[PREVIOUS QUERY DATA:]" not in msg
