"""Stages 200–240 — Quick-exit handlers that bypass the LLM entirely."""
from __future__ import annotations

import re

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import (
    _create_prompt_response,
    _detect_df_command,
    _detect_prompt_request,
    _format_prompt_report,
    _collect_df_map,
    _render_head_df,
    _render_list_dfs,
)
from cortex.db import row_to_dict
from cortex.db_helpers import _create_block_internal, save_conversation_message
from cortex.memory_commands import (
    detect_memory_intent,
    execute_memory_command,
    execute_memory_intent,
    parse_memory_command,
)
from cortex.workflow_commands import (
    detect_workflow_intent,
    execute_use_workflow,
    execute_workflow_command,
    parse_workflow_command,
)

logger = get_logger(__name__)


# ── 200  Capabilities ─────────────────────────────────────────────────────

class CapabilitiesStage:
    name = "capabilities"
    priority = 200

    async def should_run(self, ctx: ChatContext) -> bool:
        phrases = [
            "what can you do", "what are your capabilities", "help",
            "what can i do", "list features", "show capabilities",
        ]
        return any(p in ctx.user_msg_lower for p in phrases)

    async def run(self, ctx: ChatContext) -> None:
        capabilities_text = (
            "\U0001f44b Welcome to **Agoutic** — your autonomous bioinformatics "
            "agent for long-read sequencing data.\n\n"
            "Here's what I can help you with:\n\n"
            "1. **Analyze a new local dataset** — Run the Dogme pipeline on "
            "pod5, bam, or fastq files on your machine\n"
            "2. **Download & analyze ENCODE data** — Search the ENCODE portal "
            "for long-read experiments, download files, and process them\n"
            "3. **Download files from URLs** — Grab files from any URL into your project\n"
            "4. **Check results from a completed job** — View QC reports, alignment "
            "stats, modification calls, and expression data\n"
            "5. **Differential expression analysis** — Run DE analysis on count "
            "matrices using edgePython (bulk, single-cell, DTU, ChIP-seq)\n\n"
            "What would you like to do?\n"
        )
        agent_block = _create_block_internal(
            ctx.session,
            ctx.project_id,
            "AGENT_PLAN",
            {
                "markdown": capabilities_text,
                "skill": ctx.active_skill or ctx.skill or "welcome",
                "model": ctx.model or "default",
            },
            status="DONE",
            owner_id=ctx.user.id,
        )
        ctx.short_circuit({
            "status": "ok",
            "user_block": row_to_dict(ctx.user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": None,
        })


register_stage(CapabilitiesStage())


# ── 210  Prompt inspection ─────────────────────────────────────────────────

class PromptInspectStage:
    name = "prompt_inspect"
    priority = 210

    async def should_run(self, ctx: ChatContext) -> bool:
        return _detect_prompt_request(ctx.message) is not None

    async def run(self, ctx: ChatContext) -> None:
        from cortex.agent_engine import AgentEngine

        prompt_request = _detect_prompt_request(ctx.message)
        if prompt_request == "ambiguous":
            clarification = (
                "I can show either the first-pass planning prompt or the "
                "second-pass analysis prompt. Ask for \"first-pass system prompt\" "
                "or \"second-pass system prompt\"."
            )
            resp = await _create_prompt_response(
                ctx.session, _req_shim(ctx), ctx.user_block, ctx.user.id,
                ctx.active_skill, ctx.model or "default", clarification,
                prompt_type="ambiguous",
            )
            ctx.short_circuit(resp)
            return

        engine = AgentEngine(model_key=ctx.model)
        rendered_prompt = engine.render_system_prompt(
            skill_key=ctx.active_skill,
            prompt_type=prompt_request,
        )
        markdown = _format_prompt_report(
            prompt_request, ctx.active_skill, engine.model_name, rendered_prompt,
        )
        resp = await _create_prompt_response(
            ctx.session, _req_shim(ctx), ctx.user_block, ctx.user.id,
            ctx.active_skill, engine.model_name, markdown,
            prompt_type=prompt_request,
        )
        ctx.short_circuit(resp)


register_stage(PromptInspectStage())


# ── 220  DF inspection commands ────────────────────────────────────────────

class DfCommandStage:
    name = "df_command"
    priority = 220

    async def should_run(self, ctx: ChatContext) -> bool:
        return _detect_df_command(ctx.user_msg_lower) is not None

    async def run(self, ctx: ChatContext) -> None:
        from sqlalchemy import select
        from cortex.models import ProjectBlock

        _df_cmd = _detect_df_command(ctx.user_msg_lower)

        # Need history blocks for DF map
        if not ctx.history_blocks:
            history_result = ctx.session.execute(
                select(ProjectBlock)
                .where(ProjectBlock.project_id == ctx.project_id)
                .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))
                .order_by(ProjectBlock.seq.asc())
            )
            ctx.history_blocks = history_result.scalars().all()

        _df_map = _collect_df_map(
            ctx.history_blocks, db=ctx.session,
            user_id=ctx.user.id, project_id=ctx.project_id,
        )
        if _df_cmd["action"] == "list":
            _md = _render_list_dfs(_df_map)
        else:
            _target_id = _df_cmd.get("df_id")
            if _target_id is None:
                _int_keys = [k for k in _df_map if isinstance(k, int)]
                _target_id = max(_int_keys) if _int_keys else None
            _n_rows = _df_cmd.get("n", 10)
            _md = _render_head_df(_df_map, _target_id, _n_rows)

        resp = await _create_prompt_response(
            ctx.session, _req_shim(ctx), ctx.user_block, ctx.user.id,
            ctx.active_skill, ctx.model or "default", _md,
            prompt_type="df_inspection",
        )
        ctx.short_circuit(resp)


