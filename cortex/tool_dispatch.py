"""Build, deduplicate, validate, and execute tool calls for chat_with_agent.

Extracted from ``cortex/app.py`` to keep the main chat orchestrator focused
on flow control while this module handles MCP client lifecycle, ENCODE retry
(relaxation + biosample/assay swap), download chaining, find_file → parse/read
chaining, edgepython output-path injection, and auto-fetch of missing ENCFF
accessions.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from atlas.config import (
    CONSORTIUM_REGISTRY,
    get_all_tool_aliases,
    get_all_param_aliases,
)
from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.config import SERVICE_REGISTRY, get_service_url
from cortex.dataframe_actions import execute_local_dataframe_call, is_local_dataframe_tool
from cortex.edgepython_plot_params import build_svg_companion_path, normalize_generate_plot_params
from cortex.encode_helpers import (
    _looks_like_assay,
    _correct_tool_routing,
    _validate_encode_params,
)
from cortex.data_call_generator import (
    _validate_analyzer_params,
    _validate_edgepython_params,
)
from cortex.igvf_helpers import _correct_igvf_tool_routing, _validate_igvf_params
from cortex.llm_validators import _parse_tag_params
from cortex.path_helpers import _pick_file_tool
from cortex.remote_orchestration import (
    _hydrate_request_placeholders,
    _inject_launchpad_context_params,
)
from cortex.tool_contracts import validate_against_schema

logger = get_logger(__name__)

REMOTE_STAGE_MCP_TIMEOUT = float(os.getenv("LAUNCHPAD_STAGE_TIMEOUT", "3600"))

_BED_COUNT_INTENT_RE = re.compile(
    r"\b(count|counts|summarize|summarise|tally|show)\b.*\bbed\b.*\b(chromosome|chromosomes|chr)\b"
    r"|\b(chromosome|chromosomes|chr)\b.*\bbed\b",
    re.IGNORECASE,
)

_MODIFICATION_COUNT_INTENT_RE = re.compile(
    r"\b(?:count|counts|summarize|summarise|tally|show|plot|graph|chart)\b.*?\b(?P<mod>[A-Za-z0-9_+\-]+)\b\s+modifications?\b.*?\b(chromosome|chromosomes|chr)\b"
    r"|\b(?P<mod2>[A-Za-z0-9_+\-]+)\b\s+modifications?\b.*?\b(chromosome|chromosomes|chr)\b",
    re.IGNORECASE,
)

_BED_FILENAME_METADATA_RE = re.compile(
    r"^(?P<sample>.+?)\.(?P<genome>[^.]+)\.(?P<strand>plus|minus)\.(?P<modification>[^.]+)\.filtered\.bed$",
    re.IGNORECASE,
)


def _extract_modification_name(user_message: str) -> str | None:
    match = _MODIFICATION_COUNT_INTENT_RE.search(user_message)
    if not match:
        return None
    return (match.group("mod") or match.group("mod2") or "").lower() or None


def _parse_bed_filename_metadata(file_path: str) -> dict[str, str] | None:
    match = _BED_FILENAME_METADATA_RE.match(Path(file_path).name)
    if not match:
        return None
    return {key: value for key, value in match.groupdict().items()}


async def _run_bed_counter_script(
    bed_paths: list[str],
    source_results: list[dict],
) -> None:
    """Run the allowlisted BED counter script and append its result."""
    chain_params = {
        "script_id": "analyze_job_results/count_bed",
        "script_args": ["--json", *bed_paths],
        "timeout_seconds": 60.0,
    }
    launchpad_client = MCPHttpClient(
        name="launchpad",
        base_url=get_service_url("launchpad"),
    )
    await launchpad_client.connect()
    try:
        chain_result = await launchpad_client.call_tool("run_allowlisted_script", **chain_params)
    finally:
        await launchpad_client.disconnect()

    source_results.append({
        "tool": "run_allowlisted_script",
        "params": chain_params,
        "data": chain_result,
    })
    logger.info("Chain call successful", tool="run_allowlisted_script")


async def _chain_list_job_files(
    result_data: dict,
    source_results: list[dict],
    *,
    user_message: str,
    active_skill: str,
) -> None:
    """After list_job_files, optionally chain into modification BED counting."""
    if active_skill != "analyze_job_results":
        return

    modification_name = _extract_modification_name(user_message)
    if not modification_name:
        return

    work_dir = result_data.get("work_dir")
    files = result_data.get("files")
    if not work_dir or not isinstance(files, list):
        return

    matched_paths: list[str] = []
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        relative_path = file_entry.get("path") or file_entry.get("name")
        if not relative_path:
            continue
        metadata = _parse_bed_filename_metadata(relative_path)
        if not metadata:
            continue
        if metadata["modification"].lower() != modification_name:
            continue
        bed_path = Path(relative_path).expanduser()
        if not bed_path.is_absolute():
            bed_path = (Path(work_dir) / bed_path).resolve()
        else:
            bed_path = bed_path.resolve()
        matched_paths.append(str(bed_path))

    if not matched_paths:
        return

    deduped_paths = sorted(dict.fromkeys(matched_paths))
    logger.info(
        "Chaining list_job_files follow-up",
        chain_tool="run_allowlisted_script",
        matched_files=deduped_paths,
        modification=modification_name,
    )
    await _run_bed_counter_script(deduped_paths, source_results)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tools that MUST route to edgepython (DE-stateful)
EDGEPYTHON_ONLY_TOOLS = frozenset({
    "annotate_genes", "filter_de_genes",
})

# Tools that MUST route to analyzer (gene annotation + enrichment)
ANALYZER_ONLY_TOOLS = frozenset({
    "lookup_gene", "translate_gene_ids",
    "run_go_enrichment", "run_pathway_enrichment",
    "get_enrichment_results", "get_term_genes",
})

_BASE_TOOL_ALIASES: dict[str, str] = {
    # Analyzer tool aliases (not in CONSORTIUM_REGISTRY)
    "list_workflows": "list_job_files",
    "list_files": "list_job_files",
    "browse_files": "list_job_files",
    "browse_workflow": "list_job_files",
    "find_files": "find_file",
    "search_file": "find_file",
    "search_files": "find_file",
    "read_file": "read_file_content",
    "get_file": "read_file_content",
    "parse_csv": "parse_csv_file",
    "parse_bed": "parse_bed_file",
    "analysis_summary": "get_analysis_summary",
    "job_summary": "get_analysis_summary",
    # Compatibility fallback for hallucinated BAM-detail request tool.
    # Analyzer does not expose show_bam_details, so route to list_job_files first.
    "show_bam_details": "list_job_files",
    # edgePython tool aliases (hallucinated gene lookup names)
    "get_gene_info": "lookup_gene",
    "gene_info": "lookup_gene",
    "gene_lookup": "lookup_gene",
    "get_gene": "lookup_gene",
    "find_gene": "lookup_gene",
    "subset_dataframe": "filter_dataframe",
    "filter_rows": "filter_dataframe",
    "select_columns": "select_dataframe_columns",
    "subset_columns": "select_dataframe_columns",
    "rename_columns": "rename_dataframe_columns",
    "group_dataframe": "aggregate_dataframe",
    "groupby_dataframe": "aggregate_dataframe",
}

_BASE_PARAM_ALIASES: dict[str, dict[str, str]] = {
    # Analyzer param aliases
    "find_file": {
        "file_path": "file_name",
        "filename": "file_name",
    },
    # Launchpad param aliases for remote execution tools
    "get_slurm_defaults": {
        "profile_id": "ssh_profile_id",
    },
    # edgePython param aliases (hallucinated parameter names for lookup_gene)
    "lookup_gene": {
        "gene_name": "gene_symbols",
        "gene": "gene_symbols",
        "symbols": "gene_symbols",
        "ids": "gene_ids",
    },
}

# Tokens that should NOT be treated as a nickname when parsing
# "defaults for <token>" in remote execution messages.
_INVALID_NICK_TOKENS = frozenset({
    "default", "defaults", "profile", "profiles", "slurm", "ssh", "remote",
    "account", "partition", "cpu", "gpu", "for", "use", "using",
})


def _looks_like_placeholder(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(stripped) and (
        (stripped.startswith("<") and stripped.endswith(">"))
        or (stripped.startswith("{") and stripped.endswith("}"))
    )


def _normalize_get_slurm_defaults_params(params: dict | None) -> dict:
    normalized = dict(params or {})
    legacy_profile_id = normalized.pop("profile_id", None)
    if legacy_profile_id and not normalized.get("ssh_profile_id"):
        normalized["ssh_profile_id"] = legacy_profile_id
    return normalized


def _extract_remote_profile_nickname(user_message: str) -> str | None:
    nickname = None
    nick_match = re.search(r'\bnickname\s+([a-zA-Z0-9._-]+)\b', user_message, re.IGNORECASE)
    if nick_match:
        cand = nick_match.group(1).strip()
        if cand.lower() not in _INVALID_NICK_TOKENS:
            nickname = cand

    if not nickname:
        profile_match = re.search(r'\bprofile\s+([a-zA-Z0-9._-]+)\b', user_message, re.IGNORECASE)
        if profile_match:
            cand = profile_match.group(1).strip()
            if cand.lower() not in _INVALID_NICK_TOKENS:
                nickname = cand

    if not nickname:
        hpc_match = re.search(r'\b(hpc\d+)\b', user_message, re.IGNORECASE)
        if hpc_match:
            nickname = hpc_match.group(1)

    return nickname


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ToolExecutionResult:
    """Aggregate result of executing all tool calls."""

    all_results: dict[str, list[dict]] = field(default_factory=dict)
    pending_download_files: list[dict] = field(default_factory=list)
    # Side effects that the orchestrator must apply back:
    active_skill: str = ""
    needs_approval: bool = False
    clean_markdown: str | None = None  # if set, overrides caller's clean_markdown


# ---------------------------------------------------------------------------
# Build calls_by_source
# ---------------------------------------------------------------------------

def _get_aliases() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Merge atlas registry aliases with our base aliases.

    Consortium-registered canonical tool names are protected: if a base alias
    would remap a name that is itself a real consortium tool (i.e. appears as
    an alias target or has param_aliases defined), the base alias is skipped
    so the consortium tool remains reachable.
    """
    tool_aliases = get_all_tool_aliases()
    # Canonical consortium tool names = alias targets + param_alias keys
    _consortium_canonical: set[str] = set(tool_aliases.values())
    param_aliases = get_all_param_aliases()
    _consortium_canonical.update(param_aliases.keys())
    # Add base aliases only when the alias key is not itself a consortium tool
    for _alias, _target in _BASE_TOOL_ALIASES.items():
        if _alias not in _consortium_canonical and _alias not in tool_aliases:
            tool_aliases[_alias] = _target
    for tool_name, pa in _BASE_PARAM_ALIASES.items():
        param_aliases.setdefault(tool_name, {}).update(pa)
    return tool_aliases, param_aliases


