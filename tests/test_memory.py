"""Tests for the memory system — CRUD, slash commands, dataframe memories, and DF integration."""
import json
import uuid

import pytest

from cortex.memory_service import (
    VALID_CATEGORIES,
    create_memory,
    delete_memory,
    restore_memory,
    pin_memory,
    update_memory,
    upgrade_to_global,
    remember_dataframe,
    list_memories,
    search_memories,
    get_remembered_df_map,
    _REMEMBERED_DF_ID_OFFSET,
)
from cortex.memory_commands import (
    parse_memory_command,
    execute_memory_command,
)
from cortex.chat_sync_handler import (
    _detect_df_command,
    _collect_df_map,
    _render_list_dfs,
    _render_head_df,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture()
def user_id():
    return str(uuid.uuid4())


@pytest.fixture()
def project_id():
    return str(uuid.uuid4())


# =====================================================================
# Memory Service — basic CRUD
# =====================================================================

class TestMemoryServiceCRUD:
    def test_create_memory(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="test note", project_id=project_id)
        assert mem.id
        assert mem.content == "test note"
        assert mem.project_id == project_id
        assert mem.category == "custom"
        assert mem.source == "user_manual"
        assert not mem.is_deleted
        assert not mem.is_pinned

    def test_create_global_memory(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="global note", project_id=None)
        assert mem.project_id is None

    def test_dedup_by_content(self, db_session, user_id, project_id):
        m1 = create_memory(db_session, user_id=user_id, content="same text", project_id=project_id)
        m2 = create_memory(db_session, user_id=user_id, content="same text", project_id=project_id)
        assert m1.id == m2.id

    def test_dedup_strips_whitespace(self, db_session, user_id, project_id):
        m1 = create_memory(db_session, user_id=user_id, content="  hello  ", project_id=project_id)
        m2 = create_memory(db_session, user_id=user_id, content="hello", project_id=project_id)
        assert m1.id == m2.id

    def test_soft_delete(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="deleteme", project_id=project_id)
        ok = delete_memory(db_session, mem.id, user_id)
        assert ok
        db_session.refresh(mem)
        assert mem.is_deleted
        assert mem.deleted_at is not None

    def test_restore(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="restore me", project_id=project_id)
        delete_memory(db_session, mem.id, user_id)
        ok = restore_memory(db_session, mem.id, user_id)
        assert ok
        db_session.refresh(mem)
        assert not mem.is_deleted

    def test_pin_unpin(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="pin me", project_id=project_id)
        assert not mem.is_pinned
        pin_memory(db_session, mem.id, user_id, pinned=True)
        db_session.refresh(mem)
        assert mem.is_pinned
        pin_memory(db_session, mem.id, user_id, pinned=False)
        db_session.refresh(mem)
        assert not mem.is_pinned

    def test_update_content(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="original", project_id=project_id)
        updated = update_memory(db_session, mem.id, user_id, content="modified")
        assert updated.content == "modified"

    def test_update_tags(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="tagged", project_id=project_id)
        updated = update_memory(db_session, mem.id, user_id, tags={"key": "val"})
        assert json.loads(updated.tags_json) == {"key": "val"}

    def test_delete_wrong_user(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="mine", project_id=project_id)
        ok = delete_memory(db_session, mem.id, "other-user")
        assert not ok

    def test_invalid_category_defaults_to_custom(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="x", category="bogus")
        assert mem.category == "custom"


# =====================================================================
# Memory Service — listing & search
# =====================================================================

class TestMemoryServiceQuery:
    def test_list_project_and_global(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="proj mem", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="global mem", project_id=None)
        mems = list_memories(db_session, user_id, project_id=project_id, include_global=True)
        contents = {m.content for m in mems}
        assert "proj mem" in contents
        assert "global mem" in contents

    def test_list_project_only(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="proj only", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="global only", project_id=None)
        mems = list_memories(db_session, user_id, project_id=project_id, include_global=False)
        contents = {m.content for m in mems}
        assert "proj only" in contents
        assert "global only" not in contents

    def test_list_excludes_deleted(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="deleted", project_id=project_id)
        delete_memory(db_session, mem.id, user_id)
        mems = list_memories(db_session, user_id, project_id=project_id, include_deleted=False)
        assert all(m.content != "deleted" for m in mems)

    def test_list_includes_deleted(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="deleted2", project_id=project_id)
        delete_memory(db_session, mem.id, user_id)
        mems = list_memories(db_session, user_id, project_id=project_id, include_deleted=True)
        assert any(m.content == "deleted2" for m in mems)

    def test_list_pinned_only(self, db_session, user_id, project_id):
        m1 = create_memory(db_session, user_id=user_id, content="pinned1", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="unpinned", project_id=project_id)
        pin_memory(db_session, m1.id, user_id, pinned=True)
        mems = list_memories(db_session, user_id, project_id=project_id, pinned_only=True)
        assert len(mems) == 1
        assert mems[0].content == "pinned1"

    def test_search_memories(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="K562 is a cell line", project_id=project_id)
        create_memory(db_session, user_id=user_id, content="unrelated note", project_id=project_id)
        results = search_memories(db_session, user_id, "K562", project_id=project_id)
        assert len(results) == 1
        assert "K562" in results[0].content

    def test_list_by_category(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="a finding", project_id=project_id, category="finding")
        create_memory(db_session, user_id=user_id, content="a note", project_id=project_id, category="custom")
        mems = list_memories(db_session, user_id, project_id=project_id, category="finding")
        assert all(m.category == "finding" for m in mems)
        assert len(mems) == 1


# =====================================================================
# Memory Service — upgrade to global
# =====================================================================

class TestUpgradeToGlobal:
    def test_upgrade_project_memory(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="promote me", project_id=project_id)
        result = upgrade_to_global(db_session, mem.id, user_id)
        assert result is not None
        assert result.project_id is None

    def test_upgrade_already_global(self, db_session, user_id):
        mem = create_memory(db_session, user_id=user_id, content="already global", project_id=None)
        result = upgrade_to_global(db_session, mem.id, user_id)
        assert result is not None
        assert result.project_id is None

    def test_upgrade_unnamed_df_blocked(self, db_session, user_id, project_id):
        """Unnamed dataframe memories cannot be upgraded to global."""
        mem = remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=project_id,
            df_id=1,
            df_data=_sample_df_data(),
            df_name=None,
        )
        result = upgrade_to_global(db_session, mem.id, user_id)
        assert result is None  # Should be blocked

    def test_upgrade_named_df_allowed(self, db_session, user_id, project_id):
        """Named dataframe memories can be upgraded to global."""
        mem = remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=project_id,
            df_id=5,
            df_data=_sample_df_data(),
            df_name="c2c12DF",
        )
        result = upgrade_to_global(db_session, mem.id, user_id)
        assert result is not None
        assert result.project_id is None

    def test_upgrade_wrong_user(self, db_session, user_id, project_id):
        mem = create_memory(db_session, user_id=user_id, content="not yours", project_id=project_id)
        result = upgrade_to_global(db_session, mem.id, "other-user")
        assert result is None


