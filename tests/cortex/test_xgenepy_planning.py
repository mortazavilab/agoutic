from cortex.plan_executor import STEP_TOOL_DEFAULTS, should_auto_execute
from cortex.planner import _detect_plan_type, _template_run_xgenepy_analysis


def test_detect_plan_type_matches_xgenepy_requests():
    assert _detect_plan_type("run xgenepy analysis on my counts and metadata") == "run_xgenepy_analysis"


def test_xgenepy_template_requires_approval_before_run():
    plan = _template_run_xgenepy_analysis(
        {
            "project_dir": "/tmp/project",
            "counts_path": "inputs/counts.csv",
            "metadata_path": "inputs/metadata.csv",
            "output_subdir": "xgenepy_runs/workflow1",
        }
    )

    assert plan["plan_type"] == "run_xgenepy_analysis"
    kinds = [step["kind"] for step in plan["steps"]]
    assert kinds == ["CHECK_EXISTING", "REQUEST_APPROVAL", "RUN_XGENEPY", "PARSE_XGENEPY_OUTPUT", "WRITE_SUMMARY"]

    approval_step = plan["steps"][1]
    run_step = plan["steps"][2]
    assert approval_step["requires_approval"] is True
    assert run_step["requires_approval"] is False
    assert run_step["tool_calls"][0]["source_key"] == "xgenepy"
    assert run_step["tool_calls"][0]["tool"] == "run_xgenepy_analysis"


def test_executor_mappings_for_xgenepy_steps():
    assert should_auto_execute({"kind": "RUN_XGENEPY", "requires_approval": False}) is False
    assert should_auto_execute({"kind": "PARSE_XGENEPY_OUTPUT", "requires_approval": False}) is True

    assert STEP_TOOL_DEFAULTS["RUN_XGENEPY"][0]["source_key"] == "xgenepy"
    assert STEP_TOOL_DEFAULTS["PARSE_XGENEPY_OUTPUT"][0]["tool"] == "parse_xgenepy_outputs"
