from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from cortex.skill_manifest import SKILL_MANIFESTS, SkillManifest


@dataclass
class SkillCommand:
    action: Literal["list", "describe", "use"]
    skill_ref: str = ""


_SLASH_LIST = re.compile(r"^/(?:skills|list-skills)$", re.IGNORECASE)
_SLASH_DESCRIBE = re.compile(r"^/(?:skill|describe-skill)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_SLASH_USE = re.compile(r"^/(?:use-skill|switch-skill)\s+(.+)$", re.IGNORECASE | re.DOTALL)

_NL_LIST_PATTERNS = (
    re.compile(r"^(?:please\s+)?(?:list|show)(?:\s+me)?\s+(?:all\s+)?(?:available\s+)?skills[?.!]*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?what\s+skills\s+(?:are\s+available|do\s+you\s+have|can\s+i\s+use|can\s+you\s+use)[?.!]*$", re.IGNORECASE),
)
_NL_DESCRIBE_PATTERNS = (
    re.compile(r"^(?:please\s+)?(?:describe|explain)\s+(?:the\s+)?(.+?)\s+skill[?.!]*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^(?:please\s+)?tell\s+me\s+about\s+(?:the\s+)?(.+?)\s+skill[?.!]*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^(?:please\s+)?what\s+does\s+(?:the\s+)?(.+?)\s+skill\s+do[?.!]*$", re.IGNORECASE | re.DOTALL),
)
_NL_USE_PATTERNS = (
    re.compile(r"^(?:please\s+)?(?:use|switch\s+to|change\s+to)\s+(?:the\s+)?(.+?)\s+skill[?.!]*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^(?:please\s+)?set\s+(?:the\s+)?active\s+skill\s+to\s+(.+?)[?.!]*$", re.IGNORECASE | re.DOTALL),
)

_SKILL_ALIASES = {
    "runworkflow": "analyze_local_sample",
    "submitjob": "analyze_local_sample",
    "rundogme": "analyze_local_sample",
    "localsample": "analyze_local_sample",
    "encodesearch": "ENCODE_Search",
    "encodelongread": "ENCODE_LongRead",
    "jobresults": "analyze_job_results",
}


def parse_skill_command(message: str) -> SkillCommand | None:
    msg = str(message or "").strip()
    if not msg.startswith("/"):
        return None

    if _SLASH_LIST.match(msg):
        return SkillCommand(action="list")

    match = _SLASH_USE.match(msg)
    if match:
        return SkillCommand(action="use", skill_ref=match.group(1).strip())

    match = _SLASH_DESCRIBE.match(msg)
    if match:
        return SkillCommand(action="describe", skill_ref=match.group(1).strip())

    return None


def detect_skill_intent(message: str) -> SkillCommand | None:
    msg = str(message or "").strip()
    if not msg or msg.startswith("/"):
        return None

    if any(pattern.match(msg) for pattern in _NL_LIST_PATTERNS):
        return SkillCommand(action="list")

    for pattern in _NL_USE_PATTERNS:
        match = pattern.match(msg)
        if match:
            return SkillCommand(action="use", skill_ref=_cleanup_skill_ref(match.group(1)))

    for pattern in _NL_DESCRIBE_PATTERNS:
        match = pattern.match(msg)
        if match:
            return SkillCommand(action="describe", skill_ref=_cleanup_skill_ref(match.group(1)))

    return None


def resolve_skill_key(skill_ref: str) -> str | None:
    ref = str(skill_ref or "").strip()
    if not ref:
        return None

    if ref in SKILL_MANIFESTS:
        return ref

    normalized = _normalize_skill_ref(ref)
    if normalized in _SKILL_ALIASES:
        return _SKILL_ALIASES[normalized]

    for key, manifest in SKILL_MANIFESTS.items():
        if _normalize_skill_ref(key) == normalized:
            return key
        if manifest.display_name and _normalize_skill_ref(manifest.display_name) == normalized:
            return key
    return None


def execute_skill_command(command: SkillCommand, *, active_skill: str) -> str:
    if command.action == "list":
        return _render_skill_list(active_skill=active_skill)

    resolved = resolve_skill_key(command.skill_ref)
    if resolved is None:
        return (
            f"Unknown skill: `{command.skill_ref}`.\n\n"
            "Use `/skills` to list available skills."
        )

    manifest = SKILL_MANIFESTS[resolved]
    if command.action == "describe":
        return _render_skill_description(manifest, active_skill=active_skill)

    if resolved == active_skill:
        return (
            f"Active skill is already **{manifest.display_name or resolved}** (`{resolved}`).\n\n"
            "Use `/skill <name>` to inspect its details or `/skills` to browse all skills."
        )

    return (
        f"Switched active skill to **{manifest.display_name or resolved}** (`{resolved}`).\n\n"
        f"Use `/skill {resolved}` to inspect its manifest-driven capabilities."
    )


def _normalize_skill_ref(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _cleanup_skill_ref(value: str) -> str:
    cleaned = str(value or "").strip().strip("`\"'")
    cleaned = re.sub(r"^[Tt]he\s+", "", cleaned)
    cleaned = re.sub(r"\s+[Ss]kill$", "", cleaned)
    return cleaned.strip().rstrip("?.!")


def _render_skill_list(*, active_skill: str) -> str:
    lines = [
        "### Available skills",
        "",
        "Use `/skill <skill_key>` to describe a skill and `/use-skill <skill_key>` to switch to one manually.",
        "Natural-language equivalents also work, such as `tell me about the differential expression skill` or `switch to the IGVF Search skill`.",
        "",
        "| Key | Name | Category | Source | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for manifest in _sorted_manifests():
        source = _source_label(manifest)
        notes: list[str] = []
        if manifest.key == active_skill:
            notes.append("current")
        if manifest.plan_type:
            notes.append(f"plan: {manifest.plan_type}")
        if manifest.slash_commands:
            notes.append("commands: " + ", ".join(manifest.slash_commands))
        note_text = "; ".join(notes) if notes else ""
        lines.append(
            f"| `{manifest.key}` | {manifest.display_name or manifest.key} | {manifest.category} | {source} | {note_text} |"
        )
    return "\n".join(lines)


def _render_skill_description(manifest: SkillManifest, *, active_skill: str) -> str:
    lines = [
        f"### {manifest.display_name or manifest.key} (`{manifest.key}`)",
        "",
        manifest.description or "No description available.",
        "",
        f"- Category: `{manifest.category}`",
        f"- Source: {_source_label(manifest)}",
        f"- Required services: {_tuple_or_none(manifest.required_services)}",
        f"- Expected inputs: {_tuple_or_none(manifest.expected_inputs)}",
        f"- Output types: {_enum_tuple_or_none(manifest.output_types)}",
        f"- Sample types: {_enum_tuple_or_none(manifest.sample_types)}",
        f"- Estimated runtime: `{manifest.estimated_runtime}`",
        f"- Plan type: `{manifest.plan_type or 'n/a'}`",
        f"- Manifest slash commands: {_tuple_or_none(manifest.slash_commands)}",
        f"- Skill file: `skills/{manifest.skill_file}`",
        f"- Manifest file: `skills/{manifest.key}/manifest.yaml`",
    ]
    if manifest.depends_on_skills:
        lines.append(f"- Depends on skills: {_tuple_or_none(manifest.depends_on_skills)}")
    if manifest.feeds_into:
        lines.append(f"- Feeds into: {_tuple_or_none(manifest.feeds_into)}")
    if manifest.key == active_skill:
        lines.append("- Status: `current active skill`")
    return "\n".join(lines)


def _sorted_manifests() -> list[SkillManifest]:
    return sorted(
        SKILL_MANIFESTS.values(),
        key=lambda manifest: (manifest.category, (manifest.display_name or manifest.key).lower(), manifest.key.lower()),
    )


def _source_label(manifest: SkillManifest) -> str:
    if manifest.source_key and manifest.source_type:
        return f"`{manifest.source_type}:{manifest.source_key}`"
    return "`n/a`"


def _tuple_or_none(values: tuple[str, ...]) -> str:
    if not values:
        return "`n/a`"
    return ", ".join(f"`{value}`" for value in values)


def _enum_tuple_or_none(values: tuple[object, ...]) -> str:
    if not values:
        return "`n/a`"
    return ", ".join(f"`{getattr(value, 'value', value)}`" for value in values)