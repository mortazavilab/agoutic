"""Tests for launchpad/mcp_tools.py."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from launchpad.mcp_tools import LaunchpadMCPTools


class FakeResponse:
    def __init__(self, *, status_code=200, json_data=None, raise_error=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self._raise_error = raise_error

    def raise_for_status(self):
        if self._raise_error is not None:
            raise self._raise_error

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self, *, post_response=None, get_responses=None, post_error=None):
        self.post_response = post_response or FakeResponse()
        self.get_responses = list(get_responses or [])
        self.post_error = post_error
        self.post_calls = []
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self.post_error is not None:
            raise self.post_error
        return self.post_response

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)


class TestHeaders:
    def test_headers_include_internal_secret(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_SECRET", "top-secret")

        tools = LaunchpadMCPTools("http://launchpad.local/")

        assert tools.server_url == "http://launchpad.local"
        assert tools._headers() == {"X-Internal-Secret": "top-secret"}

    def test_headers_omit_secret_when_unset(self, monkeypatch):
        monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)

        tools = LaunchpadMCPTools("http://launchpad.local")

        assert tools._headers() == {}


class TestSubmitDogmeJob:
    @pytest.mark.asyncio
    async def test_submit_dogme_job_posts_expected_payload(self, monkeypatch):
        fake_client = FakeAsyncClient(
            post_response=FakeResponse(
                json_data={"run_uuid": "run-1", "status": "PENDING"}
            )
        )
        monkeypatch.setenv("INTERNAL_API_SECRET", "secret")

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            result = await tools.submit_dogme_job(
                project_id="proj-1",
                sample_name="sample-a",
                mode="DNA",
                input_directory="/data/input",
                reference_genome=["GRCh38", "mm39"],
                input_type="pod5",
                modkit_filter_threshold=0.75,
                min_cov=4,
                per_mod=7,
                accuracy="hac",
                max_gpu_tasks=2,
                custom_dogme_profile="export MODKITBASE=/cluster/modkit\n",
                custom_dogme_bind_paths=["/cluster/modkit"],
                user_id="user-1",
                username="alim",
                project_slug="project-a",
                execution_mode="slurm",
                ssh_profile_id="prof-1",
                slurm_account="lab",
                slurm_partition="gpu",
                slurm_cpus=8,
                slurm_memory_gb=64,
                slurm_walltime="12:00:00",
                slurm_gpus=1,
                slurm_gpu_type="a100",
                remote_base_path="/remote/agoutic",
                remote_input_path="/remote/agoutic/incoming/sample-a",
                staged_remote_input_path="/remote/agoutic/data/fp123",
                result_destination="both",
            )

        assert result == {"run_uuid": "run-1", "status": "PENDING"}
        assert len(fake_client.post_calls) == 1
        url, kwargs = fake_client.post_calls[0]
        assert url == "http://launchpad.local/jobs/submit"
        assert kwargs["headers"] == {"X-Internal-Secret": "secret"}
        assert isinstance(kwargs["timeout"], httpx.Timeout)
        assert kwargs["timeout"].read == 900.0
        assert kwargs["timeout"].connect == 30.0
        assert kwargs["json"] == {
            "project_id": "proj-1",
            "sample_name": "sample-a",
            "mode": "DNA",
            "input_directory": "/data/input",
            "run_type": "dogme",
            "reference_genome": ["GRCh38", "mm39"],
            "execution_mode": "slurm",
            "input_type": "pod5",
            "modkit_filter_threshold": 0.75,
            "min_cov": 4,
            "per_mod": 7,
            "accuracy": "hac",
            "max_gpu_tasks": 2,
            "custom_dogme_profile": "export MODKITBASE=/cluster/modkit\n",
            "custom_dogme_bind_paths": ["/cluster/modkit"],
            "user_id": "user-1",
            "username": "alim",
            "project_slug": "project-a",
            "ssh_profile_id": "prof-1",
            "slurm_account": "lab",
            "slurm_partition": "gpu",
            "slurm_cpus": 8,
            "slurm_memory_gb": 64,
            "slurm_walltime": "12:00:00",
            "slurm_gpus": 1,
            "slurm_gpu_type": "a100",
            "remote_base_path": "/remote/agoutic",
            "remote_input_path": "/remote/agoutic/incoming/sample-a",
            "staged_remote_input_path": "/remote/agoutic/data/fp123",
            "result_destination": "both",
        }
        assert "modifications" not in kwargs["json"]
        assert "entry_point" not in kwargs["json"]

    @pytest.mark.asyncio
    async def test_submit_dogme_job_passes_script_fields_when_provided(self):
        fake_client = FakeAsyncClient(
            post_response=FakeResponse(
                json_data={"run_uuid": "run-script-1", "status": "RUNNING"}
            )
        )

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            result = await tools.submit_dogme_job(
                project_id="proj-1",
                sample_name="script-a",
                mode="DNA",
                input_directory="/tmp",
                run_type="script",
                script_id="reconcileBams",
                script_path="/opt/agoutic/scripts/reconcile_bams.py",
                script_args=["--threads", "4"],
                script_working_directory="/opt/agoutic/scripts",
            )

        assert result == {"run_uuid": "run-script-1", "status": "RUNNING"}
        _, kwargs = fake_client.post_calls[0]
        assert kwargs["json"]["run_type"] == "script"
        assert kwargs["json"]["script_id"] == "reconcileBams"
        assert kwargs["json"]["script_path"] == "/opt/agoutic/scripts/reconcile_bams.py"
        assert kwargs["json"]["script_args"] == ["--threads", "4"]
        assert kwargs["json"]["script_working_directory"] == "/opt/agoutic/scripts"


class TestRunAllowlistedScript:
    @pytest.mark.asyncio
    async def test_run_allowlisted_script_returns_stdout(self, monkeypatch, tmp_path):
        script_path = tmp_path / "count_bed.py"
        script_path.write_text("print('chr1 | 5')\n")

        class _FakeProcess:
            returncode = 0

            async def communicate(self):
                return (b"chr1 | 5\n", b"")

        monkeypatch.setattr(
            "launchpad.mcp_tools.resolve_allowlisted_script",
            lambda **_kwargs: SimpleNamespace(script_id="analyze_job_results/count_bed", script_path=script_path.resolve()),
        )
        monkeypatch.setattr("launchpad.mcp_tools.normalize_script_args", lambda args: args or [])
        monkeypatch.setattr("launchpad.mcp_tools.validate_script_working_directory", lambda _path: script_path.parent.resolve())
        monkeypatch.setattr("launchpad.mcp_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProcess()))

        tools = LaunchpadMCPTools("http://launchpad.local")
        result = await tools.run_allowlisted_script(
            script_id="analyze_job_results/count_bed",
            script_args=["/tmp/example.bed"],
        )

        assert result["success"] is True
        assert result["script_id"] == "analyze_job_results/count_bed"
        assert result["script_args"] == ["/tmp/example.bed"]
        assert "chr1 | 5" in result["stdout"]

    @pytest.mark.asyncio
    async def test_run_allowlisted_script_extracts_dataframe_from_json_stdout(self, monkeypatch, tmp_path):
        script_path = tmp_path / "count_bed.py"
        script_path.write_text("print('ok')\n")
        json_stdout = json.dumps({
            "columns": ["Sample", "Genome", "Modification", "Chromosome", "Count"],
            "data": [{"Sample": "JamshidP", "Genome": "mm39", "Modification": "inosine", "Chromosome": "chr1", "Count": 7}],
            "row_count": 1,
            "metadata": {"label": "BED chromosome counts"},
        })

        class _FakeProcess:
            returncode = 0

            async def communicate(self):
                return (json_stdout.encode("utf-8"), b"")

        monkeypatch.setattr(
            "launchpad.mcp_tools.resolve_allowlisted_script",
            lambda **_kwargs: SimpleNamespace(script_id="analyze_job_results/count_bed", script_path=script_path.resolve()),
        )
        monkeypatch.setattr("launchpad.mcp_tools.normalize_script_args", lambda args: args or [])
        monkeypatch.setattr("launchpad.mcp_tools.validate_script_working_directory", lambda _path: script_path.parent.resolve())
        monkeypatch.setattr("launchpad.mcp_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProcess()))

        tools = LaunchpadMCPTools("http://launchpad.local")
        result = await tools.run_allowlisted_script(
            script_id="analyze_job_results/count_bed",
            script_args=["--json", "/tmp/example.bed"],
        )

        assert result["success"] is True
        assert result["dataframe"]["row_count"] == 1
        assert result["dataframe"]["metadata"]["label"] == "BED chromosome counts"

    @pytest.mark.asyncio
    async def test_run_allowlisted_script_surfaces_json_error_payload(self, monkeypatch, tmp_path):
        script_path = tmp_path / "reconcile_check.py"
        script_path.write_text("print('bad')\n")
        json_stdout = json.dumps({
            "ok": False,
            "errors": ["/path/workflow2: missing", "/path/workflow3: missing"],
        })

        class _FakeProcess:
            returncode = 1

            async def communicate(self):
                return (json_stdout.encode("utf-8"), b"")

        monkeypatch.setattr(
            "launchpad.mcp_tools.resolve_allowlisted_script",
            lambda **_kwargs: SimpleNamespace(script_id="reconcile_bams/check_workflow_references", script_path=script_path.resolve()),
        )
        monkeypatch.setattr("launchpad.mcp_tools.normalize_script_args", lambda args: args or [])
        monkeypatch.setattr("launchpad.mcp_tools.validate_script_working_directory", lambda _path: script_path.parent.resolve())
        monkeypatch.setattr("launchpad.mcp_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProcess()))

        tools = LaunchpadMCPTools("http://launchpad.local")
        result = await tools.run_allowlisted_script(
            script_id="reconcile_bams/check_workflow_references",
            script_args=["--json", "/tmp/example"],
        )

        assert result["success"] is False
        assert "workflow2: missing" in result["error"]
        assert result["script_output"]["ok"] is False

    @pytest.mark.asyncio
    async def test_stage_remote_sample_posts_expected_payload(self, monkeypatch):
        fake_client = FakeAsyncClient(
            post_response=FakeResponse(
                json_data={"task_id": "stg-abc123", "status": "accepted"}
            )
        )

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            result = await tools.stage_remote_sample(
                project_id="proj-1",
                user_id="user-1",
                username="alim",
                project_slug="project-a",
                sample_name="Jamshid",
                mode="CDNA",
                input_directory="/data/pod5",
                reference_genome=["mm39"],
                ssh_profile_id="profile-1",
                remote_base_path="/remote/agoutic",
            )

        assert result["task_id"] == "stg-abc123"
        url, kwargs = fake_client.post_calls[0]
        assert url == "http://launchpad.local/remote/stage"
        assert isinstance(kwargs["timeout"], httpx.Timeout)
        assert kwargs["timeout"].read == 60.0
        assert kwargs["timeout"].connect == 30.0
        assert kwargs["json"] == {
            "project_id": "proj-1",
            "user_id": "user-1",
            "sample_name": "Jamshid",
            "mode": "CDNA",
            "input_directory": "/data/pod5",
            "reference_genome": ["mm39"],
            "ssh_profile_id": "profile-1",
            "username": "alim",
            "project_slug": "project-a",
            "remote_base_path": "/remote/agoutic",
        }

    @pytest.mark.asyncio
    async def test_stage_remote_sample_posts_remote_input_path_without_local_input(self, monkeypatch):
        fake_client = FakeAsyncClient(
            post_response=FakeResponse(
                json_data={"task_id": "stg-remote1", "status": "accepted"}
            )
        )

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            result = await tools.stage_remote_sample(
                project_id="proj-1",
                user_id="user-1",
                sample_name="Jamshid",
                mode="CDNA",
                ssh_profile_id="profile-1",
                input_directory="",
                remote_input_path="/remote/agoutic/incoming/Jamshid",
            )

        assert result["task_id"] == "stg-remote1"
        _, kwargs = fake_client.post_calls[0]
        assert kwargs["json"] == {
            "project_id": "proj-1",
            "user_id": "user-1",
            "sample_name": "Jamshid",
            "mode": "CDNA",
            "input_directory": "",
            "reference_genome": "mm39",
            "ssh_profile_id": "profile-1",
            "remote_input_path": "/remote/agoutic/incoming/Jamshid",
        }

    @pytest.mark.asyncio
    async def test_stage_remote_sample_wraps_errors(self):
        fake_client = FakeAsyncClient(post_error=httpx.ReadTimeout("", request=None))

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match="Failed to start staging"):
                await tools.stage_remote_sample(
                    project_id="proj-1",
                    user_id="user-1",
                    sample_name="Jamshid",
                    mode="CDNA",
                    input_directory="/data/pod5",
                    reference_genome=["mm39"],
                    ssh_profile_id="profile-1",
                )

    @pytest.mark.asyncio
    async def test_get_staging_task_status(self):
        fake_client = FakeAsyncClient(
            get_responses=[FakeResponse(json_data={"task_id": "stg-abc", "status": "running", "progress": {"file_percent": 42}})]
        )

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            result = await tools.get_staging_task_status("stg-abc")

        assert result["status"] == "running"
        assert result["progress"]["file_percent"] == 42
        url, kwargs = fake_client.get_calls[0]
        assert url == "http://launchpad.local/remote/stage/stg-abc"

    @pytest.mark.asyncio
    async def test_submit_dogme_job_wraps_transport_errors(self):
        fake_client = FakeAsyncClient(post_error=httpx.ConnectError("connection refused"))

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match="Failed to submit job: connection refused"):
                await tools.submit_dogme_job(
                    project_id="proj-1",
                    sample_name="sample-a",
                    mode="DNA",
                    input_directory="/data/input",
                )

    @pytest.mark.asyncio
    async def test_submit_dogme_job_read_timeout_has_actionable_message(self):
        fake_client = FakeAsyncClient(post_error=httpx.ReadTimeout("", request=None))

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match="LAUNCHPAD_SUBMIT_TIMEOUT"):
                await tools.submit_dogme_job(
                    project_id="proj-1",
                    sample_name="sample-a",
                    mode="DNA",
                    input_directory="/data/input",
                )

    @pytest.mark.asyncio
    async def test_submit_dogme_job_wraps_empty_exception_message(self):
        fake_client = FakeAsyncClient(post_error=Exception())

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match=r"Failed to submit job: Exception: Exception\(\)"):
                await tools.submit_dogme_job(
                    project_id="proj-1",
                    sample_name="sample-a",
                    mode="DNA",
                    input_directory="/data/input",
                )


class TestStatusAndReport:
    @pytest.mark.asyncio
    async def test_check_nextflow_status_returns_json(self):
        fake_client = FakeAsyncClient(
            get_responses=[FakeResponse(json_data={"run_uuid": "run-1", "status": "RUNNING"})]
        )

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            result = await tools.check_nextflow_status("run-1")

        assert result == {"run_uuid": "run-1", "status": "RUNNING"}
        assert fake_client.get_calls[0][0] == "http://launchpad.local/jobs/run-1/status"

    @pytest.mark.asyncio
    async def test_check_nextflow_status_uses_extended_status_timeout(self, monkeypatch):
        fake_client = FakeAsyncClient(
            get_responses=[FakeResponse(json_data={"run_uuid": "run-1", "status": "RUNNING"})]
        )
        monkeypatch.setenv("LAUNCHPAD_STATUS_TIMEOUT", "120")

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            await tools.check_nextflow_status("run-1")

        assert fake_client.get_calls[0][1]["timeout"] == 120.0

    @pytest.mark.asyncio
    async def test_sync_job_results_posts_expected_payload(self):
        fake_client = FakeAsyncClient(
            post_response=FakeResponse(
                json_data={"success": True, "status": "outputs_downloaded", "run_uuid": "run-1"}
            )
        )

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            result = await tools.sync_job_results("run-1", force=True)

        assert result["success"] is True
        assert len(fake_client.post_calls) == 1
        url, kwargs = fake_client.post_calls[0]
        assert url == "http://launchpad.local/jobs/run-1/sync-results"
        assert kwargs["params"] == {"force": "true"}

    @pytest.mark.asyncio
    async def test_sync_job_results_uses_extended_sync_timeout(self, monkeypatch):
        fake_client = FakeAsyncClient(
            post_response=FakeResponse(json_data={"success": True, "status": "outputs_downloaded"})
        )
        monkeypatch.setenv("LAUNCHPAD_SYNC_TIMEOUT", "9600")

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            await tools.sync_job_results("run-1")

        assert fake_client.post_calls[0][1]["timeout"] == 9600.0

    @pytest.mark.asyncio
    async def test_sync_job_results_wraps_not_found(self):
        fake_client = FakeAsyncClient(post_response=FakeResponse(status_code=404))

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match="Failed to sync job results: Job run-404 not found"):
                await tools.sync_job_results("run-404")

    @pytest.mark.asyncio
    async def test_sync_job_results_uses_describe_exception_for_empty_errors(self):
        fake_client = FakeAsyncClient(post_error=Exception())

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match=r"Failed to sync job results: Exception: Exception\(\)"):
                await tools.sync_job_results("run-err")

    @pytest.mark.asyncio
    async def test_sync_job_results_read_timeout_has_actionable_message(self):
        fake_client = FakeAsyncClient(post_error=httpx.ReadTimeout("", request=None))

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match="check job status and transfer_state"):
                await tools.sync_job_results("run-timeout")

    @pytest.mark.asyncio
    async def test_check_nextflow_status_wraps_not_found(self):
        fake_client = FakeAsyncClient(get_responses=[FakeResponse(status_code=404)])

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match="Failed to check status: Job run-404 not found"):
                await tools.check_nextflow_status("run-404")

    @pytest.mark.asyncio
    async def test_get_dogme_report_wraps_not_found(self):
        fake_client = FakeAsyncClient(get_responses=[FakeResponse(status_code=404)])

        with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
            tools = LaunchpadMCPTools("http://launchpad.local")
            with pytest.raises(RuntimeError, match="Failed to get report: Job run-404 not found"):
                await tools.get_dogme_report("run-404")

    @pytest.mark.asyncio
    async def test_submit_dogme_nextflow_generates_project_id(self):
        tools = LaunchpadMCPTools("http://launchpad.local")
        tools.submit_dogme_job = AsyncMock(return_value={"run_uuid": "run-55"})
        fake_uuid = SimpleNamespace(hex="1234567890abcdef")

        with patch("uuid.uuid4", return_value=fake_uuid):
            result = await tools.submit_dogme_nextflow(
                sample_name="sample-b",
                input_dir="/data/input",
                mode="RNA",
                reference_genome="mm39",
                modifications="m6A",
            )

        assert result == "run-55"
        tools.submit_dogme_job.assert_awaited_once_with(
            project_id="auto_12345678",
            sample_name="sample-b",
            mode="RNA",
            input_directory="/data/input",
            reference_genome="mm39",
            modifications="m6A",
        )


class TestLocalHelpers:
    @pytest.mark.asyncio
    async def test_find_pod5_directory_returns_metadata_for_existing_directory(self, tmp_path):
        pod5_dir = tmp_path / "pod5s"
        pod5_dir.mkdir()
        (pod5_dir / "a.pod5").write_bytes(b"1234")
        (pod5_dir / "b.pod5").write_bytes(b"12")

        tools = LaunchpadMCPTools("http://launchpad.local")
        result = await tools.find_pod5_directory(str(pod5_dir))

        assert result["found"] is True
        assert result["path"] == str(pod5_dir)
        assert result["file_count"] == 2
        assert result["total_size_gb"] > 0

    @pytest.mark.asyncio
    async def test_generate_dogme_config_returns_serialized_config(self):
        tools = LaunchpadMCPTools("http://launchpad.local")

        result = await tools.generate_dogme_config(
            sample_name="sample-c",
            read_type="DNA",
            genome="GRCh38",
            modifications="5mC",
        )

        assert result["sample_name"] == "sample-c"
        assert result["read_type"] == "DNA"
        parsed_config = json.loads(result["config"])
        assert "sample = 'sample-c'" in parsed_config
        assert "modifications = '5mC'" in parsed_config

    @pytest.mark.asyncio
    async def test_scaffold_dogme_dir_reports_missing_and_existing_inputs(self, tmp_path):
        tools = LaunchpadMCPTools("http://launchpad.local")
        missing = await tools.scaffold_dogme_dir("sample-d", str(tmp_path / "missing"))

        existing_dir = tmp_path / "input"
        existing_dir.mkdir()
        existing = await tools.scaffold_dogme_dir("sample-d", str(existing_dir))

        assert missing == {
            "success": False,
            "work_dir": None,
            "message": f"Input directory not found: {tmp_path / 'missing'}",
        }
        assert existing == {
            "success": True,
            "work_dir": str(existing_dir),
            "message": "Workspace validated for sample-d",
        }
