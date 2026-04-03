from __future__ import annotations

import ast
import json
import re
from typing import Any

from common.logging_config import get_logger
from cortex.dataframe_transforms import (
    aggregate_dataframe,
    dataframe_to_payload,
    filter_dataframe,
    join_dataframes,
    melt_dataframe,
    payload_to_dataframe,
    pivot_dataframe,
    rename_columns,
    select_columns,
    sort_dataframe,
)
from cortex.llm_validators import get_block_payload

logger = get_logger(__name__)


LOCAL_DATAFRAME_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "filter_dataframe": {
        "description": "Filter rows in an existing conversation dataframe and return a new dataframe.",
        "parameters": {
            "type": "object",
            "properties": {
                "df_id": {"type": "integer", "description": "Source dataframe id, for example 1 for DF1."},
                "column": {"type": "string", "description": "Column to filter on."},
                "operator": {"type": "string", "enum": ["==", "!=", ">", ">=", "<", "<=", "in", "contains"]},
                "value": {"description": "Comparison value. Use JSON array syntax for operator=in."},
            },
            "required": ["df_id", "column", "operator", "value"],
        },
    },
    "select_dataframe_columns": {
        "description": "Keep only selected columns from a dataframe and return a new dataframe.",
        "parameters": {
            "type": "object",
            "properties": {
                "df_id": {"type": "integer", "description": "Source dataframe id."},
                "columns": {"type": "string", "description": "Columns to keep, separated by | or as a JSON array."},
            },
            "required": ["df_id", "columns"],
        },
    },
    "rename_dataframe_columns": {
        "description": "Rename dataframe columns and return a new dataframe.",
        "parameters": {
            "type": "object",
            "properties": {
                "df_id": {"type": "integer", "description": "Source dataframe id."},
                "rename_map": {"type": "string", "description": "Column rename mapping as JSON object or old:new|old2:new2."},
            },
            "required": ["df_id", "rename_map"],
        },
    },
    "sort_dataframe": {
        "description": "Sort a dataframe by one or more columns and return a new dataframe.",
        "parameters": {
            "type": "object",
            "properties": {
                "df_id": {"type": "integer", "description": "Source dataframe id."},
                "sort_by": {"type": "string", "description": "Column name or list of columns separated by |."},
                "ascending": {"type": "string", "description": "true/false or a | separated list matching sort_by."},
            },
            "required": ["df_id", "sort_by"],
        },
    },
    "melt_dataframe": {
        "description": "Melt a wide dataframe into long format and return a new dataframe.",
        "parameters": {
            "type": "object",
            "properties": {
                "df_id": {"type": "integer", "description": "Source dataframe id."},
                "id_vars": {"type": "string", "description": "ID columns to keep, separated by | or as JSON array."},
                "value_vars": {"type": "string", "description": "Optional columns to melt. Omit to melt all non-id columns."},
                "var_name": {"type": "string", "description": "Output column name for former column headers."},
                "value_name": {"type": "string", "description": "Output column name for former cell values."},
            },
            "required": ["df_id", "id_vars", "var_name", "value_name"],
        },
    },
    "aggregate_dataframe": {
        "description": "Group a dataframe and aggregate one or more columns. Returns a new dataframe.",
        "parameters": {
            "type": "object",
            "properties": {
                "df_id": {"type": "integer", "description": "Source dataframe id."},
                "group_by": {"type": "string", "description": "Group-by columns separated by | or as JSON array."},
                "aggregations": {"type": "string", "description": "Aggregation mapping as JSON object or col:func|col2:func2."},
            },
            "required": ["df_id", "group_by", "aggregations"],
        },
    },
    "join_dataframes": {
        "description": "Join two existing conversation dataframes and return a new dataframe.",
        "parameters": {
            "type": "object",
            "properties": {
                "left_df_id": {"type": "integer", "description": "Left dataframe id."},
                "right_df_id": {"type": "integer", "description": "Right dataframe id."},
                "on": {"type": "string", "description": "Shared join columns separated by | or as JSON array."},
                "left_on": {"type": "string", "description": "Optional left-only join columns."},
                "right_on": {"type": "string", "description": "Optional right-only join columns."},
                "how": {"type": "string", "enum": ["inner", "left", "right", "outer"]},
                "suffixes": {"type": "string", "description": "Optional pair of suffixes as JSON array or left|right."},
            },
            "required": ["left_df_id", "right_df_id"],
        },
    },
    "pivot_dataframe": {
        "description": "Pivot a dataframe wider using index, columns, and values parameters.",
        "parameters": {
            "type": "object",
            "properties": {
                "df_id": {"type": "integer", "description": "Source dataframe id."},
                "index": {"type": "string", "description": "Index columns separated by | or as JSON array."},
                "columns": {"type": "string", "description": "Column whose values become new columns."},
                "values": {"type": "string", "description": "Column providing output values."},
                "aggfunc": {"type": "string", "enum": ["first", "sum", "mean", "count", "max", "min"]},
            },
            "required": ["df_id", "index", "columns", "values"],
        },
    },
}

