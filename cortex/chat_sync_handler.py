"""Synchronous helpers for chat request handling.

Small pure-ish functions called from ``chat_with_agent`` in *app.py*.
Extracted to keep app.py focused on request routing.
"""
from __future__ import annotations

import re
import time as _time
from typing import TYPE_CHECKING

from common.logging_config import get_logger
from cortex.llm_validators import get_block_payload
from cortex.db_helpers import _create_block_internal, save_conversation_message
from cortex.db import row_to_dict

if TYPE_CHECKING:
    from cortex.app import ChatRequest

logger = get_logger(__name__)

# --- CHAT PROGRESS TRACKING ---
# Tracks processing stages for active chat requests so the UI can poll for updates.
# Entries are cleaned up after 5 minutes.
_chat_progress: dict[str, dict] = {}  # request_id -> {"stage": str, "detail": str, "ts": float}


def _detect_prompt_request(message: str) -> str | None:
    """Detect whether the user is asking to inspect the current system prompt."""
    msg = (message or "").lower()
    prompt_phrases = [
        "system prompt",
        "current prompt",
        "your prompt",
        "show prompt",
        "show me the prompt",
        "show me your prompt",
        "show me the system prompt",
        "what is your system prompt",
        "report your system prompt",
    ]
    if not any(phrase in msg for phrase in prompt_phrases):
        return None

    if any(phrase in msg for phrase in ["first pass", "first-pass", "planning prompt", "planning system prompt"]):
        return "first_pass"
    if any(phrase in msg for phrase in ["second pass", "second-pass", "analysis prompt", "summary prompt"]):
        return "second_pass"
    return "ambiguous"


def _format_prompt_report(prompt_type: str, skill_key: str, model_name: str, prompt_text: str) -> str:
    """Format a rendered prompt for chat display without losing its exact text."""
    label = "First-pass system prompt" if prompt_type == "first_pass" else "Second-pass system prompt"
    return (
        f"### {label}\n\n"
        f"- Skill: **{skill_key}**\n"
        f"- Model: **{model_name}**\n\n"
        "`````text\n"
        f"{prompt_text}\n"
        "`````"
    )


# ── DF inspection helpers ──────────────────────────────────────────────

_LIST_DF_RE = re.compile(
    r"^(?:list|show)\s+(?:dfs|dataframes|data\s*frames)$"
)
_HEAD_DF_RE = re.compile(
    r"^head\s+(?:df\s*(\d+)|df)\s*(\d+)?$"
)
# Named remembered DF: "head c2c12DF" / "head c2c12DF 20"
_HEAD_NAMED_RE = re.compile(
    r"^head\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+(\d+))?$"
)


def _detect_df_command(msg_lower: str) -> dict | None:
    """Return ``{"action": "list"}`` or ``{"action": "head", ...}``
    if *msg_lower* is a DF inspection command, otherwise ``None``.

    The ``"df_id"`` value is an ``int`` for numeric DFs or a ``str`` name
    for remembered named DFs.
    """
    msg = msg_lower.strip()
    if _LIST_DF_RE.match(msg):
        return {"action": "list"}
    m = _HEAD_DF_RE.match(msg)
    if m:
        df_id = int(m.group(1)) if m.group(1) else None
        n = int(m.group(2)) if m.group(2) else 10
        return {"action": "head", "df_id": df_id, "n": n}
    # Try named DF ("head myName 20") — skip bare "head df" already caught above
    m = _HEAD_NAMED_RE.match(msg)
    if m and m.group(1) not in ("df", "dfs", "dataframes"):
        n = int(m.group(2)) if m.group(2) else 10
        return {"action": "head", "df_id": m.group(1), "n": n}
    return None


