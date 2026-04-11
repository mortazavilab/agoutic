"""
IGVF-specific helper functions for parameter repair and tool routing.

Parallel to cortex/encode_helpers.py — last-chance parameter sanitisation
and tool rerouting before MCP calls for IGVF tools.
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

# Organism name normalisation (underscores, capitalisation)
_ORGANISM_NORMALISE: dict[str, str] = {
    "mus_musculus": "Mus musculus",
    "mus musculus": "Mus musculus",
    "mouse": "Mus musculus",
    "homo_sapiens": "Homo sapiens",
    "homo sapiens": "Homo sapiens",
    "human": "Homo sapiens",
}

# Object-type reroute map for search_measurement_sets misuse
_OBJECT_TYPE_REROUTE: dict[str, str] = {
    "analysisset": "search_analysis_sets",
    "analysis_set": "search_analysis_sets",
    "analysis set": "search_analysis_sets",
    "predictionset": "search_prediction_sets",
    "prediction_set": "search_prediction_sets",
    "prediction set": "search_prediction_sets",
}

# Params that signal biosample intent when found on search_measurement_sets
_BIOSAMPLE_SIGNAL_PARAMS = frozenset([
    "biosample_type", "biosample_ontology", "biosample", "cell_line",
    "tissue", "cell_type",
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


def _normalise_organism(value: str) -> str:
    """Normalise organism strings (Mus_musculus → Mus musculus, etc.)."""
    return _ORGANISM_NORMALISE.get(value.lower().strip(), value)


def _correct_igvf_tool_routing(
    tool: str, params: dict, user_message: str,
) -> tuple[str, dict]:
    """
    Fix cases where the LLM picks the wrong IGVF tool.

    Common mistakes:
    - search_measurement_sets + file_format param → should be search_files
    - search_measurement_sets + object_type=AnalysisSet → search_analysis_sets
    - search_measurement_sets + object_type=PredictionSet → search_prediction_sets
    - search_measurement_sets + biosample_type/biosample_ontology → search_by_sample
    """
    params = dict(params)

    if tool == "search_measurement_sets":
        # ── Redirect to search_files when file_format is present ──
        file_format = params.get("file_format")
        if file_format:
            new_params: dict = {"file_format": file_format}
            for k in ("content_type", "dataset_accession", "status", "limit"):
                if k in params:
                    new_params[k] = params[k]
            logger.warning(
                "Rerouted search_measurement_sets → search_files (file_format present)",
                original_params=params, new_params=new_params,
            )
            return "search_files", new_params

        # ── Redirect by object_type to specialised search tools ──
        obj_type = params.get("object_type", "")
        target_tool = _OBJECT_TYPE_REROUTE.get(obj_type.lower().strip())
        if target_tool:
            kept = {k: v for k, v in params.items() if k != "object_type"}
            logger.warning(
                "Rerouted search_measurement_sets → %s (object_type=%s)",
                target_tool, obj_type,
                original_params=params, new_params=kept,
            )
            return target_tool, kept

        # ── Redirect to search_by_sample when biosample params present ──
        biosample_keys = params.keys() & _BIOSAMPLE_SIGNAL_PARAMS
        if biosample_keys:
            sample_term = None
            for bk in ("biosample", "cell_line", "tissue", "cell_type",
                        "biosample_type", "biosample_ontology"):
                val = params.get(bk, "")
                if val:
                    sample_term = val
                    break
            if not sample_term:
                sample_term = _extract_igvf_search_term(user_message)
            new_params = {}
            if sample_term:
                new_params["sample_term"] = sample_term
            for k in ("organism", "assay", "status", "limit"):
                if k in params:
                    new_params[k] = params[k]
            logger.warning(
                "Rerouted search_measurement_sets → search_by_sample "
                "(biosample params: %s)", biosample_keys,
                original_params=params, new_params=new_params,
            )
            return "search_by_sample", new_params

    return tool, params


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

    # --- Fix 3: organism normalisation (Mus_musculus → Mus musculus, etc.) ---
    if "organism" in params:
        normalised = _normalise_organism(params["organism"])
        if normalised != params["organism"]:
            logger.warning("Normalised organism %r → %r",
                           params["organism"], normalised)
            params["organism"] = normalised

    return params
