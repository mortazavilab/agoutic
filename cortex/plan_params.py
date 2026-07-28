"""
Parameter extraction for plan generation.

Parses user messages and conversation state to build param dicts
consumed by plan templates.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from common.logging_config import get_logger
from cortex.config import GENOME_ALIASES, default_haplotype_vcf_for_reference
from cortex import user_jail

if TYPE_CHECKING:
    from cortex.schemas import ConversationState

logger = get_logger(__name__)

_PROJECT_WORKFLOW_REF_RE = re.compile(
    r"\b(?P<project>[a-z0-9][a-z0-9_-]*)\s*:\s*(?P<workflow>workflow\d+)\b",
    re.IGNORECASE,
)
_BED_PATH_RE = re.compile(r"(?P<path>(?:/|~|\.)[^\s,;]+\.bed)\b", re.IGNORECASE)
_PORE_C_INPUT_PATH_RE = re.compile(
    r"(?P<path>(?:/|~|\.)[^\s,;]+?\.(?:bam|fastq|fq)(?:\.gz)?)\b",
    re.IGNORECASE,
)
_PORE_C_HINTED_INPUT_RE = re.compile(
    r"\b(?P<input_type>bam|fastq|fq)\b(?:\s+(?:file|files|input|inputs|reads?|directory|dir))?\s*(?:at|in|from|=|:)?\s*(?P<path>(?:/|~|\.)\S+)",
    re.IGNORECASE,
)
_PORE_C_REFERENCE_PATH_RE = re.compile(
    r"(?P<path>(?:/|~|\.)[^\s,;]+?\.(?:fa|fasta|fna)(?:\.gz)?)\b",
    re.IGNORECASE,
)
_PORE_C_VCF_PATH_RE = re.compile(
    r"(?P<path>(?:/|~|\.)[^\s,;]+?\.vcf(?:\.gz)?)\b",
    re.IGNORECASE,
)
_HAPLOTYPE_WORKFLOW_RE = re.compile(r"\b(workflow\d+)\b", re.IGNORECASE)
_HAPLOTYPE_MODE_RE = re.compile(r"\b(DNA|RNA|cDNA)\b", re.IGNORECASE)
_HAPLOTYPE_VCF_HINT_RE = re.compile(
    r"(?:with\s+file|using|with)\s+(?P<path>(?:/|~|\.)?[^\s,;]+?\.vcf(?:\.gz)?)\b",
    re.IGNORECASE,
)
_HAPLOTYPE_VCF_SAMPLE_FLAG_RE = re.compile(r"--vcf-sample\s+(?P<sample>[^\s,;]+(?:,[^\s,;]+)?)", re.IGNORECASE)
_HAPLOTYPE_FOUNDER_PAIR_RE = re.compile(
    r"\bfounders?\s+(?P<first>[A-Za-z0-9/_\- ]+?)\s*(?:,|and|vs)\s*(?P<second>[A-Za-z0-9/_\- ]+?)(?=\s+workflow\d+\b|\s+(?:with|using|from|in|on)\b|[.,;]|$)",
    re.IGNORECASE,
)
_HAPLOTYPE_SAMPLE_PAIR_RE = re.compile(
    r"\bsample\s+(?P<first>[A-Za-z0-9/_\- ]+?)\s*(?:,|and|vs)\s*(?P<second>[A-Za-z0-9/_\- ]+?)(?=\s+workflow\d+\b|\s+(?:with|using|from|in|on)\b|[.,;]|$)",
    re.IGNORECASE,
)
_HAPLOTYPE_MOUSE_SAMPLE_RE = re.compile(
    r"\bhaplotype\s+(?:mouse|mm39)(?:\s+(?:DNA|RNA|cDNA))?\s+sample\s+(?P<sample>.+?)(?=\s+workflow\d+\b|\s+(?:with|using|from|in|on)\b|[.,;]|$)",
    re.IGNORECASE,
)
_HAPLOTYPE_MOUSE_BETWEEN_PAIR_RE = re.compile(
    r"\bhaplotype\s+(?:mouse|mm39)\b.*?\bbetween\s+(?P<first>[A-Za-z0-9/_\- ]+?)\s+(?:and|vs)\s+(?P<second>[A-Za-z0-9/_\- ]+?)(?=\s+workflow\d+\b|\s+(?:with|using|from|in|on)\b|[.,;]|$)",
    re.IGNORECASE,
)
_HAPLOTYPE_MOUSE_VS_PAIR_RE = re.compile(
    r"\bhaplotype\s+(?:mouse|mm39)\b.*?\b(?P<first>[A-Za-z0-9/_\- ]+?)\s+vs\s+(?P<second>[A-Za-z0-9/_\- ]+?)(?=\s+workflow\d+\b|\s+(?:with|using|from|in|on)\b|[.,;]|$)",
    re.IGNORECASE,
)
_HAPLOTYPE_F1_TOKEN_RE = re.compile(r"\b(?P<sample>[A-Za-z0-9/_-]*F1)\b", re.IGNORECASE)
_GENOME_ALIAS_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(alias) for alias in GENOME_ALIASES), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
) if GENOME_ALIASES else re.compile(r"$^")
_DOGME_BATCH_SAMPLE_RE = re.compile(
    r"\b(?P<sample_name>[A-Za-z0-9_-]+)\s*(?:=|:)\s*(?P<input_directory>(?:/|~|\.)[^\s,;]+)",
    re.IGNORECASE,
)
_PORE_C_SAMPLE_SHEET_RE = re.compile(
    r"(?:sample[_ ]sheet|samplesheet)\s+(?:at|in|from|path)?\s*[=:]?\s*(?P<path>\S+\.(?:csv|tsv|txt))",
    re.IGNORECASE,
)

_MOUSE_FOUNDER_ALIAS_TO_CANONICAL = {
    "ref": "C57BL_6J",
    "b6": "C57BL_6J",
    "c57bl6": "C57BL_6J",
    "c57bl6j": "C57BL_6J",
    "aj": "A_J",
    "a": "A_J",
    "129s1": "129S1_SvImJ",
    "129s1svimj": "129S1_SvImJ",
    "nod": "NOD_ShiLtJ",
    "nodshiltj": "NOD_ShiLtJ",
    "nzo": "NZO_HlLtJ",
    "nzohlltj": "NZO_HlLtJ",
    "cast": "CAST_EiJ",
    "casteij": "CAST_EiJ",
    "pwk": "PWK_PhJ",
    "pwkphj": "PWK_PhJ",
    "wsb": "WSB_EiJ",
    "wsbeij": "WSB_EiJ",
}
_MOUSE_FOUNDER_ORDER = ["C57BL_6J", "A_J", "129S1_SvImJ", "NOD_ShiLtJ", "NZO_HlLtJ", "CAST_EiJ", "PWK_PhJ", "WSB_EiJ"]
_MOUSE_FOUNDER_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MOUSE_FOUNDER_SEPARATOR_RE = re.compile(r"[\s/_-]+")
_MOUSE_F1_SUFFIX_RE = re.compile(r"f1$", re.IGNORECASE)
_MOUSE_FOUNDER_F1_KEYS = sorted(
    _MOUSE_FOUNDER_ALIAS_TO_CANONICAL.items(),
    key=lambda item: (-len(item[0]), item[0]),
)


def _collapse_mouse_founder_token(value: str) -> str:
    return _MOUSE_FOUNDER_NON_ALNUM_RE.sub("", str(value or "").strip().lower())


def _mouse_founder_lookup_keys(value: str) -> tuple[str, ...]:
    raw_value = str(value or "").strip().lower()
    if not raw_value:
        return ()
    keys: list[str] = []
    collapsed = _collapse_mouse_founder_token(raw_value)
    if collapsed:
        keys.append(collapsed)
    prefix = _collapse_mouse_founder_token(_MOUSE_FOUNDER_SEPARATOR_RE.split(raw_value, maxsplit=1)[0])
    if prefix and prefix not in keys:
        keys.append(prefix)
    return tuple(keys)


def _resolve_mouse_founder_alias(value: str) -> str | None:
    for key in _mouse_founder_lookup_keys(value):
        canonical = _MOUSE_FOUNDER_ALIAS_TO_CANONICAL.get(key)
        if canonical:
            return canonical
    return None


def _parse_mouse_founder_f1(value: str) -> list[str] | None:
    raw_value = str(value or "").strip()
    if not raw_value or not _MOUSE_F1_SUFFIX_RE.search(raw_value):
        return None

    body = _MOUSE_F1_SUFFIX_RE.sub("", raw_value).strip()
    if not body:
        return None

    parts = [token for token in _MOUSE_FOUNDER_SEPARATOR_RE.split(body) if token]
    if len(parts) == 2:
        first = _resolve_mouse_founder_alias(parts[0])
        second = _resolve_mouse_founder_alias(parts[1])
        if first and second and first != second:
            return [label for label in _MOUSE_FOUNDER_ORDER if label in {first, second}]
        return None

    collapsed = _collapse_mouse_founder_token(body)
    if not collapsed:
        return None

    for prefix, first in _MOUSE_FOUNDER_F1_KEYS:
        if not collapsed.startswith(prefix):
            continue
        remainder = collapsed[len(prefix):]
        if not remainder:
            continue
        second = _MOUSE_FOUNDER_ALIAS_TO_CANONICAL.get(remainder)
        if second and second != first:
            return [label for label in _MOUSE_FOUNDER_ORDER if label in {first, second}]
    return None


def _clean_haplotype_founder_token(value: str) -> str:
    return str(value or "").strip().strip('"').strip("'").rstrip(".,;:!?")


def _extract_haplotype_founder_samples(message: str) -> list[str]:
    requested_tokens: list[str] = []
    for match in _HAPLOTYPE_VCF_SAMPLE_FLAG_RE.finditer(message):
        requested_tokens.extend(
            _clean_haplotype_founder_token(part)
            for part in match.group("sample").split(",")
            if _clean_haplotype_founder_token(part)
        )

    for pattern in (
        _HAPLOTYPE_FOUNDER_PAIR_RE,
        _HAPLOTYPE_SAMPLE_PAIR_RE,
        _HAPLOTYPE_MOUSE_BETWEEN_PAIR_RE,
        _HAPLOTYPE_MOUSE_VS_PAIR_RE,
    ):
        pair_match = pattern.search(message)
        if not pair_match:
            continue
        requested_tokens.extend(
            [
                _clean_haplotype_founder_token(pair_match.group("first")),
                _clean_haplotype_founder_token(pair_match.group("second")),
            ]
        )
        break

    mouse_sample_match = _HAPLOTYPE_MOUSE_SAMPLE_RE.search(message)
    if mouse_sample_match:
        requested_tokens.append(_clean_haplotype_founder_token(mouse_sample_match.group("sample")))

    for match in _HAPLOTYPE_F1_TOKEN_RE.finditer(message):
        token = _clean_haplotype_founder_token(match.group("sample"))
        if token and token not in requested_tokens:
            requested_tokens.append(token)

    resolved: list[str] = []
    for token in requested_tokens:
        founder_pair = _parse_mouse_founder_f1(token)
        if founder_pair:
            for founder in founder_pair:
                if founder not in resolved:
                    resolved.append(founder)
            continue
        founder = _resolve_mouse_founder_alias(token)
        if founder and founder not in resolved:
            resolved.append(founder)

    return [label for label in _MOUSE_FOUNDER_ORDER if label in resolved]


def _extract_mentioned_reference_genomes(message: str) -> list[str]:
    mentioned_references: list[str] = []
    for match in _GENOME_ALIAS_RE.findall(message):
        canonical = GENOME_ALIASES.get(str(match).strip().lower())
        if canonical and canonical not in mentioned_references:
            mentioned_references.append(canonical)
    return mentioned_references


def _clean_overlap_display_label(label_text: str) -> str:
    cleaned = str(label_text or "").strip().strip('"').strip("'")
    return cleaned.strip(" ,;:.!?")


def _extract_overlap_label_override(message: str, sample_key: str) -> str | None:
    if not message:
        return None

    other_key = "b" if sample_key == "a" else "a"
    stop_pattern = rf"(?=\s*(?:,|;|\.)\s*|\s+and\s+sample\s*{other_key}\b|\s+and\s+title\b|\s+title\b|$)"
    patterns = (
        rf"\bsample\s*{sample_key}(?:\s+(?:label|name))?\s*(?:=|:|as|is|called|named)\s*(.+?){stop_pattern}",
        rf"\brename\s+sample\s*{sample_key}\s+to\s*(.+?){stop_pattern}",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if not match:
            continue
        cleaned = _clean_overlap_display_label(match.group(1))
        if cleaned:
            return cleaned
    return None


def _extract_overlap_label_overrides(message: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    sample_a_label = _extract_overlap_label_override(message, "a")
    sample_b_label = _extract_overlap_label_override(message, "b")
    if sample_a_label:
        overrides["sample_a_label"] = sample_a_label
    if sample_b_label:
        overrides["sample_b_label"] = sample_b_label
    return overrides


def _extract_overlap_plot_title(message: str) -> str | None:
    if not message:
        return None

    patterns = (
        r"\btitle\s+it\s+(?:as\s+)?(.+?)(?:[.;]|$)",
        r"\bwith\s+title\s*(?:=|:)?\s*(.+?)(?:[.;]|$)",
        r"\bplot\s+title\s*(?:=|:|is|as)?\s*(.+?)(?:[.;]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if not match:
            continue
        cleaned = _clean_overlap_display_label(match.group(1))
        if cleaned:
            return cleaned
    return None


def _project_owner_root(project_dir: str, default_work_dir: str = "") -> str:
    candidate = (project_dir or "").strip() or (default_work_dir or "").strip()
    if not candidate:
        return ""
    candidate = candidate.rstrip("/")
    if re.search(r"/workflow\d+$", candidate, re.IGNORECASE):
        candidate = candidate.rsplit("/", 1)[0]
    parent = Path(candidate).parent
    if not str(parent) or str(parent) == ".":
        return ""
    return str(parent)


def _resolve_project_workflow_ref(ref: str, project_dir: str, default_work_dir: str = "") -> str:
    workflow_dir = _resolve_project_workflow_dir(ref, project_dir, default_work_dir)
    if workflow_dir == ref:
        return ref
    return f"{workflow_dir}/openChromatin"


def _resolve_project_workflow_dir(ref: str, project_dir: str, default_work_dir: str = "") -> str:
    match = _PROJECT_WORKFLOW_REF_RE.fullmatch((ref or "").strip())
    if not match:
        return ref
    owner_root = _project_owner_root(project_dir, default_work_dir)
    if not owner_root:
        return ref
    return f"{owner_root.rstrip('/')}/{match.group('project')}/{match.group('workflow')}"


def _resolve_existing_path(path_value: str, conv_state: "ConversationState", project_dir: str) -> str:
    path_text = str(path_value or "").strip().rstrip(".,;:!?")
    if not path_text:
        return path_text
    expanded = os.path.expanduser(path_text)
    if os.path.isabs(expanded):
        return expanded
    work_dir = getattr(conv_state, "work_dir", "") or project_dir
    if work_dir:
        return str((Path(work_dir) / expanded).resolve())
    return expanded


def _pore_c_allowed_root(conv_state: "ConversationState", project_dir: str) -> Path | None:
    users_root = (user_jail.AGOUTIC_DATA / "users").resolve()
    for raw_root in (getattr(conv_state, "work_dir", None), project_dir):
        if not raw_root:
            continue
        candidate = Path(os.path.expanduser(str(raw_root))).resolve()
        try:
            relative = candidate.relative_to(users_root)
        except ValueError:
            continue
        if relative.parts:
            return users_root / relative.parts[0]
    return None


def _resolve_pore_c_jailed_path(path_value: str, conv_state: "ConversationState", project_dir: str) -> str:
    resolved = Path(_resolve_existing_path(path_value, conv_state, project_dir)).expanduser().resolve()

    try:
        user_jail._ensure_within_jail(resolved)
    except PermissionError as exc:
        raise ValueError(f"Pore-C path must stay inside the user jail: {resolved}") from exc

    allowed_root = _pore_c_allowed_root(conv_state, project_dir)
    if allowed_root is not None:
        try:
            resolved.relative_to(allowed_root.resolve())
        except ValueError as exc:
            raise ValueError(f"Pore-C path must stay inside the user jail: {resolved}") from exc

    return str(resolved)


def _trim_path_token(path_value: str) -> str:
    return str(path_value or "").strip().strip('"').strip("'").rstrip(".,;:!?")


def _derive_sample_name_from_input_path(path_value: str) -> str:
    file_name = Path(path_value).name
    lowered = file_name.lower()
    for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq", ".bam"):
        if lowered.endswith(suffix):
            return file_name[: -len(suffix)] or "pore_c_sample"
    return Path(file_name).stem or "pore_c_sample"


def _pore_c_output_root(input_path: str, conv_state: "ConversationState", project_dir: str) -> str:
    output_root = _project_output_root(project_dir, conv_state)
    if output_root:
        return output_root

    normalized_input = _trim_path_token(input_path)
    if not normalized_input:
        return ""

    input_candidate = Path(os.path.expanduser(normalized_input))
    parent = input_candidate if input_candidate.is_dir() else input_candidate.parent
    return str(parent)


def _pore_c_output_flags(message: str) -> dict[str, bool]:
    def _alias_pattern(alias: str) -> str:
        parts = [part for part in re.split(r"[\s_-]+", alias.strip()) if part]
        return r"[\s_-]+".join(re.escape(part) for part in parts)

    def _extract_flag(default: bool, *aliases: str) -> bool:
        if not aliases:
            return default
        for alias in aliases:
            alias_pattern = _alias_pattern(alias)
            if re.search(rf"\b(?:no|without|disable|disabled|skip)\s+{alias_pattern}\b", message, re.IGNORECASE):
                return False
        for alias in aliases:
            alias_pattern = _alias_pattern(alias)
            if re.search(rf"\b(?:with|enable|enabled|generate|include|output)?\s*{alias_pattern}\b", message, re.IGNORECASE):
                return True
        return default

    flags = {
        "pairs": _extract_flag(True, "pairs"),
        "mcool": _extract_flag(True, "mcool", "cooler", "contact map"),
        "hi_c": _extract_flag(False, "hi c", "hic"),
        "bed": _extract_flag(False, "bed"),
        "chromunity": _extract_flag(False, "chromunity"),
        "coverage": _extract_flag(False, "coverage"),
        "paired_end": _extract_flag(False, "paired end", "paired_end"),
    }
    if flags["bed"]:
        flags["paired_end"] = True
    return flags


def _extract_pore_c_sample_name(message: str) -> str | None:
    patterns = (
        r"\bsample(?:\s+name)?\s+([A-Za-z0-9_.-]+)",
        r"\bsample(?:\s+name)?\s*(?:=|:|is|called|named)\s*([A-Za-z0-9_.-]+)",
        r"\bcalled\s+([A-Za-z0-9_.-]+)",
        r"\bnamed\s+([A-Za-z0-9_.-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if not match:
            continue
        sample_name = match.group(1).strip().strip(".,;:!?")
        if sample_name:
            return sample_name
    return None


def _extract_pore_c_cutter(message: str) -> str | None:
    match = re.search(
        r"\b(?:cutter|enzyme|restriction\s+enzyme)\s*(?:=|:|is)?\s*([A-Za-z0-9_.-]+)",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip().strip(".,;:!?")


def _project_output_root(project_dir: str, conv_state: "ConversationState") -> str:
    if project_dir:
        return project_dir
    work_dir = str(getattr(conv_state, "work_dir", "") or "").strip().rstrip("/")
    if re.search(r"/workflow\d+$", work_dir, re.IGNORECASE):
        return work_dir.rsplit("/", 1)[0]
    return work_dir


def _next_project_workflow_dir(project_dir: str) -> str:
    root = Path(project_dir).expanduser()
    if not root.exists() or not root.is_dir():
        return str(root / "workflow1")
    highest = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        match = re.fullmatch(r"workflow(\d+)", child.name, re.IGNORECASE)
        if not match:
            continue
        highest = max(highest, int(match.group(1)))
    return str(root / f"workflow{highest + 1}")


# ---------------------------------------------------------------------------
# Plot selection heuristic
# ---------------------------------------------------------------------------

def _select_plot_type(message: str) -> str:
    """Keyword-based chart type selection from the user's request."""
    msg = message.lower()
    if any(w in msg for w in ("volcano", "de plot", "differential expression plot")):
        return "volcano"
    if "upset" in msg:
        return "upset"
    if "venn" in msg:
        return "venn"
    if "violin" in msg:
        return "violin"
    if "strip" in msg:
        return "strip"
    if re.search(r"\bline\s+(?:chart|plot|graph)\b", msg):
        return "line"
    if re.search(r"\barea\s+(?:chart|plot|graph)\b", msg):
        return "area"
    if any(w in msg for w in ("heatmap", "heat map", "cluster")):
        return "heatmap"
    if any(w in msg for w in ("box", "boxplot", "box plot", "distribution")):
        return "box"
    if any(w in msg for w in ("pie", "proportion", "fraction", "percentage")):
        return "pie"
    if any(w in msg for w in ("scatter", "correlation", "xy")):
        return "scatter"
    if any(w in msg for w in ("histogram", "hist", "frequency")):
        return "histogram"
    # Default: bar chart for categorical / count data
    return "bar"


