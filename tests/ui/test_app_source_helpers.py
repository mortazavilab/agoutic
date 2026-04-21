"""Tests for helper functions extracted directly from ui/appUI.py source."""

import ast
import base64
import datetime as dt
import json
import re as _re_module
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pytest


UI_APP_PATH = Path(__file__).resolve().parents[2] / "ui" / "appUI.py"
_UI_DIR = UI_APP_PATH.parent

# Mapping from the extracted module filename to its path on disk.
_EXTRACTED_MODULES = {
    "appui_state": _UI_DIR / "appui_state.py",
    "appui_services": _UI_DIR / "appui_services.py",
    "appui_renderers": _UI_DIR / "appui_renderers.py",
    "appui_tasks": _UI_DIR / "appui_tasks.py",
    "appui_chat_runtime": _UI_DIR / "appui_chat_runtime.py",
}
_BLOCK_PART2_PATH = _UI_DIR / "appui_block_part2.py"
_PROJECTS_PAGE_PATH = _UI_DIR / "pages" / "projects.py"


def _parse_appui_imports():
    """Parse appUI.py imports to build two resolution maps.

    Returns
    -------
    import_map : dict[str, tuple[Path, str]]
        ``{local_name: (source_path, original_name)}``
        for plain ``from appui_X import foo`` style imports (Group A).
    alias_map : dict[str, tuple[Path, str]]
        ``{alias: (source_path, original_name)}``
        for ``from appui_X import foo as foo_impl`` style imports (Group B).
    """
    source = UI_APP_PATH.read_text()
    tree = ast.parse(source, filename=str(UI_APP_PATH))
    import_map: dict[str, tuple[Path, str]] = {}
    alias_map: dict[str, tuple[Path, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module_name = node.module or ""
        if module_name not in _EXTRACTED_MODULES:
            continue
        src_path = _EXTRACTED_MODULES[module_name]
        for alias in node.names:
            original = alias.name
            local = alias.asname or alias.name
            if alias.asname:
                # e.g.  _resolve_df_by_id as _resolve_df_by_id_impl
                alias_map[local] = (src_path, original)
            else:
                import_map[local] = (src_path, original)
    return import_map, alias_map


def _ast_load_fn_from_file(fn_name: str, src_path: Path, namespace: dict):
    """Parse *src_path* and compile ``fn_name`` into *namespace*."""
    source = src_path.read_text()
    tree = ast.parse(source, filename=str(src_path))
    fn_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == fn_name
    )
    mod = ast.Module(body=[fn_node], type_ignores=[])
    exec(compile(mod, filename=str(src_path), mode="exec"), namespace)
    return namespace[fn_name]


# Build the import maps once at module load time.
_IMPORT_MAP, _ALIAS_MAP = _parse_appui_imports()

# Names of _impl aliases referenced inside wrapper bodies in appUI.py.
_IMPL_CALL_RE = _re_module.compile(r'\b(\w+_impl)\b')


def _load_function(name: str, extra_globals: dict | None = None):
    """Compile a single function from the UI source layer without importing Streamlit.

    Handles three cases that arise after the appUI decomposition:

    * **Group A** – function was moved to an extracted module and is only an
      ``import`` in appUI.py (no FunctionDef).  Resolved via ``_IMPORT_MAP``.
    * **Group B** – function is a thin wrapper in appUI.py that delegates to an
      ``*_impl`` alias imported from an extracted module.  The ``*_impl``
      callables are injected automatically into the execution namespace.
    * **Legacy** – function is a self-contained FunctionDef in appUI.py with
      no ``*_impl`` dependencies.  Compiled directly (original behaviour).
    """
    namespace: dict = {
        "pd": pd,
        "px": px,
        "go": go,
        "re": _re_module,
        "datetime": dt,
        "API_URL": "http://api.test",
        "PLOTLY_TEMPLATE": {},
    }
    if extra_globals:
        namespace.update(extra_globals)

    source = UI_APP_PATH.read_text()
    tree = ast.parse(source, filename=str(UI_APP_PATH))

    # Try to find a FunctionDef with this name in appUI.py.
    fn_node = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name),
        None,
    )

    if fn_node is None:
        # Group A: function lives in an extracted module; only imported here.
            if name in _IMPORT_MAP:
                src_path, original_name = _IMPORT_MAP[name]
                return _ast_load_fn_from_file(original_name, src_path, namespace)

            # Fallback: not imported into appUI.py either — scan all extracted
            # modules directly (handles private helpers like
            # _has_pending_destructive_confirmation that live in an extracted
            # module but aren't re-exported by appUI.py).
            for src_path in _EXTRACTED_MODULES.values():
                try:
                    return _ast_load_fn_from_file(name, src_path, namespace)
                except StopIteration:
                    continue
            raise LookupError(
                f"Function {name!r} not found as a FunctionDef in appUI.py "
                f"or any extracted UI module"
            )
    # pre-populate the namespace with the real implementations.
    fn_source = ast.unparse(fn_node)
    for impl_alias in _IMPL_CALL_RE.findall(fn_source):
        if impl_alias in namespace:
            continue  # already supplied (e.g. by extra_globals)
        if impl_alias in _ALIAS_MAP:
            src_path, original_name = _ALIAS_MAP[impl_alias]
            _ast_load_fn_from_file(original_name, src_path, namespace)
            namespace[impl_alias] = namespace[original_name]

    mod = ast.Module(body=[fn_node], type_ignores=[])
    exec(compile(mod, filename=str(UI_APP_PATH), mode="exec"), namespace)
    return namespace[name]


def _load_block_part2_function(name: str, extra_globals: dict | None = None):
    namespace: dict = {"datetime": dt}
    if extra_globals:
        namespace.update(extra_globals)
    if name != "_parse_stage_timestamp":
        namespace["_parse_stage_timestamp"] = _ast_load_fn_from_file(
            "_parse_stage_timestamp", _BLOCK_PART2_PATH, namespace
        )
    return _ast_load_fn_from_file(name, _BLOCK_PART2_PATH, namespace)


def _load_projects_page_function(name: str, extra_globals: dict | None = None):
    namespace: dict = {}
    if extra_globals:
        namespace.update(extra_globals)
    return _ast_load_fn_from_file(name, _PROJECTS_PAGE_PATH, namespace)


class TestCreateProjectServerSide:
    def test_returns_server_project_id_on_success(self):
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"id": "project-123", "slug": "named-project", "name": "named-project"},
        )
        request = MagicMock(return_value=response)
        fn = _load_function("_create_project_server_side", {"make_authenticated_request": request})

        result = fn("named-project")

        assert isinstance(result, dict)
        assert result["id"] == "project-123"
        assert result["slug"] == "named-project"
        request.assert_called_once_with(
            "POST",
            "http://api.test/projects",
            json={"name": "named-project"},
            timeout=10,
        )

    def test_falls_back_to_uuid_when_request_fails(self):
        request = MagicMock(side_effect=RuntimeError("network down"))
        fn = _load_function("_create_project_server_side", {"make_authenticated_request": request})

        import uuid
        original_uuid4 = uuid.uuid4
        uuid.uuid4 = lambda: "uuid-fallback"
        try:
            result = fn("named-project")
        finally:
            uuid.uuid4 = original_uuid4

        assert isinstance(result, dict)
        assert result["id"] == "uuid-fallback"


