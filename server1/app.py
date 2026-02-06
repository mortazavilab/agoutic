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
from server1.config import SKILLS_REGISTRY, GENOME_ALIASES, AVAILABLE_GENOMES

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
        
        # If an APPROVAL_GATE was rejected, trigger rejection handling
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "REJECTED":
            import asyncio
            asyncio.create_task(handle_rejection(block.project_id, block_id))
        
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
        "input_type": "pod5",  # Default to pod5
        "entry_point": None,  # Dogme entry point
        "reference_genome": [],  # Now a list for multi-genome support
        "modifications": None,
        # Advanced parameters (optional)
        "modkit_filter_threshold": None,  # Will use default 0.9 if not specified
        "min_cov": None,  # Will default based on mode if not specified
        "per_mod": None,  # Will use default 5 if not specified
        "accuracy": None,  # Will use default "sup" if not specified
    }
    
    # Detect Dogme entry point from conversation
    if "only basecall" in all_user_text or "just basecalling" in all_user_text or "basecall only" in all_user_text:
        params["entry_point"] = "basecall"
        params["input_type"] = "pod5"
    elif "call modifications" in all_user_text or "run modkit" in all_user_text or "extract modifications" in all_user_text:
        params["entry_point"] = "modkit"
        params["input_type"] = "bam"  # modkit needs mapped BAM
    elif "generate report" in all_user_text or "create summary" in all_user_text or "reports only" in all_user_text:
        params["entry_point"] = "reports"
    elif "annotate" in all_user_text and ("transcripts" in all_user_text or "rna" in all_user_text):
        params["entry_point"] = "annotateRNA"
        params["input_type"] = "bam"  # annotateRNA needs mapped BAM
    elif "unmapped bam" in all_user_text or "remap" in all_user_text:
        params["entry_point"] = "remap"
        params["input_type"] = "bam"
    elif ".bam" in all_user_text_original:
        # Detect if BAM is mapped or unmapped based on context
        if "mapped" in all_user_text and "unmapped" not in all_user_text:
            params["input_type"] = "bam"
            # Will need to determine entry point based on other keywords
        else:
            params["entry_point"] = "remap"
            params["input_type"] = "bam"
    
    # Detect genome from keywords - support multiple genomes
    import re
    genome_keywords = ["human", "mouse", "hg38", "mm39", "mm10", "grch38"]
    found_genomes = set()
    
    # Find all genome mentions
    for keyword in genome_keywords:
        if keyword in all_user_text:
            canonical = GENOME_ALIASES.get(keyword, keyword)
            found_genomes.add(canonical)
    
    # Check for "both X and Y" pattern
    multi_genome_pattern = r'both\s+(\w+)\s+and\s+(\w+)'
    multi_match = re.search(multi_genome_pattern, all_user_text)
    if multi_match:
        g1 = GENOME_ALIASES.get(multi_match.group(1), multi_match.group(1))
        g2 = GENOME_ALIASES.get(multi_match.group(2), multi_match.group(2))
        found_genomes.add(g1)
        found_genomes.add(g2)
    
    # Convert to list, default to mouse if none found
    params["reference_genome"] = list(found_genomes) if found_genomes else ["mm39"]
    
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
    path_pattern = r'(/[^\s,]+(?:/[^\s,]+)*)'
    paths = re.findall(path_pattern, all_user_text_original)
    if paths:
        # Strip trailing punctuation (commas, periods, etc.)
        cleaned_path = paths[0].rstrip('.,;:!?')
        params["input_directory"] = cleaned_path
    else:
        params["input_directory"] = "/data/samples/test"
    
    # Extract sample name from context
    # First check for explicit "X is sample name" or "sample name is X" patterns
    explicit_patterns = [
        r'([a-zA-Z0-9_-]+)\s+is\s+(?:the\s+)?sample\s+name',  # "Jamshid is sample name"
        r'sample\s+name\s+is\s+([a-zA-Z0-9_-]+)',  # "sample name is Jamshid"
        r'named\s+([a-zA-Z0-9_-]+)',  # "named Ali1"
        r'called\s+([a-zA-Z0-9_-]+)',  # "called Ali1"
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, all_user_text, re.IGNORECASE)
        if match:
            candidate = match.group(1)
            # Skip common words and genome names
            if candidate.lower() not in ['is', 'the', 'a', 'an', 'this', 'that', 'it', 'at', 'in', 'on', 'mm39', 'grch38', 'hg38', 'mm10']:
                params["sample_name"] = candidate
                break
    
    # If not found via explicit pattern, check standalone answers (but filter more carefully)
    if not params["sample_name"]:
        for msg in reversed(user_messages):
            msg_lower = msg.lower().strip()
            # Check if this is a short, standalone response (likely an answer to a question)
            if len(msg.split()) <= 5 and len(msg) < 50:
                # Extract just the name part if it says "X is sample name"
                name_match = re.search(r'^([a-zA-Z0-9_-]+)(?:\s+is\s+sample\s+name)?$', msg, re.IGNORECASE)
                if name_match:
                    potential_name = name_match.group(1)
                    # Not a path, not a common word, not a genome name
                    if (potential_name.lower() not in ['dna', 'rna', 'cdna', 'mm39', 'grch38', 'hg38', 'mm10', 'human', 'mouse', 'yes', 'no', 'sup', 'hac', 'fast']
                        and '/' not in potential_name):
                        params["sample_name"] = potential_name
                        break
    
    if not params["sample_name"]:
        # Use genome type + project timestamp as default
        genome_type = "mouse" if "mm39" in params["reference_genome"] else "human"
        params["sample_name"] = f"{genome_type}_sample_{project_id.split('_')[-1]}"
    
    # Extract advanced parameters if mentioned
    # modkit_filter_threshold (handle "threshold of 0.85" or "threshold: 0.85")
    threshold_pattern = r'(?:modkit\s+)?(?:threshold|filter)(?:[:\s]+of\s+|[:\s]+)([0-9.]+)'
    threshold_match = re.search(threshold_pattern, all_user_text)
    if threshold_match:
        try:
            params["modkit_filter_threshold"] = float(threshold_match.group(1))
        except ValueError:
            pass
    
    # min_cov (handle "coverage of 10" or "min cov: 10")
    mincov_pattern = r'(?:min[_\s]*cov|minimum[_\s]*coverage)(?:[:\s]+of\s+|[:\s]+)(\d+)'
    mincov_match = re.search(mincov_pattern, all_user_text)
    if mincov_match:
        params["min_cov"] = int(mincov_match.group(1))
    
    # per_mod (handle "per mod of 8" or "per_mod: 8")
    permod_pattern = r'(?:per[_\s]*mod|percentage)(?:[:\s]+of\s+|[:\s]+)(\d+)'
    permod_match = re.search(permod_pattern, all_user_text)
    if permod_match:
        params["per_mod"] = int(permod_match.group(1))
    
    # accuracy (sup, hac, fast)
    if "accuracy" in all_user_text:
        if "hac" in all_user_text:
            params["accuracy"] = "hac"
        elif "fast" in all_user_text:
            params["accuracy"] = "fast"
        elif "sup" in all_user_text:
            params["accuracy"] = "sup"
    
    print(f"✅ Extracted parameters (heuristics): {params}")
    return params


