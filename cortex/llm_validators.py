"""
LLM output validation and skill-detection helpers.

Extracted from cortex/app.py — functions that validate, clean, and
route LLM responses before they reach the rest of the pipeline.

Functions:
    get_block_payload          — deserialise a ProjectBlock's JSON payload
    _parse_tag_params          — parse key=value strings from DATA_CALL tags
    _validate_llm_output       — contract-check the raw LLM response
    _auto_detect_skill_switch  — pre-LLM skill rerouting by keyword signals
"""

import json
import re

from cortex.models import ProjectBlock
from common.logging_config import get_logger

logger = get_logger(__name__)


# ── Utility ────────────────────────────────────────────────────────────────

def get_block_payload(block: ProjectBlock) -> dict:
    """Helper to get payload as dict from payload_json"""
    return json.loads(block.payload_json) if block.payload_json else {}


def _parse_tag_params(params_str: str | None) -> dict:
    """
    Parse a comma-separated key=value parameter string from a DATA_CALL tag.
    E.g., "search_term=K562, organism=Homo sapiens" -> {"search_term": "K562", "organism": "Homo sapiens"}
    Handles JSON arrays like gene_symbols=["TP53", "BRCA1"] without splitting inside brackets.
    """
    params = {}
    if not params_str:
        return params
    # Split on commas that are NOT inside square brackets
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in params_str:
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current))
    for part in parts:
        part = part.strip()
        if '=' in part:
            key, value = part.split('=', 1)
            value = value.strip()
            # Try to parse JSON arrays/objects so MCP tools receive native types
            if value.startswith('['):
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    value = value.strip('"\'')
            else:
                value = value.strip('"\'')
            params[key.strip()] = value
    return params


# ── Output Contract Validator ──────────────────────────────────────────────

