"""Stage 800 — Override detection: user-data listing, browsing, remote, sync, auto-gen."""
from __future__ import annotations

import re

from sqlalchemy import select

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_sync_handler import _emit_progress
from cortex.data_call_generator import _auto_generate_data_calls
from cortex.llm_validators import _parse_tag_params
from cortex.models import UserFile
from cortex.db import SessionLocal
from cortex.remote_orchestration import (
    _extract_remote_browse_request,
    _resolve_ssh_profile_reference,
)

logger = get_logger(__name__)

PRIORITY = 800


def _suppress_all_tags(ctx: ChatContext) -> None:
    """Clear LLM-generated tags and flags for override paths."""
    ctx.data_call_matches = []
    ctx.legacy_encode_matches = []
    ctx.legacy_analysis_matches = []
    ctx.has_any_tags = False
    ctx.needs_approval = False
    ctx.plot_specs = []
    ctx.injected_dfs = {}
    ctx.injected_previous_data = False
    ctx.auto_calls = []


class OverrideDetectionStage:
    name = "overrides"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        _msg_lower = ctx.user_msg_lower

        # ── Referential-word flag (used later for hallucination guard) ─
        _ref_words = [
            "them", "these", "those", "each", "all of them",
            "each of them", "for those", "the experiments",
            "the accessions", "same",
        ]
        _has_referential = any(w in _msg_lower for w in _ref_words)

        # ── User-data listing override ────────────────────────────────
        _user_data_patterns = [
            r'\b(?:list|show|what)\s+(?:my\s+)?(?:data|data\s+files?)\b',
            r'\b(?:list|show)\s+my\s+files?\b',
            r'\bwhat\s+(?:data\s+)?files?\s+do\s+I\s+have\b',
        ]
        if any(re.search(p, _msg_lower) for p in _user_data_patterns):
            _emit_progress(ctx.request_id, "tools", "Listing your data files...")
            ctx.clean_markdown = _build_user_data_listing(ctx)
            ctx.is_user_data_override = True
            _suppress_all_tags(ctx)
            return

        # ── Browsing-command override ─────────────────────────────────
        _browsing_patterns = [
            r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
            r'\b(?:list|show)\s+(?:the\s+)?(?:project\s+)?files?\b',
        ]
        if any(re.search(p, _msg_lower) for p in _browsing_patterns):
            _browse_calls = _auto_generate_data_calls(
                ctx.message, ctx.active_skill,
                ctx.conversation_history,
                history_blocks=ctx.history_blocks,
                project_dir=ctx.project_dir,
                project_id=ctx.project_id,
            )
            if _browse_calls:
                _browse_calls = [c for c in _browse_calls if c.get("source_key") == "analyzer"]
            if _browse_calls:
                ctx.is_browsing_override = True
                _suppress_all_tags(ctx)
                ctx.auto_calls = _browse_calls
                ctx.clean_markdown = ""
                logger.warning(
                    "Browsing-command override: replacing LLM tags with analyzer calls",
                    skill=ctx.active_skill, calls=len(ctx.auto_calls),
                )
                return

        # ── Remote browsing override ──────────────────────────────────
        _remote_req = _extract_remote_browse_request(ctx.message)
        if _remote_req:
            try:
                _ssh_id, _ssh_nick = await _resolve_ssh_profile_reference(
                    ctx.user.id, None,
                    _remote_req.get("ssh_profile_nickname"),
                )
            except Exception as _err:
                logger.warning("Remote browsing override: SSH profile resolution failed",
                               message=ctx.message, error=str(_err))
            else:
                ctx.active_skill = "remote_execution"
                _remote_params = {"user_id": ctx.user.id, "ssh_profile_id": _ssh_id}
                if _remote_req.get("path"):
                    _remote_params["path"] = _remote_req["path"]
                ctx.is_remote_browsing_override = True
                _suppress_all_tags(ctx)
                ctx.auto_calls = [{
                    "source_type": "service",
                    "source_key": "launchpad",
                    "tool": "list_remote_files",
                    "params": _remote_params,
                }]
                ctx.clean_markdown = ""
                logger.warning(
                    "Remote browsing override: replacing LLM response with Launchpad browse call",
                    skill=ctx.active_skill, profile=_ssh_nick,
                )
                return

        # ── Sync-results override ─────────────────────────────────────
        _sync_calls = _auto_generate_data_calls(
            ctx.message, ctx.active_skill, ctx.conversation_history,
            history_blocks=ctx.history_blocks,
            project_dir=ctx.project_dir,
            project_id=ctx.project_id,
        )
        if _sync_calls and any(c.get("tool") == "sync_job_results" for c in _sync_calls):
            ctx.is_sync_override = True
            _suppress_all_tags(ctx)
            ctx.auto_calls = _sync_calls
            ctx.clean_markdown = ""
            logger.warning("Sync-results override: replacing LLM tags with sync call",
                           calls=len(ctx.auto_calls))
            return

        # ── Auto-generate / hallucination guard ───────────────────────
        if not ctx.has_any_tags and (not ctx.injected_previous_data or ctx.injected_was_capped):
            ctx.auto_calls = _auto_generate_data_calls(
                ctx.message, ctx.active_skill, ctx.conversation_history,
                history_blocks=ctx.history_blocks,
                project_dir=ctx.project_dir,
                project_id=ctx.project_id,
            )
            if ctx.auto_calls:
                logger.warning("LLM failed to generate DATA_CALL tags, auto-generating",
                               count=len(ctx.auto_calls), skill=ctx.active_skill)
                ctx.clean_markdown = ""
            elif ctx.active_skill in ("ENCODE_Search", "ENCODE_LongRead"):
                _halluc_patterns = [
                    r'there are [\d,]+ .* experiments',
                    r'found [\d,]+ .* results',
                    r'interactive table below',
                    r'experiment list .* below',
                    r'[\d,]+ experiments in ENCODE',
                ]
                if any(re.search(p, ctx.clean_markdown, re.IGNORECASE) for p in _halluc_patterns):
                    logger.warning(
                        "LLM hallucinated ENCODE results without tool execution",
                        skill=ctx.active_skill,
                        response_preview=ctx.clean_markdown[:200],
                    )
                    ctx.clean_markdown = (
                        "I wasn't able to find an ENCODE query tool for that "
                        "search term. Could you clarify — is this a **cell line**, "
                        "**tissue**, **target protein**, or **assay type**? "
                        "That will help me pick the right query."
                    )
        elif ctx.has_any_tags and _has_referential:
            _validate_referential_accessions(ctx)