async def handle_rejection(project_id: str, gate_block_id: str):
    """
    Background task to handle rejection of an approval gate.
    Passes feedback to LLM to regenerate plan. Limits to 3 attempts.
    """
    session = SessionLocal()
    
    try:
        # Get the rejected gate block
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if not gate_block:
            print(f"❌ Gate block {gate_block_id} not found")
            return
        
        # Get rejection details from payload
        rejection_reason = gate_block.payload.get("rejection_reason", "No reason provided")
        attempt_number = gate_block.payload.get("attempt_number", 1)
        rejection_history = gate_block.payload.get("rejection_history", [])
        
        # Add to rejection history
        rejection_history.append({
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "reason": rejection_reason,
            "attempt": attempt_number
        })
        
        # Update gate block with history
        gate_payload = dict(gate_block.payload)
        gate_payload["rejection_history"] = rejection_history
        gate_block.payload = gate_payload
        session.commit()
        
        print(f"🔄 Handling rejection for {project_id}, attempt {attempt_number}/3")
        print(f"   Reason: {rejection_reason}")
        
        # Check attempt limit
        if attempt_number >= 3:
            print(f"❌ Max attempts (3) reached for {project_id}. Creating manual parameter form.")
            
            # Extract parameters for manual form
            params = await extract_job_parameters_from_conversation(session, project_id)
            
            # Create a manual parameter form block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": f"""### ⚠️ Manual Configuration Required (Attempt 3/3)

