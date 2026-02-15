import asyncio
import datetime
import uuid
import json
import os
import re
import httpx
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool  # To run LLM without blocking
from pydantic import BaseModel
from sqlalchemy import select, desc

# ✅ Import from your package
from server1.schemas import BlockCreate, BlockOut, BlockStreamOut, BlockUpdate
from server1.agent_engine import AgentEngine
from server1.config import SKILLS_REGISTRY, GENOME_ALIASES, AVAILABLE_GENOMES
from server1.db import SessionLocal, init_db_sync, next_seq_sync, row_to_dict
from server1.models import ProjectBlock, Conversation, ConversationMessage, JobResult, User, ProjectAccess
from server1.middleware import AuthMiddleware
from server1.auth import router as auth_router
from server1.admin import router as admin_router
from common import MCPHttpClient
from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
from server2 import format_results
from server2.config import (
    CONSORTIUM_REGISTRY,
    get_consortium_entry, get_all_fallback_patterns,
)
from server1.config import SERVICE_REGISTRY, get_service_url

# --- LOGGING ---
setup_logging("server1")
logger = get_logger(__name__)

# --- APP ---
app = FastAPI()

# Add request logging middleware FIRST (outermost)
app.add_middleware(RequestLoggingMiddleware)

# Add auth middleware (before CORS)
app.add_middleware(AuthMiddleware)

# Add CORS middleware for Streamlit UI cross-origin requests
# Allow both localhost and environment-specified origins
allowed_origins = ["http://localhost:8501"]
if frontend_origin := os.getenv("FRONTEND_URL"):
    if frontend_origin not in allowed_origins:
        allowed_origins.append(frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router)
app.include_router(admin_router)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db_sync()

# --- SCHEMAS ---
class ChatRequest(BaseModel):
    project_id: str
    message: str
    skill: str = "welcome" # Default skill
    model: str = "default"         # Default model (devstral-2)

# --- HELPERS ---
def get_block_payload(block: ProjectBlock) -> dict:
    """Helper to get payload as dict from payload_json"""
    return json.loads(block.payload_json) if block.payload_json else {}


def _parse_tag_params(params_str: str | None) -> dict:
    """
    Parse a comma-separated key=value parameter string from a DATA_CALL tag.
    E.g., "search_term=K562, organism=Homo sapiens" -> {"search_term": "K562", "organism": "Homo sapiens"}
    """
    params = {}
    if params_str:
        for param_pair in params_str.split(','):
            param_pair = param_pair.strip()
            if '=' in param_pair:
                key, value = param_pair.split('=', 1)
                value = value.strip().strip('"\'')
                params[key.strip()] = value
    return params


def _auto_generate_data_calls(user_message: str, skill_key: str,
                              conversation_history: list | None = None) -> list[dict]:
    """
    Safety net: if the LLM failed to generate DATA_CALL tags, detect obvious
    patterns in the user's message and auto-generate the appropriate tool calls.

    Also resolves conversational references ("them", "each of them", "these")
    by scanning recent conversation history for accessions.

    Returns a list of dicts: [{"source_type": str, "source_key": str, "tool": str, "params": dict}]
    """
    calls = []
    msg_lower = user_message.lower()

    # --- ENCODE patterns ---
    # Detect ENCODE accession numbers (ENCSR, ENCFF, ENCLB, etc.)
    accession_matches = re.findall(r'(ENC[A-Z]{2}\d{3}[A-Z]{3})', user_message, re.IGNORECASE)
    accessions = [a.upper() for a in accession_matches]

    # If no accession in the message, check for conversational references
    # ("them", "these", "each", "all of them", "for those", etc.)
    referential_words = ["them", "these", "those", "each", "all of them",
                         "each of them", "for those", "the experiments",
                         "the accessions", "same"]
    if not accessions and any(w in msg_lower for w in referential_words):
        # Scan recent conversation history (last 4 messages) for accessions.
        # IMPORTANT: Strip <details>...</details> blocks first so we only
        # pick up accessions from the clean summary, not from raw query dumps
        # which may contain unrelated experiments from broader searches.
        if conversation_history:
            recent = conversation_history[-4:]
            for msg in recent:
                content = msg.get("content", "")
                # Remove raw data sections that may contain extra accessions
                content = re.sub(r'<details>.*?</details>', '', content, flags=re.DOTALL)
                found = re.findall(r'(ENC[A-Z]{2}\d{3}[A-Z]{3})', content, re.IGNORECASE)
                for acc in found:
                    acc_upper = acc.upper()
                    # Only use experiment-level accessions (ENCSR), not file accessions
                    if acc_upper.startswith("ENCSR") and acc_upper not in accessions:
                        accessions.append(acc_upper)

    if accessions and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
        # Determine which tool based on what the user is asking
        file_keywords = ["bam", "fastq", "file", "files", "pod5", "tar", "bigwig",
                         "download", "available", "methylated", "accessions",
                         "alignments"]
        summary_keywords = ["summary", "how many files", "file size"]
        metadata_keywords = ["detail", "metadata", "info", "what is", "tell me about",
                            "describe", "experiment"]

        for accession in accessions:
            if any(kw in msg_lower for kw in file_keywords):
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_files_by_type", "params": {"accession": accession},
                })
            elif any(kw in msg_lower for kw in summary_keywords):
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_files_by_type", "params": {"accession": accession},
                })
            elif any(kw in msg_lower for kw in metadata_keywords):
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_experiment", "params": {"accession": accession},
                })
            else:
                # Default: get experiment details for an accession
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_experiment", "params": {"accession": accession},
                })

    # Detect biosample searches (no accession but mentions cell lines/tissues)
    elif skill_key in ("ENCODE_Search", "ENCODE_LongRead") and not accessions:
        # Common cell lines and tissues
        biosamples = {
            "k562": ("K562", "Homo sapiens"),
            "gm12878": ("GM12878", "Homo sapiens"),
            "hela": ("HeLa-S3", "Homo sapiens"),
            "hepg2": ("HepG2", "Homo sapiens"),
            "c2c12": ("C2C12", "Mus musculus"),
            "liver": ("liver", None),
            "brain": ("brain", None),
            "heart": ("heart", None),
        }
        for keyword, (term, organism) in biosamples.items():
            if keyword in msg_lower:
                params = {"search_term": term}
                if organism:
                    params["organism"] = organism
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "search_by_biosample", "params": params,
                })
                break

    return calls


