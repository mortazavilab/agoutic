"""Parse and correct LLM response tags for chat_with_agent.

Extracts DATA_CALL, legacy ENCODE_CALL/ANALYSIS_CALL, [[PLOT:...]], and
[[APPROVAL_NEEDED]] tags from the LLM's raw output.  Applies fallback
corrections for common LLM hallucination patterns (wrong tag syntax,
Mistral native tool-call format, REST-style TOOL_CALL tags).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from atlas.config import get_all_fallback_patterns
from common.logging_config import get_logger
from cortex.llm_validators import _parse_tag_params

logger = get_logger(__name__)

# --- Compiled patterns (module-level for reuse) ---

_TRIGGER_TAG = "[[APPROVAL_NEEDED]]"

_APPROVAL_SKILLS = frozenset({
    "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
    "analyze_local_sample", "download_files", "remote_execution",
})

# Unified DATA_CALL: (source_type, source_key, tool, params)
DATA_CALL_PATTERN = re.compile(
    r'\[\[DATA_CALL:\s*(?:(consortium|service)=(\w+)),\s*tool=(\w+)(?:,\s*(.+))?\]\]'
)

LEGACY_ENCODE_PATTERN = re.compile(
    r'\[\[ENCODE_CALL:\s*([\w_]+)(?:,\s*([^\]]+))?\]\]'
)

LEGACY_ANALYSIS_PATTERN = re.compile(
    r'\[\[ANALYSIS_CALL:\s*(\w+),\s*run_uuid=([a-f0-9-]+)\]\]'
)

# PLOT tags — non-greedy with DOTALL so ] inside params doesn't break
PLOT_TAG_PATTERN = re.compile(r'\[\[PLOT:\s*(.*?)\]\]', re.DOTALL)

SKILL_SWITCH_PATTERN = re.compile(r'\[\[SKILL_SWITCH_TO:\s*\w+\]\]')

# Mistral-native [TOOL_CALLS]DATA_CALL[ARGS]{json}
_MISTRAL_TC_PATTERN = re.compile(
    r'\[TOOL_CALLS\]\s*DATA_CALL\s*\[ARGS\]\s*(\{[^}]+\})'
)

# Mistral inline [TOOL_CALLS]DATA_CALL: key=value, ...
_MISTRAL_INLINE_PATTERN = re.compile(
    r'\[TOOL_CALLS\]\s*DATA_CALL:\s*(.+?)(?:\n|$)'
)

# REST-style [[TOOL_CALL: GET /analysis/...?params]]
_TOOL_CALL_PATTERN = re.compile(
    r'\[\[TOOL_CALL:\s*(?:GET\s+)?/analysis/[^?]*\?([^\]]+)\]\]'
)

# Plot keywords the user might write
_PLOT_KEYWORDS = frozenset({
    "plot", "chart", "pie", "histogram", "scatter", "bar chart",
    "box plot", "heatmap", "visualize", "graph", "distribution",
})

_PLOT_CODE_RE = re.compile(
    r'```python.*?(?:matplotlib|plt\.|plotly|px\.|fig\.|\.pie|\.bar|\.hist|\.scatter)',
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class ParsedLLMResponse:
    """Result of parsing and correcting the LLM's raw response."""

    corrected_response: str
    clean_markdown: str
    data_call_matches: list = field(default_factory=list)
    legacy_encode_matches: list = field(default_factory=list)
    legacy_analysis_matches: list = field(default_factory=list)
    plot_specs: list = field(default_factory=list)
    needs_approval: bool = False
    fallback_fixes_applied: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _convert_tool_call_to_data_call(m: re.Match) -> str:
    query_string = m.group(1)
    params: dict[str, str] = {}
    for part in query_string.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()
    url_path = m.group(0).lower()
    if "summary" in url_path:
        tool = "get_analysis_summary"
    elif "categori" in url_path:
        tool = "categorize_job_files"
    elif "file" in url_path:
        tool = "list_job_files"
    else:
        tool = "get_analysis_summary"
    param_str = ", ".join(f"{k}={v}" for k, v in params.items())
    return f"[[DATA_CALL: service=analyzer, tool={tool}, {param_str}]]"


def _convert_mistral_tool_call(m: re.Match) -> str:
    try:
        payload = json.loads(m.group(1))
        source = payload.pop("service", payload.pop("consortium", "edgepython"))
        tool = payload.pop("tool", "")
        if not tool:
            return m.group(0)
        source_type = "consortium" if source == "encode" else "service"
        param_str = ", ".join(f"{k}={v}" for k, v in payload.items())
        tag = f"[[DATA_CALL: {source_type}={source}, tool={tool}"
        if param_str:
            tag += f", {param_str}"
        tag += "]]"
        return tag
    except (json.JSONDecodeError, TypeError):
        return m.group(0)


