"""
Parameter extraction for plan generation.

Parses user messages and conversation state to build param dicts
consumed by plan templates.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from common.logging_config import get_logger

if TYPE_CHECKING:
    from cortex.schemas import ConversationState

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Plot selection heuristic
# ---------------------------------------------------------------------------

def _select_plot_type(message: str) -> str:
    """Keyword-based chart type selection from the user's request."""
    msg = message.lower()
    if any(w in msg for w in ("volcano", "de plot", "differential expression plot")):
        return "volcano"
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


def _split_de_samples(raw: str) -> list[str]:
    cleaned = re.sub(r"\b(?:the|samples?|sample|group|groups)\b", " ", raw, flags=re.I)
    parts = re.split(r"\s*(?:,|and|&)\s*", cleaned)
    values = [part.strip().strip(".,;:!?") for part in parts if part.strip().strip(".,;:!?")]
    return values


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
        "- compare exc and jbh to gko and lwf from DF1\n"
        "- /de AD=exc,jbh vs control=gko,lwf"
    )


# ---------------------------------------------------------------------------
# _extract_plan_params
# ---------------------------------------------------------------------------

def _extract_plan_params(message: str, conv_state: "ConversationState", plan_type: str,
                         project_dir: str = "") -> dict:
    """Extract relevant parameters from the user message and conversation state."""
    params: dict = {"goal": message}

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
            prep_base = project_dir or conv_state.work_dir or "."
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

    if plan_type == "reconcile_bams":
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

        # Default output to the current project directory so cross-project
        # reconcile writes into the active project, not the source project.
        if not params.get("output_directory") and project_dir:
            params["output_directory"] = project_dir

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