def _validate_llm_output(
    response: str,
    active_skill: str,
    history_blocks: list | None = None,
) -> tuple[str, list[str]]:
    """
    Validate the LLM's raw response against output contracts.
    Auto-corrects recoverable violations; logs all violations.

    Returns:
        (cleaned_response, violations) — list of human-readable violation strings.
    """
    violations: list[str] = []
    cleaned = response

    # 1. Malformed DATA_CALL: has the marker but no tool=
    _dc_incomplete = re.findall(r'\[\[DATA_CALL:[^\]]*\]\]', cleaned)
    for _tag in _dc_incomplete:
        if "tool=" not in _tag:
            violations.append(f"Malformed DATA_CALL (no tool=): {_tag[:80]}")
            cleaned = cleaned.replace(_tag, "")  # strip it

    # 2. Multiple APPROVAL_NEEDED tags — keep only the first
    _approval_count = cleaned.count("[[APPROVAL_NEEDED]]")
    if _approval_count > 1:
        violations.append(f"Duplicate APPROVAL_NEEDED tags ({_approval_count})")
        # Keep first, remove rest
        _first_pos = cleaned.index("[[APPROVAL_NEEDED]]") + len("[[APPROVAL_NEEDED]]")
        cleaned = cleaned[:_first_pos] + cleaned[_first_pos:].replace("[[APPROVAL_NEEDED]]", "")

    # 3. SKILL_SWITCH during active job (block status=RUNNING)
    # Guard only fires if the job is genuinely still running AND the user has
    # not sent a new message after the job was created (which means the user is
    # asking for something new and the switch should be allowed).
    # The block.status field may lag after a server restart (the background
    # poll task was killed), so we double-check the nested job_status payload
    # before blocking.
    _has_switch = "[[SKILL_SWITCH_TO:" in cleaned
    if _has_switch and history_blocks:
        for blk in reversed(history_blocks):
            if blk.type == "EXECUTION_JOB" and blk.status == "RUNNING":
                _blk_pl = get_block_payload(blk)
                _inner_status = _blk_pl.get("job_status", {}).get("status", "")
                if _inner_status in ("COMPLETED", "FAILED"):
                    # Block status is stale (likely due to server restart).
                    # Job is actually done — allow the skill switch.
                    break
                # Check if the user sent a message *after* this running job.
                # If so, the user is working on something new — allow the switch.
                _job_seq = getattr(blk, "seq", 0)
                _user_msg_after_job = any(
                    b.type == "USER_MESSAGE" and getattr(b, "seq", 0) > _job_seq
                    for b in history_blocks
                )
                if _user_msg_after_job:
                    break  # User moved on — allow the skill switch
                violations.append("SKILL_SWITCH attempted during running job — stripped")
                cleaned = re.sub(r'\[\[SKILL_SWITCH_TO:\s*\w+\]\]', '', cleaned)
                break

    # 4. DATA_CALL with unknown tool name (not in any known registry)
    # Using a lightweight check — just flag obviously wrong patterns
    _known_tools = {
        # ENCODE
        "get_experiment", "search_by_biosample", "search_by_assay",
        "search_by_target", "search_by_organism", "get_files_by_type",
        "get_files_summary", "get_file_metadata", "get_file_url",
        "get_available_output_types", "get_all_metadata", "list_experiments",
        "get_cache_stats", "get_server_info",
        # IGVF
        "search_measurement_sets", "search_analysis_sets",
        "search_prediction_sets", "search_by_sample",
        "search_files", "get_dataset",
        "get_file_download_url", "get_files_for_dataset",
        "search_genes", "get_gene", "search_samples",
        # Common aliases that are resolved downstream — not violations
        "search",
        # Launchpad (Dogme)
        "submit_dogme_job", "check_nextflow_status", "get_dogme_report",
        "stage_remote_sample", "list_remote_files",
        "list_ssh_profiles", "test_ssh_connection", "get_slurm_defaults", "cancel_slurm_job",
        "run_allowlisted_script",
        "submit_dogme_nextflow", "find_pod5_directory", "generate_dogme_config",
        "scaffold_dogme_dir", "get_job_logs", "get_job_debug",
        # Analyzer (Analysis)
        "list_job_files", "find_file", "read_file_content",
        "parse_csv_file", "parse_bed_file", "get_analysis_summary",
        "categorize_job_files",
        # Compatibility alias corrected downstream to find_file.
        "show_bam_details",
        # edgePython (Differential Expression)
        "load_data", "load_data_auto", "filter_genes", "normalize",
        "normalize_chip", "set_design", "set_design_matrix",
        "estimate_dispersion", "fit_model", "test_contrast",
        "exact_test", "get_top_genes", "generate_plot",
        "annotate_genes", "translate_gene_ids", "lookup_gene",
    }
    for _dc_m in re.finditer(r'\[\[DATA_CALL:.*?tool=(\w+)', cleaned):
        _tool = _dc_m.group(1)
        if _tool not in _known_tools:
            violations.append(f"Unknown tool in DATA_CALL: {_tool}")

    # 5. Mixed sources in one response for non-browsing context
    _sources_in_tags = set()
    for _dc_m in re.finditer(r'\[\[DATA_CALL:\s*(?:consortium|service)=(\w+)', cleaned):
        _sources_in_tags.add(_dc_m.group(1))
    if len(_sources_in_tags) > 1:
        # Only flag if it's not an ENCODE + analyzer browsing combo
        if _sources_in_tags != {"encode", "analyzer"} and _sources_in_tags != {"launchpad", "analyzer"}:
            violations.append(f"Mixed sources in DATA_CALL tags: {sorted(_sources_in_tags)}")

    if violations:
        logger.warning("LLM output violations detected",
                      count=len(violations), violations=violations,
                      skill=active_skill)

    return cleaned, violations


# ── Skill Detection ────────────────────────────────────────────────────────

