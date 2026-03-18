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
        remote_base_path="/remote/eli/agoutic",
        is_enabled=True,
    )


def test_normalize_reference_id_lowercases():
    assert SlurmBackend._normalize_reference_id("GRCh38") == "grch38"


def test_derive_remote_paths_are_base_scoped():
    params = SubmitParams(
        project_id="proj-a",
        project_slug="project-a",
        workflow_number=3,
        remote_base_path="/remote/eli/agoutic",
    )
    paths = SlurmBackend._derive_remote_paths(params, _profile())

    assert paths["ref_root"] == "/remote/eli/agoutic/ref"
    assert paths["data_root"] == "/remote/eli/agoutic/data"
    assert paths["remote_work"] == "/remote/eli/agoutic/project-a/workflow3"
    assert paths["remote_output"] == "/remote/eli/agoutic/project-a/workflow3/output"


def test_derive_remote_paths_reuses_same_base_across_projects():
    params_a = SubmitParams(project_id="proj-a", project_slug="proj-a", workflow_number=1, remote_base_path="/remote/eli/agoutic")
    params_b = SubmitParams(project_id="proj-b", project_slug="proj-b", workflow_number=2, remote_base_path="/remote/eli/agoutic")

    paths_a = SlurmBackend._derive_remote_paths(params_a, _profile())
    paths_b = SlurmBackend._derive_remote_paths(params_b, _profile())

    assert paths_a["ref_root"] == paths_b["ref_root"]
    assert paths_a["data_root"] == paths_b["data_root"]


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
