"""Stage 600 — First-pass LLM call, output validation, skill-switch re-think."""
from __future__ import annotations

import re

from fastapi.concurrency import run_in_threadpool

from common.logging_config import get_logger
from cortex.agent_engine import AgentEngine
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _emit_progress, _is_cancelled
from cortex.config import SKILLS_REGISTRY
from cortex.context_injection import _inject_job_context
from cortex.llm_validators import _validate_llm_output
from cortex.remote_orchestration import _extract_remote_browse_request
from cortex.tool_contracts import fetch_all_tool_schemas, _CACHE_LOADED
from cortex.tool_dispatch import ChatCancelled

logger = get_logger(__name__)

PRIORITY = 600

_SKILL_ALIASES = {
    "run_workflow": "analyze_local_sample",
    "submit_job": "analyze_local_sample",
    "run_dogme": "analyze_local_sample",
    "local_sample": "analyze_local_sample",
    "encode_search": "ENCODE_Search",
    "encode_longread": "ENCODE_LongRead",
    "job_results": "analyze_job_results",
}


class FirstPassLLMStage:
    name = "llm_first_pass"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        if _is_cancelled(ctx.request_id):
            raise ChatCancelled("thinking", "Cancelled before the model started thinking.")

        # Retry schema fetch if startup loading failed
        if not _CACHE_LOADED:
            try:
                import cortex.config as _cfg
                import atlas.config as _atlas_cfg
                await fetch_all_tool_schemas(_cfg.SERVICE_REGISTRY, _atlas_cfg.CONSORTIUM_REGISTRY)
            except Exception:
                pass

        raw_response, ctx.think_usage = await run_in_threadpool(
            ctx.engine.think,
            ctx.augmented_message,
            ctx.active_skill,
            ctx.conversation_history,
        )
        ctx.raw_response = raw_response

        # Validate output
        ctx.raw_response, ctx.output_violations = _validate_llm_output(
            ctx.raw_response, ctx.active_skill, history_blocks=ctx.history_blocks,
        )
        _pre_switch_skill = ctx.active_skill

        # ── Skill switch tag ──────────────────────────────────────────
        skill_switch_match = re.search(
            r'\[\[SKILL_SWITCH_TO:\s*(\w+)\]\]', ctx.raw_response,
        )
        _remote_browse_request = _extract_remote_browse_request(ctx.message)
        _skill_switch_debug: dict = {
            "tag_found": bool(skill_switch_match),
            "pre_switch_skill": _pre_switch_skill,
        }

        if skill_switch_match:
            new_skill = skill_switch_match.group(1)
            new_skill = _SKILL_ALIASES.get(new_skill, new_skill)
            _skill_switch_debug["requested_skill"] = new_skill
            _skill_switch_debug["in_registry"] = new_skill in SKILLS_REGISTRY

            if _remote_browse_request and new_skill == "analyze_job_results":
                logger.warning(
                    "Ignoring incorrect skill switch for remote browsing request",
                    from_skill=ctx.active_skill, requested_skill=new_skill,
                    message=ctx.message,
                )
                _skill_switch_debug["ignored"] = True
                _skill_switch_debug["ignore_reason"] = "remote_browse_request"
            elif new_skill in SKILLS_REGISTRY:
                logger.info("Agent switching skill",
                            from_skill=ctx.active_skill, to_skill=new_skill)
                _emit_progress(ctx.request_id, "switching",
                               f"Switching to {new_skill} skill...")
                if _is_cancelled(ctx.request_id):
                    raise ChatCancelled(
                        "thinking",
                        f"Cancelled before re-thinking with {new_skill} skill.",
                    )
                ctx.engine = AgentEngine(model_key=ctx.model)
                ctx.augmented_message, ctx.injected_dfs, ctx.inject_debug = (
                    _inject_job_context(
                        ctx.message, new_skill, ctx.conversation_history,
                        history_blocks=ctx.history_blocks,
                        db=ctx.session, user_id=ctx.user.id,
                        project_id=ctx.project_id,
                    )
                )
                ctx.raw_response, ctx.think_usage = await run_in_threadpool(
                    ctx.engine.think,
                    ctx.augmented_message,
                    new_skill,
                    ctx.conversation_history,
                )
                ctx.raw_response, _switch_violations = _validate_llm_output(
                    ctx.raw_response, new_skill, history_blocks=ctx.history_blocks,
                )
                ctx.output_violations.extend(_switch_violations)
                ctx.active_skill = new_skill
                _skill_switch_debug["switched"] = True
                _skill_switch_debug["new_response_preview"] = ctx.raw_response[:200]

        _skill_switch_debug["post_switch_skill"] = ctx.active_skill
        ctx.inject_debug["skill_switch"] = _skill_switch_debug


register_stage(FirstPassLLMStage())
