"""
Gene ID annotation service for AGOUTIC.

Provides offline Ensembl gene ID -> symbol translation using
pre-built lookup tables. Supports human (ENSG) and mouse (ENSMUSG).

Usage:
    from common.gene_annotation import GeneAnnotator
    annotator = GeneAnnotator()
    annotator.translate("ENSG00000141510")         # -> "TP53"
    annotator.translate("ENSG00000141510.17")       # -> "TP53" (version stripped)
    annotator.annotate_dataframe(df, id_column="GeneID")  # adds Symbol column
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# ---------------------------------------------------------------------------
# Organism detection patterns
# ---------------------------------------------------------------------------

_ORGANISM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^ENSMUSG\d"), "mouse"),
    (re.compile(r"^ENSG\d"), "human"),
    (re.compile(r"^ENSMUST\d"), "mouse"),
    (re.compile(r"^ENST\d"), "human"),
]

# TSV filename convention: <organism>_genes.tsv
_ORGANISM_FILES = {
    "human": "human_genes.tsv",
    "mouse": "mouse_genes.tsv",
}


class GeneAnnotator:
    """Offline gene ID annotation service backed by TSV lookup tables.

    Loads ``<organism>_genes.tsv`` files from ``data_dir/reference/``.
    Each TSV must have at least ``gene_id`` and ``gene_symbol`` columns.

    If no reference files are found the annotator still initialises
    successfully but all lookups return ``None``.
    """

    def __init__(self, data_dir: str | Path | None = None) -> None:
        # gene_id (no version) -> {symbol, name, biotype}
        self._maps: dict[str, dict[str, dict[str, str]]] = {}

        if data_dir is None:
            data_dir = os.environ.get(
                "AGOUTIC_DATA",
                str(Path(__file__).resolve().parent.parent / "data"),
            )

        ref_dir = Path(data_dir) / "reference"
        if not ref_dir.is_dir():
            return

        for organism, filename in _ORGANISM_FILES.items():
            tsv_path = ref_dir / filename
            if tsv_path.is_file():
                self._maps[organism] = _load_tsv(tsv_path)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def strip_version(gene_id: str) -> str:
        """Remove the version suffix from an Ensembl ID.

        ``ENSG00000141510.17`` -> ``ENSG00000141510``
        """
        dot = gene_id.find(".")
        if dot != -1 and gene_id[dot + 1:].isdigit():
            return gene_id[:dot]
        return gene_id

    @staticmethod
    def detect_organism(gene_id: str) -> str | None:
        """Infer organism from an Ensembl ID prefix.

        Returns ``'human'``, ``'mouse'``, or ``None``.
        """
        stripped = GeneAnnotator.strip_version(gene_id.strip())
        for pattern, organism in _ORGANISM_PATTERNS:
            if pattern.match(stripped):
                return organism
        return None

    # ------------------------------------------------------------------
    # Single-gene translation
    # ------------------------------------------------------------------

    def translate(
        self,
        gene_id: str,
        organism: str | None = None,
    ) -> str | None:
        """Translate a single Ensembl gene ID to its symbol.

        Returns ``None`` if the ID is not found or no reference data
        is available.
        """
        stripped = self.strip_version(gene_id.strip())
        if organism is None:
            organism = self.detect_organism(stripped)

        if organism and organism in self._maps:
            entry = self._maps[organism].get(stripped)
            if entry:
                return entry.get("symbol")

        # Try all organisms as fallback
        for org_map in self._maps.values():
            entry = org_map.get(stripped)
            if entry:
                return entry.get("symbol")
        return None

    # ------------------------------------------------------------------
    # Batch translation
    # ------------------------------------------------------------------

    def translate_batch(
        self,
        gene_ids: list[str],
        organism: str | None = None,
    ) -> dict[str, str | None]:
        """Translate a list of gene IDs. Returns ``{original_id: symbol}``."""
        if not gene_ids:
            return {}

        # Auto-detect organism from the first recognisable ID
        if organism is None:
            for gid in gene_ids[:10]:
                organism = self.detect_organism(gid)
                if organism:
                    break

        return {gid: self.translate(gid, organism=organism) for gid in gene_ids}

    # ------------------------------------------------------------------
    # DataFrame annotation
    # ------------------------------------------------------------------

    def annotate_dataframe(
        self,
        df: "pd.DataFrame",
        id_column: str = "GeneID",
        symbol_column: str = "Symbol",
    ) -> "pd.DataFrame":
        """Add a *symbol_column* to *df* by translating *id_column*.

        Modifies *df* in place and also returns it for chaining.
        Missing translations are set to ``None``.
        """
        if id_column not in df.columns:
            return df
        if not self.is_available:
            return df

        ids = list(df[id_column].astype(str))
        mapping = self.translate_batch(ids)
        df[symbol_column] = [mapping.get(gid) for gid in ids]
        return df

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """``True`` if at least one lookup table is loaded."""
        return bool(self._maps)

    @property
    def available_organisms(self) -> list[str]:
        """List of organisms with loaded reference data."""
        return list(self._maps.keys())

    def stats(self) -> dict[str, int]:
        """Gene count per organism, e.g. ``{'human': 62000, 'mouse': 55000}``."""
        return {org: len(m) for org, m in self._maps.items()}


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _load_tsv(path: Path) -> dict[str, dict[str, str]]:
    """Load a gene reference TSV into a lookup dict.

    Expected columns: ``gene_id  gene_symbol  [gene_name  biotype]``
    Returns ``{gene_id: {'symbol': ..., 'name': ..., 'biotype': ...}}``.
    """
    mapping: dict[str, dict[str, str]] = {}
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().strip().split("\t")
        # Map column names to indices
        col_idx: dict[str, int] = {name: i for i, name in enumerate(header)}
        id_idx = col_idx.get("gene_id")
        sym_idx = col_idx.get("gene_symbol")
        if id_idx is None or sym_idx is None:
            return mapping
        name_idx = col_idx.get("gene_name")
        bio_idx = col_idx.get("biotype")

        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(id_idx, sym_idx):
                continue
            gene_id = parts[id_idx].strip()
            symbol = parts[sym_idx].strip()
            if not gene_id or not symbol:
                continue
            entry: dict[str, str] = {"symbol": symbol}
            if name_idx is not None and len(parts) > name_idx:
                entry["name"] = parts[name_idx].strip()
            if bio_idx is not None and len(parts) > bio_idx:
                entry["biotype"] = parts[bio_idx].strip()
            mapping[gene_id] = entry

    return mapping