register_stage(DfCommandStage())


# ── 230  Memory slash commands ─────────────────────────────────────────────

class MemoryCommandStage:
    name = "memory_command"
    priority = 230

    async def should_run(self, ctx: ChatContext) -> bool:
        return parse_memory_command(ctx.message) is not None

    async def run(self, ctx: ChatContext) -> None:
        from sqlalchemy import select
        from cortex.models import ProjectBlock

        _mem_cmd = parse_memory_command(ctx.message)

        if not ctx.history_blocks:
            history_result = ctx.session.execute(
                select(ProjectBlock)
                .where(ProjectBlock.project_id == ctx.project_id)
                .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))
                .order_by(ProjectBlock.seq.asc())
            )
            ctx.history_blocks = history_result.scalars().all()

        _mem_response = execute_memory_command(
            ctx.session, _mem_cmd, ctx.user.id, ctx.project_id,
            history_blocks=ctx.history_blocks,
        )
        resp = await _create_prompt_response(
            ctx.session, _req_shim(ctx), ctx.user_block, ctx.user.id,
            ctx.active_skill, ctx.model or "default", _mem_response,
            prompt_type="memory_command",
        )
        ctx.short_circuit(resp)


register_stage(MemoryCommandStage())


class WorkflowCommandStage:
    name = "workflow_command"
    priority = 235

    async def should_run(self, ctx: ChatContext) -> bool:
        cmd = parse_workflow_command(ctx.message)
        return cmd is not None and cmd.action != "use"

    async def run(self, ctx: ChatContext) -> None:
        workflow_cmd = parse_workflow_command(ctx.message)
        markdown = await execute_workflow_command(
            ctx.session,
            workflow_cmd,
            project_id=ctx.project_id,
        )
        resp = await _create_prompt_response(
            ctx.session,
            _req_shim(ctx),
            ctx.user_block,
            ctx.user.id,
            ctx.active_skill,
            ctx.model or "default",
            markdown,
            prompt_type="workflow_command",
        )
        ctx.short_circuit(resp)


