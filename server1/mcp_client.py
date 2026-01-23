"""
Server 1 - MCP Client for Server 3 Integration
Allows the Agent Engine to call Server 3 tools via MCP protocol.

This provides a seamless interface for the LLM agent to control
Dogme/Nextflow pipeline execution.
"""
import asyncio
import json
import subprocess
import sys
from typing import Optional, Any
from pathlib import Path

class Server3MCPClient:
    """
    MCP client for communicating with Server 3 MCP server.
    
    Maintains a subprocess connection to the Server 3 MCP server
    and handles tool invocation and result parsing.
    """
    
    def __init__(self, mcp_server_command: Optional[str] = None):
        """
        Initialize MCP client.
        
        Args:
            mcp_server_command: Command to start Server 3 MCP server
                               (default: uses Python module)
        """
        self.mcp_server_command = mcp_server_command or "python -m server3.mcp_server"
        self.process = None
        self.reader = None
        self.writer = None
    
    async def connect(self) -> None:
        """Connect to Server 3 MCP server via stdio."""
        try:
            self.process = await asyncio.create_subprocess_shell(
                self.mcp_server_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.reader = self.process.stdout
            self.writer = self.process.stdin
            print("✅ Connected to Server 3 MCP server")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to MCP server: {e}")
    
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
        Call a Server 3 tool via MCP protocol.
        
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
    
    async def submit_dogme_job(
        self,
        project_id: str,
        sample_name: str,
        mode: str,
        input_directory: str,
        reference_genome: str = "GRCh38",
        modifications: Optional[str] = None,
    ) -> str:
        """Submit a Dogme job and return the run UUID."""
        result = await self.call_tool(
            "submit_dogme_job",
            project_id=project_id,
            sample_name=sample_name,
            mode=mode,
            input_directory=input_directory,
            reference_genome=reference_genome,
            modifications=modifications,
        )
        return result.get("run_uuid", "")
    
    async def check_nextflow_status(self, run_uuid: str) -> dict:
        """Check the status of a Nextflow job."""
        return await self.call_tool("check_nextflow_status", run_uuid=run_uuid)
    
    async def get_dogme_report(self, run_uuid: str) -> dict:
        """Get analysis results from a completed job."""
        return await self.call_tool("get_dogme_report", run_uuid=run_uuid)
    
    async def find_pod5_directory(self, query: str) -> dict:
        """Find pod5 files matching a query."""
        return await self.call_tool("find_pod5_directory", query=query)
    
    async def generate_dogme_config(
        self,
        sample_name: str,
        read_type: str,
        genome: str = "GRCh38",
        modifications: Optional[str] = None,
    ) -> dict:
        """Generate a Dogme configuration."""
        return await self.call_tool(
            "generate_dogme_config",
            sample_name=sample_name,
            read_type=read_type,
            genome=genome,
            modifications=modifications,
        )


class Server1AgentWithMCP:
    """
    Enhanced Server 1 Agent that can use Server 3 via MCP.
    
    This integration allows the LLM agent to:
    1. Detect when [[APPROVAL_NEEDED]] tag is present
    2. Use MCP tools to submit/track Dogme jobs
    3. Poll job status and retrieve results
    """
    
    def __init__(self, mcp_client: Optional[Server3MCPClient] = None):
        self.mcp_client = mcp_client
        self.connected = False
    
    async def initialize(self) -> None:
        """Initialize MCP connection."""
        if self.mcp_client:
            await self.mcp_client.connect()
            self.connected = True
    
    async def handle_approval_needed(
        self,
        project_id: str,
        message: str,
        skill: str,
        approved: bool = True,
    ) -> dict:
        """
        Handle approval gate by submitting job if approved.
        
        Args:
            project_id: Project identifier
            message: User's original request
            skill: Skill being executed (e.g., "run_dogme_dna")
            approved: Whether user approved the action
        
        Returns:
            Job submission result or error
        """
        if not approved:
            return {"status": "rejected", "message": "User did not approve"}
        
        if not self.connected:
            return {"status": "error", "message": "MCP client not connected"}
        
        try:
            # Parse the message to extract job parameters
            # (In real implementation, LLM would extract these)
            job_params = self._extract_job_params(message, skill)
            
            # Submit job via MCP
            run_uuid = await self.mcp_client.submit_dogme_job(
                project_id=project_id,
                sample_name=job_params["sample_name"],
                mode=job_params["mode"],
                input_directory=job_params["input_directory"],
                reference_genome=job_params.get("reference_genome", "GRCh38"),
                modifications=job_params.get("modifications"),
            )
            
            return {
                "status": "submitted",
                "run_uuid": run_uuid,
                "message": f"Job submitted successfully: {run_uuid}"
            }
        
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def poll_job_status(
        self,
        run_uuid: str,
        max_polls: int = 100,
        poll_interval: int = 5,
    ) -> dict:
        """
        Poll a job until completion.
        
        Args:
            run_uuid: Job UUID
            max_polls: Maximum number of polls
            poll_interval: Seconds between polls
        
        Returns:
            Final job status and results
        """
        for poll_num in range(max_polls):
            try:
                status = await self.mcp_client.check_nextflow_status(run_uuid)
                
                print(f"Poll #{poll_num+1}: {status['status']} ({status['progress_percent']}%)")
                
                if status["status"] in ("COMPLETED", "FAILED"):
                    # Get full results
                    results = await self.mcp_client.get_dogme_report(run_uuid)
                    return {
                        "status": "done",
                        "job_status": status["status"],
                        "results": results
                    }
                
                await asyncio.sleep(poll_interval)
            
            except Exception as e:
                print(f"Poll error: {e}")
                await asyncio.sleep(poll_interval)
        
        return {"status": "timeout", "message": "Job polling timed out"}
    
    def _extract_job_params(self, message: str, skill: str) -> dict:
        """
        Extract job parameters from user message and skill.
        
        In a real implementation, the LLM would parse this.
        Here we provide sensible defaults.
        """
        mode_map = {
            "run_dogme_dna": "DNA",
            "run_dogme_rna": "RNA",
            "run_dogme_cdna": "CDNA",
        }
        
        return {
            "sample_name": "sample_from_message",  # LLM would extract this
            "mode": mode_map.get(skill, "DNA"),
            "input_directory": "/data/samples",  # LLM would extract this
            "reference_genome": "GRCh38",
            "modifications": None,  # LLM would extract this
        }
    
    async def shutdown(self) -> None:
        """Shutdown MCP connection."""
        if self.mcp_client and self.connected:
            await self.mcp_client.disconnect()
            self.connected = False


# Usage example
async def example_workflow():
    """Example of using Server 1 with Server 3 MCP integration."""
    
    # Initialize MCP client
    mcp_client = Server3MCPClient()
    agent = Server1AgentWithMCP(mcp_client)
    
    try:
        # Connect to Server 3
        await agent.initialize()
        
        # Simulate user approval of job submission
        print("📤 Submitting job via MCP...")
        submission_result = await agent.handle_approval_needed(
            project_id="test_project",
            message="Analyze the liver DNA sample",
            skill="run_dogme_dna",
            approved=True,
        )
        
        print(f"Submission: {submission_result}")
        
        if submission_result["status"] == "submitted":
            run_uuid = submission_result["run_uuid"]
            
            # Poll job status
            print(f"\n📊 Polling job {run_uuid}...")
            final_result = await agent.poll_job_status(run_uuid, max_polls=10)
            
            print(f"Final result: {final_result}")
    
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(example_workflow())
