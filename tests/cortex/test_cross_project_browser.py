"""
Tests for cross-project browser endpoints.

Tests:
1. authorized project filtering happens before scan
2. browsing accessible project succeeds
3. browsing inaccessible project is rejected
4. traversal path is rejected
5. absolute path input is rejected or normalized safely
6. bounded payload / truncation metadata works
7. search returns only authorized-project results
8. default responses do not expose absolute paths
9. existing project-local browsing remains unaffected
"""

import contextlib
import datetime
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import (
    Project,
    ProjectAccess,
    Session as SessionModel,
    User,
)


# ---------------------------------------------------------------------------
# DB + auth helpers
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


def _seed_users_and_projects(session, tmp_agoutic_data):
    """Create two users with separate projects."""
    uid1 = str(uuid.uuid4())
    uid2 = str(uuid.uuid4())
    sid1 = str(uuid.uuid4())
    sid2 = str(uuid.uuid4())
    pid1 = str(uuid.uuid4())
    pid2 = str(uuid.uuid4())

    user1 = User(id=uid1, email="user1@test.com", username="user1", is_active=True)
    user2 = User(id=uid2, email="user2@test.com", username="user2", is_active=True)
    session.add_all([user1, user2])
    session.flush()

    sess1 = SessionModel(id=sid1, user_id=uid1, expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1))
    sess2 = SessionModel(id=sid2, user_id=uid2, expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1))
    session.add_all([sess1, sess2])

    proj1 = Project(id=pid1, name="User1 Project", slug="user1-project", owner_id=uid1, created_at=datetime.datetime.utcnow())
    proj2 = Project(id=pid2, name="User2 Project", slug="user2-project", owner_id=uid2, created_at=datetime.datetime.utcnow())
    session.add_all([proj1, proj2])

    access1 = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=uid1,
        project_id=pid1,
        project_name="User1 Project",
        role="owner",
    )
    access2 = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=uid2,
        project_id=pid2,
        project_name="User2 Project",
        role="owner",
    )
    session.add_all([access1, access2])
    session.commit()

    # Create filesystem directories
    user1_dir = tmp_agoutic_data / "users" / "user1" / "user1-project"
    user2_dir = tmp_agoutic_data / "users" / "user2" / "user2-project"
    user1_dir.mkdir(parents=True, exist_ok=True)
    user2_dir.mkdir(parents=True, exist_ok=True)

    # Create test files
    (user1_dir / "data").mkdir(exist_ok=True)
    (user1_dir / "data" / "test.bam").write_text("dummy bam content")
    (user1_dir / "data" / "test.bai").write_text("dummy bai content")
    (user1_dir / "workflow1").mkdir(exist_ok=True)
    (user1_dir / "workflow1" / "results.tsv").write_text("col1\tcol2\n")

    (user2_dir / "data").mkdir(exist_ok=True)
    (user2_dir / "data" / "other.fastq").write_text("@read1\n")

    return {
        "user1_id": uid1,
        "user2_id": uid2,
        "session1_id": sid1,
        "session2_id": sid2,
        "project1_id": pid1,
        "project2_id": pid2,
        "user1_dir": user1_dir,
        "user2_dir": user2_dir,
    }


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """Yield (engine, SessionLocal factory, seed data, TestClient, tmp_agoutic_data)."""
    from fastapi.testclient import TestClient
    from cortex.app import app

    # Set up temp AGOUTIC_DATA
    data_dir = tmp_path / "agoutic_data"
    data_dir.mkdir()

    monkeypatch.setattr("cortex.config.AGOUTIC_DATA", data_dir)
    monkeypatch.setattr("cortex.user_jail.AGOUTIC_DATA", data_dir)
    monkeypatch.setattr("cortex.db_helpers._cfg.AGOUTIC_DATA", data_dir)
    monkeypatch.setattr("cortex.routes.cross_project._cfg.AGOUTIC_DATA", data_dir)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SL = sessionmaker(bind=engine)
    session = SL()
    data = _seed_users_and_projects(session, data_dir)
    session.close()

    with _patch_session(SL):
        client = TestClient(app, raise_server_exceptions=False)
        yield engine, SL, data, client, data_dir


