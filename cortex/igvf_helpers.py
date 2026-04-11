"""
IGVF-specific helper functions for parameter repair.

Parallel to cortex/encode_helpers.py — last-chance parameter sanitisation
before MCP calls for IGVF tools.
"""

import re

from common.logging_config import get_logger

logger = get_logger(__name__)

# Patterns to extract a sample/biosample term from natural language queries
_IGVF_SAMPLE_PATTERNS = [
    # "search IGVF for K562 data"
    re.compile(r'search\s+igvf\s+for\s+(.+?)(?:\s+data|\s+datasets?|\s+experiments?|\s*$)', re.IGNORECASE),
    # "search for K562 in IGVF"
    re.compile(r'search\s+for\s+(.+?)\s+(?:in|on|from)\s+igvf', re.IGNORECASE),
    # "IGVF K562 datasets" / "IGVF K562 data"
    re.compile(r'igvf\s+(.+?)\s+(?:data|datasets?|experiments?|results?)', re.IGNORECASE),
    # "what datasets are available for K562"
    re.compile(r'(?:what|which|show|find|get)\s+.*?(?:for|about|with)\s+(.+?)(?:\s+in\s+igvf|\s*\??\s*$)', re.IGNORECASE),
    # "K562 data in IGVF"
    re.compile(r'(.+?)\s+(?:data|datasets?|experiments?)\s+(?:in|on|from)\s+igvf', re.IGNORECASE),
    # "does IGVF have K562"
    re.compile(r'does\s+igvf\s+have\s+(.+?)(?:\s+data|\s+datasets?|\s*\??\s*$)', re.IGNORECASE),
]

# Known assay names for IGVF — used to distinguish sample vs assay queries
_IGVF_ASSAY_KEYWORDS = frozenset([
    "atac-seq", "atacseq", "atac seq", "rna-seq", "rnaseq", "rna seq",
    "chip-seq", "chipseq", "chip seq", "hi-c", "hic",
    "whole genome sequencing", "wgs", "crispr screen", "crispr",
    "starr-seq", "starrseq", "mpra", "perturb-seq", "perturbseq",
    "dnase-seq", "dnaseseq", "mint-chip", "parse split-seq",
])

# Stop words to strip when falling back to extracting content tokens
_IGVF_STOP_WORDS = frozenset([
    "search", "igvf", "for", "in", "the", "a", "an", "of", "data",
    "datasets", "dataset", "experiments", "experiment", "what", "which",
    "show", "find", "get", "me", "all", "available", "any", "are",
    "is", "does", "have", "how", "many", "about", "from", "on", "with",
    "please", "can", "you", "i", "want", "to", "see", "look", "up",
    "results", "result",
])


def _extract_igvf_search_term(user_message: str) -> str | None:
    """Extract the most likely sample/search term from an IGVF query."""
    msg = user_message.strip()

    for pattern in _IGVF_SAMPLE_PATTERNS:
        m = pattern.search(msg)
        if m:
            term = m.group(1).strip().rstrip("?.,")
            if term and term.lower() not in _IGVF_STOP_WORDS:
                return term

    # Fallback: strip stop words and return remaining content tokens
    tokens = re.findall(r'[A-Za-z0-9][\w.\-]*', msg)
    content = [t for t in tokens if t.lower() not in _IGVF_STOP_WORDS]
    if content:
        return " ".join(content)

    return None


def _validate_igvf_params(tool: str, params: dict, user_message: str) -> dict:
    """
    Last-chance fix for IGVF tool params before the MCP call.

    Catches:
    - search_by_sample called without sample_term (required)
    - search_by_assay called without assay_title (required)
    """
    params = dict(params)  # shallow copy

    # --- Fix 1: missing sample_term in search_by_sample ---
    if tool == "search_by_sample" and "sample_term" not in params:
        # Try to salvage from other params that might contain the sample name
        for candidate_key in ("sample_name", "sample", "assay", "status"):
            val = params.get(candidate_key, "")
            if val and val.lower() not in _IGVF_ASSAY_KEYWORDS and candidate_key not in ("organism", "status"):
                params["sample_term"] = val
                del params[candidate_key]
                logger.warning("Moved %s to sample_term (was sample, not %s)",
                               candidate_key, candidate_key,
                               sample_term=val)
                break
        else:
            # Extract from user message
            extracted = _extract_igvf_search_term(user_message)
            if extracted:
                params["sample_term"] = extracted
                logger.warning("Injected missing sample_term from user message",
                               sample_term=extracted)

    # --- Fix 2: missing assay_title in search_by_assay ---
    if tool == "search_by_assay" and "assay_title" not in params:
        # Try to extract assay from user message
        msg_lower = user_message.lower()
        for assay_kw in _IGVF_ASSAY_KEYWORDS:
            if assay_kw in msg_lower:
                params["assay_title"] = assay_kw
                logger.warning("Injected missing assay_title from user message",
                               assay_title=assay_kw)
                break

    return params
