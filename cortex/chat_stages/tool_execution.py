"""Stage 900 — Build, deduplicate, validate, and execute MCP tool calls."""
from __future__ import annotations

import datetime
import json
import re

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _emit_progress, _is_cancelled
from cortex.config import get_service_url
from cortex.path_helpers import _pick_file_tool
from cortex.tool_dispatch import (
    auto_fetch_missing_encff,
    build_calls_by_source,
    deduplicate_calls,
    execute_tool_calls as _execute_tool_calls,
    validate_call_schemas,
)

logger = get_logger(__name__)

PRIORITY = 900


class ToolExecutionStage:
    name = "tool_execution"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        # Always run — even when there are no tags, we attempt auto-generation
        return True

    async def run(self, ctx: ChatContext) -> None:
        # ── Build calls ───────────────────────────────────────────────
        calls_by_source = build_calls_by_source(
            data_call_matches=ctx.data_call_matches,
            legacy_encode_matches=ctx.legacy_encode_matches,
            legacy_analysis_matches=ctx.legacy_analysis_matches,
            auto_calls=ctx.auto_calls,
            has_any_tags=ctx.has_any_tags,
            user_id=ctx.user.id,
            project_id=ctx.project_id,
            user_message=ctx.message,
            conversation_history=ctx.conversation_history,
            history_blocks=ctx.history_blocks,
            project_dir=ctx.project_dir,
            active_skill=ctx.active_skill,
        )
        calls_by_source = deduplicate_calls(calls_by_source)
        validate_call_schemas(calls_by_source)

        # ── Execute ───────────────────────────────────────────────────
        _exec_result = await _execute_tool_calls(
            calls_by_source,
            user_id=ctx.user.id,
            username=getattr(ctx.user, 'username', None),
            project_id=ctx.project_id,
            project_dir_path=ctx.project_dir_path,
            user_message=ctx.message,
            active_skill=ctx.active_skill,
            needs_approval=ctx.needs_approval,
            request_id=ctx.request_id,
            history_blocks=ctx.history_blocks,
            is_cancelled=_is_cancelled,
            emit_progress=_emit_progress,
        )
        ctx.all_results = _exec_result.all_results
        ctx.pending_download_files = _exec_result.pending_download_files
        if _exec_result.active_skill != ctx.active_skill:
            ctx.active_skill = _exec_result.active_skill
        if _exec_result.needs_approval:
            ctx.needs_approval = True
        if _exec_result.clean_markdown is not None:
            ctx.clean_markdown = _exec_result.clean_markdown

        # Auto-fetch missing ENCFF files for multi-file downloads
        _autofetch_md = await auto_fetch_missing_encff(
            ctx.pending_download_files,
            ctx.message if hasattr(ctx, 'message') else "",
        )
        if _autofetch_md is not None:
            ctx.clean_markdown = _autofetch_md

        # ── Provenance ────────────────────────────────────────────────
        _now_ts = datetime.datetime.utcnow().isoformat() + "Z"
        for _src, _results in ctx.all_results.items():
            for _r in _results:
                _entry: dict = {
                    "source": _src,
                    "tool": _r.get("tool", "?"),
                    "params": {
                        k: v for k, v in _r.get("params", {}).items()
                        if k not in ("__routing_error__",)
                    },
                    "timestamp": _now_ts,
                    "success": "data" in _r,
                }
                if "data" in _r:
                    _data = _r["data"]
                    if isinstance(_data, dict):
                        _rows = (
                            _data.get("total")
                            or _data.get("count")
                            or len(_data.get("experiments",
                                             _data.get("files",
                                                        _data.get("entries", []))))
                        )
                        if not _rows and (_data.get("found") is True or _data.get("ok") is True):
                            _rows = 1
                        _entry["rows"] = _rows
                    elif isinstance(_data, list):
                        _entry["rows"] = len(_data)
                if "error" in _r:
                    _entry["error"] = str(_r["error"])[:200]
                ctx.provenance.append(_entry)

        # ── Chain-recovery: LLM echoed find_file JSON ─────────────────
        if not ctx.all_results and '"primary_path"' in ctx.clean_markdown:
            await _chain_recovery(ctx)


register_stage(ToolExecutionStage())


# ── Helpers ────────────────────────────────────────────────────────────────

async def _chain_recovery(ctx: ChatContext) -> None:
    """When the LLM echoed a find_file JSON result, auto-chain to the parse tool."""
    _echo_stripped = re.sub(r'```(?:json)?|```', '', ctx.clean_markdown).strip()
    if '"success": true' not in _echo_stripped and '"success":true' not in _echo_stripped:
        return
    try:
        _echo_data = json.loads(_echo_stripped)
    except (json.JSONDecodeError, ValueError):
        return
    if not (_echo_data.get("success") and _echo_data.get("primary_path") and _echo_data.get("run_uuid")):
        return

    _path = _echo_data["primary_path"]
    _uuid = _echo_data["run_uuid"]
    _tool = _pick_file_tool(_path)
    logger.info("[CHAIN-RECOVERY] LLM echoed find_file JSON — auto-chaining",
                tool=_tool, file_path=_path, run_uuid=_uuid)

    _params: dict = {"run_uuid": _uuid, "file_path": _path}
    if _tool == "parse_csv_file":
        _params["max_rows"] = 100
    elif _tool == "parse_bed_file":
        _params["max_records"] = 100
    elif _tool == "read_file_content":
        _params["preview_lines"] = 50

    try:
        _url = get_service_url("analyzer")
        _mcp = MCPHttpClient(name="analyzer", base_url=_url)
        await _mcp.connect()
        _result = await _mcp.call_tool(_tool, **_params)
        await _mcp.disconnect()
        ctx.clean_markdown = ""
        ctx.all_results["analyzer"] = [{
            "tool": _tool, "params": _params, "data": _result,
        }]
        logger.info("[CHAIN-RECOVERY] Parse succeeded", tool=_tool)
    except Exception as _e:
        logger.error("[CHAIN-RECOVERY] Parse failed", tool=_tool, error=str(_e))
