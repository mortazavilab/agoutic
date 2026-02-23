import asyncio
import datetime
import uuid
import json
import os
import re
import httpx
from typing import Optional

from fastapi import FastAPI, HTTPException, Path as FastAPIPath, Request
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool  # To run LLM without blocking
from pydantic import BaseModel
from sqlalchemy import select, desc, func, text

# ✅ Import from your package
from cortex.schemas import BlockCreate, BlockOut, BlockStreamOut, BlockUpdate
from cortex.agent_engine import AgentEngine
from cortex.config import SKILLS_REGISTRY, GENOME_ALIASES, AVAILABLE_GENOMES, AGOUTIC_DATA
from cortex.db import SessionLocal, init_db_sync, next_seq_sync, row_to_dict
from cortex.models import ProjectBlock, Conversation, ConversationMessage, JobResult, User, ProjectAccess, Project
from cortex.middleware import AuthMiddleware
from cortex.auth import router as auth_router
from cortex.admin import router as admin_router
from cortex.dependencies import require_project_access, require_run_uuid_access
from common import MCPHttpClient
from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
from cortex.tool_contracts import fetch_all_tool_schemas, validate_against_schema, invalidate_schema_cache
from atlas import format_results
from atlas.config import (
    CONSORTIUM_REGISTRY,
    get_consortium_entry, get_all_fallback_patterns, get_all_tool_aliases,
    get_all_param_aliases,
)
from cortex.config import SERVICE_REGISTRY, get_service_url

# --- LOGGING ---
setup_logging("cortex")
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
    # Fetch tool schemas from MCP servers (best-effort, non-blocking on failure)
    try:
        from cortex.config import SERVICE_REGISTRY
        from atlas.config import CONSORTIUM_REGISTRY
        await fetch_all_tool_schemas(SERVICE_REGISTRY, CONSORTIUM_REGISTRY)
    except Exception as e:
        logger.warning("Failed to fetch tool schemas at startup (will retry on first use)", error=str(e))

# --- SCHEMAS ---
class ChatRequest(BaseModel):
    project_id: str
    message: str
    skill: str = "welcome" # Default skill
    model: str = "default"         # Default model (devstral-2)
    request_id: str = ""           # Optional: for progress tracking via /chat/status

# --- CHAT PROGRESS TRACKING ---
# Tracks processing stages for active chat requests so the UI can poll for updates.
# Entries are cleaned up after 5 minutes.
import time as _time
_chat_progress: dict[str, dict] = {}  # request_id -> {"stage": str, "detail": str, "ts": float}

def _emit_progress(request_id: str, stage: str, detail: str = ""):
    """Record a processing stage for a chat request."""
    if not request_id:
        return
    _chat_progress[request_id] = {
        "stage": stage,
        "detail": detail,
        "ts": _time.time(),
    }
    # Housekeeping: remove entries older than 5 minutes
    cutoff = _time.time() - 300
    stale = [k for k, v in _chat_progress.items() if v["ts"] < cutoff]
    for k in stale:
        del _chat_progress[k]

# --- HELPERS ---
def get_block_payload(block: ProjectBlock) -> dict:
    """Helper to get payload as dict from payload_json"""
    return json.loads(block.payload_json) if block.payload_json else {}


def _resolve_project_dir(session, user, project_id: str) -> Path:
    """Resolve the on-disk directory for a project.

    Uses slug-based path (AGOUTIC_DATA/users/{username}/{slug}/) if both
    username and slug are available.  Falls back to legacy UUID-based path.
    Returns the Path (may or may not exist yet).
    """
    project = session.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one_or_none()

    username = getattr(user, "username", None)
    slug = project.slug if project else None

    if username and slug:
        return AGOUTIC_DATA / "users" / username / slug
    # Legacy fallback
    return AGOUTIC_DATA / "users" / user.id / project_id

# Mapping from user-message fragments to canonical ENCODE assay_title values.
# Used both for auto-generating filtered DATA_CALLs and for server-side
# dataframe filtering when the previous result set is injected as context.
_ENCODE_ASSAY_ALIASES: dict[str, str] = {
    "long read rna": "long read RNA-seq",
    "long-read rna": "long read RNA-seq",
    "long read rna-seq": "long read RNA-seq",
    "chip-seq": "TF ChIP-seq",
    "chipseq": "TF ChIP-seq",
    "tf chip": "TF ChIP-seq",
    "histone chip": "Histone ChIP-seq",
    "atac-seq": "ATAC-seq",
    "atacseq": "ATAC-seq",
    "atac seq": "ATAC-seq",
    "dnase-seq": "DNase-seq",
    "dnase seq": "DNase-seq",
    "microrna-seq": "microRNA-seq",
    "microrna seq": "microRNA-seq",
    "mirna-seq": "microRNA-seq",
    "mirna seq": "microRNA-seq",
    "micro rna-seq": "microRNA-seq",
    "rna-seq": "total RNA-seq",
    "rnaseq": "total RNA-seq",
    "polya plus rna": "polyA plus RNA-seq",
    "polya rna": "polyA plus RNA-seq",
    "shrna rna": "shRNA RNA-seq",
    "crispr rna": "CRISPR RNA-seq",
    "eclip": "eCLIP",
    "clip-seq": "eCLIP",
    "hi-c": "in situ Hi-C",
    "hic": "in situ Hi-C",
    "wgbs": "WGBS",
    "rrbs": "RRBS",
}


_SEARCH_STYLE_PARAMS = frozenset(
    ["search_term", "assay_title", "biosample", "cell_line", "tissue", "target", "organism"]
)
# Well-known human cell lines — used only for defaulting organism=Homo sapiens.
# The misrouting guard itself is structural (any non-ENCSR accession redirects).
_HUMAN_BIOSAMPLES = frozenset([
    "k562", "gm12878", "hela", "hela-s3", "hepg2", "hek293", "hek293t",
    "jurkat", "mcf-7", "mcf7", "a549", "u2os", "imr90", "imr-90",
    "h1", "h9", "hff", "hff-myc", "wtc-11", "wtc11", "lncap", "panc-1",
    "panc1", "sk-n-sh", "sknsh", "caco-2", "caco2", "sh-sy5y", "shsy5y",
    "hl-60", "hl60", "thp-1", "thp1", "u937", "nb4", "kasumi-1",
    "raji", "namalwa", "rpmi-8226", "mm.1s",
])
# Pattern for a valid ENCODE experiment accession: ENCSR + 6 uppercase alphanumeric chars
_ENCSR_PATTERN = re.compile(r'^ENCSR[A-Z0-9]{6}$', re.IGNORECASE)
# Pattern for a valid ENCODE file accession (ENCFF...)
_ENCFF_PATTERN = re.compile(r'^ENCFF[A-Z0-9]{6}$', re.IGNORECASE)
# Substrings that indicate a string is an assay name rather than a biosample name.
_ASSAY_INDICATORS = (
    "-seq", " seq", "chip", "atac", "clip", "wgbs", "rrbs", "hi-c",
    "rna-seq", "dnase", "crispr", "eclip", "iclip", "rampage",
    "long read", "long-read",
)


def _looks_like_assay(s: str) -> bool:
    """Return True if *s* looks like an assay name rather than a biosample."""
    sl = s.lower()
    if any(ind in sl for ind in _ASSAY_INDICATORS):
        return True
    # Also check against the aliases map (both keys and canonical values)
    if sl in _ENCODE_ASSAY_ALIASES:
        return True
    canon = _ENCODE_ASSAY_ALIASES.get(sl)
    if canon:
        return True
    if any(sl == v.lower() for v in _ENCODE_ASSAY_ALIASES.values()):
        return True
    return False


def _correct_tool_routing(tool: str, params: dict, user_message: str,
                          conversation_history: list | None = None) -> tuple[str, dict]:
    """
    Fix cases where the LLM uses the wrong tool for a given accession type.

    Common mistakes:
    - Using get_experiment with search-style params or a non-ENCSR value as
      'accession' (e.g. a biosample name like K562 or MCF-7) instead of
      using search_by_biosample.
    - Using get_experiment for ENCFF (file) accessions instead of get_file_metadata
    - Mangling ENCFF → ENCSR (changing the prefix to match the tool it chose)
    - Calling get_file_metadata without the required experiment accession

    get_experiment requires exactly: accession=ENCSR[A-Z0-9]{6}
    get_file_metadata requires: accession=ENCSR... + file_accession=ENCFF...
    """
    accession = params.get("accession", "")
    msg_upper = user_message.upper()

    # ── Redirect get_experiment → search_by_biosample ──────────────────────
    # Trigger when: (a) any search-style param was passed, OR (b) the accession
    # value is not a valid ENCSR accession (catches ALL unknown cell line names,
    # not just those in a hardcoded list).
    if tool == "get_experiment":
        acc_upper = accession.upper()
        has_search_params = bool(params.keys() & _SEARCH_STYLE_PARAMS)
        acc_invalid = bool(accession) and not _ENCSR_PATTERN.match(accession) \
                      and not _ENCFF_PATTERN.match(accession)

        if has_search_params or acc_invalid:
            # Determine whether this is an assay-only query or a biosample query.
            # An assay-only query has assay info but no real biosample to anchor to.
            explicit_assay = params.get("assay_title") or (
                accession if (acc_invalid and _looks_like_assay(accession)) else None
            )
            # Resolve assay name through alias map if needed
            if explicit_assay:
                explicit_assay = _ENCODE_ASSAY_ALIASES.get(
                    explicit_assay.lower(), explicit_assay
                )

            explicit_biosample = (
                params.get("search_term")
                or params.get("biosample")
                or params.get("cell_line")
                or (accession if (acc_invalid and not _looks_like_assay(accession)) else None)
            )

            # ── Case A: assay-only (no biosample) → search_by_assay ──
            if explicit_assay and not explicit_biosample:
                new_params: dict = {"assay_title": explicit_assay}
                if "organism" in params:
                    new_params["organism"] = params["organism"]
                if "target" in params:
                    new_params["target"] = params["target"]
                logger.warning(
                    "Rerouted get_experiment → search_by_assay (assay-only)",
                    original_params=params, new_params=new_params,
                )
                return "search_by_assay", new_params

            # ── Case B: biosample (± assay) → search_by_biosample ──
            search_term = explicit_biosample
            if not search_term:
                # Last resort: grab the first capitalised word from the user message
                candidates = re.findall(
                    r'\b([A-Z][A-Za-z0-9]{1,10}(?:[-][A-Za-z0-9]+)?)\b', user_message
                )
                for c in candidates:
                    if c.upper() not in ("ENCODE", "WHAT", "HOW", "MANY", "ARE", "DOES", "HAVE"):
                        search_term = c
                        break

            new_params = {}
            if search_term:
                new_params["search_term"] = search_term

            for key in ("organism", "target", "exclude_revoked"):
                if key in params:
                    new_params[key] = params[key]
            if explicit_assay:
                new_params["assay_title"] = explicit_assay

            # Default organism=Homo sapiens for well-known human lines
            if "organism" not in new_params and (search_term or "").lower() in _HUMAN_BIOSAMPLES:
                new_params["organism"] = "Homo sapiens"

            if new_params.get("search_term"):
                logger.warning(
                    "Rerouted get_experiment → search_by_biosample",
                    original_params=params, new_params=new_params,
                )
                return "search_by_biosample", new_params

    if tool == "get_experiment" and accession:
        acc_upper = accession.upper()
        file_acc = None

        # Case 1: LLM passed an ENCFF accession to get_experiment directly
        if acc_upper.startswith("ENCFF"):
            file_acc = accession

        # Case 2: LLM mangled ENCFF → ENCSR (changed prefix to match tool)
        # ENCODE accessions: 5-char prefix (ENC + 2-letter type) + 6 alphanumeric
        elif acc_upper.startswith("ENCSR"):
            suffix = acc_upper[5:]  # e.g. "921XAH" (skip 5-char prefix)
            candidate = f"ENCFF{suffix}"
            if candidate in msg_upper:
                file_acc = candidate

        # Case 3: User message mentions an ENCFF accession but the LLM
        # hallucinated a completely different ENCSR accession for get_experiment.
        # Extract the ENCFF from the user message directly.
        if not file_acc:
            encff_in_msg = re.findall(r'(ENCFF[A-Z0-9]{6})', msg_upper)
            if encff_in_msg:
                file_acc = encff_in_msg[0]
                logger.warning(
                    "LLM hallucinated accession for get_experiment, "
                    "user message has ENCFF — rerouting",
                    hallucinated=accession, file_accession=file_acc)

        if file_acc:
            # Find parent experiment accession from conversation history
            exp_acc = _find_experiment_for_file(file_acc, conversation_history)
            if exp_acc:
                logger.warning("Rerouting get_experiment → get_file_metadata",
                              file_accession=file_acc, experiment=exp_acc)
                return "get_file_metadata", {
                    "accession": exp_acc, "file_accession": file_acc}
            else:
                # No experiment found — can't call get_file_metadata without it.
                # Return a routing error so the caller skips the MCP call.
                logger.warning(
                    "Cannot find parent experiment for file accession",
                    file_accession=file_acc)
                return "get_file_metadata", {
                    "file_accession": file_acc,
                    "__routing_error__": (
                        f"Cannot look up file metadata for {file_acc} without "
                        f"knowing its parent experiment (ENCSR...). "
                        f"Please first query the experiment that contains this "
                        f"file, then ask about the file."
                    ),
                }

    # Also fix get_file_metadata called without experiment accession
    if tool == "get_file_metadata":
        file_acc = params.get("file_accession", params.get("accession", ""))
        exp_acc = params.get("accession", "")
        # If accession looks like a file accession, it's misplaced
        if exp_acc.upper().startswith("ENCFF"):
            file_acc = exp_acc
            exp_acc = ""
        # If we don't have experiment accession, find it from history
        if not exp_acc or not exp_acc.upper().startswith("ENCSR"):
            found_exp = _find_experiment_for_file(file_acc, conversation_history)
            if found_exp:
                logger.warning("Added missing experiment accession for get_file_metadata",
                              file_accession=file_acc, experiment=found_exp)
                return "get_file_metadata", {
                    "accession": found_exp, "file_accession": file_acc}
            else:
                # No experiment found — return routing error
                return "get_file_metadata", {
                    "file_accession": file_acc,
                    "__routing_error__": (
                        f"Cannot look up file metadata for {file_acc} without "
                        f"knowing its parent experiment (ENCSR...). "
                        f"Please first query the experiment that contains this "
                        f"file, then ask about the file."
                    ),
                }

    # --- General ENCFF catch-all (any ENCODE tool, not just get_experiment) ---
    # When the user message contains an ENCFF file accession but the tool
    # called is *not* get_file_metadata (e.g. LLM called get_files_by_type
    # with a hallucinated ENCSR instead).
    if tool not in ("get_file_metadata",):
        encff_in_msg = re.findall(r'(ENCFF[A-Z0-9]{6})', user_message.upper())
        if encff_in_msg:
            file_acc = encff_in_msg[0]
            exp_acc = _find_experiment_for_file(file_acc, conversation_history)
            if exp_acc:
                logger.warning(
                    "General ENCFF catch-all: ENCFF in user message, rerouting to get_file_metadata",
                    from_tool=tool, file_accession=file_acc, experiment=exp_acc,
                )
                return "get_file_metadata", {
                    "accession": exp_acc, "file_accession": file_acc}
            else:
                logger.warning(
                    "General ENCFF catch-all: ENCFF in user message but no parent experiment found",
                    from_tool=tool, file_accession=file_acc,
                )
                return "get_file_metadata", {
                    "file_accession": file_acc,
                    "__routing_error__": (
                        f"Cannot look up file metadata for {file_acc} without "
                        f"knowing its parent experiment (ENCSR...). "
                        f"Please first query the experiment that contains this "
                        f"file, then ask about the file."
                    ),
                }

    return tool, params


def _validate_encode_params(tool: str, params: dict, user_message: str) -> dict:
    """
    Last-chance fix for ENCODE tool params before the MCP call.

    Common LLM mistakes this catches:
    - search_by_biosample called without search_term (required) but with
      assay_title containing the biosample name (e.g. assay_title=MEL).
    - organism=Homo sapiens/Mus musculus added when the user never
      mentioned a species — wrong organism filter → zero results.
    """
    params = dict(params)  # shallow copy

    # --- Fix 1: missing search_term in search_by_biosample ---
    if tool == "search_by_biosample" and "search_term" not in params:
        # Try to salvage from assay_title if it doesn't look like an assay
        assay_val = params.get("assay_title", "")
        if assay_val and not _looks_like_assay(assay_val):
            params["search_term"] = assay_val
            del params["assay_title"]
            logger.warning("Moved assay_title to search_term (was biosample, not assay)",
                          search_term=assay_val)
        else:
            # Try to extract from user message
            extracted = _extract_encode_search_term(user_message)
            if extracted:
                params["search_term"] = extracted
                logger.warning("Injected missing search_term from user message",
                              search_term=extracted)

    # --- Fix 2: strip organism unless user explicitly mentioned it ---
    if "organism" in params:
        msg_lower = user_message.lower()
        _explicit_organism = any(kw in msg_lower for kw in (
            "mouse", "human", "homo sapiens", "mus musculus",
            "drosophila", "c. elegans", "worm", "fly",
        ))
        if not _explicit_organism:
            removed = params.pop("organism")
            logger.info("Stripped auto-organism (user didn't request it)",
                       removed_organism=removed)

    # --- Fix 3: replace hallucinated ENCSR with what the user explicitly stated ---
    # If the user message contains a valid ENCSR accession and the LLM put a
    # *different* ENCSR in "accession", always trust the user's value.
    if "accession" in params and params["accession"]:
        _encsr_in_msg = re.findall(r'(ENCSR[A-Z0-9]{6})', user_message, re.IGNORECASE)
        if _encsr_in_msg:
            _user_encsr = _encsr_in_msg[0].upper()
            if params["accession"].upper() != _user_encsr:
                logger.warning(
                    "Replaced hallucinated ENCSR with user-stated accession",
                    hallucinated=params["accession"], correct=_user_encsr,
                )
                params["accession"] = _user_encsr

    return params


def _validate_analyzer_params(
    tool: str, params: dict, user_message: str,
    conversation_history: list | None = None,
    history_blocks: list | None = None,
    project_dir: str = "",
) -> dict:
    """
    Always force *work_dir* from conversation context and strip unknown params.

    The LLM cannot be trusted to produce the correct work_dir — it may
    emit a placeholder (``/work_dir``, ``{work_dir}``), an invented path,
    or the project-level dir instead of a workflow dir.  We therefore
    always resolve the real work_dir from history and override whatever
    the LLM supplied.

    Also strips parameters that the Analyzer MCP tool doesn't accept
    (e.g. ``sample=Jamshid``) to prevent Pydantic validation errors.
    """
    params = dict(params)  # shallow copy

    # --- Strip unknown params for each tool ---
    _KNOWN_PARAMS: dict[str, set[str]] = {
        "list_job_files": {"work_dir", "run_uuid", "extensions", "compact", "max_depth"},
        "find_file": {"file_name", "work_dir", "run_uuid"},
        "read_file_content": {"file_path", "work_dir", "run_uuid", "preview_lines"},
        "parse_csv_file": {"file_path", "work_dir", "run_uuid", "max_rows"},
        "parse_bed_file": {"file_path", "work_dir", "run_uuid", "max_records"},
        "get_analysis_summary": {"run_uuid", "work_dir"},
        "categorize_job_files": {"work_dir", "run_uuid"},
    }
    allowed = _KNOWN_PARAMS.get(tool)

    # --- Rescue subfolder hints before stripping unknown params ---
    # The LLM may pass invented params like subfolder=annot, path=annot,
    # directory=annot etc.  Capture these before they're stripped.
    _subfolder_hint = ""
    for _sf_key in ("subfolder", "subpath", "path", "directory", "folder", "subdir"):
        if _sf_key in params and (not allowed or _sf_key not in allowed):
            _subfolder_hint = params[_sf_key]
            break

    if allowed:
        _extra = set(params) - allowed
        if _extra:
            logger.warning("Stripping unknown Analyzer params",
                          tool=tool, extra_params=sorted(_extra))
            params = {k: v for k, v in params.items() if k in allowed}

    # --- Force work_dir from context ---
    ctx = _extract_job_context_from_history(
        conversation_history, history_blocks=history_blocks
    )
    real_wd = ctx.get("work_dir", "")

    # If multiple workflows, pick the matching one by filename/sample
    if not real_wd and ctx.get("workflows"):
        real_wd = ctx["workflows"][-1].get("work_dir", "")
    # Fallback: use the project directory if no workflow-level work_dir
    if not real_wd and project_dir:
        real_wd = project_dir
    workflows = ctx.get("workflows", [])
    if len(workflows) > 1:
        _fname = params.get("file_name", "").lower()
        for wf in workflows:
            sn = wf.get("sample_name", "").lower()
            if sn and (_fname and sn in _fname):
                real_wd = wf["work_dir"]
                break

    llm_wd = params.get("work_dir", "")
    if real_wd:
        # "list workflows" needs the *project* dir, not a workflow dir.
        if tool == "list_job_files" and re.search(
            r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
            user_message, re.IGNORECASE,
        ):
            # If real_wd came from a workflow (has /workflow\d+/ suffix),
            # strip to the parent project directory.
            if re.search(r'/workflow\d+/?$', real_wd):
                real_wd = real_wd.rstrip("/").rsplit("/", 1)[0]
            # Otherwise real_wd is already the project dir (from project_dir fallback)
            params["max_depth"] = 1  # only top-level dirs
            params["name_pattern"] = "workflow*"  # filter to workflow dirs only

        # "list files in <subpath>" — append subfolder to work_dir.
        # Source 1 (preferred): parse from user message — preserves
        #   workflow prefix (e.g. "workflow1/annot") that the LLM may strip.
        # Source 2 (fallback): subfolder hint from LLM's invented param.
        if tool == "list_job_files" and not params.get("max_depth"):
            _sub = ""
            _sub_m = re.search(
                r'\b(?:list|show)\s+(?:the\s+)?files?\s+'
                r'(?:in|under|at|of)\s+(.+)',
                user_message, re.IGNORECASE,
            )
            if _sub_m:
                _sub = _sub_m.group(1).strip().strip('"\'')
            if not _sub:
                _sub = _subfolder_hint
            if _sub:
                real_wd = _resolve_workflow_path(_sub, real_wd, workflows)
                # Show only immediate contents of the subfolder, not deep recursion
                params["max_depth"] = 1

        # If the incoming work_dir is already a valid subdirectory of the
        # resolved context dir OR the project dir, keep it — auto-generated
        # calls (from _auto_generate_data_calls) already resolved the correct
        # path and overriding would lose the subfolder.
        # Derive project dir from real_wd (strip /workflowN suffix).
        _proj_wd = (
            real_wd.rstrip("/").rsplit("/", 1)[0]
            if re.search(r'/workflow\d+/?$', real_wd)
            else real_wd
        )
        _is_valid_sub = llm_wd and (
            llm_wd.startswith(real_wd.rstrip("/") + "/")
            or llm_wd.startswith(_proj_wd.rstrip("/") + "/")
            or llm_wd == _proj_wd  # exact match on project dir
        )
        if _is_valid_sub:
            logger.info(
                "Keeping incoming work_dir (valid subdirectory of context/project)",
                incoming=llm_wd, context_wd=real_wd, project_wd=_proj_wd, tool=tool,
            )
            params["work_dir"] = llm_wd
        else:
            if real_wd != llm_wd:
                logger.warning(
                    "Overriding LLM work_dir with context value",
                    llm_value=llm_wd, resolved=real_wd, tool=tool,
                )
            params["work_dir"] = real_wd
    elif not llm_wd:
        logger.warning(
            "No work_dir in context or LLM params", tool=tool,
        )

    return params


def _find_experiment_for_file(file_accession: str,
                              conversation_history: list | None) -> str | None:
    """
    Scan conversation history to find the ENCSR experiment accession
    that was queried when the given ENCFF file accession appeared.

    Strategy: look for assistant messages that mention the ENCFF accession
    and also contain an ENCSR accession (the parent experiment).
    """
    if not conversation_history or not file_accession:
        return None

    file_acc_upper = file_accession.upper()

    for msg in reversed(conversation_history):
        content = msg.get("content", "")
        if file_acc_upper not in content.upper():
            continue
        # This message mentions our file — find ENCSR accessions in it
        experiments = re.findall(r'(ENCSR[A-Z0-9]{6})', content, re.IGNORECASE)
        if experiments:
            return experiments[0].upper()

    # Fallback: find the most recent ENCSR in any message
    for msg in reversed(conversation_history):
        content = msg.get("content", "")
        experiments = re.findall(r'(ENCSR[A-Z0-9]{6})', content, re.IGNORECASE)
        if experiments:
            return experiments[0].upper()

    return None


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


# ==================== Output Contract Validator ====================

