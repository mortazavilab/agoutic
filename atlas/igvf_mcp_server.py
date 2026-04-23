"""
IGVF Portal MCP Server

Standalone FastMCP server providing tools to search and browse
the IGVF Data Portal (https://data.igvf.org).

Tools cover MeasurementSets, AnalysisSets, PredictionSets,
Files, Genes, and Samples.
"""

from typing import Optional

import httpx
from fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

from atlas.igvf_client import IGVFClient
from atlas.igvf_tool_schemas import TOOL_SCHEMAS as _IGVF_TOOL_SCHEMAS


# --- Singleton client & server instances ---
_client = IGVFClient()
server = FastMCP("AGOUTIC-IGVF")


# --- Schema endpoint (fetched by Cortex at startup) ---
async def _tools_schema_endpoint(request):
    """Return machine-readable tool schemas for all IGVF MCP tools."""
    return JSONResponse(_IGVF_TOOL_SCHEMAS)


# =============================================================================
# Helper: compact dataset record for search results
# =============================================================================

def _compact_dataset(obj: dict) -> dict:
    """Extract key fields from a dataset object for search result display."""
    samples = obj.get("samples", [])
    sample_summaries = []
    for s in (samples if isinstance(samples, list) else []):
        if isinstance(s, dict):
            sample_summaries.append(s.get("summary", s.get("@id", "")))
        else:
            sample_summaries.append(str(s))

    # Search results use preferred_assay_titles (list); full objects may have assay_term
    assay_titles = obj.get("preferred_assay_titles", obj.get("assay_titles", []))
    if isinstance(assay_titles, list):
        assay_name = ", ".join(assay_titles) if assay_titles else ""
    else:
        assay_name = str(assay_titles)
    # Fallback to assay_term for full object responses
    if not assay_name:
        assay_term = obj.get("assay_term", {})
        assay_name = assay_term.get("term_name", "") if isinstance(assay_term, dict) else str(assay_term)

    return {
        "accession": obj.get("accession", ""),
        "assay": assay_name,
        "summary": obj.get("summary", ""),
        "status": obj.get("status", ""),
        "lab": obj.get("lab", {}).get("title", "") if isinstance(obj.get("lab"), dict) else str(obj.get("lab", "")),
        "samples": sample_summaries,
        "link": f"https://data.igvf.org{obj.get('@id', '')}",
    }


def _compact_file(obj: dict) -> dict:
    """Extract key fields from a file object."""
    # Search results use file_set; full objects may have dataset
    file_set = obj.get("file_set", obj.get("dataset", ""))
    if isinstance(file_set, dict):
        file_set_id = file_set.get("accession", file_set.get("@id", ""))
    else:
        file_set_id = str(file_set) if file_set else ""

    return {
        "accession": obj.get("accession", ""),
        "file_format": obj.get("file_format", ""),
        "content_type": obj.get("content_type", ""),
        "summary": obj.get("summary", ""),
        "file_size": obj.get("file_size", 0),
        "status": obj.get("status", ""),
        "file_set": file_set_id,
        "href": obj.get("href", ""),
        "link": f"https://data.igvf.org{obj.get('@id', '')}",
    }


# =============================================================================
# Search Tools
# =============================================================================

@server.tool()
def search_measurement_sets(
    status: Optional[str] = "released",
    assay: Optional[str] = None,
    sample: Optional[str] = None,
    organism: Optional[str] = None,
    lab: Optional[str] = None,
    limit: int = 25,
) -> dict:
    """
    Search for IGVF MeasurementSets (primary experimental datasets).

    MeasurementSets are the IGVF equivalent of ENCODE Experiments — they
    represent assay data produced from biological samples.

    Args:
        status: Filter by status ('released', 'in progress', etc.). Default 'released'.
        assay: Assay term name filter (e.g. 'ATAC-seq', 'whole genome sequencing').
        sample: Sample summary or term to filter by.
        organism: Organism/taxa filter (e.g. 'Homo sapiens', 'Mus musculus').
        lab: Lab name or title filter.
        limit: Max results (default 25, max 500).
    """
    filters: dict[str, str] = {}
    if status:
        filters["status"] = status
    if assay:
        filters["preferred_assay_titles"] = assay
    if sample:
        filters["samples.sample_terms.term_name"] = sample
    if organism:
        filters["donors.taxa"] = organism
    if lab:
        filters["lab.title"] = lab

    resp = _client.search("MeasurementSet", limit=min(limit, 500), field_filters=filters)
    results = [_compact_dataset(obj) for obj in resp.get("@graph", [])]
    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "results": results,
    }