def _create_block_internal(session, project_id, block_type, payload, status="NEW", owner_id=None):
    """
    Internal helper to insert a block and handle the sequence number safely.
    """
    # Calculate next sequence
    seq_num = next_seq_sync(session, project_id)

    new_block = ProjectBlock(
        id=str(uuid.uuid4()),
        project_id=project_id,
        owner_id=owner_id,
        seq=seq_num,
        type=block_type,
        status=status,
        payload_json=json.dumps(payload),
        parent_id=None,
        created_at=datetime.datetime.utcnow()
    )
    session.add(new_block)
    session.commit()
    session.refresh(new_block)
    return new_block

# --- ENDPOINTS ---

@app.get("/health")
async def health_check():
    """Health check endpoint (no auth required)"""
    return {"status": "ok", "service": "server1"}

@app.get("/skills")
async def get_available_skills():
    """Return the list of all available skills."""
    return {
        "skills": list(SKILLS_REGISTRY.keys()),
        "count": len(SKILLS_REGISTRY)
    }

@app.get("/jobs/{run_uuid}/debug")
async def get_job_debug_info(run_uuid: str):
    """
    Proxy endpoint for Server 3 job debug info via MCP.
    This allows the UI to get debug info without calling Server 3 directly.
    """
    return await _call_server3_tool("get_job_debug", run_uuid=run_uuid)

@app.post("/block", response_model=BlockOut)
async def create_block(block_in: BlockCreate, request: Request):
    # Get current user from middleware
    user = request.state.user
    
    session = SessionLocal()
    try:
        new_block = _create_block_internal(
            session, 
            block_in.project_id, 
            block_in.type, 
            block_in.payload, 
            block_in.status,
            owner_id=user.id
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
        result = session.execute(select(ProjectBlock).where(ProjectBlock.id == block_id))
        block = result.scalar_one_or_none()
        if not block:
            raise HTTPException(status_code=404, detail="Block not found")
            
        old_status = block.status
        
        if body.status is not None:
            block.status = body.status
        if body.payload is not None:
            block.payload_json = json.dumps(body.payload)
            
        session.commit()
        session.refresh(block)
        
        # If an APPROVAL_GATE was just approved, trigger job submission
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "APPROVED":
            asyncio.create_task(submit_job_after_approval(block.project_id, block_id))
        
        # If an APPROVAL_GATE was rejected, trigger rejection handling
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "REJECTED":
            asyncio.create_task(handle_rejection(block.project_id, block_id))
        
        return row_to_dict(block)
    finally:
        session.close()


@app.delete("/projects/{project_id}/blocks")
async def clear_project_blocks(project_id: str, request: Request):
    """
    Delete all blocks for a project, effectively clearing the chat history.
    Conversation records are preserved separately.
    """
    user = request.state.user
    session = SessionLocal()
    try:
        result = session.execute(
            select(ProjectBlock).where(ProjectBlock.project_id == project_id)
        )
        blocks = result.scalars().all()
        count = len(blocks)
        for b in blocks:
            session.delete(b)
        session.commit()
        logger.info("Cleared project blocks", project_id=project_id, count=count, user=user.email)
        return {"status": "ok", "deleted": count}
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
            text = get_block_payload(block).get('text', '')
            conversation.append(f"User: {text}")
            user_messages.append(text)
        elif block.type == "AGENT_PLAN":
            conversation.append(f"Agent: {get_block_payload(block).get('markdown', '')}")
    
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
    elif ".fastq" in all_user_text_original or ".fq" in all_user_text_original or "fastq" in all_user_text:
        params["input_type"] = "fastq"
    
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
    
    logger.info("Extracted parameters", method="heuristics", params=params)
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
            logger.error("Gate block not found", gate_block_id=gate_block_id)
            return
        
        # Get owner_id from the gate block
        owner_id = gate_block.owner_id
        
        # Get rejection details from payload
        gate_payload = get_block_payload(gate_block)
        rejection_reason = gate_payload.get("rejection_reason", "No reason provided")
        attempt_number = gate_payload.get("attempt_number", 1)
        rejection_history = gate_payload.get("rejection_history", [])
        
        # Add to rejection history
        rejection_history.append({
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "reason": rejection_reason,
            "attempt": attempt_number
        })
        
        # Update gate block with history
        gate_payload["rejection_history"] = rejection_history
        gate_block.payload_json = json.dumps(gate_payload)
        session.commit()
        
        logger.info("Handling rejection", project_id=project_id, attempt=attempt_number, reason=rejection_reason)
        
        # Check attempt limit
        if attempt_number >= 3:
            logger.warning("Max attempts reached, creating manual parameter form", project_id=project_id)
            
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
                    "skill": get_block_payload(gate_block).get("skill", "analyze_local_sample"),
                    "model": get_block_payload(gate_block).get("model", "default")
                },
                status="NEW",
                owner_id=owner_id
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
                status="PENDING",
                owner_id=owner_id
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
            block_payload = get_block_payload(block)
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block_payload.get("text", "")
                })
            elif block.type == "AGENT_PLAN":
                conversation_history.append({
                    "role": "assistant",
                    "content": block_payload.get("markdown", "")
                })
                active_skill = block_payload.get("skill")
        
        # Add rejection feedback as user message
        conversation_history.append({
            "role": "user",
            "content": f"I'm rejecting your plan (attempt {attempt_number}/3). Please revise it.\n\nFeedback: {rejection_reason}\n\nPlease generate a new plan that addresses my concerns."
        })
        
        # Get the active skill and model
        gate_payload = get_block_payload(gate_block)
        skill_name = active_skill or gate_payload.get("skill", "analyze_local_sample")
        model_name = gate_payload.get("model", "default")
        
        # Call LLM to regenerate plan
        agent_engine = AgentEngine(model_name=model_name)
        
        try:
            llm_response = await run_in_threadpool(
                agent_engine.think,
                skill_name=skill_name,
                user_message=f"[REVISION REQUEST] {rejection_reason}",
                conversation_history=conversation_history
            )
            
            # Check if response needs approval or is asking for more info
            needs_approval = "[[APPROVAL_NEEDED]]" in llm_response
            clean_response = llm_response.replace("[[APPROVAL_NEEDED]]", "").strip()
            
            # Create new AGENT_PLAN block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": clean_response,
                    "skill": skill_name,
                    "model": model_name,
                    "revision_attempt": attempt_number + 1
                },
                status="DONE" if needs_approval else "NEW",
                owner_id=owner_id
            )
            
            # Only create approval gate if the agent explicitly requested it
            if needs_approval:
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
                    status="PENDING",
                    owner_id=owner_id
                )
                logger.info("Generated revised plan", project_id=project_id, attempt=attempt_number + 1)
            else:
                logger.info("Agent requesting more information", project_id=project_id, attempt=attempt_number + 1)
            
        except Exception as e:
            logger.error("LLM failed during revision", error=str(e), project_id=project_id)
            # Create error block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": f"### ⚠️ Error\n\nFailed to generate revised plan: {str(e)}\n\nPlease try again or use manual configuration.",
                    "error": str(e)
                },
                status="ERROR",
                owner_id=owner_id
            )
    
    finally:
        session.close()

