"""Stage 500 — Plan detection: MULTI_STEP → generate & return; CHAIN → inject hints."""
from __future__ import annotations

import asyncio
import re

from fastapi.concurrency import run_in_threadpool

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _emit_progress, _is_cancelled
from cortex.db import row_to_dict
from cortex.db_helpers import _create_block_internal, save_conversation_message
from cortex.remote_orchestration import _WORKFLOW_PLAN_TYPE
from cortex.task_service import sync_project_tasks, archive_superseded_tasks
from cortex.tool_dispatch import ChatCancelled

logger = get_logger(__name__)

PRIORITY = 500


class PlanDetectionStage:
    name = "plan_detection"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        from cortex.planner import classify_request, generate_plan, render_plan_markdown

        _request_class = classify_request(
            ctx.message, ctx.active_skill, ctx.conv_state,
        )
        ctx.active_chain = None

        # ── CHAIN_MULTI_STEP: inject hints, fall through ──────────────
        if _request_class == "CHAIN_MULTI_STEP":
            from cortex.plan_chains import load_chains_for_skill, match_chain, render_chain_plan
            _chains = load_chains_for_skill(ctx.active_skill)
            ctx.active_chain = match_chain(ctx.message, _chains) if _chains else None
            if ctx.active_chain:
                _chain_plan_md = render_chain_plan(ctx.active_chain, ctx.message)
                _emit_progress(ctx.request_id, "planning", _chain_plan_md)
                _chain_hint = (
                    "\n\n[PLAN CHAIN: This is a multi-step request. "
                    "You must execute ALL of the following steps in order:\n"
                )
                for _cs in ctx.active_chain.steps:
                    _chain_hint += f"  {_cs.order}. {_cs.kind}: {_cs.title}\n"
                if ctx.active_chain.plot_hint:
                    _chain_hint += (
                        f"\nPLOT INSTRUCTION: {ctx.active_chain.plot_hint}. "
                        "After the data is retrieved, you MUST emit a "
                        "[[PLOT:...]] tag to visualize the results. "
                        "If this plot depends on data fetched in this same response, "
                        "do NOT guess or reuse an older DF number. Omit df= from the "
                        "[[PLOT:...]] tag and the system will bind it to the newly "
                        "created dataframe after retrieval. Infer the chart type and "
                        "grouping column from the user's message.]\n"
                    )
                else:
                    _chain_hint += "]\n"
                ctx.augmented_message += _chain_hint
                logger.info("Chain plan injected", chain=ctx.active_chain.name,
                            steps=len(ctx.active_chain.steps))
            # Fall through — do NOT return early

        # ── MULTI_STEP: generate plan & return ────────────────────────
        if _request_class == "MULTI_STEP":
            _emit_progress(ctx.request_id, "planning",
                           "Generating execution plan...")
            _plan_payload = await run_in_threadpool(
                generate_plan, ctx.message, ctx.active_skill, ctx.conv_state,
                ctx.engine, ctx.conversation_history,
                project_dir=ctx.project_dir,
            )
            if _plan_payload:
                _workflow_block = _create_block_internal(
                    ctx.session, ctx.project_id, _WORKFLOW_PLAN_TYPE,
                    _plan_payload, status="PENDING", owner_id=ctx.user.id,
                )
                # Archive tasks from previous workflows before sync
                archive_superseded_tasks(ctx.session, ctx.project_id)
                sync_project_tasks(ctx.session, ctx.project_id)

                _plan_md = render_plan_markdown(_plan_payload)
                _plan_agent_block = _create_block_internal(
                    ctx.session, ctx.project_id, "AGENT_PLAN",
                    {
                        "markdown": _plan_md,
                        "skill": ctx.active_skill or ctx.skill or "welcome",
                        "model": ctx.engine.model_name,
                        "plan_block_id": _workflow_block.id,
                        "state": ctx.conv_state.to_dict(),
                        "tokens": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "model": ctx.engine.model_name,
                        },
                    },
                    status="DONE", owner_id=ctx.user.id,
                )

                await save_conversation_message(
                    ctx.session, ctx.project_id, ctx.user.id, "assistant",
                    _plan_md,
                    token_data={
                        "prompt_tokens": 0, "completion_tokens": 0,
                        "total_tokens": 0, "model": ctx.engine.model_name,
                    },
                    model_name=ctx.engine.model_name,
                )

                from cortex.chat_downloads import _auto_execute_plan_steps
                if _plan_payload.get("auto_execute_safe_steps", True):
                    asyncio.create_task(
                        _auto_execute_plan_steps(
                            ctx.project_id, _workflow_block.id,
                            ctx.user, ctx.engine.model_name,
                            request_id=ctx.request_id,
                        )
                    )

                _emit_progress(ctx.request_id, "done", "Plan generated")
                ctx.short_circuit({
                    "status": "ok",
                    "user_block": row_to_dict(ctx.user_block),
                    "agent_block": row_to_dict(_plan_agent_block),
                    "gate_block": None,
                    "plan_block": row_to_dict(_workflow_block),
                })

        # ── Notify user about injected data ───────────────────────────
        if ctx.injected_dfs:
            _df_ref_match = re.search(r'\bDF\s*(\d+)\b', ctx.message, re.IGNORECASE)
            if _df_ref_match:
                _notify_ids = [f"DF{_df_ref_match.group(1)}"]
            else:
                _notify_ids = []
                for _d in ctx.injected_dfs.values():
                    _src_id = _d.get("metadata", {}).get("source_df_id")
                    if _src_id:
                        _notify_ids.append(f"DF{_src_id}")
                if not _notify_ids:
                    _notify_ids = list(ctx.injected_dfs.keys())[:2]
            _emit_progress(
                ctx.request_id, "context",
                f"Answering from previous data ({', '.join(_notify_ids)})...",
            )


register_stage(PlanDetectionStage())
