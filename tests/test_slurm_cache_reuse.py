"""Tests for SLURM cache helper behavior."""
from pathlib import Path

from launchpad.backends.base import SubmitParams
from launchpad.backends.slurm_backend import SlurmBackend
from launchpad.backends.ssh_manager import SSHProfileData


def _profile() -> SSHProfileData:
    return SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc",
        ssh_host="cluster.example.edu",
        ssh_port=22,
        ssh_username="eli",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
    )


def test_normalize_reference_id_lowercases():
    assert SlurmBackend._normalize_reference_id("GRCh38") == "grch38"


def test_derive_cache_roots_are_user_profile_scoped():
    params = SubmitParams(remote_input_path="/scratch/eli/agoutic/projA/sample/input")
    ref_root, data_root = SlurmBackend._derive_cache_roots(params, _profile(), "user-1")

    assert ref_root == "/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/references"
    assert data_root == "/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/data"


def test_derive_cache_roots_cross_project_reuse_same_user_profile():
    params_a = SubmitParams(remote_input_path="/scratch/eli/agoutic/projA/wf/input")
    params_b = SubmitParams(remote_input_path="/scratch/eli/agoutic/projB/wf/input")

    roots_a = SlurmBackend._derive_cache_roots(params_a, _profile(), "user-1")
    roots_b = SlurmBackend._derive_cache_roots(params_b, _profile(), "user-1")

    assert roots_a == roots_b


def test_compute_input_fingerprint_changes_with_content(tmp_path: Path):
    sample = tmp_path / "sample"
    sample.mkdir()
    data_file = sample / "reads.pod5"
    data_file.write_bytes(b"abc")

    first = SlurmBackend._compute_input_fingerprint(str(sample))
    data_file.write_bytes(b"abcd")
    second = SlurmBackend._compute_input_fingerprint(str(sample))

    assert first != second


def test_compute_directory_signature_changes_with_file_update(tmp_path: Path):
    ref_dir = tmp_path / "mm39"
    ref_dir.mkdir()
    fasta = ref_dir / "genome.fa"
    fasta.write_text(">chr1\nACGT\n")

    first = SlurmBackend._compute_directory_signature(ref_dir)
    fasta.write_text(">chr1\nACGTT\n")
    second = SlurmBackend._compute_directory_signature(ref_dir)

    assert first != second