def _auto_detect_skill_switch(user_message: str, current_skill: str) -> str | None:
    """
    Pre-LLM safety net: detect when the user's message obviously requires
    a different skill than the currently active one.

    Returns the correct skill key if a switch is needed, or None to stay.
    Only triggers on strong, unambiguous signals to avoid false positives.
    """
    msg_lower = user_message.lower()

    # --- Early DE pre-check ---
    # Catch obvious DE requests before the local_sample check can steal them.
    # A standalone "de"/"deg" word + a counts file (.csv/.tsv) is never a
    # sequencing-data submission — it's always differential expression.
    if current_skill != "differential_expression":
        _has_de_word_early = bool(re.search(r'\b(de|deg|degs)\b', msg_lower))
        _has_counts_file_early = bool(re.search(r'counts?\.(csv|tsv|txt)\b', msg_lower))
        if _has_de_word_early and _has_counts_file_early:
            return "differential_expression"

    # --- Early remote execution pre-check ---
    _remote_words_early = ["slurm", "sbatch", "cluster", "remote execution"]
    _has_remote_word_early = any(w in msg_lower for w in _remote_words_early)
    _has_run_intent_early = any(w in msg_lower for w in ["run", "submit", "launch", "analyze", "analyse", "process", "stage", "staging"])
    _has_remote_browse_intent_early = bool(re.search(
        r'\b(?:list|show|browse|what)\s+(?:the\s+)?(?:top\s+)?(?:files?|folders?|directories?)\b',
        msg_lower,
    ))
    _has_remote_profile_phrase_early = bool(re.search(
        r'\b(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)[a-zA-Z0-9_-]+(?:\s+profile)?(?:[?.!,]|$)|(?:using|via)\s+(?:the\s+)?[a-zA-Z0-9_-]+\s+profile)\b',
        msg_lower,
    ))
    if current_skill != "remote_execution" and (_has_remote_word_early or _has_remote_profile_phrase_early) and (_has_run_intent_early or _has_remote_browse_intent_early):
        return "remote_execution"
    if current_skill == "remote_execution" and (_has_remote_word_early or _has_remote_profile_phrase_early) and _has_remote_browse_intent_early:
        return None

    # --- Signals for analyze_local_sample ---
    # User mentions a local file path + analysis intent
    _has_local_path = bool(re.search(r'(/[a-z_][\w/.-]+|~[\w/.-]+)', user_message))
    # Also detect relative paths with known extensions (e.g. data/ENCFF921XAH.bam)
    _has_relative_data_path = bool(re.search(r'\b[\w./]+\.(bam|pod5|fastq|fq|fast5)\b', msg_lower))
    _has_any_path = _has_local_path or _has_relative_data_path
    _analysis_words = ["analyze", "analyse", "process", "run", "submit", "launch"]
    _has_analysis = any(w in msg_lower for w in _analysis_words)
    _sample_words = ["sample", "pod5", "local", "my data", "my files", ".bam", "bam file", "bam files"]
    _has_sample = any(w in msg_lower for w in _sample_words)
    _data_type_words = ["cdna", "dna", "rna", "fiber-seq", "fiberseq"]
    _has_data_type = any(w in msg_lower for w in _data_type_words)

    if current_skill not in ("analyze_local_sample", "differential_expression"):
        # Strong signal: path + (analysis verb OR sample keyword OR data type)
        # Even from Dogme analysis skills (run_dogme_*), a message like
        # "Analyze the local CDNA sample at /path" is clearly a NEW submission,
        # not a follow-up on the current job's results. Require at least TWO
        # signals when switching FROM a Dogme analysis skill to avoid false
        # positives on things like "parse /path/to/result.csv".
        _signal_count = sum([_has_analysis, _has_sample, _has_data_type])
        _from_dogme_skill = current_skill in (
            "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna"
        )
        if _has_any_path:
            if _from_dogme_skill:
                # From a Dogme skill, require path + at least 2 of:
                # analysis verb, sample keyword, data type keyword
                if _signal_count >= 2:
                    return "analyze_local_sample"
            else:
                # From other skills, path + any 1 signal is enough
                if _signal_count >= 1:
                    return "analyze_local_sample"

    # --- Signals for reconcile_bams ---
    if current_skill != "reconcile_bams":
        if re.search(r"(?:reconcile|merge|combine)\s+(?:the\s+)?(?:annotated\s+)?bams?", msg_lower):
            return "reconcile_bams"
        if any(w in msg_lower for w in (
            "cross-workflow bam",
            "cross workflow bam",
        )):
            return "reconcile_bams"

    # --- Signals for ENCODE_Search ---
    _encode_words = ["encode", "encsr", "encff", "encode portal"]
    _search_words = ["search", "how many", "experiments", "accession", "biosample"]
    _has_encode = any(w in msg_lower for w in _encode_words)
    _has_search = any(w in msg_lower for w in _search_words)

    if current_skill not in ("ENCODE_Search", "ENCODE_LongRead"):
        if _has_encode and _has_search:
            return "ENCODE_Search"
        # Strong signal: explicit accession mention
        if re.search(r'ENCSR[A-Z0-9]{6}', user_message, re.IGNORECASE):
            return "ENCODE_Search"

    # --- Signals for IGVF_Search ---
    _igvf_words = ["igvf", "igvf portal", "igvf data", "igvf dataset"]
    _igvf_search_words = ["search", "how many", "datasets", "measurement sets",
                          "prediction sets", "analysis sets", "accession",
                          "samples", "files", "genes"]
    _has_igvf = any(w in msg_lower for w in _igvf_words)
    _has_igvf_search = any(w in msg_lower for w in _igvf_search_words)

    if current_skill != "IGVF_Search":
        if _has_igvf and _has_igvf_search:
            return "IGVF_Search"
        # Strong signal: IGVF accession pattern
        if re.search(r'IGVF(?:DS|FI)[A-Z0-9]{4,8}', user_message):
            return "IGVF_Search"

    # --- Signals for analyze_job_results ---
    _results_words = [
        "qc report", "quality control", "parse the",
        "read the output", "show me the results", "show the results",
        "check the bed", "analyze results", "analyze the result",
        "analyse the result", "analyse results",
        "view the result", "view results", "show result",
        "job results", "the results", "see the result",
        "list files", "show files",
        "list workflow", "list workflows", "show workflow",
    ]
    if current_skill != "analyze_job_results":
        if any(w in msg_lower for w in _results_words):
            return "analyze_job_results"

    # --- Signals for download_files ---
    _download_words = ["download", "grab", "fetch", "save"]
    _has_download = any(w in msg_lower for w in _download_words)
    _has_file_accession = bool(re.search(r'ENCFF[A-Z0-9]{6}', user_message, re.IGNORECASE))
    _has_url = bool(re.search(r'https?://', msg_lower))
    if current_skill != "download_files":
        if _has_download and (_has_file_accession or _has_url):
            return "download_files"

    # --- Signals for enrichment analysis ---
    _enrichment_words = [
        "go enrichment", "gene ontology", "pathway enrichment",
        "go analysis", "pathway analysis", "enrichment analysis",
        "kegg enrichment", "reactome enrichment",
        "biological process", "molecular function", "cellular component",
        "enriched terms", "enriched pathways", "enriched go",
        "go:bp", "go:mf", "go:cc",
    ]
    if current_skill != "enrichment_analysis":
        if any(w in msg_lower for w in _enrichment_words):
            return "enrichment_analysis"

    # --- Signals for XgenePy cis/trans analysis ---
    _xgenepy_words = [
        "xgenepy", "xgeneopy", "cis/trans", "cis trans",
        "allele-specific", "allele specific", "proportion cis",
        "regulatory assignment", "fit_summary.json", "assignments.tsv",
    ]
    if current_skill != "xgenepy_analysis":
        if any(w in msg_lower for w in _xgenepy_words):
            return "xgenepy_analysis"

    # --- Signals for gene lookup (routes to differential_expression / edgePython) ---
    _gene_words = [
        "ensembl", "ensembl id", "gene id", "gene symbol",
        "gene name", "gene annotation", "gene info",
        "ensg0", "ensmusg0",
    ]
    _gene_query_words = [
        "what gene", "which gene", "look up gene", "lookup gene",
        "what is the gene", "what is the ensembl",
        "gene id for", "ensembl id for", "info on ensg",
    ]
    _has_gene = any(w in msg_lower for w in _gene_words)
    _has_gene_query = any(w in msg_lower for w in _gene_query_words)
    if current_skill not in ("differential_expression",):
        if _has_gene_query or _has_gene:
            return "differential_expression"

    # --- Signals for differential_expression ---
    _de_words = [
        "differential expression", "differentially expressed",
        "de analysis", "de genes", "deg", "degs", "edgepython", "edger",
        "count matrix", "counts table", "counts matrix",
        "volcano plot", "ma plot", "md plot", "bcv plot",
        "fold change", "log fold change", "logfc",
        "nebula", "single-cell de", "single cell de",
        "dtu", "differential transcript usage",
        "chip-seq enrichment", "chip enrichment",
    ]
    _de_action_words = [
        "compare", "contrast", "test", "run de", "find de",
    ]
    _de_context_words = [
        "treated vs control", "treatment vs control",
        "knockout vs wildtype", "ko vs wt",
        "mutant vs wildtype", "condition",
        "fdr", "p-value", "pvalue",
    ]
    _has_de = any(w in msg_lower for w in _de_words)
    # Standalone "de" / "DE" as a word (not part of "decode", "define", etc.)
    _has_de_standalone = bool(re.search(r'\bde\b', msg_lower))
    # File path containing "counts" + .csv/.tsv extension → likely a count matrix
    _has_counts_file = bool(re.search(r'counts?\.(csv|tsv|txt)\b', msg_lower))
    _has_de_action = any(w in msg_lower for w in _de_action_words)
    _has_de_context = any(w in msg_lower for w in _de_context_words)

    if current_skill != "differential_expression":
        if _has_de:
            return "differential_expression"
        # "DE" standalone + a counts file path is a strong signal
        if _has_de_standalone and _has_counts_file:
            return "differential_expression"
        # "DE" standalone + any known DE action or context word
        if _has_de_standalone and (_has_de_action or _has_de_context):
            return "differential_expression"
        if _has_de_action and _has_de_context:
            return "differential_expression"

    return None
