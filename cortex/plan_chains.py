"""
Skill-defined plan chains: parse multi-step chain definitions from skill
Markdown files and match them against user queries.

Skill authors add a ``## Plan Chains`` section to their ``.md`` file with
one or more chain blocks like:

    ## Plan Chains

    ### search_and_visualize
    - description: Search for data and visualize the results
    - trigger: search|get|find + plot|chart|visualize|graph|pie|histogram|bar|scatter|heatmap
    - steps:
      1. SEARCH_DATA: Search for the requested data
      2. GENERATE_PLOT: Visualize the results
    - auto_approve: true
    - plot_hint: Infer chart type and grouping column from the user's message

Each chain provides:
  - ``trigger``: Two groups of keywords joined by ``+``. Both groups must have
    at least one keyword present in the user message for the chain to match.
  - ``steps``: Ordered step descriptions (kind: title).
  - ``auto_approve``: Whether the chain can execute without user approval.
  - ``plot_hint``: Optional free-form hint passed to the LLM so it knows to
    emit a ``[[PLOT:...]]`` tag after data retrieval.

This module is intentionally **read-only** — it parses skill files but never
modifies them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from common.logging_config import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ChainStep:
    """A single step within a plan chain."""
    order: int
    kind: str
    title: str


@dataclass
class PlanChain:
    """A multi-step chain parsed from a skill file."""
    name: str
    description: str
    trigger_groups: list[list[str]]   # e.g. [["search","get","find"], ["plot","chart"]]
    steps: list[ChainStep]
    auto_approve: bool = True
    plot_hint: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_CHAIN_HEADER_RE = re.compile(r'^###\s+(\w+)', re.MULTILINE)
_KV_RE = re.compile(r'^-\s+(\w[\w_]*):\s*(.+)$', re.MULTILINE)
_STEP_RE = re.compile(r'^\s*(\d+)\.\s+(\w+):\s*(.+)$', re.MULTILINE)


def parse_chains_from_skill(skill_text: str) -> list[PlanChain]:
    """Extract all plan chains from the ``## Plan Chains`` section of a skill."""

    # Locate the section
    section_match = re.search(
        r'^##\s+Plan\s+Chains\s*\n(.*?)(?=\n##\s[^#]|\Z)',
        skill_text,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return []

    section_text = section_match.group(1)

    # Split into individual chain blocks by ### headers
    chain_starts = list(_CHAIN_HEADER_RE.finditer(section_text))
    if not chain_starts:
        return []

    chains: list[PlanChain] = []
    for i, m in enumerate(chain_starts):
        name = m.group(1)
        start = m.end()
        end = chain_starts[i + 1].start() if i + 1 < len(chain_starts) else len(section_text)
        block = section_text[start:end]

        # Parse key-value fields
        kv: dict[str, str] = {}
        for kv_match in _KV_RE.finditer(block):
            kv[kv_match.group(1).lower()] = kv_match.group(2).strip()

        # Parse steps
        steps: list[ChainStep] = []
        for step_match in _STEP_RE.finditer(block):
            steps.append(ChainStep(
                order=int(step_match.group(1)),
                kind=step_match.group(2).strip(),
                title=step_match.group(3).strip(),
            ))

        # Parse trigger groups: "group1_kw1|kw2 + group2_kw1|kw2|kw3"
        trigger_raw = kv.get("trigger", "")
        trigger_groups = _parse_trigger(trigger_raw)

        chains.append(PlanChain(
            name=name,
            description=kv.get("description", ""),
            trigger_groups=trigger_groups,
            steps=steps,
            auto_approve=kv.get("auto_approve", "true").lower() in ("true", "yes", "1"),
            plot_hint=kv.get("plot_hint", ""),
        ))

    logger.debug("Parsed plan chains from skill", count=len(chains),
                 names=[c.name for c in chains])
    return chains


def _parse_trigger(raw: str) -> list[list[str]]:
    """
    Parse a trigger string like::

        search|get|find + plot|chart|visualize

    Returns a list of keyword groups.  Each group is a list of lowercase keywords.
    """
    groups: list[list[str]] = []
    for part in raw.split("+"):
        part = part.strip()
        if not part:
            continue
        keywords = [kw.strip().lower() for kw in part.split("|") if kw.strip()]
        if keywords:
            groups.append(keywords)
    return groups


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_chain(message: str, chains: list[PlanChain]) -> PlanChain | None:
    """Return the first chain whose trigger matches the user message, or None."""
    msg_lower = message.lower()
    for chain in chains:
        if _triggers_match(msg_lower, chain.trigger_groups):
            logger.info("Plan chain matched", chain=chain.name,
                        triggers=[g[:3] for g in chain.trigger_groups])
            return chain
    return None


def _triggers_match(msg_lower: str, groups: list[list[str]]) -> bool:
    """Every trigger group must have at least one keyword present in the message."""
    if not groups:
        return False
    for group in groups:
        if not any(kw in msg_lower for kw in group):
            return False
    return True


# ---------------------------------------------------------------------------
# Plan rendering
# ---------------------------------------------------------------------------

_STEP_ICONS = {
    "SEARCH_DATA": "\U0001f50d",       # 🔍
    "SEARCH_ENCODE": "\U0001f50d",
    "GENERATE_PLOT": "\U0001f4ca",      # 📊
    "FILTER_DATA": "\U0001f50e",        # 🔎
    "DOWNLOAD_DATA": "\u2b07\ufe0f",    # ⬇️
    "SUBMIT_WORKFLOW": "\U0001f680",    # 🚀
    "PARSE_OUTPUT_FILE": "\U0001f4c4", # 📄
    "SUMMARIZE_QC": "\U0001f4cb",      # 📋
    "INTERPRET_RESULTS": "\U0001f9e0", # 🧠
    "WRITE_SUMMARY": "\u270d\ufe0f",   # ✍️
    "COMPARE_SAMPLES": "\u2696\ufe0f", # ⚖️
}


def render_chain_plan(chain: PlanChain, user_message: str) -> str:
    """Render a matched chain as user-visible markdown plan summary."""
    lines = [f"**Plan** — {chain.description or chain.name}\n"]
    for step in chain.steps:
        icon = _STEP_ICONS.get(step.kind, "\u25b6\ufe0f")
        lines.append(f"{step.order}. {icon} {step.title}")
    if chain.auto_approve:
        lines.append("\n*All steps are safe — executing automatically.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill file loading helper
# ---------------------------------------------------------------------------

def load_chains_for_skill(skill_key: str) -> list[PlanChain]:
    """Load plan chains for a skill key using the same registry as AgentEngine."""
    from cortex.config import SKILLS_DIR, SKILLS_REGISTRY
    if skill_key not in SKILLS_REGISTRY:
        return []
    skill_path = SKILLS_DIR / SKILLS_REGISTRY[skill_key]
    if not skill_path.exists():
        return []
    return parse_chains_from_skill(skill_path.read_text(encoding="utf-8"))