def _validate_llm_output(
    response: str,
    active_skill: str,
    history_blocks: list | None = None,
) -> tuple[str, list[str]]:
    """
    Validate the LLM's raw response against output contracts.
    Auto-corrects recoverable violations; logs all violations.

    Returns:
        (cleaned_response, violations) — list of human-readable violation strings.
    """
    violations: list[str] = []
    cleaned = response

    # 1. Malformed DATA_CALL: has the marker but no tool=
    _dc_incomplete = re.findall(r'\[\[DATA_CALL:[^\]]*\]\]', cleaned)
    for _tag in _dc_incomplete:
        if "tool=" not in _tag:
            violations.append(f"Malformed DATA_CALL (no tool=): {_tag[:80]}")
            cleaned = cleaned.replace(_tag, "")  # strip it

    # 2. Multiple APPROVAL_NEEDED tags — keep only the first
    _approval_count = cleaned.count("[[APPROVAL_NEEDED]]")
    if _approval_count > 1:
        violations.append(f"Duplicate APPROVAL_NEEDED tags ({_approval_count})")
        # Keep first, remove rest
        _first_pos = cleaned.index("[[APPROVAL_NEEDED]]") + len("[[APPROVAL_NEEDED]]")
        cleaned = cleaned[:_first_pos] + cleaned[_first_pos:].replace("[[APPROVAL_NEEDED]]", "")

    # 3. SKILL_SWITCH during active job (block status=RUNNING)
    _has_switch = "[[SKILL_SWITCH_TO:" in cleaned
    if _has_switch and history_blocks:
        for blk in reversed(history_blocks):
            if blk.type == "EXECUTION_JOB" and blk.status == "RUNNING":
                violations.append("SKILL_SWITCH attempted during running job — stripped")
                cleaned = re.sub(r'\[\[SKILL_SWITCH_TO:\s*\w+\]\]', '', cleaned)
                break

    # 4. DATA_CALL with unknown tool name (not in any known registry)
    # Using a lightweight check — just flag obviously wrong patterns
    _known_tools = {
        # ENCODE
        "get_experiment", "search_by_biosample", "search_by_assay",
        "search_by_target", "search_by_organism", "get_files_by_type",
        "get_files_summary", "get_file_metadata", "get_file_url",
        "get_available_output_types", "get_all_metadata", "list_experiments",
        "get_cache_stats", "get_server_info",
        # Launchpad (Dogme)
        "submit_dogme_job", "check_nextflow_status", "get_dogme_report",
        "submit_dogme_nextflow", "find_pod5_directory", "generate_dogme_config",
        "scaffold_dogme_dir", "get_job_logs", "get_job_debug",
        # Analyzer (Analysis)
        "list_job_files", "find_file", "read_file_content",
        "parse_csv_file", "parse_bed_file", "get_analysis_summary",
        "categorize_job_files",
    }
    for _dc_m in re.finditer(r'\[\[DATA_CALL:.*?tool=(\w+)', cleaned):
        _tool = _dc_m.group(1)
        if _tool not in _known_tools:
            violations.append(f"Unknown tool in DATA_CALL: {_tool}")

    # 5. Mixed sources in one response for non-browsing context
    _sources_in_tags = set()
    for _dc_m in re.finditer(r'\[\[DATA_CALL:\s*(?:consortium|service)=(\w+)', cleaned):
        _sources_in_tags.add(_dc_m.group(1))
    if len(_sources_in_tags) > 1:
        # Only flag if it's not an ENCODE + analyzer browsing combo
        if _sources_in_tags != {"encode", "analyzer"} and _sources_in_tags != {"launchpad", "analyzer"}:
            violations.append(f"Mixed sources in DATA_CALL tags: {sorted(_sources_in_tags)}")

    if violations:
        logger.warning("LLM output violations detected",
                      count=len(violations), violations=violations,
                      skill=active_skill)

    return cleaned, violations


def _auto_detect_skill_switch(user_message: str, current_skill: str) -> str | None:
    """
    Pre-LLM safety net: detect when the user's message obviously requires
    a different skill than the currently active one.

    Returns the correct skill key if a switch is needed, or None to stay.
    Only triggers on strong, unambiguous signals to avoid false positives.
    """
    msg_lower = user_message.lower()

    # --- Signals for analyze_local_sample ---
    # User mentions a local file path + analysis intent
    _has_local_path = bool(re.search(r'(/[a-z_][\w/.-]+|~[\w/.-]+)', user_message))
    _analysis_words = ["analyze", "analyse", "process", "run", "submit", "launch"]
    _has_analysis = any(w in msg_lower for w in _analysis_words)
    _sample_words = ["sample", "pod5", "local", "my data", "my files"]
    _has_sample = any(w in msg_lower for w in _sample_words)
    _data_type_words = ["cdna", "dna", "rna", "fiber-seq", "fiberseq"]
    _has_data_type = any(w in msg_lower for w in _data_type_words)

    if current_skill not in ("analyze_local_sample",):
        # Strong signal: path + (analysis verb OR sample keyword OR data type)
        # Even from Dogme analysis skills (run_dogme_*), a message like
        # "Analyze the local CDNA sample at /path" is clearly a NEW submission,
        # not a follow-up on the current job's results. Require at least TWO
        # signals when switching FROM a Dogme analysis skill to avoid false
        # positives on things like "parse /path/to/result.csv".
        _signal_count = sum([_has_analysis, _has_sample, _has_data_type])
        _from_dogme_skill = current_skill in (
            "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna"
        )
        if _has_local_path:
            if _from_dogme_skill:
                # From a Dogme skill, require path + at least 2 of:
                # analysis verb, sample keyword, data type keyword
                if _signal_count >= 2:
                    return "analyze_local_sample"
            else:
                # From other skills, path + any 1 signal is enough
                if _signal_count >= 1:
                    return "analyze_local_sample"

    # --- Signals for ENCODE_Search ---
    _encode_words = ["encode", "encsr", "encff", "encode portal"]
    _search_words = ["search", "how many", "experiments", "accession", "biosample"]
    _has_encode = any(w in msg_lower for w in _encode_words)
    _has_search = any(w in msg_lower for w in _search_words)

    if current_skill not in ("ENCODE_Search", "ENCODE_LongRead"):
        if _has_encode and _has_search:
            return "ENCODE_Search"
        # Strong signal: explicit accession mention
        if re.search(r'ENCSR[A-Z0-9]{6}', user_message, re.IGNORECASE):
            return "ENCODE_Search"

    # --- Signals for analyze_job_results ---
    _results_words = ["qc report", "quality control", "parse the",
                      "read the output", "show me the results",
                      "check the bed", "analyze results", "job results"]
    if current_skill != "analyze_job_results":
        if any(w in msg_lower for w in _results_words):
            return "analyze_job_results"

    # --- Signals for download_files ---
    _download_words = ["download", "grab", "fetch", "save"]
    _has_download = any(w in msg_lower for w in _download_words)
    _has_file_accession = bool(re.search(r'ENCFF[A-Z0-9]{6}', user_message, re.IGNORECASE))
    _has_url = bool(re.search(r'https?://', msg_lower))
    if current_skill != "download_files":
        if _has_download and (_has_file_accession or _has_url):
            return "download_files"

    return None


# Stop-words for the ENCODE search-term extractor.  These are stripped before
# identifying the likely biosample / search term from the user's query.
_ENCODE_STOP_WORDS = frozenset([
    "how", "many", "much", "does", "do", "did", "is", "are", "was", "were",
    "what", "which", "where", "the", "a", "an", "of", "in", "for", "on",
    "and", "or", "to", "with", "from", "by", "at", "its", "it", "this",
    "that", "there", "have", "has", "had", "can", "could", "will", "would",
    "show", "me", "give", "get", "find", "search", "list", "tell", "about",
    "encode", "experiments", "experiment", "data", "results", "available",
    "portal", "database", "total", "count", "number", "please", "i", "want",
    "need", "look", "up", "any", "all", "some",
    # Referential / pronoun words — should not be treated as search terms
    "them", "they", "those", "these", "their", "its", "ones", "samples",
    # Visualization / follow-up — not search terms
    "plot", "chart", "graph", "histogram", "scatter", "visualize", "visualise",
    "heatmap", "pie", "bar", "box", "distribution", "summarize", "summarise",
    "compare", "filter", "sort", "group", "aggregate", "breakdown", "table",
])


def _extract_encode_search_term(user_message: str) -> str | None:
    """
    Extract the most likely biosample / search term from an ENCODE query.

    Uses pattern matching first (e.g. "how many X experiments"), then falls
    back to stripping stop-words and returning the remaining content word(s).
    Returns None only if nothing usable can be extracted.
    """
    msg = user_message.strip()

    # Pattern 1: "how many <TERM> experiments"
    m = re.search(r'how many\s+(.+?)\s+experiments?', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pattern 2: "search encode for <TERM>"  /  "search for <TERM>"
    m = re.search(r'search\s+(?:encode\s+)?for\s+(.+?)(?:\s+experiments?|\s*$)', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pattern 3: "<TERM> experiments in encode"
    m = re.search(r'(.+?)\s+experiments?\s+(?:in|on|from)\s+encode', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pattern 4: "does encode have <TERM>"
    m = re.search(r'does\s+encode\s+have\s+(.+?)(?:\s+experiments?|\s*\??\s*$)', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Fallback: strip stop-words and return the remaining token(s)
    tokens = re.findall(r'[A-Za-z0-9][\w.\-]*', msg)
    content = [t for t in tokens if t.lower() not in _ENCODE_STOP_WORDS]
    if content:
        return " ".join(content)

    return None


def _auto_generate_data_calls(user_message: str, skill_key: str,
                              conversation_history: list | None = None,
                              history_blocks: list | None = None,
                              project_dir: str = "") -> list[dict]:
    """
    Safety net: if the LLM failed to generate DATA_CALL tags, detect obvious
    patterns in the user's message and auto-generate the appropriate tool calls.

    Also resolves conversational references ("them", "each of them", "these")
    by scanning recent conversation history for accessions or biosample terms.

    Returns a list of dicts: [{"source_type": str, "source_key": str, "tool": str, "params": dict}]
    """
    calls = []
    msg_lower = user_message.lower()

    # --- DF reference / visualization follow-up: never auto-call ---
    # If the user references an existing DataFrame (DF1, DF2, ...) this is
    # a follow-up on in-memory data — no new API call is needed.
    if re.search(r'\bDF\s*\d+\b', user_message, re.IGNORECASE):
        return calls
    # Pure visualization intent (no new data request)
    _VIZ_KEYWORDS = ("plot", "chart", "graph", "histogram", "scatter",
                     "visualize", "visualise", "heatmap", "pie chart",
                     "bar chart", "box plot", "distribution")
    if any(kw in msg_lower for kw in _VIZ_KEYWORDS):
        return calls

    # --- Browsing commands (highest priority, skill-independent) ---
    # "list workflows", "list files" etc. always route to analyzer when there
    # is a project directory, regardless of the active skill.  Must run before
    # any skill-specific logic (e.g. ENCODE catch-all) to avoid mis-routing.
    job_context = _extract_job_context_from_history(
        conversation_history, history_blocks=history_blocks)
    work_dir = job_context.get("work_dir", "")
    run_uuid = job_context.get("run_uuid", "")
    workflows = job_context.get("workflows", [])
    # Derive the project directory — prefer parent-of-work_dir (when
    # work_dir is a workflow directory like .../test16/workflow1); fall back
    # to the explicitly-passed project_dir (from the chat endpoint).
    _project_dir = ""
    if work_dir:
        if re.search(r'/workflow\d+/?$', work_dir):
            _project_dir = work_dir.rstrip("/").rsplit("/", 1)[0]
        else:
            _project_dir = work_dir  # already project-level
    if not _project_dir and project_dir:
        _project_dir = project_dir

    # --- "list workflows" command ---
    if re.search(r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b', msg_lower):
        if _project_dir:
            calls.append({
                "source_type": "service", "source_key": "analyzer",
                "tool": "list_job_files",
                "params": {"work_dir": _project_dir, "max_depth": 1, "name_pattern": "workflow*"},
            })
        return calls

    # --- "list project files [in <path>]" — explicitly target project root ---
    _list_proj_m = re.search(
        r'\b(?:list|show)\s+project\s+files?\b'
        r'(?:\s+(?:in|under|at|of)\s+(.+?))?\s*$',
        user_message, re.IGNORECASE,
    )
    if _list_proj_m and _project_dir:
        _subpath = (_list_proj_m.group(1) or "").strip().strip('"\'')
        _target_wd = _resolve_workflow_path(
            _subpath, _project_dir, workflows,
        ) if _subpath else _project_dir
        _params: dict = {"work_dir": _target_wd, "max_depth": 1}
        calls.append({
            "source_type": "service", "source_key": "analyzer",
            "tool": "list_job_files",
            "params": _params,
        })
        return calls

    # --- "list files" / "list files in <path>" command ---
    _list_files_m = re.search(
        r'\b(?:list|show|what)\s+(?:the\s+)?files?\b'
        r'(?:\s+(?:in|under|at|of)\s+(.+?))?\s*$',
        user_message, re.IGNORECASE,
    )
    if _list_files_m:
        _subpath = (_list_files_m.group(1) or "").strip().strip('"\'')
        if _subpath:
            # Try work_dir/<subpath> first; fall back to project_dir/<subpath>
            _target_wd = ""
            _wd_candidate = _resolve_workflow_path(
                _subpath, work_dir, workflows,
            ) if work_dir else ""
            if _wd_candidate and os.path.isdir(_wd_candidate):
                _target_wd = _wd_candidate
            elif _project_dir and _project_dir != work_dir:
                _proj_candidate = _resolve_workflow_path(
                    _subpath, _project_dir, workflows,
                )
                if _proj_candidate and os.path.isdir(_proj_candidate):
                    _target_wd = _proj_candidate
            # Last resort: use whichever candidate we have, let the server
            # report "not found" if neither exists.
            if not _target_wd:
                _target_wd = _wd_candidate or (
                    f"{_project_dir.rstrip('/')}/{_subpath}" if _project_dir else ""
                )
        else:
            # No subpath → list current workflow dir
            _target_wd = work_dir or _project_dir or ""

        if _target_wd:
            _params_f: dict = {"work_dir": _target_wd}
            if _subpath:
                _params_f["max_depth"] = 1
            calls.append({
                "source_type": "service", "source_key": "analyzer",
                "tool": "list_job_files",
                "params": _params_f,
            })
        # If no resolvable path, return empty calls — the browsing error
        # handler in the results pipeline will show helpful suggestions.
        return calls

    # Organism lookup for KNOWN biosamples.  This is NOT exhaustive — it
    # exists only so we can add an organism= hint when we recognise the term.
    # Unknown terms still get sent to the API (see catch-all at the bottom).
    _KNOWN_ORGANISMS: dict[str, str] = {
        # Human
        "k562": "Homo sapiens", "gm12878": "Homo sapiens",
        "hela": "Homo sapiens", "hepg2": "Homo sapiens",
        "hek293": "Homo sapiens", "a549": "Homo sapiens",
        "mcf-7": "Homo sapiens", "mcf7": "Homo sapiens",
        "jurkat": "Homo sapiens", "imr-90": "Homo sapiens",
        "imr90": "Homo sapiens", "u2os": "Homo sapiens",
        "hff": "Homo sapiens", "wtc-11": "Homo sapiens",
        "lncap": "Homo sapiens", "panc-1": "Homo sapiens",
        "sk-n-sh": "Homo sapiens", "h1": "Homo sapiens",
        "h9": "Homo sapiens", "caco-2": "Homo sapiens",
        "sh-sy5y": "Homo sapiens",
        # Mouse
        "c2c12": "Mus musculus", "nih3t3": "Mus musculus",
        "mef": "Mus musculus", "mel": "Mus musculus",
        "es-e14": "Mus musculus", "mesc": "Mus musculus",
        "g1e": "Mus musculus", "ch12": "Mus musculus",
        "v6.5": "Mus musculus",
    }

    # --- ENCODE patterns ---
    # Detect ENCODE accession numbers (ENCSR, ENCFF, ENCLB, etc.)
    accession_matches = re.findall(r'(ENC[A-Z]{2}\d{3}[A-Z]{3})', user_message, re.IGNORECASE)
    accessions = [a.upper() for a in accession_matches]

    # --- Download intent detection ---
    # "download ENCFF921XAH" should resolve the file URL and start a download,
    # not trigger a get_files_by_type search.
    _download_intent = any(w in msg_lower for w in (
        "download", "grab", "fetch", "save", "get me",
    ))
    _encff_accessions = [a for a in accessions if a.startswith("ENCFF")]

    if _download_intent and _encff_accessions:
        # Find the parent experiment — check current message first, then history
        _encsr_in_msg = [a for a in accessions if a.startswith("ENCSR")]
        for _encff in _encff_accessions:
            _parent_exp = _encsr_in_msg[0] if _encsr_in_msg else \
                _find_experiment_for_file(_encff, conversation_history)
            if _parent_exp:
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_file_metadata",
                    "params": {"accession": _parent_exp, "file_accession": _encff},
                    "_chain": "download",  # signal to auto-download after metadata resolves
                })
            else:
                # No parent experiment — try to get metadata with just the ENCFF
                # (the _correct_tool_routing code will handle this)
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_file_metadata",
                    "params": {"file_accession": _encff},
                    "_chain": "download",
                })
        return calls

    # If no accession in the message, check for conversational references
    # ("them", "these", "each", "all of them", "for those", etc.)
    referential_words = ["them", "these", "those", "each", "all of them",
                         "each of them", "for those", "the experiments",
                         "the accessions", "same", "list"]
    _has_referential = any(w in msg_lower for w in referential_words)

    if not accessions and _has_referential:
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
                         "download", "available", "accessions", "alignments"]
        # Note: "methylated" removed from file_keywords — it's a follow-up filter
        # handled by _inject_job_context, not a new fetch trigger.
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
        # Detect assay-type filter in user message using the module-level map.
        detected_assay: str | None = None
        for alias, canonical in _ENCODE_ASSAY_ALIASES.items():
            if alias in msg_lower:
                detected_assay = canonical
                break

        # --- Strategy: try specific detections first, then catch-all ---
        # 1. Known biosample (organism hint available)
        # 2. Referential follow-up from conversation history
        # 3. Assay-only query
        # 4. Known target protein / histone mark
        # 5. CATCH-ALL: extract unknown term and send to search_by_biosample

        # 1. Check for a known biosample (lets us add organism= hint)
        for keyword, organism in _KNOWN_ORGANISMS.items():
            if keyword in msg_lower:
                # Grab the original-case version from the user message
                _orig_m = re.search(rf'\b({re.escape(keyword)})\b', user_message, re.IGNORECASE)
                _search_term = _orig_m.group(1) if _orig_m else keyword.upper()
                params: dict[str, str] = {"search_term": _search_term}
                # Only add organism if user explicitly mentioned species
                if "mouse" in msg_lower or "mus musculus" in msg_lower:
                    params["organism"] = "Mus musculus"
                elif "human" in msg_lower or "homo sapiens" in msg_lower:
                    params["organism"] = "Homo sapiens"
                if detected_assay:
                    params["assay_title"] = detected_assay
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "search_by_biosample", "params": params,
                })
                break

        # 2. Referential follow-up — scan history for previous biosample term
        if not calls and (_has_referential or detected_assay) and conversation_history:
            for hist_msg in reversed(conversation_history[-6:]):
                content_lower = hist_msg.get("content", "").lower()
                for keyword, organism in _KNOWN_ORGANISMS.items():
                    if keyword in content_lower:
                        _orig_m = re.search(rf'\b({re.escape(keyword)})\b',
                                            hist_msg.get("content", ""), re.IGNORECASE)
                        _search_term = _orig_m.group(1) if _orig_m else keyword.upper()
                        params = {"search_term": _search_term}
                        if detected_assay:
                            params["assay_title"] = detected_assay
                        calls.append({
                            "source_type": "consortium", "source_key": "encode",
                            "tool": "search_by_biosample", "params": params,
                        })
                        break
                if calls:
                    break

        # Assay-only query: assay detected but no biosample found in message or history.
        # e.g. "how many RNA-seq experiments are in ENCODE?"
        if not calls and detected_assay and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
            calls.append({
                "source_type": "consortium", "source_key": "encode",
                "tool": "search_by_assay",
                "params": {"assay_title": detected_assay},
            })

        # 4. Target-based query: no biosample, no assay, but a known target protein
        # e.g. "search ENCODE for CTCF" or "H3K27ac experiments"
        if not calls and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
            _known_targets = {
                "ctcf", "polr2a", "ep300", "max", "myc", "jun", "fos",
                "rest", "yy1", "tcf7l2", "gata1", "gata2", "spi1",
                "cebpb", "stat1", "stat3", "irf1", "nrf1", "rad21",
                "smc3", "nipbl", "znf143", "brd4", "mediator",
                "h3k27ac", "h3k4me3", "h3k4me1", "h3k36me3",
                "h3k27me3", "h3k9me3", "h3k79me2", "h2afz", "h4k20me1",
            }
            # Check if user mentions a known target (word-boundary match)
            for _tgt in _known_targets:
                if re.search(rf'\b{re.escape(_tgt)}\b', msg_lower):
                    # Use original case from user message for the target value
                    _tgt_match = re.search(rf'\b({re.escape(_tgt)})\b', user_message, re.IGNORECASE)
                    _tgt_val = _tgt_match.group(1) if _tgt_match else _tgt.upper()
                    calls.append({
                        "source_type": "consortium", "source_key": "encode",
                        "tool": "search_by_target",
                        "params": {"target": _tgt_val},
                    })
                    break

        # 5. CATCH-ALL: nothing matched above but we're on an ENCODE skill.
        #    Extract the most likely search term from the user's message and
        #    send it to search_by_biosample.  Let the ENCODE API decide if
        #    the term is valid — we can't enumerate every cell line / tissue.
        if not calls and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
            _extracted = _extract_encode_search_term(user_message)
            if _extracted:
                params = {"search_term": _extracted}
                # Infer organism from context words
                if "mouse" in msg_lower or "mus musculus" in msg_lower:
                    params["organism"] = "Mus musculus"
                elif "human" in msg_lower or "homo sapiens" in msg_lower:
                    params["organism"] = "Homo sapiens"
                if detected_assay:
                    params["assay_title"] = detected_assay
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "search_by_biosample", "params": params,
                })
                logger.info("Catch-all: extracted unknown search term for ENCODE",
                           search_term=_extracted)

    # --- Dogme / Analyzer file-parsing patterns ---
    # When in a Dogme analysis skill and user asks to parse/show a file,
    # auto-generate find_file + parse/read calls so the LLM gets real data.
    dogme_skills = {"run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
                    "analyze_job_results"}
    if not calls and skill_key in dogme_skills:
        # job_context, work_dir, run_uuid, workflows already extracted above
        # (browsing block at the top of this function).

        # --- "set workflow" / "use workflow" command ---
        # Handled conversationally — the LLM picks up which workflow to use.
        # We just inject context (done by _inject_job_context).

        # --- File-parse / read patterns ---
        parse_keywords = ["parse", "show me", "read", "open", "display",
                          "view", "get", "what's in", "contents of"]
        if any(kw in msg_lower for kw in parse_keywords):
            # Extract filename / relative path from user message.
            # Handles: "parse annot/File.csv", "parse workflow2/annot/File.csv",
            #          "parse File.csv", "show me the file File.csv"
            file_pattern = (
                r'(?:parse|show\s+me|read|open|display|view|get)'
                r'\s+(?:the\s+)?(?:file\s+)?'
                r'(\S+\.(?:csv|tsv|bed|txt|log|html))'
            )
            file_match = re.search(file_pattern, msg_lower)
            if file_match:
                filename = file_match.group(1)
                # Grab the original-case version from the raw message
                file_match_orig = re.search(file_pattern, user_message, re.IGNORECASE)
                if file_match_orig:
                    filename = file_match_orig.group(1)

                # Resolve the path: could be just a filename, a subpath
                # (annot/File.csv), or workflow-prefixed (workflow2/annot/File.csv).
                _resolved_wd, _resolved_file = _resolve_file_path(
                    filename, work_dir, workflows,
                )
                if not _resolved_wd and work_dir:
                    _resolved_wd = work_dir
                if not _resolved_wd and run_uuid:
                    _resolved_wd = None  # will use run_uuid fallback

                if _resolved_wd or run_uuid:
                    _params: dict = {"file_name": _resolved_file}
                    if _resolved_wd:
                        _params["work_dir"] = _resolved_wd
                    else:
                        _params["run_uuid"] = run_uuid
                    calls.append({
                        "source_type": "service", "source_key": "analyzer",
                        "tool": "find_file",
                        "params": _params,
                        "_chain": _pick_file_tool(_resolved_file),
                    })

    return calls


def _pick_file_tool(filename: str) -> str:
    """Return the right analyzer tool for a given filename extension."""
    lower = filename.lower()
    if lower.endswith((".csv", ".tsv")):
        return "parse_csv_file"
    elif lower.endswith(".bed"):
        return "parse_bed_file"
    else:
        return "read_file_content"


def _resolve_workflow_path(
    subpath: str,
    default_work_dir: str,
    workflows: list[dict],
) -> str:
    """
    Resolve a user-provided subpath to an absolute directory for `list_job_files`.

    Handles:
      - Empty subpath → default_work_dir (current workflow)
      - "workflow2" → that workflow's work_dir
      - "workflow2/annot" → that workflow's work_dir + "/annot"
      - "annot" → default_work_dir + "/annot"

    Returns the absolute directory path, or default_work_dir if resolution fails.
    """
    if not subpath:
        return default_work_dir

    # Split the subpath to check if the first component is a known workflow
    parts = subpath.replace("\\", "/").split("/", 1)
    first = parts[0].lower()
    remainder = parts[1] if len(parts) > 1 else ""

    # Check if first component matches a workflow folder name
    for wf in workflows:
        wf_dir = wf.get("work_dir", "")
        wf_folder = wf_dir.rstrip("/").rsplit("/", 1)[-1].lower() if wf_dir else ""
        if first == wf_folder:
            base = wf_dir.rstrip("/")
            return f"{base}/{remainder}" if remainder else base

    # Not a workflow name — treat entire subpath as relative to default_work_dir
    if default_work_dir:
        return f"{default_work_dir.rstrip('/')}/{subpath}"

    return default_work_dir


def _resolve_file_path(
    user_path: str,
    default_work_dir: str,
    workflows: list[dict],
) -> tuple[str, str]:
    """
    Resolve a user-provided file path (may include workflow prefix or subdir)
    into (work_dir, filename_or_relpath) for find_file / parse calls.

    Handles:
      - "File.csv"                           → (default_work_dir, "File.csv")
      - "annot/File.csv"                     → (default_work_dir, "File.csv")
                                                 (find_file searches recursively)
      - "workflow2/annot/File.csv"           → (workflow2_dir, "File.csv")

    Returns (resolved_work_dir, filename).  The filename is always just the
    basename so that find_file's recursive search works regardless of subdir.
    """
    user_path = user_path.replace("\\", "/").strip("/")
    parts = user_path.split("/")

    # If there are path components, check if the first one is a workflow folder
    if len(parts) > 1:
        first = parts[0].lower()
        for wf in workflows:
            wf_dir = wf.get("work_dir", "")
            wf_folder = wf_dir.rstrip("/").rsplit("/", 1)[-1].lower() if wf_dir else ""
            if first == wf_folder:
                # Match by sample name too for multi-workflow disambiguation
                return wf_dir, parts[-1]

        # Also try matching by sample name
        for wf in workflows:
            sn = wf.get("sample_name", "").lower()
            if sn and sn in parts[-1].lower():
                return wf.get("work_dir", default_work_dir), parts[-1]

    return default_work_dir, parts[-1]


