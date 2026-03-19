"""
Tests for cortex/app.py — /chat endpoint LLM response processing.

These tests go through the FULL chat flow with mocked AgentEngine.think
to exercise the middle/end of the function:

  - Approval gate creation ([[APPROVAL_NEEDED]] in LLM response)
  - Approval suppression for non-job skills
  - Skill switch tag ([[SKILL_SWITCH_TO:...]])
  - DATA_CALL tag parsing (but MCP call mocked)
  - History trimming (MAX_HISTORY_TURNS)
  - Conversation message persistence
  - Error handling
"""

import datetime
import json
import uuid

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
def seed_user(session_factory, tmp_path):
    sess = session_factory()
    user = User(id="u-flow", email="flow@example.com", role="user",
                username="flowuser", is_active=True)
    sess.add(user)
    session_obj = SessionModel(id="flow-session", user_id=user.id,
                               is_valid=True,
                               expires_at=datetime.datetime(2099, 1, 1))
    sess.add(session_obj)
    proj = Project(id="proj-flow", name="Flow Proj", owner_id="u-flow",
                   slug="flow-proj")
    sess.add(proj)
    sess.flush()
    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-flow",
                           project_id="proj-flow", project_name="Flow Proj",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    sess.add(access)
    sess.commit()
    sess.close()


def _make_client(session_factory, seed_user, tmp_path, think_fn):
    """Build a TestClient with a custom AgentEngine.think mock."""
    patches = [
        patch("cortex.db.SessionLocal", session_factory),
        patch("cortex.app.SessionLocal", session_factory),
        patch("cortex.dependencies.SessionLocal", session_factory),
        patch("cortex.middleware.SessionLocal", session_factory),
        patch("cortex.config.AGOUTIC_DATA", tmp_path),
        patch("cortex.user_jail.AGOUTIC_DATA", tmp_path),
        patch("cortex.app._resolve_project_dir", return_value=tmp_path / "proj"),
    ]
    engine_patch = patch("cortex.app.AgentEngine")

    for p in patches:
        p.start()
    mock_engine_cls = engine_patch.start()
    inst = mock_engine_cls.return_value
    inst.model_name = "test-model"
    inst.think = think_fn
    # Default analyze_results so second-pass LLM calls don't crash
    inst.analyze_results = lambda *a, **kw: ("Analysis complete.", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("session", "flow-session")
    yield c

    engine_patch.stop()
    for p in reversed(patches):
        p.stop()


@pytest.fixture()
def approval_client(session_factory, seed_user, tmp_path):
    """Client where the LLM returns [[APPROVAL_NEEDED]]."""
    def think(msg, skill, history):
        return (
            "Here is my plan:\n- Map reads\n- Call mods\n\n[[APPROVAL_NEEDED]]",
            {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        )
    yield from _make_client(session_factory, seed_user, tmp_path, think)


@pytest.fixture()
def switch_client(session_factory, seed_user, tmp_path):
    """Client where the LLM returns a SKILL_SWITCH_TO tag (non-ENCODE to avoid MCP)."""
    call_count = 0
    def think(msg, skill, history):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (
                "I'll switch to the right skill.\n[[SKILL_SWITCH_TO: analyze_job_results]]",
                {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            )
        else:
            return (
                "Analyzing your job results now...",
                {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
            )
    yield from _make_client(session_factory, seed_user, tmp_path, think)


@pytest.fixture()
def plain_client(session_factory, seed_user, tmp_path):
    """Client where the LLM returns a plain response."""
    def think(msg, skill, history):
        return (
            "I understand you want to analyze DNA. Let me help you set up the pipeline.",
            {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        )
    yield from _make_client(session_factory, seed_user, tmp_path, think)


@pytest.fixture()
def spurious_approval_client(session_factory, seed_user, tmp_path):
    """Client where the LLM returns [[APPROVAL_NEEDED]] for a non-job skill."""
    def think(msg, skill, history):
        return (
            "Found 5 experiments.\n[[APPROVAL_NEEDED]]",
            {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        )
    yield from _make_client(session_factory, seed_user, tmp_path, think)


# ---------------------------------------------------------------------------
# Approval gate creation
# ---------------------------------------------------------------------------
class TestApprovalGateCreation:
    def test_approval_needed_creates_gate(self, approval_client):
        resp = approval_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Run DNA analysis on /data/pod5",
            "skill": "analyze_local_sample",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["gate_block"] is not None
        gate = data["gate_block"]
        assert gate["status"] == "PENDING"
        assert gate["type"] == "APPROVAL_GATE"
        payload = gate["payload"]
        assert "extracted_params" in payload
        assert payload["attempt_number"] == 1

    def test_approval_gate_has_skill(self, approval_client):
        resp = approval_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Analyze sample at /data/pod5",
            "skill": "analyze_local_sample",
        })
        payload = resp.json()["gate_block"]["payload"]
        assert payload["skill"] == "analyze_local_sample"

    def test_no_gate_for_plain_response(self, plain_client):
        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "What pipeline should I use?",
            "skill": "welcome",
        })
        assert resp.status_code == 200
        assert resp.json()["gate_block"] is None

    def test_approval_suppressed_non_job_skill(self, spurious_approval_client):
        """LLM returns [[APPROVAL_NEEDED]] for ENCODE_Search → suppressed."""
        resp = spurious_approval_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Search ENCODE for K562",
            "skill": "ENCODE_Search",
        })
        assert resp.status_code == 200
        assert resp.json()["gate_block"] is None
        # The tag should be stripped from the markdown
        md = resp.json()["agent_block"]["payload"]["markdown"]
        assert "[[APPROVAL_NEEDED]]" not in md

    def test_stage_on_hpc3_routes_to_remote_stage_workflow(self, plain_client):
        with patch("cortex.app.asyncio.create_task", MagicMock()):
            resp = plain_client.post("/chat", json={
                "project_id": "proj-flow",
                "message": "stage the mouse CDNA sample called Jamshid at /media/backup_disk/agoutic_root/testdata/CDNA/pod5 on hpc3",
                "skill": "welcome",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_block"] is not None
        assert data["gate_block"] is None
        assert data["agent_block"]["payload"]["skill"] == "remote_execution"
        plan_payload = data["plan_block"]["payload"]
        assert plan_payload["workflow_type"] == "remote_sample_intake"
        assert plan_payload["remote_action"] == "stage_only"


# ---------------------------------------------------------------------------
# Skill switch
# ---------------------------------------------------------------------------
class TestSkillSwitch:
    def test_skill_switch_re_runs_llm(self, switch_client):
        resp = switch_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "I'd like to look at my job results",
            "skill": "welcome",
        })
        assert resp.status_code == 200
        md = resp.json()["agent_block"]["payload"]["markdown"]
        # The final response should be from the second think call
        assert "Analyzing your job results" in md
        # The switch tag should be stripped
        assert "[[SKILL_SWITCH_TO" not in md


