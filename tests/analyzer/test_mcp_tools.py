import asyncio

from analyzer.mcp_tools import list_job_files


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