@server.tool()
def search_analysis_sets(
    status: Optional[str] = "released",
    sample: Optional[str] = None,
    organism: Optional[str] = None,
    limit: int = 25,
) -> dict:
    """
    Search for IGVF AnalysisSets (computational analysis results).

    AnalysisSets contain processed/analyzed data derived from MeasurementSets.

    Args:
        status: Filter by status. Default 'released'.
        sample: Sample term name filter.
        organism: Organism/taxa filter.
        limit: Max results (default 25).
    """
    filters: dict[str, str] = {}
    if status:
        filters["status"] = status
    if sample:
        filters["samples.sample_terms.term_name"] = sample
    if organism:
        filters["donors.taxa"] = organism

    resp = _client.search("AnalysisSet", limit=min(limit, 500), field_filters=filters)
    results = [_compact_dataset(obj) for obj in resp.get("@graph", [])]
    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "results": results,
    }


@server.tool()
def search_prediction_sets(
    status: Optional[str] = "released",
    sample: Optional[str] = None,
    organism: Optional[str] = None,
    limit: int = 25,
) -> dict:
    """
    Search for IGVF PredictionSets (computational predictions).

    PredictionSets contain model predictions (e.g. variant effect predictions,
    regulatory element predictions).

    Args:
        status: Filter by status. Default 'released'.
        sample: Sample term name filter.
        organism: Organism/taxa filter.
        limit: Max results (default 25).
    """
    filters: dict[str, str] = {}
    if status:
        filters["status"] = status
    if sample:
        filters["samples.sample_terms.term_name"] = sample
    if organism:
        filters["donors.taxa"] = organism

    resp = _client.search("PredictionSet", limit=min(limit, 500), field_filters=filters)
    results = [_compact_dataset(obj) for obj in resp.get("@graph", [])]
    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "results": results,
    }


@server.tool()
def search_by_sample(
    sample_term: str,
    organism: Optional[str] = None,
    assay: Optional[str] = None,
    status: Optional[str] = "released",
    limit: int = 25,
) -> dict:
    """
    Search for datasets by sample/biosample (cell line, tissue, etc.).

    Searches across MeasurementSets for the given sample term.

    Args:
        sample_term: Sample name or summary (e.g. 'K562', 'liver', 'iPSC').
        organism: Organism filter (e.g. 'Homo sapiens').
        assay: Optional assay filter.
        status: Status filter. Default 'released'.
        limit: Max results (default 25).
    """
    filters: dict[str, str] = {}
    filters["samples.sample_terms.term_name"] = sample_term
    if status:
        filters["status"] = status
    if organism:
        filters["donors.taxa"] = organism
    if assay:
        filters["preferred_assay_titles"] = assay

    resp = _client.search("MeasurementSet", limit=min(limit, 500), field_filters=filters)
    results = [_compact_dataset(obj) for obj in resp.get("@graph", [])]
    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "sample_term": sample_term,
        "results": results,
    }


@server.tool()
def search_by_assay(
    assay_title: str,
    organism: Optional[str] = None,
    status: Optional[str] = "released",
    limit: int = 25,
) -> dict:
    """
    Search for datasets by assay type.

    Use when the user asks about a specific assay across all samples.

    Args:
        assay_title: Assay type (e.g. 'ATAC-seq', 'whole genome sequencing',
                     'CRISPR screen', 'Hi-C', 'RNA-seq').
        organism: Optional organism filter.
        status: Status filter. Default 'released'.
        limit: Max results (default 25).
    """
    filters: dict[str, str] = {}
    filters["preferred_assay_titles"] = assay_title
    if status:
        filters["status"] = status
    if organism:
        filters["donors.taxa"] = organism

    resp = _client.search("MeasurementSet", limit=min(limit, 500), field_filters=filters)
    results = [_compact_dataset(obj) for obj in resp.get("@graph", [])]
    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "assay_title": assay_title,
        "results": results,
    }


