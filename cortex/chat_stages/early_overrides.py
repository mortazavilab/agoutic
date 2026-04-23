"""Stage 360 — Early override detection for commands that skip the LLM.

Detects sync, browsing, and user-data intents from the raw message text
*before* the LLM first pass (600) so the expensive model call is skipped
entirely.  The later overrides stage (800) still handles tool-call setup;
this stage only sets the ``skip_*`` flags and emits an early progress
signal so the UI shows immediate feedback.
"""
from __future__ import annotations

import re

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _emit_progress

logger = get_logger(__name__)

PRIORITY = 360

# ── Sync intent patterns (mirrors data_call_generator) ─────────────────
_SYNC_PATTERNS = [
    re.compile(
        r"\b(?:sync|re\s*sync|resync|copy|retry)\b.*\b(?:results?|outputs?)\b.*\b(?:back|local(?:ly)?|download)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:sync|re\s*sync|resync)\b.*\b(?:workflow\d+|run\b|job\b)\b",
        re.IGNORECASE,
    ),
]

# ── User-data listing patterns (mirrors overrides.py) ──────────────────
_USER_DATA_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r'\b(?:list|show|what)\s+(?:my\s+)?(?:data|data\s+files?)\b',
        r'\b(?:list|show)\s+my\s+files?\b',
        r'\bwhat\s+(?:data\s+)?files?\s+do\s+I\s+have\b',
    ]
]

# ── Browsing-command patterns (mirrors overrides.py) ───────────────────
_BROWSING_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
        r'\b(?:list|show)\s+(?:the\s+)?(?:project\s+)?files?\b',
    ]
]


def _is_sync_intent(msg: str) -> bool:
    return any(p.search(msg) for p in _SYNC_PATTERNS)


def _is_user_data_intent(msg: str) -> bool:
    return any(p.search(msg) for p in _USER_DATA_PATTERNS)


def _is_browsing_intent(msg: str) -> bool:
    return any(p.search(msg) for p in _BROWSING_PATTERNS)


class EarlyOverrideDetectionStage:
    name = "early_overrides"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        msg = ctx.user_msg_lower

        if _is_sync_intent(msg):
            ctx.skip_llm_first_pass = True
            ctx.skip_tag_parsing = True
            _emit_progress(ctx.request_id, "tools", "Starting result sync...")
            logger.info("Early sync override: skipping LLM first pass")
            return

        if _is_user_data_intent(msg):
            ctx.skip_llm_first_pass = True
            ctx.skip_tag_parsing = True
            _emit_progress(ctx.request_id, "tools", "Listing your data files...")
            logger.info("Early user-data override: skipping LLM first pass")
            return

        if _is_browsing_intent(msg):
            ctx.skip_llm_first_pass = True
            ctx.skip_tag_parsing = True
            _emit_progress(ctx.request_id, "tools", "Browsing project files...")
            logger.info("Early browsing override: skipping LLM first pass")
            return


register_stage(EarlyOverrideDetectionStage())
