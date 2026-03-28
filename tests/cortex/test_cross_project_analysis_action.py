"""
Tests for cross-project staging/analysis action endpoints.

Tests:
1. stage endpoint revalidates every selected file server-side
2. source files are not modified
3. staging uses deterministic layout:
   - selected/
   - manifest.json
   - by_project/<project_name>/...
4. manifest.json contains all required fields:
   - action_type
   - created_at
   - destination_project_id
   - destination_project_name
   - staging_mode
   - selected_files with required provenance fields
5. mixed unauthorized file selection is rejected
6. traversal/path escape in selected files is rejected
7. explicit destination project is required
8. create-new-destination path works
9. analyze_together / compare_together incompatible selections are rejected
10. no auto-analysis is triggered by selection or stage request alone
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


def test_cross_project_schemas_import_cleanly():
    """Cross-project schemas are importable for route wiring."""
    from cortex.schemas import (  # noqa: F401
        CrossProjectProjectOut,
        CrossProjectFileOut,
        CrossProjectBrowseResponse,
        CrossProjectSearchResponse,
        SelectedFileInput,
        StageRequest,
        StageResponse,
        StageStatusResponse,
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


def _seed_for_staging(session, tmp_agoutic_data):
    """Create user with two projects - one source and one destination."""
    uid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    src_pid = str(uuid.uuid4())
    dest_pid = str(uuid.uuid4())

    user = User(id=uid, email="stager@test.com", username="stager", is_active=True)
    session.add(user)
    session.flush()

    sess = SessionModel(id=sid, user_id=uid, expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1))
    session.add(sess)

    src_proj = Project(id=src_pid, name="Source Project", slug="source-project", owner_id=uid, created_at=datetime.datetime.utcnow())
    dest_proj = Project(id=dest_pid, name="Destination Project", slug="dest-project", owner_id=uid, created_at=datetime.datetime.utcnow())
    session.add_all([src_proj, dest_proj])

    access_src = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=uid,
        project_id=src_pid,
        project_name="Source Project",
        role="owner",
    )
    access_dest = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=uid,
        project_id=dest_pid,
        project_name="Destination Project",
        role="owner",
    )
    session.add_all([access_src, access_dest])
    session.commit()

    # Create filesystem directories for source project
    src_dir = tmp_agoutic_data / "users" / "stager" / "source-project"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "data").mkdir(exist_ok=True)

    # Create test files
    test_files = {
        "data/sample1.bam": "bam content 1",
        "data/sample2.bam": "bam content 2",
        "data/sample1.bai": "bai content",
        "workflow1/results.tsv": "col1\tcol2\nval1\tval2\n",
        "data/mixed.fastq": "fastq content",
    }
    for rel_path, content in test_files.items():
        file_path = src_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    # Create destination directory
    dest_dir = tmp_agoutic_data / "users" / "stager" / "dest-project"
    dest_dir.mkdir(parents=True, exist_ok=True)

    return {
        "user_id": uid,
        "session_id": sid,
        "source_project_id": src_pid,
        "dest_project_id": dest_pid,
        "src_dir": src_dir,
        "dest_dir": dest_dir,
        "test_files": test_files,
    }


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """Yield (engine, SessionLocal factory, seed data, TestClient, tmp_agoutic_data)."""
    from fastapi.testclient import TestClient
    from cortex.app import app

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
    data = _seed_for_staging(session, data_dir)
    session.close()

    with _patch_session(SL):
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("session", data["session_id"])
        yield engine, SL, data, client, data_dir


# ===========================================================================
# Test: Stage endpoint revalidates every selected file server-side
# ===========================================================================

class TestServerSideRevalidation:
    def test_nonexistent_file_rejected(self, setup):
        """Staging a file that doesn't exist is rejected."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/nonexistent.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_directory_selection_rejected(self, setup):
        """Selecting a directory (not a file) is rejected."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 400
        assert "director" in resp.json()["detail"].lower()


# ===========================================================================
# Test: Source files are not modified
# ===========================================================================

class TestSourceFilesUnmodified:
    def test_source_files_remain_unchanged(self, setup):
        """Source files should not be modified after staging."""
        engine, SL, data, client, tmp_data = setup

        # Get original content
        src_file = data["src_dir"] / "data" / "sample1.bam"
        original_content = src_file.read_text()
        original_mtime = src_file.stat().st_mtime

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        # Verify source file unchanged
        assert src_file.read_text() == original_content
        assert src_file.stat().st_mtime == original_mtime


# ===========================================================================
# Test: Staging uses deterministic layout
# ===========================================================================

class TestStagingLayout:
    def test_staging_creates_selected_directory(self, setup):
        """Staging creates selected/ directory."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        # Check layout
        selected_dir = data["dest_dir"] / "selected"
        assert selected_dir.exists()
        assert (selected_dir / "manifest.json").exists()
        assert (selected_dir / "by_project").exists()

    def test_staging_creates_by_project_subdirs(self, setup):
        """Staging creates by_project/<project_name>/ structure."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        # Check by_project structure
        by_project = data["dest_dir"] / "selected" / "by_project"
        assert by_project.exists()

        # Should have at least one project directory
        subdirs = list(by_project.iterdir())
        assert len(subdirs) > 0

    def test_staging_creates_symlinks(self, setup):
        """Staged files are symlinks pointing to originals."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        # Find the staged file
        by_project = data["dest_dir"] / "selected" / "by_project"
        staged_files = list(by_project.rglob("sample1.bam"))
        assert len(staged_files) == 1

        staged_file = staged_files[0]
        assert staged_file.is_symlink()

        # Symlink should point to original
        resolved = staged_file.resolve()
        assert resolved == (data["src_dir"] / "data" / "sample1.bam").resolve()


