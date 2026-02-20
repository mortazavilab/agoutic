"""
Server2 extended ENCODE MCP server.

Extends ENCODELIB's FastMCP server with additional tools without
modifying ENCODELIB itself.

This module must be imported AFTER ENCODELIB has been added to sys.path
(done by launch_encode.py before importing this module).
"""

from typing import Optional

# encode_server is in ENCODELIB — importable after launch_encode.py
# adds that directory to sys.path.
from encode_server import server, get_encode_instance  # noqa: E402


@server.tool()
def search_by_assay(
    assay_title: str,
    organism: Optional[str] = None,
    target: Optional[str] = None,
    exclude_revoked: bool = True,
) -> dict:
    """
    Search for experiments by assay type across all organisms (or a specific one).

    Use this when the user asks "how many RNA-seq experiments are in ENCODE" or
    "show me all ATAC-seq experiments" — i.e. assay-first queries with no
    specific cell line or tissue context.

    When organism is omitted, results are returned for both Homo sapiens and
    Mus musculus and combined into a single response with per-organism counts.

    Args:
        assay_title: Assay type to search for (e.g. 'total RNA-seq', 'ATAC-seq',
                     'TF ChIP-seq', 'long read RNA-seq').
        organism: Optional. 'Homo sapiens' or 'Mus musculus'. If omitted, both
                  are queried and results are merged.
        target: Optional filter by target name.
        exclude_revoked: Whether to exclude revoked experiments (default True).

    Returns:
        Dict with keys 'total', 'human', 'mouse' (or single organism), each
        containing experiment list and count.
    """
    encode = get_encode_instance()

    def _fetch(org: str) -> list:
        results = encode.search_experiments_by_organism(
            org,
            assay_title=assay_title,
            target=target,
            exclude_revoked=exclude_revoked,
            return_objects=True,
        )
        return [
            {
                "accession": exp.accession,
                "organism": exp.organism,
                "assay": exp.assay,
                "biosample": exp.biosample,
                "lab": exp.lab,
                "status": exp.status,
                "targets": exp.targets,
                "replicate_count": exp.replicate_count,
                "description": exp.description,
                "link": exp.link,
            }
            for exp in results
        ]

    if organism:
        rows = _fetch(organism)
        return {
            "total": len(rows),
            "assay_title": assay_title,
            "organism": organism,
            "experiments": rows,
        }

    human = _fetch("Homo sapiens")
    mouse = _fetch("Mus musculus")
    return {
        "assay_title": assay_title,
        "total": len(human) + len(mouse),
        "human_count": len(human),
        "mouse_count": len(mouse),
        "human": human,
        "mouse": mouse,
    }
