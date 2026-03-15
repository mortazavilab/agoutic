"""Gene annotation tools for Analyzer.

Provides gene ID translation and lookup using the shared GeneAnnotator
from common/gene_annotation.py. These tools are standalone — no dependency
on edgePython or DE state.
"""

import json
from typing import Optional

from common.gene_annotation import GeneAnnotator

_annotator = GeneAnnotator()


def translate_gene_ids(
    gene_ids: list,
    organism: Optional[str] = None,
) -> str:
    """Translate a list of Ensembl gene IDs to gene symbols.

    Args:
        gene_ids: List of Ensembl gene IDs
            (e.g. ["ENSG00000141510", "ENSG00000157764"]).
        organism: 'human' or 'mouse'. Auto-detected from ID prefix
            if not provided.
    """
    if not _annotator.is_available:
        return "Gene annotation data not available. Place reference TSV files in data/reference/."

    ids = [str(g) for g in gene_ids]
    results = _annotator.translate_batch(ids, organism=organism)

    lines = [f"{'Gene ID':<25} {'Symbol':<15}"]
    lines.append("-" * 40)
    for gid in ids:
        symbol = results.get(gid) or "—"
        lines.append(f"{gid:<25} {symbol:<15}")

    n_found = sum(1 for v in results.values() if v is not None)
    lines.append(f"\nTranslated: {n_found}/{len(ids)}")
    return "\n".join(lines)


def lookup_gene(
    gene_symbols: Optional[list] = None,
    gene_ids: Optional[list] = None,
    organism: Optional[str] = None,
) -> str:
    """Look up genes by symbol or Ensembl ID (bidirectional).

    Provide gene_symbols to get Ensembl IDs, or gene_ids to get symbols.
    Can also provide both.

    Args:
        gene_symbols: Gene symbols (e.g. ["TP53", "BRCA1"]).
        gene_ids: Ensembl gene IDs (e.g. ["ENSG00000141510"]).
        organism: 'human' or 'mouse'. Auto-detected if not provided.
    """
    if not _annotator.is_available:
        return "Gene annotation data not available. Place reference TSV files in data/reference/."

    # Accept string arguments (LLM may pass "TP53" instead of ["TP53"],
    # or DATA_CALL parser may pass '["TP53"]' as a JSON-encoded string)
    if isinstance(gene_symbols, str):
        try:
            parsed = json.loads(gene_symbols)
            gene_symbols = parsed if isinstance(parsed, list) else [gene_symbols]
        except (json.JSONDecodeError, ValueError):
            gene_symbols = [gene_symbols]
    if isinstance(gene_ids, str):
        try:
            parsed = json.loads(gene_ids)
            gene_ids = parsed if isinstance(parsed, list) else [gene_ids]
        except (json.JSONDecodeError, ValueError):
            gene_ids = [gene_ids]

    if not gene_symbols and not gene_ids:
        return "Provide gene_symbols (e.g. ['TP53']) or gene_ids (e.g. ['ENSG00000141510'])."

    lines = [f"{'Gene ID':<25} {'Symbol':<15} {'Biotype':<25} {'Name'}"]
    lines.append("-" * 80)

    queries = list(gene_symbols or []) + list(gene_ids or [])
    for q in queries:
        q_str = str(q).strip()
        info = _annotator.lookup(q_str, organism=organism)
        if info:
            lines.append(
                f"{info.get('gene_id', '—'):<25} "
                f"{info.get('symbol', '—'):<15} "
                f"{info.get('biotype', '—'):<25} "
                f"{info.get('name', '—')}"
            )
        else:
            lines.append(f"{q_str:<25} {'—':<15} {'—':<25} (not found)")

    n_found = sum(1 for line in lines[2:] if "(not found)" not in line)
    lines.append(f"\nFound: {n_found}/{len(queries)}")
    return "\n".join(lines)
