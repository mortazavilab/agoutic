"""Tests for cortex.skill_manifest — declarative skill capability manifest."""

import pytest

from cortex.skill_manifest import (
    SKILL_MANIFESTS,
    SKILLS_REGISTRY,
    OutputType,
    SampleType,
    SkillManifest,
    check_service_availability,
    get_manifest,
    get_skill_path,
    skills_for_sample_type,
    skills_for_service,
)


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------

class TestRegistryIntegrity:
    def test_all_14_skills_registered(self):
        assert len(SKILL_MANIFESTS) == 14

    def test_compat_dict_matches_manifests(self):
        """SKILLS_REGISTRY compat dict has same keys and paths."""
        assert set(SKILLS_REGISTRY.keys()) == set(SKILL_MANIFESTS.keys())
        for key, path in SKILLS_REGISTRY.items():
            assert path == SKILL_MANIFESTS[key].skill_file

    def test_every_manifest_has_skill_file(self):
        for m in SKILL_MANIFESTS.values():
            assert m.skill_file.endswith("/SKILL.md"), f"{m.key}: bad skill_file"

    def test_keys_are_unique(self):
        keys = [m.key for m in SKILL_MANIFESTS.values()]
        assert len(keys) == len(set(keys))

    def test_required_services_are_tuples(self):
        for m in SKILL_MANIFESTS.values():
            assert isinstance(m.required_services, tuple), f"{m.key}: not a tuple"


# ---------------------------------------------------------------------------
# get_manifest / get_skill_path
# ---------------------------------------------------------------------------

class TestLookups:
    def test_get_manifest_found(self):
        m = get_manifest("differential_expression")
        assert m is not None
        assert m.key == "differential_expression"
        assert "edgepython" in m.required_services

    def test_get_manifest_missing(self):
        assert get_manifest("nonexistent") is None

    def test_get_skill_path(self):
        assert get_skill_path("welcome") == "welcome/SKILL.md"

    def test_get_skill_path_missing(self):
        assert get_skill_path("nope") is None


# ---------------------------------------------------------------------------
# Filter queries
# ---------------------------------------------------------------------------

class TestFilters:
    def test_skills_for_service_analyzer(self):
        results = skills_for_service("analyzer")
        keys = {m.key for m in results}
        assert "analyze_job_results" in keys
        assert "run_dogme_dna" in keys
        assert "differential_expression" not in keys

    def test_skills_for_service_empty(self):
        assert skills_for_service("nonexistent") == []

    def test_skills_for_sample_type_dna(self):
        results = skills_for_sample_type(SampleType.DNA)
        keys = {m.key for m in results}
        assert "run_dogme_dna" in keys
        assert "analyze_local_sample" in keys
        # ANY matching means universal skills are included
        assert "welcome" in keys

    def test_skills_for_sample_type_excludes_incompatible(self):
        results = skills_for_sample_type(SampleType.DNA)
        keys = {m.key for m in results}
        # DE only supports Bulk-RNA, scRNA, cDNA
        assert "differential_expression" not in keys


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_all_services_available(self):
        ok, missing = check_service_availability(
            "differential_expression", {"edgepython", "analyzer"}
        )
        assert ok is True
        assert missing == []

    def test_missing_service(self):
        ok, missing = check_service_availability(
            "xgenepy_analysis", {"analyzer", "launchpad"}
        )
        assert ok is False
        assert "xgenepy" in missing

    def test_unknown_skill(self):
        ok, missing = check_service_availability("fake_skill", set())
        assert ok is False
        assert "unknown skill" in missing[0]

    def test_welcome_no_services_needed(self):
        ok, missing = check_service_availability("welcome", set())
        assert ok is True
        assert missing == []


# ---------------------------------------------------------------------------
# Manifest field semantics
# ---------------------------------------------------------------------------

class TestManifestFields:
    def test_analysis_only_flag(self):
        for key in ("run_dogme_dna", "run_dogme_rna", "run_dogme_cdna"):
            assert get_manifest(key).analysis_only is True
        for key in ("analyze_local_sample", "remote_execution"):
            assert get_manifest(key).analysis_only is False

    def test_execution_skills_have_job_output(self):
        for key in ("analyze_local_sample", "remote_execution"):
            m = get_manifest(key)
            assert OutputType.JOB in m.output_types

    def test_welcome_not_switchable(self):
        assert get_manifest("welcome").switchable is False

    def test_estimated_runtime_values(self):
        valid = {"fast", "medium", "slow", "variable"}
        for m in SKILL_MANIFESTS.values():
            assert m.estimated_runtime in valid, f"{m.key}: bad runtime"