LOCAL_DATAFRAME_TOOL_NAMES = frozenset(LOCAL_DATAFRAME_TOOL_SCHEMAS)

_BOOLEAN_TRUE = {"1", "true", "t", "yes", "y", "descending", "desc"}
_AFFIRM_RE = re.compile(r"^\s*(?:yes|y|ok|okay|sure|please do|do it|go ahead|proceed|continue)\s*[.!]*\s*$", re.IGNORECASE)


def get_local_tool_schemas(source_key: str) -> dict[str, dict[str, Any]]:
    return LOCAL_DATAFRAME_TOOL_SCHEMAS if source_key == "cortex" else {}


def is_local_dataframe_tool(tool_name: str) -> bool:
    return tool_name in LOCAL_DATAFRAME_TOOL_NAMES


def is_short_affirmation(message: str) -> bool:
    return bool(_AFFIRM_RE.match((message or "").strip()))


def summarize_dataframe_action(tool_name: str, params: dict[str, Any]) -> str:
    if tool_name == "melt_dataframe":
        return f"Melt DF{params.get('df_id')} into long format"
    if tool_name == "filter_dataframe":
        return f"Filter DF{params.get('df_id')} on {params.get('column')} {params.get('operator')} {params.get('value')}"
    if tool_name == "select_dataframe_columns":
        return f"Select columns from DF{params.get('df_id')}"
    if tool_name == "rename_dataframe_columns":
        return f"Rename columns in DF{params.get('df_id')}"
    if tool_name == "sort_dataframe":
        return f"Sort DF{params.get('df_id')} by {params.get('sort_by')}"
    if tool_name == "aggregate_dataframe":
        return f"Aggregate DF{params.get('df_id')} by {params.get('group_by')}"
    if tool_name == "join_dataframes":
        return f"Join DF{params.get('left_df_id')} with DF{params.get('right_df_id')}"
    if tool_name == "pivot_dataframe":
        return f"Pivot DF{params.get('df_id')}"
    return "Execute dataframe action"


def parse_pending_action_tag(response: str) -> list[dict[str, Any]]:
    pattern = re.compile(r"\[\[PENDING_ACTION:\s*(.*?)\]\]", re.DOTALL)
    pending: list[dict[str, Any]] = []
    for match in pattern.finditer(response or ""):
        params = _parse_pending_action_params(match.group(1))
        tool = str(params.get("tool") or "").strip()
        if not is_local_dataframe_tool(tool):
            continue
        normalized = _normalize_action_params(tool, params)
        summary = str(params.get("summary") or summarize_dataframe_action(tool, normalized)).strip()
        pending.append({
            "source_type": "service",
            "source_key": "cortex",
            "tool": tool,
            "params": normalized,
            "summary": summary,
        })
    return pending


