"""FastMCP interface for literature retrieval."""
from fastmcp import FastMCP
from starlette.responses import JSONResponse

from cortex.ncbi_credentials import load_ncbi_credentials
from literature.service import LiteratureService
from literature.tool_schemas import TOOL_SCHEMAS

server = FastMCP("AGOUTIC Literature")
_service = LiteratureService(credentials_loader=load_ncbi_credentials)


@server.tool()
async def search_literature(query: str, result_count: int = 10, user_id: str | None = None) -> dict:
    """Search PubMed and PMC for evidence-attributed, ranked papers."""
    return await _service.search_literature(query, result_count=result_count, user_id=user_id)


async def _tools_schema_endpoint(_request):
    return JSONResponse(TOOL_SCHEMAS)