def _collect_df_map(history_blocks, *, db=None, user_id=None, project_id=None) -> dict:
    """Build ``{key: {columns, data, row_count, label}}`` from all AGENT_PLAN blocks.

    Keys are ``int`` for conversation DFs and ``str`` (the user-given name)
    for remembered named DFs.  Unnamed remembered DFs use integer IDs 900+.
    """
    df_map: dict[int | str, dict] = {}
    for blk in history_blocks:
        if blk.type != "AGENT_PLAN":
            continue
        _pl = get_block_payload(blk)
        for _key, _val in _pl.get("_dataframes", {}).items():
            _meta = _val.get("metadata", {})
            _df_id = _meta.get("df_id")
            if not isinstance(_df_id, int):
                continue
            if not _meta.get("visible", True):
                continue
            df_map[_df_id] = {
                "columns": _val.get("columns", []),
                "data": _val.get("data", []),
                "row_count": _val.get("row_count", len(_val.get("data", []))),
                "label": _meta.get("label", _key),
            }

    # Merge remembered dataframe memories (IDs 900+)
    if db is not None and user_id is not None:
        from cortex.memory_service import get_remembered_df_map
        remembered = get_remembered_df_map(db, user_id, project_id)
        df_map.update(remembered)

    return df_map


def _render_list_dfs(df_map: dict) -> str:
    """Render a markdown table listing all known dataframes."""
    if not df_map:
        return "No dataframes in this conversation yet."
    lines = ["| DF | Label | Rows | Columns |", "| --- | --- | ---: | --- |"]
    # Sort: numeric keys first (by value), then string keys alphabetically
    int_keys = sorted(k for k in df_map if isinstance(k, int))
    str_keys = sorted(k for k in df_map if isinstance(k, str))
    for df_id in int_keys + str_keys:
        d = df_map[df_id]
        cols_str = ", ".join(d["columns"][:12])
        if len(d["columns"]) > 12:
            cols_str += f" … (+{len(d['columns']) - 12} more)"
        # Named remembered DFs show the name; numeric DFs show DF<n>
        id_label = f"**{df_id}**" if isinstance(df_id, str) else f"**DF{df_id}**"
        lines.append(
            f"| {id_label} | {d['label']} | {d['row_count']} | {cols_str} |"
        )
    return "\n".join(lines)


def _render_head_df(df_map: dict, df_id: int | str | None, n: int) -> str:
    """Render the first *n* rows of a dataframe as a markdown table."""
    if not df_map:
        return "No dataframes in this conversation yet."
    if df_id is None:
        return "No dataframes in this conversation yet."

    # For string df_id, also try case-insensitive lookup
    lookup_key = df_id
    if isinstance(df_id, str) and df_id not in df_map:
        for k in df_map:
            if isinstance(k, str) and k.lower() == df_id.lower():
                lookup_key = k
                break

    if lookup_key not in df_map:
        int_labels = [f"DF{k}" for k in sorted(k2 for k2 in df_map if isinstance(k2, int))]
        str_labels = sorted(k for k in df_map if isinstance(k, str))
        available = ", ".join(int_labels + str_labels)
        name = df_id if isinstance(df_id, str) else f"DF{df_id}"
        return f"**{name}** not found. Available: {available}. Use `list dfs` to see details."
    d = df_map[lookup_key]
    cols = d["columns"]
    rows = d["data"][:n]
    total = d["row_count"]
    showing = min(n, len(rows))

    name = lookup_key if isinstance(lookup_key, str) else f"DF{lookup_key}"
    header = f"**{name}** — {d['label']} ({total} rows, showing first {showing})\n\n"
    if not rows:
        return header + "_No data rows._"

    # Build markdown table with all columns
    hdr_line = "| " + " | ".join(str(c) for c in cols) + " |"
    sep_line = "| " + " | ".join("---" for _ in cols) + " |"
    body_lines = []
    for row in rows:
        cells = []
        for c in cols:
            val = row.get(c, "") if isinstance(row, dict) else ""
            # Escape pipe chars in cell values
            cells.append(str(val).replace("|", "\\|"))
        body_lines.append("| " + " | ".join(cells) + " |")

    return header + "\n".join([hdr_line, sep_line] + body_lines)