def strip_pending_action_tags(text: str) -> str:
    return re.sub(r"\[\[PENDING_ACTION:\s*.*?\]\]", "", text or "", flags=re.DOTALL).strip()


def execute_local_dataframe_call(
    tool_name: str,
    params: dict[str, Any],
    *,
    history_blocks: list,
) -> dict[str, Any]:
    normalized = _normalize_action_params(tool_name, params)
    if tool_name == "join_dataframes":
        left_payload = _find_dataframe_payload(normalized["left_df_id"], history_blocks)
        right_payload = _find_dataframe_payload(normalized["right_df_id"], history_blocks)
        left = payload_to_dataframe(left_payload)
        right = payload_to_dataframe(right_payload)
        normalized["on"] = _resolve_column_names(left, normalized.get("on")) if normalized.get("on") else None
        normalized["left_on"] = _resolve_column_names(left, normalized.get("left_on")) if normalized.get("left_on") else None
        normalized["right_on"] = _resolve_column_names(right, normalized.get("right_on")) if normalized.get("right_on") else None
        result = join_dataframes(
            left,
            right,
            on=normalized.get("on"),
            left_on=normalized.get("left_on"),
            right_on=normalized.get("right_on"),
            how=normalized.get("how", "inner"),
            suffixes=tuple(normalized.get("suffixes") or ("_x", "_y")),
        )
        label = f"join_DF{normalized['left_df_id']}_DF{normalized['right_df_id']}"
        return dataframe_to_payload(
            result,
            source_payload=left_payload,
            operation=tool_name,
            label=label,
            extra_metadata={
                "source_df_ids": [normalized["left_df_id"], normalized["right_df_id"]],
            },
        )

    source_payload = _find_dataframe_payload(normalized["df_id"], history_blocks)
    frame = payload_to_dataframe(source_payload)

    if tool_name == "filter_dataframe":
        normalized["column"] = _resolve_column_name(frame, normalized["column"])
    elif tool_name == "select_dataframe_columns":
        normalized["columns"] = _resolve_column_names(frame, normalized["columns"])
    elif tool_name == "rename_dataframe_columns":
        normalized["rename_map"] = {
            _resolve_column_name(frame, key): value
            for key, value in normalized["rename_map"].items()
        }
    elif tool_name == "sort_dataframe":
        normalized["sort_by"] = _resolve_column_names(frame, normalized["sort_by"])
    elif tool_name == "melt_dataframe":
        normalized["id_vars"] = _resolve_column_names(frame, normalized["id_vars"])
        if normalized.get("value_vars"):
            normalized["value_vars"] = _resolve_column_names(frame, normalized["value_vars"])
    elif tool_name == "aggregate_dataframe":
        normalized["group_by"] = _resolve_column_names(frame, normalized["group_by"])
        normalized["aggregations"] = {
            _resolve_column_name(frame, key): value
            for key, value in normalized["aggregations"].items()
        }
    elif tool_name == "pivot_dataframe":
        normalized["index"] = _resolve_column_names(frame, normalized["index"])
        normalized["columns"] = _resolve_column_name(frame, normalized["columns"])
        normalized["values"] = _resolve_column_name(frame, normalized["values"])

    if tool_name == "filter_dataframe":
        result = filter_dataframe(
            frame,
            column=normalized["column"],
            operator=normalized["operator"],
            value=normalized["value"],
        )
    elif tool_name == "select_dataframe_columns":
        result = select_columns(frame, columns=normalized["columns"])
    elif tool_name == "rename_dataframe_columns":
        result = rename_columns(frame, rename_map=normalized["rename_map"])
    elif tool_name == "sort_dataframe":
        result = sort_dataframe(
            frame,
            sort_by=normalized["sort_by"],
            ascending=normalized["ascending"],
        )
    elif tool_name == "melt_dataframe":
        result = melt_dataframe(
            frame,
            id_vars=normalized["id_vars"],
            value_vars=normalized.get("value_vars"),
            var_name=normalized["var_name"],
            value_name=normalized["value_name"],
        )
    elif tool_name == "aggregate_dataframe":
        result = aggregate_dataframe(
            frame,
            group_by=normalized["group_by"],
            aggregations=normalized["aggregations"],
        )
    elif tool_name == "pivot_dataframe":
        result = pivot_dataframe(
            frame,
            index=normalized["index"],
            columns=normalized["columns"],
            values=normalized["values"],
            aggfunc=normalized.get("aggfunc", "first"),
        )
    else:
        raise ValueError(f"Unsupported local dataframe tool: {tool_name}")

    label = f"{tool_name}_DF{normalized['df_id']}"
    return dataframe_to_payload(
        result,
        source_payload=source_payload,
        operation=tool_name,
        label=label,
        extra_metadata={"source_df_id": normalized["df_id"]},
    )


