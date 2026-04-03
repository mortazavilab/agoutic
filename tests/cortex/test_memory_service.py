"""Tests for cortex/memory_service.py — CRUD, querying, auto-capture, annotations."""

import json
import uuid

import pytest

from cortex.memory_service import (
    create_memory,
    delete_memory,
    restore_memory,
    pin_memory,
    update_memory,
    list_memories,
    search_memories,
    find_memory_by_content,
    get_memory_context,
    auto_capture_step,
    auto_capture_result,
    auto_capture_plot,
    annotate_sample,
    soft_delete_project_memories,
    memory_to_dict,
    VALID_CATEGORIES,
)
from cortex.models import Memory, UserFile


@pytest.fixture()
def user_id():
    return str(uuid.uuid4())


@pytest.fixture()
def project_id():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateMemory:
    def test_basic_create(self, db_session, user_id, project_id):
        mem = create_memory(
            db_session, user_id=user_id, content="test note",
            project_id=project_id,
        )
        assert mem.id
        assert mem.content == "test note"
        assert mem.user_id == user_id
        assert mem.project_id == project_id
        assert mem.category == "custom"
        assert mem.source == "user_manual"
        assert not mem.is_pinned
        assert not mem.is_deleted

    def test_create_global(self, db_session, user_id):
        mem = create_memory(
            db_session, user_id=user_id, content="global pref",
            project_id=None, category="preference",
        )
        assert mem.project_id is None
        assert mem.category == "preference"

    def test_create_with_structured_data(self, db_session, user_id):
        data = {"sample_name": "sample1", "condition": "AD"}
        mem = create_memory(
            db_session, user_id=user_id, content="sample1 is AD",
            structured_data=data,
        )
        assert json.loads(mem.structured_data) == data

    def test_invalid_category_defaults_to_custom(self, db_session, user_id):
        mem = create_memory(
            db_session, user_id=user_id, content="test",
            category="nonexistent",
        )
        assert mem.category == "custom"

    def test_create_pinned(self, db_session, user_id):
        mem = create_memory(
            db_session, user_id=user_id, content="important",
            is_pinned=True,
        )
        assert mem.is_pinned

    def test_create_plot_category(self, db_session, user_id, project_id):
        mem = create_memory(
            db_session, user_id=user_id, content="plot note",
            category="plot", project_id=project_id,
        )
        assert mem.category == "plot"


class TestDeleteMemory:
    def test_soft_delete(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="deleteme")
        ok = delete_memory(db_session, mem.id, user_id)
        assert ok
        db_session.refresh(mem)
        assert mem.is_deleted
        assert mem.deleted_at is not None

    def test_delete_nonexistent(self, db_session, user_id):
        ok = delete_memory(db_session, "nonexistent-id", user_id)
        assert not ok

    def test_delete_wrong_user(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="mine")
        ok = delete_memory(db_session, mem.id, "other-user")
        assert not ok


class TestRestoreMemory:
    def test_restore(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="restore me")
        delete_memory(db_session, mem.id, user_id)
        ok = restore_memory(db_session, mem.id, user_id)
        assert ok
        db_session.refresh(mem)
        assert not mem.is_deleted
        assert mem.deleted_at is None


class TestPinMemory:
    def test_pin_unpin(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="pin me")
        assert not mem.is_pinned
        pin_memory(db_session, mem.id, user_id, pinned=True)
        db_session.refresh(mem)
        assert mem.is_pinned
        pin_memory(db_session, mem.id, user_id, pinned=False)
        db_session.refresh(mem)
        assert not mem.is_pinned


class TestUpdateMemory:
    def test_update_content(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="old")
        updated = update_memory(db_session, mem.id, user_id, content="new")
        assert updated.content == "new"

    def test_update_tags(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="tagged")
        updated = update_memory(db_session, mem.id, user_id, tags={"key": "val"})
        assert json.loads(updated.tags_json) == {"key": "val"}

    def test_update_deleted_returns_none(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="deleted")
        delete_memory(db_session, mem.id, user_id)
        result = update_memory(db_session, mem.id, user_id, content="nope")
        assert result is None


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