async def _create_prompt_response(
    session,
    req: ChatRequest,
    user_block,
    user_id: str,
    active_skill: str,
    model_name: str,
    markdown: str,
    prompt_type: str | None = None,
):
    """Create a deterministic AGENT_PLAN response for prompt inspection or clarification."""
    payload = {
        "markdown": markdown,
        "skill": active_skill or req.skill or "welcome",
        "model": model_name,
        "tokens": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "model": model_name,
        },
        "_debug": {
            "prompt_inspection": True,
            "prompt_type": prompt_type,
            "requested_skill": req.skill,
            "active_skill": active_skill,
        },
    }
    agent_block = _create_block_internal(
        session,
        req.project_id,
        "AGENT_PLAN",
        payload,
        status="DONE",
        owner_id=user_id,
    )
    await save_conversation_message(
        session,
        req.project_id,
        user_id,
        "assistant",
        markdown,
        token_data=payload["tokens"],
        model_name=model_name,
    )
    return {
        "status": "ok",
        "user_block": row_to_dict(user_block),
        "agent_block": row_to_dict(agent_block),
        "gate_block": None,
    }

def _emit_progress(request_id: str, stage: str, detail: str = ""):
    """Record a processing stage for a chat request."""
    if not request_id:
        return
    # Preserve the cancelled flag across progress updates
    _cancelled = _chat_progress.get(request_id, {}).get("cancelled", False)
    _chat_progress[request_id] = {
        "stage": stage,
        "detail": detail,
        "ts": _time.time(),
        "cancelled": _cancelled,
    }
    # Housekeeping: remove entries older than 5 minutes
    cutoff = _time.time() - 300
    stale = [k for k, v in _chat_progress.items() if v["ts"] < cutoff]
    for k in stale:
        del _chat_progress[k]


_COMMON_PLOT_COLOR_NAMES = {
    "red", "green", "blue", "yellow", "orange", "purple", "pink",
    "brown", "black", "white", "gray", "grey", "teal", "cyan",
    "magenta", "lime", "navy", "maroon", "olive", "gold", "silver",
    "violet", "indigo", "turquoise", "salmon", "coral", "beige",
}


def _extract_plot_style_params(message: str) -> dict[str, str]:
    """Extract explicit plot styling hints like literal colors from the user message."""
    if not message:
        return {}

    def _normalize_literal_color(raw_value: str | None) -> str | None:
        if not raw_value:
            return None
        value = raw_value.strip().strip('"').strip("'").rstrip(".,;:)")
        if not value:
            return None
        if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value):
            return value
        if re.fullmatch(r"(?:rgb|rgba|hsl|hsla)\([^\)]*\)", value, re.IGNORECASE):
            return value
        value_lower = value.lower()
        if value_lower in _COMMON_PLOT_COLOR_NAMES:
            return value_lower
        return None

    patterns = [
        r'\bin\s+(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8}))\b',
        r'\bin\s+((?:rgb|rgba|hsl|hsla)\([^\)]*\))',
        r'\bin\s+([A-Za-z]+)\b',
        r'\b(?:colored?|coloured?)\s+((?:#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|(?:rgb|rgba|hsl|hsla)\([^\)]*\)|[A-Za-z]+))\b',
        r'\bwith\s+((?:#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|(?:rgb|rgba|hsl|hsla)\([^\)]*\)|[A-Za-z]+))\s+(?:bars?|points?|lines?|slices?)\b',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, message, re.IGNORECASE):
            color = _normalize_literal_color(match.group(1))
            if color:
                return {"palette": color}
    return {}

def _is_cancelled(request_id: str) -> bool:
    """Check whether the user has requested cancellation for this chat request."""
    if not request_id:
        return False
    return _chat_progress.get(request_id, {}).get("cancelled", False)