_DE_TRAILING_CONTEXT = (
    r"(?=(?:\s+(?:from|using|on|in)\b"
    r"|\s+(?:at|by)\s+(?:gene|transcript)\s+level\b"
    r"|\s+(?:with|using)\s+(?:exact[_ ]test|qlf|glm|quasi)\b"
    r"|$))"
)

_DE_LABELED_GROUP_RE = re.compile(
    rf"compare\s+(?:the\s+)?([A-Za-z][\w.-]*)\s+samples?\s+(.+?)\s+"
    rf"(?:to|vs?\.?|versus|against)\s+(?:the\s+)?([A-Za-z][\w.-]*)\s+samples?\s+(.+?){_DE_TRAILING_CONTEXT}",
    re.I,
)
_DE_UNLABELED_GROUP_RE = re.compile(
    rf"compare\s+(.+?)\s+(?:to|vs?\.?|versus|against)\s+(.+?){_DE_TRAILING_CONTEXT}",
    re.I,
)
_DE_SLASH_LABELED_RE = re.compile(
    r"^/de\s+([A-Za-z][\w.-]*)\s*=\s*([^=]+?)\s+(?:to|vs?\.?|versus|against)\s+([A-Za-z][\w.-]*)\s*=\s*(.+)$",
    re.I,
)
_DE_SIGNIFICANCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pvalue",
        re.compile(
            r"\b(?:raw\s+)?p(?:[- ]?value)?\b(?:\s+(?:threshold|cutoff))?(?:\s*(?:of|=|:|<|<=|at|to))?\s*([0-9]*\.?[0-9]+(?:e-?\d+)?)?",
            re.I,
        ),
    ),
    (
        "fdr",
        re.compile(
            r"\b(?:fdr|q[- ]?value|adjusted\s+p(?:[- ]?value)?|adj\.?\s*p)\b(?:\s+(?:threshold|cutoff))?(?:\s*(?:of|=|:|<|<=|at|to))?\s*([0-9]*\.?[0-9]+(?:e-?\d+)?)?",
            re.I,
        ),
    ),
)


