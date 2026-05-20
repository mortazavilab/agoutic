"""Contract tests for Launchpad workflow executor registry."""

import pytest

from launchpad import config as launchpad_config
from launchpad.workflow_executors import get_workflow_executor, workflow_executor_keys
from launchpad.workflow_executors.base import UnknownWorkflowKeyError


def test_known_workflow_keys_resolve_to_expected_executors():
    assert workflow_executor_keys() == ("dogme", "wf_pore_c")
    assert get_workflow_executor("dogme").workflow_key == "dogme"
    assert get_workflow_executor("WF_PORE_C").workflow_key == "wf_pore_c"


def test_registered_executors_expose_required_contract_methods():
    for workflow_key in workflow_executor_keys():
        executor = get_workflow_executor(workflow_key)
        assert hasattr(executor, "validate_submission")
        assert hasattr(executor, "remote_validate_submission")
        assert hasattr(executor, "remote_stage_inputs")
        assert hasattr(executor, "remote_reference_assets")
        assert hasattr(executor, "remote_work_dir_path")
        assert hasattr(executor, "remote_config_artifacts")
        assert hasattr(executor, "remote_build_command")
        assert hasattr(executor, "remote_result_sync_spec")
        assert hasattr(executor, "remote_summary_contract")
        assert hasattr(executor, "validate_inputs")
        assert hasattr(executor, "stage_inputs")
        assert hasattr(executor, "render_nextflow_config")
        assert hasattr(executor, "build_command")
        assert hasattr(executor, "result_sync_spec")
        assert hasattr(executor, "summary_contract")
        assert hasattr(executor, "build_local_submit_kwargs")
        assert hasattr(executor, "build_backend_submit_params")
        assert hasattr(executor, "build_preview")


def test_unknown_workflow_key_fails_cleanly():
    try:
        get_workflow_executor("mystery_workflow")
    except UnknownWorkflowKeyError as exc:
        assert "Unknown workflow_key 'mystery_workflow'" in str(exc)
        assert "dogme" in str(exc)
        assert "wf_pore_c" in str(exc)
    else:
        raise AssertionError("Expected UnknownWorkflowKeyError for unknown workflow key")


def test_wf_pore_c_preview_contains_pinned_revision_and_defaults(monkeypatch):
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", False)

    executor = get_workflow_executor("wf_pore_c")
    preview = executor.build_preview(
        sample_name="pore-c-sample",
        input_type="bam",
        input_path="/data/pore-c.bam",
        reference_fasta="/data/ref.fa",
        output_directory="/data/workflow5",
    )

    assert preview.workflow_key == "wf_pore_c"
    assert preview.supports_submission is False
    assert preview.command.startswith("nextflow")
    assert "epi2me-labs/wf-pore-c" in preview.command
    assert "-r" in preview.command
    assert "v1.3.1" in preview.command
    assert "--pairs" in preview.command
    assert "--mcool" in preview.command
    assert "--cutter" in preview.command


def test_wf_pore_c_submission_gate_tracks_feature_flag(monkeypatch):
    executor = get_workflow_executor("wf_pore_c")

    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)

    assert executor.supports_submission is True
    executor.validate_submission(mode=None)
    with pytest.raises(ValueError, match="mode"):
        executor.validate_submission(mode="DNA")

    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", False)

    assert executor.supports_submission is False
    with pytest.raises(ValueError, match="WF_PORE_C_ENABLED"):
        executor.validate_submission(mode=None)


def test_dogme_backend_submit_params_include_workflow_executor():
    executor = get_workflow_executor("dogme")
    request = type(
        "Request",
        (),
        {
            "project_id": "proj-1",
            "user_id": "user-1",
            "username": "alice",
            "project_slug": "proj-1",
            "sample_name": "sample-1",
            "mode": "RNA",
            "input_type": "pod5",
            "input_directory": "/data/input",
            "reference_genome": ["mm39"],
            "modifications": None,
            "entry_point": None,
            "modkit_filter_threshold": 0.9,
            "min_cov": None,
            "per_mod": 5,
            "accuracy": "sup",
            "local_max_task_cpus": None,
            "local_max_task_memory_gb": None,
            "custom_dogme_profile": None,
            "custom_dogme_bind_paths": [],
            "resume_from_dir": None,
            "parent_block_id": None,
            "ssh_profile_id": "profile-1",
            "slurm_account": None,
            "slurm_partition": None,
            "slurm_gpu_account": None,
            "slurm_gpu_partition": None,
            "slurm_cpus": None,
            "slurm_memory_gb": None,
            "slurm_walltime": None,
            "slurm_gpus": None,
            "slurm_gpu_type": None,
            "remote_base_path": "/remote/agoutic",
            "remote_input_path": None,
            "staged_remote_input_path": None,
            "cache_preflight": None,
            "result_destination": "local",
        },
    )()

    params = executor.build_backend_submit_params(
        request=request,
        workflow_number=7,
        max_gpu_tasks=None,
    )

    assert params["workflow_executor"] is executor


def test_wf_pore_c_remote_submission_is_allowed_when_flag_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)

    executor = get_workflow_executor("wf_pore_c")
    input_bam = tmp_path / "sample.bam"
    input_bam.write_text("bam", encoding="utf-8")
    reference_fasta = tmp_path / "reference.fa"
    reference_fasta.write_text(">chr1\nA\n", encoding="utf-8")

    executor.remote_validate_submission(
        request=type(
            "Request",
            (),
            {
                "mode": None,
                "input_type": "bam",
                "input_directory": str(input_bam),
                "reference_fasta": str(reference_fasta),
                "remote_input_path": None,
                "staged_remote_input_path": None,
                "sample_sheet": None,
                "vcf": None,
            },
        )()
    )