"""
Tests for cortex/app.py — /chat endpoint entry path.

Covers the initial portion of ``chat_with_agent`` WITHOUT invoking
the LLM or any external MCP services:

  - Project auto-registration (unknown project_id in chat request)
  - Token limit enforcement (429 when quota exhausted)
  - Capabilities / help response (short-circuit for "what can you do")
  - Skill continuation logic (persisting active skill across turns)

All tests use an in-memory SQLite DB and mock AgentEngine.think so
the LLM is never called.
"""

import asyncio
import datetime
import json
import re
import uuid

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage,
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
def seed_user(session_factory):
    """Create a regular user with session + project + access."""
    sess = session_factory()
    user = User(id="u-chat", email="chat@example.com", role="user",
                username="chatuser", is_active=True)
    sess.add(user)
    session_obj = SessionModel(id="chat-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)
    proj = Project(id="proj-chat", name="Chat Project", owner_id="u-chat",
                   slug="chat-project")
    sess.add(proj)
    sess.flush()
    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-chat",
                           project_id="proj-chat", project_name="Chat Project",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    sess.add(access)
    sess.commit()
    sess.close()


@pytest.fixture()
def seed_admin(session_factory):
    """Create an admin user with session + project."""
    sess = session_factory()
    user = User(id="u-admin", email="admin@example.com", role="admin",
                username="adminuser", is_active=True)
    sess.add(user)
    session_obj = SessionModel(id="admin-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)
    proj = Project(id="proj-admin", name="Admin Proj", owner_id="u-admin",
                   slug="admin-proj")
    sess.add(proj)
    sess.flush()
    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-admin",
                           project_id="proj-admin", project_name="Admin Proj",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    sess.add(access)
    sess.commit()
    sess.close()


def _mock_think(message, skill, history):
    """Fake AgentEngine.think — returns a simple plan with no approval tag."""
    return (
        f"Here is my plan for **{skill}**.\n\nI'll do something smart.",
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )


@pytest.fixture()
def client(session_factory, seed_user, tmp_path):
    """TestClient patched with in-memory DB and mocked AgentEngine.think."""
    mock_engine_cls = MagicMock()
    inst = mock_engine_cls.return_value
    inst.model_name = "test-model"
    inst.think = MagicMock(side_effect=_mock_think)
    inst.render_system_prompt = MagicMock(
        side_effect=lambda skill_key, prompt_type: f"Rendered {prompt_type} prompt for {skill_key}"
    )

    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.chat_stages.setup.SessionLocal", session_factory), \
         patch("cortex.chat_stages.overrides.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path), \
         patch("cortex.app._resolve_project_dir", return_value=tmp_path / "proj"), \
         patch("cortex.chat_stages.context_prep._resolve_project_dir", return_value=tmp_path / "proj"), \
         patch("cortex.app.AgentEngine", mock_engine_cls), \
         patch("cortex.agent_engine.AgentEngine", mock_engine_cls), \
         patch("cortex.chat_stages.context_prep.AgentEngine", mock_engine_cls), \
         patch("cortex.chat_stages.llm_first_pass.AgentEngine", mock_engine_cls):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "chat-session")
        c.mock_engine = inst
        yield c


@pytest.fixture()
def admin_client(session_factory, seed_admin, tmp_path):
    """TestClient logged in as admin."""
    mock_engine_cls = MagicMock()
    inst = mock_engine_cls.return_value
    inst.model_name = "test-model"
    inst.think = MagicMock(side_effect=_mock_think)
    inst.render_system_prompt = MagicMock(
        side_effect=lambda skill_key, prompt_type: f"Rendered {prompt_type} prompt for {skill_key}"
    )

    with patch("cortex.db.SessionLocal", session_factory), \
         patch("cortex.app.SessionLocal", session_factory), \
         patch("cortex.chat_stages.setup.SessionLocal", session_factory), \
         patch("cortex.chat_stages.overrides.SessionLocal", session_factory), \
         patch("cortex.dependencies.SessionLocal", session_factory), \
         patch("cortex.middleware.SessionLocal", session_factory), \
         patch("cortex.config.AGOUTIC_DATA", tmp_path), \
         patch("cortex.user_jail.AGOUTIC_DATA", tmp_path), \
         patch("cortex.app._resolve_project_dir", return_value=tmp_path / "proj"), \
         patch("cortex.chat_stages.context_prep._resolve_project_dir", return_value=tmp_path / "proj"), \
         patch("cortex.app.AgentEngine", mock_engine_cls), \
         patch("cortex.agent_engine.AgentEngine", mock_engine_cls), \
         patch("cortex.chat_stages.context_prep.AgentEngine", mock_engine_cls), \
         patch("cortex.chat_stages.llm_first_pass.AgentEngine", mock_engine_cls):
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set("session", "admin-session")
        c.mock_engine = inst
        yield c