def build_auto_dataframe_call(user_message: str) -> dict[str, Any] | None:
    message = user_message or ""
    df_refs = re.findall(r"\bDF\s*(\d+)\b", message, re.IGNORECASE)
    if not df_refs:
        return None
    df_ids = [int(value) for value in df_refs]
    lower = message.lower()

    melt_match = re.search(
        r"\b(?:melt|unpivot|reshape)\s+df\s*(\d+)\b.*?\b(?:long|long\s+format)\b(?:.*?columns?\s+([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+))?",
        message,
        re.IGNORECASE,
    )
    if melt_match:
        df_id = int(melt_match.group(1))
        source_columns = _list_source_columns(df_id, message)
        id_vars = [source_columns[0]] if source_columns else ["sample"]
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "melt_dataframe",
            "params": {
                "df_id": df_id,
                "id_vars": id_vars,
                "var_name": melt_match.group(3) or "variable",
                "value_name": melt_match.group(4) or "value",
            },
        }

    filter_match = re.search(
        r"\b(?:filter|subset)\s+df\s*(\d+)\b.*?\bwhere\s+([A-Za-z0-9_]+)\s*(==|=|!=|>=|<=|>|<)\s*([^,.;]+)",
        message,
        re.IGNORECASE,
    )
    if filter_match:
        operator = filter_match.group(3)
        if operator == "=":
            operator = "=="
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "filter_dataframe",
            "params": {
                "df_id": int(filter_match.group(1)),
                "column": filter_match.group(2),
                "operator": operator,
                "value": _coerce_scalar(filter_match.group(4).strip()),
            },
        }

    subset_filter_match = re.search(
        r"\bsubset\s+df\s*(\d+)\b.*?\bto\s+rows\s+where\s+([A-Za-z0-9_]+)\s*(==|=|!=|>=|<=|>|<)\s*([^,.;]+)",
        message,
        re.IGNORECASE,
    )
    if subset_filter_match:
        operator = subset_filter_match.group(3)
        if operator == "=":
            operator = "=="
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "filter_dataframe",
            "params": {
                "df_id": int(subset_filter_match.group(1)),
                "column": subset_filter_match.group(2),
                "operator": operator,
                "value": _coerce_scalar(subset_filter_match.group(4).strip()),
            },
        }

    select_match = re.search(
        r"\b(?:select|keep)\s+columns?\s+(.+?)\s+from\s+df\s*(\d+)\b",
        message,
        re.IGNORECASE,
    )
    if select_match:
        columns = _parse_column_phrase(select_match.group(1))
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "select_dataframe_columns",
            "params": {"df_id": int(select_match.group(2)), "columns": columns},
        }

    subset_columns_match = re.search(
        r"\bsubset\s+df\s*(\d+)\b\s+to\s+(?:columns?\s+)?(.+?)(?:$|\s+for\s+|\s+and\s+then\s+)",
        message,
        re.IGNORECASE,
    )
    if subset_columns_match and "where" not in subset_columns_match.group(2).lower():
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "select_dataframe_columns",
            "params": {
                "df_id": int(subset_columns_match.group(1)),
                "columns": _parse_column_phrase(subset_columns_match.group(2)),
            },
        }

    keep_in_match = re.search(
        r"\bkeep\s+only\s+(.+?)\s+in\s+df\s*(\d+)\b",
        message,
        re.IGNORECASE,
    )
    if keep_in_match:
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "select_dataframe_columns",
            "params": {
                "df_id": int(keep_in_match.group(2)),
                "columns": _parse_column_phrase(keep_in_match.group(1)),
            },
        }

    rename_match = re.search(
        r"\brename\s+column\s+([A-Za-z0-9_]+)\s+to\s+([A-Za-z0-9_]+)\s+in\s+df\s*(\d+)\b",
        message,
        re.IGNORECASE,
    )
    if rename_match:
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "rename_dataframe_columns",
            "params": {
                "df_id": int(rename_match.group(3)),
                "rename_map": {rename_match.group(1): rename_match.group(2)},
            },
        }

    rename_columns_match = re.search(
        r"\brename\s+columns?\s+(.+?)\s+in\s+df\s*(\d+)\b",
        message,
        re.IGNORECASE,
    )
    if rename_columns_match:
        rename_map = _parse_rename_pairs(rename_columns_match.group(1))
        if rename_map:
            return {
                "source_type": "service",
                "source_key": "cortex",
                "tool": "rename_dataframe_columns",
                "params": {
                    "df_id": int(rename_columns_match.group(2)),
                    "rename_map": rename_map,
                },
            }

    rename_df_match = re.search(
        r"\brename\s+df\s*(\d+)\s+columns?\s+(.+)$",
        message,
        re.IGNORECASE,
    )
    if rename_df_match:
        rename_map = _parse_rename_pairs(rename_df_match.group(2))
        if rename_map:
            return {
                "source_type": "service",
                "source_key": "cortex",
                "tool": "rename_dataframe_columns",
                "params": {
                    "df_id": int(rename_df_match.group(1)),
                    "rename_map": rename_map,
                },
            }

    sort_match = re.search(
        r"\bsort\s+df\s*(\d+)\b.*?\bby\s+([A-Za-z0-9_]+)(?:\s+(ascending|descending|asc|desc))?",
        message,
        re.IGNORECASE,
    )
    if sort_match:
        desc = (sort_match.group(3) or "").lower() in {"descending", "desc"}
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "sort_dataframe",
            "params": {
                "df_id": int(sort_match.group(1)),
                "sort_by": [sort_match.group(2)],
                "ascending": [not desc],
            },
        }

    aggregate_match = re.search(
        r"\b(?:group|aggregate)\s+df\s*(\d+)\b.*?\bby\s+([A-Za-z0-9_]+)\b.*?\b(sum|mean|count|max|min)\s+(?:of\s+)?([A-Za-z0-9_]+)",
        message,
        re.IGNORECASE,
    )
    if aggregate_match:
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "aggregate_dataframe",
            "params": {
                "df_id": int(aggregate_match.group(1)),
                "group_by": [aggregate_match.group(2)],
                "aggregations": {aggregate_match.group(4): aggregate_match.group(3).lower()},
            },
        }

    summarize_match = re.search(
        r"\b(?:summarize|summary|group\s+by|group)\s+df\s*(\d+)\b.*?\bby\s+([A-Za-z0-9_,| ]+)\b.*?\b(sum|mean|count|max|min)\b(?:\s+(?:of\s+)?)?([A-Za-z0-9_]+|rows?)",
        message,
        re.IGNORECASE,
    )
    if summarize_match:
        agg_target = summarize_match.group(4)
        if agg_target.lower().startswith("row"):
            agg_target = summarize_match.group(2).split(",")[0].split("|")[0].strip()
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "aggregate_dataframe",
            "params": {
                "df_id": int(summarize_match.group(1)),
                "group_by": _parse_column_phrase(summarize_match.group(2)),
                "aggregations": {agg_target: summarize_match.group(3).lower()},
            },
        }

    if re.search(r"\bjoin\b", lower) and len(df_ids) >= 2:
        join_match = re.search(r"\bon\s+([A-Za-z0-9_]+)", message, re.IGNORECASE)
        how_match = re.search(r"\b(inner|left|right|outer)\s+join\b", lower)
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "join_dataframes",
            "params": {
                "left_df_id": df_ids[0],
                "right_df_id": df_ids[1],
                "on": [join_match.group(1)] if join_match else None,
                "how": how_match.group(1) if how_match else "inner",
            },
        }

    pivot_match = re.search(
        r"\bpivot\s+df\s*(\d+)\b.*?\bindex\s+([A-Za-z0-9_|, ]+)\b.*?\bcolumns\s+([A-Za-z0-9_]+)\b.*?\bvalues\s+([A-Za-z0-9_]+)",
        message,
        re.IGNORECASE,
    )
    if pivot_match:
        return {
            "source_type": "service",
            "source_key": "cortex",
            "tool": "pivot_dataframe",
            "params": {
                "df_id": int(pivot_match.group(1)),
                "index": _split_values(pivot_match.group(2)),
                "columns": pivot_match.group(3),
                "values": pivot_match.group(4),
                "aggfunc": "first",
            },
        }

    return None


