"""Stage 700 — Parse approval, DATA_CALL, PLOT tags; clean markdown."""
from __future__ import annotations

import re

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _extract_plot_style_params
from cortex.tag_parser import (
    apply_plot_code_fallback,
    apply_response_corrections,
    clean_tags_from_markdown,
    fix_hallucinated_accessions,
    override_hallucinated_df_refs,
    parse_approval_tag,
    parse_data_tags,
    parse_plot_tags,
    suppress_tags_for_plot_command,
    user_wants_plot as _user_wants_plot_check,
)

logger = get_logger(__name__)

PRIORITY = 700


class TagParsingStage:
    name = "tag_parsing"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        ctx.needs_approval, ctx.raw_response = parse_approval_tag(
            ctx.raw_response, ctx.active_skill,
        )
        corrected_response, _fallback_fixes = apply_response_corrections(ctx.raw_response)
        ctx.data_call_matches, ctx.legacy_encode_matches, ctx.legacy_analysis_matches = (
            parse_data_tags(corrected_response)
        )

        # Plot tags
        ctx.plot_specs = parse_plot_tags(corrected_response)
        override_hallucinated_df_refs(
            ctx.plot_specs, ctx.message,
            ctx.conv_state.latest_dataframe if ctx.conv_state else None,
        )

        ctx.plot_specs, corrected_response = apply_plot_code_fallback(
            ctx.plot_specs, ctx.message, corrected_response,
            ctx.injected_dfs,
            ctx.conv_state.latest_dataframe if ctx.conv_state else None,
            _extract_plot_style_params,
        )
        (
            ctx.data_call_matches,
            ctx.legacy_encode_matches,
            ctx.legacy_analysis_matches,
            ctx.needs_approval,
            corrected_response,
        ) = suppress_tags_for_plot_command(
            ctx.message, ctx.plot_specs,
            ctx.data_call_matches, ctx.legacy_encode_matches,
            ctx.legacy_analysis_matches, ctx.needs_approval,
            corrected_response,
        )

        ctx.clean_markdown = clean_tags_from_markdown(corrected_response, ctx.plot_specs)
        ctx.clean_markdown = fix_hallucinated_accessions(ctx.clean_markdown, ctx.message)

        ctx.has_any_tags = bool(
            ctx.data_call_matches or ctx.legacy_encode_matches or ctx.legacy_analysis_matches
        )

        # ── Suppress redundant DATA_CALLs when injected data present ──
        if ctx.injected_dfs and not ctx.injected_was_capped:
            _suppressed = []
            if ctx.data_call_matches:
                _suppressed.extend(m.group(3) for m in ctx.data_call_matches)
                ctx.data_call_matches = []
            if ctx.legacy_encode_matches:
                _suppressed.extend(f"legacy:{m.group(1)}" for m in ctx.legacy_encode_matches)
                ctx.legacy_encode_matches = []
            if _suppressed:
                logger.info(
                    "Suppressed ALL DATA_CALL tags (injected data already present)",
                    suppressed_tools=_suppressed,
                )
                ctx.has_any_tags = bool(
                    ctx.data_call_matches or ctx.legacy_encode_matches
                    or ctx.legacy_analysis_matches
                )
                ctx.inject_debug["suppressed_calls"] = _suppressed
            ctx.inject_debug["injected_was_capped"] = ctx.injected_was_capped


register_stage(TagParsingStage())
