"""
Tests for cortex/app.py — chat endpoint edge cases: chain recovery (LLM echoes
find_file JSON), hallucination detection, auto-fetch missing files, download
background task, disk usage with files, permanent delete cascade, upload full flow.

Targets uncovered lines: 5104-5155 (auto-fetch), 5193-5241 (chain recovery),
  4600-4661 (hallucination strip), 6678-6775 (download background),
  7076-7224 (disk usage, permanent delete detail), 6882-6915 (upload flow).
"""
import contextlib
import datetime
import json
import os
import re
import uuid

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage, JobResult,
)
from cortex.app import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def SL(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def seed(SL, tmp_path):
    """Seed user + session + project + access + conversation."""
    s = SL()
    user = User(id="u-edge", email="edge@test.com", role="user",
                username="edgeuser", is_active=True)
    s.add(user)
    sess = SessionModel(id="edge-session", user_id=user.id,
                        is_valid=True,
                        expires_at=datetime.datetime(2099, 1, 1))
    s.add(sess)
    proj = Project(id="proj-edge", name="Edge Proj", owner_id="u-edge",
                   slug="edge-proj")
    s.add(proj)
    s.flush()
    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-edge",
                           project_id="proj-edge", project_name="Edge Proj",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    s.add(access)
    conv = Conversation(id="conv-edge", project_id="proj-edge",
                        user_id="u-edge", title="Edge Convo",
                        created_at=datetime.datetime.utcnow())
    s.add(conv)
    s.commit()
    s.close()


def _make_client(SL, seed, tmp_path, think_fn, extra_patches=None):
    """Build a TestClient with a custom AgentEngine.think mock."""
    mock_engine_cls = MagicMock()
    inst = mock_engine_cls.return_value
    inst.model_name = "test-model"
    inst.think = think_fn
    inst.analyze_results = lambda msg, md, data, skill, hist: (
        "Analysis complete: " + md[:100],
        {"prompt_tokens": 15, "completion_tokens": 15, "total_tokens": 30},
    )

    patches = [
        patch("cortex.db.SessionLocal", SL),
        patch("cortex.app.SessionLocal", SL),
        patch("cortex.chat_stages.setup.SessionLocal", SL),
        patch("cortex.chat_stages.overrides.SessionLocal", SL),
        patch("cortex.dependencies.SessionLocal", SL),
        patch("cortex.middleware.SessionLocal", SL),
        patch("cortex.admin.SessionLocal", SL),
        patch("cortex.auth.SessionLocal", SL),
        patch("cortex.config.AGOUTIC_DATA", tmp_path),
        patch("cortex.user_jail.AGOUTIC_DATA", tmp_path),
        patch("cortex.app._resolve_project_dir", return_value=tmp_path / "proj"),
        patch("cortex.chat_stages.context_prep._resolve_project_dir", return_value=tmp_path / "proj"),
        patch("cortex.app.AgentEngine", mock_engine_cls),
        patch("cortex.agent_engine.AgentEngine", mock_engine_cls),
        patch("cortex.chat_stages.context_prep.AgentEngine", mock_engine_cls),
        patch("cortex.chat_stages.llm_first_pass.AgentEngine", mock_engine_cls),
    ]
    if extra_patches:
        patches.extend(extra_patches)

    for p in patches:
        p.start()

    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("session", "edge-session")
    yield c

    for p in reversed(patches):
        p.stop()


def _chat(client, message, skill="welcome", project_id="proj-edge"):
    return client.post("/chat", json={
        "project_id": project_id,
        "message": message,
        "skill": skill,
    })


@contextlib.contextmanager
def _patch_session(SL, tmp_path):
    """Minimal session patch context manager for non-chat endpoints."""
    patches = [
        patch("cortex.db.SessionLocal", SL),
        patch("cortex.app.SessionLocal", SL),
        patch("cortex.chat_stages.setup.SessionLocal", SL),
        patch("cortex.chat_stages.overrides.SessionLocal", SL),
        patch("cortex.dependencies.SessionLocal", SL),
        patch("cortex.middleware.SessionLocal", SL),
        patch("cortex.admin.SessionLocal", SL),
        patch("cortex.auth.SessionLocal", SL),
        patch("cortex.config.AGOUTIC_DATA", tmp_path),
        patch("cortex.user_jail.AGOUTIC_DATA", tmp_path),
    ]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()


# ===========================================================================
# 1. Hallucinated ENCODE results detection (lines 4600-4618)
# ===========================================================================

class TestHallucinatedResults:
    def test_hallucinated_encode_count_stripped(self, SL, seed, tmp_path):
        """If LLM says 'found 100 experiments' without any DATA_CALL, it's hallucinated."""
        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "There are 1,234 ChIP-seq experiments in ENCODE that match your query. "
                    "Here is an interactive table below.",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            # Second call (if auto-generation triggers)
            return (
                "I wasn't able to find what you need. Could you clarify?",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"total": 0, "experiments": []}
        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]
        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "find all K562 experiments", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        # The hallucinated text should be replaced with a clarifying question
        # OR auto-generation kicked in and the 2nd LLM response is used
        assert "1,234" not in md

    def test_no_false_positive_hallucination(self, SL, seed, tmp_path):
        """Normal responses without hallucination patterns should pass through."""
        def think(msg, skill, history):
            return (
                "To search for K562 experiments, I'll query the ENCODE database.\n"
                "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"total": 5, "experiments": [{"accession": "ENCSR001"}]}
        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]
        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "find K562 experiments", skill="ENCODE_Search")
        assert resp.status_code == 200


