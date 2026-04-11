"""Manifest-driven plan composition helpers."""

from __future__ import annotations

import re
from typing import Any

from common.logging_config import get_logger
from cortex.plan_templates import _make_step, _manifest_tool_call
from cortex.skill_manifest import (
    SKILL_MANIFESTS,
    OutputType,
    SkillManifest,
    check_service_availability,
    get_manifest,
)

logger = get_logger(__name__)


_EXPECTED_INPUT_PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "counts_matrix": ("counts_matrix", "counts_path", "df_id", "work_dir"),
    "sample_metadata": ("sample_metadata", "sample_info_path", "group_a_samples", "group_b_samples", "df_id"),
    "design_formula": ("design_formula", "method", "group_column", "group_a_label", "group_b_label"),
    "contrast": ("contrast", "group_a_label", "group_b_label"),
    "gene_list": ("gene_list", "direction", "result_name", "work_dir"),
    "organism": ("organism", "species", "genome", "work_dir"),
    "counts_table": ("counts_table", "counts_path"),
    "strain_column": ("strain_column", "group_column"),
}

_OUTPUT_INPUT_COMPATIBILITY: dict[OutputType, set[str]] = {
    OutputType.DATAFRAME: {"counts_matrix", "sample_metadata", "gene_list", "counts_table", "strain_column"},
    OutputType.FILE: {"counts_path", "file_path", "bam_paths", "workflow_uuids"},
    OutputType.REPORT: {"job_uuid", "work_dir", "result_name"},
    OutputType.JOB: {"job_uuid", "work_dir"},
    OutputType.PLOT: {"result_name", "work_dir"},
    OutputType.ANNOTATION: {"gene_list", "organism"},
}

_RUNTIME_WEIGHT = {
    "fast": 0,
    "medium": 1,
    "slow": 2,
    "variable": 3,
}


def _has_expected_input_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _missing_expected_inputs(manifest: SkillManifest, params: dict[str, Any]) -> list[str]:
    missing_inputs: list[str] = []
    for expected_input in manifest.expected_inputs:
        candidate_keys = _EXPECTED_INPUT_PARAM_ALIASES.get(expected_input, (expected_input,))
        if any(_has_expected_input_value(params.get(key)) for key in candidate_keys):
            continue
        missing_inputs.append(expected_input)
    return missing_inputs


def _candidate_dependency_keys(manifest: SkillManifest, params: dict[str, Any]) -> list[str]:
    missing_inputs = set(_missing_expected_inputs(manifest, params))
    if not missing_inputs:
        return []

    if manifest.depends_on_skills:
        return list(manifest.depends_on_skills)

    candidates: list[str] = []
    for candidate in SKILL_MANIFESTS.values():
        if candidate.key == manifest.key:
            continue
        candidate_inputs: set[str] = set()
        for output_type in candidate.output_types:
            candidate_inputs.update(_OUTPUT_INPUT_COMPATIBILITY.get(output_type, set()))
        if candidate_inputs & missing_inputs:
            candidates.append(candidate.key)

    return candidates if len(candidates) == 1 else []


def resolve_skill_chain(skill_key: str, params: dict[str, Any]) -> list[SkillManifest]:
    visited: set[str] = set()
    ordered: list[SkillManifest] = []

    def _visit(current_key: str) -> None:
        if current_key in visited:
            return
        manifest = get_manifest(current_key)
        if manifest is None:
            return
        visited.add(current_key)
        for dependency_key in _candidate_dependency_keys(manifest, params):
            _visit(dependency_key)
        ordered.append(manifest)

    _visit(skill_key)
    return ordered


def _runtime_summary(skill_chain: list[SkillManifest]) -> tuple[str, str]:
    if not skill_chain:
        return "fast", "Estimated runtime: fast"

    counts: dict[str, int] = {}
    dominant_runtime = "fast"
    for manifest in skill_chain:
        runtime = manifest.estimated_runtime
        counts[runtime] = counts.get(runtime, 0) + 1
        if _RUNTIME_WEIGHT[runtime] > _RUNTIME_WEIGHT[dominant_runtime]:
            dominant_runtime = runtime

    if len(skill_chain) == 1:
        return dominant_runtime, f"Estimated runtime: {dominant_runtime}"

    details = ", ".join(
        f"{count} {runtime}"
        for runtime, count in sorted(counts.items(), key=lambda item: _RUNTIME_WEIGHT[item[0]], reverse=True)
    )
    return dominant_runtime, f"Estimated runtime: {dominant_runtime} ({details})"


