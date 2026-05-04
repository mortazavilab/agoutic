from pathlib import Path

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