"""
Server 3 MCP Server Wrapper
Exposes Server 3 tools via the Model Context Protocol (MCP).

Allows Server 1's LLM Agent to directly invoke job management tools.
Runs alongside the REST API server.
"""
import asyncio
import sys
from typing import Any
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent, ToolResult
import mcp.server.stdio
import mcp.types as types

from server3.mcp_tools import Server3MCPTools, TOOL_REGISTRY

# Initialize MCP server
server = mcp.server.stdio.StdioServer("server3-mcp")
tools = Server3MCPTools()

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name=tool_name,
            description=tool_info["description"],
            inputSchema=tool_info["input_schema"],
        )
        for tool_name, tool_info in TOOL_REGISTRY.items()
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent | ToolResult]:
    """Execute a tool call."""
    try:
        # Route to the appropriate tool method
        if name == "submit_dogme_job":
            result = await tools.submit_dogme_job(**arguments)
        elif name == "check_nextflow_status":
            result = await tools.check_nextflow_status(**arguments)
        elif name == "get_dogme_report":
            result = await tools.get_dogme_report(**arguments)
        elif name == "submit_dogme_nextflow":
            result = await tools.submit_dogme_nextflow(**arguments)
        elif name == "find_pod5_directory":
            result = await tools.find_pod5_directory(**arguments)
        elif name == "generate_dogme_config":
            result = await tools.generate_dogme_config(**arguments)
        elif name == "scaffold_dogme_dir":
            result = await tools.scaffold_dogme_dir(**arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
        
        # Convert result to proper format
        if isinstance(result, dict):
            import json
            result_text = json.dumps(result, indent=2)
        else:
            result_text = str(result)
        
        return [TextContent(type="text", text=result_text)]
    
    except Exception as e:
        error_msg = f"Tool {name} failed: {str(e)}"
        return [ToolResult(
            content=[TextContent(type="text", text=error_msg)],
            isError=True,
        )]

@server.initialize()
async def initialize(options: InitializationOptions) -> types.InitializationResult:
    """Initialize the MCP server."""
    return types.InitializationResult(
        protocolVersion="2024-11-05",
        capabilities=types.ServerCapabilities(
            tools=types.ToolCapabilities(
                listChanged=False,
            )
        ),
        serverInfo=types.Implementation(
            name="AGOUTIC-Server3-MCP",
            version="0.3.0",
        ),
    )

async def main():
    """Run the MCP server."""
    async with server:
        print("🚀 Server 3 MCP server started on stdio", file=sys.stderr)
        print("Available tools:", file=sys.stderr)
        for tool_name in TOOL_REGISTRY.keys():
            print(f"  - {tool_name}", file=sys.stderr)
        
        # Keep the server running
        await server.wait_for_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
