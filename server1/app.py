import json
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from sqlalchemy import select
from .db import SessionLocal, init_db, next_seq, row_to_dict
from .models import ProjectBlock
from .schemas import BlockCreate, BlockOut, BlockStreamOut

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="AGOUTIC Server 1 (State Engine)", version="2.3", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "online", "system": "AGOUTIC v2.3"}

@app.post("/block", response_model=BlockOut)
async def add_block(body: BlockCreate):
    async with SessionLocal() as session:
        seq = await next_seq(session, body.project_id)
        new_id = str(uuid.uuid4())

        row = ProjectBlock(
            id=new_id,
            project_id=body.project_id,
            seq=seq,
            type=body.type,
            status=body.status,
            payload_json=json.dumps(body.payload),
            parent_id=body.parent_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row_to_dict(row)

@app.get("/blocks", response_model=BlockStreamOut)
async def get_blocks(
    project_id: str = Query(..., min_length=1),
    since_seq: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
):
    async with SessionLocal() as session:
        q = (
            select(ProjectBlock)
            .where(ProjectBlock.project_id == project_id, ProjectBlock.seq > since_seq)
            .order_by(ProjectBlock.seq.asc())
            .limit(limit)
        )
        res = await session.execute(q)
        rows = res.scalars().all()

        blocks = [row_to_dict(r) for r in rows]
        latest = (blocks[-1]["seq"] if blocks else since_seq)

        return {"blocks": blocks, "latest_seq": latest}