def _split_de_samples(raw: str) -> list[str]:
    cleaned = re.sub(r"\b(?:the|samples?|sample|group|groups)\b", " ", raw, flags=re.I)
    parts = re.split(r"\s*(?:,|and|&)\s*", cleaned)
    values = [part.strip().strip(".,;:!?") for part in parts if part.strip().strip(".,;:!?")]
    return values


def _extract_de_significance_request(message: str) -> tuple[str, float, bool] | None:
    matches: list[tuple[int, str, float, bool]] = []
    for metric, pattern in _DE_SIGNIFICANCE_PATTERNS:
        for match in pattern.finditer(message or ""):
            numeric = match.group(1)
            threshold = 0.05
            explicit_threshold = False
            if numeric is not None and numeric != "":
                try:
                    threshold = float(numeric)
                    explicit_threshold = True
                except ValueError:
                    continue
            matches.append((match.start(), metric, threshold, explicit_threshold))

    if not matches:
        return None

    _pos, metric, threshold, explicit_threshold = min(matches, key=lambda item: item[0])
    return metric, threshold, explicit_threshold


def _resolve_de_source_path(path_value: str, conv_state: "ConversationState") -> str:
    resolved = path_value.rstrip(".,;:!?")
    if resolved and not os.path.isabs(resolved) and conv_state.work_dir:
        return os.path.join(conv_state.work_dir, resolved)
    return resolved