def _service_warnings(skill_chain: list[SkillManifest], available_services: set[str]) -> list[str]:
    warnings: list[str] = []
    for manifest in skill_chain:
        services_ok, missing_services = check_service_availability(manifest.key, available_services)
        if services_ok:
            continue
        warnings.append(
            f"{manifest.display_name or manifest.key} requires unavailable service(s): {', '.join(missing_services)}"
        )
    return warnings


def _input_warnings(skill_chain: list[SkillManifest], params: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for manifest in skill_chain:
        missing_inputs = _missing_expected_inputs(manifest, params)
        if missing_inputs:
            warnings.append(
                f"Missing expected inputs for {manifest.display_name or manifest.key}: {', '.join(missing_inputs)}"
            )
    return warnings


def _apply_plan_metadata(
    plan: dict[str, Any],
    *,
    target_manifest: SkillManifest,
    skill_chain: list[SkillManifest],
    params: dict[str, Any],
    available_services: set[str],
) -> dict[str, Any]:
    estimated_runtime, runtime_summary = _runtime_summary(skill_chain)
    plan["planning_skill"] = target_manifest.key
    plan["resolved_skill_chain"] = [manifest.key for manifest in skill_chain]
    plan["estimated_runtime"] = estimated_runtime
    plan["estimated_runtime_summary"] = runtime_summary
    plan["service_warnings"] = _service_warnings(skill_chain, available_services)

    input_warnings = _input_warnings(skill_chain, params)
    if input_warnings:
        plan["input_warnings"] = input_warnings
    return plan


def _tag_steps(plan: dict[str, Any], skill_key: str, extra_kinds: set[str] | None = None) -> None:
    extra_kinds = extra_kinds or set()
    manifest = get_manifest(skill_key)
    required_services = set(manifest.required_services) if manifest is not None else set()
    for step in plan.get("steps", []):
        if not isinstance(step, dict):
            continue
        tool_calls = step.get("tool_calls") if isinstance(step.get("tool_calls"), list) else []
        if step.get("kind") in extra_kinds or any(
            isinstance(tool_call, dict) and tool_call.get("source_key") in required_services
            for tool_call in tool_calls
        ):
            step["skill_key"] = skill_key


def _compose_de_plan(manifest: SkillManifest, params: dict[str, Any]) -> dict[str, Any]:
    counts_path = params.get("counts_path", "counts.csv")
    sample_info = params.get("sample_info_path", "sample_info.csv")
    group_col = params.get("group_column", "condition")
    contrast = params.get("contrast", "treated - control")
    group_a_label = params.get("group_a_label", "group1")
    group_b_label = params.get("group_b_label", "group2")
    group_a_samples = params.get("group_a_samples") or []
    group_b_samples = params.get("group_b_samples") or []
    use_prep = bool(group_a_samples and group_b_samples)
    method = params.get("method") or ("exact_test" if use_prep else "glm")
    work_dir = str(params.get("work_dir") or "").strip()
    result_name = params.get("result_name") or (
        f"{re.sub(r'[^a-zA-Z0-9]+', '_', str(group_a_label)).strip('_').lower() or 'group1'}"
        f"_vs_{re.sub(r'[^a-zA-Z0-9]+', '_', str(group_b_label)).strip('_').lower() or 'group2'}"
        f"_{params.get('level', 'gene')}"
    )

    steps = []
    idx = 0

    s_check = _make_step(
        "CHECK_EXISTING",
        "Check for existing DE results",
        idx,
        tool_calls=[
            {
                "source_key": "analyzer",
                "tool": "find_file",
                "params": {
                    "file_name": "de_results",
                    **({"work_dir": work_dir} if work_dir else {}),
                },
            }
        ],
    )
    steps.append(s_check)
    idx += 1

    de_depends = [s_check["id"]]
    if use_prep:
        s_prep = _make_step(
            "PREPARE_DE_INPUT",
            f"Prepare DE inputs ({group_a_label} vs {group_b_label})",
            idx,
            depends_on=[s_check["id"]],
        )
        s_prep["counts_path"] = params.get("counts_path", "")
        s_prep["work_dir"] = params.get("work_dir", "")
        s_prep["df_id"] = params.get("df_id")
        s_prep["output_dir"] = params.get("prep_output_dir", "")
        s_prep["group_a_label"] = group_a_label
        s_prep["group_a_samples"] = list(group_a_samples)
        s_prep["group_b_label"] = group_b_label
        s_prep["group_b_samples"] = list(group_b_samples)
        s_prep["level"] = params.get("level", "gene")
        steps.append(s_prep)
        idx += 1
        de_depends.append(s_prep["id"])
        sample_info = params.get("sample_info_path", "")
        group_col = "group"
        contrast = f"{group_a_label} - {group_b_label}"

    de_tool_calls = [
        _manifest_tool_call(
            manifest.key,
            "load_data",
            {"counts_path": counts_path, "sample_info_path": sample_info, "group_column": group_col},
            default_source_key="edgepython",
        ),
        _manifest_tool_call(
            manifest.key,
            "filter_genes",
            {"min_count": 10, "min_total_count": 15},
            default_source_key="edgepython",
        ),
        _manifest_tool_call(
            manifest.key,
            "normalize",
            {"method": "TMM"},
            default_source_key="edgepython",
        ),
    ]
    if method == "exact_test":
        de_tool_calls.extend(
            [
                _manifest_tool_call(
                    manifest.key,
                    "estimate_dispersion",
                    {"robust": True},
                    default_source_key="edgepython",
                ),
                _manifest_tool_call(
                    manifest.key,
                    "exact_test",
                    {"pair": [group_a_label, group_b_label], "name": result_name},
                    default_source_key="edgepython",
                ),
            ]
        )
    else:
        de_tool_calls.extend(
            [
                _manifest_tool_call(
                    manifest.key,
                    "set_design",
                    {"formula": "~ 0 + group"},
                    default_source_key="edgepython",
                ),
                _manifest_tool_call(
                    manifest.key,
                    "estimate_dispersion",
                    {"robust": True},
                    default_source_key="edgepython",
                ),
                _manifest_tool_call(
                    manifest.key,
                    "fit_model",
                    {"robust": True},
                    default_source_key="edgepython",
                ),
                _manifest_tool_call(
                    manifest.key,
                    "test_contrast",
                    {"contrast": contrast, "name": result_name},
                    default_source_key="edgepython",
                ),
            ]
        )
    de_tool_calls.append(
        _manifest_tool_call(
            manifest.key,
            "get_top_genes",
            {"name": result_name, "n": 20, "fdr_threshold": 0.05},
            default_source_key="edgepython",
        )
    )

    s_de = _make_step(
        "RUN_DE_PIPELINE",
        f"Run DE analysis ({contrast})",
        idx,
        requires_approval=False,
        depends_on=de_depends,
        tool_calls=de_tool_calls,
    )
    steps.append(s_de)
    idx += 1

    s_save = _make_step(
        "SAVE_RESULTS",
        "Save full DE results",
        idx,
        depends_on=[s_de["id"]],
        tool_calls=[
            _manifest_tool_call(
                manifest.key,
                "save_results",
                {"name": result_name, "format": "tsv"},
                default_source_key="edgepython",
            )
        ],
    )
    steps.append(s_save)
    idx += 1

    s_annotate = _make_step(
        "ANNOTATE_RESULTS",
        "Annotate gene symbols",
        idx,
        depends_on=[s_de["id"]],
        tool_calls=[
            _manifest_tool_call(
                manifest.key,
                "annotate_genes",
                {},
                default_source_key="edgepython",
            )
        ],
    )
    steps.append(s_annotate)
    idx += 1

    s_plot = _make_step(
        "GENERATE_DE_PLOT",
        "Generate volcano plot",
        idx,
        depends_on=[s_annotate["id"]],
        tool_calls=[
            _manifest_tool_call(
                manifest.key,
                "generate_plot",
                {"plot_type": "volcano", "result_name": result_name},
                default_source_key="edgepython",
            )
        ],
    )
    steps.append(s_plot)
    idx += 1

    s_interpret = _make_step(
        "INTERPRET_RESULTS",
        "Interpret DE results",
        idx,
        depends_on=[s_annotate["id"]],
    )
    steps.append(s_interpret)
    idx += 1

    s_summary = _make_step(
        "WRITE_SUMMARY",
        "Write DE analysis summary",
        idx,
        depends_on=[s_save["id"], s_plot["id"], s_interpret["id"]],
    )
    steps.append(s_summary)

    plan = {
        "plan_type": "run_de_pipeline",
        "title": f"Differential expression: {contrast}",
        "goal": params.get("goal", f"Run DE analysis: {contrast}"),
        "workflow_type": "de_analysis",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "work_dir": work_dir,
        "steps": steps,
        "artifacts": [],
    }
    _tag_steps(
        plan,
        manifest.key,
        extra_kinds={
            "PREPARE_DE_INPUT",
            "RUN_DE_PIPELINE",
            "SAVE_RESULTS",
            "ANNOTATE_RESULTS",
            "GENERATE_DE_PLOT",
            "INTERPRET_RESULTS",
            "WRITE_SUMMARY",
        },
    )
    return plan


def _compose_enrichment_plan(manifest: SkillManifest, params: dict[str, Any]) -> dict[str, Any]:
    direction = params.get("direction", "all")
    database = params.get("database")

    steps = []
    idx = 0

    s_filter = _make_step(
        "FILTER_DE_GENES",
        f"Filter DE genes ({direction})",
        idx,
        tool_calls=[
            _manifest_tool_call(
                manifest.key,
                "filter_de_genes",
                {"direction": direction},
                default_source_key="edgepython",
            )
        ],
    )
    steps.append(s_filter)
    idx += 1

    s_go = _make_step(
        "RUN_GO_ENRICHMENT",
        f"GO enrichment ({direction})",
        idx,
        depends_on=[s_filter["id"]],
        tool_calls=[
            _manifest_tool_call(
                manifest.key,
                "run_go_enrichment",
                {"direction": direction},
                default_source_key="edgepython",
            )
        ],
    )
    steps.append(s_go)
    idx += 1

    if database != "GO_ONLY":
        db = database or "KEGG"
        s_pathway = _make_step(
            "RUN_PATHWAY_ENRICHMENT",
            f"{db} pathway enrichment ({direction})",
            idx,
            depends_on=[s_filter["id"]],
            tool_calls=[
                _manifest_tool_call(
                    manifest.key,
                    "run_pathway_enrichment",
                    {"direction": direction, "database": db},
                    default_source_key="edgepython",
                )
            ],
        )
        steps.append(s_pathway)
        idx += 1
        plot_deps = [s_go["id"], s_pathway["id"]]
    else:
        plot_deps = [s_go["id"]]

    s_plot = _make_step(
        "PLOT_ENRICHMENT",
        "Plot enrichment results",
        idx,
        depends_on=plot_deps,
        tool_calls=[
            _manifest_tool_call(
                manifest.key,
                "generate_plot",
                {"plot_type": "enrichment_bar"},
                default_source_key="edgepython",
            )
        ],
    )
    steps.append(s_plot)
    idx += 1

    s_summary = _make_step(
        "SUMMARIZE_ENRICHMENT",
        "Interpret enrichment results",
        idx,
        depends_on=[s_plot["id"]],
    )
    steps.append(s_summary)

    plan = {
        "plan_type": "run_enrichment",
        "title": f"Enrichment analysis ({direction})",
        "goal": params.get("goal", f"Run enrichment analysis ({direction})"),
        "workflow_type": "enrichment_analysis",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }
    _tag_steps(plan, manifest.key, extra_kinds={"SUMMARIZE_ENRICHMENT"})
    return plan


def _compose_xgenepy_plan(manifest: SkillManifest, params: dict[str, Any]) -> dict[str, Any]:
    project_dir = params.get("project_dir") or params.get("work_dir") or ""
    counts_path = params.get("counts_path", "counts.csv")
    metadata_path = params.get("metadata_path", "metadata.csv")
    output_subdir = params.get("output_subdir", "xgenepy_runs")
    trans_model = params.get("trans_model", "log_additive")
    alpha = float(params.get("alpha", 0.05))

    steps = []
    idx = 0

    s_check = _make_step(
        "CHECK_EXISTING",
        "Check for existing XgenePy manifest",
        idx,
        tool_calls=[
            {
                "source_key": "analyzer",
                "tool": "find_file",
                "params": {
                    "work_dir": project_dir,
                    "file_name": "run_manifest.json",
                },
            }
        ],
    )
    steps.append(s_check)
    idx += 1

    s_approve = _make_step(
        "REQUEST_APPROVAL",
        "Approve local XgenePy execution",
        idx,
        requires_approval=True,
        depends_on=[s_check["id"]],
    )
    steps.append(s_approve)
    idx += 1

    s_run = _make_step(
        "RUN_XGENEPY",
        "Run local XgenePy cis/trans analysis",
        idx,
        requires_approval=False,
        depends_on=[s_approve["id"]],
        tool_calls=[
            _manifest_tool_call(
                manifest.key,
                "run_xgenepy_analysis",
                {
                    "project_dir": project_dir,
                    "counts_path": counts_path,
                    "metadata_path": metadata_path,
                    "output_subdir": output_subdir,
                    "trans_model": trans_model,
                    "alpha": alpha,
                    "execution_mode": "local",
                },
                default_source_key="xgenepy",
            )
        ],
    )
    steps.append(s_run)
    idx += 1

    s_parse = _make_step(
        "PARSE_XGENEPY_OUTPUT",
        "Parse canonical XgenePy outputs",
        idx,
        depends_on=[s_run["id"]],
        tool_calls=[
            {
                "source_key": "analyzer",
                "tool": "parse_xgenepy_outputs",
                "params": {
                    "work_dir": project_dir,
                    "output_dir": output_subdir,
                },
            }
        ],
    )
    steps.append(s_parse)
    idx += 1

    s_summary = _make_step(
        "WRITE_SUMMARY",
        "Summarize XgenePy analysis results",
        idx,
        depends_on=[s_parse["id"]],
    )
    steps.append(s_summary)

    plan = {
        "plan_type": "run_xgenepy_analysis",
        "title": "Run XgenePy cis/trans analysis",
        "goal": params.get("goal", "Run XgenePy analysis"),
        "workflow_type": "xgenepy_analysis",
        "auto_execute_safe_steps": True,
        "status": "PENDING",
        "current_step_id": steps[0]["id"],
        "steps": steps,
        "artifacts": [],
    }
    _tag_steps(plan, manifest.key, extra_kinds={"RUN_XGENEPY", "WRITE_SUMMARY"})
    return plan


def compose_plan_from_manifest(
    skill_key: str,
    params: dict[str, Any],
    available_services: set[str],
) -> dict[str, Any] | None:
    manifest = get_manifest(skill_key)
    if manifest is None or not manifest.plan_type:
        return None

    skill_chain = resolve_skill_chain(skill_key, params)
    if not skill_chain:
        return None

    if manifest.key == "differential_expression":
        plan = _compose_de_plan(manifest, params)
    elif manifest.key == "enrichment_analysis":
        plan = _compose_enrichment_plan(manifest, params)
    elif manifest.key == "xgenepy_analysis":
        plan = _compose_xgenepy_plan(manifest, params)
    else:
        logger.info("Manifest plan composition not implemented for skill", skill_key=skill_key)
        return None

    return _apply_plan_metadata(
        plan,
        target_manifest=manifest,
        skill_chain=skill_chain,
        params=params,
        available_services=available_services,
    )