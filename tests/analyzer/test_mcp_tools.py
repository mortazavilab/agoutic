import asyncio
import os
from pathlib import Path

from analyzer.mcp_tools import compare_bed_region_overlaps_tool, list_job_files


def test_list_job_files_allow_missing_returns_empty_success(tmp_path):
    missing = tmp_path / "workflow5" / "annot"

    result = asyncio.run(list_job_files(work_dir=str(missing), allow_missing=True))

    assert result["success"] is True
    assert result["file_count"] == 0
    assert result["files"] == []
    assert result["missing_work_dir"] is True


def test_list_job_files_missing_without_allow_missing_returns_error(tmp_path):
    missing = tmp_path / "workflow6" / "annot"

    result = asyncio.run(list_job_files(work_dir=str(missing), allow_missing=False))

    assert result["success"] is False
    assert result["error"] == "Job not found or work directory missing"


def test_compare_bed_region_overlaps_builds_connected_components_and_exports(tmp_path):
    sample_a = tmp_path / "projectA" / "workflow1" / "openChromatin" / "alpha.m6Aopen.bed"
    sample_b = tmp_path / "projectB" / "workflow9" / "openChromatin" / "beta.m6Aopen.bed"
    sample_a.parent.mkdir(parents=True)
    sample_b.parent.mkdir(parents=True)

    sample_a.write_text(
        "chr1\t10\t20\n"
        "chr1\t30\t40\n"
        "chr1\t60\t70\n",
        encoding="utf-8",
    )
    sample_b.write_text(
        "chr1\t19\t31\n"
        "chr1\t80\t90\n",
        encoding="utf-8",
    )

    result = asyncio.run(compare_bed_region_overlaps_tool(
        bed_a_path=str(sample_a),
        bed_b_path=str(sample_b),
        sample_a_label="Alpha",
        sample_b_label="Beta",
        min_overlap_bp=1,
    ))

    assert result["success"] is True
    assert result["counts"] == {
        "shared_components": 1,
        "sample_a_only_components": 1,
        "sample_b_only_components": 1,
        "total_components": 3,
    }

    bundle = result["_dataframes"]
    shared_df = bundle["Shared open chromatin overlaps (Alpha vs Beta)"]
    assert shared_df["row_count"] == 1
    shared_row = shared_df["data"][0]
    assert shared_row["component_start"] == 10
    assert shared_row["component_end"] == 40
    assert shared_row["sample_a_start"] == 10
    assert shared_row["sample_a_end"] == 40
    assert shared_row["sample_b_start"] == 19
    assert shared_row["sample_b_end"] == 31
    assert shared_row["sample_a_interval_count"] == 2
    assert shared_row["sample_b_interval_count"] == 1

    component_df = bundle["Overlap membership components (Alpha vs Beta)"]
    assert component_df["metadata"]["kind"] == "overlap_membership"
    assert component_df["metadata"]["set_columns"] == ["Alpha", "Beta"]
    component_rows = {row["component_id"]: row for row in component_df["data"]}
    assert any(row["Alpha"] and row["Beta"] for row in component_rows.values())
    assert any(row["Alpha"] and not row["Beta"] for row in component_rows.values())
    assert any((not row["Alpha"]) and row["Beta"] for row in component_rows.values())

    exported_files = result["exported_files"]
    for exported_path in exported_files.values():
        assert Path(exported_path).exists()

    shared_alpha_lines = Path(exported_files["shared_sample_a_bed"]).read_text(encoding="utf-8").strip().splitlines()
    shared_beta_lines = Path(exported_files["shared_sample_b_bed"]).read_text(encoding="utf-8").strip().splitlines()
    alpha_only_lines = Path(exported_files["sample_a_only_bed"]).read_text(encoding="utf-8").strip().splitlines()
    beta_only_lines = Path(exported_files["sample_b_only_bed"]).read_text(encoding="utf-8").strip().splitlines()

    assert shared_alpha_lines == ["chr1\t10\t40\tC1"]
    assert shared_beta_lines == ["chr1\t19\t31\tC1"]
    assert alpha_only_lines == ["chr1\t60\t70\tC2"]
    assert beta_only_lines == ["chr1\t80\t90\tC3"]


def test_compare_bed_region_overlaps_folder_search_chooses_newest_match(tmp_path):
    folder_a = tmp_path / "projectA" / "workflow2" / "openChromatin"
    folder_b = tmp_path / "projectB" / "workflow3" / "openChromatin"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)

    older_a = folder_a / "alpha_old.m6Aopen.bed"
    newer_a = folder_a / "alpha_new.m6Aopen.bed"
    older_a.write_text("chr1\t10\t20\n", encoding="utf-8")
    newer_a.write_text("chr1\t10\t20\n", encoding="utf-8")
    os.utime(older_a, (1_700_000_000, 1_700_000_000))
    os.utime(newer_a, (1_800_000_000, 1_800_000_000))

    sample_b = folder_b / "beta_current.m6Aopen.bed"
    sample_b.write_text("chr1\t15\t25\n", encoding="utf-8")

    result = asyncio.run(compare_bed_region_overlaps_tool(
        folder_a=str(folder_a),
        pattern_a="*m6Aopen.bed",
        bed_b_path=str(sample_b),
        sample_a_label="Alpha",
        sample_b_label="Beta",
    ))

    assert result["success"] is True
    assert result["sample_a"]["bed_path"] == str(newer_a.resolve())
    assert result["sample_a"]["matched_count"] == 2