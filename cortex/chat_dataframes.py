"""DataFrame embedding, DF-ID assignment, and plot-spec resolution.

Extracted from ``cortex/app.py`` (Phase G of ``chat_with_agent``) to keep the
main orchestrator focused on flow control.  This module handles:

* Extracting structured DataFrames from MCP tool results (parse_csv_file,
  search_by_biosample, etc.)
* Collecting generated plot images from edgepython results
* Assigning sequential DF IDs (DF1, DF2, …) to visible DataFrames
* Rebinding plot specs to the newest turn's DataFrame
* Post-DF-assignment plot fallback and spec deduplication
"""

from __future__ import annotations

import base64
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from atlas.config import CONSORTIUM_REGISTRY
from common.logging_config import get_logger
from cortex.config import SERVICE_REGISTRY
from cortex.dataframe_actions import build_overlap_dataframe_payload
from cortex.llm_validators import get_block_payload
from cortex.tag_parser import user_wants_plot

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEARCH_TOOLS = frozenset({
    "search_by_biosample", "search_by_target",
    "search_by_organism", "search_by_assay", "list_experiments",
    # IGVF portal search tools
    "search_measurement_sets", "search_analysis_sets", "search_prediction_sets",
    "search_by_sample", "search_by_assay", "search_files",
    "search_genes", "search_samples", "get_files_for_dataset",
})

_FILE_TOOLS = frozenset({"get_files_by_type"})

_PLOT_KEYWORDS = frozenset({
    "plot", "chart", "pie", "histogram", "scatter", "bar chart",
    "box plot", "heatmap", "visualize", "graph", "distribution",
    "venn", "upset", "violin plot", "strip plot", "line chart",
    "line plot", "area chart", "area plot",
})


# ---------------------------------------------------------------------------
# DataFrame extraction
# ---------------------------------------------------------------------------

def extract_embedded_dataframes(
    all_results: dict[str, list[dict]],
    user_message: str,
) -> dict[str, dict]:
    """Extract structured DataFrames from tool results.

    Returns ``{label: {"columns": [...], "data": [...], "row_count": int, "metadata": {...}}}``.
    """
    embedded: dict[str, dict] = {}

    for src_key, src_results in all_results.items():
        reg_entry = (
            SERVICE_REGISTRY.get(src_key)
            or CONSORTIUM_REGISTRY.get(src_key)
            or {}
        )
        table_cols = reg_entry.get("table_columns", [])

        for r in src_results:
            tool = r.get("tool", "")

            if tool in ("parse_csv_file", "parse_bed_file") and "data" in r:
                _extract_file_parse(r, embedded)

            elif tool == "run_allowlisted_script" and "data" in r:
                _extract_script_dataframe(r, embedded)

            elif src_key == "cortex" and tool in {
                "filter_dataframe",
                "select_dataframe_columns",
                "rename_dataframe_columns",
                "sort_dataframe",
                "melt_dataframe",
                "aggregate_dataframe",
                "join_dataframes",
                "pivot_dataframe",
                "build_overlap_dataframe",
            } and "data" in r:
                _extract_transform_result(r, embedded)

            elif tool in _FILE_TOOLS and "data" in r:
                _extract_files_by_type(r, user_message, embedded)

            elif tool in _SEARCH_TOOLS and "data" in r:
                _extract_search_results(r, table_cols, embedded)

    return embedded


def _extract_file_parse(r: dict, embedded: dict) -> None:
    rd = r["data"]
    if not (isinstance(rd, dict) and rd.get("data")):
        return
    fname = rd.get("file_path") or r["params"].get("file_path", "unknown")
    fname = fname.rsplit("/", 1)[-1] if "/" in fname else fname
    embedded[fname] = {
        "columns": rd.get("columns", []),
        "data": rd["data"],
        "row_count": rd.get("row_count", len(rd["data"])),
        "metadata": rd.get("metadata", {}),
    }


def _extract_script_dataframe(r: dict, embedded: dict) -> None:
    rd = r["data"]
    dataframe = rd.get("dataframe") if isinstance(rd, dict) else None
    if not (isinstance(dataframe, dict) and dataframe.get("data")):
        return

    metadata = dict(dataframe.get("metadata") or {})
    label = metadata.get("label") or rd.get("script_id") or "script_result"
    embedded[label] = {
        "columns": dataframe.get("columns", []),
        "data": dataframe["data"],
        "row_count": dataframe.get("row_count", len(dataframe["data"])),
        "metadata": metadata,
    }


def _extract_transform_result(r: dict, embedded: dict) -> None:
    rd = r["data"]
    if not (isinstance(rd, dict) and isinstance(rd.get("data"), list)):
        return

    metadata = dict(rd.get("metadata") or {})
    label = metadata.get("label") or r.get("tool") or "dataframe_transform"
    embedded[label] = {
        "columns": rd.get("columns", []),
        "data": rd.get("data", []),
        "row_count": rd.get("row_count", len(rd.get("data", []))),
        "metadata": metadata,
    }