# ===========================================================================
# Test: Authorized project filtering happens before scan
# ===========================================================================

class TestAuthorizationFiltering:
    def test_router_registered_and_endpoint_reachable(self, setup):
        """Cross-project router is registered and endpoint is reachable."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get("/cross-project/projects")
        assert resp.status_code == 200

    def test_list_projects_only_returns_authorized(self, setup):
        """User1 should only see their own projects."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get("/cross-project/projects")
        assert resp.status_code == 200

        projects = resp.json()
        project_ids = [p["project_id"] for p in projects]

        # User1 should see project1 but not project2
        assert data["project1_id"] in project_ids
        assert data["project2_id"] not in project_ids

    def test_browse_inaccessible_project_rejected(self, setup):
        """User1 cannot browse User2's project."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        # Try to browse user2's project
        resp = client.get(f"/cross-project/projects/{data['project2_id']}/browse")
        assert resp.status_code == 403

    def test_search_returns_only_authorized_results(self, setup):
        """Search should only return files from authorized projects."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        # Search for a file that exists in user2's project
        resp = client.get("/cross-project/search", params={"q": "fastq"})
        assert resp.status_code == 200

        results = resp.json()
        # Should not find user2's fastq file
        assert results["total_count"] == 0


# ===========================================================================
# Test: Browse accessible project succeeds
# ===========================================================================

class TestBrowsingAccessibleProject:
    def test_browse_root_succeeds(self, setup):
        """User can browse their own project root."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(f"/cross-project/projects/{data['project1_id']}/browse")
        assert resp.status_code == 200

        result = resp.json()
        assert result["project_id"] == data["project1_id"]
        assert len(result["items"]) > 0

    def test_browse_subpath_succeeds(self, setup):
        """User can browse a subdirectory."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"subpath": "data"}
        )
        assert resp.status_code == 200

        result = resp.json()
        names = [item["name"] for item in result["items"]]
        assert "test.bam" in names

    def test_browse_recursive(self, setup):
        """Recursive browse returns all files."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"recursive": "true"}
        )
        assert resp.status_code == 200

        result = resp.json()
        names = [item["name"] for item in result["items"]]
        # Should include files from multiple directories
        assert "test.bam" in names
        assert "results.tsv" in names


# ===========================================================================
# Test: Path traversal and absolute paths are rejected
# ===========================================================================

class TestPathSecurity:
    def test_traversal_path_rejected(self, setup):
        """Paths with .. are rejected."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"subpath": "../../../etc/passwd"}
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_absolute_path_rejected(self, setup):
        """Absolute paths are rejected."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"subpath": "/etc/passwd"}
        )
        assert resp.status_code == 400
        assert "absolute" in resp.json()["detail"].lower()

    def test_null_bytes_rejected(self, setup):
        """Paths with null bytes are rejected."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"subpath": "data\x00/file.txt"}
        )
        assert resp.status_code == 400


# ===========================================================================
# Test: Bounded payload and truncation
# ===========================================================================

