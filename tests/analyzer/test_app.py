"""Tests for analyzer/app.py endpoint behavior."""

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from analyzer.app import app


def _file_listing_payload():
    return {
        "run_uuid": "run-1",
        "work_dir": "/tmp/workflow1",
        "files": [
            {
                "path": "results.csv",
                "name": "results.csv",
                "size": 42,
                "extension": ".csv",
                "modified_time": None,
            }
        ],
        "file_count": 1,
        "total_size": 42,
    }


def _job_file_summary_payload():
    return {
        "txt_files": [],
        "csv_files": [
            {
                "path": "results.csv",
                "name": "results.csv",
                "size": 42,
                "extension": ".csv",
                "modified_time": None,
            }
        ],
        "bed_files": [],
        "other_files": [],
    }


def _content_payload():
    return {
        "run_uuid": "run-1",
        "file_path": "results.csv",
        "content": "gene,score\nBRCA1,0.9\n",
        "line_count": 2,
        "is_truncated": False,
        "file_size": 22,
    }


def _csv_payload():
    return {
        "run_uuid": "run-1",
        "file_path": "results.csv",
        "columns": ["gene", "score"],
        "row_count": 1,
        "data": [{"gene": "BRCA1", "score": 0.9}],
        "preview_rows": 1,
        "metadata": {"delimiter": ","},
    }


def _bed_payload():
    return {
        "run_uuid": "run-1",
        "file_path": "regions.bed",
        "record_count": 1,
        "records": [
            {
                "chrom": "chr1",
                "chromStart": 100,
                "chromEnd": 200,
                "name": "peak1",
                "score": 500,
                "strand": "+",
                "extra_fields": {},
            }
        ],
        "preview_records": 1,
        "metadata": {"format": "BED6"},
    }


def _summary_payload():
    return {
        "run_uuid": "run-1",
        "sample_name": "sample-a",
        "mode": "DNA",
        "status": "COMPLETED",
        "work_dir": "/tmp/workflow1",
        "file_summary": _job_file_summary_payload(),
        "all_file_counts": {"csv": 1},
        "key_results": {"genes": 1},
        "parsed_reports": {"stats": "ok"},
    }


def _xgenepy_payload():
    return {
        "run_uuid": "run-1",
        "output_dir": "xgenepy_runs/workflow1",
        "required_outputs_present": True,
        "missing_outputs": [],
        "fit_summary": {"row_count": 2},
        "model_metadata": {"trans_model": "log_additive"},
        "run_manifest": {"schema_version": "1.0"},
        "assignments": [{"gene": "G1", "cis_prop": 0.9}],
        "proportion_cis": [{"gene": "G1", "cis_prop": 0.9}],
        "plots": ["xgenepy_runs/workflow1/plots/assignments.png"],
        "metadata": {"preview_rows": 100},
    }