@server.tool()
def search_files(
    file_format: Optional[str] = None,
    content_type: Optional[str] = None,
    dataset_accession: Optional[str] = None,
    status: Optional[str] = "released",
    limit: int = 25,
) -> dict:
    """
    Search for files in the IGVF portal.

    Args:
        file_format: File format filter (e.g. 'fastq', 'bam', 'bed', 'tsv', 'vcf').
        content_type: Content type filter (e.g. 'reads', 'alignments',
                      'variant calls', 'signal').
        dataset_accession: Filter files belonging to a specific dataset accession.
        status: Status filter. Default 'released'.
        limit: Max results (default 25).
    """
    # Search across all File types
    filters: dict[str, str] = {}
    if status:
        filters["status"] = status
    if file_format:
        filters["file_format"] = file_format
    if content_type:
        filters["content_type"] = content_type
    if dataset_accession:
        filters["file_set.accession"] = dataset_accession

    resp = _client.search("File", limit=min(limit, 500), field_filters=filters)
    results = [_compact_file(obj) for obj in resp.get("@graph", [])]
    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "results": results,
    }


# =============================================================================
# Object Retrieval Tools
# =============================================================================

@server.tool()
def get_dataset(accession: str) -> dict:
    """
    Get full metadata for an IGVF dataset by accession.

    Works for MeasurementSets, AnalysisSets, PredictionSets, and other
    dataset types. Use the IGVFDS-prefixed accession.

    NEVER pass IGVFFI file accessions here — use get_file_metadata instead.

    Args:
        accession: Dataset accession (e.g. 'IGVFDS1234ABCD').
    """
    obj = _client.get_object(accession)
    return _compact_dataset(obj) | {
        "raw": {
            k: v for k, v in obj.items()
            if not k.startswith("@") and k not in ("audit", "actions")
        },
    }


@server.tool()
def get_file_metadata(file_accession: str) -> dict:
    """
    Get full metadata for a specific IGVF file.

    Args:
        file_accession: File accession (IGVFFI format, e.g. 'IGVFFI1234ABCD').
    """
    obj = _client.get_object(file_accession)
    return _compact_file(obj) | {
        "raw": {
            k: v for k, v in obj.items()
            if not k.startswith("@") and k not in ("audit", "actions")
        },
    }


@server.tool()
def get_file_download_url(file_accession: str) -> dict:
    """
    Get the download URL for an IGVF file.

    Args:
        file_accession: File accession (IGVFFI format).
    """
    url = _client.get_file_download_url(file_accession)
    # Fetch basic file info for context
    try:
        obj = _client.get_object(file_accession)
        return {
            "file_accession": file_accession,
            "download_url": url,
            "file_format": obj.get("file_format", ""),
            "content_type": obj.get("content_type", ""),
            "file_size": obj.get("file_size", 0),
        }
    except Exception:
        return {"file_accession": file_accession, "download_url": url}


