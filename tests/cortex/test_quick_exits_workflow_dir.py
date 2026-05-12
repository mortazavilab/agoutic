from pathlib import Path
from types import SimpleNamespace

from cortex.chat_stages.quick_exits import _resolve_workflow_command_project_dir


def test_resolve_workflow_command_project_dir_prefers_context_path(tmp_path):
    project_dir = tmp_path / "project"
    ctx = SimpleNamespace(
        project_dir_path=project_dir,
        project_dir="",
        session=None,
        user=None,
        project_id="proj-1",
    )

    assert _resolve_workflow_command_project_dir(ctx) == str(project_dir)


def test_resolve_workflow_command_project_dir_falls_back_to_db_helper(monkeypatch, tmp_path):
    project_dir = tmp_path / "project-from-db"

    monkeypatch.setattr(
        "cortex.chat_stages.quick_exits._resolve_project_dir",
        lambda session, user, project_id: Path(project_dir),
    )

    ctx = SimpleNamespace(
        project_dir_path=None,
        project_dir="",
        session=object(),
        user=SimpleNamespace(id="user-1"),
        project_id="proj-1",
    )

    assert _resolve_workflow_command_project_dir(ctx) == str(project_dir)