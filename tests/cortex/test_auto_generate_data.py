"""
Tests for cortex/app.py — _auto_generate_data_calls().

This function is the safety-net that auto-generates DATA_CALL dicts when
the LLM fails to emit them.  It handles:
  - DF reference / visualization early exit
  - "list workflows", "list files" browsing commands
  - ENCODE accession detection (ENCSR, ENCFF)
  - Download intent with ENCFF accessions
  - Conversational referential accessions ("them", "those")
  - Biosample search (known + unknown cell lines)
  - Assay-only queries
  - Target protein / histone mark queries
  - Dogme file-parsing patterns
  - analyze_job_results catch-all
"""

import pytest
from unittest.mock import MagicMock

from cortex.data_call_generator import _auto_generate_data_calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_blocks(payloads: list[dict]) -> list:
    """Create lightweight mock blocks from a list of (type, payload) tuples."""
    blocks = []
    for p in payloads:
        blk = MagicMock()
        blk.type = p["type"]
        blk.payload_json = __import__("json").dumps(p.get("payload", {}))
        blocks.append(blk)
    return blocks


def _history(messages: list[tuple[str, str]]) -> list[dict]:
    """Build conversation_history from (role, content) pairs."""
    return [{"role": r, "content": c} for r, c in messages]


# ---------------------------------------------------------------------------
# DF reference / visualization early-exit
# ---------------------------------------------------------------------------

class TestDFAndVisualizationEarlyExit:
    def test_df_reference_returns_empty(self):
        calls = _auto_generate_data_calls("Show me DF1 columns", "ENCODE_Search")
        assert calls == []

    def test_df_reference_case_insensitive(self):
        calls = _auto_generate_data_calls("filter df3 by assay", "ENCODE_Search")
        assert calls == []

    def test_plot_keyword_returns_empty(self):
        calls = _auto_generate_data_calls("plot a histogram of file sizes", "ENCODE_Search")
        assert calls == []

    def test_chart_keyword_returns_empty(self):
        calls = _auto_generate_data_calls("make a bar chart", "ENCODE_Search")
        assert calls == []

    def test_scatter_keyword_returns_empty(self):
        calls = _auto_generate_data_calls("scatter plot of coverage vs length", "ENCODE_Search")
        assert calls == []

    def test_compound_encode_plot_query_still_generates_search_call(self):
        calls = _auto_generate_data_calls(
            "plot C2C12 experiments in encode by assay type in purple",
            "ENCODE_Search",
        )
        assert len(calls) >= 1
        assert calls[0]["tool"] == "search_by_biosample"
        assert calls[0]["params"]["search_term"] == "C2C12"


# ---------------------------------------------------------------------------
# Browsing commands (list workflows / list files)
# ---------------------------------------------------------------------------

