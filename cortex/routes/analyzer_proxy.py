"""
Analyzer proxy routes — extracted from cortex/app.py (Tier 3).

These proxy endpoints allow the UI to access Analyzer analysis tools
via MCP over HTTP. The Analyzer MCP server must be running.
Also includes the Launchpad proxy endpoints.
"""

import sys
import re
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

import cortex.config as _cfg
import cortex.dependencies as _deps
import common as _common
from common.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _safe_relative_file_path(file_path: str) -> str:
    """Validate analyzer file_path input as a safe relative path."""
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path is required")
    if file_path.startswith("/") or file_path.startswith("\\"):
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed")
    if re.match(r'^[a-zA-Z]:', file_path):
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed")
    if ".." in file_path:
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")
    if "\x00" in file_path:
        raise HTTPException(status_code=400, detail="Invalid characters in path")
    if any(tok in file_path for tok in ["*", "?", "|", "<", ">"]):
        raise HTTPException(status_code=400, detail="Invalid path tokens")
    return file_path


def _require_run_uuid_access(run_uuid: str, user) -> None:
    """Honor legacy `cortex.app.require_run_uuid_access` test patches."""
    app_mod = sys.modules.get("cortex.app")
    override = getattr(app_mod, "require_run_uuid_access", None) if app_mod else None
    if callable(override):
        return override(run_uuid, user)
    return _deps.require_run_uuid_access(run_uuid, user)


# --- MCP tool call helpers ---

async def _call_analyzer_tool(tool_name: str, **kwargs):
    """Helper to call a Analyzer MCP tool via the generic MCP HTTP client."""
    try:
        analyzer_url = _cfg.get_service_url("analyzer")
        client = _common.MCPHttpClient(name="analyzer", base_url=analyzer_url)
        await client.connect()
        try:
            result = await client.call_tool(tool_name, **kwargs)
            # Surface MCP tool errors as proper HTTP errors
            if isinstance(result, dict) and result.get("success") is False:
                detail = result.get("error", "Unknown error")
                extra = result.get("detail", "")
                raise HTTPException(
                    status_code=422,
                    detail=f"{detail}: {extra}" if extra else detail,
                )
            return result
        finally:
            await client.disconnect()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analyzer error: {str(e)}")


async def _call_launchpad_tool(tool_name: str, **kwargs):
    """Helper to call a Launchpad MCP tool via the generic MCP HTTP client."""
    try:
        launchpad_url = _cfg.get_service_url("launchpad")
        client = _common.MCPHttpClient(name="launchpad", base_url=launchpad_url)
        await client.connect()
        try:
            result = await client.call_tool(tool_name, **kwargs)
            if isinstance(result, dict) and result.get("success") is False:
                detail = result.get("error", "Unknown error")
                extra = result.get("detail", "")
                raise HTTPException(
                    status_code=422,
                    detail=f"{detail}: {extra}" if extra else detail,
                )
            return result
        finally:
            await client.disconnect()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Launchpad error: {str(e)}")


# --- Proxy Endpoints ---

@router.get("/analysis/jobs/{run_uuid}/summary")
async def get_job_analysis_summary(run_uuid: str, request: Request):
    """
    Get comprehensive analysis summary for a completed job.
    Proxies to Analyzer analysis engine via MCP.
    """
    user = request.state.user
    _require_run_uuid_access(run_uuid, user)
    return await _call_analyzer_tool("get_analysis_summary", run_uuid=run_uuid)


@router.get("/analysis/jobs/{run_uuid}/files")
async def list_job_files(run_uuid: str, extensions: Optional[str] = None, request: Request = None):
    """
    List all files for a completed job.
    Query params:
      - extensions: Comma-separated list (e.g., ".csv,.txt")
    """
    user = request.state.user
    _require_run_uuid_access(run_uuid, user)
    kwargs = {"run_uuid": run_uuid}
    if extensions:
        kwargs["extensions"] = extensions
    return await _call_analyzer_tool("list_job_files", **kwargs)


@router.get("/analysis/jobs/{run_uuid}/files/categorize")
async def categorize_job_files(run_uuid: str, request: Request):
    """
    Categorize job files by type (csv, txt, bed, other).
    """
    user = request.state.user
    _require_run_uuid_access(run_uuid, user)
    return await _call_analyzer_tool("categorize_job_files", run_uuid=run_uuid)


@router.get("/analysis/files/content")
async def read_file_content(
    run_uuid: str,
    file_path: str,
    preview_lines: Optional[int] = 50,
    request: Request = None
):
    """
    Read content of a specific file.
    """
    user = request.state.user
    _require_run_uuid_access(run_uuid, user)
    safe_file_path = _safe_relative_file_path(file_path)
    return await _call_analyzer_tool(
        "read_file_content",
        run_uuid=run_uuid,
        file_path=safe_file_path,
        preview_lines=preview_lines,
    )


@router.get("/analysis/files/parse/csv")
async def parse_csv_file(
    run_uuid: str,
    file_path: str,
    max_rows: Optional[int] = 100,
    request: Request = None
):
    """
    Parse CSV/TSV file into structured data.
    Returns column stats, dtypes, and row data.
    """
    user = request.state.user
    _require_run_uuid_access(run_uuid, user)
    safe_file_path = _safe_relative_file_path(file_path)
    return await _call_analyzer_tool(
        "parse_csv_file",
        run_uuid=run_uuid,
        file_path=safe_file_path,
        max_rows=max_rows,
    )


@router.get("/analysis/files/parse/bed")
async def parse_bed_file(
    run_uuid: str,
    file_path: str,
    max_records: Optional[int] = 100,
    request: Request = None
):
    """
    Parse BED genomic file.
    """
    user = request.state.user
    _require_run_uuid_access(run_uuid, user)
    safe_file_path = _safe_relative_file_path(file_path)
    return await _call_analyzer_tool(
        "parse_bed_file",
        run_uuid=run_uuid,
        file_path=safe_file_path,
        max_records=max_records,
    )


@router.get("/analysis/files/download")
async def proxy_download_file(
    run_uuid: str,
    file_path: str,
    request: Request = None,
):
    """
    Proxy file download from Analyzer REST API.
    Streams the file through Cortex so the UI never contacts Analyzer directly.
    """
    user = request.state.user
    _require_run_uuid_access(run_uuid, user)
    safe_file_path = _safe_relative_file_path(file_path)
    try:
        analyzer_rest = _cfg.SERVICE_REGISTRY["analyzer"]["rest_url"]
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(
                f"{analyzer_rest}/analysis/files/download",
                params={"run_uuid": run_uuid, "file_path": safe_file_path},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            # Forward the file as a streaming response
            content_disposition = resp.headers.get("content-disposition", "")
            media_type = resp.headers.get("content-type", "application/octet-stream")
            headers = {}
            if content_disposition:
                headers["content-disposition"] = content_disposition
            return StreamingResponse(
                iter([resp.content]),
                media_type=media_type,
                headers=headers,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download proxy error: {str(e)}")
