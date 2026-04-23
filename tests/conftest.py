"""
Root conftest — shared fixtures for the entire test suite.

Provides:
  - In-memory SQLite database sessions (sync & async)
  - Mock user objects with configurable roles
  - Mock FastAPI request objects
  - Temporary AGOUTIC_DATA directory
  - Mock MCP client
  - Mock AgentEngine
"""

import json
import uuid
import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_engine(tmp_path):
    """Create an in-memory SQLite engine with all tables."""
    from common.database import Base
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session(db_engine):
    """Yield a SQLAlchemy session bound to the in-memory engine."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# User / Auth fixtures
# ---------------------------------------------------------------------------

class FakeUser:
    """Lightweight stand-in for cortex.models.User in tests."""
    def __init__(self, *, id=None, email="test@example.com", role="user",
                 username="testuser", display_name="Test User", is_active=True):
        self.id = id or str(uuid.uuid4())
        self.email = email
        self.role = role
        self.username = username
        self.display_name = display_name
        self.is_active = is_active
        self.last_project_id = None
        self.token_limit = None


@pytest.fixture()
def mock_user():
    """A default authenticated non-admin user."""
    return FakeUser()


@pytest.fixture()
def mock_admin():
    """An admin user."""
    return FakeUser(email="admin@example.com", role="admin", username="admin")


@pytest.fixture()
def mock_request(mock_user):
    """A FastAPI Request stub with state.user set."""
    req = MagicMock()
    req.state.user = mock_user
    req.cookies = {}
    return req


# ---------------------------------------------------------------------------
# Filesystem fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_agoutic_data(tmp_path, monkeypatch):
    """
    Monkeypatch AGOUTIC_DATA across all modules to a temp directory.
    Returns the Path so tests can create files under it.
    """
    data_dir = tmp_path / "agoutic_data"
    data_dir.mkdir()

    # Patch in every module that imports AGOUTIC_DATA
    monkeypatch.setattr("cortex.config.AGOUTIC_DATA", data_dir)
    monkeypatch.setattr("cortex.user_jail.AGOUTIC_DATA", data_dir)

    return data_dir


# ---------------------------------------------------------------------------
# Mock MCP client
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_mcp_client():
    """A mock MCPHttpClient whose call_tool returns configurable responses."""
    client = AsyncMock()
    client.name = "test-mcp"
    client.base_url = "http://localhost:9999"
    client.call_tool = AsyncMock(return_value={"status": "ok"})
    client.list_tools = AsyncMock(return_value=[])
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Mock AgentEngine
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_agent_engine():
    """A mock AgentEngine whose think/analyze_results return configurable responses."""
    engine = MagicMock()
    engine.model_name = "test-model"
    engine.display_name = "test (test-model)"
    engine.think = AsyncMock(return_value=("Test response", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}))
    engine.analyze_results = AsyncMock(return_value=("Analysis response", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}))
    engine.construct_system_prompt = MagicMock(return_value="System prompt")
    return engine


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------

class FakeBlock:
    """Minimal stand-in for ProjectBlock, usable in tests for _validate_llm_output etc."""
    def __init__(self, *, type="AGENT_PLAN", status="DONE", payload=None):
        self.id = str(uuid.uuid4())
        self.project_id = "proj-test"
        self.owner_id = "owner-test"
        self.seq = 1
        self.type = type
        self.status = status
        self.payload_json = json.dumps(payload or {})
        self.parent_id = None
        self.created_at = datetime.datetime.utcnow().isoformat()


@pytest.fixture()
def make_block():
    """Factory fixture: call make_block(type=..., status=..., payload=...) to create FakeBlocks."""
    def _factory(**kwargs):
        return FakeBlock(**kwargs)
    return _factory
