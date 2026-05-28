#!/usr/bin/env python3
"""Workflow-aware long-read BAM haplotyping with an indexed VCF."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pysam

from common.workflow_paths import next_workflow_number, workflow_dir_name


RNA_MODES = {"RNA", "CDNA", "cDNA"}
PROGRESS_PREFIX = "HAPLOTYPE_PROGRESS"
ANNOTATED_BAM_RE = re.compile(r".+\.annotated\.bam$", re.IGNORECASE)
WORKFLOW_NAME_RE = re.compile(r"^workflow\d+$", re.IGNORECASE)


class HaplotypeInputError(Exception):
    pass


class HaplotypeExecutionError(Exception):
    pass


def _emit_progress(event: str, **fields: object) -> None:
    parts = [PROGRESS_PREFIX, event]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    print("\t".join(parts), file=sys.stderr, flush=True)


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text.strip().lower()).strip("-")
    return slug or "label"


def _trim_path_token(path_value: str) -> str:
    return str(path_value or "").strip().strip('"').strip("'").rstrip(".,;:!?")


def _index_sidecars(path: Path) -> tuple[Path, ...]:
    return (
        Path(f"{path}.bai"),
        Path(f"{path}.csi"),
        path.with_suffix(path.suffix + ".bai"),
        path.with_suffix(path.suffix + ".csi"),
    )


def _has_any_index(path: Path) -> bool:
    return any(candidate.exists() for candidate in _index_sidecars(path))


def _has_any_vcf_index(path: Path) -> bool:
    return any(
        candidate.exists()
        for candidate in (
            Path(f"{path}.tbi"),
            Path(f"{path}.csi"),
        )
    )


def _discover_workflow_dirs(project_dir: Path, explicit_workflow_dirs: list[str]) -> list[Path]:
    if explicit_workflow_dirs:
        return [Path(item).expanduser().resolve() for item in explicit_workflow_dirs]
    if not project_dir.is_dir():
        return []
    return sorted(path for path in project_dir.iterdir() if path.is_dir() and path.name.startswith("workflow"))


def _resolve_output_root(project_dir: Path, workflow_dirs: list[Path], requested_output_dir: str) -> tuple[Path, Path | None]:
    requested = (requested_output_dir or ".").strip()
    if requested and requested != ".":
        requested_path = Path(requested).expanduser().resolve()
        if WORKFLOW_NAME_RE.fullmatch(requested_path.name):
            return requested_path.parent, requested_path
        return requested_path, None

    if workflow_dirs:
        workflow_parents = [str(path.parent) for path in workflow_dirs]
        return Path(os.path.commonpath(workflow_parents)).expanduser().resolve(), None

    script_dir = Path(__file__).resolve().parent
    if project_dir.is_dir() and project_dir != script_dir:
        return project_dir, None

    cwd = Path.cwd().resolve()
    if cwd != script_dir:
        return cwd, None

    raise HaplotypeInputError(
        "Unable to determine a writable haplotype output directory automatically. Provide --output-dir explicitly."
    )


def _ensure_workflow_dir(output_root: Path, explicit_workflow_dir: Path | None = None) -> Path:
    workflow_dir = explicit_workflow_dir or (output_root / workflow_dir_name(next_workflow_number(output_root)))
    if workflow_dir.exists():
        raise HaplotypeInputError(f"Requested haplotype workflow directory already exists: {workflow_dir}")
    workflow_dir.mkdir(parents=True, exist_ok=False)
    return workflow_dir


def _detect_workflow_type(workflow_dir: Path, assay_mode: str) -> str:
    root_annotated = sorted(workflow_dir.glob("*.annotated.bam"))
    if root_annotated:
        if assay_mode not in RNA_MODES:
            raise HaplotypeInputError(
                f"Workflow '{workflow_dir}' looks like a reconcile RNA workflow but mode '{assay_mode}' was requested."
            )
        return "reconcile"

    if assay_mode in RNA_MODES and (workflow_dir / "annot").is_dir():
        return "dogme_rna"
    if assay_mode == "DNA" and (workflow_dir / "bams").is_dir():
        return "dogme_dna"

    if assay_mode in RNA_MODES:
        raise HaplotypeInputError(
            f"Workflow '{workflow_dir}' does not expose RNA annotated BAMs in annot/ or reconcile-style root outputs."
        )
    raise HaplotypeInputError(f"Workflow '{workflow_dir}' does not expose mapped DNA BAMs in bams/.")


def _workflow_bam_candidates(workflow_dir: Path, workflow_type: str) -> list[Path]:
    if workflow_type == "reconcile":
        return sorted(path.resolve() for path in workflow_dir.glob("*.annotated.bam") if path.is_file())
    if workflow_type == "dogme_rna":
        annot_dir = workflow_dir / "annot"
        return sorted(path.resolve() for path in annot_dir.glob("*.annotated.bam") if path.is_file())
    bam_dir = workflow_dir / "bams"
    return sorted(
        path.resolve()
        for path in bam_dir.glob("*.bam")
        if path.is_file() and not path.name.endswith(".unmapped.bam")
    )


def _discover_bams(workflow_dirs: list[Path], assay_mode: str, requested_names: set[str]) -> list[dict]:
    metadata: list[dict] = []
    seen: set[Path] = set()
    for workflow_dir in workflow_dirs:
        workflow_type = _detect_workflow_type(workflow_dir, assay_mode)
        candidates = _workflow_bam_candidates(workflow_dir, workflow_type)
        if requested_names:
            candidates = [path for path in candidates if path.name in requested_names]
        for bam_path in candidates:
            if bam_path in seen:
                continue
            seen.add(bam_path)
            metadata.append(
                {
                    "path": str(bam_path),
                    "name": bam_path.name,
                    "workflow_dir": str(workflow_dir),
                    "workflow_name": workflow_dir.name,
                    "workflow_type": workflow_type,
                }
            )
    return metadata


def _validate_bam(path: Path) -> tuple[str, bool, bool]:
    try:
        with pysam.AlignmentFile(path, "rb") as bam_file:
            sort_order = str((bam_file.header.to_dict().get("HD") or {}).get("SO") or "").strip().lower()
            if sort_order != "coordinate":
                raise HaplotypeInputError(f"BAM must be coordinate sorted: {path}")
            if not bam_file.has_index():
                raise HaplotypeInputError(f"BAM must be indexed: {path}")

            has_gx = False
            has_tx = False
            inspected = 0
            for read in bam_file.fetch(until_eof=True):
                if read.is_unmapped:
                    continue
                inspected += 1
                has_gx = has_gx or read.has_tag("GX")
                has_tx = has_tx or read.has_tag("TX")
                if inspected >= 2000 and has_gx and has_tx:
                    break
    except ValueError as exc:
        raise HaplotypeInputError(f"Failed to open BAM '{path}': {exc}") from exc
    return sort_order, has_gx, has_tx


def _open_vcf(path: Path) -> pysam.VariantFile:
    if not path.exists() or not path.is_file():
        raise HaplotypeInputError(f"VCF does not exist or is not a file: {path}")
    if not _has_any_vcf_index(path):
        raise HaplotypeInputError(f"VCF must be indexed with .tbi or .csi: {path}")
    try:
        return pysam.VariantFile(str(path))
    except (OSError, ValueError) as exc:
        raise HaplotypeInputError(f"Unable to open indexed VCF '{path}': {exc}") from exc


def _resolve_assignment_model(
    vcf_file: pysam.VariantFile,
    requested_samples: list[str],
    label_a_override: str | None,
    label_b_override: str | None,
) -> dict:
    available_samples = list(vcf_file.header.samples)
    if not available_samples:
        raise HaplotypeInputError("VCF header does not contain any samples.")

    if requested_samples:
        if len(requested_samples) not in {1, 2}:
            raise HaplotypeInputError("Select either one VCF sample or exactly two VCF samples.")
        missing = [sample for sample in requested_samples if sample not in available_samples]
        if missing:
            raise HaplotypeInputError(
                f"Selected VCF sample(s) not found in VCF header: {', '.join(missing)}"
            )
        selected_samples = requested_samples
    elif len(available_samples) == 1:
        selected_samples = available_samples[:1]
    elif len(available_samples) == 2:
        selected_samples = available_samples[:2]
    else:
        raise HaplotypeInputError(
            "VCF contains more than two samples. Select one sample for single-sample mode or two samples for comparison mode."
        )

    if len(selected_samples) == 1:
        return {
            "assignment_mode": "single_sample",
            "available_samples": available_samples,
            "selected_samples": selected_samples,
            "label_a": label_a_override or "haplotype1",
            "label_b": label_b_override or "haplotype2",
            "ambiguous_label": "ambiguous",
        }

    return {
        "assignment_mode": "two_sample",
        "available_samples": available_samples,
        "selected_samples": selected_samples,
        "label_a": label_a_override or selected_samples[0],
        "label_b": label_b_override or selected_samples[1],
        "ambiguous_label": "ambiguous",
    }


def _is_biallelic_snp(record: pysam.VariantRecord) -> bool:
    alleles = record.alleles or ()
    if len(alleles) != 2:
        return False
    return all(isinstance(allele, str) and len(allele) == 1 for allele in alleles)


def _build_variant_model(
    vcf_file: pysam.VariantFile,
    contig: str,
    assignment_config: dict,
) -> tuple[list[int], list[str], list[str], int]:
    positions: list[int] = []
    allele_a: list[str] = []
    allele_b: list[str] = []
    informative_count = 0

    try:
        iterator = vcf_file.fetch(contig)
    except (ValueError, OSError):
        return positions, allele_a, allele_b, informative_count

    assignment_mode = assignment_config["assignment_mode"]
    selected_samples = assignment_config["selected_samples"]

    for record in iterator:
        if not _is_biallelic_snp(record):
            continue

        if assignment_mode == "single_sample":
            call = record.samples[selected_samples[0]]
            genotype = call.get("GT") or ()
            if len(genotype) != 2 or genotype[0] is None or genotype[1] is None:
                continue
            if genotype[0] == genotype[1]:
                continue
            try:
                first_allele = record.alleles[genotype[0]].upper()
                second_allele = record.alleles[genotype[1]].upper()
            except (IndexError, AttributeError):
                continue
            positions.append(int(record.pos) - 1)
            allele_a.append(first_allele)
            allele_b.append(second_allele)
            informative_count += 1
            continue

        first_call = record.samples[selected_samples[0]]
        second_call = record.samples[selected_samples[1]]
        first_gt = first_call.get("GT") or ()
        second_gt = second_call.get("GT") or ()
        if len(first_gt) != 2 or len(second_gt) != 2:
            continue
        if None in first_gt or None in second_gt:
            continue
        if first_gt[0] != first_gt[1] or second_gt[0] != second_gt[1]:
            continue
        if first_gt[0] == second_gt[0]:
            continue
        try:
            first_allele = record.alleles[first_gt[0]].upper()
            second_allele = record.alleles[second_gt[0]].upper()
        except (IndexError, AttributeError):
            continue
        positions.append(int(record.pos) - 1)
        allele_a.append(first_allele)
        allele_b.append(second_allele)
        informative_count += 1

    return positions, allele_a, allele_b, informative_count


def _summarize_bam_inputs(metadata: list[dict], assay_mode: str) -> list[dict]:
    summarized: list[dict] = []
    for item in metadata:
        bam_path = Path(item["path"])
        sort_order, has_gx, has_tx = _validate_bam(bam_path)
        summarized.append(
            {
                **item,
                "sort_order": sort_order,
                "indexed": True,
                "gx_tag_present": has_gx if assay_mode in RNA_MODES else False,
                "tx_tag_present": has_tx if assay_mode in RNA_MODES else False,
            }
        )
    return summarized


def _selected_bam_metadata(
    input_bams: list[str],
    assay_mode: str,
    workflow_dirs: list[Path],
    requested_names: set[str],
) -> list[dict]:
    if input_bams:
        metadata = []
        for item in input_bams:
            bam_path = Path(item).expanduser().resolve()
            if not bam_path.exists() or not bam_path.is_file():
                raise HaplotypeInputError(f"Input BAM does not exist or is not a file: {bam_path}")
            metadata.append(
                {
                    "path": str(bam_path),
                    "name": bam_path.name,
                    "workflow_dir": str(bam_path.parent),
                    "workflow_name": bam_path.parent.name,
                    "workflow_type": "explicit",
                }
            )
        return _summarize_bam_inputs(metadata, assay_mode)

    discovered = _discover_bams(workflow_dirs, assay_mode, requested_names)
    if not discovered:
        raise HaplotypeInputError(
            "No eligible BAM inputs found. Provide --input-bam or point --workflow-dir at a workflow with matching BAM outputs."
        )
    return _summarize_bam_inputs(discovered, assay_mode)


def _count_informative_variants_for_bams(
    vcf_file: pysam.VariantFile,
    bam_metadata: list[dict],
    assignment_config: dict,
) -> tuple[dict[str, int], dict[str, int]]:
    informative_by_contig: Counter[str] = Counter()
    contigs_seen: set[str] = set()

    for item in bam_metadata:
        with pysam.AlignmentFile(item["path"], "rb") as bam_file:
            for contig in bam_file.references:
                if contig in contigs_seen:
                    continue
                contigs_seen.add(contig)
                _positions, _allele_a, _allele_b, count = _build_variant_model(vcf_file, contig, assignment_config)
                if count:
                    informative_by_contig[contig] = count

    return dict(informative_by_contig), {item["name"]: len(informative_by_contig) for item in bam_metadata}


def _build_preflight_payload(
    *,
    assay_mode: str,
    bam_metadata: list[dict],
    assignment_config: dict,
    vcf_path: Path,
    informative_by_contig: dict[str, int],
    output_root: Path,
    min_informative_sites: int,
    min_mapq: int,
    progress_read_interval: int,
) -> dict:
    return {
        "success": True,
        "status": "preflight_ready",
        "message": "Haplotype preflight validation passed. Ready for approval.",
        "mode": assay_mode,
        "assignment_mode": assignment_config["assignment_mode"],
        "vcf": {
            "path": str(vcf_path),
            "available_samples": assignment_config["available_samples"],
            "selected_samples": assignment_config["selected_samples"],
        },
        "labels": {
            "label_a": assignment_config["label_a"],
            "label_b": assignment_config["label_b"],
            "ambiguous": assignment_config["ambiguous_label"],
        },
        "inputs": {
            "count": len(bam_metadata),
            "bams": bam_metadata,
        },
        "thresholds": {
            "min_informative_sites": int(min_informative_sites),
            "min_mapq": int(min_mapq),
        },
        "contigs": {
            "informative_variant_counts": informative_by_contig,
            "informative_contig_count": len(informative_by_contig),
        },
        "execution_defaults": {
            "script_id": "haplotype_with_vcf/haplotype_with_vcf",
            "underlying_script_id": "haplotype_with_vcf/haplotype_with_vcf",
            "mode": assay_mode,
            "selected_samples": assignment_config["selected_samples"],
            "label_a": assignment_config["label_a"],
            "label_b": assignment_config["label_b"],
            "ambiguous_label": assignment_config["ambiguous_label"],
            "min_informative_sites": int(min_informative_sites),
            "min_mapq": int(min_mapq),
            "progress_read_interval": int(progress_read_interval),
        },
        "outputs": {
            "output_root": str(output_root),
            "artifacts": [],
        },
    }


def _unique_output_stems(bam_metadata: list[dict]) -> dict[str, str]:
    counts = Counter(Path(item["path"]).stem for item in bam_metadata)
    resolved: dict[str, str] = {}
    for item in bam_metadata:
        stem = Path(item["path"]).stem
        if counts[stem] == 1:
            resolved[item["path"]] = stem
            continue
        resolved[item["path"]] = f"{_slugify(item['workflow_name'])}-{stem}"
    return resolved


def _assign_read(
    read: pysam.AlignedSegment,
    positions: list[int],
    allele_a: list[str],
    allele_b: list[str],
    *,
    min_informative_sites: int,
    min_mapq: int,
) -> tuple[str, int, float, float, float]:
    if read.is_unmapped or read.mapping_quality < min_mapq or not positions:
        return "ambiguous", 0, 0.0, 0.0, 0.0

    if read.reference_start is None or read.reference_end is None:
        return "ambiguous", 0, 0.0, 0.0, 0.0

    start_idx = bisect_left(positions, read.reference_start)
    end_idx = bisect_left(positions, read.reference_end)
    if start_idx >= end_idx:
        return "ambiguous", 0, 0.0, 0.0, 0.0

    query_sequence = read.query_sequence or ""
    query_qualities = read.query_qualities or []
    support_a = 0.0
    support_b = 0.0
    informative_sites = 0

    aligned_pairs = read.get_aligned_pairs(matches_only=True)
    pair_index = 0
    variant_index = start_idx
    while pair_index < len(aligned_pairs) and variant_index < end_idx:
        query_pos, ref_pos = aligned_pairs[pair_index]
        target_pos = positions[variant_index]
        if ref_pos < target_pos:
            pair_index += 1
            continue
        if ref_pos > target_pos:
            variant_index += 1
            continue
        if query_pos is None or query_pos >= len(query_sequence):
            pair_index += 1
            variant_index += 1
            continue

        base = query_sequence[query_pos].upper()
        quality = float(query_qualities[query_pos]) if query_pos < len(query_qualities) else 1.0
        if base == allele_a[variant_index] and base != allele_b[variant_index]:
            support_a += quality
            informative_sites += 1
        elif base == allele_b[variant_index] and base != allele_a[variant_index]:
            support_b += quality
            informative_sites += 1

        pair_index += 1
        variant_index += 1

    if informative_sites < min_informative_sites:
        return "ambiguous", informative_sites, support_a, support_b, 0.0

    delta = abs(support_a - support_b)
    if support_a > support_b:
        return "label_a", informative_sites, support_a, support_b, delta
    if support_b > support_a:
        return "label_b", informative_sites, support_a, support_b, delta
    return "ambiguous", informative_sites, support_a, support_b, delta


def _set_assignment_tags(
    read: pysam.AlignedSegment,
    *,
    status: str,
    label: str,
    informative_sites: int,
    score_delta: float,
    hp_value: int | None,
) -> None:
    read.set_tag("ZL", label, value_type="Z")
    read.set_tag("ZS", status, value_type="Z")
    read.set_tag("ZI", int(informative_sites), value_type="i")
    read.set_tag("ZD", float(score_delta), value_type="f")
    if hp_value is not None:
        read.set_tag("HP", int(hp_value), value_type="i")
    elif read.has_tag("HP"):
        read.set_tag("HP", None)


def _write_tsv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        writer.writerows(rows)


def _index_bam(path: Path) -> Path:
    pysam.index(str(path))
    return Path(f"{path}.bai")


def _process_bam(
    *,
    bam_item: dict,
    vcf_path: Path,
    assignment_config: dict,
    output_dir: Path,
    output_stem: str,
    assay_mode: str,
    min_informative_sites: int,
    min_mapq: int,
    progress_read_interval: int,
    bam_index: int,
    total_bams: int,
) -> dict:
    bam_path = Path(bam_item["path"])
    label_a = assignment_config["label_a"]
    label_b = assignment_config["label_b"]
    ambiguous_label = assignment_config["ambiguous_label"]
    label_a_slug = _slugify(label_a)
    label_b_slug = _slugify(label_b)

    combined_path = output_dir / f"{output_stem}.haplotyped.bam"
    label_a_path = output_dir / f"{output_stem}.{label_a_slug}.haplotyped.bam"
    label_b_path = output_dir / f"{output_stem}.{label_b_slug}.haplotyped.bam"
    ambiguous_path = output_dir / f"{output_stem}.ambiguous.haplotyped.bam"

    _emit_progress(
        "BAM_START",
        bam=bam_path.name,
        bam_index=bam_index,
        total_bams=total_bams,
    )

    chromosome_rows: list[list[object]] = []
    global_counts: Counter[str] = Counter()
    gene_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    transcript_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    skipped_contigs: list[str] = []

    with pysam.VariantFile(str(vcf_path)) as vcf_file, pysam.AlignmentFile(str(bam_path), "rb") as bam_file:
        with (
            pysam.AlignmentFile(str(combined_path), "wb", template=bam_file) as combined_out,
            pysam.AlignmentFile(str(label_a_path), "wb", template=bam_file) as label_a_out,
            pysam.AlignmentFile(str(label_b_path), "wb", template=bam_file) as label_b_out,
            pysam.AlignmentFile(str(ambiguous_path), "wb", template=bam_file) as ambiguous_out,
        ):
            total_contigs = len(bam_file.references)
            for contig_index, contig in enumerate(bam_file.references, start=1):
                positions, allele_a, allele_b, informative_variant_count = _build_variant_model(
                    vcf_file,
                    contig,
                    assignment_config,
                )
                contig_counts: Counter[str] = Counter()
                contig_reads = 0
                contig_informative_sites = 0
                contig_started = time.time()

                if informative_variant_count == 0:
                    skipped_contigs.append(contig)

                _emit_progress(
                    "CHROM_START",
                    bam=bam_path.name,
                    chrom=contig,
                    bam_index=bam_index,
                    total_bams=total_bams,
                    chrom_index=contig_index,
                    total_chroms=total_contigs,
                    informative_variants=informative_variant_count,
                )

                for read in bam_file.fetch(contig):
                    contig_reads += 1
                    result_key, informative_sites, support_a, support_b, score_delta = _assign_read(
                        read,
                        positions,
                        allele_a,
                        allele_b,
                        min_informative_sites=min_informative_sites,
                        min_mapq=min_mapq,
                    )
                    contig_informative_sites += informative_sites

                    if result_key == "label_a":
                        final_label = label_a
                        hp_value = 1 if assignment_config["assignment_mode"] == "single_sample" else None
                        split_out = label_a_out
                    elif result_key == "label_b":
                        final_label = label_b
                        hp_value = 2 if assignment_config["assignment_mode"] == "single_sample" else None
                        split_out = label_b_out
                    else:
                        final_label = ambiguous_label
                        hp_value = None
                        split_out = ambiguous_out

                    status = "assigned" if result_key in {"label_a", "label_b"} else "ambiguous"
                    _set_assignment_tags(
                        read,
                        status=status,
                        label=final_label,
                        informative_sites=informative_sites,
                        score_delta=score_delta,
                        hp_value=hp_value,
                    )
                    combined_out.write(read)
                    split_out.write(read)

                    contig_counts[final_label] += 1
                    global_counts[final_label] += 1
                    global_counts["total_reads"] += 1

                    if assay_mode in RNA_MODES:
                        if read.has_tag("GX"):
                            gene_counts[(str(read.get_tag("GX")), final_label)] += 1
                        if read.has_tag("TX"):
                            transcript_counts[(str(read.get_tag("TX")), final_label)] += 1

                    if contig_reads % progress_read_interval == 0:
                        _emit_progress(
                            "CHROM_PROGRESS",
                            bam=bam_path.name,
                            chrom=contig,
                            reads=contig_reads,
                            assigned_a=contig_counts[label_a],
                            assigned_b=contig_counts[label_b],
                            ambiguous=contig_counts[ambiguous_label],
                            informative_sites=contig_informative_sites,
                        )

                chromosome_rows.append(
                    [
                        bam_path.name,
                        contig,
                        contig_reads,
                        contig_counts[label_a],
                        contig_counts[label_b],
                        contig_counts[ambiguous_label],
                        informative_variant_count,
                        contig_informative_sites,
                        round(time.time() - contig_started, 3),
                    ]
                )
                _emit_progress(
                    "CHROM_END",
                    bam=bam_path.name,
                    chrom=contig,
                    reads=contig_reads,
                    assigned_a=contig_counts[label_a],
                    assigned_b=contig_counts[label_b],
                    ambiguous=contig_counts[ambiguous_label],
                    informative_sites=contig_informative_sites,
                    elapsed_seconds=round(time.time() - contig_started, 3),
                )

    _emit_progress("INDEX_START", bam=bam_path.name)
    combined_index = _index_bam(combined_path)
    label_a_index = _index_bam(label_a_path)
    label_b_index = _index_bam(label_b_path)
    ambiguous_index = _index_bam(ambiguous_path)
    _emit_progress("INDEX_END", bam=bam_path.name)

    chromosome_summary_path = output_dir / f"{output_stem}.chromosomes.tsv"
    _write_tsv(
        chromosome_summary_path,
        [
            "bam_name",
            "chromosome",
            "total_reads",
            label_a_slug,
            label_b_slug,
            "ambiguous",
            "informative_variants",
            "informative_sites_observed",
            "elapsed_seconds",
        ],
        chromosome_rows,
    )

    genome_summary_path = output_dir / f"{output_stem}.summary.tsv"
    _write_tsv(
        genome_summary_path,
        ["bam_name", "total_reads", label_a_slug, label_b_slug, "ambiguous"],
        [[bam_path.name, global_counts["total_reads"], global_counts[label_a], global_counts[label_b], global_counts[ambiguous_label]]],
    )

    gene_summary_path = None
    transcript_summary_path = None
    if assay_mode in RNA_MODES:
        gene_rows = [[gene_id, label, count] for (gene_id, label), count in sorted(gene_counts.items())]
        transcript_rows = [[transcript_id, label, count] for (transcript_id, label), count in sorted(transcript_counts.items())]
        gene_summary_path = output_dir / f"{output_stem}.genes.tsv"
        transcript_summary_path = output_dir / f"{output_stem}.transcripts.tsv"
        _write_tsv(gene_summary_path, ["gene_id", "label", "count"], gene_rows)
        _write_tsv(transcript_summary_path, ["transcript_id", "label", "count"], transcript_rows)

    _emit_progress(
        "BAM_END",
        bam=bam_path.name,
        total_reads=global_counts["total_reads"],
        assigned_a=global_counts[label_a],
        assigned_b=global_counts[label_b],
        ambiguous=global_counts[ambiguous_label],
    )

    artifacts = [
        {"type": "haplotyped_bam", "path": str(combined_path)},
        {"type": "bam_index", "path": str(combined_index)},
        {"type": "split_bam", "path": str(label_a_path)},
        {"type": "bam_index", "path": str(label_a_index)},
        {"type": "split_bam", "path": str(label_b_path)},
        {"type": "bam_index", "path": str(label_b_index)},
        {"type": "split_bam", "path": str(ambiguous_path)},
        {"type": "bam_index", "path": str(ambiguous_index)},
        {"type": "summary_tsv", "path": str(chromosome_summary_path)},
        {"type": "summary_tsv", "path": str(genome_summary_path)},
    ]
    if gene_summary_path is not None:
        artifacts.append({"type": "gene_counts", "path": str(gene_summary_path)})
    if transcript_summary_path is not None:
        artifacts.append({"type": "transcript_counts", "path": str(transcript_summary_path)})

    return {
        "bam_name": bam_path.name,
        "input_bam": str(bam_path),
        "labels": {
            "label_a": label_a,
            "label_b": label_b,
            "ambiguous": ambiguous_label,
        },
        "counts": {
            "total_reads": global_counts["total_reads"],
            "label_a": global_counts[label_a],
            "label_b": global_counts[label_b],
            "ambiguous": global_counts[ambiguous_label],
        },
        "skipped_contigs": skipped_contigs,
        "artifacts": artifacts,
    }


def _build_error_payload(message: str) -> dict:
    return {
        "success": False,
        "status": "error",
        "message": message,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Haplotype long-read BAMs with an indexed VCF.")
    parser.add_argument("--project-dir", default=".", help="Project directory containing workflow* folders.")
    parser.add_argument("--workflow-dir", action="append", default=[], help="Explicit workflow directory (repeatable).")
    parser.add_argument("--input-bam", action="append", default=[], help="Explicit BAM input path (repeatable).")
    parser.add_argument("--bam-name", action="append", default=[], help="Restrict workflow discovery to these BAM file names.")
    parser.add_argument("--mode", required=True, choices=["DNA", "RNA", "cDNA"], help="Assay mode.")
    parser.add_argument("--vcf", required=True, help="Indexed VCF path.")
    parser.add_argument("--vcf-sample", action="append", default=[], help="Selected VCF sample (repeat for two-sample mode).")
    parser.add_argument("--label-a", help="Override label for assignment group A.")
    parser.add_argument("--label-b", help="Override label for assignment group B.")
    parser.add_argument("--output-dir", default=".", help="Parent directory or explicit workflowN destination.")
    parser.add_argument("--min-informative-sites", type=int, default=2, help="Minimum informative SNP observations required to assign a read.")
    parser.add_argument("--min-mapq", type=int, default=0, help="Minimum mapping quality required before attempting assignment.")
    parser.add_argument("--progress-read-interval", type=int, default=100000, help="Emit a progress line every N reads within a chromosome.")
    parser.add_argument("--preflight-only", action="store_true", help="Run validation only and stop before execution.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")

    args = parser.parse_args()

    payload: dict
    try:
        assay_mode = "CDNA" if str(args.mode).lower() == "cdna" else args.mode.upper()
        project_dir = Path(args.project_dir).expanduser().resolve()
        workflow_dirs = _discover_workflow_dirs(project_dir, list(args.workflow_dir or []))
        output_root, explicit_workflow_dir = _resolve_output_root(project_dir, workflow_dirs, args.output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        requested_names = {name.strip() for name in args.bam_name if str(name).strip()}
        bam_metadata = _selected_bam_metadata(list(args.input_bam or []), assay_mode, workflow_dirs, requested_names)

        vcf_path = Path(_trim_path_token(args.vcf)).expanduser().resolve()
        with _open_vcf(vcf_path) as vcf_file:
            assignment_config = _resolve_assignment_model(
                vcf_file,
                [sample.strip() for sample in args.vcf_sample if str(sample).strip()],
                args.label_a,
                args.label_b,
            )
            informative_by_contig, _informative_by_bam = _count_informative_variants_for_bams(
                vcf_file,
                bam_metadata,
                assignment_config,
            )

        preflight_payload = _build_preflight_payload(
            assay_mode=assay_mode,
            bam_metadata=bam_metadata,
            assignment_config=assignment_config,
            vcf_path=vcf_path,
            informative_by_contig=informative_by_contig,
            output_root=output_root if explicit_workflow_dir is None else explicit_workflow_dir,
            min_informative_sites=args.min_informative_sites,
            min_mapq=args.min_mapq,
            progress_read_interval=args.progress_read_interval,
        )

        if args.preflight_only:
            payload = preflight_payload
        else:
            workflow_dir = _ensure_workflow_dir(output_root, explicit_workflow_dir=explicit_workflow_dir)
            output_stems = _unique_output_stems(bam_metadata)
            bam_runs: list[dict] = []
            all_artifacts: list[dict] = []
            for bam_index, bam_item in enumerate(bam_metadata, start=1):
                bam_result = _process_bam(
                    bam_item=bam_item,
                    vcf_path=vcf_path,
                    assignment_config=assignment_config,
                    output_dir=workflow_dir,
                    output_stem=output_stems[bam_item["path"]],
                    assay_mode=assay_mode,
                    min_informative_sites=args.min_informative_sites,
                    min_mapq=args.min_mapq,
                    progress_read_interval=args.progress_read_interval,
                    bam_index=bam_index,
                    total_bams=len(bam_metadata),
                )
                bam_runs.append(bam_result)
                all_artifacts.extend(bam_result["artifacts"])

            _emit_progress("COMPLETE", total_bams=len(bam_runs), workflow=str(workflow_dir))
            payload = {
                "success": True,
                "status": "completed",
                "message": "Haplotype-with-VCF execution completed successfully.",
                "mode": assay_mode,
                "assignment_mode": assignment_config["assignment_mode"],
                "vcf": {
                    "path": str(vcf_path),
                    "selected_samples": assignment_config["selected_samples"],
                },
                "labels": {
                    "label_a": assignment_config["label_a"],
                    "label_b": assignment_config["label_b"],
                    "ambiguous": assignment_config["ambiguous_label"],
                },
                "inputs": {
                    "count": len(bam_metadata),
                    "bams": bam_metadata,
                },
                "thresholds": {
                    "min_informative_sites": int(args.min_informative_sites),
                    "min_mapq": int(args.min_mapq),
                },
                "workflow": {
                    "directory": str(workflow_dir),
                    "output_directory": str(workflow_dir),
                },
                "outputs": {
                    "output_root": str(workflow_dir),
                    "bam_runs": bam_runs,
                    "artifacts": all_artifacts,
                },
            }
    except (HaplotypeInputError, HaplotypeExecutionError) as exc:
        payload = _build_error_payload(str(exc))
    except Exception as exc:  # pragma: no cover - defensive last resort
        payload = _build_error_payload(f"Unexpected haplotype execution error: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload, indent=2), file=sys.stdout)
    return 0 if payload.get("success", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())