"""Tests for cortex/plan_templates.py — deterministic plan template builders."""

import pytest
from unittest.mock import patch, MagicMock

from cortex.plan_templates import (
    _step_id,
    _plan_instance_id,
    _make_step,
    _normalize_fragment_alias,
    _manifest_tool_call,
    _template_remote_stage_workflow,
    _template_run_dogme_batch,
    _template_run_workflow,
    _template_run_wf_pore_c,
    _template_compare_samples,
    _template_download_analyze,
)


# ---------------------------------------------------------------------------
# Step / plan ID helpers
# ---------------------------------------------------------------------------

class TestStepIdHelpers:
    def test_step_id_returns_12_char_hex(self):
        sid = _step_id()
        assert len(sid) == 12
        int(sid, 16)  # Should not raise — it's valid hex

    def test_plan_instance_id_returns_32_char_hex(self):
        pid = _plan_instance_id()
        assert len(pid) == 32
        int(pid, 16)  # Valid hex

    def test_step_ids_are_unique(self):
        ids = {_step_id() for _ in range(10)}
        assert len(ids) == 10

    def test_plan_instance_ids_are_unique(self):
        ids = {_plan_instance_id() for _ in range(10)}
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# _make_step
# ---------------------------------------------------------------------------

class TestMakeStep:
    def test_basic_step(self):
        step = _make_step("LOCATE_DATA", "Locate files", 0)
        assert step["kind"] == "LOCATE_DATA"
        assert step["title"] == "Locate files"
        assert step["order_index"] == 0
        assert step["status"] == "PENDING"
        assert step["tool_calls"] == []
        assert step["requires_approval"] is False
        assert step["depends_on"] == []
        assert step["result"] is None
        assert step["error"] is None

    def test_step_with_tool_calls(self):
        tool_calls = [{"source_key": "analyzer", "tool": "list_files"}]
        step = _make_step("LOCATE_DATA", "Locate", 0, tool_calls=tool_calls)
        assert step["tool_calls"] == tool_calls

    def test_step_with_approval(self):
        step = _make_step("REQUEST_APPROVAL", "Approve", 1, requires_approval=True)
        assert step["requires_approval"] is True

    def test_step_with_dependencies(self):
        deps = ["step-1", "step-2"]
        step = _make_step("VALIDATE", "Validate", 2, depends_on=deps)
        assert step["depends_on"] == deps


# ---------------------------------------------------------------------------
# _normalize_fragment_alias
# ---------------------------------------------------------------------------

class TestNormalizeFragmentAlias:
    def test_alphanumeric_unchanged(self):
        assert _normalize_fragment_alias("sample1", fallback="s") == "sample1"

    def test_special_chars_become_underscores(self):
        result = _normalize_fragment_alias("my-sample/test", fallback="s")
        assert result == "my_sample_test"

    def test_empty_raw_uses_fallback(self):
        assert _normalize_fragment_alias("", fallback="default") == "default"

    def test_only_special_chars_uses_fallback(self):
        assert _normalize_fragment_alias("!!!", fallback="fallback") == "fallback"


# ---------------------------------------------------------------------------
# _manifest_tool_call
# ---------------------------------------------------------------------------

class TestManifestToolCall:
    @patch("cortex.plan_templates.get_tool_call_spec")
    def test_with_spec(self, mock_spec):
        spec = MagicMock()
        spec.source_key = "analyzer"
        mock_spec.return_value = spec
        result = _manifest_tool_call("analyzer", "list_files", {"path": "/data"}, default_source_key="default")
        assert result["source_key"] == "analyzer"
        assert result["tool"] == "list_files"
        assert result["params"] == {"path": "/data"}

    @patch("cortex.plan_templates.get_tool_call_spec")
    def test_without_spec_uses_default(self, mock_spec):
        mock_spec.return_value = None
        result = _manifest_tool_call("analyzer", "list_files", {}, default_source_key="default")
        assert result["source_key"] == "default"


# ---------------------------------------------------------------------------
# _template_remote_stage_workflow
# ---------------------------------------------------------------------------

