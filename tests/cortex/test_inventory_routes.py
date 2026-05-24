import datetime
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.base import BaseHTTPMiddleware

from common.database import Base
from cortex.routes.inventory import router as inventory_router
from cortex.models import Project, ProjectAccess, Session as SessionModel, User, UserFile
from launchpad.models import DogmeJob, RemoteStagedSample


class _FakeMCPClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def call_tool(self, tool_name: str, **kwargs):
        self.calls.append((tool_name, dict(kwargs)))
        if not self._responses:
            raise AssertionError(f"Unexpected MCP call: {tool_name}")
        return self._responses.pop(0)


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
def client(session_factory, tmp_path):
    sess = session_factory()

    user = User(id="u-inv", email="inv@example.com", role="user", username="invuser", is_active=True)
    sess.add(user)
    sess.add(
        SessionModel(
            id="inv-session",
            user_id=user.id,
            is_valid=True,
            expires_at=datetime.datetime(2099, 1, 1),
        )
    )

    project = Project(id="proj-1", name="Project Alpha", owner_id=user.id, slug="project-alpha")
    sess.add(project)
    sess.add(
        ProjectAccess(
            id=str(uuid.uuid4()),
            user_id=user.id,
            project_id=project.id,
            project_name=project.name,
            role="owner",
            last_accessed=datetime.datetime.utcnow(),
        )
    )

    central_dir = tmp_path / "users" / "invuser" / "data"
    central_dir.mkdir(parents=True, exist_ok=True)
    project_dir = tmp_path / "users" / "invuser" / "project-alpha"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "workflow7").mkdir(parents=True, exist_ok=True)
    (project_dir / "workflow8").mkdir(parents=True, exist_ok=True)

    sess.add(
        UserFile(
            id="uf-1",
            user_id=user.id,
            filename="tumor-a.pod5",
            md5_hash="abc",
            size_bytes=1024,
            source="upload",
            sample_name="tumor-a",
            disk_path=str(central_dir / "tumor-a.pod5"),
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
    )
    sess.add(
        RemoteStagedSample(
            id="staged-1",
            user_id=user.id,
            ssh_profile_id="profile-1",
            ssh_profile_nickname="hpc3",
            sample_name="tumor-a",
            sample_slug="tumor-a",
            mode="DNA",
            reference_genome_json=["GRCh38"],
            source_path="/data/tumor-a",
            input_fingerprint="fp-1",
            remote_base_path="/remote/agoutic",
            remote_data_path="/remote/agoutic/data/tumor-a",
            remote_reference_paths_json={"GRCh38": "/remote/agoutic/ref/GRCh38"},
            status="READY",
            last_staged_at=datetime.datetime.utcnow(),
            last_used_at=datetime.datetime.utcnow(),
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
    )
    sess.add(
        DogmeJob(
            run_uuid="run-imported-1",
            project_id=project.id,
            user_id=user.id,
            workflow_index=7,
            workflow_folder_name="workflow7",
            workflow_display_name="tumor-a",
            sample_name="tumor-a",
            mode="DNA",
            input_directory="/data/tumor-a",
            status="COMPLETED",
            imported_source_kind="slurm",
            imported_source_path="/remote/project/workflow3",
            imported_source_complete=True,
            submitted_at=datetime.datetime.utcnow(),
            completed_at=datetime.datetime.utcnow(),
        )
    )
    sess.add(
        DogmeJob(
            run_uuid="run-local-7",
            project_id=project.id,
            user_id=user.id,
            workflow_index=7,
            workflow_folder_name="workflow7",
            workflow_display_name="tumor-a",
            sample_name="tumor-a",
            mode="DNA",
            input_directory="/data/tumor-a",
            nextflow_work_dir=str(project_dir / "workflow7"),
            status="COMPLETED",
            submitted_at=datetime.datetime.utcnow(),
            completed_at=datetime.datetime.utcnow(),
        )
    )
    sess.commit()
    sess.close()

    class _InjectUserMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            auth_session = session_factory()
            try:
                request.state.user = auth_session.get(User, "u-inv")
            finally:
                auth_session.close()
            return await call_next(request)

    test_app = FastAPI()
    test_app.add_middleware(_InjectUserMiddleware)
    test_app.include_router(inventory_router)

    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.routes.inventory._db.SessionLocal", session_factory), \
         patch("cortex.db_helpers._cfg.AGOUTIC_DATA", tmp_path), \
         patch("cortex.list_commands.get_service_url", lambda _key: "http://analyzer"):
        c = TestClient(test_app, raise_server_exceptions=False)
        yield c


def test_inventory_samples_endpoint(client):
    resp = client.get("/inventory/samples")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["items"][0]["sample_name"] == "tumor-a"


def test_inventory_staged_endpoint(client):
    resp = client.get("/inventory/staged", params={"profile": "hpc3"})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["items"][0]["profile"] == "hpc3"


def test_inventory_imported_endpoint(client):
    resp = client.get("/inventory/imported")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["items"][0]["workflow"] == "workflow7"
    assert payload["items"][0]["project"] == "Project Alpha"


def test_inventory_workflows_endpoint_lists_tracked_and_untracked(client):
    resp = client.get("/inventory/workflows", params={"project_id": "proj-1"})

    assert resp.status_code == 200
    payload = resp.json()
    names = {item["workflow"] for item in payload["items"]}
    assert {"workflow7", "workflow8"}.issubset(names)


def test_inventory_files_endpoint_lists_requested_workflow(client):
    fake_client = _FakeMCPClient(
        [
            {
                "success": True,
                "work_dir": "/tmp/project-alpha/workflow7",
                "file_count": 1,
                "files": [{"path": "report.txt", "name": "report.txt", "size": 64, "modified_time": "2026-05-24T12:00:00Z"}],
            }
        ]
    )

    with patch("cortex.list_commands.MCPHttpClient", lambda name, base_url: fake_client):
        resp = client.get(
            "/inventory/files",
            params={"project_id": "proj-1", "workflow_ref": "workflow7", "max_depth": 1},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["items"][0]["path"] == "report.txt"
    assert fake_client.calls[0][0] == "list_job_files"