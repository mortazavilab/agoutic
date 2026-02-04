import asyncio
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
            
        old_status = block.status
        
        if body.status is not None:
            block.status = body.status
        if body.payload is not None:
            block.payload = body.payload
            
        session.commit()
        session.refresh(block)
        
        # If an APPROVAL_GATE was just approved, trigger job submission
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "APPROVED":
            # Launch background task to submit job to Server3
            import asyncio
            asyncio.create_task(submit_job_after_approval(block.project_id, block_id))
        
        return row_to_dict(block)
    finally:
        session.close()

async def extract_job_parameters_from_conversation(session, project_id: str) -> dict:
    """
    Extract job parameters from conversation history using LLM.
    
    Analyzes all USER_MESSAGE and AGENT_PLAN blocks to determine:
    - sample_name
    - mode (DNA/RNA/CDNA)
    - input_directory (path to pod5 files)
    - reference_genome (GRCh38, mm39, etc.)
    - modifications (optional)
    """
    # Fetch all blocks for this project
    query = select(ProjectBlock)\
        .where(ProjectBlock.project_id == project_id)\
        .order_by(ProjectBlock.seq.asc())
    
    result = session.execute(query)
    blocks = result.scalars().all()
    
    # Build conversation context
    conversation = []
    user_messages = []
    for block in blocks:
        if block.type == "USER_MESSAGE":
            text = block.payload.get('text', '')
            conversation.append(f"User: {text}")
            user_messages.append(text)
        elif block.type == "AGENT_PLAN":
            conversation.append(f"Agent: {block.payload.get('markdown', '')}")
    
    if not conversation:
        return None
    
    conversation_text = "\n".join(conversation)
    all_user_text_original = " ".join(user_messages)  # Keep original case for path extraction
    all_user_text = all_user_text_original.lower()  # Lowercase for keyword matching
    
    # First, use simple heuristics for quick detection
    params = {
        "sample_name": None,
        "mode": None,
        "input_directory": None,
        "reference_genome": None,
        "modifications": None
    }
    
    # Detect genome from keywords
    if "mouse" in all_user_text or "mm39" in all_user_text or "mm10" in all_user_text:
        params["reference_genome"] = "mm39"
    elif "human" in all_user_text or "grch38" in all_user_text or "hg38" in all_user_text:
        params["reference_genome"] = "GRCh38"
    else:
        params["reference_genome"] = "mm39"  # Default to mouse
    
    # Detect mode from keywords
    if "rna" in all_user_text and "cdna" not in all_user_text:
        params["mode"] = "RNA"
    elif "cdna" in all_user_text:
        params["mode"] = "CDNA"
    elif "dna" in all_user_text or "genomic" in all_user_text or "fiber" in all_user_text:
        params["mode"] = "DNA"
    else:
        params["mode"] = "DNA"  # Default to DNA
    
    # Look for paths in user messages (use ORIGINAL case to preserve path)
    import re
    path_pattern = r'(/[^\s,]+(?:/[^\s,]+)*)'
    paths = re.findall(path_pattern, all_user_text_original)
    if paths:
        # Strip trailing punctuation (commas, periods, etc.)
        cleaned_path = paths[0].rstrip('.,;:!?')
        params["input_directory"] = cleaned_path
    else:
        params["input_directory"] = "/data/samples/test"
    
    # Extract sample name from context
    # Look for patterns like "named X", "called X", "sample: X", etc.
    sample_patterns = [
        r'named\s+([a-zA-Z0-9_-]+)',  # "named Ali1"
        r'called\s+([a-zA-Z0-9_-]+)',  # "called Ali1"
        r'sample[:\s]+name[:\s]+([a-zA-Z0-9_-]+)',  # "sample name: Ali1"
        r'name[:\s]+([a-zA-Z0-9_-]+)',  # "name: Ali1"
        r'sample[:\s]+([a-zA-Z0-9_-]+)',  # "sample: Ali1"
    ]
    for pattern in sample_patterns:
        match = re.search(pattern, all_user_text, re.IGNORECASE)
        if match:
            candidate = match.group(1)
            # Skip common words that aren't sample names
            if candidate.lower() not in ['is', 'the', 'a', 'an', 'this', 'that', 'it']:
                params["sample_name"] = candidate
                break
    
    if not params["sample_name"]:
        # Use genome type + project timestamp as default
        genome_type = "mouse" if params["reference_genome"] == "mm39" else "human"
        params["sample_name"] = f"{genome_type}_sample_{project_id.split('_')[-1]}"
    
    print(f"✅ Extracted parameters (heuristics): {params}")
    return params