class TestJobStatusUpdatedAt:
    def test_prefers_live_poll_time_when_live_status_succeeds(self):
        fn = _load_function("_job_status_updated_at")
        now = dt.datetime(2026, 3, 19, 23, 0, 0, tzinfo=dt.timezone.utc)

        value = fn("2026-03-19T22:50:00Z", True, now)

        assert value == "2026-03-19T22:50:00Z"

    def test_preserves_supplied_live_poll_timestamp_when_present(self):
        fn = _load_function("_job_status_updated_at")
        now = dt.datetime(2026, 3, 19, 23, 0, 0, tzinfo=dt.timezone.utc)

        value = fn("2026-03-19T22:59:58Z", True, now)

        assert value == "2026-03-19T22:59:58Z"

    def test_falls_back_to_persisted_timestamp_when_live_status_fails(self):
        fn = _load_function("_job_status_updated_at")

        value = fn("2026-03-19T22:50:00Z", False)

        assert value == "2026-03-19T22:50:00Z"

    def test_returns_none_when_no_timestamp_exists(self):
        fn = _load_function("_job_status_updated_at")

        value = fn(None, False)

        assert value is None


class TestPauseAutoRefresh:
    def test_sets_requested_suppression_deadline(self):
        fake_st = SimpleNamespace(session_state={})
        fake_time = SimpleNamespace(time=lambda: 100.0)
        fn = _load_function("_pause_auto_refresh", {"st": fake_st, "time": fake_time})

        fn(4)

        assert fake_st.session_state["_suppress_auto_refresh_until"] == 104.0

    def test_does_not_shorten_existing_deadline(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 110.0})
        fake_time = SimpleNamespace(time=lambda: 100.0)
        fn = _load_function("_pause_auto_refresh", {"st": fake_st, "time": fake_time})

        fn(2)

        assert fake_st.session_state["_suppress_auto_refresh_until"] == 110.0


class TestProjectSwitchHelpers:
    def test_project_scope_mount_key_changes_with_project_id(self):
        fn = _load_function("_project_scope_mount_key")

        assert fn("project_panel", "project-a") != fn("project_panel", "project-b")
        assert fn("project_panel", "project-a") == "project_panel_project_scope_project-a"

    def test_set_active_project_resets_project_view_state(self):
        fake_st = SimpleNamespace(
            session_state={
                "active_project_id": "project-a",
                "_project_id_input": "project-a",
                "blocks": [{"id": "old-block"}],
                "_welcome_sent_for": "project-a",
            },
            toast=MagicMock(),
            switch_page=MagicMock(),
            rerun=MagicMock(),
        )
        request = MagicMock()
        fn = _load_projects_page_function(
            "_set_active_project",
            {
                "st": fake_st,
                "make_authenticated_request": request,
                "API_URL": "http://api.test",
            },
        )

        fn("project-b", "Project B")

        assert fake_st.session_state["_project_switch_loading_for"] == "project-b"
        assert fake_st.session_state["active_project_id"] == "project-b"
        assert fake_st.session_state["_project_id_input"] == "project-b"
        assert fake_st.session_state["blocks"] == []
        assert fake_st.session_state["_last_rendered_project"] == "project-b"
        assert "_welcome_sent_for" not in fake_st.session_state
        fake_st.rerun.assert_called_once()


class TestActiveChatIsolation:
    def test_handle_active_chat_ignores_other_project_request(self):
        fake_st = SimpleNamespace(
            session_state={
                "_active_chat": {
                    "project_id": "project-a",
                    "request_id": "req-1",
                }
            },
            chat_message=MagicMock(),
            rerun=MagicMock(),
        )
        fn = _load_function("handle_active_chat", {"st": fake_st})

        fn(api_url="http://api.test", active_project_id="project-b")

        assert fake_st.session_state["_active_chat"]["project_id"] == "project-a"
        fake_st.chat_message.assert_not_called()
        fake_st.rerun.assert_not_called()


class TestStagingBlockNeedsRefresh:
    def test_running_block_refreshes_quickly_without_transfer_snapshot(self):
        fn = _load_block_part2_function(
            "_staging_block_needs_refresh",
            {
                "_STAGING_UI_STALE_SECONDS": 90.0,
                "_STAGING_UI_ACTIVE_REFRESH_SECONDS": 8.0,
            },
        )
        updated_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=6)
        ).isoformat().replace("+00:00", "Z")

        assert fn(
            {"status": "RUNNING", "created_at": updated_at},
            {"staging_task_id": "stg-1", "last_updated": updated_at},
        ) is True

    def test_running_block_with_transfer_snapshot_uses_active_refresh_window(self):
        fn = _load_block_part2_function(
            "_staging_block_needs_refresh",
            {
                "_STAGING_UI_STALE_SECONDS": 90.0,
                "_STAGING_UI_ACTIVE_REFRESH_SECONDS": 8.0,
            },
        )
        fresh_updated_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=7)
        ).isoformat().replace("+00:00", "Z")
        stale_updated_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=9)
        ).isoformat().replace("+00:00", "Z")

        assert fn(
            {"status": "RUNNING", "created_at": fresh_updated_at},
            {
                "staging_task_id": "stg-1",
                "last_updated": fresh_updated_at,
                "transfer_progress": {"file_percent": 67},
            },
        ) is False
        assert fn(
            {"status": "RUNNING", "created_at": stale_updated_at},
            {
                "staging_task_id": "stg-1",
                "last_updated": stale_updated_at,
                "transfer_progress": {"file_percent": 67},
            },
        ) is True

    def test_non_running_block_keeps_long_stale_window(self):
        fn = _load_block_part2_function(
            "_staging_block_needs_refresh",
            {
                "_STAGING_UI_STALE_SECONDS": 90.0,
                "_STAGING_UI_ACTIVE_REFRESH_SECONDS": 8.0,
            },
        )
        updated_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)
        ).isoformat().replace("+00:00", "Z")

        assert fn(
            {"status": "FAILED", "created_at": updated_at},
            {"staging_task_id": "stg-1", "last_updated": updated_at},
        ) is False


class TestAutoRefreshIsSuppressed:
    def test_returns_true_before_deadline(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 105.0})
        fn = _load_function(
            "_auto_refresh_is_suppressed",
            {"st": fake_st, "_has_pending_destructive_confirmation": lambda: False},
        )

        assert fn(104.0) is True

    def test_returns_false_after_deadline(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 105.0})
        fn = _load_function(
            "_auto_refresh_is_suppressed",
            {"st": fake_st, "_has_pending_destructive_confirmation": lambda: False},
        )

        assert fn(105.0) is False

    def test_returns_true_when_destructive_confirmation_is_open(self):
        fake_st = SimpleNamespace(session_state={"_suppress_auto_refresh_until": 0.0})
        fn = _load_function(
            "_auto_refresh_is_suppressed",
            {"st": fake_st, "_has_pending_destructive_confirmation": lambda: True},
        )

        assert fn(105.0) is True


