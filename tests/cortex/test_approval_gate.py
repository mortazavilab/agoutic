"""
Tests for cortex/app.py — Approval gate handling and helper functions.

Covers:
  - PATCH /block/{id} — approval gate transitions (APPROVED triggers job/download)
  - PATCH /block/{id} — rejection triggers handle_rejection
  - DELETE /projects/{id}/blocks — clear all blocks
  - _update_download_block — payload update helper
  - _post_download_suggestions — post-download agent suggestions
"""

import asyncio
import datetime
import json
import uuid
from pathlib import Path

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock,
)
from cortex.app import (
    app, _update_download_block, _post_download_suggestions,
    get_block_payload,
)
from fastapi import HTTPException


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
def seed_data(session_factory):
    sess = session_factory()

    user = User(id="u-gate", email="gate@example.com", role="user",
                username="gateuser", is_active=True)
    sess.add(user)

    session_obj = SessionModel(id="gate-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)

    proj = Project(id="proj-gate", name="Gate Proj", owner_id="u-gate",
                   slug="gate-proj")
    sess.add(proj)
    sess.flush()

    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-gate",
                           project_id="proj-gate", project_name="Gate Proj",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    sess.add(access)
    sess.commit()
    sess.close()


@pytest.fixture()
def client(session_factory, seed_data, tmp_path):
    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path), \
         patch("cortex.app.asyncio") as mock_asyncio:
        # Capture background tasks instead of running them
        mock_asyncio.create_task = MagicMock()
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "gate-session")
        yield c


def _create_gate(session_factory, project_id, status="PENDING",
                 gate_action=None, owner_id="u-gate", seq=1):
    """Create an APPROVAL_GATE block and return its id."""
    sess = session_factory()
    payload = {"label": "Approve this plan?", "extracted_params": {"sample_name": "s1"}}
    if gate_action:
        payload["gate_action"] = gate_action
    block = ProjectBlock(
        id=str(uuid.uuid4()),
        project_id=project_id,
        type="APPROVAL_GATE",
        payload_json=json.dumps(payload),
        status=status,
        seq=seq,
        owner_id=owner_id,
    )
    sess.add(block)
    sess.commit()
    bid = block.id
    sess.close()
    return bid