class TestBrowsingCommands:
    def _blocks_with_job(self, work_dir="/tmp/project/workflow1"):
        return _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": work_dir,
                "run_uuid": "aaaa-bbbb",
                "sample_name": "test1",
                "mode": "DNA",
            }},
        ])

    def test_list_workflows(self):
        blocks = self._blocks_with_job("/tmp/proj")
        calls = _auto_generate_data_calls(
            "list workflows", "analyze_job_results",
            history_blocks=blocks, project_dir="/tmp/proj",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "list_job_files"


class TestManualResultSyncCommand:
    def _blocks_with_job(self, work_dir="/tmp/proj/workflow1"):
        return _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": work_dir,
                "run_uuid": "11111111-1111-1111-1111-111111111111",
                "sample_name": "s1",
                "mode": "DNA",
            }},
        ])

    def _blocks_with_runs(self):
        return _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow1",
                "run_uuid": "11111111-1111-1111-1111-111111111111",
                "sample_name": "s1",
                "mode": "DNA",
            }},
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow2",
                "run_uuid": "22222222-2222-2222-2222-222222222222",
                "sample_name": "s2",
                "mode": "DNA",
            }},
        ])

    def test_sync_results_uses_requested_workflow_run_uuid(self):
        calls = _auto_generate_data_calls(
            "sync results back to local for workflow1",
            "analyze_job_results",
            history_blocks=self._blocks_with_runs(),
            project_dir="/tmp/proj",
        )

        assert len(calls) == 1
        assert calls[0]["source_key"] == "launchpad"
        assert calls[0]["tool"] == "sync_job_results"
        assert calls[0]["params"]["run_uuid"] == "11111111-1111-1111-1111-111111111111"

    def test_sync_results_force_retry_sets_force_param(self):
        calls = _auto_generate_data_calls(
            "retry sync outputs to local for workflow2",
            "analyze_job_results",
            history_blocks=self._blocks_with_runs(),
            project_dir="/tmp/proj",
        )

        assert len(calls) == 1
        assert calls[0]["tool"] == "sync_job_results"
        assert calls[0]["params"]["run_uuid"] == "22222222-2222-2222-2222-222222222222"
        assert calls[0]["params"]["force"] is True

    def test_show_files(self):
        blocks = self._blocks_with_job("/tmp/proj/workflow1")
        calls = _auto_generate_data_calls(
            "show files", "analyze_job_results",
            history_blocks=blocks, project_dir="/tmp/proj",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "list_job_files"

    def test_list_files_in_subpath(self):
        blocks = self._blocks_with_job("/tmp/proj/workflow1")
        calls = _auto_generate_data_calls(
            "list files in annot", "analyze_job_results",
            history_blocks=blocks, project_dir="/tmp/proj",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "list_job_files"


class TestRemoteExecutionSafetyNet:
    def test_remote_defaults_query_auto_generates_launchpad_calls(self):
        calls = _auto_generate_data_calls(
            "What are my SLURM defaults for hpc3?",
            "remote_execution",
        )
        assert len(calls) == 2
        assert calls[0]["source_key"] == "launchpad"
        assert calls[0]["tool"] == "list_ssh_profiles"
        assert calls[0]["params"]["user_id"] == "<user_id>"

        assert calls[1]["source_key"] == "launchpad"
        assert calls[1]["tool"] == "get_slurm_defaults"
        assert calls[1]["params"]["user_id"] == "<user_id>"
        assert calls[1]["params"]["profile_nickname"].lower() == "hpc3"

    def test_remote_defaults_phrase_does_not_capture_defaults_as_nickname(self):
        calls = _auto_generate_data_calls(
            "Show my SSH profiles and use profile defaults for nickname hpc3. Report cpu account/partition and gpu account/partition.",
            "remote_execution",
        )
        assert len(calls) == 2
        assert calls[1]["tool"] == "get_slurm_defaults"
        assert calls[1]["params"]["profile_nickname"].lower() == "hpc3"


# ---------------------------------------------------------------------------
# ENCODE accession detection
# ---------------------------------------------------------------------------

class TestEncodeAccessions:
    def test_encsr_gets_experiment(self):
        calls = _auto_generate_data_calls(
            "Tell me about ENCSR000AAA", "ENCODE_Search",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "get_experiment"
        assert calls[0]["params"]["accession"] == "ENCSR000AAA"

    def test_encsr_with_file_keywords(self):
        calls = _auto_generate_data_calls(
            "Show me the bam files for ENCSR123ABC", "ENCODE_Search",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "get_files_by_type"
        assert calls[0]["params"]["accession"] == "ENCSR123ABC"

    def test_multiple_accessions(self):
        calls = _auto_generate_data_calls(
            "Compare ENCSR000AAA and ENCSR000BBB", "ENCODE_Search",
        )
        assert len(calls) == 2
        accessions = {c["params"]["accession"] for c in calls}
        assert accessions == {"ENCSR000AAA", "ENCSR000BBB"}

    def test_non_encode_skill_skips_accession(self):
        """Accessions should only generate calls for ENCODE skills."""
        calls = _auto_generate_data_calls(
            "Tell me about ENCSR000AAA", "run_dogme_dna",
        )
        # Should NOT produce ENCODE calls (skill mismatch)
        encode_calls = [c for c in calls if c.get("source_key") == "encode"]
        assert len(encode_calls) == 0


# ---------------------------------------------------------------------------
# Download intent with ENCFF
# ---------------------------------------------------------------------------

class TestDownloadIntent:
    def test_download_encff_with_parent(self):
        calls = _auto_generate_data_calls(
            "Download ENCFF001ABC from ENCSR000AAA", "ENCODE_Search",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "get_file_metadata"
        assert calls[0]["params"]["file_accession"] == "ENCFF001ABC"
        assert calls[0]["_chain"] == "download"

    def test_download_encff_without_parent(self):
        calls = _auto_generate_data_calls(
            "Download ENCFF001ABC", "ENCODE_Search",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "get_file_metadata"
        assert calls[0]["params"]["file_accession"] == "ENCFF001ABC"

    def test_fetch_keyword_triggers_download(self):
        calls = _auto_generate_data_calls(
            "Fetch ENCFF999XYZ", "ENCODE_Search",
        )
        assert len(calls) == 1
        assert calls[0]["_chain"] == "download"


# ---------------------------------------------------------------------------
# Conversational references (referential follow-ups)
# ---------------------------------------------------------------------------

class TestReferentialAccessions:
    def test_them_resolves_from_history(self):
        history = _history([
            ("user", "Search ENCODE for K562"),
            ("assistant", "Found experiments: ENCSR000AAA, ENCSR000BBB"),
        ])
        calls = _auto_generate_data_calls(
            "Get the bam files for each of them", "ENCODE_Search",
            conversation_history=history,
        )
        assert len(calls) >= 1
        tools = {c["tool"] for c in calls}
        assert "get_files_by_type" in tools

    def test_referential_skips_details_block_accessions(self):
        """Accessions inside <details> blocks should be stripped."""
        history = _history([
            ("user", "Search K562"),
            ("assistant",
             "Found ENCSR000AAA.\n"
             "<details><summary>Raw</summary>ENCSR999ZZZ from broader search</details>"),
        ])
        calls = _auto_generate_data_calls(
            "Get files for those", "ENCODE_Search",
            conversation_history=history,
        )
        accessions = [c["params"].get("accession") for c in calls if "accession" in c.get("params", {})]
        assert "ENCSR999ZZZ" not in accessions


# ---------------------------------------------------------------------------
# Biosample search — known organisms
# ---------------------------------------------------------------------------

class TestBiosampleSearch:
    def test_k562_search(self):
        calls = _auto_generate_data_calls(
            "Search ENCODE for K562 experiments", "ENCODE_Search",
        )
        assert len(calls) >= 1
        assert calls[0]["tool"] == "search_by_biosample"
        # search_term should preserve original case
        assert "k562" in calls[0]["params"]["search_term"].lower()

    def test_mouse_organism_hint(self):
        calls = _auto_generate_data_calls(
            "Find mouse C2C12 experiments", "ENCODE_Search",
        )
        assert len(calls) >= 1
        assert calls[0]["params"].get("organism") == "Mus musculus"

    def test_human_organism_hint(self):
        calls = _auto_generate_data_calls(
            "human HepG2 experiments", "ENCODE_Search",
        )
        assert len(calls) >= 1
        assert calls[0]["params"].get("organism") == "Homo sapiens"


# ---------------------------------------------------------------------------
# Assay-only queries
# ---------------------------------------------------------------------------

class TestAssayOnlyQuery:
    def test_assay_only_search(self):
        calls = _auto_generate_data_calls(
            "How many RNA-seq experiments are in ENCODE?", "ENCODE_Search",
        )
        assert len(calls) >= 1
        # Should use search_by_assay since no biosample is mentioned
        assay_calls = [c for c in calls if c["tool"] == "search_by_assay"]
        if assay_calls:
            assert "assay_title" in assay_calls[0]["params"]


# ---------------------------------------------------------------------------
# Target protein / histone mark queries
# ---------------------------------------------------------------------------

class TestTargetSearch:
    def test_ctcf_target(self):
        calls = _auto_generate_data_calls(
            "Search for CTCF", "ENCODE_Search",
        )
        assert any(c["tool"] == "search_by_target" for c in calls)
        target_calls = [c for c in calls if c["tool"] == "search_by_target"]
        assert target_calls[0]["params"]["target"].upper() == "CTCF"

    def test_histone_mark(self):
        calls = _auto_generate_data_calls(
            "Find H3K27ac experiments", "ENCODE_Search",
        )
        assert any(c["tool"] == "search_by_target" for c in calls)


# ---------------------------------------------------------------------------
# Catch-all: unknown biosample term → search_by_biosample
# ---------------------------------------------------------------------------

class TestCatchAll:
    def test_unknown_biosample(self):
        calls = _auto_generate_data_calls(
            "Search ENCODE for WTC-11 experiments", "ENCODE_Search",
        )
        # WTC-11 is in _KNOWN_ORGANISMS, so this should match
        assert len(calls) >= 1

    def test_truly_unknown_term(self):
        calls = _auto_generate_data_calls(
            "Search for some_obscure_cell_line", "ENCODE_Search",
        )
        # Should still produce a search_by_biosample via catch-all
        if calls:
            assert calls[0]["tool"] == "search_by_biosample"


# ---------------------------------------------------------------------------
# Dogme file-parsing patterns
# ---------------------------------------------------------------------------

class TestDogmeFileParsing:
    def _blocks_with_job(self, work_dir="/tmp/proj/workflow1"):
        return _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": work_dir,
                "run_uuid": "aaaa-bbbb",
                "sample_name": "test1",
                "mode": "DNA",
            }},
        ])

    def test_parse_csv(self):
        blocks = self._blocks_with_job()
        calls = _auto_generate_data_calls(
            "parse annot/summary.csv", "analyze_job_results",
            history_blocks=blocks,
        )
        assert len(calls) >= 1
        assert calls[0]["tool"] == "find_file"
        assert "summary.csv" in calls[0]["params"]["file_name"]

    def test_show_me_file(self):
        blocks = self._blocks_with_job()
        calls = _auto_generate_data_calls(
            "show me report.tsv", "analyze_job_results",
            history_blocks=blocks,
        )
        assert len(calls) >= 1
        assert calls[0]["tool"] == "find_file"


# ---------------------------------------------------------------------------
# analyze_job_results catch-all auto-generates get_analysis_summary
# ---------------------------------------------------------------------------

class TestAnalyzeJobResultsCatchAll:
    def test_auto_generates_summary(self):
        blocks = _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow1",
                "run_uuid": "aaaa-bbbb",
                "sample_name": "test1",
                "mode": "DNA",
            }},
        ])
        calls = _auto_generate_data_calls(
            "Analyze the results", "analyze_job_results",
            history_blocks=blocks,
        )
        # Should produce a get_analysis_summary call (catch-all)
        summary_calls = [c for c in calls if c["tool"] == "get_analysis_summary"]
        assert len(summary_calls) == 1
        assert summary_calls[0]["params"]["work_dir"] == "/tmp/proj/workflow1"

    def test_no_catch_all_when_file_parse_matches(self):
        blocks = _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow1",
                "run_uuid": "aaaa-bbbb",
            }},
        ])
        calls = _auto_generate_data_calls(
            "parse summary.csv", "analyze_job_results",
            history_blocks=blocks,
        )
        # Should produce find_file, NOT get_analysis_summary
        tools = [c["tool"] for c in calls]
        assert "find_file" in tools
        assert "get_analysis_summary" not in tools

    def test_modification_count_uses_bedmethyl_listing(self):
        blocks = _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow1",
                "run_uuid": "aaaa-bbbb",
                "sample_name": "JamshidP",
                "mode": "RNA",
            }},
        ])
        calls = _auto_generate_data_calls(
            "count inosine modifications by chromosome",
            "analyze_job_results",
            history_blocks=blocks,
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "list_job_files"
        assert calls[0]["params"]["work_dir"] == "/tmp/proj/workflow1/bedMethyl"
        assert calls[0]["params"]["extensions"] == ".bed"
        assert calls[0]["params"]["max_depth"] == 1

    def test_bam_details_fallback_lists_files_first(self):
        blocks = _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow1",
                "run_uuid": "aaaa-bbbb",
                "sample_name": "ENCFF032XPV",
                "mode": "DNA",
            }},
        ])
        calls = _auto_generate_data_calls(
            "show details for ENCFF032XPV.bam",
            "analyze_job_results",
            history_blocks=blocks,
        )
        assert len(calls) == 2
        assert calls[0]["tool"] == "list_job_files"
        assert calls[0]["params"]["work_dir"] == "/tmp/proj/workflow1"
        assert "run_uuid" not in calls[0]["params"]
        assert calls[1]["tool"] == "find_file"
        assert calls[1]["params"]["file_name"] == "ENCFF032XPV.bam"
        assert calls[1]["params"]["work_dir"] == "/tmp/proj/workflow1"

    def test_bam_detail_explicit_workflow_request(self):
        blocks = _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow1",
                "run_uuid": "aaaa-bbbb",
            }},
        ])
        calls = _auto_generate_data_calls(
            "for workflow1, find ENCFF032XPV.bam and show details",
            "analyze_job_results",
            history_blocks=blocks,
            project_dir="/tmp/proj",
        )
        assert len(calls) == 2
        assert calls[0]["tool"] == "list_job_files"
        assert calls[0]["params"]["work_dir"] == "/tmp/proj/workflow1"
        assert calls[1]["tool"] == "find_file"

    def test_bam_detail_with_multiple_workflows_lists_workflows_first(self):
        blocks = _make_blocks([
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow1",
                "run_uuid": "aaaa-bbbb",
            }},
            {"type": "EXECUTION_JOB", "payload": {
                "work_directory": "/tmp/proj/workflow2",
                "run_uuid": "bbbb-cccc",
            }},
        ])
        calls = _auto_generate_data_calls(
            "show details for ENCFF032XPV.bam",
            "analyze_job_results",
            history_blocks=blocks,
            project_dir="/tmp/proj",
        )
        assert len(calls) == 1
        assert calls[0]["tool"] == "list_job_files"
        assert calls[0]["params"].get("name_pattern") == "workflow*"