def _convert_mistral_inline(m: re.Match) -> str:
    return f"[[DATA_CALL: {m.group(1).rstrip()}]]"


def _detect_chart_type(message: str) -> str:
    msg = message.lower()
    if "pie" in msg:
        return "pie"
    if "scatter" in msg:
        return "scatter"
    if "bar" in msg:
        return "bar"
    if "box" in msg:
        return "box"
    if "heatmap" in msg or "correlation" in msg:
        return "heatmap"
    if "histogram" in msg or "distribution" in msg:
        return "histogram"
    return "bar"


def _detect_x_column(message: str) -> str | None:
    by_match = re.search(r'\bby\s+(\w+)', message, re.IGNORECASE)
    if by_match and not re.match(r'DF\d+', by_match.group(1), re.IGNORECASE):
        return by_match.group(1)
    of_match = re.search(r'\b(?:of|for)\s+(\w+)', message, re.IGNORECASE)
    if of_match and not re.match(r'DF\d+', of_match.group(1), re.IGNORECASE):
        return of_match.group(1)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_response_corrections(raw_response: str) -> tuple[str, int]:
    """Apply all fallback tag corrections the LLM might need.

    Returns ``(corrected_response, fixes_applied_count)``.
    """
    all_fallback_patterns = get_all_fallback_patterns()
    corrected = raw_response
    fixes = 0

    for pattern, replacement in all_fallback_patterns.items():
        before = corrected
        corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
        if before != corrected:
            fixes += 1

    # REST-style TOOL_CALL → DATA_CALL
    before = corrected
    corrected = _TOOL_CALL_PATTERN.sub(_convert_tool_call_to_data_call, corrected)
    if corrected != before:
        fixes += 1
        logger.warning("Converted [[TOOL_CALL:...]] to [[DATA_CALL:...]]")

    # Mistral native [TOOL_CALLS]DATA_CALL[ARGS]{json}
    before = corrected
    corrected = _MISTRAL_TC_PATTERN.sub(_convert_mistral_tool_call, corrected)
    if corrected != before:
        fixes += 1
        logger.warning("Converted [TOOL_CALLS]...[ARGS]{json} to [[DATA_CALL:...]]")

    # Mistral inline [TOOL_CALLS]DATA_CALL: …
    before = corrected
    corrected = _MISTRAL_INLINE_PATTERN.sub(_convert_mistral_inline, corrected)
    if corrected != before:
        fixes += 1
        logger.warning("Converted [TOOL_CALLS]DATA_CALL:... to [[DATA_CALL:...]]")

    if fixes:
        logger.warning("Applied fallback tag fixes to LLM response", count=fixes)

    return corrected, fixes


def parse_data_tags(response: str) -> tuple[list, list, list]:
    """Parse DATA_CALL, legacy ENCODE_CALL, and legacy ANALYSIS_CALL tags.

    Returns ``(data_call_matches, legacy_encode_matches, legacy_analysis_matches)``.
    """
    return (
        list(DATA_CALL_PATTERN.finditer(response)),
        list(LEGACY_ENCODE_PATTERN.finditer(response)),
        list(LEGACY_ANALYSIS_PATTERN.finditer(response)),
    )


def parse_approval_tag(response: str, active_skill: str) -> tuple[bool, str]:
    """Check for ``[[APPROVAL_NEEDED]]`` and suppress for non-job skills.

    Returns ``(needs_approval, cleaned_response)``.
    """
    needs_approval = _TRIGGER_TAG in response
    cleaned = response
    if needs_approval and active_skill not in _APPROVAL_SKILLS:
        logger.warning(
            "Suppressing spurious APPROVAL_NEEDED for non-job skill",
            skill=active_skill,
        )
        needs_approval = False
        cleaned = cleaned.replace(_TRIGGER_TAG, "").strip()
    return needs_approval, cleaned


