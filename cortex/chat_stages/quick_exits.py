"""Stages 200–240 — Quick-exit handlers that bypass the LLM entirely."""
from __future__ import annotations

import re

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.dataframe_sources import hydrate_dataframe_payload_locally
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
from cortex.db_helpers import _create_block_internal, _resolve_project_dir, save_conversation_message
from cortex.file_commands import execute_file_command, parse_file_command
from cortex.list_commands import detect_list_intent, execute_list_command, parse_list_command
from cortex.memory_commands import (
    detect_memory_intent,
    execute_memory_command,
    execute_memory_intent,
    parse_memory_command,
)
from cortex.skill_commands import detect_skill_intent, execute_skill_command, parse_skill_command, resolve_skill_key
from cortex.workflow_commands import (
    detect_workflow_intent,
    execute_manual_workflow_analysis,
    execute_use_workflow,
    execute_workflow_command,
    parse_workflow_command,
)

logger = get_logger(__name__)


def _slash_commands_markdown() -> str:
    return (
        "### Slash Commands\n\n"
        "Use `/commands` any time to reopen this list.\n\n"
        "**Skills**\n"
        "- `/skills`\n"
        "- `/skill <skill_key>`\n"
        "- `/use-skill <skill_key>`\n\n"
        "**Inventory**\n"
        "- `/list samples`\n"
        "- `/list staged [--profile NAME]`\n"
        "- `/list imported`\n"
        "- `/list dfs`\n"
        "- `/list workflows`\n"
        "- `/list files [target] [--project] [--depth N]`\n\n"
        "**Workflows**\n"
        "- `/use <workflow>`\n"
        "- `/reanalyze [workflow[, workflow2, ...]]` — omit the workflow to use the active workflow\n"
        "- `/rerun [workflow[, workflow2, ...]]` — omit the workflow to use the active workflow\n"
        "- `/delete [workflow[, workflow2, ...]]` — omit the workflow to use the active workflow\n"
        "- `/sync-workflow [workflow[, workflow2, ...]]` — omit the workflow to use the active workflow\n"
        "- `/cancel-sync <workflow[, workflow2, ...]>`\n"
        "- `/rename <workflow> <new_name>`\n"
        "- `/import-workflow <path> [--remote] [--profile NAME] [--full-copy] [--sample-name NAME] [--mode DNA|RNA|CDNA] [--reference GRCh38,mm39] [--modifications MODS]`\n"
        "- `/list-launchpad-workflows`\n\n"
        "**Files**\n"
        "- `/read-file <path> [--lines N] [--mode auto|plain|markdown|html_text|html_raw]`\n\n"
        "**Differential Expression**\n"
        "- `/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2`\n\n"
        "**Memory**\n"
        "- `/memories`\n"
        "- `/remember <text>`\n"
        "- `/remember-global <text>`\n"
        "- `/remember-df DF5 as <name>`\n"
        "- `/pin #<id>` · `/unpin #<id>` · `/restore #<id>`\n"
        "- `/annotate <sample> key=value`\n"
        "- `/search-memories <query>`\n"
        "- `/upgrade-to-global #<id>`\n"
    )


def _is_slash_commands_request(message: str) -> bool:
    msg = str(message or "").strip().lower()
    return msg in {
        "/commands",
        "/slash-commands",
        "/help-commands",
        "show commands",
        "list commands",
        "show slash commands",
        "list slash commands",
        "what slash commands are available",
    }


def _resolve_workflow_command_project_dir(ctx: ChatContext) -> str:
    if ctx.project_dir_path:
        return str(ctx.project_dir_path)
    if ctx.project_dir:
        return str(ctx.project_dir)
    if not ctx.session or not ctx.user or not ctx.project_id:
        return ""
    resolved = _resolve_project_dir(ctx.session, ctx.user, ctx.project_id)
    return str(resolved) if resolved else ""


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
            "5. **Differential expression analysis** — Run edgePython on count "
            "matrices, reconciled abundance tables, or saved dataframes; compare "
            "named groups for bulk, single-cell, DTU, or ChIP-seq analyses\n"
            "6. **GO & pathway enrichment** — Run enrichment analysis on gene lists "
            "from DE results or custom gene sets\n"
            "7. **Search IGVF data** — Browse IGVF datasets, files, samples, and "
            "genes from the IGVF portal\n\n"
            "Use `/commands` to list all slash commands by category.\n\n"
            "Useful slash commands:\n"
            "- `/commands`\n"
            "- Inventory: `/list samples`, `/list staged [--profile NAME]`, `/list imported`, `/list dfs`, `/list workflows`, `/list files [target] [--project] [--depth N]`\n"
            "- Skills: `/skills`, `/skill <skill_key>`, `/use-skill <skill_key>`\n"
            "- Workflows: `/use <workflow>`, `/reanalyze [workflow[, workflow2, ...]]`, `/rerun [workflow[, workflow2, ...]]`, `/delete [workflow[, workflow2, ...]]`, `/sync-workflow [workflow[, workflow2, ...]]`\n"
            "- Files: `/read-file <path> [--lines N] [--mode auto|plain|markdown|html_text|html_raw]`\n"
            "- Differential expression: `/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2`\n"
            "- Memory: `/remember <text>`, `/remember-global <text>`, `/remember-df DF5 as <name>`, `/memories`, `/pin #<id>`, `/unpin #<id>`, `/restore #<id>`, `/annotate <sample> key=value`, `/search-memories <query>`, `/upgrade-to-global #<id>`\n\n"
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


