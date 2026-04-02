"""
Memory commands — slash command parser and natural language intent detection.

Handles /remember, /forget, /memories, /pin, /unpin, /restore, /annotate
and natural language patterns like "remember that...", "sample X is AD", etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from cortex import memory_service
from common.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MemoryCommand:
    """Parsed slash command."""
    action: Literal[
        "remember", "remember_global", "forget", "list",
        "pin", "unpin", "restore", "annotate", "search",
    ]
    text: str = ""
    memory_id: str = ""
    sample_name: str = ""
    annotations: dict[str, str] = field(default_factory=dict)
    global_only: bool = False


@dataclass
class MemoryIntent:
    """Natural language memory intent (side-effect alongside normal LLM response)."""
    action: Literal["remember", "forget", "annotate", "search"]
    text: str = ""
    sample_name: str = ""
    annotations: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Slash command parsing
# ---------------------------------------------------------------------------

_SLASH_PATTERNS = {
    "remember_global": re.compile(r"^/remember[_-]global\s+(.+)", re.IGNORECASE | re.DOTALL),
    "remember": re.compile(r"^/remember\s+(.+)", re.IGNORECASE | re.DOTALL),
    "forget": re.compile(r"^/forget\s+(.+)", re.IGNORECASE | re.DOTALL),
    "list": re.compile(r"^/memories(?:\s+(--global))?$", re.IGNORECASE),
    "pin": re.compile(r"^/pin\s+#?(\S+)", re.IGNORECASE),
    "unpin": re.compile(r"^/unpin\s+#?(\S+)", re.IGNORECASE),
    "restore": re.compile(r"^/restore\s+#?(\S+)", re.IGNORECASE),
    "annotate": re.compile(
        r"^/annotate\s+(\S+)\s+(.+)", re.IGNORECASE | re.DOTALL
    ),
    "search": re.compile(r"^/search[_-]memories?\s+(.+)", re.IGNORECASE | re.DOTALL),
}


def parse_memory_command(message: str) -> MemoryCommand | None:
    """Parse a slash command from a user message. Returns None if not a command."""
    msg = message.strip()
    if not msg.startswith("/"):
        return None

    # /remember-global
    m = _SLASH_PATTERNS["remember_global"].match(msg)
    if m:
        return MemoryCommand(action="remember_global", text=m.group(1).strip())

    # /remember
    m = _SLASH_PATTERNS["remember"].match(msg)
    if m:
        return MemoryCommand(action="remember", text=m.group(1).strip())

    # /forget
    m = _SLASH_PATTERNS["forget"].match(msg)
    if m:
        target = m.group(1).strip()
        # Check if it's an ID reference (#xxx or just a UUID-like string)
        if target.startswith("#"):
            return MemoryCommand(action="forget", memory_id=target.lstrip("#"))
        return MemoryCommand(action="forget", text=target)

    # /memories
    m = _SLASH_PATTERNS["list"].match(msg)
    if m:
        return MemoryCommand(action="list", global_only=bool(m.group(1)))

    # /pin
    m = _SLASH_PATTERNS["pin"].match(msg)
    if m:
        return MemoryCommand(action="pin", memory_id=m.group(1).strip())

    # /unpin
    m = _SLASH_PATTERNS["unpin"].match(msg)
    if m:
        return MemoryCommand(action="unpin", memory_id=m.group(1).strip())

    # /restore
    m = _SLASH_PATTERNS["restore"].match(msg)
    if m:
        return MemoryCommand(action="restore", memory_id=m.group(1).strip())

    # /annotate sample_name key=value [key2=value2 ...]
    m = _SLASH_PATTERNS["annotate"].match(msg)
    if m:
        sample = m.group(1).strip()
        kv_str = m.group(2).strip()
        annotations = _parse_kv_pairs(kv_str)
        return MemoryCommand(action="annotate", sample_name=sample, annotations=annotations)

    # /search-memories
    m = _SLASH_PATTERNS["search"].match(msg)
    if m:
        return MemoryCommand(action="search", text=m.group(1).strip())

    return None


def _parse_kv_pairs(text: str) -> dict[str, str]:
    """Parse 'key=value key2=value2' or 'key=value, key2=value2' strings."""
    pairs = {}
    # Split on whitespace or commas, then parse key=value
    for token in re.split(r"[,\s]+", text):
        if "=" in token:
            k, _, v = token.partition("=")
            k = k.strip()
            v = v.strip().strip("'\"")
            if k:
                pairs[k] = v
    return pairs


# ---------------------------------------------------------------------------
# Natural language intent detection
# ---------------------------------------------------------------------------

_NL_REMEMBER = re.compile(
    r"(?:^|\b)(?:remember|note|keep in mind|save|store|record)\s+(?:that\s+)?(.+)",
    re.IGNORECASE | re.DOTALL,
)
_NL_FORGET = re.compile(
    r"(?:^|\b)(?:forget|delete|remove)\s+(?:the\s+)?(?:memory|note|entry)?\s*(?:about|for|that)?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
_NL_ANNOTATE = re.compile(
    r"(?:^|\b)(?:sample|file)\s+(\S+)\s+is\s+(?:an?\s+)?(.+)",
    re.IGNORECASE,
)
_NL_ANNOTATE_MARK = re.compile(
    r"(?:^|\b)(?:mark|label|tag|annotate)\s+(?:sample|file)\s+(\S+)\s+(?:as\s+)?(.+)",
    re.IGNORECASE,
)
_NL_SEARCH = re.compile(
    r"(?:^|\b)(?:what do you (?:remember|know)|what are my (?:notes|memories))\s+(?:about|on|for)\s+(.+)",
    re.IGNORECASE,
)


def detect_memory_intent(message: str) -> MemoryIntent | None:
    """Detect memory-related intent from natural language.

    Returns MemoryIntent if detected, None otherwise.
    This is a side-effect — the message still goes to the LLM.
    """
    msg = message.strip()

    # Skip if it looks like a slash command
    if msg.startswith("/"):
        return None

    # Annotation: "sample X is AD" / "sample X is a control"
    m = _NL_ANNOTATE.search(msg)
    if m:
        sample = m.group(1).strip()
        value = m.group(2).strip().rstrip(".,;")
        return MemoryIntent(
            action="annotate",
            sample_name=sample,
            annotations={"condition": value},
        )

    # Annotation: "mark sample X as treated"
    m = _NL_ANNOTATE_MARK.search(msg)
    if m:
        sample = m.group(1).strip()
        value = m.group(2).strip().rstrip(".,;")
        return MemoryIntent(
            action="annotate",
            sample_name=sample,
            annotations={"condition": value},
        )

    # Remember
    m = _NL_REMEMBER.match(msg)
    if m:
        return MemoryIntent(action="remember", text=m.group(1).strip())

    # Forget
    m = _NL_FORGET.match(msg)
    if m:
        return MemoryIntent(action="forget", text=m.group(1).strip())

    # Search
    m = _NL_SEARCH.search(msg)
    if m:
        return MemoryIntent(action="search", text=m.group(1).strip())

    return None


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def execute_memory_command(
    db: Session,
    command: MemoryCommand,
    user_id: str,
    project_id: str | None,
) -> str:
    """Execute a parsed memory command. Returns markdown response text."""

    if command.action == "remember":
        mem = memory_service.create_memory(
            db, user_id=user_id, content=command.text,
            project_id=project_id, source="user_manual",
        )
        return f"✅ Remembered: {command.text}\n\n*Memory ID: `{mem.id[:8]}`*"

    if command.action == "remember_global":
        mem = memory_service.create_memory(
            db, user_id=user_id, content=command.text,
            project_id=None, source="user_manual",
        )
        return f"✅ Remembered (global): {command.text}\n\n*Memory ID: `{mem.id[:8]}`*"

    if command.action == "forget":
        if command.memory_id:
            # Find by ID prefix
            ok = _delete_by_id_prefix(db, command.memory_id, user_id, project_id)
            if ok:
                return f"✅ Memory `{command.memory_id}` deleted."
            return f"❌ No memory found with ID starting with `{command.memory_id}`."
        # Find by content match
        matches = memory_service.find_memory_by_content(
            db, user_id, command.text, project_id=project_id,
        )
        if not matches:
            return f"❌ No memory found matching: {command.text}"
        if len(matches) == 1:
            memory_service.delete_memory(db, matches[0].id, user_id)
            return f"✅ Deleted: {matches[0].content}\n\n*Memory ID: `{matches[0].id[:8]}`*"
        # Multiple matches — show them
        lines = ["Found multiple matching memories. Please specify by ID:\n"]
        for m in matches[:10]:
            pin = "⭐ " if m.is_pinned else ""
            lines.append(f"- `{m.id[:8]}` — {pin}{m.content}")
        return "\n".join(lines)

    if command.action == "list":
        mems = memory_service.list_memories(
            db, user_id, project_id=project_id,
            include_global=not command.global_only,
        )
        if not mems:
            return "📋 No memories yet. Use `/remember <text>` or say \"remember that...\" to add one."
        lines = ["📋 **Memories**\n"]
        for m in mems:
            pin = "⭐ " if m.is_pinned else ""
            scope = "🌐" if m.project_id is None else "📁"
            lines.append(f"- {scope} {pin}`{m.id[:8]}` [{m.category}] {m.content}")
        return "\n".join(lines)

    if command.action == "pin":
        ok = _pin_by_id_prefix(db, command.memory_id, user_id, project_id, pinned=True)
        if ok:
            return f"⭐ Memory `{command.memory_id}` pinned."
        return f"❌ No memory found with ID starting with `{command.memory_id}`."

    if command.action == "unpin":
        ok = _pin_by_id_prefix(db, command.memory_id, user_id, project_id, pinned=False)
        if ok:
            return f"Memory `{command.memory_id}` unpinned."
        return f"❌ No memory found with ID starting with `{command.memory_id}`."

    if command.action == "restore":
        ok = _restore_by_id_prefix(db, command.memory_id, user_id)
        if ok:
            return f"✅ Memory `{command.memory_id}` restored."
        return f"❌ No deleted memory found with ID starting with `{command.memory_id}`."

    if command.action == "annotate":
        mem = memory_service.annotate_sample(
            db, user_id=user_id, sample_name=command.sample_name,
            annotations=command.annotations, project_id=project_id,
        )
        ann_str = ", ".join(f"{k}={v}" for k, v in command.annotations.items())
        return f"✅ Sample **{command.sample_name}** annotated: {ann_str}"

    if command.action == "search":
        mems = memory_service.search_memories(
            db, user_id, command.text, project_id=project_id,
        )
        if not mems:
            return f"No memories found matching: {command.text}"
        lines = [f"🔍 **Memories matching \"{command.text}\"**\n"]
        for m in mems:
            pin = "⭐ " if m.is_pinned else ""
            lines.append(f"- {pin}`{m.id[:8]}` [{m.category}] {m.content}")
        return "\n".join(lines)

    return "❌ Unknown memory command."


def execute_memory_intent(
    db: Session,
    intent: MemoryIntent,
    user_id: str,
    project_id: str | None,
) -> str | None:
    """Execute an NL memory intent as a side-effect. Returns brief confirmation or None."""

    if intent.action == "remember":
        mem = memory_service.create_memory(
            db, user_id=user_id, content=intent.text,
            project_id=project_id, source="user_manual",
        )
        logger.info("NL memory created", memory_id=mem.id, content=intent.text[:60])
        return f"\u2705 Remembered: {intent.text}\n\n*Memory ID: `{mem.id[:8]}`*"

    if intent.action == "forget":
        matches = memory_service.find_memory_by_content(
            db, user_id, intent.text, project_id=project_id,
        )
        if matches:
            memory_service.delete_memory(db, matches[0].id, user_id)
            logger.info("NL memory deleted", memory_id=matches[0].id)
            return f"\u2705 Forgot memory about: {intent.text}"
        return f"\u26a0\ufe0f No matching memory found for: {intent.text}"

    if intent.action == "annotate":
        memory_service.annotate_sample(
            db, user_id=user_id, sample_name=intent.sample_name,
            annotations=intent.annotations, project_id=project_id,
        )
        logger.info("NL annotation created", sample=intent.sample_name)
        _kv = ", ".join(f"{k}={v}" for k, v in intent.annotations.items())
        return f"\u2705 Annotated **{intent.sample_name}**: {_kv}"

    if intent.action == "search":
        results = memory_service.search_memories(
            db, user_id, project_id, intent.text,
        )
        if not results:
            return f"No memories found matching \"{intent.text}\"."
        lines = [f"- [{m.category}] {m.content[:80]}" for m in results[:10]]
        return f"**Memories matching \"{intent.text}\":**\n" + "\n".join(lines)

    return None


# ---------------------------------------------------------------------------
# ID-prefix helpers (since IDs are UUIDs, users only type first 8 chars)
# ---------------------------------------------------------------------------

from sqlalchemy import select  # noqa: E402

from cortex.models import Memory  # noqa: E402


def _find_by_id_prefix(
    db: Session, prefix: str, user_id: str, project_id: str | None = None,
) -> Memory | None:
    """Find a single memory by ID prefix."""
    conditions = [
        Memory.user_id == user_id,
        Memory.id.like(f"{prefix}%"),
    ]
    mems = db.execute(
        select(Memory).where(*conditions).limit(2)
    ).scalars().all()
    if len(mems) == 1:
        return mems[0]
    return None


def _delete_by_id_prefix(
    db: Session, prefix: str, user_id: str, project_id: str | None,
) -> bool:
    mem = _find_by_id_prefix(db, prefix, user_id, project_id)
    if mem:
        return memory_service.delete_memory(db, mem.id, user_id)
    return False


def _pin_by_id_prefix(
    db: Session, prefix: str, user_id: str, project_id: str | None,
    *, pinned: bool,
) -> bool:
    mem = _find_by_id_prefix(db, prefix, user_id, project_id)
    if mem:
        return memory_service.pin_memory(db, mem.id, user_id, pinned=pinned)
    return False


def _restore_by_id_prefix(db: Session, prefix: str, user_id: str) -> bool:
    mem = _find_by_id_prefix(db, prefix, user_id)
    if mem:
        return memory_service.restore_memory(db, mem.id, user_id)
    return False