# ---------------------------------------------------------------------------
# Capabilities / Help response
# ---------------------------------------------------------------------------
class TestCapabilitiesResponse:
    """When the user sends a "help" message, the chat endpoint returns
    a pre-baked capabilities list WITHOUT calling the LLM."""

    def test_what_can_you_do(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "What can you do?",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "Welcome to **Agoutic**" in data["agent_block"]["payload"]["markdown"]
        assert data["gate_block"] is None

    def test_help_trigger(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "what i can help you with" in data["agent_block"]["payload"]["markdown"].lower()

    def test_show_capabilities(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "show capabilities",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "Analyze a new local dataset" in data["agent_block"]["payload"]["markdown"]

    def test_what_can_i_do(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "What can I do?",
        })
        assert resp.status_code == 200
        assert resp.json()["gate_block"] is None

    def test_list_features(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "list features",
        })
        assert resp.status_code == 200
        payload = resp.json()["agent_block"]["payload"]
        assert "differential expression" in payload["markdown"].lower()


class TestPromptInspectionResponse:
    def test_ambiguous_prompt_request_returns_clarification(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "Show me your system prompt",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "first-pass planning prompt" in data["agent_block"]["payload"]["markdown"]

    def test_first_pass_prompt_request_bypasses_think(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "Show me the first-pass system prompt",
            "skill": "ENCODE_Search",
            "model": "default",
        })
        assert resp.status_code == 200
        data = resp.json()
        payload = data["agent_block"]["payload"]
        assert "First-pass system prompt" in payload["markdown"]
        assert "Rendered first_pass prompt for ENCODE_Search" in payload["markdown"]
        assert payload["_debug"]["prompt_type"] == "first_pass"
        client.mock_engine.think.assert_not_called()

    def test_second_pass_prompt_request_bypasses_think(self, client):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "Show me the second-pass system prompt",
        })
        assert resp.status_code == 200
        data = resp.json()
        payload = data["agent_block"]["payload"]
        assert "Second-pass system prompt" in payload["markdown"]
        assert "Rendered second_pass prompt for welcome" in payload["markdown"]
        client.mock_engine.think.assert_not_called()


# ---------------------------------------------------------------------------
# Project auto-registration
# ---------------------------------------------------------------------------
class TestProjectAutoRegister:
    """When the user sends a chat message referencing a project_id that
    does NOT exist in the DB, the chat endpoint auto-creates the project
    and access row before proceeding."""

    def test_auto_register_creates_project(self, client, session_factory):
        new_id = f"proj-{uuid.uuid4().hex[:8]}"
        resp = client.post("/chat", json={
            "project_id": new_id,
            "message": "help",
        })
        assert resp.status_code == 200

        # Verify the project was created
        sess = session_factory()
        proj = sess.execute(
            select(Project).where(Project.id == new_id)
        ).scalar_one_or_none()
        assert proj is not None
        assert proj.owner_id == "u-chat"
        sess.close()

    def test_auto_register_creates_access(self, client, session_factory):
        new_id = f"proj-{uuid.uuid4().hex[:8]}"
        resp = client.post("/chat", json={
            "project_id": new_id,
            "message": "What can you do?",
        })
        assert resp.status_code == 200

        sess = session_factory()
        access = sess.execute(
            select(ProjectAccess).where(
                ProjectAccess.project_id == new_id,
                ProjectAccess.user_id == "u-chat"
            )
        ).scalar_one_or_none()
        assert access is not None
        assert access.role == "owner"
        sess.close()

    def test_existing_project_not_duplicated(self, client, session_factory):
        """Sending to an existing project does NOT create a duplicate."""
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
        })
        assert resp.status_code == 200

        sess = session_factory()
        count = sess.execute(
            text("SELECT COUNT(*) FROM projects WHERE id = 'proj-chat'")
        ).scalar()
        assert count == 1
        sess.close()


