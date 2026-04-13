"""Gene annotation tools for Analyzer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from common.gene_annotation import GeneAnnotator

_annotator = GeneAnnotator()


def _annotation_unavailable_message() -> str:
    return (
        "Gene annotation data not available. Start servers to auto-build shared "
        "reference caches or run build_gene_cache() for a specific GTF."
    )


def _normalize_query_list(values: Optional[list | str]) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        try:
            parsed = json.loads(values)
            if isinstance(parsed, list):
                return [str(value) for value in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        return [values]
    return [str(value) for value in values]


def translate_gene_ids(
    gene_ids: Optional[list] = None,
    transcript_ids: Optional[list] = None,
    organism: Optional[str] = None,
) -> str:
    """Translate gene or transcript IDs to gene symbols.

    Args:
        gene_ids: Optional list of Ensembl gene IDs.
        transcript_ids: Optional list of Ensembl transcript IDs.
        organism: Optional organism label. Auto-detected from ID prefix if not provided.
    """
    if not _annotator.is_available:
        return _annotation_unavailable_message()

    gene_list = _normalize_query_list(gene_ids)
    transcript_list = _normalize_query_list(transcript_ids)
    if not gene_list and not transcript_list:
        return "Provide gene_ids or transcript_ids."

    lines = [f"{'Type':<12} {'Identifier':<25} {'Symbol':<15}"]
    lines.append("-" * 58)

    n_found = 0
    if gene_list:
        results = _annotator.translate_batch(gene_list, organism=organism)
        for gene_id in gene_list:
            symbol = results.get(gene_id) or "—"
            if results.get(gene_id) is not None:
                n_found += 1
            lines.append(f"{'gene':<12} {gene_id:<25} {symbol:<15}")

    if transcript_list:
        results = _annotator.translate_transcript_batch(transcript_list, organism=organism)
        for transcript_id in transcript_list:
            symbol = results.get(transcript_id) or "—"
            if results.get(transcript_id) is not None:
                n_found += 1
            lines.append(f"{'transcript':<12} {transcript_id:<25} {symbol:<15}")

    total = len(gene_list) + len(transcript_list)
    lines.append(f"\nTranslated: {n_found}/{total}")
    return "\n".join(lines)


def build_gene_cache(gtf_path: str, organism: Optional[str] = None) -> str:
    """Build colocated cache TSVs for a specific GTF and load them immediately."""
    resolved = Path(gtf_path).expanduser().resolve()
    info = _annotator.load_gtf(resolved, organism=organism, cache=True)
    return (
        f"Cached {info['gene_count']:,} genes and {info['transcript_count']:,} transcripts from "
        f"{resolved}. Gene cache: {info['gene_cache_path']}"
    )


def lookup_gene(
    gene_symbols: Optional[list] = None,
    gene_ids: Optional[list] = None,
    transcript_ids: Optional[list] = None,
    organism: Optional[str] = None,
) -> str:
    """Look up genes or transcripts by symbol or Ensembl ID.

    Provide gene_symbols to get Ensembl IDs, or gene_ids to get symbols.
    Can also provide transcript_ids.

    Args:
        gene_symbols: Gene symbols (e.g. ["TP53", "BRCA1"]).
        gene_ids: Ensembl gene IDs (e.g. ["ENSG00000141510"]).
        transcript_ids: Ensembl transcript IDs (e.g. ["ENST00000378418"]).
        organism: Optional organism label. Auto-detected if not provided.
    """
    if not _annotator.is_available:
        return _annotation_unavailable_message()

    symbol_queries = _normalize_query_list(gene_symbols)
    gene_queries = _normalize_query_list(gene_ids)
    transcript_queries = _normalize_query_list(transcript_ids)

    if not symbol_queries and not gene_queries and not transcript_queries:
        return (
            "Provide gene_symbols (e.g. ['TP53']), gene_ids (e.g. ['ENSG00000141510']), "
            "or transcript_ids (e.g. ['ENST00000378418'])."
        )

    lines = [f"{'Type':<12} {'Identifier':<25} {'Gene ID':<25} {'Symbol':<15} {'Biotype':<25} {'Name'}"]
    lines.append("-" * 118)

    n_found = 0
    for query in symbol_queries:
        q_str = str(query).strip()
        info = _annotator.lookup(q_str, organism=organism)
        if info:
            n_found += 1
            lines.append(
                f"{'symbol':<12} {q_str:<25} {info.get('gene_id', '—'):<25} "
                f"{info.get('symbol', '—'):<15} {info.get('biotype', '—'):<25} {info.get('name', '—')}"
            )
        else:
            lines.append(f"{'symbol':<12} {q_str:<25} {'—':<25} {'—':<15} {'—':<25} (not found)")

    for query in gene_queries:
        q_str = str(query).strip()
        info = _annotator.lookup(q_str, organism=organism)
        if info:
            n_found += 1
            lines.append(
                f"{'gene':<12} {q_str:<25} {info.get('gene_id', '—'):<25} "
                f"{info.get('symbol', '—'):<15} {info.get('biotype', '—'):<25} {info.get('name', '—')}"
            )
        else:
            lines.append(f"{'gene':<12} {q_str:<25} {'—':<25} {'—':<15} {'—':<25} (not found)")

    for query in transcript_queries:
        q_str = str(query).strip()
        info = _annotator.lookup_transcript(q_str, organism=organism)
        if info:
            n_found += 1
            lines.append(
                f"{'transcript':<12} {info.get('transcript_id', q_str):<25} {info.get('gene_id', '—'):<25} "
                f"{info.get('symbol', '—'):<15} {info.get('biotype', '—'):<25} {info.get('name', '—')}"
            )
        else:
            lines.append(f"{'transcript':<12} {q_str:<25} {'—':<25} {'—':<15} {'—':<25} (not found)")

    total = len(symbol_queries) + len(gene_queries) + len(transcript_queries)
    lines.append(f"\nFound: {n_found}/{total}")
    return "\n".join(lines)
