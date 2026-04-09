"""Planner tests for local and remote staging workflow plan steps."""

from cortex.schemas import ConversationState
from cortex.planner import (
    _detect_plan_type,
    _extract_plan_params,
    _template_reconcile_bams,
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


def test_detect_plan_type_matches_reconcile_bams_request():
    assert _detect_plan_type("Reconcile annotated BAM files across workflows") == "reconcile_bams"


def test_detect_plan_type_matches_reconcile_the_bams_request():
    assert _detect_plan_type("I want to reconcile the bams of C2C12r1 and C2C12r3") == "reconcile_bams"


def test_reconcile_bams_template_orders_preflight_before_approval_and_run():
    plan = _template_reconcile_bams({"work_dir": "/tmp/project", "output_prefix": "merged"})
    kinds = [step["kind"] for step in plan["steps"]]

    assert plan["plan_type"] == "reconcile_bams"
    assert kinds == [
        "LOCATE_DATA",
        "CHECK_EXISTING",
        "REQUEST_APPROVAL",
        "RUN_SCRIPT",
        "LOCATE_DATA",
        "PARSE_OUTPUT_FILE",
        "WRITE_SUMMARY",
    ]

    preflight_idx = kinds.index("CHECK_EXISTING")
    approve_idx = kinds.index("REQUEST_APPROVAL")
    run_idx = kinds.index("RUN_SCRIPT")
    assert preflight_idx < approve_idx < run_idx

    preflight_step = plan["steps"][preflight_idx]
    assert preflight_step["tool_calls"][0]["params"]["script_id"] == "reconcile_bams/reconcile_bams"
    preflight_args = preflight_step["tool_calls"][0]["params"]["script_args"]
    assert "--preflight-only" in preflight_args

    run_step = plan["steps"][run_idx]
    assert run_step["requires_approval"] is True
    assert run_step["tool_calls"][0]["params"]["script_id"] == "reconcile_bams/reconcile_bams"
    assert "--json" in run_step["tool_calls"][0]["params"]["script_args"]


def test_reconcile_bams_template_locate_step_uses_workflow_annot_dirs():
    plan = _template_reconcile_bams(
        {
            "workflow_dirs": ["/tmp/workflow2", "/tmp/workflow3"],
            "output_prefix": "merged",
        }
    )

    locate_step = plan["steps"][0]
    assert locate_step["kind"] == "LOCATE_DATA"
    assert locate_step["tool_calls"] == [
        {
            "source_key": "analyzer",
            "tool": "list_job_files",
            "params": {
                "work_dir": "/tmp/workflow2/annot",
                "extensions": ".bam",
                "max_depth": 1,
                "allow_missing": True,
            },
        },
        {
            "source_key": "analyzer",
            "tool": "list_job_files",
            "params": {
                "work_dir": "/tmp/workflow3/annot",
                "extensions": ".bam",
                "max_depth": 1,
                "allow_missing": True,
            },
        },
    ]


def test_reconcile_bams_template_defaults_output_directory_to_common_workflow_parent():
    plan = _template_reconcile_bams(
        {
            "workflow_dirs": ["/tmp/project/workflow2", "/tmp/project/workflow3"],
            "output_prefix": "merged",
        }
    )

    assert plan["output_directory"] == "/tmp/project"
    preflight_args = plan["steps"][1]["tool_calls"][0]["params"]["script_args"]
    run_args = plan["steps"][3]["tool_calls"][0]["params"]["script_args"]
    assert ["--output-dir", "/tmp/project"] == preflight_args[preflight_args.index("--output-dir"):preflight_args.index("--output-dir") + 2]
    assert ["--output-dir", "/tmp/project"] == run_args[run_args.index("--output-dir"):run_args.index("--output-dir") + 2]


def test_reconcile_bams_template_has_parse_step_before_summary():
    """WRITE_SUMMARY should depend on PARSE_OUTPUT_FILE, which depends on a post-run LOCATE_DATA."""
    plan = _template_reconcile_bams({
        "work_dir": "/tmp/project",
        "output_prefix": "reconciled",
        "output_directory": "/tmp/project/workflow10",
    })
    kinds = [step["kind"] for step in plan["steps"]]

    # The last three steps should be LOCATE_DATA → PARSE_OUTPUT_FILE → WRITE_SUMMARY
    assert kinds[-3:] == ["LOCATE_DATA", "PARSE_OUTPUT_FILE", "WRITE_SUMMARY"]

    locate_out = plan["steps"][-3]
    parse_step = plan["steps"][-2]
    summary_step = plan["steps"][-1]

    # Post-run LOCATE_DATA lists the output directory for tsv/csv files
    assert locate_out["tool_calls"][0]["tool"] == "list_job_files"
    assert locate_out["tool_calls"][0]["params"]["work_dir"] == "/tmp/project/workflow10"
    assert locate_out["tool_calls"][0]["params"]["extensions"] == ".tsv,.csv"

    # PARSE_OUTPUT_FILE depends on the post-run LOCATE_DATA
    assert locate_out["id"] in parse_step["depends_on"]

    # WRITE_SUMMARY depends on PARSE_OUTPUT_FILE
    assert parse_step["id"] in summary_step["depends_on"]

    # Post-run LOCATE_DATA depends on RUN_SCRIPT
    run_step = next(s for s in plan["steps"] if s["kind"] == "RUN_SCRIPT")
    assert run_step["id"] in locate_out["depends_on"]


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


def test_classify_request_marks_reconcile_the_bams_as_multistep():
    class _State:
        pass

    assert classify_request(
        "I want to reconcile the bams of C2C12r1 and C2C12r3",
        "run_dogme_rna",
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


def test_extract_plan_params_reconcile_annotation_gtf():
    params = _extract_plan_params(
        "Reconcile annotated BAMs using annotation gtf /tmp/manual.GRCh38.annotation.gtf into /tmp/out",
        ConversationState(active_skill="reconcile_bams", active_project="proj-1"),
        "reconcile_bams",
    )

    assert params["annotation_gtf"] == "/tmp/manual.GRCh38.annotation.gtf"


def test_extract_plan_params_reconcile_named_workflows_from_state():
    params = _extract_plan_params(
        "I want to reconcile the bams of C2C12r1 and C2C12r3",
        ConversationState(
            active_skill="run_dogme_rna",
            active_project="proj-1",
            workflows=[
                {"sample_name": "C2C12r1", "work_dir": "/tmp/C2C12r1"},
                {"sample_name": "C2C12r2", "work_dir": "/tmp/C2C12r2"},
                {"sample_name": "C2C12r3", "work_dir": "/tmp/C2C12r3"},
            ],
        ),
        "reconcile_bams",
    )

    assert params["workflow_dirs"] == ["/tmp/C2C12r1", "/tmp/C2C12r3"]


def test_extract_plan_params_reconcile_workflow_qualified_names_from_state():
    params = _extract_plan_params(
        "I want to reconcile the bams of C2C12r1 in workflow2 and C2C12r3 in workflow3",
        ConversationState(
            active_skill="run_dogme_rna",
            active_project="proj-1",
            workflows=[
                {"sample_name": "C2C12r1", "work_dir": "/tmp/workflow2"},
                {"sample_name": "C2C12r2", "work_dir": "/tmp/workflow9"},
                {"sample_name": "C2C12r3", "work_dir": "/tmp/workflow3"},
            ],
        ),
        "reconcile_bams",
    )

    assert params["workflow_dirs"] == ["/tmp/workflow2", "/tmp/workflow3"]


def test_extract_plan_params_reconcile_does_not_treat_to_reconcile_as_output_dir():
    params = _extract_plan_params(
        "I want to reconcile the bams of C2C12r1 in workflow2 and C2C12r3 in workflow3",
        ConversationState(active_skill="run_dogme_rna", active_project="proj-1"),
        "reconcile_bams",
    )

    assert "output_directory" not in params


def test_extract_plan_params_reconcile_cross_project_workflow_refs_resolve_from_known_base():
    params = _extract_plan_params(
        (
            "reconcile bams from C2C12r1 in project-2026-03-27:workflow1, "
            "C2C12r2 in project-2026-03-27-1:workflow1 and "
            "C2C12r3 in project-2026-03-27-2:workflow1"
        ),
        ConversationState(
            active_skill="reconcile_bams",
            active_project="proj-1",
            workflows=[
                {
                    "sample_name": "anchor",
                    "work_dir": "/share/crsp/lab/seyedam/share/agoutic/elnaz/project-2026-03-30/workflow1",
                }
            ],
        ),
        "reconcile_bams",
    )

    assert params["workflow_dirs"] == [
        "/share/crsp/lab/seyedam/share/agoutic/elnaz/project-2026-03-27/workflow1",
        "/share/crsp/lab/seyedam/share/agoutic/elnaz/project-2026-03-27-1/workflow1",
        "/share/crsp/lab/seyedam/share/agoutic/elnaz/project-2026-03-27-2/workflow1",
    ]


def test_extract_plan_params_reconcile_cross_project_workflow_refs_keep_relative_without_base():
    params = _extract_plan_params(
        "reconcile bams from A in projectX:workflow2 and B in projectY:workflow7",
        ConversationState(active_skill="reconcile_bams", active_project="proj-1"),
        "reconcile_bams",
    )

    assert params["workflow_dirs"] == [
        "projectX/workflow2",
        "projectY/workflow7",
    ]


def test_extract_plan_params_reconcile_bare_workflow_names_resolve_against_project_dir():
    """Bare workflow folder names like 'workflow5' should be resolved to absolute
    paths using project_dir when conv_state has no matching workflows."""
    params = _extract_plan_params(
        "reconcile the annotated bams from workflow5, workflow6, workflow7, and workflow8",
        ConversationState(active_skill="reconcile_bams", active_project="ad-samples"),
        "reconcile_bams",
        project_dir="/media/backup_disk/agoutic_root/users/elnaz-a/ad-samples",
    )

    assert params["workflow_dirs"] == [
        "/media/backup_disk/agoutic_root/users/elnaz-a/ad-samples/workflow5",
        "/media/backup_disk/agoutic_root/users/elnaz-a/ad-samples/workflow6",
        "/media/backup_disk/agoutic_root/users/elnaz-a/ad-samples/workflow7",
        "/media/backup_disk/agoutic_root/users/elnaz-a/ad-samples/workflow8",
    ]


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
