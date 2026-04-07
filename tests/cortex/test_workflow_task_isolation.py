"""Tests that tasks from superseded workflows are archived and not shown."""

import datetime
import json
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.db_helpers import _create_block_internal
from cortex.models import ProjectBlock, ProjectTask
from cortex.task_service import (
    archive_superseded_tasks,
    build_task_sections,
    sync_project_tasks,
)


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _plan_payload(*, sample_name: str, status: str = "PENDING") -> dict:
    return {
        "title": f"Workflow for {sample_name}",
        "sample_name": sample_name,
        "status": status,
        "workflow_type": "rna",
        "steps": [
            {
                "id": f"step-{sample_name}-1",
                "kind": "LOCATE_DATA",
                "title": f"Locate data for {sample_name}",
                "status": status,
            },
            {
                "id": f"step-{sample_name}-2",
                "kind": "SUBMIT_WORKFLOW",
                "title": f"Run analysis for {sample_name}",
                "status": status,
            },
        ],
    }


# ------------------------------------------------------------------
# Core: only the active workflow's tasks are returned
# ------------------------------------------------------------------

class TestWorkflowTaskIsolation:
    def test_only_active_workflow_tasks_shown(self, session_factory):
        """When two workflows exist (one COMPLETED, one PENDING), sync
        returns only the active (PENDING) workflow's tasks."""
        session = session_factory()
        pid = "proj-isolation-1"

        # Old completed workflow
        old_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="GKO", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )

        # New active workflow
        new_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="EXC", status="PENDING"),
            status="PENDING", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        titles = [t.title for t in tasks]
        sample_names = {
            json.loads(t.metadata_json or "{}").get("sample_name")
            for t in tasks
            if t.metadata_json
        }

        # Active workflow tasks present
        assert any("EXC" in t for t in titles), f"Expected EXC tasks, got: {titles}"
        # Old workflow tasks NOT present
        assert not any("GKO" in t for t in titles), f"GKO tasks should be archived, got: {titles}"

        # Old tasks should have archived_at set
        all_tasks = session.execute(select(ProjectTask).where(ProjectTask.project_id == pid)).scalars().all()
        archived = [t for t in all_tasks if t.archived_at is not None]
        assert len(archived) > 0, "Old workflow tasks should be archived"
        for t in archived:
            assert "GKO" in t.title

        session.close()

    def test_all_completed_shows_most_recent(self, session_factory):
        """If all workflows are completed, the most recent one's tasks
        should still be visible."""
        session = session_factory()
        pid = "proj-isolation-2"

        _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="GKO", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )
        _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="LWF", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        titles = [t.title for t in tasks]

        # Only the most recent (LWF) should be visible
        assert any("LWF" in t for t in titles), f"Expected LWF tasks, got: {titles}"
        assert not any("GKO" in t for t in titles), f"GKO should be archived, got: {titles}"

        session.close()

    def test_approval_gate_skipped_for_old_workflow(self, session_factory):
        """APPROVAL_GATE blocks linked to a superseded workflow plan
        should not produce tasks."""
        session = session_factory()
        pid = "proj-isolation-3"

        old_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="OLD", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )
        # Approval gate linked to old workflow via parent_id
        gate_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id=pid,
            owner_id="u1",
            seq=100,
            type="APPROVAL_GATE",
            status="PENDING",
            payload_json=json.dumps({"label": "Approve old workflow"}),
            parent_id=old_block.id,
            created_at=datetime.datetime.utcnow(),
        )
        session.add(gate_block)
        session.commit()

        new_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="NEW", status="PENDING"),
            status="PENDING", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        titles = [t.title for t in tasks]

        assert not any("Approve old" in t for t in titles), f"Old approval gate should be hidden: {titles}"
        assert any("NEW" in t for t in titles)

        session.close()

    def test_staging_task_skipped_for_old_workflow(self, session_factory):
        """STAGING_TASK blocks with workflow_plan_block_id pointing to
        a superseded workflow should not produce tasks."""
        session = session_factory()
        pid = "proj-isolation-4"

        old_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="OLD", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )
        # Staging task linked to old workflow
        staging_block = _create_block_internal(
            session, pid, "STAGING_TASK",
            {
                "sample_name": "OLD_SAMPLE",
                "mode": "rna",
                "workflow_plan_block_id": old_block.id,
                "progress_percent": 100,
                "status": "DONE",
            },
            status="DONE", owner_id="u1",
        )

        new_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="NEW", status="PENDING"),
            status="PENDING", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        titles = [t.title for t in tasks]

        assert not any("OLD_SAMPLE" in t for t in titles), f"Old staging task should be hidden: {titles}"
        assert any("NEW" in t for t in titles)

        session.close()

    def test_execution_job_skipped_for_old_workflow(self, session_factory):
        """EXECUTION_JOB blocks with workflow_plan_block_id pointing to
        a superseded workflow should not produce tasks."""
        session = session_factory()
        pid = "proj-isolation-5"

        old_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="OLD", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )
        # Execution job linked to old workflow
        _create_block_internal(
            session, pid, "EXECUTION_JOB",
            {
                "sample_name": "OLD_RUN",
                "mode": "rna",
                "run_uuid": str(uuid.uuid4()),
                "workflow_plan_block_id": old_block.id,
            },
            status="DONE", owner_id="u1",
        )

        new_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="NEW", status="PENDING"),
            status="PENDING", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        titles = [t.title for t in tasks]

        assert not any("OLD_RUN" in t for t in titles), f"Old execution job should be hidden: {titles}"
        assert any("NEW" in t for t in titles)

        session.close()

    def test_standalone_execution_job_always_shown(self, session_factory):
        """EXECUTION_JOB blocks without workflow_plan_block_id should
        always be visible regardless of workflow state."""
        session = session_factory()
        pid = "proj-isolation-6"

        _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="ACTIVE", status="PENDING"),
            status="PENDING", owner_id="u1",
        )

        # Standalone job (no workflow_plan_block_id)
        _create_block_internal(
            session, pid, "EXECUTION_JOB",
            {
                "sample_name": "STANDALONE",
                "mode": "rna",
                "run_uuid": str(uuid.uuid4()),
            },
            status="RUNNING", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        titles = [t.title for t in tasks]

        assert any("STANDALONE" in t for t in titles), f"Standalone job should be visible: {titles}"
        assert any("ACTIVE" in t for t in titles)

        session.close()

    def test_download_task_always_shown(self, session_factory):
        """DOWNLOAD_TASK blocks are standalone and should always show."""
        session = session_factory()
        pid = "proj-isolation-7"

        _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="ACTIVE", status="PENDING"),
            status="PENDING", owner_id="u1",
        )

        _create_block_internal(
            session, pid, "DOWNLOAD_TASK",
            {
                "download_id": str(uuid.uuid4()),
                "files": [{"filename": "test.bam", "url": "http://example.com/test.bam"}],
                "total_files": 1,
                "downloaded": 0,
                "status": "RUNNING",
            },
            status="RUNNING", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        kinds = {t.kind for t in tasks}

        assert "download" in kinds, f"Download task should be visible: {kinds}"

        session.close()

    def test_no_workflows_all_blocks_shown(self, session_factory):
        """When there are no WORKFLOW_PLAN blocks, all blocks should
        produce tasks normally."""
        session = session_factory()
        pid = "proj-isolation-8"

        _create_block_internal(
            session, pid, "EXECUTION_JOB",
            {
                "sample_name": "SOLO",
                "mode": "rna",
                "run_uuid": str(uuid.uuid4()),
            },
            status="RUNNING", owner_id="u1",
        )

        tasks = sync_project_tasks(session, pid)
        titles = [t.title for t in tasks]

        assert any("SOLO" in t for t in titles), f"Standalone job should be visible: {titles}"

        session.close()


# ------------------------------------------------------------------
# Proactive archive helper
# ------------------------------------------------------------------

class TestArchiveSupersededTasks:
    def test_archive_clears_existing_tasks(self, session_factory):
        """archive_superseded_tasks sets archived_at on all non-archived tasks."""
        session = session_factory()
        pid = "proj-archive-1"

        # Create a workflow and sync to produce tasks
        _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="OLD", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )
        tasks_before = sync_project_tasks(session, pid)
        assert len(tasks_before) > 0

        # Archive all tasks
        count = archive_superseded_tasks(session, pid)
        assert count == len(tasks_before)

        # All tasks should now be archived
        remaining = session.execute(
            select(ProjectTask)
            .where(ProjectTask.project_id == pid)
            .where(ProjectTask.archived_at.is_(None))
        ).scalars().all()
        assert len(remaining) == 0

        session.close()

    def test_archive_idempotent(self, session_factory):
        """Calling archive_superseded_tasks twice doesn't fail."""
        session = session_factory()
        pid = "proj-archive-2"

        _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="X", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )
        sync_project_tasks(session, pid)

        count1 = archive_superseded_tasks(session, pid)
        count2 = archive_superseded_tasks(session, pid)

        assert count1 > 0
        assert count2 == 0  # No non-archived tasks left

        session.close()


