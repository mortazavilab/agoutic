from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

import common.database as common_db


ROOT = Path(__file__).resolve().parents[2]


def _reset_database_state() -> None:
    common_db.reset_engines()
    common_db.SessionLocal.reset()
    common_db.AsyncSessionLocal.reset()


def test_maintenance_migration_creates_system_settings_with_default_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "maintenance-migration.sqlite"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setattr(common_db, "DATABASE_URL", db_url)
    _reset_database_state()

    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    engine = None

    try:
        command.upgrade(alembic_cfg, "a9f1d7c3e5b2")
        engine = create_engine(db_url)

        with engine.begin() as conn:
            tables = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
            assert "system_settings" not in tables

        command.upgrade(alembic_cfg, "c4d7e2f9a1b3")

        with engine.begin() as conn:
            columns = {
                row[1]: row[2]
                for row in conn.execute(text("PRAGMA table_info(system_settings)"))
            }
            assert columns == {
                "key": "VARCHAR(100)",
                "value": "TEXT",
                "updated_at": "DATETIME",
                "updated_by": "VARCHAR",
            }

            rows = conn.execute(
                text(
                    """
                    SELECT key, value, updated_at, updated_by
                      FROM system_settings
                     ORDER BY key
                    """
                )
            ).mappings().all()
            assert rows == [
                {
                    "key": "maintenance_message",
                    "value": "",
                    "updated_at": rows[0]["updated_at"],
                    "updated_by": None,
                },
                {
                    "key": "maintenance_mode",
                    "value": "false",
                    "updated_at": rows[1]["updated_at"],
                    "updated_by": None,
                },
                {
                    "key": "maintenance_starts_at",
                    "value": "",
                    "updated_at": rows[2]["updated_at"],
                    "updated_by": None,
                },
            ]
            assert conn.execute(
                text("SELECT value FROM system_settings WHERE key = 'maintenance_mode'")
            ).scalar_one() == "false"
    finally:
        if engine is not None:
            engine.dispose()
        _reset_database_state()