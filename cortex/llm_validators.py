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
    """
    params = {}
    if params_str:
        for param_pair in params_str.split(','):
            param_pair = param_pair.strip()
            if '=' in param_pair:
                key, value = param_pair.split('=', 1)
                value = value.strip().strip('"\'')
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
    # Guard only fires if the job is genuinely still running.  The block.status
    # field may lag after a server restart (the background poll task was killed),
    # so we double-check the nested job_status payload before blocking.
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
        # Common aliases that are resolved downstream — not violations
        "search",
        # Launchpad (Dogme)
        "submit_dogme_job", "check_nextflow_status", "get_dogme_report",
        "submit_dogme_nextflow", "find_pod5_directory", "generate_dogme_config",
        "scaffold_dogme_dir", "get_job_logs", "get_job_debug",
        # Analyzer (Analysis)
        "list_job_files", "find_file", "read_file_content",
        "parse_csv_file", "parse_bed_file", "get_analysis_summary",
        "categorize_job_files",
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

    if current_skill not in ("analyze_local_sample",):
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

    # --- Signals for analyze_job_results ---
    _results_words = [
        "qc report", "quality control", "parse the",
        "read the output", "show me the results", "show the results",
        "check the bed", "analyze results", "analyze the result",
        "analyse the result", "analyse results",
        "view the result", "view results", "show result",
        "job results", "the results", "see the result",
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

    return None