async def submit_job_after_approval(project_id: str, gate_block_id: str):
    """
    Background task to submit a job to Server3 after approval.
    Extracts parameters from conversation history and creates an EXECUTION_JOB block.
    """
    import requests
    session = SessionLocal()
    
    try:
        # Extract parameters from conversation history
        job_params = await extract_job_parameters_from_conversation(session, project_id)
        
        if not job_params:
            # Failed to extract parameters, create error block
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "error": "Failed to extract job parameters from conversation",
                    "message": "Could not determine sample name, data type, or input directory",
                    "job_status": {
                        "status": "FAILED",
                        "progress_percent": 0,
                        "message": "Parameter extraction failed",
                        "tasks": {}
                    }
                },
                status="FAILED"
            )
            print(f"❌ Failed to extract parameters for project {project_id}")
            return
        
        job_data = {
            "project_id": project_id,
            "sample_name": job_params.get("sample_name", f"sample_{project_id.split('_')[-1]}"),
            "mode": job_params.get("mode", "DNA"),
            "input_directory": job_params.get("input_directory", "/data/samples/test"),
            "reference_genome": job_params.get("reference_genome", "mm39"),
            "modifications": job_params.get("modifications"),
        }
        
        print(f"📋 Extracted parameters: {job_data}")
        
        # Submit job to Server3
        resp = requests.post(
            "http://127.0.0.1:8003/jobs/submit",
            json=job_data,
            timeout=10,
        )
        
        if resp.status_code == 200:
            result = resp.json()
            run_uuid = result.get("run_uuid")
            
            # Create EXECUTION_JOB block
            job_block = _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "run_uuid": run_uuid,
                    "sample_name": job_data["sample_name"],
                    "mode": job_data["mode"],
                    "status": "SUBMITTED",
                    "message": f"Job submitted: {run_uuid}",
                    "job_status": {
                        "status": "PENDING",
                        "progress_percent": 0,
                        "message": "Job submitted, waiting to start...",
                        "tasks": {}
                    },
                    "logs": []
                },
                status="RUNNING"
            )
            
            print(f"✅ Job submitted: {run_uuid}")
            
            # Start polling job status in background
            asyncio.create_task(poll_job_status(project_id, job_block.id, run_uuid))
        else:
            # Create error block
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "error": f"Failed to submit job: {resp.status_code}",
                    "message": resp.text,
                    "job_status": {
                        "status": "FAILED",
                        "progress_percent": 0,
                        "message": f"Submission failed: {resp.status_code}",
                        "tasks": {}
                    }
                },
                status="FAILED"
            )
            print(f"❌ Job submission failed: {resp.status_code}")
    
    except Exception as e:
        # Create error block
        _create_block_internal(
            session,
            project_id,
            "EXECUTION_JOB",
            {
                "error": str(e),
                "message": "Failed to submit job to Server3",
                "job_status": {
                    "status": "FAILED",
                    "progress_percent": 0,
                    "message": f"Error: {str(e)}",
                    "tasks": {}
                }
            },
            status="FAILED"
        )
        print(f"❌ Job submission error: {e}")
    finally:
        session.close()

