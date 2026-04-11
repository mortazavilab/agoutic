"""
Declarative skill capability manifest.

Replaces the flat ``SKILLS_REGISTRY`` dict with rich per-skill metadata
that enables smarter planner routing, runtime availability checks, and
token-budget awareness.

Every skill declares its expected inputs, output types, required MCP
services, estimated runtime, and supported sample types.  The registry
still exposes the same ``key → path`` mapping for backward-compat via
:func:`get_skill_path` and the ``SKILLS_REGISTRY`` compat dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# ── Enums / types ─────────────────────────────────────────────────────────

class SampleType(str, Enum):
    DNA = "DNA"
    RNA = "RNA"
    CDNA = "cDNA"
    FIBER_SEQ = "Fiber-seq"
    BULK_RNA = "Bulk-RNA"
    SINGLE_CELL = "scRNA"
    ANY = "any"


class OutputType(str, Enum):
    """Broad category of what a skill produces."""
    DATAFRAME = "dataframe"      # tabular results
    FILE = "file"                # downloaded / generated files
    REPORT = "report"            # narrative analysis text
    JOB = "job"                  # submitted pipeline job
    PLOT = "plot"                # visualisation
    ANNOTATION = "annotation"    # gene / pathway annotation


# ── SkillManifest ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SkillManifest:
    """Declarative capability descriptor for a single skill."""

    # Identity
    key: str                                          # unique skill key
    skill_file: str                                   # relative path under skills/
    display_name: str = ""                            # human-friendly label

    # Routing / classification
    description: str = ""                             # one-line purpose
    category: str = "general"                         # logical grouping

    # Required back-end services (keys into SERVICE_REGISTRY / CONSORTIUM_REGISTRY)
    required_services: tuple[str, ...] = ()

    # Expected inputs (free-form labels such as "accession", "counts_matrix")
    expected_inputs: tuple[str, ...] = ()

    # What the skill produces
    output_types: tuple[OutputType, ...] = ()

    # Sample-type compatibility
    sample_types: tuple[SampleType, ...] = (SampleType.ANY,)

    # Rough runtime bucket so the planner can warn users
    estimated_runtime: Literal["fast", "medium", "slow", "variable"] = "fast"

    # Whether the skill is analysis-only (should NOT be routed for job submission)
    analysis_only: bool = False

    # Whether this skill can be the target of a SKILL_SWITCH_TO tag
    switchable: bool = True


# ── Manifest declarations ─────────────────────────────────────────────────

SKILL_MANIFESTS: dict[str, SkillManifest] = {}


def _register(*manifests: SkillManifest) -> None:
    for m in manifests:
        SKILL_MANIFESTS[m.key] = m


_register(
    # ── Entry point ───────────────────────────────────────────────────
    SkillManifest(
        key="welcome",
        skill_file="welcome/SKILL.md",
        display_name="Welcome",
        description="Entry point — greets the user and routes to the right skill.",
        category="routing",
        switchable=False,
    ),

    # ── ENCODE ────────────────────────────────────────────────────────
    SkillManifest(
        key="ENCODE_Search",
        skill_file="ENCODE_Search/SKILL.md",
        display_name="ENCODE Search",
        description="Search the ENCODE portal for experiments, biosamples, and files.",
        category="data_retrieval",
        required_services=("encode",),
        expected_inputs=("biosample", "assay_type", "accession", "search_term"),
        output_types=(OutputType.DATAFRAME,),
        sample_types=(SampleType.ANY,),
    ),
    SkillManifest(
        key="ENCODE_LongRead",
        skill_file="ENCODE_LongRead/SKILL.md",
        display_name="ENCODE Long-Read",
        description="Browse and download long-read experiments from ENCODE.",
        category="data_retrieval",
        required_services=("encode",),
        expected_inputs=("experiment_accession",),
        output_types=(OutputType.DATAFRAME, OutputType.FILE),
        sample_types=(SampleType.ANY,),
    ),

    # ── IGVF ──────────────────────────────────────────────────────────
    SkillManifest(
        key="IGVF_Search",
        skill_file="IGVF_Search/SKILL.md",
        display_name="IGVF Search",
        description="Search the IGVF Data Portal for measurement sets, analysis sets, samples, genes, and files.",
        category="data_retrieval",
        required_services=("igvf",),
        expected_inputs=("sample_term", "assay_title", "accession", "gene_symbol"),
        output_types=(OutputType.DATAFRAME,),
        sample_types=(SampleType.ANY,),
    ),

    # ── Dogme interpretation (analysis-only) ──────────────────────────
    SkillManifest(
        key="run_dogme_dna",
        skill_file="run_dogme_dna/SKILL.md",
        display_name="DNA Results Interpretation",
        description="Interpret completed Dogme DNA/Fiber-seq workflow results.",
        category="analysis",
        required_services=("analyzer",),
        expected_inputs=("job_uuid", "work_dir"),
        output_types=(OutputType.REPORT, OutputType.DATAFRAME),
        sample_types=(SampleType.DNA, SampleType.FIBER_SEQ),
        estimated_runtime="medium",
        analysis_only=True,
    ),
    SkillManifest(
        key="run_dogme_rna",
        skill_file="run_dogme_rna/SKILL.md",
        display_name="RNA Results Interpretation",
        description="Interpret completed Dogme direct-RNA workflow results.",
        category="analysis",
        required_services=("analyzer",),
        expected_inputs=("job_uuid", "work_dir"),
        output_types=(OutputType.REPORT, OutputType.DATAFRAME),
        sample_types=(SampleType.RNA,),
        estimated_runtime="medium",
        analysis_only=True,
    ),
    SkillManifest(
        key="run_dogme_cdna",
        skill_file="run_dogme_cdna/SKILL.md",
        display_name="cDNA Results Interpretation",
        description="Interpret completed Dogme cDNA workflow results.",
        category="analysis",
        required_services=("analyzer",),
        expected_inputs=("job_uuid", "work_dir"),
        output_types=(OutputType.REPORT, OutputType.DATAFRAME),
        sample_types=(SampleType.CDNA,),
        estimated_runtime="medium",
        analysis_only=True,
    ),

    # ── Job submission / intake ───────────────────────────────────────
    SkillManifest(
        key="analyze_local_sample",
        skill_file="analyze_local_sample/SKILL.md",
        display_name="Local Sample Intake",
        description="Collect parameters and submit a local Dogme pipeline job.",
        category="execution",
        required_services=("launchpad",),
        expected_inputs=("file_path", "sample_name", "reference_genome", "mode"),
        output_types=(OutputType.JOB,),
        sample_types=(SampleType.DNA, SampleType.RNA, SampleType.CDNA),
        estimated_runtime="slow",
    ),

    # ── Job results ───────────────────────────────────────────────────
    SkillManifest(
        key="analyze_job_results",
        skill_file="analyze_job_results/SKILL.md",
        display_name="Job Results Browser",
        description="Browse and analyze completed pipeline job outputs.",
        category="analysis",
        required_services=("analyzer",),
        expected_inputs=("job_uuid",),
        output_types=(OutputType.REPORT, OutputType.DATAFRAME, OutputType.PLOT),
        sample_types=(SampleType.ANY,),
        estimated_runtime="medium",
    ),

    # ── Download ──────────────────────────────────────────────────────
    SkillManifest(
        key="download_files",
        skill_file="download_files/SKILL.md",
        display_name="File Download",
        description="Download files from ENCODE or arbitrary URLs.",
        category="data_retrieval",
        required_services=("encode",),
        expected_inputs=("accession", "url"),
        output_types=(OutputType.FILE,),
        sample_types=(SampleType.ANY,),
        estimated_runtime="variable",
    ),

    # ── Differential expression ───────────────────────────────────────
    SkillManifest(
        key="differential_expression",
        skill_file="differential_expression/SKILL.md",
        display_name="Differential Expression",
        description="Run edgeR-based differential expression analysis.",
        category="analysis",
        required_services=("edgepython",),
        expected_inputs=("counts_matrix", "sample_metadata", "design_formula", "contrast"),
        output_types=(OutputType.DATAFRAME, OutputType.PLOT, OutputType.REPORT),
        sample_types=(SampleType.BULK_RNA, SampleType.SINGLE_CELL, SampleType.CDNA),
        estimated_runtime="medium",
    ),

    # ── Enrichment ────────────────────────────────────────────────────
    SkillManifest(
        key="enrichment_analysis",
        skill_file="enrichment_analysis/SKILL.md",
        display_name="GO / Pathway Enrichment",
        description="Run GO, KEGG, or Reactome enrichment on gene lists.",
        category="analysis",
        required_services=("analyzer",),
        expected_inputs=("gene_list", "organism"),
        output_types=(OutputType.DATAFRAME, OutputType.REPORT, OutputType.ANNOTATION),
        sample_types=(SampleType.ANY,),
    ),

    # ── XgenePy ───────────────────────────────────────────────────────
    SkillManifest(
        key="xgenepy_analysis",
        skill_file="xgenepy_analysis/SKILL.md",
        display_name="Cis/Trans Analysis",
        description="Run XgenePy cis/trans regulatory modeling.",
        category="analysis",
        required_services=("xgenepy",),
        expected_inputs=("counts_table", "sample_metadata", "strain_column"),
        output_types=(OutputType.DATAFRAME, OutputType.PLOT, OutputType.REPORT),
        sample_types=(SampleType.ANY,),
        estimated_runtime="medium",
    ),

    # ── Remote execution ──────────────────────────────────────────────
    SkillManifest(
        key="remote_execution",
        skill_file="remote_execution/SKILL.md",
        display_name="Remote SLURM Execution",
        description="Submit pipeline jobs to a remote SLURM cluster via SSH.",
        category="execution",
        required_services=("launchpad",),
        expected_inputs=("ssh_profile", "slurm_resources", "sample_name", "reference_genome", "mode"),
        output_types=(OutputType.JOB,),
        sample_types=(SampleType.DNA, SampleType.RNA, SampleType.CDNA, SampleType.FIBER_SEQ),
        estimated_runtime="slow",
    ),

    # ── Reconcile BAMs ────────────────────────────────────────────────
    SkillManifest(
        key="reconcile_bams",
        skill_file="reconcile_bams/SKILL.md",
        display_name="Reconcile BAMs",
        description="Merge annotated BAM outputs across multiple workflows.",
        category="execution",
        required_services=("launchpad",),
        expected_inputs=("workflow_uuids", "bam_paths"),
        output_types=(OutputType.FILE,),
        sample_types=(SampleType.ANY,),
        estimated_runtime="medium",
    ),
)


# ── Backwards-compatible SKILLS_REGISTRY dict ─────────────────────────────
# Consumers that only need key → path can keep using this.

SKILLS_REGISTRY: dict[str, str] = {
    m.key: m.skill_file for m in SKILL_MANIFESTS.values()
}


# ── Query helpers ─────────────────────────────────────────────────────────

def get_skill_path(skill_key: str) -> str | None:
    """Return the skill file relative path, or None if not registered."""
    m = SKILL_MANIFESTS.get(skill_key)
    return m.skill_file if m else None


def get_manifest(skill_key: str) -> SkillManifest | None:
    """Return the full manifest for a skill, or None."""
    return SKILL_MANIFESTS.get(skill_key)


def skills_for_service(service_key: str) -> list[SkillManifest]:
    """Return all skills that require *service_key*."""
    return [
        m for m in SKILL_MANIFESTS.values()
        if service_key in m.required_services
    ]


def skills_for_sample_type(sample_type: SampleType) -> list[SkillManifest]:
    """Return skills compatible with the given sample type."""
    return [
        m for m in SKILL_MANIFESTS.values()
        if SampleType.ANY in m.sample_types or sample_type in m.sample_types
    ]


def check_service_availability(
    skill_key: str,
    available_services: set[str],
) -> tuple[bool, list[str]]:
    """Check whether all services required by a skill are available.

    Returns:
        (ok, missing_services)
    """
    m = SKILL_MANIFESTS.get(skill_key)
    if m is None:
        return False, [f"unknown skill: {skill_key}"]
    missing = [s for s in m.required_services if s not in available_services]
    return len(missing) == 0, missing