async def submit_job_after_approval(project_id: str, gate_block_id: str):
    """
    Background task to submit a job to Server3 after approval.
    Uses edited_params if available, otherwise falls back to extracted_params.
    """
    session = SessionLocal()
    
    try:
        # Get the gate block to check for edited params
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        
        if not gate_block:
            logger.error("Gate block not found", gate_block_id=gate_block_id)
            return
        
        # Get owner_id from the gate block
        owner_id = gate_block.owner_id
        
        # Prefer edited_params over extracted_params
        gate_payload = get_block_payload(gate_block)
        job_params = gate_payload.get("edited_params") or gate_payload.get("extracted_params")
        
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
                status="FAILED",
                owner_id=owner_id
            )
            logger.error("Failed to extract parameters", project_id=project_id)
            return
        
        # Normalize reference_genome to list for Server3 (Nextflow handles multi-genome in parallel)
        ref_genome = job_params.get("reference_genome", ["mm39"])
        if isinstance(ref_genome, str):
            ref_genome = [ref_genome]
        
        job_data = {
            "project_id": project_id,
            "sample_name": job_params.get("sample_name", f"sample_{project_id.split('_')[-1]}"),
            "mode": job_params.get("mode", "DNA"),
            "input_directory": job_params.get("input_directory", "/data/samples/test"),
            "reference_genome": ref_genome,  # List — Nextflow parallelizes across genomes
            "modifications": job_params.get("modifications"),
            "input_type": job_params.get("input_type", "pod5"),
            "entry_point": job_params.get("entry_point"),
            # Advanced parameters - use Server3 defaults if None
            "modkit_filter_threshold": job_params.get("modkit_filter_threshold") or 0.9,
            "min_cov": job_params.get("min_cov"),  # Let Server3 handle None (mode-dependent default)
            "per_mod": job_params.get("per_mod") or 5,
            "accuracy": job_params.get("accuracy") or "sup",
        }
        
        logger.info("Job parameters prepared", source="edited" if gate_payload.get('edited_params') else "extracted",
                    job_data=job_data)
        
        # Submit job to Server3 via MCP (single call — Nextflow handles multi-genome)
        try:
            server3_url = get_service_url("server3")
            client = MCPHttpClient(name="server3", base_url=server3_url)
            await client.connect()
            try:
                result = await client.call_tool("submit_dogme_job", **job_data)
            finally:
                await client.disconnect()
        except Exception as e:
            raise Exception(f"MCP call to Server3 failed: {e}")
        
        run_uuid = result.get("run_uuid") if isinstance(result, dict) else None
        
        if run_uuid:
            
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
                status="RUNNING",
                owner_id=owner_id
            )
            
            logger.info("Job submitted", run_uuid=run_uuid, project_id=project_id)
            
            # Start polling job status in background
            asyncio.create_task(poll_job_status(project_id, job_block.id, run_uuid))
        else:
            # MCP call succeeded but no run_uuid in response
            # Show the actual result for debugging
            error_detail = str(result) if result else "empty response"
            _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "error": f"No run_uuid in Server3 response: {error_detail}",
                    "message": error_detail,
                    "job_data": {k: str(v) for k, v in job_data.items()},  # Include params for debugging
                    "job_status": {
                        "status": "FAILED",
                        "progress_percent": 0,
                        "message": f"Submission failed: {error_detail}",
                        "tasks": {}
                    }
                },
                status="FAILED",
                owner_id=owner_id
            )
            logger.error("Job submission failed: no run_uuid in response", result=result, result_type=type(result).__name__, job_data=job_data)
    
    except Exception as e:
        # Create error block with full details
        import traceback
        error_trace = traceback.format_exc()
        error_msg = str(e)
        _create_block_internal(
            session,
            project_id,
            "EXECUTION_JOB",
            {
                "error": error_msg,
                "message": f"Failed to submit job to Server3: {error_msg}",
                "job_status": {
                    "status": "FAILED",
                    "progress_percent": 0,
                    "message": f"Error: {error_msg}",
                    "tasks": {}
                }
            },
            status="FAILED",
            owner_id=owner_id
        )
        logger.error("Job submission error", error=error_msg, traceback=error_trace, project_id=project_id)
    finally:
        session.close()