# ===========================================================================
# Test: Manifest contains all required fields
# ===========================================================================

class TestManifestFields:
    def test_manifest_has_required_fields(self, setup):
        """Manifest.json contains all required fields."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        # Read manifest
        manifest_path = data["dest_dir"] / "selected" / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        # Required top-level fields
        assert "action_type" in manifest
        assert "created_at" in manifest
        assert "destination_project_id" in manifest
        assert "destination_project_name" in manifest
        assert "staging_mode" in manifest
        assert "selected_files" in manifest

        assert manifest["action_type"] == "stage_workspace"
        assert manifest["staging_mode"] == "symlink"
        assert manifest["destination_project_id"] == data["dest_project_id"]

    def test_manifest_selected_files_have_required_fields(self, setup):
        """Each entry in selected_files has required provenance fields."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        # Read manifest
        manifest_path = data["dest_dir"] / "selected" / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert len(manifest["selected_files"]) == 1
        entry = manifest["selected_files"][0]

        # Required per-file fields
        assert "source_project_id" in entry
        assert "source_project_name" in entry
        assert "relative_path" in entry
        assert "category" in entry
        assert "file_type" in entry
        assert "size" in entry
        assert "modified_time" in entry


# ===========================================================================
# Test: Mixed unauthorized file selection is rejected
# ===========================================================================