def _build_conversation_state(
    active_skill: str,
    conversation_history: list | None,
    history_blocks: list | None = None,
    project_id: str | None = None,
) -> "ConversationState":
    """
    Build a structured ConversationState from blocks and conversation history.

    Fast path: if the last AGENT_PLAN block has a "state" key, use it directly.
    Slow path: scan all blocks to reconstruct state (same logic as the old
    _extract_job_context_from_history + parameter extraction).
    """
    from cortex.schemas import ConversationState

    # Fast path: check last AGENT_PLAN block for cached state
    if history_blocks:
        for blk in reversed(history_blocks):
            if blk.type == "AGENT_PLAN":
                _pl = get_block_payload(blk)
                cached = _pl.get("state")
                if cached and isinstance(cached, dict):
                    state = ConversationState.from_dict(cached)
                    state.active_skill = active_skill
                    # The cached state was saved *before* the block's own DFs
                    # were embedded, so latest_dataframe may be stale.  Patch it
                    # by scanning the same block's embedded dataframes.
                    _block_dfs = _pl.get("_dataframes", {})
                    _max_id = 0
                    for _bdf_val in _block_dfs.values():
                        _bdf_meta = _bdf_val.get("metadata", {})
                        _bdf_id = _bdf_meta.get("df_id")
                        if isinstance(_bdf_id, int) and _bdf_id > _max_id:
                            _max_id = _bdf_id
                    if _max_id > 0:
                        _patched = f"DF{_max_id}"
                        if state.latest_dataframe != _patched:
                            state.latest_dataframe = _patched
                            # Also append to known_dataframes if missing
                            if not any(k.startswith(_patched + " ") or k == _patched
                                       for k in state.known_dataframes):
                                _bdf_label = ""
                                _bdf_rows = "?"
                                for _v in _block_dfs.values():
                                    _m = _v.get("metadata", {})
                                    if _m.get("df_id") == _max_id:
                                        _bdf_label = _m.get("label", "")
                                        _bdf_rows = _m.get("row_count", "?")
                                        break
                                state.known_dataframes.append(
                                    f"{_patched} ({_bdf_label}, {_bdf_rows} rows)"
                                )
                    return state
                break  # only check the most recent AGENT_PLAN

    # Slow path: reconstruct from all blocks
    state = ConversationState(active_skill=active_skill, active_project=project_id)

    # --- Extract workflows from EXECUTION_JOB blocks ---
    if history_blocks:
        for blk in history_blocks:
            if blk.type != "EXECUTION_JOB":
                continue
            _pl = get_block_payload(blk)
            _wd = _pl.get("work_directory", "")
            _uuid = _pl.get("run_uuid", "")
            if _wd or _uuid:
                state.workflows.append({
                    "work_dir": _wd,
                    "sample_name": _pl.get("sample_name", ""),
                    "mode": _pl.get("mode", ""),
                    "run_uuid": _uuid,
                })
        if state.workflows:
            latest = state.workflows[-1]
            state.work_dir = latest.get("work_dir")
            state.sample_name = latest.get("sample_name")
            state.sample_type = latest.get("mode")
            state.active_workflow_index = len(state.workflows) - 1

    # --- Extract ENCSR/ENCFF accessions from conversation ---
    if conversation_history:
        _encsr_pattern = re.compile(r'ENCSR[0-9A-Z]{6}')
        _encff_pattern = re.compile(r'ENCFF[0-9A-Z]{6}')
        for msg in reversed(conversation_history):
            content = msg.get("content", "")
            if not state.active_experiment:
                _m = _encsr_pattern.search(content)
                if _m:
                    state.active_experiment = _m.group()
            if not state.active_file:
                _m = _encff_pattern.search(content)
                if _m:
                    state.active_file = _m.group()
            if state.active_experiment and state.active_file:
                break

    # --- Extract known dataframes from AGENT_PLAN blocks ---
    if history_blocks:
        for blk in history_blocks:
            if blk.type != "AGENT_PLAN":
                continue
            _pl = get_block_payload(blk)
            _dfs = _pl.get("_dataframes", {})
            for _df_key, _df_val in _dfs.items():
                _meta = _df_val.get("metadata", {})
                _df_id = _meta.get("df_id")
                _label = _meta.get("label", _df_key)
                _row_count = _meta.get("row_count", "?")
                if _df_id and _meta.get("visible", True):
                    state.known_dataframes.append(f"DF{_df_id} ({_label}, {_row_count} rows)")
        # The latest DF is the highest-numbered one
        if state.known_dataframes:
            state.latest_dataframe = state.known_dataframes[-1].split(" ")[0]  # e.g. "DF8"

    # --- Extract collected parameters (for analyze_local_sample) ---
    if conversation_history and active_skill == "analyze_local_sample":
        _sample_re = re.compile(r'(?:called?|named?|sample[_ ]?name)\s+["\']?(\w+)', re.I)
        _path_re = re.compile(r'(?:path|directory|folder)\s*[:=]?\s*(/\S+)', re.I)
        _type_re = re.compile(r'\b(cdna|c-dna|cDNA|rna|dna)\b', re.I)
        _genome_re = re.compile(r'\b(GRCh38|mm39|hg38|mm10)\b', re.I)
        for msg in conversation_history:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if "sample_name" not in state.collected_params:
                _m = _sample_re.search(content)
                if _m:
                    state.collected_params["sample_name"] = _m.group(1)
            if "path" not in state.collected_params:
                _m = _path_re.search(content)
                if _m:
                    state.collected_params["path"] = _m.group(1)
            if "sample_type" not in state.collected_params:
                _m = _type_re.search(content)
                if _m:
                    state.collected_params["sample_type"] = _m.group(1).upper()
            if "reference_genome" not in state.collected_params:
                _m = _genome_re.search(content)
                if _m:
                    state.collected_params["reference_genome"] = _m.group(1)

    return state


def _extract_job_context_from_history(
    conversation_history: list | None,
    history_blocks: list | None = None,
) -> dict:
    """
    Scan conversation / block history for job context.

    Returns a dict with:
      - 'work_dir'  : str — work directory of the *most recent* job
      - 'run_uuid'  : str — run UUID of the most recent job (internal only)
      - 'workflows' : list[dict] — all workflows in the project
           Each dict: {work_dir, sample_name, mode, run_uuid}
    """
    context: dict = {}

    # --- Primary source: EXECUTION_JOB blocks (authoritative) -----------
    workflows: list[dict] = []
    if history_blocks:
        for blk in history_blocks:
            if blk.type != "EXECUTION_JOB":
                continue
            _pl = get_block_payload(blk)
            _wd = _pl.get("work_directory", "")
            _uuid = _pl.get("run_uuid", "")
            if _wd or _uuid:
                workflows.append({
                    "work_dir": _wd,
                    "sample_name": _pl.get("sample_name", ""),
                    "mode": _pl.get("mode", ""),
                    "run_uuid": _uuid,
                })
        if workflows:
            latest = workflows[-1]
            context["work_dir"] = latest["work_dir"]
            context["run_uuid"] = latest["run_uuid"]
            context["workflows"] = workflows
            return context

    # --- Fallback: parse conversation text for UUID/work_dir (legacy) ----
    if not conversation_history:
        return context

    uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    for msg in reversed(conversation_history):
        content = msg.get("content", "")
        if not content:
            continue
        if "run_uuid" not in context:
            explicit_match = re.search(
                r'(?:use UUID|Run UUID|run_uuid|UUID)[:\s=]+\s*(' + uuid_pattern + r')',
                content, re.IGNORECASE
            )
            if explicit_match:
                context["run_uuid"] = explicit_match.group(1).lower()
        if "work_dir" not in context:
            work_dir_match = re.search(r'Work Directory:\s*(\S+)', content)
            if work_dir_match:
                context["work_dir"] = work_dir_match.group(1).strip()
        if "run_uuid" in context and "work_dir" in context:
            break
    if "run_uuid" not in context:
        for msg in reversed(conversation_history):
            content = msg.get("content", "")
            uuid_matches = re.findall(uuid_pattern, content, re.IGNORECASE)
            if uuid_matches:
                context["run_uuid"] = uuid_matches[-1].lower()
                break
    return context


def _inject_job_context(user_message: str, active_skill: str,
                        conversation_history: list | None,
                        history_blocks: list | None = None) -> tuple[str, dict, dict]:
    """
    Inject relevant conversational context into the user message so the LLM
    can maintain continuity without having to parse conversation history itself.

    Returns:
        (augmented_message, injected_dataframes, debug_info)
        injected_dataframes is a dict suitable for merging into _embedded_dataframes.
        It contains the server-side filtered subset so the UI can render it.
        debug_info is a dict of diagnostic data for the UI debug panel.

    Covers:
    - Dogme skills: inject UUID and work directory
    - ENCODE skills: inject previous dataframe rows for follow-up filter questions
    """
    if not conversation_history:
        return user_message, {}, {}

    # --- Dogme skills: inject workflow directory paths ---
    dogme_skills = {"run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
                    "analyze_job_results"}
    if active_skill in dogme_skills:
        context = _extract_job_context_from_history(
            conversation_history, history_blocks=history_blocks
        )
        workflows = context.get("workflows", [])

        # Build [CONTEXT] line(s) — list ALL workflows so the LLM can
        # reference files from any workflow in the project.
        parts = []
        if workflows:
            if len(workflows) == 1:
                wf = workflows[0]
                parts.append(f"work_dir={wf['work_dir']}")
                if wf.get("sample_name"):
                    parts.append(f"sample={wf['sample_name']}")
            else:
                # Multiple workflows — enumerate them
                wf_lines = []
                for i, wf in enumerate(workflows, 1):
                    _folder = wf["work_dir"].rstrip("/").rsplit("/", 1)[-1] if wf["work_dir"] else f"workflow{i}"
                    _label = f"{_folder} (sample={wf.get('sample_name', '?')}, mode={wf.get('mode', '?')}): work_dir={wf['work_dir']}"
                    wf_lines.append(_label)
                parts.append("workflows=[\n  " + "\n  ".join(wf_lines) + "\n]")
                # Also note which one is the most recent / active
                latest = workflows[-1]
                _latest_folder = latest["work_dir"].rstrip("/").rsplit("/", 1)[-1] if latest["work_dir"] else "?"
                parts.append(f"active_workflow={_latest_folder}")
        elif context.get("work_dir"):
            # Fallback: single work_dir from conversation text
            parts.append(f"work_dir={context['work_dir']}")
        elif context.get("run_uuid"):
            # Legacy fallback
            parts.append(f"run_uuid={context['run_uuid']}")

        # Check for an explicit DF reference (e.g. "plot a histogram of DF1").
        # If found, look up the DataFrame from history and inject its metadata so
        # the LLM knows DF<N> is an in-memory table — NOT a file to look up.
        _df_ref_match = re.search(r'\bDF\s*(\d+)\b', user_message, re.IGNORECASE)
        _df_note = ""
        # Also detect implicit DF reference via plot/viz keywords ("plot this")
        _viz_keywords = {"plot", "chart", "graph", "histogram", "scatter",
                         "pie", "heatmap", "visualize", "bar chart", "box plot"}
        _implicit_df_ref = (
            not _df_ref_match
            and any(kw in user_message.lower() for kw in _viz_keywords)
        )
        _tgt_df_id = None
        if _df_ref_match:
            _tgt_df_id = int(_df_ref_match.group(1))
        elif _implicit_df_ref and history_blocks:
            # Find the highest DF ID across all history blocks
            for _hblk in reversed(history_blocks):
                if _hblk.type != "AGENT_PLAN":
                    continue
                _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                for _dfd in _hblk_dfs.values():
                    _m = _dfd.get("metadata", {})
                    _did = _m.get("df_id")
                    if isinstance(_did, int):
                        if _tgt_df_id is None or _did > _tgt_df_id:
                            _tgt_df_id = _did
                if _tgt_df_id is not None:
                    break  # found DFs in the most recent AGENT_PLAN

        if _tgt_df_id is not None and history_blocks:
            for _hblk in reversed(history_blocks):
                if _hblk.type != "AGENT_PLAN":
                    continue
                _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                for _dfd in _hblk_dfs.values():
                    _m = _dfd.get("metadata", {})
                    if _m.get("df_id") == _tgt_df_id:
                        _cols = _dfd.get("columns", [])
                        _nrows = len(_dfd.get("data", []))
                        _label = _m.get("label", f"DF{_tgt_df_id}")
                        _df_note = (
                            f"\n[NOTE: DF{_tgt_df_id} is an in-memory DataFrame from this "
                            f"conversation — it is NOT a file or run result to look up. "
                            f"Label: '{_label}'. Columns: {_cols}. Rows: {_nrows}. "
                            f"To visualize it use [[PLOT:...]] tags. "
                            f"Do NOT call find_file, list_job_files, or any analyzer tool for this.]"
                        )
                        break
                if _df_note:
                    break

        context_line = f"[CONTEXT: {', '.join(parts)}]" if parts else ""
        augmented = "\n".join(filter(None, [context_line, user_message])) + _df_note
        return augmented, {}, {"skill": active_skill, "context": "dogme",
                               "df_note_injected": bool(_df_note)}

    # --- Local sample intake: inject already-collected parameters ---
    # Weak models lose track of parameters gathered across turns when the
    # system prompt is large.  Scan the conversation so far and inject a
    # concise [CONTEXT] line so the LLM doesn't have to re-parse history.
    if active_skill == "analyze_local_sample":
        _collected: dict[str, str] = {}
        # Field names MUST match the skill doc (Local_Sample_Intake.md):
        #   sample_name, path, sample_type, reference_genome
        _field_patterns = {
            "sample_name": re.compile(
                r'(?:sample\s*name)[:\s*]+([^\n*,|]+)', re.IGNORECASE),
            "path": re.compile(
                r'(?:data\s*path|path|directory)[:\s*]+(/[^\n*,]+)', re.IGNORECASE),
            "sample_type": re.compile(
                r'(?:data\s*type|sample\s*type|type|mode)[:\s*]+'
                r'(DNA|RNA|CDNA|cDNA|Fiber-seq|Fiberseq)', re.IGNORECASE),
            "reference_genome": re.compile(
                r'(?:reference\s*genome|genome)[:\s*]+'
                r'(GRCh38|mm39|mm10|hg38|T2T-CHM13)', re.IGNORECASE),
        }

        # First pass: extract from the original user request and any assistant
        # summaries already in conversation_history.
        for msg in conversation_history:
            content = msg.get("content", "")
            for field, pat in _field_patterns.items():
                m = pat.search(content)
                if m:
                    _collected[field] = m.group(1).strip().rstrip("*").strip()

        # Heuristic: detect sample_type from keywords in ALL user messages
        # AND the current message (which hasn't been appended to history yet).
        # ALWAYS run this — user messages are the source of truth for sample_type,
        # not assistant echoes (which may have gotten it wrong, e.g. "DNA" for CDNA).
        _st_from_user: str | None = None
        _all_user_texts = [m.get("content", "") for m in conversation_history
                           if m.get("role") == "user"]
        _all_user_texts.append(user_message)  # current turn
        for _ut in _all_user_texts:
            _fl = _ut.lower()
            if "cdna" in _fl or "c-dna" in _fl:
                _st_from_user = "CDNA"; break
            elif "rna" in _fl and "cdna" not in _fl:
                _st_from_user = "RNA"; break
            elif "fiber" in _fl:
                _st_from_user = "Fiber-seq"; break
            elif "dna" in _fl:
                _st_from_user = "DNA"; break
        if _st_from_user:
            _collected["sample_type"] = _st_from_user

        # Heuristic: extract sample_name from phrasing like "called <name>"
        if "sample_name" not in _collected:
            for msg in conversation_history:
                if msg.get("role") != "user":
                    continue
                _called_m = re.search(
                    r'(?:called|named|name(?:d)?)\s+(\S+)', msg["content"], re.IGNORECASE)
                if _called_m:
                    _collected["sample_name"] = _called_m.group(1).strip().rstrip(".,;:")
                    break
            # Also check current message (first turn won't be in history yet)
            if "sample_name" not in _collected:
                _called_m = re.search(
                    r'(?:called|named|name(?:d)?)\s+(\S+)', user_message, re.IGNORECASE)
                if _called_m:
                    _collected["sample_name"] = _called_m.group(1).strip().rstrip(".,;:")

        # Heuristic: extract path from user messages containing absolute paths
        if "path" not in _collected:
            for msg in conversation_history:
                if msg.get("role") != "user":
                    continue
                _path_m = re.search(r'(/[^\s,;:*?"<>|]+)', msg["content"])
                if _path_m:
                    _collected["path"] = _path_m.group(1).strip()
                    break
            # Also check current message
            if "path" not in _collected:
                _path_m = re.search(r'(/[^\s,;:*?"<>|]+)', user_message)
                if _path_m:
                    _collected["path"] = _path_m.group(1).strip()

        # Heuristic: detect reference_genome from short user reply like "mm39"
        if "reference_genome" not in _collected:
            for msg in conversation_history:
                if msg.get("role") != "user":
                    continue
                _genome_m = re.match(
                    r'^\s*(GRCh38|mm39|mm10|hg38)\s*$', msg["content"], re.IGNORECASE)
                if _genome_m:
                    _collected["reference_genome"] = _genome_m.group(1).strip()

        # Also check current message for a genome answer
        _cur_genome_m = re.match(
            r'^\s*(GRCh38|mm39|mm10|hg38)\s*$', user_message, re.IGNORECASE)
        if _cur_genome_m:
            _collected["reference_genome"] = _cur_genome_m.group(1).strip()

        # Heuristic: infer reference_genome from organism keywords
        if "reference_genome" not in _collected:
            _all_text = " ".join(
                m.get("content", "") for m in conversation_history
                if m.get("role") == "user"
            ) + " " + user_message
            _all_lower = _all_text.lower()
            if "mouse" in _all_lower or "mus musculus" in _all_lower:
                _collected["reference_genome"] = "mm39"
            elif "human" in _all_lower or "homo sapiens" in _all_lower:
                _collected["reference_genome"] = "GRCh38"

        if _collected:
            _parts = []
            for k, v in _collected.items():
                _parts.append(f"{k}={v}")

            # If all 4 fields are collected, give the LLM an unambiguous
            # directive so it doesn't misread e.g. "CDNA" as "DNA".
            _required = {"sample_name", "path", "sample_type", "reference_genome"}
            if _required.issubset(_collected.keys()):
                context_line = (
                    f"[CONTEXT: ALL 4 parameters are collected — go straight to "
                    f"the approval summary. Use these EXACT values:\n"
                    f"  sample_name={_collected['sample_name']}\n"
                    f"  path={_collected['path']}\n"
                    f"  sample_type={_collected['sample_type']}\n"
                    f"  reference_genome={_collected['reference_genome']}\n"
                    f"Show the summary with these values and include [[APPROVAL_NEEDED]]. "
                    f"The pipeline is Dogme {_collected['sample_type']}.]"
                )
            else:
                context_line = (
                    f"[CONTEXT: Parameters already collected from this conversation: "
                    f"{', '.join(_parts)}. "
                    f"Do NOT re-ask for these. Only ask for fields still missing.]"
                )
            augmented = f"{context_line}\n{user_message}"
            return augmented, {}, {"skill": active_skill, "context": "local_sample_intake",
                                   "collected_params": _collected}

        return user_message, {}, {"skill": active_skill, "context": "local_sample_intake",
                                  "collected_params": {}}

    # --- ENCODE skills: inject previous search context for follow-ups ---
    encode_skills = {"ENCODE_Search", "ENCODE_LongRead"}
    if active_skill in encode_skills:
        msg_lower = user_message.lower()

        # Browsing commands ("list files", "list workflows") should NOT inject
        # previous ENCODE dataframes — they route to the analyzer, not ENCODE.
        _browsing_cmd_patterns = [
            r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
            r'\b(?:list|show)\s+(?:the\s+)?files?\b',
        ]
        if any(re.search(p, msg_lower) for p in _browsing_cmd_patterns):
            return user_message, {}, {"skill": active_skill, "context": "browsing_command"}

        # Check if current message already has an explicit accession
        has_accession = bool(re.findall(r'ENC[A-Z]{2}\d{3}[A-Z]{3}', user_message, re.IGNORECASE))

        # Detect if the message is a NEW search query (not a follow-up on
        # existing data).  We use positive signals for "new query" rather
        # than trying to enumerate every biosample — that doesn't scale.
        #
        # A message is a NEW query if it:
        #   a) Contains explicit new-query patterns, OR
        #   b) Mentions a term that wasn't in the previous results.
        #
        # A message is a FOLLOW-UP if it:
        #   - Asks about data already on screen ("how many of them are RNA-seq?")
        #   - References a DF ("show me DF1")
        #   - Uses referential words with no new subject ("which are methylated?")

        # Positive signals for a NEW independent query
        _new_query_patterns = [
            r'how many\s+\S+.*experiments?',      # "how many X experiments"
            r'search\s+(?:encode\s+)?for\s+\S+',  # "search encode for X"
            r'does\s+encode\s+have\s+\S+',         # "does encode have X"
            r'\S+\s+experiments?\s+(?:in|on|from)\s+encode',  # "X experiments in encode"
            r'(?:find|list|show|get)\s+(?:all\s+)?\S+\s+experiments?',  # "list X experiments"
        ]
        _is_new_query = any(re.search(p, msg_lower) for p in _new_query_patterns)

        # Referential language is a strong signal for follow-up —
        # the user is talking about data already on screen.
        _followup_signals = [
            r'\bof\s+them\b',          # "how many of them"
            r'\bthose\b',               # "show those"
            r'\bthese\b',               # "filter these"
            r'\bthe\s+results?\b',     # "the results"
            r'\bthe\s+data\b',         # "the data"
            r'\bfrom\s+(?:the\s+)?(?:previous|last|above)\b',
            r'\bDF\s*\d+\b',          # explicit DF reference
            r'\bamong\s+them\b',       # "among them"
            r'\bof\s+those\b',         # "of those"
        ]
        _is_followup = any(re.search(p, msg_lower) for p in _followup_signals)
        if _is_followup:
            _is_new_query = False  # override — referential language wins

        # Also check: does the message mention a term that is NOT present in
        # any previous dataframe labels?  If so, it's a new search subject.
        if not _is_new_query and not has_accession and not _is_followup:
            _extracted_term = _extract_encode_search_term(user_message)
            if _extracted_term:
                _prev_labels_lower = set()
                if history_blocks:
                    for _hblk in reversed(history_blocks[-4:]):
                        if _hblk.type == "AGENT_PLAN":
                            _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                            for _dn in _hblk_dfs:
                                _prev_labels_lower.add(_dn.lower())
                # If the extracted term doesn't appear in any previous DF label,
                # it's a new subject
                _term_lower = _extracted_term.lower()
                if _prev_labels_lower and not any(
                    _term_lower in lbl for lbl in _prev_labels_lower
                ):
                    _is_new_query = True
                    logger.info(
                        "Detected new ENCODE search subject (not in prev DFs)",
                        term=_extracted_term, prev_labels=_prev_labels_lower,
                    )

        if not has_accession and not _is_new_query:
            # No new subject in message — this is a follow-up question.
            # PREFERRED: inject dataframe rows from the most recent AGENT_PLAN
            # block that has _dataframes — this gives the LLM the full, accurate
            # tabular data rather than the potentially truncated <details> text.
            # Detect assay filter in the current message so we can
            # pre-filter dataframe rows server-side (reliable) instead of
            # asking the LLM to filter a large table (unreliable).
            _msg_lower_enc = user_message.lower()
            _assay_filter: str | None = None
            for _alias, _canonical in _ENCODE_ASSAY_ALIASES.items():
                if re.search(r'\b' + re.escape(_alias) + r'\b', _msg_lower_enc):
                    _assay_filter = _canonical
                    break

            # Detect output_type filter (for file follow-ups like
            # "which are methylated reads?", "show me unfiltered alignments")
            _output_type_filter: str | None = None
            _output_type_aliases: dict[str, str] = {
                "methylated reads": "methylated reads",
                "unfiltered alignments": "unfiltered alignments",
                "filtered alignments": "filtered alignments",
                "alignments": "alignments",
                "signal p-value": "signal p-value",
                "fold change over control": "fold change over control",
                "peaks": "peaks",
                "conservative idr thresholded peaks": "conservative IDR thresholded peaks",
                "optimal idr thresholded peaks": "optimal IDR thresholded peaks",
                "transcriptome alignments": "transcriptome alignments",
                "gene quantifications": "gene quantifications",
                "transcript quantifications": "transcript quantifications",
                "reads": "reads",
            }
            if not _assay_filter:  # only look for output_type if no assay matched
                for _ot_alias, _ot_canonical in _output_type_aliases.items():
                    if re.search(r'\b' + re.escape(_ot_alias) + r'\b', _msg_lower_enc):
                        _output_type_filter = _ot_canonical
                        break

            # Detect file-type context from CURRENT message only.
            # e.g. "show me the bed files" → inject bed df only.
            # For follow-ups like "which of them are methylated reads?"
            # (no file type in message), the last-visible-DF logic below
            # selects the right dataframe automatically.
            _known_file_types = {"bam", "fastq", "fastq.gz", "bed", "bigwig", "bigbed",
                                 "tsv", "csv", "gtf", "txt", "hic"}
            _file_type_filter: str | None = None
            for _ft in _known_file_types:
                if re.search(r'\b' + re.escape(_ft) + r'\b', _msg_lower_enc):
                    _file_type_filter = _ft
                    break

            # Check for explicit DF reference (e.g. "DF3", "df 3").
            # If found, use that specific dataframe instead of guessing.
            _target_df_id: int | None = None
            _df_ref_match = re.search(r'\bDF\s*(\d+)\b', user_message, re.IGNORECASE)
            if _df_ref_match:
                _target_df_id = int(_df_ref_match.group(1))

            # If no explicit DF reference and no file-type keyword in the
            # CURRENT message, default to the most recent *visible* DF.
            # "them" / "those" / "which of them" refers to the last table
            # the user actually saw.
            if _target_df_id is None and not _file_type_filter and history_blocks:
                _best_visible_id: int | None = None
                for _hblk in reversed(history_blocks):
                    if _hblk.type != "AGENT_PLAN":
                        continue
                    _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                    for _dfd in _hblk_dfs.values():
                        _m = _dfd.get("metadata", {})
                        _did = _m.get("df_id")
                        if _did is not None and _m.get("visible", False):
                            if _best_visible_id is None or _did > _best_visible_id:
                                _best_visible_id = _did
                    if _best_visible_id is not None:
                        break  # only check the most recent block with dfs
                if _best_visible_id is not None:
                    _target_df_id = _best_visible_id
                    logger.info(
                        "Auto-selected last visible DF for follow-up",
                        df_id=_target_df_id,
                    )
                else:
                    logger.info(
                        "No visible DF found in history for auto-selection",
                        blocks_checked=sum(1 for b in history_blocks if b.type == "AGENT_PLAN"),
                    )

            logger.info(
                "_inject_job_context ENCODE follow-up",
                target_df_id=_target_df_id,
                file_type_filter=_file_type_filter,
                assay_filter=_assay_filter,
                output_type_filter=_output_type_filter,
            )

            if history_blocks:
                # Scan ALL history blocks (not just recent ones) for DF data.
                # When user references "DF1" explicitly, that DF may be from
                # much earlier in the conversation.
                # For heuristic (no explicit DF ref), we stop at the first
                # block with matching dataframes (most recent).
                table_sections: list[str] = []
                _injected_dfs: dict = {}
                for blk in reversed(history_blocks):
                    if blk.type != "AGENT_PLAN":
                        continue
                    blk_payload = get_block_payload(blk)
                    dfs = blk_payload.get("_dataframes")
                    if not dfs:
                        continue
                    _found_in_block = False
                    for df_name, df_data in dfs.items():
                        rows = df_data.get("data", [])
                        cols = df_data.get("columns", [])
                        if not rows or not cols:
                            continue

                        # DataFrame selection: by explicit DF reference OR
                        # by file-type filter heuristic.
                        _df_meta = df_data.get("metadata", {})
                        _df_id = _df_meta.get("df_id")

                        if _target_df_id is not None:
                            # User referenced a specific DF — skip all others.
                            if _df_id != _target_df_id:
                                continue
                        else:
                            # Heuristic: file-type context from message/history.
                            _df_file_type = _df_meta.get("file_type", "")
                            if _file_type_filter and _df_file_type:
                                if _df_file_type.lower() != _file_type_filter.lower():
                                    continue
                            elif _file_type_filter and not _df_file_type:
                                if _file_type_filter not in df_name.lower():
                                    continue

                        # Determine filter columns
                        _assay_col = next(
                            (c for c in cols if c.lower() in ("assay", "assay type", "assay_type")),
                            None
                        )
                        _output_type_col = next(
                            (c for c in cols if c.lower() in ("output type", "output_type")),
                            None
                        )

                        filtered_rows = rows
                        filter_desc = ""
                        if _assay_filter and _assay_col:
                            filtered_rows = [
                                r for r in rows
                                if _assay_filter.lower() in str(r.get(_assay_col, "")).lower()
                            ]
                            filter_desc = f" filtered to assay='{_assay_filter}'"
                            if not filtered_rows:
                                if _target_df_id is not None:
                                    # User explicitly referenced this DF — inject
                                    # the full data so the LLM can confirm 0 matches
                                    # rather than falling through to API calls.
                                    filtered_rows = rows
                                    filter_desc = (
                                        f" (0 rows match assay='{_assay_filter}'"
                                        f" — showing all {len(rows)} rows)"
                                    )
                                else:
                                    continue
                        elif _output_type_filter and _output_type_col:
                            filtered_rows = [
                                r for r in rows
                                if _output_type_filter.lower() in str(r.get(_output_type_col, "")).lower()
                            ]
                            filter_desc = f" filtered to output_type='{_output_type_filter}'"
                            if not filtered_rows:
                                # No exact match — inject all rows from this (already
                                # file-type-scoped) dataframe so the LLM can reason
                                # accurately rather than falling through to hallucination.
                                filtered_rows = rows
                                filter_desc = f" (user asked for output_type='{_output_type_filter}', answer from this data)"
                                logger.info(
                                    "output_type filter matched 0 rows; injecting full file-type df",
                                    df=df_name, output_type_filter=_output_type_filter,
                                )

                        # Cap at 500 rows for large unfiltered datasets
                        MAX_DF_ROWS = 500
                        shown = filtered_rows[:MAX_DF_ROWS]
                        capped = len(filtered_rows) > MAX_DF_ROWS
                        header = "| " + " | ".join(cols) + " |"
                        sep = "|" + "|".join(["---"] * len(cols)) + "|"
                        body_lines = [
                            "| " + " | ".join(str(row.get(c, "")) for c in cols) + " |"
                            for row in shown
                        ]
                        suffix = f"\n*({len(filtered_rows)} total rows)*" if capped else ""
                        _df_id_str = f"DF{_df_id}: " if _df_id else ""
                        table_sections.append(
                            f"**{_df_id_str}{df_name}** ({len(filtered_rows)} rows{filter_desc}):\n"
                            + header + "\n" + sep + "\n"
                            + "\n".join(body_lines)
                            + suffix
                        )
                        # Build an injected dataframe for the UI to render.
                        # If we applied a filter, use a descriptive label;
                        # otherwise reuse the original name.
                        if filter_desc:
                            _inj_label = f"{df_name}{filter_desc}"
                        else:
                            _inj_label = df_name
                        _injected_dfs[_inj_label] = {
                            "columns": cols,
                            "data": filtered_rows,
                            "row_count": len(filtered_rows),
                            "metadata": {
                                # Copy source metadata but drop df_id so the
                                # injected (filtered) df gets a fresh sequential ID.
                                **{k: v for k, v in _df_meta.items() if k != "df_id"},
                                "visible": True,
                                "source_df_id": _df_id,  # for progress notification
                            },
                        }
                        _found_in_block = True

                    # For explicit DF ref: keep scanning older blocks (the
                    # target DF may be anywhere in history).
                    # For heuristic (no explicit ref): stop at the first
                    # block that had matching data — it's the most recent.
                    if _found_in_block and _target_df_id is None:
                        break  # heuristic: most recent match is enough
                    if _found_in_block and _target_df_id is not None:
                        break  # explicit ref found — no need to keep scanning

                if table_sections:
                        logger.info(
                            "_inject_job_context: injecting data",
                            table_count=len(table_sections),
                            injected_df_count=len(_injected_dfs),
                            target_df_id=_target_df_id,
                        )
                        context_line = (
                            "[CONTEXT: This is a follow-up question. The answer "
                            "is likely in your previous query data below. "
                            "READ THIS DATA FIRST and answer directly from it. "
                            "Do NOT make a new DATA_CALL — the data is already provided. "
                            "Only make a new DATA_CALL if the data below is completely empty.]\n"
                            "[PREVIOUS QUERY DATA:]\n"
                            + "\n\n".join(table_sections)
                        )
                        _inject_debug = {
                            "source": "encode_df_injection",
                            "target_df_id": _target_df_id,
                            "file_type_filter": _file_type_filter,
                            "assay_filter": _assay_filter,
                            "output_type_filter": _output_type_filter,
                            "injected_df_names": list(_injected_dfs.keys()),
                            "injected_row_counts": {
                                k: v.get("row_count", len(v.get("data", [])))
                                for k, v in _injected_dfs.items()
                            },
                            "table_sections_count": len(table_sections),
                            "augmented_message_preview": context_line[:500],
                        }
                        return f"{context_line}\n\n{user_message}", _injected_dfs, _inject_debug
                else:
                    logger.info(
                        "_inject_job_context: no matching data found in history blocks",
                        target_df_id=_target_df_id,
                        file_type_filter=_file_type_filter,
                    )

            # FALLBACK: extract from <details> text in conversation history
            for hist_msg in reversed(conversation_history[-6:]):
                content = hist_msg.get("content", "")
                if hist_msg.get("role") != "assistant":
                    continue
                details_match = re.search(
                    r'<details>.*?<summary>.*?</summary>\s*(.*?)\s*</details>',
                    content, re.DOTALL
                )
                if details_match:
                    raw_data = details_match.group(1).strip()
                    if len(raw_data) > 6000:
                        raw_data = raw_data[:6000] + "\n... (truncated)"
                    context_line = (
                        "[CONTEXT: This is a follow-up question. The answer "
                        "may be in your previous query data below. Check this "
                        "data FIRST and answer directly if it contains the "
                        "answer. Only make a new DATA_CALL if this data does "
                        "NOT have what the user needs.]\n"
                        "[PREVIOUS QUERY DATA:]\n"
                        f"{raw_data}"
                    )
                    return f"{context_line}\n{user_message}", {}, {"source": "fallback_details"}

    return user_message, {}, {}



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
    return {"status": "ok", "service": "cortex"}

