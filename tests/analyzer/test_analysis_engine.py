"""
Tests for analyzer/analysis_engine.py — file discovery and parsing.

Uses tmp_path to create temporary directory structures that mimic
real Dogme job output, testing discover_files, categorize_files,
read_file_content, and parse_csv_file.
"""

import csv
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# We test the functions that don't require DB access by mocking resolve_work_dir
from analyzer.analysis_engine import (
    discover_files,
    categorize_files,
    generate_analysis_summary,
    read_file_content,
    parse_csv_file,
    parse_xgenepy_outputs,
    resolve_work_dir,
)
from analyzer.schemas import JobFileSummary


# ---------------------------------------------------------------------------
# Fixtures: create realistic job directory structures
# ---------------------------------------------------------------------------

@pytest.fixture()
def job_dir(tmp_path):
    """Create a fake Dogme job directory with representative files."""
    wd = tmp_path / "workflow1"
    wd.mkdir()

    # Create some analysis output files
    (wd / "final_stats.csv").write_text("gene,score,pval\nBRCA1,0.95,0.001\nTP53,0.88,0.005\n")
    (wd / "summary.txt").write_text("Analysis complete.\n10 genes processed.\n")
    (wd / "report.html").write_text("<html><body><h1>QC report</h1><p>All checks passed.</p></body></html>")
    (wd / "notes.md").write_text("# Summary\n\n- item one\n")
    (wd / "regions.bed").write_text("chr1\t100\t200\tpeak1\t500\nchr2\t300\t400\tpeak2\t750\n")
    (wd / "data.tsv").write_text("sample\tcount\nliver\t42\nheart\t37\n")
    (wd / "output.bam").write_bytes(b"\x00" * 100)

    # Create subdirectory with more files
    annot = wd / "annot"
    annot.mkdir()
    (annot / "annotations.csv").write_text("id,type\n1,exon\n2,intron\n")

    # Create work/ directory (should be excluded from discovery)
    work = wd / "work"
    work.mkdir()
    (work / "intermediate.tmp").write_text("should be excluded")

    return wd


# ---------------------------------------------------------------------------
# resolve_work_dir
# ---------------------------------------------------------------------------
class TestResolveWorkDir:
    def test_direct_path_exists(self, job_dir):
        result = resolve_work_dir(work_dir=str(job_dir))
        assert result == job_dir

    def test_direct_path_absolute_nonexistent(self, tmp_path):
        missing = tmp_path / "nonexistent"
        result = resolve_work_dir(work_dir=str(missing))
        assert result == missing  # Returns even if doesn't exist

    def test_neither_provided(self):
        result = resolve_work_dir()
        assert result is None


# ---------------------------------------------------------------------------
# discover_files
# ---------------------------------------------------------------------------
class TestDiscoverFiles:
    def test_discover_all_files(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir))
        names = {f.name for f in listing.files}
        assert "final_stats.csv" in names
        assert "summary.txt" in names
        assert "regions.bed" in names
        assert "output.bam" in names
        assert "annotations.csv" in names  # In subdirectory

    def test_excludes_work_directory(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir))
        names = {f.name for f in listing.files}
        assert "intermediate.tmp" not in names

    def test_filter_by_extension(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir), extensions=[".csv"])
        names = {f.name for f in listing.files}
        assert "final_stats.csv" in names
        assert "annotations.csv" in names
        assert "summary.txt" not in names

    def test_file_count(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir))
        assert listing.file_count == len(listing.files)
        assert listing.file_count >= 5  # At least our created files

    def test_total_size(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir))
        assert listing.total_size > 0

    def test_work_dir_stored(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir))
        assert listing.work_dir == str(job_dir)

    def test_nonexistent_dir_raises(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            discover_files(work_dir_path=str(missing))

    def test_depth_limited_listing(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir), max_depth=1)
        names = {f.name.rstrip("/") for f in listing.files}
        # Should show immediate children including annot/ directory
        assert "annot" in names
        assert "final_stats.csv" in names
        # Should NOT include files inside subdirectories
        assert "annotations.csv" not in names

    def test_name_pattern_filter(self, job_dir):
        listing = discover_files(work_dir_path=str(job_dir), max_depth=1, name_pattern="*.csv")
        names = {f.name for f in listing.files}
        assert "final_stats.csv" in names
        assert "summary.txt" not in names


