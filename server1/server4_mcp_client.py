"""
Server 1 - MCP Client for Server 4 Integration
Allows the Agent Engine to call Server 4 analysis tools via MCP protocol.

This provides a seamless interface for the LLM agent to analyze
completed Dogme job results (txt, csv, bed files).
"""
import asyncio
import json
from typing import Optional, List

from server1.mcp_client import Server3MCPClient


class Server4MCPClient:
    """
    MCP client for communicating with Server 4 MCP server (Analysis Server).
    
    Maintains a subprocess connection to the Server 4 MCP server
    and handles tool invocation for file analysis.
    """
    
    def __init__(self, mcp_server_command: Optional[str] = None):
        """
        Initialize MCP client for Server 4.
        
        Args:
            mcp_server_command: Command to start Server 4 MCP server
                               (default: uses Python module)
        """
        self.mcp_server_command = mcp_server_command or "python -m server4.mcp_server"
        self.process = None
        self.reader = None
        self.writer = None
    
    async def connect(self) -> None:
        """Connect to Server 4 MCP server via stdio."""
        try:
            self.process = await asyncio.create_subprocess_shell(
                self.mcp_server_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.reader = self.process.stdout
            self.writer = self.process.stdin
            print("✅ Connected to Server 4 MCP server (Analysis)")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Server 4 MCP server: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
    
    async def call_tool(self, tool_name: str, **kwargs) -> dict:
        """
        Call a Server 4 tool via MCP protocol.
        
        Args:
            tool_name: Name of the tool to invoke
            **kwargs: Tool parameters
        
        Returns:
            Tool result as dictionary
        """
        # Build MCP request
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": kwargs
            }
        }
        
        try:
            # Send request
            request_json = json.dumps(request) + "\n"
            self.writer.write(request_json.encode())
            await self.writer.drain()
            
            # Receive response
            response_line = await self.reader.readline()
            response = json.loads(response_line.decode())
            
            if "error" in response:
                raise RuntimeError(f"Tool error: {response['error']}")
            
            # Parse result
            result_text = response.get("result", {}).get("content", [{}])[0].get("text", "{}")
            return json.loads(result_text)
        
        except Exception as e:
            raise RuntimeError(f"Tool call failed: {e}")
    
    # ==================== Analysis Tool Wrappers ====================
    
    async def list_job_files(
        self,
        run_uuid: str,
        extensions: Optional[str] = None
    ) -> dict:
        """
        List all files in a job's work directory.
        
        Args:
            run_uuid: Job UUID
            extensions: Optional comma-separated extensions (e.g., ".txt,.csv,.bed")
        
        Returns:
            Dictionary with file listing
        """
        params = {"run_uuid": run_uuid}
        if extensions:
            params["extensions"] = extensions
        
        return await self.call_tool("list_job_files", **params)
    
    async def read_file_content(
        self,
        run_uuid: str,
        file_path: str,
        preview_lines: Optional[int] = None
    ) -> dict:
        """
        Read content from a specific file.
        
        Args:
            run_uuid: Job UUID
            file_path: Relative path to file
            preview_lines: Optional line limit
        
        Returns:
            Dictionary with file content
        """
        params = {
            "run_uuid": run_uuid,
            "file_path": file_path
        }
        if preview_lines is not None:
            params["preview_lines"] = preview_lines
        
        return await self.call_tool("read_file_content", **params)
    
    async def parse_csv_file(
        self,
        run_uuid: str,
        file_path: str,
        max_rows: Optional[int] = 100
    ) -> dict:
        """
        Parse a CSV or TSV file.
        
        Args:
            run_uuid: Job UUID
            file_path: Relative path to CSV/TSV file
            max_rows: Maximum rows to return
        
        Returns:
            Dictionary with parsed table data
        """
        params = {
            "run_uuid": run_uuid,
            "file_path": file_path
        }
        if max_rows is not None:
            params["max_rows"] = max_rows
        
        return await self.call_tool("parse_csv_file", **params)
    
    async def parse_bed_file(
        self,
        run_uuid: str,
        file_path: str,
        max_records: Optional[int] = 100
    ) -> dict:
        """
        Parse a BED format file.
        
        Args:
            run_uuid: Job UUID
            file_path: Relative path to BED file
            max_records: Maximum records to return
        
        Returns:
            Dictionary with parsed BED records
        """
        params = {
            "run_uuid": run_uuid,
            "file_path": file_path
        }
        if max_records is not None:
            params["max_records"] = max_records
        
        return await self.call_tool("parse_bed_file", **params)
    
    async def get_analysis_summary(self, run_uuid: str) -> dict:
        """
        Get comprehensive analysis summary for a job.
        
        Args:
            run_uuid: Job UUID
        
        Returns:
            Dictionary with complete analysis summary
        """
        return await self.call_tool("get_analysis_summary", run_uuid=run_uuid)
    
    async def categorize_job_files(self, run_uuid: str) -> dict:
        """
        Categorize files by type (txt, csv, bed, other).
        
        Args:
            run_uuid: Job UUID
        
        Returns:
            Dictionary with categorized file lists
        """
        return await self.call_tool("categorize_job_files", run_uuid=run_uuid)


# Usage example
async def example_analysis_workflow():
    """Example of using Server 4 MCP client for result analysis."""
    
    # Initialize MCP client for Server 4
    analysis_client = Server4MCPClient()
    
    try:
        # Connect to Server 4
        await analysis_client.connect()
        
        # Example run UUID (replace with actual job)
        run_uuid = "72f9f625-c755-441b-bab4-d91e735b8612"
        
        print(f"🔍 Analyzing results for job: {run_uuid}\n")
        
        # Get analysis summary
        print("📊 Getting analysis summary...")
        summary = await analysis_client.get_analysis_summary(run_uuid)
        print(f"Summary: {json.dumps(summary, indent=2)}\n")
        
        # Categorize files
        print("📁 Categorizing files...")
        categories = await analysis_client.categorize_job_files(run_uuid)
        print(f"File counts: CSV={len(categories.get('csv_files', []))}, "
              f"BED={len(categories.get('bed_files', []))}, "
              f"TXT={len(categories.get('txt_files', []))}\n")
        
        # Parse a CSV file if available
        csv_files = categories.get("csv_files", [])
        if csv_files:
            csv_path = csv_files[0]["path"]
            print(f"📈 Parsing CSV file: {csv_path}")
            csv_data = await analysis_client.parse_csv_file(run_uuid, csv_path, max_rows=5)
            print(f"Columns: {csv_data.get('columns', [])}")
            print(f"Row count: {csv_data.get('row_count', 0)}\n")
        
        # Parse a BED file if available
        bed_files = categories.get("bed_files", [])
        if bed_files:
            bed_path = bed_files[0]["path"]
            print(f"🧬 Parsing BED file: {bed_path}")
            bed_data = await analysis_client.parse_bed_file(run_uuid, bed_path, max_records=5)
            print(f"Record count: {bed_data.get('record_count', 0)}\n")
        
        # Read a text file if available
        txt_files = categories.get("txt_files", [])
        if txt_files:
            txt_path = txt_files[0]["path"]
            print(f"📄 Reading text file: {txt_path}")
            txt_content = await analysis_client.read_file_content(
                run_uuid, txt_path, preview_lines=10
            )
            print(f"Preview ({txt_content.get('line_count', 0)} lines):")
            print(txt_content.get("content", "")[:500] + "...\n")
    
    except Exception as e:
        print(f"❌ Error: {e}")
    
    finally:
        await analysis_client.disconnect()


if __name__ == "__main__":
    asyncio.run(example_analysis_workflow())
