import ast
import os
from pathlib import Path


PART2_PATH = Path(__file__).resolve().parents[2] / "ui" / "appui_block_part2.py"


def _load_part2_namespace() -> dict:
    source = PART2_PATH.read_text()
    tree = ast.parse(source, filename=str(PART2_PATH))
    include_names = {
        "_WF_PORE_C_OUTPUT_FLAG_ORDER",
        "_WF_PORE_C_OUTPUT_FLAG_LABELS",
        "_format_usage_duration",
        "_format_usage_memory_mb",
        "_wf_pore_c_ui_enabled",
        "_workflow_key_from_payload",
        "_workflow_usage_metrics",
        "_wf_pore_c_output_flag_values",
        "_workflow_path_label",
        "_wf_pore_c_output_flag_summary",
        "_workflow_specific_metadata",
        "_staging_card_metadata",
        "_execution_run_metadata",
    }

    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in include_names:
            selected_nodes.append(node)
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in include_names:
                    selected_nodes.append(node)
                    break

    namespace: dict = {"os": os}
    module = ast.Module(body=selected_nodes, type_ignores=[])
    exec(compile(module, filename=str(PART2_PATH), mode="exec"), namespace)
    return namespace


def test_staging_card_metadata_preserves_dogme_mode_label(monkeypatch):
    monkeypatch.delenv("WF_PORE_C_ENABLED", raising=False)
    namespace = _load_part2_namespace()

    metadata = namespace["_staging_card_metadata"](
        {"workflow_key": "dogme", "mode": "RNA"},
        remote_profile="hpc3",
        progress=25,
    )

    assert metadata == {"Mode": "RNA", "Target": "hpc3", "Progress": "25%"}


def test_staging_card_metadata_uses_workflow_identity_for_wf_pore_c(monkeypatch):
    monkeypatch.setenv("WF_PORE_C_ENABLED", "true")
    namespace = _load_part2_namespace()

    metadata = namespace["_staging_card_metadata"](
        {"workflow_key": "wf_pore_c", "input_type": "fastq"},
        remote_profile="hpc3",
        progress=40,
    )

    assert metadata == {
        "Workflow": "wf-pore-c",
        "Target": "hpc3",
        "Progress": "40%",
        "Input": "FASTQ",
    }


def test_workflow_specific_metadata_summarizes_wf_pore_c_fields(monkeypatch):
    monkeypatch.setenv("WF_PORE_C_ENABLED", "true")
    namespace = _load_part2_namespace()

    metadata = namespace["_workflow_specific_metadata"](
        {
            "workflow_key": "wf_pore_c",
            "reference_fasta": "/refs/hg38.fa.gz",
            "sample_sheet": "/refs/porec.csv",
            "cutter": "DpnII",
            "output_flags": {"pairs": True, "bed": True},
        }
    )

    assert metadata == {
        "Reference": "hg38.fa.gz",
        "Sample Sheet": "porec.csv",
        "Cutter": "DpnII",
        "Outputs": "Pairs, mcool, BED, Paired-End",
    }


def test_execution_run_metadata_preserves_dogme_mode_and_folder(monkeypatch):
    monkeypatch.delenv("WF_PORE_C_ENABLED", raising=False)
    namespace = _load_part2_namespace()

    metadata = namespace["_execution_run_metadata"](
        {"workflow_key": "dogme", "mode": "DNA"},
        {},
        run_uuid="run-1",
        workflow_label="workflow7",
        started_label="2026-05-01 12:00",
        completed_label="",
        duration_label="15m",
        is_script_job=False,
        script_id="",
    )

    assert metadata == {
        "Mode": "DNA",
        "Run UUID": "run-1",
        "Workflow": "workflow7",
        "Started": "2026-05-01 12:00",
        "Duration": "15m",
    }


def test_execution_run_metadata_uses_workflow_identity_for_wf_pore_c(monkeypatch):
    monkeypatch.setenv("WF_PORE_C_ENABLED", "true")
    namespace = _load_part2_namespace()

    metadata = namespace["_execution_run_metadata"](
        {"workflow_key": "wf_pore_c"},
        {},
        run_uuid="run-2",
        workflow_label="workflow9",
        started_label="",
        completed_label="",
        duration_label="",
        is_script_job=False,
        script_id="",
    )

    assert metadata == {
        "Workflow": "wf-pore-c",
        "Run UUID": "run-2",
        "Folder": "workflow9",
    }


def test_completed_execution_branch_renders_workflow_usage_section():
    source = PART2_PATH.read_text()
    tree = ast.parse(source, filename=str(PART2_PATH))
    render_fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_block_part2"
    )

    completed_branch = None
    for node in ast.walk(render_fn):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if not isinstance(test.left, ast.Name) or test.left.id != "status_str":
            continue
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            continue
        if len(test.comparators) != 1:
            continue
        comparator = test.comparators[0]
        if isinstance(comparator, ast.Constant) and comparator.value == "COMPLETED":
            completed_branch = ast.get_source_segment(source, node)
            break

    assert completed_branch is not None
    assert "_workflow_usage_metrics(workflow_usage)" in completed_branch
    assert 'st.caption("Workflow usage")' in completed_branch


def test_workflow_usage_metrics_show_billing_by_account(monkeypatch):
    monkeypatch.delenv("WF_PORE_C_ENABLED", raising=False)
    namespace = _load_part2_namespace()

    metrics = namespace["_workflow_usage_metrics"](
        {
            "cpu_seconds": 124.0,
            "gpu_seconds": 55.0,
            "billing_units": 9.999,
            "billing_entries": [
                {
                    "resource_type": "CPU",
                    "account": "cpu-default",
                    "billing_hours": 0.019,
                },
                {
                    "resource_type": "GPU",
                    "account": "gpu-default",
                    "billing_hours": 0.031,
                },
            ],
            "billing_hours_by_account": {
                "cpu-default": 0.019,
                "gpu-default": 0.031,
            },
        }
    )

    assert metrics["CPU Time"] == "2m 4s"
    assert metrics["GPU Time"] == "55s"
    assert metrics["CPU Billing Hours (cpu-default)"] == 0.019
    assert metrics["GPU Billing Hours (gpu-default)"] == 0.031
    assert "SLURM Billing Hours" not in metrics


def test_workflow_usage_metrics_prefer_queue_time_labels_for_slurm(monkeypatch):
    monkeypatch.delenv("WF_PORE_C_ENABLED", raising=False)
    namespace = _load_part2_namespace()

    metrics = namespace["_workflow_usage_metrics"](
        {
            "cpu_seconds": 170.0,
            "cpu_queue_seconds": 100.0,
            "gpu_seconds": 55.0,
            "gpu_queue_seconds": 55.0,
            "billing_entries": [
                {
                    "resource_type": "CPU",
                    "account": "cpu-default",
                    "billing_hours": 0.028,
                },
                {
                    "resource_type": "GPU",
                    "account": "gpu-default",
                    "billing_hours": 0.031,
                },
            ],
        }
    )

    assert metrics["CPU Queue Time"] == "1m 40s"
    assert metrics["GPU Queue Time"] == "55s"
    assert "CPU Time" not in metrics
    assert "GPU Time" not in metrics
    assert metrics["CPU Billing Hours (cpu-default)"] == 0.028
    assert metrics["GPU Billing Hours (gpu-default)"] == 0.031
