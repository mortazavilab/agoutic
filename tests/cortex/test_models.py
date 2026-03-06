"""
Tests for cortex/models.py — SQLAlchemy ORM models.

Validates that all models can be instantiated, persisted, and queried
using an in-memory SQLite database.
"""

import uuid
import datetime
import json

import pytest
from sqlalchemy import select

from cortex.models import (
    Base, User, Session, Project, ProjectAccess,
    Conversation, ConversationMessage, JobResult, ProjectBlock,
)


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------
class TestUserModel:
    def test_create_user(self, db_session):
        user = User(
            id=str(uuid.uuid4()),
            email="alice@example.com",
            role="user",
            is_active=True,
            username="alice",
            display_name="Alice Test",
        )
        db_session.add(user)
        db_session.commit()
        fetched = db_session.execute(select(User).where(User.email == "alice@example.com")).scalar_one()
        assert fetched.username == "alice"
        assert fetched.role == "user"
        assert fetched.is_active is True

    def test_user_defaults(self, db_session):
        user = User(id="u1", email="bob@example.com", is_active=False)
        db_session.add(user)
        db_session.commit()
        fetched = db_session.execute(select(User).where(User.id == "u1")).scalar_one()
        assert fetched.role == "user"
        assert fetched.is_active is False
        assert fetched.username is None
        assert fetched.token_limit is None

    def test_unique_email(self, db_session):
        u1 = User(id="u1", email="dup@test.com", is_active=True)
        u2 = User(id="u2", email="dup@test.com", is_active=True)
        db_session.add(u1)
        db_session.commit()
        db_session.add(u2)
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_unique_username(self, db_session):
        u1 = User(id="u1", email="a@test.com", username="same", is_active=True)
        u2 = User(id="u2", email="b@test.com", username="same", is_active=True)
        db_session.add(u1)
        db_session.commit()
        db_session.add(u2)
        with pytest.raises(Exception):
            db_session.commit()


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------
class TestSessionModel:
    def test_create_session(self, db_session):
        now = datetime.datetime.utcnow()
        sess = Session(
            id="session-token-1",
            user_id="u1",
            expires_at=now + datetime.timedelta(hours=24),
            is_valid=True,
        )
        db_session.add(sess)
        db_session.commit()
        fetched = db_session.execute(select(Session).where(Session.id == "session-token-1")).scalar_one()
        assert fetched.user_id == "u1"
        assert fetched.is_valid is True


# ---------------------------------------------------------------------------
# Project model
# ---------------------------------------------------------------------------
class TestProjectModel:
    def test_create_project(self, db_session):
        proj = Project(
            id="proj-1",
            name="My ENCODE Project",
            slug="my-encode-project",
            owner_id="u1",
        )
        db_session.add(proj)
        db_session.commit()
        fetched = db_session.execute(select(Project).where(Project.id == "proj-1")).scalar_one()
        assert fetched.name == "My ENCODE Project"
        assert fetched.slug == "my-encode-project"
        assert fetched.is_public is False
        assert fetched.is_archived is False

    def test_project_defaults(self, db_session):
        proj = Project(id="p2", name="Test", owner_id="u1")
        db_session.add(proj)
        db_session.commit()
        fetched = db_session.execute(select(Project).where(Project.id == "p2")).scalar_one()
        assert fetched.slug is None
        assert fetched.is_public is False


# ---------------------------------------------------------------------------
# ProjectAccess model
# ---------------------------------------------------------------------------
class TestProjectAccessModel:
    def test_create_access(self, db_session):
        access = ProjectAccess(
            id="acc-1",
            user_id="u1",
            project_id="proj-1",
            project_name="Test Project",
            role="owner",
        )
        db_session.add(access)
        db_session.commit()
        fetched = db_session.execute(select(ProjectAccess).where(ProjectAccess.id == "acc-1")).scalar_one()
        assert fetched.role == "owner"