# =====================================================================
# Memory Service — dataframe memories
# =====================================================================

class TestRememberDataframe:
    def test_remember_unnamed_df(self, db_session, user_id, project_id):
        mem = remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=project_id,
            df_id=3,
            df_data=_sample_df_data(),
        )
        assert mem.category == "dataframe"
        assert "DF3" in mem.content
        sd = json.loads(mem.structured_data)
        assert sd["df_id"] == 3
        assert sd["columns"] == ["gene", "score"]
        assert len(sd["data"]) == 2
        tags = json.loads(mem.tags_json)
        assert tags["df_id"] == 3
        assert "df_name" not in tags

    def test_remember_named_df(self, db_session, user_id, project_id):
        mem = remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=project_id,
            df_id=5,
            df_data=_sample_df_data(),
            df_name="c2c12DF",
        )
        assert "c2c12DF" in mem.content
        tags = json.loads(mem.tags_json)
        assert tags["df_name"] == "c2c12DF"

    def test_dataframe_category_is_valid(self):
        assert "dataframe" in VALID_CATEGORIES

    def test_get_remembered_df_map(self, db_session, user_id, project_id):
        remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=project_id,
            df_id=1,
            df_data=_sample_df_data(),
            df_name="myDF",
        )
        df_map = get_remembered_df_map(db_session, user_id, project_id)
        assert len(df_map) == 1
        # Named DFs use their name as the key
        assert "myDF" in df_map
        entry = df_map["myDF"]
        assert entry["columns"] == ["gene", "score"]
        assert "myDF" in entry["label"]
        assert entry["row_count"] == 2

    def test_remembered_df_map_includes_global(self, db_session, user_id, project_id):
        remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=project_id,
            df_id=1,
            df_data=_sample_df_data(),
            df_name="projDF",
        )
        remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=None,
            df_id=2,
            df_data=_sample_df_data(),
            df_name="globalDF",
        )
        df_map = get_remembered_df_map(db_session, user_id, project_id)
        assert len(df_map) == 2
        assert "projDF" in df_map
        assert "globalDF" in df_map

    def test_unnamed_df_gets_numeric_key(self, db_session, user_id, project_id):
        """Unnamed remembered DFs still get an integer (900+) key."""
        remember_dataframe(
            db_session,
            user_id=user_id,
            project_id=project_id,
            df_id=3,
            df_data=_sample_df_data(),
            df_name=None,
        )
        df_map = get_remembered_df_map(db_session, user_id, project_id)
        assert len(df_map) == 1
        key = next(iter(df_map))
        assert isinstance(key, int)
        assert key >= _REMEMBERED_DF_ID_OFFSET


