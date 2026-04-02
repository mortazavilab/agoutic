"""Stage 300 — Build conversation history from project blocks."""
from __future__ import annotations

import json
import re

from sqlalchemy import select

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock

logger = get_logger(__name__)

PRIORITY = 300

_DETAILS_RE = re.compile(
    r'\s*---\s*\n\s*<details>.*?</details>', re.DOTALL,
)
MAX_HISTORY_TURNS = 20  # 20 user+assistant pairs = 40 messages


class HistoryStage:
    name = "history"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        history_result = ctx.session.execute(
            select(ProjectBlock)
            .where(ProjectBlock.project_id == ctx.project_id)
            .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))
            .order_by(ProjectBlock.seq.asc())
        )
        ctx.history_blocks = history_result.scalars().all()

        conversation_history: list[dict] = []
        for block in ctx.history_blocks[:-1]:  # exclude the USER_MESSAGE we just saved
            block_payload = get_block_payload(block)
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block_payload.get("text", ""),
                })
            elif block.type == "AGENT_PLAN":
                _md = block_payload.get("markdown", "")
                _md = _DETAILS_RE.sub("", _md)
                # Skip bare find_file JSON echo blocks
                _md_stripped = re.sub(r'```(?:json)?|```', '', _md).strip()
                if ('"primary_path"' in _md_stripped
                        and ('"success": true' in _md_stripped
                             or '"success":true' in _md_stripped)):
                    try:
                        _probe = json.loads(_md_stripped)
                        if _probe.get("success") and _probe.get("primary_path"):
                            logger.debug("Skipping bare find_file echo block",
                                         primary_path=_probe["primary_path"])
                            continue
                    except (json.JSONDecodeError, ValueError):
                        pass
                conversation_history.append({
                    "role": "assistant",
                    "content": _md,
                })

        # Trim to last MAX_HISTORY_TURNS pairs
        if len(conversation_history) > MAX_HISTORY_TURNS * 2:
            conversation_history = conversation_history[-(MAX_HISTORY_TURNS * 2):]

        ctx.conversation_history = conversation_history


register_stage(HistoryStage())
