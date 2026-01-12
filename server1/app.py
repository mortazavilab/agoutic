import datetime
import uuid
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from sqlalchemy import create_engine, Column, String, Integer, JSON, DateTime, select, desc
from sqlalchemy.orm import sessionmaker, declarative_base

# ✅ UPDATED IMPORT: Uses 'schemas' (plural)
from schemas import BlockCreate, BlockOut, BlockStreamOut, BlockUpdate

# --- CONFIG ---
DATABASE_URL = "sqlite:///./agoutic_v23.sqlite"

# --- DATABASE SETUP ---
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ProjectBlock(Base):
    __tablename__ = "project_blocks"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, index=True, nullable=False)
    seq = Column(Integer, index=True)  # Auto-incrementing sequence per project
    type = Column(String, nullable=False)
    status = Column(String, default="NEW")
    payload = Column(JSON, default={})
    parent_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

# --- APP ---
app = FastAPI()

# Helper: Convert DB Row to Pydantic Schema
def row_to_dict(row):
    return {
        "id": row.id,
        "project_id": row.project_id,
        "seq": row.seq,
        "type": row.type,
        "status": row.status,
        "payload": row.payload,
        "parent_id": row.parent_id,
        "created_at": row.created_at.isoformat()
    }

@app.post("/block", response_model=BlockOut)
async def create_block(block_in: BlockCreate):
    session = SessionLocal()
    try:
        # Calculate next sequence number for this project
        last_seq = session.query(ProjectBlock.seq)\
            .filter(ProjectBlock.project_id == block_in.project_id)\
            .order_by(desc(ProjectBlock.seq))\
            .first()
        
        next_seq = (last_seq[0] + 1) if last_seq else 1

        new_block = ProjectBlock(
            project_id=block_in.project_id,
            seq=next_seq,
            type=block_in.type,
            status=block_in.status,
            payload=block_in.payload,
            parent_id=block_in.parent_id
        )
        session.add(new_block)
        session.commit()
        session.refresh(new_block)
        return row_to_dict(new_block)
    finally:
        session.close()

@app.get("/blocks", response_model=BlockStreamOut)
async def get_blocks(project_id: str, since_seq: int = 0, limit: int = 100):
    session = SessionLocal()
    try:
        # Fetch only blocks newer than 'since_seq'
        query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == project_id)\
            .where(ProjectBlock.seq > since_seq)\
            .order_by(ProjectBlock.seq.asc())\
            .limit(limit)
        
        result = session.execute(query)
        blocks = result.scalars().all()
        
        # Calculate new high-water mark
        new_latest = since_seq
        if blocks:
            new_latest = blocks[-1].seq
            
        return {
            "blocks": [row_to_dict(b) for b in blocks],
            "latest_seq": new_latest
        }
    finally:
        session.close()

@app.patch("/block/{block_id}", response_model=BlockOut)
async def update_block(
    block_id: str = Path(..., min_length=1),
    body: BlockUpdate = ...
):
    session = SessionLocal()
    try:
        # Find block
        block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
        if not block:
            raise HTTPException(status_code=404, detail="Block not found")
            
        # Update fields if provided
        if body.status is not None:
            block.status = body.status
        if body.payload is not None:
            block.payload = body.payload
            
        session.commit()
        session.refresh(block)
        return row_to_dict(block)
    finally:
        session.close()