def parse_plot_tags(response: str) -> list[dict]:
    """Parse ``[[PLOT:...]]`` tags from the response.

    Returns a list of plot spec dicts, each with at least ``type`` and
    ``df_id`` keys (``df_id`` may be ``None`` if unresolvable).
    """
    matches = list(PLOT_TAG_PATTERN.finditer(response, re.DOTALL))
    specs: list[dict] = []
    for pm in matches:
        raw_inner = pm.group(1)
        params = _parse_tag_params(raw_inner)
        # Natural-language fallback inside the tag
        if not params.get("df"):
            nl = raw_inner
            for ct in ("histogram", "scatter", "bar", "box", "heatmap", "pie"):
                if ct in nl.lower():
                    params.setdefault("type", ct)
                    break
            nl_df = re.search(r'\bDF\s*(\d+)\b', nl, re.IGNORECASE)
            if nl_df:
                params["df"] = f"DF{nl_df.group(1)}"
            x_m = re.search(
                r'\b(\w+)\s+(?:on\s+the\s+)?x[- ]axis', nl, re.IGNORECASE
            ) or re.search(
                r'\bx\s*[=:]\s*([\w][\w ]*?)(?:,|\.|\band\b|$)', nl, re.IGNORECASE
            )
            if x_m:
                params.setdefault("x", x_m.group(1).strip())
            y_m = re.search(
                r'\b(\w+)\s+(?:on\s+the\s+)?y[- ]axis', nl, re.IGNORECASE
            ) or re.search(
                r'\by\s*[=:]\s*([\w][\w ]*?)(?:,|\.|\band\b|$)', nl, re.IGNORECASE
            )
            if y_m:
                params.setdefault("y", y_m.group(1).strip())
        # Normalise df_id
        df_ref = params.get("df", "")
        df_id_m = re.match(r'(?:DF)?\s*(\d+)', df_ref, re.IGNORECASE)
        params["df_id"] = int(df_id_m.group(1)) if df_id_m else None
        params.setdefault("type", "histogram")
        specs.append(params)
    if specs:
        logger.info(
            "Parsed PLOT tags", count=len(specs),
            specs=[{"type": s.get("type"), "df_id": s.get("df_id")} for s in specs],
        )
    return specs


def override_hallucinated_df_refs(
    plot_specs: list[dict],
    user_message: str,
    latest_dataframe: str | None,
) -> None:
    """Fix plot specs that reference stale DF IDs when user said 'plot this'."""
    user_explicit_df = re.search(r'\bDF\s*(\d+)\b', user_message, re.IGNORECASE)
    if not plot_specs or user_explicit_df or not latest_dataframe:
        return
    latest_m = re.match(r'DF(\d+)', latest_dataframe)
    if not latest_m:
        return
    latest_id = int(latest_m.group(1))
    for ps in plot_specs:
        if ps.get("df_id") is not None and ps["df_id"] != latest_id:
            logger.warning(
                "Overriding PLOT df_id with latest DF",
                llm_df_id=ps["df_id"], latest_df_id=latest_id,
            )
            ps["df_id"] = latest_id
            ps["df"] = f"DF{latest_id}"


def apply_plot_code_fallback(
    plot_specs: list[dict],
    user_message: str,
    corrected_response: str,
    injected_dfs: dict,
    latest_dataframe: str | None,
    extract_plot_style_params,
) -> tuple[list[dict], str]:
    """When LLM wrote Python plot code instead of [[PLOT:...]], auto-generate spec.

    Returns ``(updated_plot_specs, updated_response)``.
    """
    user_wants_plot = any(kw in user_message.lower() for kw in _PLOT_KEYWORDS)
    has_code_plot = bool(_PLOT_CODE_RE.search(corrected_response))
    specs_invalid = plot_specs and all(s.get("df_id") is None for s in plot_specs)
    specs_missing = user_wants_plot and not plot_specs

    if not (
        user_wants_plot
        and (has_code_plot or specs_invalid or specs_missing)
        and not (plot_specs and any(s.get("df_id") is not None for s in plot_specs))
    ):
        return plot_specs, corrected_response

    logger.warning("LLM wrote Python plot code instead of [[PLOT:...]] tag — auto-generating")

    auto_df_id = None
    df_ref_in_msg = re.search(r'\bDF\s*(\d+)\b', user_message, re.IGNORECASE)
    if df_ref_in_msg:
        auto_df_id = int(df_ref_in_msg.group(1))
    else:
        df_ref_in_resp = re.search(r'\bDF\s*(\d+)\b', corrected_response, re.IGNORECASE)
        if df_ref_in_resp:
            auto_df_id = int(df_ref_in_resp.group(1))
        elif injected_dfs:
            for ij_data in injected_dfs.values():
                ij_id = ij_data.get("metadata", {}).get("df_id")
                if ij_id is not None:
                    auto_df_id = ij_id
                    break
        if auto_df_id is None and latest_dataframe:
            ld_m = re.match(r'DF(\d+)', latest_dataframe)
            if ld_m:
                auto_df_id = int(ld_m.group(1))

    auto_type = _detect_chart_type(user_message)
    auto_x = _detect_x_column(user_message)

    if auto_df_id is not None:
        auto_spec: dict = {
            "type": auto_type,
            "df_id": auto_df_id,
            "df": f"DF{auto_df_id}",
        }
        if auto_x:
            auto_spec["x"] = auto_x
        auto_spec.update(extract_plot_style_params(user_message))
        plot_specs.append(auto_spec)
        logger.info("Auto-generated PLOT spec from code fallback", spec=auto_spec)

    # Strip the code block and boilerplate from the markdown
    corrected_response = re.sub(
        r'```python.*?```', '', corrected_response, flags=re.DOTALL
    ).strip()
    corrected_response = re.sub(
        r'\n*(?:Explanation|Output|Note|Here is|The (?:pie|bar|scatter|histogram|box) chart).*$',
        '', corrected_response, flags=re.DOTALL | re.IGNORECASE
    ).strip()

    return plot_specs, corrected_response


