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
        remote_input_path="/scratch/u1/input",
        remote_work_path="/scratch/u1/work",
        remote_output_path="/scratch/u1/work/output",
        remote_reference_cache_root="/scratch/u1/cache/references",
        remote_data_cache_root="/scratch/u1/cache/data",
        cache_preflight=cache_preflight,
        result_destination="local",
    )

    parsed = json.loads(result)
    assert parsed["run_uuid"] == "run-123"

    submit_mock.assert_awaited_once_with(
        project_id="proj-1",
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
        resume_from_dir=None,
        execution_mode="slurm",
        ssh_profile_id="profile-1",
        slurm_account=None,
        slurm_partition=None,
        slurm_cpus=None,
        slurm_memory_gb=None,
        slurm_walltime=None,
        slurm_gpus=None,
        slurm_gpu_type=None,
        remote_input_path="/scratch/u1/input",
        remote_work_path="/scratch/u1/work",
        remote_output_path="/scratch/u1/work/output",
        remote_reference_cache_root="/scratch/u1/cache/references",
        remote_data_cache_root="/scratch/u1/cache/data",
        cache_preflight=cache_preflight,
        result_destination="local",
    )
