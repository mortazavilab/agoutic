"""Enrichment analysis engine for Analyzer.

Provides GO and pathway enrichment via g:Profiler, with per-conversation
state management. These tools accept explicit gene lists only — no
dependency on edgePython DE state.
"""

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Per-conversation enrichment state
# ---------------------------------------------------------------------------

_enrichment_state: dict[str, dict] = {}


def _get_state(conversation_id: str) -> dict:
    """Get or create per-conversation enrichment state."""
    if conversation_id not in _enrichment_state:
        _enrichment_state[conversation_id] = {
            "enrichment_results": {},
            "last_enrichment": None,
        }
    return _enrichment_state[conversation_id]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_species_code(gene_ids: list) -> str:
    """Auto-detect species from gene ID prefixes. Returns g:Profiler species."""
    if not gene_ids:
        return "hsapiens"
    sample = [str(g) for g in gene_ids[:20]]
    ensg_count = sum(1 for g in sample if g.startswith("ENSG0"))
    ensmusg_count = sum(1 for g in sample if g.startswith("ENSMUSG"))
    if ensmusg_count > ensg_count:
        return "mmusculus"
    return "hsapiens"


def _parse_gene_list(gene_list: str) -> list[str]:
    """Parse comma-separated gene list string into list."""
    return [g.strip() for g in gene_list.split(",") if g.strip()]


def _resolve_species(species: str, genes: list[str]) -> str:
    """Resolve species string to g:Profiler organism code."""
    if species == "auto":
        return _detect_species_code(genes)
    sp_map = {
        "human": "hsapiens", "mouse": "mmusculus",
        "Hs": "hsapiens", "Mm": "mmusculus",
    }
    return sp_map.get(species, species)


def get_go_enrichment_dataframe(
    genes: list[str] | str,
    *,
    species: str = "auto",
    sources: str = "GO:BP,GO:MF,GO:CC",
) -> pd.DataFrame:
    """Return GO enrichment results as a DataFrame.

    This is the structured counterpart to ``run_go_enrichment`` for internal
    callers that need the full table instead of only formatted text.
    """
    gene_list = _parse_gene_list(genes) if isinstance(genes, str) else [str(g).strip() for g in genes if str(g).strip()]
    if not gene_list:
        return pd.DataFrame()

    sp = _resolve_species(species, gene_list)
    sources_list = [s.strip() for s in sources.split(",") if s.strip()]

    from gprofiler import GProfiler

    gp = GProfiler(return_dataframe=True)
    try:
        df = gp.profile(organism=sp, query=gene_list, sources=sources_list)
    except Exception as exc:
        raise RuntimeError(f"GO enrichment failed: {exc}") from exc

    if isinstance(df, pd.DataFrame):
        return df.copy()
    return pd.DataFrame(df)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def run_go_enrichment(
    gene_list: str,
    species: str = "auto",
    sources: str = "GO:BP,GO:MF,GO:CC",
    name: Optional[str] = None,
    conversation_id: str = "default",
) -> str:
    """Run Gene Ontology enrichment analysis on a gene list.

    Args:
        gene_list: Comma-separated gene IDs or symbols (e.g. "TP53,BRCA1,MDM2").
        species: Species code ('auto', 'human', 'mouse'). Default: auto-detect.
        sources: GO sources, comma-separated. Default: 'GO:BP,GO:MF,GO:CC'.
        name: Label for this enrichment result. Default: auto-generated.
        conversation_id: Conversation/session identifier for state management.

    Returns:
        Top enrichment terms table.
    """
    genes = _parse_gene_list(gene_list)
    if not genes:
        return "No genes provided. Pass a comma-separated gene list."
    try:
        df = get_go_enrichment_dataframe(genes, species=species, sources=sources)
    except RuntimeError as exc:
        return str(exc)

    sources_list = [s.strip() for s in sources.split(",") if s.strip()]

    if df.empty:
        return "No significant GO terms found."

    # Store result
    state = _get_state(conversation_id)
    ename = name or f"go_{'_'.join(sources_list[:2]).replace(':', '').lower()}"
    state["enrichment_results"][ename] = df
    state["last_enrichment"] = ename

    # Format top results
    show = df.head(15)
    lines = [f"GO Enrichment: {ename} ({len(genes)} genes, {len(df)} terms found)", ""]
    lines.append(f"{'Source':<8} {'Term':<50} {'p-value':<12} {'Genes':<6} {'Size':<6}")
    lines.append("-" * 85)
    for _, row in show.iterrows():
        src = str(row.get("source", ""))[:7]
        term = str(row.get("name", ""))[:49]
        pval = f"{row.get('p_value', 1):.2e}"
        inter = str(row.get("intersection_size", ""))
        tsize = str(row.get("term_size", ""))
        lines.append(f"{src:<8} {term:<50} {pval:<12} {inter:<6} {tsize:<6}")

    if len(df) > 15:
        lines.append(f"\n... and {len(df) - 15} more terms. Use get_enrichment_results() to see all.")

    return "\n".join(lines)