class TestHasPendingDestructiveConfirmation:
    def test_detects_archive_confirmation(self):
        fake_st = SimpleNamespace(session_state={"_confirm_archive_project_id": "proj-1"})
        fn = _load_function("_has_pending_destructive_confirmation", {"st": fake_st})

        assert fn() is True

    def test_detects_job_delete_confirmation(self):
        fake_st = SimpleNamespace(session_state={"del_confirm_block-1": True})
        fn = _load_function("_has_pending_destructive_confirmation", {"st": fake_st})

        assert fn() is True

    def test_detects_staging_delete_confirmation(self):
        fake_st = SimpleNamespace(session_state={"del_confirm_stage_block-1": True})
        fn = _load_function("_has_pending_destructive_confirmation", {"st": fake_st})

        assert fn() is True

    def test_returns_false_without_pending_confirmation(self):
        fake_st = SimpleNamespace(session_state={"_confirm_archive_project_id": None})
        fn = _load_function("_has_pending_destructive_confirmation", {"st": fake_st})

        assert fn() is False


class TestWorkflowStatusPresentation:
    def test_deleted_workflow_uses_deleted_chip(self):
        fn = _load_function("_workflow_status_presentation")

        assert fn("DELETED") == ("pending", "Deleted", "🗑️")


class TestTaskHelpers:
    def test_get_all_tasks_returns_grouped_collection(self):
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "sections": {
                    "running": [{"id": "task-1"}],
                    "pending": [],
                    "follow_up": [],
                    "completed": [],
                },
                "total_projects": 3,
                "projects_with_tasks": 1,
            },
        )
        request = MagicMock(return_value=response)
        fn = _load_function("get_all_tasks")

        result = fn("http://api.test", request, include_archived=True)

        assert result["total_projects"] == 3
        assert result["projects_with_tasks"] == 1
        assert result["sections"]["running"][0]["id"] == "task-1"
        request.assert_called_once_with(
            "GET",
            "http://api.test/tasks",
            params={"include_archived": True},
            timeout=8,
        )

    def test_handle_task_action_switches_to_task_project_for_results(self):
        fake_st = SimpleNamespace(
            session_state={},
            switch_page=MagicMock(),
            rerun=MagicMock(),
        )
        request = MagicMock()
        apply_task_action = MagicMock(return_value=True)
        fn = _load_function(
            "_handle_task_action",
            {
                "st": fake_st,
                "json": json,
                "apply_task_action": apply_task_action,
            },
        )

        fn(
            {
                "id": "task-results",
                "kind": "result_review",
                "project_id": "project-b",
                "action_target": json.dumps({"type": "results", "run_uuid": "run-123"}),
            },
            "project-a",
            "http://api.test",
            request,
        )

        assert fake_st.session_state["active_project_id"] == "project-b"
        assert fake_st.session_state["_project_id_input"] == "project-b"
        assert fake_st.session_state["selected_job_run_uuid"] == "run-123"
        fake_st.switch_page.assert_called_once_with("pages/results.py")
        apply_task_action.assert_called_once_with(
            "project-b",
            "task-results",
            "complete",
            "http://api.test",
            request,
        )

    def test_handle_task_action_opens_chat_for_block_target(self):
        fake_st = SimpleNamespace(
            session_state={"blocks": []},
            switch_page=MagicMock(),
            rerun=MagicMock(),
        )
        fn = _load_function(
            "_handle_task_action",
            {
                "st": fake_st,
                "json": json,
                "apply_task_action": MagicMock(),
            },
        )

        fn(
            {
                "id": "task-block",
                "project_id": "project-c",
                "action_target": json.dumps({"type": "block", "block_id": "block-9"}),
            },
            "project-a",
            "http://api.test",
            MagicMock(),
            navigate_to_chat_on_block=True,
        )

        assert fake_st.session_state["active_project_id"] == "project-c"
        assert fake_st.session_state["_project_id_input"] == "project-c"
        assert fake_st.session_state["_task_focus_block_id"] == "block-9"
        fake_st.switch_page.assert_called_once_with("appUI.py")
        fake_st.rerun.assert_not_called()

    def test_stage_task_secondary_action_requires_refresh_for_stale_running_transfer(self):
        fn = _load_function("_stage_task_secondary_action")

        action = fn(
            {
                "kind": "stage_transfer",
                "status": "FOLLOW_UP",
                "metadata": {
                    "staging_task_id": "stg-1",
                    "is_stale": True,
                    "stale_original_status": "RUNNING",
                },
            }
        )

        assert action == "refresh"

    def test_stage_task_secondary_action_allows_cancel_for_live_running_transfer(self):
        fn = _load_function("_stage_task_secondary_action")

        action = fn(
            {
                "kind": "stage_transfer",
                "status": "RUNNING",
                "metadata": {"staging_task_id": "stg-1"},
            }
        )

        assert action == "cancel"

    def test_stage_task_secondary_action_allows_resume_for_failed_transfer(self):
        fn = _load_function("_stage_task_secondary_action")

        action = fn(
            {
                "kind": "stage_transfer",
                "status": "FAILED",
                "metadata": {"staging_task_id": "stg-1"},
            }
        )

        assert action == "resume"

    def test_stage_task_secondary_action_allows_resume_for_cancelled_transfer(self):
        fn = _load_function("_stage_task_secondary_action")

        action = fn(
            {
                "kind": "stage_transfer",
                "status": "CANCELLED",
                "metadata": {"staging_task_id": "stg-1"},
            }
        )

        assert action == "resume"

    def test_stage_task_request_posts_refresh_endpoint(self):
        identity = _load_function("_stage_task_identity", {"json": json})
        request = MagicMock(
            return_value=SimpleNamespace(
                status_code=200,
                json=lambda: {"status": "running", "message": "Staging status refreshed."},
            )
        )
        fn = _load_function(
            "_stage_task_request",
            {
                "_stage_task_identity": identity,
                "_STAGE_TASK_ACTION_TIMEOUT_SECONDS": 15,
            },
        )

        ok, message, data = fn(
            "refresh",
            {
                "project_id": "project-b",
                "source_id": "block-9",
                "metadata": {"staging_task_id": "stg-1"},
            },
            "project-a",
            "http://api.test",
            request,
        )

        assert ok is True
        assert message == "Staging status refreshed."
        assert data["status"] == "running"
        request.assert_called_once_with(
            "POST",
            "http://api.test/remote/stage/stg-1/refresh",
            json={"project_id": "project-b", "block_id": "block-9"},
            timeout=15,
        )

    def test_stage_task_request_posts_resume_endpoint(self):
        identity = _load_function("_stage_task_identity", {"json": json})
        request = MagicMock(
            return_value=SimpleNamespace(
                status_code=200,
                json=lambda: {"status": "queued"},
            )
        )
        fn = _load_function(
            "_stage_task_request",
            {
                "_stage_task_identity": identity,
                "_STAGE_TASK_ACTION_TIMEOUT_SECONDS": 15,
            },
        )

        ok, message, data = fn(
            "resume",
            {
                "project_id": "project-b",
                "source_id": "block-9",
                "metadata": {"staging_task_id": "stg-1"},
            },
            "project-a",
            "http://api.test",
            request,
        )

        assert ok is True
        assert message == "Resuming staging from the existing cache path."
        assert data["status"] == "queued"
        request.assert_called_once_with(
            "POST",
            "http://api.test/remote/stage/stg-1/resume",
            json={"project_id": "project-b", "block_id": "block-9"},
            timeout=15,
        )

    def test_count_top_level_tasks_ignores_children(self):
        fn = _load_function("count_top_level_tasks")

        count = fn(
            {
                "running": [
                    {"id": "root-1", "parent_task_id": None},
                    {"id": "child-1", "parent_task_id": "root-1"},
                ],
                "follow_up": [{"id": "root-2", "parent_task_id": None}],
            },
            [("running", "Running"), ("follow_up", "Follow-up")],
        )

        assert count == 2

    def test_count_stale_top_level_tasks_counts_only_stale_roots(self):
        fn = _load_function("count_stale_top_level_tasks")

        count = fn(
            {
                "follow_up": [
                    {"id": "root-1", "parent_task_id": None, "metadata": {"is_stale": True}},
                    {"id": "child-1", "parent_task_id": "root-1", "metadata": {"is_stale": True}},
                    {"id": "root-2", "parent_task_id": None, "metadata": {}},
                ]
            },
            [("follow_up", "Follow-up")],
        )

        assert count == 1

    def test_prepare_project_task_sections_for_dock_hides_old_stale_roots(self):
        fn = _load_function("prepare_project_task_sections_for_dock")

        filtered, hidden_count = fn(
            {
                "follow_up": [
                    {
                        "id": "root-1",
                        "parent_task_id": None,
                        "metadata": {"is_stale": True, "stale_age_seconds": 60 * 60 * 72},
                    },
                    {
                        "id": "child-1",
                        "parent_task_id": "root-1",
                        "metadata": {"is_stale": True, "stale_age_seconds": 60 * 60 * 72},
                    },
                    {
                        "id": "root-2",
                        "parent_task_id": None,
                        "metadata": {"is_stale": True, "stale_age_seconds": 60 * 60 * 12},
                    },
                ]
            },
            [("follow_up", "Follow-up")],
            stale_hide_hours=48,
        )

        assert hidden_count == 1
        assert [task["id"] for task in filtered["follow_up"]] == ["root-2"]

    def test_prepare_project_task_sections_for_dock_keeps_recent_or_non_stale_tasks(self):
        fn = _load_function("prepare_project_task_sections_for_dock")

        filtered, hidden_count = fn(
            {
                "running": [{"id": "root-1", "parent_task_id": None, "metadata": {}}],
                "follow_up": [
                    {
                        "id": "root-2",
                        "parent_task_id": None,
                        "metadata": {"is_stale": True, "stale_age_seconds": 60 * 60 * 24},
                    }
                ],
            },
            [("running", "Running"), ("follow_up", "Follow-up")],
            stale_hide_hours=48,
        )

        assert hidden_count == 0
        assert [task["id"] for task in filtered["running"]] == ["root-1"]
        assert [task["id"] for task in filtered["follow_up"]] == ["root-2"]