class CommandsCatalogStage:
    name = "commands_catalog"
    priority = 205

    async def should_run(self, ctx: ChatContext) -> bool:
        return _is_slash_commands_request(ctx.message)

    async def run(self, ctx: ChatContext) -> None:
        resp = await _create_prompt_response(
            ctx.session,
            _req_shim(ctx),
            ctx.user_block,
            ctx.user.id,
            ctx.active_skill,
            ctx.model or "default",
            _slash_commands_markdown(),
            prompt_type="commands_catalog",
        )
        ctx.short_circuit(resp)


register_stage(CommandsCatalogStage())


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
            if isinstance(_target_id, int) and _target_id in _df_map:
                _stored = _df_map[_target_id]
                _stored_rows = len(_stored.get("data", []))
                _declared_rows = int(_stored.get("row_count") or _stored_rows)
                if _n_rows > _stored_rows and _declared_rows > _stored_rows:
                    _full_payload = hydrate_dataframe_payload_locally(
                        _target_id,
                        ctx.history_blocks,
                        project_dir_path=ctx.project_dir_path,
                    )
                    if _full_payload:
                        _meta = _full_payload.get("metadata") or {}
                        _df_map[_target_id] = {
                            "columns": _full_payload.get("columns", []),
                            "data": _full_payload.get("data", []),
                            "row_count": _full_payload.get("row_count", len(_full_payload.get("data", []))),
                            "label": _meta.get("label", _stored.get("label", f"DF{_target_id}")),
                        }
            _md = _render_head_df(_df_map, _target_id, _n_rows)

        resp = await _create_prompt_response(
            ctx.session, _req_shim(ctx), ctx.user_block, ctx.user.id,
            ctx.active_skill, ctx.model or "default", _md,
            prompt_type="df_inspection",
        )
        ctx.short_circuit(resp)


register_stage(DfCommandStage())


class ListCommandStage:
    name = "list_command"
    priority = 223

    async def should_run(self, ctx: ChatContext) -> bool:
        return parse_list_command(ctx.message) is not None

    async def run(self, ctx: ChatContext) -> None:
        from sqlalchemy import select
        from cortex.models import ProjectBlock

        list_cmd = parse_list_command(ctx.message)

        if not ctx.history_blocks and list_cmd.action in {"dfs", "files"}:
            history_result = ctx.session.execute(
                select(ProjectBlock)
                .where(ProjectBlock.project_id == ctx.project_id)
                .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))
                .order_by(ProjectBlock.seq.asc())
            )
            ctx.history_blocks = history_result.scalars().all()

        markdown = await execute_list_command(
            ctx.session,
            list_cmd,
            user_id=ctx.user.id,
            project_id=ctx.project_id,
            project_dir=_resolve_workflow_command_project_dir(ctx),
            history_blocks=ctx.history_blocks,
        )
        resp = await _create_prompt_response(
            ctx.session,
            _req_shim(ctx),
            ctx.user_block,
            ctx.user.id,
            ctx.active_skill,
            ctx.model or "default",
            markdown,
            prompt_type="list_command",
        )
        ctx.short_circuit(resp)


register_stage(ListCommandStage())


# ── 225  Skill slash commands ──────────────────────────────────────────────

class SkillCommandStage:
    name = "skill_command"
    priority = 225

    async def should_run(self, ctx: ChatContext) -> bool:
        return parse_skill_command(ctx.message) is not None or detect_skill_intent(ctx.message) is not None

    async def run(self, ctx: ChatContext) -> None:
        skill_cmd = parse_skill_command(ctx.message) or detect_skill_intent(ctx.message)
        markdown = execute_skill_command(skill_cmd, active_skill=ctx.active_skill)

        response_skill = ctx.active_skill
        if skill_cmd.action == "use":
            resolved_skill = resolve_skill_key(skill_cmd.skill_ref)
            if resolved_skill:
                response_skill = resolved_skill
                ctx.active_skill = resolved_skill

        resp = await _create_prompt_response(
            ctx.session,
            _req_shim(ctx),
            ctx.user_block,
            ctx.user.id,
            response_skill,
            ctx.model or "default",
            markdown,
            prompt_type="skill_command" if ctx.message.strip().startswith("/") else "skill_intent",
        )
        ctx.short_circuit(resp)


register_stage(SkillCommandStage())


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