async def poll_job_status(project_id: str, block_id: str, run_uuid: str):
    """
    Background task to poll Server3 for job status via MCP and update the EXECUTION_JOB block.
    Continues until job is completed or failed.
    """
    
    poll_interval = 3  # seconds
    max_polls = 600  # 30 minutes max
    
    for poll_num in range(max_polls):
        await asyncio.sleep(poll_interval)
        
        session = SessionLocal()
        try:
            # Get current status from Server3 via MCP
            server3_url = get_service_url("server3")
            client = MCPHttpClient(name="server3", base_url=server3_url)
            await client.connect()
            try:
                status_data = await client.call_tool("check_nextflow_status", run_uuid=run_uuid)
                logs_data = await client.call_tool("get_job_logs", run_uuid=run_uuid, limit=50)
            finally:
                await client.disconnect()
            
            if not isinstance(status_data, dict):
                logger.warning("Failed to get status", run_uuid=run_uuid)
                continue
            
            logs = logs_data.get("logs", []) if isinstance(logs_data, dict) else []
            
            # Update the block with new data
            block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
            if block:
                # Create new payload dict to ensure SQLAlchemy detects the change
                payload = get_block_payload(block)
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
                
                # Reassign payload_json to trigger SQLAlchemy update
                block.payload_json = json.dumps(payload)
                session.commit()
                session.refresh(block)
                
                logger.info("Job status updated", run_uuid=run_uuid, job_status=job_status, progress=status_data.get('progress_percent', 0))
                
                # Stop polling if job is done
                if job_status in ("COMPLETED", "FAILED"):
                    logger.info("Job finished", run_uuid=run_uuid, job_status=job_status)
                    
                    # On completion, auto-trigger analysis
                    if job_status == "COMPLETED":
                        await _auto_trigger_analysis(
                            project_id, run_uuid, payload, block.owner_id
                        )
                    
                    break
        
        except Exception as e:
            logger.warning("Error polling job", run_uuid=run_uuid, error=str(e))
        finally:
            session.close()
    
    logger.info("Stopped polling job", run_uuid=run_uuid)


async def _auto_trigger_analysis(
    project_id: str, run_uuid: str, job_payload: dict, owner_id: str | None
):
    """
    Automatically start analysing a just-completed Dogme job.

    Creates a system USER_MESSAGE + AGENT_PLAN block that fetches the analysis
    summary from Server 4 and presents it to the user, then invites further
    exploration using the mode-specific Dogme analysis skill.
    """
    sample_name = job_payload.get("sample_name", "Unknown")
    mode = job_payload.get("mode", "DNA")

    logger.info("Auto-triggering analysis", run_uuid=run_uuid, sample_name=sample_name, mode=mode)

    session = SessionLocal()
    try:
        # 1. Create a system message announcing the transition
        _create_block_internal(
            session,
            project_id,
            "USER_MESSAGE",
            {"text": f"Job \"{sample_name}\" completed. Analyze the results."},
            owner_id=owner_id,
        )

        # 2. Fetch analysis summary from Server 4
        summary_md = ""
        try:
            server4_url = get_service_url("server4")
            client = MCPHttpClient(name="server4", base_url=server4_url)
            await client.connect()
            try:
                summary = await client.call_tool("get_analysis_summary", run_uuid=run_uuid)
            finally:
                await client.disconnect()

            if isinstance(summary, dict):
                # Build a concise overview from the summary
                file_summary = summary.get("file_summary", {})
                key_results = summary.get("key_results", {})
                total_files = key_results.get("total_files", 0)
                csv_count = len(file_summary.get("csv_files", []))
                bed_count = len(file_summary.get("bed_files", []))
                txt_count = len(file_summary.get("txt_files", []))

                summary_md = (
                    f"### 📊 Analysis Ready: {sample_name}\n\n"
                    f"**Run UUID:** `{run_uuid}`\n"
                    f"**Mode:** {summary.get('mode', mode)} &nbsp;|&nbsp; "
                    f"**Status:** {summary.get('status', 'COMPLETED')} &nbsp;|&nbsp; "
                    f"**Total files:** {total_files}\n\n"
                    f"| Category | Count |\n"
                    f"|----------|-------|\n"
                    f"| CSV / TSV | {csv_count} |\n"
                    f"| BED | {bed_count} |\n"
                    f"| Text / other | {txt_count} |\n\n"
                )

                # List key result files
                csv_files = file_summary.get("csv_files", [])
                if csv_files:
                    summary_md += "**Key result files:**\n"
                    for f in csv_files[:8]:
                        size_kb = f.get("size", 0) / 1024
                        summary_md += f"- `{f['name']}` ({size_kb:.1f} KB)\n"
                    if len(csv_files) > 8:
                        summary_md += f"- _…and {len(csv_files) - 8} more_\n"
                    summary_md += "\n"
        except Exception as e:
            logger.warning("Failed to fetch analysis summary", run_uuid=run_uuid, error=str(e))
            summary_md = (
                f"### 📊 Job Completed: {sample_name}\n\n"
                f"The {mode} job finished successfully. "
                f"I couldn't fetch the file summary automatically, but you can ask me to "
                f"analyze the results.\n\n"
            )

        # 3. Map mode → Dogme analysis skill
        mode_skill_map = {
            "DNA": "run_dogme_dna",
            "RNA": "run_dogme_rna",
            "CDNA": "run_dogme_cdna",
        }
        analysis_skill = mode_skill_map.get(mode.upper(), "analyze_job_results")

        summary_md += (
            "💡 *You can ask me to dive deeper — for example:*\n"
            "- \"Show me the modification summary\"\n"
            "- \"Parse the CSV results\"\n"
            "- \"Give me a QC report\"\n"
        )

        # 4. Create AGENT_PLAN block with the summary
        _create_block_internal(
            session,
            project_id,
            "AGENT_PLAN",
            {
                "markdown": summary_md,
                "skill": analysis_skill,
                "model": "system",
            },
            status="DONE",
            owner_id=owner_id,
        )

        logger.info("Auto-analysis block created", run_uuid=run_uuid, skill=analysis_skill)

    except Exception as e:
        logger.error("Auto-trigger analysis failed", run_uuid=run_uuid, error=str(e))
    finally:
        session.close()


