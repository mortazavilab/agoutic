"""Tests for launchpad/mcp_server.py wrappers."""

import json
import sys
import types
from unittest.mock import AsyncMock

import pytest


class _FakeFastMCP:
    def __init__(self, *args, **kwargs):
        pass

    def tool(self):
        def decorator(func):
            return func
        return decorator

    def http_app(self):
        class _App:
            routes = []
        return _App()


_fastmcp_module = types.ModuleType("fastmcp")
_fastmcp_module.FastMCP = _FakeFastMCP
sys.modules.setdefault("fastmcp", _fastmcp_module)

from launchpad import mcp_server


@pytest.mark.asyncio
async def test_run_allowlisted_script_forwards_arguments(monkeypatch):
    run_mock = AsyncMock(return_value={"success": True, "stdout": "chr1 | 5\n"})
    monkeypatch.setattr(mcp_server.tools, "run_allowlisted_script", run_mock)

    result = await mcp_server.run_allowlisted_script(
        script_id="analyze_job_results/count_bed",
        script_args=["/tmp/example.bed"],
        timeout_seconds=30.0,
    )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert "chr1 | 5" in parsed["stdout"]
    run_mock.assert_awaited_once_with(
        script_id="analyze_job_results/count_bed",
        script_path=None,
        script_args=["/tmp/example.bed"],
        script_working_directory=None,
        timeout_seconds=30.0,
    )


@pytest.mark.asyncio
async def test_submit_dogme_job_forwards_cache_fields(monkeypatch):
    submit_mock = AsyncMock(return_value={"run_uuid": "run-123", "status": "PENDING"})
    monkeypatch.setattr(mcp_server.tools, "submit_dogme_job", submit_mock)

    cache_preflight = {
        "scope": "per_user_cross_project",
        "status": "ready",
        "reference_actions": [{"reference_id": "mm39", "action": "reuse"}],
    }

    result = await mcp_server.submit_dogme_job(
        sample_name="Jamshid",
        mode="CDNA",
        input_directory="/data/pod5",
        reference_genome=["mm39"],
        project_id="proj-1",
        user_id="user-1",
        execution_mode="slurm",
        ssh_profile_id="profile-1",
        remote_base_path="/remote/u1/agoutic",
        cache_preflight=cache_preflight,
        result_destination="local",
    )

    parsed = json.loads(result)
    assert parsed["run_uuid"] == "run-123"

    submit_mock.assert_awaited_once_with(
        project_id="proj-1",
        run_type="dogme",
        user_id="user-1",
        username=None,
        project_slug=None,
        sample_name="Jamshid",
        mode="CDNA",
        reference_genome=["mm39"],
        input_directory="/data/pod5",
        modifications=None,
        input_type=None,
        entry_point=None,
        modkit_filter_threshold=None,
        min_cov=None,
        per_mod=None,
        accuracy=None,
        max_gpu_tasks=None,
        custom_dogme_profile=None,
        custom_dogme_bind_paths=None,
        resume_from_dir=None,
        execution_mode="slurm",
        ssh_profile_id="profile-1",
        slurm_account=None,
        slurm_partition=None,
        slurm_gpu_account=None,
        slurm_gpu_partition=None,
        slurm_cpus=None,
        slurm_memory_gb=None,
        slurm_walltime=None,
        slurm_gpus=None,
        slurm_gpu_type=None,
        remote_base_path="/remote/u1/agoutic",
        remote_input_path=None,
        staged_remote_input_path=None,
        cache_preflight=cache_preflight,
        result_destination="local",
        script_id=None,
        script_path=None,
        script_args=None,
        script_working_directory=None,
    )


@pytest.mark.asyncio
async def test_stage_remote_sample_forwards_arguments(monkeypatch):
    stage_mock = AsyncMock(return_value={"remote_data_path": "/remote/u1/agoutic/data/fp1"})
    monkeypatch.setattr(mcp_server.tools, "stage_remote_sample", stage_mock)

    result = await mcp_server.stage_remote_sample(
        project_id="proj-1",
        user_id="user-1",
        sample_name="Jamshid",
        mode="CDNA",
        input_directory="/data/pod5",
        ssh_profile_id="profile-1",
        reference_genome=["mm39"],
        remote_base_path="/remote/u1/agoutic",
    )

    parsed = json.loads(result)
    assert parsed["remote_data_path"] == "/remote/u1/agoutic/data/fp1"
    stage_mock.assert_awaited_once_with(
        project_id="proj-1",
        user_id="user-1",
        username=None,
        project_slug=None,
        sample_name="Jamshid",
        mode="CDNA",
        input_directory="/data/pod5",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        remote_base_path="/remote/u1/agoutic",
        remote_input_path=None,
    )


@pytest.mark.asyncio
async def test_submit_dogme_job_forwards_remote_input_path(monkeypatch):
    submit_mock = AsyncMock(return_value={"run_uuid": "run-remote", "status": "PENDING"})
    monkeypatch.setattr(mcp_server.tools, "submit_dogme_job", submit_mock)

    result = await mcp_server.submit_dogme_job(
        sample_name="Jamshid",
        mode="DNA",
        input_directory="",
        project_id="proj-1",
        user_id="user-1",
        execution_mode="slurm",
        ssh_profile_id="profile-1",
        remote_input_path="/remote/agoutic/incoming/Jamshid",
    )

    parsed = json.loads(result)
    assert parsed["run_uuid"] == "run-remote"
    submit_mock.assert_awaited_once()
    assert submit_mock.await_args.kwargs["remote_input_path"] == "/remote/agoutic/incoming/Jamshid"


@pytest.mark.asyncio
async def test_submit_dogme_job_forwards_custom_profile_fields(monkeypatch):
    submit_mock = AsyncMock(return_value={"run_uuid": "run-custom", "status": "PENDING"})
    monkeypatch.setattr(mcp_server.tools, "submit_dogme_job", submit_mock)

    await mcp_server.submit_dogme_job(
        sample_name="Jamshid",
        mode="DNA",
        input_directory="/data/pod5",
        project_id="proj-1",
        custom_dogme_profile="export MODKITBASE=/cluster/modkit\n",
        custom_dogme_bind_paths=["/cluster/modkit"],
    )

    assert submit_mock.await_args.kwargs["custom_dogme_profile"] == "export MODKITBASE=/cluster/modkit\n"
    assert submit_mock.await_args.kwargs["custom_dogme_bind_paths"] == ["/cluster/modkit"]


@pytest.mark.asyncio
async def test_sync_job_results_forwards_arguments(monkeypatch):
    sync_mock = AsyncMock(return_value={"success": True, "status": "outputs_downloaded"})
    monkeypatch.setattr(mcp_server.tools, "sync_job_results", sync_mock)

    result = await mcp_server.sync_job_results(
        run_uuid="run-123",
        force=True,
    )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["status"] == "outputs_downloaded"
    sync_mock.assert_awaited_once_with(run_uuid="run-123", force=True)
