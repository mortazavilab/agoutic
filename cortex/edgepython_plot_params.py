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

    output_path = normalized.get("output_path")
    if output_path and not normalized.get("svg_output_path"):
        normalized["svg_output_path"] = build_svg_companion_path(str(output_path))

    return normalized