"""
Tool Schema Contracts — Fetched from MCP servers and injected into the LLM prompt.

Provides:
  - fetch_all_tool_schemas(): Fetch schemas from all registered MCP servers
  - format_tool_contract(): Format schemas into a compact prompt block
  - validate_against_schema(): Pre-call parameter validation
"""
from __future__ import annotations

import httpx
from common.logging_config import get_logger
from cortex.dataframe_actions import get_local_tool_schemas

logger = get_logger(__name__)

# Module-level cache: {source_key: {tool_name: schema_dict}}
_SCHEMA_CACHE: dict[str, dict] = {}
_CACHE_LOADED = False


async def fetch_all_tool_schemas(
    service_registry: dict,
    consortium_registry: dict,
) -> dict[str, dict]:
    """
    Fetch tool schemas from all registered MCP servers.
    Caches results so subsequent calls are instant.
    Falls back gracefully if a server is unreachable.
    """
    global _SCHEMA_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return _SCHEMA_CACHE

    all_sources: dict[str, str] = {}
    for key, entry in consortium_registry.items():
        all_sources[key] = entry["url"]
    for key, entry in service_registry.items():
        all_sources[key] = entry["url"]

    for source_key, base_url in all_sources.items():
        schema_url = f"{base_url}/tools/schema"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(schema_url)
                if resp.status_code == 200:
                    _SCHEMA_CACHE[source_key] = resp.json()
                    logger.info("Loaded tool schemas",
                                source=source_key,
                                tool_count=len(_SCHEMA_CACHE[source_key]))
                else:
                    logger.warning("Schema endpoint returned non-200",
                                  source=source_key, status=resp.status_code)
        except Exception as e:
            logger.warning("Could not fetch tool schemas (server may not be running)",
                          source=source_key, error=str(e))

    # Only mark as loaded if we actually got at least one source's schemas.
    # If all servers were unreachable, leave _CACHE_LOADED=False so the
    # next call retries instead of permanently serving an empty cache.
    if _SCHEMA_CACHE:
        _CACHE_LOADED = True
    else:
        logger.warning("No tool schemas fetched from any server — will retry on next call")
    return _SCHEMA_CACHE


def invalidate_schema_cache():
    """Force re-fetch on next call (useful after server restarts)."""
    global _SCHEMA_CACHE, _CACHE_LOADED
    _SCHEMA_CACHE = {}
    _CACHE_LOADED = False


def format_tool_contract(source_key: str, source_type: str) -> str:
    """
    Format cached schemas for a specific source into a compact prompt block.

    Returns an empty string if no schemas are cached for this source.
    Example output:
        TOOL: get_files_by_type (consortium=encode)
          REQUIRED: accession (ENCSR format — experiment, NOT file)
          OPTIONAL: file_type (bam|bigwig|bed|fastq)
          NEVER: pass ENCFF file accessions here — use get_file_metadata
    """
    schemas = get_local_tool_schemas(source_key) or _SCHEMA_CACHE.get(source_key, {})
    if not schemas:
        return ""

    lines = []
    tag_prefix = f"{source_type}={source_key}"

    for tool_name, schema in schemas.items():
        desc = schema.get("description", "")
        params_obj = schema.get("parameters", {})
        properties = params_obj.get("properties", {})
        required_keys = set(params_obj.get("required", []))

        lines.append(f"TOOL: {tool_name} ({tag_prefix})")
        if desc:
            lines.append(f"  {desc}")

        required_parts = []
        optional_parts = []
        warnings = []

        for param_name, param_info in properties.items():
            p_desc = param_info.get("description", "")
            p_type = param_info.get("type", "")
            p_enum = param_info.get("enum")
            p_pattern = param_info.get("pattern", "")

            # Build compact param description
            type_hint = ""
            if p_enum:
                type_hint = "|".join(str(v) for v in p_enum)
            elif p_type:
                type_hint = p_type
            if p_pattern:
                type_hint = p_pattern

            compact = f"{param_name}"
            if type_hint:
                compact += f" ({type_hint})"

            # Extract NEVER warnings from description
            if "NEVER" in p_desc.upper() or "NOT" in p_desc.upper():
                warnings.append(f"{param_name}: {p_desc}")
                # Still include the param in the list
            elif p_desc:
                compact += f" — {p_desc}"

            if param_name in required_keys:
                required_parts.append(compact)
            else:
                optional_parts.append(compact)

        if required_parts:
            lines.append(f"  REQUIRED: {', '.join(required_parts)}")
        if optional_parts:
            lines.append(f"  OPTIONAL: {', '.join(optional_parts)}")
        for w in warnings:
            lines.append(f"  ⚠️ {w}")
        lines.append("")  # blank line between tools

    return "\n".join(lines)


def validate_against_schema(
    tool_name: str,
    params: dict,
    source_key: str,
) -> tuple[dict, list[str]]:
    """
    Validate and clean tool parameters against the cached schema.

    Returns:
        (cleaned_params, violations) where violations is a list of
        human-readable strings describing what was wrong. Empty list = valid.

    Fixes applied:
        - Strips parameters not in the schema's properties
        - Checks required fields are present
        - Validates enum values (case-insensitive match)
    """
    violations: list[str] = []
    schemas = get_local_tool_schemas(source_key) or _SCHEMA_CACHE.get(source_key, {})
    schema = schemas.get(tool_name)

    if not schema:
        # No schema available — pass through unchanged
        return params, []

    params_obj = schema.get("parameters", {})
    properties = params_obj.get("properties", {})
    required_keys = set(params_obj.get("required", []))

    # Strip unknown parameters
    known_keys = set(properties.keys())
    unknown = set(params.keys()) - known_keys
    cleaned = {k: v for k, v in params.items() if k in known_keys}
    if unknown:
        violations.append(f"Stripped unknown params: {', '.join(sorted(unknown))}")
        logger.info("Schema validation: stripped unknown params",
                    tool=tool_name, source=source_key, unknown=sorted(unknown))

    # Check required fields
    missing = required_keys - set(cleaned.keys())
    if missing:
        violations.append(f"Missing required params: {', '.join(sorted(missing))}")
        logger.warning("Schema validation: missing required params",
                      tool=tool_name, source=source_key, missing=sorted(missing))

    # Validate enum values (case-insensitive)
    for param_name, param_info in properties.items():
        if param_name not in cleaned:
            continue
        p_enum = param_info.get("enum")
        if p_enum:
            val = cleaned[param_name]
            # Case-insensitive match
            enum_lower = {str(e).lower(): e for e in p_enum}
            if isinstance(val, str) and val.lower() in enum_lower:
                cleaned[param_name] = enum_lower[val.lower()]
            elif isinstance(val, str) and val.lower() not in enum_lower:
                violations.append(
                    f"Invalid value for {param_name}: '{val}' (expected one of: {', '.join(str(e) for e in p_enum)})"
                )

    return cleaned, violations
