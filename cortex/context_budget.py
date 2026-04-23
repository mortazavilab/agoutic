"""
Unified token/context budget manager.

Allocates the LLM context window across system prompt, skill content, memory,
tool schemas, and conversation history using tiktoken for accurate counting
rather than character heuristics.
"""

from __future__ import annotations

import tiktoken
from dataclasses import dataclass, field

from cortex.config import LLM_NUM_CTX
from common.logging_config import get_logger

logger = get_logger(__name__)

# tiktoken encoding used for token counting.
# cl100k_base covers GPT-4 / GPT-3.5-turbo family and is a reasonable proxy
# for other modern tokenizers (Mistral, Qwen, etc).  Override via set_encoding()
# if the deployment uses a model with a substantially different tokenizer.
_ENCODING_NAME = "cl100k_base"
_encoding: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding(_ENCODING_NAME)
    return _encoding


def set_encoding(name: str) -> None:
    """Switch the tokenizer encoding at startup (e.g. for a different model family)."""
    global _ENCODING_NAME, _encoding
    _ENCODING_NAME = name
    _encoding = None  # lazy re-init


def count_tokens(text: str) -> int:
    """Return the token count for *text* using the active encoding."""
    if not text:
        return 0
    return len(_get_encoding().encode(text))


def count_message_tokens(messages: list[dict]) -> int:
    """Approximate token count for an OpenAI-style message list.

    Each message carries ~4 overhead tokens (role, separators).
    """
    total = 0
    for msg in messages:
        total += 4  # per-message overhead
        total += count_tokens(msg.get("content", ""))
    total += 2  # reply priming
    return total


# ── Budget allocation defaults ────────────────────────────────────────────
# Expressed as fractions of the *total* context window after reserving
# headroom for the model's completion output.

_DEFAULT_COMPLETION_RESERVE = 4096  # tokens reserved for model output

_DEFAULT_PRIORITIES: dict[str, BudgetSlot] = {}  # populated after class definition


@dataclass
class BudgetSlot:
    """Allocation descriptor for one context section."""
    priority: int          # lower = higher priority (trimmed last)
    fraction: float        # ideal fraction of the available window
    min_tokens: int = 128  # never shrink below this
    hard: bool = False     # if True, content is never truncated


@dataclass
class BudgetAllocation:
    """Concrete token limits produced by the manager for a single LLM call."""
    system_prompt: int = 0
    skill_content: int = 0
    memory: int = 0
    tool_schemas: int = 0
    conversation_history: int = 0
    user_message: int = 0
    total_available: int = 0
    completion_reserve: int = 0


# Priority 0 = highest (never trimmed); higher numbers are trimmed first.
DEFAULT_SLOTS: dict[str, BudgetSlot] = {
    "system_prompt":        BudgetSlot(priority=0, fraction=0.20, min_tokens=512, hard=True),
    "tool_schemas":         BudgetSlot(priority=1, fraction=0.15, min_tokens=256),
    "skill_content":        BudgetSlot(priority=2, fraction=0.15, min_tokens=256),
    "user_message":         BudgetSlot(priority=3, fraction=0.10, min_tokens=256, hard=True),
    "memory":               BudgetSlot(priority=4, fraction=0.05, min_tokens=64),
    "conversation_history": BudgetSlot(priority=5, fraction=0.35, min_tokens=128),
}


