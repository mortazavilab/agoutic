from __future__ import annotations

import pandas as pd


def payload_to_dataframe(df_payload: dict) -> pd.DataFrame:
    columns = list(df_payload.get("columns") or [])
    rows = list(df_payload.get("data") or [])
    frame = pd.DataFrame(rows)
    if columns:
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame = frame[columns]
    return frame


def dataframe_to_payload(
    frame: pd.DataFrame,
    *,
    source_payload: dict,
    operation: str,
    label: str,
    extra_metadata: dict | None = None,
) -> dict:
    metadata = dict(source_payload.get("metadata") or {})
    metadata.pop("df_id", None)
    metadata["label"] = label
    metadata["row_count"] = int(len(frame))
    metadata["visible"] = True
    metadata["operation"] = operation
    if extra_metadata:
        metadata.update(extra_metadata)
    clean_frame = frame.where(pd.notna(frame), None)
    return {
        "columns": clean_frame.columns.tolist(),
        "data": clean_frame.to_dict(orient="records"),
        "row_count": int(len(clean_frame)),
        "metadata": metadata,
    }


def filter_dataframe(
    frame: pd.DataFrame,
    *,
    column: str,
    operator: str,
    value,
) -> pd.DataFrame:
    series = frame[column]
    if operator == "==":
        mask = series == value
    elif operator == "!=":
        mask = series != value
    elif operator == ">":
        mask = series > value
    elif operator == ">=":
        mask = series >= value
    elif operator == "<":
        mask = series < value
    elif operator == "<=":
        mask = series <= value
    elif operator == "in":
        mask = series.isin(value)
    elif operator == "contains":
        mask = series.astype(str).str.contains(str(value), case=False, na=False)
    else:
        raise ValueError(f"Unsupported filter operator: {operator}")
    return frame.loc[mask].reset_index(drop=True)


def select_columns(frame: pd.DataFrame, *, columns: list[str]) -> pd.DataFrame:
    return frame.loc[:, columns].copy()


def rename_columns(frame: pd.DataFrame, *, rename_map: dict[str, str]) -> pd.DataFrame:
    return frame.rename(columns=rename_map)


def sort_dataframe(
    frame: pd.DataFrame,
    *,
    sort_by: list[str],
    ascending: list[bool],
) -> pd.DataFrame:
    return frame.sort_values(by=sort_by, ascending=ascending, kind="stable").reset_index(drop=True)


def melt_dataframe(
    frame: pd.DataFrame,
    *,
    id_vars: list[str],
    value_vars: list[str] | None,
    var_name: str,
    value_name: str,
) -> pd.DataFrame:
    return pd.melt(
        frame,
        id_vars=id_vars,
        value_vars=value_vars,
        var_name=var_name,
        value_name=value_name,
    )


def aggregate_dataframe(
    frame: pd.DataFrame,
    *,
    group_by: list[str],
    aggregations: dict[str, str],
) -> pd.DataFrame:
    if not aggregations:
        raise ValueError("At least one aggregation is required")
    grouped = frame.groupby(group_by, dropna=False).agg(aggregations).reset_index()
    grouped.columns = [
        "_".join(str(part) for part in col if str(part)) if isinstance(col, tuple) else str(col)
        for col in grouped.columns
    ]
    return grouped


def join_dataframes(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    on: list[str] | None,
    left_on: list[str] | None,
    right_on: list[str] | None,
    how: str,
    suffixes: tuple[str, str],
) -> pd.DataFrame:
    return left.merge(
        right,
        how=how,
        on=on,
        left_on=left_on,
        right_on=right_on,
        suffixes=suffixes,
    )


def pivot_dataframe(
    frame: pd.DataFrame,
    *,
    index: list[str],
    columns: str,
    values: str,
    aggfunc: str,
) -> pd.DataFrame:
    pivoted = frame.pivot_table(
        index=index,
        columns=columns,
        values=values,
        aggfunc=aggfunc,
    ).reset_index()
    pivoted.columns = [str(col) for col in pivoted.columns]
    return pivoted