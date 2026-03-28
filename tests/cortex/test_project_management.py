"""
Tests for project management, token/disk usage, delete, and download endpoints.
Targets uncovered lines: 6370-6530 (project stats), 6530-6670 (downloads),
  6870-6920 (upload), 6920-7060 (token usage), 7060-7120 (disk usage),
  7120-7225 (delete project), 7338-7412 (analyzer proxy helpers).
"""
import contextlib
import datetime
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex.app import (
    _active_downloads,
    app,
    get_block_payload,
    _create_block_internal,
)
from common.database import Base
from cortex.models import (
    Conversation,
    ConversationMessage,
    JobResult,
    Project,
    ProjectAccess,
    ProjectBlock,
    Session as SessionModel,
    User,
)

# ---------------------------------------------------------------------------
# DB + auth helpers (same pattern as other test files)
# ---------------------------------------------------------------------------

MODULES_TO_PATCH = [
    "cortex.app", "cortex.db", "cortex.dependencies",
    "cortex.middleware", "cortex.admin", "cortex.auth",
]


@contextlib.contextmanager
def _patch_session(session_factory):
    patches = []
    for mod in MODULES_TO_PATCH:
        p = patch(f"{mod}.SessionLocal", session_factory)
        patches.append(p)
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()


def _seed(session):
    """Create user, session, and project for tests. Return dict."""
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    pid = str(uuid.uuid4())

    user = User(id=uid, email="pm@test.com", username="pmuser", is_active=True)
    session.add(user)
    session.flush()
    sess = SessionModel(id=sid, user_id=uid, expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1))
    session.add(sess)
    proj = Project(id=pid, name="PM Project", slug="pm-project", owner_id=uid, created_at=datetime.datetime.utcnow())
    session.add(proj)
    # ProjectAccess record
    access = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=uid,
        project_id=pid,
        project_name="PM Project",
        role="owner",
    )
    session.add(access)
    session.flush()
    session.commit()
    return {"user_id": uid, "session_id": sid, "project_id": pid}


