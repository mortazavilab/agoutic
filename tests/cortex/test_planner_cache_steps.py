"""Planner tests for SLURM staging cache preflight steps."""

from cortex.planner import _template_run_workflow


def test_run_workflow_includes_cache_preflight_before_approval():
    plan = _template_run_workflow({"sample_name": "sampleA"})
    kinds = [step["kind"] for step in plan["steps"]]

    assert "FIND_REFERENCE_CACHE" in kinds
    assert "FIND_DATA_CACHE" in kinds

    ref_idx = kinds.index("FIND_REFERENCE_CACHE")
    data_idx = kinds.index("FIND_DATA_CACHE")
    approve_idx = kinds.index("REQUEST_APPROVAL")

    assert ref_idx < data_idx < approve_idx
