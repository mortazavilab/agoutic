import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker, Session
from .config import DATABASE_URL
from .models import Base, ProjectBlock

# Async engine (for future async endpoints)
async_engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

# Sync engine (for compatibility with existing code)
# Convert async URL to sync URL
sync_url = DATABASE_URL.replace("sqlite+aiosqlite://", "sqlite://")
sync_engine = create_engine(sync_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

def init_db_sync() -> None:
    """Initialize database using sync engine"""
    Base.metadata.create_all(bind=sync_engine)

async def init_db() -> None:
    """Initialize database using async engine"""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