# =====================================================================
# Memory Commands — slash command parsing
# =====================================================================

class TestMemoryCommandParsing:
    def test_remember(self):
        cmd = parse_memory_command("/remember k562 is a cell line")
        assert cmd is not None
        assert cmd.action == "remember"
        assert cmd.text == "k562 is a cell line"

    def test_remember_global(self):
        cmd = parse_memory_command("/remember-global always use hg38")
        assert cmd is not None
        assert cmd.action == "remember_global"
        assert cmd.text == "always use hg38"

    def test_forget_by_text(self):
        cmd = parse_memory_command("/forget k562 note")
        assert cmd is not None
        assert cmd.action == "forget"
        assert cmd.text == "k562 note"

    def test_forget_by_id(self):
        cmd = parse_memory_command("/forget #abc12345")
        assert cmd is not None
        assert cmd.action == "forget"
        assert cmd.memory_id == "abc12345"

    def test_list(self):
        cmd = parse_memory_command("/memories")
        assert cmd is not None
        assert cmd.action == "list"
        assert not cmd.global_only

    def test_list_global(self):
        cmd = parse_memory_command("/memories --global")
        assert cmd is not None
        assert cmd.action == "list"
        assert cmd.global_only

    def test_pin(self):
        cmd = parse_memory_command("/pin #abc12345")
        assert cmd is not None
        assert cmd.action == "pin"
        assert cmd.memory_id == "abc12345"

    def test_unpin(self):
        cmd = parse_memory_command("/unpin abc12345")
        assert cmd is not None
        assert cmd.action == "unpin"
        assert cmd.memory_id == "abc12345"

    def test_restore(self):
        cmd = parse_memory_command("/restore abc12345")
        assert cmd is not None
        assert cmd.action == "restore"

    def test_search(self):
        cmd = parse_memory_command("/search-memories k562")
        assert cmd is not None
        assert cmd.action == "search"
        assert cmd.text == "k562"

    def test_annotate(self):
        cmd = parse_memory_command("/annotate sample1 condition=treated source=lab")
        assert cmd is not None
        assert cmd.action == "annotate"
        assert cmd.sample_name == "sample1"
        assert cmd.annotations == {"condition": "treated", "source": "lab"}

    def test_upgrade_to_global(self):
        cmd = parse_memory_command("/upgrade-to-global abc12345")
        assert cmd is not None
        assert cmd.action == "upgrade_global"
        assert cmd.memory_id == "abc12345"

    def test_make_global_alias(self):
        cmd = parse_memory_command("/make-global abc12345")
        assert cmd is not None
        assert cmd.action == "upgrade_global"

    def test_remember_df_unnamed(self):
        cmd = parse_memory_command("/remember-df 5")
        assert cmd is not None
        assert cmd.action == "remember_df"
        assert cmd.df_id == 5
        assert cmd.df_name is None

    def test_remember_df_with_prefix(self):
        cmd = parse_memory_command("/remember-df DF5")
        assert cmd is not None
        assert cmd.action == "remember_df"
        assert cmd.df_id == 5

    def test_remember_df_named(self):
        cmd = parse_memory_command("/remember-df DF5 as c2c12DF")
        assert cmd is not None
        assert cmd.action == "remember_df"
        assert cmd.df_id == 5
        assert cmd.df_name == "c2c12DF"

    def test_remember_df_named_without_as(self):
        cmd = parse_memory_command("/remember-df 3 myName")
        assert cmd is not None
        assert cmd.action == "remember_df"
        assert cmd.df_id == 3
        assert cmd.df_name == "myName"

    def test_not_a_command(self):
        cmd = parse_memory_command("just a normal message")
        assert cmd is None