def _extract_files_by_type(
    r: dict,
    user_message: str,
    embedded: dict,
) -> None:
    rd = r["data"]
    if not isinstance(rd, dict):
        return
    exp_acc = r.get("params", {}).get("accession", "files")
    cols = ["Accession", "Output Type", "Replicate", "Size", "Status"]
    msg_lower = user_message.lower()
    asked_file_types: set[str] = set()
    for candidate_ft in rd.keys():
        if candidate_ft.lower().split()[0] in msg_lower:
            asked_file_types.add(candidate_ft)

    for ftype, flist in rd.items():
        if not isinstance(flist, list) or not flist:
            continue
        rows = []
        for fobj in flist:
            if isinstance(fobj, dict):
                rows.append({
                    "Accession": fobj.get("accession", ""),
                    "Output Type": fobj.get("output_type", ""),
                    "Replicate": (
                        fobj.get("biological_replicates_formatted")
                        or ", ".join(
                            f"Rep {x}" for x in fobj.get("biological_replicates", [])
                        )
                    ),
                    "Size": fobj.get("file_size", ""),
                    "Status": fobj.get("status", ""),
                })
        if rows:
            df_label = f"{exp_acc} {ftype} files ({len(rows)})"
            is_visible = not asked_file_types or ftype in asked_file_types
            embedded[df_label] = {
                "columns": cols,
                "data": rows,
                "row_count": len(rows),
                "metadata": {
                    "file_type": ftype,
                    "accession": exp_acc,
                    "visible": is_visible,
                },
            }


