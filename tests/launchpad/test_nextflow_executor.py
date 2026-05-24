"""Tests for launchpad/nextflow_executor.py."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import launchpad.nextflow_executor as nextflow_module
from launchpad import config as launchpad_config
from launchpad.config import (
    REFERENCE_GENOMES,
    DOGME_DNA_MODKITBASE,
    DOGME_DNA_MODKITMODEL,
    DOGME_DNA_OPENCHROM_BINARY_DIR,
    DOGME_DNA_OPENCHROM_LIBTORCH,
    DOGME_DNA_OPENCHROM_MODEL,
    DOGME_DNA_OPENCHROM_MODKITBASE,
    LOCAL_DEFAULT_MAX_TASK_MEMORY_GB,
    SLURM_DEFAULT_CPU_MEMORY_GB,
)
from launchpad.workflow_accounting import (
    is_gpu_task_name,
    summarize_slurm_workflow_usage,
    summarize_nextflow_trace_text,
)
from launchpad.nextflow_executor import (
    NextflowConfig,
    NextflowExecutor,
    resolve_dogme_profile_content,
    resolve_dogme_profile_task_runtime_exports,
    resolve_local_max_task_cpus,
    resolve_local_max_task_memory_gb,
    resolve_slurm_cpu_memory_gb,
)
from launchpad.workflow_executors import get_workflow_executor


class TestWorkflowAccounting:
    def test_summarize_nextflow_trace_text_tracks_cpu_memory_and_gpu_runtime(self):
        summary = summarize_nextflow_trace_text(
            "\n".join(
                [
                    "task_id\thash\tnative_id\tname\tstatus\texit\tsubmit\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar",
                    "1\tabc\t1001\tmainWorkflow:doradoTask (1)\tCOMPLETED\t0\t2026-05-24T10:00:00\t1m 30s\t60s\t320%\t1.5 GB\t2 GB\t0\t0",
                    "2\tbcd\t1002\tmainWorkflow:minimapTask (1)\tCOMPLETED\t0\t2026-05-24T10:02:00\t2m\t120s\t180%\t768 MB\t1.25 GB\t0\t0",
                    "3\tcde\t1003\tmainWorkflow:openChromatinTaskBg (1)\tFAILED\t1\t2026-05-24T10:05:00\t30s\t25s\t250%\t2 GiB\t3 GiB\t0\t0",
                    "4\tdef\t1004\tlauncher\tCOMPLETED\t0\t2026-05-24T10:06:00\t1s\t1s\t100%\t4 MB\t8 MB\t0\t0",
                    "5\tefg\t1005\tmainWorkflow:doradoTask (2)\tCACHED\t0\t2026-05-24T10:07:00\t0s\t0s\t0%\t0 MB\t0 MB\t0\t0",
                ]
            ),
            accounting_mode="local",
            trace_path="/tmp/workflow1/sample_trace.txt",
        )

        assert summary == {
            "source": "nextflow_trace",
            "accounting_mode": "local",
            "accounted_task_count": 4,
            "completed_task_count": 2,
            "failed_task_count": 1,
            "cached_task_count": 1,
            "cpu_seconds": 470.5,
            "task_realtime_seconds": 205.0,
            "estimated_gpu_task_seconds": 85.0,
            "max_rss_mb": 2048.0,
            "max_vmem_mb": 3072.0,
            "trace_path": "/tmp/workflow1/sample_trace.txt",
        }

    def test_is_gpu_task_name_matches_expected_process_suffixes(self):
        assert is_gpu_task_name("mainWorkflow:doradoTask (1)") is True
        assert is_gpu_task_name("reports:openChromatinTaskBed (7)") is True
        assert is_gpu_task_name("mainWorkflow:minimapTask (2)") is False

    def test_summarize_slurm_workflow_usage_tracks_billing_hours_by_account(self):
        summary = summarize_slurm_workflow_usage(
            "\n".join(
                [
                    "task_id\thash\tnative_id\tname\tstatus\texit\tsubmit\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar",
                    "1\tabc\t1001\tmainWorkflow:doradoTask (1)\tCOMPLETED\t0\t2026-05-24T10:00:00\t1m\t55s\t300%\t1.5 GB\t2 GB\t0\t0",
                    "2\tbcd\t1002\tmainWorkflow:minimapTask (1)\tCOMPLETED\t0\t2026-05-24T10:02:00\t90s\t70s\t150%\t768 MB\t1.25 GB\t0\t0",
                ]
            ),
            "\n".join(
                [
                    "1001|gpu-default|COMPLETED|55|00:01:10|2G|billing=2,cpu=4,gres/gpu=1",
                    "1002|cpu-default|COMPLETED|70|00:01:30|1G|billing=1,cpu=2",
                    "2000|cpu-default|RUNNING|30|00:00:10|512M|billing=1,cpu=1",
                ]
            ),
            trace_path="/tmp/workflow1/trace.txt",
            launcher_job_id="2000",
        )

        assert summary["source"] == "slurm_sacct+nextflow_trace"
        assert summary["cpu_seconds"] == 170.0
        assert summary["task_realtime_seconds"] == 155.0
        assert summary["billing_units"] == 0.058
        assert summary["billing_hours_by_account"] == {
            "cpu-default": 0.028,
            "gpu-default": 0.031,
        }
        assert summary["billing_entries"] == [
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
        ]
        assert summary["slurm_accounted_job_count"] == 3
        assert summary["slurm_launcher_accounted"] is True
        assert summary["gpu_seconds"] == 55.0


class TestGenerateConfig:
    def test_resolve_slurm_cpu_memory_gb_applies_default_only_when_missing(self):
        assert resolve_slurm_cpu_memory_gb(None) == SLURM_DEFAULT_CPU_MEMORY_GB
        assert resolve_slurm_cpu_memory_gb(16) == 16
        assert resolve_slurm_cpu_memory_gb(192) == 192

    def test_resolve_local_max_task_memory_gb_applies_default_only_when_missing(self):
        assert resolve_local_max_task_memory_gb(None) == LOCAL_DEFAULT_MAX_TASK_MEMORY_GB
        assert resolve_local_max_task_memory_gb(48) == 48

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
        assert "minCov = 3" in config
        assert "perMod = 5" in config
        assert 'accuracy = "hac"' in config
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
        assert (
            f"annot: '{REFERENCE_GENOMES['GRCh38']['gtf']}'],\n"
            f"        [name: 'mm39', genome: '{REFERENCE_GENOMES['mm39']['fasta']}'"
        ) in config
        assert f"kallistoIndex = '{REFERENCE_GENOMES['GRCh38']['kallisto_index']}'" in config
        assert f"t2g = '{REFERENCE_GENOMES['GRCh38']['kallisto_t2g']}'" in config
        assert "modifications = 'inosine_m6A_2OmeA,pseU_2OmeU,m5C_2OmeC,2OmeG'" in config
        assert "minCov = 3" in config
        assert 'accuracy = "sup"' in config

    def test_multi_genome_config_includes_commas_between_reference_maps(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-mm39-mad1",
            mode="CDNA",
            input_dir="/tmp/input",
            reference_genome=["mm39", "mad1"],
        )

        assert (
            f"annot: '{REFERENCE_GENOMES['mm39']['gtf']}'],\n"
            f"        [name: 'mad1', genome: '{REFERENCE_GENOMES['mad1']['fasta']}'"
        ) in config

    def test_grch38_config_uses_human_kallisto_sidecars(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-grch38",
            mode="RNA",
            input_dir="/tmp/input",
            reference_genome=["GRCh38"],
        )

        assert f"kallistoIndex = '{REFERENCE_GENOMES['GRCh38']['kallisto_index']}'" in config
        assert f"t2g = '{REFERENCE_GENOMES['GRCh38']['kallisto_t2g']}'" in config

    def test_mm39_config_uses_reference_folder_for_kallisto_sidecars(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-mm39",
            mode="RNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
        )

        assert f"kallistoIndex = '{REFERENCE_GENOMES['mm39']['kallisto_index']}'" in config
        assert f"t2g = '{REFERENCE_GENOMES['mm39']['kallisto_t2g']}'" in config

    def test_mad1_dna_config_omits_kallisto_when_sidecars_are_not_configured(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-mad1",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mad1"],
        )

        assert "[name: 'mad1'" in config
        assert f"genome: '{REFERENCE_GENOMES['mad1']['fasta']}'" in config
        assert f"annot: '{REFERENCE_GENOMES['mad1']['gtf']}'" in config
        assert "kallistoIndex =" not in config
        assert "t2g =" not in config

    def test_mad1_rna_config_omits_kallisto_when_sidecars_are_not_configured(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-mad1-rna",
            mode="RNA",
            input_dir="/tmp/input",
            reference_genome=["mad1"],
        )

        assert "[name: 'mad1'" in config
        assert f"genome: '{REFERENCE_GENOMES['mad1']['fasta']}'" in config
        assert f"annot: '{REFERENCE_GENOMES['mad1']['gtf']}'" in config
        assert "kallistoIndex =" not in config
        assert "t2g =" not in config

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

    def test_default_gpu_concurrency_omits_maxforks(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-unbounded",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome="GRCh38",
        )

        assert "maxForks =" not in config

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
        assert 'accuracy = "hac"' in config

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
        assert "apptainer {" not in config
        assert "clusterOptions = \"--account=${cpuAccount}\"" not in config

    def test_generated_config_uses_explicit_params_namespace_for_derived_paths(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-derived-paths",
            mode="CDNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="local",
        )

        assert 'topDir = "${launchDir}"' in config
        assert 'modDir = "${params.topDir}/dorModels"' in config
        assert 'dorDir = "${params.topDir}/dor12-${params.sample}"' in config
        assert 'podDir = "${params.topDir}/pod5"' in config
        assert 'kallistoDir = "${params.topDir}/kallisto"' in config
        assert '"${topDir}/' not in config
        assert '${sample}' not in config

    def test_local_execution_defaults_max_task_memory_to_64_gb(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-local-default-memory",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="local",
        )

        expected_local_cpus = resolve_local_max_task_cpus(None)
        assert f"withName: 'modkitTask' {{\n        memory = '64 GB'\n        cpus = {expected_local_cpus}" in config
        assert f"withName: 'minimapTask' {{\n        cpus = {expected_local_cpus}\n        memory = '64 GB'" in config

    def test_local_execution_caps_task_cpu_and_memory_requests(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-local-capped",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="local",
            local_max_task_cpus=8,
            local_max_task_memory_gb=48,
        )

        assert "withName: 'extractfastqTask' {\n        // Matches the script's thread count and gives safe memory buffer\n        cpus = 6" in config
        assert "withName: 'modkitTask' {\n        memory = '48 GB'\n        cpus = 8" in config
        assert "withName: 'minimapTask' {\n        cpus = 8\n        memory = '48 GB'" in config

    def test_resolve_local_max_task_cpus_clamps_to_host_availability(self, monkeypatch):
        monkeypatch.setattr(nextflow_module.os, "cpu_count", lambda: 12)
        assert resolve_local_max_task_cpus(20) == 12
        assert resolve_local_max_task_cpus(6) == 6

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
            slurm_cpus=4,
            slurm_memory_gb=16,
            slurm_walltime="48:00:00",
            slurm_bind_paths=["/share/crsp/lab/seyedam/share/agoutic/seyedam/testslurm1"],
            apptainer_cache_dir="/share/crsp/lab/seyedam/share/agoutic/seyedam/.nxf-apptainer-cache",
            max_gpu_tasks=2,
        )

        assert "executor = 'slurm'" in config
        assert "container = 'ghcr.io/mortazavilab/dogme-pipeline:latest'" in config
        assert "cpuPartition = 'cpu-part'" in config
        assert "gpuPartition = 'gpu-part'" in config
        assert "cpuAccount = 'cpu-acct'" in config
        assert "gpuAccount = 'gpu-acct'" in config
        assert "    cpus = 4" in config
        assert "    memory = '16 GB'" in config
        assert "    time = '48:00:00'" in config
        assert "clusterOptions = '--account=cpu-acct'" in config
        assert "queue = 'cpu-part'" in config
        assert "clusterOptions = '--account=gpu-acct --gres=gpu:1'" in config
        assert "queue = 'gpu-part'" in config
        assert f"--bind {REFERENCE_GENOMES['mm39']['fasta']}" not in config
        assert "containerOptions = '--no-mount hostfs --bind /share/crsp/lab/seyedam/share/agoutic/seyedam/testslurm1'" in config
        assert "containerOptions = '--nv --no-mount hostfs --bind /share/crsp/lab/seyedam/share/agoutic/seyedam/testslurm1'" in config
        assert "apptainer {" in config
        assert "autoMounts = false" in config

    def test_slurm_execution_keeps_cpu_defaults_separate_from_gpu_task_overrides(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-slurm-split",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
            slurm_cpu_partition="standard",
            slurm_gpu_partition="gpu",
            slurm_cpu_account="SEYEDAM_LAB",
            slurm_gpu_account="BIOD132_CLASS_GPU",
            slurm_cpus=4,
            slurm_memory_gb=16,
            slurm_walltime="48:00:00",
            apptainer_cache_dir="/share/crsp/lab/seyedam/share/agoutic/seyedam/.nxf-apptainer-cache",
        )

        assert "cpuPartition = 'standard'" in config
        assert "gpuPartition = 'gpu'" in config
        assert "cpuAccount = 'SEYEDAM_LAB'" in config
        assert "gpuAccount = 'BIOD132_CLASS_GPU'" in config
        assert "    cpus = 4" in config
        assert "    memory = '16 GB'" in config
        assert "    time = '48:00:00'" in config
        assert "    clusterOptions = '--account=SEYEDAM_LAB'" in config
        assert "    queue = 'standard'" in config
        assert "withName: 'doradoTask' {\n        clusterOptions = '--account=BIOD132_CLASS_GPU --gres=gpu:1'\n        queue = 'gpu'" in config
        assert 'cacheDir = "/share/crsp/lab/seyedam/share/agoutic/seyedam/.nxf-apptainer-cache"' in config
        assert "singularity {" not in config
        assert "docker {" not in config

    def test_slurm_execution_defaults_cpu_resources_to_12_cpus_and_64_gb(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-slurm-defaults",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
            slurm_cpu_partition="standard",
            slurm_gpu_partition="gpu",
            slurm_cpu_account="SEYEDAM_LAB",
            slurm_gpu_account="BIOD132_CLASS_GPU",
        )

        assert "    cpus = 12" in config
        assert "    memory = '64 GB'" in config
        assert "    time = '8:00:00'" in config
        assert "withName: 'minimapTask' {\n        cpus = 16\n        memory = '96 GB'" in config

    def test_slurm_execution_preserves_higher_explicit_minimap_resources(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-slurm-minimap-high",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
            slurm_cpu_partition="standard",
            slurm_gpu_partition="gpu",
            slurm_cpu_account="SEYEDAM_LAB",
            slurm_gpu_account="BIOD132_CLASS_GPU",
            slurm_cpus=64,
            slurm_memory_gb=192,
        )

        assert "withName: 'minimapTask' {\n        cpus = 64\n        memory = '192 GB'" in config

    def test_slurm_dna_defaults_scope_container_gpu_modkit_to_openchromatin_tasks(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-dna-container-openchrom",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
            slurm_bind_paths=["/remote/workflow4", "/remote/ref/mm39"],
        )

        assert "container = 'ghcr.io/mortazavilab/dogme-pipeline:latest'" in config
        assert "withName: 'modkitTask' {\n        memory = '64 GB'\n        cpus = 12\n        containerOptions = '--no-mount hostfs --bind /remote/workflow4,/remote/ref/mm39'" in config
        assert config.count("containerOptions = '--nv --no-mount hostfs --bind /remote/workflow4,/remote/ref/mm39 --env \\\'MODKITBASE=") == 2
        assert f"LIBTORCH={DOGME_DNA_OPENCHROM_LIBTORCH}" in config
        assert f"LD_LIBRARY_PATH=/opt/conda/lib:{DOGME_DNA_OPENCHROM_LIBTORCH}/lib:\\\\$LD_LIBRARY_PATH" in config
        assert f"DYLD_LIBRARY_PATH={DOGME_DNA_OPENCHROM_LIBTORCH}/lib:\\\\$DYLD_LIBRARY_PATH" in config

    def test_slurm_cdna_uses_shared_dogme_container(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-cdna-shared-container",
            mode="CDNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
        )

        assert "container = 'ghcr.io/mortazavilab/dogme-pipeline:latest'" in config

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

    def test_slurm_custom_modkit_bind_paths_are_scoped_to_openchromatin_tasks(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-modkit",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
            slurm_bind_paths=["/remote/workflow4", "/remote/ref/mm39"],
            slurm_modkit_bind_paths=[
                "/remote/workflow4",
                "/remote/ref/mm39",
                "/cluster/modkit",
                "/lib64/libgomp.so.1",
                "/lib64/libstdc++.so.6",
                "/lib64/libgcc_s.so.1",
            ],
        )

        assert "containerOptions = '--no-mount hostfs --bind /remote/workflow4,/remote/ref/mm39'" in config
        assert "containerOptions = '--nv --no-mount hostfs --bind /remote/workflow4,/remote/ref/mm39'" in config
        assert "withName: 'modkitTask' {" in config
        assert "withName: 'modkitTask' {\n        memory = '64 GB'\n        cpus = 12\n        containerOptions = '--no-mount hostfs --bind /remote/workflow4,/remote/ref/mm39'" in config
        assert "withName: 'openChromatinTaskBg' {" in config
        assert "withName: 'openChromatinTaskBed' {" in config
        assert config.count("containerOptions = '--nv --no-mount hostfs --bind /remote/workflow4,/remote/ref/mm39,/cluster/modkit,/lib64/libgomp.so.1,/lib64/libstdc++.so.6,/lib64/libgcc_s.so.1 --env \\\'MODKITBASE=") == 2
        assert f"LD_LIBRARY_PATH=/lib64:/opt/conda/lib:{DOGME_DNA_OPENCHROM_LIBTORCH}/lib:\\\\$LD_LIBRARY_PATH" in config
        assert f"DYLD_LIBRARY_PATH={DOGME_DNA_OPENCHROM_LIBTORCH}/lib:\\\\$DYLD_LIBRARY_PATH" in config

    def test_slurm_openchromatin_runtime_exports_are_scoped_away_from_dorado_and_modkit_tasks(self):
        config = NextflowConfig.generate_config(
            sample_name="sample-runtime",
            mode="DNA",
            input_dir="/tmp/input",
            reference_genome=["mm39"],
            execution_mode="slurm",
            slurm_modkit_bind_paths=[
                "/cluster/modkit",
                "/lib64/libgomp.so.1",
                "/lib64/libstdc++.so.6",
                "/lib64/libgcc_s.so.1",
            ],
            modkit_task_runtime_exports=[
                "export MODKITBASE=/cluster/modkit",
                "export PATH=/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch:${PATH}",
                "export MODKITMODEL=/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0",
                "export LIBTORCH=/cluster/modkit/libtorch",
                "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}",
                "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}",
            ],
        )

        assert "process {\n    // <-- Container Settings --->\n    container = 'ghcr.io/mortazavilab/dogme-pipeline:latest'\n    // Remote SLURM runs bind only workflow-specific remote paths.\n    containerOptions = '--no-mount hostfs'\n    beforeScript = 'export PATH=/opt/conda/bin:$PATH'" in config
        assert "withName: 'modkitTask' {\n        memory = '64 GB'\n        cpus = 12\n        containerOptions = '--no-mount hostfs'" in config
        assert "beforeScript = 'export PATH=/opt/conda/bin:$PATH; export LIBTORCH=/cluster/modkit/libtorch" not in config
        assert config.count("--bind /cluster/modkit,/lib64/libgomp.so.1,/lib64/libstdc++.so.6,/lib64/libgcc_s.so.1 --env \\\'MODKITBASE=/cluster/modkit,PREPEND_PATH=/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch,MODKITMODEL=/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0,LIBTORCH=/cluster/modkit/libtorch,LD_LIBRARY_PATH=/lib64:/cluster/modkit/libtorch/lib:\\\\$LD_LIBRARY_PATH,DYLD_LIBRARY_PATH=/cluster/modkit/libtorch/lib:\\\\$DYLD_LIBRARY_PATH\\\'") == 2
        assert "containerOptions = '--nv --no-mount hostfs --bind /cluster/modkit,/lib64/libgomp.so.1,/lib64/libstdc++.so.6,/lib64/libgcc_s.so.1 --env \\\'MODKITBASE=/cluster/modkit,PREPEND_PATH=/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch,MODKITMODEL=/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0,LIBTORCH=/cluster/modkit/libtorch,LD_LIBRARY_PATH=/lib64:/cluster/modkit/libtorch/lib:\\\\$LD_LIBRARY_PATH,DYLD_LIBRARY_PATH=/cluster/modkit/libtorch/lib:\\\\$DYLD_LIBRARY_PATH\\\''" in config
        assert "withName: 'openChromatinTaskBg' {" in config
        assert "withName: 'openChromatinTaskBed' {" in config


class TestResolveDogmeProfileContent:
    def test_dna_mode_uses_container_modkit_paths(self):
        profile = resolve_dogme_profile_content("DNA")

        assert f"export MODKITBASE=${{MODKITBASE:-{DOGME_DNA_MODKITBASE}}}" in profile
        assert f"export MODKITMODEL=${{MODKITMODEL:-${{MODKITBASE}}/models/{DOGME_DNA_MODKITMODEL.name}}}" in profile
        assert "LIBTORCH" not in profile
        assert "DYLD_LIBRARY_PATH" not in profile
        assert "LD_LIBRARY_PATH" not in profile

    def test_dna_mode_defaults_openchromatin_to_container_gpu_runtime(self):
        runtime_exports = resolve_dogme_profile_task_runtime_exports("DNA")

        assert runtime_exports == [
            f"export MODKITBASE={DOGME_DNA_OPENCHROM_MODKITBASE}",
            f"export PATH={DOGME_DNA_OPENCHROM_BINARY_DIR}:${{PATH}}",
            f"export MODKITMODEL={DOGME_DNA_OPENCHROM_MODEL}",
            f"export LIBTORCH={DOGME_DNA_OPENCHROM_LIBTORCH}",
            "export LD_LIBRARY_PATH=/opt/conda/lib:${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}",
            "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}",
        ]

    def test_custom_profile_keeps_staged_dna_profile_container_safe(self):
        profile = resolve_dogme_profile_content("DNA", custom_profile="export MODKITBASE=/remote/modkit")

        assert profile == (
            "# Dogme environment profile\n"
            "# DNA mode modkit paths inside the dogme container image\n"
            f"export MODKITBASE=${{MODKITBASE:-{DOGME_DNA_MODKITBASE}}}\n"
            f"export MODKITMODEL=${{MODKITMODEL:-${{MODKITBASE}}/models/{DOGME_DNA_MODKITMODEL.name}}}\n"
        )

    def test_custom_profile_runtime_exports_are_removed_from_global_profile(self):
        profile = resolve_dogme_profile_content(
            "DNA",
            custom_profile=(
                "export MODKITBASE=/remote/modkit\n"
                "export PATH=${MODKITBASE}:${PATH}\n"
                "export LIBTORCH=${MODKITBASE}/libtorch\n"
                "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}\n"
                "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}\n"
            ),
        )

        assert profile == (
            "# Dogme environment profile\n"
            "# DNA mode modkit paths inside the dogme container image\n"
            f"export MODKITBASE=${{MODKITBASE:-{DOGME_DNA_MODKITBASE}}}\n"
            f"export MODKITMODEL=${{MODKITMODEL:-${{MODKITBASE}}/models/{DOGME_DNA_MODKITMODEL.name}}}\n"
        )

    def test_custom_profile_runtime_exports_are_available_for_openchromatin_tasks(self):
        runtime_exports = resolve_dogme_profile_task_runtime_exports(
            "DNA",
            custom_profile=(
                "export MODKITBASE=/remote/modkit\n"
                "export PATH=${MODKITBASE}:${PATH}\n"
                "export MODKITMODEL=${MODKITBASE}/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0\n"
                "export LIBTORCH=${MODKITBASE}/libtorch\n"
                "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}\n"
                "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}\n"
            ),
        )

        assert runtime_exports == [
            "export MODKITBASE=/remote/modkit",
            "export PATH=/remote/modkit:${PATH}",
            "export MODKITMODEL=/remote/modkit/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0",
            "export LIBTORCH=/remote/modkit/libtorch",
            "export LD_LIBRARY_PATH=/remote/modkit/libtorch/lib:${LD_LIBRARY_PATH:-}",
            "export DYLD_LIBRARY_PATH=/remote/modkit/libtorch/lib:${DYLD_LIBRARY_PATH:-}",
        ]

    def test_non_dna_ignores_custom_profile_override(self, monkeypatch, tmp_path):
        dogme_repo = tmp_path / "dogme"
        dogme_repo.mkdir()
        (dogme_repo / "dogme.profile").write_text("export EXISTING_PROFILE=1\n")

        monkeypatch.setattr(nextflow_module, "DOGME_REPO", dogme_repo)

        assert (
            resolve_dogme_profile_content("RNA", custom_profile="export MODKITBASE=/remote/modkit")
            == "export EXISTING_PROFILE=1\n"
        )

    def test_non_dna_mode_uses_repo_profile_when_present(self, monkeypatch, tmp_path):
        dogme_repo = tmp_path / "dogme"
        dogme_repo.mkdir()
        (dogme_repo / "dogme.profile").write_text("export EXISTING_PROFILE=1\n")

        monkeypatch.setattr(nextflow_module, "DOGME_REPO", dogme_repo)

        assert resolve_dogme_profile_content("RNA") == "export EXISTING_PROFILE=1\n"

    def test_non_dna_mode_falls_back_to_minimal_profile_when_missing(self, monkeypatch, tmp_path):
        dogme_repo = tmp_path / "dogme"
        dogme_repo.mkdir()

        monkeypatch.setattr(nextflow_module, "DOGME_REPO", dogme_repo)

        assert resolve_dogme_profile_content("CDNA") == (
            "# Dogme environment profile\n"
            "# Add environment variables here if needed\n"
        )


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


class TestGenericWorkflowSubmission:
    @pytest.mark.asyncio
    async def test_submit_job_uses_workflow_executor_contract_for_wf_pore_c(self, monkeypatch, tmp_path):
        agoutic_data = tmp_path / "agoutic-data"
        nextflow_bin = tmp_path / "bin" / "nextflow"
        nextflow_bin.parent.mkdir(parents=True, exist_ok=True)
        nextflow_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.delenv("NXF_SYNTAX_PARSER", raising=False)

        input_path = tmp_path / "inputs" / "sample.fastq.gz"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text("@read\nACGT\n+\n!!!!\n", encoding="utf-8")

        reference_fasta = tmp_path / "refs" / "reference.fa"
        reference_fasta.parent.mkdir(parents=True, exist_ok=True)
        reference_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

        captured: dict[str, object] = {}

        class _FakeProcess:
            pid = 4321

            async def wait(self):
                return 0

        async def fake_subprocess_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["cwd"] = kwargs["cwd"]
            captured["env"] = kwargs["env"]
            return _FakeProcess()

        def fake_create_task(coro):
            captured.setdefault("tasks", []).append(coro)
            coro.close()
            return SimpleNamespace()

        monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)
        monkeypatch.setattr(nextflow_module, "AGOUTIC_DATA", agoutic_data)
        monkeypatch.setattr(nextflow_module.asyncio, "create_subprocess_exec", fake_subprocess_exec)
        monkeypatch.setattr(nextflow_module.asyncio, "create_task", fake_create_task)

        executor = NextflowExecutor()
        executor.nextflow_bin = nextflow_bin
        executor.work_dir = tmp_path / "launchpad-work"
        executor.work_dir.mkdir(parents=True, exist_ok=True)
        executor.logs_dir = tmp_path / "logs"
        executor.logs_dir.mkdir(parents=True, exist_ok=True)

        request = SimpleNamespace(
            sample_name="POREC_A",
            mode=None,
            input_directory=str(input_path),
            input_type="fastq",
            reference_fasta=str(reference_fasta),
            vcf=None,
            sample_sheet=None,
            cutter="NlaIII",
            workflow_repo=None,
            workflow_version=None,
            output_flags={"pairs": True, "mcool": True},
            output_directory=None,
        )

        workflow_executor = get_workflow_executor("wf_pore_c")

        run_uuid, work_dir = await executor.submit_job(
            run_uuid="run-pore-c",
            workflow_key="wf_pore_c",
            workflow_executor=workflow_executor,
            request=request,
            sample_name="POREC_A",
            mode=None,
            input_type="fastq",
            input_dir=str(input_path),
            reference_genome=["GRCh38"],
            workflow_index=1,
            username="alice",
            project_slug="proj-1",
        )

        expected_work_dir = agoutic_data / "users" / "alice" / "proj-1" / "workflow1"
        expected_work_path = agoutic_data / "users" / "alice" / "proj-1" / ".nextflow-work" / "wf-pore-c" / "workflow1"
        staged_input = expected_work_dir / ".agoutic" / "wf-pore-c" / "staged-inputs" / "input" / input_path.name
        staged_reference = expected_work_dir / ".agoutic" / "wf-pore-c" / "staged-inputs" / "reference" / reference_fasta.name

        assert run_uuid == "run-pore-c"
        assert work_dir == expected_work_dir
        assert captured["cmd"][:5] == [
            str(nextflow_bin),
            "run",
            "epi2me-labs/wf-pore-c",
            "-r",
            "v1.3.1",
        ]
        assert "--ref" in captured["cmd"]
        assert str(staged_reference) in captured["cmd"]
        assert "--sample" in captured["cmd"]
        assert str(staged_input) in captured["cmd"]
        assert "-work-dir" in captured["cmd"]
        assert str(expected_work_path) in captured["cmd"]
        assert captured["env"]["NXF_SYNTAX_PARSER"] == "v1"
        assert expected_work_path.parent.exists()
        assert not str(expected_work_path).startswith(str(expected_work_dir) + "/")
        assert staged_input.is_symlink()
        assert staged_reference.is_symlink()
        assert not (work_dir / "dogme.profile").exists()
        assert (work_dir / ".agoutic" / "wf-pore-c" / "submit-config.json").exists()
        assert (work_dir / ".launch_command").exists()
        assert (work_dir / ".nextflow_pid").read_text() == "4321"

        metadata = json.loads((work_dir / ".agoutic.workflow.json").read_text())
        assert metadata["workflow_key"] == "wf_pore_c"
        assert metadata["summary_contract"]["workflow_key"] == "wf_pore_c"
        assert metadata["result_sync_spec"]["report_filename"] == "wf-pore-c-report.html"

    @pytest.mark.asyncio
    async def test_submit_job_copies_staged_input_when_symlink_rejected(self, monkeypatch, tmp_path):
        agoutic_data = tmp_path / "agoutic-data"
        nextflow_bin = tmp_path / "bin" / "nextflow"
        nextflow_bin.parent.mkdir(parents=True, exist_ok=True)
        nextflow_bin.write_text("#!/bin/sh\n", encoding="utf-8")

        input_path = tmp_path / "inputs" / "sample.fastq.gz"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text("@read\nACGT\n+\n!!!!\n", encoding="utf-8")

        reference_fasta = tmp_path / "refs" / "reference.fa"
        reference_fasta.parent.mkdir(parents=True, exist_ok=True)
        reference_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

        captured: dict[str, object] = {}
        symlink_attempts: list[tuple[str, str]] = []

        class _FakeProcess:
            pid = 9876

            async def wait(self):
                return 0

        async def fake_subprocess_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["cwd"] = kwargs["cwd"]
            return _FakeProcess()

        def fake_create_task(coro):
            captured.setdefault("tasks", []).append(coro)
            coro.close()
            return SimpleNamespace()

        def rejecting_symlink(src, dst, target_is_directory=False):
            symlink_attempts.append((src, dst))
            raise OSError("EPERM")

        monkeypatch.setattr(launchpad_config, "WF_PORE_C_ENABLED", True)
        monkeypatch.setattr(nextflow_module, "AGOUTIC_DATA", agoutic_data)
        monkeypatch.setattr(nextflow_module.asyncio, "create_subprocess_exec", fake_subprocess_exec)
        monkeypatch.setattr(nextflow_module.asyncio, "create_task", fake_create_task)
        monkeypatch.setattr("launchpad.workflow_executors.wf_pore_c.os.symlink", rejecting_symlink)

        executor = NextflowExecutor()
        executor.nextflow_bin = nextflow_bin
        executor.work_dir = tmp_path / "launchpad-work"
        executor.work_dir.mkdir(parents=True, exist_ok=True)
        executor.logs_dir = tmp_path / "logs"
        executor.logs_dir.mkdir(parents=True, exist_ok=True)

        request = SimpleNamespace(
            sample_name="POREC_A",
            mode=None,
            input_directory=str(input_path),
            input_type="fastq",
            reference_fasta=str(reference_fasta),
            vcf=None,
            sample_sheet=None,
            cutter="NlaIII",
            workflow_repo=None,
            workflow_version=None,
            output_flags={"pairs": True, "mcool": True},
            output_directory=None,
        )

        workflow_executor = get_workflow_executor("wf_pore_c")

        run_uuid, work_dir = await executor.submit_job(
            run_uuid="run-pore-c-copy",
            workflow_key="wf_pore_c",
            workflow_executor=workflow_executor,
            request=request,
            sample_name="POREC_A",
            mode=None,
            input_type="fastq",
            input_dir=str(input_path),
            reference_genome=["GRCh38"],
            workflow_index=1,
            username="alice",
            project_slug="proj-1",
        )

        staged_input = work_dir / ".agoutic" / "wf-pore-c" / "staged-inputs" / "input" / input_path.name

        assert run_uuid == "run-pore-c-copy"
        assert symlink_attempts
        assert staged_input.exists()
        assert not staged_input.is_symlink()
        assert staged_input.read_text(encoding="utf-8") == input_path.read_text(encoding="utf-8")
        assert str(staged_input) in captured["cmd"]
