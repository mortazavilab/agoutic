"""Tests for launchpad/nextflow_executor.py."""

from pathlib import Path

from launchpad.config import REFERENCE_GENOMES
from launchpad.nextflow_executor import NextflowConfig, NextflowExecutor


class TestGenerateConfig:
    def test_dna_mode_uses_defaults_for_string_reference(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-a",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome="GRCh38",
        )

        assert "sample = 'sample-a'" in config
        assert "readType = 'DNA'" in config
        assert "modifications = '5mCG_5hmCG,6mA'" in config
        assert "minCov = 1" in config
        assert "[name: 'GRCh38'" in config
        assert f"genome: '{REFERENCE_GENOMES['GRCh38']['fasta']}'" in config
        assert f"annot: '{REFERENCE_GENOMES['GRCh38']['gtf']}'" in config

    def test_multi_genome_config_renders_all_requested_genomes(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-b",
            mode="RNA",
            input_dir="/tmp/input",
            reference_genome=["GRCh38", "mm39"],
        )

        assert "[name: 'GRCh38'" in config
        assert "[name: 'mm39'" in config
        assert f"kallistoIndex = '{REFERENCE_GENOMES['GRCh38'].get('kallisto_index', '/home/seyedam/genRefs/mm39GencM36_k63.idx')}'" in config
        assert f"t2g = '{REFERENCE_GENOMES['GRCh38'].get('kallisto_t2g', '/home/seyedam/genRefs/mm39GencM36_k63.t2g')}'" in config
        assert "modifications = 'inosine_m6A_2OmeA,pseU_2OmeU,m5C_2OmeC,2OmeG'" in config
        assert "minCov = 3" in config

    def test_mm39_config_uses_reference_folder_for_kallisto_sidecars(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-mm39",
            mode="RNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
        )

        assert f"kallistoIndex = '{REFERENCE_GENOMES['mm39']['kallisto_index']}'" in config
        assert f"t2g = '{REFERENCE_GENOMES['mm39']['kallisto_t2g']}'" in config

    def test_explicit_modifications_override_mode_defaults(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-c",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome="GRCh38",
            modifications="custom_mods",
            min_cov=7,
            accuracy="hac",
            max_gpu_tasks=3,
        )

        assert "modifications = 'custom_mods'" in config
        assert "minCov = 7" in config
        assert 'accuracy = "hac"' in config
        assert "maxForks = 3  // Limit concurrent GPU tasks" in config

    def test_cdna_mode_disables_modifications(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-d",
            mode="CDNA",
            input_dir="/tmp/input",
            reference_genome="mm39",
        )

        assert "readType = 'CDNA'" in config
        assert "// No modifications for CDNA mode" in config
        assert "modifications = ''" in config
        assert "minCov = 3" in config

    def test_unknown_genome_falls_back_to_mm39_reference_paths(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-e",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["unknown-genome"],
        )

        assert "[name: 'unknown-genome'" in config
        assert f"genome: '{REFERENCE_GENOMES['mm39']['fasta']}'" in config
        assert f"annot: '{REFERENCE_GENOMES['mm39']['gtf']}'" in config

    def test_local_execution_keeps_docker_runtime(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-local",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="local",
        )

        assert "executor = 'local'" in config
        assert "docker {" in config
        assert "singularity {" not in config
        assert "clusterOptions = \"--account=${cpuAccount}\"" not in config

    def test_slurm_execution_uses_accounts_partitions_and_singularity(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-slurm",
            mode="RNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
            slurm_cpu_partition="cpu-part",
            slurm_gpu_partition="gpu-part",
            slurm_cpu_account="cpu-acct",
            slurm_gpu_account="gpu-acct",
            max_gpu_tasks=2,
        )

        assert "executor = 'slurm'" in config
        assert "cpuPartition = 'cpu-part'" in config
        assert "gpuPartition = 'gpu-part'" in config
        assert "cpuAccount = 'cpu-acct'" in config
        assert "gpuAccount = 'gpu-acct'" in config
        assert "clusterOptions = \"--account=${cpuAccount}\"" in config
        assert "queue = \"${cpuPartition}\"" in config
        assert "clusterOptions = \"--account=${gpuAccount} --gres=gpu:1\"" in config
        assert "queue = \"${gpuPartition}\"" in config
        assert f"--bind {REFERENCE_GENOMES['mm39']['fasta']}" not in config
        assert "containerOptions = \"--nv\"" in config
        assert "singularity {" in config
        assert "autoMounts = true" in config
        assert "docker {" not in config

    def test_slurm_reference_overrides_replace_kallisto_sidecars(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-remote",
            mode="RNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            reference_overrides={
                "mm39": {
                    "fasta": "/remote/ref/mm39/IGVFFI9282QLXO.fasta",
                    "gtf": "/remote/ref/mm39/IGVFFI4777RDZK.gtf",
                    "kallisto_index": "/remote/ref/mm39/mm39.idx",
                    "kallisto_t2g": "/remote/ref/mm39/mm39.t2g",
                }
            },
            execution_mode="slurm",
        )

        assert "kallistoIndex = '/remote/ref/mm39/mm39.idx'" in config
        assert "t2g = '/remote/ref/mm39/mm39.t2g'" in config


class TestWriteConfigFile:
    def test_write_config_file_creates_parent_directories(self, tmp_path):
        output_path = tmp_path / "nested" / "workflow" / "nextflow.config"

        written = NextflowConfig.write_config_file("params {\n}\n", output_path)

        assert written == output_path
        assert output_path.exists()
        assert output_path.read_text() == "params {\n}\n"


class TestNextWorkflowNumber:
    def test_missing_project_dir_starts_at_one(self, tmp_path):
        project_dir = tmp_path / "missing-project"

        assert NextflowExecutor._next_workflow_number(project_dir) == 1

    def test_ignores_non_matching_and_invalid_workflow_directories(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "workflow1").mkdir()
        (project_dir / "workflow09").mkdir()
        (project_dir / "workflowx").mkdir()
        (project_dir / "notes").mkdir()
        (project_dir / "workflow3.txt").write_text("not a dir")

        assert NextflowExecutor._next_workflow_number(project_dir) == 10