def build_de_group_clarification(
    message: str,
    conv_state: "ConversationState",
    params: dict,
) -> str | None:
    msg_lower = message.lower()
    has_explicit_groups = bool(params.get("group_a_samples") and params.get("group_b_samples"))
    has_sample_info = bool(params.get("sample_info_path"))
    if has_explicit_groups or has_sample_info:
        return None

    requests_de = (
        message.strip().lower().startswith("/de")
        or "compare" in msg_lower
        or "edgepython" in msg_lower
        or "differential expression" in msg_lower
        or re.search(r"\bde\b", msg_lower) is not None
    )
    if not requests_de:
        return None

    has_source = bool(
        params.get("df_id")
        or params.get("counts_path")
        or params.get("work_dir")
        or "dataframe" in msg_lower
        or "abundance" in msg_lower
        or "reconcile" in msg_lower
    )
    if not has_source:
        return None

    if params.get("df_id"):
        source_desc = f"from DF{params['df_id']}"
    elif params.get("counts_path"):
        source_desc = f"from {os.path.basename(str(params['counts_path']))}"
    elif conv_state.work_dir:
        source_desc = "from the current workflow's abundance table"
    else:
        source_desc = "for this DE request"

    return (
        f"I can run edgePython {source_desc}, but I need the two sample groups. "
        "Please either provide a sample metadata file, or name which sample columns belong to each group.\n\n"
        "Examples:\n"
        "- compare the AD samples exc and jbh to the control samples gko and lwf\n"
        "- compare treated_1 and treated_2 to ctrl_1 and ctrl_2 from DF1\n"
        "- /de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2"
    )


# ---------------------------------------------------------------------------
# _extract_plan_params
# ---------------------------------------------------------------------------

