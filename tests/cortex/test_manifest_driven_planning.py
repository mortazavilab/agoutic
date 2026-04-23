import pytest

from cortex.plan_classifier import _detect_plan_type, _detect_plan_type_from_manifests
from cortex.planner import generate_plan
from cortex.schemas import ConversationState


class _NoopEngine:
    def plan(self, *_args, **_kwargs):
        raise AssertionError("Deterministic manifest-backed planning should not call the LLM")


@pytest.mark.parametrize(
    ("message", "expected_plan_type"),
    [
        (
            "compare the AD samples exc and jbh to the control samples gko and lwf",
            "run_de_pipeline",
        ),
        (
            "run GO enrichment analysis on the upregulated genes",
            "run_enrichment",
        ),
    ],
)
def test_manifest_detection_matches_expected_plan_type(message, expected_plan_type):
    assert _detect_plan_type_from_manifests(message) == expected_plan_type
    assert _detect_plan_type(message) == expected_plan_type


def test_generate_plan_adds_manifest_metadata_to_de_plan():
    state = ConversationState(
        active_skill="differential_expression",
        active_project="proj-1",
        work_dir="/tmp/project/workflow10",
    )

    plan = generate_plan(
        "compare the AD samples exc and jbh to the control samples gko and lwf",
        "differential_expression",
        state,
        _NoopEngine(),
        conversation_history=[],
        project_dir="/tmp/project",
    )

    assert plan is not None
    assert plan["planning_skill"] == "differential_expression"
    assert plan["estimated_runtime"] == "medium"
    assert "medium" in plan["estimated_runtime_summary"]
    assert plan["service_warnings"] == []

    run_step = next(step for step in plan["steps"] if step["kind"] == "RUN_DE_PIPELINE")
    assert run_step["skill_key"] == "differential_expression"
    assert [tool_call["tool"] for tool_call in run_step["tool_calls"]] == [
        "load_data",
        "filter_genes",
        "normalize",
        "estimate_dispersion",
        "exact_test",
        "get_top_genes",
    ]


def test_generate_plan_warns_when_required_manifest_service_is_unavailable(monkeypatch):
    import cortex.planner as planner

    monkeypatch.setattr(planner, "_configured_service_keys", lambda: {"analyzer"})

    state = ConversationState(
        active_skill="differential_expression",
        active_project="proj-1",
        work_dir="/tmp/project/workflow10",
    )

    plan = generate_plan(
        "compare the AD samples exc and jbh to the control samples gko and lwf",
        "differential_expression",
        state,
        _NoopEngine(),
        conversation_history=[],
        project_dir="/tmp/project",
    )

    assert plan is not None
    assert plan["service_warnings"]
    assert "edgepython" in plan["service_warnings"][0]