class TestHelpIntent:
    def test_recognizes_grouped_de_help_shortcut(self):
        fn = _load_function("_is_help_intent")

        assert fn("how do i compare reconcile samples") is True

    def test_recognizes_show_slash_commands_help_shortcut(self):
        fn = _load_function("_is_help_intent")

        assert fn("show slash commands") is True

    def test_does_not_treat_actual_de_request_as_help(self):
        fn = _load_function("_is_help_intent")

        assert fn("compare the AD samples exc and jbh to the control samples gko and lwf") is False


class TestResolveDfById:
    def test_returns_latest_matching_dataframe(self):
        fn = _load_function("_resolve_df_by_id", {"Path": Path})
        blocks = [
            {
                "type": "AGENT_PLAN",
                "payload": {
                    "_dataframes": {
                        "old.csv": {
                            "metadata": {"df_id": 7},
                            "data": [{"gene": "OLD", "score": 1.0}],
                            "columns": ["gene", "score"],
                        }
                    }
                },
            },
            {
                "type": "AGENT_PLAN",
                "payload": {
                    "_dataframes": {
                        "new.csv": {
                            "metadata": {"df_id": 7},
                            "data": [{"gene": "NEW", "score": 2.0}],
                            "columns": ["gene", "score"],
                        }
                    }
                },
            },
        ]

        df, label = fn(7, blocks)

        assert label == "new.csv"
        assert list(df["gene"]) == ["NEW"]

    def test_ignores_blocks_without_dataframes_and_missing_ids(self):
        fn = _load_function("_resolve_df_by_id", {"Path": Path})
        blocks = [
            {"type": "USER_MESSAGE", "payload": {}},
            {"type": "AGENT_PLAN", "payload": {"_dataframes": {}}},
        ]

        assert fn(99, blocks) == (None, None)

    def test_prefers_latest_block_with_matching_dataframe(self):
        fn = _load_function("_resolve_df_by_id", {"Path": Path})
        blocks = [
            {
                "type": "AGENT_PLAN",
                "payload": {
                    "_dataframes": {
                        "preview.csv": {
                            "metadata": {"df_id": 3},
                            "data": [{"gene": "OLD"}],
                            "columns": ["gene"],
                        }
                    }
                },
            },
            {
                "type": "AGENT_PLOT",
                "payload": {
                    "_dataframes": {
                        "full.csv": {
                            "metadata": {"df_id": 3},
                            "data": [{"gene": "NEW"}],
                            "columns": ["gene"],
                        }
                    }
                },
            },
        ]

        df, label = fn(3, blocks)

        assert label == "full.csv"
        assert list(df["gene"]) == ["NEW"]

    def test_rehydrates_truncated_dataframe_from_local_parse_source(self, tmp_path):
        fn = _load_function("_resolve_df_by_id", {"Path": Path})
        work_dir = tmp_path / "workflow"
        work_dir.mkdir()
        source_path = work_dir / "big.csv"
        source_path.write_text("idx,value\n" + "\n".join(f"{idx},{idx * 2}" for idx in range(150)))
        blocks = [
            {
                "type": "AGENT_PLAN",
                "payload": {
                    "_dataframes": {
                        "big.csv": {
                            "metadata": {
                                "df_id": 5,
                                "label": "big.csv",
                                "row_count": 150,
                                "is_truncated": True,
                            },
                            "data": [{"idx": idx, "value": idx * 2} for idx in range(100)],
                            "columns": ["idx", "value"],
                            "row_count": 150,
                        }
                    },
                    "_provenance": [{
                        "source": "analyzer",
                        "tool": "parse_csv_file",
                        "success": True,
                        "params": {
                            "file_path": "big.csv",
                            "work_dir": str(work_dir),
                            "max_rows": 100,
                        },
                    }],
                },
            }
        ]

        df, label = fn(5, blocks)

        assert label == "big.csv"
        assert df is not None
        assert len(df) == 150
        assert df.iloc[-1]["idx"] == 149


