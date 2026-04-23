"""
Database connection and utilities for Cortex.

Delegates to common.database for engine/session creation.
Retains Cortex-specific helper functions.
"""
import json
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from common.database import (
    SessionLocal,
    AsyncSessionLocal,
    init_db_sync,
    init_db_async as init_db,
)
from cortex.models import ProjectBlock


async def next_seq(session: AsyncSession, project_id: str) -> int:
    q = select(func.coalesce(func.max(ProjectBlock.seq), 0)).where(ProjectBlock.project_id == project_id)
    res = await session.execute(q)
    return int(res.scalar_one()) + 1

def next_seq_sync(session: Session, project_id: str) -> int:
    """Sync version of next_seq for existing code"""
    q = select(func.coalesce(func.max(ProjectBlock.seq), 0)).where(ProjectBlock.project_id == project_id)
    res = session.execute(q)
    max_seq = res.scalar()
    return max_seq + 1

def row_to_dict(row: ProjectBlock) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "seq": row.seq,
        "type": row.type,
        "status": row.status,
        "payload": json.loads(row.payload_json),
        "parent_id": row.parent_id,
        "created_at": str(row.created_at),
    }
