import datetime
import uuid
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from fastapi.concurrency import run_in_threadpool  # To run LLM without blocking
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Integer, JSON, DateTime, select, desc
from sqlalchemy.orm import sessionmaker, declarative_base

# ✅ Import from your package
from server1.schemas import BlockCreate, BlockOut, BlockStreamOut, BlockUpdate
from server1.agent_engine import AgentEngine
from server1.config import SKILLS_REGISTRY

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

# --- SCHEMAS ---
class ChatRequest(BaseModel):
    project_id: str
    message: str
    skill: str = "ENCODE_LongRead" # Default skill
    model: str = "default"         # Default model (devstral-2)

# --- HELPERS ---
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

def _create_block_internal(session, project_id, block_type, payload, status="NEW"):
    """
    Internal helper to insert a block and handle the sequence number safely.
    """
    # Calculate next sequence
    last_seq = session.query(ProjectBlock.seq)\
        .filter(ProjectBlock.project_id == project_id)\
        .order_by(desc(ProjectBlock.seq))\
        .first()
    
    next_seq = (last_seq[0] + 1) if last_seq else 1

    new_block = ProjectBlock(
        project_id=project_id,
        seq=next_seq,
        type=block_type,
        status=status,
        payload=payload
    )
    session.add(new_block)
    session.commit()
    session.refresh(new_block)
    return new_block

# --- ENDPOINTS ---

@app.get("/skills")
async def get_available_skills():
    """Return the list of all available skills."""
    return {
        "skills": list(SKILLS_REGISTRY.keys()),
        "count": len(SKILLS_REGISTRY)
    }

@app.post("/block", response_model=BlockOut)
async def create_block(block_in: BlockCreate):
    session = SessionLocal()
    try:
        new_block = _create_block_internal(
            session, 
            block_in.project_id, 
            block_in.type, 
            block_in.payload, 
            block_in.status
        )
        return row_to_dict(new_block)
    finally:
        session.close()

@app.get("/blocks", response_model=BlockStreamOut)
async def get_blocks(project_id: str, since_seq: int = 0, limit: int = 100):
    session = SessionLocal()
    try:
        query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == project_id)\
            .where(ProjectBlock.seq > since_seq)\
            .order_by(ProjectBlock.seq.asc())\
            .limit(limit)
        
        result = session.execute(query)
        blocks = result.scalars().all()
        
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
        block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
        if not block:
            raise HTTPException(status_code=404, detail="Block not found")
            
        if body.status is not None:
            block.status = body.status
        if body.payload is not None:
            block.payload = body.payload
            
        session.commit()
        session.refresh(block)
        return row_to_dict(block)
    finally:
        session.close()

# --- CHAT & AGENT LOGIC ---
@app.post("/chat")
async def chat_with_agent(req: ChatRequest):
    """
    1. Saves User Message -> DB
    2. Runs Agent Engine (LLM)
    3. Parses output for [[APPROVAL_NEEDED]] tag
    4. Saves Agent Plan -> DB (Clean text)
    5. If tag found -> Saves APPROVAL_GATE -> DB (Interactive buttons)
    """
    session = SessionLocal()
    try:
        # 1. Save USER_MESSAGE
        user_block = _create_block_internal(
            session,
            req.project_id,
            "USER_MESSAGE",
            {"text": req.message}
        )
        
        # 2. Run the Brain (in a thread so it doesn't block the server)
        # Initialize engine with the selected model (from UI request)
        engine = AgentEngine(model_key=req.model)
        
        # 'think' talks to Ollama. 
        raw_response = await run_in_threadpool(engine.think, req.message, req.skill)
        
        # 3. Parse for Skill Switch Tag
        import re
        skill_switch_match = re.search(r'\[\[SKILL_SWITCH_TO:\s*(\w+)\]\]', raw_response)
        if skill_switch_match:
            new_skill = skill_switch_match.group(1)
            if new_skill in SKILLS_REGISTRY:
                print(f"🔄 Agent switching from '{req.skill}' to '{new_skill}'")
                # Re-run with the new skill
                engine = AgentEngine(model_key=req.model)
                raw_response = await run_in_threadpool(engine.think, req.message, new_skill)
                req.skill = new_skill  # Update the skill for the response
        
        # 4. Parse the "Approval Gate Tag"
        trigger_tag = "[[APPROVAL_NEEDED]]"
        needs_approval = trigger_tag in raw_response
        
        # Clean all tags out of the text so the user doesn't see them
        clean_markdown = raw_response.replace(trigger_tag, "").strip()
        clean_markdown = re.sub(r'\[\[SKILL_SWITCH_TO:\s*\w+\]\]', '', clean_markdown).strip()
        
        # 4. Save AGENT_PLAN (The Text)
        # Status is DONE because the text itself is just informational. 
        # The flow control happens in the gate block below.
        agent_block = _create_block_internal(
            session,
            req.project_id,
            "AGENT_PLAN",
            {
                "markdown": clean_markdown, 
                "skill": req.skill, 
                "model": engine.model_name
            },
            status="DONE" 
        )
        
        # 5. Insert APPROVAL_GATE (The Buttons) if requested
        gate_block = None
        if needs_approval:
            gate_block = _create_block_internal(
                session,
                req.project_id,
                "APPROVAL_GATE",
                {"label": "Do you authorize the Agent to proceed with this plan?"},
                status="PENDING"
            )
        
        return {
            "status": "ok", 
            "user_block": row_to_dict(user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": row_to_dict(gate_block) if gate_block else None
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()