@pytest.fixture()
def setup():
    """Yield (engine, SessionLocal factory, seed data, TestClient)."""
    from fastapi.testclient import TestClient

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # Project stats endpoint reads dogme_jobs via raw SQL; create the table in test DB.
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS dogme_jobs (
                run_uuid TEXT,
                sample_name TEXT,
                mode TEXT,
                status TEXT,
                submitted_at DATETIME,
                started_at DATETIME,
                completed_at DATETIME,
                nextflow_work_dir TEXT,
                remote_work_dir TEXT,
                execution_mode TEXT,
                user_id TEXT,
                project_id TEXT
            )
            """
        ))
    SL = sessionmaker(bind=engine)
    session = SL()
    data = _seed(session)
    session.close()

    with _patch_session(SL):
        # Also patch _resolve_project_dir to use tmp
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("session", data["session_id"])
        yield engine, SL, data, client


# ===========================================================================
# Token usage endpoint
# ===========================================================================

class TestTokenUsage:
    def test_empty_token_usage(self, setup):
        engine, SL, data, client = setup
        resp = client.get("/user/token-usage")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        body = resp.json()
        assert body["lifetime"]["total_tokens"] == 0
        assert body["by_conversation"] == []
        assert body["daily"] == []
        assert body["tracking_since"] is None

    def test_with_tracked_messages(self, setup):
        engine, SL, data, client = setup
        s = SL()
        cid = str(uuid.uuid4())
        conv = Conversation(
            id=cid,
            project_id=data["project_id"],
            user_id=data["user_id"],
            title="Token conv",
            created_at=datetime.datetime.utcnow(),
        )
        s.add(conv)
        msg = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=cid,
            role="assistant",
            content="Hello",
            seq=1,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            created_at=datetime.datetime.utcnow(),
        )
        s.add(msg)
        s.commit()
        s.close()

        resp = client.get("/user/token-usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["lifetime"]["total_tokens"] == 30
        assert len(body["by_conversation"]) == 1
        assert body["by_conversation"][0]["conversation_id"] == cid
        assert len(body["daily"]) >= 1
        assert body["tracking_since"] is not None

    def test_unauthenticated(self, setup):
        engine, SL, data, client = setup
        client.cookies.clear()
        resp = client.get("/user/token-usage")
        assert resp.status_code in (401, 403)


# ===========================================================================
# Disk usage endpoint
# ===========================================================================

class TestDiskUsage:
    def test_empty_disk_usage(self, setup, tmp_path):
        engine, SL, data, client = setup
        with patch("cortex.app._resolve_project_dir", return_value=tmp_path / "nonexist"):
            resp = client.get("/user/disk-usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_bytes"] == 0
        assert body["projects"] == [{"project_id": data["project_id"], "size_bytes": 0, "size_mb": 0.0, "file_count": 0}]

    def test_with_files(self, setup, tmp_path):
        engine, SL, data, client = setup
        # Create some files in temp dir
        pdir = tmp_path / "project"
        pdir.mkdir()
        (pdir / "file1.txt").write_text("hello")
        (pdir / "file2.txt").write_text("world!")

        with patch("cortex.app._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir), \
             patch("cortex.config.AGOUTIC_DATA", tmp_path):
            resp = client.get("/user/disk-usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_bytes"] > 0
        assert body["projects"][0]["file_count"] == 2


# ===========================================================================
# Delete project (archive)
# ===========================================================================

class TestDeleteProject:
    def test_archive_project(self, setup):
        engine, SL, data, client = setup
        resp = client.delete(f"/projects/{data['project_id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "archived"

        # Verify in DB
        s = SL()
        proj = s.query(Project).filter(Project.id == data["project_id"]).first()
        assert proj.is_archived is True
        s.close()

    def test_archive_nonexistent(self, setup):
        engine, SL, data, client = setup
        resp = client.delete(f"/projects/{uuid.uuid4()}")
        # Should fail - no access record
        assert resp.status_code in (403, 404)


# ===========================================================================
# Permanent delete project
# ===========================================================================

class TestPermanentDeleteProject:
    def test_permanent_delete(self, setup, tmp_path):
        engine, SL, data, client = setup
        pid = data["project_id"]

        # Add a conversation + message + block to ensure cascade
        s = SL()
        cid = str(uuid.uuid4())
        conv = Conversation(id=cid, project_id=pid, user_id=data["user_id"],
                            title="To delete", created_at=datetime.datetime.utcnow())
        s.add(conv)
        msg = ConversationMessage(id=str(uuid.uuid4()), conversation_id=cid,
                                  role="user", content="hi", seq=1,
                                  created_at=datetime.datetime.utcnow())
        s.add(msg)
        blk = ProjectBlock(id=str(uuid.uuid4()), project_id=pid, type="USER_MESSAGE",
                           payload_json='{}', seq=1, owner_id=data["user_id"],
                           created_at=datetime.datetime.utcnow())
        s.add(blk)
        s.commit()
        s.close()

        pdir = tmp_path / "proj_dir"
        pdir.mkdir()
        (pdir / "file.txt").write_text("data")

        with patch("cortex.app._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir):
            resp = client.delete(f"/projects/{pid}/permanent")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"

        # Verify DB cleanup
        s = SL()
        assert s.query(Project).filter(Project.id == pid).first() is None
        assert s.query(Conversation).filter(Conversation.project_id == pid).count() == 0
        assert s.query(ProjectBlock).filter(ProjectBlock.project_id == pid).count() == 0
        s.close()

    def test_permanent_delete_not_found(self, setup):
        engine, SL, data, client = setup
        resp = client.delete(f"/projects/{uuid.uuid4()}/permanent")
        assert resp.status_code in (403, 404)


# ===========================================================================
# Project files listing
# ===========================================================================

class TestProjectFiles:
    def test_list_empty(self, setup, tmp_path):
        engine, SL, data, client = setup
        with patch("cortex.app._resolve_project_dir", return_value=tmp_path / "nope"):
            resp = client.get(f"/projects/{data['project_id']}/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_count"] == 0
        assert body["files"] == []

    def test_list_with_files(self, setup, tmp_path):
        engine, SL, data, client = setup
        pdir = tmp_path / "files_test"
        pdir.mkdir()
        (pdir / "report.csv").write_text("a,b\n1,2")
        (pdir / "sub").mkdir()
        (pdir / "sub" / "nested.txt").write_text("nested")

        with patch("cortex.app._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir):
            resp = client.get(f"/projects/{data['project_id']}/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_count"] == 2
        names = {f["name"] for f in body["files"]}
        assert "report.csv" in names
        assert "nested.txt" in names
        assert body["total_size_bytes"] > 0


# ===========================================================================
# Project stats (dashboard)
# ===========================================================================

class TestProjectStats:
    def test_stats_basic(self, setup, tmp_path):
        engine, SL, data, client = setup
        pid = data["project_id"]

        # Add some blocks & conversations
        s = SL()
        cid = str(uuid.uuid4())
        conv = Conversation(id=cid, project_id=pid, user_id=data["user_id"],
                            title="Stats conv", created_at=datetime.datetime.utcnow())
        s.add(conv)
        blk = ProjectBlock(id=str(uuid.uuid4()), project_id=pid, type="USER_MESSAGE",
                           payload_json='{}', seq=1, owner_id=data["user_id"],
                           created_at=datetime.datetime.utcnow())
        s.add(blk)
        s.commit()
        s.close()

        pdir = tmp_path / "stats_proj"
        pdir.mkdir()

        with patch("cortex.app._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir), \
             patch("cortex.config.AGOUTIC_DATA", tmp_path):
            resp = client.get(f"/projects/{pid}/stats")
        assert resp.status_code == 200
        body = resp.json()
        # The stats endpoint returns various fields; just verify we got valid JSON
        assert isinstance(body, dict)
        # At least block_count or conversation_count should be present
        assert "block_count" in body or "conversation_count" in body or "jobs" in body


# ===========================================================================
# Download initiation / status / cancel
# ===========================================================================

class TestDownloadEndpoints:
    def test_initiate_download(self, setup, tmp_path):
        engine, SL, data, client = setup
        pdir = tmp_path / "dl_proj"
        pdir.mkdir()

        with patch("cortex.app._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir), \
             patch("cortex.app.asyncio"), \
             patch("cortex.routes.files.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            resp = client.post(
                f"/projects/{data['project_id']}/downloads",
                json={
                    "source": "url",
                    "files": [{"url": "https://example.com/file.txt", "filename": "file.txt"}],
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "download_id" in body
        assert "block_id" in body
        assert body["file_count"] == 1
        # Cleanup
        _active_downloads.pop(body["download_id"], None)

    def test_initiate_empty_files(self, setup):
        engine, SL, data, client = setup
        resp = client.post(
            f"/projects/{data['project_id']}/downloads",
            json={"source": "url", "files": []},
        )
        assert resp.status_code == 422

    def test_download_status(self, setup, tmp_path):
        engine, SL, data, client = setup
        pid = data["project_id"]

        # Create a block manually
        s = SL()
        pdir = tmp_path / "dl_status"
        pdir.mkdir()
        with patch("cortex.app._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir):
            blk = _create_block_internal(s, pid, "DOWNLOAD_TASK", {
                "download_id": "dl-1", "status": "RUNNING", "downloaded": 0,
            }, status="RUNNING", owner_id=data["user_id"])
            s.commit()
            blk_id = blk.id
        s.close()

        # Register in active downloads
        _active_downloads["dl-1"] = {"block_id": blk_id, "project_id": pid, "cancelled": False}
        try:
            resp = client.get(f"/projects/{pid}/downloads/dl-1")
            assert resp.status_code == 200
            body = resp.json()
            assert body["download_id"] == "dl-1"
        finally:
            _active_downloads.pop("dl-1", None)

    def test_download_status_not_found(self, setup):
        engine, SL, data, client = setup
        resp = client.get(f"/projects/{data['project_id']}/downloads/nonexistent")
        assert resp.status_code == 404

    def test_cancel_download(self, setup):
        engine, SL, data, client = setup
        pid = data["project_id"]
        _active_downloads["dl-cancel"] = {"block_id": "x", "project_id": pid, "cancelled": False}
        try:
            resp = client.delete(f"/projects/{pid}/downloads/dl-cancel")
            assert resp.status_code == 200
            assert resp.json()["status"] == "cancelling"
            assert _active_downloads["dl-cancel"]["cancelled"] is True
        finally:
            _active_downloads.pop("dl-cancel", None)

    def test_cancel_not_found(self, setup):
        engine, SL, data, client = setup
        resp = client.delete(f"/projects/{data['project_id']}/downloads/nope")
        assert resp.status_code == 404


# ===========================================================================
# Upload files
# ===========================================================================

class TestUploadFiles:
    def test_upload_success(self, setup, tmp_path):
        engine, SL, data, client = setup
        pdir = tmp_path / "upload_proj"
        pdir.mkdir()
        central = tmp_path / "users" / "testuser" / "data"
        central.mkdir(parents=True, exist_ok=True)
        (pdir / "data").mkdir(parents=True, exist_ok=True)

        with patch("cortex.routes.files._resolve_user_and_project",
                   return_value=("testuser", "upload-proj", pdir)), \
             patch("cortex.routes.files.get_user_data_dir", return_value=central), \
             patch("cortex.routes.files.create_project_file_symlink",
                   return_value=pdir / "data" / "test.txt"), \
             patch("cortex.app._post_download_suggestions", new_callable=AsyncMock), \
             patch("cortex.routes.files._post_download_suggestions", new_callable=AsyncMock):
            resp = client.post(
                f"/projects/{data['project_id']}/upload",
                files={"file1": ("test.txt", b"hello world", "text/plain")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["uploaded"][0]["filename"] == "test.txt"
        # File now lives in central data dir
        assert (central / "test.txt").exists()

    def test_upload_no_files(self, setup, tmp_path):
        engine, SL, data, client = setup
        pdir = tmp_path / "upload_empty"
        pdir.mkdir()

        with patch("cortex.app._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir), \
             patch("cortex.db_helpers._resolve_project_dir", return_value=pdir):
            resp = client.post(
                f"/projects/{data['project_id']}/upload",
                data={"no_file_field": "value"},
            )
        assert resp.status_code == 422


# ===========================================================================
# _call_analyzer_tool / _call_launchpad_tool
# ===========================================================================

class TestAnalyzerLaunchpadHelpers:
    @pytest.mark.asyncio
    async def test_call_analyzer_tool_success(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"status": "ok", "data": [1, 2, 3]}

        with patch("cortex.app.get_service_url", return_value="http://analyzer:8000"), \
             patch("cortex.config.get_service_url", return_value="http://analyzer:8000"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("common.MCPHttpClient", return_value=mock_client):
            from cortex.app import _call_analyzer_tool
            result = await _call_analyzer_tool("get_analysis_summary", run_uuid="abc-123")

        assert result == {"status": "ok", "data": [1, 2, 3]}
        mock_client.connect.assert_awaited_once()
        mock_client.call_tool.assert_awaited_once_with("get_analysis_summary", run_uuid="abc-123")
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_analyzer_tool_mcp_error(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"success": False, "error": "Not found"}

        with patch("cortex.app.get_service_url", return_value="http://analyzer:8000"), \
             patch("cortex.config.get_service_url", return_value="http://analyzer:8000"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("common.MCPHttpClient", return_value=mock_client):
            from cortex.app import _call_analyzer_tool
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await _call_analyzer_tool("get_analysis_summary", run_uuid="bad")
            assert exc_info.value.status_code == 422
            assert "Not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_call_analyzer_tool_connection_error(self):
        mock_client = AsyncMock()
        mock_client.connect.side_effect = ConnectionError("refused")

        with patch("cortex.app.get_service_url", return_value="http://analyzer:8000"), \
             patch("cortex.config.get_service_url", return_value="http://analyzer:8000"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("common.MCPHttpClient", return_value=mock_client):
            from cortex.app import _call_analyzer_tool
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await _call_analyzer_tool("any_tool")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_call_launchpad_tool_success(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"run_uuid": "x-y-z"}

        with patch("cortex.app.get_service_url", return_value="http://launchpad:8000"), \
             patch("cortex.config.get_service_url", return_value="http://launchpad:8000"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("common.MCPHttpClient", return_value=mock_client):
            from cortex.app import _call_launchpad_tool
            result = await _call_launchpad_tool("check_nextflow_status", run_uuid="x-y-z")
        assert result["run_uuid"] == "x-y-z"

    @pytest.mark.asyncio
    async def test_call_launchpad_tool_failure(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"success": False, "error": "timeout", "detail": "worker hung"}

        with patch("cortex.app.get_service_url", return_value="http://launchpad:8000"), \
             patch("cortex.config.get_service_url", return_value="http://launchpad:8000"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("common.MCPHttpClient", return_value=mock_client):
            from cortex.app import _call_launchpad_tool
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await _call_launchpad_tool("check_nextflow_status", run_uuid="z")
            assert exc_info.value.status_code == 422
            assert "timeout" in exc_info.value.detail
            assert "worker hung" in exc_info.value.detail


# ===========================================================================
# Conversation messages endpoint
# ===========================================================================

class TestConversationMessages:
    def test_get_messages(self, setup):
        engine, SL, data, client = setup
        s = SL()
        cid = str(uuid.uuid4())
        conv = Conversation(id=cid, project_id=data["project_id"], user_id=data["user_id"],
                            title="Msg conv", created_at=datetime.datetime.utcnow())
        s.add(conv)
        msg = ConversationMessage(id=str(uuid.uuid4()), conversation_id=cid,
                                  role="user", content="hello", seq=1,
                                  created_at=datetime.datetime.utcnow())
        s.add(msg)
        s.commit()
        s.close()

        resp = client.get(f"/conversations/{cid}/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == cid
        assert len(body["messages"]) == 1
        assert body["messages"][0]["content"] == "hello"

    def test_get_messages_wrong_user(self, setup):
        engine, SL, data, client = setup
        s = SL()
        cid = str(uuid.uuid4())
        other_uid = str(uuid.uuid4())
        other_user = User(id=other_uid, email="other@test.com", username="other")
        s.add(other_user)
        conv = Conversation(id=cid, project_id=data["project_id"], user_id=other_uid,
                            title="Other conv", created_at=datetime.datetime.utcnow())
        s.add(conv)
        s.commit()
        s.close()

        resp = client.get(f"/conversations/{cid}/messages")
        assert resp.status_code in (403, 404)


# ===========================================================================
# Record project access
# ===========================================================================

class TestRecordProjectAccess:
    def test_record_access(self, setup):
        engine, SL, data, client = setup
        resp = client.post(
            f"/projects/{data['project_id']}/access",
            params={"project_name": "Updated Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ===========================================================================
# User last project
# ===========================================================================

class TestUserLastProject:
    def test_no_last_project(self, setup):
        engine, SL, data, client = setup
        resp = client.get("/user/last-project")
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_project_id"] is None

    def test_with_last_project(self, setup):
        engine, SL, data, client = setup
        s = SL()
        user = s.query(User).filter(User.id == data["user_id"]).first()
        user.last_project_id = data["project_id"]
        s.commit()
        s.close()

        resp = client.get("/user/last-project")
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_project_id"] == data["project_id"]


# ===========================================================================
# Analyzer proxy endpoints (via TestClient)
# ===========================================================================

class TestAnalyzerProxyEndpoints:
    def test_get_job_summary(self, setup):
        engine, SL, data, client = setup

        # Seed a job so require_run_uuid_access passes
        s = SL()
        cid = str(uuid.uuid4())
        conv = Conversation(id=cid, project_id=data["project_id"], user_id=data["user_id"],
                            title="Proxy conv", created_at=datetime.datetime.utcnow())
        s.add(conv)
        job = JobResult(id=str(uuid.uuid4()), conversation_id=cid, run_uuid="run-abc",
                        sample_name="S1", workflow_type="DNA", status="COMPLETED",
                        created_at=datetime.datetime.utcnow())
        s.add(job)
        s.commit()
        s.close()

        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"summary": "Great results"}

        with patch("cortex.app.get_service_url", return_value="http://a:8000"), \
             patch("cortex.config.get_service_url", return_value="http://a:8000"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("common.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.require_run_uuid_access", lambda run_uuid, user: None):
            resp = client.get("/analysis/jobs/run-abc/summary")
        assert resp.status_code == 200
        assert resp.json()["summary"] == "Great results"

    def test_list_job_files_proxy(self, setup):
        engine, SL, data, client = setup

        s = SL()
        cid = str(uuid.uuid4())
        conv = Conversation(id=cid, project_id=data["project_id"], user_id=data["user_id"],
                            title="Proxy conv2", created_at=datetime.datetime.utcnow())
        s.add(conv)
        job = JobResult(id=str(uuid.uuid4()), conversation_id=cid, run_uuid="run-files",
                        sample_name="S2", workflow_type="RNA", status="COMPLETED",
                        created_at=datetime.datetime.utcnow())
        s.add(job)
        s.commit()
        s.close()

        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"files": ["a.csv", "b.bed"]}

        with patch("cortex.app.get_service_url", return_value="http://a:8000"), \
             patch("cortex.config.get_service_url", return_value="http://a:8000"), \
             patch("cortex.app.MCPHttpClient", return_value=mock_client), \
             patch("common.MCPHttpClient", return_value=mock_client), \
             patch("cortex.app.require_run_uuid_access", lambda run_uuid, user: None):
            resp = client.get("/analysis/jobs/run-files/files")
        assert resp.status_code == 200
        assert resp.json()["files"] == ["a.csv", "b.bed"]