I've tried 3 times but couldn't meet your requirements. Please verify and edit these parameters manually:

**Extracted Parameters:**
- Sample Name: `{params.get('sample_name', 'unknown')}`
- Mode: `{params.get('mode', 'DNA')}`
- Input Type: `{params.get('input_type', 'pod5')}`
- Input Directory: `{params.get('input_directory', '/path/to/data')}`
- Reference Genome(s): `{', '.join(params.get('reference_genome', ['mm39']))}`
- Modifications: `{params.get('modifications', 'auto')}`

Please use the parameter editing form below to make corrections.""",
                    "skill": gate_block.payload.get("skill", "analyze_local_sample"),
                    "model": gate_block.payload.get("model", "default")
                },
                status="NEW"
            )
            
            # Create a new approval gate with manual mode flag
            _create_block_internal(
                session,
                project_id,
                "APPROVAL_GATE",
                {
                    "label": "Manual Configuration - Verify Parameters",
                    "extracted_params": params,
                    "manual_mode": True,
                    "attempt_number": 3,
                    "rejection_history": rejection_history
                },
                status="PENDING"
            )
            
            return
        
        # Build conversation history for LLM
        query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == project_id)\
            .order_by(ProjectBlock.seq.asc())
        
        result = session.execute(query)
        blocks = result.scalars().all()
        
        conversation_history = []
        active_skill = None
        
        for block in blocks:
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
                active_skill = block.payload.get("skill")
        
        # Add rejection feedback as user message
        conversation_history.append({
            "role": "user",
            "content": f"I'm rejecting your plan (attempt {attempt_number}/3). Please revise it.\n\nFeedback: {rejection_reason}\n\nPlease generate a new plan that addresses my concerns."
        })
        
        # Get the active skill and model
        skill_name = active_skill or gate_block.payload.get("skill", "analyze_local_sample")
        model_name = gate_block.payload.get("model", "default")
        
        # Call LLM to regenerate plan
        agent_engine = AgentEngine(model_name=model_name)
        
        try:
            llm_response = await run_in_threadpool(
                agent_engine.think,
                skill_name=skill_name,
                user_message=f"[REVISION REQUEST] {rejection_reason}",
                conversation_history=conversation_history
            )
            
            # Create new AGENT_PLAN block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": llm_response,
                    "skill": skill_name,
                    "model": model_name,
                    "revision_attempt": attempt_number + 1
                },
                status="NEW"
            )
            
            # Extract parameters for new approval gate
            params = await extract_job_parameters_from_conversation(session, project_id)
            
            # Create new APPROVAL_GATE
            _create_block_internal(
                session,
                project_id,
                "APPROVAL_GATE",
                {
                    "label": f"Revised Plan (Attempt {attempt_number + 1}/3)",
                    "extracted_params": params,
                    "attempt_number": attempt_number + 1,
                    "rejection_history": rejection_history,
                    "skill": skill_name,
                    "model": model_name
                },
                status="PENDING"
            )
            
            print(f"✅ Generated revised plan for {project_id} (attempt {attempt_number + 1}/3)")
            
        except Exception as e:
            print(f"❌ LLM failed during revision: {e}")
            # Create error block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": f"### ⚠️ Error\n\nFailed to generate revised plan: {str(e)}\n\nPlease try again or use manual configuration.",
                    "error": str(e)
                },
                status="ERROR"
            )
    
    finally:
        session.close()

async def submit_job_after_approval(project_id: str, gate_block_id: str):
    """
    Background task to submit a job to Server3 after approval.
    Uses edited_params if available, otherwise falls back to extracted_params.
    """
    import requests
    session = SessionLocal()
    
    try:
        # Get the gate block to check for edited params
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        
        if not gate_block:
            print(f"❌ Gate block {gate_block_id} not found")
            return
        
        # Prefer edited_params over extracted_params
        job_params = gate_block.payload.get("edited_params") or gate_block.payload.get("extracted_params")
        
        if not job_params:
            # Fallback: extract from conversation
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
        
        # Normalize reference_genome to list for Server3
        ref_genome = job_params.get("reference_genome", ["mm39"])
        if isinstance(ref_genome, str):
            ref_genome = [ref_genome]
        
        job_data = {
            "project_id": project_id,
            "sample_name": job_params.get("sample_name", f"sample_{project_id.split('_')[-1]}"),
            "mode": job_params.get("mode", "DNA"),
            "input_type": job_params.get("input_type", "pod5"),
            "input_directory": job_params.get("input_directory", "/data/samples/test"),
            "reference_genome": ref_genome,  # Now a list
            "modifications": job_params.get("modifications"),
            "entry_point": job_params.get("entry_point"),
            # Advanced parameters - use Server3 defaults if None
            "modkit_filter_threshold": job_params.get("modkit_filter_threshold") or 0.9,
            "min_cov": job_params.get("min_cov"),  # Let Server3 handle None (mode-dependent default)
            "per_mod": job_params.get("per_mod") or 5,
            "accuracy": job_params.get("accuracy") or "sup",
        }
        
        print(f"📋 Job parameters (using {'edited' if gate_block.payload.get('edited_params') else 'extracted'}): {job_data}")
        
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
            error_detail = resp.text
            try:
                error_json = resp.json()
                error_detail = error_json.get("detail", resp.text)
            except:
                pass
            
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "error": f"Failed to submit job: {resp.status_code}",
                    "message": error_detail,
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
            print(f"   Error details: {error_detail}")
            print(f"   Job data sent: {job_data}")
    
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
        
        # Check if user is asking for capabilities
        user_msg_lower = req.message.lower()
        if any(phrase in user_msg_lower for phrase in ["what can you do", "what are your capabilities", "help", "what can i do", "list features", "show capabilities"]):
            # Return capabilities list
            capabilities_text = """### 🧬 AGOUTIC Capabilities