# ---------------------------------------------------------------------------
# Conversation + ConversationMessage
# ---------------------------------------------------------------------------
class TestConversationModel:
    def test_create_conversation_with_messages(self, db_session):
        conv = Conversation(
            id="conv-1",
            project_id="proj-1",
            user_id="u1",
            title="ENCODE discussion",
        )
        db_session.add(conv)
        db_session.commit()

        msg1 = ConversationMessage(
            id="msg-1", conversation_id="conv-1",
            role="user", content="Search K562", seq=1,
        )
        msg2 = ConversationMessage(
            id="msg-2", conversation_id="conv-1",
            role="assistant", content="Found 150 experiments",
            seq=2, prompt_tokens=50, completion_tokens=100, total_tokens=150,
            model_name="qwen2.5:72b",
        )
        db_session.add_all([msg1, msg2])
        db_session.commit()

        messages = db_session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == "conv-1")
            .order_by(ConversationMessage.seq)
        ).scalars().all()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].total_tokens == 150

    def test_message_token_tracking_optional(self, db_session):
        msg = ConversationMessage(
            id="msg-no-tokens", conversation_id="conv-x",
            role="user", content="hello", seq=1,
        )
        db_session.add(msg)
        db_session.commit()
        fetched = db_session.execute(
            select(ConversationMessage).where(ConversationMessage.id == "msg-no-tokens")
        ).scalar_one()
        assert fetched.prompt_tokens is None
        assert fetched.model_name is None


# ---------------------------------------------------------------------------
# JobResult model
# ---------------------------------------------------------------------------
class TestJobResultModel:
    def test_create_job_result(self, db_session):
        jr = JobResult(
            id="jr-1",
            conversation_id="conv-1",
            run_uuid="run-uuid-123",
            sample_name="C2C12r1",
            workflow_type="DNA",
            status="COMPLETED",
        )
        db_session.add(jr)
        db_session.commit()
        fetched = db_session.execute(
            select(JobResult).where(JobResult.run_uuid == "run-uuid-123")
        ).scalar_one()
        assert fetched.sample_name == "C2C12r1"
        assert fetched.workflow_type == "DNA"


# ---------------------------------------------------------------------------
# ProjectBlock model
# ---------------------------------------------------------------------------
class TestProjectBlockModel:
    def test_create_block(self, db_session):
        block = ProjectBlock(
            id="block-1",
            project_id="proj-1",
            owner_id="u1",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({"markdown": "Hello"}),
        )
        db_session.add(block)
        db_session.commit()
        fetched = db_session.execute(
            select(ProjectBlock).where(ProjectBlock.id == "block-1")
        ).scalar_one()
        assert fetched.type == "AGENT_PLAN"
        payload = json.loads(fetched.payload_json)
        assert payload["markdown"] == "Hello"

    def test_block_ordering(self, db_session):
        for i in range(5):
            block = ProjectBlock(
                id=f"block-{i}",
                project_id="proj-1",
                owner_id="u1",
                seq=i,
                type="AGENT_PLAN",
                status="DONE",
                payload_json="{}",
            )
            db_session.add(block)
        db_session.commit()
        blocks = db_session.execute(
            select(ProjectBlock)
            .where(ProjectBlock.project_id == "proj-1")
            .order_by(ProjectBlock.seq)
        ).scalars().all()
        assert [b.seq for b in blocks] == [0, 1, 2, 3, 4]

    def test_block_parent_id(self, db_session):
        parent = ProjectBlock(
            id="parent-1", project_id="p1", owner_id="u1",
            seq=1, type="AGENT_PLAN", status="DONE", payload_json="{}",
        )
        child = ProjectBlock(
            id="child-1", project_id="p1", owner_id="u1",
            seq=2, type="EXECUTION_JOB", status="RUNNING",
            payload_json="{}", parent_id="parent-1",
        )
        db_session.add_all([parent, child])
        db_session.commit()
        fetched = db_session.execute(
            select(ProjectBlock).where(ProjectBlock.id == "child-1")
        ).scalar_one()
        assert fetched.parent_id == "parent-1"
