"""
Tests for _auto_detect_skill_switch in cortex/app.py.

Verifies skill routing logic for all supported skills:
  - analyze_local_sample
  - ENCODE_Search
  - analyze_job_results
  - download_files
"""

import pytest
from cortex.llm_validators import _auto_detect_skill_switch


class TestAnalyzeLocalSample:
    """Tests for routing to analyze_local_sample skill."""

    def test_absolute_path_with_analysis_verb(self):
        result = _auto_detect_skill_switch(
            "analyze the sample at /data/pod5/run1", "welcome"
        )
        assert result == "analyze_local_sample"

    def test_relative_bam_path_with_analysis(self):
        result = _auto_detect_skill_switch(
            "analyze the sample using data/ENCFF921XAH.bam", "welcome"
        )
        assert result == "analyze_local_sample"

    def test_sample_keyword_with_path(self):
        result = _auto_detect_skill_switch(
            "my pod5 files are at /data/run1", "welcome"
        )
        assert result == "analyze_local_sample"

    def test_data_type_with_path(self):
        result = _auto_detect_skill_switch(
            "run dna analysis on /data/files", "welcome"
        )
        assert result == "analyze_local_sample"

    def test_no_path_no_switch(self):
        result = _auto_detect_skill_switch(
            "analyze the dna sample", "welcome"
        )
        assert result is None  # No path = not enough signal

    def test_from_dogme_skill_needs_stronger_signal(self):
        """From a Dogme skill, .bam counts as sample keyword + path = 2 signals."""
        result = _auto_detect_skill_switch(
            "analyze /data/file.bam", "run_dogme_dna"
        )
        # .bam extension counts as a sample keyword, giving 2 signals (path + sample)
        assert result == "analyze_local_sample"

    def test_from_dogme_skill_with_strong_signal(self):
        """From Dogme skill: path + analysis + data type = switch."""
        result = _auto_detect_skill_switch(
            "analyze the dna sample at data/sample.pod5", "run_dogme_dna"
        )
        assert result == "analyze_local_sample"

    def test_already_on_skill_no_switch(self):
        result = _auto_detect_skill_switch(
            "analyze /data/pod5 sample", "analyze_local_sample"
        )
        assert result is None


class TestRemoteExecution:
    def test_using_slurm_routes_remote_execution(self):
        result = _auto_detect_skill_switch(
            "Run the mouse cDNA sample Jamshid3 at /data/pod5 using slurm", "welcome"
        )
        assert result == "remote_execution"

    def test_on_hpc3_routes_remote_execution(self):
        result = _auto_detect_skill_switch(
            "Run the mouse cDNA sample Jamshid3 at /data/pod5 on hpc3", "welcome"
        )
        assert result == "remote_execution"

    def test_stage_on_hpc3_routes_remote_execution(self):
        result = _auto_detect_skill_switch(
            "Stage the mouse cDNA sample called Jamshid at /data/pod5 on hpc3", "welcome"
        )
        assert result == "remote_execution"

    def test_on_arbitrary_profile_nickname_routes_remote_execution(self):
        result = _auto_detect_skill_switch(
            "Run the mouse cDNA sample Jamshid3 at /data/pod5 on mycluster", "welcome"
        )
        assert result == "remote_execution"

    def test_list_files_on_hpc3_routes_remote_execution(self):
        result = _auto_detect_skill_switch(
            "list files on hpc3", "welcome"
        )
        assert result == "remote_execution"

    def test_list_files_on_arbitrary_profile_nickname_routes_remote_execution(self):
        result = _auto_detect_skill_switch(
            "list files on mycluster", "welcome"
        )
        assert result == "remote_execution"

    def test_show_files_on_cluster_switches_from_job_results(self):
        result = _auto_detect_skill_switch(
            "show files on the cluster", "analyze_job_results"
        )
        assert result == "remote_execution"


