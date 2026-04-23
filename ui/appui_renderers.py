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
    """Resolve DF by ID across blocks that carry payload _dataframes."""
    for blk in reversed(all_blocks):
        payload = blk.get("payload", {})
        dfs = payload.get("_dataframes", {})
        for fname, fdata in dfs.items():
            meta = fdata.get("metadata", {})
            if meta.get("df_id") == df_id:
                rows = fdata.get("data", [])
                cols = fdata.get("columns") or None
                declared_rows = meta.get("row_count") or fdata.get("row_count") or meta.get("total_rows")
                if meta.get("is_truncated") or (
                    isinstance(declared_rows, int) and declared_rows > len(rows)
                ):
                    candidates = {Path(str(fname)).name}
                    label = str(meta.get("label") or "").strip()
                    if label:
                        candidates.add(Path(label).name)
                    parse_entries = [
                        entry for entry in (payload.get("_provenance") or [])
                        if entry.get("success")
                        and entry.get("source") == "analyzer"
                        and entry.get("tool") == "parse_csv_file"
                    ]
                    parse_source = None
                    for entry in parse_entries:
                        params = dict(entry.get("params") or {})
                        file_path = str(params.get("file_path") or "").strip()
                        if file_path and Path(file_path).name in candidates:
                            parse_source = params
                            break
                    if parse_source is None and len(parse_entries) == 1:
                        parse_source = dict(parse_entries[0].get("params") or {})
                    if parse_source:
                        raw_file_path = str(parse_source.get("file_path") or label or fname).strip()
                        raw_work_dir = str(parse_source.get("work_dir") or "").strip()
                        candidate_path = Path(raw_file_path).expanduser()
                        full_path = None
                        if candidate_path.is_absolute():
                            if candidate_path.exists() and candidate_path.is_file():
                                full_path = candidate_path
                        elif raw_work_dir:
                            work_dir = Path(raw_work_dir).expanduser()
                            for path_try in ((work_dir / candidate_path), (work_dir / candidate_path.name)):
                                if path_try.exists() and path_try.is_file():
                                    full_path = path_try
                                    break
                        if full_path is not None:
                            sep = "\t" if full_path.suffix.lower() == ".tsv" else ","
                            try:
                                rehydrated = pd.read_csv(full_path, sep=sep, low_memory=False)
                            except Exception:
                                rehydrated = None
                            if rehydrated is not None and not rehydrated.empty:
                                return rehydrated, fname
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
            declared_rows = meta.get("row_count") or fdata.get("row_count") or meta.get("total_rows")
            if meta.get("is_truncated") or (
                isinstance(declared_rows, int) and declared_rows > len(rows)
            ):
                raw_file_path = str(meta.get("source_file_path") or meta.get("label") or fname).strip()
                raw_work_dir = str(meta.get("source_work_dir") or "").strip()
                candidate_path = Path(raw_file_path).expanduser()
                full_path = None
                if candidate_path.is_absolute():
                    if candidate_path.exists() and candidate_path.is_file():
                        full_path = candidate_path
                elif raw_work_dir:
                    work_dir = Path(raw_work_dir).expanduser()
                    for path_try in ((work_dir / candidate_path), (work_dir / candidate_path.name)):
                        if path_try.exists() and path_try.is_file():
                            full_path = path_try
                            break
                if full_path is not None:
                    sep = "\t" if full_path.suffix.lower() == ".tsv" else ","
                    try:
                        rehydrated = pd.read_csv(full_path, sep=sep, low_memory=False)
                    except Exception:
                        rehydrated = None
                    if rehydrated is not None and not rehydrated.empty:
                        return rehydrated, fname
            return pd.DataFrame(rows, columns=cols or None), fname
    return None, None


def _default_plot_export_name(chart_type: str, df_label: str, fig: go.Figure) -> str:
    title_text = str(getattr(getattr(fig.layout, "title", None), "text", "") or "").strip()
    raw_name = re.sub(r"<[^>]+>", "", title_text) if title_text else f"{df_label}_{chart_type}"
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._")
    return sanitized or f"{chart_type}_plot"


def _default_publication_settings(chart_type: str, fig: go.Figure, default_name: str) -> dict:
    width_default = int(getattr(fig.layout, "width", None) or (1200 if chart_type in {"venn", "upset"} else 1400))
    height_default = int(getattr(fig.layout, "height", None) or (850 if chart_type == "bar" else 780))
    return {
        "file_stem": default_name,
        "figure_width": width_default,
        "figure_height": height_default,
        "title_size": 16,
        "axis_title_size": 12,
        "tick_font_size": 10,
        "legend_font_size": 10,
        "legend_position": "right",
        "show_grid": True,
        "title_bold": True,
        "axis_title_bold": True,
        "x_tick_angle": "auto",
        "show_value_labels": chart_type == "bar",
        "value_label_size": 10,
        "value_label_angle": "auto",
        "value_label_bold": True,
        "export_format": "SVG",
        "export_scale": 2,
        "transparent_background": False,
    }