class TestUnauthorizedRejection:
    def test_unauthorized_source_project_rejected(self, setup):
        """Cannot stage files from a project you don't have access to."""
        engine, SL, data, client, tmp_data = setup

        # Create another user's project
        s = SL()
        other_uid = str(uuid.uuid4())
        other_pid = str(uuid.uuid4())
        other_user = User(id=other_uid, email="other@test.com", username="other", is_active=True)
        s.add(other_user)
        other_proj = Project(id=other_pid, name="Other Project", slug="other-project", owner_id=other_uid, created_at=datetime.datetime.utcnow())
        s.add(other_proj)
        s.commit()
        s.close()

        payload = {
            "selected_files": [
                {"source_project_id": other_pid, "relative_path": "data/file.txt"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 403


# ===========================================================================
# Test: Traversal/path escape in selected files is rejected
# ===========================================================================

class TestPathEscapeRejection:
    def test_traversal_in_selection_rejected(self, setup):
        """Path traversal in selected file path is rejected."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "../../../etc/passwd"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_absolute_path_in_selection_rejected(self, setup):
        """Absolute path in selected file path is rejected."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "/etc/passwd"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 400


# ===========================================================================
# Test: Explicit destination project is required
# ===========================================================================

class TestDestinationRequired:
    def test_no_destination_rejected(self, setup):
        """Must specify either destination_project_id or destination_project_name."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            # No destination specified
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 400
        assert "destination" in resp.json()["detail"].lower()


# ===========================================================================
# Test: Create new destination path works
# ===========================================================================

class TestCreateNewDestination:
    def test_create_new_destination_project(self, setup):
        """Can create a new destination project during staging."""
        engine, SL, data, client, tmp_data = setup

        new_project_name = "My New Cross-Project Workspace"

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_name": new_project_name,
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        result = resp.json()
        assert result["destination_project_name"] == new_project_name
        assert result["destination_project_id"] is not None

        # Verify project was created in database
        s = SL()
        from cortex.models import Project
        new_proj = s.execute(
            __import__('sqlalchemy').select(Project).where(Project.id == result["destination_project_id"])
        ).scalar_one_or_none()
        assert new_proj is not None
        assert new_proj.name == new_project_name
        s.close()


# ===========================================================================
# Test: Incompatible selections are rejected for analyze/compare
# ===========================================================================

class TestActionCompatibility:
    def test_compare_mixed_types_rejected(self, setup):
        """Cannot compare files of different types."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"},
                {"source_project_id": data["source_project_id"], "relative_path": "data/mixed.fastq"},
            ],
            "action_type": "compare_together",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 422
        detail = resp.json()["detail"].lower()
        assert "compare" in detail or "mixed" in detail

    def test_compare_same_type_succeeds(self, setup):
        """Can compare files of the same type."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"},
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample2.bam"},
            ],
            "action_type": "compare_together",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200


# ===========================================================================
# Test: No auto-analysis triggered
# ===========================================================================

class TestNoAutoAnalysis:
    def test_stage_does_not_trigger_analysis(self, setup):
        """Staging does not automatically run any analysis."""
        engine, SL, data, client, tmp_data = setup

        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "analyze_together",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200

        result = resp.json()
        # Action type is recorded but no analysis is triggered
        assert result["action_type"] == "analyze_together"

        # Read manifest to verify action is recorded but not executed
        manifest_path = data["dest_dir"] / "selected" / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["action_type"] == "analyze_together"
        # No additional analysis artifacts should be created
        # The selected/ directory should only contain manifest and by_project
        selected_contents = list((data["dest_dir"] / "selected").iterdir())
        content_names = {c.name for c in selected_contents}
        assert content_names == {"manifest.json", "by_project"}


# ===========================================================================
# Test: Stage status endpoint
# ===========================================================================

class TestStageStatus:
    def test_get_stage_status(self, setup):
        """Can retrieve status of a staged workspace."""
        engine, SL, data, client, tmp_data = setup

        # First stage something
        payload = {
            "selected_files": [
                {"source_project_id": data["source_project_id"], "relative_path": "data/sample1.bam"}
            ],
            "action_type": "stage_workspace",
            "destination_project_id": data["dest_project_id"],
        }

        resp = client.post("/cross-project/stage", json=payload)
        assert resp.status_code == 200
        stage_id = resp.json()["stage_id"]

        # Now get status
        status_resp = client.get(f"/cross-project/stage/{stage_id}")
        assert status_resp.status_code == 200

        status = status_resp.json()
        assert status["stage_id"] == stage_id
        assert status["destination_project_id"] == data["dest_project_id"]
        assert status["item_count"] == 1

    def test_nonexistent_stage_not_found(self, setup):
        """Getting status of nonexistent stage returns 404."""
        engine, SL, data, client, tmp_data = setup

        resp = client.get("/cross-project/stage/nonexistent-id")
        assert resp.status_code == 404
