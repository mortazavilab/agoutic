"""Stage 400 — Engine init, project dir, pre-LLM skill switch, context injection."""
from __future__ import annotations

import re

from common.logging_config import get_logger
from cortex.agent_engine import AgentEngine
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _emit_progress
from cortex.config import SKILLS_REGISTRY
from cortex.context_injection import _inject_job_context, inject_memory_context
from cortex.conversation_state import _build_conversation_state
from cortex.db_helpers import _resolve_project_dir
from cortex.llm_validators import _auto_detect_skill_switch
from cortex.memory_service import get_memory_context as _get_memory_context
from cortex.remote_orchestration import _extract_remote_execution_request

logger = get_logger(__name__)

PRIORITY = 400


class ContextPrepStage:
    name = "context_prep"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        # ── Engine ────────────────────────────────────────────────────
        ctx.engine = AgentEngine(model_key=ctx.model)
        _emit_progress(ctx.request_id, "thinking",
                       f"Thinking using {ctx.engine.model_name}...")

        # ── Project directory ─────────────────────────────────────────
        ctx.project_dir_path = _resolve_project_dir(
            ctx.session, ctx.user, ctx.project_id,
        )
        ctx.project_dir = str(ctx.project_dir_path) if ctx.project_dir_path else ""

        # ── Pre-LLM skill switch ──────────────────────────────────────
        _remote_req = _extract_remote_execution_request(ctx.message)
        if _remote_req:
            ctx.auto_skill = "remote_execution"
        else:
            ctx.auto_skill = _auto_detect_skill_switch(ctx.message, ctx.active_skill)

        ctx.pre_llm_skill = ctx.active_skill
        if ctx.auto_skill and ctx.auto_skill in SKILLS_REGISTRY:
            logger.info("Auto-detected skill switch before LLM call",
                        from_skill=ctx.active_skill, to_skill=ctx.auto_skill)
            _emit_progress(ctx.request_id, "switching",
                           f"Switching to {ctx.auto_skill} skill...")
            ctx.active_skill = ctx.auto_skill

        # ── Job context injection ─────────────────────────────────────
        ctx.augmented_message, ctx.injected_dfs, ctx.inject_debug = _inject_job_context(
            ctx.message, ctx.active_skill, ctx.conversation_history,
            history_blocks=ctx.history_blocks,
            db=ctx.session, user_id=ctx.user.id, project_id=ctx.project_id,
        )

        # ── Memory context injection (per-request cache) ──────────────
        # Memory context is static within a request. Cache by (user_id, project_id)
        # to avoid redundant DB queries when multiple stages reference it.
        _mem_cache_key = (ctx.user.id, ctx.project_id)
        if not hasattr(ctx, '_mem_cache'):
            ctx._mem_cache: dict[tuple, str] = {}
        if _mem_cache_key not in ctx._mem_cache:
            ctx._mem_cache[_mem_cache_key] = _get_memory_context(
                ctx.session, ctx.user.id, ctx.project_id
            )
        ctx.augmented_message = inject_memory_context(
            ctx.augmented_message, ctx._mem_cache[_mem_cache_key]
        )

        # ── Conversation state (per-request cache) ────────────────────
        # The state is deterministic given the same inputs. For identical
        # follow-up messages (common in chat), skip redundant computation.
        _state_cache_key = (ctx.active_skill, tuple(
            (b.type, b.seq) for b in ctx.history_blocks[-10:]
        ))
        if not hasattr(ctx, '_state_cache'):
            ctx._state_cache: dict[tuple, str] = {}
        if _state_cache_key not in ctx._state_cache:
            _conv_state = _build_conversation_state(
                ctx.active_skill, ctx.conversation_history,
                history_blocks=ctx.history_blocks,
                project_id=ctx.project_id,
                db=ctx.session,
                user_id=ctx.user.id,
            )
            ctx.conv_state = _conv_state
            _state_json = _conv_state.to_json()
            if _state_json and _state_json != "{}":
                ctx._state_cache[_state_cache_key] = (
                    f"[STATE]\n{_state_json}\n[/STATE]\n\n"
                )
            else:
                ctx._state_cache[_state_cache_key] = ""
        else:
            # Cache hit — rebuild conv_state from cached JSON to avoid
            # re-running the full computation.
            _cached_prefix = ctx._state_cache[_state_cache_key]
            if _cached_prefix:
                ctx.augmented_message = _cached_prefix + ctx.augmented_message

        # ── Track injected-data flags ─────────────────────────────────
        ctx.injected_previous_data = "[PREVIOUS QUERY DATA:]" in ctx.augmented_message
        ctx.injected_was_capped = (
            ctx.injected_previous_data and "total rows)*" in ctx.augmented_message
        )


register_stage(ContextPrepStage())
