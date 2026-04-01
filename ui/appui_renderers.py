import re
from collections import defaultdict

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


def _build_plotly_figure(chart_spec: dict, df: pd.DataFrame, df_label: str, plotly_template: str):
    """Build a Plotly figure from chart spec and dataframe."""
    chart_type = chart_spec.get("type", "histogram")
    x_col = chart_spec.get("x")
    y_col = chart_spec.get("y")
    color_col = chart_spec.get("color")
    palette = chart_spec.get("palette")
    title = chart_spec.get("title", "")
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
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value))

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

    px_color_kwargs = {}
    available = list(df.columns)

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

    if color_col:
        px_color_kwargs["color"] = color_col
    if palette_values:
        px_color_kwargs["color_discrete_sequence"] = palette_values

    try:
        df_plot = df.copy()
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
                cat_cols = df_plot.select_dtypes(exclude="number").columns
                x_col = cat_cols[0] if len(cat_cols) > 0 else available[0]
            x_is_cat = not pd.api.types.is_numeric_dtype(df_plot[x_col])
            if not y_col and x_is_cat and agg != "count":
                num_cols = df_plot.select_dtypes(include="number").columns.tolist()
                if num_cols:
                    _count_names = {"count", "counts", "value", "values", "n", "total", "freq", "frequency", "amount"}
                    y_col = next((c for c in num_cols if c.lower() in _count_names), num_cols[0])
            if agg == "count" or not y_col:
                counts = df_plot[x_col].value_counts().reset_index()
                counts.columns = [x_col, "Count"]
                fig = px.bar(counts, x=x_col, y="Count", **px_color_kwargs, title=title or f"Count by {x_col}")
            else:
                if agg == "mean":
                    agg_df = df_plot.groupby(x_col, as_index=False)[y_col].mean()
                elif agg == "sum":
                    agg_df = df_plot.groupby(x_col, as_index=False)[y_col].sum()
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

        fig.update_layout(template=plotly_template)
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
                combined.update_layout(
                    template=plotly_template,
                    title=" / ".join(title_parts) if title_parts else f"DF{df_id} — {chart_type}",
                )
                _safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                st.plotly_chart(combined, width="stretch", key=_safe_key)
            else:
                st.warning(f"Could not render multi-trace {chart_type} chart for DF{df_id}.")
        chart_idx += 1


def _render_workflow_plot_payload(payload: dict, block_id: str, step_suffix: str, plotly_template: str):
    """Render workflow-local plots embedded in a step result payload."""
    charts = payload.get("charts", []) if isinstance(payload, dict) else []
    dfs = payload.get("_dataframes", {}) if isinstance(payload, dict) else {}
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
