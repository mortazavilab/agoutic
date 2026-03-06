"""
Conversation history routes — extracted from cortex/app.py (Tier 3).

Handles listing conversations, fetching messages, listing jobs,
and linking jobs to conversations.
"""

import datetime
import uuid

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, desc

import cortex.db as _db
from cortex.dependencies import require_project_access
from cortex.models import Conversation, ConversationMessage, JobResult
from common.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/projects/{project_id}/conversations")
async def get_project_conversations(project_id: str, request: Request):
    """Get all conversations for a project."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = _db.SessionLocal()
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


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str, request: Request):
    """Get all messages in a conversation."""
    user = request.state.user

    session = _db.SessionLocal()
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


@router.get("/projects/{project_id}/jobs")
async def get_project_jobs(project_id: str, request: Request):
    """Get all jobs linked to a project."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = _db.SessionLocal()
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


@router.post("/conversations/{conversation_id}/jobs")
async def link_job_to_conversation(conversation_id: str, run_uuid: str, request: Request):
    """Link a job to a conversation."""
    user = request.state.user

    session = _db.SessionLocal()
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
