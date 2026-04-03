"""Stage 350 — detect short affirmative replies and resume pending dataframe actions."""
from __future__ import annotations

import json

from sqlalchemy import select

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.dataframe_actions import is_short_affirmation
from cortex.models import ProjectBlock

logger = get_logger(__name__)

PRIORITY = 350


class ConfirmationDetectionStage:
    name = "confirmation_detection"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return is_short_affirmation(ctx.message)

    async def run(self, ctx: ChatContext) -> None:
        result = ctx.session.execute(
            select(ProjectBlock)
            .where(ProjectBlock.project_id == ctx.project_id)
            .where(ProjectBlock.owner_id == ctx.user.id)
            .where(ProjectBlock.type == "PENDING_ACTION")
            .where(ProjectBlock.status == "PENDING")
            .order_by(ProjectBlock.seq.desc())
            .limit(1)
        )
        pending = result.scalar_one_or_none()
        if not pending:
            return

        payload = json.loads(pending.payload_json or "{}") if pending.payload_json else {}
        action_call = payload.get("action_call") or {}
        if not action_call:
            return

        pending.status = "CONFIRMED"
        ctx.session.commit()
        ctx.pending_action_source_block = pending
        ctx.auto_calls = [action_call]
        ctx.clean_markdown = str(payload.get("confirmation_markdown") or payload.get("summary") or "Executing pending dataframe action.")
        ctx.skip_llm_first_pass = True
        ctx.skip_tag_parsing = True
        ctx.skip_second_pass = True
        ctx.inject_debug["pending_action_resumed"] = {
            "block_id": pending.id,
            "tool": action_call.get("tool"),
        }
        logger.info("Resuming pending dataframe action", block_id=pending.id, tool=action_call.get("tool"))


register_stage(ConfirmationDetectionStage())