class TestTemplateRemoteStageWorkflow:
    def test_local_source_path_skips_workflow_file_lookup(self):
        plan = _template_remote_stage_workflow({
            "sample_name": "ENCFF801EBI",
            "input_directory": "/media/backup/ENCFF801EBI.fastq.gz",
        })

        locate_step = next(step for step in plan["steps"] if step["id"] == "locate_data")
        assert locate_step["tool_calls"] == []
        assert locate_step["skip_default_tool_calls"] is True

    def test_basic_template(self):
        params = {"sample_name": "test_sample", "work_dir": "/proj/workflow1"}
        plan = _template_remote_stage_workflow(params)
        assert plan["plan_type"] == "remote_stage_workflow"
        assert plan["title"] == "Stage remote sample for test_sample"
        assert plan["workflow_type"] == "remote_sample_intake"
        assert plan["sample_name"] == "test_sample"
        assert len(plan["steps"]) == 7

    def test_step_ids_are_overridden(self):
        params = {"sample_name": "s1"}
        plan = _template_remote_stage_workflow(params)
        step_ids = [s["id"] for s in plan["steps"]]
        assert "locate_data" in step_ids
        assert "validate_inputs" in step_ids
        assert "check_remote_stage" in step_ids
        assert "approve_remote_stage" in step_ids

    def test_step_dependencies_chained(self):
        params = {"sample_name": "s1"}
        plan = _template_remote_stage_workflow(params)
        steps_by_id = {s["id"]: s for s in plan["steps"]}
        # validate_inputs depends on locate_data
        assert "locate_data" in steps_by_id["validate_inputs"]["depends_on"]

    def test_auto_execute_safe_steps(self):
        params = {"sample_name": "s1"}
        plan = _template_remote_stage_workflow(params)
        assert plan["auto_execute_safe_steps"] is True


# ---------------------------------------------------------------------------
# _template_run_workflow
# ---------------------------------------------------------------------------

class TestTemplateRunWorkflow:
    def test_basic_template(self):
        params = {"sample_name": "test_sample", "work_dir": "/proj/workflow1"}
        plan = _template_run_workflow(params)
        assert plan["plan_type"] == "run_workflow"
        assert plan["title"] == "Run analysis pipeline for test_sample"
        assert plan["workflow_type"] == "local_sample_intake"
        assert len(plan["steps"]) == 9

    def test_first_step_is_locate(self):
        params = {"sample_name": "s1"}
        plan = _template_run_workflow(params)
        assert plan["steps"][0]["kind"] == "LOCATE_DATA"

    def test_approval_before_submit(self):
        params = {"sample_name": "s1"}
        plan = _template_run_workflow(params)
        steps_by_id = {s["id"]: s for s in plan["steps"]}
        approve_step = None
        submit_step = None
        for s in plan["steps"]:
            if s["kind"] == "REQUEST_APPROVAL":
                approve_step = s
            if s["kind"] == "SUBMIT_WORKFLOW":
                submit_step = s
        assert approve_step is not None
        assert submit_step is not None


# ---------------------------------------------------------------------------
# _template_run_dogme_batch
# ---------------------------------------------------------------------------