def build_calls_by_source(
    *,
    data_call_matches: list,
    legacy_encode_matches: list,
    legacy_analysis_matches: list,
    auto_calls: list[dict],
    has_any_tags: bool,
    user_id: str,
    project_id: str | None,
    user_message: str,
    conversation_history: list,
    history_blocks: list,
    project_dir: str | None,
    active_skill: str,
) -> dict[str, list[dict]]:
    """Collect all tool calls (auto-generated + LLM tags) into a unified structure.

    Returns ``{source_key: [{"tool": str, "params": dict}, …]}``.
    """
    tool_aliases, param_aliases = _get_aliases()
    calls_by_source: dict[str, list[dict]] = {}

    # --- From auto-generated tags (safety net) ---
    if not has_any_tags and auto_calls:
        for ac in auto_calls:
            ac_tool = ac["tool"]
            ac_params = _hydrate_request_placeholders(
                dict(ac["params"]),
                user_id=user_id,
                project_id=project_id,
            )
            ac_source = ac["source_key"]
            if ac_source == "encode":
                ac_tool, ac_params = _correct_tool_routing(
                    ac_tool, ac_params, user_message, conversation_history)
                ac_params = _validate_encode_params(ac_tool, ac_params, user_message)
            elif ac_source == "igvf":
                ac_params = _validate_igvf_params(ac_tool, ac_params, user_message)
            elif ac_source == "analyzer":
                ac_params = _validate_analyzer_params(
                    ac_tool, ac_params, user_message,
                    conversation_history=conversation_history,
                    history_blocks=history_blocks,
                    project_dir=project_dir,
                )
            entry: dict = {"tool": ac_tool, "params": ac_params}
            if "_chain" in ac:
                entry["_chain"] = ac["_chain"]
            calls_by_source.setdefault(ac_source, []).append(entry)

    # --- From new DATA_CALL tags ---
    for match in data_call_matches:
        source_type = match.group(1)
        source_key = match.group(2)
        tool_name = match.group(3)
        params_str = match.group(4)

        corrected_tool = tool_aliases.get(tool_name, tool_name)
        if corrected_tool != tool_name:
            logger.warning("Corrected hallucinated tool name",
                           original=tool_name, corrected=corrected_tool)

        # Cross-source re-routing
        if corrected_tool in EDGEPYTHON_ONLY_TOOLS and source_key != "edgepython":
            logger.warning("Re-routing tool to edgepython",
                           tool=corrected_tool,
                           original_source=f"{source_type}/{source_key}")
            source_type = "service"
            source_key = "edgepython"
        elif corrected_tool in ANALYZER_ONLY_TOOLS and source_key != "analyzer":
            logger.warning("Re-routing tool to analyzer",
                           tool=corrected_tool,
                           original_source=f"{source_type}/{source_key}")
            source_type = "service"
            source_key = "analyzer"

        params = _hydrate_request_placeholders(
            _parse_tag_params(params_str),
            user_id=user_id,
            project_id=project_id,
        )

        # Fix hallucinated parameter names
        p_aliases = param_aliases.get(corrected_tool, {})
        if p_aliases:
            params = {p_aliases.get(k, k): v for k, v in params.items()}

        if source_key == "encode":
            corrected_tool, params = _correct_tool_routing(
                corrected_tool, params, user_message, conversation_history)
            params = _validate_encode_params(corrected_tool, params, user_message)
        if source_key == "igvf":
            corrected_tool, params = _correct_igvf_tool_routing(
                corrected_tool, params, user_message)
            params = _validate_igvf_params(corrected_tool, params, user_message)
        if source_key == "analyzer":
            params = _validate_analyzer_params(
                corrected_tool, params, user_message,
                conversation_history=conversation_history,
                history_blocks=history_blocks,
                project_dir=project_dir,
            )
        if source_key == "edgepython":
            params = _validate_edgepython_params(
                corrected_tool, params, user_message,
                conversation_history=conversation_history,
            )

        calls_by_source.setdefault(source_key, []).append({
            "tool": corrected_tool,
            "params": params,
        })

    # --- From legacy ENCODE_CALL tags ---
    for match in legacy_encode_matches:
        tool_name = match.group(1)
        params_str = match.group(2)
        params = _hydrate_request_placeholders(
            _parse_tag_params(params_str),
            user_id=user_id,
            project_id=project_id,
        )
        corrected_tool = tool_aliases.get(tool_name, tool_name)
        if corrected_tool != tool_name:
            logger.warning("Corrected hallucinated tool name (legacy)",
                           original=tool_name, corrected=corrected_tool)
        p_aliases = param_aliases.get(corrected_tool, {})
        if p_aliases:
            params = {p_aliases.get(k, k): v for k, v in params.items()}
        corrected_tool, params = _correct_tool_routing(
            corrected_tool, params, user_message, conversation_history)
        params = _validate_encode_params(corrected_tool, params, user_message)
        calls_by_source.setdefault("encode", []).append({
            "tool": corrected_tool,
            "params": params,
        })

    # --- From legacy ANALYSIS_CALL tags ---
    tool_map = {
        "summary": "get_analysis_summary",
        "categorize_files": "categorize_job_files",
        "list_files": "list_job_files",
    }
    for match in legacy_analysis_matches:
        analysis_type = match.group(1)
        run_uuid = match.group(2)
        mapped_tool = tool_map.get(analysis_type, analysis_type)
        calls_by_source.setdefault("analyzer", []).append({
            "tool": mapped_tool,
            "params": _hydrate_request_placeholders(
                {"run_uuid": run_uuid},
                user_id=user_id,
                project_id=project_id,
            ),
        })

    # --- Remote execution hardening ---
    if active_skill == "remote_execution":
        _augment_remote_execution_calls(
            calls_by_source, user_message, user_id, project_id,
        )

    return calls_by_source


