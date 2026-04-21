"""Helpers for normalizing edgePython plot parameters before dispatch."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cortex.plot_routing import normalize_plot_type

_DPI_PRESETS = {
    "web": 300,
    "draft": 300,
    "publication": 600,
    "print": 600,
    "high res": 900,
    "high resolution": 900,
    "poster": 900,
    "journal max": 1200,
}

_DPI_RE = re.compile(
    r"\b(?:dpi|resolution)\s*(?:=|:|at|to)?\s*(\d{2,4})\b|\b(\d{2,4})\s*dpi\b",
    re.IGNORECASE,
)

_PLOT_TRANSCRIPT_LABEL_RE = re.compile(
    r"(?:\b(?:with|show|include|display|label|labels|annotat(?:e|ed|ion))\b.{0,30}\b(?:transcript|isoform)(?:\s+(?:id|ids|name|names|label|labels))?s?\b)|(?:\b(?:transcript|isoform)(?:\s+(?:id|ids|name|names|label|labels))?s?\b.{0,20}\b(?:on|in)\s+the\s+plot\b)",
    re.IGNORECASE,
)

_PLOT_TYPE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:principal\s+component(?:s)?(?:\s+analysis)?|pca)\b", re.IGNORECASE), "pca"),
    (re.compile(r"\bstacked\s+bar\b", re.IGNORECASE), "stacked_bar"),
    (re.compile(r"\bheat\s*map\b|\bheatmap\b", re.IGNORECASE), "heatmap"),
    (re.compile(r"\bvolcano\b", re.IGNORECASE), "volcano"),
    (re.compile(r"\bma\s+plot\b|\bmean\s+average\b", re.IGNORECASE), "ma"),
    (re.compile(r"\bmd\s+plot\b|\bmean[- ]difference\b", re.IGNORECASE), "md"),
    (re.compile(r"\bbar(?:\s+chart|\s+plot|\s+graph)?\b", re.IGNORECASE), "bar"),
)

_STACKED_MODE_RE = re.compile(r"\bstacked\b", re.IGNORECASE)
_PERCENT_MODE_RE = re.compile(r"\b(?:percent|percentage|normalized|normalised)\b", re.IGNORECASE)
_GROUP_MODE_RE = re.compile(r"\b(?:grouped|side[- ]by[- ]side|dodged)\b", re.IGNORECASE)


def _normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_]", " ", value.strip().lower())).strip()


def coerce_plot_dpi(value: Any) -> int | None:
    """Return a numeric DPI from an int-like value or supported preset phrase."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        dpi = int(value)
        return dpi if dpi > 0 else None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        dpi = int(text)
        return dpi if dpi > 0 else None

    return _DPI_PRESETS.get(_normalize_phrase(text))


def coerce_optional_bool(value: Any) -> bool | None:
    """Return a bool for common true/false spellings, or None when unclear."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = _normalize_phrase(str(value))
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def extract_plot_dpi(text: str) -> int | None:
    """Extract a requested DPI from user text using numeric or named presets."""
    if not text:
        return None

    match = _DPI_RE.search(text)
    if match:
        value = match.group(1) or match.group(2)
        return coerce_plot_dpi(value)

    normalized = _normalize_phrase(text)
    for phrase in sorted(_DPI_PRESETS.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            return _DPI_PRESETS[phrase]

    return None


def extract_label_transcripts(text: str) -> bool:
    """Return True when the user explicitly asks for transcript/isoform labels."""
    if not text:
        return False
    return bool(_PLOT_TRANSCRIPT_LABEL_RE.search(text))


def extract_plot_type(text: str) -> str | None:
    """Infer a generate_plot plot_type from the surrounding user text."""
    if not text:
        return None
    for pattern, plot_type in _PLOT_TYPE_HINTS:
        if pattern.search(text):
            return plot_type
    return None


def normalize_bar_mode(value: Any) -> str | None:
    """Normalize supported bar mode aliases to group/stack/percent."""
    if value is None or value == "":
        return None
    normalized = _normalize_phrase(str(value))
    aliases = {
        "group": "group",
        "grouped": "group",
        "stack": "stack",
        "stacked": "stack",
        "percent": "percent",
        "percentage": "percent",
        "normalized": "percent",
        "normalised": "percent",
    }
    return aliases.get(normalized)


def extract_bar_mode(text: str, *, plot_type: str | None = None) -> str | None:
    """Infer group/stack/percent mode from the user request when omitted."""
    if not text:
        return "stack" if plot_type == "stacked_bar" else None
    if _PERCENT_MODE_RE.search(text):
        return "percent"
    if _STACKED_MODE_RE.search(text):
        return "stack"
    if _GROUP_MODE_RE.search(text):
        return "group"
    if plot_type == "stacked_bar":
        return "stack"
    return None


def build_svg_companion_path(output_path: str) -> str:
    """Return the SVG companion path for a raster plot output path."""
    return str(Path(str(output_path).strip()).with_suffix(".svg"))


def normalize_generate_plot_params(params: dict[str, Any], *, text_pool: str = "") -> dict[str, Any]:
    """Normalize `generate_plot` params without widening the public contract."""
    normalized = dict(params)

    if normalized.get("plot_type"):
        normalized["plot_type"] = normalize_plot_type(str(normalized.get("plot_type") or ""))
    else:
        inferred_plot_type = extract_plot_type(text_pool)
        if inferred_plot_type:
            normalized["plot_type"] = inferred_plot_type

    if normalized.get("plot_type") == "bar":
        hinted_plot_type = extract_plot_type(text_pool)
        if hinted_plot_type == "stacked_bar":
            normalized["plot_type"] = hinted_plot_type

    df_ref = normalized.get("df")
    if df_ref and not normalized.get("df_id"):
        match = re.match(r'(?:DF)?\s*(\d+)', str(df_ref), re.IGNORECASE)
        if match:
            normalized["df_id"] = int(match.group(1))

    mode = normalize_bar_mode(normalized.get("mode"))
    if mode is None:
        mode = extract_bar_mode(text_pool, plot_type=str(normalized.get("plot_type") or ""))
    if mode is not None:
        normalized["mode"] = mode

    if "resolution" in normalized and not normalized.get("dpi"):
        normalized["dpi"] = normalized.pop("resolution")

    dpi = coerce_plot_dpi(normalized.get("dpi"))
    if dpi is None:
        dpi = extract_plot_dpi(text_pool)
    if dpi is not None:
        normalized["dpi"] = dpi

    label_transcripts = coerce_optional_bool(normalized.get("label_transcripts"))
    if label_transcripts is None and extract_label_transcripts(text_pool):
        label_transcripts = True
    if label_transcripts is not None:
        normalized["label_transcripts"] = label_transcripts

    output_path = normalized.get("output_path")
    if output_path and not normalized.get("svg_output_path"):
        normalized["svg_output_path"] = build_svg_companion_path(str(output_path))

    return normalized