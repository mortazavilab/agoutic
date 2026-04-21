"""Helpers for normalizing edgePython plot parameters before dispatch."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_DPI_PRESETS = {
    "web": 300,
    "draft": 300,
    "publication": 600,
    "print": 600,
    "high res": 600,
    "high resolution": 600,
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


def build_svg_companion_path(output_path: str) -> str:
    """Return the SVG companion path for a raster plot output path."""
    return str(Path(str(output_path).strip()).with_suffix(".svg"))


def normalize_generate_plot_params(params: dict[str, Any], *, text_pool: str = "") -> dict[str, Any]:
    """Normalize `generate_plot` params without widening the public contract."""
    normalized = dict(params)

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