class TestListMemories:
    def test_list_project_and_global(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="proj mem", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="global mem", project_id=None)
        mems = list_memories(db_session, user_id, project_id=project_id, include_global=True)
        assert len(mems) == 2

    def test_list_project_only(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="proj", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="global", project_id=None)
        mems = list_memories(db_session, user_id, project_id=project_id, include_global=False)
        assert len(mems) == 1
        assert mems[0].content == "proj"

    def test_excludes_deleted(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="deleted", project_id=project_id)
        delete_memory(db_session, mem.id, user_id)
        mems = list_memories(db_session, user_id, project_id=project_id)
        assert len(mems) == 0

    def test_include_deleted(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="deleted", project_id=project_id)
        delete_memory(db_session, mem.id, user_id)
        mems = list_memories(db_session, user_id, project_id=project_id, include_deleted=True)
        assert len(mems) == 1

    def test_filter_by_category(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="r1", project_id=project_id, category="result")
        create_memory(db_session, user_id=user_id, content="p1", project_id=project_id, category="preference")
        mems = list_memories(db_session, user_id, project_id=project_id, category="result")
        assert len(mems) == 1
        assert mems[0].content == "r1"

    def test_pinned_first(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="unpinned", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="pinned", project_id=project_id, is_pinned=True)
        mems = list_memories(db_session, user_id, project_id=project_id)
        assert mems[0].content == "pinned"


class TestSearchMemories:
    def test_search_by_content(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="sample1 is AD", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="sample2 is control", project_id=project_id)
        results = search_memories(db_session, user_id, "AD", project_id=project_id)
        assert len(results) == 1
        assert "AD" in results[0].content


# ---------------------------------------------------------------------------
# Memory context
# ---------------------------------------------------------------------------


class TestGetMemoryContext:
    def test_empty(self, db_session, user_id, project_id):
        ctx = get_memory_context(db_session, user_id, project_id)
        assert ctx == ""

    def test_formats_project_and_global(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="proj note", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="global pref", project_id=None, category="preference")
        ctx = get_memory_context(db_session, user_id, project_id)
        assert "[MEMORY: Project notes]" in ctx
        assert "[MEMORY: User preferences]" in ctx
        assert "proj note" in ctx
        assert "global pref" in ctx

    def test_pinned_starred(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="important", project_id=project_id, is_pinned=True)
        ctx = get_memory_context(db_session, user_id, project_id)
        assert "⭐ important" in ctx


# ---------------------------------------------------------------------------
# Auto-capture
# ---------------------------------------------------------------------------


class TestAutoCapture:
    def test_auto_capture_step(self, db_session, user_id, project_id):
        step = {"id": "step1", "title": "Align reads", "kind": "RUN_SCRIPT"}
        plan = {"sample_name": "sample1", "workflow_type": "DNA"}
        mem = auto_capture_step(
            db_session, user_id=user_id, project_id=project_id,
            step=step, plan_payload=plan, block_id="block1",
        )
        assert mem is not None
        assert "Align reads" in mem.content
        assert mem.category == "pipeline_step"
        assert mem.source == "auto_step"

    def test_auto_capture_step_dedup(self, db_session, user_id, project_id):
        step = {"id": "step1", "title": "Align reads", "kind": "RUN_SCRIPT"}
        plan = {"sample_name": "sample1", "workflow_type": "DNA"}
        mem1 = auto_capture_step(
            db_session, user_id=user_id, project_id=project_id,
            step=step, plan_payload=plan, block_id="block1",
        )
        mem2 = auto_capture_step(
            db_session, user_id=user_id, project_id=project_id,
            step=step, plan_payload=plan, block_id="block1",
        )
        assert mem1.id == mem2.id  # Deduplication

    def test_auto_capture_result(self, db_session, user_id, project_id):
        mem = auto_capture_result(
            db_session, user_id=user_id, project_id=project_id,
            run_uuid="run-123", sample_name="K562",
            workflow_type="RNA", work_directory="/tmp/output",
            block_id="block2",
        )
        assert mem is not None
        assert "K562" in mem.content
        assert mem.category == "result"
        assert mem.is_pinned  # Auto-pinned

    def test_auto_capture_result_dedup(self, db_session, user_id, project_id):
        args = dict(
            user_id=user_id, project_id=project_id,
            run_uuid="run-123", sample_name="K562",
        )
        mem1 = auto_capture_result(db_session, **args)
        mem2 = auto_capture_result(db_session, **args)
        assert mem1.id == mem2.id

    def test_auto_capture_plot_creates_project_memory(self, db_session, user_id, project_id):
        charts = [{
            "type": "bar",
            "df_id": 1,
            "x": "sample",
            "y": "reads",
            "title": "Reads by Sample",
            "ylabel": "Reads",
        }]
        mem = auto_capture_plot(
            db_session,
            user_id=user_id,
            project_id=project_id,
            charts=charts,
            block_id="plot-block-1",
        )
        assert mem is not None
        assert mem.category == "plot"
        assert mem.project_id == project_id
        assert mem.related_block_id == "plot-block-1"
        assert mem.source == "system"
        assert "Reads by Sample" in mem.content
        assert json.loads(mem.structured_data)["charts"] == charts

    def test_auto_capture_plot_dedups_by_block_id_only(self, db_session, user_id, project_id):
        charts = [{"type": "bar", "df_id": 1, "x": "sample", "y": "reads"}]
        mem1 = auto_capture_plot(
            db_session,
            user_id=user_id,
            project_id=project_id,
            charts=charts,
            block_id="plot-block-1",
        )
        mem2 = auto_capture_plot(
            db_session,
            user_id=user_id,
            project_id=project_id,
            charts=charts,
            block_id="plot-block-1",
        )
        mem3 = auto_capture_plot(
            db_session,
            user_id=user_id,
            project_id=project_id,
            charts=charts,
            block_id="plot-block-2",
        )
        assert mem1.id == mem2.id
        assert mem3.id != mem1.id