def suppress_tags_for_plot_command(
    user_message: str,
    plot_specs: list[dict],
    data_call_matches: list,
    legacy_encode_matches: list,
    legacy_analysis_matches: list,
    needs_approval: bool,
    corrected_response: str,
) -> tuple[list, list, list, bool, str]:
    """When user wants a plot and we have valid specs, suppress other tags.

    Returns ``(data_call_matches, legacy_encode_matches,
    legacy_analysis_matches, needs_approval, corrected_response)``.
    """
    user_wants_plot = any(kw in user_message.lower() for kw in _PLOT_KEYWORDS)
    if not (user_wants_plot and plot_specs and any(s.get("df_id") is not None for s in plot_specs)):
        return data_call_matches, legacy_encode_matches, legacy_analysis_matches, needs_approval, corrected_response

    if data_call_matches or legacy_encode_matches or legacy_analysis_matches or needs_approval:
        logger.warning(
            "Plot-command override: suppressing LLM DATA_CALL/APPROVAL tags",
            data_calls=len(data_call_matches),
            legacy_encode=len(legacy_encode_matches),
            needs_approval=needs_approval,
        )
    corrected_response = DATA_CALL_PATTERN.sub('', corrected_response).strip()
    corrected_response = LEGACY_ENCODE_PATTERN.sub('', corrected_response).strip()
    corrected_response = LEGACY_ANALYSIS_PATTERN.sub('', corrected_response).strip()
    corrected_response = corrected_response.replace(_TRIGGER_TAG, "").strip()
    return [], [], [], False, corrected_response


def clean_tags_from_markdown(
    text: str,
    plot_specs: list | None = None,
) -> str:
    """Remove all LLM tag syntax from text, leaving user-visible content."""
    cleaned = text.replace(_TRIGGER_TAG, "").strip()
    cleaned = SKILL_SWITCH_PATTERN.sub('', cleaned).strip()
    cleaned = DATA_CALL_PATTERN.sub('', cleaned).strip()
    cleaned = LEGACY_ENCODE_PATTERN.sub('', cleaned).strip()
    cleaned = LEGACY_ANALYSIS_PATTERN.sub('', cleaned).strip()
    cleaned = PLOT_TAG_PATTERN.sub('', cleaned).strip()

    for pattern in get_all_fallback_patterns():
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()

    # If only a PLOT tag was emitted, insert a brief placeholder
    if not cleaned and plot_specs:
        ps0 = plot_specs[0]
        chart_type = ps0.get("type", "chart").replace("_", " ")
        df_label = ps0.get("df", "")
        if not df_label and ps0.get("df_id") is not None:
            df_label = f"DF{ps0['df_id']}"
        cleaned = (
            f"Here is the {chart_type} for **{df_label}**:"
            if df_label
            else f"Here is the {chart_type}:"
        )

    return cleaned


def fix_hallucinated_accessions(clean_markdown: str, user_message: str) -> str:
    """Replace wrong ENCSR accessions the LLM may have hallucinated."""
    encsr_in_user = re.findall(r'(ENCSR[A-Z0-9]{6})', user_message, re.IGNORECASE)
    if len(encsr_in_user) != 1:
        return clean_markdown
    correct = encsr_in_user[0].upper()
    encsr_in_reply = re.findall(r'(ENCSR[A-Z0-9]{6})', clean_markdown, re.IGNORECASE)
    wrong = {a.upper() for a in encsr_in_reply if a.upper() != correct}
    for w in wrong:
        logger.warning("Fixing hallucinated ENCSR in LLM text",
                       hallucinated=w, correct=correct)
        clean_markdown = re.sub(re.escape(w), correct, clean_markdown, flags=re.IGNORECASE)
    return clean_markdown


def user_wants_plot(message: str) -> bool:
    """Return True if the user's message indicates a plot/chart request."""
    return any(kw in message.lower() for kw in _PLOT_KEYWORDS)
