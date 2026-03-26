"""
ENCODE-specific helper functions and constants.

Extracted from cortex/app.py — pure functions with no database or app
dependencies.  All functions operate solely on their arguments.

Functions:
    _looks_like_assay        — classify a string as assay vs biosample
    _correct_tool_routing    — fix LLM tool/param mismatches
    _validate_encode_params  — last-chance param sanitisation before MCP call
    _find_experiment_for_file — scan history for parent ENCSR of an ENCFF
    _extract_encode_search_term — pull biosample / search term from user query
"""

import re

from common.logging_config import get_logger

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

# Mapping from user-message fragments to canonical ENCODE assay_title values.
# Used both for auto-generating filtered DATA_CALLs and for server-side
# dataframe filtering when the previous result set is injected as context.
_ENCODE_ASSAY_ALIASES: dict[str, str] = {
    "long read rna": "long read RNA-seq",
    "long-read rna": "long read RNA-seq",
    "long read rna-seq": "long read RNA-seq",
    "chip-seq": "TF ChIP-seq",
    "chipseq": "TF ChIP-seq",
    "tf chip": "TF ChIP-seq",
    "histone chip": "Histone ChIP-seq",
    "atac-seq": "ATAC-seq",
    "atacseq": "ATAC-seq",
    "atac seq": "ATAC-seq",
    "dnase-seq": "DNase-seq",
    "dnase seq": "DNase-seq",
    "microrna-seq": "microRNA-seq",
    "microrna seq": "microRNA-seq",
    "mirna-seq": "microRNA-seq",
    "mirna seq": "microRNA-seq",
    "micro rna-seq": "microRNA-seq",
    "rna-seq": "total RNA-seq",
    "rnaseq": "total RNA-seq",
    "polya plus rna": "polyA plus RNA-seq",
    "polya rna": "polyA plus RNA-seq",
    "shrna rna": "shRNA RNA-seq",
    "crispr rna": "CRISPR RNA-seq",
    "eclip": "eCLIP",
    "clip-seq": "eCLIP",
    "hi-c": "in situ Hi-C",
    "hic": "in situ Hi-C",
    "wgbs": "WGBS",
    "rrbs": "RRBS",
}


_SEARCH_STYLE_PARAMS = frozenset(
    ["search_term", "assay_title", "biosample", "cell_line", "tissue", "target", "organism"]
)
# Well-known human cell lines — used only for defaulting organism=Homo sapiens.
# The misrouting guard itself is structural (any non-ENCSR accession redirects).
_HUMAN_BIOSAMPLES = frozenset([
    "k562", "gm12878", "hela", "hela-s3", "hepg2", "hek293", "hek293t",
    "jurkat", "mcf-7", "mcf7", "a549", "u2os", "imr90", "imr-90",
    "h1", "h9", "hff", "hff-myc", "wtc-11", "wtc11", "lncap", "panc-1",
    "panc1", "sk-n-sh", "sknsh", "caco-2", "caco2", "sh-sy5y", "shsy5y",
    "hl-60", "hl60", "thp-1", "thp1", "u937", "nb4", "kasumi-1",
    "raji", "namalwa", "rpmi-8226", "mm.1s",
])
# Pattern for a valid ENCODE experiment accession: ENCSR + 6 uppercase alphanumeric chars
_ENCSR_PATTERN = re.compile(r'^ENCSR[A-Z0-9]{6}$', re.IGNORECASE)
# Pattern for a valid ENCODE file accession (ENCFF...)
_ENCFF_PATTERN = re.compile(r'^ENCFF[A-Z0-9]{6}$', re.IGNORECASE)
# Standalone ENCFF mentions should reroute ENCODE tools; path fragments like
# data/ENCFF921XAH.bam should not.
_ENCFF_MENTION_PATTERN = re.compile(
    r'(?<![A-Z0-9_./-])(ENCFF[A-Z0-9]{6})(?![A-Z0-9_.-])',
    re.IGNORECASE,
)
# Substrings that indicate a string is an assay name rather than a biosample name.
_ASSAY_INDICATORS = (
    "-seq", " seq", "chip", "atac", "clip", "wgbs", "rrbs", "hi-c",
    "rna-seq", "dnase", "crispr", "eclip", "iclip", "rampage",
    "long read", "long-read",
)