register_stage(WorkflowCommandStage())


# ── 240  Natural-language memory intent ────────────────────────────────────

class WorkflowIntentStage:
    name = "workflow_intent"
    priority = 238

    async def should_run(self, ctx: ChatContext) -> bool:
        cmd = detect_workflow_intent(ctx.message)
        return cmd is not None and cmd.action != "use"

    async def run(self, ctx: ChatContext) -> None:
        workflow_cmd = detect_workflow_intent(ctx.message)
        markdown = await execute_workflow_command(
            ctx.session,
            workflow_cmd,
            project_id=ctx.project_id,
        )
        resp = await _create_prompt_response(
            ctx.session,
            _req_shim(ctx),
            ctx.user_block,
            ctx.user.id,
            ctx.active_skill,
            ctx.model or "default",
            markdown,
            prompt_type="workflow_intent",
        )
        ctx.short_circuit(resp)


register_stage(WorkflowIntentStage())

class MemoryIntentStage:
    name = "memory_intent"
    priority = 240

    async def should_run(self, ctx: ChatContext) -> bool:
        return detect_memory_intent(ctx.message) is not None

    async def run(self, ctx: ChatContext) -> None:
        _mem_intent = detect_memory_intent(ctx.message)
        _mem_ack = execute_memory_intent(
            ctx.session, _mem_intent, ctx.user.id, ctx.project_id,
        )
        if _mem_ack:
            resp = await _create_prompt_response(
                ctx.session, _req_shim(ctx), ctx.user_block, ctx.user.id,
                ctx.active_skill, ctx.model or "default", _mem_ack,
                prompt_type="memory_intent",
            )
            ctx.short_circuit(resp)


register_stage(MemoryIntentStage())


# ── 410  Use-workflow (needs conv_state from priority 400) ─────────────────

class UseWorkflowStage:
    name = "use_workflow"
    priority = 410

    async def should_run(self, ctx: ChatContext) -> bool:
        cmd = parse_workflow_command(ctx.message) or detect_workflow_intent(ctx.message)
        return cmd is not None and cmd.action == "use"

    async def run(self, ctx: ChatContext) -> None:
        cmd = parse_workflow_command(ctx.message) or detect_workflow_intent(ctx.message)
        conv_state = ctx.conv_state
        project_dir = ctx.project_dir or ""

        updated_state, markdown = execute_use_workflow(
            conv_state, project_dir, cmd.workflow_ref,
        )
        ctx.conv_state = updated_state

        model_name = ctx.model or "default"
        payload = {
            "markdown": markdown,
            "skill": ctx.active_skill or ctx.skill or "welcome",
            "model": model_name,
            "state": updated_state.to_dict(),
            "tokens": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "model": model_name,
            },
        }
        agent_block = _create_block_internal(
            ctx.session,
            ctx.project_id,
            "AGENT_PLAN",
            payload,
            status="DONE",
            owner_id=ctx.user.id,
        )
        await save_conversation_message(
            ctx.session,
            ctx.project_id,
            ctx.user.id,
            "assistant",
            markdown,
            token_data=payload["tokens"],
            model_name=model_name,
        )
        ctx.short_circuit({
            "status": "ok",
            "user_block": row_to_dict(ctx.user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": None,
        })


register_stage(UseWorkflowStage())


# ── Helpers ────────────────────────────────────────────────────────────────

class _ReqShim:
    """Lightweight shim matching the ChatRequest fields that _create_prompt_response expects."""
    __slots__ = ("project_id", "message", "skill", "model", "request_id")

    def __init__(self, ctx: ChatContext):
        self.project_id = ctx.project_id
        self.message = ctx.message
        self.skill = ctx.skill
        self.model = ctx.model
        self.request_id = ctx.request_id


def _req_shim(ctx: ChatContext) -> _ReqShim:
    return _ReqShim(ctx)
