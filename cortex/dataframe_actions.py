from __future__ import annotations

import ast
import json
import re
from typing import Any

import pandas as pd

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
    "build_overlap_dataframe": {
        "description": "Build a reusable overlap-membership dataframe for venn/upset plots from either multiple dataframes or one dataframe split by sample.",
        "parameters": {
            "type": "object",
            "properties": {
                "dfs": {"type": "string", "description": "Optional source dataframe ids separated by |, for example DF1|DF2|DF3."},
                "sources": {"type": "string", "description": "Optional per-source mapping such as DF1:gene_id|DF2:ensembl_id."},
                "match_cols": {"type": "string", "description": "Optional match columns for multi-dataframe mode, separated by | and aligned to dfs."},
                "match_on": {"type": "string", "description": "Shared match column for all sources or the match column for single-dataframe split mode."},
                "df_id": {"type": "integer", "description": "Single dataframe id for sample-splitting mode."},
                "sample_col": {"type": "string", "description": "Required for single-dataframe split mode. Column containing sample/set identifiers."},
                "sample_values": {"type": "string", "description": "Optional sample/set values separated by |. Omit to include all values from sample_col."},
                "labels": {"type": "string", "description": "Optional display labels separated by |."},
                "plot_type": {"type": "string", "enum": ["venn", "upset"], "description": "Optional overlap plot type for set-count validation."},
                "output_label": {"type": "string", "description": "Optional label for the synthetic overlap dataframe."},
            },
            "required": [],
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
    if tool_name == "build_overlap_dataframe":
        if params.get("df_ids"):
            refs = ", ".join(f"DF{df_id}" for df_id in params.get("df_ids", []))
            return f"Build overlap dataframe from {refs}"
        if params.get("df_id"):
            return f"Build overlap dataframe from DF{params.get('df_id')} split by {params.get('sample_col')}"
        return "Build overlap dataframe"
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
    current_dataframes: dict[str, dict] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_action_params(tool_name, params)
    if tool_name == "build_overlap_dataframe":
        return build_overlap_dataframe_payload(
            normalized,
            history_blocks=history_blocks,
            current_dataframes=current_dataframes,
        )

    if tool_name == "join_dataframes":
        left_payload = _find_dataframe_payload(
            normalized["left_df_id"],
            history_blocks,
            current_dataframes=current_dataframes,
        )
        right_payload = _find_dataframe_payload(
            normalized["right_df_id"],
            history_blocks,
            current_dataframes=current_dataframes,
        )
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

    source_payload = _find_dataframe_payload(
        normalized["df_id"],
        history_blocks,
        current_dataframes=current_dataframes,
    )
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


def build_overlap_dataframe_payload(
    params: dict[str, Any],
    *,
    history_blocks: list,
    current_dataframes: dict[str, dict] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_action_params("build_overlap_dataframe", params)
    plot_type = str(normalized.get("plot_type") or "").strip().lower()

    if normalized.get("df_ids"):
        payload = _build_multi_dataframe_overlap_payload(
            normalized,
            history_blocks=history_blocks,
            current_dataframes=current_dataframes,
        )
    elif normalized.get("df_id") is not None:
        payload = _build_single_dataframe_overlap_payload(
            normalized,
            history_blocks=history_blocks,
            current_dataframes=current_dataframes,
        )
    else:
        raise ValueError(
            "Overlap dataframe build requires either dfs/sources for multi-dataframe mode "
            "or df_id + sample_col + match_on for single-dataframe split mode."
        )

    set_columns = list((payload.get("metadata") or {}).get("set_columns") or [])
    if plot_type == "venn" and len(set_columns) not in {2, 3}:
        raise ValueError("Venn plots require exactly 2 or 3 sets after overlap dataframe construction.")
    if plot_type == "upset" and len(set_columns) < 2:
        raise ValueError("UpSet plots require at least 2 sets after overlap dataframe construction.")
    if len(set_columns) < 2:
        raise ValueError("Overlap dataframe construction requires at least 2 sets.")

    return payload


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


def _find_dataframe_payload(
    df_id: int,
    history_blocks: list,
    current_dataframes: dict[str, dict] | None = None,
) -> dict[str, Any]:
    for payload in (current_dataframes or {}).values():
        metadata = payload.get("metadata") or {}
        if metadata.get("df_id") == df_id:
            return payload
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

    if tool_name == "build_overlap_dataframe":
        normalized["df_ids"] = _normalize_overlap_df_ids(normalized)
        if normalized.get("sources") and not normalized.get("match_cols"):
            _, source_match_cols = _parse_overlap_sources(normalized.get("sources"))
            normalized["match_cols"] = source_match_cols
        else:
            normalized["match_cols"] = _split_values(normalized.get("match_cols"))
        if normalized.get("match_on") is not None:
            normalized["match_on"] = str(normalized.get("match_on") or "").strip()
        if normalized.get("sample_col") is not None:
            normalized["sample_col"] = str(normalized.get("sample_col") or "").strip()
        normalized["sample_values"] = _split_values(normalized.get("sample_values"))
        normalized["labels"] = _split_values(normalized.get("labels"))
        if normalized.get("output_label") is not None:
            normalized["output_label"] = str(normalized.get("output_label") or "").strip()
        if normalized.get("plot_type") is not None:
            normalized["plot_type"] = str(normalized.get("plot_type") or "").strip().lower()
        if normalized.get("df_id") is not None:
            normalized["df_id"] = int(normalized["df_id"])
        return normalized

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


def _normalize_overlap_df_ids(params: dict[str, Any]) -> list[int]:
    if params.get("df_ids"):
        return [_normalize_df_ref(value) for value in params.get("df_ids")]
    if params.get("sources"):
        df_ids, _ = _parse_overlap_sources(params.get("sources"))
        return df_ids
    return [_normalize_df_ref(value) for value in _split_values(params.get("dfs"))]


def _parse_overlap_sources(raw: Any) -> tuple[list[int], list[str]]:
    df_ids: list[int] = []
    match_cols: list[str] = []
    for chunk in _split_values(raw):
        if ":" not in chunk:
            raise ValueError("Each overlap source must look like DF1:column_name.")
        df_token, match_col = chunk.split(":", 1)
        df_ids.append(_normalize_df_ref(df_token))
        match_col = str(match_col).strip()
        if not match_col:
            raise ValueError("Each overlap source must include a non-empty match column.")
        match_cols.append(match_col)
    return df_ids, match_cols


def _normalize_df_ref(raw: Any) -> int:
    match = re.match(r"(?:DF)?\s*(\d+)", str(raw).strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid dataframe reference: {raw}")
    return int(match.group(1))


def _build_multi_dataframe_overlap_payload(
    params: dict[str, Any],
    *,
    history_blocks: list,
    current_dataframes: dict[str, dict] | None,
) -> dict[str, Any]:
    df_ids = list(params.get("df_ids") or [])
    if len(df_ids) < 2:
        raise ValueError("Multi-dataframe overlap requires at least 2 dataframe ids.")

    match_cols = list(params.get("match_cols") or [])
    shared_match = str(params.get("match_on") or "").strip()
    if not match_cols and shared_match:
        match_cols = [shared_match] * len(df_ids)
    if len(match_cols) == 1 and len(df_ids) > 1:
        match_cols = match_cols * len(df_ids)
    if len(match_cols) != len(df_ids):
        raise ValueError("Multi-dataframe overlap requires one match column per dataframe.")

    explicit_labels = list(params.get("labels") or [])
    if explicit_labels and len(explicit_labels) != len(df_ids):
        raise ValueError("If labels are provided, their count must match the number of source dataframes.")

    set_values_by_label: dict[str, list[str]] = {}
    source_payloads: list[dict[str, Any]] = []
    source_labels: list[str] = []
    resolved_match_cols: list[str] = []
    for idx, df_id in enumerate(df_ids):
        payload = _find_dataframe_payload(df_id, history_blocks, current_dataframes=current_dataframes)
        source_payloads.append(payload)
        frame = payload_to_dataframe(payload)
        resolved_match_col = _resolve_column_name(frame, match_cols[idx])
        resolved_match_cols.append(resolved_match_col)
        fallback_label = str((payload.get("metadata") or {}).get("label") or f"DF{df_id}")
        source_label = explicit_labels[idx] if explicit_labels else fallback_label
        source_labels.append(source_label)
        set_values_by_label[source_label] = _extract_overlap_values(frame, resolved_match_col)

    unique_labels = _deduplicate_labels(source_labels)
    remapped_values = {
        unique_labels[idx]: set_values_by_label[source_labels[idx]]
        for idx in range(len(unique_labels))
    }
    return _overlap_payload_from_sets(
        remapped_values,
        source_payload=source_payloads[0],
        label=params.get("output_label") or _default_overlap_label(unique_labels),
        extra_metadata={
            "source_df_ids": df_ids,
            "source_labels": unique_labels,
            "source_match_cols": resolved_match_cols,
            "match_key_column": "match_key",
        },
    )


def _build_single_dataframe_overlap_payload(
    params: dict[str, Any],
    *,
    history_blocks: list,
    current_dataframes: dict[str, dict] | None,
) -> dict[str, Any]:
    if params.get("df_id") is None:
        raise ValueError("Single-dataframe overlap requires df_id.")
    sample_col = str(params.get("sample_col") or "").strip()
    if not sample_col:
        raise ValueError("sample_col is required when building overlap data from one dataframe.")
    match_on = str(params.get("match_on") or "").strip()
    if not match_on:
        raise ValueError("match_on is required when building overlap data from one dataframe.")

    payload = _find_dataframe_payload(params["df_id"], history_blocks, current_dataframes=current_dataframes)
    frame = payload_to_dataframe(payload)
    resolved_sample_col = _resolve_column_name(frame, sample_col)
    resolved_match_on = _resolve_column_name(frame, match_on)

    explicit_sample_values = list(params.get("sample_values") or [])
    if explicit_sample_values:
        actual_sample_values = [
            _resolve_sample_value(frame[resolved_sample_col], value)
            for value in explicit_sample_values
        ]
    else:
        actual_sample_values = _ordered_sample_values(frame[resolved_sample_col])
    if len(actual_sample_values) < 2:
        raise ValueError("Overlap dataframe construction requires at least 2 sample values.")

    explicit_labels = list(params.get("labels") or [])
    if explicit_labels and len(explicit_labels) != len(actual_sample_values):
        raise ValueError("If labels are provided, their count must match the number of sample values.")

    source_labels = explicit_labels or [str(value) for value in actual_sample_values]
    unique_labels = _deduplicate_labels(source_labels)
    set_values_by_label: dict[str, list[str]] = {}
    for idx, sample_value in enumerate(actual_sample_values):
        subset = frame.loc[frame[resolved_sample_col] == sample_value]
        set_values_by_label[unique_labels[idx]] = _extract_overlap_values(subset, resolved_match_on)

    return _overlap_payload_from_sets(
        set_values_by_label,
        source_payload=payload,
        label=params.get("output_label") or _default_overlap_label(unique_labels),
        extra_metadata={
            "source_df_id": params["df_id"],
            "source_df_ids": [params["df_id"]],
            "source_labels": unique_labels,
            "sample_col": resolved_sample_col,
            "sample_values": [str(value) for value in actual_sample_values],
            "source_match_cols": [resolved_match_on],
            "match_key_column": "match_key",
        },
    )


def _overlap_payload_from_sets(
    set_values_by_label: dict[str, list[str]],
    *,
    source_payload: dict[str, Any],
    label: str,
    extra_metadata: dict[str, Any],
) -> dict[str, Any]:
    ordered_keys = _ordered_overlap_keys(set_values_by_label.values())
    rows: list[dict[str, Any]] = []
    set_columns = list(set_values_by_label.keys())
    membership_sets = {name: set(values) for name, values in set_values_by_label.items()}
    for key in ordered_keys:
        row = {"match_key": key}
        for set_name in set_columns:
            row[set_name] = key in membership_sets[set_name]
        rows.append(row)

    frame = pd.DataFrame(rows, columns=["match_key", *set_columns])
    return dataframe_to_payload(
        frame,
        source_payload=source_payload,
        operation="build_overlap_dataframe",
        label=str(label).strip() or _default_overlap_label(set_columns),
        extra_metadata={
            **extra_metadata,
            "kind": "overlap_membership",
            "set_columns": set_columns,
        },
    )


def _extract_overlap_values(frame: pd.DataFrame, column: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for raw_value in frame[column].tolist():
        normalized = _normalize_overlap_value(raw_value)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        values.append(normalized)
    return values


def _normalize_overlap_value(raw_value: Any) -> str | None:
    if raw_value is None or pd.isna(raw_value):
        return None
    text = str(raw_value).strip()
    return text or None


def _ordered_sample_values(series: pd.Series) -> list[Any]:
    values: list[Any] = []
    seen: set[str] = set()
    for raw_value in series.tolist():
        normalized = _normalize_overlap_value(raw_value)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        values.append(raw_value)
    return values


def _resolve_sample_value(series: pd.Series, requested: Any):
    requested_normalized = _normalize_overlap_value(requested)
    if requested_normalized is None:
        raise ValueError(f"Invalid sample value: {requested}")

    exact_values = _ordered_sample_values(series)
    for value in exact_values:
        if str(value) == str(requested):
            return value
    for value in exact_values:
        if _normalize_overlap_value(value).lower() == requested_normalized.lower():
            return value
    raise ValueError(f"Sample value '{requested}' was not found in column '{series.name}'.")


def _ordered_overlap_keys(value_lists: list[list[str]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for values in value_lists:
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return ordered


def _deduplicate_labels(labels: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique: list[str] = []
    for raw_label in labels:
        label = str(raw_label).strip() or "set"
        next_count = counts.get(label, 0) + 1
        counts[label] = next_count
        unique.append(label if next_count == 1 else f"{label} ({next_count})")
    return unique


def _default_overlap_label(labels: list[str]) -> str:
    if not labels:
        return "overlap_dataframe"
    preview = "_vs_".join(re.sub(r"\s+", "_", str(label).strip()) for label in labels[:3])
    return f"overlap_{preview}"