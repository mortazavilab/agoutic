from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import common.database as common_db


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT / "alembic/versions/a9f1d7c3e5b2_add_project_collaborator_audit_and_uniqueness.py"
)


def _reset_database_state() -> None:
    common_db.reset_engines()
    common_db.SessionLocal.reset()
    common_db.AsyncSessionLocal.reset()


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("project_collaboration_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_project_collaboration_migration_dedupes_and_backfills_owner_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "project-collaboration.sqlite"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setattr(common_db, "DATABASE_URL", db_url)
    _reset_database_state()

    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    engine = None

    try:
        command.upgrade(alembic_cfg, "c2f4e6a8b0d1")
        engine = create_engine(db_url)

        with engine.begin() as conn:
            columns = {
                row[1]: row[3]
                for row in conn.execute(text("PRAGMA table_info(project_access)"))
            }
            assert "invited_by" not in columns
            assert "created_at" not in columns
            assert "updated_at" not in columns

            conn.execute(
                text(
                    """
                    INSERT INTO users (id, email, role, is_active)
                    VALUES
                      ('owner-1', 'owner1@example.com', 'user', 1),
                      ('owner-2', 'owner2@example.com', 'user', 1),
                      ('user-dupe', 'dupe@example.com', 'user', 1),
                      ('user-conflict', 'conflict@example.com', 'user', 1),
                      ('user-tie', 'tie@example.com', 'user', 1)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO projects (id, name, slug, owner_id, is_public, is_archived, created_at, updated_at)
                    VALUES
                      ('proj-1', 'Project One', 'project-one', 'owner-1', 0, 0, '2024-01-01 00:00:00', '2024-01-01 00:00:00'),
                      ('proj-2', 'Project Two', 'project-two', 'owner-2', 0, 0, '2024-02-01 00:00:00', '2024-02-01 00:00:00')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO project_access (id, user_id, project_id, project_name, role, last_accessed)
                    VALUES
                      ('legacy-conflict-owner', 'user-conflict', 'proj-1', 'Project One', 'owner', '2024-01-02 00:00:00'),
                      ('conflict-editor-existing', 'user-conflict', 'proj-1', 'Project One', 'editor', '2024-01-04 00:00:00'),
                      ('dup-viewer', 'user-dupe', 'proj-1', 'Project One', 'viewer', '2024-01-02 00:00:00'),
                      ('dup-editor', 'user-dupe', 'proj-1', 'Project One', 'editor', '2024-01-03 00:00:00'),
                      ('tie-a', 'user-tie', 'proj-2', 'Project Two', 'viewer', '2024-02-02 00:00:00'),
                      ('tie-b', 'user-tie', 'proj-2', 'Project Two', 'viewer', '2024-02-02 00:00:00')
                    """
                )
            )

        command.upgrade(alembic_cfg, "a9f1d7c3e5b2")

        with engine.begin() as conn:
            columns = {
                row[1]: row[3]
                for row in conn.execute(text("PRAGMA table_info(project_access)"))
            }
            assert columns["created_at"] == 1
            assert columns["updated_at"] == 1

            rows = conn.execute(
                text(
                    """
                    SELECT id, user_id, project_id, role, invited_by, created_at, updated_at, last_accessed
                      FROM project_access
                     ORDER BY project_id, user_id, id
                    """
                )
            ).mappings().all()

            by_project_user = {(row["project_id"], row["user_id"]): row for row in rows}
            assert set(by_project_user) == {
                ("proj-1", "owner-1"),
                ("proj-1", "user-conflict"),
                ("proj-1", "user-dupe"),
                ("proj-2", "owner-2"),
                ("proj-2", "user-tie"),
            }
            assert by_project_user[("proj-1", "owner-1")]["role"] == "owner"
            assert by_project_user[("proj-2", "owner-2")]["role"] == "owner"
            assert by_project_user[("proj-1", "user-dupe")]["id"] == "dup-editor"
            assert by_project_user[("proj-1", "user-dupe")]["role"] == "editor"
            assert by_project_user[("proj-1", "user-conflict")]["id"] == "conflict-editor-existing"
            assert by_project_user[("proj-1", "user-conflict")]["role"] == "editor"
            assert by_project_user[("proj-2", "user-tie")]["id"] == "tie-b"
            assert by_project_user[("proj-2", "user-tie")]["role"] == "viewer"

            for row in rows:
                assert row["invited_by"] is None
                assert row["created_at"] is not None
                assert row["updated_at"] is not None

            owner_rows = conn.execute(
                text(
                    """
                    SELECT p.id, pa.user_id, pa.role
                      FROM projects p
                      JOIN project_access pa
                        ON pa.project_id = p.id
                       AND pa.role = 'owner'
                     ORDER BY p.id
                    """
                )
            ).mappings().all()
            assert owner_rows == [
                {"id": "proj-1", "user_id": "owner-1", "role": "owner"},
                {"id": "proj-2", "user_id": "owner-2", "role": "owner"},
            ]

            index_rows = conn.execute(text("PRAGMA index_list('project_access')")).fetchall()
            index_names = {row[1] for row in index_rows}
            assert "uq_project_access_project_user" in index_names

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO project_access (
                            id, user_id, project_id, project_name, role,
                            invited_by, created_at, updated_at, last_accessed
                        ) VALUES (
                            'dup-after-upgrade', 'user-dupe', 'proj-1', 'Project One', 'viewer',
                            NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
    finally:
        if engine is not None:
            engine.dispose()
        _reset_database_state()


def test_project_collaboration_migration_preflight_rejects_duplicate_memberships(tmp_path):
    module = _load_migration_module()
    db_path = tmp_path / "project-collaboration-preflight.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")

    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)"))
            conn.execute(
                text(
                    """
                    CREATE TABLE project_access (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        role TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(text("INSERT INTO projects (id, owner_id) VALUES ('proj-1', 'owner-1')"))
            conn.execute(
                text(
                    """
                    INSERT INTO project_access (id, user_id, project_id, role)
                    VALUES
                      ('owner-row', 'owner-1', 'proj-1', 'owner'),
                      ('dup-a', 'user-1', 'proj-1', 'viewer'),
                      ('dup-b', 'user-1', 'proj-1', 'editor')
                    """
                )
            )

            with pytest.raises(RuntimeError, match="duplicate memberships remain"):
                module._verify_preflight(conn)
    finally:
        engine.dispose()