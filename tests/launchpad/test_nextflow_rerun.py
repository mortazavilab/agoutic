from launchpad.nextflow_executor import NextflowExecutor


def test_prepare_rerun_directory_archives_prior_artifacts_and_clears_markers(tmp_path):
    work_dir = tmp_path / "workflow9"
    work_dir.mkdir()
    (work_dir / "report.html").write_text("report", encoding="utf-8")
    (work_dir / "tumor_report.html").write_text("sample report", encoding="utf-8")
    (work_dir / "tumor_timeline.html").write_text("timeline", encoding="utf-8")
    (work_dir / "tumor_trace.txt").write_text("trace", encoding="utf-8")
    (work_dir / ".nextflow_success").write_text("ok", encoding="utf-8")
    (work_dir / ".nextflow_pid").write_text("123", encoding="utf-8")

    archived = NextflowExecutor.prepare_rerun_directory(work_dir, ["tumor"])

    assert set(archived) == {"report.html", "tumor_report.html", "tumor_timeline.html", "tumor_trace.txt"}
    assert not (work_dir / "report.html").exists()
    assert not (work_dir / "tumor_report.html").exists()
    assert not (work_dir / "tumor_timeline.html").exists()
    assert not (work_dir / "tumor_trace.txt").exists()
    assert all((work_dir / archived_name).exists() for archived_name in archived.values())
    assert not (work_dir / ".nextflow_success").exists()
    assert not (work_dir / ".nextflow_pid").exists()