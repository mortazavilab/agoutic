"""
Shared DB helper functions — extracted from cortex/app.py (Tier 3).

These utilities are used by multiple route modules and by
the main app, so they live in a neutral module to avoid
circular imports.
"""

import datetime
import json
import uuid

from pathlib import Path
from sqlalchemy import select, desc

import cortex.config as _cfg
import cortex.db as _db
from cortex.models import (
    ProjectBlock, Project, Conversation, ConversationMessage,
    User, ProjectAccess,
)
from common.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# _resolve_project_dir
# ---------------------------------------------------------------------------

def _resolve_project_dir(session, user, project_id: str) -> Path:
    """Resolve the on-disk directory for a project.

    Uses slug-based path (_cfg.AGOUTIC_DATA/users/{username}/{slug}/) if both
    username and slug are available.  Falls back to legacy UUID-based path.
    Returns the Path (may or may not exist yet).
    """
    project = session.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one_or_none()

    username = getattr(user, "username", None)
    slug = project.slug if project else None

    if username and slug:
        return _cfg.AGOUTIC_DATA / "users" / username / slug
    # Legacy fallback
    return _cfg.AGOUTIC_DATA / "users" / user.id / project_id


# ---------------------------------------------------------------------------
# _create_block_internal
# ---------------------------------------------------------------------------

def _create_block_internal(session, project_id, block_type, payload, status="NEW", owner_id=None):
    """
    Internal helper to insert a block and handle the sequence number safely.
    """
    # Calculate next sequence
    seq_num = _db.next_seq_sync(session, project_id)

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


# ---------------------------------------------------------------------------
# save_conversation_message
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# track_project_access
# ---------------------------------------------------------------------------

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