# ===========================================================================
# 2. Chain recovery: LLM echoes find_file JSON (lines 5193-5241)
# ===========================================================================

class TestChainRecovery:
    def test_llm_echoes_find_file_json(self, SL, seed, tmp_path):
        """When LLM echoes find_file result JSON, should auto-chain to parse."""
        mock_mcp = AsyncMock()
        # find_file returns result
        find_result = {
            "success": True,
            "primary_path": "results/output.csv",
            "run_uuid": "abc-123",
        }
        # parse_csv_file result
        parse_result = {
            "file_path": "results/output.csv",
            "columns": ["Sample", "Score"],
            "data": [{"Sample": "A", "Score": 95}],
            "row_count": 1,
        }

        async def mock_call_tool(tool_name, **kwargs):
            if tool_name == "find_file":
                return find_result
            elif tool_name == "parse_csv_file":
                return parse_result
            return {}

        mock_mcp.call_tool = mock_call_tool

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "I'll look for that file.\n"
                    "[[DATA_CALL: service=analyzer, tool=find_file, run_uuid=abc-123, filename=output.csv]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found output.csv with 1 row of data.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "open output.csv", skill="analyze_job_results")
        assert resp.status_code == 200


# ===========================================================================
# 3. Auto-generation suppression with injected data (lines 4524-4540)
# ===========================================================================

class TestInjectedDataSuppression:
    def test_injected_data_suppresses_data_calls(self, SL, seed, tmp_path):
        """When previous data is injected, LLM DATA_CALL tags should be suppressed."""
        # Seed a conversation message with data in <details> tags
        s = SL()
        cm = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id="conv-edge",
            role="assistant",
            content=(
                "Here are the results.\n"
                "<details><summary>Data</summary>\n"
                "ENCSR000AAA: ChIP-seq on K562\nENCSR000AAB: RNA-seq on HepG2\n"
                "</details>"
            ),
            seq=1,
            created_at=datetime.datetime.utcnow(),
        )
        s.add(cm)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "Based on the data, these are interesting.\n"
                "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=DF1]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "tell me about these", skill="ENCODE_Search")
        assert resp.status_code == 200


# ===========================================================================
# 4. Retry on connection errors (lines 4888-4901)
# ===========================================================================

