"""
Tests for Pydantic schemas in cortex/schemas.py.

Covers: BlockCreate, BlockOut, BlockStreamOut, BlockUpdate
"""

import pytest
from pydantic import ValidationError
from cortex.schemas import BlockCreate, BlockOut, BlockStreamOut, BlockUpdate


class TestBlockCreate:
    def test_valid(self):
        b = BlockCreate(project_id="proj1", type="AGENT_PLAN", payload={"text": "hi"})
        assert b.project_id == "proj1"
        assert b.status == "NEW"

    def test_empty_project_id_rejected(self):
        with pytest.raises(ValidationError):
            BlockCreate(project_id="", type="AGENT_PLAN", payload={})

    def test_empty_type_rejected(self):
        with pytest.raises(ValidationError):
            BlockCreate(project_id="proj1", type="", payload={})

    def test_optional_parent_id(self):
        b = BlockCreate(project_id="proj1", type="AGENT_PLAN", payload={}, parent_id="p1")
        assert b.parent_id == "p1"

    def test_default_parent_id_none(self):
        b = BlockCreate(project_id="proj1", type="AGENT_PLAN", payload={})
        assert b.parent_id is None


class TestBlockOut:
    def test_valid(self):
        b = BlockOut(
            id="id1", project_id="proj1", seq=1, type="AGENT_PLAN",
            status="DONE", payload={}, created_at="2026-01-01T00:00:00"
        )
        assert b.seq == 1

    def test_serialization(self):
        b = BlockOut(
            id="id1", project_id="proj1", seq=1, type="AGENT_PLAN",
            status="DONE", payload={"key": "val"}, created_at="2026-01-01"
        )
        d = b.model_dump()
        assert d["payload"] == {"key": "val"}


class TestBlockStreamOut:
    def test_valid(self):
        block = BlockOut(
            id="id1", project_id="proj1", seq=1, type="AGENT_PLAN",
            status="DONE", payload={}, created_at="2026-01-01"
        )
        stream = BlockStreamOut(blocks=[block], latest_seq=1)
        assert stream.latest_seq == 1
        assert len(stream.blocks) == 1

    def test_empty_blocks(self):
        stream = BlockStreamOut(blocks=[], latest_seq=0)
        assert stream.blocks == []


class TestBlockUpdate:
    def test_status_only(self):
        u = BlockUpdate(status="DONE")
        assert u.status == "DONE"
        assert u.payload is None

    def test_payload_only(self):
        u = BlockUpdate(payload={"new": "data"})
        assert u.payload == {"new": "data"}
        assert u.status is None

    def test_both(self):
        u = BlockUpdate(status="FAILED", payload={"error": "timeout"})
        assert u.status == "FAILED"
