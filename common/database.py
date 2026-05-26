"""
Shared database infrastructure for all AGOUTIC services.

Provides:
  - DATABASE_URL from environment with SQLite fallback
  - Single shared Base for all ORM models
  - Engine factory functions (sync + async) that handle SQLite and Postgres
  - Session factory functions
  - Database initialization helpers
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, Engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool


# ---------------------------------------------------------------------------
# DATABASE_URL — single source of truth
# ---------------------------------------------------------------------------
# Canonical form is the SYNC driver (no +aiosqlite / +asyncpg suffix).
# Async variants are derived programmatically in _to_async_url().


def _default_sqlite_url() -> str:
    """Build the default SQLite URL from AGOUTIC_DATA."""
    agoutic_code = Path(
        os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent)
    )
    agoutic_data = Path(os.getenv("AGOUTIC_DATA", agoutic_code / "data"))
    db_folder = agoutic_data / "database"
    db_folder.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_folder / 'agoutic_v24.sqlite'}"


DATABASE_URL: str = os.getenv("DATABASE_URL", _default_sqlite_url())

_SQLITE_BUSY_TIMEOUT_SECONDS = float(os.getenv("SQLITE_BUSY_TIMEOUT_SECONDS", "30"))
_SQLITE_JOURNAL_MODE = str(os.getenv("SQLITE_JOURNAL_MODE", "WAL") or "WAL").strip().upper()


def is_sqlite(url: str | None = None) -> bool:
    """Check if the given (or default) URL targets SQLite."""
    return (url or DATABASE_URL).startswith("sqlite")


def _is_in_memory_sqlite(url: str) -> bool:
    """Return True when the SQLite URL uses an in-memory database."""
    return url.startswith("sqlite") and (":memory:" in url or "mode=memory" in url)


# ---------------------------------------------------------------------------
# URL derivation helpers
# ---------------------------------------------------------------------------


def _to_async_url(url: str) -> str:
    """Convert a sync DATABASE_URL to its async-driver equivalent."""
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _sync_connect_args(url: str) -> dict:
    """Return connect_args needed for the sync driver."""
    if url.startswith("sqlite"):
        return {
            "check_same_thread": False,
            "timeout": _SQLITE_BUSY_TIMEOUT_SECONDS,
        }
    return {}


def _async_connect_args(url: str) -> dict:
    """Return connect_args needed for the async driver."""
    if url.startswith("sqlite"):
        return {"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS}
    return {}


def _engine_pool_kwargs(url: str) -> dict:
    """Return engine kwargs that keep SQLite stable under multi-service load."""
    if not url.startswith("sqlite"):
        return {}
    if _is_in_memory_sqlite(url):
        return {"poolclass": StaticPool}
    # File-backed SQLite behaves better without a tiny shared QueuePool.
    return {"poolclass": NullPool}


def _configure_sqlite_pragmas(engine: Engine, url: str) -> None:
    """Apply SQLite runtime pragmas to every new DB-API connection."""
    if not url.startswith("sqlite"):
        return

    journal_mode = None if _is_in_memory_sqlite(url) else _SQLITE_JOURNAL_MODE
    busy_timeout_ms = max(int(_SQLITE_BUSY_TIMEOUT_SECONDS * 1000), 0)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            cursor.execute("PRAGMA foreign_keys=ON")
            if journal_mode:
                cursor.execute(f"PRAGMA journal_mode={journal_mode}")
                cursor.fetchone()
                cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()
    return {}


# ---------------------------------------------------------------------------
# Shared declarative Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Engine factories (lazily cached)
# ---------------------------------------------------------------------------

_sync_engine: Engine | None = None
_async_engine: AsyncEngine | None = None


def get_sync_engine(url: str | None = None) -> Engine:
    """Return (and cache) the sync SQLAlchemy engine."""
    global _sync_engine
    if _sync_engine is None:
        _url = url or DATABASE_URL
        _sync_engine = create_engine(
            _url,
            connect_args=_sync_connect_args(_url),
            echo=False,
            **_engine_pool_kwargs(_url),
        )
        _configure_sqlite_pragmas(_sync_engine, _url)
    return _sync_engine


def get_async_engine(url: str | None = None) -> AsyncEngine:
    """Return (and cache) the async SQLAlchemy engine."""
    global _async_engine
    if _async_engine is None:
        _url = _to_async_url(url or DATABASE_URL)
        _async_engine = create_async_engine(
            _url,
            connect_args=_async_connect_args(_url),
            echo=False,
            **_engine_pool_kwargs(_url),
        )
        _configure_sqlite_pragmas(_async_engine.sync_engine, _url)
    return _async_engine


def reset_engines() -> None:
    """Reset cached engines (useful for tests)."""
    global _sync_engine, _async_engine
    _sync_engine = None
    _async_engine = None


# ---------------------------------------------------------------------------
# Session factories — lazy callable proxies
# ---------------------------------------------------------------------------
# Deferred binding: the real sessionmaker is created on first call,
# allowing tests to patch DATABASE_URL before any engine is created.


class _LazySyncSessionLocal:
    """Proxy that creates the real sync sessionmaker on first call."""

    _factory: sessionmaker | None = None

    def __call__(self, **kw: object) -> Session:
        if self._factory is None:
            self._factory = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=get_sync_engine(),
            )
        return self._factory(**kw)

    def configure(self, engine: Engine) -> None:
        """Rebind to a specific engine (used by tests)."""
        self._factory = sessionmaker(
            autocommit=False, autoflush=False, bind=engine
        )

    def reset(self) -> None:
        self._factory = None


class _LazyAsyncSessionLocal:
    """Proxy that creates the real async sessionmaker on first call."""

    _factory: async_sessionmaker | None = None

    def __call__(self, **kw: object) -> AsyncSession:
        if self._factory is None:
            self._factory = async_sessionmaker(
                bind=get_async_engine(),
                expire_on_commit=False,
                class_=AsyncSession,
            )
        return self._factory(**kw)

    def configure(self, engine: AsyncEngine) -> None:
        """Rebind to a specific engine (used by tests)."""
        self._factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, class_=AsyncSession
        )

    def reset(self) -> None:
        self._factory = None


SessionLocal = _LazySyncSessionLocal()
AsyncSessionLocal = _LazyAsyncSessionLocal()


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------


def _import_all_models() -> None:
    """Import every model module so Base.metadata knows about all tables.

    Called by init_db functions and by Alembic's env.py.
    """
    import cortex.models  # noqa: F401
    import launchpad.models  # noqa: F401
    import analyzer.models  # noqa: F401


# ---------------------------------------------------------------------------
# Initialization helpers
# ---------------------------------------------------------------------------


def init_db_sync() -> None:
    """Ensure all model classes are imported so metadata is populated.

    Schema creation/migration is handled exclusively by Alembic
    (``alembic upgrade head``).  This function no longer calls
    ``Base.metadata.create_all()`` to avoid conflicts with migrations.
    """
    _import_all_models()


async def init_db_async() -> None:
    """Async counterpart of :func:`init_db_sync`.

    Schema creation/migration is handled exclusively by Alembic.
    """
    _import_all_models()