# =====================================================================
# Memory Commands — execution
# =====================================================================

class TestMemoryCommandExecution:
    def test_execute_remember(self, db_session, user_id, project_id):
        cmd = parse_memory_command("/remember test note")
        resp = execute_memory_command(db_session, cmd, user_id, project_id)
        assert "✅" in resp
        assert "test note" in resp

    def test_execute_remember_global(self, db_session, user_id, project_id):
        cmd = parse_memory_command("/remember-global global pref")
        resp = execute_memory_command(db_session, cmd, user_id, project_id)
        assert "global" in resp.lower()

    def test_execute_list(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="listed mem", project_id=project_id)
        cmd = parse_memory_command("/memories")
        resp = execute_memory_command(db_session, cmd, user_id, project_id)
        assert "listed mem" in resp

    def test_execute_search(self, db_session, user_id, project_id):
        create_memory(db_session, user_id=user_id, content="findable item", project_id=project_id)
        cmd = parse_memory_command("/search-memories findable")
        resp = execute_memory_command(db_session, cmd, user_id, project_id)
        assert "findable" in resp

    def test_execute_upgrade_unnamed_df_blocked(self, db_session, user_id, project_id):
        mem = remember_dataframe(
            db_session, user_id=user_id, project_id=project_id,
            df_id=1, df_data=_sample_df_data(), df_name=None,
        )
        cmd = parse_memory_command(f"/upgrade-to-global {mem.id[:8]}")
        resp = execute_memory_command(db_session, cmd, user_id, project_id)
        assert "❌" in resp
        assert "named" in resp.lower() or "name" in resp.lower()


# =====================================================================
# DF Memory Integration — _collect_df_map with remembered DFs
# =====================================================================

