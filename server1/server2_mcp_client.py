"""
Server 1 - MCP Client for Server 2 (ENCODELIB) Integration
Allows the Agent Engine to call ENCODE data retrieval tools via MCP protocol.

This provides a seamless interface for the LLM agent to search, retrieve,
and download experiments and files from the ENCODE Portal.
"""
import asyncio
import json
from typing import Optional, List, Dict, Any


class Server2MCPClient:
    """
    MCP client for communicating with Server 2 MCP server (ENCODELIB).
    
    Maintains a subprocess connection to the ENCODE MCP server
    and handles tool invocation for experiment search, metadata retrieval,
    and file downloads.
    """
    
    def __init__(self, mcp_server_command: Optional[str] = None):
        """
        Initialize MCP client for Server 2 (ENCODELIB).
        
        Args:
            mcp_server_command: Command to start ENCODE MCP server
                               (default: uses Python module from ENCODELIB path)
        """
        # Default to ENCODELIB location - can be overridden by environment variable
        import os
        encodelib_path = os.getenv("ENCODELIB_PATH", "/Users/eli/code/ENCODELIB")
        self.mcp_server_command = mcp_server_command or f"python {encodelib_path}/encode_server.py"
        self.process = None
        self.reader = None
        self.writer = None
    
    async def connect(self) -> None:
        """Connect to Server 2 (ENCODELIB) MCP server via stdio."""
        try:
            self.process = await asyncio.create_subprocess_shell(
                self.mcp_server_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,  # Suppress fastmcp logs
                limit=1024 * 1024  # 1MB buffer for large responses
            )
            self.reader = self.process.stdout
            self.writer = self.process.stdin
            
            # MCP Protocol: Send initialization handshake
            init_request = {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "agoutic-server2-client",
                        "version": "1.0.0"
                    }
                }
            }
            
            init_json = json.dumps(init_request) + "\n"
            self.writer.write(init_json.encode())
            await self.writer.drain()
            
            # Read initialization response, skipping any non-JSON lines (fastmcp banner)
            init_result = None
            max_attempts = 10
            
            for attempt in range(max_attempts):
                try:
                    line = await asyncio.wait_for(self.reader.readline(), timeout=10.0)
                    if not line:
                        raise RuntimeError("Server closed connection without responding")
                    
                    # Try to parse as JSON
                    init_result = json.loads(line.decode())
                    break  # Success! Got valid JSON
                    
                except json.JSONDecodeError:
                    # Skip non-JSON line (banner/logs) and try next line
                    if attempt == max_attempts - 1:
                        raise RuntimeError(f"Server sent {max_attempts} non-JSON lines, giving up")
                    continue
                    
                except asyncio.TimeoutError:
                    raise RuntimeError("Server did not respond within 10 seconds")
            
            if not init_result:
                raise RuntimeError("Failed to get valid JSON response from server")
            
            if "error" in init_result:
                raise RuntimeError(f"MCP initialization failed: {init_result['error']}")
            
            print("✅ Connected to Server 2 MCP server (ENCODELIB)")
        except Exception as e:
            # Cleanup on error
            if self.writer:
                self.writer.close()
            if self.process:
                try:
                    self.process.kill()
                    await self.process.wait()
                except:
                    pass
            raise RuntimeError(f"Failed to connect to Server 2 MCP server: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        
        if self.process:
            # Check if process is still running before trying to terminate
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
    
    async def call_tool(self, tool_name: str, **kwargs) -> dict:
        """
        Call a Server 2 (ENCODELIB) tool via MCP protocol.
        
        Args:
            tool_name: Name of the tool to invoke
            **kwargs: Tool parameters
        
        Returns:
            Tool result as dictionary
        """
        # Filter out None values from kwargs (MCP doesn't accept null for optional params)
        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        
        # Build MCP request
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": filtered_kwargs
            }
        }
        
        try:
            # Send request
            request_json = json.dumps(request) + "\n"
            self.writer.write(request_json.encode())
            await self.writer.drain()
            
            # Read response - skip any non-JSON lines (same as initialization)
            response = None
            max_attempts = 10
            
            for attempt in range(max_attempts):
                try:
                    line = await asyncio.wait_for(self.reader.readline(), timeout=30.0)
                    if not line:
                        raise RuntimeError("Server closed connection")
                    
                    # Try to parse as JSON
                    response = json.loads(line.decode())
                    break  # Success!
                    
                except json.JSONDecodeError:
                    # Skip non-JSON line and try next
                    if attempt == max_attempts - 1:
                        raise RuntimeError(f"Server sent {max_attempts} non-JSON lines during tool call")
                    continue
            
            if not response:
                raise RuntimeError("Failed to get valid JSON response from tool call")
            
            if "error" in response:
                raise RuntimeError(f"Tool error: {response['error']}")
            
            # Extract result from JSON-RPC response
            rpc_result = response.get("result")
            if not rpc_result:
                return {}
            
            # fastmcp wraps tool results in content[0].text as a JSON string
            if isinstance(rpc_result, dict) and "content" in rpc_result:
                content_list = rpc_result.get("content", [])
                if content_list and len(content_list) > 0:
                    text_content = content_list[0].get("text", "")
                    if text_content:
                        # Parse the JSON string to get actual data
                        try:
                            return json.loads(text_content)
                        except json.JSONDecodeError:
                            return text_content
                # Check for isError flag from fastmcp
                if rpc_result.get("isError", False):
                    raise RuntimeError(f"Tool returned error: {rpc_result}")
                return {}
            
            # Fallback for non-fastmcp format
            return rpc_result
            
        except Exception as e:
            raise RuntimeError(f"MCP communication error: {e}")
    
    # ============================================================================
    # Search Methods
    # ============================================================================
    
    async def search_by_biosample(
        self,
        search_term: str,
        organism: Optional[str] = None,
        assay_title: Optional[str] = None,
        target: Optional[str] = None,
        exclude_revoked: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search for experiments by biosample (cell type, tissue name).
        
        Args:
            search_term: Cell type or tissue name (e.g., 'GM12878', 'K562')
            organism: Optional organism filter (e.g., 'Homo sapiens')
            assay_title: Optional assay type filter
            target: Optional target name filter
            exclude_revoked: Whether to exclude revoked experiments
        
        Returns:
            List of experiment metadata dictionaries
        """
        return await self.call_tool(
            "search_by_biosample",
            search_term=search_term,
            organism=organism,
            assay_title=assay_title,
            target=target,
            exclude_revoked=exclude_revoked,
        )
    
    async def search_by_organism(
        self,
        organism: str,
        search_term: Optional[str] = None,
        assay_title: Optional[str] = None,
        target: Optional[str] = None,
        exclude_revoked: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search for experiments by organism.
        
        Args:
            organism: Organism name (e.g., 'Homo sapiens', 'Mus musculus')
            search_term: Optional additional search term
            assay_title: Optional assay type filter
            target: Optional target name filter
            exclude_revoked: Whether to exclude revoked experiments
        
        Returns:
            List of experiment metadata dictionaries
        """
        return await self.call_tool(
            "search_by_organism",
            organism=organism,
            search_term=search_term,
            assay_title=assay_title,
            target=target,
            exclude_revoked=exclude_revoked,
        )
    
    async def search_by_target(
        self,
        target: str,
        organism: Optional[str] = None,
        assay_title: Optional[str] = None,
        exclude_revoked: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search for experiments by target (transcription factor, histone mark).
        
        Args:
            target: Target name to search for
            organism: Optional organism filter
            assay_title: Optional assay type filter
            exclude_revoked: Whether to exclude revoked experiments
        
        Returns:
            List of experiment metadata dictionaries
        """
        return await self.call_tool(
            "search_by_target",
            target=target,
            organism=organism,
            assay_title=assay_title,
            exclude_revoked=exclude_revoked,
        )
    
    # ============================================================================
    # Experiment Metadata Methods
    # ============================================================================
    
    async def get_experiment(self, accession: str) -> Dict[str, Any]:
        """
        Get detailed metadata for a specific experiment.
        
        Args:
            accession: ENCODE experiment accession (e.g., 'ENCSR000CDC')
        
        Returns:
            Experiment metadata dictionary
        """
        return await self.call_tool("get_experiment", accession=accession)
    
    async def get_all_metadata(self, accession: str) -> Dict[str, Any]:
        """
        Get all available metadata for an experiment from ENCODE API.
        
        Args:
            accession: ENCODE experiment accession
        
        Returns:
            Complete raw metadata from ENCODE API
        """
        return await self.call_tool("get_all_metadata", accession=accession)
    
    # ============================================================================
    # File Discovery Methods
    # ============================================================================
    
    async def get_file_types(self, accession: str) -> List[str]:
        """
        Get available file types for an experiment.
        
        Args:
            accession: Experiment accession
        
        Returns:
            List of file types
        """
        return await self.call_tool("get_file_types", accession=accession)
    
    async def get_files_by_type(
        self,
        accession: str,
        after_date: Optional[str] = None,
        file_status: str = "released",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get all files organized by file type.
        
        Args:
            accession: Experiment accession
            after_date: Optional date filter (YYYY-MM-DD)
            file_status: Filter by file status
        
        Returns:
            Dictionary with file type as key and list of file metadata
        """
        return await self.call_tool(
            "get_files_by_type",
            accession=accession,
            after_date=after_date,
            file_status=file_status,
        )
    
    async def get_file_accessions_by_type(
        self,
        accession: str,
        after_date: Optional[str] = None,
        file_types: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        """
        Get file accessions organized by file type.
        
        Args:
            accession: Experiment accession
            after_date: Optional date filter
            file_types: Optional list of specific file types
        
        Returns:
            Dictionary with file type as key and list of accessions
        """
        return await self.call_tool(
            "get_file_accessions_by_type",
            accession=accession,
            after_date=after_date,
            file_types=file_types,
        )
    
    async def get_available_output_categories(self, accession: str) -> List[str]:
        """Get available output categories for an experiment."""
        return await self.call_tool(
            "get_available_output_categories",
            accession=accession
        )
    
    async def get_available_output_types(self, accession: str) -> List[str]:
        """Get available output types for an experiment."""
        return await self.call_tool(
            "get_available_output_types",
            accession=accession
        )
    
    async def get_files_summary(
        self,
        accession: str,
        max_files_per_type: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get a summary view of files by type.
        
        Args:
            accession: Experiment accession
            max_files_per_type: Maximum files to show per type
        
        Returns:
            Dictionary with file type summary
        """
        return await self.call_tool(
            "get_files_summary",
            accession=accession,
            max_files_per_type=max_files_per_type,
        )
    
    # ============================================================================
    # File Metadata Methods
    # ============================================================================
    
    async def get_file_metadata(
        self,
        accession: str,
        file_accession: str,
    ) -> Dict[str, Any]:
        """
        Get comprehensive metadata for a specific file.
        
        Args:
            accession: Experiment accession
            file_accession: File accession ID
        
        Returns:
            Complete file metadata dictionary
        """
        return await self.call_tool(
            "get_file_metadata",
            accession=accession,
            file_accession=file_accession,
        )
    
    async def get_file_url(
        self,
        accession: str,
        file_accession: str,
    ) -> Dict[str, str]:
        """
        Get download URL for a specific file.
        
        Args:
            accession: Experiment accession
            file_accession: File accession ID
        
        Returns:
            Dictionary with download URL
        """
        return await self.call_tool(
            "get_file_url",
            accession=accession,
            file_accession=file_accession,
        )
    
    # ============================================================================
    # Download Methods
    # ============================================================================
    
    async def download_files(
        self,
        accession: str,
        file_types: Optional[List[str]] = None,
        file_accessions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Download files from an experiment.
        
        Args:
            accession: Experiment accession
            file_types: Optional list of file types to download
            file_accessions: Optional list of specific file accessions
        
        Returns:
            Dictionary with download results (downloaded, failed, skipped)
        """
        return await self.call_tool(
            "download_files",
            accession=accession,
            file_types=file_types,
            file_accessions=file_accessions,
        )
    
    # ============================================================================
    # Cache Management Methods
    # ============================================================================
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the metadata cache.
        
        Returns:
            Dictionary with cache statistics
        """
        return await self.call_tool("get_cache_stats")
    
    async def clear_cache(self, clear_metadata: bool = False) -> Dict[str, str]:
        """
        Clear caches.
        
        Args:
            clear_metadata: If True, also clear metadata cache
        
        Returns:
            Confirmation message
        """
        return await self.call_tool(
            "clear_cache",
            clear_metadata=clear_metadata
        )
    
    # ============================================================================
    # Utility Methods
    # ============================================================================
    
    async def list_experiments(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        List loaded experiments with pagination.
        
        Args:
            limit: Maximum number of experiments to return
            offset: Starting index
        
        Returns:
            Dictionary with experiment list and pagination info
        """
        return await self.call_tool(
            "list_experiments",
            limit=limit,
            offset=offset,
        )
    
    async def get_server_info(self) -> Dict[str, Any]:
        """
        Get server configuration information.
        
        Returns:
            Dictionary with server settings
        """
        return await self.call_tool("get_server_info")


# ============================================================================
# Convenience Functions for Synchronous Usage
# ============================================================================


def create_server2_client(mcp_server_command: Optional[str] = None) -> Server2MCPClient:
    """
    Factory function to create a Server2MCPClient instance.
    
    Args:
        mcp_server_command: Optional custom command to start the server
    
    Returns:
        Server2MCPClient instance
    """
    return Server2MCPClient(mcp_server_command)


async def get_encode_experiments(
    search_term: str,
    organism: Optional[str] = None,
    assay_title: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience function to search ENCODE experiments.
    
    Args:
        search_term: Cell type or tissue name
        organism: Optional organism filter
        assay_title: Optional assay type filter
    
    Returns:
        List of experiment metadata
    """
    client = Server2MCPClient()
    await client.connect()
    try:
        results = await client.search_by_biosample(
            search_term=search_term,
            organism=organism,
            assay_title=assay_title,
        )
        return results
    finally:
        await client.disconnect()


# ============================================================================
# Test Block
# ============================================================================


if __name__ == "__main__":
    async def test_server2_client():
        """Test the Server2MCPClient connection and basic operations."""
        client = Server2MCPClient()
        
        try:
            print("🔌 Connecting to ENCODELIB server...")
            await client.connect()
            
            print("\n📊 Getting server info...")
            info = await client.get_server_info()
            print(f"   Server: {info.get('server_name', 'Unknown')}")
            print(f"   Version: {info.get('version', 'Unknown')}")
            
            print("\n🔍 Searching for K562 experiments...")
            results = await client.search_by_biosample(
                search_term="K562",
                organism="Homo sapiens",
                assay_title="TF ChIP-seq",
            )
            print(f"   Found {len(results)} experiments")
            
            if results:
                exp = results[0]
                print(f"\n📁 First experiment: {exp.get('accession')}")
                print(f"   Biosample: {exp.get('biosample')}")
                print(f"   Assay: {exp.get('assay')}")
                
                # Get file types
                accession = exp.get('accession')
                file_types = await client.get_file_types(accession)
                print(f"   File types: {file_types}")
            
            print("\n✅ Test completed successfully!")
            
        except Exception as e:
            print(f"\n❌ Error: {e}")
        
        finally:
            await client.disconnect()
            print("\n🔌 Disconnected from server")
    
    # Run test
    asyncio.run(test_server2_client())