# ---------------------------------------------------------------------------
# categorize_files
# ---------------------------------------------------------------------------
class TestCategorizeFiles:
    def test_categorization(self, job_dir):
        summary = categorize_files(work_dir_path=str(job_dir))
        bed_names = {f.name for f in summary.bed_files}
        csv_names = {f.name for f in summary.csv_files}
        txt_names = {f.name for f in summary.txt_files}

        assert "regions.bed" in bed_names
        assert "final_stats.csv" in csv_names or "data.tsv" in csv_names
        assert "summary.txt" in txt_names


# ---------------------------------------------------------------------------
# read_file_content
# ---------------------------------------------------------------------------
class TestReadFileContent:
    def test_read_full_file(self, job_dir):
        result = read_file_content(work_dir_path=str(job_dir), file_path="summary.txt")
        assert "Analysis complete" in result.content
        assert result.is_truncated is False

    def test_read_with_preview_lines(self, job_dir):
        result = read_file_content(
            work_dir_path=str(job_dir),
            file_path="final_stats.csv",
            preview_lines=1,
        )
        assert result.is_truncated is True
        # Should only have the header line
        assert result.content.count("\n") <= 1

    def test_file_size_reported(self, job_dir):
        result = read_file_content(work_dir_path=str(job_dir), file_path="summary.txt")
        assert result.file_size > 0

    def test_missing_file_raises(self, job_dir):
        with pytest.raises(FileNotFoundError):
            read_file_content(work_dir_path=str(job_dir), file_path="missing.txt")

    def test_path_traversal_rejected(self, job_dir):
        with pytest.raises(ValueError, match="Invalid file path"):
            read_file_content(work_dir_path=str(job_dir), file_path="../../etc/passwd")

    def test_read_subdirectory_file(self, job_dir):
        result = read_file_content(
            work_dir_path=str(job_dir), file_path="annot/annotations.csv"
        )
        assert "exon" in result.content

    def test_auto_render_html_as_readable_text(self, job_dir):
        result = read_file_content(work_dir_path=str(job_dir), file_path="report.html")
        assert result.render_mode == "html_text"
        assert "QC report" in result.content
        assert "All checks passed." in result.content
        assert "<h1>" not in result.content

    def test_render_raw_html_when_requested(self, job_dir):
        result = read_file_content(
            work_dir_path=str(job_dir),
            file_path="report.html",
            render_mode="html_raw",
        )
        assert result.render_mode == "html_raw"
        assert "<h1>QC report</h1>" in result.content

    def test_auto_render_markdown_as_markdown(self, job_dir):
        result = read_file_content(work_dir_path=str(job_dir), file_path="notes.md")
        assert result.render_mode == "markdown"
        assert result.content.startswith("# Summary")


# ---------------------------------------------------------------------------
# parse_csv_file
# ---------------------------------------------------------------------------
class TestParseCsvFile:
    def test_parse_csv(self, job_dir):
        result = parse_csv_file(work_dir_path=str(job_dir), file_path="final_stats.csv")
        assert "gene" in result.columns
        assert result.row_count == 2

    def test_parse_tsv(self, job_dir):
        result = parse_csv_file(work_dir_path=str(job_dir), file_path="data.tsv")
        assert "sample" in result.columns
        assert result.row_count == 2

    def test_max_rows(self, job_dir):
        result = parse_csv_file(
            work_dir_path=str(job_dir), file_path="final_stats.csv", max_rows=1
        )
        assert len(result.data) == 1

    def test_column_stats_numeric(self, job_dir):
        result = parse_csv_file(work_dir_path=str(job_dir), file_path="final_stats.csv")
        col_stats = result.metadata.get("column_stats", {})
        assert "score" in col_stats
        stats = col_stats["score"]
        assert "mean" in stats
        assert "min" in stats

    def test_column_stats_categorical(self, job_dir):
        result = parse_csv_file(work_dir_path=str(job_dir), file_path="final_stats.csv")
        col_stats = result.metadata.get("column_stats", {})
        assert "gene" in col_stats
        stats = col_stats["gene"]
        assert "unique" in stats

    def test_missing_csv_raises(self, job_dir):
        with pytest.raises(FileNotFoundError):
            parse_csv_file(work_dir_path=str(job_dir), file_path="missing.csv")