@app.get("/skills")
async def get_available_skills():
    """Return the list of all available skills."""
    return {
        "skills": list(SKILLS_REGISTRY.keys()),
        "count": len(SKILLS_REGISTRY)
    }

@app.get("/jobs/{run_uuid}/status")
async def get_job_status_proxy(run_uuid: str, request: Request):
    """
    Proxy endpoint for Launchpad job status via direct REST call.
    Bypasses MCP for reliability — the UI calls this for live job progress.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)

    from cortex.config import INTERNAL_API_SECRET
    launchpad_rest = SERVICE_REGISTRY.get("launchpad", {}).get(
        "rest_url", os.getenv("LAUNCHPAD_REST_URL", "http://localhost:8003")
    )
    headers = {}
    if INTERNAL_API_SECRET:
        headers["X-Internal-Secret"] = INTERNAL_API_SECRET

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{launchpad_rest}/jobs/{run_uuid}/status",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Failed to proxy job status from Launchpad",
                       run_uuid=run_uuid, error=str(e))
        raise HTTPException(status_code=502, detail=f"Launchpad unreachable: {e}")


@app.get("/jobs/{run_uuid}/debug")
async def get_job_debug_info(run_uuid: str, request: Request):
    """
    Proxy endpoint for Launchpad job debug info via MCP.
    This allows the UI to get debug info without calling Launchpad directly.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)
    return await _call_launchpad_tool("get_job_debug", run_uuid=run_uuid)

@app.post("/block", response_model=BlockOut)
async def create_block(block_in: BlockCreate, request: Request):
    # Get current user from middleware
    user = request.state.user
    require_project_access(block_in.project_id, user, min_role="editor")
    
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
async def get_blocks(project_id: str, request: Request, since_seq: int = 0, limit: int = 100):
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")
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
    request: Request,
    block_id: str = FastAPIPath(..., min_length=1),
    body: BlockUpdate = ...
):
    user = request.state.user
    session = SessionLocal()
    try:
        result = session.execute(select(ProjectBlock).where(ProjectBlock.id == block_id))
        block = result.scalar_one_or_none()
        if not block:
            raise HTTPException(status_code=404, detail="Block not found")

        # Verify user has editor access to this block's project
        require_project_access(block.project_id, user, min_role="editor")
            
        old_status = block.status
        
        if body.status is not None:
            block.status = body.status
        if body.payload is not None:
            block.payload_json = json.dumps(body.payload)
            
        session.commit()
        session.refresh(block)
        
        # If an APPROVAL_GATE was just approved, trigger job submission or download
        if block.type == "APPROVAL_GATE" and old_status == "PENDING" and block.status == "APPROVED":
            gate_payload = get_block_payload(block)
            if gate_payload.get("gate_action") == "download":
                asyncio.create_task(download_after_approval(block.project_id, block_id))
            else:
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
    require_project_access(project_id, user, min_role="owner")
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
    Extract job parameters from conversation history using heuristics.
    
    Scopes to the **most recent submission cycle** — only blocks after the
    last EXECUTION_JOB or APPROVAL_GATE (approved).  This prevents stale
    parameters from earlier jobs (e.g. a previous sample name) bleeding
    into a new submission.
    
    Analyzes USER_MESSAGE and AGENT_PLAN blocks to determine:
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
    
    # --- Scope to the most recent submission cycle ---
    # Find the last EXECUTION_JOB or approved APPROVAL_GATE block.
    # Only consider blocks AFTER that point for parameter extraction.
    last_boundary_idx = -1
    for i, block in enumerate(blocks):
        if block.type == "EXECUTION_JOB":
            last_boundary_idx = i
        elif block.type == "APPROVAL_GATE" and block.status == "APPROVED":
            last_boundary_idx = i
    
    recent_blocks = blocks[last_boundary_idx + 1:] if last_boundary_idx >= 0 else blocks
    
    # Build conversation context from recent blocks only
    conversation = []
    user_messages = []
    for block in recent_blocks:
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
        "max_gpu_tasks": None,  # Will use default 1 if not specified
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
    # Search user messages in REVERSE order (most recent first) so a new
    # submission request wins over older ones in the same cycle.
    explicit_patterns = [
        r'([a-zA-Z0-9_-]+)\s+is\s+(?:the\s+)?sample\s+name',  # "Jamshid is sample name"
        r'sample\s+name\s+is\s+([a-zA-Z0-9_-]+)',  # "sample name is Jamshid"
        r'named\s+([a-zA-Z0-9_-]+)',  # "named Ali1"
        r'called\s+([a-zA-Z0-9_-]+)',  # "called Ali1"
    ]
    _skip_words = {'is', 'the', 'a', 'an', 'this', 'that', 'it', 'at', 'in', 'on',
                   'mm39', 'grch38', 'hg38', 'mm10'}
    for msg in reversed(user_messages):
        for pattern in explicit_patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                if candidate.lower() not in _skip_words:
                    params["sample_name"] = candidate
                    break
        if params["sample_name"]:
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
    
    # max_gpu_tasks (handle "max gpu tasks 2", "limit dorado to 3", "run 2 gpu tasks at a time")
    gpu_task_patterns = [
        r'max[_\s]*gpu[_\s]*tasks?[:\s]+(?:of\s+)?(\d+)',
        r'limit\s+(?:dorado|gpu)\s+(?:tasks?\s+)?to\s+(\d+)',
        r'(\d+)\s+(?:concurrent|simultaneous|parallel)\s+(?:dorado|gpu)\s+tasks?',
        r'(?:run|allow)\s+(\d+)\s+(?:dorado|gpu)\s+tasks?',
    ]
    for gp in gpu_task_patterns:
        gpu_match = re.search(gp, all_user_text)
        if gpu_match:
            params["max_gpu_tasks"] = int(gpu_match.group(1))
            break
    
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


async def download_after_approval(project_id: str, gate_block_id: str):
    """Background task to start downloading files after approval.

    If the gate extracted_params contains a 'files' list (set by the download
    chain), use those directly.  Otherwise falls back to scanning conversation
    blocks for HTTP/HTTPS URLs.
    """
    session = SessionLocal()
    try:
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if not gate_block:
            logger.error("Gate block not found for download", gate_block_id=gate_block_id)
            return

        owner_id = gate_block.owner_id
        gate_payload = get_block_payload(gate_block)
        gate_params = gate_payload.get("extracted_params", {})

        # --- Preferred path: files list from download chain ---
        urls: list[dict] = []
        _target_dir_str: str | None = gate_params.get("target_dir")

        if gate_params.get("files"):
            for f in gate_params["files"]:
                urls.append({
                    "url": f["url"],
                    "filename": f.get("filename", f["url"].rsplit("/", 1)[-1].split("?")[0] or "file"),
                    "size_bytes": f.get("size_bytes"),
                })

        # --- Fallback: scan conversation blocks for URLs ---
        if not urls:
            query = select(ProjectBlock)\
                .where(ProjectBlock.project_id == project_id)\
                .order_by(ProjectBlock.seq.asc())
            blocks = session.execute(query).scalars().all()

            url_pattern = re.compile(r'https?://[^\s\)\"\'>\]]+')

            for blk in blocks:
                if blk.type not in ("AGENT_PLAN", "USER_MESSAGE"):
                    continue
                payload = get_block_payload(blk)
                text = payload.get("markdown", "") or payload.get("text", "")
                for match in url_pattern.finditer(text):
                    url = match.group(0).rstrip(".,;:")
                    # Skip obvious non-file URLs (API endpoints, portal pages)
                    if any(skip in url for skip in ["/api/", "/search/", "portal.encode"]):
                        continue
                    filename = url.rsplit("/", 1)[-1].split("?")[0] or "file"
                    # Avoid duplicates
                    if not any(u["url"] == url for u in urls):
                        urls.append({"url": url, "filename": filename})

        if not urls:
            _create_block_internal(
                session, project_id, "AGENT_PLAN",
                {
                    "markdown": "⚠️ No download URLs found in the conversation. "
                                "Please provide URLs or ENCODE file accessions and try again.",
                    "skill": "download_files",
                    "model": "system",
                },
                status="DONE",
                owner_id=owner_id,
            )
            return

        # Resolve target directory from gate params or project directory
        if _target_dir_str:
            target_dir = Path(_target_dir_str)
        else:
            owner_user = session.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
            project_obj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()

            if owner_user and owner_user.username and project_obj and project_obj.slug:
                project_dir = Path(AGOUTIC_DATA) / "users" / owner_user.username / project_obj.slug
            else:
                project_dir = Path(AGOUTIC_DATA) / "users" / owner_id / project_id

            target_dir = project_dir / "data"
        target_dir.mkdir(parents=True, exist_ok=True)

        download_id = str(uuid.uuid4())

        block = _create_block_internal(
            session, project_id, "DOWNLOAD_TASK",
            {
                "download_id": download_id,
                "source": "conversation",
                "files": [{"filename": u["filename"], "url": u["url"]} for u in urls],
                "target_dir": str(target_dir),
                "downloaded": 0,
                "total_files": len(urls),
                "bytes_downloaded": 0,
                "status": "RUNNING",
            },
            status="RUNNING",
            owner_id=owner_id,
        )

        _active_downloads[download_id] = {
            "block_id": block.id,
            "project_id": project_id,
            "cancelled": False,
        }

        asyncio.create_task(
            _download_files_background(
                download_id=download_id,
                project_id=project_id,
                block_id=block.id,
                owner_id=owner_id,
                files=urls,
                target_dir=target_dir,
            )
        )

    except Exception as e:
        logger.error("download_after_approval failed", error=str(e), exc_info=True)
    finally:
        session.close()