def run_pathway_enrichment(
    gene_list: str,
    species: str = "auto",
    database: str = "KEGG",
    name: Optional[str] = None,
    conversation_id: str = "default",
) -> str:
    """Run pathway enrichment analysis (KEGG or Reactome).

    Args:
        gene_list: Comma-separated gene IDs or symbols.
        species: Species ('auto', 'human', 'mouse'). Default: auto-detect.
        database: 'KEGG' or 'REAC' (Reactome). Default: 'KEGG'.
        name: Label for this result. Default: auto-generated.
        conversation_id: Conversation/session identifier for state management.

    Returns:
        Top pathway enrichment results.
    """
    genes = _parse_gene_list(gene_list)
    if not genes:
        return "No genes provided. Pass a comma-separated gene list."

    sp = _resolve_species(species, genes)

    source = database.upper()
    if source not in ("KEGG", "REAC"):
        source = "KEGG"

    from gprofiler import GProfiler
    gp = GProfiler(return_dataframe=True)
    try:
        df = gp.profile(organism=sp, query=genes, sources=[source])
    except Exception as exc:
        return f"Pathway enrichment failed: {exc}"

    if df.empty:
        return f"No significant {source} pathways found."

    # Store result
    state = _get_state(conversation_id)
    ename = name or f"{source.lower()}_enrichment"
    state["enrichment_results"][ename] = df
    state["last_enrichment"] = ename

    # Format top results
    show = df.head(15)
    db_label = "KEGG" if source == "KEGG" else "Reactome"
    lines = [f"{db_label} Pathway Enrichment: {ename} ({len(genes)} genes, {len(df)} pathways)", ""]
    lines.append(f"{'ID':<15} {'Pathway':<50} {'p-value':<12} {'Genes':<6}")
    lines.append("-" * 85)
    for _, row in show.iterrows():
        pid = str(row.get("native", ""))[:14]
        pname = str(row.get("name", ""))[:49]
        pval = f"{row.get('p_value', 1):.2e}"
        inter = str(row.get("intersection_size", ""))
        lines.append(f"{pid:<15} {pname:<50} {pval:<12} {inter:<6}")

    if len(df) > 15:
        lines.append(f"\n... and {len(df) - 15} more pathways.")

    return "\n".join(lines)


def get_enrichment_results(
    name: Optional[str] = None,
    max_terms: int = 30,
    pvalue_threshold: float = 0.05,
    source_filter: Optional[str] = None,
    conversation_id: str = "default",
) -> str:
    """Retrieve stored enrichment results with optional filtering.

    Args:
        name: Enrichment result name. Default: most recent.
        max_terms: Maximum terms to return. Default: 30.
        pvalue_threshold: Filter terms by p-value. Default: 0.05.
        source_filter: Filter by source (e.g. 'GO:BP', 'KEGG'). Default: all.
        conversation_id: Conversation/session identifier for state management.

    Returns:
        Formatted enrichment results table.
    """
    state = _get_state(conversation_id)
    ename = name or state.get("last_enrichment")
    if not ename or ename not in state.get("enrichment_results", {}):
        available = list(state.get("enrichment_results", {}).keys())
        if available:
            return f"No enrichment results named '{ename}'. Available: {', '.join(available)}"
        return "No enrichment results available. Run run_go_enrichment() or run_pathway_enrichment() first."

    df = state["enrichment_results"][ename]
    if df.empty:
        return f"Enrichment result '{ename}' is empty."

    # Filter
    filtered = df[df["p_value"] <= pvalue_threshold] if "p_value" in df.columns else df
    if source_filter and "source" in filtered.columns:
        filtered = filtered[filtered["source"] == source_filter]

    show = filtered.head(max_terms)

    lines = [f"Enrichment results: {ename} ({len(show)} of {len(filtered)} terms, p <= {pvalue_threshold})", ""]
    lines.append(f"{'Source':<8} {'ID':<15} {'Term':<45} {'p-value':<12} {'Genes':<6} {'Size':<6}")
    lines.append("-" * 95)
    for _, row in show.iterrows():
        src = str(row.get("source", ""))[:7]
        rid = str(row.get("native", ""))[:14]
        term = str(row.get("name", ""))[:44]
        pval = f"{row.get('p_value', 1):.2e}"
        inter = str(row.get("intersection_size", ""))
        tsize = str(row.get("term_size", ""))
        lines.append(f"{src:<8} {rid:<15} {term:<45} {pval:<12} {inter:<6} {tsize:<6}")

    return "\n".join(lines)


def get_term_genes(
    term_id: str,
    name: Optional[str] = None,
    conversation_id: str = "default",
) -> str:
    """Show genes contributing to a specific GO term or pathway.

    Args:
        term_id: The term identifier (e.g. 'GO:0006915' or 'KEGG:04110').
        name: Enrichment result name. Default: most recent.
        conversation_id: Conversation/session identifier for state management.

    Returns:
        List of genes in the intersection for that term.
    """
    import pandas as pd

    state = _get_state(conversation_id)
    ename = name or state.get("last_enrichment")
    if not ename or ename not in state.get("enrichment_results", {}):
        return "No enrichment results available."

    df = state["enrichment_results"][ename]
    if df.empty:
        return "Enrichment result is empty."

    # Look up the term
    match = df[df["native"] == term_id] if "native" in df.columns else pd.DataFrame()
    if match.empty and "name" in df.columns:
        match = df[df["name"].str.contains(term_id, case=False, na=False)]
    if match.empty:
        return f"Term '{term_id}' not found in enrichment result '{ename}'."

    row = match.iloc[0]
    term_name = row.get("name", term_id)
    source = row.get("source", "?")
    pval = row.get("p_value", "?")

    # Get intersecting genes
    intersections = row.get("intersections", [])
    if isinstance(intersections, str):
        genes = [g.strip() for g in intersections.split(",")]
    elif isinstance(intersections, (list, np.ndarray)):
        genes = list(intersections)
    else:
        genes = []

    lines = [f"Term: {term_name} ({term_id})",
             f"Source: {source}  |  p-value: {pval}",
             f"Genes ({len(genes)}):"]
    if genes:
        lines.append(", ".join(str(g) for g in genes))
    else:
        lines.append("(gene intersection list not available from g:Profiler result)")

    return "\n".join(lines)