class TestEncodeSearch:
    """Tests for routing to ENCODE_Search skill."""

    def test_encode_with_search(self):
        result = _auto_detect_skill_switch(
            "search encode for K562 experiments", "welcome"
        )
        assert result == "ENCODE_Search"

    def test_encsr_accession(self):
        result = _auto_detect_skill_switch(
            "tell me about ENCSR123ABC", "welcome"
        )
        assert result == "ENCODE_Search"

    def test_encff_accession_not_encode_search(self):
        """ENCFF without 'search' might not trigger ENCODE_Search."""
        result = _auto_detect_skill_switch(
            "download ENCFF789GHI", "welcome"
        )
        # ENCFF + download would be download_files, not ENCODE_Search
        assert result != "ENCODE_Search"

    def test_already_on_encode_no_switch(self):
        result = _auto_detect_skill_switch(
            "search encode for HeLa", "ENCODE_Search"
        )
        assert result is None

    def test_encode_long_read_no_switch(self):
        result = _auto_detect_skill_switch(
            "search encode for HeLa experiments", "ENCODE_LongRead"
        )
        assert result is None


class TestAnalyzeJobResults:
    """Tests for routing to analyze_job_results skill."""

    def test_qc_report(self):
        result = _auto_detect_skill_switch("show me the qc report", "welcome")
        assert result == "analyze_job_results"

    def test_analyze_results(self):
        result = _auto_detect_skill_switch("analyze results", "welcome")
        assert result == "analyze_job_results"

    def test_analyze_the_result(self):
        result = _auto_detect_skill_switch("analyze the result", "welcome")
        assert result == "analyze_job_results"

    def test_show_the_results(self):
        result = _auto_detect_skill_switch("show the results", "welcome")
        assert result == "analyze_job_results"

    def test_see_the_result(self):
        result = _auto_detect_skill_switch("see the result", "welcome")
        assert result == "analyze_job_results"

    def test_job_results(self):
        result = _auto_detect_skill_switch("job results", "welcome")
        assert result == "analyze_job_results"

    def test_already_on_skill_no_switch(self):
        result = _auto_detect_skill_switch("analyze results", "analyze_job_results")
        assert result is None

    def test_list_files(self):
        result = _auto_detect_skill_switch("list files", "welcome")
        assert result == "analyze_job_results"

    def test_list_workflows(self):
        result = _auto_detect_skill_switch("list workflows", "welcome")
        assert result == "analyze_job_results"

    def test_show_files(self):
        result = _auto_detect_skill_switch("show files", "welcome")
        assert result == "analyze_job_results"

    def test_list_my_data_no_skill_switch(self):
        """'list my data' is handled directly in chat handler, not via skill routing."""
        result = _auto_detect_skill_switch("list my data", "welcome")
        assert result is None

    def test_list_my_files_no_skill_switch(self):
        """'list my files' is handled directly in chat handler, not via skill routing."""
        result = _auto_detect_skill_switch("list my files", "welcome")
        assert result is None

    def test_what_files_no_skill_switch(self):
        """'what files do I have' is handled directly in chat handler, not via skill routing."""
        result = _auto_detect_skill_switch("what files do I have?", "welcome")
        assert result is None

    def test_project_workflow_region_venn_routes_to_analyze_job_results(self):
        result = _auto_detect_skill_switch(
            "make a venn diagram of the regions in testslopenchrom:workflow2 and testopenchrom2:workflow4",
            "welcome",
        )
        assert result == "analyze_job_results"


class TestDownloadFiles:
    """Tests for routing to download_files skill."""

    def test_download_encff(self):
        result = _auto_detect_skill_switch(
            "download ENCFF546HTC", "ENCODE_Search"
        )
        assert result == "download_files"

    def test_fetch_with_url(self):
        result = _auto_detect_skill_switch(
            "fetch https://example.com/file.bam", "welcome"
        )
        # URL with .bam extension triggers analyze_local_sample (path+sample signals)
        assert result == "analyze_local_sample"

    def test_grab_with_accession(self):
        result = _auto_detect_skill_switch(
            "grab ENCFF212RUB from ENCSR160HKZ", "ENCODE_LongRead"
        )
        assert result == "download_files"

    def test_download_igvf_file_from_igvf_search(self):
        result = _auto_detect_skill_switch(
            "download file IGVFFI1476XCPC", "IGVF_Search"
        )
        assert result == "download_files"

    def test_download_igvf_dataset_from_welcome(self):
        result = _auto_detect_skill_switch(
            "download the bam files for IGVFDS3560WHCX", "welcome"
        )
        assert result == "download_files"

    def test_download_without_target(self):
        """'download the results' triggers analyze_job_results due to results keywords."""
        result = _auto_detect_skill_switch(
            "download the results", "welcome"
        )
        assert result == "analyze_job_results"

    def test_already_on_skill_no_switch(self):
        result = _auto_detect_skill_switch(
            "download ENCFF123ABC", "download_files"
        )
        assert result is None


