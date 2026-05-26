import importlib.util

from sqlalchemy.pool import NullPool, StaticPool

import common.database as common_db


def _has_aiosqlite() -> bool:
    return importlib.util.find_spec("aiosqlite") is not None


def test_file_backed_sqlite_uses_runtime_pragmas_and_null_pool(tmp_path, monkeypatch):
    db_path = tmp_path / "agoutic.sqlite"
    db_url = f"sqlite:///{db_path}"

    common_db.reset_engines()
    monkeypatch.setattr(common_db, "DATABASE_URL", db_url)

    engine = common_db.get_sync_engine()
    async_engine = common_db.get_async_engine() if _has_aiosqlite() else None

    try:
        assert isinstance(engine.pool, NullPool)
        if async_engine is not None:
            assert isinstance(async_engine.sync_engine.pool, NullPool)

        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
            assert int(conn.exec_driver_sql("PRAGMA busy_timeout").scalar()) == 30000
    finally:
        if async_engine is not None:
            async_engine.sync_engine.dispose()
        engine.dispose()
        common_db.reset_engines()


def test_in_memory_sqlite_keeps_static_pool(monkeypatch):
    common_db.reset_engines()
    monkeypatch.setattr(common_db, "DATABASE_URL", "sqlite:///:memory:")

    engine = common_db.get_sync_engine()
    async_engine = common_db.get_async_engine() if _has_aiosqlite() else None

    try:
        assert isinstance(engine.pool, StaticPool)
        if async_engine is not None:
            assert isinstance(async_engine.sync_engine.pool, StaticPool)
    finally:
        if async_engine is not None:
            async_engine.sync_engine.dispose()
        engine.dispose()
        common_db.reset_engines()