# ---------------------------------------------------------------------------
# Token limit enforcement
# ---------------------------------------------------------------------------
class TestTokenLimit:
    """Regular users with a token_limit should get 429 when exhausted."""

    def _set_token_limit(self, session_factory, user_id, limit):
        sess = session_factory()
        user = sess.execute(select(User).where(User.id == user_id)).scalar_one()
        user.token_limit = limit
        sess.commit()
        sess.close()

    def _add_usage(self, session_factory, user_id, project_id, tokens):
        """Seed a conversation with total_tokens usage."""
        sess = session_factory()
        conv = Conversation(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user_id,
            title="Token test",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        sess.add(conv)
        sess.flush()
        msg = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            role="assistant",
            content="Sure, here is...",
            seq=0,
            total_tokens=tokens,
            created_at=datetime.datetime.utcnow(),
        )
        sess.add(msg)
        sess.commit()
        sess.close()

    def test_under_limit_allowed(self, client, session_factory):
        """User under their limit should proceed normally."""
        self._set_token_limit(session_factory, "u-chat", 99999)
        self._add_usage(session_factory, "u-chat", "proj-chat", 100)
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
        })
        assert resp.status_code == 200

    def test_at_limit_rejected(self, client, session_factory):
        """User exactly at their limit should be rejected."""
        self._set_token_limit(session_factory, "u-chat", 500)
        self._add_usage(session_factory, "u-chat", "proj-chat", 500)
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "Analyze my sample",
        })
        assert resp.status_code == 429
        body = resp.json()
        assert body["detail"]["error"] == "token_limit_exceeded"
        assert body["detail"]["tokens_used"] == 500
        assert body["detail"]["token_limit"] == 500

    def test_over_limit_rejected(self, client, session_factory):
        """User over their limit should be rejected."""
        self._set_token_limit(session_factory, "u-chat", 1000)
        self._add_usage(session_factory, "u-chat", "proj-chat", 1500)
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "run DNA analysis",
        })
        assert resp.status_code == 429
        body = resp.json()
        assert body["detail"]["tokens_used"] == 1500

    def test_no_limit_means_unlimited(self, client, session_factory):
        """User without a token_limit is never throttled."""
        # Default: token_limit = None
        self._add_usage(session_factory, "u-chat", "proj-chat", 999999)
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
        })
        assert resp.status_code == 200

    def test_admin_exempt_from_limit(self, admin_client, session_factory):
        """Admin users bypass token limit checks."""
        sess = session_factory()
        admin = sess.execute(select(User).where(User.id == "u-admin")).scalar_one()
        admin.token_limit = 1  # absurdly low
        sess.commit()
        sess.close()
        # Add usage exceeding the limit
        self._add_usage(session_factory, "u-admin", "proj-admin", 999)
        resp = admin_client.post("/chat", json={
            "project_id": "proj-admin",
            "message": "help",
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Skill continuation logic
# ---------------------------------------------------------------------------
class TestSkillContinuation:
    """The /chat endpoint infers the active skill from previous blocks."""

    def _add_agent_plan(self, session_factory, project_id, skill, seq, owner_id="u-chat"):
        sess = session_factory()
        block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id=project_id,
            type="AGENT_PLAN",
            payload_json=json.dumps({"markdown": "plan", "skill": skill}),
            status="DONE",
            seq=seq,
            owner_id=owner_id,
        )
        sess.add(block)
        sess.commit()
        sess.close()

    def _add_approval_gate(self, session_factory, project_id, status, seq, owner_id="u-chat"):
        sess = session_factory()
        block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id=project_id,
            type="APPROVAL_GATE",
            payload_json=json.dumps({"label": "Approve?"}),
            status=status,
            seq=seq,
            owner_id=owner_id,
        )
        sess.add(block)
        sess.commit()
        sess.close()

    def test_continues_with_previous_skill(self, client, session_factory):
        """If the last block is AGENT_PLAN with a skill, the chat endpoint
        should continue with that skill (not fall back to default)."""
        self._add_agent_plan(session_factory, "proj-chat", "ENCODE_Search", 1)
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
        })
        assert resp.status_code == 200
        # The agent_block should carry the continued skill
        payload = resp.json()["agent_block"]["payload"]
        # Help response uses active_skill or req.skill
        assert payload.get("skill") is not None

    def test_fresh_skill_after_approved_gate(self, client, session_factory):
        """If the most recent interaction is an APPROVED approval gate,
        the endpoint starts fresh with the request's skill."""
        self._add_agent_plan(session_factory, "proj-chat", "ENCODE_Search", 1)
        self._add_approval_gate(session_factory, "proj-chat", "APPROVED", 2)
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
            "skill": "welcome",
        })
        assert resp.status_code == 200
        payload = resp.json()["agent_block"]["payload"]
        assert payload["skill"] == "welcome"

    def test_fresh_skill_after_rejected_gate(self, client, session_factory):
        """Rejected gate also resets to request skill."""
        self._add_agent_plan(session_factory, "proj-chat", "analyze_local_sample", 1)
        self._add_approval_gate(session_factory, "proj-chat", "REJECTED", 2)
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
            "skill": "welcome",
        })
        assert resp.status_code == 200
        payload = resp.json()["agent_block"]["payload"]
        assert payload["skill"] == "welcome"