@server.tool()
def get_files_for_dataset(
    accession: str,
    file_format: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """
    Get all files associated with a dataset.

    Args:
        accession: Dataset accession (IGVFDS format).
        file_format: Optional file format filter (e.g. 'fastq', 'bam').
        limit: Max files to return (default 100).
    """
    filters: dict[str, str] = {"status": "released"}
    if file_format:
        filters["file_format"] = file_format

    # Try multiple dataset type paths
    results: list[dict] = []
    filters["file_set.accession"] = accession
    resp = _client.search("File", limit=limit, field_filters=filters)
    graph = resp.get("@graph", [])
    results.extend(_compact_file(obj) for obj in graph)

    # Group by file format
    by_format: dict[str, list[dict]] = {}
    for f in results:
        fmt = f.get("file_format", "unknown")
        by_format.setdefault(fmt, []).append(f)

    return {
        "accession": accession,
        "total_files": len(results),
        "by_format": {fmt: len(files) for fmt, files in by_format.items()},
        "files": results,
    }


# =============================================================================
# Gene Tools
# =============================================================================

@server.tool()
def search_genes(
    query: str,
    organism: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """
    Search for genes in the IGVF portal.

    Args:
        query: Gene symbol, name, or ID to search for (e.g. 'TP53', 'BRCA1').
        organism: Optional organism filter (e.g. 'Homo sapiens').
        limit: Max results (default 10).
    """
    filters: dict[str, str] = {"query": query}
    if organism:
        filters["taxa"] = organism

    resp = _client.search("Gene", limit=limit, field_filters=filters)
    results = []
    for obj in resp.get("@graph", []):
        results.append({
            "gene_id": obj.get("geneid", ""),
            "symbol": obj.get("symbol", ""),
            "title": obj.get("title", ""),
            "synonyms": obj.get("synonyms", []),
            "dbxrefs": obj.get("dbxrefs", []),
            "taxa": obj.get("taxa", ""),
            "link": f"https://data.igvf.org{obj.get('@id', '')}",
        })

    # Promote exact symbol match to the front (the API ranks partial matches
    # above exact ones in some cases, e.g. "BRCA1" returns BRCA1P1 first).
    query_upper = query.upper()
    results.sort(key=lambda r: (0 if r["symbol"].upper() == query_upper else 1))

    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "results": results,
    }


@server.tool()
def get_gene(gene_id: str) -> dict:
    """
    Get detailed information for a specific gene.

    Args:
        gene_id: Gene identifier — can be an IGVF @id path, Entrez gene ID,
                 or gene symbol.
    """
    # Try direct lookup first
    try:
        obj = _client.get_object(f"/genes/{gene_id}/")
    except httpx.HTTPStatusError:
        # Fallback: try exact symbol match, then free-text query
        resp = _client.search("Gene", limit=1, field_filters={"symbol": gene_id})
        graph = resp.get("@graph", [])
        if not graph:
            resp = _client.search("Gene", limit=1, field_filters={"query": gene_id})
            graph = resp.get("@graph", [])
        if not graph:
            return {"error": f"Gene not found: {gene_id}"}
        # Search results are sparse; fetch full object via @id for all fields
        obj_id = graph[0].get("@id", "")
        if obj_id:
            try:
                obj = _client.get_object(obj_id)
            except httpx.HTTPStatusError:
                obj = graph[0]
        else:
            obj = graph[0]

    return {
        "gene_id": obj.get("geneid", ""),
        "symbol": obj.get("symbol", ""),
        "title": obj.get("title", ""),
        "synonyms": obj.get("synonyms", []),
        "dbxrefs": obj.get("dbxrefs", []),
        "taxa": obj.get("taxa", ""),
        "locations": obj.get("locations", []),
        "link": f"https://data.igvf.org{obj.get('@id', '')}",
    }


# =============================================================================
# Sample Tools
# =============================================================================

@server.tool()
def search_samples(
    sample_type: Optional[str] = None,
    organism: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 25,
) -> dict:
    """
    Search for samples (biosamples) in the IGVF portal.

    Args:
        sample_type: Sample type to search (e.g. 'InVitroSystem', 'PrimaryCell',
                     'Tissue', 'WholeOrganism', 'TechnicalSample').
                     If omitted, searches all sample types.
        organism: Organism filter (e.g. 'Homo sapiens').
        query: Text query for sample summary.
        limit: Max results (default 25).
    """
    obj_type = sample_type or "Sample"
    filters: dict[str, str] = {}
    if organism:
        filters["taxa"] = organism
    if query:
        filters["query"] = query

    resp = _client.search(obj_type, limit=min(limit, 500), field_filters=filters)
    results = []
    for obj in resp.get("@graph", []):
        results.append({
            "accession": obj.get("accession", ""),
            "summary": obj.get("summary", ""),
            "sample_type": obj.get("@type", [""])[0] if obj.get("@type") else "",
            "taxa": obj.get("taxa", ""),
            "status": obj.get("status", ""),
            "link": f"https://data.igvf.org{obj.get('@id', '')}",
        })
    return {
        "total": resp.get("total", len(results)),
        "count": len(results),
        "results": results,
    }


# =============================================================================
# Utility Tools
# =============================================================================

@server.tool()
def get_server_info() -> dict:
    """Get IGVF MCP server info and portal status."""
    return {
        "server": "AGOUTIC-IGVF",
        "portal": "https://data.igvf.org",
        "description": "Impact of Genomic Variation on Function (IGVF) Data Portal",
        "tools": list(_IGVF_TOOL_SCHEMAS.keys()),
        "cache_size": len(_client._cache),
    }