async def submit_job_after_approval(project_id: str, gate_block_id: str):
    """
    Background task to submit a job to Launchpad after approval.
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
        
        # Normalize reference_genome to list for Launchpad (Nextflow handles multi-genome in parallel)
        ref_genome = job_params.get("reference_genome", ["mm39"])
        if isinstance(ref_genome, str):
            ref_genome = [ref_genome]

        # Look up username and project_slug for human-readable work directories
        owner_user = session.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
        project_obj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        _username = owner_user.username if owner_user else None
        _project_slug = project_obj.slug if project_obj else None
        
        job_data = {
            "project_id": project_id,
            "user_id": owner_id,  # Pass owner for jailed file paths and job ownership
            "username": _username,  # Human-readable dir name (may be None for legacy users)
            "project_slug": _project_slug,  # Human-readable dir name (may be None for legacy projects)
            "sample_name": job_params.get("sample_name", f"sample_{project_id.split('_')[-1]}"),
            "mode": job_params.get("mode", "DNA"),
            "input_directory": job_params.get("input_directory", "/data/samples/test"),
            "reference_genome": ref_genome,  # List — Nextflow parallelizes across genomes
            "modifications": job_params.get("modifications"),
            "input_type": job_params.get("input_type", "pod5"),
            "entry_point": job_params.get("entry_point"),
            # Advanced parameters - use Launchpad defaults if None
            "modkit_filter_threshold": job_params.get("modkit_filter_threshold") or 0.9,
            "min_cov": job_params.get("min_cov"),  # Let Launchpad handle None (mode-dependent default)
            "per_mod": job_params.get("per_mod") or 5,
            "accuracy": job_params.get("accuracy") or "sup",
            "max_gpu_tasks": job_params.get("max_gpu_tasks") or 1,
        }
        
        logger.info("Job parameters prepared", source="edited" if gate_payload.get('edited_params') else "extracted",
                    job_data=job_data)
        
        # Submit job to Launchpad via MCP (single call — Nextflow handles multi-genome)
        try:
            launchpad_url = get_service_url("launchpad")
            client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
            await client.connect()
            try:
                result = await client.call_tool("submit_dogme_job", **job_data)
            finally:
                await client.disconnect()
        except Exception as e:
            raise Exception(f"MCP call to Launchpad failed: {e}")
        
        run_uuid = result.get("run_uuid") if isinstance(result, dict) else None
        _work_directory = result.get("work_directory", "") if isinstance(result, dict) else ""
        
        if run_uuid:
            
            # Create EXECUTION_JOB block
            # Include model_name so _auto_trigger_analysis can call the same LLM
            _gate_model = gate_payload.get("model", "default")
            job_block = _create_block_internal(
                session,
                project_id,
                "EXECUTION_JOB",
                {
                    "run_uuid": run_uuid,
                    "work_directory": _work_directory,
                    "sample_name": job_data["sample_name"],
                    "mode": job_data["mode"],
                    "model": _gate_model,
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
                    "error": f"No run_uuid in Launchpad response: {error_detail}",
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
                "message": f"Failed to submit job to Launchpad: {error_msg}",
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
    Background task to poll Launchpad for job status via MCP and update the EXECUTION_JOB block.
    Continues until job is completed or failed.
    """
    
    poll_interval = 3  # seconds
    max_polls = 600  # 30 minutes max
    
    for poll_num in range(max_polls):
        await asyncio.sleep(poll_interval)
        
        session = SessionLocal()
        try:
            # Get current status from Launchpad via MCP
            launchpad_url = get_service_url("launchpad")
            client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
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
                payload["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
                
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
    Automatically analyse a just-completed Dogme job.

    1. Fetches the analysis summary (file listing) from Analyzer.
    2. Parses key CSV result files (final_stats, qc_summary) via Analyzer MCP.
    3. Passes everything to the LLM for an intelligent first interpretation.
    4. Saves the LLM response as an AGENT_PLAN block with token tracking.

    Falls back to a static template if the LLM call fails.
    """
    sample_name = job_payload.get("sample_name", "Unknown")
    mode = job_payload.get("mode", "DNA")
    model_key = job_payload.get("model", "default")
    work_directory = job_payload.get("work_directory", "")

    logger.info("Auto-triggering analysis", run_uuid=run_uuid,
                sample_name=sample_name, mode=mode, model=model_key)

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

        # Also save to conversation history so the LLM sees it
        if owner_id:
            await save_conversation_message(
                session, project_id, owner_id, "user",
                f"Job \"{sample_name}\" completed. Analyze the results."
            )

        # 2. Fetch analysis summary + key CSV data from Analyzer
        summary_data = {}  # structured summary from get_analysis_summary
        parsed_csvs = {}   # filename → parsed rows from key CSVs
        try:
            analyzer_url = get_service_url("analyzer")
            client = MCPHttpClient(name="analyzer", base_url=analyzer_url)
            await client.connect()
            try:
                summary_data = await client.call_tool(
                    "get_analysis_summary", run_uuid=run_uuid,
                    work_dir=work_directory or None,
                )
                if not isinstance(summary_data, dict):
                    summary_data = {}

                # Parse key CSV files for the LLM
                # Prioritise small, high-value files: final_stats, qc_summary
                csv_files = (
                    summary_data
                    .get("file_summary", {})
                    .get("csv_files", [])
                )
                _KEY_PATTERNS = ("final_stats", "qc_summary")
                for finfo in csv_files:
                    fname = finfo.get("name", "")
                    fsize = finfo.get("size", 0)
                    if fsize > 500_000:  # skip files > 500 KB
                        continue
                    if any(pat in fname.lower() for pat in _KEY_PATTERNS):
                        try:
                            _csv_params: dict = {
                                "file_path": fname,
                                "max_rows": 50,
                            }
                            if work_directory:
                                _csv_params["work_dir"] = work_directory
                            else:
                                _csv_params["run_uuid"] = run_uuid
                            parse_result = await client.call_tool(
                                "parse_csv_file",
                                **_csv_params,
                            )
                            if isinstance(parse_result, dict) and parse_result.get("data"):
                                parsed_csvs[fname] = parse_result
                        except Exception as csv_err:
                            logger.debug("Failed to parse CSV for auto-analysis",
                                         filename=fname, error=str(csv_err))
            finally:
                await client.disconnect()
        except Exception as e:
            logger.warning("Failed to fetch analysis summary", run_uuid=run_uuid, error=str(e))

        # 3. Map mode → Dogme analysis skill
        mode_skill_map = {
            "DNA": "run_dogme_dna",
            "RNA": "run_dogme_rna",
            "CDNA": "run_dogme_cdna",
        }
        analysis_skill = mode_skill_map.get(mode.upper(), "analyze_job_results")

        # 4. Build data context string for the LLM
        data_context = _build_auto_analysis_context(
            sample_name, mode, run_uuid, summary_data, parsed_csvs
        )

        # 5. Call the LLM for an intelligent interpretation
        llm_md = ""
        llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        engine = None
        try:
            engine = AgentEngine(model_key=model_key)
            user_prompt = (
                f"A Dogme {mode} job for sample \"{sample_name}\" just completed.\n"
                f"Work directory: {work_directory}\n\n"
                f"Here is the analysis summary and key result data:\n\n"
                f"{data_context}\n\n"
                f"Provide a concise interpretation of these results. "
                f"Highlight key metrics, any QC concerns, and suggest "
                f"next steps the user could explore."
            )
            llm_md, llm_usage = await run_in_threadpool(
                engine.think,
                user_prompt,
                analysis_skill,
                None,  # no conversation history needed — data is self-contained
            )
            # Strip any tags the LLM might emit (it shouldn't, but be safe)
            llm_md = re.sub(r'\[\[DATA_CALL:.*?\]\]', '', llm_md)
            llm_md = re.sub(r'\[\[SKILL_SWITCH_TO:.*?\]\]', '', llm_md)
            llm_md = re.sub(r'\[\[APPROVAL_NEEDED\]\]', '', llm_md)
            llm_md = llm_md.strip()
            logger.info("Auto-analysis LLM call succeeded",
                        run_uuid=run_uuid, tokens=llm_usage.get("total_tokens", 0))
        except Exception as llm_err:
            logger.warning("Auto-analysis LLM call failed, using static template",
                           run_uuid=run_uuid, error=str(llm_err))
            llm_md = ""  # fall through to static template below

        # 6. Build the final markdown
        if llm_md:
            # LLM succeeded — prepend a header and append exploration hints
            _wf_name = work_directory.rstrip('/').rsplit('/', 1)[-1] if work_directory else ''
            final_md = (
                f"### 📊 Analysis: {sample_name}\n"
                f"**Workflow:** {_wf_name} &nbsp;|&nbsp; "
                f"**Mode:** {mode} &nbsp;|&nbsp; "
                f"**Status:** COMPLETED\n\n"
                f"{llm_md}\n\n"
                f"💡 *You can ask me to dive deeper — for example:*\n"
                f"- \"Show me the modification summary\"\n"
                f"- \"Parse the CSV results\"\n"
                f"- \"Give me a QC report\"\n"
            )
            _model_name = engine.model_name if engine else "system"
        else:
            # Fallback: static template (same as before)
            final_md = _build_static_analysis_summary(
                sample_name, mode, run_uuid, summary_data,
                work_directory=work_directory,
            )
            _model_name = "system"
            llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # 7. Create AGENT_PLAN block with the analysis
        _token_payload = {
            **llm_usage,
            "model": _model_name,
        }
        _create_block_internal(
            session,
            project_id,
            "AGENT_PLAN",
            {
                "markdown": final_md,
                "skill": analysis_skill,
                "model": _model_name,
                "tokens": _token_payload,
            },
            status="DONE",
            owner_id=owner_id,
        )

        # Save assistant response to conversation history with token tracking
        if owner_id:
            await save_conversation_message(
                session, project_id, owner_id, "assistant", final_md,
                token_data=_token_payload, model_name=_model_name
            )

        logger.info("Auto-analysis block created", run_uuid=run_uuid,
                    skill=analysis_skill, model=_model_name,
                    tokens=llm_usage.get("total_tokens", 0))

    except Exception as e:
        logger.error("Auto-trigger analysis failed", run_uuid=run_uuid, error=str(e))
    finally:
        session.close()


def _build_auto_analysis_context(
    sample_name: str, mode: str, run_uuid: str,
    summary_data: dict, parsed_csvs: dict,
) -> str:
    """
    Build a structured text context from the Analyzer summary and parsed CSVs
    for the LLM to interpret.
    """
    parts = []

    # File inventory
    if summary_data:
        all_counts = summary_data.get("all_file_counts", {})
        file_summary = summary_data.get("file_summary", {})
        total_files = all_counts.get("total_files", 0)
        csv_count = all_counts.get("csv_count", len(file_summary.get("csv_files", [])))
        bed_count = all_counts.get("bed_count", len(file_summary.get("bed_files", [])))
        txt_count = all_counts.get("txt_count", len(file_summary.get("txt_files", [])))
        parts.append(
            f"## File Inventory\n"
            f"Total files: {total_files} (CSV/TSV: {csv_count}, BED: {bed_count}, "
            f"Text/other: {txt_count})"
        )

        csv_files = file_summary.get("csv_files", [])
        if csv_files:
            parts.append("**Available CSV files:**")
            for f in csv_files[:12]:
                size_kb = f.get("size", 0) / 1024
                parts.append(f"- {f['name']} ({size_kb:.1f} KB)")
            if len(csv_files) > 12:
                parts.append(f"- …and {len(csv_files) - 12} more")

    # Parsed CSV data
    for fname, parse_result in parsed_csvs.items():
        rows = parse_result.get("data", [])
        columns = parse_result.get("columns", [])
        total_rows = parse_result.get("total_rows", len(rows))
        if not rows:
            continue
        parts.append(f"\n## Data: {fname} ({total_rows} rows)")
        if columns:
            parts.append("| " + " | ".join(str(c) for c in columns) + " |")
            parts.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in rows[:30]:  # cap at 30 rows for token economy
            if isinstance(row, dict):
                vals = [str(row.get(c, "")) for c in columns] if columns else [str(v) for v in row.values()]
            elif isinstance(row, (list, tuple)):
                vals = [str(v) for v in row]
            else:
                vals = [str(row)]
            parts.append("| " + " | ".join(vals) + " |")
        if total_rows > 30:
            parts.append(f"\n_(showing 30 of {total_rows} rows)_")

    return "\n".join(parts) if parts else "No analysis data available."


def _build_static_analysis_summary(
    sample_name: str, mode: str, run_uuid: str, summary_data: dict,
    work_directory: str = "",
) -> str:
    """
    Build a static markdown summary (fallback when the LLM call fails).
    This is the original template that was used before the LLM pass was added.
    """
    if summary_data:
        all_counts = summary_data.get("all_file_counts", {})
        file_summary = summary_data.get("file_summary", {})
        total_files = all_counts.get("total_files", 0)
        csv_count = all_counts.get("csv_count", len(file_summary.get("csv_files", [])))
        bed_count = all_counts.get("bed_count", len(file_summary.get("bed_files", [])))
        txt_count = all_counts.get("txt_count", len(file_summary.get("txt_files", [])))

        _wf_name = work_directory.rstrip("/").rsplit("/", 1)[-1] if work_directory else ""
        md = (
            f"### 📊 Analysis Ready: {sample_name}\n\n"
            f"**Workflow:** {_wf_name}\n"
            f"**Mode:** {summary_data.get('mode', mode)} &nbsp;|&nbsp; "
            f"**Status:** {summary_data.get('status', 'COMPLETED')} &nbsp;|&nbsp; "
            f"**Total files:** {total_files}\n\n"
            f"| Category | Count |\n"
            f"|----------|-------|\n"
            f"| CSV / TSV | {csv_count} |\n"
            f"| BED | {bed_count} |\n"
            f"| Text / other | {txt_count} |\n\n"
        )

        csv_files = file_summary.get("csv_files", [])
        if csv_files:
            md += "**Key result files:**\n"
            for f in csv_files[:8]:
                size_kb = f.get("size", 0) / 1024
                md += f"- `{f['name']}` ({size_kb:.1f} KB)\n"
            if len(csv_files) > 8:
                md += f"- _…and {len(csv_files) - 8} more_\n"
            md += "\n"
    else:
        md = (
            f"### 📊 Job Completed: {sample_name}\n\n"
            f"The {mode} job finished successfully. "
            f"I couldn't fetch the file summary automatically, but you can ask me to "
            f"analyze the results.\n\n"
        )

    md += (
        "💡 *You can ask me to dive deeper — for example:*\n"
        "- \"Show me the modification summary\"\n"
        "- \"Parse the CSV results\"\n"
        "- \"Give me a QC report\"\n"
    )
    return md


# --- CHAT PROGRESS STATUS ---
@app.get("/chat/status/{request_id}")
async def chat_status(request_id: str):
    """Return the current processing stage for a chat request (used by UI polling)."""
    entry = _chat_progress.get(request_id)
    if entry:
        return {"stage": entry["stage"], "detail": entry["detail"]}
    return {"stage": "waiting", "detail": ""}


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

    # Auto-register the project if it doesn't exist yet (handles UI fallback UUIDs
    # created when the POST /projects call timed out or failed).
    _ensure_session = SessionLocal()
    try:
        _proj = _ensure_session.execute(
            select(Project).where(Project.id == req.project_id)
        ).scalar_one_or_none()
        if not _proj:
            from cortex.user_jail import get_user_project_dir
            try:
                get_user_project_dir(user.id, req.project_id)
            except PermissionError:
                pass  # directory creation failed; DB rows still useful
            now = datetime.datetime.utcnow()
            _proj = Project(
                id=req.project_id,
                name=f"Project {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
                owner_id=user.id,
                is_public=False,
                is_archived=False,
                created_at=now,
                updated_at=now,
            )
            _ensure_session.add(_proj)
            _acc = ProjectAccess(
                id=str(uuid.uuid4()),
                user_id=user.id,
                project_id=req.project_id,
                project_name=_proj.name,
                role="owner",
                last_accessed=now,
            )
            _ensure_session.add(_acc)
            _ensure_session.commit()
            logger.info("Auto-registered project from chat", project_id=req.project_id, user=user.email)
    except Exception:
        _ensure_session.rollback()
    finally:
        _ensure_session.close()

    require_project_access(req.project_id, user, min_role="editor")

    # --- TOKEN LIMIT CHECK ---
    # Admins are exempt. For regular users, compare lifetime total against their
    # limit (if set) before spending any compute on the LLM call.
    if user.role != "admin" and user.token_limit is not None:
        _limit_session = SessionLocal()
        try:
            _used_row = _limit_session.execute(
                text("""
                    SELECT COALESCE(SUM(cm.total_tokens), 0)
                    FROM conversation_messages cm
                    JOIN conversations c ON cm.conversation_id = c.id
                    WHERE c.user_id = :uid
                      AND cm.role = 'assistant'
                      AND cm.total_tokens IS NOT NULL
                """),
                {"uid": user.id}
            ).fetchone()
            _tokens_used = _used_row[0] if _used_row else 0
        finally:
            _limit_session.close()

        if _tokens_used >= user.token_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "token_limit_exceeded",
                    "message": f"You have used {_tokens_used:,} of your {user.token_limit:,} token limit. "
                               "Please contact an admin to increase your quota.",
                    "tokens_used": _tokens_used,
                    "token_limit": user.token_limit,
                }
            )

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
            .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))\
            .order_by(ProjectBlock.seq.asc())
        
        history_result = session.execute(history_query)
        history_blocks = history_result.scalars().all()
        
        # Build conversation history in OpenAI format.
        # IMPORTANT: Strip <details>…</details> blocks from assistant messages.
        # These contain raw tool output (e.g. all 69 file rows) shown to the
        # user via a collapsible widget.  If we leave them in conversation
        # history, the LLM sees the FULL unfiltered data from a previous turn
        # and ignores the server-side filtered [PREVIOUS QUERY DATA:] injected
        # by _inject_job_context — causing wrong follow-up answers.
        _DETAILS_RE = re.compile(
            r'\s*---\s*\n\s*<details>.*?</details>', re.DOTALL
        )
        conversation_history = []
        for block in history_blocks[:-1]:  # Exclude the message we just added
            block_payload = get_block_payload(block)
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block_payload.get("text", "")
                })
            elif block.type == "AGENT_PLAN":
                _md = block_payload.get("markdown", "")
                # Remove raw data <details> blocks — the filtered data
                # will be injected by _inject_job_context when needed.
                _md = _DETAILS_RE.sub("", _md)
                # Fix A: skip bare find_file JSON echo blocks — if the entire
                # assistant turn is just a find_file result JSON (LLM echoed it
                # instead of acting on it), exclude it from history so the LLM
                # doesn't treat it as a "completed" action and loop.
                _md_stripped = re.sub(r'```(?:json)?|```', '', _md).strip()
                if '"primary_path"' in _md_stripped and ('"success": true' in _md_stripped or '"success":true' in _md_stripped):
                    try:
                        _probe = json.loads(_md_stripped)
                        if _probe.get("success") and _probe.get("primary_path"):
                            logger.debug("Skipping bare find_file echo block from history",
                                         primary_path=_probe["primary_path"])
                            continue  # don't add to conversation_history
                    except (json.JSONDecodeError, ValueError):
                        pass  # not clean JSON — keep it
                conversation_history.append({
                    "role": "assistant",
                    "content": _md
                })
        
        # 2. Run the Brain (in a thread so it doesn't block the server)
        # Initialize engine with the selected model (from UI request)
        engine = AgentEngine(model_key=req.model)
        _emit_progress(req.request_id, "thinking", f"Thinking using {engine.model_name}...")

        # Resolve the project directory path for file-browsing commands.
        # This is the minimum context needed for list_job_files, list_workflows, etc.
        _project_dir_path = _resolve_project_dir(session, user, req.project_id)
        _project_dir_str = str(_project_dir_path) if _project_dir_path else ""

        # 2a. Pre-LLM skill switch detection — catch obvious mismatches
        # before wasting time on an LLM call with the wrong skill.
        auto_skill = _auto_detect_skill_switch(req.message, active_skill)
        _pre_llm_skill = active_skill  # Track for debug
        if auto_skill and auto_skill in SKILLS_REGISTRY:
            logger.info("Auto-detected skill switch before LLM call",
                       from_skill=active_skill, to_skill=auto_skill)
            _emit_progress(req.request_id, "switching",
                          f"Switching to {auto_skill} skill...")
            active_skill = auto_skill

        # Inject job context (UUID, work_dir) for Dogme skills so the LLM
        # doesn't waste time searching conversation history for known info.
        # For ENCODE skills, inject full dataframe rows from the previous block.
        augmented_message, _injected_dfs, _inject_debug = _inject_job_context(
            req.message, active_skill, conversation_history,
            history_blocks=history_blocks
        )

        # Build structured conversation state and prepend as JSON
        _conv_state = _build_conversation_state(
            active_skill, conversation_history,
            history_blocks=history_blocks,
            project_id=req.project_id,
        )
        _state_json = _conv_state.to_json()
        if _state_json and _state_json != "{}":
            augmented_message = f"[STATE]\n{_state_json}\n[/STATE]\n\n{augmented_message}"

        # Notify user when answering from a previously fetched dataframe
        if _injected_dfs:
            # Show the SOURCE DF IDs (from original dataframes), not the
            # injected copy IDs (which don't have IDs yet).
            _df_ref_match_notify = re.search(r'\bDF\s*(\d+)\b', req.message, re.IGNORECASE)
            if _df_ref_match_notify:
                _notify_ids = [f"DF{_df_ref_match_notify.group(1)}"]
            else:
                # Try to find source df_id from the injected metadata's
                # original context (we stored source_df_id for this purpose)
                _notify_ids = []
                for _d in _injected_dfs.values():
                    _src_id = _d.get("metadata", {}).get("source_df_id")
                    if _src_id:
                        _notify_ids.append(f"DF{_src_id}")
                if not _notify_ids:
                    _notify_ids = list(_injected_dfs.keys())[:2]
            _emit_progress(
                req.request_id, "context",
                f"Answering from previous data ({', '.join(_notify_ids)})..."
            )
        
        # 'think' talks to Ollama using the active skill and conversation history
        # Returns (content, usage_dict) — capture tokens for this turn
        _think_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        raw_response, _think_usage = await run_in_threadpool(
            engine.think, 
            augmented_message, 
            active_skill,
            conversation_history
        )

        # 2c. Validate LLM output against output contracts
        raw_response, _output_violations = _validate_llm_output(
            raw_response, active_skill, history_blocks=history_blocks
        )
        _pre_switch_skill = active_skill  # Track for debug
        
        # 3. Parse for Skill Switch Tag
        skill_switch_match = re.search(r'\[\[SKILL_SWITCH_TO:\s*(\w+)\]\]', raw_response)
        _skill_switch_debug = {
            "tag_found": bool(skill_switch_match),
            "pre_switch_skill": _pre_switch_skill,
        }
        if skill_switch_match:
            new_skill = skill_switch_match.group(1)
            _skill_switch_debug["requested_skill"] = new_skill
            _skill_switch_debug["in_registry"] = new_skill in SKILLS_REGISTRY
            if new_skill in SKILLS_REGISTRY:
                logger.info("Agent switching skill", from_skill=active_skill, to_skill=new_skill)
                _emit_progress(req.request_id, "switching", f"Switching to {new_skill} skill...")
                # Re-run with the new skill, injecting context for the new skill
                engine = AgentEngine(model_key=req.model)
                augmented_message, _injected_dfs, _inject_debug = _inject_job_context(
                    req.message, new_skill, conversation_history,
                    history_blocks=history_blocks
                )
                raw_response, _think_usage = await run_in_threadpool(
                    engine.think, 
                    augmented_message, 
                    new_skill,
                    conversation_history
                )
                # Validate the re-run output too
                raw_response, _switch_violations = _validate_llm_output(
                    raw_response, new_skill, history_blocks=history_blocks
                )
                _output_violations.extend(_switch_violations)
                active_skill = new_skill  # Update the skill for the response
                _skill_switch_debug["switched"] = True
                _skill_switch_debug["new_response_preview"] = raw_response[:200]
        _skill_switch_debug["post_switch_skill"] = active_skill
        _inject_debug["skill_switch"] = _skill_switch_debug

        
        # 4. Parse the "Approval Gate Tag"
        trigger_tag = "[[APPROVAL_NEEDED]]"
        needs_approval = trigger_tag in raw_response

        # Suppress spurious approval gates for skills that never submit jobs.
        # The LLM sometimes echoes [[APPROVAL_NEEDED]] from conversation
        # history even when the current skill is a search/browse skill.
        _APPROVAL_SKILLS = {
            "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
            "analyze_local_sample", "download_files",
        }
        if needs_approval and active_skill not in _APPROVAL_SKILLS:
            logger.warning(
                "Suppressing spurious APPROVAL_NEEDED for non-job skill",
                skill=active_skill,
            )
            needs_approval = False
            raw_response = raw_response.replace(trigger_tag, "").strip()
        
        # 5. Parse DATA_CALL Tags (unified format for all consortia and services)
        # Format: [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]
        #     or: [[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=...]]
        
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

        # 5a. Parse [[PLOT:...]] tags for interactive chart generation
        # Format: [[PLOT: type=scatter, df=DF5, x=score, y=enrichment, color=biosample_type, title=My Title]]
        # NOTE: use non-greedy .*? with DOTALL so that ] characters inside the tag
        # (e.g. Columns: ['A', 'B']) don't break the match.
        plot_tag_pattern = r'\[\[PLOT:\s*(.*?)\]\]'
        plot_tag_matches = list(re.finditer(plot_tag_pattern, corrected_response, re.DOTALL))
        plot_specs = []
        for _pm in plot_tag_matches:
            _raw_inner = _pm.group(1)
            _plot_params = _parse_tag_params(_raw_inner)
            # If no key=value pairs were found (LLM wrote natural language inside
            # the tag, e.g. "histogram of DF1 with Category on the x-axis"),
            # attempt to extract parameters from the free-form text.
            if not _plot_params.get("df"):
                _nl = _raw_inner
                # Chart type
                for _ct in ("histogram", "scatter", "bar", "box", "heatmap", "pie"):
                    if _ct in _nl.lower():
                        _plot_params.setdefault("type", _ct)
                        break
                # DF reference: "DF1", "df 2", etc.
                _nl_df_m = re.search(r'\bDF\s*(\d+)\b', _nl, re.IGNORECASE)
                if _nl_df_m:
                    _plot_params["df"] = f"DF{_nl_df_m.group(1)}"
                # x column — "Category on the x-axis", "x=Category", "x: Category"
                _x_m = re.search(
                    r'\b(\w+)\s+(?:on\s+the\s+)?x[- ]axis', _nl, re.IGNORECASE
                ) or re.search(
                    r'\bx\s*[=:]\s*([\w][\w ]*?)(?:,|\.|\band\b|$)', _nl, re.IGNORECASE
                )
                if _x_m:
                    _plot_params.setdefault("x", _x_m.group(1).strip())
                # y column — "Count on the y-axis", "y=Count"
                _y_m = re.search(
                    r'\b(\w+)\s+(?:on\s+the\s+)?y[- ]axis', _nl, re.IGNORECASE
                ) or re.search(
                    r'\by\s*[=:]\s*([\w][\w ]*?)(?:,|\.|\band\b|$)', _nl, re.IGNORECASE
                )
                if _y_m:
                    _plot_params.setdefault("y", _y_m.group(1).strip())
            # Normalize: extract df_id integer from "DF5" or "5"
            _df_ref = _plot_params.get("df", "")
            _df_id_match = re.match(r'(?:DF)?\s*(\d+)', _df_ref, re.IGNORECASE)
            if _df_id_match:
                _plot_params["df_id"] = int(_df_id_match.group(1))
            else:
                _plot_params["df_id"] = None
            # Default chart type to histogram if missing
            if "type" not in _plot_params:
                _plot_params["type"] = "histogram"
            plot_specs.append(_plot_params)
        if plot_specs:
            logger.info("Parsed PLOT tags", count=len(plot_specs),
                       specs=[{"type": s.get("type"), "df_id": s.get("df_id")} for s in plot_specs])

        # 5a-fix. Override hallucinated DF references in PLOT tags.
        # When the user says "plot this" / "plot it" without naming a specific DF,
        # the LLM often defaults to DF1.  Correct to the most recent DF.
        _user_explicit_df = re.search(r'\bDF\s*(\d+)\b', req.message, re.IGNORECASE)
        if plot_specs and not _user_explicit_df and _conv_state.latest_dataframe:
            _latest_id_m = re.match(r'DF(\d+)', _conv_state.latest_dataframe)
            if _latest_id_m:
                _latest_id = int(_latest_id_m.group(1))
                for _ps in plot_specs:
                    if _ps.get("df_id") is not None and _ps["df_id"] != _latest_id:
                        logger.warning(
                            "Overriding PLOT df_id with latest DF",
                            llm_df_id=_ps["df_id"], latest_df_id=_latest_id,
                        )
                        _ps["df_id"] = _latest_id
                        _ps["df"] = f"DF{_latest_id}"

        # 5a-b. FALLBACK: If LLM wrote Python code for plotting instead of [[PLOT:...]] tags,
        #        auto-generate the tag from context clues (user message + DF references).
        _plot_keywords = {"plot", "chart", "pie", "histogram", "scatter", "bar chart",
                          "box plot", "heatmap", "visualize", "graph", "distribution"}
        _user_wants_plot = any(kw in req.message.lower() for kw in _plot_keywords)
        _has_code_plot = bool(re.search(
            r'```python.*?(?:matplotlib|plt\.|plotly|px\.|fig\.|\.pie|\.bar|\.hist|\.scatter)',
            corrected_response, re.DOTALL | re.IGNORECASE
        ))
        # Also trigger the fallback when:
        # (a) the LLM produced [[PLOT:...]] tags but none resolved to a valid df_id, OR
        # (b) no plot specs were parsed at all (regex match failed, e.g. ] inside tag
        #     ate the match before the NL parser could run).
        _plot_specs_invalid = plot_specs and all(s.get("df_id") is None for s in plot_specs)
        _plot_specs_missing = _user_wants_plot and not plot_specs
        if _user_wants_plot and (_has_code_plot or _plot_specs_invalid or _plot_specs_missing) and not (
            plot_specs and any(s.get("df_id") is not None for s in plot_specs)
        ):
            logger.warning("LLM wrote Python plot code instead of [[PLOT:...]] tag — auto-generating")
            # Detect which DF the user or LLM referenced
            _auto_df_id = None
            _df_ref_in_msg = re.search(r'\bDF\s*(\d+)\b', req.message, re.IGNORECASE)
            if _df_ref_in_msg:
                _auto_df_id = int(_df_ref_in_msg.group(1))
            else:
                _df_ref_in_resp = re.search(r'\bDF\s*(\d+)\b', corrected_response, re.IGNORECASE)
                if _df_ref_in_resp:
                    _auto_df_id = int(_df_ref_in_resp.group(1))
                elif _injected_dfs:
                    # Use the first injected DF's ID
                    for _ij_data in _injected_dfs.values():
                        _ij_id = _ij_data.get("metadata", {}).get("df_id")
                        if _ij_id is not None:
                            _auto_df_id = _ij_id
                            break
                # Last resort: use latest_dataframe from conversation state
                if _auto_df_id is None and _conv_state.latest_dataframe:
                    _ld_m = re.match(r'DF(\d+)', _conv_state.latest_dataframe)
                    if _ld_m:
                        _auto_df_id = int(_ld_m.group(1))
            # Detect chart type from user message
            _msg_l = req.message.lower()
            if "pie" in _msg_l:
                _auto_type = "pie"
            elif "scatter" in _msg_l:
                _auto_type = "scatter"
            elif "bar" in _msg_l:
                _auto_type = "bar"
            elif "box" in _msg_l:
                _auto_type = "box"
            elif "heatmap" in _msg_l or "correlation" in _msg_l:
                _auto_type = "heatmap"
            elif "histogram" in _msg_l or "distribution" in _msg_l:
                _auto_type = "histogram"
            else:
                _auto_type = "bar"
            # Detect x column from user message (look for "by <column>", "of <column>")
            # Prefer "by" matches over "of/for" since "by" usually indicates the grouping column.
            # Skip DF references (e.g., "for DF1") — those aren't column names.
            _auto_x = None
            _by_match = re.search(r'\bby\s+(\w+)', req.message, re.IGNORECASE)
            if _by_match and not re.match(r'DF\d+', _by_match.group(1), re.IGNORECASE):
                _auto_x = _by_match.group(1)
            if not _auto_x:
                _of_match = re.search(r'\b(?:of|for)\s+(\w+)', req.message, re.IGNORECASE)
                if _of_match and not re.match(r'DF\d+', _of_match.group(1), re.IGNORECASE):
                    _auto_x = _of_match.group(1)
            if _auto_df_id is not None:
                _auto_spec = {
                    "type": _auto_type,
                    "df_id": _auto_df_id,
                    "df": f"DF{_auto_df_id}",
                }
                if _auto_x:
                    _auto_spec["x"] = _auto_x
                # Do NOT set agg=count for bar by default — if the DF is
                # pre-aggregated (has a numeric value column), _build_plotly_figure
                # will use it automatically via the categorical-x companion-column logic.
                if _auto_type == "pie" and not _auto_spec.get("y"):
                    # Pie charts with only x column count occurrences
                    pass  # UI handles this automatically
                plot_specs.append(_auto_spec)
                logger.info("Auto-generated PLOT spec from code fallback",
                           spec=_auto_spec)
            # Strip the code block from the markdown so user doesn't see code
            corrected_response = re.sub(
                r'```python.*?```', '', corrected_response, flags=re.DOTALL
            ).strip()
            # Also strip any "Explanation:" boilerplate that follows code blocks
            corrected_response = re.sub(
                r'\n*(?:Explanation|Output|Note|Here is|The (?:pie|bar|scatter|histogram|box) chart).*$',
                '', corrected_response, flags=re.DOTALL | re.IGNORECASE
            ).strip()

        # 5a-c. Plot-command override: when the user wants a plot and we have
        # valid plot_specs, suppress any DATA_CALL / APPROVAL tags the LLM
        # emitted.  Without this, "plot this" on Dogme skills triggers
        # list_job_files + a spurious job submission approval gate.
        if _user_wants_plot and plot_specs and any(s.get("df_id") is not None for s in plot_specs):
            if data_call_matches or legacy_encode_matches or legacy_analysis_matches or needs_approval:
                logger.warning(
                    "Plot-command override: suppressing LLM DATA_CALL/APPROVAL tags",
                    data_calls=len(data_call_matches),
                    legacy_encode=len(legacy_encode_matches),
                    needs_approval=needs_approval,
                )
            data_call_matches = []
            legacy_encode_matches = []
            legacy_analysis_matches = []
            needs_approval = False
            # Strip all tags from LLM response so only the plot placeholder remains
            corrected_response = re.sub(data_call_pattern, '', corrected_response).strip()
            corrected_response = re.sub(legacy_encode_pattern, '', corrected_response).strip()
            corrected_response = re.sub(legacy_analysis_pattern, '', corrected_response).strip()
            corrected_response = corrected_response.replace(trigger_tag, "").strip()

        # Clean all tags from user-visible text
        clean_markdown = corrected_response.replace(trigger_tag, "").strip()
        clean_markdown = re.sub(r'\[\[SKILL_SWITCH_TO:\s*\w+\]\]', '', clean_markdown).strip()
        clean_markdown = re.sub(data_call_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(legacy_encode_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(legacy_analysis_pattern, '', clean_markdown).strip()
        clean_markdown = re.sub(plot_tag_pattern, '', clean_markdown, flags=re.DOTALL).strip()
        
        # Also remove any remaining plain text patterns that might not have been converted
        for pattern in all_fallback_patterns.keys():
            clean_markdown = re.sub(pattern, '', clean_markdown, flags=re.IGNORECASE).strip()

        # If the LLM only emitted a PLOT tag (no surrounding text), insert a
        # brief placeholder so the AGENT_PLAN block isn't empty in the UI.
        if not clean_markdown and plot_specs:
            _ps0 = plot_specs[0]
            _chart_type = _ps0.get("type", "chart").replace("_", " ")
            _df_label = _ps0.get("df", "")  # string like "DF2"
            if not _df_label and _ps0.get("df_id") is not None:
                _df_label = f"DF{_ps0['df_id']}"
            clean_markdown = f"Here is the {_chart_type} for **{_df_label}**:" if _df_label else f"Here is the {_chart_type}:"

        # Fix hallucinated ENCSR accessions in LLM text response.
        # If the user explicitly stated exactly one ENCSR accession and the LLM
        # mentions a *different* ENCSR in its text, replace the wrong one.
        _encsr_in_user_msg = re.findall(r'(ENCSR[A-Z0-9]{6})', req.message, re.IGNORECASE)
        if len(_encsr_in_user_msg) == 1:
            _correct_encsr = _encsr_in_user_msg[0].upper()
            _encsr_in_reply = re.findall(r'(ENCSR[A-Z0-9]{6})', clean_markdown, re.IGNORECASE)
            _wrong_encsr = [a.upper() for a in _encsr_in_reply if a.upper() != _correct_encsr]
            for _wrong in set(_wrong_encsr):
                logger.warning("Fixing hallucinated ENCSR in LLM text",
                               hallucinated=_wrong, correct=_correct_encsr)
                clean_markdown = re.sub(
                    re.escape(_wrong), _correct_encsr, clean_markdown, flags=re.IGNORECASE
                )


        #     detect patterns in the user message and auto-generate appropriate calls.
        #     ALSO validates accessions when the user uses referential words ("them",
        #     "each of them", etc.) to catch LLM-hallucinated accession numbers.
        #     SKIP auto-generation if we injected previous query data (the LLM was
        #     told to answer from existing data, so no tags is correct behaviour).
        has_any_tags = bool(data_call_matches or legacy_encode_matches or legacy_analysis_matches)
        auto_calls = []
        _injected_previous_data = "[PREVIOUS QUERY DATA:]" in augmented_message
        # If the injected data was row-capped (large dataset), we still want to
        # allow auto-generation for assay-filter follow-ups (e.g. K562 → long read RNA-seq).
        # Detect this by checking if the injected data contained a truncation note.
        _injected_was_capped = _injected_previous_data and "total rows)*" in augmented_message

        # When we actually injected data into context (not capped), suppress
        # ALL DATA_CALL / legacy ENCODE_CALL tags the LLM may have emitted.
        # The injected context already contains the answer — any API call is
        # redundant and often wrong (e.g. the LLM passes "DF1" as a literal
        # search term).  Only suppress when _injected_dfs is non-empty, meaning
        # we found a matching DF.  If the user referenced a DF that doesn't
        # exist (e.g. DF55), _injected_dfs will be empty and we should let the
        # DATA_CALL proceed normally.
        if _injected_dfs and not _injected_was_capped:
            _suppressed = []
            if data_call_matches:
                _suppressed.extend(m.group(3) for m in data_call_matches)
                data_call_matches = []
            if legacy_encode_matches:
                _suppressed.extend(f"legacy:{m.group(1)}" for m in legacy_encode_matches)
                legacy_encode_matches = []
            if _suppressed:
                logger.info(
                    "Suppressed ALL DATA_CALL tags (injected data already present)",
                    suppressed_tools=_suppressed,
                )
                has_any_tags = bool(data_call_matches or legacy_encode_matches or legacy_analysis_matches)
                _inject_debug["suppressed_calls"] = _suppressed
            _inject_debug["injected_was_capped"] = _injected_was_capped

        # Check for conversational references in the user's message
        _ref_words = ["them", "these", "those", "each", "all of them",
                      "each of them", "for those", "the experiments",
                      "the accessions", "same"]
        _msg_lower = req.message.lower()
        _has_referential = any(w in _msg_lower for w in _ref_words)

        # --- Browsing-command override ---
        # "list workflows", "list files", etc. must always route to analyzer,
        # regardless of the active skill.  When the skill is ENCODE_Search the
        # LLM may generate an ENCODE tag instead of a analyzer tag.  Detect
        # this and force the correct auto-generated call.
        _is_browsing_override = False
        _browsing_patterns = [
            r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
            r'\b(?:list|show)\s+(?:the\s+)?(?:project\s+)?files?\b',
        ]
        if any(re.search(p, _msg_lower) for p in _browsing_patterns):
            _browse_calls = _auto_generate_data_calls(
                req.message, active_skill,
                conversation_history, history_blocks=history_blocks,
                project_dir=_project_dir_str,
            )
            if _browse_calls:
                # Only keep analyzer calls from the override
                _browse_calls = [c for c in _browse_calls if c.get("source_key") == "analyzer"]
            if _browse_calls:
                auto_calls = _browse_calls
                _is_browsing_override = True
                # Suppress any LLM-generated tags (they're wrong for browsing)
                data_call_matches = []
                legacy_encode_matches = []
                legacy_analysis_matches = []
                has_any_tags = False
                clean_markdown = ""
                needs_approval = False  # browsing is not a job submission
                plot_specs = []         # no plots for file listings
                # Clear injected ENCODE DFs so they're not rendered alongside
                # the file listing
                _injected_dfs = {}
                _injected_previous_data = False
                logger.warning(
                    "Browsing-command override: replacing LLM tags with analyzer calls",
                    skill=active_skill, calls=len(auto_calls),
                )

        if not _is_browsing_override and not has_any_tags and (not _injected_previous_data or _injected_was_capped):
            # Case 1: LLM produced no tags at all — auto-generate
            auto_calls = _auto_generate_data_calls(req.message, active_skill, conversation_history,
                                                     history_blocks=history_blocks,
                                                     project_dir=_project_dir_str)
            if auto_calls:
                logger.warning("LLM failed to generate DATA_CALL tags, auto-generating",
                              count=len(auto_calls), skill=active_skill)
                # Replace the LLM's unhelpful response with a brief note
                clean_markdown = ""
            elif active_skill in ("ENCODE_Search", "ENCODE_LongRead"):
                # Hallucination guard: the LLM may have confidently stated a
                # count or claimed results exist, but NO tool was actually
                # called.  Detect this and strip the hallucinated response.
                _halluc_patterns = [
                    r'there are [\d,]+ .* experiments',
                    r'found [\d,]+ .* results',
                    r'interactive table below',
                    r'experiment list .* below',
                    r'[\d,]+ experiments in ENCODE',
                ]
                _is_hallucinated = any(
                    re.search(p, clean_markdown, re.IGNORECASE)
                    for p in _halluc_patterns
                )
                if _is_hallucinated:
                    logger.warning(
                        "LLM hallucinated ENCODE results without tool execution, "
                        "stripping response",
                        skill=active_skill,
                        response_preview=clean_markdown[:200],
                    )
                    clean_markdown = (
                        "I wasn't able to find an ENCODE query tool for that "
                        "search term. Could you clarify — is this a **cell line**, "
                        "**tissue**, **target protein**, or **assay type**? "
                        "That will help me pick the right query."
                    )
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
                auto_calls = _auto_generate_data_calls(req.message, active_skill, conversation_history,
                                                         history_blocks=history_blocks,
                                                         project_dir=_project_dir_str)
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
        
        # Load aliases for correcting LLM-hallucinated tool/param names
        _tool_aliases = get_all_tool_aliases()
        # Analyzer tool aliases (not in CONSORTIUM_REGISTRY)
        _tool_aliases.update({
            "list_workflows": "list_job_files",
            "list_files": "list_job_files",
            "browse_files": "list_job_files",
            "browse_workflow": "list_job_files",
            "find_files": "find_file",
            "search_file": "find_file",
            "search_files": "find_file",
            "read_file": "read_file_content",
            "get_file": "read_file_content",
            "parse_csv": "parse_csv_file",
            "parse_bed": "parse_bed_file",
            "analysis_summary": "get_analysis_summary",
            "job_summary": "get_analysis_summary",
        })
        _param_aliases = get_all_param_aliases()
        
        # From auto-generated tags (safety net)
        if not has_any_tags and auto_calls:
            for ac in auto_calls:
                _ac_tool = ac["tool"]
                _ac_params = dict(ac["params"])
                _ac_source = ac["source_key"]

                # Apply the same corrections as LLM-generated tags
                if _ac_source == "encode":
                    _ac_tool, _ac_params = _correct_tool_routing(
                        _ac_tool, _ac_params, req.message, conversation_history)
                    _ac_params = _validate_encode_params(_ac_tool, _ac_params, req.message)
                elif _ac_source == "analyzer":
                    _ac_params = _validate_analyzer_params(
                        _ac_tool, _ac_params, req.message,
                        conversation_history=conversation_history,
                        history_blocks=history_blocks,
                        project_dir=_project_dir_str,
                    )

                entry = {"tool": _ac_tool, "params": _ac_params}
                if "_chain" in ac:
                    entry["_chain"] = ac["_chain"]
                calls_by_source.setdefault(_ac_source, []).append(entry)

        # From new DATA_CALL tags
        for match in data_call_matches:
            source_type = match.group(1)  # "consortium" or "service"
            source_key = match.group(2)   # e.g., "encode", "analyzer"
            tool_name = match.group(3)
            params_str = match.group(4)
            
            # Fix hallucinated tool names using alias map
            corrected_tool = _tool_aliases.get(tool_name, tool_name)
            if corrected_tool != tool_name:
                logger.warning("Corrected hallucinated tool name",
                              original=tool_name, corrected=corrected_tool)
            
            params = _parse_tag_params(params_str)

            # Fix hallucinated parameter names using param alias map
            _p_aliases = _param_aliases.get(corrected_tool, {})
            if _p_aliases:
                params = {_p_aliases.get(k, k): v for k, v in params.items()}

            # Fix wrong tool for accession type (e.g. get_experiment for ENCFF)
            corrected_tool, params = _correct_tool_routing(
                corrected_tool, params, req.message, conversation_history)

            # Validate/fix ENCODE params (missing search_term, wrong organism)
            if source_key == "encode":
                params = _validate_encode_params(corrected_tool, params, req.message)

            # Validate/fix Analyzer params (placeholder work_dir)
            if source_key == "analyzer":
                params = _validate_analyzer_params(
                    corrected_tool, params, req.message,
                    conversation_history=conversation_history,
                    history_blocks=history_blocks,
                    project_dir=_project_dir_str,
                )

            calls_by_source.setdefault(source_key, []).append({
                "tool": corrected_tool,
                "params": params,
            })
        
        # From legacy ENCODE_CALL tags (backward compat)
        for match in legacy_encode_matches:
            tool_name = match.group(1)
            params_str = match.group(2)
            params = _parse_tag_params(params_str)
            
            # Fix hallucinated tool names using alias map
            corrected_tool = _tool_aliases.get(tool_name, tool_name)
            if corrected_tool != tool_name:
                logger.warning("Corrected hallucinated tool name (legacy)",
                              original=tool_name, corrected=corrected_tool)

            # Fix hallucinated parameter names using param alias map
            _p_aliases = _param_aliases.get(corrected_tool, {})
            if _p_aliases:
                params = {_p_aliases.get(k, k): v for k, v in params.items()}

            # Fix wrong tool for accession type (e.g. get_experiment for ENCFF)
            corrected_tool, params = _correct_tool_routing(
                corrected_tool, params, req.message, conversation_history)

            # Validate/fix ENCODE params (missing search_term, wrong organism)
            params = _validate_encode_params(corrected_tool, params, req.message)

            calls_by_source.setdefault("encode", []).append({
                "tool": corrected_tool,
                "params": params,
            })
        
        # From legacy ANALYSIS_CALL tags (backward compat)
        for match in legacy_analysis_matches:
            analysis_type = match.group(1)
            run_uuid = match.group(2)
            
            # Map legacy analysis types to Analyzer MCP tool names
            tool_map = {
                "summary": "get_analysis_summary",
                "categorize_files": "categorize_job_files",
                "list_files": "list_job_files",
            }
            tool_name = tool_map.get(analysis_type, analysis_type)
            
            calls_by_source.setdefault("analyzer", []).append({
                "tool": tool_name,
                "params": {"run_uuid": run_uuid},
            })
        
        # 6b. Deduplicate tool calls within each source.
        # The LLM sometimes emits both a DATA_CALL and a legacy ENCODE_CALL
        # (or two DATA_CALL tags with slightly different casing) for the same
        # logical query.  Deduplicate by (tool, canonical_params) so the same
        # search isn't executed twice.
        for _src_key in list(calls_by_source):
            _seen: set[str] = set()
            _deduped: list[dict] = []
            for _call in calls_by_source[_src_key]:
                # Build a canonical key: tool + sorted params (case-insensitive values)
                _canon_params = tuple(
                    sorted((k, v.lower() if isinstance(v, str) else v)
                           for k, v in _call["params"].items())
                )
                _key = (_call["tool"], _canon_params)
                _key_str = str(_key)
                if _key_str not in _seen:
                    _seen.add(_key_str)
                    _deduped.append(_call)
                else:
                    logger.info("Deduplicated duplicate tool call",
                               tool=_call["tool"], params=_call["params"])
            calls_by_source[_src_key] = _deduped

        # 6b2. Schema validation: validate params against cached tool schemas
        for _src_key, _calls_list in calls_by_source.items():
            for _call in _calls_list:
                if "__routing_error__" in _call.get("params", {}):
                    continue  # will be handled as routing error
                _cleaned, _violations = validate_against_schema(
                    _call["tool"], _call["params"], _src_key
                )
                if _violations:
                    logger.warning("Schema validation issues",
                                  tool=_call["tool"], source=_src_key,
                                  violations=_violations)
                _call["params"] = _cleaned

        # 6c. Execute all tool calls grouped by source
        all_results = {}  # {source_key: [result_dicts]}
        _pending_download_files: list[dict] = []  # populated by download chain
        
        for source_key, calls in calls_by_source.items():
            logger.info("Executing tool calls", source=source_key, count=len(calls))
            _source_label = CONSORTIUM_REGISTRY.get(source_key, {}).get("display_name") \
                or SERVICE_REGISTRY.get(source_key, {}).get("display_name", source_key)
            _emit_progress(req.request_id, "tools", f"Querying {_source_label}...")
            
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

                    # Check for routing errors (e.g. missing parent experiment)
                    if "__routing_error__" in params:
                        error_msg = params.pop("__routing_error__")
                        logger.warning("Skipping tool call due to routing error",
                                      tool=tool_name, error=error_msg)
                        source_results.append({
                            "tool": tool_name,
                            "params": params,
                            "error": error_msg,
                        })
                        continue
                    
                    logger.info("Calling tool", source=source_key, tool=tool_name, params=params)
                    
                    try:
                        result_data = await mcp_client.call_tool(tool_name, **params)
                    except (ConnectionError, TimeoutError, OSError) as _conn_err:
                        # Single retry for transient connection errors
                        logger.warning("Transient error, retrying once",
                                      source=source_key, tool=tool_name, error=str(_conn_err))
                        await asyncio.sleep(2)
                        try:
                            result_data = await mcp_client.call_tool(tool_name, **params)
                        except Exception as _retry_err:
                            logger.error("Retry also failed", source=source_key,
                                        tool=tool_name, error=str(_retry_err))
                            source_results.append({
                                "tool": tool_name,
                                "params": params,
                                "error": f"Connection failed after retry: {_retry_err}",
                            })
                            continue

                    try:
                        source_results.append({
                            "tool": tool_name,
                            "params": params,
                            "data": result_data,
                        })
                        logger.info("Tool call successful", source=source_key, tool=tool_name)

                        # --- Chaining: download after get_file_metadata ---
                        # Trigger when _chain=="download" (auto-generated) OR
                        # when active_skill is download_files and LLM generated
                        # the get_file_metadata tag directly (no _chain key),
                        # OR when the user's message has clear download intent
                        # (e.g. "download ENCFF..." on any ENCODE skill).
                        _msg_lower = req.message.lower() if hasattr(req, 'message') else ""
                        _user_wants_download = any(
                            w in _msg_lower for w in ("download", "grab", "fetch", "save")
                        )
                        _is_download_chain = (
                            call.get("_chain") == "download"
                            or active_skill == "download_files"
                            or _user_wants_download
                        )
                        if _is_download_chain \
                                and tool_name == "get_file_metadata" \
                                and isinstance(result_data, dict):
                            _dl_url = None
                            _file_acc = params.get("file_accession", "")
                            _file_size = result_data.get("file_size")
                            _file_fmt = result_data.get("file_format", "")
                            _file_name = f"{_file_acc}.{_file_fmt}" if _file_acc and _file_fmt else _file_acc

                            # Prefer the cloud_metadata URL (direct S3 link)
                            _cloud = result_data.get("cloud_metadata")
                            if isinstance(_cloud, dict) and _cloud.get("url"):
                                _dl_url = _cloud["url"]
                            # Fallback: construct from href
                            elif result_data.get("href"):
                                _dl_url = f"https://www.encodeproject.org{result_data['href']}"

                            if _dl_url:
                                _dl_file_info = {
                                    "url": _dl_url,
                                    "filename": _file_name,
                                    "size_bytes": _file_size,
                                    "accession": _file_acc,
                                }
                                # Append (not replace) so multi-file downloads accumulate
                                _pending_download_files.append(_dl_file_info)
                                logger.info(
                                    "Download chain: resolved URL from file metadata",
                                    file_accession=_file_acc, url=_dl_url,
                                    size_bytes=_file_size,
                                )
                                # Force download_files skill and approval
                                active_skill = "download_files"
                                needs_approval = True
                                # Build a clean confirmation message listing all pending files
                                _plan_lines = ["**Download Plan:**"]
                                _total_mb = 0
                                for _pf in _pending_download_files:
                                    _sz = _pf.get("size_bytes") or 0
                                    _mb = round(_sz / (1024 * 1024), 1)
                                    _total_mb += _mb
                                    _plan_lines.append(f"- **File:** `{_pf['filename']}` ({_mb} MB)")
                                _plan_lines.append(f"- **Total:** {round(_total_mb, 1)} MB")
                                _plan_lines.append(f"- **Destination:** `data/`\n")
                                _plan_lines.append("Proceed with download?")
                                clean_markdown = "\n".join(_plan_lines)

                        # --- Chaining: follow up find_file with parse/read ---
                        # Works whether _chain was set by auto_calls or needs
                        # to be inferred (e.g. LLM generated the find_file tag directly).
                        if tool_name == "find_file" and isinstance(result_data, dict) \
                                and result_data.get("success") and result_data.get("primary_path"):
                            chain_tool = call.get("_chain")
                            primary_path = result_data["primary_path"]
                            # Infer follow-up tool from filename if not explicitly set
                            if not chain_tool:
                                chain_tool = _pick_file_tool(primary_path)
                            # Prefer work_dir over run_uuid for chaining
                            chain_work_dir = result_data.get("work_dir") or params.get("work_dir")
                            chain_uuid = result_data.get("run_uuid") or params.get("run_uuid")
                            if chain_work_dir or chain_uuid:
                                logger.info("Chaining find_file → parse/read",
                                           chain_tool=chain_tool, file_path=primary_path)
                                try:
                                    chain_params: dict = {"file_path": primary_path}
                                    if chain_work_dir:
                                        chain_params["work_dir"] = chain_work_dir
                                    elif chain_uuid:
                                        chain_params["run_uuid"] = chain_uuid
                                    if chain_tool == "parse_csv_file":
                                        chain_params["max_rows"] = 100
                                    elif chain_tool == "parse_bed_file":
                                        chain_params["max_records"] = 100
                                    elif chain_tool == "read_file_content":
                                        chain_params["preview_lines"] = 50
                                    chain_result = await mcp_client.call_tool(chain_tool, **chain_params)
                                    source_results.append({
                                        "tool": chain_tool,
                                        "params": chain_params,
                                        "data": chain_result,
                                    })
                                    logger.info("Chain call successful", tool=chain_tool)
                                except Exception as ce:
                                    logger.error("Chain call failed", tool=chain_tool, error=str(ce))
                                    source_results.append({
                                        "tool": chain_tool,
                                        "params": chain_params,
                                        "error": str(ce),
                                    })

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
        
        # 6c. Build provenance records for all tool calls (audit trail)
        _provenance: list[dict] = []
        _now_ts = datetime.datetime.utcnow().isoformat() + "Z"
        for _prov_src, _prov_results in all_results.items():
            for _prov_r in _prov_results:
                _prov_entry = {
                    "source": _prov_src,
                    "tool": _prov_r.get("tool", "?"),
                    "params": {k: v for k, v in _prov_r.get("params", {}).items()
                               if k not in ("__routing_error__",)},
                    "timestamp": _now_ts,
                    "success": "data" in _prov_r,
                }
                if "data" in _prov_r:
                    _data = _prov_r["data"]
                    if isinstance(_data, dict):
                        _prov_entry["rows"] = _data.get("total") or _data.get("count") or len(_data.get("experiments", _data.get("files", [])))
                    elif isinstance(_data, list):
                        _prov_entry["rows"] = len(_data)
                if "error" in _prov_r:
                    _prov_entry["error"] = str(_prov_r["error"])[:200]
                _provenance.append(_prov_entry)

        # 6d. Format results and run second-pass LLM analysis
        _analyze_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # Fix B: Recovery guard — detect when the LLM echoed a find_file JSON
        # result verbatim instead of emitting a [[DATA_CALL:...]] tag.
        # This happens with weaker models (e.g. devstral-2) that have low
        # instruction-following fidelity. When detected:
        #   1. Extract primary_path and run_uuid from the echoed JSON
        #   2. Call the appropriate parse tool directly
        #   3. Inject the result into all_results so the normal second-pass
        #      analyze_results flow handles it as if a DATA_CALL had fired.
        if not all_results and '"primary_path"' in clean_markdown:
            _echo_stripped = re.sub(r'```(?:json)?|```', '', clean_markdown).strip()
            if '"success": true' in _echo_stripped or '"success":true' in _echo_stripped:
                try:
                    _echo_data = json.loads(_echo_stripped)
                    if (_echo_data.get("success")
                            and _echo_data.get("primary_path")
                            and _echo_data.get("run_uuid")):
                        _recovery_path = _echo_data["primary_path"]
                        _recovery_uuid = _echo_data["run_uuid"]
                        _recovery_tool = _pick_file_tool(_recovery_path)
                        logger.info(
                            "[CHAIN-RECOVERY] LLM echoed find_file JSON — auto-chaining",
                            tool=_recovery_tool,
                            file_path=_recovery_path,
                            run_uuid=_recovery_uuid,
                        )
                        _chain_params: dict = {
                            "run_uuid": _recovery_uuid,
                            "file_path": _recovery_path,
                        }
                        if _recovery_tool == "parse_csv_file":
                            _chain_params["max_rows"] = 100
                        elif _recovery_tool == "parse_bed_file":
                            _chain_params["max_records"] = 100
                        elif _recovery_tool == "read_file_content":
                            _chain_params["preview_lines"] = 50
                        try:
                            _recovery_url = get_service_url("analyzer")
                            _recovery_mcp = MCPHttpClient(name="analyzer", base_url=_recovery_url)
                            await _recovery_mcp.connect()
                            _recovery_result = await _recovery_mcp.call_tool(
                                _recovery_tool, **_chain_params
                            )
                            await _recovery_mcp.disconnect()
                            # Replace clean_markdown with a neutral placeholder so
                            # analyze_results writes the real summary, not the echo.
                            clean_markdown = ""
                            all_results["analyzer"] = [{
                                "tool": _recovery_tool,
                                "params": _chain_params,
                                "data": _recovery_result,
                            }]
                            logger.info("[CHAIN-RECOVERY] Parse succeeded",
                                        tool=_recovery_tool)
                        except Exception as _re:
                            logger.error("[CHAIN-RECOVERY] Parse failed",
                                         tool=_recovery_tool, error=str(_re))
                except (json.JSONDecodeError, ValueError):
                    pass  # not clean JSON — leave all_results empty, fall through normally

        if all_results:
            # Check if all results are errors (skip second pass if so)
            has_real_data = any(
                any("data" in r for r in results)
                for results in all_results.values()
            )

            # Format results into compact markdown for the LLM to analyze
            formatted_data_parts = []
            # Collect structured error blocks for the LLM
            _error_blocks = []
            for source_key, results in all_results.items():
                if results:
                    entry = SERVICE_REGISTRY.get(source_key)
                    results_markdown = format_results(source_key, results, registry_entry=entry)
                    formatted_data_parts.append(results_markdown)
                    # Build structured error blocks for any failed tool calls
                    for _r in results:
                        if "error" in _r:
                            _err_msg = _r["error"]
                            _err_type = "unknown"
                            if "not found" in _err_msg.lower() or "404" in _err_msg:
                                _err_type = "not_found"
                            elif "connection" in _err_msg.lower() or "timeout" in _err_msg.lower():
                                _err_type = "connection_failed"
                            elif "permission" in _err_msg.lower() or "403" in _err_msg:
                                _err_type = "permission_denied"
                            elif "routing" in _err_msg.lower():
                                _err_type = "routing_error"
                            _error_blocks.append(
                                f"[TOOL_ERROR: tool={_r.get('tool', '?')}, "
                                f"error_type={_err_type}, "
                                f"message=\"{_err_msg}\"]\n"
                                f"Follow the TOOL FAILURE RULES in your system prompt."
                            )

            formatted_data = "\n".join(formatted_data_parts)
            # Build provenance headers for traceability
            _prov_block = ""
            if _provenance:
                _prov_lines = []
                for _p in _provenance:
                    if _p.get("success"):
                        _prov_lines.append(
                            f"[TOOL_RESULT: source={_p['source']}, tool={_p['tool']}, "
                            f"params={{{', '.join(f'{k}={v}' for k, v in _p.get('params', {}).items())}}}, "
                            f"rows={_p.get('rows', '?')}, timestamp={_p['timestamp']}]"
                        )
                if _prov_lines:
                    _prov_block = "\n".join(_prov_lines)
                    # Prepend to formatted_data so the LLM sees provenance context
                    formatted_data = _prov_block + "\n\n" + formatted_data
            # Append structured errors so the LLM uses the error-handling playbook
            if _error_blocks and not has_real_data:
                formatted_data += "\n\n" + "\n".join(_error_blocks)
            logger.info("Formatted tool results", size=len(formatted_data),
                       has_real_data=has_real_data,
                       preview=formatted_data[:500])

            # --- Skip second-pass LLM for pure browsing commands ---
            # "list files", "list workflows" etc. should show the file table
            # directly — no LLM re-interpretation needed.
            _browsing_tools = {"list_job_files", "categorize_job_files"}
            _all_tools_used = set()
            _has_download_chain = False
            for _src_results in all_results.values():
                for _r in _src_results:
                    _all_tools_used.add(_r.get("tool", ""))
                    if _r.get("_chain") == "download":
                        _has_download_chain = True
            _is_browsing = bool(_all_tools_used) and _all_tools_used <= _browsing_tools

            # Also skip second-pass for download workflows — the metadata is
            # used to build the approval gate, not to summarise for the user.
            _is_download = _has_download_chain or active_skill == "download_files"

            if (_is_browsing or _is_download) and has_real_data and formatted_data.strip():
                # Show the formatted file listing directly — no second pass.
                # Use the raw results (without provenance prefix) and append
                # provenance as a collapsed section at the end.
                _display_data = "\n".join(formatted_data_parts)
                if _prov_block:
                    _display_data += (
                        "\n\n<details><summary>📋 Query Details</summary>\n\n"
                        + _prov_block
                        + "\n\n</details>"
                    )
                clean_markdown = _display_data

            elif _is_browsing and not has_real_data:
                # Browsing command failed (e.g. directory not found).
                # Show the error plus helpful suggestions directly.
                _err_detail = ""
                for _src_results in all_results.values():
                    for _r in _src_results:
                        if "error" in _r:
                            _err_detail = _r.get("error", "")
                            break
                clean_markdown = (
                    f"⚠️ {_err_detail}\n\n"
                    "Try one of these:\n"
                    "- **list files** — files in the current workflow\n"
                    "- **list project files** — top-level project contents\n"
                    "- **list project files in data** — a subfolder under the project root\n"
                    "- **list files in workflow2/annot** — a specific workflow subfolder\n"
                    "- **list workflows** — all workflows in the project"
                )

            elif has_real_data and formatted_data.strip():
                # Cap data size to avoid overwhelming the LLM context window.
                # For large list results, keep only the header block (tool name,
                # found-count, and the 📊 Summary table) and drop the individual
                # rows — the full table is already stored as an embedded dataframe
                # and will be shown interactively in the UI.
                MAX_DATA_CHARS = 12000
                if len(formatted_data) > MAX_DATA_CHARS:
                    logger.warning("Truncating tool results for LLM",
                                  original_size=len(formatted_data), max_size=MAX_DATA_CHARS)
                    # Try to keep everything up to and including the 📊 Summary block
                    _summary_end = formatted_data.find("\n**Total:")
                    if _summary_end != -1:
                        # Include the Total line itself
                        _nl = formatted_data.find("\n", _summary_end + 1)
                        _cut = _nl if _nl != -1 else _summary_end + 200
                        formatted_data = (
                            formatted_data[:_cut]
                            + "\n\n*(The full result set is shown as an interactive "
                            "dataframe above — only the summary is shown here.)*"
                        )
                    else:
                        formatted_data = (
                            formatted_data[:MAX_DATA_CHARS]
                            + "\n\n*(Results truncated — full data in interactive dataframe above.)*"
                        )

                # Second-pass: send data to LLM for analysis/filtering/summarization
                logger.info("Running second-pass LLM analysis", data_size=len(formatted_data))
                _emit_progress(req.request_id, "analyzing", "Analyzing results...")
                analyzed_response, _analyze_usage = await run_in_threadpool(
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
        
        # 7. Extract any parsed DataFrames from tool results so the UI
        #    can render them interactively with st.dataframe.
        #    This covers:
        #      a) File-parsing tools (parse_csv_file, parse_bed_file)
        #      b) Search/list tools that return a plain list of dicts
        #         (search_by_biosample, search_by_target, search_by_organism,
        #          list_experiments) — stored BEFORE markdown truncation so the
        #         full result set is always available in the UI regardless of
        #         how many rows the LLM saw.
        _SEARCH_TOOLS = {
            "search_by_biosample", "search_by_target",
            "search_by_organism", "list_experiments",
        }
        _FILE_TOOLS = {"get_files_by_type"}
        _embedded_dataframes = {}
        for _src_key, _src_results in all_results.items():
            _reg_entry = SERVICE_REGISTRY.get(_src_key) or {}
            _table_cols = _reg_entry.get("table_columns", [])  # [(header, field_key), ...]
            for _r in _src_results:
                _tool = _r.get("tool", "")
                if _tool in ("parse_csv_file", "parse_bed_file") and "data" in _r:
                    _rd = _r["data"]
                    if isinstance(_rd, dict) and _rd.get("data"):
                        _fname = _rd.get("file_path") or _r["params"].get("file_path", "unknown")
                        _fname = _fname.rsplit("/", 1)[-1] if "/" in _fname else _fname
                        _embedded_dataframes[_fname] = {
                            "columns": _rd.get("columns", []),
                            "data": _rd["data"],
                            "row_count": _rd.get("row_count", len(_rd["data"])),
                            "metadata": _rd.get("metadata", {}),
                        }
                elif _tool in _FILE_TOOLS and "data" in _r:
                    # get_files_by_type returns {"bam": [...], "fastq": [...], ...}
                    # Store ONE dataframe per file type so follow-up queries like
                    # "which bam files are methylated reads?" inject ONLY the bam
                    # rows — not a merged table of all 69+ files.
                    _rd = _r["data"]
                    if isinstance(_rd, dict):
                        _exp_acc = _r.get("params", {}).get("accession", "files")
                        _cols = ["Accession", "Output Type", "Replicate", "Size", "Status"]
                        # Detect which file type(s) the user asked about so we
                        # only show those tables prominently in the UI.  All
                        # tables are still stored for follow-up queries.
                        _msg_lower_ft = req.message.lower()
                        _asked_file_types: set[str] = set()
                        for _candidate_ft in _rd.keys():
                            if _candidate_ft.lower().split()[0] in _msg_lower_ft:
                                _asked_file_types.add(_candidate_ft)
                        for _ftype, _flist in _rd.items():
                            if not isinstance(_flist, list) or not _flist:
                                continue
                            _rows = []
                            for _fobj in _flist:
                                if isinstance(_fobj, dict):
                                    _rows.append({
                                        "Accession": _fobj.get("accession", ""),
                                        "Output Type": _fobj.get("output_type", ""),
                                        "Replicate": _fobj.get("biological_replicates_formatted")
                                                     or ", ".join(
                                                         f"Rep {x}" for x in
                                                         _fobj.get("biological_replicates", [])
                                                     ),
                                        "Size": _fobj.get("file_size", ""),
                                        "Status": _fobj.get("status", ""),
                                    })
                            if _rows:
                                _df_label = f"{_exp_acc} {_ftype} files ({len(_rows)})"
                                # Mark as visible if user asked about this type
                                # (or all visible if no specific type mentioned).
                                _is_visible = (not _asked_file_types
                                               or _ftype in _asked_file_types)
                                _embedded_dataframes[_df_label] = {
                                    "columns": _cols,
                                    "data": _rows,
                                    "row_count": len(_rows),
                                    "metadata": {
                                        "file_type": _ftype,
                                        "accession": _exp_acc,
                                        "visible": _is_visible,
                                    },
                                }
                elif _tool in _SEARCH_TOOLS and "data" in _r:
                    _rd = _r["data"]
                    if isinstance(_rd, list) and _rd and isinstance(_rd[0], dict):
                        # Derive column list from registry table_columns or first row keys
                        if _table_cols:
                            _cols = [h for h, _ in _table_cols]
                            _rows = [
                                {h: (item.get(k) if not isinstance(item.get(k), list)
                                     else ", ".join(str(v) for v in item.get(k, [])))
                                 for h, k in _table_cols}
                                for item in _rd
                            ]
                        else:
                            _cols = list(_rd[0].keys())
                            _rows = _rd
                        _params = _r.get("params", {})
                        _label = _tool.replace("_", " ").title()
                        if _params.get("search_term"):
                            _label = _params["search_term"]
                        elif _params.get("target"):
                            _label = _params["target"]
                        elif _params.get("organism"):
                            _label = _params["organism"]
                        _fname = f"{_label} ({len(_rd)} results)"
                        _embedded_dataframes[_fname] = {
                            "columns": _cols,
                            "data": _rows,
                            "row_count": len(_rd),
                            "metadata": {"visible": True},
                        }

        # 8. Save AGENT_PLAN (The Text)
        # Status is DONE because the text itself is just informational. 
        # The flow control happens in the gate block below.
        _emit_progress(req.request_id, "done", "Complete")
        # Merge any dataframes produced by _inject_job_context (server-side
        # filtered follow-up results) into the embedded dataframes dict so
        # the UI renders them as interactive tables.
        if _injected_dfs:
            _embedded_dataframes.update(_injected_dfs)

        # Assign sequential DF IDs (DF1, DF2, ...) to VISIBLE dataframes only.
        # Non-visible DFs (e.g. tar/bed when user asked about bam) don't get
        # numbered IDs — they appear collapsed inside raw query details.
        # Scan history to find the highest existing ID, then continue from there.
        _next_df_id = 1
        for _hblk in history_blocks[:-1]:  # exclude the current USER_MESSAGE
            if _hblk.type == "AGENT_PLAN":
                _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                for _dfd in _hblk_dfs.values():
                    _existing_id = _dfd.get("metadata", {}).get("df_id")
                    if isinstance(_existing_id, int):
                        _next_df_id = max(_next_df_id, _existing_id + 1)
        for _df_data in _embedded_dataframes.values():
            _meta = _df_data.setdefault("metadata", {})
            if _meta.get("df_id"):
                continue  # already has an ID (e.g. injected df)
            if _meta.get("visible", True):  # default True for DFs without flag
                _meta["df_id"] = _next_df_id
                _next_df_id += 1
            # Non-visible DFs intentionally get no df_id

        # Update _conv_state with the newly-embedded DFs so the cached state
        # saved into this block is accurate for future requests' fast path.
        if _embedded_dataframes:
            _max_new_id = 0
            for _edf_val in _embedded_dataframes.values():
                _edf_meta = _edf_val.get("metadata", {})
                _edf_id = _edf_meta.get("df_id")
                if isinstance(_edf_id, int) and _edf_id > _max_new_id:
                    _max_new_id = _edf_id
                    _edf_label = _edf_meta.get("label", "")
                    _edf_rows = _edf_meta.get("row_count", "?")
            if _max_new_id > 0:
                _new_latest = f"DF{_max_new_id}"
                _conv_state.latest_dataframe = _new_latest
                if not any(k.startswith(_new_latest + " ") or k == _new_latest
                           for k in _conv_state.known_dataframes):
                    _conv_state.known_dataframes.append(
                        f"{_new_latest} ({_edf_label}, {_edf_rows} rows)"
                    )

        # Consolidate token usage from both LLM passes (think + analyze_results)
        _total_usage = {
            "prompt_tokens": _think_usage["prompt_tokens"] + _analyze_usage["prompt_tokens"],
            "completion_tokens": _think_usage["completion_tokens"] + _analyze_usage["completion_tokens"],
            "total_tokens": _think_usage["total_tokens"] + _analyze_usage["total_tokens"],
            "model": engine.model_name,
        }

        _plan_payload = {
            "markdown": clean_markdown, 
            "skill": active_skill,
            "model": engine.model_name,
            "tokens": _total_usage,
            "state": _conv_state.to_dict(),
        }
        if _embedded_dataframes:
            _plan_payload["_dataframes"] = _embedded_dataframes
        if _provenance:
            _plan_payload["_provenance"] = _provenance
        # Attach debug info for the UI debug panel
        _debug_payload = dict(_inject_debug)  # copy the injection debug info
        _debug_payload["has_injected_dfs"] = bool(_injected_dfs)
        _debug_payload["injected_previous_data"] = _injected_previous_data
        _debug_payload["auto_calls_count"] = len(auto_calls)
        if auto_calls:
            _debug_payload["auto_calls"] = [str(c) for c in auto_calls[:10]]
        _debug_payload["llm_tags_remaining"] = {
            "data_call": len(data_call_matches),
            "legacy_encode": len(legacy_encode_matches),
            "legacy_analysis": len(legacy_analysis_matches),
        }
        _debug_payload["referential_words_detected"] = _has_referential
        _debug_payload["embedded_df_count"] = len(_embedded_dataframes)
        _debug_payload["active_skill"] = active_skill
        _debug_payload["pre_llm_skill"] = _pre_llm_skill
        _debug_payload["auto_skill_detected"] = auto_skill
        _debug_payload["requested_skill"] = req.skill
        _debug_payload["llm_raw_response_preview"] = raw_response[:800] if raw_response else ""
        if _output_violations:
            _debug_payload["output_violations"] = _output_violations
        _plan_payload["_debug"] = _debug_payload
        agent_block = _create_block_internal(
            session,
            req.project_id,
            "AGENT_PLAN",
            _plan_payload,
            status="DONE",
            owner_id=user.id
        )
        
        # Save assistant's response to conversation history (with token counts)
        await save_conversation_message(
            session, req.project_id, user.id, "assistant", clean_markdown,
            token_data=_total_usage, model_name=engine.model_name
        )

        # 8b. Insert AGENT_PLOT blocks for any [[PLOT:...]] tags
        plot_blocks = []
        if plot_specs:
            # Validate plot specs against available dataframes.
            # Build a map of df_id → {columns, data, metadata} from all
            # history blocks AND the current block's embedded dataframes.
            _all_df_map = {}  # df_id → {columns, data, metadata, label}
            for _hblk in history_blocks:
                if _hblk.type == "AGENT_PLAN":
                    _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                    for _hfname, _hfdata in _hblk_dfs.items():
                        _hid = _hfdata.get("metadata", {}).get("df_id")
                        if _hid is not None:
                            _all_df_map[_hid] = {**_hfdata, "label": _hfname}
            # Also include DFs from the block we just created
            for _efname, _efdata in _embedded_dataframes.items():
                _eid = _efdata.get("metadata", {}).get("df_id")
                if _eid is not None:
                    _all_df_map[_eid] = {**_efdata, "label": _efname}

            # Group plot specs by df_id for multi-trace support.
            # Skip any spec where df_id is None — they can't be rendered.
            from collections import defaultdict
            _plots_by_df = defaultdict(list)
            for _ps in plot_specs:
                _df_id_val = _ps.get("df_id")
                if _df_id_val is None:
                    logger.warning("Skipping PLOT spec with no df_id", spec=_ps)
                    continue
                _plots_by_df[_df_id_val].append(_ps)

            for _df_key, _chart_group in _plots_by_df.items():
                _charts = []
                _df_info = _all_df_map.get(_df_key)
                for _cs in _chart_group:
                    _chart_entry = {
                        "type": _cs.get("type", "histogram"),
                        "df_id": _cs.get("df_id"),
                        "x": _cs.get("x"),
                        "y": _cs.get("y"),
                        "color": _cs.get("color"),
                        "title": _cs.get("title"),
                        "agg": _cs.get("agg"),
                    }
                    # Remove None values for cleaner payload
                    _chart_entry = {k: v for k, v in _chart_entry.items() if v is not None}
                    _charts.append(_chart_entry)

                _plot_payload = {
                    "charts": _charts,
                    "skill": active_skill,
                    "model": engine.model_name,
                }
                _plot_block = _create_block_internal(
                    session,
                    req.project_id,
                    "AGENT_PLOT",
                    _plot_payload,
                    status="DONE",
                    owner_id=user.id
                )
                plot_blocks.append(_plot_block)
                logger.info("Created AGENT_PLOT block",
                           block_id=_plot_block.id,
                           chart_count=len(_charts),
                           df_key=_df_key)

        # 5. Insert APPROVAL_GATE (The Buttons) if requested
        gate_block = None
        if needs_approval:
            # Determine gate action based on active skill
            gate_action = "download" if active_skill == "download_files" else "job"

            if gate_action == "download" and _pending_download_files:
                # Download gate: show the files to be downloaded, not Dogme params
                _total_dl_bytes = sum(f.get("size_bytes") or 0 for f in _pending_download_files)
                _dl_target = str(_resolve_project_dir(session, user, req.project_id) / "data")
                extracted_params = {
                    "gate_action": "download",
                    "files": _pending_download_files,
                    "total_size_bytes": _total_dl_bytes,
                    "target_dir": _dl_target,
                }
            else:
                # Job gate: extract Dogme pipeline parameters
                extracted_params = await extract_job_parameters_from_conversation(session, req.project_id)
            
            gate_block = _create_block_internal(
                session,
                req.project_id,
                "APPROVAL_GATE",
                {
                    "label": "Do you authorize the Agent to proceed with this plan?",
                    "extracted_params": extracted_params,
                    "gate_action": gate_action,
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
            "gate_block": row_to_dict(gate_block) if gate_block else None,
            "plot_blocks": [row_to_dict(pb) for pb in plot_blocks] if plot_blocks else None
        }
        
    except Exception as e:
        import traceback
        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        logger.error("Chat endpoint error", error=error_detail, exc_info=True)
        raise HTTPException(status_code=500, detail=error_detail)
    finally:
        session.close()


# --- CONVERSATION HISTORY ENDPOINTS ---

async def save_conversation_message(
    session,
    project_id: str,
    user_id: str,
    role: str,
    content: str,
    token_data: dict | None = None,
    model_name: str | None = None,
):
    """Helper to save conversation messages, optionally with token usage."""
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
        created_at=datetime.datetime.utcnow(),
        prompt_tokens=token_data.get("prompt_tokens") if token_data else None,
        completion_tokens=token_data.get("completion_tokens") if token_data else None,
        total_tokens=token_data.get("total_tokens") if token_data else None,
        model_name=model_name,
    )
    session.add(message)
    
    # Update conversation timestamp
    conversation.updated_at = datetime.datetime.utcnow()
    
    session.commit()


@app.get("/projects/{project_id}/conversations")
async def get_project_conversations(project_id: str, request: Request):
    """Get all conversations for a project."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")
    
    session = SessionLocal()
    try:
        query = select(Conversation)\
            .where(Conversation.project_id == project_id)\
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
    require_project_access(project_id, user, min_role="viewer")
    
    session = SessionLocal()
    try:
        # Get all conversations for this project
        conv_query = select(Conversation)\
            .where(Conversation.project_id == project_id)
        
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
        
        # TODO: Query launchpad for job details
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