class TestDFMemoryIntegration:
    def test_collect_df_map_includes_remembered(self, db_session, user_id, project_id, make_block):
        """Remembered named DFs appear in df_map with their name as key."""
        # Create a conversation DF in a block
        block = make_block(payload={
            "_dataframes": {
                "results.csv": {
                    "columns": ["a", "b"],
                    "data": [{"a": 1, "b": 2}],
                    "row_count": 1,
                    "metadata": {"df_id": 1, "label": "Results", "visible": True},
                }
            }
        })
        # Create a remembered named DF in the database
        remember_dataframe(
            db_session, user_id=user_id, project_id=project_id,
            df_id=5, df_data=_sample_df_data(), df_name="savedDF",
        )

        df_map = _collect_df_map(
            [block], db=db_session, user_id=user_id, project_id=project_id,
        )
        # Should have conv DF (id=1) + remembered DF (key="savedDF")
        assert 1 in df_map
        assert "savedDF" in df_map
        assert "savedDF" in df_map["savedDF"]["label"]

    def test_collect_df_map_no_db_is_conv_only(self, make_block):
        """Without db params, only conversation DFs are returned."""
        block = make_block(payload={
            "_dataframes": {
                "test.csv": {
                    "columns": ["x"],
                    "data": [{"x": 1}],
                    "row_count": 1,
                    "metadata": {"df_id": 1, "label": "Test", "visible": True},
                }
            }
        })
        df_map = _collect_df_map([block])
        assert len(df_map) == 1
        assert 1 in df_map

    def test_render_list_includes_remembered(self, db_session, user_id, project_id, make_block):
        """'list dfs' output should include remembered DFs."""
        remember_dataframe(
            db_session, user_id=user_id, project_id=project_id,
            df_id=3, df_data=_sample_df_data(), df_name="myTable",
        )
        df_map = _collect_df_map(
            [], db=db_session, user_id=user_id, project_id=project_id,
        )
        md = _render_list_dfs(df_map)
        assert "myTable" in md
        assert "gene" in md  # column name

    def test_render_head_remembered_df(self, db_session, user_id, project_id):
        """'head c2c12DF' works for remembered named DFs."""
        remember_dataframe(
            db_session, user_id=user_id, project_id=project_id,
            df_id=1, df_data=_sample_df_data(), df_name="headTest",
        )
        df_map = _collect_df_map(
            [], db=db_session, user_id=user_id, project_id=project_id,
        )
        assert "headTest" in df_map
        md = _render_head_df(df_map, "headTest", 10)
        assert "TP53" in md  # row data
        assert "gene" in md  # column header
        assert "headTest" in md  # name in header, not DF900

    def test_remember_df_command_execution(self, db_session, user_id, project_id, make_block):
        """/remember-df should save a DF from conversation history."""
        block = make_block(payload={
            "_dataframes": {
                "encode_files.tsv": {
                    "columns": ["accession", "file_type", "size"],
                    "data": [
                        {"accession": "ENCFF001", "file_type": "BAM", "size": 1000},
                        {"accession": "ENCFF002", "file_type": "BED", "size": 500},
                    ],
                    "row_count": 2,
                    "metadata": {"df_id": 1, "label": "K562 files", "visible": True},
                }
            }
        })
        cmd = parse_memory_command("/remember-df DF1 as k562files")
        resp = execute_memory_command(
            db_session, cmd, user_id, project_id,
            history_blocks=[block],
        )
        assert "✅" in resp
        assert "k562files" in resp

        # Verify it's now in the remembered map
        df_map = get_remembered_df_map(db_session, user_id, project_id)
        assert len(df_map) == 1
        entry = next(iter(df_map.values()))
        assert entry["columns"] == ["accession", "file_type", "size"]
        assert entry["row_count"] == 2

    def test_remember_df_not_found(self, db_session, user_id, project_id, make_block):
        """/remember-df with non-existent DF ID returns error."""
        block = make_block(payload={"_dataframes": {}})
        cmd = parse_memory_command("/remember-df DF99")
        resp = execute_memory_command(
            db_session, cmd, user_id, project_id,
            history_blocks=[block],
        )
        assert "❌" in resp
        assert "DF99" in resp

    def test_remember_df_no_history(self, db_session, user_id, project_id):
        """/remember-df without history_blocks returns error."""
        cmd = parse_memory_command("/remember-df DF1")
        resp = execute_memory_command(db_session, cmd, user_id, project_id)
        assert "❌" in resp


# =====================================================================
# DF inspection commands (existing functionality)
# =====================================================================

class TestDFInspectionCommands:
    def test_detect_list_dfs(self):
        assert _detect_df_command("list dfs") == {"action": "list"}
        assert _detect_df_command("show dataframes") == {"action": "list"}
        assert _detect_df_command("list data frames") == {"action": "list"}

    def test_detect_head(self):
        result = _detect_df_command("head df3")
        assert result is not None
        assert result["action"] == "head"
        assert result["df_id"] == 3

    def test_detect_head_default_n(self):
        result = _detect_df_command("head df1")
        assert result["n"] == 10

    def test_detect_head_named(self):
        result = _detect_df_command("head c2c12df")
        assert result is not None
        assert result["action"] == "head"
        assert result["df_id"] == "c2c12df"
        assert result["n"] == 10

    def test_detect_head_named_with_n(self):
        result = _detect_df_command("head mytable 20")
        assert result is not None
        assert result["df_id"] == "mytable"
        assert result["n"] == 20

    def test_detect_not_df_command(self):
        assert _detect_df_command("hello world") is None
        assert _detect_df_command("remember this df") is None

    def test_render_empty_map(self):
        md = _render_list_dfs({})
        assert "No dataframes" in md

    def test_render_head_missing_id(self):
        md = _render_head_df({1: {"columns": ["x"], "data": [], "row_count": 0, "label": "x"}}, 99, 10)
        assert "not found" in md.lower()

    def test_render_head_case_insensitive(self):
        df_map = {"MyTable": {"columns": ["x"], "data": [{"x": 1}], "row_count": 1, "label": "test"}}
        md = _render_head_df(df_map, "mytable", 10)
        assert "MyTable" in md  # Uses the canonical casing from the map


