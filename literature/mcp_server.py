"""FastMCP interface for literature retrieval."""
from fastmcp import FastMCP
from starlette.responses import JSONResponse

from cortex.ncbi_credentials import load_ncbi_credentials
from literature.service import LiteratureService
from literature.tool_schemas import TOOL_SCHEMAS

server = FastMCP("AGOUTIC Literature")
_service = LiteratureService(credentials_loader=load_ncbi_credentials)


@server.tool()
async def search_literature(
    query: str,
    result_count: int = 10,
    user_id: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    organism: str | None = None,
    article_type: str | None = None,
    study_design: str | None = None,
    open_access_only: bool = False,
    sort_by: str = "relevance",
) -> dict:
    """Search PubMed and PMC for evidence-attributed, ranked papers."""
    return await _service.search_literature(
        query,
        result_count=result_count,
        user_id=user_id,
        year_from=year_from,
        year_to=year_to,
        organism=organism,
        article_type=article_type,
        study_design=study_design,
        open_access_only=open_access_only,
        sort_by=sort_by,
    )


async def _tools_schema_endpoint(_request):
    return JSONResponse(TOOL_SCHEMAS)