class ContextBudgetManager:
    """Allocates and enforces a token budget across context sections.

    Usage::

        mgr = ContextBudgetManager()
        alloc = mgr.allocate(
            system_prompt=sys_text,
            skill_content=skill_text,
            memory=mem_text,
            tool_schemas=schema_text,
            conversation_history=history_msgs,
            user_message=user_text,
        )
        # Use alloc to decide what to trim before sending to the LLM.
        trimmed_history = mgr.trim_history(history_msgs, alloc.conversation_history)
    """

    def __init__(
        self,
        context_window: int | None = None,
        completion_reserve: int = _DEFAULT_COMPLETION_RESERVE,
        slots: dict[str, BudgetSlot] | None = None,
    ):
        self.context_window = context_window or LLM_NUM_CTX
        self.completion_reserve = completion_reserve
        self.slots = dict(slots or DEFAULT_SLOTS)

    @property
    def available_tokens(self) -> int:
        return max(self.context_window - self.completion_reserve, 0)

    # ── Public API ────────────────────────────────────────────────────

    def allocate(
        self,
        *,
        system_prompt: str = "",
        skill_content: str = "",
        memory: str = "",
        tool_schemas: str = "",
        conversation_history: list[dict] | None = None,
        user_message: str = "",
    ) -> BudgetAllocation:
        """Compute a concrete allocation given the actual content sizes.

        The algorithm:
        1. Measure actual token usage for each section.
        2. Hard sections get their full measured size (capped at available).
        3. Remaining budget is distributed to soft sections proportionally,
           respecting min_tokens guarantees.
        4. If total demand < available, each section simply gets what it needs.
        """
        available = self.available_tokens

        # 1. Measure
        measured: dict[str, int] = {
            "system_prompt": count_tokens(system_prompt),
            "skill_content": count_tokens(skill_content),
            "memory": count_tokens(memory),
            "tool_schemas": count_tokens(tool_schemas),
            "conversation_history": count_message_tokens(conversation_history or []),
            "user_message": count_tokens(user_message),
        }

        total_measured = sum(measured.values())

        # 2. Fast path — everything fits
        if total_measured <= available:
            alloc = BudgetAllocation(
                total_available=available,
                completion_reserve=self.completion_reserve,
                **measured,
            )
            logger.info(
                "Context budget: all content fits",
                total_tokens=total_measured,
                available=available,
                window=self.context_window,
            )
            return alloc

        # 3. Need to trim — assign hard slots first, then distribute remainder
        logger.warning(
            "Context budget: trimming required",
            total_tokens=total_measured,
            available=available,
            overflow=total_measured - available,
        )

        allocated: dict[str, int] = {}
        remaining = available

        # Hard slots: give them what they need (up to available)
        for name, slot in sorted(self.slots.items(), key=lambda kv: kv[1].priority):
            if slot.hard:
                give = min(measured[name], remaining)
                allocated[name] = give
                remaining -= give

        # Soft slots: sort by priority (highest priority = lowest number = last to trim)
        soft_slots = [
            (name, slot) for name, slot in self.slots.items()
            if not slot.hard and name not in allocated
        ]
        # Sort descending by priority so we trim the *least* important first
        soft_slots.sort(key=lambda kv: kv[1].priority, reverse=True)

        # First pass: guarantee min_tokens for every soft slot
        min_total = sum(s.min_tokens for _, s in soft_slots)
        if min_total > remaining:
            # Even minimums don't fit; proportionally scale minimums
            scale = remaining / max(min_total, 1)
            for name, slot in soft_slots:
                allocated[name] = max(int(slot.min_tokens * scale), 0)
                remaining -= allocated[name]
        else:
            # Distribute remaining above minimums by measured need
            for name, slot in soft_slots:
                allocated[name] = slot.min_tokens
                remaining -= slot.min_tokens

            # Second pass: give surplus to slots that need it (highest priority first)
            soft_slots_asc = sorted(soft_slots, key=lambda kv: kv[1].priority)
            for name, slot in soft_slots_asc:
                need = measured[name] - allocated[name]
                if need > 0 and remaining > 0:
                    give = min(need, remaining)
                    allocated[name] += give
                    remaining -= give

        alloc = BudgetAllocation(
            system_prompt=allocated.get("system_prompt", measured["system_prompt"]),
            skill_content=allocated.get("skill_content", 0),
            memory=allocated.get("memory", 0),
            tool_schemas=allocated.get("tool_schemas", 0),
            conversation_history=allocated.get("conversation_history", 0),
            user_message=allocated.get("user_message", measured["user_message"]),
            total_available=available,
            completion_reserve=self.completion_reserve,
        )

        logger.info(
            "Context budget allocated",
            alloc={k: v for k, v in alloc.__dict__.items()},
            measured=measured,
        )
        return alloc

    # ── Trimming helpers ──────────────────────────────────────────────

    def trim_text(self, text: str, max_tokens: int) -> str:
        """Truncate *text* to fit within *max_tokens*, appending an indicator."""
        if not text or max_tokens <= 0:
            return ""
        enc = _get_encoding()
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        # Reserve a few tokens for the truncation indicator
        indicator = "\n\n[...truncated to fit context budget]"
        indicator_tokens = len(enc.encode(indicator))
        keep = max(max_tokens - indicator_tokens, 0)
        return enc.decode(tokens[:keep]) + indicator

    def trim_history(
        self,
        messages: list[dict],
        max_tokens: int,
    ) -> list[dict]:
        """Keep the most recent messages that fit within *max_tokens*.

        Trims from the oldest end to preserve recency.
        """
        if not messages:
            return []
        if max_tokens <= 0:
            return []

        # Walk from newest to oldest, accumulating until we hit the limit
        kept: list[dict] = []
        used = 2  # reply priming overhead
        for msg in reversed(messages):
            msg_tokens = 4 + count_tokens(msg.get("content", ""))
            if used + msg_tokens > max_tokens:
                break
            kept.append(msg)
            used += msg_tokens

        kept.reverse()

        if len(kept) < len(messages):
            logger.info(
                "Conversation history trimmed",
                original_messages=len(messages),
                kept_messages=len(kept),
                max_tokens=max_tokens,
            )

        return kept

    def fit_messages(
        self,
        *,
        system_prompt: str,
        skill_content: str = "",
        memory: str = "",
        tool_schemas: str = "",
        conversation_history: list[dict] | None = None,
        user_message: str,
    ) -> list[dict]:
        """One-shot: allocate budget, trim all sections, return ready-to-send messages.

        The system prompt is assembled from *system_prompt* (which may already
        contain skill_content and tool_schemas baked in).  If *skill_content*,
        *memory*, or *tool_schemas* are passed separately they are measured
        independently for budget purposes but are NOT re-inserted (the caller
        is responsible for composing the final system prompt text).

        For the common case where skill_content and tool_schemas are already
        embedded inside system_prompt, pass them as empty strings here and the
        full system_prompt will be treated as one block.
        """
        alloc = self.allocate(
            system_prompt=system_prompt,
            skill_content=skill_content,
            memory=memory,
            tool_schemas=tool_schemas,
            conversation_history=conversation_history,
            user_message=user_message,
        )

        # Trim the individual pieces
        trimmed_system = self.trim_text(system_prompt, alloc.system_prompt)
        trimmed_history = self.trim_history(
            conversation_history or [], alloc.conversation_history,
        )
        trimmed_user = self.trim_text(user_message, alloc.user_message)

        messages = [{"role": "system", "content": trimmed_system}]
        messages.extend(trimmed_history)
        messages.append({"role": "user", "content": trimmed_user})
        return messages