async def poll_job_status(project_id: str, block_id: str, run_uuid: str):
    """
    Background task to poll Server3 for job status and update the EXECUTION_JOB block.
    Continues until job is completed or failed.
    """
    import requests
    
    poll_interval = 3  # seconds
    max_polls = 600  # 30 minutes max
    
    for poll_num in range(max_polls):
        await asyncio.sleep(poll_interval)
        
        session = SessionLocal()
        try:
            # Get current status from Server3
            status_resp = requests.get(
                f"http://127.0.0.1:8003/jobs/{run_uuid}/status",
                timeout=5,
            )
            
            if status_resp.status_code != 200:
                print(f"⚠️ Failed to get status for {run_uuid}")
                continue
            
            status_data = status_resp.json()
            
            # Get logs from Server3
            logs_resp = requests.get(
                f"http://127.0.0.1:8003/jobs/{run_uuid}/logs",
                params={"limit": 50},
                timeout=5,
            )
            logs = logs_resp.json().get("logs", []) if logs_resp.status_code == 200 else []
            
            # Update the block with new data
            block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
            if block:
                # Create new payload dict to ensure SQLAlchemy detects the change
                payload = dict(block.payload)
                payload["job_status"] = status_data
                payload["logs"] = logs
                
                # Update block status based on job status
                job_status = status_data.get("status", "UNKNOWN")
                if job_status == "COMPLETED":
                    block.status = "DONE"
                elif job_status == "FAILED":
                    block.status = "FAILED"
                else:
                    block.status = "RUNNING"
                
                # Reassign payload to trigger SQLAlchemy update
                block.payload = payload
                session.commit()
                session.refresh(block)
                
                print(f"🔄 Updated job {run_uuid}: {job_status} ({status_data.get('progress_percent', 0)}%)")
                
                # Stop polling if job is done
                if job_status in ("COMPLETED", "FAILED"):
                    print(f"✅ Job {run_uuid} finished with status: {job_status}")
                    break
        
        except Exception as e:
            print(f"⚠️ Error polling job {run_uuid}: {e}")
        finally:
            session.close()
    
    print(f"🛑 Stopped polling job {run_uuid}")


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
        # Check if there's an active skill in progress (no approval gate yet)
        # Look for the last AGENT_PLAN block to see what skill was being used
        last_agent_query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == req.project_id)\
            .where(ProjectBlock.type == "AGENT_PLAN")\
            .order_by(desc(ProjectBlock.seq))\
            .limit(1)
        
        last_agent_result = session.execute(last_agent_query)
        last_agent_block = last_agent_result.scalar_one_or_none()
        
        # Check if there's a pending approval gate or completed one
        approval_query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == req.project_id)\
            .where(ProjectBlock.type == "APPROVAL_GATE")\
            .order_by(desc(ProjectBlock.seq))\
            .limit(1)
        
        approval_result = session.execute(approval_query)
        approval_block = approval_result.scalar_one_or_none()
        
        # If the last agent block exists and there's no approval gate after it,
        # we're in the middle of a conversation - continue with the same skill
        active_skill = req.skill
        if last_agent_block and not approval_block:
            # Continue with the skill from the last agent response
            last_skill = last_agent_block.payload.get("skill")
            if last_skill:
                active_skill = last_skill
                print(f"📌 Continuing with active skill: {active_skill}")
        elif approval_block and approval_block.status in ["APPROVED", "REJECTED"]:
            # If the last approval was resolved, allow new skill from UI
            active_skill = req.skill
            print(f"🆕 Starting fresh with skill: {active_skill}")
        
        # 1. Save USER_MESSAGE
        user_block = _create_block_internal(
            session,
            req.project_id,
            "USER_MESSAGE",
            {"text": req.message}
        )
        
        # Build conversation history for the LLM
        # Get all USER_MESSAGE and AGENT_PLAN blocks for this project
        history_query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == req.project_id)\
            .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN"]))\
            .order_by(ProjectBlock.seq.asc())
        
        history_result = session.execute(history_query)
        history_blocks = history_result.scalars().all()
        
        # Build conversation history in OpenAI format
        conversation_history = []
        for block in history_blocks[:-1]:  # Exclude the message we just added
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block.payload.get("text", "")
                })
            elif block.type == "AGENT_PLAN":
                conversation_history.append({
                    "role": "assistant",
                    "content": block.payload.get("markdown", "")
                })
        
        # 2. Run the Brain (in a thread so it doesn't block the server)
        # Initialize engine with the selected model (from UI request)
        engine = AgentEngine(model_key=req.model)
        
        # 'think' talks to Ollama using the active skill and conversation history
        raw_response = await run_in_threadpool(
            engine.think, 
            req.message, 
            active_skill,
            conversation_history
        )
        
        # 3. Parse for Skill Switch Tag
        import re
        skill_switch_match = re.search(r'\[\[SKILL_SWITCH_TO:\s*(\w+)\]\]', raw_response)
        if skill_switch_match:
            new_skill = skill_switch_match.group(1)
            if new_skill in SKILLS_REGISTRY:
                print(f"🔄 Agent switching from '{active_skill}' to '{new_skill}'")
                # Re-run with the new skill
                engine = AgentEngine(model_key=req.model)
                raw_response = await run_in_threadpool(
                    engine.think, 
                    req.message, 
                    new_skill,
                    conversation_history
                )
                active_skill = new_skill  # Update the skill for the response
        
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
                "skill": active_skill,  # Store the active skill
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