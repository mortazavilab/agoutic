import pytest

from cortex.plan_templates import _template_run_de_pipeline
from cortex.schemas import ConversationState


def _de_params() -> dict:
    return {
        "goal": "compare grouped samples",
        "work_dir": "/tmp/project/workflow10",
        "group_a_label": "AD",
        "group_a_samples": ["exc", "jbh"],
        "group_b_label": "control",
        "group_b_samples": ["gko", "lwf"],
        "level": "gene",
        "prep_output_dir": "/tmp/project/workflow10/de_inputs",
        "method": "exact_test",
        "contrast": "AD - control",
    }


def test_compose_plan_from_manifest_matches_de_template_structure():
    from cortex.plan_composer import compose_plan_from_manifest

    params = _de_params()
    plan = compose_plan_from_manifest("differential_expression", params, {"edgepython", "analyzer"})
    template = _template_run_de_pipeline(params)

    assert plan is not None
    assert plan["plan_type"] == "run_de_pipeline"
    assert [step["kind"] for step in plan["steps"]] == [step["kind"] for step in template["steps"]]
    assert plan["planning_skill"] == "differential_expression"
    assert plan["service_warnings"] == []
    assert "medium" in plan["estimated_runtime_summary"]

    run_step = next(step for step in plan["steps"] if step["kind"] == "RUN_DE_PIPELINE")
    template_run_step = next(step for step in template["steps"] if step["kind"] == "RUN_DE_PIPELINE")
    assert run_step["skill_key"] == "differential_expression"
    assert [tool_call["tool"] for tool_call in run_step["tool_calls"]] == [
        tool_call["tool"] for tool_call in template_run_step["tool_calls"]
    ]
    exact_call = next(tool for tool in run_step["tool_calls"] if tool["tool"] == "exact_test")
    top_genes_call = next(tool for tool in run_step["tool_calls"] if tool["tool"] == "get_top_genes")
    plot_step = next(step for step in plan["steps"] if step["kind"] == "GENERATE_DE_PLOT")
    plot_call = plot_step["tool_calls"][0]
    assert exact_call["params"]["significance_metric"] == "pvalue"
    assert exact_call["params"]["significance_threshold"] == 0.05
    assert top_genes_call["params"]["significance_metric"] == "pvalue"
    assert top_genes_call["params"]["significance_threshold"] == 0.05
    assert "use_fdr_y" not in plot_call["params"]


def test_compose_plan_from_manifest_threads_explicit_pvalue_threshold():
    from cortex.plan_composer import compose_plan_from_manifest

    params = _de_params() | {
        "significance_metric": "pvalue",
        "significance_threshold": 0.01,
        "significance_explicit": True,
    }
    plan = compose_plan_from_manifest("differential_expression", params, {"edgepython", "analyzer"})

    run_step = next(step for step in plan["steps"] if step["kind"] == "RUN_DE_PIPELINE")
    exact_call = next(tool for tool in run_step["tool_calls"] if tool["tool"] == "exact_test")
    top_genes_call = next(tool for tool in run_step["tool_calls"] if tool["tool"] == "get_top_genes")
    plot_step = next(step for step in plan["steps"] if step["kind"] == "GENERATE_DE_PLOT")
    plot_call = plot_step["tool_calls"][0]

    assert exact_call["params"]["significance_metric"] == "pvalue"
    assert exact_call["params"]["significance_threshold"] == 0.01
    assert top_genes_call["params"]["significance_metric"] == "pvalue"
    assert top_genes_call["params"]["significance_threshold"] == 0.01
    assert plot_call["params"]["use_fdr_y"] is False
    assert plot_call["params"]["pvalue_cutoff"] == 0.01


def test_compose_plan_from_manifest_warns_when_required_service_missing():
    from cortex.plan_composer import compose_plan_from_manifest

    plan = compose_plan_from_manifest("differential_expression", _de_params(), {"analyzer"})

    assert plan is not None
    assert plan["service_warnings"]
    assert "edgepython" in plan["service_warnings"][0]


def test_generate_plan_uses_manifest_composer_before_template(monkeypatch):
    import cortex.planner as planner

    called: dict[str, object] = {}

    monkeypatch.setattr(planner, "_detect_plan_type", lambda _msg: "run_de_pipeline")
    monkeypatch.setattr(planner, "_extract_plan_params", lambda *_args, **_kwargs: _de_params())

    def _fake_compose(skill_key, params, available_services):
        called["skill_key"] = skill_key
        called["available_services"] = set(available_services)
        return {
            "plan_type": "run_de_pipeline",
            "title": "Manifest plan",
            "goal": "goal",
            "status": "PENDING",
            "auto_execute_safe_steps": True,
            "planning_skill": "differential_expression",
            "estimated_runtime": "medium",
            "estimated_runtime_summary": "Estimated runtime: medium",
            "service_warnings": [],
            "steps": [
                {
                    "id": "step1",
                    "kind": "CHECK_EXISTING",
                    "title": "Check",
                    "status": "PENDING",
                    "order_index": 0,
                    "tool_calls": [],
                    "requires_approval": False,
                    "depends_on": [],
                    "result": None,
                    "error": None,
                    "started_at": None,
                    "completed_at": None,
                }
            ],
            "current_step_id": "step1",
            "artifacts": [],
        }

    monkeypatch.setattr(planner, "compose_plan_from_manifest", _fake_compose)
    monkeypatch.setattr(
        planner,
        "_template_run_de_pipeline",
        lambda _params: (_ for _ in ()).throw(AssertionError("template fallback should not run")),
    )

    plan = planner.generate_plan(
        "compare the AD samples exc and jbh to the control samples gko and lwf",
        "differential_expression",
        ConversationState(active_skill="differential_expression", active_project="proj-1"),
        object(),
        conversation_history=[],
        project_dir="/tmp/project",
    )

    assert plan is not None
    assert plan["planning_skill"] == "differential_expression"
    assert called["skill_key"] == "differential_expression"
    assert "edgepython" in called["available_services"]


@pytest.mark.parametrize("composer_result", [None, RuntimeError("boom")])
def test_generate_plan_falls_back_to_template_when_manifest_composer_fails(monkeypatch, composer_result):
    import cortex.planner as planner

    monkeypatch.setattr(planner, "_detect_plan_type", lambda _msg: "run_de_pipeline")
    monkeypatch.setattr(planner, "_extract_plan_params", lambda *_args, **_kwargs: _de_params())

    if isinstance(composer_result, Exception):
        def _fake_compose(*_args, **_kwargs):
            raise composer_result
    else:
        def _fake_compose(*_args, **_kwargs):
            return composer_result

    monkeypatch.setattr(planner, "compose_plan_from_manifest", _fake_compose)

    template_called = {"value": False}

    def _fake_template(params):
        template_called["value"] = True
        return _template_run_de_pipeline(params)

    monkeypatch.setattr(planner, "_template_run_de_pipeline", _fake_template)

    plan = planner.generate_plan(
        "compare the AD samples exc and jbh to the control samples gko and lwf",
        "differential_expression",
        ConversationState(active_skill="differential_expression", active_project="proj-1", work_dir="/tmp/project/workflow10"),
        object(),
        conversation_history=[],
        project_dir="/tmp/project",
    )

    assert plan is not None
    assert template_called["value"] is True
    assert plan["planning_skill"] == "differential_expression"