async def track_project_access(session, user_id: str, project_id: str, project_name: str = None, role: str = None):
    """Track when a user accesses a project. Preserves existing role if not specified."""
    # Check if access record exists (use scalars().all() to handle duplicates
    # gracefully — older DBs may have duplicate (user_id, project_id) rows)
    access_query = select(ProjectAccess)\
        .where(ProjectAccess.user_id == user_id)\
        .where(ProjectAccess.project_id == project_id)
    
    result = session.execute(access_query)
    all_matches = result.scalars().all()
    access = all_matches[0] if all_matches else None

    # Clean up duplicates if any exist
    if len(all_matches) > 1:
        for dup in all_matches[1:]:
            session.delete(dup)
    
    if access:
        # Update last accessed time; preserve existing role
        access.last_accessed = datetime.datetime.utcnow()
        if project_name:
            access.project_name = project_name
        # Only update role if explicitly provided (don't downgrade on re-access)
        if role:
            access.role = role
    else:
        # Create new access record — default to owner if not specified
        access = ProjectAccess(
            id=str(uuid.uuid4()),
            user_id=user_id,
            project_id=project_id,
            project_name=project_name or project_id,
            role=role or "owner",
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


# --- Project Schemas ---
class ProjectCreateRequest(BaseModel):
    name: str


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    is_archived: Optional[bool] = None


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert arbitrary text to a filesystem-safe slug."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s[:max_len] or "project"


def _dedup_slug(session, owner_id: str, slug: str, exclude_project_id: str | None = None) -> str:
    """Ensure slug is unique among this owner's projects."""
    for i in range(0, 10000):
        candidate = slug if i == 0 else f"{slug}-{i}"
        query = select(Project).where(Project.owner_id == owner_id, Project.slug == candidate)
        if exclude_project_id:
            query = query.where(Project.id != exclude_project_id)
        if not session.execute(query).scalar_one_or_none():
            return candidate
    raise HTTPException(status_code=500, detail="Could not generate unique slug")


@app.post("/projects")
async def create_project(req: ProjectCreateRequest, request: Request):
    """
    Create a new project with a server-generated UUID.
    Atomic: mkdir first (idempotent), then DB insert in a single commit.
    """
    from cortex.user_jail import get_user_project_dir, get_user_project_dir_by_uuid

    user = request.state.user
    project_id = str(uuid.uuid4())

    # 2. Atomic DB write: project row + owner access row in one commit
    session = SessionLocal()
    try:
        # Generate unique slug
        slug = _slugify(req.name)
        slug = _dedup_slug(session, user.id, slug)

        # 1. Create filesystem directory
        # Use slug-based path if user has a username, otherwise fall back to UUID
        if user.username:
            project_dir = get_user_project_dir(user.username, slug)
            (project_dir / "data").mkdir(exist_ok=True)
        else:
            get_user_project_dir_by_uuid(user.id, project_id)

        now = datetime.datetime.utcnow()
        project = Project(
            id=project_id,
            name=req.name,
            slug=slug,
            owner_id=user.id,
            is_public=False,
            is_archived=False,
            created_at=now,
            updated_at=now,
        )
        session.add(project)

        access = ProjectAccess(
            id=str(uuid.uuid4()),
            user_id=user.id,
            project_id=project_id,
            project_name=req.name,
            role="owner",
            last_accessed=now,
        )
        session.add(access)

        # Also set as user's last project
        user_obj = session.execute(select(User).where(User.id == user.id)).scalar_one_or_none()
        if user_obj:
            user_obj.last_project_id = project_id

        session.commit()  # Single commit — both rows or neither

        logger.info("Project created", project_id=project_id, name=req.name, slug=slug, user=user.email)
        return {
            "id": project_id,
            "name": req.name,
            "slug": slug,
            "created_at": str(now),
        }
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")
    finally:
        session.close()


@app.get("/projects")
async def list_projects(request: Request, include_archived: bool = False):
    """
    List all projects the user owns or has been granted access to.
    Returns metadata: name, created date, last activity, role.
    """
    user = request.state.user

    session = SessionLocal()
    try:
        # Get all project_access records for this user
        query = select(ProjectAccess)\
            .where(ProjectAccess.user_id == user.id)\
            .order_by(desc(ProjectAccess.last_accessed))

        result = session.execute(query)
        access_records = result.scalars().all()

        projects = []
        for acc in access_records:
            # Try to get the full Project record (may not exist for legacy projects)
            proj = session.execute(
                select(Project).where(Project.id == acc.project_id)
            ).scalar_one_or_none()

            is_archived = proj.is_archived if proj else False
            if is_archived and not include_archived:
                continue

            # Lightweight job count from dogme_jobs table
            try:
                jcount = session.execute(
                    text("SELECT COUNT(*) FROM dogme_jobs WHERE project_id = :pid"),
                    {"pid": acc.project_id}
                ).scalar() or 0
            except Exception:
                jcount = None

            projects.append({
                "id": acc.project_id,
                "name": proj.name if proj else acc.project_name,
                "slug": proj.slug if proj else None,
                "role": acc.role if hasattr(acc, 'role') else "owner",
                "is_archived": is_archived,
                "is_public": proj.is_public if proj else False,
                "created_at": str(proj.created_at) if proj else None,
                "last_accessed": str(acc.last_accessed),
                "job_count": jcount,
            })

        return {"projects": projects}
    finally:
        session.close()


@app.patch("/projects/{project_id}")
async def update_project(project_id: str, req: ProjectUpdateRequest, request: Request):
    """
    Update project metadata (rename, archive, change slug). Requires owner role.
    When name changes, slug auto-updates and directory is renamed on disk.
    """
    from cortex.user_jail import rename_project_dir

    user = request.state.user
    require_project_access(project_id, user, min_role="owner")

    session = SessionLocal()
    try:
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        old_slug = project.slug

        if req.name is not None:
            project.name = req.name
            # Auto-update slug from name (unless an explicit slug was provided)
            if req.slug is None:
                new_slug = _slugify(req.name)
                new_slug = _dedup_slug(session, user.id, new_slug, exclude_project_id=project_id)
                project.slug = new_slug
            # Also update name in project_access for consistency
            access = session.execute(
                select(ProjectAccess)
                .where(ProjectAccess.project_id == project_id)
                .where(ProjectAccess.user_id == user.id)
            ).scalar_one_or_none()
            if access:
                access.project_name = req.name

        if req.slug is not None:
            # Explicit slug change
            slug_clean = req.slug.strip().lower()
            slug_clean = re.sub(r"[^a-z0-9_-]", "-", slug_clean).strip("-")
            if not slug_clean:
                raise HTTPException(status_code=422, detail="Invalid slug")
            slug_clean = _dedup_slug(session, user.id, slug_clean, exclude_project_id=project_id)
            project.slug = slug_clean

        if req.is_archived is not None:
            project.is_archived = req.is_archived

        project.updated_at = datetime.datetime.utcnow()

        # Rename directory on disk if slug changed
        new_slug = project.slug
        if old_slug and new_slug and old_slug != new_slug and user.username:
            try:
                rename_project_dir(user.username, old_slug, new_slug)
                # Update nextflow_work_dir in dogme_jobs
                from cortex.config import AGOUTIC_DATA
                old_prefix = str(AGOUTIC_DATA / "users" / user.username / old_slug)
                new_prefix = str(AGOUTIC_DATA / "users" / user.username / new_slug)
                try:
                    session.execute(
                        text(
                            "UPDATE dogme_jobs SET nextflow_work_dir = "
                            "REPLACE(nextflow_work_dir, :old, :new) "
                            "WHERE nextflow_work_dir LIKE :pattern"
                        ),
                        {"old": old_prefix, "new": new_prefix, "pattern": old_prefix + "%"},
                    )
                except Exception:
                    pass  # dogme_jobs may not be in this DB
            except PermissionError as e:
                raise HTTPException(status_code=409, detail=str(e))

        session.commit()

        logger.info("Project updated", project_id=project_id, slug=new_slug, user=user.email)
        return {"status": "ok", "id": project_id, "slug": new_slug}
    finally:
        session.close()


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
    """Record that user accessed a project. Requires existing access."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")
    
    session = SessionLocal()
    try:
        await track_project_access(session, user.id, project_id, project_name)
        return {"status": "ok"}
    finally:
        session.close()

# =============================================================================
# PROJECT DASHBOARD ENDPOINTS
# =============================================================================

@app.get("/projects/{project_id}/stats")
async def get_project_stats(project_id: str, request: Request):
    """Get detailed stats for a project: job count, file count, disk usage."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = SessionLocal()
    try:
        from cortex.config import AGOUTIC_DATA

        # Get project info
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        # Count blocks (messages)
        block_count = session.execute(
            select(func.count(ProjectBlock.id)).where(ProjectBlock.project_id == project_id)
        ).scalar() or 0

        # Count conversations
        conv_count = session.execute(
            select(func.count(Conversation.id)).where(Conversation.project_id == project_id)
        ).scalar() or 0

        # Query dogme_jobs for this project (shared DB, raw SQL)
        jobs_result = session.execute(text(
            "SELECT run_uuid, sample_name, mode, status, submitted_at, "
            "completed_at, nextflow_work_dir, user_id "
            "FROM dogme_jobs WHERE project_id = :pid ORDER BY submitted_at DESC"
        ), {"pid": project_id}).fetchall()

        jobs = []
        total_completed = 0
        total_failed = 0
        total_running = 0
        for row in jobs_result:
            status = row[3] or "UNKNOWN"
            if status == "COMPLETED":
                total_completed += 1
            elif status == "FAILED":
                total_failed += 1
            elif status == "RUNNING":
                total_running += 1
            jobs.append({
                "run_uuid": row[0],
                "sample_name": row[1],
                "mode": row[2],
                "status": status,
                "submitted_at": str(row[4]) if row[4] else None,
                "completed_at": str(row[5]) if row[5] else None,
                "work_dir": row[6],
            })

        # Calculate disk usage — scan actual work directories from dogme_jobs
        # (handles both jailed paths and legacy launchpad_work paths)
        disk_bytes = 0
        file_count = 0
        scanned_dirs = set()

        # 1. Check user project directory (slug-based or legacy UUID)
        project_dir = _resolve_project_dir(session, user, project_id)
        if project_dir.exists():
            scanned_dirs.add(str(project_dir))
            for f in project_dir.rglob("*"):
                if f.is_file():
                    try:
                        disk_bytes += f.stat().st_size
                        file_count += 1
                    except OSError:
                        pass

        # 2. Also scan work directories recorded in dogme_jobs (covers legacy paths)
        for job in jobs:
            wdir = job.get("work_dir")
            if wdir and wdir not in scanned_dirs:
                wpath = Path(wdir)
                if wpath.exists():
                    scanned_dirs.add(wdir)
                    for f in wpath.rglob("*"):
                        if f.is_file():
                            try:
                                disk_bytes += f.stat().st_size
                                file_count += 1
                            except OSError:
                                pass

        # Token usage for this project
        token_row = session.execute(
            text("""
                SELECT
                    COALESCE(SUM(cm.prompt_tokens), 0)     AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)      AS total_tokens
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.project_id = :pid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
            """),
            {"pid": project_id}
        ).fetchone()

        return {
            "project_id": project_id,
            "name": project.name if project else project_id,
            "is_archived": project.is_archived if project else False,
            "created_at": str(project.created_at) if project else None,
            "message_count": block_count,
            "conversation_count": conv_count,
            "jobs": jobs,
            "job_count": len(jobs),
            "completed_count": total_completed,
            "failed_count": total_failed,
            "running_count": total_running,
            "disk_usage_bytes": disk_bytes,
            "disk_usage_mb": round(disk_bytes / (1024 * 1024), 2),
            "file_count": file_count,
            "token_usage": {
                "prompt_tokens": token_row[0] if token_row else 0,
                "completion_tokens": token_row[1] if token_row else 0,
                "total_tokens": token_row[2] if token_row else 0,
            },
        }
    finally:
        session.close()


@app.get("/projects/{project_id}/files")
async def list_project_files(project_id: str, request: Request):
    """List all files in a project — checks both jailed dir and legacy work dirs."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    from cortex.config import AGOUTIC_DATA

    files = []
    total_size = 0
    scanned_dirs = set()

    def _scan_dir(base_dir: Path, label: str = ""):
        nonlocal total_size
        if not base_dir.exists() or str(base_dir) in scanned_dirs:
            return
        scanned_dirs.add(str(base_dir))
        for f in sorted(base_dir.rglob("*")):
            if f.is_file():
                try:
                    size = f.stat().st_size
                    total_size += size
                    files.append({
                        "path": str(f.relative_to(base_dir)),
                        "name": f.name,
                        "size_bytes": size,
                        "extension": f.suffix,
                        "modified": str(datetime.datetime.fromtimestamp(f.stat().st_mtime)),
                        "source": label,
                    })
                except OSError:
                    pass

    # 1. User project directory (slug-based or legacy UUID)
    session_files = SessionLocal()
    try:
        project_dir = _resolve_project_dir(session_files, user, project_id)
    finally:
        session_files.close()
    _scan_dir(project_dir, "project")

    # 2. Legacy work directories from dogme_jobs
    session = SessionLocal()
    try:
        rows = session.execute(
            text("SELECT run_uuid, nextflow_work_dir FROM dogme_jobs WHERE project_id = :pid"),
            {"pid": project_id}
        ).fetchall()
        for row in rows:
            wdir = row[1]
            if wdir:
                _scan_dir(Path(wdir), f"job:{row[0][:8]}")
    except Exception:
        pass
    finally:
        session.close()

    return {
        "files": files,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "file_count": len(files),
    }


# =============================================================================
# FILE DOWNLOAD & UPLOAD
# =============================================================================
# Downloads ENCODE files (via get_file_url + httpx), arbitrary URLs,
# and accepts local file uploads — all into the user's project data/ dir.

# In-memory tracking of active downloads (keyed by download_id)
_active_downloads: dict[str, dict] = {}


class DownloadRequest(BaseModel):
    """Request to download one or more files into the project."""
    source: str  # "encode" or "url"
    files: list[dict]  # [{"url": str, "filename": str, "size_bytes": int | None}]
    subfolder: Optional[str] = "data"  # relative to project root


@app.post("/projects/{project_id}/downloads")
async def initiate_download(project_id: str, req: DownloadRequest, request: Request):
    """Start downloading files into the project's data directory.

    Creates a DOWNLOAD_TASK block and kicks off a background download task.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    if not req.files:
        raise HTTPException(status_code=422, detail="No files to download")

    session = SessionLocal()
    try:
        project_dir = _resolve_project_dir(session, user, project_id)
        target_dir = project_dir / (req.subfolder or "data")
        target_dir.mkdir(parents=True, exist_ok=True)

        download_id = str(uuid.uuid4())

        # Create a DOWNLOAD_TASK block
        file_summaries = [
            {"filename": f.get("filename", "unknown"), "url": f["url"],
             "size_bytes": f.get("size_bytes")}
            for f in req.files
        ]
        block = _create_block_internal(
            session,
            project_id,
            "DOWNLOAD_TASK",
            {
                "download_id": download_id,
                "source": req.source,
                "files": file_summaries,
                "target_dir": str(target_dir),
                "downloaded": 0,
                "total_files": len(req.files),
                "bytes_downloaded": 0,
                "status": "RUNNING",
            },
            status="RUNNING",
            owner_id=user.id,
        )

        _active_downloads[download_id] = {
            "block_id": block.id,
            "project_id": project_id,
            "cancelled": False,
        }

        # Fire and forget background download
        asyncio.create_task(
            _download_files_background(
                download_id=download_id,
                project_id=project_id,
                block_id=block.id,
                owner_id=user.id,
                files=req.files,
                target_dir=target_dir,
            )
        )

        return {
            "download_id": download_id,
            "block_id": block.id,
            "target_dir": str(target_dir),
            "file_count": len(req.files),
        }
    finally:
        session.close()


@app.get("/projects/{project_id}/downloads/{download_id}")
async def get_download_status(project_id: str, download_id: str, request: Request):
    """Poll download progress."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    info = _active_downloads.get(download_id)
    if not info:
        raise HTTPException(status_code=404, detail="Download not found")

    session = SessionLocal()
    try:
        block = session.query(ProjectBlock).filter(ProjectBlock.id == info["block_id"]).first()
        if not block:
            raise HTTPException(status_code=404, detail="Download block not found")
        return get_block_payload(block)
    finally:
        session.close()


@app.delete("/projects/{project_id}/downloads/{download_id}")
async def cancel_download(project_id: str, download_id: str, request: Request):
    """Cancel an in-progress download."""
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    info = _active_downloads.get(download_id)
    if not info:
        raise HTTPException(status_code=404, detail="Download not found")
    info["cancelled"] = True
    return {"status": "cancelling", "download_id": download_id}


async def _download_files_background(
    download_id: str,
    project_id: str,
    block_id: str,
    owner_id: str,
    files: list[dict],
    target_dir: Path,
):
    """Background task that streams files from URLs into the target directory.

    Provides per-file byte-level progress updates so the UI can render a
    progress bar.  Expected total size is computed from file_spec['size_bytes']
    when available.
    """
    session = SessionLocal()
    downloaded_files = []
    total_bytes = 0
    # Expected total bytes across all files (may be 0 if sizes are unknown)
    expected_total_bytes = sum(f.get("size_bytes") or 0 for f in files)
    # Throttle progress-block writes to avoid DB spam (update at most every 0.5s)
    _last_progress_write = 0.0
    import time as _time

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(600.0)) as client:
            for i, file_spec in enumerate(files):
                dl_info = _active_downloads.get(download_id, {})
                if dl_info.get("cancelled"):
                    _update_download_block(session, block_id, {
                        "status": "CANCELLED",
                        "downloaded": i,
                        "message": "Download cancelled by user",
                    }, status="FAILED")
                    return

                url = file_spec["url"]
                filename = file_spec.get("filename") or url.rsplit("/", 1)[-1].split("?")[0] or f"file_{i}"
                dest = target_dir / filename
                file_expected = file_spec.get("size_bytes") or 0

                try:
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        # Use Content-Length header if per-file size is unknown
                        if not file_expected:
                            _cl = resp.headers.get("content-length")
                            if _cl and _cl.isdigit():
                                file_expected = int(_cl)
                        file_bytes = 0
                        with open(dest, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
                                file_bytes += len(chunk)
                                total_bytes += len(chunk)

                                # Throttled per-chunk progress update
                                _now = _time.monotonic()
                                if _now - _last_progress_write >= 0.5:
                                    _last_progress_write = _now
                                    _update_download_block(session, block_id, {
                                        "downloaded": i,
                                        "bytes_downloaded": total_bytes,
                                        "expected_total_bytes": expected_total_bytes,
                                        "current_file": filename,
                                        "current_file_bytes": file_bytes,
                                        "current_file_expected": file_expected,
                                    })

                    downloaded_files.append({
                        "filename": filename,
                        "size_bytes": file_bytes,
                        "path": str(dest),
                    })

                    # Per-file completion update
                    _update_download_block(session, block_id, {
                        "downloaded": i + 1,
                        "bytes_downloaded": total_bytes,
                        "expected_total_bytes": expected_total_bytes,
                        "current_file": filename,
                        "current_file_bytes": file_bytes,
                        "current_file_expected": file_expected,
                    })

                except Exception as e:
                    logger.error("Download failed for file", url=url, error=str(e))
                    downloaded_files.append({
                        "filename": filename,
                        "error": str(e),
                    })

        # All done — update block to DONE
        _update_download_block(session, block_id, {
            "status": "DONE",
            "downloaded": len(files),
            "bytes_downloaded": total_bytes,
            "downloaded_files": downloaded_files,
        }, status="DONE")

        # Generate post-download suggestions
        await _post_download_suggestions(session, project_id, owner_id, downloaded_files, target_dir)

    except Exception as e:
        logger.error("Download background task failed", download_id=download_id, error=str(e))
        _update_download_block(session, block_id, {
            "status": "FAILED",
            "message": str(e),
            "downloaded_files": downloaded_files,
        }, status="FAILED")
    finally:
        _active_downloads.pop(download_id, None)
        session.close()


def _update_download_block(session, block_id: str, updates: dict, status: str | None = None):
    """Update a DOWNLOAD_TASK block's payload (and optionally status)."""
    block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
    if not block:
        return
    payload = get_block_payload(block)
    payload.update(updates)
    block.payload_json = json.dumps(payload)
    if status:
        block.status = status
    session.commit()


async def _post_download_suggestions(
    session, project_id: str, owner_id: str,
    downloaded_files: list[dict], target_dir: Path,
):
    """After downloads complete, create an AGENT_PLAN block with smart suggestions."""
    # Categorize files by extension
    seq_files = []  # .pod5, .bam, .fastq, .fastq.gz, .fq, .fq.gz
    table_files = []  # .csv, .tsv, .bed, .bedgraph
    other_files = []

    seq_exts = {".pod5", ".bam", ".fastq", ".fq", ".fast5"}
    table_exts = {".csv", ".tsv", ".bed", ".bedgraph", ".txt"}

    for f in downloaded_files:
        if f.get("error"):
            continue
        fname = f.get("filename", "")
        # Handle .gz extensions
        ext = Path(fname).suffix.lower()
        if ext == ".gz":
            ext = Path(fname).stem
            ext = Path(ext).suffix.lower()

        if ext in seq_exts:
            seq_files.append(f)
        elif ext in table_exts:
            table_files.append(f)
        else:
            other_files.append(f)

    # Build suggestion markdown
    lines = [f"**Download Complete** — {len(downloaded_files)} file(s) saved to `{target_dir.name}/`\n"]

    if seq_files:
        fnames = ", ".join(f.get("filename", "?") for f in seq_files)
        lines.append(f"**Sequencing files detected**: {fnames}")
        lines.append("These look like sequencing data. I can help you run the **Dogme pipeline** for DNA, RNA, or cDNA analysis.")
        lines.append("Would you like to set up a pipeline run?\n")
        lines.append("[[SKILL_SWITCH_TO: analyze_local_sample]]")

    if table_files:
        fnames = ", ".join(f.get("filename", "?") for f in table_files)
        lines.append(f"**Tabular files detected**: {fnames}")
        lines.append("I can load these into a dataframe for exploration and analysis.")
        lines.append("Would you like me to read and summarize them?\n")
        lines.append("[[SKILL_SWITCH_TO: analyze_job_results]]")

    if other_files and not seq_files and not table_files:
        fnames = ", ".join(f.get("filename", "?") for f in other_files)
        lines.append(f"**Files**: {fnames}")
        lines.append("Let me know what you'd like to do with these files.")

    suggestion_md = "\n".join(lines)

    _create_block_internal(
        session,
        project_id,
        "AGENT_PLAN",
        {
            "markdown": suggestion_md,
            "skill": "download_files",
            "model": "system",
        },
        status="DONE",
        owner_id=owner_id,
    )


# --- File Upload ---

@app.post("/projects/{project_id}/upload")
async def upload_file(project_id: str, request: Request):
    """Upload files into the project's data/ directory.

    Accepts multipart/form-data with field name 'files'.
    """
    from fastapi import UploadFile
    import aiofiles

    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    session = SessionLocal()
    try:
        project_dir = _resolve_project_dir(session, user, project_id)
        data_dir = project_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
    finally:
        session.close()

    form = await request.form()
    uploaded = []

    for key in form:
        file = form[key]
        if not hasattr(file, "read"):
            continue  # skip non-file fields
        filename = getattr(file, "filename", key)
        # Sanitize filename
        safe_name = re.sub(r"[^\w.\-]", "_", filename)
        dest = data_dir / safe_name

        content = await file.read()
        with open(dest, "wb") as f:
            f.write(content)

        uploaded.append({
            "filename": safe_name,
            "size_bytes": len(content),
            "path": str(dest),
        })

    if not uploaded:
        raise HTTPException(status_code=422, detail="No files uploaded")

    # Create a system message about the upload to trigger agent suggestions
    session2 = SessionLocal()
    try:
        await _post_download_suggestions(
            session2, project_id, user.id, uploaded, data_dir,
        )
    finally:
        session2.close()

    return {
        "uploaded": uploaded,
        "count": len(uploaded),
        "target_dir": str(data_dir),
    }


@app.get("/user/token-usage")
async def get_user_token_usage(request: Request):
    """Return token usage for the authenticated user.

    Response shape:
      {
        "lifetime": {prompt_tokens, completion_tokens, total_tokens},
        "by_conversation": [ {conversation_id, project_id, title, prompt_tokens,
                               completion_tokens, total_tokens, last_message_at} ],
        "daily": [ {date, prompt_tokens, completion_tokens, total_tokens} ],
        "tracking_since": ISO string of earliest tracked message (or null)
      }
    """
    user = request.state.user
    session = SessionLocal()
    try:
        # Lifetime totals
        lifetime_row = session.execute(
            text("""
                SELECT
                    COALESCE(SUM(cm.prompt_tokens), 0)     AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)      AS total_tokens
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
            """),
            {"uid": user.id}
        ).fetchone()

        # Per-conversation totals
        conv_rows = session.execute(
            text("""
                SELECT
                    c.id                                        AS conversation_id,
                    c.project_id,
                    c.title,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens,
                    MAX(cm.created_at)                          AS last_message_at
                FROM conversations c
                JOIN conversation_messages cm ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
                GROUP BY c.id, c.project_id, c.title
                ORDER BY last_message_at DESC
            """),
            {"uid": user.id}
        ).fetchall()

        # Daily time-series (SQLite date() function)
        daily_rows = session.execute(
            text("""
                SELECT
                    DATE(cm.created_at)                         AS date,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
                GROUP BY DATE(cm.created_at)
                ORDER BY DATE(cm.created_at) ASC
            """),
            {"uid": user.id}
        ).fetchall()

        # Earliest tracked message
        tracking_since_row = session.execute(
            text("""
                SELECT MIN(cm.created_at)
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
            """),
            {"uid": user.id}
        ).fetchone()

        return {
            "lifetime": {
                "prompt_tokens": lifetime_row[0] if lifetime_row else 0,
                "completion_tokens": lifetime_row[1] if lifetime_row else 0,
                "total_tokens": lifetime_row[2] if lifetime_row else 0,
            },
            "by_conversation": [
                {
                    "conversation_id": r[0],
                    "project_id": r[1],
                    "title": r[2],
                    "prompt_tokens": r[3],
                    "completion_tokens": r[4],
                    "total_tokens": r[5],
                    "last_message_at": str(r[6]) if r[6] else None,
                }
                for r in conv_rows
            ],
            "daily": [
                {
                    "date": str(r[0]),
                    "prompt_tokens": r[1],
                    "completion_tokens": r[2],
                    "total_tokens": r[3],
                }
                for r in daily_rows
            ],
            "tracking_since": str(tracking_since_row[0]) if tracking_since_row and tracking_since_row[0] else None,
            "token_limit": user.token_limit,
        }
    finally:
        session.close()


@app.get("/user/disk-usage")
async def get_user_disk_usage(request: Request):
    """Get total disk usage for the current user across all projects.
    Scans both jailed user directories and legacy job work directories."""
    user = request.state.user

    from cortex.config import AGOUTIC_DATA

    session = SessionLocal()
    try:
        # Get all project IDs for this user
        access_rows = session.execute(
            select(ProjectAccess.project_id)
            .where(ProjectAccess.user_id == user.id)
        ).all()
        project_ids = [r[0] for r in access_rows]

        project_usage = []
        total_bytes = 0

        for pid in project_ids:
            proj_bytes = 0
            file_count = 0
            scanned_dirs = set()

            # 1. User project directory (slug-based or legacy UUID)
            project_dir = _resolve_project_dir(session, user, pid)
            if project_dir.exists():
                scanned_dirs.add(str(project_dir))
                for f in project_dir.rglob("*"):
                    if f.is_file():
                        try:
                            proj_bytes += f.stat().st_size
                            file_count += 1
                        except OSError:
                            pass

            # 2. Legacy work dirs from dogme_jobs
            try:
                job_rows = session.execute(
                    text("SELECT nextflow_work_dir FROM dogme_jobs WHERE project_id = :pid"),
                    {"pid": pid}
                ).fetchall()
                for row in job_rows:
                    wdir = row[0]
                    if wdir and wdir not in scanned_dirs:
                        wpath = Path(wdir)
                        if wpath.exists():
                            scanned_dirs.add(wdir)
                            for f in wpath.rglob("*"):
                                if f.is_file():
                                    try:
                                        proj_bytes += f.stat().st_size
                                        file_count += 1
                                    except OSError:
                                        pass
            except Exception:
                pass

            total_bytes += proj_bytes
            project_usage.append({
                "project_id": pid,
                "size_bytes": proj_bytes,
                "size_mb": round(proj_bytes / (1024 * 1024), 2),
                "file_count": file_count,
            })

        return {
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "projects": project_usage,
        }
    finally:
        session.close()


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    """
    Soft-delete a project (archives it). Only the owner can delete.
    Does NOT remove files — use archive to hide from listing.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="owner")

    session = SessionLocal()
    try:
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project.is_archived = True
        project.updated_at = datetime.datetime.utcnow()
        session.commit()

        logger.info("Project archived", project_id=project_id, user=user.email)
        return {"status": "archived", "id": project_id}
    finally:
        session.close()


@app.delete("/projects/{project_id}/permanent")
async def delete_project_permanent(project_id: str, request: Request):
    """
    Permanently delete a project and all associated data:
    conversations, messages, blocks, job_results, project_access,
    dogme_jobs rows, and the on-disk directory.
    Requires owner role.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="owner")

    session = SessionLocal()
    try:
        # 1. Delete conversation messages (via conversation ids)
        conv_ids = [r[0] for r in session.execute(
            select(Conversation.id).where(Conversation.project_id == project_id)
        ).all()]
        if conv_ids:
            session.execute(
                ConversationMessage.__table__.delete().where(
                    ConversationMessage.conversation_id.in_(conv_ids)
                )
            )
            # Delete job_results linked to those conversations
            session.execute(
                JobResult.__table__.delete().where(
                    JobResult.conversation_id.in_(conv_ids)
                )
            )

        # 2. Delete conversations
        session.execute(
            Conversation.__table__.delete().where(Conversation.project_id == project_id)
        )

        # 3. Delete project blocks
        session.execute(
            ProjectBlock.__table__.delete().where(ProjectBlock.project_id == project_id)
        )

        # 4. Delete dogme_jobs rows (cross-server table)
        try:
            session.execute(
                text("DELETE FROM dogme_jobs WHERE project_id = :pid"),
                {"pid": project_id}
            )
        except Exception:
            pass  # Table may not exist yet

        # 5. Delete project_access records
        session.execute(
            ProjectAccess.__table__.delete().where(ProjectAccess.project_id == project_id)
        )

        # 6. Delete the project record itself
        session.execute(
            Project.__table__.delete().where(Project.id == project_id)
        )

        session.commit()

        # 7. Remove on-disk directory (best-effort)
        import shutil
        project_dir = _resolve_project_dir(session, user, project_id)
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        # Also try legacy UUID path
        legacy_dir = AGOUTIC_DATA / "users" / str(user.id) / project_id
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir, ignore_errors=True)

        logger.info("Project permanently deleted", project_id=project_id, user=user.email)
        return {"status": "deleted", "id": project_id}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error("Failed to delete project", project_id=project_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    finally:
        session.close()


# =============================================================================
# ANALYZER ANALYSIS PROXY ENDPOINTS
# =============================================================================
# These proxy endpoints allow the UI to access Analyzer analysis tools
# via MCP over HTTP. The Analyzer MCP server must be running.

@app.get("/analysis/jobs/{run_uuid}/summary")
async def get_job_analysis_summary(run_uuid: str, request: Request):
    """
    Get comprehensive analysis summary for a completed job.
    Proxies to Analyzer analysis engine via MCP.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)
    return await _call_analyzer_tool("get_analysis_summary", run_uuid=run_uuid)

@app.get("/analysis/jobs/{run_uuid}/files")
async def list_job_files(run_uuid: str, extensions: Optional[str] = None, request: Request = None):
    """
    List all files for a completed job.
    Query params:
      - extensions: Comma-separated list (e.g., ".csv,.txt")
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)
    kwargs = {"run_uuid": run_uuid}
    if extensions:
        kwargs["extensions"] = extensions
    return await _call_analyzer_tool("list_job_files", **kwargs)