# ---------------------------------------------------------------------------
# Sample annotation
# ---------------------------------------------------------------------------


class TestAnnotateSample:
    def test_annotate_with_existing_file(self, db_session, user_id, project_id):
        # Create a UserFile
        uf = UserFile(
            id=str(uuid.uuid4()), user_id=user_id,
            filename="sample1.bam", source="upload",
            sample_name="sample1", disk_path="/tmp/sample1.bam",
        )
        db_session.add(uf)
        db_session.commit()

        mem = annotate_sample(
            db_session, user_id=user_id, sample_name="sample1",
            annotations={"condition": "AD", "replicate": "1"},
            project_id=project_id,
        )
        assert mem is not None
        assert mem.category == "sample_annotation"
        assert mem.related_file_id == uf.id

        # Verify tags_json updated on UserFile
        db_session.refresh(uf)
        tags = json.loads(uf.tags_json)
        assert tags["condition"] == "AD"
        assert tags["replicate"] == "1"

    def test_annotate_without_file(self, db_session, user_id, project_id):
        # No matching file — still creates memory
        mem = annotate_sample(
            db_session, user_id=user_id, sample_name="missing_sample",
            annotations={"condition": "control"},
            project_id=project_id,
        )
        assert mem is not None
        assert mem.related_file_id is None

    def test_annotate_merges_tags(self, db_session, user_id, project_id):
        uf = UserFile(
            id=str(uuid.uuid4()), user_id=user_id,
            filename="sample2.bam", source="upload",
            sample_name="sample2", disk_path="/tmp/sample2.bam",
            tags_json=json.dumps({"existing": "value"}),
        )
        db_session.add(uf)
        db_session.commit()

        annotate_sample(
            db_session, user_id=user_id, sample_name="sample2",
            annotations={"condition": "treated"},
            project_id=project_id,
        )
        db_session.refresh(uf)
        tags = json.loads(uf.tags_json)
        assert tags["existing"] == "value"
        assert tags["condition"] == "treated"


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


class TestSoftDeleteProjectMemories:
    def test_cascade_soft_delete(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="m1", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="m2", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="global", project_id=None)
        count = soft_delete_project_memories(db_session, project_id)
        assert count == 2
        # Global unaffected
        mems = list_memories(db_session, user_id, project_id=None)
        assert len(mems) == 1


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestMemoryToDict:
    def test_serialization(self, db_session, user_id):
        mem = create_memory(
            db_session, user_id=user_id, content="test",
            structured_data={"k": "v"}, tags={"t": "1"},
        )
        d = memory_to_dict(mem)
        assert d["id"] == mem.id
        assert d["content"] == "test"
        assert d["structured_data"] == {"k": "v"}
        assert d["tags"] == {"t": "1"}
