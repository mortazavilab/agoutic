from types import SimpleNamespace

import pytest

from cortex.list_commands import ListCommand, detect_list_intent, execute_list_command, parse_list_command


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


class _FakeSession:
    def execute(self, _query):
        raise AssertionError("Unexpected DB execute call in this test")


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


class TestParseListCommand:
    def test_parse_list_samples(self):
        cmd = parse_list_command("/list samples")
        assert cmd == ListCommand(action="samples")

    def test_parse_list_staged_with_profile(self):
        cmd = parse_list_command("/list staged --profile hpc3")
        assert cmd == ListCommand(action="staged", profile_ref="hpc3")

    def test_parse_list_dfs(self):
        cmd = parse_list_command("/list dfs")
        assert cmd == ListCommand(action="dfs")

    def test_parse_list_files_with_project_scope_and_target(self):
        cmd = parse_list_command("/list files --project data")
        assert cmd == ListCommand(action="files", target_ref="data", project_scope=True)


class TestDetectListIntent:
    def test_detect_local_samples(self):
        cmd = detect_list_intent("show my samples")
        assert cmd == ListCommand(action="samples")

    def test_detect_staged_samples_with_profile(self):
        cmd = detect_list_intent("show staged samples on hpc3")
        assert cmd == ListCommand(action="staged", profile_ref="hpc3")

    def test_detect_imported_samples(self):
        cmd = detect_list_intent("what imported samples do i have")
        assert cmd == ListCommand(action="imported")

    def test_detect_workflows(self):
        cmd = detect_list_intent("show workflows in this project")
        assert cmd == ListCommand(action="workflows")

    def test_detect_files_in_workflow(self):
        cmd = detect_list_intent("list files in workflow7/annot")
        assert cmd == ListCommand(action="files", target_ref="workflow7/annot")


@pytest.mark.asyncio
async def test_execute_list_samples_renders_inventory_rows(monkeypatch):
    monkeypatch.setattr(
        "cortex.list_commands._list_local_sample_rows",
        lambda _session, _user_id: [
            {
                "sample_name": "tumor-a",
                "file_count": 2,
                "total_size": "1.5 GB",
                "sources": "upload, url",
                "projects": "proj-a, proj-b",
                "added": "2026-05-24",
            }
        ],
    )

    markdown = await execute_list_command(_FakeSession(), ListCommand(action="samples"), user_id="user-1")

    assert "Local samples (1):" in markdown
    assert "tumor-a" in markdown
    assert "proj-a, proj-b" in markdown


@pytest.mark.asyncio
async def test_execute_list_staged_renders_inventory_rows(monkeypatch):
    monkeypatch.setattr(
        "cortex.list_commands._list_staged_sample_rows",
        lambda _session, _user_id, _profile_ref: [
            {
                "sample_name": "tumor-a",
                "mode": "DNA",
                "profile": "hpc3",
                "status": "READY",
                "remote_data_path": "/dfs9/user/agoutic/data/tumor-a",
                "last_staged": "2026-05-24",
                "last_used": "2026-05-24",
            }
        ],
    )

    markdown = await execute_list_command(
        _FakeSession(),
        ListCommand(action="staged", profile_ref="hpc3"),
        user_id="user-1",
    )

    assert "Staged samples (1):" in markdown
    assert "/dfs9/user/agoutic/data/tumor-a" in markdown


@pytest.mark.asyncio
async def test_execute_list_imported_renders_inventory_rows(monkeypatch):
    monkeypatch.setattr(
        "cortex.list_commands._list_imported_sample_rows",
        lambda _session, _user_id: [
            {
                "sample_name": "tumor-a",
                "project": "proj-a",
                "workflow": "workflow7",
                "source_kind": "slurm",
                "source_path": "/remote/project/workflow3",
                "status": "COMPLETED",
                "completed": "2026-05-24",
            }
        ],
    )

    markdown = await execute_list_command(_FakeSession(), ListCommand(action="imported"), user_id="user-1")

    assert "Imported samples (1):" in markdown
    assert "workflow7" in markdown
    assert "/remote/project/workflow3" in markdown


@pytest.mark.asyncio
async def test_execute_list_dfs_reuses_existing_dataframe_renderer(monkeypatch):
    monkeypatch.setattr(
        "cortex.list_commands._collect_df_map",
        lambda *_args, **_kwargs: {
            1: {
                "columns": ["sample", "reads"],
                "data": [{"sample": "tumor-a", "reads": 5}],
                "row_count": 1,
                "label": "Tumor Reads",
            }
        },
    )

    markdown = await execute_list_command(
        _FakeSession(),
        ListCommand(action="dfs"),
        user_id="user-1",
        project_id="proj-1",
        history_blocks=[],
    )

    assert "DF1" in markdown
    assert "Tumor Reads" in markdown


@pytest.mark.asyncio
async def test_execute_list_workflows_renders_inventory_rows(monkeypatch):
    monkeypatch.setattr(
        "cortex.list_commands._list_workflow_rows",
        lambda _session, _project_id, project_dir="": [
            {
                "workflow": "workflow7",
                "display_name": "tumor-a",
                "tracked": "yes",
                "on_disk": "yes",
                "status": "COMPLETED",
                "run_uuid": "run-7",
            }
        ],
    )

    markdown = await execute_list_command(
        _FakeSession(),
        ListCommand(action="workflows"),
        user_id="user-1",
        project_id="proj-1",
        project_dir="/tmp/proj",
    )

    assert "Workflows in this project (1):" in markdown
    assert "workflow7" in markdown
    assert "run-7" in markdown


@pytest.mark.asyncio
async def test_execute_list_files_uses_active_workflow_context(monkeypatch):
    fake_client = _FakeMCPClient(
        [
            {
                "success": True,
                "work_dir": "/tmp/proj/workflow10",
                "file_count": 2,
                "files": [
                    {"path": "annot/", "name": "annot/", "size": 0, "modified_time": "2026-05-24T12:00:00Z"},
                    {"path": "report.txt", "name": "report.txt", "size": 128, "modified_time": "2026-05-24T12:00:00Z"},
                ],
            }
        ]
    )

    monkeypatch.setattr("cortex.list_commands.get_service_url", lambda _key: "http://analyzer")
    monkeypatch.setattr("cortex.list_commands.MCPHttpClient", lambda name, base_url: fake_client)

    markdown = await execute_list_command(
        _FakeSession(),
        ListCommand(action="files"),
        user_id="user-1",
        project_id="proj-1",
        project_dir="/tmp/proj",
        history_blocks=_history_blocks(),
    )

    assert "Files under `/tmp/proj/workflow10` (2):" in markdown
    assert "report.txt" in markdown
    assert fake_client.calls == [
        (
            "list_job_files",
            {
                "work_dir": "/tmp/proj/workflow10",
                "max_depth": None,
            },
        )
    ]