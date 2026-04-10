import re
from collections import defaultdict
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


def _render_md_with_dataframes(md: str, block_id: str, section: str):
    """Render markdown while converting pipe tables to interactive dataframes."""
    lines = md.splitlines(keepends=True)
    buf_text: list[str] = []
    buf_table: list[str] = []
    table_index = [0]

    def flush_text():
        chunk = "".join(buf_text).strip()
        if chunk:
            st.markdown(chunk)
        buf_text.clear()

    def flush_table():
        raw = "".join(buf_table)
        rows = [l for l in buf_table if l.strip() and not re.match(r"^\s*\|[-| :]+\|\s*$", l)]
        if not rows:
            buf_table.clear()
            return
        try:
            all_rows = [l.strip() for l in buf_table if l.strip()]
            header_row = all_rows[0]
            data_rows = [r for r in all_rows[2:] if r.startswith("|")]
            parse_row = lambda r: [c.strip() for c in r.strip("|").split("|")]
            headers = parse_row(header_row)
            records = [parse_row(r) for r in data_rows]
            records = [r + [""] * (len(headers) - len(r)) for r in records]
            df = pd.DataFrame(records, columns=headers)
            idx = table_index[0]
            table_index[0] += 1
            st.dataframe(
                df,
                width="stretch",
                hide_index=True,
                height=min(400, 35 * len(df) + 38),
                key=f"_mdtbl_{block_id}_{section}_{idx}",
            )
        except Exception:
            st.markdown(raw)
        buf_table.clear()

    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            if buf_table:
                flush_table()
            buf_text.append(line)
            continue

        if in_code_block:
            buf_text.append(line)
            continue

        is_table_line = stripped.startswith("|")
        if is_table_line:
            if buf_text:
                flush_text()
            buf_table.append(line)
        else:
            if buf_table:
                flush_table()
            buf_text.append(line)

    if buf_table:
        flush_table()
    if buf_text:
        flush_text()


def _render_embedded_dataframes(dfs: dict, block_id: str, *, only_visible: bool = True):
    """Render dataframes stored in payload _dataframes."""
    for _df_idx, (_fname, _fdata) in enumerate(dfs.items()):
        _meta = _fdata.get("metadata", {})
        _df_id = _meta.get("df_id")
        _has_id = _df_id is not None
        if only_visible and not _has_id:
            continue
        if not only_visible and _has_id:
            continue

        _is_visible = _has_id
        _rows = _fdata.get("data", [])
        _cols = _fdata.get("columns", [])
        _total = _fdata.get("row_count", len(_rows))
        _df = pd.DataFrame(_rows)
        if _df.empty:
            st.info(f"📊 **{_fname}** — empty table")
            continue

        _safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", f"{block_id}_{_df_idx}")
        _df_prefix = f"DF{_df_id}: " if _df_id else ""
        with st.expander(
            f"📊 {_df_prefix}**{_fname}** — {_total:,} rows × {len(_cols)} columns",
            expanded=_is_visible,
        ):
            _col_stats = _meta.get("column_stats", {})
            if _col_stats:
                with st.popover("📈 Column statistics"):
                    for _cn, _ci in _col_stats.items():
                        _dt = _ci.get("dtype", "")
                        _nl = _ci.get("nulls", 0)
                        _ln = f"**{_cn}** ({_dt})"
                        if _nl:
                            _ln += f" — {_nl} nulls"
                        if "mean" in _ci and _ci["mean"] is not None:
                            _ln += (
                                f"  \nmin={_ci.get('min')}  max={_ci.get('max')}  "
                                f"mean={_ci.get('mean'):.4g}  median={_ci.get('median'):.4g}"
                            )
                        elif "unique" in _ci:
                            _ln += f"  \n{_ci['unique']} unique values"
                        st.markdown(_ln)

            if len(_cols) > 4:
                _sel_cols = st.multiselect(
                    "Columns to display",
                    _cols,
                    default=_cols,
                    key=f"dfchat_{_safe_key}",
                )
            else:
                _sel_cols = _cols
            _disp = _df[_sel_cols] if _sel_cols else _df
            st.dataframe(
                _disp,
                width="stretch",
                hide_index=True,
                height=min(400, 35 * len(_disp) + 38),
            )
            if _meta.get("is_truncated"):
                st.caption(f"Showing {len(_rows):,} of {_total:,} rows")
            _csv = _disp.to_csv(index=False).encode("utf-8")
            st.download_button(
                f"⬇️ Download {_fname}",
                data=_csv,
                file_name=_fname,
                mime="text/csv",
                key=f"dldfc_{_safe_key}",
            )