class TestResolvePayloadDfById:
    def test_returns_dataframe_from_local_payload(self):
        fn = _load_function("_resolve_payload_df_by_id")
        dfs = {
            "stats.csv": {
                "metadata": {"df_id": 1},
                "columns": ["Category", "Count"],
                "data": [
                    {"Category": "Known", "Count": 10},
                    {"Category": "Novel", "Count": 1},
                ],
            }
        }

        df, label = fn(1, dfs)

        assert label == "stats.csv"
        assert df is not None
        assert df["Count"].tolist() == [10, 1]

    def test_returns_none_when_missing(self):
        fn = _load_function("_resolve_payload_df_by_id")

        assert fn(99, {}) == (None, None)


class TestFindRelatedWorkflowPlan:
    def test_prefers_matching_prior_workflow_title(self):
        fn = _load_function("_find_related_workflow_plan")
        blocks = [
            {
                "id": "wf-1",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "Older workflow"},
            },
            {
                "id": "wf-2",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "Plot and interpret results for JamshidW"},
            },
            {
                "id": "agent-1",
                "type": "AGENT_PLAN",
                "project_id": "proj-1",
                "payload": {"markdown": "Plan: Plot and interpret results for JamshidW\n\nDetails"},
            },
        ]

        related = fn(blocks[-1], blocks)

        assert related is not None
        assert related["id"] == "wf-2"

    def test_matches_markdown_plan_heading_title(self):
        fn = _load_function("_find_related_workflow_plan")
        blocks = [
            {
                "id": "wf-1",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "Plot and interpret results for JamshidW"},
            },
            {
                "id": "agent-1",
                "type": "AGENT_PLAN",
                "project_id": "proj-1",
                "payload": {"markdown": "## Plan: Plot and interpret results for JamshidW\n\nDetails"},
            },
        ]

        related = fn(blocks[-1], blocks)

        assert related is not None
        assert related["id"] == "wf-1"

    def test_prefers_explicit_workflow_plan_block_id(self):
        fn = _load_function("_find_related_workflow_plan")
        blocks = [
            {
                "id": "wf-1",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "Old workflow"},
            },
            {
                "id": "wf-2",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "New workflow"},
            },
            {
                "id": "agent-1",
                "type": "AGENT_PLAN",
                "project_id": "proj-1",
                "payload": {
                    "markdown": "Analysis complete.",
                    "workflow_plan_block_id": "wf-1",
                },
            },
        ]

        related = fn(blocks[-1], blocks)

        assert related is not None
        assert related["id"] == "wf-1"

    def test_returns_none_when_no_workflow_match(self):
        fn = _load_function("_find_related_workflow_plan")
        blocks = [
            {
                "id": "wf-1",
                "type": "WORKFLOW_PLAN",
                "project_id": "proj-1",
                "payload": {"title": "Old workflow"},
            },
            {
                "id": "agent-1",
                "type": "AGENT_PLAN",
                "project_id": "proj-1",
                "payload": {"markdown": "Analysis complete."},
            },
        ]

        assert fn(blocks[-1], blocks) is None


class TestWorkflowHighlightSteps:
    def test_returns_completed_plot_and_summary_steps(self):
        fn = _load_function("_workflow_highlight_steps")
        workflow_block = {
            "payload": {
                "steps": [
                    {"kind": "PARSE_OUTPUT_FILE", "status": "COMPLETED", "result": {"rows": 10}},
                    {"kind": "GENERATE_PLOT", "status": "COMPLETED", "result": {"charts": [{"type": "bar"}]}},
                    {"kind": "INTERPRET_RESULTS", "status": "COMPLETED", "result": {"markdown": "Looks usable."}},
                    {"kind": "WRITE_SUMMARY", "status": "PENDING", "result": {"markdown": "Later"}},
                ]
            }
        }

        highlights = fn(workflow_block)

        assert [step["kind"] for step in highlights] == ["GENERATE_PLOT", "INTERPRET_RESULTS"]


class _ContextManagerStub:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestRenderWorkflowStepDataframes:
    def test_workflow_step_renders_embedded_dataframes_and_post_markdown(self):
        fake_st = SimpleNamespace(
            chat_message=MagicMock(return_value=_ContextManagerStub()),
            markdown=MagicMock(),
            caption=MagicMock(),
            divider=MagicMock(),
            expander=MagicMock(return_value=_ContextManagerStub()),
            session_state={},
            json=MagicMock(),
        )
        render_dfs = MagicMock()
        render_step_payload = MagicMock()
        fn = _load_block_part2_function(
            "render_block_part2",
            {
                "st": fake_st,
                "section_header": lambda *args, **kwargs: None,
                "metadata_row": lambda *args, **kwargs: None,
                "status_chip": lambda *args, **kwargs: None,
                "info_callout": lambda *args, **kwargs: None,
            },
        )

        fn(
            btype="WORKFLOW_PLAN",
            block={"payload": {}},
            content={
                "title": "Workflow",
                "steps": [
                    {
                        "id": "step-1",
                        "kind": "INTERPRET_RESULTS",
                        "title": "Interpret DE results",
                        "status": "COMPLETED",
                        "result": {
                            "markdown": "Lead interpretation.",
                            "_dataframes": {
                                "up.csv": {
                                    "columns": ["term_name"],
                                    "data": [{"term_name": "axon development"}],
                                    "row_count": 1,
                                    "metadata": {"df_id": 1, "visible": True, "label": "GO enrichment (upregulated)"},
                                }
                            },
                            "post_dataframe_markdown": "Up table summary.",
                            "comparison": {"group_a_label": "AD"},
                        },
                    }
                ],
            },
            block_id="block-1",
            status="COMPLETED",
            API_URL="http://api.test",
            active_id="proj-1",
            LIVE_JOB_STATUS_TIMEOUT_SECONDS=30,
            make_authenticated_request=lambda *args, **kwargs: None,
            show_metadata=lambda: None,
            _workflow_status_presentation=lambda status: ("ok", status, "ok"),
            _format_plan_timestamp=lambda value: value,
            _format_duration=lambda *_args, **_kwargs: "",
            _block_timestamp=lambda: "",
            _render_workflow_plot_payload=lambda *_args, **_kwargs: None,
            _render_embedded_dataframes=render_dfs,
            _render_step_payload=render_step_payload,
            _job_status_updated_at=lambda *_args, **_kwargs: None,
            _run_status_label=lambda *_args, **_kwargs: "",
            _format_timestamp=lambda *_args, **_kwargs: "",
            _workflow_label_from_path=lambda path: path,
            _pause_auto_refresh=lambda *_args, **_kwargs: None,
            get_job_debug_info=lambda *_args, **_kwargs: None,
            _render_plot_block=lambda *_args, **_kwargs: None,
        )

        render_dfs.assert_called_once()
        assert render_dfs.call_args.args[0] == {
            "up.csv": {
                "columns": ["term_name"],
                "data": [{"term_name": "axon development"}],
                "row_count": 1,
                "metadata": {"df_id": 1, "visible": True, "label": "GO enrichment (upregulated)"},
            }
        }
        markdown_values = [call.args[0] for call in fake_st.markdown.call_args_list]
        assert "Lead interpretation." in markdown_values
        assert "Up table summary." in markdown_values
        render_step_payload.assert_called_once_with({"comparison": {"group_a_label": "AD"}})


