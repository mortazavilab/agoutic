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
        (
            "haplotype RNA workflow7 with file ./parent.vcf",
            "haplotype_with_vcf",
        ),
        (
            "/haplotype DNA workflow7 ./parent.vcf",
            "haplotype_with_vcf",
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


def test_generate_plan_adds_manifest_metadata_to_haplotype_plan():
    state = ConversationState(
        active_skill="analyze_job_results",
        active_project="proj-1",
        work_dir="/tmp/project/workflow10",
    )

    plan = generate_plan(
        "haplotype RNA workflow7 with file ./parent.vcf",
        "analyze_job_results",
        state,
        _NoopEngine(),
        conversation_history=[],
        project_dir="/tmp/project",
    )

    assert plan is not None
    assert plan["planning_skill"] == "haplotype_with_vcf"
    assert plan["estimated_runtime"] == "slow"
    assert plan["input_type"] == "RNA"
    assert plan["vcf_path"] == "./parent.vcf"

    run_step = next(step for step in plan["steps"] if step["kind"] == "RUN_SCRIPT")
    assert run_step["skill_key"] == "haplotype_with_vcf"
    assert run_step["tool_calls"][0]["params"]["script_id"] == "haplotype_with_vcf/haplotype_with_vcf"


def test_generate_plan_adds_manifest_metadata_to_mouse_founder_cross_project_haplotype_plan(tmp_path, monkeypatch):
    owner_root = tmp_path / "owner"
    current_project = owner_root / "testhaplo"
    other_project = owner_root / "erisa-drna"
    (current_project / "workflow10").mkdir(parents=True)
    (other_project / "workflow5").mkdir(parents=True)

    monkeypatch.setattr(
        "cortex.plan_params.default_haplotype_vcf_for_reference",
        lambda reference: str(other_project / "mgp_REL2021_snps_founders.vcf.gz") if reference == "mm39" else None,
    )

    state = ConversationState(
        active_skill="haplotype_with_vcf",
        active_project="proj-1",
        work_dir=str(current_project / "workflow10"),
    )

    plan = generate_plan(
        "Haplotype mouse sample B6 Cast F1 in erisa-drna:workflow5",
        "haplotype_with_vcf",
        state,
        _NoopEngine(),
        conversation_history=[],
        project_dir=str(current_project),
    )

    assert plan is not None
    assert plan["planning_skill"] == "haplotype_with_vcf"
    assert plan["input_type"] == "RNA"
    assert plan["vcf_defaulted"] is True
    assert plan["vcf_selected_samples"] == ["C57BL_6J", "CAST_EiJ"]
    assert plan["output_directory"] == str(current_project / "workflow11")

    locate_step = next(step for step in plan["steps"] if step["kind"] == "LOCATE_DATA")
    assert locate_step["tool_calls"][0]["params"]["work_dir"] == str(other_project / "workflow5" / "annot")

    run_step = next(step for step in plan["steps"] if step["kind"] == "RUN_SCRIPT")
    assert run_step["tool_calls"][0]["params"]["script_args"][:3] == ["--json", "--mode", "RNA"]
    assert str(other_project / "workflow5") in run_step["tool_calls"][0]["params"]["script_args"]