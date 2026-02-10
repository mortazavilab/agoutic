"""
MCP Server for Server4 (Analysis Server).
Exposes analysis tools via Model Context Protocol.
"""

import asyncio
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from server4.mcp_tools import TOOL_REGISTRY, TOOL_SCHEMAS
from server4.config import SERVER4_MCP_PORT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("server4.mcp_server")

# Create MCP server instance
mcp_server = Server("agoutic-server4-analysis")


# ==================== Tool Registration ====================

@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available analysis tools."""
    tools = []
    
    for tool_name, schema in TOOL_SCHEMAS.items():
        tools.append(Tool(
            name=tool_name,
            description=schema["description"],
            inputSchema=schema["parameters"]
        ))
    
    logger.info(f"Listed {len(tools)} tools")
    return tools


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a tool with given arguments."""
    logger.info(f"Tool called: {name} with arguments: {arguments}")
    
    # Check if tool exists
    if name not in TOOL_REGISTRY:
        error_msg = f"Unknown tool: {name}"
        logger.error(error_msg)
        return [TextContent(
            type="text",
            text=f'{{"success": false, "error": "{error_msg}"}}'
        )]
    
    try:
        # Get tool function
        tool_func = TOOL_REGISTRY[name]
        
        # Execute tool
        result = await tool_func(**arguments)
        
        logger.info(f"Tool {name} executed successfully")
        return [TextContent(type="text", text=result)]
    
    except TypeError as e:
        error_msg = f"Invalid arguments for tool {name}: {str(e)}"
        logger.error(error_msg)
        return [TextContent(
            type="text",
            text=f'{{"success": false, "error": "{error_msg}"}}'
        )]
    
    except Exception as e:
        error_msg = f"Tool execution failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return [TextContent(
            type="text",
            text=f'{{"success": false, "error": "{error_msg}"}}'
        )]


# ==================== Server Entry Point ====================

async def main():
    """Run the MCP server."""
    logger.info("Starting Server4 MCP Server (Analysis Server)")
    logger.info(f"Registered tools: {list(TOOL_REGISTRY.keys())}")
    
    # Run server with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        logger.info("MCP Server running on stdio")
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options()
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        raise
