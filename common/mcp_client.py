"""
Generic MCP Client over HTTP/SSE

Connects to any MCP server running over HTTP (FastMCP's http_app or similar).
Replaces the per-server subprocess-based MCP clients with a single HTTP client.

Usage:
    client = MCPHttpClient("encode", "http://localhost:8006")
    await client.connect()
    result = await client.call_tool("search_by_biosample", search_term="K562")
    await client.disconnect()
"""

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class MCPHttpClient:
    """
    MCP client that connects to a running MCP server over HTTP/SSE.

    Works with FastMCP's http_app (which exposes /mcp/ endpoints) and any
    MCP server using the Streamable HTTP transport.
    """

    def __init__(self, name: str, base_url: str, timeout: float = 120.0):
        """
        Args:
            name: Human-readable name for logging (e.g., "encode", "server3")
            base_url: Base URL of the running MCP server (e.g., "http://localhost:8006")
            timeout: HTTP request timeout in seconds
        """
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self._request_id = 0

    async def connect(self) -> None:
        """
        Open an HTTP session to the MCP server.
        Performs an MCP initialize handshake via JSON-RPC over HTTP.
        """
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=True,
        )

        # MCP initialize handshake
        self._request_id += 1
        init_request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": f"agoutic-{self.name}-client",
                    "version": "2.0.0",
                },
            },
        }

        try:
            response = await self._client.post(
                "/mcp/",
                json=init_request,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            response.raise_for_status()

            result = self._parse_response(response)
            if "error" in result:
                raise RuntimeError(f"MCP initialization failed: {result['error']}")

            # Store session ID if server provides one
            self._session_id = response.headers.get("mcp-session-id")

            logger.info(f"✅ Connected to MCP server: {self.name} at {self.base_url}")

        except httpx.ConnectError:
            await self._cleanup()
            raise RuntimeError(
                f"Cannot connect to {self.name} MCP server at {self.base_url}. "
                f"Is it running? Start it with: agoutic_servers.sh"
            )
        except Exception as e:
            await self._cleanup()
            raise RuntimeError(f"Failed to connect to {self.name} MCP server: {e}")

    async def disconnect(self) -> None:
        """Close the HTTP session."""
        await self._cleanup()
        logger.info(f"🔌 Disconnected from MCP server: {self.name}")

    async def call_tool(self, tool_name: str, **kwargs) -> Any:
        """
        Call a tool on the MCP server via JSON-RPC over HTTP.

        Args:
            tool_name: Name of the MCP tool to invoke
            **kwargs: Tool parameters (None values are filtered out)

        Returns:
            Parsed tool result (dict, list, str, etc.)
        """
        if not self._client:
            raise RuntimeError(f"Not connected to {self.name}. Call connect() first.")

        # Filter out None values (MCP doesn't accept null for optional params)
        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": filtered_kwargs,
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        try:
            response = await self._client.post(
                "/mcp/",
                json=request,
                headers=headers,
            )
            response.raise_for_status()

            rpc_response = self._parse_response(response)

            if "error" in rpc_response:
                raise RuntimeError(f"Tool error: {rpc_response['error']}")

            return self._extract_result(rpc_response)

        except httpx.ConnectError:
            raise RuntimeError(
                f"{self.name} MCP server at {self.base_url} is not reachable. "
                f"Is it running?"
            )
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"MCP communication error with {self.name}: {e}")

    async def list_tools(self) -> list[dict]:
        """
        List available tools on the MCP server.

        Returns:
            List of tool descriptors with name, description, inputSchema.
        """
        if not self._client:
            raise RuntimeError(f"Not connected to {self.name}. Call connect() first.")

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/list",
            "params": {},
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        response = await self._client.post(
            "/mcp/",
            json=request,
            headers=headers,
        )
        response.raise_for_status()

        rpc_response = self._parse_response(response)
        if "error" in rpc_response:
            raise RuntimeError(f"tools/list error: {rpc_response['error']}")

        return rpc_response.get("result", {}).get("tools", [])

    # --- Internal helpers ---

    def _extract_result(self, rpc_response: dict) -> Any:
        """Extract the tool result from a JSON-RPC response."""
        logger.info(f"[MCP HTTP] Extracting result from RPC response keys: {list(rpc_response.keys())}")
        rpc_result = rpc_response.get("result")
        if not rpc_result:
            logger.warning(f"[MCP HTTP] No 'result' field in RPC response")
            return {}

        # FastMCP wraps tool results in content[0].text as a JSON string
        if isinstance(rpc_result, dict) and "content" in rpc_result:
            content_list = rpc_result.get("content", [])
            logger.info(f"[MCP HTTP] Found content list with {len(content_list)} items")
            if content_list and len(content_list) > 0:
                text_content = content_list[0].get("text", "")
                logger.info(f"[MCP HTTP] Text content length: {len(text_content)} chars")
                if text_content:
                    try:
                        parsed = json.loads(text_content)
                        logger.info(f"[MCP HTTP] Successfully parsed JSON from text content")
                        return parsed
                    except json.JSONDecodeError as e:
                        logger.warning(f"[MCP HTTP] Failed to parse JSON from text content: {e}")
                        return text_content

            # Check for isError flag from FastMCP
            if rpc_result.get("isError", False):
                raise RuntimeError(f"Tool returned error: {rpc_result}")
            logger.warning(f"[MCP HTTP] Returning empty dict - no text content found")
            return {}

        # Fallback for non-FastMCP format
        logger.info(f"[MCP HTTP] Using fallback - returning rpc_result directly")
        return rpc_result

    def _parse_response(self, response: httpx.Response) -> dict:
        """
        Parse HTTP response, handling both JSON and SSE formats.
        FastMCP's StreamableHTTP can return either format depending on Accept header.
        """
        content_type = response.headers.get("content-type", "").lower()
        
        # If response is SSE format (text/event-stream)
        if "text/event-stream" in content_type:
            text = response.text.strip()
            logger.info(f"[MCP HTTP] SSE response (first 500 chars): {text[:500]}")
            # SSE format: "data: {...}\n\n"
            # Extract JSON from SSE wrapper
            for line in text.split("\n"):
                if line.startswith("data: "):
                    json_str = line[6:]  # Remove "data: " prefix
                    try:
                        result = json.loads(json_str)
                        logger.info(f"[MCP HTTP] Parsed SSE JSON successfully")
                        return result
                    except json.JSONDecodeError as e:
                        logger.warning(f"[MCP HTTP] Failed to parse SSE line: {line[:100]} - {e}")
                        continue
            # If no valid JSON found in SSE stream
            raise RuntimeError(f"No valid JSON in SSE response: {text[:200]}")
        
        # Default to JSON parsing
        result = response.json()
        logger.info(f"[MCP HTTP] JSON response received")
        return result

    async def _cleanup(self) -> None:
        """Close HTTP client if open."""
        if self._client:
            await self._client.aclose()
            self._client = None
            self._session_id = None