# Stop-words for the ENCODE search-term extractor.  These are stripped before
# identifying the likely biosample / search term from the user's query.
_ENCODE_STOP_WORDS = frozenset([
    "how", "many", "much", "does", "do", "did", "is", "are", "was", "were",
    "what", "which", "where", "the", "a", "an", "of", "in", "for", "on",
    "and", "or", "to", "with", "from", "by", "at", "its", "it", "this",
    "that", "there", "have", "has", "had", "can", "could", "will", "would",
    "show", "me", "give", "get", "find", "search", "list", "tell", "about",
    "encode", "experiments", "experiment", "data", "results", "available",
    "portal", "database", "total", "count", "number", "please", "i", "want",
    "need", "look", "up", "any", "all", "some",
    # Referential / pronoun words — should not be treated as search terms
    "them", "they", "those", "these", "their", "its", "ones", "samples",
    "accessions", "accession",
    # Visualization / follow-up — not search terms
    "plot", "chart", "graph", "histogram", "scatter", "visualize", "visualise",
    "heatmap", "pie", "bar", "box", "distribution", "summarize", "summarise",
    "compare", "filter", "sort", "group", "aggregate", "breakdown", "table",
])


# ── Functions ──────────────────────────────────────────────────────────────

def _looks_like_assay(s: str) -> bool:
    """Return True if *s* looks like an assay name rather than a biosample."""
    sl = s.lower()
    if any(ind in sl for ind in _ASSAY_INDICATORS):
        return True
    # Also check against the aliases map (both keys and canonical values)
    if sl in _ENCODE_ASSAY_ALIASES:
        return True
    canon = _ENCODE_ASSAY_ALIASES.get(sl)
    if canon:
        return True
    if any(sl == v.lower() for v in _ENCODE_ASSAY_ALIASES.values()):
        return True
    return False


def _find_experiment_for_file(file_accession: str,
                              conversation_history: list | None) -> str | None:
    """
    Scan conversation history to find the ENCSR experiment accession
    that was queried when the given ENCFF file accession appeared.

    Strategy: look for assistant messages that mention the ENCFF accession
    and also contain an ENCSR accession (the parent experiment).
    """
    if not conversation_history or not file_accession:
        return None

    file_acc_upper = file_accession.upper()

    for msg in reversed(conversation_history):
        content = msg.get("content", "")
        if file_acc_upper not in content.upper():
            continue
        # This message mentions our file — find ENCSR accessions in it
        experiments = re.findall(r'(ENCSR[A-Z0-9]{6})', content, re.IGNORECASE)
        if experiments:
            return experiments[0].upper()

    # Fallback: find the most recent ENCSR in any message
    for msg in reversed(conversation_history):
        content = msg.get("content", "")
        experiments = re.findall(r'(ENCSR[A-Z0-9]{6})', content, re.IGNORECASE)
        if experiments:
            return experiments[0].upper()

    return None


def _extract_standalone_encff_mentions(text: str) -> list[str]:
    """Return ENCFF accessions that appear as standalone mentions, not paths."""
    if not text:
        return []
    return [match.upper() for match in _ENCFF_MENTION_PATTERN.findall(text)]