class TestAnalyzerApp:
    def test_root_and_health_endpoints(self):
        client = TestClient(app, raise_server_exceptions=False)

        root = client.get("/")
        health = client.get("/health")

        assert root.status_code == 200
        assert root.json()["service"] == "AGOUTIC Analyzer"
        assert health.status_code == 200
        assert health.json() == {"status": "healthy"}

    def test_list_files_parses_extensions_query(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.discover_files", return_value=_file_listing_payload()) as discover_files:
            response = client.get("/analysis/jobs/run-1/files?extensions=.csv, .tsv")

        assert response.status_code == 200
        assert response.json()["file_count"] == 1
        discover_files.assert_called_once_with("run-1", [".csv", ".tsv"])

    def test_categorize_files_maps_missing_job_to_404(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.categorize_files", side_effect=FileNotFoundError("job missing")):
            response = client.get("/analysis/jobs/run-404/files/categorize")

        assert response.status_code == 404
        assert response.json()["detail"] == "job missing"

    def test_get_file_content_maps_validation_errors(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.read_file_content", side_effect=ValueError("Invalid file path")):
            response = client.get("/analysis/files/content?run_uuid=run-1&file_path=../secret.txt")

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid file path"

    def test_get_file_content_success(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.read_file_content", return_value=_content_payload()) as read_file_content:
            response = client.get("/analysis/files/content?run_uuid=run-1&file_path=results.csv&preview_lines=5")

        assert response.status_code == 200
        assert response.json()["file_path"] == "results.csv"
        read_file_content.assert_called_once_with("run-1", "results.csv", 5)

    def test_download_file_rejects_path_traversal(self, tmp_path):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.get_job_work_dir", return_value=tmp_path):
            response = client.get("/analysis/files/download?run_uuid=run-1&file_path=../../etc/passwd")

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid file path"

    def test_download_file_returns_404_for_missing_file(self, tmp_path):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.get_job_work_dir", return_value=tmp_path):
            response = client.get("/analysis/files/download?run_uuid=run-1&file_path=missing.txt")

        assert response.status_code == 404
        assert response.json()["detail"] == "File not found"

    def test_download_file_streams_existing_file(self, tmp_path):
        client = TestClient(app, raise_server_exceptions=False)
        work_dir = tmp_path / "workflow1"
        work_dir.mkdir()
        target = work_dir / "report.txt"
        target.write_text("analysis complete")

        with patch("analyzer.app.get_job_work_dir", return_value=work_dir):
            response = client.get("/analysis/files/download?run_uuid=run-1&file_path=report.txt")

        assert response.status_code == 200
        assert response.content == b"analysis complete"
        assert 'filename="report.txt"' in response.headers["content-disposition"]

    def test_parse_csv_endpoint_maps_file_errors(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.parse_csv_file", side_effect=FileNotFoundError("missing csv")):
            missing = client.get("/analysis/files/parse/csv?run_uuid=run-1&file_path=missing.csv")

        with patch("analyzer.app.parse_csv_file", side_effect=ValueError("bad csv")):
            invalid = client.get("/analysis/files/parse/csv?run_uuid=run-1&file_path=bad.csv")

        with patch("analyzer.app.parse_csv_file", return_value=_csv_payload()):
            ok = client.get("/analysis/files/parse/csv?run_uuid=run-1&file_path=results.csv&max_rows=2")

        assert missing.status_code == 404
        assert invalid.status_code == 400
        assert ok.status_code == 200
        assert ok.json()["columns"] == ["gene", "score"]

    def test_parse_bed_endpoint_maps_errors_and_success(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.parse_bed_file", side_effect=ValueError("bad bed")):
            invalid = client.get("/analysis/files/parse/bed?run_uuid=run-1&file_path=bad.bed")

        with patch("analyzer.app.parse_bed_file", return_value=_bed_payload()) as parse_bed_file:
            ok = client.get("/analysis/files/parse/bed?run_uuid=run-1&file_path=regions.bed&max_records=5")

        assert invalid.status_code == 400
        assert ok.status_code == 200
        assert ok.json()["records"][0]["chrom"] == "chr1"
        parse_bed_file.assert_called_once_with("run-1", "regions.bed", 5)

    def test_get_summary_maps_missing_job_and_success(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.generate_analysis_summary", side_effect=ValueError("job missing")):
            missing = client.get("/analysis/summary/run-404")

        with patch("analyzer.app.generate_analysis_summary", return_value=_summary_payload()):
            ok = client.get("/analysis/summary/run-1")

        assert missing.status_code == 404
        assert missing.json()["detail"] == "job missing"
        assert ok.status_code == 200
        assert ok.json()["sample_name"] == "sample-a"

    def test_parse_xgenepy_endpoint_maps_errors_and_success(self):
        client = TestClient(app, raise_server_exceptions=False)

        with patch("analyzer.app.parse_xgenepy_outputs", side_effect=FileNotFoundError("missing run")):
            missing = client.get("/analysis/files/parse/xgenepy?work_dir=/tmp/project&output_dir=xgenepy_runs/workflow1")

        with patch("analyzer.app.parse_xgenepy_outputs", side_effect=ValueError("invalid path")):
            invalid = client.get("/analysis/files/parse/xgenepy?work_dir=/tmp/project&output_dir=../../outside")

        with patch("analyzer.app.parse_xgenepy_outputs", return_value=_xgenepy_payload()) as parse_xgenepy_outputs:
            ok = client.get("/analysis/files/parse/xgenepy?work_dir=/tmp/project&output_dir=xgenepy_runs/workflow1&max_rows=1")

        assert missing.status_code == 404
        assert invalid.status_code == 400
        assert ok.status_code == 200
        assert ok.json()["required_outputs_present"] is True
        parse_xgenepy_outputs.assert_called_once()
