"""Tests for Launchpad MCP active-job cancellation."""

from unittest.mock import patch

import pytest

from launchpad.mcp_tools import LaunchpadMCPTools
from tests.launchpad.test_mcp_tools import FakeAsyncClient, FakeResponse


@pytest.mark.asyncio
async def test_cancel_job_posts_to_active_job_endpoint(monkeypatch):
    fake_client = FakeAsyncClient(
        post_response=FakeResponse(json_data={"status": "cancelled", "run_uuid": "run-1"})
    )
    monkeypatch.setenv("INTERNAL_API_SECRET", "secret")

    with patch("launchpad.mcp_tools.httpx.AsyncClient", return_value=fake_client):
        result = await LaunchpadMCPTools("http://launchpad.local").cancel_job("run-1")

    assert result == {"status": "cancelled", "run_uuid": "run-1"}
    assert fake_client.post_calls == [
        (
            "http://launchpad.local/jobs/run-1/cancel",
            {"headers": {"X-Internal-Secret": "secret"}, "timeout": 30.0},
        )
    ]