def _extract_plan_params(message: str, conv_state: "ConversationState", plan_type: str,
                         project_dir: str = "") -> dict:
    """Extract relevant parameters from the user message and conversation state."""
    params: dict = {"goal": message}

    if plan_type == "run_dogme_batch":
        batch_samples = [
            {
                "sample_id": str(index + 1),
                "sample_name": match.group("sample_name"),
                "input_directory": match.group("input_directory").rstrip(".,;:!?"),
            }
            for index, match in enumerate(_DOGME_BATCH_SAMPLE_RE.finditer(message))
        ]
        shared_params: dict = {}
        mode_match = re.search(r"\b(DNA|RNA|cDNA)\b", message, re.IGNORECASE)
        if mode_match:
            shared_params["mode"] = mode_match.group(1).upper()
        fastq_inputs = [
            sample["input_directory"].lower().endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz"))
            for sample in batch_samples
        ]
        if any(fastq_inputs):
            shared_params["input_type"] = "fastq"
            if shared_params.get("mode") == "CDNA":
                shared_params["entry_point"] = "fastqCDNA"
        genome_matches = [
            GENOME_ALIASES.get(match.group(1).lower(), match.group(1))
            for match in _GENOME_ALIAS_RE.finditer(message)
        ]
        if genome_matches:
            shared_params["reference_genome"] = list(dict.fromkeys(genome_matches))
        if re.search(r"\b(slurm|sbatch|cluster|remote)\b", message, re.IGNORECASE):
            shared_params["execution_mode"] = "slurm"

        parallelism_match = re.search(
            r"\b(?:parallelism|max(?:imum)?\s+parallel|parallel)\s*(?:=|:|of)?\s*(\d+)\b",
            message,
            re.IGNORECASE,
        )
        if parallelism_match:
            params["requested_max_parallel"] = int(parallelism_match.group(1))
        params["batch_samples"] = batch_samples
        params["shared_params"] = shared_params
        return params

    if plan_type == "compare_samples":
        # Try to extract sample names from the message
        sample_matches = re.findall(r"(\b\w+(?:_\w+)*)\s+(?:and|vs?\.?|versus)\s+(\b\w+(?:_\w+)*)", message, re.I)
        if sample_matches:
            params["samples"] = list(sample_matches[0])
        elif conv_state.workflows and len(conv_state.workflows) >= 2:
            params["samples"] = [
                conv_state.workflows[-2].get("sample_name", "sample A"),
                conv_state.workflows[-1].get("sample_name", "sample B"),
            ]
        return params

    if plan_type == "compare_workflows":
        # Extract workflow names/indices from message or state
        sample_matches = re.findall(r"(\b\w+(?:_\w+)*)\s+(?:and|vs?\.?|versus)\s+(\b\w+(?:_\w+)*)", message, re.I)
        if sample_matches:
            params["workflows"] = list(sample_matches[0])
        elif conv_state.workflows and len(conv_state.workflows) >= 2:
            params["workflows"] = [
                conv_state.workflows[-2].get("sample_name", "workflow A"),
                conv_state.workflows[-1].get("sample_name", "workflow B"),
            ]
            # Also carry work_dirs
            params["work_dir_a"] = conv_state.workflows[-2].get("work_dir", "")
            params["work_dir_b"] = conv_state.workflows[-1].get("work_dir", "")
        return params

    if plan_type == "download_analyze":
        # Extract search term from message
        m = re.search(r"(?:download|get|fetch)\s+(.+?)(?:\s+(?:from|and|then))", message, re.I)
        if m:
            params["search_term"] = m.group(1).strip()
        return params

    if plan_type == "search_compare_to_local":
        # Extract search term and local sample from message
        m = re.search(r"(?:download|get|fetch)\s+(.+?)(?:\s+(?:from|and|then|compare))", message, re.I)
        if m:
            params["search_term"] = m.group(1).strip()
        # Local sample from state
        if conv_state.sample_name:
            params["local_sample"] = conv_state.sample_name
        if conv_state.work_dir:
            params["local_work_dir"] = conv_state.work_dir
        return params

    if plan_type == "run_de_pipeline":
        msg = message.strip()
        msg_lower = msg.lower()
        significance_request = _extract_de_significance_request(msg)
        if significance_request is not None:
            metric, threshold, explicit_threshold = significance_request
            params["significance_metric"] = metric
            params["significance_threshold"] = threshold
            params["significance_explicit"] = True
            params["significance_threshold_explicit"] = explicit_threshold

        # Extract counts path and sample info path from message
        m = re.search(r"counts?\s+(?:at|in|from|path)?\s*[=:]?\s*(\S+\.(?:csv|tsv|txt))", message, re.I)
        if m:
            params["counts_path"] = m.group(1)
        else:
            m = re.search(r"(?:from|using|on)\s+(\S*(?:abundance|counts?|matrix)\S*\.(?:csv|tsv|txt))", message, re.I)
            if m:
                params["counts_path"] = _resolve_de_source_path(m.group(1), conv_state)
            else:
                m = re.search(r"\b(reconciled_abundance\.(?:csv|tsv)|abundance\.(?:csv|tsv))\b", message, re.I)
                if m:
                    params["counts_path"] = _resolve_de_source_path(m.group(1), conv_state)

        m = re.search(r"(?:sample[_ ]?info|metadata|design)\s+(?:at|in|from|path)?\s*[=:]?\s*(\S+\.(?:csv|tsv|txt))", message, re.I)
        if m:
            params["sample_info_path"] = m.group(1)

        m = re.search(r"(?:group|condition)\s+(?:column)?\s*[=:]?\s*(\w+)", message, re.I)
        if m:
            params["group_column"] = m.group(1)

        m = re.search(r"(\w+)\s+(?:vs?\.?|versus|compared?\s+to)\s+(\w+)", message, re.I)
        if m:
            params["contrast"] = f"{m.group(1)} - {m.group(2)}"

        slash_match = _DE_SLASH_LABELED_RE.match(msg)
        if slash_match:
            params["group_a_label"] = slash_match.group(1)
            params["group_a_samples"] = _split_de_samples(slash_match.group(2))
            params["group_b_label"] = slash_match.group(3)
            params["group_b_samples"] = _split_de_samples(slash_match.group(4))
        else:
            labeled_match = _DE_LABELED_GROUP_RE.search(msg)
            if labeled_match:
                params["group_a_label"] = labeled_match.group(1)
                params["group_a_samples"] = _split_de_samples(labeled_match.group(2))
                params["group_b_label"] = labeled_match.group(3)
                params["group_b_samples"] = _split_de_samples(labeled_match.group(4))
            else:
                unlabeled_match = _DE_UNLABELED_GROUP_RE.search(msg)
                if unlabeled_match and re.search(r"(?:from|using|on)\s+(?:df\s*\d+|\S*(?:abundance|counts?|matrix)\S*\.(?:csv|tsv|txt))", msg, re.I):
                    params["group_a_label"] = params.get("group_a_label") or "group1"
                    params["group_a_samples"] = _split_de_samples(unlabeled_match.group(1))
                    params["group_b_label"] = params.get("group_b_label") or "group2"
                    params["group_b_samples"] = _split_de_samples(unlabeled_match.group(2))

        df_match = re.search(r"\bDF\s*(\d+)\b", msg, re.I)
        if df_match and (re.search(r"\b(?:from|using|on)\s+DF\s*\d+\b", msg, re.I) or "dataframe" in msg_lower or "df" in msg_lower):
            params["df_id"] = int(df_match.group(1))
        elif "dataframe" in msg_lower and conv_state.latest_dataframe:
            latest_match = re.search(r"(\d+)", conv_state.latest_dataframe, re.I)
            if latest_match:
                params["df_id"] = int(latest_match.group(1))

        if not params.get("counts_path") and conv_state.work_dir:
            params["work_dir"] = conv_state.work_dir

        if params.get("group_a_samples") and params.get("group_b_samples"):
            params["contrast"] = (
                f"{params.get('group_a_label', 'group1')} - {params.get('group_b_label', 'group2')}"
            )

        if re.search(r"(?:at|by)\s+transcript\s+level", msg, re.I):
            params["level"] = "transcript"
        else:
            params.setdefault("level", "gene")

        if re.search(r"(?:exact[_ ]test|exact\s+test)", msg, re.I):
            params["method"] = "exact_test"
        elif re.search(r"(?:qlf|glm|quasi|contrast)", msg, re.I):
            params["method"] = "glm"
        elif params.get("group_a_samples") and params.get("group_b_samples"):
            params["method"] = "exact_test"

        if project_dir or conv_state.work_dir:
            prep_base = conv_state.work_dir or project_dir or "."
            params["prep_output_dir"] = os.path.join(prep_base, "de_inputs")

        return params

    if plan_type == "run_enrichment":
        msg = message.lower()
        if "up" in msg and "down" not in msg:
            params["direction"] = "up"
        elif "down" in msg and "up" not in msg:
            params["direction"] = "down"
        else:
            params["direction"] = "all"
        if "kegg" in msg:
            params["database"] = "KEGG"
        elif "reactome" in msg:
            params["database"] = "REAC"
        return params

    if plan_type == "compare_region_overlaps":
        project_refs = [match.group(0) for match in _PROJECT_WORKFLOW_REF_RE.finditer(message or "")]
        bed_paths = [match.group("path") for match in _BED_PATH_RE.finditer(message or "")]
        plot_type = _select_plot_type(message)
        default_work_dir = str(getattr(conv_state, "work_dir", "") or "")
        label_overrides = _extract_overlap_label_overrides(message or "")
        plot_title = _extract_overlap_plot_title(message or "")

        if len(project_refs) >= 2:
            params["folder_a"] = _resolve_project_workflow_ref(project_refs[0], project_dir, default_work_dir)
            params["folder_b"] = _resolve_project_workflow_ref(project_refs[1], project_dir, default_work_dir)
            params["sample_a_label"] = label_overrides.get("sample_a_label") or project_refs[0].strip()
            params["sample_b_label"] = label_overrides.get("sample_b_label") or project_refs[1].strip()
            params["pattern_a"] = "*.m6Aopen.bed"
            params["pattern_b"] = "*.m6Aopen.bed"
            params["input_directory"] = project_dir or params["folder_a"]
        elif len(bed_paths) >= 2:
            params["bed_a_path"] = _resolve_existing_path(bed_paths[0], conv_state, project_dir)
            params["bed_b_path"] = _resolve_existing_path(bed_paths[1], conv_state, project_dir)
            params["sample_a_label"] = label_overrides.get("sample_a_label") or Path(params["bed_a_path"]).stem
            params["sample_b_label"] = label_overrides.get("sample_b_label") or Path(params["bed_b_path"]).stem
            params["input_directory"] = str(Path(params["bed_a_path"]).parent)

        output_root = _project_output_root(project_dir, conv_state)
        if output_root:
            params["output_directory"] = _next_project_workflow_dir(output_root)

        params["sample_name"] = "open_chromatin_overlap"
        params["mode"] = "DNA"
        params["plot_type"] = plot_type if plot_type in {"venn", "upset"} else "venn"
        if plot_title:
            params["plot_title"] = plot_title
        params["min_overlap_bp"] = 1
        return params

    if plan_type == "run_wf_pore_c":
        input_match = next(_PORE_C_INPUT_PATH_RE.finditer(message or ""), None)
        input_path = ""
        input_type = ""
        if input_match is not None:
            input_path = _resolve_pore_c_jailed_path(input_match.group("path"), conv_state, project_dir)
            lowered = input_path.lower()
            input_type = "bam" if lowered.endswith(".bam") else "fastq"
        else:
            hinted_match = _PORE_C_HINTED_INPUT_RE.search(message or "")
            if hinted_match is not None:
                input_path = _resolve_pore_c_jailed_path(hinted_match.group("path"), conv_state, project_dir)
                hinted_type = hinted_match.group("input_type").lower()
                input_type = "bam" if hinted_type == "bam" else "fastq"

        reference_match = _PORE_C_REFERENCE_PATH_RE.search(message or "")
        vcf_match = _PORE_C_VCF_PATH_RE.search(message or "")
        sample_sheet_match = _PORE_C_SAMPLE_SHEET_RE.search(message or "")

        if input_path:
            params["file_path"] = input_path
            params["source_path"] = input_path
            params["input_directory"] = input_path
        if input_type:
            params["input_type"] = input_type
        if reference_match is not None:
            params["reference_fasta"] = _resolve_pore_c_jailed_path(reference_match.group("path"), conv_state, project_dir)
        if vcf_match is not None:
            params["vcf"] = _resolve_pore_c_jailed_path(vcf_match.group("path"), conv_state, project_dir)
        if sample_sheet_match is not None:
            params["sample_sheet"] = _resolve_pore_c_jailed_path(sample_sheet_match.group("path"), conv_state, project_dir)

        sample_name = _extract_pore_c_sample_name(message or "")
        if not sample_name and input_path:
            sample_name = _derive_sample_name_from_input_path(input_path)
        if sample_name:
            params["sample_name"] = sample_name
            params["sample"] = sample_name

        params["cutter"] = _extract_pore_c_cutter(message or "") or "NlaIII"
        params["workflow_key"] = "wf_pore_c"
        params["workflow_repo"] = "epi2me-labs/wf-pore-c"
        params["workflow_version"] = "v1.3.1"
        params["report_filename"] = "wf-pore-c-report.html"
        params["preview_only"] = True
        params["output_flags"] = _pore_c_output_flags(message or "")

        output_root = _pore_c_output_root(input_path, conv_state, project_dir)
        if output_root:
            params["output_directory"] = _next_project_workflow_dir(output_root)

        return params

    if plan_type == "run_xgenepy_analysis":
        m = re.search(r"counts?\s+(?:at|in|from|path)?\s*[=:]?\s*(\S+\.(?:csv|tsv|txt))", message, re.I)
        if m:
            params["counts_path"] = m.group(1).rstrip(".,;:!?")
        m = re.search(r"(?:metadata|sample[_ ]?meta(?:data)?)\s+(?:at|in|from|path)?\s*[=:]?\s*(\S+\.(?:csv|tsv|txt))", message, re.I)
        if m:
            params["metadata_path"] = m.group(1).rstrip(".,;:!?")
        m = re.search(r"(?:output\s+(?:subdir|dir|directory)|save\s+to)\s*[=:]?\s*(\S+)", message, re.I)
        if m:
            params["output_subdir"] = m.group(1).rstrip(".,;:!?")
        m = re.search(r"trans[_\s-]?model\s*[=:]?\s*([A-Za-z0-9_\-]+)", message, re.I)
        if m:
            params["trans_model"] = m.group(1)
        m = re.search(r"alpha\s*[=:]?\s*([0-9]*\.?[0-9]+)", message, re.I)
        if m:
            try:
                params["alpha"] = float(m.group(1))
            except ValueError:
                pass

    if plan_type == "haplotype_with_vcf":
        mode_match = _HAPLOTYPE_MODE_RE.search(message)
        if mode_match:
            raw_mode = mode_match.group(1).strip().lower()
            params["input_type"] = "cDNA" if raw_mode == "cdna" else raw_mode.upper()

        default_work_dir = str(getattr(conv_state, "work_dir", "") or "")

        mentioned_references = _extract_mentioned_reference_genomes(message)
        if len(mentioned_references) == 1:
            params["reference_genome"] = mentioned_references[0]

        vcf_match = _HAPLOTYPE_VCF_HINT_RE.search(message) or _PORE_C_VCF_PATH_RE.search(message)
        if vcf_match:
            params["vcf_path"] = _trim_path_token(vcf_match.group("path"))

        founder_samples = _extract_haplotype_founder_samples(message)
        if founder_samples:
            params["vcf_selected_samples"] = founder_samples
            params.setdefault("reference_genome", "mm39")

        if not params.get("vcf_path"):
            default_reference = params.get("reference_genome")
            default_vcf = default_haplotype_vcf_for_reference(default_reference)
            if default_vcf:
                params["vcf_path"] = default_vcf
                params["vcf_defaulted"] = True

        project_ref_matches = list(_PROJECT_WORKFLOW_REF_RE.finditer(message or ""))
        project_ref_workflow_names = {match.group("workflow").strip().lower() for match in project_ref_matches}
        workflow_tokens = [match.strip() for match in _HAPLOTYPE_WORKFLOW_RE.findall(message)]
        if workflow_tokens or project_ref_matches:
            workflow_dirs: list[str] = []
            for match in project_ref_matches:
                resolved = _resolve_project_workflow_dir(match.group(0), project_dir, default_work_dir)
                if isinstance(resolved, str) and resolved and resolved not in workflow_dirs:
                    workflow_dirs.append(resolved)
            for workflow_name in workflow_tokens:
                if workflow_name.lower() in project_ref_workflow_names:
                    continue
                if project_dir:
                    workflow_dirs.append(str(Path(project_dir) / workflow_name))
                elif default_work_dir:
                    base = Path(default_work_dir).resolve().parent
                    workflow_dirs.append(str((base / workflow_name).resolve()))
            if workflow_dirs:
                params["workflow_dirs"] = workflow_dirs
                params["work_dir"] = workflow_dirs[0]

        output_root = _project_output_root(project_dir, conv_state)
        if output_root:
            params["output_directory"] = _next_project_workflow_dir(output_root)

        params.setdefault(
            "goal",
            "Label long-read BAM reads with haplotype or genotype assignments using an indexed VCF",
        )
        return params

    if plan_type == "reconcile_bams":
        mentioned_references: list[str] = []
        for match in re.findall(r"\b(GRCh38|mm39|mad1|hg38|mm10|human|mouse)\b", message, re.I):
            canonical = GENOME_ALIASES.get(match.strip().lower())
            if canonical and canonical not in mentioned_references:
                mentioned_references.append(canonical)
        if len(mentioned_references) == 1:
            params["reference"] = mentioned_references[0]

        m = re.search(r"(?:output\s+(?:prefix|name)|prefix)\s*[=:]?\s*([a-zA-Z0-9._-]+)", message, re.I)
        if m:
            params["output_prefix"] = m.group(1)

        m = re.search(r"(?:output\s+(?:dir|directory))\s+(\S+)", message, re.I)
        if m:
            params["output_directory"] = m.group(1).rstrip(".,;:!?")
        else:
            m = re.search(r"into\s+(\S+)", message, re.I)
            if m:
                params["output_directory"] = m.group(1).rstrip(".,;:!?")
            else:
                m = re.search(r"\bto\s+((?:/|~|\.|[A-Za-z0-9._-]+/)\S*)", message, re.I)
                if m:
                    params["output_directory"] = m.group(1).rstrip(".,;:!?")

        # Default output to a fresh workflow directory in the active project so
        # reconcile can pass the exact destination downstream instead of asking
        # the wrapper to allocate one later.
        output_root = _project_output_root(project_dir, conv_state)
        if not params.get("output_directory") and output_root:
            params["output_directory"] = _next_project_workflow_dir(output_root)

        m = re.search(r"(?:annotation\s+gtf|gtf\s+(?:path|file)|use\s+gtf)\s*[=:]?\s*(\S+\.(?:gtf|gtf\.gz))", message, re.I)
        if m:
            params["annotation_gtf"] = m.group(1).rstrip(".,;:!?")

        workflow_dirs: list[str] = []
        selected_names: list[str] = []
        selected_workflow_tokens = {
            match.strip().lower()
            for match in re.findall(r"\b(workflow[\w.-]+)\b", message, re.I)
        }
        project_workflow_refs: list[tuple[str, str]] = []

        # Cross-project explicit mentions:
        # "sampleA in projectX:workflow2"
        # "projectX:workflow2"
        project_workflow_mentions = re.findall(
            r"([a-zA-Z0-9_.-]+)\s+in\s+([a-zA-Z0-9_.-]+)\s*:\s*(workflow[\w.-]+)",
            message,
            re.I,
        )
        if project_workflow_mentions:
            selected_names = [sample for sample, _project, _wf in project_workflow_mentions]
            for _sample, project_name, workflow_name in project_workflow_mentions:
                selected_workflow_tokens.add(workflow_name.lower())
                project_workflow_refs.append((project_name.strip(), workflow_name.strip()))

        for project_name, workflow_name in re.findall(
            r"\b([a-zA-Z0-9_.-]+)\s*:\s*(workflow[\w.-]+)\b",
            message,
            re.I,
        ):
            ref = (project_name.strip(), workflow_name.strip())
            if ref not in project_workflow_refs:
                project_workflow_refs.append(ref)

        workflow_qualified_mentions = re.findall(
            r"([a-zA-Z0-9_.-]+)\s+in\s+(workflow[\w.-]+)",
            message,
            re.I,
        )
        if workflow_qualified_mentions:
            selected_names = [sample for sample, _wf in workflow_qualified_mentions]
            selected_workflow_tokens.update(wf.lower() for _sample, wf in workflow_qualified_mentions)

        named_pair_patterns = [
            r"(?:bams?|workflows?)\s+(?:of|from|between)\s+([a-zA-Z0-9_.-]+)\s+(?:and|vs?\.?|versus)\s+([a-zA-Z0-9_.-]+)",
            r"([a-zA-Z0-9_.-]+)\s+(?:and|vs?\.?|versus)\s+([a-zA-Z0-9_.-]+)",
        ]
        if not selected_names:
            for pattern in named_pair_patterns:
                match = re.search(pattern, message, re.I)
                if not match:
                    continue
                selected_names = [match.group(1), match.group(2)]
                break

        def _add_candidate_base_dir(base_dirs: list[str], value: str | None) -> None:
            if not isinstance(value, str):
                return
            candidate = value.strip().rstrip("/.,;:!?")
            if not candidate or not candidate.startswith("/"):
                return
            if candidate not in base_dirs:
                base_dirs.append(candidate)

        def _derive_base_dir_from_work_dir(work_dir_value: str | None) -> str | None:
            if not isinstance(work_dir_value, str):
                return None
            wd = work_dir_value.strip().rstrip("/")
            if not wd or not wd.startswith("/"):
                return None
            wf_match = re.search(r"/workflow[\w.-]+$", wd, re.I)
            if wf_match:
                project_dir = wd[: wf_match.start()]
            else:
                project_dir = wd
            base_dir = os.path.dirname(project_dir.rstrip("/"))
            return base_dir or "/"

        candidate_base_dirs: list[str] = []
        base_match = re.search(
            r"(?:base\s+(?:dir(?:ectory)?|path)|remote\s+base\s+path)\s*[=:]?\s*(/\S+)",
            message,
            re.I,
        )
        if base_match:
            _add_candidate_base_dir(candidate_base_dirs, base_match.group(1))

        if getattr(conv_state, "workflows", None):
            for wf in conv_state.workflows:
                if not isinstance(wf, dict):
                    continue
                _add_candidate_base_dir(
                    candidate_base_dirs,
                    _derive_base_dir_from_work_dir(wf.get("work_dir")),
                )

        _add_candidate_base_dir(
            candidate_base_dirs,
            _derive_base_dir_from_work_dir(getattr(conv_state, "work_dir", None)),
        )

        remote_paths = getattr(conv_state, "remote_paths", None)
        if isinstance(remote_paths, dict):
            _add_candidate_base_dir(candidate_base_dirs, remote_paths.get("remote_base_path"))
            for key in ("remote_work_path", "remote_output_path", "remote_input_path"):
                _add_candidate_base_dir(
                    candidate_base_dirs,
                    _derive_base_dir_from_work_dir(remote_paths.get(key)),
                )

        # Derive user root from project_dir (parent of the project slug dir)
        # so cross-project references like "testc2c12local:workflow2" resolve
        # to absolute paths even when the current project has no prior jobs.
        if not candidate_base_dirs and project_dir:
            _user_root = os.path.dirname(project_dir.rstrip("/"))
            _add_candidate_base_dir(candidate_base_dirs, _user_root)

        if project_workflow_refs:
            selected_workflow_tokens.clear()
            for project_name, workflow_name in project_workflow_refs:
                selected_workflow_tokens.add(workflow_name.lower())
                if candidate_base_dirs:
                    resolved = f"{candidate_base_dirs[0].rstrip('/')}/{project_name}/{workflow_name}"
                else:
                    resolved = f"{project_name}/{workflow_name}"
                if resolved not in workflow_dirs:
                    workflow_dirs.append(resolved)

        if not project_workflow_refs and getattr(conv_state, "workflows", None):
            normalized_targets = {name.lower() for name in selected_names}
            for wf in conv_state.workflows:
                if not isinstance(wf, dict):
                    continue
                wf_dir = wf.get("work_dir")
                if not isinstance(wf_dir, str) or not wf_dir:
                    continue
                work_dir_name = wf_dir.rstrip("/").split("/")[-1].lower()

                if selected_workflow_tokens and work_dir_name not in selected_workflow_tokens:
                    continue

                if normalized_targets:
                    sample_name = str(wf.get("sample_name") or "").strip().lower()
                    if sample_name not in normalized_targets and work_dir_name not in normalized_targets:
                        continue

                if wf_dir not in workflow_dirs:
                    workflow_dirs.append(wf_dir)

            if not workflow_dirs:
                for wf in conv_state.workflows:
                    if isinstance(wf, dict):
                        wf_dir = wf.get("work_dir")
                        if isinstance(wf_dir, str) and wf_dir and wf_dir not in workflow_dirs:
                            workflow_dirs.append(wf_dir)

        known_workflow_basenames = {
            wf_dir.rstrip("/").split("/")[-1].lower()
            for wf_dir in workflow_dirs
            if isinstance(wf_dir, str) and wf_dir
        }
        if not project_workflow_refs:
            for match in re.findall(r"(workflow[\w.-]+)", message, re.I):
                normalized = match.strip()
                if normalized and normalized.lower() not in known_workflow_basenames and normalized not in workflow_dirs:
                    # Resolve bare folder names against project_dir when available
                    if project_dir:
                        resolved = f"{project_dir.rstrip('/')}/{normalized}"
                    else:
                        resolved = normalized
                    workflow_dirs.append(resolved)

        if workflow_dirs:
            params["workflow_dirs"] = workflow_dirs

        return params

    if plan_type == "parse_plot_interpret":
        params["plot_type"] = _select_plot_type(message)
        # Fall through to pick up sample_name / work_dir below

    if plan_type == "remote_stage_workflow":
        sample_match = re.search(r'(?:called|named)\s+([a-zA-Z0-9_-]+)', message, re.I)
        if not sample_match:
            sample_match = re.search(r'(?:the\s+)?sample\s+([a-zA-Z0-9_-]+)', message, re.I)
        if sample_match:
            params["sample_name"] = sample_match.group(1)

        input_match = re.search(r"(?:at|from)\s+(\S+)", message, re.I)
        if input_match:
            params["input_directory"] = input_match.group(1).rstrip(".,;:!?")

    # run_workflow / remote_stage_workflow / summarize_results / parse_plot_interpret
    if conv_state.sample_name:
        params["sample_name"] = conv_state.sample_name
    if conv_state.work_dir:
        params["work_dir"] = conv_state.work_dir

    return params
