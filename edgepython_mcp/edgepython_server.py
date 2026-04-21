# This code was written by Claude (Anthropic). The project was directed by Lior Pachter.
"""
edgePython MCP Server — Differential expression analysis via tool calls.

Wraps edgePython's core pipeline so AI agents can run RNA-seq DE analysis
step-by-step: load data → filter → normalize → estimate dispersions →
fit model → test contrasts → get results → generate plots.

Usage:
    python3 edgepython_server.py
"""

import os
import sys
import re
import json
from pathlib import Path
import numpy as np
import pandas as pd
from typing import Optional

from fastmcp import FastMCP

# Ensure edgePython is importable
_edgepython_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _edgepython_root not in sys.path:
    sys.path.insert(0, _edgepython_root)

import edgepython as ep

from common.gene_annotation import GeneAnnotator

_annotator = GeneAnnotator()

# ---------------------------------------------------------------------------
# Server + state
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "edgepython",
    instructions=(
        "RNA-seq differential expression analysis server powered by edgePython. "
        "Bulk workflow: load_data → filter_genes → normalize → set_design → "
        "estimate_dispersion (or estimate_glm_robust_dispersion) → "
        "voom_transform (optional) → fit_model → test_contrast → "
        "get_top_genes / generate_plot. DTU: dtu_diff_splice_dge or "
        "dtu_splice_variants. "
        "Single-cell workflow: load_sc_data → fit_sc_model → test_sc_coef → get_sc_top_genes. "
        "Use describe() or describe_sc() to see current pipeline state."
    ),
)

# Module-level state — mutated by tools across a session.
_state: dict = {
    "dgelist": None,       # Current DGEList
    "design": None,        # Design matrix (ndarray)
    "design_info": None,   # Column names for the design matrix
    "fit": None,           # Fitted QL-GLM (DGEGLM-like dict)
    "glm_fit": None,       # Fitted GLM (non-QL)
    "results": {},         # name → DGELRT-like dict
    "last_result": None,   # Name of most recent test result
    "enrichment_results": {},  # name → DataFrame of enrichment results
    "last_enrichment": None,   # name of most recent enrichment result
    "_filtered_genes": None,   # last filtered gene set (dict with up/down/all lists)
    "voom": None,          # Most recent voom/voomLmFit output dict
    "filtered": False,
    "normalized": False,
    "dispersions_estimated": False,
    "feature_level": "unknown",  # inferred: gene | transcript | unknown
    "feature_level_note": None,
    "analysis_context": "unknown",  # inferred: bulk | single_cell_10x | single_cell | unknown
    "analysis_context_note": None,
    "counts_path": None,
    "work_dir": None,
    "annotation_gtf": None,
}


def _resolve_existing_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return resolved if resolved.exists() else None


def _infer_annotation_dir(data_input: object = None, path: Optional[str] = None) -> Path | None:
    candidates: list[object] = []
    if path:
        candidates.append(path)
    if isinstance(data_input, (list, tuple)):
        candidates.extend(data_input)
    elif data_input is not None:
        candidates.append(data_input)

    for candidate in candidates:
        resolved = _resolve_existing_path(candidate)
        if resolved is None:
            continue
        return resolved if resolved.is_dir() else resolved.parent
    return None


def _configure_annotation_sources(
    *,
    work_dir: str | Path | None = None,
    annotation_gtf: str | Path | None = None,
) -> dict[str, str | int | None] | None:
    global _annotator

    _annotator = GeneAnnotator()
    if annotation_gtf:
        candidate = _resolve_existing_path(annotation_gtf)
        if candidate is not None and candidate.is_file():
            return _annotator.load_gtf(candidate, cache=True)
    if work_dir:
        candidate_dir = _resolve_existing_path(work_dir)
        if candidate_dir is not None:
            search_dir = candidate_dir if candidate_dir.is_dir() else candidate_dir.parent
            return _annotator.load_reconciled_gtf(search_dir)
    return None


def _require(key: str, label: str):
    """Raise if a pipeline stage hasn't been completed yet."""
    if _state[key] is None:
        raise ValueError(
            f"No {label} available. Run the appropriate step first."
        )


_TRANSCRIPT_ID_PATTERNS = (
    r"(^|[|_.-])ENST\d+",
    r"(^|[|_.-])NM_\d+",
    r"(^|[|_.-])NR_\d+",
    r"(^|[|_.-])XM_\d+",
    r"(^|[|_.-])XR_\d+",
    r"\btx\d+\b",
    r"\btranscript\b",
    r"\bisoform\b",
)


def _first_present_string(row, columns: tuple[str, ...]) -> Optional[str]:
    for col in columns:
        if col not in row.index:
            continue
        val = row[col]
        if val is None:
            continue
        text = str(val).strip()
        if text and text != "nan":
            return text
    return None


def _gene_name(row):
    """Extract gene name from a top_tags result row."""
    # Prefer symbol (from annotation) over raw gene ID
    gene_name = _first_present_string(row, ("Symbol", "gene_name", "gene_id", "GeneID"))
    if gene_name:
        return gene_name
    # Fall back to row index
    return str(row.name)


def _transcript_name(row) -> Optional[str]:
    transcript_name = _first_present_string(
        row,
        ("TranscriptID", "transcript_id", "transcript", "ExonID", "exon_id", "exonid"),
    )
    if transcript_name:
        return transcript_name

    row_name = str(row.name).strip()
    if not row_name or row_name == "nan":
        return None
    if row_name == _gene_name(row):
        return None
    if _state.get("feature_level") == "transcript":
        return row_name
    if any(re.search(pattern, row_name, flags=re.IGNORECASE) for pattern in _TRANSCRIPT_ID_PATTERNS):
        return row_name
    return None


def _format_feature_label(
    row,
    *,
    include_transcript: bool = False,
    separator: str = "\n",
) -> str:
    gene_label = _gene_name(row)
    if not include_transcript:
        return gene_label

    transcript_label = _transcript_name(row)
    if not transcript_label or transcript_label == gene_label:
        return gene_label
    return f"{gene_label}{separator}{transcript_label}"


def _feature_label_candidates(row) -> set[str]:
    candidates = {str(_gene_name(row)).strip().lower()}
    transcript_label = _transcript_name(row)
    if transcript_label:
        candidates.add(transcript_label.strip().lower())
    return {candidate for candidate in candidates if candidate}


def _feature_lookup_key(row) -> str:
    row_name = str(row.name).strip()
    if row_name and row_name != "nan":
        return row_name
    transcript_label = _transcript_name(row)
    if transcript_label:
        return transcript_label
    return _gene_name(row)


def _infer_feature_level(feature_ids: Optional[list]) -> tuple[str, str]:
    """Heuristically infer feature level from row IDs."""
    if not feature_ids:
        return "unknown", "unknown (no feature IDs found)"

    ids = [str(x).strip() for x in feature_ids if str(x).strip()]
    if not ids:
        return "unknown", "unknown (no non-empty feature IDs found)"

    # Sample up to 500 IDs for a lightweight heuristic.
    ids = ids[:500]
    n = len(ids)

    gene_patterns = (
        r"(^|[|_.-])ENSG\d+",
        r"(^|[|_.-])FBgn\d+",
        r"(^|[|_.-])WBGene\d+",
    )

    tx_hits = 0
    gene_hits = 0
    for s in ids:
        if any(re.search(p, s, flags=re.IGNORECASE) for p in _TRANSCRIPT_ID_PATTERNS):
            tx_hits += 1
        if any(re.search(p, s, flags=re.IGNORECASE) for p in gene_patterns):
            gene_hits += 1

    tx_frac = tx_hits / n
    gene_frac = gene_hits / n

    # Bias to gene-level when uncertain (edgePython default use case).
    if tx_frac >= 0.10 and tx_hits >= 5:
        return "transcript", f"transcript/isoform-like IDs ({tx_hits}/{n})"
    if gene_frac >= 0.10 and gene_hits >= 5:
        return "gene", f"gene-like IDs ({gene_hits}/{n})"
    return "gene", "gene-level assumed (IDs do not strongly indicate transcript-level)"


def _infer_analysis_context(
    source: Optional[str] = None,
    data_input: Optional[object] = None,
    sample_names: Optional[list] = None,
    default_bulk: bool = False,
) -> tuple[str, str]:
    """Heuristically infer sequencing/analysis context."""
    src = (source or "").strip().lower()

    # Flatten potential path-like clues.
    clues = []
    if isinstance(data_input, (list, tuple)):
        clues.extend([str(x).lower() for x in data_input])
    elif data_input is not None:
        clues.append(str(data_input).lower())

    clue_text = " ".join(clues)

    # Strong single-cell/10x signals.
    if src == "10x" or "10x" in clue_text or "cellranger" in clue_text:
        return "single_cell_10x", "10x/cellranger-like source/path"

    if src == "anndata" or any(s.endswith('.h5ad') for s in clues):
        # Could be 10x-derived or non-10x single-cell.
        if "10x" in clue_text or "cellranger" in clue_text:
            return "single_cell_10x", "AnnData with 10x/cellranger-like clues"
        return "single_cell", "AnnData source/path"

    # SMART-seq / random-primed style clues -> bulk-like transcriptome input.
    if any(k in clue_text for k in ("smartseq", "smart-seq", "smart_seq", "random primed", "random_primed", "random-primed")):
        return "bulk", "SMART-seq/random-primed-like clues"

    # Source families typically used for bulk transcript quantification.
    if src in {"kallisto", "salmon", "rsem", "oarfish", "table", "matrix", "dataframe", "sparse", "rds"}:
        return "bulk", f"source='{src}' typically bulk/pseudobulk-like"

    # Sample-name heuristic for 10x-like barcodes.
    if sample_names:
        names = [str(x) for x in sample_names]
        if names:
            barcode_hits = 0
            for n in names:
                s = n.strip()
                if re.fullmatch(r"[ACGT]{8,}(?:-\d+)?", s):
                    barcode_hits += 1
            frac = barcode_hits / len(names)
            if len(names) >= 100 and frac >= 0.5:
                return "single_cell_10x", f"sample names look like 10x barcodes ({barcode_hits}/{len(names)})"

    if default_bulk:
        return "bulk", "bulk assumed from count-matrix workflow"

    return "unknown", "insufficient clues"


def _is_bulk_like_context() -> bool:
    return _state.get("analysis_context") == "bulk"


def _gene_level_warning() -> Optional[str]:
    """Soft warning shown only for likely bulk/random-primed contexts."""
    if _state.get("feature_level") == "gene" and _is_bulk_like_context():
        return (
            "Note: This appears to be gene-level DE in a bulk/random-primed-like context. "
            "If transcript/isoform quantifications are available, consider isoform-level "
            "analysis to avoid masking isoform-specific changes."
        )
    return None


def _append_gene_level_warning(lines: list) -> None:
    warn = _gene_level_warning()
    if warn:
        lines.extend(["", warn])


def _resolve_gene_exon_ids(
    d: dict,
    gene_column: Optional[str] = None,
    exon_column: Optional[str] = None,
):
    """Resolve gene/exon IDs from DGEList gene annotation or fallback IDs."""
    genes = d.get("genes")
    if genes is None:
        gene_ids = d.get("_gene_ids")
        if gene_ids is None:
            raise ValueError("No gene annotation available; provide DGEList genes with GeneID.")
        gene_ids = np.asarray(gene_ids)
        exon_ids = np.arange(len(gene_ids))
        return gene_ids, exon_ids

    gdf = genes if isinstance(genes, pd.DataFrame) else pd.DataFrame(genes)

    if gene_column is not None:
        if gene_column not in gdf.columns:
            raise ValueError(f"gene_column '{gene_column}' not found in gene annotation.")
        gene_ids = gdf[gene_column].astype(str).values
    else:
        found = None
        for col in ("GeneID", "geneid", "gene_id", "Gene"):
            if col in gdf.columns:
                found = col
                break
        if found is None:
            raise ValueError("Could not infer gene IDs. Set gene_column explicitly.")
        gene_ids = gdf[found].astype(str).values

    if exon_column is not None:
        if exon_column not in gdf.columns:
            raise ValueError(f"exon_column '{exon_column}' not found in gene annotation.")
        exon_ids = gdf[exon_column].astype(str).values
    else:
        found = None
        for col in ("ExonID", "exonid", "exon_id", "TranscriptID", "transcript_id"):
            if col in gdf.columns:
                found = col
                break
        exon_ids = gdf[found].astype(str).values if found is not None else np.arange(len(gene_ids))

    return np.asarray(gene_ids), np.asarray(exon_ids)


# ---------------------------------------------------------------------------
# Tool 1: load_data
# ---------------------------------------------------------------------------

@mcp.tool()
def load_data(
    counts_path: str,
    sample_info_path: Optional[str] = None,
    group_column: Optional[str] = None,
    separator: Optional[str] = None,
    annotation_gtf: Optional[str] = None,
) -> str:
    """Load a count matrix (and optional sample metadata) to create a DGEList.

    Args:
        counts_path: Path to count matrix file (TSV/CSV). Genes as rows,
            samples as columns. First column is gene IDs.
        sample_info_path: Optional path to sample metadata file (TSV/CSV).
            Must have a column matching sample names in the count matrix.
        group_column: Column in sample info to use as group factor.
            Auto-detected if not provided.
        separator: Column separator ('\\t' or ','). Auto-detected from
            file extension if not provided.
    """
    # Auto-detect separator
    if separator is None:
        separator = "," if counts_path.endswith(".csv") else "\t"

    # Read counts
    counts_df = pd.read_csv(counts_path, sep=separator, index_col=0)
    gene_ids = list(counts_df.index)
    sample_names = list(counts_df.columns)
    counts = counts_df.values.astype(np.float64)

    genes_df = pd.DataFrame({"GeneID": gene_ids})
    work_dir = str(Path(counts_path).expanduser().resolve().parent)
    annotation_info = _configure_annotation_sources(work_dir=work_dir, annotation_gtf=annotation_gtf)

    # Auto-annotate gene symbols if reference data is available
    _n_annotated = 0
    if _annotator.is_available:
        _annotator.annotate_dataframe(genes_df, id_column="GeneID", symbol_column="Symbol")
        _n_annotated = int(genes_df["Symbol"].notna().sum())

    # Read sample info if provided
    group = None
    samples_df = None
    if sample_info_path is not None:
        sep2 = "," if sample_info_path.endswith(".csv") else "\t"
        samples_df = pd.read_csv(sample_info_path, sep=sep2)

        # Try to align rows to sample order
        for col in samples_df.columns:
            vals = [str(v) for v in samples_df[col]]
            if set(sample_names).issubset(set(vals)):
                samples_df = samples_df.set_index(col).loc[sample_names].reset_index()
                break

        # Extract group
        if group_column is not None:
            group = list(samples_df[group_column].astype(str))
        else:
            # Auto-detect: first column with few unique values
            for col in samples_df.columns:
                n_unique = samples_df[col].nunique()
                if 2 <= n_unique <= len(sample_names) // 2:
                    group = list(samples_df[col].astype(str))
                    group_column = col
                    break

    dgelist = ep.make_dgelist(
        counts=counts,
        group=group,
        genes=genes_df,
        samples=samples_df,
    )
    # Attach sample/gene names for display
    dgelist["_sample_names"] = sample_names
    dgelist["_gene_ids"] = gene_ids

    level, level_note = _infer_feature_level(gene_ids)
    context, context_note = _infer_analysis_context(
        source=None,
        data_input=counts_path,
        sample_names=sample_names,
        default_bulk=True,
    )

    _state["dgelist"] = dgelist
    _state["feature_level"] = level
    _state["feature_level_note"] = level_note
    _state["analysis_context"] = context
    _state["analysis_context_note"] = context_note
    _state["counts_path"] = str(Path(counts_path).expanduser().resolve())
    _state["work_dir"] = work_dir
    _state["annotation_gtf"] = annotation_info.get("gtf_path") if annotation_info else None
    _state["filtered"] = False
    _state["normalized"] = False
    _state["dispersions_estimated"] = False
    _state["design"] = None
    _state["design_info"] = None
    _state["fit"] = None
    _state["glm_fit"] = None
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None

    # Build summary
    lib_sizes = dgelist["samples"]["lib.size"].values
    lines = [
        f"Loaded {counts.shape[0]:,} genes × {counts.shape[1]} samples",
        f"Samples: {', '.join(sample_names)}",
    ]
    if group is not None:
        from collections import Counter
        gc = Counter(group)
        lines.append(f"Groups ({group_column}): " + ", ".join(
            f"{k} (n={v})" for k, v in gc.items()
        ))
    lines.append(
        f"Library sizes: {int(lib_sizes.min()):,} – {int(lib_sizes.max()):,} "
        f"(median {int(np.median(lib_sizes)):,})"
    )
    if _state.get("feature_level_note"):
        lines.append(f"Feature level: {_state['feature_level']} ({_state['feature_level_note']})")
    if _state.get("analysis_context_note"):
        lines.append(f"Context: {_state['analysis_context']} ({_state['analysis_context_note']})")
    if _n_annotated:
        lines.append(f"Gene symbols: {_n_annotated:,}/{len(gene_ids):,} annotated")
    if annotation_info and annotation_info.get("gtf_path"):
        lines.append(f"Annotation GTF: {annotation_info['gtf_path']}")
    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 1b: load_data_auto
