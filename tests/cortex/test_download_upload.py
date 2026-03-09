"""
Tests for cortex/app.py — Download & Upload endpoints.

Covers:
  - POST /projects/{id}/downloads  (initiate_download)
  - GET  /projects/{id}/downloads/{dl_id}  (get_download_status)
  - DELETE /projects/{id}/downloads/{dl_id}  (cancel_download)
  - POST /projects/{id}/upload  (upload_file)
"""

import datetime
import io
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
    ProjectBlock, UserFile, UserFileProjectLink,
)
from cortex.app import app, _active_downloads

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

    user = User(id="u-dl", email="dl@example.com", role="user",
                username="dluser", is_active=True)
    sess.add(user)

    session_obj = SessionModel(id="dl-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)

    proj = Project(id="proj-dl", name="DL Project", owner_id="u-dl",
                   slug="dl-project")
    sess.add(proj)
    sess.flush()

    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-dl",
                           project_id="proj-dl", project_name="DL Project",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    sess.add(access)
    sess.commit()
    sess.close()


@pytest.fixture()
def client(session_factory, seed_data, tmp_path):
    # Clean any leftover active downloads from other tests
    _active_downloads.clear()

    # Return (username, slug, project_dir) for the test user/project
    _mock_resolve = MagicMock(return_value=("dluser", "dl-project", tmp_path / "proj"))

    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path), \
         patch("cortex.routes.files._resolve_user_and_project", _mock_resolve), \
         patch("cortex.routes.files.get_user_data_dir", return_value=tmp_path / "users" / "dluser" / "data"), \
         patch("cortex.routes.files.create_project_file_symlink", side_effect=lambda u, s, fn, cp: tmp_path / "proj" / "data" / fn), \
         patch("cortex.app._resolve_project_dir", return_value=tmp_path / "proj"), \
         patch("cortex.db_helpers._resolve_project_dir", return_value=tmp_path / "proj"), \
         patch("cortex.app.asyncio"), \
         patch("cortex.routes.files.asyncio") as mock_asyncio:
        # Prevent actual background tasks from running
        mock_asyncio.create_task = lambda coro: None
        # Ensure central data dir exists
        (tmp_path / "users" / "dluser" / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "proj" / "data").mkdir(parents=True, exist_ok=True)
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "dl-session")
        yield c
    _active_downloads.clear()