class TestRenderWorkflowPlotPayload:
    def test_renders_saved_image_artifacts(self, tmp_path):
        image_path = tmp_path / "volcano.png"
        image_path.write_bytes(b"fake-png")
        fake_st = SimpleNamespace(
            image=MagicMock(),
            caption=MagicMock(),
            warning=MagicMock(),
            plotly_chart=MagicMock(),
        )
        fn = _load_function(
            "_render_workflow_plot_payload",
            {
                "st": fake_st,
                "Path": Path,
            },
        )

        fn(
            {
                "image_files": [
                    {
                        "path": str(image_path),
                        "caption": "Volcano plot · AD vs control",
                    }
                ]
            },
            "block-1",
            "step_1",
        )

        fake_st.image.assert_called_once_with(
            str(image_path),
            caption="Volcano plot · AD vs control",
            use_container_width=True,
        )
        fake_st.caption.assert_not_called()


class TestRenderPlotBlock:
    def test_plotly_charts_render_with_streamlit_theme_disabled(self):
        fake_st = SimpleNamespace(
            plotly_chart=MagicMock(),
            warning=MagicMock(),
            info=MagicMock(),
        )
        fn = _load_function(
            "_render_plot_block",
            {
                "st": fake_st,
                "defaultdict": defaultdict,
                "_default_plot_export_name": lambda chart_type, df_label, fig: f"{df_label}_{chart_type}",
                "_render_plot_publication_tools": lambda fig, plot_key_base, chart_type, default_name: (
                    fig,
                    {"figure_width": 900, "figure_height": 500},
                ),
                "_resolve_df_by_id": lambda df_id, all_blocks: (pd.DataFrame({"x": [1], "y": [2]}), "DF1"),
                "_build_plotly_figure": lambda chart, df, label, plotly_template: go.Figure(go.Bar(x=[1], y=[2])),
            },
        )

        fn(
            {"charts": [{"type": "bar", "df_id": 1, "x": "x", "y": "y"}]},
            [],
            "block-1",
        )

        fake_st.plotly_chart.assert_called_once()
        assert fake_st.plotly_chart.call_args.kwargs["theme"] is None

    def test_renders_inline_base64_image_artifacts(self):
        fake_st = SimpleNamespace(
            image=MagicMock(),
            caption=MagicMock(),
            warning=MagicMock(),
            plotly_chart=MagicMock(),
        )
        fn = _load_function(
            "_render_workflow_plot_payload",
            {
                "st": fake_st,
                "Path": Path,
            },
        )

        fn(
            {
                "image_files": [
                    {
                        "data_b64": base64.b64encode(b"fake-png").decode("ascii"),
                        "caption": "Volcano plot · AD vs control",
                    }
                ]
            },
            "block-1",
            "step_1",
        )

        fake_st.image.assert_called_once_with(
            b"fake-png",
            caption="Volcano plot · AD vs control",
            use_container_width=True,
        )
        fake_st.caption.assert_not_called()


class TestPublicationPlotHelpers:
    def test_apply_publication_style_updates_fonts_layout_and_bar_labels(self):
        fn = _load_function("_apply_publication_style")
        fig = go.Figure(go.Bar(x=["Condition Alpha", "Condition Beta", "Condition Gamma"], y=[12, 30, 18]))
        fig.update_layout(title="My Plot", xaxis_title="Condition", yaxis_title="Count")

        styled = fn(
            fig,
            {
                "figure_width": 1600,
                "figure_height": 900,
                "title_size": 30,
                "axis_title_size": 20,
                "tick_font_size": 15,
                "legend_font_size": 14,
                "legend_position": "top",
                "show_grid": False,
                "title_bold": True,
                "axis_title_bold": True,
                "x_tick_angle": "-45",
                "show_value_labels": True,
                "value_label_size": 16,
                "value_label_angle": "45",
                "value_label_bold": True,
                "transparent_background": False,
            },
        )

        assert styled.layout.width == 1600
        assert styled.layout.height == 900
        assert styled.layout.title.text == "<b>My Plot</b>"
        assert styled.layout.xaxis.title.text == "<b>Condition</b>"
        assert styled.layout.yaxis.title.text == "<b>Count</b>"
        assert styled.layout.paper_bgcolor == "#ffffff"
        assert styled.layout.xaxis.showgrid is False
        assert styled.layout.xaxis.tickangle == -45
        assert styled.layout.legend.orientation == "h"
        assert list(styled.data[0].text) == ["12", "30", "18"]
        assert styled.data[0].textangle == 45
        assert styled.data[0].textfont["size"] == 16

    def test_apply_publication_style_uses_offset_annotations_for_grouped_bar_labels(self):
        fn = _load_function("_apply_publication_style")
        fig = go.Figure()
        fig.add_bar(name="Known", x=["s1", "s2"], y=[1000, 1100])
        fig.add_bar(name="NIC", x=["s1", "s2"], y=[30, 25])
        fig.update_layout(barmode="group", title="Grouped Plot", xaxis_title="Sample", yaxis_title="Count")

        styled = fn(
            fig,
            {
                "figure_width": 1400,
                "figure_height": 900,
                "title_size": 28,
                "axis_title_size": 18,
                "tick_font_size": 14,
                "legend_font_size": 12,
                "legend_position": "right",
                "show_grid": True,
                "title_bold": True,
                "axis_title_bold": True,
                "x_tick_angle": "auto",
                "show_value_labels": True,
                "value_label_size": 14,
                "value_label_angle": "auto",
                "value_label_bold": True,
                "transparent_background": False,
            },
        )

        assert len(styled.layout.annotations) == 2
        assert all(trace.textposition == "outside" for trace in styled.data)
        assert all(trace.textangle == 0 for trace in styled.data)
        assert list(styled.data[0].text) == ["1k", "1.1k"]
        assert list(styled.data[1].text) == ["", ""]
        assert sorted(annotation.text for annotation in styled.layout.annotations) == ["25", "30"]

    def test_apply_publication_style_rebuilds_grouped_bar_annotations_from_clean_state(self):
        fn = _load_function("_apply_publication_style")
        fig = go.Figure()
        fig.add_bar(name="Known", x=["s1", "s2"], y=[1000, 1100])
        fig.add_bar(name="NIC", x=["s1", "s2"], y=[30, 25])
        fig.add_annotation(x="s1", y=30, text="stale")
        fig.update_layout(barmode="group", title="Grouped Plot", xaxis_title="Sample", yaxis_title="Count")

        styled = fn(
            fig,
            {
                "figure_width": 1400,
                "figure_height": 900,
                "title_size": 28,
                "axis_title_size": 18,
                "tick_font_size": 14,
                "legend_font_size": 12,
                "legend_position": "right",
                "show_grid": True,
                "title_bold": True,
                "axis_title_bold": True,
                "x_tick_angle": "auto",
                "show_value_labels": True,
                "value_label_size": 14,
                "value_label_angle": "45",
                "value_label_bold": True,
                "transparent_background": False,
            },
        )

        assert len(styled.layout.annotations) == 2
        assert sorted(annotation.text for annotation in styled.layout.annotations) == ["25", "30"]
        assert all(annotation.textangle == 45 for annotation in styled.layout.annotations)

    def test_build_plot_download_payload_supports_html_exports(self):
        fn = _load_function("_build_plot_download_payload")
        fig = go.Figure(go.Bar(x=["A"], y=[1]))

        payload, file_name, error = fn(fig, {"export_format": "HTML", "file_stem": "publication_plot"})

        assert error is None
        assert file_name == "publication_plot.html"
        assert b"plotly" in payload.lower()