# =====================================================================
# Context injection — remembered DF data injection for LLM
# =====================================================================

from cortex.context_injection import _inject_job_context


class TestContextInjectionRememberedDF:
    """Verify that _inject_job_context injects remembered named DF data."""

    def _conv_history(self, user_msg: str) -> list:
        return [{"role": "user", "content": user_msg}]

    def test_named_df_injected_for_any_skill(self, db_session, user_id, project_id):
        """A named DF reference in any skill triggers data injection."""
        remember_dataframe(
            db_session, user_id=user_id, project_id=project_id,
            df_id=2, df_data=_sample_df_data(), df_name="c2c12DF",
        )
        aug, inj, debug = _inject_job_context(
            "plot c2c12DF by assay", "remote_execution",
            self._conv_history("plot c2c12DF by assay"),
            history_blocks=[],
            db=db_session, user_id=user_id, project_id=project_id,
        )
        assert "PREVIOUS QUERY DATA" in aug
        assert "c2c12DF" in aug
        assert "TP53" in aug  # row data injected
        assert "c2c12DF" in inj
        assert debug["source"] == "remembered_df_injection"

    def test_no_injection_without_named_df(self, db_session, user_id, project_id):
        """No injection when message doesn't reference a remembered DF."""
        aug, inj, debug = _inject_job_context(
            "hello world", "remote_execution",
            self._conv_history("hello world"),
            history_blocks=[],
            db=db_session, user_id=user_id, project_id=project_id,
        )
        assert "PREVIOUS QUERY DATA" not in aug
        assert not inj

    def test_dogme_skill_named_df_note(self, db_session, user_id, project_id, make_block):
        """Dogme skills inject a [NOTE] for named DF references."""
        remember_dataframe(
            db_session, user_id=user_id, project_id=project_id,
            df_id=1, df_data=_sample_df_data(), df_name="myGenes",
        )
        aug, inj, debug = _inject_job_context(
            "plot myGenes by score", "analyze_job_results",
            self._conv_history("plot myGenes by score"),
            history_blocks=[],
            db=db_session, user_id=user_id, project_id=project_id,
        )
        assert "myGenes" in aug
        assert "remembered" in aug.lower() or "NOTE" in aug

    def test_encode_skill_named_df_injection(self, db_session, user_id, project_id, make_block):
        """ENCODE skills inject full table data for named DF references."""
        remember_dataframe(
            db_session, user_id=user_id, project_id=project_id,
            df_id=2, df_data=_sample_df_data(), df_name="encodeDF",
        )
        # Need at least one prior assistant message so ENCODE follow-up path activates
        history = [
            {"role": "user", "content": "search K562"},
            {"role": "assistant", "content": "Found 58 results."},
            {"role": "user", "content": "plot encodeDF by gene"},
        ]
        aug, inj, debug = _inject_job_context(
            "plot encodeDF by gene", "ENCODE_Search", history,
            history_blocks=[],
            db=db_session, user_id=user_id, project_id=project_id,
        )
        assert "PREVIOUS QUERY DATA" in aug
        assert "encodeDF" in aug
        assert "encodeDF" in inj

    def test_no_db_no_injection(self):
        """Without db params, no remembered DF injection occurs."""
        aug, inj, debug = _inject_job_context(
            "plot c2c12DF by assay", "remote_execution",
            [{"role": "user", "content": "plot c2c12DF by assay"}],
            history_blocks=[],
        )
        assert "PREVIOUS QUERY DATA" not in aug
        assert not inj


# =====================================================================
# Helpers
# =====================================================================

def _sample_df_data() -> dict:
    """Return a minimal DF data dict for testing."""
    return {
        "columns": ["gene", "score"],
        "data": [
            {"gene": "TP53", "score": 0.95},
            {"gene": "BRCA1", "score": 0.87},
        ],
        "row_count": 2,
        "metadata": {"df_id": 1, "label": "Gene scores", "visible": True},
    }
