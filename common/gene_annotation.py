"""Gene and transcript annotation service for AGOUTIC.

Provides offline Ensembl ID <-> symbol translation using colocated GTF-derived
TSV caches. Shared reference caches are generated from configured GTFs on first
use when needed, while workflow-specific caches can be loaded directly from a
reference or reconciled GTF.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from common.gtf_parser import (
    cache_path_for_gtf,
    ensure_reference_caches,
    load_gene_cache,
    load_or_parse,
    load_transcript_cache,
    parse_gtf,
)

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

_FEATURE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^ENSMUST\d"), "transcript"),
    (re.compile(r"^ENST\d"), "transcript"),
    (re.compile(r"^ENSMUSG\d"), "gene"),
    (re.compile(r"^ENSG\d"), "gene"),
]

# TSV filename convention: <organism>_genes.tsv
_ORGANISM_FILES = {
    "human": "human_genes.tsv",
    "mouse": "mouse_genes.tsv",
}


class GeneAnnotator:
    """Offline gene/transcript annotation service backed by TSV lookup tables."""

    def __init__(self, data_dir: str | Path | None = None, gtf_path: str | Path | None = None) -> None:
        # gene_id (no version) -> {symbol, name, biotype}
        self._maps: dict[str, dict[str, dict[str, str]]] = {}
        # symbol (upper-case) -> gene_id  (built lazily)
        self._reverse_maps: dict[str, dict[str, str]] = {}
        # transcript_id (no version) -> {gene_id, symbol, name, biotype}
        self._transcript_maps: dict[str, dict[str, dict[str, str]]] = {}
        self._explicit_data_dir = data_dir is not None
        self._reference_autoload_attempted = False
        self._legacy_loaded = False

        if data_dir is None:
            data_dir = os.environ.get(
                "AGOUTIC_DATA",
                str(Path(__file__).resolve().parent.parent / "data"),
            )

        self._data_dir = Path(data_dir).expanduser().resolve()
        self._load_colocated_reference_caches()
        if gtf_path is not None:
            self.load_gtf(gtf_path)

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

    @staticmethod
    def detect_feature_type(identifier: str) -> str | None:
        """Infer whether an identifier is gene-like or transcript-like."""
        stripped = GeneAnnotator.strip_version(str(identifier or "").strip())
        for pattern, feature_type in _FEATURE_PATTERNS:
            if pattern.match(stripped):
                return feature_type
        return None

    def _ensure_reference_data_loaded(self) -> None:
        if self._reference_autoload_attempted:
            return
        self._reference_autoload_attempted = True

        if not self._explicit_data_dir:
            try:
                ensure_reference_caches()
            except Exception:
                pass
            self._load_colocated_reference_caches(overwrite=not bool(self._maps or self._transcript_maps))

        if not self._legacy_loaded:
            self._load_legacy_reference_tables(overwrite=not bool(self._maps or self._transcript_maps))

    def _merge_gene_map(
        self,
        organism: str,
        mapping: dict[str, dict[str, str]],
        *,
        overwrite: bool = True,
    ) -> None:
        if not mapping:
            return
        org_map = self._maps.setdefault(organism, {})
        if overwrite:
            org_map.update(mapping)
        else:
            for key, value in mapping.items():
                org_map.setdefault(key, value)
        self._reverse_maps.pop(organism, None)

    def _merge_transcript_map(
        self,
        organism: str,
        mapping: dict[str, dict[str, str]],
        *,
        overwrite: bool = True,
    ) -> None:
        if not mapping:
            return
        org_map = self._transcript_maps.setdefault(organism, {})
        if overwrite:
            org_map.update(mapping)
        else:
            for key, value in mapping.items():
                org_map.setdefault(key, value)

    def _infer_organism_label(
        self,
        mapping: dict[str, dict[str, str]],
        fallback: str | None = None,
    ) -> str:
        for identifier in list(mapping.keys())[:10]:
            organism = self.detect_organism(identifier)
            if organism:
                return organism
        return fallback or "custom"

    def _organism_label_from_path(self, path: Path) -> str:
        parent = path.parent.name
        if parent.lower() in {"grch38", "hg38", "human"}:
            return "human"
        if parent.lower() in {"mm39", "mm10", "mouse"}:
            return "mouse"
        if path.name.startswith("human_"):
            return "human"
        if path.name.startswith("mouse_"):
            return "mouse"
        return parent or "custom"

    def _load_colocated_reference_caches(self, *, overwrite: bool = True) -> None:
        references_dir = self._data_dir / "references"
        if not references_dir.is_dir():
            return

        for gene_cache_path in sorted(references_dir.rglob("*.genes.tsv")):
            genes = load_gene_cache(gene_cache_path)
            transcripts = load_transcript_cache(gene_cache_path.with_name(gene_cache_path.name.replace(".genes.tsv", ".transcripts.tsv")))
            organism = self._infer_organism_label(genes, fallback=self._organism_label_from_path(gene_cache_path))
            self._merge_gene_map(organism, genes, overwrite=overwrite)
            self._merge_transcript_map(organism, transcripts, overwrite=overwrite)

    def _load_legacy_reference_tables(self, *, overwrite: bool = True) -> None:
        ref_dir = self._data_dir / "reference"
        self._legacy_loaded = True
        if not ref_dir.is_dir():
            return
        for organism, filename in _ORGANISM_FILES.items():
            tsv_path = ref_dir / filename
            if tsv_path.is_file():
                self._merge_gene_map(organism, _load_tsv(tsv_path), overwrite=overwrite)

    def _resolve_reference_genome(self, genome_name: str) -> tuple[str, str | None]:
        from cortex.config import GENOME_ALIASES
        from launchpad.config import REFERENCE_GENOMES

        if genome_name in REFERENCE_GENOMES:
            canonical = genome_name
        else:
            canonical = GENOME_ALIASES.get(str(genome_name).strip().lower(), genome_name)
        organism = "human" if str(canonical).lower() in {"grch38", "hg38", "human"} else None
        if organism is None and str(canonical).lower() in {"mm39", "mm10", "mouse"}:
            organism = "mouse"
        return canonical, organism

    def load_gtf(
        self,
        gtf_path: str | Path,
        organism: str | None = None,
        cache: bool = True,
    ) -> dict[str, str | int | None]:
        """Load annotations from a GTF, caching colocated TSVs by default."""
        parsed = load_or_parse(gtf_path) if cache else parse_gtf(gtf_path)
        organism_label = organism or parsed.get("organism") or self._infer_organism_label(parsed["genes"])
        self._merge_gene_map(organism_label, parsed["genes"], overwrite=True)
        self._merge_transcript_map(organism_label, parsed["transcripts"], overwrite=True)
        return {
            "organism": organism_label,
            "gtf_path": str(parsed["gtf_path"]),
            "gene_cache_path": str(parsed.get("gene_cache_path") or cache_path_for_gtf(gtf_path, ".genes.tsv")),
            "transcript_cache_path": str(parsed.get("transcript_cache_path") or cache_path_for_gtf(gtf_path, ".transcripts.tsv")),
            "status": str(parsed.get("status") or "parsed"),
            "gene_count": int(parsed.get("gene_count") or 0),
            "transcript_count": int(parsed.get("transcript_count") or 0),
        }

    def load_reference_gtf(self, genome_name: str) -> dict[str, str | int | None]:
        """Load a shared configured reference GTF by genome name or alias."""
        from launchpad.config import REFERENCE_GENOMES

        canonical, organism = self._resolve_reference_genome(genome_name)
        config = REFERENCE_GENOMES.get(canonical)
        if not isinstance(config, dict) or not config.get("gtf"):
            raise ValueError(f"No configured reference GTF for genome '{genome_name}'.")
        return self.load_gtf(config["gtf"], organism=organism, cache=True)

    def load_reconciled_gtf(self, work_dir: str | Path) -> dict[str, str | int | None] | None:
        """Load reconciled GTF annotations from a workflow directory when present."""
        root = Path(work_dir).expanduser().resolve()
        for candidate in (root / "reconciled.gtf", root / "reconciled.gtf.gz"):
            if candidate.is_file():
                return self.load_gtf(candidate, cache=True)
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
        self._ensure_reference_data_loaded()
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
                return entry.get("symbol") or None
        return None

    def translate_transcript(
        self,
        transcript_id: str,
        organism: str | None = None,
    ) -> str | None:
        """Translate a transcript ID to a gene symbol using transcript caches."""
        self._ensure_reference_data_loaded()
        stripped = self.strip_version(transcript_id.strip())
        if organism is None:
            organism = self.detect_organism(stripped)

        if organism and organism in self._transcript_maps:
            entry = self._transcript_maps[organism].get(stripped)
            if entry:
                return entry.get("symbol") or None

        for org_map in self._transcript_maps.values():
            entry = org_map.get(stripped)
            if entry:
                return entry.get("symbol") or None
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
        self._ensure_reference_data_loaded()
        if not gene_ids:
            return {}

        # Auto-detect organism from the first recognisable ID
        if organism is None:
            for gid in gene_ids[:10]:
                organism = self.detect_organism(gid)
                if organism:
                    break

        return {gid: self.translate(gid, organism=organism) for gid in gene_ids}

    def translate_transcript_batch(
        self,
        transcript_ids: list[str],
        organism: str | None = None,
    ) -> dict[str, str | None]:
        """Translate a list of transcript IDs to gene symbols."""
        self._ensure_reference_data_loaded()
        if not transcript_ids:
            return {}

        if organism is None:
            for transcript_id in transcript_ids[:10]:
                organism = self.detect_organism(transcript_id)
                if organism:
                    break

        return {tx_id: self.translate_transcript(tx_id, organism=organism) for tx_id in transcript_ids}

    # ------------------------------------------------------------------
    # Reverse lookup (symbol -> gene ID)
    # ------------------------------------------------------------------

    def _ensure_reverse_map(self, organism: str) -> dict[str, str]:
        """Build the reverse map for *organism* on first access."""
        self._ensure_reference_data_loaded()
        if organism not in self._reverse_maps and organism in self._maps:
            rev: dict[str, str] = {}
            for gene_id, entry in self._maps[organism].items():
                sym = entry.get("symbol")
                if sym:
                    rev[sym.upper()] = gene_id
            self._reverse_maps[organism] = rev
        return self._reverse_maps.get(organism, {})

    def reverse_translate(
        self,
        symbol: str,
        organism: str | None = None,
    ) -> str | None:
        """Translate a gene symbol to its Ensembl gene ID.

        Returns ``None`` if the symbol is not found.
        """
        self._ensure_reference_data_loaded()
        key = symbol.strip().upper()
        if organism:
            rev = self._ensure_reverse_map(organism)
            if key in rev:
                return rev[key]

        for org in self._maps:
            rev = self._ensure_reverse_map(org)
            if key in rev:
                return rev[key]
        return None

    def lookup(
        self,
        query: str,
        organism: str | None = None,
    ) -> dict[str, str | None] | None:
        """Look up a gene by either Ensembl ID or symbol.

        Returns a dict with ``gene_id``, ``symbol``, ``name``, ``biotype``
        keys, or ``None`` if not found.
        """
        self._ensure_reference_data_loaded()
        query = query.strip()
        # Try as Ensembl ID first (reuse translate's lookup path)
        stripped = self.strip_version(query)
        feature_type = self.detect_feature_type(stripped)
        if feature_type == "transcript":
            return self.lookup_transcript(query, organism=organism)
        detected_org = self.detect_organism(stripped)
        if detected_org:
            org = organism or detected_org
            for org_key in ([org] + [k for k in self._maps if k != org]):
                if org_key not in self._maps:
                    continue
                entry = self._maps[org_key].get(stripped)
                if entry:
                    return {"gene_id": stripped, **entry}

        # Try as symbol (delegate to reverse_translate)
        gene_id = self.reverse_translate(query, organism=organism)
        if gene_id:
            for org_map in self._maps.values():
                entry = org_map.get(gene_id)
                if entry:
                    return {"gene_id": gene_id, **entry}
        return None

    def lookup_transcript(
        self,
        transcript_id: str,
        organism: str | None = None,
    ) -> dict[str, str | None] | None:
        """Look up a transcript ID and its mapped gene annotation."""
        self._ensure_reference_data_loaded()
        stripped = self.strip_version(transcript_id.strip())
        if organism is None:
            organism = self.detect_organism(stripped)

        org_keys = [organism] if organism else []
        org_keys.extend([org for org in self._transcript_maps if org not in org_keys])
        for org_key in org_keys:
            if org_key not in self._transcript_maps:
                continue
            entry = self._transcript_maps[org_key].get(stripped)
            if entry:
                return {"transcript_id": stripped, **entry}
        return None

    # ------------------------------------------------------------------
    # DataFrame annotation
    # ------------------------------------------------------------------

    def annotate_dataframe(
        self,
        df: "pd.DataFrame",
        id_column: str = "GeneID",
        symbol_column: str = "Symbol",
        organism: str | None = None,
        feature_type: str | None = None,
    ) -> "pd.DataFrame":
        """Add a *symbol_column* to *df* by translating *id_column*.

        Modifies *df* in place and also returns it for chaining.
        Missing translations are set to ``None``.
        """
        if id_column not in df.columns:
            return df
        self._ensure_reference_data_loaded()
        if not self.is_available:
            return df

        ids = list(df[id_column].astype(str))
        if feature_type is None:
            for identifier in ids[:10]:
                feature_type = self.detect_feature_type(identifier)
                if feature_type:
                    break
        if feature_type == "transcript":
            mapping = self.translate_transcript_batch(ids, organism=organism)
        else:
            mapping = self.translate_batch(ids, organism=organism)
        df[symbol_column] = [mapping.get(identifier) for identifier in ids]
        series = df[symbol_column].astype(object)
        df[symbol_column] = series.where(series.notna(), None)
        return df

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """``True`` if at least one lookup table is loaded."""
        self._ensure_reference_data_loaded()
        return bool(self._maps or self._transcript_maps)

    @property
    def available_organisms(self) -> list[str]:
        """List of organisms with loaded reference data."""
        self._ensure_reference_data_loaded()
        return sorted(set(self._maps) | set(self._transcript_maps))

    def stats(self) -> dict[str, int]:
        """Gene count per organism, e.g. ``{'human': 62000, 'mouse': 55000}``."""
        self._ensure_reference_data_loaded()
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
            if len(parts) <= id_idx:
                continue
            gene_id = GeneAnnotator.strip_version(parts[id_idx].strip())
            if not gene_id:
                continue
            entry: dict[str, str] = {"symbol": parts[sym_idx].strip() if len(parts) > sym_idx else ""}
            if name_idx is not None and len(parts) > name_idx:
                entry["name"] = parts[name_idx].strip()
            if bio_idx is not None and len(parts) > bio_idx:
                entry["biotype"] = parts[bio_idx].strip()
            mapping[gene_id] = entry

    return mapping