class TestTruncation:
    def test_limit_respected(self, setup):
        """Limit parameter is respected."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        # Create many files
        data_dir = data["user1_dir"] / "data"
        for i in range(20):
            (data_dir / f"file{i}.txt").write_text(f"content {i}")

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"subpath": "data", "limit": 5}
        )
        assert resp.status_code == 200

        result = resp.json()
        assert len(result["items"]) <= 5
        assert result["truncated"] is True
        assert result["total_count"] > 5

    def test_truncation_metadata(self, setup):
        """Truncation metadata is accurate."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        # Create files
        data_dir = data["user1_dir"] / "data"
        for i in range(10):
            (data_dir / f"trunctest{i}.txt").write_text(f"content {i}")

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"subpath": "data", "limit": 3}
        )
        assert resp.status_code == 200

        result = resp.json()
        # total_count should reflect actual count
        assert result["total_count"] > len(result["items"])
        assert result["limit"] == 3

    def test_recursive_browse_stops_early_at_limit_by_default(self, setup):
        """Recursive browse should stop scanning at limit unless full scan is requested."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        root = data["user1_dir"] / "deep"
        for i in range(50):
            nested = root / f"lvl{i // 10}" / f"sub{i % 10}"
            nested.mkdir(parents=True, exist_ok=True)
            (nested / f"sample_{i}.txt").write_text("x")

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"recursive": "true", "subpath": "deep", "limit": 5}
        )
        assert resp.status_code == 200

        result = resp.json()
        assert len(result["items"]) == 5
        assert result["total_count"] == 5
        assert result["truncated"] is True

    def test_search_stops_early_at_limit_by_default(self, setup):
        """Search should stop scanning at limit unless full scan is requested."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        data_dir = data["user1_dir"] / "data"
        for i in range(40):
            (data_dir / f"match_{i}.txt").write_text("content")

        resp = client.get(
            "/cross-project/search",
            params={"q": "match_", "limit": 7}
        )
        assert resp.status_code == 200

        result = resp.json()
        assert len(result["items"]) == 7
        assert result["total_count"] == 7
        assert result["truncated"] is True

    def test_search_full_scan_opt_in_keeps_exact_total_count(self, setup):
        """Explicit full scan should keep counting matches after limit."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        data_dir = data["user1_dir"] / "data"
        for i in range(15):
            (data_dir / f"fullscan_{i}.txt").write_text("content")

        resp = client.get(
            "/cross-project/search",
            params={"q": "fullscan_", "limit": 5, "total_count_full_scan": "true"}
        )
        assert resp.status_code == 200

        result = resp.json()
        assert len(result["items"]) == 5
        assert result["total_count"] > 5
        assert result["truncated"] is True


# ===========================================================================
# Test: Responses do not expose absolute paths
# ===========================================================================

class TestNoAbsolutePaths:
    def test_browse_response_no_absolute_paths(self, setup):
        """Browse response should not contain absolute paths."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(f"/cross-project/projects/{data['project1_id']}/browse")
        assert resp.status_code == 200

        result_str = json.dumps(resp.json())
        # Should not contain the tmp path
        assert str(tmp_data) not in result_str
        # relative_path should be relative
        for item in resp.json()["items"]:
            assert not item["relative_path"].startswith("/")

    def test_search_response_no_absolute_paths(self, setup):
        """Search response should not contain absolute paths."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get("/cross-project/search", params={"q": "bam"})
        assert resp.status_code == 200

        result_str = json.dumps(resp.json())
        # Should not contain the tmp path
        assert str(tmp_data) not in result_str


# ===========================================================================
# Test: File type classification
# ===========================================================================

class TestFileClassification:
    def test_file_types_detected(self, setup):
        """File types are correctly classified."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"subpath": "data"}
        )
        assert resp.status_code == 200

        items = resp.json()["items"]
        types_by_name = {item["name"]: item["file_type"] for item in items}

        assert types_by_name.get("test.bam") == "alignment"
        assert types_by_name.get("test.bai") == "alignment_index"

    def test_workflow_folder_detected(self, setup):
        """Workflow folder is correctly detected."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(
            f"/cross-project/projects/{data['project1_id']}/browse",
            params={"recursive": "true"}
        )
        assert resp.status_code == 200

        items = resp.json()["items"]
        workflow_items = [i for i in items if i.get("workflow_folder")]

        # results.tsv is in workflow1
        results_item = next((i for i in items if i["name"] == "results.tsv"), None)
        assert results_item is not None
        assert results_item["workflow_folder"] == "workflow1"


# ===========================================================================
# Test: Existing project-local browsing remains unaffected
# ===========================================================================

class TestExistingBrowsingUnaffected:
    def test_projects_list_endpoint_still_works(self, setup):
        """The existing /projects endpoint still works."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        # The original projects endpoint
        resp = client.get("/projects")
        assert resp.status_code == 200
        assert "projects" in resp.json()

    def test_project_files_endpoint_still_works(self, setup):
        """The existing /projects/{id}/files endpoint still works."""
        engine, SL, data, client, tmp_data = setup
        client.cookies.set("session", data["session1_id"])

        resp = client.get(f"/projects/{data['project1_id']}/files")
        # May return 200 or 404 depending on whether files exist
        assert resp.status_code in (200, 404)