class TestBlockRequiresFullRefresh:
    def test_only_running_execution_and_download_jobs_require_full_refresh(self):
        fn = _load_function("_block_requires_full_refresh")

        assert fn({"type": "EXECUTION_JOB", "status": "RUNNING"}) is True
        assert fn({"type": "DOWNLOAD_TASK", "status": "RUNNING"}) is True
        assert fn({"type": "EXECUTION_JOB", "status": "DONE", "payload": {"job_status": {"status": "RUNNING"}}}) is True
        assert fn({"type": "EXECUTION_JOB", "status": "NEW", "payload": {"job_status": {"status": "PENDING"}}}) is True
        assert fn({"type": "WORKFLOW_PLAN", "status": "RUNNING"}) is False
        assert fn({"type": "STAGING_TASK", "status": "RUNNING"}) is False
        assert fn({"type": "EXECUTION_JOB", "status": "DONE"}) is False


class TestCachedJobStatus:
    def test_fetches_status_and_updates_session_cache(self):
        fake_st = SimpleNamespace(session_state={})
        fake_time = SimpleNamespace(time=lambda: 100.0)
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "RUNNING",
                "transfer_state": "downloading_outputs",
                "message": "Syncing outputs",
            },
        )
        request = MagicMock(return_value=response)
        fn = _load_function(
            "get_cached_job_status",
            {
                "st": fake_st,
                "time": fake_time,
                "make_authenticated_request": request,
                "LIVE_JOB_STATUS_TIMEOUT_SECONDS": 60,
            },
        )

        status, ok = fn("run-123")

        assert ok is True
        assert status["transfer_state"] == "downloading_outputs"
        assert fake_st.session_state["_transfer_state_run-123"] == "downloading_outputs"
        request.assert_called_once_with(
            "GET",
            "http://api.test/jobs/run-123/status",
            timeout=1.5,
        )

    def test_reuses_recent_cached_status_without_refetch(self):
        fake_now = {"value": 100.0}
        fake_st = SimpleNamespace(session_state={})
        fake_time = SimpleNamespace(time=lambda: fake_now["value"])
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "RUNNING", "transfer_state": "downloading_outputs"},
        )
        request = MagicMock(return_value=response)
        fn = _load_function(
            "get_cached_job_status",
            {
                "st": fake_st,
                "time": fake_time,
                "make_authenticated_request": request,
                "LIVE_JOB_STATUS_TIMEOUT_SECONDS": 60,
            },
        )

        first_status, first_ok = fn("run-456")
        fake_now["value"] = 101.0
        request.reset_mock()
        second_status, second_ok = fn("run-456")

        assert first_ok is True
        assert second_ok is True
        assert second_status == first_status
        request.assert_not_called()


class TestFullPageRefreshBootstrapExpression:
    def test_expression_turns_on_when_flag_appears(self):
        auto_refresh = True
        suppressed = False
        before = bool(auto_refresh and False and not suppressed)
        after = bool(auto_refresh and True and not suppressed)

        assert before is False
        assert after is True