def _augment_remote_execution_calls(
    calls_by_source: dict[str, list[dict]],
    user_message: str,
    user_id: str,
    project_id: str | None,
) -> None:
    """If user asked about defaults/profiles, ensure get_slurm_defaults is included."""
    launchpad_calls = calls_by_source.setdefault("launchpad", [])
    nickname = _extract_remote_profile_nickname(user_message)

    submit_calls = [c for c in launchpad_calls if c.get("tool") == "submit_dogme_job"]
    if submit_calls:
        launchpad_calls[:] = [c for c in launchpad_calls if c.get("tool") != "submit_dogme_job"]

        has_list_profiles = any(c.get("tool") == "list_ssh_profiles" for c in launchpad_calls)
        has_get_defaults = any(c.get("tool") == "get_slurm_defaults" for c in launchpad_calls)

        if not has_list_profiles:
            launchpad_calls.insert(0, {"tool": "list_ssh_profiles", "params": {"user_id": user_id}})

        if not has_get_defaults:
            defaults_params: dict[str, str] = {"user_id": user_id}
            if project_id:
                defaults_params["project_id"] = project_id
            if nickname:
                defaults_params["profile_nickname"] = nickname
            launchpad_calls.append({"tool": "get_slurm_defaults", "params": defaults_params})

        logger.info(
            "Removed direct remote submit call and preserved pre-approval profile discovery",
            user_id=user_id,
            project_id=project_id,
            profile_nickname=nickname,
            removed_calls=len(submit_calls),
        )

    if not re.search(
        r'\b(defaults?|account|partition|slurm|hpc\d+)\b',
        user_message, re.IGNORECASE,
    ):
        for call in launchpad_calls:
            if call.get("tool") != "get_slurm_defaults":
                continue
            params = _normalize_get_slurm_defaults_params(call.get("params"))
            if _looks_like_placeholder(params.get("ssh_profile_id")):
                params.pop("ssh_profile_id", None)
                if nickname and not params.get("profile_nickname"):
                    params["profile_nickname"] = nickname
                call["params"] = params
        return

    has_list_profiles = any(c.get("tool") == "list_ssh_profiles" for c in launchpad_calls)
    has_get_defaults = any(c.get("tool") == "get_slurm_defaults" for c in launchpad_calls)

    for call in launchpad_calls:
        if call.get("tool") != "get_slurm_defaults":
            continue
        params = _normalize_get_slurm_defaults_params(call.get("params"))
        if _looks_like_placeholder(params.get("ssh_profile_id")):
            params.pop("ssh_profile_id", None)
            if nickname and not params.get("profile_nickname"):
                params["profile_nickname"] = nickname
            call["params"] = params

    if not (has_list_profiles and not has_get_defaults):
        return

    defaults_params: dict[str, str] = {"user_id": user_id}
    if project_id:
        defaults_params["project_id"] = project_id
    if nickname:
        defaults_params["profile_nickname"] = nickname

    launchpad_calls.append({"tool": "get_slurm_defaults", "params": defaults_params})
    logger.info(
        "Augmented remote_execution call set with get_slurm_defaults",
        user_id=user_id, project_id=project_id, profile_nickname=nickname,
    )