class TestParseXgenePyOutputs:
    def _write_canonical_outputs(self, root: Path) -> None:
        run_dir = root / "xgenepy_runs" / "workflow1"
        run_dir.mkdir(parents=True)
        (run_dir / "plots").mkdir(parents=True)

        (run_dir / "fit_summary.json").write_text('{"row_count": 2}', encoding="utf-8")
        (run_dir / "model_metadata.json").write_text('{"trans_model": "log_additive"}', encoding="utf-8")
        (run_dir / "run_manifest.json").write_text('{"schema_version": "1.0"}', encoding="utf-8")
        (run_dir / "assignments.tsv").write_text(
            "gene\treg_assignment\tcis_prop\nG1\tcis\t0.9\nG2\ttrans\t0.1\n",
            encoding="utf-8",
        )
        (run_dir / "proportion_cis.tsv").write_text(
            "gene\tcis_prop\nG1\t0.9\nG2\t0.1\n",
            encoding="utf-8",
        )
        (run_dir / "plots" / "assignments.png").write_bytes(b"fakepng")

    def test_parse_xgenepy_outputs_success(self, tmp_path):
        self._write_canonical_outputs(tmp_path)
        parsed = parse_xgenepy_outputs(
            work_dir_path=str(tmp_path),
            output_dir="xgenepy_runs/workflow1",
            max_rows=1,
        )

        assert parsed.required_outputs_present is True
        assert parsed.missing_outputs == []
        assert parsed.fit_summary["row_count"] == 2
        assert len(parsed.assignments) == 1
        assert len(parsed.proportion_cis) == 1
        assert len(parsed.plots) == 1

    def test_parse_xgenepy_outputs_missing_artifact(self, tmp_path):
        self._write_canonical_outputs(tmp_path)
        (tmp_path / "xgenepy_runs" / "workflow1" / "model_metadata.json").unlink()

        parsed = parse_xgenepy_outputs(
            work_dir_path=str(tmp_path),
            output_dir="xgenepy_runs/workflow1",
        )

        assert parsed.required_outputs_present is False
        assert "model_metadata.json" in parsed.missing_outputs

    def test_parse_xgenepy_outputs_rejects_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid output_dir path"):
            parse_xgenepy_outputs(
                work_dir_path=str(tmp_path),
                output_dir="../../outside",
            )


