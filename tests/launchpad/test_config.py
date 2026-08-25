"""
Tests for launchpad/config.py — DogmeMode, JobStatus enums and config constants.
"""

import pytest

from launchpad.config import DogmeMode, JobStatus, REFERENCE_GENOMES, discover_reference_genomes


class TestDogmeMode:
    def test_dna(self):
        assert DogmeMode.DNA == "DNA"
        assert DogmeMode.DNA.value == "DNA"

    def test_rna(self):
        assert DogmeMode.RNA == "RNA"

    def test_cdna(self):
        assert DogmeMode.CDNA == "CDNA"

    def test_membership(self):
        assert "DNA" in [m.value for m in DogmeMode]
        assert "RNA" in [m.value for m in DogmeMode]
        assert "CDNA" in [m.value for m in DogmeMode]


class TestJobStatus:
    def test_all_statuses(self):
        expected = {"PENDING", "RUNNING", "STALE", "COMPLETED", "FAILED", "CANCELLED", "DELETED"}
        actual = {s.value for s in JobStatus}
        assert actual == expected

    def test_string_equality(self):
        assert JobStatus.RUNNING == "RUNNING"
        assert JobStatus.COMPLETED == "COMPLETED"


class TestReferenceGenomes:
    def test_discovers_complete_reference_directory(self, tmp_path):
        reference_dir = tmp_path / "future_reference"
        reference_dir.mkdir()
        fasta_path = reference_dir / "future.fa"
        gtf_path = reference_dir / "future.gtf"
        fasta_path.touch()
        gtf_path.touch()

        assert discover_reference_genomes(tmp_path) == {
            "future_reference": {"fasta": fasta_path, "gtf": gtf_path}
        }

    def test_skips_incomplete_or_ambiguous_reference_directories(self, tmp_path):
        incomplete_dir = tmp_path / "incomplete"
        incomplete_dir.mkdir()
        (incomplete_dir / "reference.fa").touch()

        ambiguous_dir = tmp_path / "ambiguous"
        ambiguous_dir.mkdir()
        (ambiguous_dir / "reference.fa").touch()
        (ambiguous_dir / "alternate.fasta").touch()
        (ambiguous_dir / "reference.gtf").touch()

        assert discover_reference_genomes(tmp_path) == {}

    def test_has_grch38(self):
        assert "GRCh38" in REFERENCE_GENOMES

    def test_has_mm39(self):
        assert "mm39" in REFERENCE_GENOMES

    def test_chm13_has_fasta_and_gtf_only(self):
        ref = REFERENCE_GENOMES["chm13"]
        assert ref["fasta"].name == "chm13v2.0.fa"
        assert ref["gtf"].name == "Homo_sapiens-GCA_009914755.4-2022_07-genes_fixed.gtf"
        assert "kallisto_index" not in ref
        assert "kallisto_t2g" not in ref

    def test_has_mad1(self):
        assert "mad1" in REFERENCE_GENOMES

    def test_grch38_has_fasta_and_gtf(self):
        ref = REFERENCE_GENOMES["GRCh38"]
        assert "fasta" in ref
        assert "gtf" in ref

    def test_grch38_has_kallisto_sidecars(self):
        ref = REFERENCE_GENOMES["GRCh38"]
        assert ref["kallisto_index"].name == "hg38Genc47_k63.idx"
        assert ref["kallisto_t2g"].name == "hg38Genc47_k63.t2g"

    def test_mad1_has_fasta_and_gtf_only(self):
        ref = REFERENCE_GENOMES["mad1"]
        assert ref["fasta"].name == "MAD1.fa"
        assert ref["gtf"].name == "MAD1.gtf"
        assert "kallisto_index" not in ref
        assert "kallisto_t2g" not in ref

    def test_default_genome(self):
        assert REFERENCE_GENOMES["default"] == "GRCh38"