def _resolve_df_by_id(df_id: int, all_blocks: list):
    """Resolve DF by ID across AGENT_PLAN blocks."""
    for blk in reversed(all_blocks):
        if blk.get("type") != "AGENT_PLAN":
            continue
        dfs = blk.get("payload", {}).get("_dataframes", {})
        for fname, fdata in dfs.items():
            meta = fdata.get("metadata", {})
            if meta.get("df_id") == df_id:
                rows = fdata.get("data", [])
                cols = fdata.get("columns") or None
                df = pd.DataFrame(rows, columns=cols)
                return df, fname
    return None, None


def _resolve_payload_df_by_id(df_id: int, dfs: dict):
    """Resolve DF by ID in a payload-local dataframe map."""
    for fname, fdata in (dfs or {}).items():
        meta = fdata.get("metadata", {}) if isinstance(fdata, dict) else {}
        if meta.get("df_id") == df_id:
            rows = fdata.get("data", []) if isinstance(fdata, dict) else []
            cols = fdata.get("columns") if isinstance(fdata, dict) else None
            return pd.DataFrame(rows, columns=cols or None), fname
    return None, None


def _match_column_name(columns: list[str], requested: str | None) -> str | None:
    if not requested:
        return None
    for column in columns:
        if column == requested:
            return column
    lowered = str(requested).strip().lower()
    for column in columns:
        if str(column).strip().lower() == lowered:
            return column
    return None


def _should_auto_melt_for_grouping(chart_type: str, color_col: str | None) -> bool:
    if chart_type not in {"bar", "box", "histogram"}:
        return False
    if not color_col:
        return False
    lowered = str(color_col).strip().lower()
    return lowered in {"sample", "samples", "group", "condition", "replicate"}


def _auto_melt_for_grouping(
    df_plot: pd.DataFrame,
    *,
    chart_type: str,
    x_col: str | None,
    y_col: str | None,
    color_col: str | None,
):
    actual_color = _match_column_name(list(df_plot.columns), color_col)
    if actual_color or not _should_auto_melt_for_grouping(chart_type, color_col):
        return df_plot, x_col, y_col, actual_color

    numeric_cols = [col for col in df_plot.select_dtypes(include="number").columns.tolist() if col != x_col]
    if len(numeric_cols) < 2:
        return df_plot, x_col, y_col, actual_color

    if x_col and x_col in df_plot.columns:
        id_vars = [x_col]
    else:
        cat_cols = [col for col in df_plot.columns if col not in numeric_cols]
        id_vars = cat_cols[:1] if cat_cols else []
    if not id_vars:
        return df_plot, x_col, y_col, actual_color

    value_name = y_col or "value"
    var_name = color_col or "group"
    melted = pd.melt(
        df_plot,
        id_vars=id_vars,
        value_vars=numeric_cols,
        var_name=var_name,
        value_name=value_name,
    )
    new_x = x_col if x_col in melted.columns else id_vars[0]
    return melted, new_x, value_name, var_name


