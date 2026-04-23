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
    read_file_content,
    parse_csv_file,
    parse_xgenepy_outputs,
    resolve_work_dir,
)


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
