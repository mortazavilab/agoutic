"""
Tests for cortex/app.py — Analyzer proxy endpoints and MCP helpers.

Covers:
  - GET  /analysis/jobs/{run_uuid}/summary
  - GET  /analysis/jobs/{run_uuid}/files
  - GET  /analysis/jobs/{run_uuid}/files/categorize
  - GET  /analysis/files/content
  - GET  /analysis/files/parse/csv
  - GET  /analysis/files/parse/bed
  - _call_analyzer_tool  (MCP helper)
  - _call_launchpad_tool (MCP helper)

These tests mock the MCP client and run_uuid_access so no external
services are needed.
"""

import datetime
import json
import uuid

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex.models import (
    Base, User, Session as SessionModel, Project, ProjectAccess,
)
from cortex.app import app, _call_analyzer_tool, _call_launchpad_tool

try:
    from launchpad.models import Base as LaunchpadBase
    _LP = True
except ImportError:
    _LP = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    if _LP:
        LaunchpadBase.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture()
def seed_data(session_factory):
    sess = session_factory()
    user = User(id="u-az", email="az@example.com", role="user",
                username="azuser", is_active=True)
    sess.add(user)
    session_obj = SessionModel(id="az-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)
    proj = Project(id="proj-az", name="AZ Proj", owner_id="u-az",
                   slug="az-proj")
    sess.add(proj)
    sess.flush()
    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-az",
                           project_id="proj-az", project_name="AZ Proj",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    sess.add(access)
    sess.commit()
    sess.close()


def _noop_run_uuid_access(run_uuid, user):
    """Bypass require_run_uuid_access for testing."""
    pass


@pytest.fixture()
def mock_mcp():
    """Provide a mock MCP client that returns configurable responses."""
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.call_tool = AsyncMock(return_value={"status": "ok", "data": "test"})
    return client


@pytest.fixture()
def client(session_factory, seed_data, mock_mcp, tmp_path):
    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.dependencies.require_run_uuid_access", _noop_run_uuid_access), \
         patch("cortex.app.require_run_uuid_access", _noop_run_uuid_access), \
         patch("cortex.app.MCPHttpClient", return_value=mock_mcp), \
         patch("cortex.app.get_service_url", return_value="http://localhost:9999"):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "az-session")
        yield c


# ---------------------------------------------------------------------------
# GET /analysis/jobs/{run_uuid}/summary
# ---------------------------------------------------------------------------
class TestGetJobAnalysisSummary:
    def test_returns_mcp_result(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": True,
            "summary": "Job completed",
            "files_count": 42,
        }
        resp = client.get("/analysis/jobs/abc-123/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "Job completed"
        assert data["files_count"] == 42

    def test_mcp_error_422(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": False,
            "error": "Work directory not found",
        }
        resp = client.get("/analysis/jobs/bad-uuid/summary")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /analysis/jobs/{run_uuid}/files
# ---------------------------------------------------------------------------
class TestListJobFiles:
    def test_list_files(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": True,
            "files": ["a.csv", "b.bam"],
        }
        resp = client.get("/analysis/jobs/abc-123/files")
        assert resp.status_code == 200
        assert len(resp.json()["files"]) == 2

    def test_with_extensions_filter(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": True,
            "files": ["a.csv"],
        }
        resp = client.get("/analysis/jobs/abc-123/files?extensions=.csv,.tsv")
        assert resp.status_code == 200
        # Verify the MCP client was called with extensions kwarg
        mock_mcp.call_tool.assert_called_with(
            "list_job_files", run_uuid="abc-123", extensions=".csv,.tsv"
        )


# ---------------------------------------------------------------------------
# GET /analysis/jobs/{run_uuid}/files/categorize
# ---------------------------------------------------------------------------
class TestCategorizeJobFiles:
    def test_categorize(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": True,
            "categories": {"csv": ["a.csv"], "bam": ["b.bam"]},
        }
        resp = client.get("/analysis/jobs/abc-123/files/categorize")
        assert resp.status_code == 200
        assert "csv" in resp.json()["categories"]


# ---------------------------------------------------------------------------
# GET /analysis/files/content
# ---------------------------------------------------------------------------
class TestReadFileContent:
    def test_read_file(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": True,
            "content": "chr1\t100\t200\n",
            "lines": 1,
        }
        resp = client.get("/analysis/files/content",
                         params={"run_uuid": "abc-123", "file_path": "/path/to/file.bed"})
        assert resp.status_code == 200
        assert resp.json()["content"] == "chr1\t100\t200\n"

    def test_with_preview_lines(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {"success": True, "content": "row1\n"}
        resp = client.get("/analysis/files/content",
                         params={"run_uuid": "abc-123", "file_path": "/x",
                                "preview_lines": 10})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /analysis/files/parse/csv
# ---------------------------------------------------------------------------
class TestParseCsv:
    def test_parse_csv(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": True,
            "columns": ["score", "name"],
            "rows": [{"score": 0.9, "name": "gene1"}],
        }
        resp = client.get("/analysis/files/parse/csv",
                         params={"run_uuid": "abc-123", "file_path": "/data.csv"})
        assert resp.status_code == 200
        assert resp.json()["columns"] == ["score", "name"]

    def test_parse_csv_max_rows(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {"success": True, "rows": []}
        resp = client.get("/analysis/files/parse/csv",
                         params={"run_uuid": "abc-123", "file_path": "/d.csv",
                                "max_rows": 5})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /analysis/files/parse/bed
# ---------------------------------------------------------------------------
class TestParseBed:
    def test_parse_bed(self, client, mock_mcp):
        mock_mcp.call_tool.return_value = {
            "success": True,
            "records": [{"chrom": "chr1", "start": 100, "end": 200}],
        }
        resp = client.get("/analysis/files/parse/bed",
                         params={"run_uuid": "abc-123", "file_path": "/f.bed"})
        assert resp.status_code == 200
        assert resp.json()["records"][0]["chrom"] == "chr1"


# ---------------------------------------------------------------------------
# _call_analyzer_tool / _call_launchpad_tool — unit tests
# ---------------------------------------------------------------------------
class TestMCPHelpers:
    @pytest.mark.asyncio
    async def test_call_analyzer_tool_success(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"success": True, "data": "ok"}
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.get_service_url", return_value="http://localhost:9999"):
            result = await _call_analyzer_tool("get_analysis_summary", run_uuid="u1")
        assert result == {"success": True, "data": "ok"}

    @pytest.mark.asyncio
    async def test_call_analyzer_tool_error_surfaces(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {
            "success": False, "error": "Not found", "detail": "No work dir"
        }
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.get_service_url", return_value="http://localhost:9999"):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await _call_analyzer_tool("get_analysis_summary", run_uuid="bad")
            assert exc_info.value.status_code == 422
            assert "Not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_call_analyzer_tool_connection_error(self):
        mock_client = AsyncMock()
        mock_client.connect.side_effect = ConnectionError("refused")
        mock_client.disconnect = AsyncMock()

        with patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.get_service_url", return_value="http://localhost:9999"):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await _call_analyzer_tool("get_analysis_summary", run_uuid="u1")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_call_launchpad_tool_success(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"status": "COMPLETED"}
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.get_service_url", return_value="http://localhost:9999"):
            result = await _call_launchpad_tool("check_nextflow_status", run_uuid="u1")
        assert result == {"status": "COMPLETED"}

    @pytest.mark.asyncio
    async def test_call_launchpad_tool_error(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"success": False, "error": "Timeout"}
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.get_service_url", return_value="http://localhost:9999"):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await _call_launchpad_tool("check_nextflow_status", run_uuid="u1")
            assert exc_info.value.status_code == 422