def _extract_encode_search_term(user_message: str) -> str | None:
    """
    Extract the most likely biosample / search term from an ENCODE query.

    Uses pattern matching first (e.g. "how many X experiments"), then falls
    back to stripping stop-words and returning the remaining content word(s).
    Returns None only if nothing usable can be extracted.
    """
    msg = user_message.strip()

    # Pattern 1: "how many <TERM> experiments"
    m = re.search(r'how many\s+(.+?)\s+experiments?', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pattern 2: "search encode for <TERM>"  /  "search for <TERM>"
    m = re.search(r'search\s+(?:encode\s+)?for\s+(.+?)(?:\s+experiments?|\s*$)', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pattern 3: "<TERM> experiments in encode"
    m = re.search(r'(.+?)\s+experiments?\s+(?:in|on|from)\s+encode', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pattern 4: "does encode have <TERM>"
    m = re.search(r'does\s+encode\s+have\s+(.+?)(?:\s+experiments?|\s*\??\s*$)', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Fallback: strip stop-words and return the remaining token(s)
    tokens = re.findall(r'[A-Za-z0-9][\w.\-]*', msg)
    content = [t for t in tokens if t.lower() not in _ENCODE_STOP_WORDS]
    if content:
        return " ".join(content)

    return None


def _correct_tool_routing(tool: str, params: dict, user_message: str,
                          conversation_history: list | None = None) -> tuple[str, dict]:
    """
    Fix cases where the LLM uses the wrong tool for a given accession type.

    Common mistakes:
    - Using get_experiment with search-style params or a non-ENCSR value as
      'accession' (e.g. a biosample name like K562 or MCF-7) instead of
      using search_by_biosample.
    - Using get_experiment for ENCFF (file) accessions instead of get_file_metadata
    - Mangling ENCFF → ENCSR (changing the prefix to match the tool it chose)
    - Calling get_file_metadata without the required experiment accession

    get_experiment requires exactly: accession=ENCSR[A-Z0-9]{6}
    get_file_metadata requires: accession=ENCSR... + file_accession=ENCFF...
    """
    accession = params.get("accession", "")
    msg_upper = user_message.upper()

    # ── Redirect get_experiment → search_by_biosample ──────────────────────
    # Trigger when: (a) any search-style param was passed, OR (b) the accession
    # value is not a valid ENCSR accession (catches ALL unknown cell line names,
    # not just those in a hardcoded list).
    if tool == "get_experiment":
        acc_upper = accession.upper()
        has_search_params = bool(params.keys() & _SEARCH_STYLE_PARAMS)
        acc_invalid = bool(accession) and not _ENCSR_PATTERN.match(accession) \
                      and not _ENCFF_PATTERN.match(accession)

        if has_search_params or acc_invalid:
            # Determine whether this is an assay-only query or a biosample query.
            # An assay-only query has assay info but no real biosample to anchor to.
            explicit_assay = params.get("assay_title") or (
                accession if (acc_invalid and _looks_like_assay(accession)) else None
            )
            # Resolve assay name through alias map if needed
            if explicit_assay:
                explicit_assay = _ENCODE_ASSAY_ALIASES.get(
                    explicit_assay.lower(), explicit_assay
                )

            explicit_biosample = (
                params.get("search_term")
                or params.get("biosample")
                or params.get("cell_line")
                or (accession if (acc_invalid and not _looks_like_assay(accession)) else None)
            )

            # ── Case A: assay-only (no biosample) → search_by_assay ──
            if explicit_assay and not explicit_biosample:
                new_params: dict = {"assay_title": explicit_assay}
                if "organism" in params:
                    new_params["organism"] = params["organism"]
                if "target" in params:
                    new_params["target"] = params["target"]
                logger.warning(
                    "Rerouted get_experiment → search_by_assay (assay-only)",
                    original_params=params, new_params=new_params,
                )
                return "search_by_assay", new_params

            # ── Case B: biosample (± assay) → search_by_biosample ──
            search_term = explicit_biosample
            if not search_term:
                # Last resort: grab the first capitalised word from the user message
                candidates = re.findall(
                    r'\b([A-Z][A-Za-z0-9]{1,10}(?:[-][A-Za-z0-9]+)?)\b', user_message
                )
                for c in candidates:
                    if c.upper() not in ("ENCODE", "WHAT", "HOW", "MANY", "ARE", "DOES", "HAVE"):
                        search_term = c
                        break

            new_params = {}
            if search_term:
                new_params["search_term"] = search_term

            for key in ("organism", "target", "exclude_revoked"):
                if key in params:
                    new_params[key] = params[key]
            if explicit_assay:
                new_params["assay_title"] = explicit_assay

            # Default organism=Homo sapiens for well-known human lines
            if "organism" not in new_params and (search_term or "").lower() in _HUMAN_BIOSAMPLES:
                new_params["organism"] = "Homo sapiens"

            if new_params.get("search_term"):
                logger.warning(
                    "Rerouted get_experiment → search_by_biosample",
                    original_params=params, new_params=new_params,
                )
                return "search_by_biosample", new_params

    if tool == "get_experiment" and accession:
        acc_upper = accession.upper()
        file_acc = None

        # Case 1: LLM passed an ENCFF accession to get_experiment directly
        if acc_upper.startswith("ENCFF"):
            file_acc = accession

        # Case 2: LLM mangled ENCFF → ENCSR (changed prefix to match tool)
        # ENCODE accessions: 5-char prefix (ENC + 2-letter type) + 6 alphanumeric
        elif acc_upper.startswith("ENCSR"):
            suffix = acc_upper[5:]  # e.g. "921XAH" (skip 5-char prefix)
            candidate = f"ENCFF{suffix}"
            if candidate in msg_upper:
                file_acc = candidate

        # Case 3: User message mentions an ENCFF accession but the LLM
        # hallucinated a completely different ENCSR accession for get_experiment.
        # Extract the ENCFF from the user message directly.
        if not file_acc:
            encff_in_msg = _extract_standalone_encff_mentions(user_message)
            if encff_in_msg:
                file_acc = encff_in_msg[0]
                logger.warning(
                    "LLM hallucinated accession for get_experiment, "
                    "user message has ENCFF — rerouting",
                    hallucinated=accession, file_accession=file_acc)

        if file_acc:
            # Find parent experiment accession from conversation history
            exp_acc = _find_experiment_for_file(file_acc, conversation_history)
            if exp_acc:
                logger.warning("Rerouting get_experiment → get_file_metadata",
                              file_accession=file_acc, experiment=exp_acc)
                return "get_file_metadata", {
                    "accession": exp_acc, "file_accession": file_acc}
            else:
                # No experiment found — can't call get_file_metadata without it.
                # Return a routing error so the caller skips the MCP call.
                logger.warning(
                    "Cannot find parent experiment for file accession",
                    file_accession=file_acc)
                return "get_file_metadata", {
                    "file_accession": file_acc,
                    "__routing_error__": (
                        f"Cannot look up file metadata for {file_acc} without "
                        f"knowing its parent experiment (ENCSR...). "
                        f"Please first query the experiment that contains this "
                        f"file, then ask about the file."
                    ),
                }

    # Also fix get_file_metadata called without experiment accession
    if tool == "get_file_metadata":
        file_acc = params.get("file_accession", params.get("accession", ""))
        exp_acc = params.get("accession", "")
        # If accession looks like a file accession, it's misplaced
        if exp_acc.upper().startswith("ENCFF"):
            file_acc = exp_acc
            exp_acc = ""
        # If we don't have experiment accession, find it from history
        if not exp_acc or not exp_acc.upper().startswith("ENCSR"):
            found_exp = _find_experiment_for_file(file_acc, conversation_history)
            if found_exp:
                logger.warning("Added missing experiment accession for get_file_metadata",
                              file_accession=file_acc, experiment=found_exp)
                return "get_file_metadata", {
                    "accession": found_exp, "file_accession": file_acc}
            else:
                # No experiment found — return routing error
                return "get_file_metadata", {
                    "file_accession": file_acc,
                    "__routing_error__": (
                        f"Cannot look up file metadata for {file_acc} without "
                        f"knowing its parent experiment (ENCSR...). "
                        f"Please first query the experiment that contains this "
                        f"file, then ask about the file."
                    ),
                }

    # --- General ENCFF catch-all (any ENCODE tool, not just get_experiment) ---
    # When the user message contains an ENCFF file accession but the tool
    # called is *not* get_file_metadata (e.g. LLM called get_files_by_type
    # with a hallucinated ENCSR instead).
    if tool not in ("get_file_metadata",):
        encff_in_msg = _extract_standalone_encff_mentions(user_message)
        if encff_in_msg:
            file_acc = encff_in_msg[0]
            exp_acc = _find_experiment_for_file(file_acc, conversation_history)
            if exp_acc:
                logger.warning(
                    "General ENCFF catch-all: ENCFF in user message, rerouting to get_file_metadata",
                    from_tool=tool, file_accession=file_acc, experiment=exp_acc,
                )
                return "get_file_metadata", {
                    "accession": exp_acc, "file_accession": file_acc}
            else:
                logger.warning(
                    "General ENCFF catch-all: ENCFF in user message but no parent experiment found",
                    from_tool=tool, file_accession=file_acc,
                )
                return "get_file_metadata", {
                    "file_accession": file_acc,
                    "__routing_error__": (
                        f"Cannot look up file metadata for {file_acc} without "
                        f"knowing its parent experiment (ENCSR...). "
                        f"Please first query the experiment that contains this "
                        f"file, then ask about the file."
                    ),
                }

    # ── Redirect search_by_biosample → search_by_assay ──────────────────
    # When alias resolution mapped "search" → "search_by_biosample" but the
    # LLM actually provided assay-style params (assay_title, organism) with
    # no search_term, the correct tool is search_by_assay.
    if tool == "search_by_biosample" and "search_term" not in params:
        assay_val = params.get("assay_title", "")
        if assay_val and _looks_like_assay(assay_val):
            new_params: dict = {"assay_title": assay_val}
            if "organism" in params:
                new_params["organism"] = params["organism"]
            if "target" in params:
                new_params["target"] = params["target"]
            logger.warning(
                "Rerouted search_by_biosample → search_by_assay (no search_term, has assay_title)",
                original_params=params, new_params=new_params,
            )
            return "search_by_assay", new_params

    return tool, params


def _validate_encode_params(tool: str, params: dict, user_message: str) -> dict:
    """
    Last-chance fix for ENCODE tool params before the MCP call.

    Common LLM mistakes this catches:
    - search_by_biosample called without search_term (required) but with
      assay_title containing the biosample name (e.g. assay_title=MEL).
    - organism=Homo sapiens/Mus musculus added when the user never
      mentioned a species — wrong organism filter → zero results.
    """
    params = dict(params)  # shallow copy

    # --- Fix 1: missing search_term in search_by_biosample ---
    if tool == "search_by_biosample" and "search_term" not in params:
        # Try to salvage from assay_title if it doesn't look like an assay
        assay_val = params.get("assay_title", "")
        if assay_val and not _looks_like_assay(assay_val):
            params["search_term"] = assay_val
            del params["assay_title"]
            logger.warning("Moved assay_title to search_term (was biosample, not assay)",
                          search_term=assay_val)
        else:
            # Try to extract from user message
            extracted = _extract_encode_search_term(user_message)
            if extracted:
                params["search_term"] = extracted
                logger.warning("Injected missing search_term from user message",
                              search_term=extracted)

    # --- Fix 2: resolve assay_title through alias map ---
    # The LLM often emits generic names like "RNA-seq" while ENCODE experiments
    # use canonical names like "total RNA-seq".  Without this, the ENCODELIB
    # exact-match filter returns 0 results.
    if "assay_title" in params and params["assay_title"]:
        _assay_lower = params["assay_title"].lower()
        _canonical = _ENCODE_ASSAY_ALIASES.get(_assay_lower)
        if _canonical:
            logger.info("Resolved assay alias",
                       original=params["assay_title"], canonical=_canonical)
            params["assay_title"] = _canonical

    # --- Fix 3: strip organism unless user explicitly mentioned it ---
    if "organism" in params:
        msg_lower = user_message.lower()
        _explicit_organism = any(kw in msg_lower for kw in (
            "mouse", "human", "homo sapiens", "mus musculus",
            "drosophila", "c. elegans", "worm", "fly",
        ))
        if not _explicit_organism:
            removed = params.pop("organism")
            logger.info("Stripped auto-organism (user didn't request it)",
                       removed_organism=removed)

    # --- Fix 4: replace hallucinated ENCSR with what the user explicitly stated ---
    # If the user message contains a valid ENCSR accession and the LLM put a
    # *different* ENCSR in "accession", always trust the user's value.
    if "accession" in params and params["accession"]:
        _encsr_in_msg = re.findall(r'(ENCSR[A-Z0-9]{6})', user_message, re.IGNORECASE)
        if _encsr_in_msg:
            _user_encsr = _encsr_in_msg[0].upper()
            if params["accession"].upper() != _user_encsr:
                logger.warning(
                    "Replaced hallucinated ENCSR with user-stated accession",
                    hallucinated=params["accession"], correct=_user_encsr,
                )
                params["accession"] = _user_encsr

    return params
