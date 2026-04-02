"""Pipeline runner — iterates registered chat stages in priority order."""
from __future__ import annotations

from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import get_stages

logger = get_logger(__name__)


async def run_chat_pipeline(ctx: ChatContext) -> dict:
    """Execute every registered stage whose ``should_run`` returns True.

    If a stage sets ``ctx.response``, the pipeline short-circuits and returns
    that dict immediately.  If no stage produces a response, a ``RuntimeError``
    is raised (this should never happen with a correctly wired pipeline).
    """
    for stage in get_stages():
        if ctx.response is not None:
            return ctx.response
        if await stage.should_run(ctx):
            logger.debug("Running chat stage", stage=stage.name, priority=stage.priority)
            await stage.run(ctx)
    if ctx.response is not None:
        return ctx.response
    raise RuntimeError("Chat pipeline completed without producing a response")