def _auto_melt_for_wide_measure_axis(
    df_plot: pd.DataFrame,
    *,
    requested_x: str | None,
    y_col: str | None,
    color_col: str | None,
):
    lowered_x = str(requested_x or "").strip().lower()
    if lowered_x not in {"measure", "measures", "metric", "metrics", "variable", "variables"}:
        return df_plot, None, y_col, color_col

    numeric_cols = df_plot.select_dtypes(include="number").columns.tolist()
    if len(numeric_cols) < 2:
        return df_plot, None, y_col, color_col

    id_candidates = [col for col in df_plot.columns if col not in numeric_cols]
    if not id_candidates:
        return df_plot, None, y_col, color_col

    group_col = next(
        (
            col
            for col in id_candidates
            if str(col).strip().lower() in {"sample", "samples", "group", "condition", "replicate"}
        ),
        id_candidates[0],
    )
    value_name = y_col or "value"
    melted = pd.melt(
        df_plot,
        id_vars=[group_col],
        value_vars=numeric_cols,
        var_name="measure",
        value_name=value_name,
    )
    return melted, "measure", value_name, color_col or group_col


def _build_plotly_figure(chart_spec: dict, df: pd.DataFrame, df_label: str, plotly_template: str):
    """Build a Plotly figure from chart spec and dataframe."""
    named_colors = {
        "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure", "beige", "bisque", "black",
        "blanchedalmond", "blue", "blueviolet", "brown", "burlywood", "cadetblue", "chartreuse",
        "chocolate", "coral", "cornflowerblue", "cornsilk", "crimson", "cyan", "darkblue",
        "darkcyan", "darkgoldenrod", "darkgray", "darkgreen", "darkgrey", "darkkhaki", "darkmagenta",
        "darkolivegreen", "darkorange", "darkorchid", "darkred", "darksalmon", "darkseagreen",
        "darkslateblue", "darkslategray", "darkslategrey", "darkturquoise", "darkviolet", "deeppink",
        "deepskyblue", "dimgray", "dimgrey", "dodgerblue", "firebrick", "floralwhite", "forestgreen",
        "fuchsia", "gainsboro", "ghostwhite", "gold", "goldenrod", "gray", "green", "greenyellow",
        "grey", "honeydew", "hotpink", "indianred", "indigo", "ivory", "khaki", "lavender",
        "lavenderblush", "lawngreen", "lemonchiffon", "lightblue", "lightcoral", "lightcyan",
        "lightgoldenrodyellow", "lightgray", "lightgreen", "lightgrey", "lightpink", "lightsalmon",
        "lightseagreen", "lightskyblue", "lightslategray", "lightslategrey", "lightsteelblue",
        "lightyellow", "lime", "limegreen", "linen", "magenta", "maroon", "mediumaquamarine",
        "mediumblue", "mediumorchid", "mediumpurple", "mediumseagreen", "mediumslateblue",
        "mediumspringgreen", "mediumturquoise", "mediumvioletred", "midnightblue", "mintcream",
        "mistyrose", "moccasin", "navajowhite", "navy", "oldlace", "olive", "olivedrab", "orange",
        "orangered", "orchid", "palegoldenrod", "palegreen", "paleturquoise", "palevioletred",
        "papayawhip", "peachpuff", "peru", "pink", "plum", "powderblue", "purple", "red",
        "rosybrown", "royalblue", "saddlebrown", "salmon", "sandybrown", "seagreen", "seashell",
        "sienna", "silver", "skyblue", "slateblue", "slategray", "slategrey", "snow", "springgreen",
        "steelblue", "tan", "teal", "thistle", "tomato", "turquoise", "violet", "wheat", "white",
        "whitesmoke", "yellow", "yellowgreen",
    }

    def _match_column_name_local(columns: list[str], requested: str | None) -> str | None:
        if not requested:
            return None
        for column in columns:
            if column == requested:
                return column
        lowered = str(requested).strip().lower()
        for column in columns:
            if str(column).strip().lower() == lowered:
                return column
        return None

    def _should_auto_melt_for_grouping_local(chart_type_value: str, color_value: str | None) -> bool:
        if chart_type_value not in {"bar", "box", "histogram"}:
            return False
        if not color_value:
            return False
        lowered = str(color_value).strip().lower()
        return lowered in {"sample", "samples", "group", "condition", "replicate"}

    def _auto_melt_for_grouping_local(
        df_plot_local: pd.DataFrame,
        *,
        chart_type_value: str,
        x_col_value: str | None,
        y_col_value: str | None,
        color_col_value: str | None,
    ):
        actual_color = _match_column_name_local(list(df_plot_local.columns), color_col_value)
        if actual_color or not _should_auto_melt_for_grouping_local(chart_type_value, color_col_value):
            return df_plot_local, x_col_value, y_col_value, actual_color

        numeric_cols = [col for col in df_plot_local.select_dtypes(include="number").columns.tolist() if col != x_col_value]
        if len(numeric_cols) < 2:
            return df_plot_local, x_col_value, y_col_value, actual_color

        if x_col_value and x_col_value in df_plot_local.columns:
            id_vars = [x_col_value]
        else:
            cat_cols = [col for col in df_plot_local.columns if col not in numeric_cols]
            id_vars = cat_cols[:1] if cat_cols else []
        if not id_vars:
            return df_plot_local, x_col_value, y_col_value, actual_color

        value_name = y_col_value or "value"
        var_name = color_col_value or "group"
        melted = pd.melt(
            df_plot_local,
            id_vars=id_vars,
            value_vars=numeric_cols,
            var_name=var_name,
            value_name=value_name,
        )
        new_x = x_col_value if x_col_value in melted.columns else id_vars[0]
        return melted, new_x, value_name, var_name

    def _auto_melt_for_wide_measure_axis_local(
        df_plot_local: pd.DataFrame,
        *,
        requested_x_value: str | None,
        y_col_value: str | None,
        color_col_value: str | None,
    ):
        lowered_x = str(requested_x_value or "").strip().lower()
        if lowered_x not in {"measure", "measures", "metric", "metrics", "variable", "variables"}:
            return df_plot_local, None, y_col_value, color_col_value

        numeric_cols = df_plot_local.select_dtypes(include="number").columns.tolist()
        if len(numeric_cols) < 2:
            return df_plot_local, None, y_col_value, color_col_value

        id_candidates = [col for col in df_plot_local.columns if col not in numeric_cols]
        if not id_candidates:
            return df_plot_local, None, y_col_value, color_col_value

        group_col = next(
            (
                col
                for col in id_candidates
                if str(col).strip().lower() in {"sample", "samples", "group", "condition", "replicate"}
            ),
            id_candidates[0],
        )
        value_name = y_col_value or "value"
        melted = pd.melt(
            df_plot_local,
            id_vars=[group_col],
            value_vars=numeric_cols,
            var_name="measure",
            value_name=value_name,
        )
        return melted, "measure", value_name, color_col_value or group_col

    chart_type = chart_spec.get("type", "histogram")
    requested_x_col = chart_spec.get("x")
    x_col = chart_spec.get("x")
    y_col = chart_spec.get("y")
    color_col = chart_spec.get("color")
    palette = chart_spec.get("palette")
    title = chart_spec.get("title", "")
    x_axis_label = chart_spec.get("xlabel") or chart_spec.get("x_label")
    y_axis_label = chart_spec.get("ylabel") or chart_spec.get("y_label")
    agg = chart_spec.get("agg")
    literal_color = None

    def _looks_like_literal_color(value):
        if not value:
            return False
        value = str(value).strip().strip('"').strip("'")
        if not value:
            return False
        if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value):
            return True
        if re.fullmatch(r"(?:rgb|rgba|hsl|hsla)\([^\)]*\)", value, re.IGNORECASE):
            return True
        return value.lower() in named_colors

    def _parse_palette_values(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            candidates = value
        else:
            candidates = re.split(r"[|;]", str(value))
        return [
            str(item).strip().strip('"').strip("'")
            for item in candidates
            if str(item).strip().strip('"').strip("'")
        ]

    palette_values = _parse_palette_values(palette)
    if palette_values and len(palette_values) == 1 and _looks_like_literal_color(palette_values[0]):
        literal_color = palette_values[0]

    available = list(df.columns)
    requested_color_col = color_col

    if x_col and x_col not in available:
        match = [c for c in available if c.lower() == x_col.lower()]
        x_col = match[0] if match else None
    if y_col and y_col not in available:
        match = [c for c in available if c.lower() == y_col.lower()]
        y_col = match[0] if match else None
    if color_col and color_col not in available:
        match = [c for c in available if c.lower() == color_col.lower()]
        if match:
            color_col = match[0]
        elif _looks_like_literal_color(color_col):
            literal_color = str(color_col).strip().strip('"').strip("'")
            color_col = None
        else:
            color_col = None

    try:
        df_plot = df.copy()
        df_plot, x_col, y_col, color_col = _auto_melt_for_grouping_local(
            df_plot,
            chart_type_value=chart_type,
            x_col_value=x_col,
            y_col_value=y_col,
            color_col_value=color_col or requested_color_col,
        )
        px_color_kwargs = {}
        if color_col:
            px_color_kwargs["color"] = color_col
        if palette_values:
            px_color_kwargs["color_discrete_sequence"] = palette_values
        for col in [x_col, y_col]:
            if col and col in df_plot.columns:
                try:
                    df_plot[col] = pd.to_numeric(df_plot[col])
                except (ValueError, TypeError):
                    pass

        if chart_type == "histogram":
            if not x_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                x_col = num_cols[0] if len(num_cols) > 0 else available[0]
            x_is_numeric = pd.api.types.is_numeric_dtype(df_plot[x_col])
            if not x_is_numeric:
                num_cols = df_plot.select_dtypes(include="number").columns.tolist()
                if num_cols:
                    _count_names = {"count", "counts", "value", "values", "n", "total", "freq", "frequency", "amount"}
                    _y_col = next((c for c in num_cols if c.lower() in _count_names), num_cols[0])
                    fig = px.bar(df_plot, x=x_col, y=_y_col, **px_color_kwargs, title=title or f"{_y_col} by {x_col}")
                else:
                    counts = df_plot[x_col].value_counts().reset_index()
                    counts.columns = [x_col, "Count"]
                    fig = px.bar(counts, x=x_col, y="Count", **px_color_kwargs, title=title or f"Count by {x_col}")
            else:
                fig = px.histogram(df_plot, x=x_col, **px_color_kwargs, title=title or f"Distribution of {x_col}")

        elif chart_type == "scatter":
            if not x_col or not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                if len(num_cols) >= 2:
                    x_col = x_col or num_cols[0]
                    y_col = y_col or num_cols[1]
                else:
                    return None
            fig = px.scatter(df_plot, x=x_col, y=y_col, **px_color_kwargs, title=title or f"{x_col} vs {y_col}")

        elif chart_type == "bar":
            if not x_col:
                df_plot, wide_x_col, wide_y_col, wide_color_col = _auto_melt_for_wide_measure_axis_local(
                    df_plot,
                    requested_x_value=requested_x_col,
                    y_col_value=y_col,
                    color_col_value=color_col,
                )
                if wide_x_col:
                    x_col = wide_x_col
                    y_col = wide_y_col
                    color_col = wide_color_col
                    if color_col:
                        px_color_kwargs["color"] = color_col
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns
                x_col = cat_cols[0] if len(cat_cols) > 0 else available[0]
            x_is_cat = not pd.api.types.is_numeric_dtype(df_plot[x_col])
            if not y_col and x_is_cat and agg != "count":
                num_cols = df_plot.select_dtypes(include="number").columns.tolist()
                if len(num_cols) > 1 and not color_col:
                    df_plot = pd.melt(
                        df_plot,
                        id_vars=[x_col],
                        value_vars=[col for col in num_cols if col != x_col],
                        var_name="measure",
                        value_name="value",
                    )
                    y_col = "value"
                    color_col = "measure"
                    px_color_kwargs["color"] = color_col
                elif num_cols:
                    _count_names = {"count", "counts", "value", "values", "n", "total", "freq", "frequency", "amount"}
                    y_col = next((c for c in num_cols if c.lower() in _count_names), num_cols[0])
            if agg == "count" or not y_col:
                counts = df_plot[x_col].value_counts().reset_index()
                counts.columns = [x_col, "Count"]
                fig = px.bar(counts, x=x_col, y="Count", **px_color_kwargs, title=title or f"Count by {x_col}")
            else:
                group_keys = [x_col] + ([color_col] if color_col and color_col in df_plot.columns else [])
                if agg == "mean":
                    agg_df = df_plot.groupby(group_keys, as_index=False)[y_col].mean()
                elif agg == "sum":
                    agg_df = df_plot.groupby(group_keys, as_index=False)[y_col].sum()
                else:
                    agg_df = df_plot
                fig = px.bar(agg_df, x=x_col, y=y_col, **px_color_kwargs, title=title or f"{y_col} by {x_col}")

        elif chart_type == "box":
            if not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                y_col = num_cols[0] if len(num_cols) > 0 else None
            if not y_col:
                return None
            fig = px.box(
                df_plot,
                x=x_col,
                y=y_col,
                **px_color_kwargs,
                title=title or f"Distribution of {y_col}" + (f" by {x_col}" if x_col else ""),
            )

        elif chart_type == "heatmap":
            num_df = df_plot.select_dtypes(include="number")
            if num_df.shape[1] < 2:
                return None
            corr = num_df.corr()
            fig = px.imshow(
                corr,
                text_auto=".2f",
                title=title or "Correlation Matrix",
                color_continuous_scale="RdBu_r",
                zmin=-1,
                zmax=1,
            )

        elif chart_type == "pie":
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns
                x_col = cat_cols[0] if len(cat_cols) > 0 else available[0]
            if y_col:
                fig = px.pie(
                    df_plot,
                    names=x_col,
                    values=y_col,
                    **({"color": color_col} if color_col else {}),
                    **({"color_discrete_sequence": palette_values} if palette_values else {}),
                    title=title or f"{x_col} Proportions",
                )
            else:
                counts = df_plot[x_col].value_counts().reset_index()
                counts.columns = [x_col, "Count"]
                fig = px.pie(
                    counts,
                    names=x_col,
                    values="Count",
                    **({"color": color_col} if color_col else {}),
                    **({"color_discrete_sequence": palette_values} if palette_values else {}),
                    title=title or f"{x_col} Distribution",
                )
        else:
            return None

        if literal_color:
            if chart_type == "pie":
                for trace in fig.data:
                    labels = getattr(trace, "labels", None)
                    trace.marker.colors = [literal_color] * len(labels or [1])
            else:
                fig.update_traces(marker_color=literal_color)
                for trace in fig.data:
                    if hasattr(trace, "line"):
                        trace.line.color = literal_color
                    if hasattr(trace, "fillcolor"):
                        trace.fillcolor = literal_color

        if chart_type == "bar" and color_col:
            fig.update_layout(barmode="group")
        layout_updates = {"template": plotly_template}
        if x_axis_label:
            layout_updates["xaxis_title"] = x_axis_label
        if y_axis_label:
            layout_updates["yaxis_title"] = y_axis_label
        fig.update_layout(**layout_updates)
        return fig
    except Exception:
        return None


def _render_plot_block(payload: dict, all_blocks: list, block_id: str, plotly_template: str):
    """Render AGENT_PLOT charts."""
    charts = payload.get("charts", [])
    if not charts:
        st.info("No chart specifications found in this plot block.")
        return

    groups = defaultdict(list)
    for chart in charts:
        key = (chart.get("df_id"), chart.get("type"))
        groups[key].append(chart)

    chart_idx = 0
    for (df_id, chart_type), chart_group in groups.items():
        if df_id is None:
            st.warning("Chart missing DataFrame reference (df=DFN).")
            continue

        df, df_label = _resolve_df_by_id(df_id, all_blocks)
        if df is None or df.empty:
            st.warning(f"DataFrame DF{df_id} not found in conversation history.")
            continue

        if len(chart_group) == 1:
            fig = _build_plotly_figure(chart_group[0], df, df_label, plotly_template)
            if fig:
                _safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                st.plotly_chart(fig, width="stretch", key=_safe_key)
            else:
                st.warning(
                    f"Could not render {chart_type} chart for DF{df_id}. "
                    "Check that the specified columns exist."
                )
        else:
            combined = go.Figure()
            title_parts = []
            for spec in chart_group:
                fig = _build_plotly_figure(spec, df, df_label, plotly_template)
                if fig:
                    for trace in fig.data:
                        combined.add_trace(trace)
                    if spec.get("title"):
                        title_parts.append(spec["title"])
            if combined.data:
                first_spec = chart_group[0] if chart_group else {}
                combined.update_layout(
                    template=plotly_template,
                    title=" / ".join(title_parts) if title_parts else f"DF{df_id} — {chart_type}",
                    xaxis_title=first_spec.get("xlabel") or first_spec.get("x_label"),
                    yaxis_title=first_spec.get("ylabel") or first_spec.get("y_label"),
                )
                _safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                st.plotly_chart(combined, width="stretch", key=_safe_key)
            else:
                st.warning(f"Could not render multi-trace {chart_type} chart for DF{df_id}.")
        chart_idx += 1


def _render_workflow_plot_payload(payload: dict, block_id: str, step_suffix: str, plotly_template: str):
    """Render workflow-local plots embedded in a step result payload."""
    if not isinstance(payload, dict):
        return

    image_files = payload.get("image_files", []) if isinstance(payload.get("image_files"), list) else []
    if not image_files:
        legacy_text = ""
        if isinstance(payload.get("data"), str):
            legacy_text = str(payload.get("data") or "")
        elif isinstance(payload.get("message"), str):
            legacy_text = str(payload.get("message") or "")
        artifact_path = ""
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
        if isinstance(artifacts.get("volcano_plot"), str):
            artifact_path = str(artifacts.get("volcano_plot") or "").strip()
        if not artifact_path and legacy_text:
            match = re.search(r"Volcano plot saved to:\s*(.+\.(?:png|jpg|jpeg|gif|webp))", legacy_text)
            if match:
                artifact_path = match.group(1).strip()
        if artifact_path:
            image_files = [{
                "path": artifact_path,
                "caption": "Volcano plot",
            }]

    if isinstance(image_files, list):
        for image_idx, image in enumerate(image_files):
            image_path = ""
            image_b64 = ""
            if isinstance(image, dict):
                image_path = str(image.get("path") or "").strip()
                image_b64 = str(image.get("data_b64") or "").strip()
                caption = str(image.get("caption") or image.get("label") or "Plot").strip() or "Plot"
            else:
                caption = "Plot"
                if isinstance(image, str):
                    image_path = image.strip()
            if not image_path and not image_b64:
                continue
            if image_b64:
                import base64

                image_bytes = base64.b64decode(image_b64)
                st.image(image_bytes, caption=caption, use_container_width=True)
                continue
            if not Path(image_path).exists():
                st.caption(f"{caption}: {image_path}")
                continue
            _safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", f"wf_img_{block_id}_{step_suffix}_{image_idx}")
            st.image(image_path, caption=caption, use_container_width=True)

    charts = payload.get("charts", [])
    dfs = payload.get("_dataframes", {})
    if not charts or not isinstance(dfs, dict):
        return

    for chart_idx, chart in enumerate(charts):
        if not isinstance(chart, dict):
            continue
        df_id = chart.get("df_id")
        if df_id is None:
            st.warning("Workflow chart is missing a dataframe reference.")
            continue
        df, df_label = _resolve_payload_df_by_id(df_id, dfs)
        if df is None or df.empty:
            st.warning(f"Workflow chart source DF{df_id} is missing.")
            continue
        fig = _build_plotly_figure(chart, df, df_label, plotly_template)
        if fig is None:
            st.warning(f"Could not render workflow chart for DF{df_id}.")
            continue
        _safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", f"wf_plot_{block_id}_{step_suffix}_{chart_idx}")
        st.plotly_chart(fig, width="stretch", key=_safe_key)