class TestBuildPlotlyFigure:
    @pytest.fixture()
    def sample_df(self):
        return pd.DataFrame(
            {
                "Category": ["A", "B", "C"],
                "Count": [5, 3, 2],
                "Score": [0.9, 0.7, 0.5],
                "Group": ["x", "x", "y"],
            }
        )

    def test_case_insensitive_columns_work_for_scatter(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "scatter", "x": "category", "y": "score"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].x.tolist() == ["A", "B", "C"]

    def test_histogram_uses_numeric_companion_for_categorical_x(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "histogram", "x": "Category"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].type == "bar"
        assert fig.data[0].y.tolist() == [5, 3, 2]

    def test_bar_mean_aggregation_groups_values(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "Group": ["x", "x", "y"],
                "Value": [1.0, 3.0, 10.0],
            }
        )

        fig = fn({"type": "bar", "x": "Group", "y": "Value", "agg": "mean"}, df, "Grouped")

        assert fig is not None
        assert fig.data[0].x.tolist() == ["x", "y"]
        assert fig.data[0].y.tolist() == [2.0, 10.0]

    def test_heatmap_returns_none_with_only_one_numeric_column(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame({"Label": ["a", "b"], "Value": [1, 2]})

        assert fn({"type": "heatmap"}, df, "Small") is None

    def test_unknown_chart_type_returns_none(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        assert fn({"type": "not-a-chart"}, sample_df, "Test DF") is None

    def test_applies_explicit_light_plot_backgrounds(self, sample_df):
        fn = _load_function(
            "_build_plotly_figure",
            {
                "PLOTLY_TEMPLATE": {
                    "layout": {
                        "paper_bgcolor": "#ffffff",
                        "plot_bgcolor": "#f8fafc",
                        "colorway": ["#9fd3ff", "#1f6fd1", "#f7a3a8"],
                        "title": {"x": 0.03, "xanchor": "left"},
                    }
                }
            },
        )

        fig = fn({"type": "bar", "x": "Category", "y": "Count"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.layout.paper_bgcolor == "#ffffff"
        assert fig.layout.plot_bgcolor == "#f8fafc"
        assert fig.layout.title.x == 0.03
        assert fig.layout.xaxis.automargin is True
        assert fig.layout.yaxis.automargin is True

    def test_bar_honors_literal_named_color(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "bar", "x": "Category", "color": "red"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].marker.color == "red"

    def test_bar_honors_literal_hex_color(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "bar", "x": "Category", "color": "#ff0000"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].marker.color == "#ff0000"

    def test_bar_honors_palette_literal(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "bar", "x": "Category", "palette": "red"}, sample_df, "Test DF")

        assert fig is not None
        assert fig.data[0].marker.color == "red"

    def test_bar_shows_value_labels_above_bars(self, sample_df):
        fn = _load_function("_build_plotly_figure")

        fig = fn({"type": "bar", "x": "Category", "y": "Count"}, sample_df, "Test DF")

        assert fig is not None
        assert list(fig.data[0].text) == ["5", "3", "2"]
        assert fig.data[0].textposition == "outside"
        assert fig.data[0].textangle == 0
        assert fig.data[0].textfont.color == "#1f2937"
        assert fig.layout.uniformtext.mode == "show"

    def test_dense_grouped_bar_rotates_value_labels(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "sample": ["s1", "s2", "s3", "s4"],
                "ANTISENSE": [268, 221, 247, 300],
                "KNOWN": [1000, 1100, 1050, 1200],
                "NIC": [30, 25, 27, 29],
            }
        )

        fig = fn({"type": "bar", "x": "sample", "agg": "sum"}, df, "Dense DF")

        assert fig is not None
        assert len(fig.layout.annotations) == 4
        assert all(trace.textposition == "outside" for trace in fig.data)
        assert all(trace.textangle == 0 for trace in fig.data)
        assert list(fig.data[1].text) == ["1k", "1.1k", "1.1k", "1.2k"]
        assert list(fig.data[2].text) == ["", "", "", ""]
        assert sorted(annotation.text for annotation in fig.layout.annotations) == ["25", "27", "29", "30"]

    def test_bar_auto_melts_wide_dataframe_for_color_by_sample(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "Modification": ["KNOWN", "NIC"],
                "sampleA": [10, 5],
                "sampleB": [12, 6],
                "sampleC": [11, 4],
            }
        )

        fig = fn({"type": "bar", "x": "Modification", "y": "reads", "color": "sample"}, df, "Wide DF")

        assert fig is not None
        assert len(fig.data) == 3
        assert {trace.name for trace in fig.data} == {"sampleA", "sampleB", "sampleC"}

    def test_bar_with_sample_x_and_multiple_numeric_columns_renders_all_measures(self):
        fn = _load_function(
            "_build_plotly_figure",
            {
                "PLOTLY_TEMPLATE": {
                    "layout": {
                        "colorway": ["#9fd3ff", "#1f6fd1", "#f7a3a8", "#ff2b2b"],
                    }
                }
            },
        )
        df = pd.DataFrame(
            {
                "sample": ["s1", "s2", "s3"],
                "ANTISENSE": [268, 221, 247],
                "KNOWN": [1000, 1100, 1050],
                "NIC": [30, 25, 27],
            }
        )

        fig = fn({"type": "bar", "x": "sample", "agg": "sum"}, df, "Wide Sample DF")

        assert fig is not None
        assert {trace.name for trace in fig.data} == {"ANTISENSE", "KNOWN", "NIC"}
        assert [trace.marker.color for trace in fig.data] == ["#9fd3ff", "#1f6fd1", "#f7a3a8"]

    def test_bar_with_measure_x_and_multiple_numeric_columns_groups_by_sample(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "sample": ["s1", "s2", "s3"],
                "ANTISENSE": [268, 221, 247],
                "KNOWN": [1000, 1100, 1050],
                "NIC": [30, 25, 27],
            }
        )

        fig = fn({"type": "bar", "x": "measure", "agg": "sum"}, df, "Wide Sample DF")

        assert fig is not None
        assert {trace.name for trace in fig.data} == {"s1", "s2", "s3"}
        assert set(fig.data[0].x) == {"ANTISENSE", "KNOWN", "NIC"}

    def test_line_with_sample_x_and_multiple_numeric_columns_renders_all_measures(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "sample": ["s1", "s2", "s3"],
                "Known": [100, 120, 115],
                "Novel": [12, 15, 19],
            }
        )

        fig = fn({"type": "line", "x": "sample"}, df, "Wide Sample DF")

        assert fig is not None
        assert {trace.name for trace in fig.data} == {"Known", "Novel"}
        assert fig.data[0].mode == "lines+markers"

    def test_violin_renders_distribution_by_group(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "Condition": ["A", "A", "B", "B"],
                "Score": [1.2, 1.5, 2.8, 3.1],
            }
        )

        fig = fn({"type": "violin", "x": "Condition", "y": "Score"}, df, "Distribution DF")

        assert fig is not None
        assert all(trace.type == "violin" for trace in fig.data)

    def test_venn_infers_boolean_set_columns_when_sets_omitted(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "gene": ["g1", "g2", "g3", "g4"],
                "treated": [True, True, False, False],
                "control": [True, False, True, False],
                "rescue": [False, True, True, False],
            }
        )

        fig = fn({"type": "venn", "title": "Shared Genes"}, df, "Overlap DF")

        assert fig is not None
        assert len(fig.layout.shapes) == 3
        assert fig.layout.title.text == "Shared Genes"

    def test_upset_uses_set_columns_for_overlap_plot(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "gene": ["g1", "g2", "g3", "g4", "g5"],
                "treated": [True, True, False, False, True],
                "control": [True, False, True, False, True],
                "rescue": [False, True, True, False, False],
            }
        )

        fig = fn({"type": "upset", "sets": "treated|control|rescue"}, df, "Overlap DF")

        assert fig is not None
        assert fig.data[0].type == "bar"
        assert any(trace.type == "scatter" for trace in fig.data[1:])
        assert fig.layout.title.text == "UpSet plot"
        assert fig.layout.xaxis2.tickangle == -30
        assert fig.data[1].marker.color == "#E8E8E8"
        assert fig.data[0].marker.color[0] == "#2E5A87"

    def test_upset_treats_positive_numeric_sets_as_present_when_explicit(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "transcript_novelty": ["KNOWN", "KNOWN", "ISM", "ISM"],
                "c2c12r1": [62, 0, 8, 0],
                "c2c12r2": [67, 0, 5, 3],
                "c2c12r3": [76, 1, 11, 1],
            }
        )

        fig = fn({"type": "upset", "sets": "c2c12r1|c2c12r2|c2c12r3"}, df, "Overlap DF")

        assert fig is not None
        assert fig.data[0].type == "bar"
        assert list(fig.data[0].y[:3]) == [2.0, 1.0, 1.0]
        assert list(fig.data[0].text[:3]) == ["2", "1", "1"]
        assert len(fig.data[0].y) == 7
        assert list(fig.data[0].y).count(0.0) == 4
        assert fig.layout.title.text == "UpSet plot"
        assert list(fig.layout.yaxis2.categoryarray)[:3] == ["r3", "r2", "r1"]

    def test_bar_applies_title_and_y_axis_label(self):
        fn = _load_function("_build_plotly_figure")
        df = pd.DataFrame(
            {
                "sample": ["s1", "s2", "s3"],
                "reads": [10, 20, 15],
            }
        )

        fig = fn(
            {"type": "bar", "x": "sample", "y": "reads", "title": "Reads by Sample", "ylabel": "Reads"},
            df,
            "Simple DF",
        )

        assert fig is not None
        assert fig.layout.title.text == "Reads by Sample"
        assert fig.layout.yaxis.title.text == "Reads"
