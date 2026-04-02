"""
Memory API routes — CRUD endpoints for the memory system.

Thin handlers per cortex/app.py guardrails; all logic lives
in cortex/memory_service.py.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional

from cortex.db import SessionLocal
from cortex.dependencies import require_project_access
from cortex.memory_service import (
    create_memory,
    delete_memory,
    restore_memory,
    pin_memory,
    update_memory,
    upgrade_to_global,
    list_memories,
    search_memories,
    get_memory_context,
    memory_to_dict,
)
from cortex.schemas import MemoryCreate, MemoryOut, MemoryUpdate, MemoryListOut
from common.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("")
async def list_memories_endpoint(
    request: Request,
    project_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_global: bool = Query(True),
    include_deleted: bool = Query(False),
    pinned_only: bool = Query(False),
    show_all: bool = Query(False),
    limit: int = Query(100, le=500),
):
    """List memories for the current user, optionally scoped to a project."""
    user = request.state.user
    session = SessionLocal()
    try:
        mems = list_memories(
            session, user.id,
            project_id=project_id,
            category=category,
            include_global=include_global,
            include_deleted=include_deleted,
            pinned_only=pinned_only,
            show_all=show_all,
            limit=limit,
        )
        return MemoryListOut(
            memories=[MemoryOut(**memory_to_dict(m)) for m in mems],
            total=len(mems),
        )
    finally:
        session.close()


@router.post("", status_code=201)
async def create_memory_endpoint(
    request: Request,
    body: MemoryCreate,
):
    """Create a new memory entry."""
    user = request.state.user
    session = SessionLocal()
    try:
        mem = create_memory(
            session,
            user_id=user.id,
            content=body.content,
            category=body.category,
            project_id=body.project_id,
            structured_data=body.structured_data,
            tags=body.tags,
            is_pinned=body.is_pinned,
        )
        return MemoryOut(**memory_to_dict(mem))
    finally:
        session.close()


@router.delete("/{memory_id}")
async def delete_memory_endpoint(
    request: Request,
    memory_id: str,
):
    """Soft-delete a memory entry."""
    user = request.state.user
    session = SessionLocal()
    try:
        ok = delete_memory(session, memory_id, user.id)
        if not ok:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"status": "deleted", "id": memory_id}
    finally:
        session.close()


@router.patch("/{memory_id}")
async def update_memory_endpoint(
    request: Request,
    memory_id: str,
    body: MemoryUpdate,
):
    """Update a memory (content, tags, or pin status)."""
    user = request.state.user
    session = SessionLocal()
    try:
        # Handle pin/unpin
        if body.is_pinned is not None:
            pin_memory(session, memory_id, user.id, pinned=body.is_pinned)

        # Handle content/tags update
        if body.content is not None or body.tags is not None:
            mem = update_memory(
                session, memory_id, user.id,
                content=body.content, tags=body.tags,
            )
            if not mem:
                raise HTTPException(status_code=404, detail="Memory not found")
            return MemoryOut(**memory_to_dict(mem))

        # Pin-only update — re-fetch
        from sqlalchemy import select
        from cortex.models import Memory
        mem = session.execute(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user.id)
        ).scalar_one_or_none()
        if not mem:
            raise HTTPException(status_code=404, detail="Memory not found")
        return MemoryOut(**memory_to_dict(mem))
    finally:
        session.close()


@router.post("/{memory_id}/restore")
async def restore_memory_endpoint(
    request: Request,
    memory_id: str,
):
    """Restore a soft-deleted memory."""
    user = request.state.user
    session = SessionLocal()
    try:
        ok = restore_memory(session, memory_id, user.id)
        if not ok:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"status": "restored", "id": memory_id}
    finally:
        session.close()


@router.post("/{memory_id}/upgrade-to-global")
async def upgrade_to_global_endpoint(
    request: Request,
    memory_id: str,
):
    """Promote a project-scoped memory to global."""
    user = request.state.user
    session = SessionLocal()
    try:
        mem = upgrade_to_global(session, memory_id, user.id)
        if not mem:
            raise HTTPException(
                status_code=400,
                detail="Memory not found, already global, or is an unnamed dataframe (name it first with /remember-df DFn as <name>)",
            )
        return MemoryOut(**memory_to_dict(mem))
    finally:
        session.close()


@router.get("/context")
async def memory_context_endpoint(
    request: Request,
    project_id: Optional[str] = Query(None),
):
    """Get the formatted memory context string (for debugging)."""
    user = request.state.user
    session = SessionLocal()
    try:
        ctx = get_memory_context(session, user.id, project_id)
        return {"context": ctx}
    finally:
        session.close()


@router.get("/search")
async def search_memories_endpoint(
    request: Request,
    q: str = Query(..., min_length=1),
    project_id: Optional[str] = Query(None),
):
    """Search memories by content text."""
    user = request.state.user
    session = SessionLocal()
    try:
        mems = search_memories(session, user.id, q, project_id=project_id)
        return MemoryListOut(
            memories=[MemoryOut(**memory_to_dict(m)) for m in mems],
            total=len(mems),
        )
    finally:
        session.close()
