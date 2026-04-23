"""Declarative skill capability manifest loaded from per-skill YAML files.

Each skill directory under ``skills/<skill_key>/`` now owns both:

- ``SKILL.md`` for LLM-facing instructions
- ``manifest.yaml`` for planner/executor-facing metadata

The loader in this module keeps the runtime API stable while removing the
need to hand-edit Cortex registries for skill-specific configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, cast

import yaml


class SampleType(str, Enum):
    DNA = "DNA"
    RNA = "RNA"
    CDNA = "cDNA"
    FIBER_SEQ = "Fiber-seq"
    BULK_RNA = "Bulk-RNA"
    SINGLE_CELL = "scRNA"
    ANY = "any"


class OutputType(str, Enum):
    """Broad category of what a skill produces."""

    DATAFRAME = "dataframe"
    FILE = "file"
    REPORT = "report"
    JOB = "job"
    PLOT = "plot"
    ANNOTATION = "annotation"


SourceType = Literal["service", "consortium", ""]
RuntimeType = Literal["fast", "medium", "slow", "variable"]


@dataclass(frozen=True)
class ToolCallSpec:
    """Declarative MCP tool entry used by manifest-driven planning."""

    source_key: str
    tool: str


@dataclass(frozen=True)
class SkillManifest:
    """Declarative capability descriptor for a single skill."""

    key: str
    skill_file: str
    display_name: str = ""
    description: str = ""
    category: str = "general"
    source_key: str = ""
    source_type: SourceType = ""
    required_services: tuple[str, ...] = ()
    expected_inputs: tuple[str, ...] = ()
    output_types: tuple[OutputType, ...] = ()
    sample_types: tuple[SampleType, ...] = (SampleType.ANY,)
    estimated_runtime: RuntimeType = "fast"
    plan_type: str = ""
    trigger_patterns: tuple[str, ...] = ()
    slash_commands: tuple[str, ...] = ()
    mcp_tool_chain: tuple[ToolCallSpec, ...] = ()
    depends_on_skills: tuple[str, ...] = ()
    feeds_into: tuple[str, ...] = ()
    classification_priority: int = 50
    analysis_only: bool = False
    switchable: bool = True


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / "skills"
_MANIFEST_FILENAMES = ("manifest.yaml", "manifest.yml")
_VALID_SOURCE_TYPES = {"", "service", "consortium"}
_VALID_RUNTIMES = {"fast", "medium", "slow", "variable"}


def _skill_dirs() -> list[Path]:
    if not _SKILLS_ROOT.exists():
        return []
    return sorted(
        child for child in _SKILLS_ROOT.iterdir()
        if child.is_dir() and (child / "SKILL.md").exists()
    )


def _manifest_path(skill_dir: Path) -> Path:
    for filename in _MANIFEST_FILENAMES:
        candidate = skill_dir / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Missing manifest.yaml for skill '{skill_dir.name}'")


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if not isinstance(value, list):
        raise TypeError(f"Expected list[str] or str, got {type(value).__name__}")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _output_types(value: Any) -> tuple[OutputType, ...]:
    return tuple(OutputType(str(item)) for item in _string_tuple(value))


def _sample_types(value: Any) -> tuple[SampleType, ...]:
    if value in (None, ""):
        return (SampleType.ANY,)
    parsed = tuple(SampleType(str(item)) for item in _string_tuple(value))
    return parsed or (SampleType.ANY,)


def _tool_chain(value: Any) -> tuple[ToolCallSpec, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise TypeError(f"Expected list[dict] for mcp_tool_chain, got {type(value).__name__}")

    specs: list[ToolCallSpec] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("Each mcp_tool_chain item must be a mapping")
        source_key = str(item.get("source_key") or "").strip()
        tool = str(item.get("tool") or "").strip()
        if not source_key or not tool:
            raise ValueError("Each mcp_tool_chain item needs non-empty source_key and tool")
        specs.append(ToolCallSpec(source_key=source_key, tool=tool))
    return tuple(specs)


def _runtime(value: Any) -> RuntimeType:
    runtime = str(value or "fast").strip()
    if runtime not in _VALID_RUNTIMES:
        raise ValueError(f"Invalid estimated_runtime: {runtime}")
    return cast(RuntimeType, runtime)


def _source(metadata: dict[str, Any]) -> tuple[str, SourceType]:
    source_data = metadata.get("source")
    if source_data in (None, ""):
        return "", ""
    if not isinstance(source_data, dict):
        raise TypeError("source must be a mapping with 'key' and 'type'")

    source_key = str(source_data.get("key") or "").strip()
    source_type = str(source_data.get("type") or "").strip()
    if source_type not in _VALID_SOURCE_TYPES:
        raise ValueError(f"Invalid source.type: {source_type}")
    if bool(source_key) != bool(source_type):
        raise ValueError("source.key and source.type must either both be set or both be empty")
    return source_key, cast(SourceType, source_type)


def _load_skill_manifest(skill_dir: Path) -> SkillManifest:
    manifest_path = _manifest_path(skill_dir)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    metadata = raw or {}
    if not isinstance(metadata, dict):
        raise TypeError(f"Skill manifest must be a mapping: {manifest_path}")

    key = str(metadata.get("key") or skill_dir.name).strip()
    if key != skill_dir.name:
        raise ValueError(f"Skill manifest key '{key}' must match folder name '{skill_dir.name}'")

    skill_file = str(metadata.get("skill_file") or f"{skill_dir.name}/SKILL.md").strip()
    source_key, source_type = _source(metadata)

    return SkillManifest(
        key=key,
        skill_file=skill_file,
        display_name=str(metadata.get("display_name") or "").strip(),
        description=str(metadata.get("description") or "").strip(),
        category=str(metadata.get("category") or "general").strip() or "general",
        source_key=source_key,
        source_type=source_type,
        required_services=_string_tuple(metadata.get("required_services")),
        expected_inputs=_string_tuple(metadata.get("expected_inputs")),
        output_types=_output_types(metadata.get("output_types")),
        sample_types=_sample_types(metadata.get("sample_types")),
        estimated_runtime=_runtime(metadata.get("estimated_runtime")),
        plan_type=str(metadata.get("plan_type") or "").strip(),
        trigger_patterns=_string_tuple(metadata.get("trigger_patterns")),
        slash_commands=_string_tuple(metadata.get("slash_commands")),
        mcp_tool_chain=_tool_chain(metadata.get("mcp_tool_chain")),
        depends_on_skills=_string_tuple(metadata.get("depends_on_skills")),
        feeds_into=_string_tuple(metadata.get("feeds_into")),
        classification_priority=int(metadata.get("classification_priority", 50)),
        analysis_only=bool(metadata.get("analysis_only", False)),
        switchable=bool(metadata.get("switchable", True)),
    )


def _load_skill_manifests() -> dict[str, SkillManifest]:
    manifests: dict[str, SkillManifest] = {}
    for skill_dir in _skill_dirs():
        manifest = _load_skill_manifest(skill_dir)
        manifests[manifest.key] = manifest
    return manifests


SKILL_MANIFESTS: dict[str, SkillManifest] = _load_skill_manifests()


def _build_compiled_trigger_entries() -> tuple[tuple[SkillManifest, tuple[re.Pattern[str], ...]], ...]:
    entries: list[tuple[SkillManifest, tuple[re.Pattern[str], ...]]] = []
    for manifest in SKILL_MANIFESTS.values():
        if not manifest.plan_type:
            continue

        patterns: list[re.Pattern[str]] = [
            re.compile(rf"^{re.escape(command)}\b", re.I)
            for command in manifest.slash_commands
        ]
        patterns.extend(re.compile(pattern, re.I) for pattern in manifest.trigger_patterns)
        if patterns:
            entries.append((manifest, tuple(patterns)))

    entries.sort(key=lambda item: (item[0].classification_priority, item[0].key))
    return tuple(entries)


_COMPILED_TRIGGER_ENTRIES = _build_compiled_trigger_entries()


SKILLS_REGISTRY: dict[str, str] = {
    manifest.key: manifest.skill_file for manifest in SKILL_MANIFESTS.values()
}


def get_skill_path(skill_key: str) -> str | None:
    manifest = SKILL_MANIFESTS.get(skill_key)
    return manifest.skill_file if manifest else None


def get_manifest(skill_key: str) -> SkillManifest | None:
    return SKILL_MANIFESTS.get(skill_key)


def get_manifest_for_plan_type(plan_type: str) -> SkillManifest | None:
    for manifest in SKILL_MANIFESTS.values():
        if manifest.plan_type == plan_type:
            return manifest
    return None


def compiled_triggers() -> tuple[tuple[SkillManifest, tuple[re.Pattern[str], ...]], ...]:
    return _COMPILED_TRIGGER_ENTRIES


def get_tool_call_spec(skill_key: str, tool_name: str) -> ToolCallSpec | None:
    manifest = SKILL_MANIFESTS.get(skill_key)
    if manifest is None:
        return None
    for spec in manifest.mcp_tool_chain:
        if spec.tool == tool_name:
            return spec
    return None


def skills_for_source(source_key: str, source_type: SourceType | None = None) -> list[SkillManifest]:
    return [
        manifest for manifest in SKILL_MANIFESTS.values()
        if manifest.source_key == source_key
        and (source_type is None or manifest.source_type == source_type)
    ]


def skills_for_service(service_key: str) -> list[SkillManifest]:
    return [
        manifest for manifest in SKILL_MANIFESTS.values()
        if service_key in manifest.required_services
    ]


def skills_for_sample_type(sample_type: SampleType) -> list[SkillManifest]:
    return [
        manifest for manifest in SKILL_MANIFESTS.values()
        if SampleType.ANY in manifest.sample_types or sample_type in manifest.sample_types
    ]


def check_service_availability(
    skill_key: str,
    available_services: set[str],
) -> tuple[bool, list[str]]:
    manifest = SKILL_MANIFESTS.get(skill_key)
    if manifest is None:
        return False, [f"unknown skill: {skill_key}"]
    missing = [service for service in manifest.required_services if service not in available_services]
    return len(missing) == 0, missing