# ------------------------------------------------------------------
# End-to-end: new workflow creation archives old tasks
# ------------------------------------------------------------------

class TestNewWorkflowCleansOld:
    def test_new_workflow_replaces_old_tasks(self, session_factory):
        """Simulates the plan_detection flow: create old workflow, sync,
        then create new workflow, archive, sync — only new tasks remain."""
        session = session_factory()
        pid = "proj-e2e-1"

        # Step 1: Create and sync old workflow
        old_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="GKO", status="COMPLETED"),
            status="DONE", owner_id="u1",
        )
        old_tasks = sync_project_tasks(session, pid)
        assert any("GKO" in t.title for t in old_tasks)

        # Step 2: Create new workflow, archive old tasks, then sync
        # (mirrors what plan_detection.py now does)
        new_block = _create_block_internal(
            session, pid, "WORKFLOW_PLAN",
            _plan_payload(sample_name="EXC", status="PENDING"),
            status="PENDING", owner_id="u1",
        )
        archive_superseded_tasks(session, pid)
        new_tasks = sync_project_tasks(session, pid)

        titles = [t.title for t in new_tasks]
        assert any("EXC" in t for t in titles), f"Expected EXC tasks: {titles}"
        assert not any("GKO" in t for t in titles), f"GKO should not appear: {titles}"

        session.close()
