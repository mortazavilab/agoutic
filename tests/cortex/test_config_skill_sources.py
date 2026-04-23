from atlas.config import CONSORTIUM_REGISTRY
from cortex.config import SERVICE_REGISTRY, get_source_for_skill


def test_get_source_for_skill_uses_manifest_metadata_for_service_skills():
    assert get_source_for_skill("differential_expression") == ("edgepython", "service")


def test_get_source_for_skill_uses_manifest_metadata_for_consortium_skills():
    assert get_source_for_skill("IGVF_Search") == ("igvf", "consortium")


def test_service_registry_skills_are_populated_from_manifests():
    assert "differential_expression" in SERVICE_REGISTRY["edgepython"]["skills"]
    assert "enrichment_analysis" in SERVICE_REGISTRY["edgepython"]["skills"]


def test_consortium_registry_skills_are_populated_from_manifests():
    assert "IGVF_Search" in CONSORTIUM_REGISTRY["igvf"]["skills"]
    assert "ENCODE_Search" in CONSORTIUM_REGISTRY["encode"]["skills"]