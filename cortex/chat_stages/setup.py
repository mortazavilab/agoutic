"""Stage 100 — Setup: project auto-registration, token limit, skill resolution, user_block."""
from __future__ import annotations

import datetime
import uuid

from fastapi import HTTPException
from sqlalchemy import select, desc, text

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.config import SKILLS_REGISTRY
from cortex.db import SessionLocal, row_to_dict
from cortex.db_helpers import _create_block_internal, track_project_access
from cortex.dependencies import require_project_access
from cortex.llm_validators import get_block_payload
from cortex.models import Project, ProjectAccess, ProjectBlock

logger = get_logger(__name__)

PRIORITY = 100


class SetupStage:
    name = "setup"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        user = ctx.user

        # ── Auto-register project if missing ──────────────────────────
        _ensure_session = SessionLocal()
        try:
            _proj = _ensure_session.execute(
                select(Project).where(Project.id == ctx.project_id)
            ).scalar_one_or_none()
            if not _proj:
                from cortex.user_jail import get_user_project_dir
                try:
                    get_user_project_dir(user.id, ctx.project_id)
                except PermissionError:
                    pass
                now = datetime.datetime.utcnow()
                _proj = Project(
                    id=ctx.project_id,
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
                    project_id=ctx.project_id,
                    project_name=_proj.name,
                    role="owner",
                    last_accessed=now,
                )
                _ensure_session.add(_acc)
                _ensure_session.commit()
                logger.info("Auto-registered project from chat",
                            project_id=ctx.project_id, user=user.email)
        except Exception:
            _ensure_session.rollback()
        finally:
            _ensure_session.close()

        require_project_access(ctx.project_id, user, min_role="editor")

        # ── Token limit check ─────────────────────────────────────────
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
                        "message": (
                            f"You have used {_tokens_used:,} of your "
                            f"{user.token_limit:,} token limit. "
                            "Please contact an admin to increase your quota."
                        ),
                        "tokens_used": _tokens_used,
                        "token_limit": user.token_limit,
                    },
                )

        # ── Open main session ─────────────────────────────────────────
        session = SessionLocal()
        ctx.session = session

        # ── Skill resolution ──────────────────────────────────────────
        last_agent_block = session.execute(
            select(ProjectBlock)
            .where(ProjectBlock.project_id == ctx.project_id)
            .where(ProjectBlock.type == "AGENT_PLAN")
            .order_by(desc(ProjectBlock.seq))
            .limit(1)
        ).scalar_one_or_none()

        approval_block = session.execute(
            select(ProjectBlock)
            .where(ProjectBlock.project_id == ctx.project_id)
            .where(ProjectBlock.type == "APPROVAL_GATE")
            .order_by(desc(ProjectBlock.seq))
            .limit(1)
        ).scalar_one_or_none()

        active_skill = ctx.skill
        if last_agent_block:
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
                active_skill = ctx.skill
                logger.info("Starting fresh with skill", skill=active_skill)

        ctx.active_skill = active_skill

        # ── Track access & save USER_MESSAGE ──────────────────────────
        await track_project_access(session, user.id, ctx.project_id, ctx.project_id)

        ctx.user_block = _create_block_internal(
            session,
            ctx.project_id,
            "USER_MESSAGE",
            {"text": ctx.message},
            owner_id=user.id,
        )

        ctx.user_msg_lower = ctx.message.lower()


register_stage(SetupStage())