def _apply_publication_style(fig: go.Figure, settings: dict) -> go.Figure:
    def _plain_text(value) -> str:
        return re.sub(r"<[^>]+>", "", str(value or "")).strip()

    def _maybe_bold(value: str, is_bold: bool) -> str:
        clean = _plain_text(value)
        if not clean:
            return clean
        return f"<b>{clean}</b>" if is_bold else clean

    def _coerce_angle(raw_value, *, default_auto: int) -> int:
        if raw_value == "auto":
            return default_auto
        try:
            return int(raw_value)
        except Exception:
            return default_auto

    def _auto_x_tick_angle(figure: go.Figure) -> int:
        categories: list[str] = []
        for trace in figure.data:
            if getattr(trace, "type", None) != "bar":
                continue
            values = list(trace.x) if getattr(trace, "x", None) is not None else []
            categories.extend(str(value) for value in values)
        if not categories:
            return 0
        unique = list(dict.fromkeys(categories))
        max_len = max(len(item) for item in unique)
        if len(unique) >= 4 or max_len >= 12:
            return -30
        return 0

    def _format_bar_value_label(value, compact: bool = False) -> str:
        if value is None or pd.isna(value):
            return ""
        try:
            numeric = float(value)
        except Exception:
            return str(value)
        if compact:
            sign = "-" if numeric < 0 else ""
            abs_numeric = abs(numeric)
            if abs_numeric >= 1_000_000:
                scaled = f"{abs_numeric / 1_000_000:.1f}".rstrip("0").rstrip(".")
                return f"{sign}{scaled}M"
            if abs_numeric >= 1_000:
                scaled = f"{abs_numeric / 1_000:.1f}".rstrip("0").rstrip(".")
                return f"{sign}{scaled}k"
        if numeric.is_integer():
            return f"{int(numeric):,}"
        return f"{numeric:,.2f}".rstrip("0").rstrip(".")

    def _auto_bar_label_angle(figure: go.Figure) -> int:
        total_bars = 0
        max_len = 0
        for trace in figure.data:
            if getattr(trace, "type", None) != "bar":
                continue
            values = trace.x if getattr(trace, "orientation", None) == "h" else trace.y
            value_list = list(values) if values is not None else []
            total_bars += len(value_list)
            for value in value_list:
                max_len = max(max_len, len(_format_bar_value_label(value)))
        return 30 if total_bars >= 8 or max_len >= 7 else 0

    transparent = bool(settings.get("transparent_background"))
    paper_bg = "rgba(0,0,0,0)" if transparent else "#ffffff"
    plot_bg = "rgba(0,0,0,0)" if transparent else "#FAFAF7"
    width = int(settings.get("figure_width") or 1400)
    height = int(settings.get("figure_height") or 850)
    title_size = int(settings.get("title_size") or 24)
    axis_title_size = int(settings.get("axis_title_size") or 18)
    tick_font_size = int(settings.get("tick_font_size") or 13)
    legend_font_size = int(settings.get("legend_font_size") or 13)
    show_grid = bool(settings.get("show_grid", True))
    title_bold = bool(settings.get("title_bold", True))
    axis_title_bold = bool(settings.get("axis_title_bold", True))
    label_font_size = int(settings.get("value_label_size") or 12)
    label_bold = bool(settings.get("value_label_bold", True))
    x_tick_angle = _coerce_angle(settings.get("x_tick_angle"), default_auto=_auto_x_tick_angle(fig))

    title_text = _maybe_bold(getattr(getattr(fig.layout, "title", None), "text", ""), title_bold)
    x_title_text = _maybe_bold(getattr(getattr(getattr(fig.layout, "xaxis", None), "title", None), "text", ""), axis_title_bold)
    y_title_text = _maybe_bold(getattr(getattr(getattr(fig.layout, "yaxis", None), "title", None), "text", ""), axis_title_bold)

    legend_position = str(settings.get("legend_position") or "right").lower()
    legend_updates = {
        "font": {"size": legend_font_size, "color": "#555555"},
        "bgcolor": paper_bg,
        "borderwidth": 0,
    }
    showlegend = legend_position != "hidden"
    extra_bottom = 84
    extra_top = 90
    if legend_position == "top":
        legend_updates.update({
            "orientation": "h",
            "x": 0,
            "xanchor": "left",
            "y": 1.12,
            "yanchor": "bottom",
        })
        extra_top = 120
    elif legend_position == "bottom":
        legend_updates.update({
            "orientation": "h",
            "x": 0.5,
            "xanchor": "center",
            "y": -0.2,
            "yanchor": "top",
        })
        extra_bottom = 120
    else:
        legend_updates.update({
            "orientation": "v",
            "x": 1.02,
            "xanchor": "left",
            "y": 1,
            "yanchor": "top",
        })

    fig.update_layout(
        width=width,
        height=height,
        paper_bgcolor=paper_bg,
        plot_bgcolor=plot_bg,
        font={
            "size": tick_font_size,
            "color": "#1A1A1A",
            "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
        },
        title={
            "text": title_text,
            "x": 0.02,
            "xanchor": "left",
            "font": {
                "size": title_size,
                "color": "#1A1A1A",
                "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
            },
        },
        showlegend=showlegend,
        legend=legend_updates,
        margin={"l": 96, "r": 42, "t": extra_top, "b": extra_bottom},
    )
    fig.update_xaxes(
        title={
            "text": x_title_text,
            "font": {
                "size": axis_title_size,
                "color": "#2B2B2B",
                "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
            },
        },
        tickfont={
            "size": tick_font_size,
            "color": "#555555",
            "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
        },
        tickangle=x_tick_angle,
        showgrid=False,
        gridcolor="#E8E8E8",
        showline=True,
        linecolor="#2B2B2B",
        linewidth=1.1,
        ticks="outside",
        zeroline=False,
        automargin=True,
        title_standoff=16,
    )
    fig.update_yaxes(
        title={
            "text": y_title_text,
            "font": {
                "size": axis_title_size,
                "color": "#2B2B2B",
                "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
            },
        },
        tickfont={
            "size": tick_font_size,
            "color": "#555555",
            "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
        },
        showgrid=show_grid,
        gridcolor="#E8E8E8",
        showline=True,
        linecolor="#2B2B2B",
        linewidth=1.1,
        ticks="outside",
        zeroline=False,
        automargin=True,
        title_standoff=16,
    )

    label_family = "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif"
    stacked_modes = {"stack", "relative"}
    is_stacked = str(getattr(fig.layout, "barmode", "") or "").lower() in stacked_modes or bool(getattr(fig.layout, "barnorm", None))
    bar_traces = [trace for trace in fig.data if getattr(trace, "type", None) == "bar"]
    if bar_traces:
        is_pure_bar_chart = len(bar_traces) == len(fig.data)
        if is_pure_bar_chart:
            # Publication styling can be applied to figures that already have
            # grouped-bar label annotations from the base render path.
            # Reset them explicitly so the label layout is rebuilt once.
            fig.layout.annotations = ()
        requested_label_angle = settings.get("value_label_angle")
        label_angle = _coerce_angle(requested_label_angle, default_auto=_auto_bar_label_angle(fig))
        show_labels = bool(settings.get("show_value_labels", True))
        is_grouped_vertical = (
            show_labels
            and not is_stacked
            and len(bar_traces) > 1
            and is_pure_bar_chart
            and all(getattr(trace, "orientation", None) != "h" for trace in bar_traces)
        )
        compact_labels = is_grouped_vertical and requested_label_angle == "auto"
        effective_label_angle = 0 if compact_labels else label_angle
        annotation_specs: list[dict] = []
        tiny_label_threshold = None
        if is_grouped_vertical:
            numeric_values: list[float] = []
            for trace in bar_traces:
                values = list(trace.y) if getattr(trace, "y", None) is not None else []
                for value in values:
                    try:
                        if value is not None and not pd.isna(value):
                            numeric_values.append(abs(float(value)))
                    except Exception:
                        continue
            if numeric_values:
                tiny_label_threshold = max(numeric_values) * 0.08
        for trace_index, trace in enumerate(bar_traces):
            values = trace.x if getattr(trace, "orientation", None) == "h" else trace.y
            value_list = list(values) if values is not None else []
            if not show_labels:
                trace.text = None
                trace.texttemplate = None
                continue
            if is_grouped_vertical:
                x_values = list(trace.x) if getattr(trace, "x", None) is not None else []
                text_values: list[str] = []
                for x_value, y_value in zip(x_values, value_list):
                    label_text = _format_bar_value_label(y_value, compact=compact_labels)
                    is_tiny_label = False
                    try:
                        if tiny_label_threshold is not None and y_value is not None and not pd.isna(y_value):
                            is_tiny_label = abs(float(y_value)) <= tiny_label_threshold
                    except Exception:
                        is_tiny_label = False
                    if is_tiny_label and label_text:
                        text_values.append("")
                        annotation_specs.append(
                            {
                                "trace_index": trace_index,
                                "x": x_value,
                                "y": y_value,
                                "y_numeric": float(y_value),
                                "text": label_text,
                            }
                        )
                    else:
                        text_values.append(label_text)
                trace.text = text_values
            else:
                trace.text = [_format_bar_value_label(value) for value in value_list]
            trace.texttemplate = "%{text}"
            trace.textposition = "auto" if is_stacked else "outside"
            trace.textangle = effective_label_angle
            trace.textfont = {"color": "#1f2937", "size": label_font_size, "family": label_family}
            trace.cliponaxis = False if not is_stacked else True

        margin_left = float(getattr(getattr(fig.layout, "margin", None), "l", 96) or 96)
        margin_right = float(getattr(getattr(fig.layout, "margin", None), "r", 36) or 36)
        margin_top = int(getattr(getattr(fig.layout, "margin", None), "t", extra_top) or extra_top)
        margin_bottom = int(getattr(getattr(fig.layout, "margin", None), "b", extra_bottom) or extra_bottom)
        if annotation_specs:
            category_values: list[str] = []
            for trace in bar_traces:
                x_values = list(trace.x) if getattr(trace, "x", None) is not None else []
                category_values.extend(str(value) for value in x_values)
            category_count = len(list(dict.fromkeys(category_values))) or 1
            usable_width = max(width - margin_left - margin_right, 320)
            slot_width = max(min((usable_width / category_count) * 0.72 / max(len(bar_traces), 1), 40), 12)
            center_offset = (len(bar_traces) - 1) / 2
            label_padding = max(14, label_font_size + 4)
            tier_step = max(12, label_font_size + 2)
            grouped_specs: dict[tuple[str, int], list[dict]] = {}
            for spec in annotation_specs:
                direction = -1 if spec["y_numeric"] < 0 else 1
                grouped_specs.setdefault((str(spec["x"]), direction), []).append({**spec, "direction": direction})
            max_tier = 0
            uses_angled_annotations = False
            for specs in grouped_specs.values():
                specs.sort(key=lambda item: abs(item["y_numeric"]))
                max_label_len = max(len(spec["text"]) for spec in specs)
                annotation_angle = effective_label_angle
                if requested_label_angle == "auto":
                    annotation_angle = -30 if len(specs) >= 3 or max_label_len >= 5 else 0
                if annotation_angle != 0:
                    uses_angled_annotations = True
                for tier_rank, spec in enumerate(specs):
                    max_tier = max(max_tier, tier_rank)
                    fig.add_annotation(
                        x=spec["x"],
                        y=spec["y"],
                        text=spec["text"],
                        showarrow=False,
                        xanchor="center",
                        yanchor="top" if spec["direction"] < 0 else "bottom",
                        xshift=int(round((spec["trace_index"] - center_offset) * slot_width)),
                        yshift=spec["direction"] * (label_padding + (tier_rank * tier_step)),
                        textangle=annotation_angle,
                        font={"color": "#1f2937", "size": label_font_size, "family": label_family},
                        align="center",
                    )
            margin_top += label_padding + (tier_step * max_tier) + (28 if uses_angled_annotations else 18)
        elif compact_labels:
            margin_top += 18
        fig.update_layout(
            margin={
                "l": margin_left,
                "r": margin_right,
                "t": margin_top,
                "b": margin_bottom,
            },
            uniformtext_minsize=label_font_size,
            uniformtext_mode="show",
        )

    for annotation in getattr(fig.layout, "annotations", []) or []:
        annotation_font = getattr(annotation, "font", None)
        if hasattr(annotation_font, "to_plotly_json"):
            current_font = dict(annotation_font.to_plotly_json() or {})
        elif isinstance(annotation_font, dict):
            current_font = dict(annotation_font)
        else:
            current_font = {}
        current_font.setdefault("color", "#1f2937")
        current_font["size"] = max(label_font_size, current_font.get("size", label_font_size))
        current_font.setdefault("family", label_family)
        annotation.font = current_font

    return fig


