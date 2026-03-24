"""Planner tests for local and remote staging workflow plan steps."""

from cortex.schemas import ConversationState
from cortex.planner import (
    _detect_plan_type,
    _extract_plan_params,
    _template_remote_stage_workflow,
    _template_run_workflow,
    classify_request,
    compose_plan_fragments,
    generate_plan,
)


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


def test_classify_request_marks_summarize_results_as_multistep():
    class _State:
        pass

    assert classify_request(
        "Summarize the results for the current project and tell me what I should look at next",
        "welcome",
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


def test_compose_plan_fragments_remaps_ids_and_dependencies():
    plan = compose_plan_fragments(
        [
            {
                "fragment_alias": "ingest",
                "steps": [
                    {
                        "id": "locate",
                        "kind": "LOCATE_DATA",
                        "title": "Locate",
                        "depends_on": [],
                    },
                    {
                        "id": "validate",
                        "kind": "VALIDATE_INPUTS",
                        "title": "Validate",
                        "depends_on": ["locate"],
                    },
                ],
            },
            {
                "fragment_alias": "analysis",
                "steps": [
                    {
                        "id": "summary",
                        "kind": "WRITE_SUMMARY",
                        "title": "Summarize",
                        "depends_on": ["ingest__validate"],
                    }
                ],
            },
        ],
        title="Hybrid plan",
        goal="Compose from fragments",
        project_id="proj-1",
    )

    ids = [step["id"] for step in plan["steps"]]
    assert ids == ["ingest__locate", "ingest__validate", "analysis__summary"]
    assert plan["steps"][1]["depends_on"] == ["ingest__locate"]
    assert plan["steps"][2]["depends_on"] == ["ingest__validate"]
    assert plan["project_id"] == "proj-1"
    assert isinstance(plan.get("plan_instance_id"), str)
    assert plan["steps"][0]["provenance"]["fragment_id"] == "ingest"


def test_compose_plan_fragments_rejects_dangling_cross_fragment_dependency():
    try:
        compose_plan_fragments(
            [
                {
                    "fragment_alias": "frag1",
                    "steps": [
                        {
                            "id": "step1",
                            "kind": "LOCATE_DATA",
                            "title": "Locate",
                            "depends_on": ["frag2__missing"],
                        }
                    ],
                }
            ],
            title="Broken plan",
            goal="Should fail",
            project_id="proj-1",
        )
    except ValueError as exc:
        assert "unknown step id" in str(exc)
    else:
        raise AssertionError("Expected ValueError for dangling cross-fragment dependency")


def test_generate_plan_composes_llm_fragments_when_provided():
    class _Engine:
        @staticmethod
        def plan(_message, _state_json, _history):
            payload = (
                '[[PLAN:{"plan_type":"custom","title":"Hybrid","goal":"merge",'
                '"fragments":[{"fragment_alias":"fragA","steps":[{"id":"a1","kind":"LOCATE_DATA",'
                '"title":"Locate","depends_on":[]}]},{"fragment_alias":"fragB","steps":[{"id":"b1",'
                '"kind":"WRITE_SUMMARY","title":"Summarize","depends_on":["fragA__a1"]}]}]}]]'
            )
            return payload, {}

    plan = generate_plan(
        "do a complex hybrid flow",
        "welcome",
        ConversationState(active_skill="welcome", active_project="proj-1"),
        _Engine(),
        conversation_history=[],
    )

    assert plan is not None
    assert plan["project_id"] == "proj-1"
    assert isinstance(plan.get("plan_instance_id"), str)
    assert [step["id"] for step in plan["steps"]] == ["fragA__a1", "fragB__b1"]
    assert plan["steps"][1]["depends_on"] == ["fragA__a1"]