register_stage(OverrideDetectionStage())


# ── Helpers ────────────────────────────────────────────────────────────────

def _build_user_data_listing(ctx: ChatContext) -> str:
    """Query UserFile table and format a markdown listing."""
    _ud_session = SessionLocal()
    try:
        _ud_rows = _ud_session.execute(
            select(UserFile)
            .where(UserFile.user_id == ctx.user.id)
            .order_by(UserFile.filename)
        ).scalars().all()
        if _ud_rows:
            _count = len(_ud_rows)
            lines = [f"\U0001f4c2 **Your Data Files** ({_count} file{'s' if _count != 1 else ''})\n"]
            lines.append("| File | Size | Source | Accession | Sample | Added |")
            lines.append("|------|------|--------|-----------|--------|-------|")
            for _uf in _ud_rows:
                _sz = _format_size(_uf.size_bytes or 0)
                _src = _uf.source or "—"
                _acc = _uf.encode_accession or "—"
                _samp = _uf.sample_name or "—"
                _date = str(_uf.created_at)[:10] if _uf.created_at else "—"
                lines.append(f"| `{_uf.filename}` | {_sz} | {_src} | {_acc} | {_samp} | {_date} |")
            return "\n".join(lines)

        # Fallback: scan disk
        _disk_files = []
        if hasattr(ctx.user, "username") and ctx.user.username:
            try:
                from cortex.user_jail import get_user_data_dir
                _data_dir = get_user_data_dir(ctx.user.username)
                if _data_dir.is_dir():
                    _disk_files = sorted([
                        f for f in _data_dir.iterdir()
                        if f.is_file() and not f.name.startswith(".")
                    ])
            except Exception:
                pass
        if _disk_files:
            _count = len(_disk_files)
            lines = [f"\U0001f4c2 **Your Data Files** ({_count} file{'s' if _count != 1 else ''})\n"]
            lines.append("| File | Size |")
            lines.append("|------|------|")
            for _df in _disk_files:
                lines.append(f"| `{_df.name}` | {_format_size(_df.stat().st_size)} |")
            return "\n".join(lines)

        return (
            "\U0001f4c2 **Your Data Files**\n\n"
            "No data files found yet. You can download files from ENCODE "
            "or upload your own to get started."
        )
    finally:
        _ud_session.close()


def _format_size(sz: int) -> str:
    if sz >= 1024 ** 3:
        return f"{sz / (1024 ** 3):.2f} GB"
    if sz >= 1024 ** 2:
        return f"{sz / (1024 ** 2):.1f} MB"
    if sz > 0:
        return f"{sz / 1024:.0f} KB"
    return "—"


def _validate_referential_accessions(ctx: ChatContext) -> None:
    """Check that LLM-produced accessions actually appear in history."""
    _llm_accessions: set[str] = set()
    for _m in ctx.data_call_matches:
        _p = _parse_tag_params(_m.group(4))
        if _p.get("accession"):
            _llm_accessions.add(_p["accession"].upper())
    for _m in ctx.legacy_encode_matches:
        _p = _parse_tag_params(_m.group(2))
        if _p.get("accession"):
            _llm_accessions.add(_p["accession"].upper())

    _history_accessions: set[str] = set()
    if ctx.conversation_history:
        for _msg in ctx.conversation_history:
            _content = re.sub(
                r'<details>.*?</details>', '',
                _msg.get("content", ""), flags=re.DOTALL,
            )
            _found = re.findall(r'(ENCSR\d{3}[A-Z]{3})', _content, re.IGNORECASE)
            _history_accessions.update(a.upper() for a in _found)

    if _llm_accessions and _history_accessions and not _llm_accessions & _history_accessions:
        logger.warning(
            "LLM hallucinated accessions in DATA_CALL tags, overriding",
            llm_accessions=sorted(_llm_accessions),
            history_accessions=sorted(_history_accessions),
        )
        ctx.auto_calls = _auto_generate_data_calls(
            ctx.message, ctx.active_skill, ctx.conversation_history,
            history_blocks=ctx.history_blocks, project_dir=ctx.project_dir,
            project_id=ctx.project_id,
        )
        if ctx.auto_calls:
            ctx.data_call_matches = []
            ctx.legacy_encode_matches = []
            ctx.legacy_analysis_matches = []
            ctx.has_any_tags = False
            ctx.clean_markdown = ""