# ---------------------------------------------------------------------------
# Deduplication and validation
# ---------------------------------------------------------------------------

def deduplicate_calls(calls_by_source: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Deduplicate tool calls within each source by (tool, canonical_params)."""
    for src_key in list(calls_by_source):
        seen: set[str] = set()
        deduped: list[dict] = []
        for call in calls_by_source[src_key]:
            canon_params = tuple(
                sorted(
                    (k, v.lower() if isinstance(v, str) else v)
                    for k, v in call["params"].items()
                )
            )
            key_str = str((call["tool"], canon_params))
            if key_str not in seen:
                seen.add(key_str)
                deduped.append(call)
            else:
                logger.info("Deduplicated duplicate tool call",
                            tool=call["tool"], params=call["params"])
        calls_by_source[src_key] = deduped
    return calls_by_source


def validate_call_schemas(calls_by_source: dict[str, list[dict]]) -> None:
    """Validate params against cached tool schemas (in-place).

    Calls with missing required parameters are removed to prevent
    guaranteed-to-fail MCP requests.
    """
    for src_key, calls_list in list(calls_by_source.items()):
        surviving: list[dict] = []
        for call in calls_list:
            if "__routing_error__" in call.get("params", {}):
                surviving.append(call)
                continue
            cleaned, violations = validate_against_schema(
                call["tool"], call["params"], src_key,
            )
            if violations:
                logger.warning("Schema validation issues",
                               tool=call["tool"], source=src_key,
                               violations=violations)
            # Block calls that are missing required params — they will
            # fail at the MCP layer with a Pydantic validation error anyway.
            has_missing_required = any(
                v.startswith("Missing required params:") for v in violations
            )
            if has_missing_required:
                logger.error(
                    "Dropping tool call: missing required params",
                    tool=call["tool"], source=src_key, violations=violations,
                )
                continue
            call["params"] = cleaned
            surviving.append(call)
        calls_by_source[src_key] = surviving


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

class ChatCancelled(Exception):
    """Raised when the user cancels the request mid-flight."""

    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        self.detail = detail
        super().__init__(detail)


async def execute_tool_calls(
    calls_by_source: dict[str, list[dict]],
    *,
    user_id: str,
    username: str | None,
    project_id: str | None,
    project_dir_path: Path | None,
    user_message: str,
    active_skill: str,
    needs_approval: bool,
    request_id: str,
    history_blocks: list,
    is_cancelled: Callable[[str], bool],
    emit_progress: Callable,
) -> ToolExecutionResult:
    """Execute all tool calls grouped by MCP source.

    Returns a :class:`ToolExecutionResult` that contains the aggregated results
    **and** any side-effect overrides (active_skill, needs_approval,
    clean_markdown) that the orchestrator must apply.
    """
    result = ToolExecutionResult(
        active_skill=active_skill,
        needs_approval=needs_approval,
    )
    all_results: dict[str, list[dict]] = {}

    for source_key, calls in calls_by_source.items():
        logger.info("Executing tool calls", source=source_key, count=len(calls))
        source_label = (
            "Local Dataframe Actions"
            if source_key == "cortex"
            else (
                CONSORTIUM_REGISTRY.get(source_key, {}).get("display_name")
                or SERVICE_REGISTRY.get(source_key, {}).get("display_name", source_key)
            )
        )
        emit_progress(request_id, "tools", f"Querying {source_label}...")

        if source_key == "cortex":
            source_results: list[dict] = []
            for call in calls:
                tool_name = call["tool"]
                params = call["params"]
                try:
                    if not is_local_dataframe_tool(tool_name):
                        raise ValueError(f"Unknown local dataframe tool: {tool_name}")
                    result_data = execute_local_dataframe_call(
                        tool_name,
                        params,
                        history_blocks=history_blocks,
                        project_dir_path=project_dir_path,
                    )
                    source_results.append({
                        "tool": tool_name,
                        "params": params,
                        "data": result_data,
                    })
                    logger.info("Local dataframe action successful", tool=tool_name)
                except Exception as exc:
                    logger.error("Local dataframe action failed", tool=tool_name, error=str(exc))
                    source_results.append({
                        "tool": tool_name,
                        "params": params,
                        "error": str(exc),
                    })
            all_results[source_key] = source_results
            continue

        try:
            url = get_service_url(source_key)
        except KeyError:
            logger.error("Unknown source", source=source_key)
            all_results[source_key] = [
                {"tool": c["tool"], "params": c["params"],
                 "error": f"Unknown source: {source_key}"}
                for c in calls
            ]
            continue

        # Launchpad calls (especially sync/submission) can take several
        # minutes for large result sets.  Use a generous timeout to avoid
        # killing transfers mid-stream.
        _mcp_timeout = REMOTE_STAGE_MCP_TIMEOUT if source_key == "launchpad" else 120.0
        mcp_client = MCPHttpClient(name=source_key, base_url=url, timeout=_mcp_timeout)
        source_results: list[dict] = []

        try:
            await mcp_client.connect()

            for call in calls:
                tool_name = call["tool"]
                params = call["params"]

                if source_key == "launchpad":
                    patched = _inject_launchpad_context_params(
                        tool_name, params,
                        user_id=user_id, project_id=project_id,
                    )
                    if patched != params:
                        logger.info("Injected missing Launchpad context params",
                                    tool=tool_name, original_params=params,
                                    patched_params=patched)
                    params = patched

                # Cancellation check
                if is_cancelled(request_id):
                    raise ChatCancelled(
                        "tools",
                        f"Cancelled before running {tool_name} on {source_label}.",
                    )

                # edgepython: stop on first pipeline failure
                if (source_key == "edgepython"
                        and source_results and "error" in source_results[-1]):
                    logger.info("Skipping remaining edgepython calls after pipeline failure",
                                skipped_tool=tool_name)
                    source_results.append({
                        "tool": tool_name, "params": params,
                        "error": "Skipped — previous pipeline step failed",
                    })
                    continue

                # Routing errors
                if "__routing_error__" in params:
                    error_msg = params.pop("__routing_error__")
                    logger.warning("Skipping tool call due to routing error",
                                   tool=tool_name, error=error_msg)
                    source_results.append({
                        "tool": tool_name, "params": params, "error": error_msg,
                    })
                    continue

                logger.info("Calling tool", source=source_key,
                            tool=tool_name, params=params)

                # --- edgepython output path injection ---
                if source_key == "edgepython" and project_dir_path:
                    _inject_edgepython_output_path(
                        tool_name, params, project_dir_path,
                    )

                # Call the tool (with single retry for transient errors)
                result_data = await _call_with_retry(
                    mcp_client, source_key, tool_name, params,
                    source_results,
                )
                if result_data is _SKIP_SENTINEL:
                    continue

                # --- ENCODE search retry: relaxation + biosample ↔ assay swap ---
                result_data, tool_name, params = await _encode_retry_if_needed(
                    mcp_client, source_key, tool_name, params, result_data,
                )

                try:
                    source_results.append({
                        "tool": tool_name, "params": params, "data": result_data,
                    })
                    logger.info("Tool call successful",
                                source=source_key, tool=tool_name)

                    # --- Download chaining ---
                    _handle_download_chain(
                        call, tool_name, params, result_data,
                        user_message, result, source_key,
                    )

                    # --- list_job_files -> modification BED counting ---
                    if (
                        tool_name == "list_job_files"
                        and isinstance(result_data, dict)
                        and result_data.get("success")
                    ):
                        await _chain_list_job_files(
                            result_data,
                            source_results,
                            user_message=user_message,
                            active_skill=result.active_skill,
                        )

                    # --- find_file → parse/read chaining ---
                    if (tool_name == "find_file"
                            and isinstance(result_data, dict)
                            and result_data.get("success")
                            and result_data.get("primary_path")):
                        await _chain_find_file(
                            mcp_client, call, params, result_data, source_results,
                            user_message=user_message,
                            active_skill=result.active_skill,
                        )

                except Exception as e:
                    logger.error("Tool call failed", source=source_key,
                                 tool=tool_name, error=str(e))
                    source_results.append({
                        "tool": tool_name, "params": params, "error": str(e),
                    })

        except ChatCancelled:
            raise
        except Exception as e:
            logger.error("Failed to connect to source",
                         source=source_key, error=str(e))
            source_results = [
                {"tool": c["tool"], "params": c["params"],
                 "error": f"Connection failed: {e}"}
                for c in calls
            ]
        finally:
            await mcp_client.disconnect()

        all_results[source_key] = source_results

    result.all_results = all_results
    return result


# --- Sentinel to signal "skip this call" from _call_with_retry ---
_SKIP_SENTINEL = object()


async def _call_with_retry(
    mcp_client: MCPHttpClient,
    source_key: str,
    tool_name: str,
    params: dict,
    source_results: list[dict],
):
    """Call a tool, retrying once on transient connection errors.

    Returns the result data, or ``_SKIP_SENTINEL`` if the call should be
    skipped (error already appended to *source_results*).
    """
    try:
        return await mcp_client.call_tool(tool_name, **params)
    except (ConnectionError, TimeoutError, OSError) as conn_err:
        logger.warning("Transient error, retrying once",
                        source=source_key, tool=tool_name, error=str(conn_err))
        await asyncio.sleep(2)
        try:
            return await mcp_client.call_tool(tool_name, **params)
        except Exception as retry_err:
            logger.error("Retry also failed", source=source_key,
                         tool=tool_name, error=str(retry_err))
            source_results.append({
                "tool": tool_name, "params": params,
                "error": f"Connection failed after retry: {retry_err}",
            })
            return _SKIP_SENTINEL
    except RuntimeError as mcp_err:
        logger.error("MCP tool error", source=source_key,
                     tool=tool_name, error=str(mcp_err))
        source_results.append({
            "tool": tool_name, "params": params,
            "error": f"MCP tool error: {mcp_err}",
        })
        return _SKIP_SENTINEL


def _inject_edgepython_output_path(
    tool_name: str,
    params: dict,
    project_dir_path: Path,
) -> None:
    """Redirect edgepython output files into the provided root's de_results folder."""
    ep_output_dir = project_dir_path / "de_results"
    if tool_name == "generate_plot":
        params.update(normalize_generate_plot_params(params))
        if not params.get("output_path"):
            ep_output_dir.mkdir(parents=True, exist_ok=True)
            plot_type = params.get("plot_type", "plot")
            result_name = params.get("result_name", "")
            suffix = f"_{result_name}" if result_name else ""
            params["output_path"] = str(ep_output_dir / f"{plot_type}{suffix}.png")
        params.setdefault("svg_output_path", build_svg_companion_path(str(params["output_path"])))
    elif tool_name == "save_results":
        ep_output_dir.mkdir(parents=True, exist_ok=True)
        fmt = params.get("format", "csv")
        orig = params.get("output_path", "")
        fname = Path(orig).name if orig else f"de_results.{fmt}"
        params["output_path"] = str(ep_output_dir / fname)


async def _encode_retry_if_needed(
    mcp_client: MCPHttpClient,
    source_key: str,
    tool_name: str,
    params: dict,
    result_data,
) -> tuple:
    """If ENCODE search returned 0 results, try relaxation then biosample↔assay swap.

    Returns ``(result_data, tool_name, params)`` (possibly updated).
    """
    if source_key != "encode":
        return result_data, tool_name, params
    if tool_name not in ("search_by_biosample", "search_by_assay"):
        return result_data, tool_name, params

    is_zero = (
        (isinstance(result_data, list) and len(result_data) == 0)
        or (isinstance(result_data, dict) and result_data.get("total", -1) == 0)
    )
    if not is_zero:
        return result_data, tool_name, params

    # Phase 1: relax assay_title filter
    relaxed = False
    if (tool_name == "search_by_biosample"
            and params.get("search_term") and params.get("assay_title")):
        relaxed_params = {k: v for k, v in params.items() if k != "assay_title"}
        logger.warning(
            "ENCODE compound search returned 0 — retrying without assay filter",
            original_params=params, relaxed_params=relaxed_params,
        )
        try:
            relax_result = await mcp_client.call_tool(tool_name, **relaxed_params)
            relax_count = (
                len(relax_result) if isinstance(relax_result, list)
                else relax_result.get("total", 0) if isinstance(relax_result, dict)
                else 0
            )
            if relax_count > 0:
                result_data = relax_result
                params = relaxed_params
                relaxed = True
                logger.info("Relaxed search returned results",
                            tool=tool_name, count=relax_count)
        except Exception as relax_err:
            logger.warning("Relaxed search failed",
                           tool=tool_name, error=str(relax_err))

    # Phase 2: biosample ↔ assay swap
    if not relaxed:
        swap_tool = None
        swap_params: dict = {}
        if tool_name == "search_by_biosample":
            st = params.get("search_term", "")
            if st:
                swap_tool = "search_by_assay"
                swap_params = {"assay_title": st}
                if "organism" in params:
                    swap_params["organism"] = params["organism"]
        elif tool_name == "search_by_assay":
            at = params.get("assay_title", "")
            if at and not _looks_like_assay(at):
                swap_tool = "search_by_biosample"
                swap_params = {"search_term": at}
                if "organism" in params:
                    swap_params["organism"] = params["organism"]

        if swap_tool:
            logger.warning(
                "ENCODE search returned 0 results — retrying with alternate tool",
                original_tool=tool_name, swap_tool=swap_tool,
                original_params=params, swap_params=swap_params,
            )
            try:
                swap_result = await mcp_client.call_tool(swap_tool, **swap_params)
                swap_count = (
                    len(swap_result) if isinstance(swap_result, list)
                    else swap_result.get("total", 0) if isinstance(swap_result, dict)
                    else 0
                )
                if swap_count > 0:
                    result_data = swap_result
                    tool_name = swap_tool
                    params = swap_params
                    logger.info("Alternate tool returned results",
                                tool=swap_tool, count=swap_count)
                else:
                    logger.info("Alternate tool also returned 0 results",
                                tool=swap_tool)
            except Exception as swap_err:
                logger.warning("Alternate tool call failed",
                               tool=swap_tool, error=str(swap_err))

    return result_data, tool_name, params


def _handle_download_chain(
    call: dict,
    tool_name: str,
    params: dict,
    result_data,
    user_message: str,
    result: ToolExecutionResult,
    source_key: str = "",
) -> None:
    """After get_file_metadata / get_file_download_url, extract the download
    URL and accumulate files for an approval-gated download."""
    _download_tools = {"get_file_metadata", "get_file_download_url"}
    if tool_name not in _download_tools or not isinstance(result_data, dict):
        return

    msg_lower = user_message.lower()
    user_wants_download = any(
        w in msg_lower for w in ("download", "grab", "fetch", "save")
    )
    is_download_chain = (
        call.get("_chain") == "download"
        or result.active_skill == "download_files"
        or user_wants_download
    )
    if not is_download_chain:
        return

    dl_url = None
    file_acc = params.get("file_accession", "")
    file_size = result_data.get("file_size")
    file_fmt = result_data.get("file_format", "")
    file_name = f"{file_acc}.{file_fmt}" if file_acc and file_fmt else file_acc

    # IGVF: get_file_download_url returns a direct "download_url" field
    if result_data.get("download_url"):
        dl_url = result_data["download_url"]
    # IGVF: get_file_metadata returns "href" (relative to api.data.igvf.org)
    elif source_key == "igvf" and result_data.get("href"):
        href = result_data["href"]
        if href.startswith("http"):
            dl_url = href
        else:
            dl_url = f"https://api.data.igvf.org{href}"
    # ENCODE: cloud_metadata or href
    else:
        cloud = result_data.get("cloud_metadata")
        if isinstance(cloud, dict) and cloud.get("url"):
            dl_url = cloud["url"]
        elif result_data.get("href"):
            dl_url = f"https://www.encodeproject.org{result_data['href']}"

    if not dl_url:
        return

    dl_file_info = {
        "url": dl_url,
        "filename": file_name,
        "size_bytes": file_size,
        "accession": file_acc,
    }
    result.pending_download_files.append(dl_file_info)
    logger.info("Download chain: resolved URL from file metadata",
                file_accession=file_acc, url=dl_url, size_bytes=file_size,
                source=source_key)

    result.active_skill = "download_files"
    result.needs_approval = True
    result.clean_markdown = _build_download_plan(result.pending_download_files)


async def _chain_find_file(
    mcp_client: MCPHttpClient,
    call: dict,
    params: dict,
    result_data: dict,
    source_results: list[dict],
    *,
    user_message: str,
    active_skill: str,
) -> None:
    """After a successful find_file, chain into parse/read for the found file."""
    chain_tool = call.get("_chain")
    primary_path = result_data["primary_path"]
    if (
        active_skill == "analyze_job_results"
        and primary_path.lower().endswith(".bed")
        and _BED_COUNT_INTENT_RE.search(user_message)
    ):
        chain_tool = "run_allowlisted_script"
    if not chain_tool:
        chain_tool = _pick_file_tool(primary_path)

    chain_work_dir = result_data.get("work_dir") or params.get("work_dir")
    chain_uuid = result_data.get("run_uuid") or params.get("run_uuid")

    if not (chain_work_dir or chain_uuid):
        return

    logger.info("Chaining find_file follow-up",
                chain_tool=chain_tool, file_path=primary_path)
    try:
        if chain_tool == "run_allowlisted_script":
            resolved_primary_path = Path(primary_path).expanduser()
            if not resolved_primary_path.is_absolute() and chain_work_dir:
                resolved_primary_path = (Path(chain_work_dir) / resolved_primary_path).resolve()
            elif resolved_primary_path.is_absolute():
                resolved_primary_path = resolved_primary_path.resolve()
            await _run_bed_counter_script([str(resolved_primary_path)], source_results)
            return
        else:
            chain_params = {"file_path": primary_path}
            if chain_work_dir:
                chain_params["work_dir"] = chain_work_dir
            elif chain_uuid:
                chain_params["run_uuid"] = chain_uuid
            if chain_tool == "parse_csv_file":
                chain_params["max_rows"] = 100
            elif chain_tool == "parse_bed_file":
                chain_params["max_records"] = 100
            elif chain_tool == "read_file_content":
                chain_params["preview_lines"] = 50
            chain_result = await mcp_client.call_tool(chain_tool, **chain_params)
        source_results.append({
            "tool": chain_tool, "params": chain_params, "data": chain_result,
        })
        logger.info("Chain call successful", tool=chain_tool)
    except Exception as ce:
        logger.error("Chain call failed", tool=chain_tool, error=str(ce))
        source_results.append({
            "tool": chain_tool, "params": chain_params, "error": str(ce),
        })


# ---------------------------------------------------------------------------
# Auto-fetch missing ENCFF
# ---------------------------------------------------------------------------

async def auto_fetch_missing_encff(
    pending_download_files: list[dict],
    user_message: str,
) -> str | None:
    """Fetch metadata for ENCFF accessions the user mentioned but the LLM missed.

    Modifies *pending_download_files* in place.  Returns an updated
    ``clean_markdown`` download plan, or ``None`` if nothing was fetched.
    """
    if not pending_download_files:
        return None

    requested_encffs = {
        m.upper()
        for m in re.findall(r'ENCFF[A-Z0-9]{6}', user_message, re.IGNORECASE)
    }
    fetched_encffs = {
        f.get("accession", "").upper() for f in pending_download_files
    }
    missing_encffs = requested_encffs - fetched_encffs
    if not missing_encffs:
        return None

    exp_match = re.search(r'(ENCSR[A-Z0-9]{6})', user_message, re.IGNORECASE)
    exp_acc = exp_match.group(1) if exp_match else None
    if not exp_acc:
        return None

    logger.info("Auto-fetching missing ENCFF files for download",
                missing=sorted(missing_encffs), experiment=exp_acc)
    try:
        autofetch_url = get_service_url("encode")
        autofetch_mcp = MCPHttpClient(name="encode", base_url=autofetch_url)
        await autofetch_mcp.connect()
        for miss_acc in sorted(missing_encffs):
            try:
                miss_result = await autofetch_mcp.call_tool(
                    "get_file_metadata",
                    accession=exp_acc,
                    file_accession=miss_acc,
                )
                if isinstance(miss_result, dict):
                    miss_dl_url = None
                    miss_cloud = miss_result.get("cloud_metadata")
                    if isinstance(miss_cloud, dict) and miss_cloud.get("url"):
                        miss_dl_url = miss_cloud["url"]
                    elif miss_result.get("href"):
                        miss_dl_url = f"https://www.encodeproject.org{miss_result['href']}"
                    if miss_dl_url:
                        miss_fmt = miss_result.get("file_format", "")
                        miss_fname = (
                            f"{miss_acc}.{miss_fmt}" if miss_fmt else miss_acc
                        )
                        pending_download_files.append({
                            "url": miss_dl_url,
                            "filename": miss_fname,
                            "size_bytes": miss_result.get("file_size"),
                            "accession": miss_acc,
                        })
                        logger.info("Auto-fetched missing file metadata",
                                    accession=miss_acc, url=miss_dl_url)
            except Exception as mfe:
                logger.warning("Failed to auto-fetch file metadata",
                               accession=miss_acc, error=str(mfe))
        await autofetch_mcp.disconnect()
        return _build_download_plan(pending_download_files)
    except Exception as conn_e:
        logger.warning("Failed to connect for auto-fetch", error=str(conn_e))
        return None


def _build_download_plan(pending_files: list[dict]) -> str:
    """Build a Markdown download plan listing all pending files."""
    plan_lines = ["**Download Plan:**"]
    total_mb = 0.0
    for pf in pending_files:
        sz = pf.get("size_bytes") or 0
        mb = round(sz / (1024 * 1024), 1)
        total_mb += mb
        plan_lines.append(f"- **File:** `{pf['filename']}` ({mb} MB)")
    plan_lines.append(f"- **Total:** {round(total_mb, 1)} MB")
    plan_lines.append("- **Destination:** `data/`\n")
    plan_lines.append("Proceed with download?")
    return "\n".join(plan_lines)
