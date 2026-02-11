"""
Result Formatter for MCP Tool Responses

Formats tool call results into user-facing markdown, driven by registry
configuration (table columns, count fields, etc.) rather than hardcoded
field names.
"""

import json
from collections import Counter
from typing import Any

from server2.config import CONSORTIUM_REGISTRY, SERVICE_REGISTRY


def format_results(source_key: str, results: list[dict]) -> str:
    """
    Format a list of tool call results into markdown for display.

    Each result dict has: {"tool": str, "params": dict, "data": Any}
    or {"tool": str, "params": dict, "error": str}

    Args:
        source_key: Key from CONSORTIUM_REGISTRY or SERVICE_REGISTRY
        results: List of tool result dicts

    Returns:
        Markdown string ready to append to the chat response
    """
    if not results:
        return ""

    # Look up registry entry for formatting config
    entry = _get_registry_entry(source_key)
    emoji = entry.get("emoji", "📡")
    display_name = entry.get("display_name", source_key.upper())
    table_columns = entry.get("table_columns", [])
    count_field = entry.get("count_field")
    count_label = entry.get("count_label", "type")

    markdown = f"\n\n---\n\n### {emoji} {display_name}\n\n"

    for result in results:
        tool_name = result.get("tool", "unknown")
        params = result.get("params", {})

        # Header for this tool call
        markdown += f"**{tool_name.replace('_', ' ').title()}**"
        if params:
            markdown += f" ({', '.join(f'{k}={v}' for k, v in params.items())})"
        markdown += "\n\n"

        if "error" in result:
            markdown += f"❌ Error: {result['error']}\n\n"
        elif "data" in result:
            data = result["data"]
            markdown += _format_data(data, table_columns, count_field, count_label)
        markdown += "\n"

    return markdown


def _format_data(
    data: Any,
    table_columns: list[tuple[str, str]],
    count_field: str | None,
    count_label: str,
) -> str:
    """Format a single data result based on its type and column config."""

    if isinstance(data, list):
        result = f"Found {len(data)} result(s):\n\n"

        if data and isinstance(data[0], dict) and table_columns:
            # Render as a table using configured columns
            headers = [col[0] for col in table_columns]
            result += "| " + " | ".join(headers) + " |\n"
            result += "|" + "|".join(["---"] * len(headers)) + "|\n"

            for item in data:
                row_values = []
                for _, field_key in table_columns:
                    value = item.get(field_key, "")
                    # Handle list fields (e.g., targets)
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    row_values.append(str(value))
                result += "| " + " | ".join(row_values) + " |\n"

            # Auto-generate summary by count_field
            if count_field:
                counts = Counter()
                for item in data:
                    val = item.get(count_field, "Unknown")
                    if val:
                        counts[val] += 1

                if counts:
                    result += f"\n**📊 Summary: {len(counts)} unique {count_label}(s)**\n\n"
                    result += f"| {count_label.title()} | Count |\n"
                    result += "|---|---|\n"
                    for val, count in counts.most_common():
                        result += f"| {val} | {count} |\n"
                    result += f"\n**Total: {len(data)} results**\n"
        elif data:
            # No table columns configured or items are not dicts — render as list
            for item in data:
                result += f"- {str(item)[:200]}\n"

        return result

    elif isinstance(data, dict):
        # Show formatted dict as JSON
        json_str = json.dumps(data, indent=2)
        return f"```json\n{json_str}\n```\n"

    else:
        return f"{str(data)}\n"


def _get_registry_entry(key: str) -> dict:
    """Look up a registry entry from either registry."""
    if key in CONSORTIUM_REGISTRY:
        return CONSORTIUM_REGISTRY[key]
    if key in SERVICE_REGISTRY:
        return SERVICE_REGISTRY[key]
    # Return sensible defaults for unknown sources
    return {
        "display_name": key.upper(),
        "emoji": "📡",
        "table_columns": [],
        "count_field": None,
        "count_label": "type",
    }
