"""Database connection for Analyzer.

Delegates to common.database for engine/session creation.
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session
from common.database import SessionLocal


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Get database session with context manager."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Get database session (for dependency injection)."""
    return SessionLocal()
