"""
Tests for central user data folder — UserFile CRUD, dedup, symlinks,
project linking/unlinking, metadata editing, and API endpoints.
"""

import datetime
import hashlib
import json
import uuid

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, UserFile, UserFileProjectLink,
)
from cortex.app import app


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
    return engine


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture()
def seed_data(session_factory, tmp_path):
    sess = session_factory()

    user = User(id="u-ud", email="ud@example.com", role="user",
                username="uduser", is_active=True)
    sess.add(user)

    session_obj = SessionModel(id="ud-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)

    proj1 = Project(id="proj-1", name="Project Alpha", owner_id="u-ud",
                    slug="project-alpha")
    proj2 = Project(id="proj-2", name="Project Beta", owner_id="u-ud",
                    slug="project-beta")
    sess.add_all([proj1, proj2])
    sess.flush()

    for pid, pname in [("proj-1", "Project Alpha"), ("proj-2", "Project Beta")]:
        access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-ud",
                               project_id=pid, project_name=pname,
                               role="owner",
                               last_accessed=datetime.datetime.utcnow())
        sess.add(access)

    # Create central data dir and project dirs
    central = tmp_path / "users" / "uduser" / "data"
    central.mkdir(parents=True, exist_ok=True)
    (tmp_path / "users" / "uduser" / "project-alpha" / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "users" / "uduser" / "project-beta" / "data").mkdir(parents=True, exist_ok=True)

    sess.commit()
    sess.close()


@pytest.fixture()
def seeded_file(session_factory, tmp_path):
    """Create a UserFile in the DB and on disk, return its id."""
    central = tmp_path / "users" / "uduser" / "data"
    central.mkdir(parents=True, exist_ok=True)
    content = b"test file content for dedup"
    fpath = central / "sample.bam"
    fpath.write_bytes(content)
    md5 = hashlib.md5(content).hexdigest()

    sess = session_factory()
    uf_id = str(uuid.uuid4())
    uf = UserFile(
        id=uf_id,
        user_id="u-ud",
        filename="sample.bam",
        md5_hash=md5,
        size_bytes=len(content),
        source="encode",
        source_url="https://example.com/sample.bam",
        encode_accession="ENCFF001ABC",
        disk_path=str(fpath),
    )
    sess.add(uf)
    sess.commit()
    sess.close()
    return uf_id


