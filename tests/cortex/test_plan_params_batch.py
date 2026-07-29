"""Batch DOGME parameter extraction tests."""

from types import SimpleNamespace

from cortex.plan_params import _extract_plan_params


def test_extract_dogme_batch_sample_pairs_and_shared_settings():
    params = _extract_plan_params(
        "Run DOGME DNA on tumor: /data/tumor and normal=/data/normal for GRCh38 on SLURM with parallelism 2",
        SimpleNamespace(sample_name=None, work_dir=None),
        "run_dogme_batch",
    )

    assert params["batch_samples"] == [
        {"sample_id": "1", "sample_name": "tumor", "input_directory": "/data/tumor"},
        {"sample_id": "2", "sample_name": "normal", "input_directory": "/data/normal"},
    ]
    assert params["shared_params"] == {
        "mode": "DNA",
        "reference_genome": ["GRCh38"],
        "execution_mode": "slurm",
    }
    assert params["requested_max_parallel"] == 2


def test_extract_cdna_fastq_batch_sets_fastq_entry_point():
    params = _extract_plan_params(
        "Run DOGME cDNA on sample-a: /data/a.fastq.gz and sample-b=/data/b.fq with parallelism 2",
        SimpleNamespace(sample_name=None, work_dir=None),
        "run_dogme_batch",
    )

    assert params["shared_params"] == {
        "mode": "CDNA",
        "input_type": "fastq",
        "entry_point": "fastqCDNA",
    }


def test_extract_cdna_fastq_batch_preserves_hpc3_as_shared_slurm_target():
    params = _extract_plan_params(
        "Run DOGME cDNA on hpc3 for GRCh38 with sample-a: /data/a.fastq.gz and sample-b: /data/b.fastq.gz",
        SimpleNamespace(sample_name=None, work_dir=None),
        "run_dogme_batch",
    )

    assert params["shared_params"] == {
        "mode": "CDNA",
        "input_type": "fastq",
        "entry_point": "fastqCDNA",
        "reference_genome": ["GRCh38"],
        "execution_mode": "slurm",
        "ssh_profile_nickname": "hpc3",
    }