# --- CHAT & AGENT LOGIC ---
@app.post("/chat")
async def chat_with_agent(req: ChatRequest, request: Request):
    """
    1. Saves User Message -> DB
    2. Runs Agent Engine (LLM)
    3. Parses output for [[APPROVAL_NEEDED]] tag
    4. Saves Agent Plan -> DB (Clean text)
    5. If tag found -> Saves APPROVAL_GATE -> DB (Interactive buttons)
    """
    # Get current user from middleware
    user = request.state.user
    
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
        if last_agent_block:
            # Check if the most recent AGENT_PLAN is newer than the most recent
            # APPROVAL_GATE.  If so, the agent has spoken *after* the gate was
            # resolved (e.g. auto-analysis after job completion) and we should
            # continue with whatever skill that AGENT_PLAN set.
            agent_is_latest = (
                not approval_block
                or last_agent_block.seq > approval_block.seq
            )
            if agent_is_latest:
                last_skill = get_block_payload(last_agent_block).get("skill")
                if last_skill:
                    active_skill = last_skill
                    logger.info("Continuing with active skill", skill=active_skill)
            elif approval_block and approval_block.status in ["APPROVED", "REJECTED"]:
                # Approval gate is the most recent interaction — start fresh
                active_skill = req.skill
                logger.info("Starting fresh with skill", skill=active_skill)
        
        # Track project access
        await track_project_access(session, user.id, req.project_id, req.project_id)
        
        # 1. Save USER_MESSAGE
        user_block = _create_block_internal(
            session,
            req.project_id,
            "USER_MESSAGE",
            {"text": req.message},
            owner_id=user.id
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
                    "skill": active_skill or req.skill or "welcome",
                    "model": req.model or "default"
                },
                status="DONE",
                owner_id=user.id
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
            block_payload = get_block_payload(block)
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block_payload.get("text", "")
                })
            elif block.type == "AGENT_PLAN":
                conversation_history.append({
                    "role": "assistant",
                    "content": block_payload.get("markdown", "")
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
                logger.info("Agent switching skill", from_skill=active_skill, to_skill=new_skill)
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
        
        # 5. Parse DATA_CALL Tags (unified format for all consortia and services)
        # Format: [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]
        #     or: [[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=...]]
        
        # FALLBACK: Fix common LLM mistakes by converting plain text patterns to proper tags
        all_fallback_patterns = get_all_fallback_patterns()
        
        corrected_response = raw_response
        fallback_fixes_applied = 0
        for pattern, replacement in all_fallback_patterns.items():
            before = corrected_response
            corrected_response = re.sub(pattern, replacement, corrected_response, flags=re.IGNORECASE)
            if before != corrected_response:
                fallback_fixes_applied += 1
        
        if fallback_fixes_applied > 0:
            logger.warning("Applied fallback tag fixes to LLM response", count=fallback_fixes_applied)
        
        # Parse unified DATA_CALL tags
        # Groups: (1) source_type [consortium|service], (2) source_key, (3) tool_name, (4) remaining params
        data_call_pattern = r'\[\[DATA_CALL:\s*(?:(consortium|service)=(\w+)),\s*tool=(\w+)(?:,\s*([^\]]+))?\]\]'
        data_call_matches = list(re.finditer(data_call_pattern, corrected_response))
        
        # Also support legacy ENCODE_CALL and ANALYSIS_CALL tags for backward compatibility
        legacy_encode_pattern = r'\[\[ENCODE_CALL:\s*([\w_]+)(?:,\s*([^\]]+))?\]\]'
        legacy_analysis_pattern = r'\[\[ANALYSIS_CALL:\s*(\w+),\s*run_uuid=([a-f0-9-]+)\]\]'
        legacy_encode_matches = list(re.finditer(legacy_encode_pattern, corrected_response))
        legacy_analysis_matches = list(re.finditer(legacy_analysis_pattern, corrected_response))
        
        # Clean all tags from user-visible text
        clean_markdown = corrected_response.replace(trigger_tag, "").strip()
        clean_markdown = re.sub(r'\[\[SKILL_SWITCH_TO:\s*\w+\]\]', '', clean_markdown).strip()
        clean_markdown = re.sub(data_call_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(legacy_encode_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(legacy_analysis_pattern, '', clean_markdown).strip()
        
        # Also remove any remaining plain text patterns that might not have been converted
        for pattern in all_fallback_patterns.keys():
            clean_markdown = re.sub(pattern, '', clean_markdown, flags=re.IGNORECASE).strip()
        
        # 5b. Auto-tag safety net: if LLM failed to generate any DATA_CALL tags,
        #     detect patterns in the user message and auto-generate appropriate calls.
        #     ALSO validates accessions when the user uses referential words ("them",
        #     "each of them", etc.) to catch LLM-hallucinated accession numbers.
        has_any_tags = bool(data_call_matches or legacy_encode_matches or legacy_analysis_matches)
        auto_calls = []

        # Check for conversational references in the user's message
        _ref_words = ["them", "these", "those", "each", "all of them",
                      "each of them", "for those", "the experiments",
                      "the accessions", "same"]
        _msg_lower = req.message.lower()
        _has_referential = any(w in _msg_lower for w in _ref_words)

        if not has_any_tags:
            # Case 1: LLM produced no tags at all — auto-generate
            auto_calls = _auto_generate_data_calls(req.message, active_skill, conversation_history)
            if auto_calls:
                logger.warning("LLM failed to generate DATA_CALL tags, auto-generating",
                              count=len(auto_calls), skill=active_skill)
                # Replace the LLM's unhelpful response with a brief note
                clean_markdown = ""
        elif has_any_tags and _has_referential:
            # Case 2: LLM produced tags BUT user used referential words.
            # Validate that the accessions in the tags actually appeared in
            # conversation history; if not, they are hallucinated.
            _llm_accessions = set()
            for _m in data_call_matches:
                _p = _parse_tag_params(_m.group(4))
                if _p.get("accession"):
                    _llm_accessions.add(_p["accession"].upper())
            for _m in legacy_encode_matches:
                _p = _parse_tag_params(_m.group(2))
                if _p.get("accession"):
                    _llm_accessions.add(_p["accession"].upper())

            _history_accessions = set()
            if conversation_history:
                for _msg in conversation_history:
                    _content = re.sub(r'<details>.*?</details>', '', _msg.get("content", ""), flags=re.DOTALL)
                    _found = re.findall(r'(ENCSR\d{3}[A-Z]{3})', _content, re.IGNORECASE)
                    _history_accessions.update(a.upper() for a in _found)

            if _llm_accessions and _history_accessions and not _llm_accessions & _history_accessions:
                # None of the LLM's accessions appear in history — hallucinated
                logger.warning(
                    "LLM hallucinated accessions in DATA_CALL tags, overriding",
                    llm_accessions=sorted(_llm_accessions),
                    history_accessions=sorted(_history_accessions),
                )
                auto_calls = _auto_generate_data_calls(req.message, active_skill, conversation_history)
                if auto_calls:
                    # Discard the LLM-generated tags
                    data_call_matches = []
                    legacy_encode_matches = []
                    legacy_analysis_matches = []
                    has_any_tags = False
                    clean_markdown = ""

        # 6. Collect all tool calls into a unified structure
        # Structure: {source_key: [{"tool": str, "params": dict}, ...]}
        calls_by_source = {}
        
        # From auto-generated tags (safety net)
        if not has_any_tags and auto_calls:
            for ac in auto_calls:
                calls_by_source.setdefault(ac["source_key"], []).append({
                    "tool": ac["tool"],
                    "params": ac["params"],
                })

        # From new DATA_CALL tags
        for match in data_call_matches:
            source_type = match.group(1)  # "consortium" or "service"
            source_key = match.group(2)   # e.g., "encode", "server4"
            tool_name = match.group(3)
            params_str = match.group(4)
            
            params = _parse_tag_params(params_str)
            
            calls_by_source.setdefault(source_key, []).append({
                "tool": tool_name,
                "params": params,
            })
        
        # From legacy ENCODE_CALL tags (backward compat)
        for match in legacy_encode_matches:
            tool_name = match.group(1)
            params_str = match.group(2)
            params = _parse_tag_params(params_str)
            
            calls_by_source.setdefault("encode", []).append({
                "tool": tool_name,
                "params": params,
            })
        
        # From legacy ANALYSIS_CALL tags (backward compat)
        for match in legacy_analysis_matches:
            analysis_type = match.group(1)
            run_uuid = match.group(2)
            
            # Map legacy analysis types to Server4 MCP tool names
            tool_map = {
                "summary": "get_analysis_summary",
                "categorize_files": "categorize_job_files",
                "list_files": "list_job_files",
            }
            tool_name = tool_map.get(analysis_type, analysis_type)
            
            calls_by_source.setdefault("server4", []).append({
                "tool": tool_name,
                "params": {"run_uuid": run_uuid},
            })
        
        # 6b. Execute all tool calls grouped by source
        all_results = {}  # {source_key: [result_dicts]}
        
        for source_key, calls in calls_by_source.items():
            logger.info("Executing tool calls", source=source_key, count=len(calls))
            
            try:
                url = get_service_url(source_key)
            except KeyError:
                logger.error("Unknown source", source=source_key)
                all_results[source_key] = [{
                    "tool": c["tool"],
                    "params": c["params"],
                    "error": f"Unknown source: {source_key}",
                } for c in calls]
                continue
            
            mcp_client = MCPHttpClient(name=source_key, base_url=url)
            source_results = []
            
            try:
                await mcp_client.connect()
                
                for call in calls:
                    tool_name = call["tool"]
                    params = call["params"]
                    
                    logger.info("Calling tool", source=source_key, tool=tool_name, params=params)
                    
                    try:
                        result_data = await mcp_client.call_tool(tool_name, **params)
                        source_results.append({
                            "tool": tool_name,
                            "params": params,
                            "data": result_data,
                        })
                        logger.info("Tool call successful", source=source_key, tool=tool_name)
                    except Exception as e:
                        logger.error("Tool call failed", source=source_key, tool=tool_name, error=str(e))
                        source_results.append({
                            "tool": tool_name,
                            "params": params,
                            "error": str(e),
                        })
            except Exception as e:
                logger.error("Failed to connect to source", source=source_key, error=str(e))
                source_results = [{
                    "tool": c["tool"],
                    "params": c["params"],
                    "error": f"Connection failed: {e}",
                } for c in calls]
            finally:
                await mcp_client.disconnect()
            
            all_results[source_key] = source_results
        
        # 6c. Format results and run second-pass LLM analysis
        if all_results:
            # Check if all results are errors (skip second pass if so)
            has_real_data = any(
                any("data" in r for r in results)
                for results in all_results.values()
            )

            # Format results into compact markdown for the LLM to analyze
            formatted_data_parts = []
            for source_key, results in all_results.items():
                if results:
                    entry = SERVICE_REGISTRY.get(source_key)
                    results_markdown = format_results(source_key, results, registry_entry=entry)
                    formatted_data_parts.append(results_markdown)

            formatted_data = "\n".join(formatted_data_parts)
            logger.info("Formatted tool results", size=len(formatted_data),
                       has_real_data=has_real_data,
                       preview=formatted_data[:500])

            if has_real_data and formatted_data.strip():
                # Cap data size to avoid overwhelming the LLM context window
                MAX_DATA_CHARS = 12000
                if len(formatted_data) > MAX_DATA_CHARS:
                    logger.warning("Truncating tool results for LLM",
                                  original_size=len(formatted_data), max_size=MAX_DATA_CHARS)
                    formatted_data = formatted_data[:MAX_DATA_CHARS] + "\n\n... (results truncated for brevity)"

                # Second-pass: send data to LLM for analysis/filtering/summarization
                logger.info("Running second-pass LLM analysis", data_size=len(formatted_data))
                analyzed_response = await run_in_threadpool(
                    engine.analyze_results,
                    req.message,
                    clean_markdown,
                    formatted_data,
                    active_skill,
                    conversation_history,
                )

                # Append the formatted source data so user can verify
                clean_markdown = analyzed_response + "\n\n---\n\n" \
                    "<details><summary>📋 Raw Query Results (click to expand)</summary>\n\n" \
                    + formatted_data + "\n\n</details>"
            else:
                # All errors or empty — show errors directly, no second pass
                clean_markdown += formatted_data
        
        # 7. Save AGENT_PLAN (The Text)
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
            status="DONE",
            owner_id=user.id
        )
        
        # Save assistant's response to conversation history
        await save_conversation_message(session, req.project_id, user.id, "assistant", clean_markdown)
        
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
                status="PENDING",
                owner_id=user.id
            )
        
        # Save conversation message
        await save_conversation_message(session, req.project_id, user.id, "user", req.message)
        
        return {
            "status": "ok", 
            "user_block": row_to_dict(user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": row_to_dict(gate_block) if gate_block else None
        }
        
    except Exception as e:
        import traceback
        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        logger.error("Chat endpoint error", error=error_detail, exc_info=True)
        raise HTTPException(status_code=500, detail=error_detail)
    finally:
        session.close()


# --- CONVERSATION HISTORY ENDPOINTS ---

async def save_conversation_message(session, project_id: str, user_id: str, role: str, content: str):
    """Helper to save conversation messages."""
    # Get or create conversation for this project
    conv_query = select(Conversation)\
        .where(Conversation.project_id == project_id)\
        .where(Conversation.user_id == user_id)\
        .order_by(desc(Conversation.created_at))\
        .limit(1)
    
    result = session.execute(conv_query)
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        # Create new conversation
        conversation = Conversation(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=user_id,
            title=content[:100] if role == "user" else "New Conversation",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        session.add(conversation)
        session.flush()
    
    # Get next sequence number
    msg_query = select(ConversationMessage)\
        .where(ConversationMessage.conversation_id == conversation.id)\
        .order_by(desc(ConversationMessage.seq))
    
    result = session.execute(msg_query)
    last_msg = result.first()
    next_seq = (last_msg[0].seq + 1) if last_msg else 0
    
    # Create message
    message = ConversationMessage(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        role=role,
        content=content,
        seq=next_seq,
        created_at=datetime.datetime.utcnow()
    )
    session.add(message)
    
    # Update conversation timestamp
    conversation.updated_at = datetime.datetime.utcnow()
    
    session.commit()


@app.get("/projects/{project_id}/conversations")
async def get_project_conversations(project_id: str, request: Request):
    """Get all conversations for a project."""
    user = request.state.user
    
    session = SessionLocal()
    try:
        query = select(Conversation)\
            .where(Conversation.project_id == project_id)\
            .where(Conversation.user_id == user.id)\
            .order_by(desc(Conversation.updated_at))
        
        result = session.execute(query)
        conversations = result.scalars().all()
        
        return {
            "conversations": [
                {
                    "id": conv.id,
                    "project_id": conv.project_id,
                    "title": conv.title,
                    "created_at": conv.created_at,
                    "updated_at": conv.updated_at
                }
                for conv in conversations
            ]
        }
    finally:
        session.close()


@app.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str, request: Request):
    """Get all messages in a conversation."""
    user = request.state.user
    
    session = SessionLocal()
    try:
        # Verify user owns this conversation
        conv_query = select(Conversation).where(Conversation.id == conversation_id)
        result = session.execute(conv_query)
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        if conversation.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get messages
        msg_query = select(ConversationMessage)\
            .where(ConversationMessage.conversation_id == conversation_id)\
            .order_by(ConversationMessage.seq)
        
        result = session.execute(msg_query)
        messages = result.scalars().all()
        
        return {
            "conversation_id": conversation_id,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "seq": msg.seq,
                    "created_at": msg.created_at
                }
                for msg in messages
            ]
        }
    finally:
        session.close()


@app.get("/projects/{project_id}/jobs")
async def get_project_jobs(project_id: str, request: Request):
    """Get all jobs linked to a project."""
    user = request.state.user
    
    session = SessionLocal()
    try:
        # Get all conversations for this project
        conv_query = select(Conversation)\
            .where(Conversation.project_id == project_id)\
            .where(Conversation.user_id == user.id)
        
        result = session.execute(conv_query)
        conversations = result.scalars().all()
        
        if not conversations:
            return {"jobs": []}
        
        # Get all job results
        conversation_ids = [c.id for c in conversations]
        job_query = select(JobResult)\
            .where(JobResult.conversation_id.in_(conversation_ids))\
            .order_by(desc(JobResult.created_at))
        
        result = session.execute(job_query)
        jobs = result.scalars().all()
        
        return {
            "jobs": [
                {
                    "id": job.id,
                    "run_uuid": job.run_uuid,
                    "sample_name": job.sample_name,
                    "workflow_type": job.workflow_type,
                    "status": job.status,
                    "created_at": job.created_at
                }
                for job in jobs
            ]
        }
    finally:
        session.close()


@app.post("/conversations/{conversation_id}/jobs")
async def link_job_to_conversation(conversation_id: str, run_uuid: str, request: Request):
    """Link a job to a conversation."""
    user = request.state.user
    
    session = SessionLocal()
    try:
        # Verify conversation exists and user owns it
        conv_query = select(Conversation).where(Conversation.id == conversation_id)
        result = session.execute(conv_query)
        conversation = result.scalar_one_or_none()
        
        if not conversation or conversation.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # TODO: Query server3 for job details
        # For now, create with minimal info
        job_result = JobResult(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            run_uuid=run_uuid,
            sample_name="Unknown",
            workflow_type="Unknown",
            status="UNKNOWN",
            created_at=datetime.datetime.utcnow()
        )
        session.add(job_result)
        session.commit()
        
        return {"status": "ok", "job_id": job_result.id}
    finally:
        session.close()


# --- PROJECT MANAGEMENT ENDPOINTS ---

async def track_project_access(session, user_id: str, project_id: str, project_name: str = None):
    """Track when a user accesses a project."""
    # Check if access record exists
    access_query = select(ProjectAccess)\
        .where(ProjectAccess.user_id == user_id)\
        .where(ProjectAccess.project_id == project_id)
    
    result = session.execute(access_query)
    access = result.scalar_one_or_none()
    
    if access:
        # Update last accessed time
        access.last_accessed = datetime.datetime.utcnow()
        if project_name:
            access.project_name = project_name
    else:
        # Create new access record
        access = ProjectAccess(
            id=str(uuid.uuid4()),
            user_id=user_id,
            project_id=project_id,
            project_name=project_name or project_id,
            last_accessed=datetime.datetime.utcnow()
        )
        session.add(access)
    
    # Update user's last project
    user_query = select(User).where(User.id == user_id)
    result = session.execute(user_query)
    user_obj = result.scalar_one_or_none()
    if user_obj:
        user_obj.last_project_id = project_id
    
    session.commit()


@app.get("/user/last-project")
async def get_last_project(request: Request):
    """Get user's last active project."""
    user = request.state.user
    
    session = SessionLocal()
    try:
        user_query = select(User).where(User.id == user.id)
        result = session.execute(user_query)
        user_obj = result.scalar_one_or_none()
        
        if user_obj and user_obj.last_project_id:
            return {
                "last_project_id": user_obj.last_project_id
            }
        
        return {"last_project_id": None}
    finally:
        session.close()


@app.get("/user/projects")
async def get_user_projects(request: Request):
    """Get all projects the user has accessed."""
    user = request.state.user
    
    session = SessionLocal()
    try:
        query = select(ProjectAccess)\
            .where(ProjectAccess.user_id == user.id)\
            .order_by(desc(ProjectAccess.last_accessed))
        
        result = session.execute(query)
        projects = result.scalars().all()
        
        return {
            "projects": [
                {
                    "id": proj.project_id,
                    "name": proj.project_name,
                    "last_accessed": proj.last_accessed
                }
                for proj in projects
            ]
        }
    finally:
        session.close()


@app.post("/projects/{project_id}/access")
async def record_project_access(project_id: str, request: Request, project_name: str = None):
    """Record that user accessed a project."""
    user = request.state.user
    
    session = SessionLocal()
    try:
        await track_project_access(session, user.id, project_id, project_name)
        return {"status": "ok"}
    finally:
        session.close()

# =============================================================================
# SERVER 4 ANALYSIS PROXY ENDPOINTS
# =============================================================================
# These proxy endpoints allow the UI to access Server 4 analysis tools
# via MCP over HTTP. The Server 4 MCP server must be running.

@app.get("/analysis/jobs/{run_uuid}/summary")
async def get_job_analysis_summary(run_uuid: str, request: Request):
    """
    Get comprehensive analysis summary for a completed job.
    Proxies to Server 4 analysis engine via MCP.
    """
    user = request.state.user
    return await _call_server4_tool("get_analysis_summary", run_uuid=run_uuid)

@app.get("/analysis/jobs/{run_uuid}/files")
async def list_job_files(run_uuid: str, extensions: Optional[str] = None, request: Request = None):
    """
    List all files for a completed job.
    Query params:
      - extensions: Comma-separated list (e.g., ".csv,.txt")
    """
    user = request.state.user
    kwargs = {"run_uuid": run_uuid}
    if extensions:
        kwargs["extensions"] = extensions
    return await _call_server4_tool("list_job_files", **kwargs)

@app.get("/analysis/jobs/{run_uuid}/files/categorize")
async def categorize_job_files(run_uuid: str, request: Request):
    """
    Categorize job files by type (csv, txt, bed, other).
    """
    user = request.state.user
    return await _call_server4_tool("categorize_job_files", run_uuid=run_uuid)

@app.get("/analysis/files/content")
async def read_file_content(
    run_uuid: str,
    file_path: str,
    preview_lines: Optional[int] = 50,
    request: Request = None
):
    """
    Read content of a specific file.
    """
    user = request.state.user
    return await _call_server4_tool(
        "read_file_content",
        run_uuid=run_uuid,
        file_path=file_path,
        preview_lines=preview_lines,
    )

@app.get("/analysis/files/parse/csv")
async def parse_csv_file(
    run_uuid: str,
    file_path: str,
    max_rows: Optional[int] = 100,
    request: Request = None
):
    """
    Parse CSV/TSV file into structured data.
    """
    user = request.state.user
    return await _call_server4_tool(
        "parse_csv_file",
        run_uuid=run_uuid,
        file_path=file_path,
        max_rows=max_rows,
    )

@app.get("/analysis/files/parse/bed")
async def parse_bed_file(
    run_uuid: str,
    file_path: str,
    max_records: Optional[int] = 100,
    request: Request = None
):
    """
    Parse BED genomic file.
    """
    user = request.state.user
    return await _call_server4_tool(
        "parse_bed_file",
        run_uuid=run_uuid,
        file_path=file_path,
        max_records=max_records,
    )


@app.get("/analysis/files/download")
async def proxy_download_file(
    run_uuid: str,
    file_path: str,
    request: Request = None,
):
    """
    Proxy file download from Server 4 REST API.
    Streams the file through Server 1 so the UI never contacts Server 4 directly.
    """
    user = request.state.user
    from starlette.responses import StreamingResponse
    try:
        server4_rest = SERVICE_REGISTRY["server4"]["rest_url"]
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(
                f"{server4_rest}/analysis/files/download",
                params={"run_uuid": run_uuid, "file_path": file_path},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            # Forward the file as a streaming response
            content_disposition = resp.headers.get("content-disposition", "")
            media_type = resp.headers.get("content-type", "application/octet-stream")
            headers = {}
            if content_disposition:
                headers["content-disposition"] = content_disposition
            return StreamingResponse(
                iter([resp.content]),
                media_type=media_type,
                headers=headers,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download proxy error: {str(e)}")


async def _call_server4_tool(tool_name: str, **kwargs):
    """Helper to call a Server4 MCP tool via the generic MCP HTTP client."""
    try:
        server4_url = get_service_url("server4")
        client = MCPHttpClient(name="server4", base_url=server4_url)
        await client.connect()
        try:
            result = await client.call_tool(tool_name, **kwargs)
            # Surface MCP tool errors as proper HTTP errors
            if isinstance(result, dict) and result.get("success") is False:
                detail = result.get("error", "Unknown error")
                extra = result.get("detail", "")
                raise HTTPException(
                    status_code=422,
                    detail=f"{detail}: {extra}" if extra else detail,
                )
            return result
        finally:
            await client.disconnect()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server 4 error: {str(e)}")

async def _call_server3_tool(tool_name: str, **kwargs):
    """Helper to call a Server3 MCP tool via the generic MCP HTTP client."""
    try:
        server3_url = get_service_url("server3")
        client = MCPHttpClient(name="server3", base_url=server3_url)
        await client.connect()
        try:
            result = await client.call_tool(tool_name, **kwargs)
            if isinstance(result, dict) and result.get("success") is False:
                detail = result.get("error", "Unknown error")
                extra = result.get("detail", "")
                raise HTTPException(
                    status_code=422,
                    detail=f"{detail}: {extra}" if extra else detail,
                )
            return result
        finally:
            await client.disconnect()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server 3 error: {str(e)}")