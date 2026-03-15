"""
Tests for cortex/app.py — Project CRUD endpoints.

Uses TestClient with in-memory SQLite and auth bypass to test:
  - POST /projects
  - GET /projects
  - PATCH /projects/{id}
  - DELETE /projects/{id}
  - GET /user/projects
  - POST /block, GET /blocks, PATCH /block/{id}, DELETE /blocks
"""

import uuid
import json
import datetime

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import User, Session as SessionModel, Project, ProjectAccess, ProjectBlock
from cortex.app import app


# ---------------------------------------------------------------------------
# Fixtures — isolated in-memory DB per test
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
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture()
def seed_user(test_session_factory):
    """Create a user and a valid session in the DB."""
    Session = test_session_factory
    session = Session()
    user = User(
        id="test-uid",
        email="test@example.com",
        role="user",
        username="testuser",
        is_active=True,
    )
    session.add(user)
    sess = SessionModel(
        id="test-session-token",
        user_id=user.id,
        is_valid=True,
        expires_at=datetime.datetime(2099, 1, 1),
    )
    session.add(sess)
    session.commit()
    session.close()
    return user


@pytest.fixture()
def client(test_session_factory, seed_user, tmp_path, monkeypatch):
    """
    TestClient with patched DB and AGOUTIC_DATA.
    Auth middleware reads from our in-memory DB.
    """
    # Patch SessionLocal everywhere it's imported
    with patch("cortex.db.SessionLocal", test_session_factory), \
         patch("cortex.app.SessionLocal", test_session_factory), \
         patch("cortex.dependencies.SessionLocal", test_session_factory), \
         patch("cortex.middleware.SessionLocal", test_session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "test-session-token")
        yield c


# ---------------------------------------------------------------------------
# Health check (no auth)
# ---------------------------------------------------------------------------
class TestHealthCheck:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /projects — create project
# ---------------------------------------------------------------------------
class TestCreateProject:
    def test_create_project(self, client):
        resp = client.post("/projects", json={"name": "My ENCODE Project"})
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["name"] == "My ENCODE Project"
        assert data["slug"] is not None

    def test_create_project_generates_slug(self, client):
        resp = client.post("/projects", json={"name": "Fancy Experiment #1"})
        data = resp.json()
        # Slug should be lowercase, hyphenated
        assert data["slug"] == data["slug"].lower()
        assert " " not in data["slug"]

    def test_create_project_no_auth(self, test_session_factory, tmp_path):
        """Without session cookie, should get 401."""
        with patch("cortex.db.SessionLocal", test_session_factory), \
             patch("cortex.app.SessionLocal", test_session_factory), \
             patch("cortex.middleware.SessionLocal", test_session_factory), \
             patch("cortex.config.AGOUTIC_DATA", tmp_path), \
             patch("cortex.user_jail.AGOUTIC_DATA", tmp_path):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post("/projects", json={"name": "Test"})
            assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /projects — list projects
