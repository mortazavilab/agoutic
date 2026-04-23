"""
Request classification and plan-type detection for the planner.

Heuristic regex-based classification — no LLM calls.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from common.logging_config import get_logger
from cortex.skill_manifest import compiled_triggers

if TYPE_CHECKING:
    from cortex.schemas import ConversationState

logger = get_logger(__name__)

_PROJECT_WORKFLOW_REF_RE = re.compile(
    r"\b[a-z0-9][a-z0-9_-]*\s*:\s*workflow\d+\b",
    re.IGNORECASE,
)
_BED_PATH_RE = re.compile(r"(?:/|~|\.)[^\s,;]+\.bed\b", re.IGNORECASE)


def _is_region_overlap_request(message: str) -> bool:
    msg = (message or "").lower()
    has_plot = any(token in msg for token in ("venn", "upset", "diagram"))
    has_region_term = any(token in msg for token in ("region", "regions", "open chromatin", "chromatin"))
    ref_count = len(_PROJECT_WORKFLOW_REF_RE.findall(message or ""))
    if ref_count < 2:
        ref_count = len(_BED_PATH_RE.findall(message or ""))
    return has_plot and has_region_term and ref_count >= 2

# ---------------------------------------------------------------------------
# Multi-step / informational pattern lists
# ---------------------------------------------------------------------------

_MULTI_STEP_PATTERNS: list[re.Pattern] = [
    re.compile(r"^/de\b", re.I),
    # Reconcile annotated BAMs
    re.compile(r"(?:reconcile|merge|combine)\s+(?:the\s+)?(?:annotated\s+)?bams?", re.I),
    re.compile(r"cross[-\s]?workflow\s+bam\s+reconcil", re.I),
    # Remote stage-only flows
    re.compile(r"stage\s+.+\b(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)[a-zA-Z0-9_-]+(?:\s+profile)?(?:[?.!,]|$)|on\s+slurm|using\s+slurm|remotely|on\s+the\s+cluster|(?:using|via)\s+(?:the\s+)?[a-zA-Z0-9_-]+\s+profile)", re.I),
    # Download + analyze
    re.compile(r"download.*(?:and|then)\s+(?:run|analyze|process)", re.I),
    re.compile(r"get.*from\s+encode.*(?:and|then)\s+(?:run|analyze|dogme)", re.I),
    re.compile(r"fetch.*(?:and|then)\s+(?:run|analyze|process)", re.I),
    # Compare
    re.compile(r"compare\s+(?:these|the|two|my|both|all)?\s*(?:samples?|results?|workflows?)", re.I),
    re.compile(r"(?:differences?|diff)\s+between\s+(?:the\s+)?(?:samples?|results?)", re.I),
    re.compile(r"(?:treated|control)\s+(?:vs?\.?|versus)\s+", re.I),
    # Pipeline / full run
    re.compile(r"run\s+(?:the\s+)?(?:full\s+)?pipeline\s+(?:on|for)", re.I),
    re.compile(r"analyze.*then\s+compare", re.I),
    re.compile(r"process.*(?:and|then)\s+(?:summarize|compare|report)", re.I),
    # Differential expression
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:differential\s+expression|DE\s+analysis)", re.I),
    re.compile(r"(?:differential\s+expression|DE)\s+(?:on|for|analysis)", re.I),
    # Parse + plot + interpret
    re.compile(r"(?:plot|graph|chart|visuali[sz]e).*(?:from|of|for)\s+(?:my|the|last|this)\s+(?:run|results?|workflow)", re.I),
    re.compile(r"(?:parse|read).*(?:and|then)\s+(?:plot|graph|chart|visuali[sz]e)", re.I),
    re.compile(r"(?:summarize|interpret|explain)\s+(?:the\s+)?(?:results?|output|qc)", re.I),
    # Search + compare to local
    re.compile(r"(?:download|get|fetch)\s+.*encode.*(?:compare|vs)", re.I),
    re.compile(r"compare\s+(?:my\s+)?(?:local|sample).*(?:to|with|against)\s+.*(?:encode|public)", re.I),
    # Enrichment analysis
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:GO|gene\s+ontology|enrichment|pathway)\s+(?:analysis|enrichment)", re.I),
    re.compile(r"(?:KEGG|Reactome)\s+(?:enrichment|analysis|pathway)", re.I),
    re.compile(r"(?:what|which)\s+(?:GO\s+terms?|pathways?|biological\s+processes?)\s+(?:are\s+)?enriched", re.I),
    # XgenePy analysis
    re.compile(r"(?:run|do|perform)\s+(?:xgenepy|xgeneopy)\s+(?:analysis)?", re.I),
    re.compile(r"(?:cis/trans|cis\s+trans|allele[-\s]?specific)\s+(?:analysis|model|assignment)", re.I),
]

_INFORMATIONAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(?:what|how|why|when|where|who|which|can you|do you|is there|are there)\s", re.I),
    re.compile(r"^(?:show|list|display|tell me about|explain|describe)\s", re.I),
    re.compile(r"^(?:what can you do|help|capabilities)\s*\??$", re.I),
]


# ---------------------------------------------------------------------------
# classify_request
# ---------------------------------------------------------------------------

def classify_request(
    message: str,
    active_skill: str,
    conv_state: "ConversationState",
) -> str:
    """
    Classify a user request as INFORMATIONAL, SINGLE_TOOL, or MULTI_STEP.

    Uses heuristic keyword matching — no LLM call.
    Also checks skill-defined plan chains for multi-step detection.
    Defaults to SINGLE_TOOL when uncertain (preserving existing behaviour).
    """
    msg = message.strip()

    # 1. Check for multi-step signals first (highest specificity)
    for pat in _MULTI_STEP_PATTERNS:
        if pat.search(msg):
            logger.info("classify_request: MULTI_STEP", pattern=pat.pattern[:60])
            return "MULTI_STEP"

    detected_plan_type = _detect_plan_type(msg)
    if detected_plan_type:
        logger.info("classify_request: MULTI_STEP", plan_type=detected_plan_type)
        return "MULTI_STEP"

    if _is_summarize_results_request(msg, conv_state):
        logger.info("classify_request: MULTI_STEP", reason="summarize_results")
        return "MULTI_STEP"

    # 1b. Check skill-defined plan chains
    from cortex.plan_chains import load_chains_for_skill, match_chain
    chains = load_chains_for_skill(active_skill)
    if chains and match_chain(msg, chains):
        logger.info("classify_request: CHAIN_MULTI_STEP", skill=active_skill)
        return "CHAIN_MULTI_STEP"

    # 2. Check for informational signals
    for pat in _INFORMATIONAL_PATTERNS:
        if pat.search(msg):
            return "INFORMATIONAL"

    # 3. Default: existing single-turn flow
    return "SINGLE_TOOL"


# ---------------------------------------------------------------------------
# Plan-type specific patterns
# ---------------------------------------------------------------------------

_DE_PATTERNS = [
    re.compile(r"^/de\b", re.I),
    re.compile(
        r"compare\s+(?:the\s+)?[a-zA-Z0-9_.-]+\s+samples?\s+.+?\s+(?:to|vs?\.?|versus|against)\s+(?:the\s+)?[a-zA-Z0-9_.-]+\s+samples?\s+.+",
        re.I,
    ),
    re.compile(
        r"compare\s+.+?\s+(?:to|vs?\.?|versus|against)\s+.+?\s+(?:from|using|on)\s+(?:df\s*\d+|\S*(?:abundance|counts?|matrix)\S*\.(?:csv|tsv|txt))",
        re.I,
    ),
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:differential\s+expression|DE\s+analysis)", re.I),
    re.compile(r"(?:differential\s+expression|DE)\s+(?:on|for|analysis)", re.I),
    re.compile(r"edger?\s+(?:analysis|pipeline)", re.I),
]

_SEARCH_COMPARE_LOCAL_PATTERNS = [
    re.compile(r"(?:download|get|fetch)\s+.*(?:encode|public).*(?:compare|vs)", re.I),
    re.compile(r"compare\s+(?:my\s+)?(?:local|sample).*(?:to|with|against)\s+.*(?:encode|public)", re.I),
    re.compile(r"(?:encode|public)\s+.*compare\s+.*(?:my|local)", re.I),
]

_COMPARE_WORKFLOW_PATTERNS = [
    re.compile(r"compare\s+(?:these|the|two|my|both|all)?\s*(?:workflows?|jobs?|runs?|pipelines?)", re.I),
    re.compile(r"(?:differences?|diff)\s+between\s+(?:the\s+)?(?:workflows?|jobs?|runs?)", re.I),
]

_COMPARE_PATTERNS = [
    re.compile(r"compare\s+(?:these|the|two|my|both|all)?\s*(?:samples?|results?|workflows?)", re.I),
    re.compile(r"(?:differences?|diff)\s+between", re.I),
    re.compile(r"\bvs?\.?\b.*\bvs?\.?\b", re.I),
    re.compile(r"(?:treated|control)\s+(?:vs?\.?|versus)\s+", re.I),
]

_DOWNLOAD_ANALYZE_PATTERNS = [
    re.compile(r"download.*(?:and|then)\s+(?:run|analyze|process)", re.I),
    re.compile(r"get.*from\s+encode.*(?:and|then)", re.I),
    re.compile(r"fetch.*(?:and|then)\s+(?:run|analyze|process)", re.I),
]

_PARSE_PLOT_PATTERNS = [
    re.compile(r"(?:plot|graph|chart|visuali[sz]e).*(?:from|of|for)\s+(?:my|the|last|this)\s+(?:run|results?|workflow)", re.I),
    re.compile(r"(?:parse|read).*(?:and|then)\s+(?:plot|graph|chart|visuali[sz]e)", re.I),
    re.compile(r"(?:show|display)\s+(?:the\s+)?(?:qc|quality|metrics?).*(?:plot|graph|chart)", re.I),
]

_RUN_WORKFLOW_PATTERNS = [
    re.compile(r"run\s+(?:the\s+)?(?:full\s+)?pipeline\s+(?:on|for)", re.I),
    re.compile(r"process\s+(?:my\s+)?(?:local\s+)?(?:sample|data|pod5|fastq|bam)", re.I),
    re.compile(r"analyze\s+(?:my\s+)?(?:local\s+)?(?:sample|data)", re.I),
]

_RECONCILE_BAMS_PATTERNS = [
    re.compile(r"(?:reconcile|merge|combine)\s+(?:the\s+)?(?:annotated\s+)?bams?", re.I),
    re.compile(r"cross[-\s]?workflow\s+bam\s+reconcil", re.I),
]

_REMOTE_STAGE_PATTERNS = [
    re.compile(r"stage(?:\s+only)?\s+(?:the\s+)?(?:sample\s+)?(?:.+?)\s+(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)[a-zA-Z0-9_-]+(?:\s+profile)?(?:[?.!,]|$)|on\s+slurm|using\s+slurm|remotely|on\s+the\s+cluster|(?:using|via)\s+(?:the\s+)?[a-zA-Z0-9_-]+\s+profile)", re.I),
]

_ENRICHMENT_PATTERNS = [
    re.compile(r"(?:run|do|perform)\s+(?:a\s+)?(?:GO|gene\s+ontology|enrichment|pathway)\s+(?:analysis|enrichment)", re.I),
    re.compile(r"(?:GO|gene\s+ontology|enrichment|pathway)\s+(?:analysis|enrichment)\s+(?:on|for|of)", re.I),
    re.compile(r"(?:what|which)\s+(?:GO\s+terms?|pathways?|biological\s+processes?)\s+(?:are\s+)?enriched", re.I),
    re.compile(r"(?:KEGG|Reactome)\s+(?:enrichment|analysis|pathway)", re.I),
    re.compile(r"(?:enrichment|GO)\s+(?:on|for)\s+(?:the\s+)?(?:up|down|significant|DE)", re.I),
]

_XGENEPY_PATTERNS = [
    re.compile(r"(?:run|do|perform)\s+(?:xgenepy|xgeneopy)\s+(?:analysis)?", re.I),
    re.compile(r"(?:xgenepy|xgeneopy)\s+(?:cis|trans|assignment|model)", re.I),
    re.compile(r"(?:cis/trans|cis\s+trans|allele[-\s]?specific)\s+(?:analysis|model|assignment)", re.I),
]


# ---------------------------------------------------------------------------
# _detect_plan_type
# ---------------------------------------------------------------------------

def _detect_plan_type_from_manifests(message: str) -> str | None:
    """Return a plan type from manifest trigger metadata, if any."""
    for manifest, patterns in compiled_triggers():
        for pattern in patterns:
            if pattern.search(message):
                return manifest.plan_type or None
    return None

def _detect_plan_type(message: str) -> str | None:
    """Return plan type string or None.

    Priority ordering: most specific patterns first.
    """
    manifest_plan_type = _detect_plan_type_from_manifests(message)
    if manifest_plan_type:
        return manifest_plan_type

    # 1. DE analysis (most specific)
    for pat in _DE_PATTERNS:
        if pat.search(message):
            return "run_de_pipeline"
    # 1b. Reconcile BAM workflows
    for pat in _RECONCILE_BAMS_PATTERNS:
        if pat.search(message):
            return "reconcile_bams"

    if _is_region_overlap_request(message):
        return "compare_region_overlaps"

    # 2. Enrichment analysis
    for pat in _ENRICHMENT_PATTERNS:
        if pat.search(message):
            return "run_enrichment"
    # 2b. XgenePy cis/trans analysis
    for pat in _XGENEPY_PATTERNS:
        if pat.search(message):
            return "run_xgenepy_analysis"
    # 3. Search + compare to local
    for pat in _SEARCH_COMPARE_LOCAL_PATTERNS:
        if pat.search(message):
            return "search_compare_to_local"
    # 4. Compare workflows (before generic compare)
    for pat in _COMPARE_WORKFLOW_PATTERNS:
        if pat.search(message):
            return "compare_workflows"
    # 5. Compare samples
    for pat in _COMPARE_PATTERNS:
        if pat.search(message):
            return "compare_samples"
    # 6. Download + analyze
    for pat in _DOWNLOAD_ANALYZE_PATTERNS:
        if pat.search(message):
            return "download_analyze"
    # 7. Parse + plot + interpret
    for pat in _PARSE_PLOT_PATTERNS:
        if pat.search(message):
            return "parse_plot_interpret"
    # 8. Remote stage workflow
    for pat in _REMOTE_STAGE_PATTERNS:
        if pat.search(message):
            return "remote_stage_workflow"
    # 9. Run workflow
    for pat in _RUN_WORKFLOW_PATTERNS:
        if pat.search(message):
            return "run_workflow"
    return None


def _is_summarize_results_request(message: str, conv_state: "ConversationState") -> bool:
    return bool(
        conv_state.work_dir
        and re.search(r"(?:summarize|interpret|explain)\s+(?:the\s+)?(?:results?|output|qc)", message, re.I)
    )
