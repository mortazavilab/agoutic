import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func
from .config import DATABASE_URL
from .models import Base, ProjectBlock

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def next_seq(session: AsyncSession, project_id: str) -> int:
    q = select(func.coalesce(func.max(ProjectBlock.seq), 0)).where(ProjectBlock.project_id == project_id)
    res = await session.execute(q)
    return int(res.scalar_one()) + 1

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
