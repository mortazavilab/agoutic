"""Shared plot routing helpers for declarative and server-side charts."""

from __future__ import annotations

import re
from typing import Any

INTERACTIVE_PLOT_TYPES = frozenset({
    "histogram",
    "scatter",
    "line",
    "area",
    "box",
    "violin",
    "strip",
    "pie",
    "venn",
    "upset",
    "bar",
})

EDGEPYTHON_PLOT_TYPES = frozenset({
    "volcano",
    "md",
    "ma",
    "pca",
    "heatmap",
    "stacked_bar",
    "enrichment_bar",
    "enrichment_dot",
})

_PLOT_TYPE_ALIASES = {
    "mean_difference": "md",
    "mean_difference_plot": "md",
    "mean_average": "ma",
    "ma_plot": "ma",
    "md_plot": "md",
    "stacked": "stacked_bar",
    "stackedbar": "stacked_bar",
    "stacked_bar_chart": "stacked_bar",
    "stacked_bar_plot": "stacked_bar",
    "bar_chart": "bar",
    "line_chart": "line",
    "area_chart": "area",
    "box_plot": "box",
    "violin_plot": "violin",
    "strip_plot": "strip",
    "pie_chart": "pie",
    "volcano_plot": "volcano",
    "heat_map": "heatmap",
}

_PUBLICATION_CONTEXT_RE = re.compile(
    r"\b(?:publication|print|poster|figure|dissertation|thesis|chapter|journal|manuscript|paper)\b",
    re.IGNORECASE,
)

_ON_IMAGE_LABEL_RE = re.compile(
    r"\b(?:label|labels|annotat(?:e|ed|ion)|transcript(?:s| ids?)?|isoform(?:s| labels?)?|outlier(?:s)?|sample ids?)\b",
    re.IGNORECASE,
)

_BAR_EDGEPYTHON_HINT_RE = re.compile(
    r"\b(?:total|totals|value labels?|error bars?|standard error|confidence interval|ci\b|grouped|group by|grouped by|side[- ]by[- ]side|dodged|direction)\b",
    re.IGNORECASE,
)

_DE_SUMMARY_BAR_RE = re.compile(
    r"\b(?:deg|differential(?:ly)?\s+expressed\s+genes?)\b.*\bcounts?(?:\s+per\s+contrast)?\b"
    r"|\bcounts?\s+per\s+contrast\b"
    r"|\bcontrast\b.*\bdeg\b",
    re.IGNORECASE,
)


def normalize_plot_type(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    normalized = re.sub(r"_+", "_", re.sub(r"[-/\s]+", "_", text)).strip("_")
    return _PLOT_TYPE_ALIASES.get(normalized, normalized)


def detect_chart_type(message: str) -> str:
    msg = (message or "").lower()
    if "volcano" in msg:
        return "volcano"
    if re.search(r"\b(?:pca|principal\s+component(?:s)?\s+analysis)\b", msg):
        return "pca"
    if re.search(r"\bstacked\s+bar\b", msg):
        return "stacked_bar"
    if re.search(r"\b(?:ma|md)\s+plot\b", msg) or "mean difference" in msg or "mean-difference" in msg:
        return "md"
    if "heatmap" in msg or "correlation" in msg:
        return "heatmap"
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
    if "histogram" in msg or "distribution" in msg:
        return "histogram"
    return "bar"


def has_publication_context(message: str) -> bool:
    return bool(_PUBLICATION_CONTEXT_RE.search(message or ""))


def plot_requests_baked_in_labels(
    plot_type: str | None,
    *,
    user_message: str = "",
    params: dict[str, Any] | None = None,
) -> bool:
    normalized = normalize_plot_type(plot_type)
    if normalized in EDGEPYTHON_PLOT_TYPES:
        return True

    params = params or {}
    if params.get("label_transcripts") or params.get("label_genes") or params.get("top_n_labels"):
        return True

    if normalized == "scatter":
        return bool(_ON_IMAGE_LABEL_RE.search(user_message or ""))

    return False


def bar_requires_edgepython(
    *,
    user_message: str = "",
    params: dict[str, Any] | None = None,
) -> bool:
    params = params or {}
    if has_publication_context(user_message):
        return True
    if _BAR_EDGEPYTHON_HINT_RE.search(user_message or ""):
        return True
    if _DE_SUMMARY_BAR_RE.search(user_message or ""):
        return True

    mode = normalize_plot_type(str(params.get("mode") or ""))
    if mode in {"stack", "stacked", "percent", "percentage", "normalize", "normalized"}:
        return True
    if params.get("color"):
        return True
    return False


def infer_plot_route(
    plot_type: str | None,
    *,
    user_message: str = "",
    params: dict[str, Any] | None = None,
) -> str:
    normalized = normalize_plot_type(plot_type) or detect_chart_type(user_message)
    if normalized == "bar":
        return "edgepython" if bar_requires_edgepython(user_message=user_message, params=params) else "declarative"
    if normalized in EDGEPYTHON_PLOT_TYPES:
        return "edgepython"
    if plot_requests_baked_in_labels(normalized, user_message=user_message, params=params):
        return "edgepython"
    if normalized in INTERACTIVE_PLOT_TYPES:
        return "declarative"
    return "edgepython"


def legacy_declarative_plot_warning(
    plot_type: str | None,
    *,
    user_message: str = "",
    params: dict[str, Any] | None = None,
) -> str | None:
    normalized = normalize_plot_type(plot_type)
    if normalized == "heatmap":
        return (
            "Legacy declarative heatmap tag preserved for compatibility; "
            "new heatmaps should route through edgePython generate_plot."
        )
    if normalized == "bar" and bar_requires_edgepython(user_message=user_message, params=params):
        return (
            "Legacy declarative bar tag with publication-only or grouped features preserved for compatibility; "
            "new complex bars should route through edgePython generate_plot."
        )
    return None
    