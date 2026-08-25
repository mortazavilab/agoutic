import importlib.util
import datetime
import json

from sqlalchemy.pool import NullPool, StaticPool

import common.database as common_db
from common.database import Base
from cortex.models import ProjectBlock


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


def test_sync_session_local_keeps_loaded_attrs_after_commit_and_close(tmp_path, monkeypatch):
    db_path = tmp_path / "agoutic.sqlite"
    db_url = f"sqlite:///{db_path}"

    common_db.reset_engines()
    common_db.SessionLocal.reset()
    monkeypatch.setattr(common_db, "DATABASE_URL", db_url)

    engine = common_db.get_sync_engine()
    Base.metadata.create_all(bind=engine)

    session = common_db.SessionLocal()
    block = ProjectBlock(
        id="blk-detached",
        project_id="proj-1",
        owner_id="user-1",
        seq=1,
        type="USER_MESSAGE",
        status="DONE",
        payload_json=json.dumps({"text": "hello"}),
        parent_id=None,
        created_at=datetime.datetime(2025, 1, 1, 12, 0, 0),
    )
    session.add(block)
    session.commit()
    session.close()

    try:
        assert block.id == "blk-detached"
        assert block.project_id == "proj-1"
        assert block.payload_json == '{"text": "hello"}'
    finally:
        engine.dispose()
        common_db.SessionLocal.reset()
        common_db.reset_engines()