I can help you analyze nanopore sequencing data using the Dogme pipeline. Here's what I can do:

#### **📊 Full Analysis Workflows**
- **DNA Analysis**: Genomic DNA with modification calling (5mCG, 5hmCG, 6mA)
- **RNA Analysis**: Direct RNA with modification detection (m6A, pseudouridine, m5C)
- **cDNA Analysis**: cDNA sequencing with transcript quantification

#### **🔄 Flexible Entry Points**
- **Full Pipeline** (pod5 → results): Complete analysis from raw data
- **Basecalling Only**: Convert pod5 files to unmapped BAM
- **Remap**: Start from unmapped BAM files
- **Modification Calling**: Run modkit on mapped BAMs
- **Transcript Annotation**: Annotate RNA/cDNA BAMs with transcript info
- **Report Generation**: Create summary reports from existing outputs

#### **🧬 Multi-Genome Support**
- Map to multiple reference genomes in one run
- Supported genomes: **GRCh38** (human), **mm39** (mouse)
- Example: "Analyze against both human and mouse genomes"

#### **📁 Input Flexibility**
- **pod5 files**: Raw nanopore data
- **Unmapped BAM**: Already basecalled data
- **Mapped BAM**: For modification calling or annotation

#### **🎛️ Parameter Control**
- Interactive parameter editing before job submission
- Specify sample names, reference genomes, modifications
- Advanced settings: modkit threshold, min coverage, accuracy mode
- Reject and revise plans up to 3 times
- Manual parameter override available

#### **💬 Example Requests**
```
"Analyze mouse cDNA sample named Ali1 at /path/to/pod5"
"Remap this unmapped BAM file: /path/to/sample.bam"
"Only basecall pod5 files in /path/to/data"
"Call modifications on mapped BAM at /path/to/aligned.bam"
"Generate report for job in /path/to/work_dir"
"Map sample to both human and mouse genomes"
```

**Ready to start? Just describe what you want to analyze!**
"""
            agent_block = _create_block_internal(
                session,
                req.project_id,
                "AGENT_PLAN",
                {
                    "markdown": capabilities_text,
                    "skill": "system",
                    "model": engine.model_name
                },
                status="DONE"
            )
            
            return {
                "status": "ok",
                "user_block": row_to_dict(user_block),
                "agent_block": row_to_dict(agent_block),
                "gate_block": None
            }
        
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
            # Extract parameters before creating approval gate
            extracted_params = await extract_job_parameters_from_conversation(session, req.project_id)
            
            gate_block = _create_block_internal(
                session,
                req.project_id,
                "APPROVAL_GATE",
                {
                    "label": "Do you authorize the Agent to proceed with this plan?",
                    "extracted_params": extracted_params,
                    "attempt_number": 1,
                    "rejection_history": [],
                    "skill": active_skill,
                    "model": engine.model_name
                },
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