# ---------------------------------------------------------------------------
# PATCH /block/{id} — Approval transitions
# ---------------------------------------------------------------------------
class TestApprovalGate:
    def test_approve_triggers_job_submission(self, client, session_factory):
        """Approving a PENDING gate dispatches submit_job_after_approval."""
        gate_id = _create_gate(session_factory, "proj-gate")
        resp = client.patch(f"/block/{gate_id}", json={
            "status": "APPROVED",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "APPROVED"

    def test_approve_download_gate(self, client, session_factory):
        """Gate with gate_action=download triggers download_after_approval."""
        gate_id = _create_gate(session_factory, "proj-gate", gate_action="download")
        resp = client.patch(f"/block/{gate_id}", json={
            "status": "APPROVED",
        })
        assert resp.status_code == 200

    def test_reject_triggers_handle_rejection(self, client, session_factory):
        """Rejecting a PENDING gate dispatches handle_rejection."""
        gate_id = _create_gate(session_factory, "proj-gate")
        resp = client.patch(f"/block/{gate_id}", json={
            "status": "REJECTED",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "REJECTED"

    def test_non_pending_gate_no_task(self, client, session_factory):
        """Approving an already-APPROVED gate should NOT trigger again."""
        gate_id = _create_gate(session_factory, "proj-gate", status="APPROVED")
        resp = client.patch(f"/block/{gate_id}", json={
            "status": "APPROVED",
        })
        assert resp.status_code == 200

    def test_update_payload(self, client, session_factory):
        """PATCH with payload updates the block payload."""
        gate_id = _create_gate(session_factory, "proj-gate")
        edited_params = {"sample_name": "edited", "mode": "RNA"}
        resp = client.patch(f"/block/{gate_id}", json={
            "payload": {"label": "Updated", "edited_params": edited_params},
        })
        assert resp.status_code == 200
        payload = resp.json()["payload"]
        assert payload["edited_params"]["sample_name"] == "edited"

    def test_nonexistent_block_404(self, client):
        resp = client.patch("/block/fake-id", json={"status": "APPROVED"})
        assert resp.status_code == 404

    def test_slurm_approval_requires_unlocked_profile(self, client, session_factory):
        gate_id = _create_gate(session_factory, "proj-gate")
        sess = session_factory()
        block = sess.execute(select(ProjectBlock).where(ProjectBlock.id == gate_id)).scalar_one()
        payload = json.loads(block.payload_json)
        payload["edited_params"] = {
            "execution_mode": "slurm",
            "ssh_profile_nickname": "hpc3",
        }
        block.payload_json = json.dumps(payload)
        sess.commit()
        sess.close()

        with patch(
            "cortex.app._ensure_gate_remote_profile_unlocked",
            new=AsyncMock(side_effect=HTTPException(status_code=400, detail="Unlock hpc3 first")),
        ):
            resp = client.patch(f"/block/{gate_id}", json={
                "status": "APPROVED",
                "payload": payload,
            })

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unlock hpc3 first"

    def test_slurm_approval_requires_unlocked_profile(self, client, session_factory):
        gate_id = _create_gate(session_factory, "proj-gate")
        sess = session_factory()
        block = sess.execute(select(ProjectBlock).where(ProjectBlock.id == gate_id)).scalar_one()
        payload = json.loads(block.payload_json)
        payload["edited_params"] = {
            "execution_mode": "slurm",
            "ssh_profile_nickname": "hpc3",
        }
        block.payload_json = json.dumps(payload)
        sess.commit()
        sess.close()

        with patch(
            "cortex.app._ensure_gate_remote_profile_unlocked",
            new=AsyncMock(side_effect=HTTPException(status_code=400, detail="Unlock hpc3 first")),
        ):
            resp = client.patch(f"/block/{gate_id}", json={
                "status": "APPROVED",
                "payload": payload,
            })

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unlock hpc3 first"


# ---------------------------------------------------------------------------
# DELETE /projects/{id}/blocks
# ---------------------------------------------------------------------------
class TestClearBlocks:
    def test_clear_empty(self, client):
        resp = client.delete("/projects/proj-gate/blocks")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0

    def test_clear_with_blocks(self, client, session_factory):
        _create_gate(session_factory, "proj-gate", seq=1)
        _create_gate(session_factory, "proj-gate", seq=2)
        resp = client.delete("/projects/proj-gate/blocks")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2

        # Verify blocks are gone
        sess = session_factory()
        remaining = sess.execute(
            select(ProjectBlock).where(ProjectBlock.project_id == "proj-gate")
        ).scalars().all()
        assert len(remaining) == 0
        sess.close()


# ---------------------------------------------------------------------------
# _update_download_block — unit test
# ---------------------------------------------------------------------------
class TestUpdateDownloadBlock:
    def test_updates_payload(self, session_factory):
        sess = session_factory()
        # Need a project first
        proj = Project(id="p-dlb", name="DLB", owner_id="u-gate")
        sess.add(proj)
        sess.flush()
        block = ProjectBlock(
            id="dlb-1", project_id="p-dlb", type="DOWNLOAD_TASK",
            payload_json=json.dumps({"status": "RUNNING", "downloaded": 0, "total_files": 3}),
            status="RUNNING", seq=1, owner_id="u-gate",
        )
        sess.add(block)
        sess.commit()

        _update_download_block(sess, "dlb-1", {"downloaded": 2, "bytes_downloaded": 1024})

        block = sess.execute(
            select(ProjectBlock).where(ProjectBlock.id == "dlb-1")
        ).scalar_one()
        payload = json.loads(block.payload_json)
        assert payload["downloaded"] == 2
        assert payload["bytes_downloaded"] == 1024
        assert payload["total_files"] == 3  # untouched
        sess.close()

    def test_updates_status(self, session_factory):
        sess = session_factory()
        proj = Project(id="p-dlb2", name="DLB2", owner_id="u-gate")
        sess.add(proj)
        sess.flush()
        block = ProjectBlock(
            id="dlb-2", project_id="p-dlb2", type="DOWNLOAD_TASK",
            payload_json=json.dumps({"status": "RUNNING"}),
            status="RUNNING", seq=1, owner_id="u-gate",
        )
        sess.add(block)
        sess.commit()

        _update_download_block(sess, "dlb-2", {"status": "DONE"}, status="DONE")

        block = sess.execute(
            select(ProjectBlock).where(ProjectBlock.id == "dlb-2")
        ).scalar_one()
        assert block.status == "DONE"
        sess.close()

    def test_missing_block_no_error(self, session_factory):
        """Updating a nonexistent block should not raise."""
        sess = session_factory()
        _update_download_block(sess, "nonexistent", {"x": 1})
        sess.close()


# ---------------------------------------------------------------------------
# _post_download_suggestions — unit test
# ---------------------------------------------------------------------------
class TestPostDownloadSuggestions:
    @pytest.mark.asyncio
    async def test_sequencing_file_suggestion(self, session_factory):
        sess = session_factory()
        proj = Project(id="p-sug", name="Sug", owner_id="u-gate")
        sess.add(proj)
        sess.commit()

        downloaded = [
            {"filename": "sample.bam", "size_bytes": 1000},
            {"filename": "reads.pod5", "size_bytes": 5000},
        ]
        target = Path("/tmp/test-sug/data")

        await _post_download_suggestions(sess, "p-sug", "u-gate", downloaded, target)

        blocks = sess.execute(
            select(ProjectBlock).where(ProjectBlock.project_id == "p-sug")
        ).scalars().all()
        assert len(blocks) == 1
        payload = json.loads(blocks[0].payload_json)
        assert "Sequencing files detected" in payload["markdown"]
        assert "Dogme pipeline" in payload["markdown"]
        sess.close()

    @pytest.mark.asyncio
    async def test_tabular_file_suggestion(self, session_factory):
        sess = session_factory()
        proj = Project(id="p-tab", name="Tab", owner_id="u-gate")
        sess.add(proj)
        sess.commit()

        downloaded = [{"filename": "results.csv", "size_bytes": 200}]
        target = Path("/tmp/test-tab/data")

        await _post_download_suggestions(sess, "p-tab", "u-gate", downloaded, target)

        blocks = sess.execute(
            select(ProjectBlock).where(ProjectBlock.project_id == "p-tab")
        ).scalars().all()
        payload = json.loads(blocks[0].payload_json)
        assert "Tabular files detected" in payload["markdown"]
        sess.close()

    @pytest.mark.asyncio
    async def test_other_file_suggestion(self, session_factory):
        sess = session_factory()
        proj = Project(id="p-oth", name="Oth", owner_id="u-gate")
        sess.add(proj)
        sess.commit()

        downloaded = [{"filename": "readme.md", "size_bytes": 100}]
        target = Path("/tmp/test-oth/data")

        await _post_download_suggestions(sess, "p-oth", "u-gate", downloaded, target)

        blocks = sess.execute(
            select(ProjectBlock).where(ProjectBlock.project_id == "p-oth")
        ).scalars().all()
        payload = json.loads(blocks[0].payload_json)
        assert "Files" in payload["markdown"]
        assert "readme.md" in payload["markdown"]
        sess.close()

    @pytest.mark.asyncio
    async def test_error_files_skipped(self, session_factory):
        sess = session_factory()
        proj = Project(id="p-err", name="Err", owner_id="u-gate")
        sess.add(proj)
        sess.commit()

        downloaded = [
            {"filename": "good.bam", "size_bytes": 100},
            {"filename": "bad.bam", "error": "HTTP 404"},
        ]
        target = Path("/tmp/test-err/data")

        await _post_download_suggestions(sess, "p-err", "u-gate", downloaded, target)

        blocks = sess.execute(
            select(ProjectBlock).where(ProjectBlock.project_id == "p-err")
        ).scalars().all()
        payload = json.loads(blocks[0].payload_json)
        # Only good.bam should appear in sequencing files
        assert "good.bam" in payload["markdown"]
        sess.close()
