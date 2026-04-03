"""Stage 1000 — Second-pass LLM analysis of tool results."""
from __future__ import annotations

import re

from fastapi.concurrency import run_in_threadpool

from atlas import format_results
from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _emit_progress, _is_cancelled
from cortex.config import SERVICE_REGISTRY
from cortex.llm_validators import get_block_payload, _parse_tag_params
from cortex.remote_orchestration import _build_remote_stage_approval_context, _update_project_block_payload
from cortex.tool_dispatch import ChatCancelled
import cortex.job_parameters as job_parameters

logger = get_logger(__name__)

PRIORITY = 1000

_PLOT_TAG_RE = r'\[\[PLOT:\s*(.*?)\]\]'


class SecondPassStage:
    name = "second_pass"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return bool(ctx.all_results) and not ctx.skip_second_pass

    async def run(self, ctx: ChatContext) -> None:
        all_results = ctx.all_results

        has_real_data = any(
            any("data" in r for r in results)
            for results in all_results.values()
        )

        # ── Format results ────────────────────────────────────────────
        formatted_parts = []
        _error_blocks = []
        for source_key, results in all_results.items():
            if results:
                entry = SERVICE_REGISTRY.get(source_key)
                formatted_parts.append(format_results(source_key, results, registry_entry=entry))
                for _r in results:
                    if "error" in _r:
                        _err_msg = _r["error"]
                        _err_type = _classify_error(_err_msg)
                        _error_blocks.append(
                            f"[TOOL_ERROR: tool={_r.get('tool', '?')}, "
                            f"error_type={_err_type}, "
                            f"message=\"{_err_msg}\"]\n"
                            f"Follow the TOOL FAILURE RULES in your system prompt."
                        )

        formatted_data = "\n".join(formatted_parts)

        # Provenance headers
        _prov_block = ""
        if ctx.provenance:
            _prov_lines = []
            for _p in ctx.provenance:
                if _p.get("success"):
                    _prov_lines.append(
                        f"[TOOL_RESULT: source={_p['source']}, tool={_p['tool']}, "
                        f"params={{{', '.join(f'{k}={v}' for k, v in _p.get('params', {}).items())}}}, "
                        f"rows={_p.get('rows', '?')}, timestamp={_p['timestamp']}]"
                    )
            if _prov_lines:
                _prov_block = "\n".join(_prov_lines)
                formatted_data = _prov_block + "\n\n" + formatted_data
        if _error_blocks and not has_real_data:
            formatted_data += "\n\n" + "\n".join(_error_blocks)

        logger.info("Formatted tool results", size=len(formatted_data),
                     has_real_data=has_real_data, preview=formatted_data[:500])

        # ── Determine result presentation mode ────────────────────────
        _browsing_tools = {"list_job_files", "categorize_job_files", "list_remote_files"}
        _all_tools_used: set[str] = set()
        _has_download_chain = False
        for _src_results in all_results.values():
            for _r in _src_results:
                _all_tools_used.add(_r.get("tool", ""))
                if _r.get("_chain") == "download":
                    _has_download_chain = True
        _is_browsing = bool(_all_tools_used) and _all_tools_used <= _browsing_tools
        _is_sync = "sync_job_results" in _all_tools_used
        _is_download = _has_download_chain or ctx.active_skill == "download_files"

        # ── Remote stage approval context ─────────────────────────────
        ctx.remote_stage_approval_context = await _build_remote_stage_approval_context(
            ctx.session,
            project_id=ctx.project_id,
            owner_id=ctx.user.id,
            user_message=ctx.message,
            active_skill=ctx.active_skill,
            all_results=all_results,
            extract_params=job_parameters.extract_job_parameters_from_conversation,
        )

        _display_data = "\n".join(formatted_parts)

        if ctx.remote_stage_approval_context and has_real_data:
            ctx.needs_approval = True
            if _prov_block:
                _display_data = _prov_block + "\n\n" + _display_data
            ctx.clean_markdown = str(
                ctx.remote_stage_approval_context.get("summary") or ""
            ).rstrip()
            if _display_data.strip():
                ctx.clean_markdown += (
                    "\n\n<details><summary>📋 Raw Query Results (click to expand)</summary>\n\n"
                    + _display_data + "\n\n</details>"
                )

        elif _is_browsing and has_real_data and formatted_data.strip():
            if _prov_block:
                _display_data += (
                    "\n\n<details><summary>📋 Query Details</summary>\n\n"
                    + _prov_block + "\n\n</details>"
                )
            ctx.clean_markdown = _display_data

        elif _is_download and has_real_data:
            if _prov_block:
                _display_data = _prov_block + "\n\n" + _display_data
            ctx.clean_markdown = ctx.clean_markdown.rstrip() + (
                "\n\n<details><summary>📋 File Metadata Details</summary>\n\n"
                + _display_data + "\n\n</details>"
            )

        elif _is_browsing and not has_real_data:
            _err_detail = ""
            for _src_results in all_results.values():
                for _r in _src_results:
                    if "error" in _r:
                        _err_detail = _r.get("error", "")
                        break
            ctx.clean_markdown = (
                f"⚠️ {_err_detail}\n\n"
                "Try one of these:\n"
                "- **list files** — files in the current workflow\n"
                "- **list project files** — top-level project contents\n"
                "- **list project files in data** — a subfolder under the project root\n"
                "- **list files in workflow2/annot** — a specific workflow subfolder\n"
                "- **list workflows** — all workflows in the project"
            )

        elif _is_sync:
            _handle_sync_presentation(ctx, all_results)

        elif has_real_data and formatted_data.strip():
            # Cap data size
            MAX_DATA_CHARS = 12000
            if len(formatted_data) > MAX_DATA_CHARS:
                logger.warning("Truncating tool results for LLM",
                               original_size=len(formatted_data), max_size=MAX_DATA_CHARS)
                _summary_end = formatted_data.find("\n**Total:")
                if _summary_end != -1:
                    _nl = formatted_data.find("\n", _summary_end + 1)
                    _cut = _nl if _nl != -1 else _summary_end + 200
                    formatted_data = (
                        formatted_data[:_cut]
                        + "\n\n*(The full result set is shown as an interactive "
                        "dataframe above — only the summary is shown here.)*"
                    )
                else:
                    formatted_data = (
                        formatted_data[:MAX_DATA_CHARS]
                        + "\n\n*(Results truncated — full data in interactive dataframe above.)*"
                    )

            # Second-pass LLM analysis
            logger.info("Running second-pass LLM analysis", data_size=len(formatted_data))
            if _is_cancelled(ctx.request_id):
                raise ChatCancelled(
                    "analyzing",
                    "Tool calls completed but cancelled before generating the summary. "
                    "You can re-send your message to see the results.",
                )
            _emit_progress(ctx.request_id, "analyzing", "Analyzing results...")
            analyzed_response, ctx.analyze_usage = await run_in_threadpool(
                ctx.engine.analyze_results,
                ctx.message,
                ctx.clean_markdown,
                formatted_data,
                ctx.active_skill,
                ctx.conversation_history,
            )

            ctx.clean_markdown = (
                analyzed_response + "\n\n---\n\n"
                "<details><summary>📋 Raw Query Results (click to expand)</summary>\n\n"
                + formatted_data + "\n\n</details>"
            )

            # Post-second-pass plot tag parsing
            _post_matches = list(re.finditer(_PLOT_TAG_RE, analyzed_response, re.DOTALL))
            if _post_matches:
                for _m in _post_matches:
                    _pp = _parse_tag_params(_m.group(1))
                    _df_ref = _pp.get("df", "")
                    _df_id_m = re.match(r'(?:DF)?\s*(\d+)', _df_ref, re.IGNORECASE)
                    _pp["df_id"] = int(_df_id_m.group(1)) if _df_id_m else None
                    if "type" not in _pp:
                        _pp["type"] = "bar"
                    ctx.plot_specs.append(_pp)
                logger.info("Parsed PLOT tags from second-pass", count=len(_post_matches))
                ctx.clean_markdown = re.sub(
                    _PLOT_TAG_RE, '', ctx.clean_markdown, flags=re.DOTALL,
                ).strip()
        else:
            ctx.clean_markdown += formatted_data