# ---------------------------------------------------------------------------
# POST /projects/{id}/downloads
# ---------------------------------------------------------------------------
class TestInitiateDownload:
    def test_basic_download(self, client, tmp_path):
        resp = client.post("/projects/proj-dl/downloads", json={
            "source": "encode",
            "files": [
                {"url": "https://example.com/ENCFF001.bam", "filename": "ENCFF001.bam"},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "download_id" in data
        assert data["file_count"] == 1
        assert "target_dir" in data
        assert data["block_id"]

    def test_multiple_files(self, client):
        resp = client.post("/projects/proj-dl/downloads", json={
            "source": "url",
            "files": [
                {"url": "https://ex.com/a.bam", "filename": "a.bam"},
                {"url": "https://ex.com/b.bam", "filename": "b.bam"},
                {"url": "https://ex.com/c.csv", "filename": "c.csv"},
            ],
        })
        assert resp.status_code == 200
        assert resp.json()["file_count"] == 3

    def test_empty_files_rejected(self, client):
        resp = client.post("/projects/proj-dl/downloads", json={
            "source": "encode",
            "files": [],
        })
        assert resp.status_code == 422

    def test_custom_subfolder(self, client, tmp_path):
        resp = client.post("/projects/proj-dl/downloads", json={
            "source": "url",
            "files": [{"url": "https://ex.com/f.pod5", "filename": "f.pod5"}],
            "subfolder": "raw",
        })
        assert resp.status_code == 200

    def test_creates_download_block(self, client, session_factory):
        resp = client.post("/projects/proj-dl/downloads", json={
            "source": "encode",
            "files": [{"url": "https://ex.com/file.bam", "filename": "file.bam"}],
        })
        assert resp.status_code == 200
        block_id = resp.json()["block_id"]

        sess = session_factory()
        block = sess.execute(
            select(ProjectBlock).where(ProjectBlock.id == block_id)
        ).scalar_one()
        assert block.type == "DOWNLOAD_TASK"
        assert block.status == "RUNNING"
        payload = json.loads(block.payload_json)
        assert payload["total_files"] == 1
        assert payload["status"] == "RUNNING"
        sess.close()

    def test_unauthenticated(self, session_factory, tmp_path):
        with patch("cortex.db.SessionLocal", session_factory), \
             patch("cortex.app.SessionLocal", session_factory), \
             patch("cortex.dependencies.SessionLocal", session_factory), \
             patch("cortex.middleware.SessionLocal", session_factory):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post("/projects/proj-dl/downloads", json={
                "source": "url",
                "files": [{"url": "https://ex.com/f.bam"}],
            })
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /projects/{id}/downloads/{dl_id}
# ---------------------------------------------------------------------------
class TestGetDownloadStatus:
    def test_unknown_download(self, client):
        resp = client.get("/projects/proj-dl/downloads/nonexistent")
        assert resp.status_code == 404

    def test_known_download(self, client, session_factory):
        # First create a download
        resp = client.post("/projects/proj-dl/downloads", json={
            "source": "url",
            "files": [{"url": "https://ex.com/f.bam", "filename": "f.bam"}],
        })
        dl_id = resp.json()["download_id"]

        # Now poll status
        resp2 = client.get(f"/projects/proj-dl/downloads/{dl_id}")
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["status"] == "RUNNING"
        assert data["total_files"] == 1


# ---------------------------------------------------------------------------
# DELETE /projects/{id}/downloads/{dl_id}
# ---------------------------------------------------------------------------
class TestCancelDownload:
    def test_cancel_unknown(self, client):
        resp = client.delete("/projects/proj-dl/downloads/nonexistent")
        assert resp.status_code == 404

    def test_cancel_active(self, client):
        # Create a download first
        resp = client.post("/projects/proj-dl/downloads", json={
            "source": "url",
            "files": [{"url": "https://ex.com/f.bam", "filename": "f.bam"}],
        })
        dl_id = resp.json()["download_id"]

        # Cancel it
        resp2 = client.delete(f"/projects/proj-dl/downloads/{dl_id}")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "cancelling"

        # Verify the in-memory flag was set
        assert _active_downloads[dl_id]["cancelled"] is True


# ---------------------------------------------------------------------------
# POST /projects/{id}/upload
# ---------------------------------------------------------------------------
class TestUploadFile:
    def test_basic_upload(self, client, tmp_path):
        resp = client.post(
            "/projects/proj-dl/upload",
            files={"file1": ("sample.bam", b"fake bam content", "application/octet-stream")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["uploaded"][0]["filename"] == "sample.bam"
        assert data["uploaded"][0]["size_bytes"] == len(b"fake bam content")

    def test_multiple_upload(self, client, tmp_path):
        resp = client.post(
            "/projects/proj-dl/upload",
            files=[
                ("file1", ("a.pod5", b"pod5data", "application/octet-stream")),
                ("file2", ("b.csv", b"col1,col2\n1,2", "text/csv")),
            ],
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_filename_sanitized(self, client, tmp_path):
        resp = client.post(
            "/projects/proj-dl/upload",
            files={"f": ("my file (1).bam", b"data", "application/octet-stream")},
        )
        assert resp.status_code == 200
        fname = resp.json()["uploaded"][0]["filename"]
        # Spaces and parens should be replaced with underscores
        assert " " not in fname
        assert "(" not in fname

    def test_no_files_rejected(self, client):
        resp = client.post(
            "/projects/proj-dl/upload",
            data={"some_field": "not a file"},
        )
        assert resp.status_code == 422

    def test_file_written_to_disk(self, client, tmp_path):
        content = b"Real file content here"
        resp = client.post(
            "/projects/proj-dl/upload",
            files={"f": ("test.txt", content, "text/plain")},
        )
        assert resp.status_code == 200
        # File should be written to central data dir
        central = tmp_path / "users" / "dluser" / "data" / "test.txt"
        assert central.exists()
        assert central.read_bytes() == content
