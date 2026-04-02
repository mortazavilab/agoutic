"""
Memory service — CRUD, querying, auto-capture, and context injection helpers.

Extracted as a focused module per cortex/app.py guardrails.
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.orm import Session

from cortex.models import Memory, UserFile, ProjectBlock
from common.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Valid categories and sources
# ---------------------------------------------------------------------------
VALID_CATEGORIES = frozenset({
    "result", "sample_annotation", "pipeline_step",
    "preference", "finding", "custom",
})
VALID_SOURCES = frozenset({
    "user_manual", "auto_step", "auto_result", "system",
})

# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_memory(
    db: Session,
    *,
    user_id: str,
    content: str,
    category: str = "custom",
    project_id: str | None = None,
    structured_data: dict | None = None,
    source: str = "user_manual",
    related_block_id: str | None = None,
    related_file_id: str | None = None,
    tags: dict | None = None,
    is_pinned: bool = False,
) -> Memory:
    """Create a new memory entry."""
    if category not in VALID_CATEGORIES:
        category = "custom"
    if source not in VALID_SOURCES:
        source = "user_manual"

    mem = Memory(
        id=str(uuid.uuid4()),
        user_id=user_id,
        project_id=project_id,
        category=category,
        content=content,
        structured_data=json.dumps(structured_data) if structured_data else None,
        source=source,
        is_pinned=is_pinned,
        related_block_id=related_block_id,
        related_file_id=related_file_id,
        tags_json=json.dumps(tags) if tags else None,
    )
    db.add(mem)
    db.commit()
    db.refresh(mem)
    logger.info("Memory created", memory_id=mem.id, category=category, source=source)
    return mem


def delete_memory(db: Session, memory_id: str, user_id: str) -> bool:
    """Soft-delete a memory (set is_deleted=True)."""
    mem = db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not mem:
        return False
    mem.is_deleted = True
    mem.deleted_at = datetime.datetime.utcnow()
    db.commit()
    logger.info("Memory soft-deleted", memory_id=memory_id)
    return True


def restore_memory(db: Session, memory_id: str, user_id: str) -> bool:
    """Undo soft-delete of a memory."""
    mem = db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not mem:
        return False
    mem.is_deleted = False
    mem.deleted_at = None
    db.commit()
    logger.info("Memory restored", memory_id=memory_id)
    return True


def pin_memory(db: Session, memory_id: str, user_id: str, pinned: bool = True) -> bool:
    """Pin or unpin a memory."""
    mem = db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not mem:
        return False
    mem.is_pinned = pinned
    db.commit()
    return True


def update_memory(
    db: Session,
    memory_id: str,
    user_id: str,
    *,
    content: str | None = None,
    tags: dict | None = None,
) -> Memory | None:
    """Update content or tags of a memory."""
    mem = db.execute(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.user_id == user_id,
            Memory.is_deleted == False,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not mem:
        return None
    if content is not None:
        mem.content = content
    if tags is not None:
        mem.tags_json = json.dumps(tags)
    db.commit()
    db.refresh(mem)
    return mem


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


def list_memories(
    db: Session,
    user_id: str,
    *,
    project_id: str | None = None,
    category: str | None = None,
    include_global: bool = True,
    include_deleted: bool = False,
    pinned_only: bool = False,
    show_all: bool = False,
    limit: int = 100,
) -> list[Memory]:
    """Return memories visible in this context (project-scoped + user-global)."""
    conditions = [Memory.user_id == user_id]

    if not include_deleted:
        conditions.append(Memory.is_deleted == False)  # noqa: E712

    # Scope: show_all > project-specific + optionally user-global > global-only
    if show_all:
        pass  # No project filter — return all user memories
    elif project_id is not None:
        if include_global:
            conditions.append(
                or_(Memory.project_id == project_id, Memory.project_id == None)  # noqa: E711
            )
        else:
            conditions.append(Memory.project_id == project_id)
    else:
        # No project context — only global memories
        conditions.append(Memory.project_id == None)  # noqa: E711

    if category:
        conditions.append(Memory.category == category)

    if pinned_only:
        conditions.append(Memory.is_pinned == True)  # noqa: E712

    stmt = (
        select(Memory)
        .where(and_(*conditions))
        .order_by(desc(Memory.is_pinned), desc(Memory.created_at))
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def search_memories(
    db: Session,
    user_id: str,
    query_text: str,
    *,
    project_id: str | None = None,
) -> list[Memory]:
    """Text search across memory content (LIKE-based for SQLite)."""
    conditions = [
        Memory.user_id == user_id,
        Memory.is_deleted == False,  # noqa: E712
        Memory.content.ilike(f"%{query_text}%"),
    ]
    if project_id is not None:
        conditions.append(
            or_(Memory.project_id == project_id, Memory.project_id == None)  # noqa: E711
        )
    stmt = (
        select(Memory)
        .where(and_(*conditions))
        .order_by(desc(Memory.created_at))
        .limit(50)
    )
    return list(db.execute(stmt).scalars().all())


def find_memory_by_content(
    db: Session,
    user_id: str,
    content_fragment: str,
    *,
    project_id: str | None = None,
) -> list[Memory]:
    """Find memories whose content matches a fragment (for NL deletion)."""
    return search_memories(db, user_id, content_fragment, project_id=project_id)


# ---------------------------------------------------------------------------
# Memory → LLM context
# ---------------------------------------------------------------------------


def get_memory_context(
    db: Session,
    user_id: str,
    project_id: str | None,
    *,
    token_budget: int = 800,
) -> str:
    """Build a formatted memory block for LLM context injection.

    Returns a string like:
        [MEMORY: Project notes]
        - ⭐ sample1 is an AD sample (sample_annotation, pinned)
        - ChIP-seq alignment for K562 completed (pipeline_step)
        [MEMORY: User preferences]
        - Always use hg38 reference genome (preference)

    Uses a dynamic budget: pinned always included, recent in full,
    older condensed.
    """
    memories = list_memories(
        db, user_id, project_id=project_id,
        include_global=True, limit=200,
    )
    if not memories:
        return ""

    # Separate into project and global
    project_mems = [m for m in memories if m.project_id is not None]
    global_mems = [m for m in memories if m.project_id is None]

    lines: list[str] = []
    budget_used = 0
    # Rough estimate: 4 chars ≈ 1 token
    chars_budget = token_budget * 4

    def _format_entry(m: Memory) -> str:
        pin = "⭐ " if m.is_pinned else ""
        return f"- {pin}{m.content} ({m.category})"

    def _add_section(title: str, mems: list[Memory]) -> None:
        nonlocal budget_used
        if not mems:
            return
        section_lines = [f"[MEMORY: {title}]"]
        now = datetime.datetime.utcnow()

        # Pinned first, always included
        pinned = [m for m in mems if m.is_pinned]
        unpinned = [m for m in mems if not m.is_pinned]

        for m in pinned:
            entry = _format_entry(m)
            budget_used += len(entry)
            section_lines.append(entry)

        # Recent (last 7 days) in full
        cutoff = now - datetime.timedelta(days=7)
        for m in unpinned:
            if budget_used >= chars_budget:
                remaining = len(unpinned) - len([
                    x for x in unpinned
                    if _parse_created_at(x) and _parse_created_at(x) >= cutoff
                ])
                if remaining > 0:
                    section_lines.append(f"- ... and {remaining} older entries")
                break
            created = _parse_created_at(m)
            if created and created >= cutoff:
                entry = _format_entry(m)
                budget_used += len(entry)
                section_lines.append(entry)
            else:
                # Older — one-line condensed
                short = m.content[:80] + ("..." if len(m.content) > 80 else "")
                entry = f"- {short} ({m.category})"
                budget_used += len(entry)
                section_lines.append(entry)

        lines.extend(section_lines)

    _add_section("Project notes", project_mems)
    _add_section("User preferences", global_mems)

    return "\n".join(lines)


def _parse_created_at(m: Memory) -> datetime.datetime | None:
    """Parse created_at into a naive datetime for comparison."""
    val = m.created_at
    if isinstance(val, datetime.datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, str):
        try:
            return datetime.datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Auto-capture helpers
# ---------------------------------------------------------------------------


def auto_capture_step(
    db: Session,
    *,
    user_id: str,
    project_id: str,
    step: dict,
    plan_payload: dict,
    block_id: str | None = None,
) -> Memory | None:
    """Auto-create a pipeline_step memory from a completed workflow step.

    Called after each successful step in a WORKFLOW_PLAN.
    Deduplicates by (project_id, category, related_block_id, step_id).
    """
    step_id = step.get("id", "")
    step_title = step.get("title", step.get("kind", "unknown"))
    sample_name = plan_payload.get("sample_name", "")

    # Dedup check
    if block_id and step_id:
        existing = db.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.project_id == project_id,
                Memory.category == "pipeline_step",
                Memory.related_block_id == block_id,
                Memory.is_deleted == False,  # noqa: E712
                Memory.structured_data.ilike(f'%"step_id": "{step_id}"%'),
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    content = f"Step '{step_title}' completed"
    if sample_name:
        content += f" for {sample_name}"

    structured = {
        "step_id": step_id,
        "step_kind": step.get("kind", ""),
        "plan_id": block_id,
        "sample_name": sample_name,
        "workflow_type": plan_payload.get("workflow_type", ""),
    }

    return create_memory(
        db,
        user_id=user_id,
        content=content,
        category="pipeline_step",
        project_id=project_id,
        structured_data=structured,
        source="auto_step",
        related_block_id=block_id,
    )


def auto_capture_result(
    db: Session,
    *,
    user_id: str,
    project_id: str,
    run_uuid: str,
    sample_name: str = "",
    workflow_type: str = "",
    work_directory: str = "",
    block_id: str | None = None,
) -> Memory | None:
    """Auto-create a result memory from a completed EXECUTION_JOB.

    Auto-pins final pipeline outputs.
    Deduplicates by (project_id, category, run_uuid).
    """
    # Dedup
    existing = db.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.project_id == project_id,
            Memory.category == "result",
            Memory.is_deleted == False,  # noqa: E712
            Memory.structured_data.ilike(f'%"run_uuid": "{run_uuid}"%'),
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    content = f"{workflow_type or 'Pipeline'} for {sample_name or 'unknown'} completed"
    if work_directory:
        content += f" — results in {work_directory}"

    structured = {
        "run_uuid": run_uuid,
        "work_directory": work_directory,
        "sample_name": sample_name,
        "workflow_type": workflow_type,
    }

    return create_memory(
        db,
        user_id=user_id,
        content=content,
        category="result",
        project_id=project_id,
        structured_data=structured,
        source="auto_result",
        related_block_id=block_id,
        is_pinned=True,  # Auto-pin final results
    )


# ---------------------------------------------------------------------------
# Sample annotation (dual-write)
# ---------------------------------------------------------------------------


def annotate_sample(
    db: Session,
    *,
    user_id: str,
    sample_name: str,
    annotations: dict[str, str],
    project_id: str | None = None,
) -> Memory | None:
    """Annotate a sample: update UserFile.tags_json + create audit memory.

    Finds the file by sample_name for the user and merges annotations
    into its tags_json.  Creates a memory entry for audit trail and
    LLM context injection.
    """
    # Find matching file
    conditions = [
        UserFile.user_id == user_id,
        UserFile.sample_name == sample_name,
    ]
    user_file = db.execute(
        select(UserFile).where(and_(*conditions))
    ).scalar_one_or_none()

    file_id = None
    if user_file:
        # Merge annotations into existing tags
        existing_tags = {}
        if user_file.tags_json:
            try:
                existing_tags = json.loads(user_file.tags_json)
            except (json.JSONDecodeError, TypeError):
                existing_tags = {}
        existing_tags.update(annotations)
        user_file.tags_json = json.dumps(existing_tags)
        file_id = user_file.id

    # Build content string
    ann_str = ", ".join(f"{k}={v}" for k, v in annotations.items())
    content = f"Sample '{sample_name}' annotated: {ann_str}"

    return create_memory(
        db,
        user_id=user_id,
        content=content,
        category="sample_annotation",
        project_id=project_id,
        structured_data={"sample_name": sample_name, **annotations},
        source="user_manual",
        related_file_id=file_id,
    )


# ---------------------------------------------------------------------------
# Bulk soft-delete (for project deletion cascade)
# ---------------------------------------------------------------------------


def soft_delete_project_memories(
    db: Session, project_id: str
) -> int:
    """Soft-delete all memories for a project (called on project deletion)."""
    now = datetime.datetime.utcnow()
    mems = db.execute(
        select(Memory).where(
            Memory.project_id == project_id,
            Memory.is_deleted == False,  # noqa: E712
        )
    ).scalars().all()
    count = 0
    for m in mems:
        m.is_deleted = True
        m.deleted_at = now
        count += 1
    if count:
        db.commit()
        logger.info("Soft-deleted project memories", project_id=project_id, count=count)
    return count


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------


def memory_to_dict(m: Memory) -> dict[str, Any]:
    """Serialize a Memory to a JSON-safe dict."""
    structured = None
    if m.structured_data:
        try:
            structured = json.loads(m.structured_data)
        except (json.JSONDecodeError, TypeError):
            structured = m.structured_data

    tags = None
    if m.tags_json:
        try:
            tags = json.loads(m.tags_json)
        except (json.JSONDecodeError, TypeError):
            tags = m.tags_json

    created_at = m.created_at
    if isinstance(created_at, datetime.datetime):
        created_at = created_at.isoformat()

    return {
        "id": m.id,
        "user_id": m.user_id,
        "project_id": m.project_id,
        "category": m.category,
        "content": m.content,
        "structured_data": structured,
        "source": m.source,
        "is_pinned": m.is_pinned,
        "is_deleted": m.is_deleted,
        "related_block_id": m.related_block_id,
        "related_file_id": m.related_file_id,
        "tags": tags,
        "created_at": str(created_at),
    }
