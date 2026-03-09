"""
Tests for launchpad/config.py — DogmeMode, JobStatus enums and config constants.
"""

import pytest

from launchpad.config import DogmeMode, JobStatus, REFERENCE_GENOMES


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
        expected = {"PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "DELETED"}
        actual = {s.value for s in JobStatus}
        assert actual == expected

    def test_string_equality(self):
        assert JobStatus.RUNNING == "RUNNING"
        assert JobStatus.COMPLETED == "COMPLETED"


class TestReferenceGenomes:
    def test_has_grch38(self):
        assert "GRCh38" in REFERENCE_GENOMES

    def test_has_mm39(self):
        assert "mm39" in REFERENCE_GENOMES

    def test_grch38_has_fasta_and_gtf(self):
        ref = REFERENCE_GENOMES["GRCh38"]
        assert "fasta" in ref
        assert "gtf" in ref

    def test_default_genome(self):
        assert REFERENCE_GENOMES["default"] == "GRCh38"
