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

from atlas.config import CONSORTIUM_REGISTRY
from common.logging_config import get_logger
from cortex.config import SERVICE_REGISTRY
from cortex.llm_validators import get_block_payload

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEARCH_TOOLS = frozenset({
    "search_by_biosample", "search_by_target",
    "search_by_organism", "search_by_assay", "list_experiments",
})

_FILE_TOOLS = frozenset({"get_files_by_type"})

_PLOT_KEYWORDS = frozenset({
    "plot", "chart", "pie", "histogram", "scatter", "bar chart",
    "box plot", "heatmap", "visualize", "graph", "distribution",
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
        if "experiments" in rd and isinstance(rd["experiments"], list):
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
    user_wants_plot = any(kw in user_message.lower() for kw in _PLOT_KEYWORDS)

    if not (user_wants_plot and plot_specs and not user_explicit_df):
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
        if isinstance(ps.get("df_id"), int) and ps.get("df_id") != newest_turn_df_id
    })
    for ps in plot_specs:
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
    user_wants_plot = any(kw in user_message.lower() for kw in _PLOT_KEYWORDS)
    has_valid_plot = plot_specs and any(
        s.get("df_id") is not None for s in plot_specs
    )

    if user_wants_plot and not has_valid_plot and embedded_dataframes:
        newest_df_id = None
        for edf_val in embedded_dataframes.values():
            edf_id = edf_val.get("metadata", {}).get("df_id")
            if isinstance(edf_id, int):
                if newest_df_id is None or edf_id > newest_df_id:
                    newest_df_id = edf_id

        if newest_df_id is not None:
            auto_type = _detect_chart_type(user_message)
            auto_x = _detect_x_column(user_message)
            auto_post_spec: dict = {
                "type": auto_type,
                "df_id": newest_df_id,
                "df": f"DF{newest_df_id}",
                "agg": "count",
            }
            if auto_x:
                auto_post_spec["x"] = auto_x
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
            "color": ps.get("color"),
            "palette": ps.get("palette"),
            "agg": ps.get("agg"),
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
        return plot_specs

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
    requested_type = _requested_plot_type(user_message)

    grouped: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for idx, spec in enumerate(plot_specs):
        grouped[(spec.get("df_id"), spec.get("type"))].append((idx, spec))

    selected: list[dict] = []
    for group_specs in grouped.values():
        if len(group_specs) == 1:
            selected.append(group_specs[0][1])
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
            selected.append(best_spec)

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


def _detect_x_column(message: str) -> str | None:
    by_m = re.search(r'\bby\s+(\w+)', message, re.IGNORECASE)
    if by_m and not re.match(r'DF\d+', by_m.group(1), re.IGNORECASE):
        return by_m.group(1)
    of_m = re.search(r'\b(?:of|for)\s+(\w+)', message, re.IGNORECASE)
    if of_m and not re.match(r'DF\d+', of_m.group(1), re.IGNORECASE):
        return of_m.group(1)
    return None


def _normalize_plot_token(value: str | None) -> str:
    if not value:
        return ""
    norm = re.sub(r'[^a-z0-9]+', '', str(value).lower())
    if norm.endswith("type"):
        norm = norm[:-4]
    return norm


def _requested_plot_type(message: str) -> str:
    msg = (message or "").lower()
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
