"""
Tests for common/mcp_client.py — MCPHttpClient internals.

Tests _extract_result() and _parse_response() with various payload shapes
without needing a real MCP server running.
"""

import json
import pytest
import httpx
from unittest.mock import AsyncMock

from common.mcp_client import MCPHttpClient


@pytest.fixture()
def client():
    """Create an MCPHttpClient without connecting."""
    return MCPHttpClient("test", "http://localhost:9999")


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
class TestInit:
    def test_default_timeout(self, client):
        assert client.timeout == 120.0

    def test_custom_timeout(self):
        c = MCPHttpClient("x", "http://localhost:1234", timeout=30.0)
        assert c.timeout == 30.0

    def test_base_url_trailing_slash_stripped(self):
        c = MCPHttpClient("x", "http://localhost:1234/")
        assert c.base_url == "http://localhost:1234"

    def test_initial_state(self, client):
        assert client._client is None
        assert client._session_id is None
        assert client._request_id == 0


# ---------------------------------------------------------------------------
# _extract_result
# ---------------------------------------------------------------------------
class TestExtractResult:
    """Test the _extract_result method with various RPC response shapes."""

    def test_empty_result(self, client):
        """No 'result' key returns empty dict."""
        assert client._extract_result({}) == {}

    def test_direct_result_dict(self, client):
        """Non-FastMCP: result is a plain dict, returned as-is."""
        rpc = {"result": {"experiments": [1, 2, 3]}}
        assert client._extract_result(rpc) == {"experiments": [1, 2, 3]}

    def test_fastmcp_content_json(self, client):
        """FastMCP wraps text content as JSON string — should be parsed."""
        inner = {"data": [1, 2], "count": 2}
        rpc = {
            "result": {
                "content": [{"text": json.dumps(inner)}],
            }
        }
        assert client._extract_result(rpc) == inner

    def test_fastmcp_content_plain_text(self, client):
        """Non-JSON text content — returned as raw string."""
        rpc = {
            "result": {
                "content": [{"text": "Just a string"}],
            }
        }
        assert client._extract_result(rpc) == "Just a string"

    def test_fastmcp_content_empty(self, client):
        """Empty content list returns empty dict."""
        rpc = {
            "result": {
                "content": [],
            }
        }
        assert client._extract_result(rpc) == {}

    def test_fastmcp_is_error_raises(self, client):
        """isError=True with text should raise RuntimeError."""
        rpc = {
            "result": {
                "isError": True,
                "content": [{"text": "Something went wrong"}],
            }
        }
        with pytest.raises(RuntimeError, match="MCP tool error"):
            client._extract_result(rpc)

    def test_fastmcp_is_error_no_content_raises(self, client):
        """isError=True with empty content still raises."""
        rpc = {
            "result": {
                "isError": True,
                "content": [],
            }
        }
        with pytest.raises(RuntimeError, match="MCP tool returned error"):
            client._extract_result(rpc)

    def test_fastmcp_is_error_false_passes(self, client):
        """isError=False behaves like normal extraction."""
        inner = {"status": "ok"}
        rpc = {
            "result": {
                "isError": False,
                "content": [{"text": json.dumps(inner)}],
            }
        }
        assert client._extract_result(rpc) == inner

    def test_result_none(self, client):
        """result=None returns empty dict."""
        rpc = {"result": None}
        assert client._extract_result(rpc) == {}


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------
class TestParseResponse:
    """Test the _parse_response method with JSON and SSE responses."""

    def _make_response(self, *, content_type, body):
        """Helper to create a fake httpx.Response."""
        return httpx.Response(
            status_code=200,
            headers={"content-type": content_type},
            text=body,
        )

    def test_json_response(self, client):
        """Standard JSON content type returns parsed dict."""
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        resp = self._make_response(content_type="application/json", body=body)
        parsed = client._parse_response(resp)
        assert parsed["result"]["ok"] is True

    def test_sse_response(self, client):
        """SSE format ('data: {...}') is correctly parsed."""
        inner = {"jsonrpc": "2.0", "id": 1, "result": {"value": 42}}
        body = f"data: {json.dumps(inner)}\n\n"
        resp = self._make_response(content_type="text/event-stream", body=body)
        parsed = client._parse_response(resp)
        assert parsed["result"]["value"] == 42

    def test_sse_skips_non_data_lines(self, client):
        """SSE parser ignores comment lines and blank lines."""
        inner = {"jsonrpc": "2.0", "id": 1, "result": {}}
        body = f": this is a comment\n\ndata: {json.dumps(inner)}\n\n"
        resp = self._make_response(content_type="text/event-stream", body=body)
        parsed = client._parse_response(resp)
        assert parsed["jsonrpc"] == "2.0"

    def test_sse_invalid_json_raises(self, client):
        """SSE with no valid JSON data raises RuntimeError."""
        body = "data: not-valid-json\n\n"
        resp = self._make_response(content_type="text/event-stream", body=body)
        with pytest.raises(RuntimeError, match="No valid JSON in SSE"):
            client._parse_response(resp)


# ---------------------------------------------------------------------------
# call_tool / connect / disconnect — just check error guards
# ---------------------------------------------------------------------------
class TestCallToolGuards:
    @pytest.mark.asyncio
    async def test_call_tool_without_connect_raises(self, client):
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.call_tool("anything")

    @pytest.mark.asyncio
    async def test_list_tools_without_connect_raises(self, client):
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.list_tools()

    @pytest.mark.asyncio
    async def test_cleanup_when_no_client(self, client):
        """_cleanup should be safe when _client is None."""
        await client._cleanup()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_cleanup_clears_state(self):
        c = MCPHttpClient("test", "http://localhost:8006")
        c._client = AsyncMock()
        c._session_id = "test-session"
        await c._cleanup()
        assert c._client is None
        assert c._session_id is None