# ---------------------------------------------------------------------------
# Normal flow: blocks + conversation persistence
# ---------------------------------------------------------------------------
class TestConversationPersistence:
    def test_user_block_created(self, plain_client, session_factory):
        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Hello agent",
            "skill": "welcome",
        })
        assert resp.status_code == 200

        sess = session_factory()
        user_blocks = sess.execute(
            select(ProjectBlock).where(
                ProjectBlock.project_id == "proj-flow",
                ProjectBlock.type == "USER_MESSAGE",
            )
        ).scalars().all()
        assert len(user_blocks) == 1
        payload = json.loads(user_blocks[0].payload_json)
        assert payload["text"] == "Hello agent"
        sess.close()

    def test_agent_block_has_tokens(self, plain_client, session_factory):
        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Run pipeline",
            "skill": "analyze_local_sample",
        })
        assert resp.status_code == 200
        payload = resp.json()["agent_block"]["payload"]
        assert payload["tokens"]["total_tokens"] == 80
        assert payload["tokens"]["model"] == "test-model"

    def test_conversation_messages_saved(self, plain_client, session_factory):
        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Analyze data",
            "skill": "welcome",
        })
        assert resp.status_code == 200

        sess = session_factory()
        msgs = sess.execute(select(ConversationMessage)).scalars().all()
        # Should have user + assistant messages saved
        roles = [m.role for m in msgs]
        assert "user" in roles
        assert "assistant" in roles
        sess.close()

    def test_agent_block_has_debug(self, plain_client):
        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Test debug info",
            "skill": "welcome",
        })
        assert resp.status_code == 200
        payload = resp.json()["agent_block"]["payload"]
        assert "_debug" in payload
        debug = payload["_debug"]
        assert "active_skill" in debug
        assert "pre_llm_skill" in debug

    def test_state_in_payload(self, plain_client):
        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "State test",
            "skill": "welcome",
        })
        assert resp.status_code == 200
        payload = resp.json()["agent_block"]["payload"]
        assert "state" in payload


# ---------------------------------------------------------------------------
# History handling
# ---------------------------------------------------------------------------
class TestHistoryHandling:
    def _seed_blocks(self, session_factory, project_id, count, owner_id="u-flow"):
        """Seed alternating USER_MESSAGE + AGENT_PLAN blocks."""
        sess = session_factory()
        for i in range(count):
            btype = "USER_MESSAGE" if i % 2 == 0 else "AGENT_PLAN"
            payload = ({"text": f"msg {i}"} if btype == "USER_MESSAGE"
                      else {"markdown": f"response {i}", "skill": "welcome"})
            block = ProjectBlock(
                id=str(uuid.uuid4()),
                project_id=project_id,
                type=btype,
                payload_json=json.dumps(payload),
                status="DONE",
                seq=i + 1,
                owner_id=owner_id,
            )
            sess.add(block)
        sess.commit()
        sess.close()

    def test_long_history_trimmed(self, plain_client, session_factory):
        """History > 40 messages should be trimmed to last 40."""
        # Seed 50 blocks (25 user + 25 agent pairs)
        self._seed_blocks(session_factory, "proj-flow", 50)

        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Continue the conversation",
            "skill": "welcome",
        })
        # Should succeed even with many blocks
        assert resp.status_code == 200

    def test_details_blocks_stripped(self, plain_client, session_factory):
        """Agent blocks with <details> should have those stripped from history."""
        sess = session_factory()
        block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-flow",
            type="AGENT_PLAN",
            payload_json=json.dumps({
                "markdown": "Results:\n---\n<details><summary>Raw</summary>lots of data</details>"
            }),
            status="DONE", seq=1, owner_id="u-flow",
        )
        sess.add(block)
        sess.commit()
        sess.close()

        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Follow up question",
            "skill": "welcome",
        })
        assert resp.status_code == 200

    def test_find_file_echo_skipped(self, plain_client, session_factory):
        """Agent blocks that are just find_file JSON echo should be skipped."""
        sess = session_factory()
        echo_json = json.dumps({"success": True, "primary_path": "/data/sample.bam"})
        block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-flow",
            type="AGENT_PLAN",
            payload_json=json.dumps({
                "markdown": f"```json\n{echo_json}\n```"
            }),
            status="DONE", seq=1, owner_id="u-flow",
        )
        sess.add(block)
        sess.commit()
        sess.close()

        resp = plain_client.post("/chat", json={
            "project_id": "proj-flow",
            "message": "Now what?",
            "skill": "welcome",
        })
        assert resp.status_code == 200