register_stage(SecondPassStage())


# ── Helpers ────────────────────────────────────────────────────────────────

def _classify_error(msg: str) -> str:
    ml = msg.lower()
    if "not found" in ml or "404" in msg:
        return "not_found"
    if "connection" in ml or "timeout" in ml:
        return "connection_failed"
    if "permission" in ml or "403" in msg:
        return "permission_denied"
    if "routing" in ml:
        return "routing_error"
    return "unknown"


def _handle_sync_presentation(ctx: ChatContext, all_results: dict) -> None:
    """Format sync_job_results output for the user."""
    _sync_data: dict = {}
    _sync_error = ""
    for _src_results in all_results.values():
        for _r in _src_results:
            if _r.get("tool") == "sync_job_results":
                if "data" in _r:
                    _sync_data = _r["data"] if isinstance(_r["data"], dict) else {}
                elif "error" in _r:
                    _sync_error = str(_r["error"])
                break
    if _sync_error:
        ctx.clean_markdown = f"⚠️ Sync failed: {_sync_error}"
        return

    _status = _sync_data.get("status", "")
    _msg = _sync_data.get("message", "")
    if _status in ("sync_started", "sync_in_progress"):
        ctx.clean_markdown = (
            f"📥 {_msg or 'Result synchronization started.'} "
            "You can track progress in the task dock below."
        )
        ctx.sync_run_uuid = _sync_data.get("run_uuid", "")
        if ctx.sync_run_uuid:
            from cortex.models import ProjectBlock
            _job_block = (
                ctx.session.query(ProjectBlock)
                .filter(
                    ProjectBlock.project_id == ctx.project_id,
                    ProjectBlock.type == "EXECUTION_JOB",
                    ProjectBlock.payload_json.contains(ctx.sync_run_uuid),
                )
                .order_by(ProjectBlock.created_at.desc())
                .first()
            )
            if _job_block:
                _update_project_block_payload(
                    ctx.session,
                    _job_block.id,
                    {"job_status": {
                        **get_block_payload(_job_block).get("job_status", {}),
                        "transfer_state": "downloading_outputs",
                    }},
                )
    elif _status == "already_synced":
        ctx.clean_markdown = f"✅ {_msg or 'Results are already synced locally.'}"
    elif _status == "not_applicable":
        ctx.clean_markdown = f"ℹ️ {_msg or 'Manual sync is not applicable for this job.'}"
    else:
        ctx.clean_markdown = _msg or "Sync request submitted."