def _extract_search_results(
    r: dict,
    table_cols: list[tuple[str, str]],
    embedded: dict,
) -> None:
    rd = r["data"]
    experiment_list: list[dict] = []
    if isinstance(rd, list):
        experiment_list = rd
    elif isinstance(rd, dict):
        # IGVF tools return {"total": N, "count": N, "results": [...]}
        # or {"total_files": N, "files": [...]}
        if "results" in rd and isinstance(rd["results"], list):
            experiment_list = rd["results"]
        elif "total_files" in rd and isinstance(rd.get("files"), list):
            experiment_list = rd["files"]
        elif "experiments" in rd and isinstance(rd["experiments"], list):
            experiment_list = rd["experiments"]
        else:
            for sub_key in ("human", "mouse"):
                sub = rd.get(sub_key)
                if isinstance(sub, list):
                    experiment_list.extend(sub)

    if not (experiment_list and isinstance(experiment_list[0], dict)):
        return

    if table_cols:
        cols = [h for h, _ in table_cols]
        rows = [
            {
                h: (
                    item.get(k) if not isinstance(item.get(k), list)
                    else ", ".join(str(v) for v in item.get(k, []))
                )
                for h, k in table_cols
            }
            for item in experiment_list
        ]
    else:
        cols = list(experiment_list[0].keys())
        rows = experiment_list

    tool = r.get("tool", "")
    params = r.get("params", {})
    label = tool.replace("_", " ").title()
    if params.get("search_term"):
        label = params["search_term"]
    elif params.get("assay_title"):
        label = params["assay_title"]
    elif params.get("target"):
        label = params["target"]
    elif params.get("organism"):
        label = params["organism"]

    fname = f"{label} ({len(experiment_list)} results)"
    embedded[fname] = {
        "columns": cols,
        "data": rows,
        "row_count": len(experiment_list),
        "metadata": {"visible": True},
    }


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def extract_embedded_images(all_results: dict[str, list[dict]]) -> list[dict]:
    """Collect generated plot images (base64 PNG) from edgepython results."""
    images: list[dict] = []
    for src_key, src_results in all_results.items():
        if src_key != "edgepython":
            continue
        for r in src_results:
            if r.get("tool") != "generate_plot" or "error" in r:
                continue
            data_str = r.get("data", "")
            if not isinstance(data_str, str):
                continue
            saved_match = re.search(r'saved to:\s*(.+\.png)', data_str, re.IGNORECASE)
            if not saved_match:
                continue
            img_path = Path(saved_match.group(1).strip())
            if not img_path.exists():
                continue
            try:
                img_bytes = img_path.read_bytes()
                b64 = base64.b64encode(img_bytes).decode("ascii")
                plot_type = r.get("params", {}).get("plot_type", "plot")
                images.append({
                    "label": f"{plot_type.replace('_', ' ').title()} Plot",
                    "data_b64": b64,
                    "path": str(img_path),
                })
                logger.info("Embedded plot image", plot_type=plot_type,
                            path=str(img_path), size_kb=len(img_bytes) // 1024)
            except Exception as img_err:
                logger.warning("Failed to embed plot image",
                               path=str(img_path), error=str(img_err))
    return images


# ---------------------------------------------------------------------------
# DF-ID assignment
# ---------------------------------------------------------------------------

def assign_df_ids(
    embedded_dataframes: dict[str, dict],
    history_blocks: list,
    injected_dfs: dict | None,
) -> int:
    """Assign sequential DF IDs to visible DataFrames.

    Merges *injected_dfs* into *embedded_dataframes* first, then assigns IDs
    continuing from the highest existing ID in *history_blocks*.

    Returns the next available DF ID after assignment.
    """
    # Merge injected DFs
    if injected_dfs:
        embedded_dataframes.update(injected_dfs)

    # Find highest existing ID in history
    next_df_id = 1
    for hblk in history_blocks[:-1]:  # exclude current USER_MESSAGE
        if hblk.type == "AGENT_PLAN":
            hblk_dfs = get_block_payload(hblk).get("_dataframes", {})
            for dfd in hblk_dfs.values():
                existing_id = dfd.get("metadata", {}).get("df_id")
                if isinstance(existing_id, int):
                    next_df_id = max(next_df_id, existing_id + 1)

    # Assign IDs to visible DFs without existing IDs
    for df_data in embedded_dataframes.values():
        meta = df_data.setdefault("metadata", {})
        if meta.get("df_id"):
            continue  # already has an ID (e.g. injected df)
        if meta.get("visible", True):  # default True for DFs without flag
            meta["df_id"] = next_df_id
            next_df_id += 1
        # Non-visible DFs intentionally get no df_id

    return next_df_id


# ---------------------------------------------------------------------------
# Plot-spec rebinding & fallback
# ---------------------------------------------------------------------------

def rebind_plots_to_new_df(
    plot_specs: list[dict],
    embedded_dataframes: dict[str, dict],
    user_message: str,
) -> None:
    """Rebind plot specs to the newest DF from this turn if user didn't specify a DF.

    Modifies *plot_specs* in place.
    """
    user_explicit_df = re.search(r'\bDF\s*(\d+)\b', user_message, re.IGNORECASE)
    user_requested_plot = user_wants_plot(user_message)

    if not (user_requested_plot and plot_specs and not user_explicit_df):
        return

    newest_turn_df_id = None
    for df_data in embedded_dataframes.values():
        df_id = df_data.get("metadata", {}).get("df_id")
        if isinstance(df_id, int):
            if newest_turn_df_id is None or df_id > newest_turn_df_id:
                newest_turn_df_id = df_id

    if newest_turn_df_id is None:
        return

    rebound_count = 0
    stale_df_ids = sorted({
        ps.get("df_id")
        for ps in plot_specs
        if (
            isinstance(ps.get("df_id"), int)
            and ps.get("df_id") != newest_turn_df_id
            and not _plot_spec_has_multi_df_overlap_source(ps)
        )
    })
    for ps in plot_specs:
        if _plot_spec_has_multi_df_overlap_source(ps):
            continue
        if ps.get("df_id") != newest_turn_df_id:
            ps["df_id"] = newest_turn_df_id
            ps["df"] = f"DF{newest_turn_df_id}"
            rebound_count += 1
        elif ps.get("df") != f"DF{newest_turn_df_id}":
            ps["df"] = f"DF{newest_turn_df_id}"
    if rebound_count or stale_df_ids:
        logger.info(
            "Rebound plot specs to dataframe created in current turn",
            new_df_id=newest_turn_df_id,
            stale_df_ids=stale_df_ids,
            rebound_count=rebound_count,
        )


def materialize_overlap_plot_dataframes(
    plot_specs: list[dict],
    embedded_dataframes: dict[str, dict],
    history_blocks: list,
    *,
    current_dataframes: dict[str, dict] | None = None,
) -> list[str]:
    """Rewrite venn/upset overlap specs into single-dataframe membership specs.

    Returns a list of human-readable build errors. Successful rewrites mutate
    ``plot_specs`` in place and inject synthetic dataframes into
    ``embedded_dataframes``.
    """
    if not plot_specs:
        return []

    errors: list[str] = []
    synthetic_frames: dict[str, dict] = {}
    pending_rewrites: list[tuple[int, dict, dict]] = []
    build_cache: dict[tuple, dict] = {}
    active_dataframes = current_dataframes or embedded_dataframes

    for idx, spec in enumerate(list(plot_specs)):
        if not _needs_overlap_materialization(spec):
            continue
        build_params = _build_overlap_plot_params(spec)
        cache_key = _overlap_request_cache_key(build_params)
        try:
            payload = build_cache.get(cache_key)
            if payload is None:
                payload = build_overlap_dataframe_payload(
                    build_params,
                    history_blocks=history_blocks,
                    current_dataframes=active_dataframes,
                )
                synthetic_key = _synthetic_overlap_key(payload, len(synthetic_frames))
                synthetic_frames[synthetic_key] = payload
                build_cache[cache_key] = payload
            pending_rewrites.append((idx, spec, payload))
        except Exception as exc:
            errors.append(_format_overlap_materialization_error(spec, exc))
            plot_specs[idx] = {"_drop": True}

    if synthetic_frames:
        assign_df_ids(embedded_dataframes, history_blocks, synthetic_frames)

    for idx, original_spec, payload in pending_rewrites:
        plot_specs[idx] = _rewrite_overlap_plot_spec(original_spec, payload)

    plot_specs[:] = [spec for spec in plot_specs if not spec.get("_drop")]
    return errors


def materialize_direct_set_overlap_plot_dataframes(
    plot_specs: list[dict],
    embedded_dataframes: dict[str, dict],
    history_blocks: list,
    *,
    current_dataframes: dict[str, dict] | None = None,
) -> list[str]:
    """Rewrite venn/upset specs with explicit sets into synthetic membership dataframes."""
    if not plot_specs:
        return []

    errors: list[str] = []
    synthetic_frames: dict[str, dict] = {}
    pending_rewrites: list[tuple[int, dict, dict]] = []
    build_cache: dict[tuple, dict] = {}
    active_dataframes = current_dataframes or embedded_dataframes

    for idx, spec in enumerate(list(plot_specs)):
        if not _needs_direct_set_materialization(spec):
            continue
        cache_key = (
            str(spec.get("type") or "").strip().lower(),
            spec.get("df_id"),
            str(spec.get("sets") or "").strip(),
        )
        try:
            payload = build_cache.get(cache_key)
            if payload is None:
                payload = _build_direct_set_overlap_payload(
                    spec,
                    history_blocks,
                    current_dataframes=active_dataframes,
                )
                synthetic_key = _synthetic_overlap_key(payload, len(synthetic_frames))
                synthetic_frames[synthetic_key] = payload
                build_cache[cache_key] = payload
            pending_rewrites.append((idx, spec, payload))
        except Exception as exc:
            errors.append(_format_overlap_materialization_error(spec, exc))
            plot_specs[idx] = {"_drop": True}

    if synthetic_frames:
        assign_df_ids(embedded_dataframes, history_blocks, synthetic_frames)

    for idx, original_spec, payload in pending_rewrites:
        plot_specs[idx] = _rewrite_overlap_plot_spec(original_spec, payload)

    plot_specs[:] = [spec for spec in plot_specs if not spec.get("_drop")]
    return errors


def apply_overlap_message_hints(
    plot_specs: list[dict],
    user_message: str,
    history_blocks: list,
    current_dataframes: dict[str, dict] | None = None,
) -> None:
    """Fill in explicit overlap set columns when the user named them in text."""
    if not plot_specs or not user_message:
        return

    for spec in plot_specs:
        if not _needs_overlap_set_hydration(spec):
            continue
        df_id = spec.get("df_id")
        if not isinstance(df_id, int):
            continue
        payload = _find_plot_dataframe_payload(
            df_id,
            history_blocks,
            current_dataframes=current_dataframes,
        )
        if not payload:
            continue
        columns = list(payload.get("columns") or [])
        set_limit = 3 if str(spec.get("type") or "").strip().lower() == "venn" else 6
        explicit_sets = _extract_explicit_overlap_set_columns(
            user_message,
            columns,
            max_count=set_limit,
        )
        if len(explicit_sets) < 2:
            continue
        spec["sets"] = "|".join(explicit_sets)
        logger.info(
            "Applied overlap plot message hints",
            df_id=df_id,
            chart_type=spec.get("type"),
            sets=explicit_sets,
        )


def post_df_assignment_plot_fallback(
    plot_specs: list[dict],
    embedded_dataframes: dict[str, dict],
    user_message: str,
    extract_plot_style_params,
) -> None:
    """Generate a plot spec when user wanted a plot but LLM didn't emit tags.

    Also fixes any specs with ``df_id=None`` that can now be resolved.
    Modifies *plot_specs* in place.
    """
    user_requested_plot = user_wants_plot(user_message)
    has_valid_plot = plot_specs and any(
        s.get("df_id") is not None for s in plot_specs
    )

    if user_requested_plot and not has_valid_plot and embedded_dataframes:
        newest_df_id = None
        newest_df_data = None
        for edf_val in embedded_dataframes.values():
            edf_id = edf_val.get("metadata", {}).get("df_id")
            if isinstance(edf_id, int):
                if newest_df_id is None or edf_id > newest_df_id:
                    newest_df_id = edf_id
                    newest_df_data = edf_val

        if newest_df_id is not None:
            auto_post_spec = _build_fallback_plot_spec(
                newest_df_id,
                newest_df_data or {},
                user_message,
            )
            auto_post_spec.update(extract_plot_style_params(user_message))
            plot_specs.append(auto_post_spec)
            logger.info("Post-DF-assignment plot fallback generated",
                        spec=auto_post_spec)

    # Fix specs with None df_id
    if plot_specs and embedded_dataframes:
        newest_id_for_fix = None
        for v in embedded_dataframes.values():
            eid = v.get("metadata", {}).get("df_id")
            if isinstance(eid, int):
                if newest_id_for_fix is None or eid > newest_id_for_fix:
                    newest_id_for_fix = eid
        if newest_id_for_fix is not None:
            for ps in plot_specs:
                if ps.get("df_id") is None:
                    ps["df_id"] = newest_id_for_fix
                    ps["df"] = f"DF{newest_id_for_fix}"
                    logger.info("Fixed plot spec with None df_id",
                                new_df_id=newest_id_for_fix)


def deduplicate_plot_specs(plot_specs: list[dict]) -> list[dict]:
    """Deduplicate identical plot specs.

    Returns the deduplicated list.
    """
    if not plot_specs:
        return plot_specs

    deduped: list[dict] = []
    seen: set = set()
    for ps in plot_specs:
        semantic = {
            "type": ps.get("type"),
            "df_id": ps.get("df_id"),
            "x": ps.get("x"),
            "y": ps.get("y"),
            "sets": ps.get("sets"),
            "color": ps.get("color"),
            "palette": ps.get("palette"),
            "agg": ps.get("agg"),
            "max_intersections": ps.get("max_intersections"),
        }
        key = tuple(
            sorted(
                (str(k), str(v).strip().lower() if isinstance(v, str) else v)
                for k, v in semantic.items()
                if v is not None
            )
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ps)

    if len(deduped) != len(plot_specs):
        logger.info("Deduplicated duplicate plot specs",
                     original_count=len(plot_specs),
                     deduped_count=len(deduped))
    return deduped


def collapse_competing_specs(
    plot_specs: list[dict],
    user_message: str,
    extract_plot_style_params,
) -> list[dict]:
    """For single-plot requests, keep only the best-matching spec per group.

    Returns the filtered list (may be the same object if no collapsing needed).
    """
    if not plot_specs:
        return plot_specs

    multi_trace = bool(re.search(
        r'\b(compare|overlay|overlaid|versus|vs\.?|both|multiple|multi[- ]trace)\b',
        user_message, re.IGNORECASE,
    ))
    if multi_trace:
        return _apply_explicit_special_x(plot_specs, _explicit_special_plot_x(user_message))

    requested_style = extract_plot_style_params(user_message)
    requested_palette = _normalize_plot_token(requested_style.get("palette"))
    requested_x_match = re.search(
        r'\bby\s+([\w][\w ]*?)(?:\s+in\s+[#A-Za-z]|,|\.|$)',
        user_message, re.IGNORECASE,
    ) or re.search(
        r'\bof\s+([\w][\w ]*?)(?:\s+in\s+[#A-Za-z]|,|\.|$)',
        user_message, re.IGNORECASE,
    )
    requested_x = (
        _normalize_plot_token(requested_x_match.group(1).strip())
        if requested_x_match else ""
    )
    explicit_special_x = _explicit_special_plot_x(user_message)
    requested_type = _requested_plot_type(user_message)

    grouped: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for idx, spec in enumerate(plot_specs):
        grouped[(spec.get("df_id"), spec.get("type"))].append((idx, spec))

    selected: list[dict] = []
    for group_specs in grouped.values():
        if len(group_specs) == 1:
            selected.append(_override_spec_x(group_specs[0][1], explicit_special_x))
            continue

        best_spec = None
        best_score = None
        for plot_index, spec in group_specs:
            score = _score_spec(
                spec, plot_index,
                requested_type, requested_x, requested_palette,
            )
            if best_score is None or score > best_score:
                best_score = score
                best_spec = spec

        if best_spec is not None:
            selected.append(_override_spec_x(best_spec, explicit_special_x))

    if len(selected) != len(plot_specs):
        logger.info(
            "Collapsed competing plot specs to a single best-match chart",
            original_count=len(plot_specs),
            selected_count=len(selected),
            requested_x=requested_x,
            requested_type=requested_type,
            requested_palette=requested_palette,
        )

    return selected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_chart_type(message: str) -> str:
    msg = message.lower()
    if "upset" in msg:
        return "upset"
    if "venn" in msg:
        return "venn"
    if "violin" in msg:
        return "violin"
    if "strip" in msg:
        return "strip"
    if re.search(r"\bline\s+(?:chart|plot|graph)\b", msg):
        return "line"
    if re.search(r"\barea\s+(?:chart|plot|graph)\b", msg):
        return "area"
    if "pie" in msg:
        return "pie"
    if "scatter" in msg:
        return "scatter"
    if "bar" in msg:
        return "bar"
    if "box" in msg:
        return "box"
    if "heatmap" in msg or "correlation" in msg:
        return "heatmap"
    if "histogram" in msg or "distribution" in msg:
        return "histogram"
    return "bar"


def _build_fallback_plot_spec(
    df_id: int,
    df_data: dict,
    user_message: str,
) -> dict:
    """Choose a sensible fallback plot spec for the newest dataframe."""
    specialized = _build_specialized_dataframe_plot_spec(df_data)
    if specialized is not None:
        specialized["df_id"] = df_id
        specialized["df"] = f"DF{df_id}"
        return specialized

    auto_type = _detect_chart_type(user_message)
    auto_x = _detect_x_column(user_message)
    auto_post_spec: dict = {
        "type": auto_type,
        "df_id": df_id,
        "df": f"DF{df_id}",
    }
    if auto_type == "bar":
        auto_post_spec["agg"] = "count"
    if auto_x:
        auto_post_spec["x"] = auto_x
    return auto_post_spec


def _build_specialized_dataframe_plot_spec(df_data: dict) -> dict | None:
    """Return a chart spec for known dataframe shapes, if applicable."""
    metadata = df_data.get("metadata") or {}
    columns = df_data.get("columns") or []
    rows = df_data.get("data") or []

    is_bed_counts = (
        metadata.get("kind") == "bed_chromosome_counts"
        or all(col in columns for col in ("Chromosome", "Count"))
    )
    if not is_bed_counts:
        return None

    spec: dict = {
        "type": "bar",
        "x": "Chromosome",
        "y": "Count",
        "title": "Regions per Chromosome",
    }

    if _column_has_multiple_values(rows, "Genome"):
        spec["color"] = "Genome"
        spec["title"] = "Regions per Chromosome by Genome"
    elif _column_has_multiple_values(rows, "Modification"):
        spec["color"] = "Modification"
        spec["title"] = "Regions per Chromosome by Modification"
    elif _column_has_multiple_values(rows, "Sample"):
        spec["color"] = "Sample"
        spec["title"] = "Regions per Chromosome by Sample"

    return spec


def _column_has_multiple_values(rows: list[dict], column: str) -> bool:
    values = {str(row.get(column, "")).strip() for row in rows if str(row.get(column, "")).strip()}
    return len(values) > 1


def _detect_x_column(message: str) -> str | None:
    by_m = re.search(r'\bby\s+(\w+)', message, re.IGNORECASE)
    if by_m and not re.match(r'DF\d+', by_m.group(1), re.IGNORECASE):
        return by_m.group(1)
    of_m = re.search(r'\b(?:of|for)\s+(\w+)', message, re.IGNORECASE)
    if of_m and not re.match(r'DF\d+', of_m.group(1), re.IGNORECASE):
        return of_m.group(1)
    return None


def _needs_overlap_set_hydration(spec: dict) -> bool:
    chart_type = str(spec.get("type") or "").strip().lower()
    if chart_type not in {"venn", "upset"}:
        return False
    if spec.get("sets"):
        return False
    return not any(
        spec.get(key)
        for key in ("dfs", "df_ids", "sources", "match_cols", "match_on", "sample_col", "sample_values")
    )


def _needs_direct_set_materialization(spec: dict) -> bool:
    chart_type = str(spec.get("type") or "").strip().lower()
    if chart_type not in {"venn", "upset"}:
        return False
    if not spec.get("sets"):
        return False
    if any(
        spec.get(key)
        for key in ("dfs", "df_ids", "sources", "match_cols", "match_on", "sample_col", "sample_values")
    ):
        return False
    return isinstance(spec.get("df_id"), int)


def _find_plot_dataframe_payload(
    df_id: int,
    history_blocks: list,
    *,
    current_dataframes: dict[str, dict] | None = None,
) -> dict | None:
    if current_dataframes:
        for payload in current_dataframes.values():
            metadata = payload.get("metadata") or {}
            if metadata.get("df_id") == df_id:
                return payload

    for block in reversed(history_blocks or []):
        payload = get_block_payload(block)
        for df_payload in (payload.get("_dataframes") or {}).values():
            metadata = df_payload.get("metadata") or {}
            if metadata.get("df_id") == df_id:
                return df_payload
    return None


def find_dataframe_parse_source(df_id: int, history_blocks: list) -> dict | None:
    """Return parse_csv provenance params for an existing dataframe when available."""
    for block in reversed(history_blocks or []):
        payload = get_block_payload(block)
        dataframes = payload.get("_dataframes") or {}
        matched_key = None
        matched_label = None
        for key, df_payload in dataframes.items():
            metadata = df_payload.get("metadata") or {}
            if metadata.get("df_id") != df_id:
                continue
            matched_key = key
            matched_label = str(metadata.get("label") or key or "").strip()
            break
        if matched_key is None:
            continue

        candidates = {Path(str(matched_key)).name}
        if matched_label:
            candidates.add(Path(matched_label).name)

        parse_entries = [
            entry for entry in (payload.get("_provenance") or [])
            if entry.get("success")
            and entry.get("source") == "analyzer"
            and entry.get("tool") == "parse_csv_file"
        ]
        for entry in parse_entries:
            params = dict(entry.get("params") or {})
            file_path = str(params.get("file_path") or "").strip()
            if file_path and Path(file_path).name in candidates:
                return params
        if len(parse_entries) == 1:
            return dict(parse_entries[0].get("params") or {})

    return None


def _extract_explicit_overlap_set_columns(
    user_message: str,
    available_columns: list[str],
    *,
    max_count: int,
) -> list[str]:
    if not user_message or not available_columns:
        return []

    patterns = [
        r"\busing\s+(?:columns?\s+)?(.+?)(?:\s+as\s+(?:the\s+)?(?:samples?|sets?)\b|\s+where\b|[.;]|$)",
        r"\b(?:samples?|sets?)\s*(?:=|:|are)?\s*(?:columns?\s+)?(.+?)(?:\s+where\b|[.;]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_message, re.IGNORECASE)
        if not match:
            continue
        resolved = _resolve_overlap_columns_fragment(match.group(1), available_columns)
        if 2 <= len(resolved) <= max_count:
            return resolved

    using_match = re.search(r"\busing\s+(.+)$", user_message, re.IGNORECASE)
    if using_match:
        trailing_fragment = re.split(
            r"\b(?:where|that|which)\b|[.;]",
            using_match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        resolved = _resolve_overlap_columns_fragment(trailing_fragment, available_columns)
        if 2 <= len(resolved) <= max_count:
            return resolved

    return []


def _resolve_overlap_columns_fragment(fragment: str, available_columns: list[str]) -> list[str]:
    if not fragment:
        return []

    normalized_fragment = re.sub(r"\band\b", ",", fragment, flags=re.IGNORECASE)
    tokens = [
        token.strip().strip('"').strip("'")
        for token in re.split(r"\s*\|\s*|\s*,\s*|\s*;\s*", normalized_fragment)
        if token.strip()
    ]
    resolved: list[str] = []
    for token in tokens:
        for column in available_columns:
            if str(column).strip().lower() == token.lower() and column not in resolved:
                resolved.append(column)
                break
    return resolved


def _default_overlap_label(labels: list[str]) -> str:
    if not labels:
        return "overlap_dataframe"
    preview = "_vs_".join(re.sub(r"\s+", "_", str(label).strip()) for label in labels[:3])
    return f"overlap_{preview}"


def _needs_overlap_materialization(spec: dict) -> bool:
    chart_type = str(spec.get("type") or "").strip().lower()
    if chart_type not in {"venn", "upset"}:
        return False
    return any(
        spec.get(key)
        for key in ("dfs", "df_ids", "sources", "match_cols", "match_on", "sample_col", "sample_values")
    )


def _parse_multi_value_param(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        candidates = value
    else:
        raw_value = str(value).strip()
        if raw_value.startswith("[") and raw_value.endswith("]"):
            raw_value = raw_value[1:-1]
        candidates = re.split(r"[|;,]", raw_value)

    normalized: list[str] = []
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


def _match_plot_column_name(columns: list[str], requested: str | None) -> str | None:
    if not requested:
        return None
    lowered = str(requested).strip().lower()
    for column in columns:
        if str(column).strip().lower() == lowered:
            return str(column)
    return None


def _coerce_overlap_membership_series(series: pd.Series, *, allow_positive_numeric: bool) -> pd.Series | None:
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


def _build_direct_set_overlap_payload(
    spec: dict,
    history_blocks: list,
    *,
    current_dataframes: dict[str, dict] | None = None,
) -> dict:
    df_id = spec.get("df_id")
    if not isinstance(df_id, int):
        raise ValueError("Direct set overlap materialization requires df_id.")

    payload = _find_plot_dataframe_payload(
        df_id,
        history_blocks,
        current_dataframes=current_dataframes,
    )
    if not payload:
        raise ValueError(f"Dataframe DF{df_id} was not found for overlap plotting.")

    metadata = payload.get("metadata") or {}
    if metadata.get("kind") == "overlap_membership":
        return payload

    frame = pd.DataFrame(payload.get("data") or [], columns=payload.get("columns") or None)
    if frame.empty:
        raise ValueError("Overlap plot source dataframe is empty.")

    requested_sets = _parse_multi_value_param(spec.get("sets"))
    if not requested_sets:
        raise ValueError("Overlap plot requires at least 2 explicit set columns.")

    resolved_set_columns: list[str] = []
    for requested in requested_sets:
        matched = _match_plot_column_name(list(frame.columns), requested)
        if not matched:
            raise ValueError(f"Column '{requested}' was not found in the dataframe")
        if matched not in resolved_set_columns:
            resolved_set_columns.append(matched)

    chart_type = str(spec.get("type") or "").strip().lower()
    if chart_type == "venn" and len(resolved_set_columns) not in {2, 3}:
        raise ValueError("Venn plots require exactly 2 or 3 set columns.")
    if chart_type == "upset" and len(resolved_set_columns) < 2:
        raise ValueError("UpSet plots require at least 2 set columns.")

    overlap_df = pd.DataFrame({
        "match_key": [str(index) for index in range(len(frame))],
    })
    for column in resolved_set_columns:
        membership = _coerce_overlap_membership_series(
            frame[column],
            allow_positive_numeric=True,
        )
        if membership is None:
            raise ValueError(f"Column '{column}' could not be interpreted as set membership.")
        overlap_df[column] = membership.tolist()

    label = _default_overlap_label(resolved_set_columns)
    overlap_rows = overlap_df.to_dict(orient="records")
    return {
        "columns": list(overlap_df.columns),
        "data": overlap_rows,
        "row_count": len(overlap_rows),
        "metadata": {
            "operation": "build_overlap_dataframe",
            "label": label,
            "kind": "overlap_membership",
            "set_columns": resolved_set_columns,
            "source_df_id": df_id,
            "source_df_ids": [df_id],
            "source_labels": resolved_set_columns,
            "match_key_column": "match_key",
            "visible": True,
        },
    }


def _plot_spec_has_multi_df_overlap_source(spec: dict) -> bool:
    return bool(spec.get("dfs") or spec.get("df_ids") or spec.get("sources"))


def _build_overlap_plot_params(spec: dict) -> dict:
    params = {
        "plot_type": str(spec.get("type") or "").strip().lower(),
    }
    for key in (
        "df",
        "df_id",
        "dfs",
        "df_ids",
        "sources",
        "match_cols",
        "match_on",
        "sample_col",
        "sample_values",
        "labels",
    ):
        if spec.get(key) is not None:
            params[key] = spec.get(key)
    return params


def _overlap_request_cache_key(params: dict) -> tuple:
    items = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, list):
            normalized = tuple(str(item) for item in value)
        else:
            normalized = str(value)
        items.append((key, normalized))
    return tuple(items)


def _synthetic_overlap_key(payload: dict, sequence: int) -> str:
    label = str((payload.get("metadata") or {}).get("label") or "overlap_dataframe")
    cleaned = re.sub(r"\s+", "_", label.strip())
    return f"{cleaned}_{sequence + 1}"


def _rewrite_overlap_plot_spec(original_spec: dict, payload: dict) -> dict:
    metadata = payload.get("metadata") or {}
    df_id = metadata.get("df_id")
    set_columns = list(metadata.get("set_columns") or [])
    rewritten = {
        "type": original_spec.get("type"),
        "df_id": df_id,
        "df": f"DF{df_id}" if isinstance(df_id, int) else original_spec.get("df"),
        "sets": "|".join(set_columns),
        "source_df_ids": metadata.get("source_df_ids"),
        "source_labels": metadata.get("source_labels"),
        "source_match_cols": metadata.get("source_match_cols"),
        "match_key_column": metadata.get("match_key_column"),
    }
    if original_spec.get("title") is not None:
        rewritten["title"] = original_spec.get("title")
    if original_spec.get("palette") is not None:
        rewritten["palette"] = original_spec.get("palette")
    if original_spec.get("color") is not None:
        rewritten["color"] = original_spec.get("color")
    if original_spec.get("max_intersections") is not None:
        rewritten["max_intersections"] = _coerce_int_like(original_spec.get("max_intersections"))
    return rewritten


def _format_overlap_materialization_error(spec: dict, exc: Exception) -> str:
    chart_type = str(spec.get("type") or "plot").strip().lower() or "plot"
    return f"Could not build {chart_type} overlap input: {exc}"


def _coerce_int_like(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return value


def _normalize_plot_token(value: str | None) -> str:
    if not value:
        return ""
    norm = re.sub(r'[^a-z0-9]+', '', str(value).lower())
    if norm.endswith("type"):
        norm = norm[:-4]
    return norm


def _explicit_special_plot_x(message: str) -> str:
    requested_x_match = re.search(
        r'\bby\s+([\w][\w ]*?)(?:\s+in\s+[#A-Za-z]|,|\.|$)',
        message,
        re.IGNORECASE,
    ) or re.search(
        r'\bof\s+([\w][\w ]*?)(?:\s+in\s+[#A-Za-z]|,|\.|$)',
        message,
        re.IGNORECASE,
    )
    if not requested_x_match:
        return ""

    normalized = _normalize_plot_token(requested_x_match.group(1).strip())
    if normalized in {"sample", "samples"}:
        return "sample"
    if normalized in {"measure", "measures", "metric", "metrics", "variable", "variables"}:
        return "measure"
    return ""


def _override_spec_x(spec: dict, explicit_special_x: str) -> dict:
    if not explicit_special_x or _normalize_plot_token(spec.get("x")) == explicit_special_x:
        return spec
    updated = dict(spec)
    updated["x"] = explicit_special_x
    return updated


def _apply_explicit_special_x(plot_specs: list[dict], explicit_special_x: str) -> list[dict]:
    if not explicit_special_x:
        return plot_specs
    return [_override_spec_x(spec, explicit_special_x) for spec in plot_specs]


def _requested_plot_type(message: str) -> str:
    msg = (message or "").lower()
    if "upset" in msg:
        return "upset"
    if "venn" in msg:
        return "venn"
    if "violin" in msg:
        return "violin"
    if "strip" in msg:
        return "strip"
    if re.search(r"\bline\s+(?:chart|plot|graph)\b", msg):
        return "line"
    if re.search(r"\barea\s+(?:chart|plot|graph)\b", msg):
        return "area"
    if "pie" in msg:
        return "pie"
    if "scatter" in msg:
        return "scatter"
    if "box" in msg:
        return "box"
    if "heatmap" in msg or "correlation" in msg:
        return "heatmap"
    if "histogram" in msg or "distribution" in msg:
        return "histogram"
    if "bar" in msg or "plot" in msg or "chart" in msg or "graph" in msg:
        return "bar"
    return ""


def _score_spec(
    spec: dict,
    plot_index: int,
    requested_type: str,
    requested_x: str,
    requested_palette: str,
) -> float:
    score = 0.0
    spec_x = _normalize_plot_token(spec.get("x"))
    spec_palette = _normalize_plot_token(
        spec.get("palette") or spec.get("color")
    )
    spec_type = str(spec.get("type") or "")

    if requested_type and spec_type == requested_type:
        score += 4
    if requested_x:
        if spec_x == requested_x:
            score += 10
        elif spec_x and (requested_x in spec_x or spec_x in requested_x):
            score += 6
    if requested_palette:
        if spec_palette == requested_palette:
            score += 5
    elif spec_palette:
        score -= 2
    if spec.get("agg") == "count":
        score += 1
    if spec.get("x"):
        score += 1
    # Prefer earlier specs on ties to avoid second-pass drift
    score -= plot_index / 1000.0
    return score