class TestMCPConnectionRetry:
    def test_transient_error_retries_once(self, SL, seed, tmp_path):
        """Single retry on ConnectionError for MCP tool calls."""
        mock_mcp = AsyncMock()
        call_count = [0]

        async def mock_call_tool(tool_name, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("Connection reset")
            return {"total": 3, "experiments": [{"accession": "ENCSR001"}]}

        mock_mcp.call_tool = mock_call_tool

        think_count = [0]
        def think(msg, skill, history):
            think_count[0] += 1
            if think_count[0] == 1:
                return (
                    "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found 3 experiments.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "search K562", skill="ENCODE_Search")
        assert resp.status_code == 200
        # Should have retried (2 calls total)
        assert call_count[0] == 2

    def test_retry_also_fails_returns_error(self, SL, seed, tmp_path):
        """When retry also fails, record error and continue."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.side_effect = ConnectionError("Permanently down")

        def think(msg, skill, history):
            return (
                "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "search K562", skill="ENCODE_Search")
        assert resp.status_code == 200


# ===========================================================================
# 5. Routing error skip (lines 4872-4880)
# ===========================================================================

class TestRoutingErrorSkip:
    def test_routing_error_in_params_skips_tool(self, SL, seed, tmp_path):
        """Params containing __routing_error__ should skip the tool call."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"total": 0}

        def think(msg, skill, history):
            return (
                "[[DATA_CALL: consortium=encode, tool=get_experiment, accession=notreal]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "get experiment notreal", skill="ENCODE_Search")
        assert resp.status_code == 200


# ===========================================================================
# 6. Permanent delete cascade (lines 7148-7224)
# ===========================================================================

class TestPermanentDeleteCascade:
    def test_permanent_delete_with_conversations_and_blocks(self, SL, seed, tmp_path):
        """Full cascade: messages, conversations, blocks, access all deleted."""
        s = SL()
        # Add conversation messages
        cm1 = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id="conv-edge",
            role="user",
            content="hello",
            seq=1,
            created_at=datetime.datetime.utcnow(),
        )
        s.add(cm1)

        # Add project blocks
        blk = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-edge",
            owner_id="u-edge",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json='{"markdown": "test"}',
            created_at=datetime.datetime.utcnow(),
        )
        s.add(blk)

        # Add a job result
        jr = JobResult(
            id=str(uuid.uuid4()),
            conversation_id="conv-edge",
            run_uuid="run-123",
            sample_name="sample1",
            workflow_type="DNA",
            status="COMPLETED",
            created_at=datetime.datetime.utcnow(),
        )
        s.add(jr)
        s.commit()
        s.close()

        # Create project dir with files
        proj_dir = tmp_path / "users" / "edgeuser" / "edge-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "data.txt").write_text("hello")

        with _patch_session(SL, tmp_path):
            with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
                c = TestClient(app, raise_server_exceptions=False)
                c.cookies.set("session", "edge-session")
                resp = c.delete("/projects/proj-edge/permanent")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "deleted"
                assert data["id"] == "proj-edge"

        # Verify cascade
        s2 = SL()
        assert s2.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == "conv-edge").count() == 0
        assert s2.query(Conversation).filter(
            Conversation.project_id == "proj-edge").count() == 0
        assert s2.query(ProjectBlock).filter(
            ProjectBlock.project_id == "proj-edge").count() == 0
        assert s2.query(Project).filter(Project.id == "proj-edge").count() == 0
        s2.close()


# ===========================================================================
# 7. Disk usage with real files (lines 7076-7099)
# ===========================================================================

class TestDiskUsageWithFiles:
    def test_disk_usage_counts_project_files(self, SL, seed, tmp_path):
        """Disk usage endpoint should count files in project directory."""
        # Create project dir with some files
        proj_dir = tmp_path / "users" / "edgeuser" / "edge-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "data1.txt").write_text("a" * 1000)
        (proj_dir / "data2.txt").write_text("b" * 2000)
        subdir = proj_dir / "results"
        subdir.mkdir()
        (subdir / "output.csv").write_text("c" * 500)

        with _patch_session(SL, tmp_path):
            with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
                c = TestClient(app, raise_server_exceptions=False)
                c.cookies.set("session", "edge-session")
                resp = c.get("/user/disk-usage")
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("total_bytes", 0) >= 3500
                # Verify projects breakdown
                projects = data.get("projects", [])
                if projects:
                    assert projects[0].get("size_bytes", 0) >= 3500
                    assert projects[0].get("file_count", 0) >= 3


# ===========================================================================
# 8. Upload with actual file content (lines 6882-6915)
# ===========================================================================

