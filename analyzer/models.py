"""Database models for Analyzer.

Analyzer primarily reads from launchpad's DogmeJob table.
DogmeJob is imported from launchpad (single source of truth).
"""

from sqlalchemy import String, Integer, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional

from common.database import Base

# Re-export DogmeJob from launchpad (Analyzer reads this table)
from launchpad.models import DogmeJob  # noqa: F401


class AnalysisCache(Base):
    """Cache for parsed analysis results to avoid re-parsing large files."""
    __tablename__ = "analysis_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_uuid: Mapped[str] = mapped_column(String, nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=False)  # MD5 or SHA256
    parsed_data: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