# ---------------------------------------------------------------------------
class TestListProjects:
    def test_list_initially_empty(self, client):
        resp = client.get("/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert isinstance(data["projects"], list)

    def test_list_after_create(self, client):
        client.post("/projects", json={"name": "Project A"})
        client.post("/projects", json={"name": "Project B"})
        resp = client.get("/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["projects"]) >= 2


# ---------------------------------------------------------------------------
# PATCH /projects/{id} — update project
# ---------------------------------------------------------------------------
class TestUpdateProject:
    def test_rename_project(self, client):
        create_resp = client.post("/projects", json={"name": "Old Name"})
        project_id = create_resp.json()["id"]
        update_resp = client.patch(
            f"/projects/{project_id}",
            json={"name": "New Name"},
        )
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data["status"] == "ok"
        assert data["id"] == project_id
        # Slug should reflect the new name
        assert "new-name" in data["slug"]


# ---------------------------------------------------------------------------
# DELETE /projects/{id} — soft-delete (archive)
# ---------------------------------------------------------------------------
class TestDeleteProject:
    def test_soft_delete(self, client):
        create_resp = client.post("/projects", json={"name": "To Delete"})
        project_id = create_resp.json()["id"]
        del_resp = client.delete(f"/projects/{project_id}")
        assert del_resp.status_code == 200


# ---------------------------------------------------------------------------
# Block endpoints: POST /block, GET /blocks, PATCH /block/{id}
# ---------------------------------------------------------------------------
class TestBlockEndpoints:
    def _create_project(self, client):
        resp = client.post("/projects", json={"name": "Block Test"})
        return resp.json()["id"]

    def test_create_and_get_blocks(self, client):
        project_id = self._create_project(client)
        # Create a block
        create_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "USER_MESSAGE",
            "payload": {"markdown": "Hello"},
            "status": "DONE",
        })
        assert create_resp.status_code == 200
        block_id = create_resp.json()["id"]

        # Get blocks
        get_resp = client.get(f"/blocks?project_id={project_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert "blocks" in data
        assert len(data["blocks"]) >= 1

    def test_update_block(self, client):
        project_id = self._create_project(client)
        create_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "AGENT_PLAN",
            "payload": {"markdown": "Original"},
            "status": "NEW",
        })
        block_id = create_resp.json()["id"]

        # Update status
        update_resp = client.patch(f"/block/{block_id}", json={
            "status": "DONE",
            "payload": {"markdown": "Updated"},
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["status"] == "DONE"

    def test_clear_project_blocks(self, client):
        project_id = self._create_project(client)
        client.post("/block", json={
            "project_id": project_id,
            "type": "USER_MESSAGE",
            "payload": {"markdown": "msg"},
            "status": "DONE",
        })
        del_resp = client.delete(f"/projects/{project_id}/blocks")
        assert del_resp.status_code == 200

        # Verify empty
        get_resp = client.get(f"/blocks?project_id={project_id}")
        assert len(get_resp.json()["blocks"]) == 0


class TestProjectTasks:
    def _create_project(self, client):
        resp = client.post("/projects", json={"name": "Task Test"})
        return resp.json()["id"]

    def test_tasks_include_pending_approval(self, client):
        project_id = self._create_project(client)
        create_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve workflow"},
            "status": "PENDING",
        })
        assert create_resp.status_code == 200

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]
        assert len(sections["pending"]) == 1
        assert sections["pending"][0]["kind"] == "approval"
        assert sections["pending"][0]["title"] == "Approve workflow"

    def test_tasks_include_run_analysis_and_follow_up(self, client):
        project_id = self._create_project(client)

        run_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-123",
                "sample_name": "sample-a",
                "mode": "DNA",
                "job_status": {"progress_percent": 100},
            },
            "status": "DONE",
        })
        assert run_resp.status_code == 200

        analysis_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "AGENT_PLAN",
            "payload": {
                "skill": "analyze_job_results",
                "markdown": "### 📊 Analysis: sample-a\n\nAnalysis complete.",
            },
            "status": "DONE",
        })
        assert analysis_resp.status_code == 200

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]

        completed_tasks = sections["completed"]
        follow_up_tasks = sections["follow_up"]
        completed_kinds = {task["kind"] for task in completed_tasks}
        follow_up_kinds = {task["kind"] for task in follow_up_tasks}

        assert "run" in completed_kinds
        assert "analysis" in completed_kinds
        assert "result_review" in follow_up_kinds

        run_task = next(task for task in completed_tasks if task["kind"] == "run")
        child_kinds = {task["kind"] for task in run_task["children"]}
        assert "analysis" in child_kinds
        assert "result_review" in child_kinds

        review_task = next(task for task in follow_up_tasks if task["kind"] == "result_review")
        assert review_task["parent_task_id"] == run_task["id"]

    def test_task_action_complete_and_reopen(self, client):
        project_id = self._create_project(client)

        client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-456",
                "sample_name": "sample-b",
                "mode": "DNA",
                "job_status": {"progress_percent": 100},
            },
            "status": "DONE",
        })

        client.post("/block", json={
            "project_id": project_id,
            "type": "AGENT_PLAN",
            "payload": {
                "skill": "analyze_job_results",
                "markdown": "### 📊 Analysis: sample-b\n\nAnalysis complete.",
            },
            "status": "DONE",
        })

        initial = client.get(f"/projects/{project_id}/tasks")
        review_task = next(
            task for task in initial.json()["sections"]["follow_up"]
            if task["kind"] == "result_review"
        )

        complete_resp = client.patch(
            f"/projects/{project_id}/tasks/{review_task['id']}",
            json={"action": "complete"},
        )
        assert complete_resp.status_code == 200
        assert complete_resp.json()["status"] == "COMPLETED"

        refreshed = client.get(f"/projects/{project_id}/tasks")
        completed_review = next(
            task for task in refreshed.json()["sections"]["completed"]
            if task["kind"] == "result_review"
        )
        assert completed_review["id"] == review_task["id"]

        reopen_resp = client.patch(
            f"/projects/{project_id}/tasks/{review_task['id']}",
            json={"action": "reopen"},
        )
        assert reopen_resp.status_code == 200
        assert reopen_resp.json()["status"] == "FOLLOW_UP"

    def test_task_action_archive_hides_task(self, client):
        project_id = self._create_project(client)
        client.post("/block", json={
            "project_id": project_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve workflow"},
            "status": "PENDING",
        })

        task_resp = client.get(f"/projects/{project_id}/tasks")
        task_id = task_resp.json()["sections"]["pending"][0]["id"]

        archive_resp = client.patch(
            f"/projects/{project_id}/tasks/{task_id}",
            json={"action": "archive"},
        )
        assert archive_resp.status_code == 200

        refreshed = client.get(f"/projects/{project_id}/tasks")
        sections = refreshed.json()["sections"]
        assert all(task["id"] != task_id for items in sections.values() for task in items)

    def test_download_and_workflow_stage_children(self, client):
        project_id = self._create_project(client)

        client.post("/block", json={
            "project_id": project_id,
            "type": "DOWNLOAD_TASK",
            "payload": {
                "files": [
                    {"filename": "reads_1.fastq.gz", "url": "https://example.test/reads_1.fastq.gz"},
                    {"filename": "reads_2.fastq.gz", "url": "https://example.test/reads_2.fastq.gz"},
                ],
                "downloaded": 1,
                "total_files": 2,
                "current_file": "reads_2.fastq.gz",
            },
            "status": "RUNNING",
        })

        client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-789",
                "sample_name": "sample-c",
                "mode": "DNA",
                "job_status": {
                    "progress_percent": 60,
                    "tasks": {
                        "completed": ["mainWorkflow:basecall (1)"],
                        "running": ["mainWorkflow:align (1)"],
                    },
                },
            },
            "status": "RUNNING",
        })

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]

        running_tasks = sections["running"]
        download_task = next(task for task in running_tasks if task["kind"] == "download")
        run_task = next(task for task in running_tasks if task["kind"] == "run")

        download_child_statuses = {child["title"]: child["status"] for child in download_task["children"]}
        assert download_child_statuses["reads_1.fastq.gz"] == "COMPLETED"
        assert download_child_statuses["reads_2.fastq.gz"] == "RUNNING"

        workflow_child_statuses = {child["title"]: child["status"] for child in run_task["children"]}
        assert workflow_child_statuses["mainWorkflow:basecall (1)"] == "COMPLETED"
        assert workflow_child_statuses["mainWorkflow:align (1)"] == "RUNNING"

    def test_workflow_plan_projects_ordered_steps(self, client):
        project_id = self._create_project(client)

        workflow_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "WORKFLOW_PLAN",
            "payload": {
                "workflow_type": "local_sample_intake",
                "title": "Process local sample Jamshid",
                "sample_name": "Jamshid",
                "status": "RUNNING",
                "next_step": "run_dogme",
                "run_uuid": "run-managed",
                "steps": [
                    {
                        "id": "stage_input",
                        "kind": "copy_sample",
                        "title": "Stage Jamshid into user data",
                        "status": "COMPLETED",
                        "order_index": 0,
                    },
                    {
                        "id": "run_dogme",
                        "kind": "run",
                        "title": "Run Dogme for Jamshid",
                        "status": "RUNNING",
                        "order_index": 1,
                        "run_uuid": "run-managed",
                        "block_id": "job-managed",
                    },
                    {
                        "id": "analyze_results",
                        "kind": "analysis",
                        "title": "Analyze results for Jamshid",
                        "status": "PENDING",
                        "order_index": 2,
                        "run_uuid": "run-managed",
                    },
                ],
            },
            "status": "RUNNING",
        })
        assert workflow_resp.status_code == 200
        workflow_block_id = workflow_resp.json()["id"]

        run_resp = client.post("/block", json={
            "project_id": project_id,
            "type": "EXECUTION_JOB",
            "payload": {
                "run_uuid": "run-managed",
                "sample_name": "Jamshid",
                "mode": "CDNA",
                "workflow_plan_block_id": workflow_block_id,
                "job_status": {"progress_percent": 20},
            },
            "status": "RUNNING",
        })
        assert run_resp.status_code == 200

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        sections = task_resp.json()["sections"]

        workflow_task = next(task for task in sections["running"] if task["kind"] == "workflow_plan")
        child_titles = [child["title"] for child in workflow_task["children"]]
        assert child_titles == [
            "Stage Jamshid into user data",
            "Run Dogme for Jamshid",
            "Analyze results for Jamshid",
        ]
        top_level_running = [task for task in sections["running"] if task["parent_task_id"] is None]
        assert all(task["kind"] != "run" for task in top_level_running if task["id"] != workflow_task["id"])

    def test_clear_blocks_clears_tasks(self, client):
        project_id = self._create_project(client)
        client.post("/block", json={
            "project_id": project_id,
            "type": "APPROVAL_GATE",
            "payload": {"label": "Approve workflow"},
            "status": "PENDING",
        })

        task_resp = client.get(f"/projects/{project_id}/tasks")
        assert task_resp.status_code == 200
        assert len(task_resp.json()["sections"]["pending"]) == 1

        clear_resp = client.delete(f"/projects/{project_id}/blocks")
        assert clear_resp.status_code == 200

        refreshed = client.get(f"/projects/{project_id}/tasks")
        assert refreshed.status_code == 200
        sections = refreshed.json()["sections"]
        assert all(len(items) == 0 for items in sections.values())


# ---------------------------------------------------------------------------
# Skills endpoint
# ---------------------------------------------------------------------------
class TestSkillsEndpoint:
    def test_get_skills(self, client):
        resp = client.get("/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        assert isinstance(data["skills"], list)
        # Should have at least welcome + ENCODE_Search
        assert "welcome" in data["skills"]