@app.get("/analysis/jobs/{run_uuid}/files/categorize")
async def categorize_job_files(run_uuid: str, request: Request):
    """
    Categorize job files by type (csv, txt, bed, other).
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)
    return await _call_analyzer_tool("categorize_job_files", run_uuid=run_uuid)

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
    require_run_uuid_access(run_uuid, user)
    return await _call_analyzer_tool(
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
    Returns column stats, dtypes, and row data.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)
    return await _call_analyzer_tool(
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
    require_run_uuid_access(run_uuid, user)
    return await _call_analyzer_tool(
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
    Proxy file download from Analyzer REST API.
    Streams the file through Cortex so the UI never contacts Analyzer directly.
    """
    user = request.state.user
    require_run_uuid_access(run_uuid, user)
    from starlette.responses import StreamingResponse
    try:
        analyzer_rest = SERVICE_REGISTRY["analyzer"]["rest_url"]
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(
                f"{analyzer_rest}/analysis/files/download",
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


async def _call_analyzer_tool(tool_name: str, **kwargs):
    """Helper to call a Analyzer MCP tool via the generic MCP HTTP client."""
    try:
        analyzer_url = get_service_url("analyzer")
        client = MCPHttpClient(name="analyzer", base_url=analyzer_url)
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
        raise HTTPException(status_code=500, detail=f"Analyzer error: {str(e)}")

async def _call_launchpad_tool(tool_name: str, **kwargs):
    """Helper to call a Launchpad MCP tool via the generic MCP HTTP client."""
    try:
        launchpad_url = get_service_url("launchpad")
        client = MCPHttpClient(name="launchpad", base_url=launchpad_url)
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
        raise HTTPException(status_code=500, detail=f"Launchpad error: {str(e)}")