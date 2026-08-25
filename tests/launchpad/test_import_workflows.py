import json
from pathlib import Path

import pytest

from launchpad import config as launchpad_config
import launchpad.import_workflows as import_workflows
from launchpad.import_workflows import copy_local_results_to_workflow, infer_local_workflow_metadata


def test_infer_local_workflow_metadata_reads_any_top_level_config_suffix(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    workflow_dir.mkdir()
    pod5_source = tmp_path / "pod5-source"
    pod5_source.mkdir()
    (workflow_dir / "pod5").symlink_to(pod5_source, target_is_directory=True)
    (workflow_dir / "custom-run.config").write_text(
        "\n".join(
            [
                "params {",
                "    sample = 'tumor-a'",
                "    readType = 'DNA'",
                "    modifications = '5mCG_5hmCG,6mA'",
                "    genome_annot_refs = [",
                "        [name: 'GRCh38', genome: '/refs/grch38.fa', annot: '/refs/grch38.gtf'],",
                "        [name: 'mm39', genome: '/refs/mm39.fa', annot: '/refs/mm39.gtf']",
                "    ]",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    metadata = infer_local_workflow_metadata(workflow_dir)

    assert metadata.sample_name == "tumor-a"
    assert metadata.mode == "DNA"
    assert metadata.reference_genome == ["GRCh38", "mm39"]
    assert metadata.modifications == "5mCG_5hmCG,6mA"
    assert metadata.config_path.endswith("custom-run.config")
    assert metadata.input_directory == str(pod5_source.resolve())
    assert metadata.source_complete is True


def test_infer_local_workflow_metadata_reads_agoutic_metadata_for_wf_pore_c(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow8"
    workflow_dir.mkdir()
    (workflow_dir / ".nextflow_success").write_text("ok\n", encoding="utf-8")
    (workflow_dir / ".agoutic.workflow.json").write_text(
        json.dumps(
            {
                "workflow_key": "wf_pore_c",
                "validated_inputs": {
                    "input_path": "/inputs/porec-fastq",
                    "reference_fasta": "/refs/grch38.fa",
                    "sample_name": "POREC_A",
                },
                "summary_contract": {
                    "workflow_key": "wf_pore_c",
                    "sample_name": "POREC_A",
                    "workflow_version": "v1.3.1",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        import_workflows,
        "REFERENCE_GENOMES",
        {
            "default": {},
            "GRCh38": {"fasta": "/refs/grch38.fa"},
        },
    )

    metadata = infer_local_workflow_metadata(workflow_dir)

    assert metadata.workflow_key == "wf_pore_c"
    assert metadata.sample_name == "POREC_A"
    assert metadata.mode is None
    assert metadata.reference_genome == ["GRCh38"]
    assert metadata.modifications is None
    assert metadata.config_path == str(workflow_dir / ".agoutic.workflow.json")
    assert metadata.input_directory == "/inputs/porec-fastq"
    assert metadata.source_complete is True


def test_copy_local_results_to_workflow_copies_subset_and_root_config(tmp_path):
    source_dir = tmp_path / "source-workflow"
    destination_dir = tmp_path / "project" / "workflow3"
    (source_dir / "annot").mkdir(parents=True)
    (source_dir / "annot" / "genes.tsv").write_text("ok\n", encoding="utf-8")
    (source_dir / "work").mkdir()
    (source_dir / "work" / "ignored.txt").write_text("ignore\n", encoding="utf-8")
    (source_dir / "nextflow.config").write_text("params { sample = 'x' }\n", encoding="utf-8")
    (source_dir / "report.html").write_text("<html></html>\n", encoding="utf-8")

    artifacts = copy_local_results_to_workflow(source_dir, destination_dir, full_copy=False)

    assert artifacts["directories"] == ["annot"]
    assert sorted(artifacts["files"]) == ["nextflow.config", "report.html"]
    assert (destination_dir / "annot" / "genes.tsv").exists()
    assert (destination_dir / "nextflow.config").exists()
    assert (destination_dir / "report.html").exists()
    assert not (destination_dir / "work").exists()


def test_copy_local_results_to_workflow_copies_wf_pore_c_subset(monkeypatch, tmp_path):
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)

    source_dir = tmp_path / "source-wf-pore-c"
    destination_dir = tmp_path / "project" / "workflow4"
    for dirname in ("pairs", "cooler", "chromunity", "filtered_out"):
        (source_dir / dirname).mkdir(parents=True, exist_ok=True)
        (source_dir / dirname / "artifact.txt").write_text("ok\n", encoding="utf-8")
    (source_dir / ".agoutic.workflow.json").write_text(
        json.dumps({"workflow_key": "wf_pore_c", "summary_contract": {"sample_name": "POREC_A"}}, indent=2),
        encoding="utf-8",
    )
    (source_dir / "wf-pore-c-report.html").write_text("<html></html>\n", encoding="utf-8")
    (source_dir / "nextflow.config").write_text("ignored\n", encoding="utf-8")
    (source_dir / "work").mkdir()

    artifacts = copy_local_results_to_workflow(
        source_dir,
        destination_dir,
        full_copy=False,
        workflow_key="wf_pore_c",
    )

    assert artifacts["directories"] == ["pairs", "cooler", "chromunity", "filtered_out"]
    assert sorted(artifacts["files"]) == [".agoutic.workflow.json", "wf-pore-c-report.html"]
    assert (destination_dir / "pairs" / "artifact.txt").exists()
    assert (destination_dir / "cooler" / "artifact.txt").exists()
    assert (destination_dir / ".agoutic.workflow.json").exists()
    assert (destination_dir / "wf-pore-c-report.html").exists()
    assert not (destination_dir / "nextflow.config").exists()
    assert not (destination_dir / "work").exists()


@pytest.mark.asyncio
async def test_infer_remote_workflow_metadata_reads_agoutic_metadata_for_wf_pore_c(monkeypatch):
    monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)
    monkeypatch.setattr(
        import_workflows,
        "REFERENCE_GENOMES",
        {
            "default": {},
            "GRCh38": {"fasta": "/refs/grch38.fa"},
        },
    )

    metadata_path = "/remote/workflow8/.agoutic.workflow.json"
    success_path = "/remote/workflow8/.nextflow_success"

    class _FakeConn:
        async def path_exists(self, path):
            return path in {metadata_path, success_path}

        async def run(self, command, check=False):
            assert metadata_path in command
            return type(
                "Result",
                (),
                {
                    "stdout": json.dumps(
                        {
                            "workflow_key": "wf_pore_c",
                            "validated_inputs": {
                                "input_path": "/inputs/porec-fastq",
                                "reference_fasta": "/refs/grch38.fa",
                                "sample_name": "POREC_REMOTE",
                            },
                            "summary_contract": {
                                "workflow_key": "wf_pore_c",
                                "sample_name": "POREC_REMOTE",
                                "workflow_version": "v1.3.1",
                            },
                        }
                    )
                },
            )()

    metadata = await import_workflows.infer_remote_workflow_metadata(_FakeConn(), "/remote/workflow8")

    assert metadata.workflow_key == "wf_pore_c"
    assert metadata.sample_name == "POREC_REMOTE"
    assert metadata.mode is None
    assert metadata.reference_genome == ["GRCh38"]
    assert metadata.modifications is None
    assert metadata.config_path == metadata_path
    assert metadata.input_directory == "/inputs/porec-fastq"
    assert metadata.source_complete is True