class TestGenerateAnalysisSummary:
    def test_generate_summary_accepts_work_dir_only_when_job_resolves(self, job_dir):
        job = MagicMock(
            run_uuid="run-555",
            sample_name="Jamshid999",
            workflow_key="dogme",
            mode="RNA",
            status="COMPLETED",
            nextflow_work_dir=str(job_dir),
            output_directory=str(job_dir),
        )
        empty_summary = JobFileSummary(
            txt_files=[],
            csv_files=[],
            bed_files=[],
            other_files=[],
        )

        with patch("analyzer.analysis_engine._get_job_by_run_uuid_or_work_dir", return_value=job) as lookup:
            with patch("analyzer.analysis_engine.categorize_files", return_value=empty_summary) as categorize:
                with patch("analyzer.analysis_engine.resolve_work_dir", return_value=job_dir):
                    summary = generate_analysis_summary(work_dir_path=str(job_dir))

        lookup.assert_called_once_with(run_uuid=None, work_dir_path=str(job_dir))
        categorize.assert_called_once_with("run-555", work_dir_path=str(job_dir))
        assert summary.run_uuid == "run-555"
        assert summary.sample_name == "Jamshid999"
        assert summary.workflow_key == "dogme"
        assert summary.work_dir == str(job_dir)

    def test_generate_summary_recognizes_wf_pore_c_with_nullable_mode(self, tmp_path):
        job_dir = tmp_path / "workflow8"
        job_dir.mkdir()
        (job_dir / "wf-pore-c-report.html").write_text("<html><body>report</body></html>", encoding="utf-8")
        pairs_dir = job_dir / "pairs"
        pairs_dir.mkdir()
        (pairs_dir / "sample.pairs.gz").write_text("pairs\n", encoding="utf-8")
        cooler_dir = job_dir / "cooler"
        cooler_dir.mkdir()
        (cooler_dir / "sample.mcool").write_text("mcool\n", encoding="utf-8")
        hic_dir = job_dir / "hi-c"
        hic_dir.mkdir()
        (hic_dir / "sample.hic").write_text("hic\n", encoding="utf-8")
        (job_dir / "pairs.stats.txt").write_text(
            "total\t100\n"
            "cis\t80\n"
            "trans\t20\n"
            "total_dups\t10\n",
            encoding="utf-8",
        )
        (job_dir / ".agoutic.workflow.json").write_text(
            json.dumps(
                {
                    "workflow_key": "wf_pore_c",
                    "validated_inputs": {
                        "reference_fasta": "/refs/grch38.fa",
                        "cutter": "NlaIII",
                        "workflow_repo": "epi2me-labs/wf-pore-c",
                        "workflow_version": "v1.3.1",
                        "sample_name": "POREC_A",
                        "sample_sheet": None,
                    },
                    "summary_contract": {
                        "workflow_key": "wf_pore_c",
                        "workflow_version": "v1.3.1",
                        "report_filename": "wf-pore-c-report.html",
                        "sample_name": "POREC_A",
                        "output_flags": {"pairs": True, "mcool": True, "hi_c": True},
                    },
                    "result_sync_spec": {
                        "workflow_key": "wf_pore_c",
                        "report_filename": "wf-pore-c-report.html",
                        "expected_outputs": [
                            "wf-pore-c-report.html",
                            "pairs/{alias}.pairs.gz",
                            "cooler/{alias}.mcool",
                            "hi-c/{alias}.hic",
                        ],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        job = MagicMock(
            run_uuid="run-pore-c",
            sample_name="POREC_A",
            workflow_key="wf_pore_c",
            mode=None,
            status="COMPLETED",
            nextflow_work_dir=str(job_dir),
            output_directory=str(job_dir),
        )

        with patch("analyzer.analysis_engine._get_job_by_run_uuid_or_work_dir", return_value=job) as lookup:
            summary = generate_analysis_summary(work_dir_path=str(job_dir))

        lookup.assert_called_once_with(run_uuid=None, work_dir_path=str(job_dir))
        assert summary.run_uuid == "run-pore-c"
        assert summary.sample_name == "POREC_A"
        assert summary.workflow_key == "wf_pore_c"
        assert summary.mode is None
        assert summary.summary_contract["workflow_key"] == "wf_pore_c"
        assert summary.result_sync_spec["report_filename"] == "wf-pore-c-report.html"
        assert summary.key_results["Workflow Report"] == "Available"
        assert summary.workflow_summary["artifacts"]["pairs"]["present"] is True
        assert summary.workflow_summary["artifacts"]["mcool"]["present"] is True
        assert summary.workflow_summary["artifacts"]["hic"]["present"] is True
        assert summary.workflow_summary["artifacts"]["report_html"]["present"] is True
        assert summary.workflow_summary["sample_alias"] == "POREC_A"
        assert summary.workflow_summary["metadata"]["reference_fasta"] == "/refs/grch38.fa"
        assert summary.workflow_summary["metadata"]["cutter"] == "NlaIII"
        assert summary.workflow_summary["metadata"]["workflow_version"] == "v1.3.1"
        assert summary.parsed_reports["pairs_stats"]["total_pairs"] == 100
        assert summary.parsed_reports["pairs_stats"]["cis_trans_ratio"] == 4.0
        assert summary.parsed_reports["pairs_stats"]["duplicate_rate"] == 0.1

    def test_generate_summary_infers_wf_pore_c_sample_alias_from_sample_sheet(self, tmp_path):
        job_dir = tmp_path / "workflow9"
        job_dir.mkdir()
        sample_sheet = tmp_path / "samples.csv"
        sample_sheet.write_text("sample,fastq\nSHEET_ALIAS,/data/sample.fastq.gz\n", encoding="utf-8")
        (job_dir / "wf-pore-c-report.html").write_text("<html></html>", encoding="utf-8")
        (job_dir / ".agoutic.workflow.json").write_text(
            json.dumps(
                {
                    "workflow_key": "wf_pore_c",
                    "validated_inputs": {
                        "reference_fasta": "/refs/grch38.fa",
                        "cutter": "NlaIII",
                        "workflow_version": "v1.3.1",
                        "sample_name": "",
                        "sample_sheet": str(sample_sheet),
                    },
                    "summary_contract": {
                        "workflow_key": "wf_pore_c",
                        "workflow_version": "v1.3.1",
                        "report_filename": "wf-pore-c-report.html",
                        "sample_name": "",
                        "output_flags": {"pairs": False, "mcool": False, "hi_c": False},
                    },
                    "result_sync_spec": {
                        "workflow_key": "wf_pore_c",
                        "report_filename": "wf-pore-c-report.html",
                        "expected_outputs": ["wf-pore-c-report.html"],
                    },
                }
            ),
            encoding="utf-8",
        )

        job = MagicMock(
            run_uuid="run-pore-c-sheet",
            sample_name="",
            workflow_key="wf_pore_c",
            mode=None,
            status="COMPLETED",
            nextflow_work_dir=str(job_dir),
            output_directory=str(job_dir),
        )

        with patch("analyzer.analysis_engine._get_job_by_run_uuid_or_work_dir", return_value=job):
            summary = generate_analysis_summary(work_dir_path=str(job_dir))

        assert summary.workflow_summary["sample_alias"] == "SHEET_ALIAS"

    def test_generate_summary_warns_when_requested_wf_pore_c_outputs_are_missing(self, tmp_path):
        job_dir = tmp_path / "workflow10"
        job_dir.mkdir()
        (job_dir / "wf-pore-c-report.html").write_text("<html></html>", encoding="utf-8")
        (job_dir / "pairs.stats.txt").write_text("total\t10\n", encoding="utf-8")
        (job_dir / ".agoutic.workflow.json").write_text(
            json.dumps(
                {
                    "workflow_key": "wf_pore_c",
                    "validated_inputs": {
                        "reference_fasta": "/refs/grch38.fa",
                        "cutter": "NlaIII",
                        "workflow_version": "v1.3.1",
                        "sample_name": "POREC_WARN",
                    },
                    "summary_contract": {
                        "workflow_key": "wf_pore_c",
                        "workflow_version": "v1.3.1",
                        "report_filename": "wf-pore-c-report.html",
                        "sample_name": "POREC_WARN",
                        "output_flags": {"pairs": True, "mcool": True, "hi_c": False},
                    },
                    "result_sync_spec": {
                        "workflow_key": "wf_pore_c",
                        "report_filename": "wf-pore-c-report.html",
                        "expected_outputs": [
                            "wf-pore-c-report.html",
                            "pairs/{alias}.pairs.gz",
                            "cooler/{alias}.mcool",
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

        job = MagicMock(
            run_uuid="run-pore-c-warn",
            sample_name="POREC_WARN",
            workflow_key="wf_pore_c",
            mode=None,
            status="COMPLETED",
            nextflow_work_dir=str(job_dir),
            output_directory=str(job_dir),
        )

        with patch("analyzer.analysis_engine._get_job_by_run_uuid_or_work_dir", return_value=job):
            summary = generate_analysis_summary(work_dir_path=str(job_dir))

        assert summary.workflow_summary["artifacts"]["pairs"]["present"] is False
        assert summary.workflow_summary["artifacts"]["mcool"]["present"] is False
        assert any("Missing requested output: .pairs.gz" == warning for warning in summary.warnings)
        assert any("Missing requested output: cooler/{alias}.mcool" == warning for warning in summary.warnings)
        assert any("pairs.stats.txt missing summary metrics" in warning for warning in summary.warnings)

    def test_generate_summary_infers_reconcile_bams_from_layout(self, tmp_path):
        job_dir = tmp_path / "workflow11"
        (job_dir / "input").mkdir(parents=True)
        (job_dir / "output").mkdir()
        (job_dir / "output" / "reconciled.inputs.tsv").write_text(
            "sample\tbam\treference\nS1\t/a.bam\tGRCh38\nS2\t/b.bam\tGRCh38\n",
            encoding="utf-8",
        )
        (job_dir / "reconciled.bam").write_bytes(b"\x00" * 32)
        (job_dir / "reconciled.bam.bai").write_bytes(b"\x00" * 8)
        (job_dir / "annotation.gtf").write_text("chr1\ttest\tgene\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
        (job_dir / "reconcile_report.txt").write_text("done\n", encoding="utf-8")

        job = MagicMock(
            run_uuid="run-reconcile",
            sample_name="reconciled",
            workflow_key="dogme",
            mode=None,
            status="COMPLETED",
            nextflow_work_dir=str(job_dir),
            output_directory=str(job_dir),
        )

        with patch("analyzer.analysis_engine._get_job_by_run_uuid_or_work_dir", return_value=job):
            summary = generate_analysis_summary(work_dir_path=str(job_dir))

        assert summary.workflow_key == "reconcile_bams"
        assert summary.key_results["Inputs Manifest"] == "Available"
        assert summary.key_results["Reconciled BAM"] == "Available"
        assert summary.workflow_summary["metadata"]["input_bam_count"] == 2
        assert summary.workflow_summary["metadata"]["reference"] == "GRCh38"
        assert summary.workflow_summary["artifacts"]["annotation_gtf"]["present"] is True

    def test_generate_summary_infers_haplotype_with_vcf_from_layout(self, tmp_path):
        job_dir = tmp_path / "workflow12"
        job_dir.mkdir()
        (job_dir / "sample.haplotyped.bam").write_bytes(b"\x00" * 32)
        (job_dir / "sample.h1.haplotyped.bam").write_bytes(b"\x00" * 16)
        (job_dir / "sample.h2.haplotyped.bam").write_bytes(b"\x00" * 16)
        (job_dir / "sample.ambiguous.haplotyped.bam").write_bytes(b"\x00" * 16)
        (job_dir / "sample.haplotyped.bam.bai").write_bytes(b"\x00" * 8)
        (job_dir / "sample.summary.tsv").write_text(
            "bam_name\ttotal_reads\th1\th2\tambiguous\nsample.haplotyped.bam\t100\t45\t40\t15\n",
            encoding="utf-8",
        )
        (job_dir / "sample.chromosomes.tsv").write_text(
            "chromosome\th1\th2\nchr1\t10\t11\n",
            encoding="utf-8",
        )
        (job_dir / "sample.genes.tsv").write_text(
            "gene\th1\th2\nGENE1\t4\t5\n",
            encoding="utf-8",
        )
        (job_dir / "sample.transcripts.tsv").write_text(
            "transcript\th1\th2\nTX1\t2\t3\n",
            encoding="utf-8",
        )

        job = MagicMock(
            run_uuid="run-haplotype",
            sample_name="sample",
            workflow_key="dogme",
            mode=None,
            status="COMPLETED",
            nextflow_work_dir=str(job_dir),
            output_directory=str(job_dir),
        )

        with patch("analyzer.analysis_engine._get_job_by_run_uuid_or_work_dir", return_value=job):
            summary = generate_analysis_summary(work_dir_path=str(job_dir))

        assert summary.workflow_key == "haplotype_with_vcf"
        assert summary.key_results["Genome Summary"] == "Available"
        assert summary.key_results["Chromosome Summary"] == "Available"
        assert summary.workflow_summary["artifacts"]["haplotyped_bam"]["count"] == 4
        assert summary.workflow_summary["artifacts"]["ambiguous_bam"]["count"] == 1
        assert summary.workflow_summary["metadata"]["assignment_labels"] == ["h1", "h2"]
        assert summary.parsed_reports["haplotype_summary"]["data"][0]["total_reads"] == "100"

    def test_generate_summary_supports_work_dir_only_differential_expression_workflow(self, tmp_path):
        job_dir = tmp_path / "workflow16"
        (job_dir / "de_inputs").mkdir(parents=True)
        (job_dir / "de_results").mkdir()
        (job_dir / "de_inputs" / "ad_vs_control_gene_counts.tsv").write_text(
            "gene\tS1\tS2\tS3\tS4\nGeneA\t10\t12\t30\t28\n",
            encoding="utf-8",
        )
        (job_dir / "de_inputs" / "ad_vs_control_gene_sample_info.csv").write_text(
            "sample,group\nS1,control\nS2,control\nS3,ad\nS4,ad\n",
            encoding="utf-8",
        )
        (job_dir / "de_results" / "de_results.tsv").write_text(
            "gene\tlogFC\tFDR\nGeneA\t1.5\t0.01\nGeneB\t-2.1\t0.02\nGeneC\t0.2\t0.5\n",
            encoding="utf-8",
        )
        (job_dir / "de_results" / "volcano_ad_vs_control_gene.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "de_results" / "volcano_ad_vs_control_gene.svg").write_text("<svg></svg>", encoding="utf-8")

        with patch("analyzer.analysis_engine._get_job_by_run_uuid_or_work_dir", return_value=None):
            summary = generate_analysis_summary(work_dir_path=str(job_dir))

        assert summary.workflow_key == "differential_expression"
        assert summary.sample_name == "workflow16"
        assert summary.key_results["DE Results"] == "Available"
        assert summary.key_results["Volcano Plot"] == "Available"
        assert summary.key_results["Input Counts"] == "Available"
        assert summary.key_results["Sample Count"] == 4
        assert summary.key_results["Significant Features"] == 2
        assert summary.workflow_summary["metadata"]["comparison_name"] == "ad_vs_control_gene"
        assert summary.workflow_summary["metadata"]["up_count"] == 1
        assert summary.workflow_summary["metadata"]["down_count"] == 1
        assert summary.parsed_reports["de_results"]["data"][0]["gene"] == "GeneA"
        assert summary.parsed_reports["de_sample_info"]["data"][0]["group"] == "control"