class TestNoSwitch:
    """Tests that verify no false-positive switching."""

    def test_generic_question(self):
        result = _auto_detect_skill_switch("hello how are you", "welcome")
        assert result is None


class TestReconcileBams:
    """Tests for routing to reconcile_bams skill."""

    def test_reconcile_bams_phrase_routes_skill(self):
        result = _auto_detect_skill_switch(
            "reconcile bams across workflow outputs", "welcome"
        )
        assert result == "reconcile_bams"

    def test_merge_annotated_bam_phrase_routes_skill(self):
        result = _auto_detect_skill_switch(
            "merge annotated bam files from workflow1 and workflow2", "welcome"
        )
        assert result == "reconcile_bams"

    def test_reconcile_the_bams_phrase_routes_skill(self):
        result = _auto_detect_skill_switch(
            "I want to reconcile the bams of C2C12r1 and C2C12r3", "run_dogme_rna"
        )
        assert result == "reconcile_bams"

    def test_already_on_reconcile_skill_no_switch(self):
        result = _auto_detect_skill_switch(
            "reconcile bams from prior workflows", "reconcile_bams"
        )
        assert result is None


class TestDifferentialExpression:
    """Tests for routing to differential_expression skill."""

    def test_de_analysis_from_welcome(self):
        result = _auto_detect_skill_switch(
            "run differential expression on my count matrix", "welcome"
        )
        assert result == "differential_expression"

    def test_de_genes_from_welcome(self):
        result = _auto_detect_skill_switch(
            "what genes are differentially expressed?", "welcome"
        )
        assert result == "differential_expression"

    def test_volcano_plot_from_results(self):
        result = _auto_detect_skill_switch(
            "generate a volcano plot of the DE results", "analyze_job_results"
        )
        assert result == "differential_expression"

    def test_edgepython_mention(self):
        result = _auto_detect_skill_switch(
            "use edgepython to analyze my data", "welcome"
        )
        assert result == "differential_expression"

    def test_edger_mention(self):
        result = _auto_detect_skill_switch(
            "I want to run edgeR-style analysis", "welcome"
        )
        assert result == "differential_expression"

    def test_count_matrix_mention(self):
        result = _auto_detect_skill_switch(
            "I have a count matrix and want to find DE genes", "welcome"
        )
        assert result == "differential_expression"

    def test_fold_change_mention(self):
        result = _auto_detect_skill_switch(
            "show me the log fold change for treated vs control", "welcome"
        )
        assert result == "differential_expression"

    def test_nebula_mention(self):
        result = _auto_detect_skill_switch(
            "run single-cell DE with NEBULA", "welcome"
        )
        assert result == "differential_expression"

    def test_dtu_mention(self):
        result = _auto_detect_skill_switch(
            "test for differential transcript usage", "welcome"
        )
        assert result == "differential_expression"

    def test_compare_treated_vs_control(self):
        result = _auto_detect_skill_switch(
            "compare treated vs control samples", "welcome"
        )
        assert result == "differential_expression"

    def test_already_on_skill_no_switch(self):
        result = _auto_detect_skill_switch(
            "run differential expression", "differential_expression"
        )
        assert result is None

    def test_followup_question(self):
        result = _auto_detect_skill_switch(
            "what assay types are there?", "ENCODE_Search"
        )
        assert result is None

    def test_plot_request(self):
        result = _auto_detect_skill_switch(
            "plot this by assay", "ENCODE_Search"
        )
        assert result is None


class TestXgenePyRouting:
    """Tests for routing to xgenepy_analysis skill."""

    def test_xgenepy_keyword_routes_skill(self):
        result = _auto_detect_skill_switch(
            "run xgenepy analysis on my count matrix", "welcome"
        )
        assert result == "xgenepy_analysis"

    def test_cis_trans_phrase_routes_skill(self):
        result = _auto_detect_skill_switch(
            "perform cis trans analysis with allele-specific metadata", "welcome"
        )
        assert result == "xgenepy_analysis"