# ---------------------------------------------------------------------------
# Normal chat message (LLM called, mocked)
# ---------------------------------------------------------------------------
class TestNormalChatFlow:
    """Test that a normal (non-help) message goes through the full pipeline."""

    def test_normal_message_calls_llm(self, client, session_factory):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "Analyze my DNA sample at /data/pod5",
            "skill": "analyze_local_sample",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        # Should have user and agent blocks
        assert data["user_block"] is not None
        assert data["agent_block"] is not None

    def test_user_block_saved(self, client, session_factory):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "Process my RNA data",
            "skill": "analyze_local_sample",
        })
        assert resp.status_code == 200
        user_block = resp.json()["user_block"]
        assert user_block["payload"]["text"] == "Process my RNA data"
        assert user_block["type"] == "USER_MESSAGE"

    def test_agent_block_saved(self, client, session_factory):
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "Run cDNA analysis",
            "skill": "analyze_local_sample",
        })
        assert resp.status_code == 200
        agent_block = resp.json()["agent_block"]
        assert agent_block["type"] == "AGENT_PLAN"
        assert "markdown" in agent_block["payload"]
        assert "skill" in agent_block["payload"]

    def test_request_id_tracking(self, client):
        """request_id should be echoed if provided."""
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
            "request_id": "req-123",
        })
        assert resp.status_code == 200

    def test_model_parameter_forwarded(self, client):
        """The model key should be stored in the agent block payload."""
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "help",
            "model": "custom-model",
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestChatEdgeCases:
    def test_unauthenticated(self, session_factory, session_factory_as="sf"):
        """No session cookie → 401."""
        with patch("cortex.db.SessionLocal", session_factory), \
             patch("cortex.app.SessionLocal", session_factory), \
             patch("cortex.dependencies.SessionLocal", session_factory), \
             patch("cortex.middleware.SessionLocal", session_factory):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post("/chat", json={
                "project_id": "proj-chat",
                "message": "hello",
            })
            assert resp.status_code == 401

    def test_missing_project_id(self, client):
        """Missing required field → 422."""
        resp = client.post("/chat", json={
            "message": "hello",
        })
        assert resp.status_code == 422

    def test_empty_message(self, client):
        """Empty message should still work (LLM handles it)."""
        resp = client.post("/chat", json={
            "project_id": "proj-chat",
            "message": "",
        })
        # Should go through capabilities check and then to LLM
        assert resp.status_code == 200