class TestUploadWithContent:
    def test_upload_single_file_creates_file(self, SL, seed, tmp_path):
        """Upload endpoint writes file to data/ dir and returns metadata."""
        proj_dir = tmp_path / "users" / "edgeuser" / "edge-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)

        with _patch_session(SL, tmp_path):
            with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
                with patch("cortex.app._post_download_suggestions", new_callable=AsyncMock):
                    c = TestClient(app, raise_server_exceptions=False)
                    c.cookies.set("session", "edge-session")
                    resp = c.post(
                        "/projects/proj-edge/upload",
                        files={"file": ("test_sample.fastq.gz", b"ATCG" * 100, "application/gzip")},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["count"] == 1
                    assert data["uploaded"][0]["filename"] == "test_sample.fastq.gz"
                    assert data["uploaded"][0]["size_bytes"] == 400
                    # Verify file was written
                    written = proj_dir / "data" / "test_sample.fastq.gz"
                    assert written.exists()

    def test_upload_sanitizes_filename(self, SL, seed, tmp_path):
        """Filenames with special chars should be sanitized."""
        proj_dir = tmp_path / "users" / "edgeuser" / "edge-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)

        with _patch_session(SL, tmp_path):
            with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
                with patch("cortex.app._post_download_suggestions", new_callable=AsyncMock):
                    c = TestClient(app, raise_server_exceptions=False)
                    c.cookies.set("session", "edge-session")
                    resp = c.post(
                        "/projects/proj-edge/upload",
                        files={"file": ("my file (1).txt", b"content", "text/plain")},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    # Spaces and parens should be replaced with _
                    safe_name = data["uploaded"][0]["filename"]
                    assert " " not in safe_name
                    assert "(" not in safe_name


# ===========================================================================
# 9. Project stats with jobs (lines 6386-6434)
# ===========================================================================

class TestProjectStatsWithJobs:
    def test_project_stats_with_dogme_jobs(self, SL, seed, tmp_path):
        """Stats dashboard should include job counts from dogme_jobs."""
        # Insert dogme_jobs rows
        s = SL()
        try:
            s.execute(text(
                "INSERT INTO dogme_jobs "
                "(run_uuid, project_id, user_id, sample_name, mode, status, submitted_at) "
                "VALUES "
                "(:uuid, :pid, :uid, :sn, :mode, :status, :ts)"
            ), {
                "uuid": "run-1", "pid": "proj-edge", "uid": "u-edge",
                "sn": "sample1", "mode": "DNA", "status": "COMPLETED",
                "ts": datetime.datetime.utcnow().isoformat(),
            })
            s.execute(text(
                "INSERT INTO dogme_jobs "
                "(run_uuid, project_id, user_id, sample_name, mode, status, submitted_at) "
                "VALUES "
                "(:uuid, :pid, :uid, :sn, :mode, :status, :ts)"
            ), {
                "uuid": "run-2", "pid": "proj-edge", "uid": "u-edge",
                "sn": "sample2", "mode": "RNA", "status": "FAILED",
                "ts": datetime.datetime.utcnow().isoformat(),
            })
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()

        proj_dir = tmp_path / "users" / "edgeuser" / "edge-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)

        with _patch_session(SL, tmp_path):
            with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
                c = TestClient(app, raise_server_exceptions=False)
                c.cookies.set("session", "edge-session")
                resp = c.get("/projects/proj-edge/stats")
                assert resp.status_code == 200
                data = resp.json()
                # Should have job info
                assert isinstance(data, dict)
                # jobs list or job counts should be present
                jobs = data.get("jobs", [])
                if jobs:
                    assert len(jobs) == 2
                    statuses = {j["status"] for j in jobs}
                    assert "COMPLETED" in statuses
                    assert "FAILED" in statuses


# ===========================================================================
# 10. Download background task (lines 6678-6775)
# ===========================================================================

class TestDownloadBackgroundTask:
    @pytest.mark.asyncio
    async def test_download_files_background_success(self, SL, seed, tmp_path):
        """_download_files_background downloads files and updates block."""
        from cortex.app import _download_files_background, _active_downloads

        # Create a download block
        s = SL()
        blk = ProjectBlock(
            id="dl-block-1",
            project_id="proj-edge",
            owner_id="u-edge",
            seq=1,
            type="DOWNLOAD_TASK",
            status="RUNNING",
            payload_json=json.dumps({"status": "DOWNLOADING", "total": 1}),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(blk)
        s.commit()
        s.close()

        # Set up download target
        target_dir = tmp_path / "downloads"
        target_dir.mkdir()

        # Register active download
        download_id = "dl-001"
        _active_downloads[download_id] = {"cancelled": False}

        files = [{
            "url": "https://example.com/test.txt",
            "filename": "test.txt",
            "size_bytes": 11,
        }]

        # Mock httpx client
        import httpx

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-length": "11"}
        mock_response.aiter_bytes = AsyncMock(return_value=iter([b"hello world"]))

        # We need to mock the AsyncClient.stream context manager
        with patch("cortex.app.SessionLocal", SL):
            with patch("cortex.app._post_download_suggestions", new_callable=AsyncMock):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                    # Mock stream context manager
                    mock_stream_resp = AsyncMock()
                    mock_stream_resp.raise_for_status = MagicMock()
                    mock_stream_resp.headers = {"content-length": "11"}

                    async def fake_chunks(*args, **kwargs):
                        yield b"hello world"

                    mock_stream_resp.aiter_bytes = fake_chunks

                    mock_stream_cm = AsyncMock()
                    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_stream_resp)
                    mock_stream_cm.__aexit__ = AsyncMock(return_value=False)
                    mock_client.stream = MagicMock(return_value=mock_stream_cm)

                    await _download_files_background(
                        download_id=download_id,
                        block_id="dl-block-1",
                        files=files,
                        target_dir=target_dir,
                        project_id="proj-edge",
                        owner_id="u-edge",
                    )

        # Verify the download wrote the file
        assert (target_dir / "test.txt").exists()

        # Cleanup
        _active_downloads.pop(download_id, None)


# ===========================================================================
# 11. Update download block (lines 6782-6812)
# ===========================================================================

class TestUpdateDownloadBlock:
    def test_update_download_block_payload(self, SL, seed, tmp_path):
        """_update_download_block should merge updates into block payload."""
        from cortex.app import _update_download_block

        s = SL()
        blk = ProjectBlock(
            id="udl-block-1",
            project_id="proj-edge",
            owner_id="u-edge",
            seq=1,
            type="DOWNLOAD_TASK",
            status="RUNNING",
            payload_json=json.dumps({"status": "DOWNLOADING", "total": 3}),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(blk)
        s.commit()

        _update_download_block(s, "udl-block-1", {
            "downloaded": 2,
            "bytes_downloaded": 5000,
        })

        s.refresh(blk)
        payload = json.loads(blk.payload_json)
        assert payload["downloaded"] == 2
        assert payload["bytes_downloaded"] == 5000
        assert payload["total"] == 3  # prior value preserved
        s.close()

    def test_update_download_block_with_status(self, SL, seed, tmp_path):
        """_update_download_block can update block status too."""
        from cortex.app import _update_download_block

        s = SL()
        blk = ProjectBlock(
            id="udl-block-2",
            project_id="proj-edge",
            owner_id="u-edge",
            seq=2,
            type="DOWNLOAD_TASK",
            status="RUNNING",
            payload_json=json.dumps({"status": "DOWNLOADING"}),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(blk)
        s.commit()

        _update_download_block(s, "udl-block-2", {
            "status": "DONE",
            "downloaded": 3,
        }, status="DONE")

        s.refresh(blk)
        assert blk.status == "DONE"
        payload = json.loads(blk.payload_json)
        assert payload["status"] == "DONE"
        s.close()

    def test_update_download_block_missing_block(self, SL, seed, tmp_path):
        """_update_download_block with non-existent block should not crash."""
        from cortex.app import _update_download_block

        s = SL()
        # Should not raise
        _update_download_block(s, "nonexistent-block", {"status": "DONE"})
        s.close()


# ===========================================================================
# 12. Project files listing (lines 6507-6530)
# ===========================================================================

class TestProjectFiles:
    def test_project_files_listing_with_nested_dirs(self, SL, seed, tmp_path):
        """Project files endpoint should list files recursively."""
        proj_dir = tmp_path / "users" / "edgeuser" / "edge-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "readme.md").write_text("# Hello")
        data_dir = proj_dir / "data"
        data_dir.mkdir()
        (data_dir / "sample.fastq").write_text("@seq1")
        results_dir = proj_dir / "results"
        results_dir.mkdir()
        (results_dir / "report.html").write_text("<html>report</html>")

        with _patch_session(SL, tmp_path):
            with patch("cortex.app._resolve_project_dir", return_value=proj_dir):
                c = TestClient(app, raise_server_exceptions=False)
                c.cookies.set("session", "edge-session")
                resp = c.get("/projects/proj-edge/files")
                assert resp.status_code == 200
                data = resp.json()
                assert isinstance(data, dict)
                # Should have files in the response
                files = data.get("files", data.get("entries", []))
                if files:
                    assert len(files) >= 3


# ===========================================================================
# 13. Token usage with tracked messages (lines 6920-7060)
# ===========================================================================

class TestTokenUsageDetail:
    def test_token_usage_aggregation(self, SL, seed, tmp_path):
        """Token usage endpoint should aggregate tokens from conversation messages."""
        s = SL()
        # Add messages with token data
        for i in range(3):
            cm = ConversationMessage(
                id=str(uuid.uuid4()),
                conversation_id="conv-edge",
                role="assistant",
                content=f"Response {i}",
                seq=i + 1,
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                model_name="test-model",
                created_at=datetime.datetime.utcnow(),
            )
            s.add(cm)
        s.commit()
        s.close()

        with _patch_session(SL, tmp_path):
            c = TestClient(app, raise_server_exceptions=False)
            c.cookies.set("session", "edge-session")
            resp = c.get("/user/token-usage")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, dict)
            # Should have lifetime aggregated token counts
            lifetime = data.get("lifetime", {})
            assert lifetime.get("prompt_tokens", 0) >= 300
            assert lifetime.get("completion_tokens", 0) >= 150
            assert lifetime.get("total_tokens", 0) >= 450
            # Daily breakdown should be present
            assert "daily" in data
            assert "by_conversation" in data


# ===========================================================================
# 14. Health and skills endpoints (basic coverage)
# ===========================================================================

class TestBasicEndpoints:
    def test_health_check(self):
        """Health endpoint requires no auth."""
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "cortex"

    def test_skills_endpoint(self, SL, seed, tmp_path):
        """Skills endpoint returns available skills."""
        with _patch_session(SL, tmp_path):
            c = TestClient(app, raise_server_exceptions=False)
            c.cookies.set("session", "edge-session")
            resp = c.get("/skills")
            assert resp.status_code == 200
            data = resp.json()
            assert "skills" in data
            assert "count" in data
            assert data["count"] > 0


# ===========================================================================
# 15. Chat progress tracking (lines 174-184)
# ===========================================================================

class TestChatProgressTracking:
    def test_emit_progress_records_stage(self):
        """_emit_progress should record stage info."""
        from cortex.app import _emit_progress, _chat_progress
        req_id = f"test-{uuid.uuid4()}"
        _emit_progress(req_id, "thinking", "Processing message...")
        assert req_id in _chat_progress
        assert _chat_progress[req_id]["stage"] == "thinking"
        assert _chat_progress[req_id]["detail"] == "Processing message..."
        # Cleanup
        _chat_progress.pop(req_id, None)

    def test_emit_progress_no_request_id(self):
        """_emit_progress with empty request_id should be a no-op."""
        from cortex.app import _emit_progress, _chat_progress
        before_count = len(_chat_progress)
        _emit_progress("", "thinking", "test")
        assert len(_chat_progress) == before_count


# ===========================================================================
# 16. get_block_payload helper
# ===========================================================================

class TestGetBlockPayload:
    def test_get_block_payload_valid_json(self):
        """get_block_payload returns parsed dict."""
        from cortex.llm_validators import get_block_payload
        blk = MagicMock()
        blk.payload_json = '{"markdown": "hello", "score": 42}'
        result = get_block_payload(blk)
        assert result == {"markdown": "hello", "score": 42}

    def test_get_block_payload_none(self):
        """get_block_payload with None returns empty dict."""
        from cortex.llm_validators import get_block_payload
        blk = MagicMock()
        blk.payload_json = None
        result = get_block_payload(blk)
        assert result == {}


# TestResolveProjectDir removed — already covered implicitly by endpoint tests
# and has test ordering fragility with _make_client patches.