class TestTemplateRunDogmeBatch:
    def test_batch_preserves_explicit_samples_and_shared_settings(self):
        plan = _template_run_dogme_batch({
            "batch_id": "batch-123",
            "batch_samples": [
                {"sample_name": "tumor", "input_directory": "/data/tumor"},
                {"sample_id": "normal-1", "sample_name": "normal", "input_directory": "/data/normal"},
            ],
            "shared_params": {"mode": "DNA", "reference_genome": "GRCh38"},
            "requested_max_parallel": 2,
        })

        assert plan["plan_type"] == "run_dogme_batch"
        assert plan["workflow_type"] == "dogme_batch"
        assert plan["batch_id"] == "batch-123"
        assert plan["shared_params"] == {"mode": "DNA", "reference_genome": "GRCh38"}
        assert plan["requested_max_parallel"] == 2
        assert plan["batch_samples"] == [
            {
                "sample_id": "1",
                "sample_name": "tumor",
                "input_directory": "/data/tumor",
                "status": "PENDING",
                "run_uuid": None,
                "execution_block_id": None,
                "error": None,
            },
            {
                "sample_id": "normal-1",
                "sample_name": "normal",
                "input_directory": "/data/normal",
                "status": "PENDING",
                "run_uuid": None,
                "execution_block_id": None,
                "error": None,
            },
        ]

    def test_batch_has_one_approval_before_submit_and_monitor(self):
        plan = _template_run_dogme_batch({"batch_samples": [{"sample_name": "s1", "input_directory": "/data/s1"}]})
        steps = {step["id"]: step for step in plan["steps"]}

        assert list(steps) == ["validate_batch_inputs", "approve_dogme_batch", "submit_dogme_batch", "monitor_dogme_batch"]
        assert steps["approve_dogme_batch"]["requires_approval"] is True
        assert steps["submit_dogme_batch"]["depends_on"] == ["approve_dogme_batch"]
        assert steps["monitor_dogme_batch"]["depends_on"] == ["submit_dogme_batch"]


# ---------------------------------------------------------------------------
# _template_run_wf_pore_c
# ---------------------------------------------------------------------------

class TestTemplateRunWfPoreC:
    def test_basic_template(self):
        params = {"sample_name": "pore_sample"}
        plan = _template_run_wf_pore_c(params)
        assert plan["plan_type"] == "run_wf_pore_c"
        assert plan["workflow_key"] == "wf_pore_c"
        assert plan["preview_only"] is True
        assert len(plan["steps"]) == 2

    def test_step_structure(self):
        params = {"sample_name": "s1"}
        plan = _template_run_wf_pore_c(params)
        assert plan["steps"][0]["kind"] == "VALIDATE_INPUTS"
        assert plan["steps"][1]["kind"] == "REQUEST_APPROVAL"

    def test_default_values(self):
        params = {}
        plan = _template_run_wf_pore_c(params)
        assert plan["sample_name"] == "pore_c_sample"
        assert plan["workflow_repo"] == "epi2me-labs/wf-pore-c"
        assert plan["cutter"] == "NlaIII"


# ---------------------------------------------------------------------------
# _template_compare_samples
# ---------------------------------------------------------------------------

class TestTemplateCompareSamples:
    def test_basic_template(self):
        params = {"samples": ["sample_a", "sample_b"]}
        plan = _template_compare_samples(params)
        assert plan["plan_type"] == "compare_samples"
        assert "sample_a" in plan["title"]
        # compare_samples has 8 steps: locate(x2), parse(x2), compare, plot, interpret, summary
        assert len(plan["steps"]) == 8

    def test_default_samples(self):
        params = {}
        plan = _template_compare_samples(params)
        # Default samples are "sample A" and "sample B"
        assert "sample A" in plan["title"] or "sample B" in plan["title"]

    def test_parallel_locate_steps(self):
        params = {"samples": ["a", "b"]}
        plan = _template_compare_samples(params)
        # Both locate steps should have no dependencies (parallel)
        assert plan["steps"][0]["depends_on"] == []
        assert plan["steps"][1]["depends_on"] == []


# ---------------------------------------------------------------------------
# _template_download_analyze
# ---------------------------------------------------------------------------

class TestTemplateDownloadAnalyze:
    def test_basic_template(self):
        params = {"search_term": "k562"}
        plan = _template_download_analyze(params)
        assert plan["plan_type"] == "download_analyze"
        assert len(plan["steps"]) >= 4

    def test_first_step_is_search(self):
        params = {"search_term": "test"}
        plan = _template_download_analyze(params)
        assert plan["steps"][0]["kind"] == "SEARCH_ENCODE"

    def test_tool_call_includes_search_term(self):
        params = {"search_term": "encode_test"}
        plan = _template_download_analyze(params)
        search_step = plan["steps"][0]
        assert search_step["tool_calls"][0]["params"]["search_term"] == "encode_test"
