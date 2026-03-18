"""Planner tests for local and remote staging workflow plan steps."""

from cortex.schemas import ConversationState
from cortex.planner import _detect_plan_type, _extract_plan_params, _template_remote_stage_workflow, _template_run_workflow, classify_request


def test_run_workflow_includes_cache_preflight_before_approval():
    plan = _template_run_workflow({"sample_name": "sampleA"})
    kinds = [step["kind"] for step in plan["steps"]]

    assert "FIND_REFERENCE_CACHE" in kinds
    assert "FIND_DATA_CACHE" in kinds

    ref_idx = kinds.index("FIND_REFERENCE_CACHE")
    data_idx = kinds.index("FIND_DATA_CACHE")
    approve_idx = kinds.index("REQUEST_APPROVAL")

    assert ref_idx < data_idx < approve_idx


def test_remote_stage_workflow_has_remote_stage_steps():
    plan = _template_remote_stage_workflow({"sample_name": "Jamshid", "input_directory": "/data/pod5"})
    kinds = [step["kind"] for step in plan["steps"]]

    assert plan["plan_type"] == "remote_stage_workflow"
    assert plan["workflow_type"] == "remote_sample_intake"
    assert kinds == [
        "LOCATE_DATA",
        "VALIDATE_INPUTS",
        "check_remote_stage",
        "CHECK_REMOTE_PROFILE_AUTH",
        "REQUEST_APPROVAL",
        "remote_stage",
        "complete_stage_only",
    ]
    assert plan["steps"][0]["id"] == "locate_data"
    assert plan["steps"][2]["id"] == "check_remote_stage"
    assert plan["steps"][5]["id"] == "stage_input"
    assert plan["steps"][5]["requires_approval"] is False


def test_detect_plan_type_matches_remote_stage_request():
    assert _detect_plan_type("Stage the mouse cDNA sample called Jamshid at /data/pod5 on hpc3") == "remote_stage_workflow"


def test_detect_plan_type_matches_remote_stage_request_for_arbitrary_profile_nickname():
    assert _detect_plan_type("Stage the mouse cDNA sample called Jamshid at /data/pod5 on mycluster") == "remote_stage_workflow"


def test_classify_request_marks_remote_stage_as_multistep():
    class _State:
        pass

    assert classify_request(
        "Stage the mouse cDNA sample called Jamshid at /data/pod5 on hpc3",
        "remote_execution",
        _State(),
    ) == "MULTI_STEP"


def test_classify_request_marks_remote_stage_on_arbitrary_profile_as_multistep():
    class _State:
        pass

    assert classify_request(
        "Stage the mouse cDNA sample called Jamshid at /data/pod5 on mycluster",
        "remote_execution",
        _State(),
    ) == "MULTI_STEP"


def test_extract_plan_params_keeps_remote_stage_sample_name_and_path():
    params = _extract_plan_params(
        "Stage the mouse cDNA sample called Jamshid at /data/pod5 on hpc3",
        ConversationState(active_skill="remote_execution", active_project="proj-1"),
        "remote_stage_workflow",
    )

    assert params["sample_name"] == "Jamshid"
    assert params["input_directory"] == "/data/pod5"


def test_extract_plan_params_keeps_remote_stage_sample_name_and_path_for_arbitrary_profile_nickname():
    params = _extract_plan_params(
        "Stage the mouse cDNA sample called Jamshid at /data/pod5 on mycluster",
        ConversationState(active_skill="remote_execution", active_project="proj-1"),
        "remote_stage_workflow",
    )

    assert params["sample_name"] == "Jamshid"
    assert params["input_directory"] == "/data/pod5"
