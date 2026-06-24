from types import SimpleNamespace

import pytest

from cortex.file_commands import FileCommand, detect_file_intent, execute_file_command, parse_file_command


class _FakeMCPClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def call_tool(self, tool_name: str, **kwargs):
        self.calls.append((tool_name, dict(kwargs)))
        if not self._responses:
            raise AssertionError(f"Unexpected MCP call: {tool_name}")
        return self._responses.pop(0)


def _history_blocks(work_dir: str = "/tmp/proj/workflow10"):
    return [
        SimpleNamespace(
            type="EXECUTION_JOB",
            payload_json=(
                '{"work_directory": "'
                + work_dir
                + '", "run_uuid": "11111111-1111-1111-1111-111111111111", '
                '"sample_name": "tumor-a", "mode": "DNA"}'
            ),
        )
    ]


class TestParseFileCommand:
    def test_parse_simple_read_file_command(self):
        cmd = parse_file_command("/read-file workflow10/reconciled_summary.txt")
        assert cmd == FileCommand(action="read", file_ref="workflow10/reconciled_summary.txt")

    def test_parse_read_file_with_options(self):
        cmd = parse_file_command("/read-file report.html --lines 40 --mode html_raw")
        assert cmd == FileCommand(
            action="read",
            file_ref="report.html",
            preview_lines=40,
            render_mode="html_raw",
        )

    def test_parse_read_file_rejects_bad_mode(self):
        cmd = parse_file_command("/read-file report.html --mode rich_text")
        assert cmd is not None
        assert "Unsupported render mode" in cmd.error


class TestDetectFileIntent:
    def test_detect_local_download_intent_for_file(self):
        cmd = detect_file_intent("download workflow10/reconciled_summary.txt")
        assert cmd == FileCommand(action="download", file_ref="workflow10/reconciled_summary.txt")

    def test_ignore_external_download_request(self):
        assert detect_file_intent("download https://example.com/file.bam") is None


@pytest.mark.asyncio
async def test_execute_file_command_reads_direct_workflow_relative_path(monkeypatch):
    fake_client = _FakeMCPClient(
        [
            {
                "success": True,
                "file_path": "reconciled_summary.txt",
                "content": "Analysis complete.\nAll checks passed.",
                "line_count": 2,
                "is_truncated": False,
                "file_size_bytes": 36,
                "render_mode": "plain",
                "source_extension": ".txt",
            }
        ]
    )

    monkeypatch.setattr("cortex.file_commands.get_service_url", lambda _key: "http://analyzer")
    monkeypatch.setattr("cortex.file_commands.MCPHttpClient", lambda name, base_url: fake_client)

    markdown = await execute_file_command(
        FileCommand(action="read", file_ref="workflow10/reconciled_summary.txt"),
        history_blocks=_history_blocks(),
        project_dir="/tmp/proj",
    )

    assert "File: reconciled_summary.txt" in markdown
    assert "Analysis complete." in markdown
    assert fake_client.calls == [
        (
            "read_file_content",
            {
                "file_path": "reconciled_summary.txt",
                "work_dir": "/tmp/proj/workflow10",
                "preview_lines": 120,
                "render_mode": "auto",
            },
        )
    ]


@pytest.mark.asyncio
async def test_execute_file_command_falls_back_to_find_then_read(monkeypatch):
    fake_client = _FakeMCPClient(
        [
            {
                "success": True,
                "work_dir": "/tmp/proj/workflow10",
                "primary_path": "reports/report.html",
                "matches": ["reports/report.html"],
            },
            {
                "success": True,
                "file_path": "reports/report.html",
                "content": "QC report\nAll checks passed.",
                "line_count": 2,
                "is_truncated": False,
                "file_size_bytes": 28,
                "render_mode": "html_text",
                "source_extension": ".html",
            },
        ]
    )

    monkeypatch.setattr("cortex.file_commands.get_service_url", lambda _key: "http://analyzer")
    monkeypatch.setattr("cortex.file_commands.MCPHttpClient", lambda name, base_url: fake_client)

    markdown = await execute_file_command(
        FileCommand(action="read", file_ref="report.html", render_mode="html_text", preview_lines=25),
        history_blocks=_history_blocks(),
        project_dir="/tmp/proj",
    )

    assert "QC report" in markdown
    assert fake_client.calls == [
        (
            "find_file",
            {"file_name": "report.html", "work_dir": "/tmp/proj/workflow10"},
        ),
        (
            "read_file_content",
            {
                "file_path": "reports/report.html",
                "work_dir": "/tmp/proj/workflow10",
                "preview_lines": 25,
                "render_mode": "html_text",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_execute_file_command_downloads_direct_workflow_relative_path(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "proj" / "workflow10"
    workflow_dir.mkdir(parents=True)
    target_file = workflow_dir / "reconciled_summary.txt"
    target_file.write_text("Analysis complete.\n")

    fake_client = _FakeMCPClient([])
    monkeypatch.setattr("cortex.file_commands.get_service_url", lambda _key: "http://analyzer")
    monkeypatch.setattr("cortex.file_commands.MCPHttpClient", lambda name, base_url: fake_client)

    markdown = await execute_file_command(
        FileCommand(action="download", file_ref="workflow10/reconciled_summary.txt"),
        history_blocks=_history_blocks(work_dir=str(workflow_dir)),
        project_dir=str(tmp_path / "proj"),
    )

    assert "Ready to download" in markdown
    assert str(target_file) in markdown
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_execute_file_command_download_falls_back_to_find(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "proj" / "workflow10"
    reports_dir = workflow_dir / "reports"
    reports_dir.mkdir(parents=True)
    target_file = reports_dir / "report.html"
    target_file.write_text("<h1>QC</h1>")

    fake_client = _FakeMCPClient(
        [
            {
                "success": True,
                "work_dir": str(workflow_dir),
                "primary_path": "reports/report.html",
                "matches": ["reports/report.html"],
            }
        ]
    )

    monkeypatch.setattr("cortex.file_commands.get_service_url", lambda _key: "http://analyzer")
    monkeypatch.setattr("cortex.file_commands.MCPHttpClient", lambda name, base_url: fake_client)

    markdown = await execute_file_command(
        FileCommand(action="download", file_ref="report.html"),
        history_blocks=_history_blocks(work_dir=str(workflow_dir)),
        project_dir=str(tmp_path / "proj"),
    )

    assert str(target_file) in markdown
    assert fake_client.calls == [
        (
            "find_file",
            {"file_name": "report.html", "work_dir": str(workflow_dir)},
        )
    ]