# ---------------------------------------------------------------------------

@mcp.tool()
def load_data_auto(
    data: Optional[str] = None,
    data_list: Optional[list] = None,
    source: Optional[str] = None,
    format: Optional[str] = None,
    path: Optional[str] = None,
    group: Optional[list] = None,
    labels: Optional[list] = None,
    columns: Optional[list] = None,
    sep: str = "\t",
    obs_col: Optional[str] = None,
    layer: Optional[str] = None,
    ngibbs: int = 100,
    verbose: bool = True,
    annotation_gtf: Optional[str] = None,
) -> str:
    """Load data using edgePython's flexible read_data() loader.

    Args:
        data: Path to file/dir or other supported value. If data_list is provided,
            this is ignored.
        data_list: Optional list of paths (e.g. quantification dirs).
        source: Data source (kallisto, salmon, oarfish, rsem, 10x, table,
            anndata, rds, sparse, matrix, dataframe). Auto-detected if None.
        format: Source-specific format (e.g. 'h5' or 'tsv' for kallisto).
        path: Base path prefix for relative paths.
        group: Optional group assignments.
        labels: Optional sample labels.
        columns: For table format: (gene_id_col, count_col) 0-based indices.
        sep: Field separator for table/CSV files.
        obs_col: For AnnData: column in .obs to use as group factor.
        layer: For AnnData: layer name to use instead of .X.
        ngibbs: For RSEM: number of Gibbs samples per sample.
        verbose: Print progress messages.
    """
    if data_list is not None:
        data_input = data_list
    elif data is not None:
        data_input = data
    else:
        raise ValueError("Provide data or data_list.")

    inferred_work_dir = _infer_annotation_dir(data_input=data_input, path=path)
    annotation_info = _configure_annotation_sources(work_dir=inferred_work_dir, annotation_gtf=annotation_gtf)

    dgelist = ep.read_data(
        data_input,
        source=source,
        format=format,
        path=path,
        group=group,
        labels=labels,
        columns=columns,
        sep=sep,
        obs_col=obs_col,
        layer=layer,
        ngibbs=ngibbs,
        verbose=verbose,
    )

    _state["dgelist"] = dgelist
    _state["filtered"] = False
    _state["normalized"] = False
    _state["dispersions_estimated"] = False
    _state["design"] = None
    _state["design_info"] = None
    _state["fit"] = None
    _state["glm_fit"] = None
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None
    _state["counts_path"] = str(_resolve_existing_path(data_input)) if isinstance(data_input, str) and _resolve_existing_path(data_input) else None
    _state["work_dir"] = str(inferred_work_dir) if inferred_work_dir is not None else None
    _state["annotation_gtf"] = annotation_info.get("gtf_path") if annotation_info else None

    counts = dgelist["counts"]
    sample_names = None
    if dgelist.get("samples") is not None:
        sdf = dgelist["samples"]
        if sdf.index is not None and len(sdf.index) == counts.shape[1]:
            sample_names = [str(x) for x in sdf.index]
            dgelist["_sample_names"] = sample_names

    gene_ids = None
    if dgelist.get("genes") is not None:
        gdf = dgelist["genes"]
        for col in ("GeneID", "gene_id"):
            if col in gdf.columns:
                gene_ids = list(gdf[col].astype(str))
                break
    if gene_ids is not None:
        dgelist["_gene_ids"] = gene_ids

    # Auto-annotate gene symbols if reference data is available
    _n_auto_annotated = 0
    if _annotator.is_available and dgelist.get("genes") is not None:
        gdf = dgelist["genes"]
        if isinstance(gdf, pd.DataFrame):
            for id_col in ("GeneID", "gene_id"):
                if id_col in gdf.columns:
                    _annotator.annotate_dataframe(gdf, id_column=id_col, symbol_column="Symbol")
                    _n_auto_annotated = int(gdf["Symbol"].notna().sum())
                    break

    level, level_note = _infer_feature_level(gene_ids)
    context, context_note = _infer_analysis_context(
        source=source,
        data_input=data_input,
        sample_names=sample_names,
        default_bulk=False,
    )
    _state["feature_level"] = level
    _state["feature_level_note"] = level_note
    _state["analysis_context"] = context
    _state["analysis_context_note"] = context_note

    lines = [f"Loaded {counts.shape[0]:,} genes × {counts.shape[1]} samples"]
    if sample_names is not None:
        lines.append(f"Samples: {', '.join(sample_names)}")
    if _state.get("feature_level_note"):
        lines.append(f"Feature level: {_state['feature_level']} ({_state['feature_level_note']})")
    if _state.get("analysis_context_note"):
        lines.append(f"Context: {_state['analysis_context']} ({_state['analysis_context_note']})")
    if _n_auto_annotated:
        lines.append(f"Gene symbols: {_n_auto_annotated:,}/{counts.shape[0]:,} annotated")
    if annotation_info and annotation_info.get("gtf_path"):
        lines.append(f"Annotation GTF: {annotation_info['gtf_path']}")
    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: describe
# ---------------------------------------------------------------------------

