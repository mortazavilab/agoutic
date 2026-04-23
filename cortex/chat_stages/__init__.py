"""Registry-based chat pipeline stage system.

Each stage module calls ``register_stage()`` at import time to add itself to the
global ``_STAGES`` list.  The pipeline runner iterates stages in priority order.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from cortex.chat_context import ChatContext


# ---------------------------------------------------------------------------
# Stage protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ChatStage(Protocol):
    """Minimal contract every pipeline stage must satisfy."""

    name: str
    priority: int  # lower = runs earlier; gaps of 100 for future insertion

    async def should_run(self, ctx: ChatContext) -> bool: ...
    async def run(self, ctx: ChatContext) -> None: ...


# ---------------------------------------------------------------------------
# Stage registry
# ---------------------------------------------------------------------------

_STAGES: list[ChatStage] = []


def register_stage(stage: ChatStage) -> None:
    """Add *stage* to the global registry, keeping it sorted by priority."""
    _STAGES.append(stage)
    _STAGES.sort(key=lambda s: s.priority)


def get_stages() -> list[ChatStage]:
    """Return all registered stages in priority order (low → high)."""
    return _STAGES


# ---------------------------------------------------------------------------
# Auto-register all built-in stages by importing them.
# Each module's top-level code calls register_stage().
# ---------------------------------------------------------------------------
from cortex.chat_stages import (  # noqa: E402, F401
    setup,
    quick_exits,
    history,
    confirmation_detection,
    early_overrides,
    context_prep,
    plan_detection,
    llm_first_pass,
    tag_parsing,
    overrides,
    tool_execution,
    second_pass,
    response_assembly,
)
