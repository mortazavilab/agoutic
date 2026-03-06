"""
Tests for cortex/db.py — row_to_dict() and next_seq_sync().
"""

import json
import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex.models import Base, ProjectBlock
from cortex.db import row_to_dict, next_seq_sync


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


class TestRowToDict:
    def test_basic_conversion(self, db):
        b = ProjectBlock(
            id="blk-1",
            project_id="proj-1",
            owner_id="user-1",
            seq=1,
            type="USER_MESSAGE",
            status="DONE",
            payload_json=json.dumps({"text": "hello"}),
            parent_id=None,
            created_at=datetime.datetime(2025, 1, 1, 12, 0, 0),
        )
        db.add(b)
        db.commit()
        d = row_to_dict(b)
        assert d["id"] == "blk-1"
        assert d["project_id"] == "proj-1"
        assert d["seq"] == 1
        assert d["type"] == "USER_MESSAGE"
        assert d["status"] == "DONE"
        assert d["payload"] == {"text": "hello"}
        assert d["parent_id"] is None
        assert "2025" in d["created_at"]

    def test_payload_is_parsed_json(self, db):
        payload = {"markdown": "# Title", "skill": "welcome", "nested": {"key": [1, 2]}}
        b = ProjectBlock(
            id="blk-2", project_id="proj-1", owner_id="user-1", seq=2,
            type="AGENT_PLAN", status="NEW",
            payload_json=json.dumps(payload),
        )
        db.add(b)
        db.commit()
        d = row_to_dict(b)
        assert d["payload"]["nested"]["key"] == [1, 2]


class TestNextSeqSync:
    def test_first_block_is_1(self, db):
        seq = next_seq_sync(db, "proj-empty")
        assert seq == 1

    def test_increments(self, db):
        for i in range(1, 4):
            b = ProjectBlock(
                id=f"blk-{i}", project_id="proj-1", owner_id="user-1", seq=i,
                type="USER_MESSAGE", status="DONE",
                payload_json="{}",
            )
            db.add(b)
        db.commit()
        seq = next_seq_sync(db, "proj-1")
        assert seq == 4

    def test_isolated_per_project(self, db):
        b = ProjectBlock(
            id="blk-a", project_id="proj-a", owner_id="user-1", seq=10,
            type="USER_MESSAGE", status="DONE", payload_json="{}",
        )
        db.add(b)
        db.commit()
        # proj-b should start at 1, not 11
        seq = next_seq_sync(db, "proj-b")
        assert seq == 1