@pytest.fixture()
def client(session_factory, seed_data, tmp_path):
    _mock_resolve = MagicMock(
        return_value=("uduser", "project-alpha", tmp_path / "users" / "uduser" / "project-alpha"),
    )

    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path), \
         patch("cortex.routes.files._resolve_user_and_project", _mock_resolve), \
         patch("cortex.routes.files.get_user_data_dir", return_value=tmp_path / "users" / "uduser" / "data"), \
         patch("cortex.routes.user_data.get_user_data_dir", return_value=tmp_path / "users" / "uduser" / "data"), \
         patch("cortex.app.asyncio"):

        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "ud-session")
        yield c


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestUserFileModel:
    def test_create_user_file(self, session_factory, tmp_path):
        sess = session_factory()
        uf = UserFile(
            id=str(uuid.uuid4()),
            user_id="u-ud",
            filename="test.bam",
            md5_hash="abc123",
            size_bytes=1024,
            source="url",
            disk_path=str(tmp_path / "test.bam"),
        )
        sess.add(uf)
        sess.commit()

        found = sess.execute(
            select(UserFile).where(UserFile.filename == "test.bam")
        ).scalar_one()
        assert found.md5_hash == "abc123"
        assert found.source == "url"
        sess.close()

    def test_create_project_link(self, session_factory, tmp_path):
        sess = session_factory()
        uf = UserFile(
            id="uf-link-test",
            user_id="u-ud",
            filename="linked.bam",
            md5_hash="def456",
            size_bytes=2048,
            source="encode",
            disk_path=str(tmp_path / "linked.bam"),
        )
        sess.add(uf)
        sess.flush()

        link = UserFileProjectLink(
            id=str(uuid.uuid4()),
            user_file_id="uf-link-test",
            project_id="proj-1",
            symlink_path=str(tmp_path / "users" / "uduser" / "project-alpha" / "data" / "linked.bam"),
        )
        sess.add(link)
        sess.commit()

        found = sess.execute(
            select(UserFileProjectLink).where(
                UserFileProjectLink.user_file_id == "uf-link-test"
            )
        ).scalar_one()
        assert found.project_id == "proj-1"
        sess.close()

    def test_metadata_fields(self, session_factory, tmp_path):
        sess = session_factory()
        tags = {"condition": "treated", "replicate": "1"}
        uf = UserFile(
            id=str(uuid.uuid4()),
            user_id="u-ud",
            filename="meta.bam",
            md5_hash="meta123",
            size_bytes=512,
            source="upload",
            disk_path=str(tmp_path / "meta.bam"),
            sample_name="sample1",
            organism="human",
            tissue="liver",
            tags_json=json.dumps(tags),
        )
        sess.add(uf)
        sess.commit()

        found = sess.execute(
            select(UserFile).where(UserFile.filename == "meta.bam")
        ).scalar_one()
        assert found.sample_name == "sample1"
        assert found.organism == "human"
        assert json.loads(found.tags_json) == tags
        sess.close()


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_compute_md5(self, tmp_path):
        from cortex.routes.files import _compute_md5
        content = b"hello world md5 test"
        fpath = tmp_path / "md5test.txt"
        fpath.write_bytes(content)
        assert _compute_md5(fpath) == hashlib.md5(content).hexdigest()

    def test_find_existing_user_file(self, session_factory, seeded_file):
        from cortex.routes.files import _find_existing_user_file
        sess = session_factory()
        found = _find_existing_user_file(sess, "u-ud", "sample.bam")
        assert found is not None
        assert found.id == seeded_file

        not_found = _find_existing_user_file(sess, "u-ud", "nonexistent.bam")
        assert not_found is None
        sess.close()

    def test_already_linked(self, session_factory, seeded_file):
        from cortex.routes.files import _already_linked, _link_file_to_project
        sess = session_factory()
        uf = sess.execute(select(UserFile).where(UserFile.id == seeded_file)).scalar_one()

        assert _already_linked(sess, seeded_file, "proj-1") is False

        _link_file_to_project(
            sess, user_file=uf, project_id="proj-1",
            symlink_path=Path("/fake/symlink"),
        )

        assert _already_linked(sess, seeded_file, "proj-1") is True
        sess.close()


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestListUserFiles:
    def test_empty_list(self, client):
        resp = client.get("/user/data")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_list_with_files(self, client, seeded_file):
        resp = client.get("/user/data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["files"][0]["filename"] == "sample.bam"
        assert data["files"][0]["encode_accession"] == "ENCFF001ABC"


class TestGetUserFile:
    def test_get_existing_file(self, client, seeded_file):
        resp = client.get(f"/user/data/{seeded_file}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == seeded_file
        assert data["filename"] == "sample.bam"

    def test_get_nonexistent(self, client):
        resp = client.get("/user/data/nonexistent-id")
        assert resp.status_code == 404


class TestUpdateMetadata:
    def test_update_sample_name(self, client, seeded_file):
        resp = client.patch(f"/user/data/{seeded_file}", json={
            "sample_name": "my-sample",
        })
        assert resp.status_code == 200
        assert resp.json()["sample_name"] == "my-sample"

    def test_update_all_fields(self, client, seeded_file):
        resp = client.patch(f"/user/data/{seeded_file}", json={
            "sample_name": "s1",
            "organism": "mouse",
            "tissue": "brain",
            "tags": {"replicate": "2", "condition": "control"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["organism"] == "mouse"
        assert data["tissue"] == "brain"
        assert data["tags"]["replicate"] == "2"

    def test_update_nonexistent(self, client):
        resp = client.patch("/user/data/fake-id", json={"sample_name": "x"})
        assert resp.status_code == 404


class TestLinkUnlink:
    def test_link_to_project(self, client, seeded_file, tmp_path):
        with patch("cortex.routes.user_data.create_project_file_symlink",
                    return_value=tmp_path / "users" / "uduser" / "project-beta" / "data" / "sample.bam"):
            resp = client.post(f"/user/data/{seeded_file}/link", json={
                "project_id": "proj-2",
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "linked"

    def test_link_already_linked(self, client, seeded_file, session_factory, tmp_path):
        # First, link manually
        sess = session_factory()
        uf = sess.execute(select(UserFile).where(UserFile.id == seeded_file)).scalar_one()
        link = UserFileProjectLink(
            id=str(uuid.uuid4()),
            user_file_id=seeded_file,
            project_id="proj-2",
            symlink_path=str(tmp_path / "symlink"),
        )
        sess.add(link)
        sess.commit()
        sess.close()

        resp = client.post(f"/user/data/{seeded_file}/link", json={
            "project_id": "proj-2",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_linked"

    def test_unlink_from_project(self, client, seeded_file, session_factory, tmp_path):
        # Create a symlink and link record
        sym_path = tmp_path / "users" / "uduser" / "project-alpha" / "data" / "sample.bam"
        sym_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a real file as the symlink target
        central_file = tmp_path / "users" / "uduser" / "data" / "sample.bam"
        if not sym_path.exists():
            sym_path.symlink_to(central_file)

        sess = session_factory()
        link = UserFileProjectLink(
            id=str(uuid.uuid4()),
            user_file_id=seeded_file,
            project_id="proj-1",
            symlink_path=str(sym_path),
        )
        sess.add(link)
        sess.commit()
        sess.close()

        resp = client.post(f"/user/data/{seeded_file}/unlink", json={
            "project_id": "proj-1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "unlinked"
        # Symlink should be removed
        assert not sym_path.is_symlink()


class TestDeleteFile:
    def test_delete_file(self, client, seeded_file, session_factory, tmp_path):
        resp = client.delete(f"/user/data/{seeded_file}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify DB row gone
        sess = session_factory()
        assert sess.execute(
            select(UserFile).where(UserFile.id == seeded_file)
        ).scalar_one_or_none() is None
        sess.close()

        # Verify file removed from disk
        central = tmp_path / "users" / "uduser" / "data" / "sample.bam"
        assert not central.exists()

    def test_delete_nonexistent(self, client):
        resp = client.delete("/user/data/fake-id")
        assert resp.status_code == 404


class TestRedownload:
    def test_redownload_requires_force(self, client, seeded_file):
        resp = client.post(f"/user/data/{seeded_file}/redownload", json={
            "force": False,
        })
        assert resp.status_code == 400

    def test_redownload_no_source_url(self, client, session_factory, tmp_path):
        # Create a file without source_url
        sess = session_factory()
        uf = UserFile(
            id="no-url-file",
            user_id="u-ud",
            filename="local.bam",
            md5_hash="xxx",
            size_bytes=100,
            source="upload",
            disk_path=str(tmp_path / "local.bam"),
        )
        sess.add(uf)
        sess.commit()
        sess.close()

        resp = client.post("/user/data/no-url-file/redownload", json={"force": True})
        assert resp.status_code == 400
        assert "no source URL" in resp.json()["detail"]

    def test_redownload_starts(self, client, seeded_file):
        with patch("cortex.routes.user_data.asyncio") as mock_async:
            mock_async.create_task = MagicMock()
            resp = client.post(f"/user/data/{seeded_file}/redownload", json={
                "force": True,
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "redownloading"


# ---------------------------------------------------------------------------
# Upload dedup test
# ---------------------------------------------------------------------------

class TestUploadDedup:
    def test_upload_deduplicates_identical_file(self, client, seeded_file, tmp_path):
        """Uploading the same content as an existing file should return deduped=True."""
        content = b"test file content for dedup"
        resp = client.post(
            "/projects/proj-1/upload",
            files={"f": ("sample.bam", content, "application/octet-stream")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["uploaded"][0].get("deduped") is True


# ---------------------------------------------------------------------------
# User jail helpers test
# ---------------------------------------------------------------------------

class TestUserJailHelpers:
    def test_get_user_data_dir(self, tmp_path):
        with patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
            from cortex.user_jail import get_user_data_dir
            d = get_user_data_dir("testuser")
            assert d == tmp_path / "users" / "testuser" / "data"
            assert d.exists()

    def test_create_project_file_symlink(self, tmp_path):
        with patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
            from cortex.user_jail import create_project_file_symlink
            # Create central file
            central = tmp_path / "users" / "testuser" / "data"
            central.mkdir(parents=True, exist_ok=True)
            central_file = central / "test.bam"
            central_file.write_bytes(b"bam content")

            sym = create_project_file_symlink(
                "testuser", "my-project", "test.bam", central_file,
            )
            assert sym.is_symlink()
            assert sym.resolve() == central_file.resolve()
            assert sym.read_bytes() == b"bam content"
