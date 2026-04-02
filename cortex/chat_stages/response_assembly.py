"""Stage 1100 — DF/image extraction, block creation, response assembly."""
from __future__ import annotations

import re
from collections import defaultdict

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_dataframes import (
    assign_df_ids,
    collapse_competing_specs,
    deduplicate_plot_specs,
    extract_embedded_dataframes,
    extract_embedded_images,
    post_df_assignment_plot_fallback,
    rebind_plots_to_new_df,
)
from cortex.chat_sync_handler import _emit_progress, _extract_plot_style_params
from cortex.db import row_to_dict
from cortex.db_helpers import _create_block_internal, save_conversation_message
from cortex.llm_validators import get_block_payload
import cortex.job_parameters as job_parameters

logger = get_logger(__name__)

PRIORITY = 1100


class ResponseAssemblyStage:
    name = "response_assembly"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        # ── Extract DFs & images ──────────────────────────────────────
        ctx.embedded_dataframes = extract_embedded_dataframes(ctx.all_results, ctx.message)
        ctx.embedded_images = extract_embedded_images(ctx.all_results)

        _emit_progress(ctx.request_id, "done", "Complete")

        # ── Assign DF IDs and rebind plots ────────────────────────────
        assign_df_ids(ctx.embedded_dataframes, ctx.history_blocks, ctx.injected_dfs)
        rebind_plots_to_new_df(ctx.plot_specs, ctx.embedded_dataframes, ctx.message)

        # Update conv_state with newly-embedded DFs
        if ctx.embedded_dataframes:
            _max_new_id = 0
            _edf_label = ""
            _edf_rows = "?"
            for _val in ctx.embedded_dataframes.values():
                _meta = _val.get("metadata", {})
                _eid = _meta.get("df_id")
                if isinstance(_eid, int) and _eid > _max_new_id:
                    _max_new_id = _eid
                    _edf_label = _meta.get("label", "")
                    _edf_rows = _meta.get("row_count", "?")
            if _max_new_id > 0:
                _new_latest = f"DF{_max_new_id}"
                ctx.conv_state.latest_dataframe = _new_latest
                if not any(
                    k.startswith(_new_latest + " ") or k == _new_latest
                    for k in ctx.conv_state.known_dataframes
                ):
                    ctx.conv_state.known_dataframes.append(
                        f"{_new_latest} ({_edf_label}, {_edf_rows} rows)"
                    )

        post_df_assignment_plot_fallback(
            ctx.plot_specs, ctx.embedded_dataframes, ctx.message,
            _extract_plot_style_params,
        )
        ctx.plot_specs = deduplicate_plot_specs(ctx.plot_specs)
        ctx.plot_specs = collapse_competing_specs(
            ctx.plot_specs, ctx.message, _extract_plot_style_params,
        )

        # ── Token totals ──────────────────────────────────────────────
        _total_usage = {
            "prompt_tokens": ctx.think_usage["prompt_tokens"] + ctx.analyze_usage["prompt_tokens"],
            "completion_tokens": ctx.think_usage["completion_tokens"] + ctx.analyze_usage["completion_tokens"],
            "total_tokens": ctx.think_usage["total_tokens"] + ctx.analyze_usage["total_tokens"],
            "model": ctx.engine.model_name,
        }

        # ── AGENT_PLAN block ──────────────────────────────────────────
        _plan_payload = {
            "markdown": ctx.clean_markdown,
            "skill": ctx.active_skill,
            "model": ctx.engine.model_name,
            "tokens": _total_usage,
            "state": ctx.conv_state.to_dict(),
        }
        if ctx.embedded_dataframes:
            _plan_payload["_dataframes"] = ctx.embedded_dataframes
        if ctx.embedded_images:
            _plan_payload["_images"] = ctx.embedded_images
        if ctx.provenance:
            _plan_payload["_provenance"] = ctx.provenance
        if ctx.sync_run_uuid:
            _plan_payload["_sync_run_uuid"] = ctx.sync_run_uuid

        # Debug payload
        _debug = dict(ctx.inject_debug)
        _debug["has_injected_dfs"] = bool(ctx.injected_dfs)
        _debug["injected_previous_data"] = ctx.injected_previous_data
        _debug["auto_calls_count"] = len(ctx.auto_calls)
        if ctx.auto_calls:
            _debug["auto_calls"] = [str(c) for c in ctx.auto_calls[:10]]
        _debug["llm_tags_remaining"] = {
            "data_call": len(ctx.data_call_matches),
            "legacy_encode": len(ctx.legacy_encode_matches),
            "legacy_analysis": len(ctx.legacy_analysis_matches),
        }
        _debug["embedded_df_count"] = len(ctx.embedded_dataframes)
        _debug["active_skill"] = ctx.active_skill
        _debug["pre_llm_skill"] = ctx.pre_llm_skill
        _debug["auto_skill_detected"] = ctx.auto_skill
        _debug["requested_skill"] = ctx.skill
        _debug["llm_raw_response_preview"] = ctx.raw_response[:800] if ctx.raw_response else ""
        if ctx.output_violations:
            _debug["output_violations"] = ctx.output_violations
        _plan_payload["_debug"] = _debug

        agent_block = _create_block_internal(
            ctx.session, ctx.project_id, "AGENT_PLAN",
            _plan_payload, status="DONE", owner_id=ctx.user.id,
        )

        await save_conversation_message(
            ctx.session, ctx.project_id, ctx.user.id, "assistant",
            ctx.clean_markdown,
            token_data=_total_usage, model_name=ctx.engine.model_name,
        )

        # ── AGENT_PLOT blocks ─────────────────────────────────────────
        plot_blocks = []
        if ctx.plot_specs:
            _all_df_map = _build_df_map(ctx)
            _plots_by_df = defaultdict(list)
            for _ps in ctx.plot_specs:
                _df_id = _ps.get("df_id")
                if _df_id is None:
                    logger.warning("Skipping PLOT spec with no df_id", spec=_ps)
                    continue
                _plots_by_df[_df_id].append(_ps)

            for _df_key, _chart_group in _plots_by_df.items():
                _charts = []
                for _cs in _chart_group:
                    _entry = {
                        "type": _cs.get("type", "histogram"),
                        "df_id": _cs.get("df_id"),
                        "x": _cs.get("x"),
                        "y": _cs.get("y"),
                        "color": _cs.get("color"),
                        "palette": _cs.get("palette"),
                        "title": _cs.get("title"),
                        "agg": _cs.get("agg"),
                    }
                    _charts.append({k: v for k, v in _entry.items() if v is not None})

                _plot_block = _create_block_internal(
                    ctx.session, ctx.project_id, "AGENT_PLOT",
                    {"charts": _charts, "skill": ctx.active_skill, "model": ctx.engine.model_name},
                    status="DONE", owner_id=ctx.user.id,
                )
                plot_blocks.append(_plot_block)
                logger.info("Created AGENT_PLOT block",
                            block_id=_plot_block.id, chart_count=len(_charts), df_key=_df_key)

        # ── APPROVAL_GATE block ───────────────────────────────────────
        gate_block = None
        if ctx.needs_approval:
            gate_block = await _build_approval_gate(ctx)

        # Save user conversation message
        await save_conversation_message(ctx.session, ctx.project_id, ctx.user.id, "user", ctx.message)

        ctx.short_circuit({
            "status": "ok",
            "user_block": row_to_dict(ctx.user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": row_to_dict(gate_block) if gate_block else None,
            "plot_blocks": [row_to_dict(pb) for pb in plot_blocks] if plot_blocks else None,
        })


register_stage(ResponseAssemblyStage())


# ── Helpers ────────────────────────────────────────────────────────────────

def _build_df_map(ctx: ChatContext) -> dict:
    """Build a map of df_id → {columns, data, metadata, label}."""
    _all = {}
    for _blk in ctx.history_blocks:
        if _blk.type == "AGENT_PLAN":
            _dfs = get_block_payload(_blk).get("_dataframes", {})
            for _name, _data in _dfs.items():
                _id = _data.get("metadata", {}).get("df_id")
                if _id is not None:
                    _all[_id] = {**_data, "label": _name}
    for _name, _data in ctx.embedded_dataframes.items():
        _id = _data.get("metadata", {}).get("df_id")
        if _id is not None:
            _all[_id] = {**_data, "label": _name}
    return _all


async def _build_approval_gate(ctx: ChatContext):
    """Create the APPROVAL_GATE block."""
    gate_action = "download" if ctx.active_skill == "download_files" else "job"
    gate_skill = ctx.active_skill

    if gate_action == "download" and ctx.pending_download_files:
        _total_bytes = sum(f.get("size_bytes") or 0 for f in ctx.pending_download_files)
        from cortex.user_jail import get_user_data_dir
        _dl_target = str(get_user_data_dir(ctx.user.username))
        extracted_params = {
            "gate_action": "download",
            "files": ctx.pending_download_files,
            "total_size_bytes": _total_bytes,
            "target_dir": _dl_target,
        }
    else:
        if (ctx.remote_stage_approval_context
                and isinstance(ctx.remote_stage_approval_context.get("params"), dict)):
            extracted_params = dict(ctx.remote_stage_approval_context.get("params") or {})
        else:
            extracted_params = await job_parameters.extract_job_parameters_from_conversation(
                ctx.session, ctx.project_id,
            )
        if isinstance(extracted_params, dict):
            gate_action = extracted_params.get("gate_action") or gate_action
            if (gate_action == "remote_stage"
                    or (extracted_params.get("remote_action") or "").strip().lower() == "stage_only"):
                gate_action = "remote_stage"
                gate_skill = "remote_execution"
        if (extracted_params.get("execution_mode") or "local") == "slurm":
            gate_skill = "remote_execution"

    return _create_block_internal(
        ctx.session, ctx.project_id, "APPROVAL_GATE",
        {
            "label": "Do you authorize the Agent to proceed with this plan?",
            "extracted_params": extracted_params,
            "cache_preflight": extracted_params.get("cache_preflight") if isinstance(extracted_params, dict) else None,
            "gate_action": gate_action,
            "attempt_number": 1,
            "rejection_history": [],
            "skill": gate_skill,
            "model": ctx.engine.model_name,
        },
        status="PENDING",
        owner_id=ctx.user.id,
    )