class FileCommandStage:
    name = "file_command"
    priority = 233

    async def should_run(self, ctx: ChatContext) -> bool:
        return parse_file_command(ctx.message) is not None

    async def run(self, ctx: ChatContext) -> None:
        from sqlalchemy import select
        from cortex.models import ProjectBlock

        file_cmd = parse_file_command(ctx.message)

        if not ctx.history_blocks:
            history_result = ctx.session.execute(
                select(ProjectBlock)
                .where(ProjectBlock.project_id == ctx.project_id)
                .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))
                .order_by(ProjectBlock.seq.asc())
            )
            ctx.history_blocks = history_result.scalars().all()

        markdown = await execute_file_command(
            file_cmd,
            history_blocks=ctx.history_blocks,
            project_dir=str(ctx.project_dir_path or ctx.project_dir or ""),
        )
        resp = await _create_prompt_response(
            ctx.session,
            _req_shim(ctx),
            ctx.user_block,
            ctx.user.id,
            ctx.active_skill,
            ctx.model or "default",
            markdown,
            prompt_type="file_command",
        )
        ctx.short_circuit(resp)


register_stage(FileCommandStage())


class WorkflowReanalyzeStage:
    name = "workflow_reanalyze"
    priority = 234

    async def should_run(self, ctx: ChatContext) -> bool:
        cmd = parse_workflow_command(ctx.message) or detect_workflow_intent(ctx.message)
        return cmd is not None and cmd.action == "reanalyze"

    async def run(self, ctx: ChatContext) -> None:
        workflow_cmd = parse_workflow_command(ctx.message) or detect_workflow_intent(ctx.message)
        workflow_refs = workflow_cmd.workflow_refs or ([workflow_cmd.workflow_ref] if workflow_cmd.workflow_ref else [])
        if not workflow_refs:
            workflow_refs = [""]

        created_blocks = []
        errors = []
        for workflow_ref in workflow_refs:
            cmd = workflow_cmd if workflow_ref == workflow_cmd.workflow_ref else type(workflow_cmd)(
                **{**workflow_cmd.__dict__, "workflow_ref": workflow_ref, "workflow_refs": [workflow_ref]}
            )
            agent_block, error_markdown = await execute_manual_workflow_analysis(
                ctx.session,
                cmd,
                project_id=ctx.project_id,
                owner_id=ctx.user.id,
                model=ctx.model or "default",
            )
            if agent_block is not None:
                created_blocks.append(agent_block)
            elif error_markdown:
                errors.append(error_markdown)

        if not created_blocks:
            resp = await _create_prompt_response(
                ctx.session,
                _req_shim(ctx),
                ctx.user_block,
                ctx.user.id,
                ctx.active_skill,
                ctx.model or "default",
                "\n".join(errors) or "Manual automatic analysis failed.",
                prompt_type="workflow_intent" if not ctx.message.strip().startswith("/") else "workflow_command",
            )
            ctx.short_circuit(resp)
            return

        if errors:
            resp = await _create_prompt_response(
                ctx.session,
                _req_shim(ctx),
                ctx.user_block,
                ctx.user.id,
                ctx.active_skill,
                ctx.model or "default",
                "\n".join(errors),
                prompt_type="workflow_intent" if not ctx.message.strip().startswith("/") else "workflow_command",
            )
            ctx.short_circuit(resp)
            return

        ctx.short_circuit({
            "status": "ok",
            "user_block": row_to_dict(ctx.user_block),
            "agent_block": row_to_dict(created_blocks[-1]),
            "gate_block": None,
        })


register_stage(WorkflowReanalyzeStage())


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
            project_dir=_resolve_workflow_command_project_dir(ctx),
            owner_id=ctx.user.id,
            model=ctx.model or "default",
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
            project_dir=_resolve_workflow_command_project_dir(ctx),
            owner_id=ctx.user.id,
            model=ctx.model or "default",
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


class ListIntentStage:
    name = "list_intent"
    priority = 239

    async def should_run(self, ctx: ChatContext) -> bool:
        return detect_list_intent(ctx.message) is not None

    async def run(self, ctx: ChatContext) -> None:
        from sqlalchemy import select
        from cortex.models import ProjectBlock

        list_cmd = detect_list_intent(ctx.message)

        if not ctx.history_blocks and list_cmd.action in {"dfs", "files"}:
            history_result = ctx.session.execute(
                select(ProjectBlock)
                .where(ProjectBlock.project_id == ctx.project_id)
                .where(ProjectBlock.type.in_(["USER_MESSAGE", "AGENT_PLAN", "EXECUTION_JOB"]))
                .order_by(ProjectBlock.seq.asc())
            )
            ctx.history_blocks = history_result.scalars().all()

        markdown = await execute_list_command(
            ctx.session,
            list_cmd,
            user_id=ctx.user.id,
            project_id=ctx.project_id,
            project_dir=_resolve_workflow_command_project_dir(ctx),
            history_blocks=ctx.history_blocks,
        )
        resp = await _create_prompt_response(
            ctx.session,
            _req_shim(ctx),
            ctx.user_block,
            ctx.user.id,
            ctx.active_skill,
            ctx.model or "default",
            markdown,
            prompt_type="list_intent",
        )
        ctx.short_circuit(resp)


register_stage(ListIntentStage())

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