@mcp.tool()
def describe() -> str:
    """Report the current state of the analysis pipeline."""
    lines = []

    d = _state["dgelist"]
    if d is None:
        return "No data loaded. Use load_data() first."

    counts = d["counts"]
    lines.append(f"Data: {counts.shape[0]:,} genes × {counts.shape[1]} samples")
    lines.append(f"Samples: {', '.join(d.get('_sample_names', [str(i) for i in range(counts.shape[1])]))}")
    lines.append(f"Filtered: {'yes' if _state['filtered'] else 'no'}")
    lines.append(f"Normalized: {'yes' if _state['normalized'] else 'no'}")

    if _state["normalized"]:
        nf = d["samples"]["norm.factors"].values
        lines.append(f"  Norm factors: {', '.join(f'{v:.4f}' for v in nf)}")

    lines.append(f"Dispersions estimated: {'yes' if _state['dispersions_estimated'] else 'no'}")
    fl = _state.get("feature_level", "unknown")
    note = _state.get("feature_level_note")
    if note:
        lines.append(f"Feature level: {fl} ({note})")
    else:
        lines.append(f"Feature level: {fl}")

    cx = _state.get("analysis_context", "unknown")
    cx_note = _state.get("analysis_context_note")
    if cx_note:
        lines.append(f"Context: {cx} ({cx_note})")
    else:
        lines.append(f"Context: {cx}")
    if _state["dispersions_estimated"]:
        cd = d.get("common.dispersion")
        if cd is not None:
            lines.append(f"  Common dispersion: {cd:.4f} (BCV: {np.sqrt(cd):.4f})")

    if _state["design"] is not None:
        dm = _state["design"]
        lines.append(f"Design: {dm.shape[1]} coefficients — {', '.join(_state['design_info'])}")

    if _state["fit"] is not None:
        fit = _state["fit"]
        pdf = fit.get("df.prior")
        if pdf is not None:
            if np.isscalar(pdf):
                lines.append(f"Model fitted (QL): prior df = {pdf:.2f}")
            else:
                lines.append(f"Model fitted (QL): median prior df = {np.median(pdf):.2f}")
    if _state["voom"] is not None:
        vw = _state["voom"].get("weights")
        if vw is not None:
            lines.append(
                f"Voom weights: {vw.shape[0]:,}×{vw.shape[1]}, "
                f"range {np.nanmin(vw):.3g}–{np.nanmax(vw):.3g}"
            )

    if _state["results"]:
        lines.append(f"Test results: {', '.join(_state['results'].keys())}")
    else:
        lines.append("No test results yet.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2b: reset_state
# ---------------------------------------------------------------------------

@mcp.tool()
def reset_state() -> str:
    """Reset all pipeline state."""
    _state["dgelist"] = None
    _state["design"] = None
    _state["design_info"] = None
    _state["fit"] = None
    _state["glm_fit"] = None
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None
    _state["filtered"] = False
    _state["normalized"] = False
    _state["dispersions_estimated"] = False
    _state["feature_level"] = "unknown"
    _state["feature_level_note"] = None
    _state["analysis_context"] = "unknown"
    _state["analysis_context_note"] = None
    _state["enrichment_results"] = {}
    _state["last_enrichment"] = None
    _state["_filtered_genes"] = None
    return "State cleared."


# ---------------------------------------------------------------------------
# Tool 3: filter_genes
# ---------------------------------------------------------------------------

@mcp.tool()
def filter_genes(
    min_count: int = 10,
    min_total_count: int = 15,
) -> str:
    """Filter out lowly-expressed genes using edgePython filterByExpr-compatible logic.

    Uses adaptive CPM thresholds based on library sizes and group structure.

    Args:
        min_count: Minimum count threshold (used to derive CPM cutoff).
            Default: 10.
        min_total_count: Minimum total count across all samples. Default: 15.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    group = d["samples"].get("group")
    keep = ep.filter_by_expr(
        d, group=group, min_count=min_count, min_total_count=min_total_count
    )

    n_before = d["counts"].shape[0]
    n_after = int(keep.sum())

    # Apply filter
    d["counts"] = d["counts"][keep]
    if "genes" in d and d["genes"] is not None:
        d["genes"] = d["genes"].iloc[keep.nonzero()[0]].reset_index(drop=True)
    gene_ids = d.get("_gene_ids")
    if gene_ids is not None:
        d["_gene_ids"] = [gene_ids[i] for i in range(n_before) if keep[i]]

    # Clear downstream state
    _state["dispersions_estimated"] = False
    _state["fit"] = None
    _state["glm_fit"] = None
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None
    _state["filtered"] = True

    # Recompute lib sizes after filtering
    d["samples"]["lib.size"] = d["counts"].sum(axis=0).astype(np.float64)

    return (
        f"Filtered: {n_before:,} → {n_after:,} genes "
        f"({n_before - n_after:,} removed)"
    )


# ---------------------------------------------------------------------------
# Tool 4: normalize
# ---------------------------------------------------------------------------

@mcp.tool()
def normalize(method: str = "TMM") -> str:
    """Apply library-size normalization.

    Args:
        method: Normalization method. One of 'TMM' (default), 'TMMwsp',
            'RLE', 'upperquartile', or 'none'.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    d = ep.calc_norm_factors(d, method=method)
    _state["dgelist"] = d
    _state["normalized"] = True
    _state["voom"] = None

    nf = d["samples"]["norm.factors"].values
    eff = d["samples"]["lib.size"].values * nf

    lines = [
        f"Normalization: {method}",
        "Sample | Norm Factor | Effective Lib Size",
        "-------|------------|-------------------",
    ]
    names = d.get("_sample_names", [f"S{i+1}" for i in range(len(nf))])
    for i, name in enumerate(names):
        lines.append(f"{name} | {nf[i]:.4f} | {int(eff[i]):,}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4b: normalize_chip — ChIP-Seq normalization to input
# ---------------------------------------------------------------------------

@mcp.tool()
def normalize_chip(
    input_csv: str,
    dispersion: float = 0.01,
    niter: int = 6,
    loss: str = "p",
) -> str:
    """Normalize ChIP-Seq counts to input control and test for enrichment.

    Reads input control counts from a CSV file and normalizes the currently
    loaded ChIP response counts against them, setting offsets for downstream
    GLM fitting.

    Args:
        input_csv: Path to a CSV/TSV file of input control counts with the
            same genes (rows) and samples (columns) as the loaded data.
        dispersion: Negative binomial dispersion (default 0.01).
        niter: Number of iterations for scaling-factor estimation.
        loss: Loss function — 'p' (cumulative probabilities) or 'z' (z-value).
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    sep = "\t" if input_csv.endswith(".tsv") else ","
    inp_df = pd.read_csv(input_csv, sep=sep, index_col=0)
    inp_mat = inp_df.values.astype(np.float64)

    if inp_mat.shape[0] != d["counts"].shape[0]:
        raise ValueError(
            f"Row count mismatch: input has {inp_mat.shape[0]} rows but "
            f"loaded data has {d['counts'].shape[0]} genes."
        )

    d = ep.calc_norm_offsets_for_chip(
        inp_mat, d, dispersion=dispersion, niter=niter, loss=loss,
    )
    _state["dgelist"] = d
    _state["voom"] = None

    lines = [
        f"ChIP normalization applied (dispersion={dispersion}, loss='{loss}').",
        f"Offset matrix set ({d['offset'].shape[0]:,} genes × "
        f"{d['offset'].shape[1]} samples).",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4c: chip_enrichment_test — per-sample ChIP enrichment p-values
# ---------------------------------------------------------------------------

@mcp.tool()
def chip_enrichment_test(
    input_csv: str,
    sample: int = 0,
    dispersion: float = 0.01,
    niter: int = 6,
    n_top: int = 20,
) -> str:
    """Test for ChIP-Seq enrichment relative to input for one sample.

    Returns the top enriched features ranked by p-value.

    Args:
        input_csv: Path to input control counts (CSV/TSV).
        sample: Sample index (0-based) to test.
        dispersion: Negative binomial dispersion.
        niter: Number of iterations.
        n_top: Number of top enriched features to report.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    sep = "\t" if input_csv.endswith(".tsv") else ","
    inp_df = pd.read_csv(input_csv, sep=sep, index_col=0)
    inp_mat = inp_df.values.astype(np.float64)

    if sample < 0 or sample >= d["counts"].shape[1]:
        raise ValueError(f"sample must be 0..{d['counts'].shape[1]-1}")

    out = ep.normalize_chip_to_input(
        inp_mat[:, sample], d["counts"][:, sample],
        dispersion=dispersion, niter=niter,
    )

    gene_ids = d.get("_gene_ids")
    if gene_ids is None:
        gene_ids = [str(i) for i in range(d["counts"].shape[0])]

    order = np.argsort(out['p_value'])
    n_show = min(n_top, len(order))

    names = d.get("_sample_names", [f"S{i}" for i in range(d["counts"].shape[1])])
    lines = [
        f"ChIP enrichment test — sample '{names[sample]}'",
        f"Scaling factor: {out['scaling_factor']:.4f}",
        f"Proportion enriched: {out['prop_enriched']:.4f}",
        "",
        f"Top {n_show} enriched features:",
        "Gene | p-value | mid-p",
        "-----|---------|------",
    ]
    for i in range(n_show):
        idx = order[i]
        lines.append(
            f"{gene_ids[idx]} | {out['p_value'][idx]:.4e} | "
            f"{out['pmid_value'][idx]:.4e}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5: set_design
# ---------------------------------------------------------------------------

@mcp.tool()
def set_design(formula: str) -> str:
    """Set the experimental design using an R-style formula.

    Args:
        formula: R-style formula, e.g. '~ 0 + group', '~ group + batch'.
            Variables must match column names in the sample metadata.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    # Build data for patsy
    samples = d.get("samples")
    if samples is None:
        raise ValueError("No sample metadata — load sample info with load_data().")

    design = ep.model_matrix(formula, data=samples)

    # Extract column names from patsy
    import patsy
    dinfo = patsy.dmatrix(formula, data=samples, return_type="dataframe")
    col_names = list(dinfo.columns)

    _state["design"] = design
    _state["design_info"] = col_names
    # Clear downstream
    _state["fit"] = None
    _state["glm_fit"] = None
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None

    lines = [
        f"Design matrix: {design.shape[0]} samples × {design.shape[1]} coefficients",
        f"Columns: {', '.join(col_names)}",
        f"Rank: {np.linalg.matrix_rank(design)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5b: set_design_matrix
# ---------------------------------------------------------------------------

@mcp.tool()
def set_design_matrix(matrix: list, columns: list) -> str:
    """Set the design matrix directly.

    Args:
        matrix: 2D list of floats with shape (samples, coefficients).
        columns: Column names for the design matrix.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]
    counts = d["counts"]

    design = np.asarray(matrix, dtype=np.float64)
    if design.ndim != 2:
        raise ValueError("Design matrix must be 2D.")
    if design.shape[0] != counts.shape[1]:
        raise ValueError(
            f"Design has {design.shape[0]} rows but data has {counts.shape[1]} samples."
        )
    if len(columns) != design.shape[1]:
        raise ValueError(
            f"Design has {design.shape[1]} columns but {len(columns)} column names provided."
        )

    _state["design"] = design
    _state["design_info"] = list(columns)
    _state["fit"] = None
    _state["glm_fit"] = None
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None

    lines = [
        f"Design matrix set: {design.shape[0]} samples × {design.shape[1]} coefficients",
        f"Columns: {', '.join(columns)}",
        f"Rank: {np.linalg.matrix_rank(design)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6: estimate_dispersion
# ---------------------------------------------------------------------------

@mcp.tool()
def estimate_dispersion(robust: bool = True) -> str:
    """Estimate NB dispersions (common, trended, tagwise).

    Args:
        robust: Use robust empirical Bayes. Default True.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    kwargs = {"robust": robust}
    if _state["design"] is not None:
        kwargs["design"] = _state["design"]

    d = ep.estimate_disp(d, **kwargs)
    _state["dgelist"] = d
    _state["dispersions_estimated"] = True
    _state["voom"] = None

    cd = d.get("common.dispersion", None)
    prior_df = d.get("prior.df", None)
    lines = []
    if cd is not None:
        lines.append(f"Common dispersion: {cd:.4f}")
        lines.append(f"BCV: {np.sqrt(cd):.4f}")
    if prior_df is not None:
        if np.isscalar(prior_df):
            lines.append(f"Prior df: {prior_df:.2f}")
        else:
            lines.append(f"Prior df (median): {np.median(prior_df):.2f}")
    td = d.get("trended.dispersion")
    if td is not None:
        lines.append(f"Trended dispersion range: {td.min():.4f} – {td.max():.4f}")

    return "\n".join(lines) if lines else "Dispersions estimated."


# ---------------------------------------------------------------------------
# Tool 6b: estimate_glm_robust_dispersion
# ---------------------------------------------------------------------------

@mcp.tool()
def estimate_glm_robust_dispersion(
    prior_df: float = 10.0,
    update_trend: bool = True,
    maxit: int = 6,
    k: float = 1.345,
    residual_type: str = "pearson",
    verbose: bool = False,
) -> str:
    """Estimate robust GLM dispersions with iterative Huber reweighting.

    Requires a design matrix; mirrors the robust dispersion workflow implemented in edgePython.
    """
    _require("dgelist", "DGEList")
    _require("design", "design matrix")
    d = _state["dgelist"]

    d = ep.estimate_glm_robust_disp(
        d,
        design=_state["design"],
        prior_df=float(prior_df),
        update_trend=bool(update_trend),
        maxit=int(maxit),
        k=float(k),
        residual_type=str(residual_type),
        verbose=bool(verbose),
        record=False,
    )
    _state["dgelist"] = d
    _state["dispersions_estimated"] = True
    _state["voom"] = None

    lines = ["Robust GLM dispersions estimated (Huber reweighting)."]
    cd = d.get("common.dispersion")
    if cd is not None:
        lines.append(f"Common dispersion: {float(cd):.4f} (BCV {np.sqrt(float(cd)):.4f})")
    td = d.get("trended.dispersion")
    if td is not None:
        lines.append(f"Trended dispersion range: {np.min(td):.4f} – {np.max(td):.4f}")
    tw = d.get("tagwise.dispersion")
    if tw is not None:
        lines.append(f"Tagwise dispersion range: {np.min(tw):.4f} – {np.max(tw):.4f}")
    w = d.get("weights")
    if w is not None:
        lines.append(f"Robust weights range: {np.min(w):.3g} – {np.max(w):.3g}")
    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6c: voom_transform
# ---------------------------------------------------------------------------

@mcp.tool()
def voom_transform(
    span: float = 0.5,
    adaptive_span: bool = False,
    sample_weights: bool = False,
    block_column: Optional[str] = None,
) -> str:
    """Run voom/voomLmFit-style mean-variance weighting.

    Uses current counts + design and stores the voom result in MCP state.
    """
    _require("dgelist", "DGEList")
    _require("design", "design matrix")
    d = _state["dgelist"]

    samples = d.get("samples")
    lib_size = None
    if samples is not None and "lib.size" in samples:
        lib_size = np.asarray(samples["lib.size"].values, dtype=np.float64)
        if "norm.factors" in samples:
            lib_size = lib_size * np.asarray(samples["norm.factors"].values, dtype=np.float64)

    block = None
    if block_column is not None:
        if samples is None or block_column not in samples.columns:
            raise ValueError(f"block_column '{block_column}' not found in sample metadata.")
        block = samples[block_column].astype(str).values

    v = ep.voom_lmfit(
        counts=np.asarray(d["counts"], dtype=np.float64),
        design=np.asarray(_state["design"], dtype=np.float64),
        block=block,
        sample_weights=bool(sample_weights),
        lib_size=lib_size,
        span=float(span),
        adaptive_span=bool(adaptive_span),
        normalize_method="none",
        save_plot=False,
        keep_elist=True,
    )
    _state["voom"] = v

    w = v.get("weights")
    lines = [
        "voom transform completed.",
        f"Input: {d['counts'].shape[0]:,} features × {d['counts'].shape[1]} samples",
        f"Span used: {float(v.get('span', span)):.3f}",
    ]
    if w is not None:
        lines.append(
            f"Weights: shape {w.shape[0]:,}×{w.shape[1]}, "
            f"range {np.nanmin(w):.3g}–{np.nanmax(w):.3g}"
        )
    corr = v.get("correlation")
    if corr is not None and np.isfinite(corr):
        lines.append(f"Consensus block correlation: {float(corr):.4f}")
    sw = v.get("sample_weights")
    if sw is not None:
        lines.append(f"Sample-weight range: {np.nanmin(sw):.3g}–{np.nanmax(sw):.3g}")
    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 7: fit_model
# ---------------------------------------------------------------------------

@mcp.tool()
def fit_model(robust: bool = True) -> str:
    """Fit a quasi-likelihood negative binomial GLM.

    Args:
        robust: Use robust empirical Bayes for QL dispersion. Default True.
    """
    _require("dgelist", "DGEList")
    _require("design", "design matrix")
    d = _state["dgelist"]

    fit = ep.glm_ql_fit(d, design=_state["design"], robust=robust)
    _state["fit"] = fit
    # Clear old results
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None

    n_genes = fit["coefficients"].shape[0]
    n_coef = fit["coefficients"].shape[1]
    pdf = fit.get("df.prior")
    s2 = fit.get("s2.prior")

    lines = [f"Fitted QL-GLM: {n_genes:,} genes, {n_coef} coefficients"]
    if pdf is not None:
        if np.isscalar(pdf):
            lines.append(f"Prior df: {pdf:.2f}")
        else:
            lines.append(f"Prior df (median): {np.median(pdf):.2f}")
    if s2 is not None:
        if np.isscalar(s2):
            lines.append(f"Prior QL dispersion: {s2:.4f}")
        else:
            lines.append(f"Prior QL dispersion (median): {np.median(s2):.4f}")
    lines.append(f"Coefficients: {', '.join(_state['design_info'])}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 7b: fit_glm
# ---------------------------------------------------------------------------

@mcp.tool()
def fit_glm() -> str:
    """Fit a negative binomial GLM (non-QL)."""
    _require("dgelist", "DGEList")
    _require("design", "design matrix")
    d = _state["dgelist"]

    fit = ep.glm_fit(d, design=_state["design"])
    _state["glm_fit"] = fit
    _state["results"] = {}
    _state["last_result"] = None
    _state["voom"] = None

    n_genes = fit["coefficients"].shape[0]
    n_coef = fit["coefficients"].shape[1]
    lines = [f"Fitted GLM: {n_genes:,} genes, {n_coef} coefficients"]
    lines.append(f"Coefficients: {', '.join(_state['design_info'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8: test_contrast
# ---------------------------------------------------------------------------

def _parse_contrast(contrast_str: str, col_names: list) -> np.ndarray:
    """Parse a contrast string like 'A - B' into a numeric vector.

    Supports:
        'A - B'           → simple difference
        '(A + B)/2 - C'   → average vs reference
        'A'               → single coefficient test
    """
    contrast = np.zeros(len(col_names))

    # Build lookup map: multiple aliases for each column index.
    # Patsy generates names like 'group[A]', 'group[T.A]', 'Intercept'.
    # Users may type 'groupA', 'group[A]', 'A', etc.
    clean_map = {}
    for i, cn in enumerate(col_names):
        clean_map[cn] = i  # exact name, e.g. 'group[A]'

        # Strip patsy treatment-coded wrapper: 'group[T.xxx]' → 'xxx'
        m = re.match(r'^(.+)\[T\.\s*(.+)\]$', cn)
        if m:
            prefix, val = m.group(1), m.group(2)
            clean_map[val] = i              # 'xxx'
            clean_map[prefix + val] = i     # 'groupxxx'

        # Strip patsy no-intercept wrapper: 'group[xxx]' → 'xxx'
        m2 = re.match(r'^(.+)\[(.+)\]$', cn)
        if m2:
            prefix, val = m2.group(1), m2.group(2)
            clean_map[val] = i              # 'xxx'  (e.g. 'A')
            clean_map[prefix + val] = i     # 'groupA'

    def _find_col(term: str) -> int:
        term = term.strip()
        if term in clean_map:
            return clean_map[term]
        # Case-insensitive fallback
        for k, v in clean_map.items():
            if k.lower() == term.lower():
                return v
        raise ValueError(
            f"Term '{term}' not found in design columns: {col_names}\n"
            f"Recognized aliases: {sorted(clean_map.keys())}"
        )

    # Try simple 'A - B' pattern first
    parts = re.split(r'\s*-\s*', contrast_str)
    if len(parts) == 2 and '(' not in contrast_str and '+' not in contrast_str:
        i_pos = _find_col(parts[0])
        i_neg = _find_col(parts[1])
        contrast[i_pos] = 1
        contrast[i_neg] = -1
        return contrast

    # General parser: split into +/- terms
    # Add '+' at start if not starting with '-'
    s = contrast_str.strip()
    if not s.startswith('-'):
        s = '+' + s

    # Tokenize: find all (+/-)(coefficient_or_group)
    tokens = re.findall(r'([+-])\s*(?:(\d*\.?\d*)\s*\*?\s*)?([A-Za-z_][\w.]*)', s)
    for sign, coeff, name in tokens:
        idx = _find_col(name)
        c = float(coeff) if coeff else 1.0
        if sign == '-':
            c = -c
        contrast[idx] += c

    if np.all(contrast == 0):
        raise ValueError(
            f"Could not parse contrast '{contrast_str}'. "
            f"Available columns: {col_names}"
        )

    return contrast


@mcp.tool()
def test_contrast(
    contrast: str,
    name: Optional[str] = None,
    method: str = "qlf",
    lfc: float = 0.585,
) -> str:
    """Test a contrast for differential expression.

    Args:
        contrast: Contrast string, e.g. 'groupB - groupA'. Terms must
            match design matrix column names (shown by set_design/describe).
        name: Label for this result set. Default: auto-generated.
        method: Test method — 'qlf' (quasi-likelihood F-test, default)
            or 'treat' (TREAT with log-FC threshold).
        lfc: Log2-fold-change threshold for TREAT method. Default: log2(1.5) ≈ 0.585.
    """
    _require("fit", "fitted model")
    fit = _state["fit"]
    col_names = _state["design_info"]

    contrast_vec = _parse_contrast(contrast, col_names)

    if method == "treat":
        res = ep.glm_treat(fit, contrast=contrast_vec, lfc=lfc)
    else:
        res = ep.glm_ql_ftest(fit, contrast=contrast_vec)

    if name is None:
        name = contrast.replace(" ", "")

    _state["results"][name] = res
    _state["last_result"] = name

    # Summary
    tt = ep.top_tags(res, n=res["table"].shape[0])
    table = tt["table"]
    fdr = table["FDR"].values
    logfc = table["logFC"].values

    n_up = int(((fdr < 0.05) & (logfc > 0)).sum())
    n_down = int(((fdr < 0.05) & (logfc < 0)).sum())
    n_ns = int((fdr >= 0.05).sum())

    lines = [
        f"Test: {method.upper()} — {contrast}",
        f"DE genes (FDR < 0.05): {n_up} up, {n_down} down, {n_ns} NS",
        "",
        "Top 5 genes:",
    ]

    top5 = ep.top_tags(res, n=5)["table"]
    # Format top genes
    lines.append(f"{'Gene':<20} {'logFC':>8} {'logCPM':>8} {'PValue':>10} {'FDR':>10}")
    lines.append("-" * 60)
    for _, row in top5.iterrows():
        gene = _gene_name(row)
        if len(gene) > 19:
            gene = gene[:17] + ".."
        lines.append(
            f"{gene:<20} {row['logFC']:>8.3f} {row['logCPM']:>8.2f} "
            f"{row['PValue']:>10.2e} {row['FDR']:>10.2e}"
        )

    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8b: test_coef
# ---------------------------------------------------------------------------

@mcp.tool()
def test_coef(
    coef: int,
    name: Optional[str] = None,
    method: str = "qlf",
    lfc: float = 0.585,
) -> str:
    """Test a single coefficient by index.

    Args:
        coef: 0-based coefficient index.
        name: Label for this result set. Default: auto-generated.
        method: 'qlf' (default) or 'treat'.
        lfc: Log2-fold-change threshold for TREAT method.
    """
    _require("fit", "fitted model")
    fit = _state["fit"]

    if method == "treat":
        res = ep.glm_treat(fit, coef=coef, lfc=lfc)
    else:
        res = ep.glm_ql_ftest(fit, coef=coef)

    if name is None:
        name = f"coef{coef}"

    _state["results"][name] = res
    _state["last_result"] = name

    tt = ep.top_tags(res, n=res["table"].shape[0])
    table = tt["table"]
    fdr = table["FDR"].values
    logfc = table["logFC"].values

    n_up = int(((fdr < 0.05) & (logfc > 0)).sum())
    n_down = int(((fdr < 0.05) & (logfc < 0)).sum())
    n_ns = int((fdr >= 0.05).sum())

    lines = [
        f"Test: {method.upper()} — coef {coef}",
        f"DE genes (FDR < 0.05): {n_up} up, {n_down} down, {n_ns} NS",
    ]
    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8c: glm_lrt_test
# ---------------------------------------------------------------------------

@mcp.tool()
def glm_lrt_test(
    coef: Optional[int] = None,
    contrast: Optional[list] = None,
    name: Optional[str] = None,
    use_ql_fit: bool = False,
) -> str:
    """Run a likelihood-ratio test (LRT) from a GLM fit.

    Args:
        coef: 0-based coefficient index (default: last).
        contrast: Optional contrast vector (same length as coefficients).
        name: Label for this result set. Default: auto-generated.
        use_ql_fit: If True, uses the QL fit from fit_model().
    """
    fit = _state["fit"] if use_ql_fit else _state["glm_fit"]
    if fit is None:
        raise ValueError("No GLM fit available. Run fit_glm() first.")

    res = ep.glm_lrt(fit, coef=coef, contrast=contrast)

    if name is None:
        if contrast is not None:
            name = "lrt_contrast"
        elif coef is not None:
            name = f"lrt_coef{coef}"
        else:
            name = "lrt"

    _state["results"][name] = res
    _state["last_result"] = name

    tt = ep.top_tags(res, n=res["table"].shape[0])
    table = tt["table"]
    fdr = table["FDR"].values
    logfc = table["logFC"].values if "logFC" in table.columns else np.zeros(len(table))

    n_up = int(((fdr < 0.05) & (logfc > 0)).sum())
    n_down = int(((fdr < 0.05) & (logfc < 0)).sum())
    n_ns = int((fdr >= 0.05).sum())

    lines = [
        "Test: LRT",
        f"DE genes (FDR < 0.05): {n_up} up, {n_down} down, {n_ns} NS",
    ]
    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8d: exact_test
# ---------------------------------------------------------------------------

@mcp.tool()
def exact_test(
    pair: Optional[list] = None,
    dispersion: str = "auto",
    rejection_region: str = "doubletail",
    big_count: int = 900,
    prior_count: float = 0.125,
    name: Optional[str] = None,
) -> str:
    """Run edgePython exact test for two-group DE.

    Args:
        pair: Two group labels to compare. Default: first two groups.
        dispersion: 'auto', 'common', 'trended', 'tagwise', or numeric.
        rejection_region: 'doubletail', 'deviance', or 'smallp'.
        big_count: Threshold for beta approximation.
        prior_count: Prior count for logFC calculation.
        name: Label for this result set. Default: auto-generated.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    res = ep.exact_test(
        d,
        pair=pair,
        dispersion=dispersion,
        rejection_region=rejection_region,
        big_count=big_count,
        prior_count=prior_count,
    )

    if name is None:
        if pair is None:
            name = "exact"
        else:
            name = f"exact_{pair[0]}_vs_{pair[1]}"

    _state["results"][name] = res
    _state["last_result"] = name

    tt = ep.top_tags(res, n=res["table"].shape[0])
    table = tt["table"]
    fdr = table["FDR"].values
    logfc = table["logFC"].values

    n_up = int(((fdr < 0.05) & (logfc > 0)).sum())
    n_down = int(((fdr < 0.05) & (logfc < 0)).sum())
    n_ns = int((fdr >= 0.05).sum())

    lines = [
        "Test: Exact",
        f"DE genes (FDR < 0.05): {n_up} up, {n_down} down, {n_ns} NS",
    ]
    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8e: dtu_diff_splice_dge
# ---------------------------------------------------------------------------

@mcp.tool()
def dtu_diff_splice_dge(
    gene_column: Optional[str] = None,
    exon_column: Optional[str] = None,
    prior_count: float = 0.125,
    name: str = "dtu_diff_splice_dge",
) -> str:
    """Run DTU testing with diff_splice_dge on the current DGEList.

    Requires a two-group design in sample metadata (samples['group']).
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]

    if d.get("samples") is None or "group" not in d["samples"].columns:
        raise ValueError("DGEList samples['group'] is required for diff_splice_dge.")

    gene_ids, exon_ids = _resolve_gene_exon_ids(d, gene_column=gene_column, exon_column=exon_column)
    res = ep.diff_splice_dge(
        d,
        geneid=gene_ids,
        exonid=exon_ids,
        group=d["samples"]["group"].values,
        dispersion="auto",
        prior_count=float(prior_count),
    )

    _state["results"][name] = res
    _state["last_result"] = name

    gt = res["gene.table"].copy()
    sig = gt[gt["FDR"] < 0.05]
    top = gt.sort_values("FDR", ascending=True).head(5)
    lines = [
        f"DTU: diff_splice_dge completed ({len(gt):,} genes).",
        f"Genes with DTU signal (FDR < 0.05): {len(sig):,}",
        "",
        "Top 5 genes by FDR:",
        f"{'GeneID':<24} {'NExons':>8} {'PValue':>12} {'FDR':>12}",
        "-" * 62,
    ]
    for _, row in top.iterrows():
        gid = str(row["GeneID"])
        if len(gid) > 23:
            gid = gid[:21] + ".."
        lines.append(
            f"{gid:<24} {int(row['NExons']):>8d} "
            f"{float(row['PValue']):>12.2e} {float(row['FDR']):>12.2e}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8f: dtu_splice_variants
# ---------------------------------------------------------------------------

@mcp.tool()
def dtu_splice_variants(
    gene_column: Optional[str] = None,
    name: str = "dtu_splice_variants",
) -> str:
    """Run splice_variants on the current DGEList to flag genes with isoform heterogeneity."""
    _require("dgelist", "DGEList")
    d = _state["dgelist"]
    gene_ids, _ = _resolve_gene_exon_ids(d, gene_column=gene_column, exon_column=None)

    sv = ep.splice_variants(d, gene_ids)
    if "FDR" not in sv.columns and "PValue" in sv.columns:
        _, fdr, _, _ = multipletests(sv["PValue"].values, method="fdr_bh")
        sv = sv.copy()
        sv["FDR"] = fdr

    _state["results"][name] = {"table": sv}
    _state["last_result"] = name

    sig = sv[sv["FDR"] < 0.05] if "FDR" in sv.columns else sv.iloc[0:0]
    top = sv.sort_values("FDR" if "FDR" in sv.columns else "PValue").head(5)

    lines = [
        f"DTU: splice_variants completed ({len(sv):,} genes).",
        f"Genes with splice-variant signal (FDR < 0.05): {len(sig):,}",
        "",
        "Top 5 genes:",
        f"{'GeneID':<24} {'NExons':>8} {'PValue':>12} {'FDR':>12}",
        "-" * 62,
    ]
    for _, row in top.iterrows():
        gid = str(row["GeneID"])
        if len(gid) > 23:
            gid = gid[:21] + ".."
        fdr_val = float(row["FDR"]) if "FDR" in row.index else np.nan
        lines.append(
            f"{gid:<24} {int(row['NExons']):>8d} "
            f"{float(row['PValue']):>12.2e} {fdr_val:>12.2e}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 9: get_top_genes
# ---------------------------------------------------------------------------

@mcp.tool()
def get_top_genes(
    name: Optional[str] = None,
    n: int = 20,
    fdr_threshold: float = 0.05,
) -> str:
    """Get the top differentially expressed genes from a test result.

    Args:
        name: Which result set to query. Default: most recent.
        n: Number of top genes to return. Default: 20.
        fdr_threshold: FDR cutoff for significance. Default: 0.05.
    """
    if name is None:
        name = _state["last_result"]
    if name is None or name not in _state["results"]:
        available = list(_state["results"].keys())
        if not available:
            return "No test results available. Run test_contrast() first."
        return f"Result '{name}' not found. Available: {', '.join(available)}"

    res = _state["results"][name]
    tt = ep.top_tags(res, n=n)
    table = tt["table"]

    # Filter by FDR
    sig = table[table["FDR"] < fdr_threshold]

    lines = [
        f"Result: {name} (showing top {min(n, len(table))} genes, "
        f"{len(sig)} with FDR < {fdr_threshold})",
        "",
    ]

    # Determine which stat column to use (F or LR)
    stat_col = "F" if "F" in table.columns else "LR" if "LR" in table.columns else None
    header = f"{'Gene':<20} {'logFC':>8} {'logCPM':>8}"
    if stat_col:
        header += f" {stat_col:>8}"
    header += f" {'PValue':>10} {'FDR':>10} {'Sig':>3}"
    lines.append(header)
    lines.append("-" * len(header))

    for _, row in table.iterrows():
        gene = _gene_name(row)
        if len(gene) > 19:
            gene = gene[:17] + ".."
        sig_mark = "***" if row["FDR"] < 0.001 else "**" if row["FDR"] < 0.01 else "*" if row["FDR"] < fdr_threshold else ""
        line = f"{gene:<20} {row['logFC']:>8.3f} {row['logCPM']:>8.2f}"
        if stat_col:
            line += f" {row[stat_col]:>8.2f}"
        line += f" {row['PValue']:>10.2e} {row['FDR']:>10.2e} {sig_mark:>3}"
        lines.append(line)

    _append_gene_level_warning(lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 9b: get_result_table
# ---------------------------------------------------------------------------

def _get_result_table_impl(
    name: Optional[str] = None,
    format: str = "tsv",
    max_rows: Optional[int] = None,
) -> str:
    """Internal helper — shared by get_result_table and save_results."""
    if name is None:
        name = _state["last_result"]
    if name is None or name not in _state["results"]:
        available = list(_state["results"].keys())
        if not available:
            return "No test results available."
        return f"Result '{name}' not found. Available: {', '.join(available)}"

    res = _state["results"][name]
    tt = ep.top_tags(res, n=res["table"].shape[0])
    table = tt["table"]
    if max_rows is not None:
        table = table.iloc[:max_rows]

    if format == "json":
        return table.to_json(orient="records")
    if format == "csv":
        return table.to_csv(index=False)
    if format == "tsv":
        return table.to_csv(sep="\t", index=False)
    return "Invalid format. Use 'tsv', 'csv', or 'json'."


@mcp.tool()
def get_result_table(
    name: Optional[str] = None,
    format: str = "tsv",
    max_rows: Optional[int] = None,
) -> str:
    """Return a full result table as TSV/CSV/JSON text.

    Args:
        name: Which result set to query. Default: most recent.
        format: 'tsv', 'csv', or 'json'.
        max_rows: Optional row cap.
    """
    return _get_result_table_impl(name=name, format=format, max_rows=max_rows)


# ---------------------------------------------------------------------------
# Tool 9c: save_results
# ---------------------------------------------------------------------------

@mcp.tool()
def save_results(
    output_path: str,
    name: Optional[str] = None,
    format: str = "tsv",
) -> str:
    """Save a full result table to disk.

    Args:
        output_path: Destination file path.
        name: Which result set to save. Default: most recent.
        format: 'tsv', 'csv', or 'json'.
    """
    content = _get_result_table_impl(name=name, format=format)
    if content.startswith("No test results") or content.startswith("Result '"):
        return content

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Saved results to: {output_path}"


# ---------------------------------------------------------------------------
# Tool 10: generate_plot
# ---------------------------------------------------------------------------

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


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _coerce_int(value: object, default: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return coerced if coerced > 0 else default


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_plot_dpi(value: object, default: int = 600) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        dpi = int(value)
        return dpi if dpi > 0 else default
    text = str(value).strip()
    if not text:
        return default
    if text.isdigit():
        dpi = int(text)
        return dpi if dpi > 0 else default
    normalized = re.sub(r"\s+", " ", re.sub(r"[-_]", " ", text.lower())).strip()
    return _DPI_PRESETS.get(normalized, default)


def _resolve_plot_output_paths(
    plot_type: str,
    result_name: Optional[str],
    output_path: Optional[str],
    svg_output_path: Optional[str],
) -> tuple[str, str]:
    if output_path is None:
        suffix = f"_{result_name}" if result_name else ""
        output_path = os.path.join(os.getcwd(), f"{plot_type}{suffix}.png")
    if svg_output_path is None:
        svg_output_path = str(Path(output_path).with_suffix(".svg"))
    return output_path, svg_output_path


def _save_plot_outputs(
    fig,
    *,
    label: str,
    output_path: str,
    svg_output_path: str,
    dpi: int,
) -> str:
    png_path = Path(output_path)
    svg_path = Path(svg_output_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    fig.savefig(svg_path, format="svg", bbox_inches="tight", facecolor="white")
    return f"{label} saved to: {png_path}\n{label} SVG saved to: {svg_path}"


def _apply_plot_header(fig, ax, *, title: str, subtitle: Optional[str] = None) -> None:
    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", pad=18)
    if subtitle:
        fig.subplots_adjust(top=0.84)
        fig.text(0.125, 0.93, subtitle, ha="left", va="center", fontsize=10, color="#4b5563")


def _apply_plot_frame(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, color="#e5e7eb", linewidth=0.6, alpha=0.9)
    ax.set_axisbelow(True)


def _place_legend_outside(
    ax,
    *,
    loc: str = "upper left",
    anchor: tuple[float, float] = (1.02, 1.0),
    fontsize: int = 8,
    ncol: int = 1,
):
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return None
    ax.figure.subplots_adjust(right=min(ax.figure.subplotpars.right, 0.78))
    legend = ax.legend(
        handles,
        labels,
        frameon=False,
        loc=loc,
        bbox_to_anchor=anchor,
        borderaxespad=0.0,
        fontsize=fontsize,
        ncol=ncol,
    )
    return legend


def _load_plot_input_table(input_path: str) -> pd.DataFrame:
    resolved = _resolve_existing_path(input_path)
    if resolved is None or not resolved.is_file():
        raise ValueError(f"Input path '{input_path}' was not found.")

    suffix = resolved.suffix.lower()
    sep_candidates: list[object]
    if suffix == ".csv":
        sep_candidates = [","]
    elif suffix in {".tsv", ".tab"}:
        sep_candidates = ["\t"]
    else:
        sep_candidates = [None, "\t", ","]

    last_error = ""
    for sep in sep_candidates:
        try:
            if sep is None:
                frame = pd.read_csv(resolved, sep=None, engine="python")
            else:
                frame = pd.read_csv(resolved, sep=sep)
            frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
            if frame.empty:
                last_error = "table is empty"
                continue
            return frame
        except Exception as exc:
            last_error = str(exc)

    raise ValueError(f"Could not read plot input table: {last_error}")


def _coerce_numeric_plot_frame(
    df: pd.DataFrame,
    *,
    exclude: tuple[str, ...] = (),
) -> pd.DataFrame:
    base = df.drop(columns=[col for col in exclude if col in df.columns], errors="ignore")
    numeric = base.apply(pd.to_numeric, errors="coerce")
    numeric = numeric.dropna(axis=1, how="all").dropna(axis=0, how="all")
    return numeric


def _infer_unique_label_column(
    df: pd.DataFrame,
    *,
    exclude: tuple[str, ...] = (),
) -> Optional[str]:
    excluded = set(exclude)
    for col in df.columns:
        if col in excluded or pd.api.types.is_numeric_dtype(df[col]):
            continue
        values = df[col].dropna().astype(str).str.strip()
        if not values.empty and len(values) == len(df) and values.nunique() == len(df):
            return str(col)
    for col in df.columns:
        if col in excluded or pd.api.types.is_numeric_dtype(df[col]):
            continue
        return str(col)
    return None


def _infer_heatmap_mode(matrix_df: pd.DataFrame) -> str:
    row_labels = [str(label).strip() for label in matrix_df.index]
    col_labels = [str(label).strip() for label in matrix_df.columns]
    if matrix_df.shape[0] == matrix_df.shape[1] and row_labels and set(row_labels) == set(col_labels):
        return "correlation"
    return "raw"


def _prepare_heatmap_matrix(df: pd.DataFrame) -> pd.DataFrame:
    label_col = _infer_unique_label_column(df)
    matrix_df = _coerce_numeric_plot_frame(df, exclude=(label_col,) if label_col else ())
    if matrix_df.empty:
        raise ValueError("heatmap requires at least one numeric column")

    if label_col:
        row_labels = df.loc[matrix_df.index, label_col].fillna("").astype(str).tolist()
        matrix_df.index = [label or f"row_{idx + 1}" for idx, label in enumerate(row_labels)]
    else:
        matrix_df.index = [str(idx) for idx in matrix_df.index]
    matrix_df.columns = [str(col) for col in matrix_df.columns]

    if _infer_heatmap_mode(matrix_df) == "correlation":
        matrix_df = matrix_df.loc[matrix_df.index, matrix_df.index]

    return matrix_df


def _heatmap_style(matrix_df: pd.DataFrame, *, mode: str) -> tuple[str, Optional[float], Optional[float], str]:
    values = matrix_df.to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("heatmap matrix has no finite numeric values")

    if mode == "correlation":
        limit = max(1.0, float(np.nanmax(np.abs(finite))))
        return "RdBu_r", -limit, limit, "Correlation"

    token_text = " ".join(str(col) for col in matrix_df.columns)
    diverging = (
        float(np.nanmin(finite)) < 0 < float(np.nanmax(finite))
        or bool(re.search(r"(?:log2?fc|fold[_ -]?change|z[_ -]?score|zscore|delta)", token_text, flags=re.IGNORECASE))
    )
    if diverging:
        limit = float(np.nanmax(np.abs(finite)))
        return "RdBu_r", -limit, limit, "Value"
    return "viridis", None, None, "Value"


def _prepare_pca_inputs(
    df: pd.DataFrame,
    *,
    color: Optional[str] = None,
) -> tuple[pd.DataFrame, list[str], Optional[pd.Series], Optional[str]]:
    group_col = color if color in df.columns else None
    label_col = _infer_unique_label_column(df, exclude=(group_col,) if group_col else ())
    if group_col is None:
        for col in df.columns:
            if col == label_col or pd.api.types.is_numeric_dtype(df[col]):
                continue
            n_unique = df[col].nunique(dropna=True)
            if 1 < n_unique < len(df):
                group_col = str(col)
                break

    numeric_df = _coerce_numeric_plot_frame(
        df,
        exclude=tuple(col for col in (label_col, group_col) if col),
    )
    if numeric_df.shape[0] < 2 or numeric_df.shape[1] < 2:
        raise ValueError("PCA requires at least two rows and two numeric columns")

    if label_col:
        labels = df.loc[numeric_df.index, label_col].fillna("").astype(str).tolist()
    else:
        labels = [str(idx) for idx in numeric_df.index]
    groups = df.loc[numeric_df.index, group_col].fillna("Unknown").astype(str) if group_col else None
    return numeric_df, labels, groups, group_col


def _coerce_bar_mode(value: object, *, default: str) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "grouped": "group",
        "stacked": "stack",
        "stack": "stack",
        "percent": "percent",
        "percentage": "percent",
        "normalized": "percent",
    }
    return aliases.get(normalized, normalized) if aliases.get(normalized, normalized) in {"group", "stack", "percent"} else default


def _coerce_bar_agg(value: object, *, default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"count", "sum", "mean"} else default


def _infer_bar_stat_columns(
    df: pd.DataFrame,
    *,
    y_column: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    error_candidates: list[str] = []
    n_candidates: list[str] = []
    if y_column:
        y_norm = str(y_column).strip().lower()
        error_candidates.extend([
            f"{y_norm}_sem",
            f"{y_norm}_se",
            f"{y_norm}_stderr",
            f"{y_norm}_std",
            f"{y_norm}_sd",
            f"{y_norm}_ci",
            f"sem_{y_norm}",
            f"se_{y_norm}",
        ])
        n_candidates.extend([
            f"{y_norm}_n",
            f"n_{y_norm}",
            f"{y_norm}_count",
            f"count_{y_norm}",
        ])
    error_candidates.extend(["sem", "se", "stderr", "std", "sd", "ci", "ci95", "error"])
    n_candidates.extend(["n", "count", "replicates", "n_reps", "sample_size"])

    normalized_columns = {str(col).strip().lower(): str(col) for col in df.columns}
    error_col = next((normalized_columns[key] for key in error_candidates if key in normalized_columns), None)
    n_col = next((normalized_columns[key] for key in n_candidates if key in normalized_columns), None)
    return error_col, n_col


def _prepare_bar_plot_table(
    df: pd.DataFrame,
    *,
    plot_type: str,
    x: Optional[str] = None,
    y: Optional[str] = None,
    color: Optional[str] = None,
    mode: Optional[str] = None,
    agg: Optional[str] = None,
) -> tuple[pd.DataFrame, str, Optional[str], str, str]:
    working = df.copy()
    for column_name, value in (("x", x), ("y", y), ("color", color)):
        if value and value not in working.columns:
            raise ValueError(f"{column_name} column '{value}' was not found")

    if x is None:
        for col in working.columns:
            if col == color or pd.api.types.is_numeric_dtype(working[col]):
                continue
            x = str(col)
            break
    if x is None:
        working = working.reset_index().rename(columns={"index": "__row__"})
        x = "__row__"

    numeric_cols = list(_coerce_numeric_plot_frame(working, exclude=tuple(col for col in (x, color) if col)).columns)
    if y and y not in numeric_cols:
        working[y] = pd.to_numeric(working[y], errors="coerce")
        if working[y].notna().sum() == 0:
            raise ValueError(f"y column '{y}' is not numeric")
        if y not in numeric_cols:
            numeric_cols.append(y)

    default_mode = "stack" if plot_type == "stacked_bar" else "group"
    resolved_mode = _coerce_bar_mode(mode, default=default_mode)

    if y is None and color is None and len(numeric_cols) > 1:
        working = working[[x] + numeric_cols].melt(
            id_vars=[x],
            value_vars=numeric_cols,
            var_name="series",
            value_name="value",
        )
        color = "series"
        y = "value"
    elif y is None and len(numeric_cols) == 1:
        y = numeric_cols[0]

    group_keys = [x] + ([color] if color else [])

    if y is None:
        plot_df = working.groupby(group_keys, dropna=False).size().reset_index(name="value")
        plot_df["n"] = plot_df["value"]
        agg_name = "count"
    else:
        repeated_groups = working[group_keys].duplicated(keep=False).any()
        agg_name = _coerce_bar_agg(
            agg,
            default=("sum" if plot_type == "stacked_bar" else ("mean" if repeated_groups else "value")),
        )
        if agg_name == "count":
            plot_df = working.groupby(group_keys, dropna=False).size().reset_index(name="value")
            plot_df["n"] = plot_df["value"]
        elif agg_name == "sum":
            plot_df = working.groupby(group_keys, dropna=False)[y].agg(["sum", "count"]).reset_index()
            plot_df = plot_df.rename(columns={"sum": "value", "count": "n"})
        elif agg_name == "mean":
            plot_df = working.groupby(group_keys, dropna=False)[y].agg(["mean", "count", "sem"]).reset_index()
            plot_df = plot_df.rename(columns={"mean": "value", "count": "n", "sem": "yerr"})
        else:
            keep_cols = group_keys + [y]
            plot_df = working[keep_cols].copy().rename(columns={y: "value"})
            error_col, n_col = _infer_bar_stat_columns(working, y_column=y)
            if error_col:
                plot_df["yerr"] = pd.to_numeric(working[error_col], errors="coerce")
            if n_col:
                plot_df["n"] = pd.to_numeric(working[n_col], errors="coerce")

    plot_df["value"] = pd.to_numeric(plot_df["value"], errors="coerce")
    plot_df = plot_df[np.isfinite(plot_df["value"])].copy()
    if plot_df.empty:
        raise ValueError("bar plot requires at least one finite numeric value")

    plot_df[x] = plot_df[x].fillna("").astype(str)
    if color:
        plot_df[color] = plot_df[color].fillna("Unknown").astype(str)
    if "yerr" in plot_df.columns:
        plot_df["yerr"] = pd.to_numeric(plot_df["yerr"], errors="coerce")
    if "n" in plot_df.columns:
        plot_df["n"] = pd.to_numeric(plot_df["n"], errors="coerce")
    return plot_df, x, color, resolved_mode, agg_name


def _annotate_bar_counts(ax, x_positions, heights, counts) -> None:
    if counts is None:
        return
    y_min, y_max = ax.get_ylim()
    offset = max((y_max - y_min) * 0.02, 0.02)
    for xpos, height, count in zip(x_positions, heights, counts):
        if not np.isfinite(height) or not np.isfinite(count):
            continue
        y_text = height + offset if height >= 0 else height - offset
        va = "bottom" if height >= 0 else "top"
        ax.text(xpos, y_text, f"n={int(count)}", ha="center", va=va, fontsize=7, color="#374151")


def _benjamini_hochberg(p_values: np.ndarray) -> np.ndarray:
    pvals = np.asarray(p_values, dtype=float)
    adjusted = np.full_like(pvals, np.nan, dtype=float)
    valid_mask = np.isfinite(pvals)
    valid = pvals[valid_mask]
    if valid.size == 0:
        return adjusted
    order = np.argsort(valid)
    ranked = valid[order] * valid.size / np.arange(1, valid.size + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    valid_adjusted = np.empty_like(valid)
    valid_adjusted[order] = np.clip(ranked, 0, 1)
    adjusted[valid_mask] = valid_adjusted
    return adjusted


def _normalize_label_genes(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[|,]", value)
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


@mcp.tool()
def generate_plot(
    plot_type: str,
    result_name: Optional[str] = None,
    input_path: Optional[str] = None,
    df_label: Optional[str] = None,
    output_path: Optional[str] = None,
    svg_output_path: Optional[str] = None,
    dpi: Optional[object] = None,
    use_fdr_y: object = False,
    pvalue_cutoff: object = 0.05,
    fdr_cutoff: object = 0.05,
    logfc_cutoff: object = 1.0,
    top_n_labels: object = 12,
    label_genes: Optional[object] = None,
    label_transcripts: object = False,
    x: Optional[str] = None,
    y: Optional[str] = None,
    color: Optional[str] = None,
    mode: Optional[str] = None,
    agg: Optional[str] = None,
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
) -> str:
    """Generate and save a visualization.

    Args:
        plot_type: Type of plot. One of:
            'mds' — multi-dimensional scaling of samples,
            'bcv' — biological coefficient of variation vs abundance,
            'ql_dispersion' — quasi-likelihood dispersion plot,
            'md'/'ma' — mean-difference (MA) plot for a test result,
            'volcano' — volcano plot (logFC vs -log10 p-value),
            'pca' — principal component analysis of an input table,
            'heatmap' — heatmap of top DE genes or an input table,
            'bar' — publication-style categorical bar chart from an input table,
            'stacked_bar' — stacked or percent bar chart from an input table,
            'enrichment_bar' — horizontal bar chart of enrichment results,
            'enrichment_dot' — dot/bubble plot of enrichment results.
        result_name: Which test result to plot (for md/volcano/heatmap).
            For enrichment plots, the name of the enrichment result.
            Default: most recent result.
        input_path: Optional generic table input path resolved by Cortex.
        df_label: Optional display label for the resolved dataframe source.
        output_path: Path to save the raster output. Default: auto-generated
            in the current working directory.
        svg_output_path: Optional path to save the SVG companion.
        dpi: Raster DPI value or preset phrase.
        label_transcripts: Include transcript/isoform IDs in labels when available.
        x/y/color/mode/agg: Optional generic table plotting parameters for
            bar/stacked_bar/PCA input tables.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_type = str(plot_type or "").strip().lower()
    valid_types = {
        "mds",
        "bcv",
        "ql_dispersion",
        "md",
        "ma",
        "volcano",
        "pca",
        "heatmap",
        "bar",
        "stacked_bar",
        "enrichment_bar",
        "enrichment_dot",
    }
    if plot_type not in valid_types:
        return f"Invalid plot_type '{plot_type}'. Choose from: {', '.join(sorted(valid_types))}"

    branch_plot_type = "md" if plot_type == "ma" else plot_type

    output_path, svg_output_path = _resolve_plot_output_paths(
        plot_type,
        result_name,
        output_path,
        svg_output_path,
    )
    raster_dpi = _coerce_plot_dpi(dpi, default=600)
    use_fdr_y = _coerce_bool(use_fdr_y, default=False)
    pvalue_cutoff = _coerce_float(pvalue_cutoff, 0.05)
    fdr_cutoff = _coerce_float(fdr_cutoff, 0.05)
    logfc_cutoff = abs(_coerce_float(logfc_cutoff, 1.0))
    top_n_labels = max(0, _coerce_int(top_n_labels, 12))
    label_genes = _normalize_label_genes(label_genes)
    label_transcripts = _coerce_bool(label_transcripts, default=False)

    d = _state["dgelist"]

    if branch_plot_type == "pca":
        if not input_path:
            return "PCA plot requires input_path resolved from a dataframe."
        try:
            table = _load_plot_input_table(input_path)
            numeric_df, point_labels, groups, group_col = _prepare_pca_inputs(table, color=color)
        except ValueError as exc:
            return f"PCA plot could not be generated: {exc}"

        values = numeric_df.to_numpy(dtype=float)
        column_means = np.nanmean(values, axis=0)
        if np.isnan(column_means).any():
            return "PCA plot could not be generated because some numeric columns contain only missing values."
        nan_rows, nan_cols = np.where(~np.isfinite(values))
        if nan_rows.size:
            values[nan_rows, nan_cols] = column_means[nan_cols]

        column_stds = values.std(axis=0, ddof=0)
        keep_mask = np.isfinite(column_stds) & (column_stds > 0)
        if int(keep_mask.sum()) < 2:
            return "PCA plot requires at least two varying numeric columns."
        values = values[:, keep_mask]
        values = values - values.mean(axis=0)
        values = values / column_stds[keep_mask]

        try:
            u, singular_values, _ = np.linalg.svd(values, full_matrices=False)
        except np.linalg.LinAlgError as exc:
            return f"PCA plot could not be generated: {exc}"
        if singular_values.size < 2:
            return "PCA plot requires at least two principal components."

        scores = u[:, :2] * singular_values[:2]
        explained = (singular_values ** 2) / np.sum(singular_values ** 2)

        fig, ax = plt.subplots(figsize=(8.5, 7))
        _apply_plot_frame(ax)
        ax.axhline(0, color="#d1d5db", linewidth=0.8)
        ax.axvline(0, color="#d1d5db", linewidth=0.8)

        if groups is not None:
            categories = list(dict.fromkeys(groups.tolist()))
            palette = plt.cm.tab10(np.linspace(0, 1, max(len(categories), 1)))
            for idx, category in enumerate(categories):
                mask = groups == category
                ax.scatter(
                    scores[mask, 0],
                    scores[mask, 1],
                    s=52,
                    alpha=0.88,
                    linewidths=0.5,
                    edgecolors="white",
                    color=palette[idx],
                    label=category,
                )
        else:
            ax.scatter(
                scores[:, 0],
                scores[:, 1],
                s=56,
                alpha=0.88,
                linewidths=0.5,
                edgecolors="white",
                color="#2563eb",
            )

        if len(point_labels) <= 20:
            for px, py, label in zip(scores[:, 0], scores[:, 1], point_labels):
                ax.text(px, py, label, fontsize=8, ha="left", va="bottom", color="#111827")

        ax.set_xlabel(xlabel or f"PC1 ({explained[0] * 100:.1f}%)")
        ax.set_ylabel(ylabel or f"PC2 ({explained[1] * 100:.1f}%)")
        pca_label = df_label or Path(str(input_path)).stem
        pca_subtitle = subtitle or f"Input: {pca_label}; numeric columns standardized before SVD"
        _apply_plot_header(fig, ax, title=title or f"PCA: {pca_label}", subtitle=pca_subtitle)
        if groups is not None:
            _place_legend_outside(ax, loc="upper left", anchor=(1.02, 1.0), fontsize=8)
        saved_text = _save_plot_outputs(
            fig,
            label="PCA plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    if branch_plot_type == "mds":
        _require("dgelist", "DGEList")
        fig, ax = ep.plot_mds(d)
        _apply_plot_header(fig, ax, title=title or "MDS plot", subtitle=subtitle)
        saved_text = _save_plot_outputs(
            fig,
            label="MDS plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "bcv":
        _require("dgelist", "DGEList")
        if not _state["dispersions_estimated"]:
            return "Dispersions not estimated yet. Run estimate_dispersion() first."
        fig, ax = ep.plot_bcv(d)
        _apply_plot_header(fig, ax, title=title or "BCV plot", subtitle=subtitle)
        saved_text = _save_plot_outputs(
            fig,
            label="BCV plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "ql_dispersion":
        _require("fit", "fitted model")
        fig, ax = ep.plot_ql_disp(_state["fit"])
        _apply_plot_header(fig, ax, title=title or "QL dispersion plot", subtitle=subtitle)
        saved_text = _save_plot_outputs(
            fig,
            label="QL dispersion plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "md":
        if result_name is None:
            result_name = _state["last_result"]
        if result_name is None or result_name not in _state["results"]:
            return "No test result available. Run test_contrast() first."
        res = _state["results"][result_name]
        status = ep.decide_tests(res)
        fig, ax = ep.plot_md(res, status=status)
        _apply_plot_frame(ax)
        ax.set_xlabel(ax.get_xlabel() or "Average logCPM")
        ax.set_ylabel(ax.get_ylabel() or "log2 fold change")
        if logfc_cutoff > 0:
            ax.axhline(logfc_cutoff, color="#9ca3af", linestyle="--", linewidth=0.8)
            ax.axhline(-logfc_cutoff, color="#9ca3af", linestyle="--", linewidth=0.8)
        md_label = "MA plot" if plot_type == "ma" else "MD plot"
        md_subtitle = subtitle or f"Thresholds: |log2FC| >= {logfc_cutoff:g}"
        _apply_plot_header(fig, ax, title=title or f"{md_label}: {result_name}", subtitle=md_subtitle)
        saved_text = _save_plot_outputs(
            fig,
            label=md_label,
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "volcano":
        if result_name is None:
            result_name = _state["last_result"]
        if result_name is None or result_name not in _state["results"]:
            return "No test result available. Run test_contrast() first."
        res = _state["results"][result_name]
        tt = ep.top_tags(res, n=res["table"].shape[0])
        table = tt["table"]

        if "logFC" not in table.columns or "PValue" not in table.columns:
            return "Volcano plot requires logFC and PValue columns in the result table."

        logfc = pd.to_numeric(table["logFC"], errors="coerce").to_numpy(dtype=float)
        pval = pd.to_numeric(table["PValue"], errors="coerce").to_numpy(dtype=float)
        if "FDR" in table.columns:
            fdr = pd.to_numeric(table["FDR"], errors="coerce").to_numpy(dtype=float)
        else:
            fdr = _benjamini_hochberg(pval)
        plot_labels = [
            _format_feature_label(row, include_transcript=label_transcripts, separator="\n")
            for _, row in table.iterrows()
        ]
        label_candidates = [_feature_label_candidates(row) for _, row in table.iterrows()]

        metric_source = fdr if use_fdr_y else pval
        metric_label = "FDR" if use_fdr_y else "p-value"
        metric_cutoff = fdr_cutoff if use_fdr_y else pvalue_cutoff
        finite_metric = np.isfinite(metric_source) & (metric_source > 0)
        finite_logfc = np.isfinite(logfc)
        valid_mask = finite_metric & finite_logfc
        if not valid_mask.any():
            return "Volcano plot could not be generated because no valid logFC/p-value points were found."

        clipped_metric = np.clip(metric_source, 1e-300, 1)
        neg_log_metric = -np.log10(clipped_metric)
        sig_mask = valid_mask & (metric_source <= metric_cutoff) & (np.abs(logfc) >= logfc_cutoff)
        up_mask = sig_mask & (logfc > 0)
        down_mask = sig_mask & (logfc < 0)
        ns_mask = valid_mask & ~(up_mask | down_mask)

        up_color = "#d55e00"
        down_color = "#0072b2"
        ns_color = "#b8b8b8"
        fig, ax = plt.subplots(figsize=(9.5, 7))
        _apply_plot_frame(ax)
        ax.scatter(logfc[ns_mask], neg_log_metric[ns_mask], c=ns_color, s=14, alpha=0.55, linewidths=0, label="Not significant")
        ax.scatter(logfc[down_mask], neg_log_metric[down_mask], c=down_color, s=18, alpha=0.8, linewidths=0, label="Down")
        ax.scatter(logfc[up_mask], neg_log_metric[up_mask], c=up_color, s=18, alpha=0.8, linewidths=0, label="Up")
        ax.set_xlabel("log2 fold change")
        ax.set_ylabel(f"-log10({metric_label})")

        threshold_y = -np.log10(max(metric_cutoff, 1e-300))
        ax.axvline(logfc_cutoff, color="#6b7280", linestyle="--", linewidth=0.9)
        ax.axvline(-logfc_cutoff, color="#6b7280", linestyle="--", linewidth=0.9)
        ax.axhline(threshold_y, color="#6b7280", linestyle="--", linewidth=0.9)

        finite_logfc_values = logfc[valid_mask]
        finite_metric_values = neg_log_metric[valid_mask]
        max_x = max(float(np.nanpercentile(np.abs(finite_logfc_values), 99.5)), logfc_cutoff * 1.3, 1.5)
        max_y = max(float(np.nanpercentile(finite_metric_values, 99.5)), threshold_y * 1.25, 2.0)
        ax.set_xlim(-max_x * 1.05, max_x * 1.05)
        ax.set_ylim(0, max_y * 1.05)
        ax.text(
            ax.get_xlim()[1] * 0.98,
            threshold_y,
            f"{metric_label} <= {metric_cutoff:g}",
            ha="right",
            va="bottom",
            fontsize=8,
            color="#374151",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 1.5},
        )
        ax.text(logfc_cutoff, ax.get_ylim()[1] * 0.98, f"+|log2FC| >= {logfc_cutoff:g}", rotation=90, va="top", ha="left", fontsize=8, color="#374151")
        ax.text(-logfc_cutoff, ax.get_ylim()[1] * 0.98, f"-|log2FC| >= {logfc_cutoff:g}", rotation=90, va="top", ha="right", fontsize=8, color="#374151")

        counts_text = "\n".join([
            f"n_up: {int(up_mask.sum())}",
            f"n_down: {int(down_mask.sum())}",
            f"n_ns: {int(ns_mask.sum())}",
        ])
        ax.text(
            0.02,
            0.98,
            counts_text,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=9,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "boxstyle": "round,pad=0.35", "alpha": 0.95},
        )

        forced_labels = {label.lower() for label in label_genes}
        forced_indices = [
            idx for idx, candidates in enumerate(label_candidates)
            if forced_labels.intersection(candidates)
        ]
        sig_candidates = np.where(sig_mask)[0]
        ranked_candidates = sorted(
            sig_candidates.tolist(),
            key=lambda idx: (neg_log_metric[idx], abs(logfc[idx])),
            reverse=True,
        )
        label_indices: list[int] = []
        for idx in forced_indices + ranked_candidates:
            if idx not in label_indices:
                label_indices.append(idx)
            if len(label_indices) >= len(forced_indices) + top_n_labels:
                break

        label_fontstyle = "normal" if label_transcripts else "italic"
        if label_indices:
            try:
                from adjustText import adjust_text
            except ImportError:
                adjust_text = None

            if adjust_text is not None:
                texts = [
                    ax.text(
                        logfc[idx],
                        neg_log_metric[idx],
                        plot_labels[idx],
                        fontsize=8,
                        fontstyle=label_fontstyle,
                        color="#111827",
                        zorder=5,
                    )
                    for idx in label_indices
                ]
                adjust_text(
                    texts,
                    x=logfc[label_indices],
                    y=neg_log_metric[label_indices],
                    ax=ax,
                    expand_points=(1.2, 1.3),
                    expand_text=(1.05, 1.2),
                    force_text=(0.18, 0.25),
                    arrowprops={"arrowstyle": "-", "color": "#6b7280", "lw": 0.6, "alpha": 0.9},
                )
            else:
                x_span = max(ax.get_xlim()[1] - ax.get_xlim()[0], 1.0)
                y_span = max(ax.get_ylim()[1] - ax.get_ylim()[0], 1.0)
                for offset, idx in enumerate(label_indices):
                    x_offset = (0.03 * x_span) * (1 if logfc[idx] >= 0 else -1)
                    y_offset = 0.03 * y_span * (1 + (offset % 4) * 0.35)
                    ax.annotate(
                        plot_labels[idx],
                        xy=(logfc[idx], neg_log_metric[idx]),
                        xytext=(logfc[idx] + x_offset, neg_log_metric[idx] + y_offset),
                        textcoords="data",
                        ha="left" if logfc[idx] >= 0 else "right",
                        va="bottom",
                        fontsize=8,
                        fontstyle=label_fontstyle,
                        color="#111827",
                        arrowprops={"arrowstyle": "-", "color": "#6b7280", "lw": 0.6, "alpha": 0.9},
                    )

        volcano_subtitle = subtitle or (
            f"Thresholds: |log2FC| >= {logfc_cutoff:g}, {metric_label} <= {metric_cutoff:g}; "
            f"colors use a colorblind-safe palette"
        )
        _apply_plot_header(fig, ax, title=title or f"Volcano plot: {result_name}", subtitle=volcano_subtitle)
        _place_legend_outside(ax, loc="upper left", anchor=(1.02, 1.0), fontsize=8)
        saved_text = _save_plot_outputs(
            fig,
            label="Volcano plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "heatmap" and input_path:
        try:
            table = _load_plot_input_table(input_path)
            matrix_df = _prepare_heatmap_matrix(table)
            heatmap_mode = _infer_heatmap_mode(matrix_df)
            cmap, vmin, vmax, colorbar_label = _heatmap_style(matrix_df, mode=heatmap_mode)
        except ValueError as exc:
            return f"Heatmap could not be generated: {exc}"

        fig, ax = plt.subplots(
            figsize=(max(6, matrix_df.shape[1] * 0.45), max(5, matrix_df.shape[0] * 0.28))
        )
        image = ax.imshow(
            matrix_df.to_numpy(dtype=float),
            aspect="auto",
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
        )
        ax.set_xticks(range(matrix_df.shape[1]))
        ax.set_xticklabels(matrix_df.columns, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(matrix_df.shape[0]))
        ax.set_yticklabels(matrix_df.index, fontsize=7)
        heatmap_label = df_label or Path(str(input_path)).stem
        heatmap_subtitle = subtitle or (
            "Correlation heatmap (square row/column labels matched)"
            if heatmap_mode == "correlation"
            else f"Raw numeric matrix from {heatmap_label}"
        )
        _apply_plot_header(fig, ax, title=title or f"Heatmap: {heatmap_label}", subtitle=heatmap_subtitle)
        fig.colorbar(image, ax=ax, label=colorbar_label, shrink=0.7)
        saved_text = _save_plot_outputs(
            fig,
            label="Heatmap",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "heatmap":
        if result_name is None:
            result_name = _state["last_result"]
        if result_name is None or result_name not in _state["results"]:
            return "No test result available. Run test_contrast() first."
        res = _state["results"][result_name]
        tt = ep.top_tags(res, n=30)
        table = tt["table"]

        logcpm = ep.cpm(d, log=True)
        heatmap_rows = list(table.iterrows())
        top_feature_keys = [_feature_lookup_key(row) for _, row in heatmap_rows]
        top_feature_labels = [
            _format_feature_label(row, include_transcript=label_transcripts, separator=" | ")
            for _, row in heatmap_rows
        ]

        gene_ids = [str(gene_id) for gene_id in d.get("_gene_ids", [])]
        idx = []
        gene_labels = []
        for feature_key, feature_label in zip(top_feature_keys, top_feature_labels):
            feature_key = str(feature_key).strip()
            if feature_key in gene_ids:
                idx.append(gene_ids.index(feature_key))
                gene_labels.append(feature_label)
        if not idx:
            idx = list(range(min(30, logcpm.shape[0])))
            gene_labels = [str(i) for i in idx]

        mat = logcpm[idx, :]
        mat_z = (mat - mat.mean(axis=1, keepdims=True)) / (mat.std(axis=1, keepdims=True) + 1e-10)

        fig, ax = plt.subplots(figsize=(max(6, len(d.get("_sample_names", [])) * 0.6), max(6, len(idx) * 0.3)))
        im = ax.imshow(mat_z, aspect="auto", cmap="RdBu_r", vmin=-3, vmax=3)
        sample_names = d.get("_sample_names", [f"S{i+1}" for i in range(mat.shape[1])])
        ax.set_xticks(range(len(sample_names)))
        ax.set_xticklabels(sample_names, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(gene_labels)))
        ax.set_yticklabels(gene_labels, fontsize=7)
        _apply_plot_header(fig, ax, title=title or f"Top DE genes: {result_name}", subtitle=subtitle)
        fig.colorbar(im, ax=ax, label="z-score (log-CPM)", shrink=0.6)
        saved_text = _save_plot_outputs(
            fig,
            label="Heatmap",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type in {"bar", "stacked_bar"}:
        if not input_path:
            return "Bar plots require input_path resolved from a dataframe."
        try:
            table = _load_plot_input_table(input_path)
            plot_df, x_col, color_col, bar_mode, agg_name = _prepare_bar_plot_table(
                table,
                plot_type=branch_plot_type,
                x=x,
                y=y,
                color=color,
                mode=mode,
                agg=agg,
            )
        except ValueError as exc:
            return f"Bar plot could not be generated: {exc}"

        category_order = list(dict.fromkeys(plot_df[x_col].tolist()))
        fig, ax = plt.subplots(figsize=(max(7, len(category_order) * 0.7), 6.5))
        _apply_plot_frame(ax)
        ax.margins(y=0.18)
        positions = np.arange(len(category_order), dtype=float)

        if color_col:
            series_names = list(dict.fromkeys(plot_df[color_col].tolist()))
            palette = plt.cm.tab10(np.linspace(0, 1, max(len(series_names), 1)))
            if bar_mode in {"stack", "percent"}:
                value_pivot = plot_df.pivot_table(
                    index=x_col,
                    columns=color_col,
                    values="value",
                    aggfunc="sum",
                    fill_value=0.0,
                ).reindex(category_order).fillna(0.0)
                if bar_mode == "percent":
                    row_sums = value_pivot.sum(axis=1).replace(0, np.nan)
                    value_pivot = value_pivot.div(row_sums, axis=0).fillna(0.0) * 100.0
                bottoms = np.zeros(len(category_order), dtype=float)
                for idx, series_name in enumerate(value_pivot.columns):
                    values = value_pivot[series_name].to_numpy(dtype=float)
                    ax.bar(
                        positions,
                        values,
                        width=0.72,
                        bottom=bottoms,
                        color=palette[idx],
                        label=series_name,
                    )
                    bottoms += values
                if "n" in plot_df.columns:
                    total_counts = (
                        plot_df.groupby(x_col)["n"].sum().reindex(category_order).to_numpy(dtype=float)
                    )
                    _annotate_bar_counts(ax, positions, bottoms, total_counts)
            else:
                width = 0.82 / max(len(series_names), 1)
                for idx, series_name in enumerate(series_names):
                    subset = plot_df[plot_df[color_col] == series_name].set_index(x_col).reindex(category_order)
                    values = pd.to_numeric(subset["value"], errors="coerce").fillna(0).to_numpy(dtype=float)
                    candidate_yerr = None
                    if "yerr" in subset.columns:
                        candidate_yerr = pd.to_numeric(subset["yerr"], errors="coerce").to_numpy(dtype=float)
                        if not np.isfinite(candidate_yerr).any():
                            candidate_yerr = None
                    offset = (idx - (len(series_names) - 1) / 2) * width
                    ax.bar(
                        positions + offset,
                        values,
                        width=width,
                        color=palette[idx],
                        label=series_name,
                        yerr=np.nan_to_num(candidate_yerr, nan=0.0) if candidate_yerr is not None else None,
                        capsize=4 if candidate_yerr is not None else 0,
                    )
                    if "n" in subset.columns:
                        counts = pd.to_numeric(subset["n"], errors="coerce").to_numpy(dtype=float)
                        _annotate_bar_counts(ax, positions + offset, values, counts)
        else:
            ordered = plot_df.drop_duplicates(subset=[x_col], keep="last").set_index(x_col).reindex(category_order)
            values = pd.to_numeric(ordered["value"], errors="coerce").fillna(0).to_numpy(dtype=float)
            candidate_yerr = None
            if "yerr" in ordered.columns:
                candidate_yerr = pd.to_numeric(ordered["yerr"], errors="coerce").to_numpy(dtype=float)
                if not np.isfinite(candidate_yerr).any():
                    candidate_yerr = None
            ax.bar(
                positions,
                values,
                width=0.72,
                color="#2563eb",
                yerr=np.nan_to_num(candidate_yerr, nan=0.0) if candidate_yerr is not None else None,
                capsize=4 if candidate_yerr is not None else 0,
            )
            if "n" in ordered.columns:
                counts = pd.to_numeric(ordered["n"], errors="coerce").to_numpy(dtype=float)
                _annotate_bar_counts(ax, positions, values, counts)

        rotate = any(len(str(label)) > 10 for label in category_order)
        ax.set_xticks(positions)
        ax.set_xticklabels(category_order, rotation=35 if rotate else 0, ha="right" if rotate else "center")
        ax.set_xlabel(xlabel or x_col)
        if bar_mode == "percent":
            y_axis_label = ylabel or "Percent of total"
        elif agg_name == "count":
            y_axis_label = ylabel or "Count"
        elif y:
            y_axis_label = ylabel or y
        else:
            y_axis_label = ylabel or "Value"
        ax.set_ylabel(y_axis_label)

        source_label = df_label or Path(str(input_path)).stem
        bar_title = title or (
            f"Stacked bar chart: {source_label}"
            if branch_plot_type == "stacked_bar" or bar_mode in {"stack", "percent"}
            else f"Bar chart: {source_label}"
        )
        if subtitle is None:
            subtitle_bits = []
            if agg_name in {"count", "sum", "mean"}:
                subtitle_bits.append(f"{agg_name} by category")
            if "yerr" in plot_df.columns and pd.to_numeric(plot_df["yerr"], errors="coerce").notna().any():
                subtitle_bits.append("error bars shown")
            if "n" in plot_df.columns and pd.to_numeric(plot_df["n"], errors="coerce").notna().any():
                subtitle_bits.append("n annotated above bars")
            subtitle_bits.append(f"input: {source_label}")
            bar_subtitle = "; ".join(subtitle_bits)
        else:
            bar_subtitle = subtitle
        _apply_plot_header(fig, ax, title=bar_title, subtitle=bar_subtitle)
        if color_col:
            _place_legend_outside(ax, loc="upper left", anchor=(1.02, 1.0), fontsize=8)
        saved_text = _save_plot_outputs(
            fig,
            label="Bar plot" if branch_plot_type == "bar" else "Stacked bar plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "enrichment_bar":
        ename = result_name or _state.get("last_enrichment")
        if not ename or ename not in _state.get("enrichment_results", {}):
            return "No enrichment results available. Run run_go_enrichment() first."
        df = _state["enrichment_results"][ename]
        if df.empty:
            return "Enrichment result is empty — nothing to plot."
        plot_df = df.head(20).copy()
        plot_df["neg_log10_p"] = -np.log10(np.clip(plot_df["p_value"].values, 1e-300, 1))
        source_colors = {
            "GO:BP": "#1b9e77", "GO:MF": "#d95f02", "GO:CC": "#7570b3",
            "KEGG": "#e7298a", "REAC": "#66a61e",
        }
        colors = [source_colors.get(s, "#999999") for s in plot_df["source"]]
        fig, ax = plt.subplots(figsize=(10, max(4, len(plot_df) * 0.35)))
        ax.barh(range(len(plot_df)), plot_df["neg_log10_p"].values, color=colors)
        ax.set_yticks(range(len(plot_df)))
        ax.set_yticklabels(plot_df["name"].values, fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("-log10(p-value)")
        _apply_plot_header(fig, ax, title=title or f"Enrichment: {ename}", subtitle=subtitle)
        from matplotlib.patches import Patch
        present = plot_df["source"].unique()
        legend_handles = [Patch(color=source_colors.get(s, "#999"), label=s) for s in present]
        if legend_handles:
            ax.figure.subplots_adjust(right=min(ax.figure.subplotpars.right, 0.78))
            ax.legend(
                handles=legend_handles,
                frameon=False,
                loc="upper left",
                bbox_to_anchor=(1.02, 1.0),
                borderaxespad=0.0,
                fontsize=8,
            )
        saved_text = _save_plot_outputs(
            fig,
            label="Enrichment bar plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text

    elif branch_plot_type == "enrichment_dot":
        ename = result_name or _state.get("last_enrichment")
        if not ename or ename not in _state.get("enrichment_results", {}):
            return "No enrichment results available. Run run_go_enrichment() first."
        df = _state["enrichment_results"][ename]
        if df.empty:
            return "Enrichment result is empty — nothing to plot."
        plot_df = df.head(20).copy()
        plot_df["neg_log10_p"] = -np.log10(np.clip(plot_df["p_value"].values, 1e-300, 1))
        plot_df["gene_ratio"] = plot_df["intersection_size"] / plot_df["query_size"]
        fig, ax = plt.subplots(figsize=(8, max(4, len(plot_df) * 0.35)))
        scatter = ax.scatter(
            plot_df["gene_ratio"], range(len(plot_df)),
            s=plot_df["intersection_size"].values * 8,
            c=plot_df["neg_log10_p"].values, cmap="YlOrRd", edgecolors="grey", linewidth=0.5,
        )
        ax.set_yticks(range(len(plot_df)))
        ax.set_yticklabels(plot_df["name"].values, fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("Gene Ratio")
        _apply_plot_header(fig, ax, title=title or f"Enrichment: {ename}", subtitle=subtitle)
        fig.colorbar(scatter, ax=ax, label="-log10(p-value)", shrink=0.6)
        saved_text = _save_plot_outputs(
            fig,
            label="Enrichment dot plot",
            output_path=output_path,
            svg_output_path=svg_output_path,
            dpi=raster_dpi,
        )
        plt.close(fig)
        return saved_text


# ===========================================================================
# Gene annotation tools
# ===========================================================================


@mcp.tool()
def annotate_genes(organism: Optional[str] = None, annotation_gtf: Optional[str] = None) -> str:
    """Add gene symbol annotations to the currently loaded dataset.

    Auto-detects organism from gene ID prefixes if not specified.
    Requires data to be loaded first (load_data or load_data_auto).

    Args:
        organism: Optional organism label. Auto-detected if not provided.
        annotation_gtf: Optional explicit GTF path. If omitted, a reconciled.gtf
            in the active work directory is preferred automatically.
    """
    _require("dgelist", "DGEList")
    d = _state["dgelist"]
    genes = d.get("genes")
    if genes is None:
        return "No gene annotation available in DGEList."
    annotation_info = _configure_annotation_sources(
        work_dir=_state.get("work_dir"),
        annotation_gtf=annotation_gtf or _state.get("annotation_gtf"),
    )
    if annotation_info and annotation_info.get("gtf_path"):
        _state["annotation_gtf"] = annotation_info["gtf_path"]
    if not _annotator.is_available:
        return (
            "Gene annotation data not available. Shared caches are generated at startup, "
            "or provide annotation_gtf to build a colocated cache for a specific GTF."
        )

    gdf = genes if isinstance(genes, pd.DataFrame) else pd.DataFrame(genes)

    # Find the ID column
    id_col = None
    for col in ("GeneID", "gene_id"):
        if col in gdf.columns:
            id_col = col
            break
    if id_col is None:
        return "No gene ID column found in gene annotation."

    _annotator.annotate_dataframe(gdf, id_column=id_col, symbol_column="Symbol")
    d["genes"] = gdf

    n_total = len(gdf)
    n_annotated = int(gdf["Symbol"].notna().sum())
    organism_detected = _annotator.detect_organism(str(gdf[id_col].iloc[0])) if n_total else None
    org_label = organism or organism_detected or "unknown"
    message = (
        f"Annotated {n_annotated:,}/{n_total:,} genes with symbols "
        f"(organism: {org_label})"
    )
    if annotation_info and annotation_info.get("gtf_path"):
        message += f" using {annotation_info['gtf_path']}"
    return message


# ===========================================================================
# Single-cell tools
# ===========================================================================

_sc_state: dict = {
    "counts": None,       # genes × cells ndarray
    "design": None,       # cells × predictors DataFrame
    "sample": None,       # per-cell sample IDs (array)
    "gene_names": None,   # gene name array
    "fit": None,          # glm_sc_fit result dict
    "n_cells": 0,
    "n_genes": 0,
    "n_samples": 0,
}


# ---------------------------------------------------------------------------
# SC Tool 1: load_sc_data
# ---------------------------------------------------------------------------

@mcp.tool()
def load_sc_data(
    h5ad_path: str,
    sample_col: str,
    cell_type_col: Optional[str] = None,
    cell_type_filter: Optional[list] = None,
    layer: str = "raw",
    log1p_transform: bool = True,
) -> str:
    """Load single-cell count data from an h5ad file.

    Reads counts, cell metadata, and gene names. Optionally subsets
    to specific cell types.

    Args:
        h5ad_path: Path to .h5ad file.
        sample_col: Column in obs for sample/subject IDs (e.g. 'orgID').
        cell_type_col: Optional column in obs for cell type annotations.
        cell_type_filter: Optional list of cell type values to keep.
            Only used if cell_type_col is provided.
        layer: Which count layer to use — 'raw' (from adata.raw.X),
            'X' (from adata.X), or a named layer. Default: 'raw'.
        log1p_transform: If True, reverse log1p transformation on
            the counts (i.e. round(expm1(x))). Default: True.
    """
    import h5py
    import scipy.sparse as sp

    h5 = h5py.File(h5ad_path, "r")

    # --- Read counts ---
    if layer == "raw" and "raw" in h5:
        x_grp = h5["raw"]["X"]
        if isinstance(x_grp, h5py.Dataset):
            # Dense
            data = x_grp[:]
            if log1p_transform:
                data = np.round(np.expm1(data)).astype(np.float64)
            counts_csr = None
            counts_dense = data
        else:
            # Sparse
            raw_data = h5["raw"]["X"]["data"][:]
            if log1p_transform:
                raw_data = np.round(np.expm1(raw_data)).astype(np.float64)
            else:
                raw_data = raw_data.astype(np.float64)
            indices = h5["raw"]["X"]["indices"][:]
            indptr = h5["raw"]["X"]["indptr"][:]
            n_obs = len(indptr) - 1
            # Gene names from raw
            var_key = "index" if "index" in h5["raw"]["var"] else "_index"
            gene_names = np.array([g.decode() if isinstance(g, bytes) else g
                                   for g in h5["raw"]["var"][var_key][:]])
            n_var = len(gene_names)
            counts_csr = sp.csr_matrix((raw_data, indices, indptr),
                                       shape=(n_obs, n_var))
            counts_dense = None
    else:
        x_grp = h5["X"]
        if isinstance(x_grp, h5py.Dataset):
            data = x_grp[:]
            if log1p_transform:
                data = np.round(np.expm1(data)).astype(np.float64)
            counts_csr = None
            counts_dense = data
        else:
            raw_data = h5["X"]["data"][:]
            if log1p_transform:
                raw_data = np.round(np.expm1(raw_data)).astype(np.float64)
            else:
                raw_data = raw_data.astype(np.float64)
            indices = h5["X"]["indices"][:]
            indptr = h5["X"]["indptr"][:]
            n_obs = len(indptr) - 1
            var_key = "index" if "index" in h5["var"] else "_index"
            gene_names = np.array([g.decode() if isinstance(g, bytes) else g
                                   for g in h5["var"][var_key][:]])
            n_var = len(gene_names)
            counts_csr = sp.csr_matrix((raw_data, indices, indptr),
                                       shape=(n_obs, n_var))
            counts_dense = None

    # Gene names (if not set from raw)
    if layer == "raw" and "raw" in h5:
        var_key = "index" if "index" in h5["raw"]["var"] else "_index"
        gene_names = np.array([g.decode() if isinstance(g, bytes) else g
                               for g in h5["raw"]["var"][var_key][:]])
    elif "gene_names" not in dir() or gene_names is None:
        var_key = "index" if "index" in h5["var"] else "_index"
        gene_names = np.array([g.decode() if isinstance(g, bytes) else g
                               for g in h5["var"][var_key][:]])

    # --- Read obs metadata ---
    obs_keys = [k for k in h5["obs"].keys() if k != "__categories"]
    categories = {}
    if "__categories" in h5["obs"]:
        for cat_name in h5["obs"]["__categories"]:
            raw_cats = h5["obs"]["__categories"][cat_name][:]
            categories[cat_name] = [c.decode() if isinstance(c, bytes) else c
                                    for c in raw_cats]

    def _decode_col(name):
        codes = h5["obs"][name][:]
        if name in categories:
            return np.array([categories[name][c] for c in codes])
        if codes.dtype.kind in ("S", "O"):
            return np.array([c.decode() if isinstance(c, bytes) else c
                             for c in codes])
        return codes

    sample_ids = _decode_col(sample_col)

    # --- Cell type filtering ---
    if cell_type_col is not None and cell_type_filter is not None:
        ct_vals = _decode_col(cell_type_col)
        mask = np.isin(ct_vals, cell_type_filter)
        if counts_csr is not None:
            counts_csr = counts_csr[mask, :]
        else:
            counts_dense = counts_dense[mask, :]
        sample_ids = sample_ids[mask]
        n_obs = int(mask.sum())
        ct_counts = {ct: int((ct_vals[mask] == ct).sum()) for ct in cell_type_filter}
    else:
        if counts_csr is not None:
            n_obs = counts_csr.shape[0]
        else:
            n_obs = counts_dense.shape[0]
        ct_counts = None

    h5.close()

    # --- Convert to genes × cells ---
    if counts_csr is not None:
        counts_T = counts_csr.T.toarray().astype(np.float64)
    else:
        counts_T = counts_dense.T.astype(np.float64)

    n_genes = counts_T.shape[0]
    n_samples = len(set(sample_ids))

    _sc_state["counts"] = counts_T
    _sc_state["sample"] = sample_ids
    _sc_state["gene_names"] = gene_names
    _sc_state["design"] = None
    _sc_state["fit"] = None
    _sc_state["n_cells"] = n_obs
    _sc_state["n_genes"] = n_genes
    _sc_state["n_samples"] = n_samples

    lines = [
        f"Loaded {n_genes:,} genes × {n_obs:,} cells, {n_samples} samples",
    ]
    if ct_counts is not None:
        for ct, n in ct_counts.items():
            lines.append(f"  {ct}: {n:,} cells")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SC Tool 2: set_sc_design
# ---------------------------------------------------------------------------

@mcp.tool()
def set_sc_design(
    covariates: dict,
) -> str:
    """Set the design matrix for single-cell analysis.

    Args:
        covariates: Dictionary mapping column names to per-cell values.
            Must include 'Intercept' (all ones). Values can be lists or
            arrays of length n_cells. Example:
            {"Intercept": [1,1,...], "fed": [1,0,1,...]}
    """
    n = _sc_state["n_cells"]
    if n == 0:
        raise ValueError("No SC data loaded. Use load_sc_data() first.")

    cols = list(covariates.keys())
    arrays = {}
    for col in cols:
        arr = np.asarray(covariates[col], dtype=np.float64)
        if len(arr) != n:
            raise ValueError(
                f"Column '{col}' has {len(arr)} values but expected {n} cells."
            )
        arrays[col] = arr

    design = pd.DataFrame(arrays, columns=cols)
    _sc_state["design"] = design
    _sc_state["fit"] = None

    return (
        f"Design matrix set: {n:,} cells × {len(cols)} predictors\n"
        f"Columns: {', '.join(cols)}"
    )


# ---------------------------------------------------------------------------
# SC Tool 3: fit_sc_model
# ---------------------------------------------------------------------------

@mcp.tool()
def fit_sc_model(
    norm_method: str = "TMM",
    verbose: bool = True,
) -> str:
    """Fit NEBULA-LN single-cell mixed model.

    Fits a per-gene negative binomial gamma mixed model with a
    sample-level random intercept. Requires load_sc_data() and
    set_sc_design() to have been called first.

    Args:
        norm_method: Normalization method — 'TMM' (default) or 'none'.
        verbose: Print progress messages.
    """
    counts = _sc_state["counts"]
    if counts is None:
        raise ValueError("No SC data loaded. Use load_sc_data() first.")
    design = _sc_state["design"]
    if design is None:
        raise ValueError("No design matrix set. Use set_sc_design() first.")
    sample = _sc_state["sample"]
    gene_names = _sc_state["gene_names"]

    import time
    t0 = time.perf_counter()

    fit = ep.glm_sc_fit(
        counts,
        design=design,
        sample=sample,
        norm_method=norm_method,
        verbose=verbose,
    )
    elapsed = time.perf_counter() - t0

    # Attach gene names
    gene_mask = fit["gene_mask"]
    if fit["genes"] is None and gene_names is not None:
        fit["genes"] = pd.DataFrame({"gene": gene_names[gene_mask]})

    _sc_state["fit"] = fit

    n_tested = fit["coefficients"].shape[0]
    n_conv = int((fit["convergence"] == 1).sum())

    return (
        f"Fitted NEBULA-LN: {n_tested:,} genes in {elapsed:.1f}s\n"
        f"Converged: {n_conv:,}/{n_tested:,}\n"
        f"Predictors: {', '.join(fit['predictor_names'])}"
    )


# ---------------------------------------------------------------------------
# SC Tool 4: test_sc_coef
# ---------------------------------------------------------------------------

@mcp.tool()
def test_sc_coef(
    coef: Optional[str] = None,
    coef_index: Optional[int] = None,
    name: Optional[str] = None,
) -> str:
    """Run a Wald test on a coefficient from the single-cell model.

    Args:
        coef: Coefficient name to test (e.g. 'fed'). Default: last coefficient.
        coef_index: 0-based coefficient index. Alternative to coef.
        name: Label for this result. Default: auto-generated.
    """
    fit = _sc_state["fit"]
    if fit is None:
        raise ValueError("No SC model fitted. Use fit_sc_model() first.")

    if coef is not None:
        c = coef
    elif coef_index is not None:
        c = coef_index
    else:
        c = None  # top_tags defaults to last coefficient

    tt = ep.top_tags(fit, n=fit["coefficients"].shape[0], coef=c)
    table = tt["table"]

    fdr = table["FDR"].values
    logfc = table["logFC"].values
    valid = ~np.isnan(fdr)

    n_up = int(((fdr < 0.05) & (logfc > 0) & valid).sum())
    n_down = int(((fdr < 0.05) & (logfc < 0) & valid).sum())
    n_sig_01 = int(((fdr < 0.1) & valid).sum())

    if name is None:
        name = coef if coef is not None else f"coef{coef_index}"
    _state["results"][name] = {"table": table, "method": "nebula_ln"}
    _state["last_result"] = name

    label = coef if coef is not None else f"coefficient {coef_index}"
    lines = [
        f"Wald test: {label}",
        f"DE genes (FDR < 0.05): {n_up} up, {n_down} down ({n_up + n_down} total)",
        f"DE genes (FDR < 0.10): {n_sig_01}",
        "",
        "Top 10 genes:",
        f"{'Gene':<30} {'logFC':>8} {'SE':>8} {'PValue':>10} {'FDR':>10}",
        "-" * 70,
    ]
    for _, row in table.head(10).iterrows():
        gene = str(row.get("gene", row.name))
        if len(gene) > 29:
            gene = gene[:27] + ".."
        lines.append(
            f"{gene:<30} {row['logFC']:>8.3f} {row['SE']:>8.3f} "
            f"{row['PValue']:>10.2e} {row['FDR']:>10.2e}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SC Tool 5: get_sc_top_genes
# ---------------------------------------------------------------------------

@mcp.tool()
def get_sc_top_genes(
    coef: Optional[str] = None,
    coef_index: Optional[int] = None,
    n: int = 20,
    fdr_threshold: float = 0.05,
) -> str:
    """Get top DE genes from the single-cell model.

    Args:
        coef: Coefficient name to test (e.g. 'fed'). Default: last coefficient.
        coef_index: 0-based coefficient index. Alternative to coef.
        n: Number of top genes to return. Default: 20.
        fdr_threshold: FDR cutoff for marking significance. Default: 0.05.
    """
    fit = _sc_state["fit"]
    if fit is None:
        raise ValueError("No SC model fitted. Use fit_sc_model() first.")

    c = coef if coef is not None else coef_index

    tt = ep.top_tags(fit, n=n, coef=c)
    table = tt["table"]

    label = coef if coef is not None else (
        f"coefficient {coef_index}" if coef_index is not None else "last coefficient"
    )
    lines = [
        f"Top {min(n, len(table))} genes for {label}:",
        "",
        f"{'Gene':<30} {'logFC':>8} {'SE':>8} {'z':>8} {'PValue':>10} {'FDR':>10} {'Sig':>3}",
        "-" * 82,
    ]
    for _, row in table.iterrows():
        gene = str(row.get("gene", row.name))
        if len(gene) > 29:
            gene = gene[:27] + ".."
        fdr_val = row["FDR"]
        sig = "***" if fdr_val < 0.001 else "**" if fdr_val < 0.01 else \
              "*" if fdr_val < fdr_threshold else ""
        lines.append(
            f"{gene:<30} {row['logFC']:>8.3f} {row['SE']:>8.3f} "
            f"{row['z']:>8.2f} {row['PValue']:>10.2e} {fdr_val:>10.2e} {sig:>3}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SC Tool 6: describe_sc
# ---------------------------------------------------------------------------

@mcp.tool()
def describe_sc() -> str:
    """Report the current state of the single-cell analysis pipeline."""
    lines = []
    if _sc_state["counts"] is None:
        return "No single-cell data loaded. Use load_sc_data() first."

    lines.append(
        f"SC data: {_sc_state['n_genes']:,} genes × "
        f"{_sc_state['n_cells']:,} cells, "
        f"{_sc_state['n_samples']} samples"
    )

    if _sc_state["design"] is not None:
        cols = list(_sc_state["design"].columns)
        lines.append(f"Design: {', '.join(cols)}")
    else:
        lines.append("Design: not set")

    fit = _sc_state["fit"]
    if fit is not None:
        n_tested = fit["coefficients"].shape[0]
        n_conv = int((fit["convergence"] == 1).sum())
        lines.append(
            f"Model: NEBULA-LN, {n_tested:,} genes tested, "
            f"{n_conv:,} converged"
        )
        lines.append(f"Predictors: {', '.join(fit['predictor_names'])}")
    else:
        lines.append("Model: not fitted")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SC Tool 7: save_sc_results
# ---------------------------------------------------------------------------

@mcp.tool()
def save_sc_results(
    output_path: str,
    coef: Optional[str] = None,
    coef_index: Optional[int] = None,
    format: str = "csv",
) -> str:
    """Save full single-cell DE results to disk.

    Args:
        output_path: Destination file path.
        coef: Coefficient name to test and save.
        coef_index: 0-based coefficient index. Alternative to coef.
        format: 'csv' (default), 'tsv', or 'json'.
    """
    fit = _sc_state["fit"]
    if fit is None:
        raise ValueError("No SC model fitted. Use fit_sc_model() first.")

    c = coef if coef is not None else coef_index
    tt = ep.top_tags(fit, n=fit["coefficients"].shape[0], coef=c)
    table = tt["table"]

    if format == "json":
        table.to_json(output_path, orient="records")
    elif format == "tsv":
        table.to_csv(output_path, sep="\t")
    else:
        table.to_csv(output_path)

    valid_fdr = table["FDR"].dropna()
    n_sig = int((valid_fdr < 0.05).sum())

    return (
        f"Saved {len(table):,} genes to {output_path}\n"
        f"DE at FDR < 0.05: {n_sig}"
    )


# ---------------------------------------------------------------------------
# SC Tool 8: reset_sc_state
# ---------------------------------------------------------------------------

@mcp.tool()
def reset_sc_state() -> str:
    """Reset all single-cell pipeline state."""
    _sc_state["counts"] = None
    _sc_state["design"] = None
    _sc_state["sample"] = None
    _sc_state["gene_names"] = None
    _sc_state["fit"] = None
    _sc_state["n_cells"] = 0
    _sc_state["n_genes"] = 0
    _sc_state["n_samples"] = 0
    return "Single-cell state cleared."


# ===========================================================================
# Enrichment analysis helpers
# ===========================================================================


def _extract_gene_list_from_de(result_name=None, fdr_threshold=0.05,
                                logfc_threshold=0.0, direction="all"):
    """Extract filtered gene list from DE results.

    Returns dict with keys: 'all', 'up', 'down' (each a list of gene IDs).
    """
    if result_name is None:
        result_name = _state.get("last_result")
    if result_name is None or result_name not in _state.get("results", {}):
        return None, "No DE result available. Run test_contrast() first."

    res = _state["results"][result_name]
    tt = ep.top_tags(res, n=res["table"].shape[0])
    table = tt["table"]

    # Filter by FDR
    if "FDR" in table.columns:
        mask = table["FDR"] < fdr_threshold
    elif "PValue" in table.columns:
        mask = table["PValue"] < fdr_threshold
    else:
        mask = pd.Series(True, index=table.index)

    # Filter by |logFC|
    if logfc_threshold > 0 and "logFC" in table.columns:
        mask = mask & (table["logFC"].abs() >= logfc_threshold)

    sig = table[mask]

    # Split by direction
    if "logFC" in sig.columns:
        up = list(sig[sig["logFC"] > 0].index)
        down = list(sig[sig["logFC"] < 0].index)
    else:
        up = list(sig.index)
        down = []

    all_genes = list(sig.index)

    result = {"all": all_genes, "up": up, "down": down}

    if direction == "up":
        genes = up
    elif direction == "down":
        genes = down
    else:
        genes = all_genes

    return genes, result


@mcp.tool()
def filter_de_genes(
    result_name: Optional[str] = None,
    fdr_threshold: float = 0.05,
    logfc_threshold: float = 0.0,
    direction: str = "all",
) -> str:
    """Filter DE results to extract gene lists for enrichment analysis.

    Args:
        result_name: Name of the DE result to filter. Default: most recent.
        fdr_threshold: FDR cutoff for significance. Default: 0.05.
        logfc_threshold: Minimum absolute log2 fold-change. Default: 0 (no filter).
        direction: 'all', 'up', or 'down'. Default: 'all'.

    Returns:
        Summary of filtered gene counts and first 20 gene names.
    """
    genes, result = _extract_gene_list_from_de(
        result_name, fdr_threshold, logfc_threshold, direction
    )
    if genes is None:
        return result  # Error message

    _state["_filtered_genes"] = result

    rname = result_name or _state.get("last_result", "?")
    lines = [f"Filtered DE genes from '{rname}' (FDR < {fdr_threshold}"
             f"{f', |logFC| >= {logfc_threshold}' if logfc_threshold > 0 else ''}):",
             f"  Up-regulated: {len(result['up'])} genes",
             f"  Down-regulated: {len(result['down'])} genes",
             f"  Total significant: {len(result['all'])} genes",
             "",
             f"Direction selected: {direction} ({len(genes)} genes)"]
    if genes:
        preview = genes[:20]
        lines.append(f"First genes: {', '.join(str(g) for g in preview)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
