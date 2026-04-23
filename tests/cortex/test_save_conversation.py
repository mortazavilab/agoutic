"""
Tests for cortex/app.py — save_conversation_message helper.

Covers:
  - Creating a new conversation when none exists
  - Appending to an existing conversation
  - Sequence numbering (auto-increment)
  - Token tracking (prompt/completion/total)
  - Model name storage
  - Conversation timestamp update
"""

import datetime
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import Conversation, ConversationMessage, User, Project
from cortex.app import save_conversation_message


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def session(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    sess = Session()
    # Seed minimal user and project (FK targets)
    user = User(id="u1", email="t@t.com", role="user", username="tu", is_active=True)
    proj = Project(id="p1", name="P", owner_id="u1")
    sess.add_all([user, proj])
    sess.commit()
    yield sess
    sess.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSaveConversationMessage:

    @pytest.mark.asyncio
    async def test_creates_conversation_if_none(self, session):
        await save_conversation_message(session, "p1", "u1", "user", "Hello!")

        conv = session.execute(
            select(Conversation).where(Conversation.project_id == "p1")
        ).scalar_one()
        assert conv.user_id == "u1"
        assert conv.title == "Hello!"  # first user message → title

    @pytest.mark.asyncio
    async def test_message_saved(self, session):
        await save_conversation_message(session, "p1", "u1", "user", "Hello!")

        msgs = session.execute(
            select(ConversationMessage)
        ).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert msgs[0].content == "Hello!"
        assert msgs[0].seq == 0

    @pytest.mark.asyncio
    async def test_assistant_message_title(self, session):
        """Assistant-first conversation gets generic title."""
        await save_conversation_message(session, "p1", "u1", "assistant", "Sure!")

        conv = session.execute(
            select(Conversation).where(Conversation.project_id == "p1")
        ).scalar_one()
        assert conv.title == "New Conversation"

    @pytest.mark.asyncio
    async def test_appends_to_existing(self, session):
        """Second call appends to the same conversation."""
        await save_conversation_message(session, "p1", "u1", "user", "First")
        await save_conversation_message(session, "p1", "u1", "assistant", "Reply")

        convs = session.execute(select(Conversation)).scalars().all()
        assert len(convs) == 1

        msgs = session.execute(
            select(ConversationMessage).order_by(ConversationMessage.seq)
        ).scalars().all()
        assert len(msgs) == 2
        assert msgs[0].seq == 0
        assert msgs[1].seq == 1

    @pytest.mark.asyncio
    async def test_sequence_increments(self, session):
        """Three messages get seq 0, 1, 2."""
        for i in range(3):
            role = "user" if i % 2 == 0 else "assistant"
            await save_conversation_message(session, "p1", "u1", role, f"msg {i}")

        msgs = session.execute(
            select(ConversationMessage).order_by(ConversationMessage.seq)
        ).scalars().all()
        assert [m.seq for m in msgs] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_token_data_stored(self, session):
        token_data = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        await save_conversation_message(
            session, "p1", "u1", "assistant", "Analysis...",
            token_data=token_data, model_name="devstral-2",
        )

        msg = session.execute(select(ConversationMessage)).scalar_one()
        assert msg.prompt_tokens == 100
        assert msg.completion_tokens == 50
        assert msg.total_tokens == 150
        assert msg.model_name == "devstral-2"

    @pytest.mark.asyncio
    async def test_no_token_data(self, session):
        """Without token_data, token fields are None."""
        await save_conversation_message(session, "p1", "u1", "user", "Hi")

        msg = session.execute(select(ConversationMessage)).scalar_one()
        assert msg.prompt_tokens is None
        assert msg.total_tokens is None
        assert msg.model_name is None

    @pytest.mark.asyncio
    async def test_conversation_updated_at(self, session):
        """Appending a message updates the conversation's updated_at."""
        await save_conversation_message(session, "p1", "u1", "user", "First")

        conv = session.execute(select(Conversation)).scalar_one()
        ts1 = conv.updated_at

        await save_conversation_message(session, "p1", "u1", "assistant", "Second")

        session.expire_all()
        conv = session.execute(select(Conversation)).scalar_one()
        assert conv.updated_at >= ts1

    @pytest.mark.asyncio
    async def test_long_title_truncated(self, session):
        """Title is truncated to 100 chars."""
        long_msg = "x" * 200
        await save_conversation_message(session, "p1", "u1", "user", long_msg)

        conv = session.execute(select(Conversation)).scalar_one()
        assert len(conv.title) == 100
