import importlib.util

from xgenepy_mcp.service import run_xgenepy_analysis


def test_rejects_non_local_execution(tmp_path):
    result = run_xgenepy_analysis(
        project_dir=str(tmp_path),
        counts_path="counts.csv",
        metadata_path="metadata.csv",
        execution_mode="remote",
    )
    assert result["success"] is False
    assert "local execution only" in result["error"].lower()


def test_rejects_absolute_and_traversal_paths(tmp_path):
    result_abs = run_xgenepy_analysis(
        project_dir=str(tmp_path),
        counts_path="/tmp/counts.csv",
        metadata_path="metadata.csv",
    )
    assert result_abs["success"] is False
    assert "project-relative" in result_abs["error"]

    result_traversal = run_xgenepy_analysis(
        project_dir=str(tmp_path),
        counts_path="counts.csv",
        metadata_path="../secret.csv",
    )
    assert result_traversal["success"] is False
    assert "traversal" in result_traversal["error"].lower()


def test_missing_xgenepy_dependency_returns_clear_message(tmp_path, monkeypatch):
    counts = tmp_path / "counts.csv"
    metadata = tmp_path / "metadata.csv"
    counts.write_text("gene,s1\nG1,10\n", encoding="utf-8")
    metadata.write_text("sample_id,strain,allele\ns1,WT,A\n", encoding="utf-8")

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "xgenepy" else object())

    result = run_xgenepy_analysis(
        project_dir=str(tmp_path),
        counts_path="counts.csv",
        metadata_path="metadata.csv",
    )

    assert result["success"] is False
    assert "dependency is missing" in result["error"].lower()
    assert "pip install -e" in result["error"]