def _build_plot_download_payload(fig: go.Figure, settings: dict) -> tuple[bytes | None, str, str | None]:
    export_format = str(settings.get("export_format") or "PNG").strip().upper()
    file_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(settings.get("file_stem") or "plot")).strip("._") or "plot"
    if export_format == "HTML":
        return fig.to_html(include_plotlyjs="cdn").encode("utf-8"), f"{file_stem}.html", None

    try:
        import plotly.io as pio

        image_bytes = pio.to_image(
            fig,
            format=export_format.lower(),
            width=int(settings.get("figure_width") or 1400),
            height=int(settings.get("figure_height") or 850),
            scale=int(settings.get("export_scale") or 2),
        )
        return image_bytes, f"{file_stem}.{export_format.lower()}", None
    except Exception as exc:
        return None, "", str(exc)


def _get_plot_display_dimensions(fig: go.Figure) -> tuple[int, int]:
    layout = getattr(fig, "layout", None)
    width = getattr(layout, "width", None) if layout is not None else None
    height = getattr(layout, "height", None) if layout is not None else None
    return int(width or 1400), int(height or 850)


def _render_plot_publication_tools(fig: go.Figure, plot_key_base: str, chart_type: str, default_name: str) -> tuple[go.Figure, dict]:
    defaults = _default_publication_settings(chart_type, fig, default_name)
    session_state = st.session_state
    applied_settings_key = f"{plot_key_base}_publication_applied_settings"
    download_payload_key = f"{plot_key_base}_publication_download_payload"
    download_name_key = f"{plot_key_base}_publication_download_name"
    download_mime_key = f"{plot_key_base}_publication_download_mime"
    download_error_key = f"{plot_key_base}_publication_download_error"

    applied_settings = dict(defaults)
    applied_settings.update(dict(session_state.get(applied_settings_key) or {}))

    widget_defaults = {
        "file_stem": applied_settings["file_stem"],
        "figure_width": applied_settings["figure_width"],
        "figure_height": applied_settings["figure_height"],
        "title_size": applied_settings["title_size"],
        "axis_title_size": applied_settings["axis_title_size"],
        "tick_font_size": applied_settings["tick_font_size"],
        "legend_font_size": applied_settings["legend_font_size"],
        "legend_position": applied_settings["legend_position"],
        "x_tick_angle": applied_settings["x_tick_angle"],
        "show_grid": applied_settings["show_grid"],
        "title_bold": applied_settings["title_bold"],
        "axis_title_bold": applied_settings["axis_title_bold"],
        "transparent_background": applied_settings["transparent_background"],
        "export_format": applied_settings["export_format"],
        "export_scale": applied_settings["export_scale"],
        "show_value_labels": applied_settings["show_value_labels"],
        "value_label_size": applied_settings["value_label_size"],
        "value_label_angle": applied_settings["value_label_angle"],
        "value_label_bold": applied_settings["value_label_bold"],
    }
    for field_name, field_value in widget_defaults.items():
        session_state.setdefault(f"{plot_key_base}_{field_name}", field_value)

    preview_fig = _apply_publication_style(go.Figure(fig), applied_settings)
    with st.popover("Publication settings"):
        st.caption("Tune the figure for publication. Preview changes apply only when you click Update preview.")
        with st.form(key=f"{plot_key_base}_publication_form"):
            col_left, col_right = st.columns(2)
            with col_left:
                file_stem = st.text_input("Filename", value=session_state[f"{plot_key_base}_file_stem"], key=f"{plot_key_base}_file_stem")
                figure_width = st.number_input("Width (px)", min_value=400, max_value=4000, value=session_state[f"{plot_key_base}_figure_width"], step=50, key=f"{plot_key_base}_figure_width")
                figure_height = st.number_input("Height (px)", min_value=300, max_value=3000, value=session_state[f"{plot_key_base}_figure_height"], step=50, key=f"{plot_key_base}_figure_height")
                title_size = st.number_input("Title size", min_value=10, max_value=64, value=session_state[f"{plot_key_base}_title_size"], step=1, key=f"{plot_key_base}_title_size")
                axis_title_size = st.number_input("Axis title size", min_value=8, max_value=48, value=session_state[f"{plot_key_base}_axis_title_size"], step=1, key=f"{plot_key_base}_axis_title_size")
                tick_font_size = st.number_input("Tick label size", min_value=8, max_value=32, value=session_state[f"{plot_key_base}_tick_font_size"], step=1, key=f"{plot_key_base}_tick_font_size")
                legend_font_size = st.number_input("Legend size", min_value=8, max_value=32, value=session_state[f"{plot_key_base}_legend_font_size"], step=1, key=f"{plot_key_base}_legend_font_size")
            with col_right:
                legend_position = st.selectbox("Legend", ["right", "top", "bottom", "hidden"], index=["right", "top", "bottom", "hidden"].index(str(session_state[f"{plot_key_base}_legend_position"])), key=f"{plot_key_base}_legend_position")
                x_tick_angle = st.selectbox("X tick angle", ["auto", "0", "-30", "-45", "-60", "90"], index=["auto", "0", "-30", "-45", "-60", "90"].index(str(session_state[f"{plot_key_base}_x_tick_angle"])), key=f"{plot_key_base}_x_tick_angle")
                show_grid = st.checkbox("Show grid lines", value=bool(session_state[f"{plot_key_base}_show_grid"]), key=f"{plot_key_base}_show_grid")
                title_bold = st.checkbox("Bold title", value=bool(session_state[f"{plot_key_base}_title_bold"]), key=f"{plot_key_base}_title_bold")
                axis_title_bold = st.checkbox("Bold axis titles", value=bool(session_state[f"{plot_key_base}_axis_title_bold"]), key=f"{plot_key_base}_axis_title_bold")
                transparent_background = st.checkbox("Transparent background", value=bool(session_state[f"{plot_key_base}_transparent_background"]), key=f"{plot_key_base}_transparent_background")
                export_format = st.selectbox("Download format", ["PNG", "SVG", "PDF", "HTML"], index=["PNG", "SVG", "PDF", "HTML"].index(str(session_state[f"{plot_key_base}_export_format"])), key=f"{plot_key_base}_export_format")
                export_scale = st.number_input("Export scale", min_value=1, max_value=6, value=session_state[f"{plot_key_base}_export_scale"], step=1, key=f"{plot_key_base}_export_scale")

            show_value_labels = bool(session_state[f"{plot_key_base}_show_value_labels"])
            value_label_size = session_state[f"{plot_key_base}_value_label_size"]
            value_label_angle = str(session_state[f"{plot_key_base}_value_label_angle"])
            value_label_bold = bool(session_state[f"{plot_key_base}_value_label_bold"])
            if chart_type == "bar":
                st.divider()
                st.caption("Bar label formatting")
                label_col1, label_col2 = st.columns(2)
                with label_col1:
                    show_value_labels = st.checkbox("Show value labels", value=bool(session_state[f"{plot_key_base}_show_value_labels"]), key=f"{plot_key_base}_show_value_labels")
                    value_label_size = st.number_input("Bar label size", min_value=8, max_value=32, value=session_state[f"{plot_key_base}_value_label_size"], step=1, key=f"{plot_key_base}_value_label_size")
                with label_col2:
                    value_label_angle = st.selectbox("Bar label angle", ["auto", "0", "30", "45", "60", "90", "-30", "-45", "-60", "-90"], index=["auto", "0", "30", "45", "60", "90", "-30", "-45", "-60", "-90"].index(str(session_state[f"{plot_key_base}_value_label_angle"])), key=f"{plot_key_base}_value_label_angle")
                    value_label_bold = st.checkbox("Bold value labels", value=bool(session_state[f"{plot_key_base}_value_label_bold"]), key=f"{plot_key_base}_value_label_bold")

            settings = {
                "file_stem": file_stem,
                "figure_width": int(figure_width),
                "figure_height": int(figure_height),
                "title_size": int(title_size),
                "axis_title_size": int(axis_title_size),
                "tick_font_size": int(tick_font_size),
                "legend_font_size": int(legend_font_size),
                "legend_position": legend_position,
                "show_grid": bool(show_grid),
                "title_bold": bool(title_bold),
                "axis_title_bold": bool(axis_title_bold),
                "x_tick_angle": x_tick_angle,
                "show_value_labels": bool(show_value_labels),
                "value_label_size": int(value_label_size),
                "value_label_angle": value_label_angle,
                "value_label_bold": bool(value_label_bold),
                "export_format": export_format,
                "export_scale": int(export_scale),
                "transparent_background": bool(transparent_background),
            }

            action_col1, action_col2 = st.columns(2)
            with action_col1:
                update_preview = st.form_submit_button("Update preview", type="primary")
            with action_col2:
                prepare_download = st.form_submit_button("Prepare download")

        if update_preview or prepare_download:
            applied_settings = dict(settings)
            session_state[applied_settings_key] = dict(applied_settings)
            preview_fig = _apply_publication_style(go.Figure(fig), applied_settings)
            if update_preview and not prepare_download:
                session_state.pop(download_payload_key, None)
                session_state.pop(download_name_key, None)
                session_state.pop(download_mime_key, None)
                session_state.pop(download_error_key, None)

        if prepare_download:
            payload_bytes, file_name, export_error = _build_plot_download_payload(go.Figure(preview_fig), applied_settings)
            if export_error:
                session_state.pop(download_payload_key, None)
                session_state.pop(download_name_key, None)
                session_state.pop(download_mime_key, None)
                session_state[download_error_key] = str(export_error)
            elif payload_bytes is not None:
                mime_map = {
                    "png": "image/png",
                    "svg": "image/svg+xml",
                    "pdf": "application/pdf",
                    "html": "text/html",
                }
                ext = file_name.rsplit(".", 1)[-1].lower()
                session_state[download_payload_key] = payload_bytes
                session_state[download_name_key] = file_name
                session_state[download_mime_key] = mime_map.get(ext, "application/octet-stream")
                session_state.pop(download_error_key, None)

        export_error = session_state.get(download_error_key)
        prepared_payload = session_state.get(download_payload_key)
        prepared_name = session_state.get(download_name_key)
        prepared_mime = session_state.get(download_mime_key)
        if export_error:
            st.caption(f"Export unavailable: {export_error}")
        elif prepared_payload is not None and prepared_name:
            st.download_button(
                f"Download {applied_settings['export_format']}",
                data=prepared_payload,
                file_name=prepared_name,
                mime=prepared_mime or "application/octet-stream",
                key=f"{plot_key_base}_download",
            )
            st.caption("SVG and PDF preserve vector text for publication. PNG uses the export scale above.")

    return preview_fig, applied_settings


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
    from itertools import product

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
    template_layout = plotly_template.get("layout", {}) if isinstance(plotly_template, dict) else {}
    template_colorway = list(template_layout.get("colorway") or [])

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

    def _parse_multi_value_param(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            candidates = value
        else:
            raw_value = str(value).strip()
            if raw_value.startswith("[") and raw_value.endswith("]"):
                raw_value = raw_value[1:-1]
            candidates = re.split(r"[|;,]", raw_value)
        normalized = []
        for item in candidates:
            cleaned = str(item).strip()
            if cleaned.startswith("["):
                cleaned = cleaned[1:]
            if cleaned.endswith("]"):
                cleaned = cleaned[:-1]
            cleaned = cleaned.strip().strip('"').strip("'")
            if cleaned:
                normalized.append(cleaned)
        return normalized

    def _coerce_membership_series(
        series: pd.Series,
        *,
        allow_positive_numeric: bool = False,
    ) -> pd.Series | None:
        if pd.api.types.is_bool_dtype(series):
            return series.fillna(False).astype(bool)

        non_null = series.dropna()
        if non_null.empty:
            return None

        numeric = pd.to_numeric(non_null, errors="coerce")
        if numeric.notna().all():
            if set(numeric.unique()).issubset({0, 1}):
                return pd.to_numeric(series, errors="coerce").fillna(0).astype(int).astype(bool)
            if allow_positive_numeric:
                has_positive = bool((numeric > 0).any())
                has_negative = bool((numeric < 0).any())
                if has_positive and not has_negative:
                    return pd.to_numeric(series, errors="coerce").fillna(0).gt(0)
            return None

        true_tokens = {"1", "true", "t", "yes", "y", "present", "member", "in"}
        false_tokens = {"0", "false", "f", "no", "n", "absent", "out"}
        normalized = non_null.astype(str).str.strip().str.lower()
        if set(normalized.unique()).issubset(true_tokens | false_tokens):
            return series.fillna("false").astype(str).str.strip().str.lower().map(lambda value: value in true_tokens)

        return None

    def _infer_set_columns_local(df_plot_local: pd.DataFrame, *, max_count: int) -> list[str]:
        inferred: list[str] = []
        for column in df_plot_local.columns:
            if _coerce_membership_series(df_plot_local[column]) is not None:
                inferred.append(column)
            if len(inferred) >= max_count:
                break
        return inferred

    def _resolve_set_columns_local(
        df_plot_local: pd.DataFrame,
        requested_sets_value,
        *,
        min_count: int,
        max_count: int,
    ) -> list[str]:
        requested = _parse_multi_value_param(requested_sets_value)
        resolved: list[str] = []
        if requested:
            for item in requested:
                matched = _match_column_name_local(list(df_plot_local.columns), item)
                if matched and matched not in resolved:
                    resolved.append(matched)
            return resolved[:max_count]

        inferred = _infer_set_columns_local(df_plot_local, max_count=max_count)
        if len(inferred) >= min_count:
            return inferred
        return []

    def _build_membership_frame_local(
        df_plot_local: pd.DataFrame,
        requested_sets_value,
        *,
        min_count: int,
        max_count: int,
    ):
        requested_sets = _parse_multi_value_param(requested_sets_value)
        set_columns = _resolve_set_columns_local(
            df_plot_local,
            requested_sets_value,
            min_count=min_count,
            max_count=max_count,
        )
        if len(set_columns) < min_count:
            return None, []

        membership = {}
        for column in set_columns:
            coerced = _coerce_membership_series(
                df_plot_local[column],
                allow_positive_numeric=bool(requested_sets),
            )
            if coerced is None:
                return None, []
            membership[column] = coerced
        return pd.DataFrame(membership), set_columns

    def _combination_weight_local(membership_frame: pd.DataFrame, weight_series: pd.Series | None) -> dict[tuple[bool, ...], float]:
        counts: dict[tuple[bool, ...], float] = {}
        for combo in product([False, True], repeat=membership_frame.shape[1]):
            if not any(combo):
                continue
            mask = membership_frame.eq(combo).all(axis=1)
            if weight_series is None:
                count_value = float(mask.sum())
            else:
                count_value = float(weight_series[mask].sum())
            counts[combo] = count_value
        return counts

    def _default_overlap_colors_local(count: int) -> list[str]:
        base = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#B279A2"]
        return base[:count]

    def _resolve_overlap_colors_local(count: int) -> list[str]:
        if palette_values:
            return [palette_values[idx % len(palette_values)] for idx in range(count)]
        if literal_color:
            return [literal_color for _ in range(count)]
        return _default_overlap_colors_local(count)

    def _format_bar_value_label(value, compact: bool = False) -> str:
        if value is None or pd.isna(value):
            return ""
        try:
            numeric = float(value)
        except Exception:
            return str(value)
        if compact:
            sign = "-" if numeric < 0 else ""
            abs_numeric = abs(numeric)
            if abs_numeric >= 1_000_000:
                scaled = f"{abs_numeric / 1_000_000:.1f}".rstrip("0").rstrip(".")
                return f"{sign}{scaled}M"
            if abs_numeric >= 1_000:
                scaled = f"{abs_numeric / 1_000:.1f}".rstrip("0").rstrip(".")
                return f"{sign}{scaled}k"
        if numeric.is_integer():
            return f"{int(numeric):,}"
        return f"{numeric:,.2f}".rstrip("0").rstrip(".")

    def _auto_bar_label_angle(figure: go.Figure) -> int:
        total_bars = 0
        max_label_len = 0
        for trace in figure.data:
            if getattr(trace, "type", None) != "bar":
                continue
            values = trace.x if getattr(trace, "orientation", None) == "h" else trace.y
            value_list = list(values) if values is not None else []
            total_bars += len(value_list)
            for value in value_list:
                max_label_len = max(max_label_len, len(_format_bar_value_label(value)))
        return 45 if total_bars >= 8 or max_label_len >= 7 else 0

    def _apply_bar_value_labels(figure: go.Figure, *, chart_mode_value: str) -> None:
        if not figure or not getattr(figure, "data", None):
            return
        label_angle = _auto_bar_label_angle(figure)
        stacked_modes = {"stack", "stacked", "relative", "percent", "percentage", "normalize", "normalized"}
        label_position = "auto" if chart_mode_value in stacked_modes else "outside"
        label_color = str((template_layout.get("font") or {}).get("color") or "#1f2937")
        bar_traces = [trace for trace in figure.data if getattr(trace, "type", None) == "bar"]
        is_grouped_vertical = (
            label_position == "outside"
            and len(bar_traces) > 1
            and all(getattr(trace, "orientation", None) != "h" for trace in bar_traces)
        )
        tiny_label_threshold = None
        annotation_specs: list[dict] = []
        label_font_size = 9 if is_grouped_vertical else 10
        if is_grouped_vertical:
            label_angle = 0
            numeric_values: list[float] = []
            for trace in bar_traces:
                values = list(trace.y) if getattr(trace, "y", None) is not None else []
                for value in values:
                    try:
                        if value is not None and not pd.isna(value):
                            numeric_values.append(abs(float(value)))
                    except Exception:
                        continue
            if numeric_values:
                tiny_label_threshold = max(numeric_values) * 0.08
        for trace_index, trace in enumerate(bar_traces):
            values = trace.x if getattr(trace, "orientation", None) == "h" else trace.y
            value_list = list(values) if values is not None else []
            if is_grouped_vertical:
                x_values = list(trace.x) if getattr(trace, "x", None) is not None else []
                text_values: list[str] = []
                for x_value, y_value in zip(x_values, value_list):
                    label_text = _format_bar_value_label(y_value, compact=True)
                    is_tiny_label = False
                    try:
                        if tiny_label_threshold is not None and y_value is not None and not pd.isna(y_value):
                            is_tiny_label = abs(float(y_value)) <= tiny_label_threshold
                    except Exception:
                        is_tiny_label = False
                    if is_tiny_label and label_text:
                        text_values.append("")
                        annotation_specs.append(
                            {
                                "trace_index": trace_index,
                                "x": x_value,
                                "y": y_value,
                                "y_numeric": float(y_value),
                                "text": label_text,
                            }
                        )
                    else:
                        text_values.append(label_text)
                trace.text = text_values
            else:
                trace.text = [_format_bar_value_label(value) for value in value_list]
            trace.texttemplate = "%{text}"
            trace.textposition = label_position
            trace.textangle = label_angle
            trace.textfont = {"color": label_color, "size": label_font_size}
            trace.cliponaxis = False if label_position == "outside" else True
        current_margin = getattr(figure.layout, "margin", None)
        margin_left = float(getattr(current_margin, "l", 84) or 84)
        margin_right = float(getattr(current_margin, "r", 24) or 24)
        margin_top = int(getattr(current_margin, "t", 72) or 72)
        margin_bottom = int(getattr(current_margin, "b", 72) or 72)
        if annotation_specs:
            plot_width = float(getattr(figure.layout, "width", None) or 1100)
            category_values: list[str] = []
            for trace in bar_traces:
                x_values = list(trace.x) if getattr(trace, "x", None) is not None else []
                category_values.extend(str(value) for value in x_values)
            category_count = len(list(dict.fromkeys(category_values))) or 1
            usable_width = max(plot_width - margin_left - margin_right, 320)
            slot_width = max(min((usable_width / category_count) * 0.72 / max(len(bar_traces), 1), 38), 12)
            center_offset = (len(bar_traces) - 1) / 2
            label_padding = max(14, label_font_size + 4)
            tier_step = max(12, label_font_size + 2)
            grouped_specs: dict[tuple[str, int], list[dict]] = {}
            for spec in annotation_specs:
                direction = -1 if spec["y_numeric"] < 0 else 1
                grouped_specs.setdefault((str(spec["x"]), direction), []).append({**spec, "direction": direction})
            max_tier = 0
            uses_angled_annotations = False
            for specs in grouped_specs.values():
                specs.sort(key=lambda item: abs(item["y_numeric"]))
                max_label_len = max(len(spec["text"]) for spec in specs)
                annotation_angle = -90 if len(specs) >= 3 or max_label_len >= 5 else 0
                if annotation_angle != 0:
                    uses_angled_annotations = True
                for tier_rank, spec in enumerate(specs):
                    max_tier = max(max_tier, tier_rank)
                    figure.add_annotation(
                        x=spec["x"],
                        y=spec["y"],
                        text=spec["text"],
                        showarrow=False,
                        xanchor="center",
                        yanchor="top" if spec["direction"] < 0 else "bottom",
                        xshift=int(round((spec["trace_index"] - center_offset) * slot_width)),
                        yshift=spec["direction"] * (label_padding + (tier_rank * tier_step)),
                        textangle=annotation_angle,
                        font={"color": label_color, "size": label_font_size},
                        align="center",
                    )
            margin_top += label_padding + (tier_step * max_tier) + (28 if uses_angled_annotations else 18)
        figure.update_layout(
            margin={
                "l": margin_left,
                "r": margin_right,
                "t": margin_top + (18 if is_grouped_vertical and not annotation_specs else 0),
                "b": margin_bottom,
            },
            uniformtext_minsize=label_font_size,
            uniformtext_mode="show",
        )

    palette_values = _parse_palette_values(palette)
    if palette_values and len(palette_values) == 1 and _looks_like_literal_color(palette_values[0]):
        literal_color = palette_values[0]

    available = list(df.columns)
    requested_color_col = color_col
    sets_value = chart_spec.get("sets")
    chart_mode = str(chart_spec.get("mode") or chart_spec.get("barmode") or "").strip().lower()

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
        elif template_colorway:
            px_color_kwargs["color_discrete_sequence"] = template_colorway
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

        elif chart_type == "line":
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns.tolist()
                x_col = cat_cols[0] if cat_cols else available[0]
            if not y_col:
                num_cols = [col for col in df_plot.select_dtypes(include="number").columns.tolist() if col != x_col]
                if len(num_cols) > 1 and x_col in df_plot.columns and not color_col:
                    df_plot = pd.melt(
                        df_plot,
                        id_vars=[x_col],
                        value_vars=num_cols,
                        var_name="measure",
                        value_name="value",
                    )
                    y_col = "value"
                    color_col = "measure"
                    px_color_kwargs["color"] = color_col
                elif num_cols:
                    y_col = num_cols[0]
                else:
                    return None
            fig = px.line(
                df_plot,
                x=x_col,
                y=y_col,
                **px_color_kwargs,
                title=title or f"{y_col} by {x_col}",
                markers=True,
            )

        elif chart_type == "area":
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns.tolist()
                x_col = cat_cols[0] if cat_cols else available[0]
            if not y_col:
                num_cols = [col for col in df_plot.select_dtypes(include="number").columns.tolist() if col != x_col]
                if len(num_cols) > 1 and x_col in df_plot.columns and not color_col:
                    df_plot = pd.melt(
                        df_plot,
                        id_vars=[x_col],
                        value_vars=num_cols,
                        var_name="measure",
                        value_name="value",
                    )
                    y_col = "value"
                    color_col = "measure"
                    px_color_kwargs["color"] = color_col
                elif num_cols:
                    y_col = num_cols[0]
                else:
                    return None
            fig = px.area(df_plot, x=x_col, y=y_col, **px_color_kwargs, title=title or f"{y_col} by {x_col}")

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

        elif chart_type == "violin":
            if not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                y_col = num_cols[0] if len(num_cols) > 0 else None
            if not y_col:
                return None
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns.tolist()
                x_col = cat_cols[0] if cat_cols else None
            fig = px.violin(
                df_plot,
                x=x_col,
                y=y_col,
                **px_color_kwargs,
                box=True,
                points="all",
                title=title or f"Distribution of {y_col}" + (f" by {x_col}" if x_col else ""),
            )

        elif chart_type == "strip":
            if not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                y_col = num_cols[0] if len(num_cols) > 0 else None
            if not y_col:
                return None
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns.tolist()
                x_col = cat_cols[0] if cat_cols else None
            fig = px.strip(
                df_plot,
                x=x_col,
                y=y_col,
                **px_color_kwargs,
                title=title or f"Individual values for {y_col}" + (f" by {x_col}" if x_col else ""),
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

        elif chart_type == "venn":
            membership_frame, set_columns = _build_membership_frame_local(
                df_plot,
                sets_value,
                min_count=2,
                max_count=3,
            )
            if membership_frame is None or membership_frame.shape[1] not in {2, 3}:
                return None
            requested_labels = _parse_multi_value_param(chart_spec.get("labels"))
            display_labels = requested_labels if len(requested_labels) == len(set_columns) else set_columns

            weight_series = None
            if y_col and y_col in df_plot.columns:
                numeric_weight = pd.to_numeric(df_plot[y_col], errors="coerce")
                if numeric_weight.notna().any():
                    weight_series = numeric_weight.fillna(0)

            combo_counts = _combination_weight_local(membership_frame, weight_series)
            colors = _resolve_overlap_colors_local(len(set_columns))
            fig = go.Figure()

            if len(set_columns) == 2:
                circles = [
                    (0.0, 0.2, 2.8, 3.0, set_columns[0], display_labels[0], colors[0]),
                    (1.8, 0.2, 4.6, 3.0, set_columns[1], display_labels[1], colors[1]),
                ]
                count_positions = {
                    (True, False): (0.95, 1.6),
                    (False, True): (3.65, 1.6),
                    (True, True): (2.3, 1.6),
                }
                label_positions = [(1.0, 3.28), (3.6, 3.28)]
                x_range = [-0.35, 4.95]
                y_range = [0.0, 3.65]
            else:
                circles = [
                    (0.3, 1.0, 2.1, 2.8, set_columns[0], display_labels[0], colors[0]),
                    (1.3, 1.0, 3.1, 2.8, set_columns[1], display_labels[1], colors[1]),
                    (0.8, 0.1, 2.6, 1.9, set_columns[2], display_labels[2], colors[2]),
                ]
                count_positions = {
                    (True, False, False): (0.95, 2.0),
                    (False, True, False): (2.45, 2.0),
                    (False, False, True): (1.7, 0.55),
                    (True, True, False): (1.7, 2.05),
                    (True, False, True): (1.2, 1.15),
                    (False, True, True): (2.2, 1.15),
                    (True, True, True): (1.7, 1.45),
                }
                label_positions = [(0.85, 3.0), (2.55, 3.0), (1.7, -0.02)]
                x_range = [0.0, 3.4]
                y_range = [-0.2, 3.2]

            for idx, (x0, y0, x1, y1, column_name, display_label, color_value) in enumerate(circles):
                fig.add_shape(
                    type="circle",
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    line={"color": color_value, "width": 3},
                    fillcolor=color_value,
                    opacity=0.32,
                    layer="below",
                )
                fig.add_annotation(
                    x=label_positions[idx][0],
                    y=label_positions[idx][1],
                    text=(
                        f"{display_label}<br>n={int(membership_frame[column_name].sum()) if weight_series is None else int(weight_series[membership_frame[column_name]].sum()):,}"
                    ),
                    showarrow=False,
                    font={"size": 18},
                )

            for combo, (x_pos, y_pos) in count_positions.items():
                fig.add_annotation(
                    x=x_pos,
                    y=y_pos,
                    text=f"{int(combo_counts.get(combo, 0)):,}",
                    showarrow=False,
                    font={"size": 28},
                )

            fig.add_trace(
                go.Scatter(
                    x=[x_range[0], x_range[1]],
                    y=[y_range[0], y_range[1]],
                    mode="markers",
                    marker={"opacity": 0},
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
            fig.update_layout(
                title=title or "Set overlap",
                xaxis={"visible": False, "range": x_range},
                yaxis={"visible": False, "range": y_range, "scaleanchor": "x", "scaleratio": 1},
                showlegend=False,
            )

        elif chart_type == "upset":
            from plotly.subplots import make_subplots

            membership_frame, set_columns = _build_membership_frame_local(
                df_plot,
                sets_value,
                min_count=2,
                max_count=6,
            )
            if membership_frame is None or membership_frame.shape[1] < 2:
                return None

            weight_series = None
            if y_col and y_col in df_plot.columns:
                numeric_weight = pd.to_numeric(df_plot[y_col], errors="coerce")
                if numeric_weight.notna().any():
                    weight_series = numeric_weight.fillna(0)

            combo_counts = _combination_weight_local(membership_frame, weight_series)
            combo_items = list(combo_counts.items())
            if not combo_items:
                return None
            combo_items.sort(key=lambda item: (-item[1], -sum(item[0]), item[0]))
            max_intersections = chart_spec.get("max_intersections")
            try:
                max_intersections = int(max_intersections) if max_intersections is not None else 12
            except Exception:
                max_intersections = 12
            combo_items = combo_items[:max(1, max_intersections)]

            shared_prefix = str(set_columns[0]) if set_columns else ""
            for set_name in set_columns[1:]:
                set_name = str(set_name)
                while shared_prefix and not set_name.startswith(shared_prefix):
                    shared_prefix = shared_prefix[:-1]
            shared_prefix = shared_prefix.rstrip("_- ")
            if len(shared_prefix) < 3:
                shared_prefix = ""
            display_set_names = []
            for set_name in set_columns:
                label = str(set_name)
                if shared_prefix and label.startswith(shared_prefix):
                    compact = label[len(shared_prefix):].lstrip("_- ")
                    if compact.isdigit():
                        trailing_alpha = re.search(r"([A-Za-z]+)$", shared_prefix)
                        if trailing_alpha:
                            compact = f"{trailing_alpha.group(1)}{compact}"
                    label = compact or label
                display_set_names.append(label)
            display_label_map = dict(zip(set_columns, display_set_names))

            combo_labels = []
            combo_values = []
            combo_text = []
            active_points_x = []
            active_points_y = []
            inactive_points_x = []
            inactive_points_y = []
            connector_x = []
            connector_y = []
            for idx, (combo, count_value) in enumerate(combo_items):
                active_sets = [display_label_map[set_columns[pos]] for pos, flag in enumerate(combo) if flag]
                combo_labels.append(" ∩ ".join(active_sets) if active_sets else "None")
                combo_values.append(count_value)
                combo_text.append(str(int(count_value)) if float(count_value).is_integer() else f"{count_value:g}")
                active_indices = [pos for pos, flag in enumerate(combo) if flag]
                for set_idx, set_name in enumerate(display_set_names):
                    if set_idx in active_indices:
                        active_points_x.append(idx)
                        active_points_y.append(set_name)
                    else:
                        inactive_points_x.append(idx)
                        inactive_points_y.append(set_name)
                if len(active_indices) > 1:
                    connector_x.extend([idx] * len(active_indices) + [None])
                    connector_y.extend([display_set_names[pos] for pos in active_indices] + [None])

            accent_color = "#2E5A87"
            muted_color = "rgba(46, 90, 135, 0.82)"
            edge_color = "#244766"
            bar_colors = [accent_color if idx == 0 else muted_color for idx in range(len(combo_items))]
            fig = make_subplots(
                rows=2,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.04,
                row_heights=[0.62, 0.38],
            )
            fig.add_trace(
                go.Bar(
                    x=list(range(len(combo_items))),
                    y=combo_values,
                    width=0.76,
                    marker={
                        "color": bar_colors,
                        "line": {"color": [edge_color] * len(combo_items), "width": 0.5},
                    },
                    text=combo_text,
                    textposition="outside",
                    textfont={
                        "size": 10,
                        "color": "#2B2B2B",
                        "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                    },
                    cliponaxis=False,
                    showlegend=False,
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=inactive_points_x,
                    y=inactive_points_y,
                    mode="markers",
                    marker={"color": "#E8E8E8", "size": 10},
                    hoverinfo="skip",
                    showlegend=False,
                ),
                row=2,
                col=1,
            )
            if connector_x:
                fig.add_trace(
                    go.Scatter(
                        x=connector_x,
                        y=connector_y,
                        mode="lines",
                        line={"color": accent_color, "width": 1.5},
                        hoverinfo="skip",
                        showlegend=False,
                    ),
                    row=2,
                    col=1,
                )
            fig.add_trace(
                go.Scatter(
                    x=active_points_x,
                    y=active_points_y,
                    mode="markers",
                    marker={"color": accent_color, "size": 12},
                    hoverinfo="skip",
                    showlegend=False,
                ),
                row=2,
                col=1,
            )
            tick_angle = -30 if len(combo_labels) >= 4 or max((len(label) for label in combo_labels), default=0) >= 12 else 0
            fig.update_xaxes(
                row=1,
                col=1,
                showgrid=False,
                showticklabels=False,
                zeroline=False,
                showline=True,
                linecolor="#2B2B2B",
                linewidth=1.1,
                ticks="outside",
            )
            fig.update_xaxes(
                row=2,
                col=1,
                tickmode="array",
                tickvals=list(range(len(combo_items))),
                ticktext=combo_labels,
                tickangle=tick_angle,
                tickfont={
                    "size": 10,
                    "color": "#555555",
                    "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                },
                showgrid=False,
                zeroline=False,
                showline=True,
                linecolor="#2B2B2B",
                linewidth=1.1,
                ticks="outside",
                automargin=True,
            )
            fig.update_yaxes(
                row=1,
                col=1,
                title_text=y_axis_label or (y_col if y_col else "Intersection size"),
                title_font={
                    "size": 12,
                    "color": "#2B2B2B",
                    "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                },
                tickfont={
                    "size": 10,
                    "color": "#555555",
                    "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                },
                showgrid=True,
                gridcolor="#E8E8E8",
                gridwidth=0.8,
                zeroline=False,
                showline=True,
                linecolor="#2B2B2B",
                linewidth=1.1,
                ticks="outside",
                rangemode="tozero",
                automargin=True,
            )
            fig.update_yaxes(
                row=2,
                col=1,
                categoryorder="array",
                categoryarray=list(reversed(display_set_names)),
                tickfont={
                    "size": 11,
                    "color": "#555555",
                    "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                },
                showgrid=False,
                zeroline=False,
                showline=False,
                ticks="",
                automargin=True,
            )
            fig.update_layout(
                title={
                    "text": title or "UpSet plot",
                    "x": 0.02,
                    "xanchor": "left",
                    "font": {
                        "size": 16,
                        "color": "#1A1A1A",
                        "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                    },
                },
                paper_bgcolor="#FFFFFF",
                plot_bgcolor="#FFFFFF",
                font={
                    "size": 11,
                    "color": "#1A1A1A",
                    "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                },
                margin={"l": 112, "r": 32, "t": 86, "b": 112},
                showlegend=False,
            )
        else:
            return None

        if literal_color:
            if chart_type == "pie":
                for trace in fig.data:
                    labels = getattr(trace, "labels", None)
                    trace.marker.colors = [literal_color] * len(labels or [1])
            elif chart_type in {"venn", "upset"}:
                pass
            else:
                fig.update_traces(marker_color=literal_color)
                for trace in fig.data:
                    if hasattr(trace, "line"):
                        trace.line.color = literal_color
                    if hasattr(trace, "fillcolor"):
                        trace.fillcolor = literal_color

        if chart_type == "bar" and color_col:
            if chart_mode in {"stack", "stacked", "relative"}:
                fig.update_layout(barmode="stack")
            elif chart_mode in {"percent", "percentage", "normalize", "normalized"}:
                fig.update_layout(barmode="stack", barnorm="percent")
            else:
                fig.update_layout(barmode="group")
        if chart_type == "bar":
            _apply_bar_value_labels(fig, chart_mode_value=chart_mode)
        layout_updates = {"template": plotly_template}
        if template_layout.get("paper_bgcolor"):
            layout_updates["paper_bgcolor"] = template_layout.get("paper_bgcolor")
        if template_layout.get("plot_bgcolor"):
            layout_updates["plot_bgcolor"] = template_layout.get("plot_bgcolor")
        template_title = template_layout.get("title") or {}
        if template_title.get("x") is not None:
            layout_updates["title_x"] = template_title.get("x")
        if template_title.get("xanchor") is not None:
            layout_updates["title_xanchor"] = template_title.get("xanchor")
        if x_axis_label:
            layout_updates["xaxis_title"] = x_axis_label
        if y_axis_label:
            layout_updates["yaxis_title"] = y_axis_label
        fig.update_layout(**layout_updates)
        fig.update_xaxes(automargin=True, title_standoff=14)
        fig.update_yaxes(automargin=True, title_standoff=14)
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
                default_name = _default_plot_export_name(chart_type, df_label, fig)
                fig, pub_settings = _render_plot_publication_tools(fig, _safe_key, chart_type, default_name)
                display_width, display_height = _get_plot_display_dimensions(fig)
                st.plotly_chart(
                    fig,
                    width=display_width,
                    height=display_height,
                    key=_safe_key,
                    theme=None,
                )
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
                template_layout = plotly_template.get("layout", {}) if isinstance(plotly_template, dict) else {}
                combined.update_layout(
                    template=plotly_template,
                    title=" / ".join(title_parts) if title_parts else f"DF{df_id} — {chart_type}",
                    xaxis_title=first_spec.get("xlabel") or first_spec.get("x_label"),
                    yaxis_title=first_spec.get("ylabel") or first_spec.get("y_label"),
                    paper_bgcolor=template_layout.get("paper_bgcolor"),
                    plot_bgcolor=template_layout.get("plot_bgcolor"),
                )
                combined.update_xaxes(automargin=True, title_standoff=14)
                combined.update_yaxes(automargin=True, title_standoff=14)
                _safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                default_name = _default_plot_export_name(chart_type, df_label, combined)
                combined, pub_settings = _render_plot_publication_tools(combined, _safe_key, chart_type, default_name)
                display_width, display_height = _get_plot_display_dimensions(combined)
                st.plotly_chart(
                    combined,
                    width=display_width,
                    height=display_height,
                    key=_safe_key,
                    theme=None,
                )
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
        default_name = _default_plot_export_name(str(chart.get("type") or "plot"), df_label, fig)
        fig, pub_settings = _render_plot_publication_tools(
            fig,
            _safe_key,
            str(chart.get("type") or "plot"),
            default_name,
        )
        display_width, display_height = _get_plot_display_dimensions(fig)
        st.plotly_chart(
            fig,
            width=display_width,
            height=display_height,
            key=_safe_key,
            theme=None,
        )