def _find_dataframe_payload(df_id: int, history_blocks: list) -> dict[str, Any]:
    for block in reversed(history_blocks or []):
        if getattr(block, "type", None) != "AGENT_PLAN":
            continue
        dataframes = get_block_payload(block).get("_dataframes", {})
        for payload in dataframes.values():
            metadata = payload.get("metadata") or {}
            if metadata.get("df_id") == df_id:
                return payload
    raise ValueError(f"DF{df_id} was not found in the conversation history")


def _resolve_column_name(frame, requested: str) -> str:
    if requested in frame.columns:
        return requested
    lowered = str(requested).strip().lower()
    for column in frame.columns:
        if str(column).strip().lower() == lowered:
            return str(column)
    raise ValueError(f"Column '{requested}' was not found in the dataframe")


def _resolve_column_names(frame, requested_columns: list[str]) -> list[str]:
    return [_resolve_column_name(frame, column) for column in requested_columns]


def _normalize_action_params(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    if "df" in normalized and "df_id" not in normalized:
        df_match = re.match(r"(?:DF)?\s*(\d+)", str(normalized["df"]), re.IGNORECASE)
        if df_match:
            normalized["df_id"] = int(df_match.group(1))

    if tool_name == "filter_dataframe":
        normalized["df_id"] = int(normalized["df_id"])
        normalized["value"] = _coerce_value(normalized.get("value"))
        return normalized

    if tool_name == "select_dataframe_columns":
        normalized["df_id"] = int(normalized["df_id"])
        normalized["columns"] = _split_values(normalized.get("columns"))
        return normalized

    if tool_name == "rename_dataframe_columns":
        normalized["df_id"] = int(normalized["df_id"])
        normalized["rename_map"] = _parse_mapping(normalized.get("rename_map"))
        return normalized

    if tool_name == "sort_dataframe":
        normalized["df_id"] = int(normalized["df_id"])
        sort_by = _split_values(normalized.get("sort_by"))
        normalized["sort_by"] = sort_by
        ascending_raw = normalized.get("ascending", True)
        if isinstance(ascending_raw, list):
            ascending = [bool(item) for item in ascending_raw]
        else:
            parts = _split_values(ascending_raw)
            if parts:
                ascending = [str(part).strip().lower() in _BOOLEAN_TRUE for part in parts]
            else:
                ascending = [True]
        if len(ascending) == 1 and len(sort_by) > 1:
            ascending = ascending * len(sort_by)
        normalized["ascending"] = ascending
        return normalized

    if tool_name == "melt_dataframe":
        normalized["df_id"] = int(normalized["df_id"])
        normalized["id_vars"] = _split_values(normalized.get("id_vars"))
        value_vars = _split_values(normalized.get("value_vars"))
        normalized["value_vars"] = value_vars or None
        normalized["var_name"] = str(normalized.get("var_name") or "variable")
        normalized["value_name"] = str(normalized.get("value_name") or "value")
        return normalized

    if tool_name == "aggregate_dataframe":
        normalized["df_id"] = int(normalized["df_id"])
        normalized["group_by"] = _split_values(normalized.get("group_by"))
        normalized["aggregations"] = _parse_mapping(normalized.get("aggregations"))
        return normalized

    if tool_name == "join_dataframes":
        normalized["left_df_id"] = int(normalized["left_df_id"])
        normalized["right_df_id"] = int(normalized["right_df_id"])
        normalized["on"] = _split_values(normalized.get("on")) or None
        normalized["left_on"] = _split_values(normalized.get("left_on")) or None
        normalized["right_on"] = _split_values(normalized.get("right_on")) or None
        normalized["how"] = str(normalized.get("how") or "inner").lower()
        suffixes = _split_values(normalized.get("suffixes"))
        if len(suffixes) >= 2:
            normalized["suffixes"] = (suffixes[0], suffixes[1])
        else:
            normalized["suffixes"] = ("_x", "_y")
        return normalized

    if tool_name == "pivot_dataframe":
        normalized["df_id"] = int(normalized["df_id"])
        normalized["index"] = _split_values(normalized.get("index"))
        normalized["columns"] = str(normalized["columns"])
        normalized["values"] = str(normalized["values"])
        normalized["aggfunc"] = str(normalized.get("aggfunc") or "first").lower()
        return normalized

    return normalized


def _split_values(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, tuple):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        parsed = _maybe_literal(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in re.split(r"\s*\|\s*|\s*,\s*", raw) if part.strip()]
    return [str(raw).strip()]


def _parse_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    if raw is None:
        return {}
    if isinstance(raw, str):
        parsed = _maybe_literal(raw)
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
        mapping: dict[str, Any] = {}
        for chunk in _split_values(raw):
            if ":" not in chunk:
                continue
            key, value = chunk.split(":", 1)
            mapping[key.strip()] = _coerce_scalar(value.strip())
        return mapping
    raise ValueError("Expected mapping parameter")


def _maybe_literal(raw: str):
    text = raw.strip()
    if not text:
        return text
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return ast.literal_eval(text)
    except Exception:
        return text


def _coerce_value(raw: Any):
    if isinstance(raw, (list, dict, int, float, bool)) or raw is None:
        return raw
    if isinstance(raw, str):
        parsed = _maybe_literal(raw)
        if parsed is not raw:
            return parsed
        return _coerce_scalar(raw)
    return raw


def _coerce_scalar(raw: str):
    text = str(raw).strip().strip('"').strip("'")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text


def _parse_pending_action_params(raw_inner: str) -> dict[str, Any]:
    from cortex.llm_validators import _parse_tag_params
    return _parse_tag_params(raw_inner)


def _parse_column_phrase(raw: str) -> list[str]:
    cleaned = re.sub(r"\band\b", ",", raw, flags=re.IGNORECASE)
    return [part.strip().strip('"').strip("'") for part in cleaned.split(",") if part.strip()]


def _parse_rename_pairs(raw: str) -> dict[str, str]:
    cleaned = re.sub(r"\band\b", ",", raw, flags=re.IGNORECASE)
    mapping: dict[str, str] = {}
    for chunk in [part.strip() for part in cleaned.split(",") if part.strip()]:
        match = re.match(r"([A-Za-z0-9_]+)\s+to\s+([A-Za-z0-9_]+)", chunk, re.IGNORECASE)
        if match:
            mapping[match.group(1)] = match.group(2)
    return mapping


def _list_source_columns(df_id: int, message: str) -> list[str]:
    match = re.search(r"columns?\s+([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)", message, re.IGNORECASE)
    if not match:
        return []
    return [match.group(1), match.group(2), match.group(3)]