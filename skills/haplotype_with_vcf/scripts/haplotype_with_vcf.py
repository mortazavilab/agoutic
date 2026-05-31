#!/usr/bin/env python3
"""Workflow-aware long-read BAM haplotyping with an indexed VCF."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from contextlib import ExitStack
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
FOUNDER_REF_LABEL = "C57BL_6J"
FOUNDER_CANONICAL_ORDER = (
    FOUNDER_REF_LABEL,
    "A_J",
    "129S1_SvImJ",
    "NOD_ShiLtJ",
    "NZO_HlLtJ",
    "CAST_EiJ",
    "PWK_PhJ",
    "WSB_EiJ",
)
FOUNDER_SEPARATOR_RE = re.compile(r"[\s/_-]+")
FOUNDER_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
FOUNDER_F1_SUFFIX_RE = re.compile(r"f1$", re.IGNORECASE)
FOUNDER_FI_LOW_CONFIDENCE_WEIGHT = 0.5


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


def _collapse_founder_token(value: str) -> str:
    return FOUNDER_NON_ALNUM_RE.sub("", str(value or "").strip().lower())


def _founder_lookup_keys(value: str) -> tuple[str, ...]:
    raw_value = str(value or "").strip().lower()
    if not raw_value:
        return ()
    keys: list[str] = []
    collapsed = _collapse_founder_token(raw_value)
    if collapsed:
        keys.append(collapsed)
    prefix = _collapse_founder_token(FOUNDER_SEPARATOR_RE.split(raw_value, maxsplit=1)[0])
    if prefix and prefix not in keys:
        keys.append(prefix)
    return tuple(keys)


def _build_founder_alias_map() -> dict[str, str]:
    alias_map = {
        "ref": FOUNDER_REF_LABEL,
        "b6": FOUNDER_REF_LABEL,
        "c57bl6": FOUNDER_REF_LABEL,
        "c57bl6j": FOUNDER_REF_LABEL,
        "aj": "A_J",
        "a": "A_J",
        "129s1": "129S1_SvImJ",
        "129s1svimj": "129S1_SvImJ",
        "nod": "NOD_ShiLtJ",
        "nodshiltj": "NOD_ShiLtJ",
        "nzo": "NZO_HlLtJ",
        "nzohlltj": "NZO_HlLtJ",
        "cast": "CAST_EiJ",
        "casteij": "CAST_EiJ",
        "pwk": "PWK_PhJ",
        "pwkphj": "PWK_PhJ",
        "wsb": "WSB_EiJ",
        "wsbeij": "WSB_EiJ",
    }
    for canonical in FOUNDER_CANONICAL_ORDER:
        for key in _founder_lookup_keys(canonical):
            alias_map.setdefault(key, canonical)
    return alias_map


FOUNDER_ALIAS_TO_CANONICAL = _build_founder_alias_map()
FOUNDER_F1_ALIAS_KEYS = sorted(
    FOUNDER_ALIAS_TO_CANONICAL.items(),
    key=lambda item: (-len(item[0]), item[0]),
)


def _resolve_mouse_founder_alias(value: str) -> str | None:
    for key in _founder_lookup_keys(value):
        canonical = FOUNDER_ALIAS_TO_CANONICAL.get(key)
        if canonical:
            return canonical
    return None


def _split_requested_vcf_samples(requested_samples: list[str]) -> list[str]:
    selected: list[str] = []
    for raw_value in requested_samples:
        for token in str(raw_value or "").split(","):
            sample_name = str(token or "").strip()
            if sample_name:
                selected.append(sample_name)
    return selected


def _ordered_founder_labels(labels: list[str]) -> list[str]:
    requested = {label for label in labels if label}
    ordered = [label for label in FOUNDER_CANONICAL_ORDER if label in requested]
    extras = [label for label in labels if label and label not in ordered]
    return ordered + [label for label in extras if label not in ordered]


def _parse_founder_pair_shorthand(value: str) -> list[str] | None:
    raw_value = str(value or "").strip()
    if not raw_value or not FOUNDER_F1_SUFFIX_RE.search(raw_value):
        return None

    body = FOUNDER_F1_SUFFIX_RE.sub("", raw_value).strip()
    if not body:
        return None

    separated_tokens = [token for token in FOUNDER_SEPARATOR_RE.split(body) if token]
    if len(separated_tokens) == 2:
        first = _resolve_mouse_founder_alias(separated_tokens[0])
        second = _resolve_mouse_founder_alias(separated_tokens[1])
        if first and second and first != second:
            return _ordered_founder_labels([first, second])
        return None

    collapsed_body = _collapse_founder_token(body)
    if not collapsed_body:
        return None

    for prefix, first in FOUNDER_F1_ALIAS_KEYS:
        if not collapsed_body.startswith(prefix):
            continue
        remainder = collapsed_body[len(prefix):]
        if not remainder:
            continue
        second = FOUNDER_ALIAS_TO_CANONICAL.get(remainder)
        if second and second != first:
            return _ordered_founder_labels([first, second])
    return None


def _resolve_founder_panel_sources(available_samples: list[str]) -> dict[str, str | None] | None:
    founder_sources: dict[str, str | None] = {FOUNDER_REF_LABEL: None}
    for sample_name in available_samples:
        canonical = _resolve_mouse_founder_alias(sample_name)
        if canonical is None:
            return None
        if canonical in founder_sources and founder_sources[canonical] not in {None, sample_name}:
            raise HaplotypeInputError(
                f"VCF founder panel contains multiple samples for canonical founder '{canonical}': "
                f"{founder_sources[canonical]}, {sample_name}"
            )
        founder_sources[canonical] = sample_name
    return founder_sources if len(founder_sources) >= 3 else None


def _available_founder_labels(founder_sources: dict[str, str | None]) -> list[str]:
    return [label for label in FOUNDER_CANONICAL_ORDER if label in founder_sources]


def _resolve_requested_founder_labels(
    requested_samples: list[str],
    founder_sources: dict[str, str | None],
) -> list[str]:
    expanded_samples: list[str] = []
    for sample_name in requested_samples:
        founder_pair = _parse_founder_pair_shorthand(sample_name)
        if founder_pair:
            expanded_samples.extend(founder_pair)
        else:
            expanded_samples.append(sample_name)

    resolved: list[str] = []
    missing: list[str] = []
    for sample_name in expanded_samples:
        canonical = _resolve_mouse_founder_alias(sample_name)
        if canonical is None or canonical not in founder_sources:
            missing.append(sample_name)
            continue
        if canonical not in resolved:
            resolved.append(canonical)

    if missing:
        available = ", ".join(_available_founder_labels(founder_sources))
        raise HaplotypeInputError(
            f"Selected founder sample(s) not found in founder panel: {', '.join(missing)}. "
            f"Available founders: {available}"
        )
    return _ordered_founder_labels(resolved)


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


def _is_plain_vcf_path(path: Path) -> bool:
    return path.suffix.lower() == ".vcf"


def _is_bgzip_vcf_path(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    return len(suffixes) >= 2 and suffixes[-2:] == [".vcf", ".gz"]


def _compressed_vcf_path(path: Path) -> Path:
    if _is_bgzip_vcf_path(path):
        return path
    if _is_plain_vcf_path(path):
        return path.with_suffix(path.suffix + ".gz")
    raise HaplotypeInputError(
        f"VCF must be a .vcf or bgzip-compressed .vcf.gz file to support haplotyping: {path}"
    )


def _run_external_vcf_command(
    command: list[str],
    *,
    error_context: str,
    stdout_path: Path | None = None,
) -> None:
    try:
        if stdout_path is None:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
        else:
            with stdout_path.open("wb") as stdout_handle:
                completed = subprocess.run(command, stdout=stdout_handle, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise HaplotypeInputError(f"{error_context}: {exc}") from exc

    stderr_output = completed.stderr
    if isinstance(stderr_output, bytes):
        stderr_output = stderr_output.decode("utf-8", errors="replace")
    stderr_output = str(stderr_output or "").strip()
    if completed.returncode != 0:
        if stdout_path is not None and stdout_path.exists():
            stdout_path.unlink(missing_ok=True)
        detail = stderr_output or f"command exited with status {completed.returncode}"
        raise HaplotypeInputError(f"{error_context}: {detail}")


def _compress_plain_vcf(source_path: Path, output_path: Path) -> None:
    bgzip_path = shutil.which("bgzip")
    bcftools_path = shutil.which("bcftools")
    if bgzip_path:
        _run_external_vcf_command(
            [bgzip_path, "-c", str(source_path)],
            error_context=f"Failed to bgzip-compress VCF '{source_path}'",
            stdout_path=output_path,
        )
        return
    if bcftools_path:
        _run_external_vcf_command(
            [bcftools_path, "view", "-Oz", "-o", str(output_path), str(source_path)],
            error_context=f"Failed to compress VCF '{source_path}' with bcftools",
        )
        return

    try:
        pysam.tabix_compress(str(source_path), str(output_path), force=True)
    except (OSError, ValueError) as exc:
        raise HaplotypeInputError(f"Failed to compress VCF '{source_path}' with pysam: {exc}") from exc


def _build_vcf_index(path: Path) -> None:
    tabix_path = shutil.which("tabix")
    bcftools_path = shutil.which("bcftools")
    if tabix_path:
        _run_external_vcf_command(
            [tabix_path, "-f", "-p", "vcf", str(path)],
            error_context=f"Failed to index VCF '{path}' with tabix",
        )
    elif bcftools_path:
        _run_external_vcf_command(
            [bcftools_path, "index", "-f", "-t", str(path)],
            error_context=f"Failed to index VCF '{path}' with bcftools",
        )
    else:
        try:
            pysam.tabix_index(str(path), preset="vcf", force=True)
        except (OSError, ValueError) as exc:
            raise HaplotypeInputError(f"Failed to index VCF '{path}' with pysam: {exc}") from exc

    if not _has_any_vcf_index(path):
        raise HaplotypeInputError(f"VCF index was not created for '{path}'.")


def _prepare_vcf(path: Path) -> tuple[Path, bool]:
    if not path.exists() or not path.is_file():
        raise HaplotypeInputError(f"VCF does not exist or is not a file: {path}")

    if _has_any_vcf_index(path):
        return path, False

    if _is_bgzip_vcf_path(path):
        _build_vcf_index(path)
        return path, True

    if _is_plain_vcf_path(path):
        compressed_path = _compressed_vcf_path(path)
        needs_compression = not compressed_path.exists()
        if not needs_compression:
            try:
                needs_compression = compressed_path.stat().st_mtime < path.stat().st_mtime
            except OSError:
                needs_compression = True
        if needs_compression:
            _compress_plain_vcf(path, compressed_path)
        if needs_compression or not _has_any_vcf_index(compressed_path):
            _build_vcf_index(compressed_path)
        return compressed_path, True

    raise HaplotypeInputError(
        f"VCF must be a .vcf or bgzip-compressed .vcf.gz file to support haplotyping: {path}"
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

    founder_sources = _resolve_founder_panel_sources(available_samples)
    if founder_sources and (requested_samples or len(available_samples) > 2):
        if label_a_override or label_b_override:
            raise HaplotypeInputError(
                "Custom label overrides are not supported for founder-panel haplotyping. "
                "Founder labels always use canonical founder names."
            )

        available_founders = _available_founder_labels(founder_sources)
        selected_founders = (
            _resolve_requested_founder_labels(requested_samples, founder_sources)
            if requested_samples
            else available_founders
        )
        if len(selected_founders) < 2:
            raise HaplotypeInputError(
                "Founder-panel haplotyping requires either no founder restriction or at least two founders."
            )
        selected_sources = {label: founder_sources.get(label) for label in selected_founders}
        return {
            "assignment_mode": "founder_panel",
            "available_samples": available_founders,
            "selected_samples": selected_founders,
            "selected_sample_sources": selected_sources,
            "assignment_labels": selected_founders,
            "label_a": selected_founders[0],
            "label_b": selected_founders[1],
            "hp_values": {},
            "ambiguous_label": "ambiguous",
        }

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
            "selected_sample_sources": {selected_samples[0]: selected_samples[0]},
            "assignment_labels": [label_a_override or "haplotype1", label_b_override or "haplotype2"],
            "label_a": label_a_override or "haplotype1",
            "label_b": label_b_override or "haplotype2",
            "hp_values": {
                label_a_override or "haplotype1": 1,
                label_b_override or "haplotype2": 2,
            },
            "ambiguous_label": "ambiguous",
        }

    return {
        "assignment_mode": "two_sample",
        "available_samples": available_samples,
        "selected_samples": selected_samples,
        "selected_sample_sources": {sample_name: sample_name for sample_name in selected_samples},
        "assignment_labels": [label_a_override or selected_samples[0], label_b_override or selected_samples[1]],
        "label_a": label_a_override or selected_samples[0],
        "label_b": label_b_override or selected_samples[1],
        "hp_values": {},
        "ambiguous_label": "ambiguous",
    }


def _is_biallelic_snp(record: pysam.VariantRecord) -> bool:
    alleles = record.alleles or ()
    if len(alleles) != 2:
        return False
    return all(isinstance(allele, str) and len(allele) == 1 for allele in alleles)


def _is_single_nucleotide_variant(record: pysam.VariantRecord) -> bool:
    alleles = record.alleles or ()
    if len(alleles) < 2:
        return False
    return all(isinstance(allele, str) and len(allele) == 1 for allele in alleles)


def _call_confidence_weight(call: pysam.libcbcf.VariantRecordSample) -> float:
    fi_value = call.get("FI")
    if isinstance(fi_value, (tuple, list)):
        fi_value = next((item for item in fi_value if item is not None), None)
    if fi_value is None:
        return 1.0
    try:
        return 1.0 if int(fi_value) else FOUNDER_FI_LOW_CONFIDENCE_WEIGHT
    except (TypeError, ValueError):
        return 1.0


def _call_homozygous_single_base_allele(
    record: pysam.VariantRecord,
    call: pysam.libcbcf.VariantRecordSample,
) -> str | None:
    genotype = call.get("GT") or ()
    if len(genotype) != 2 or None in genotype:
        return None
    if genotype[0] != genotype[1]:
        return None
    try:
        allele = record.alleles[genotype[0]].upper()
    except (IndexError, AttributeError):
        return None
    return allele if isinstance(allele, str) and len(allele) == 1 else None


def _contig_fetch_candidates(contig: str) -> tuple[str, ...]:
    raw_contig = str(contig or "").strip()
    if not raw_contig:
        return ()

    candidates: list[str] = [raw_contig]
    if raw_contig.startswith("chr") and len(raw_contig) > 3:
        bare_contig = raw_contig[3:]
        candidates.append(bare_contig)
        if bare_contig == "M":
            candidates.append("MT")
        elif bare_contig == "MT":
            candidates.append("M")
    else:
        candidates.append(f"chr{raw_contig}")
        if raw_contig == "M":
            candidates.extend(["MT", "chrM"])
        elif raw_contig == "MT":
            candidates.extend(["M", "chrM"])

    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return tuple(deduped)


def _iter_contig_records(
    vcf_file: pysam.VariantFile,
    contig: str,
) -> pysam.libcbcf.VariantFileIterator | None:
    for candidate in _contig_fetch_candidates(contig):
        try:
            return vcf_file.fetch(candidate)
        except (ValueError, OSError):
            continue
    return None


def _build_variant_model(
    vcf_file: pysam.VariantFile,
    contig: str,
    assignment_config: dict,
) -> tuple[list[int], list[dict[str, list[tuple[str, float]]]], int]:
    positions: list[int] = []
    variant_supports: list[dict[str, list[tuple[str, float]]]] = []
    informative_count = 0

    iterator = _iter_contig_records(vcf_file, contig)
    if iterator is None:
        return positions, variant_supports, informative_count

    assignment_mode = assignment_config["assignment_mode"]
    selected_samples = assignment_config["selected_samples"]
    label_a = assignment_config["label_a"]
    label_b = assignment_config["label_b"]

    for record in iterator:
        if assignment_mode == "founder_panel":
            if not _is_single_nucleotide_variant(record):
                continue

            allele_to_labels: defaultdict[str, list[tuple[str, float]]] = defaultdict(list)
            for founder_label in selected_samples:
                sample_name = assignment_config["selected_sample_sources"].get(founder_label)
                if sample_name is None:
                    allele = str(record.ref or "").upper()
                    if len(allele) != 1:
                        continue
                    allele_to_labels[allele].append((founder_label, 1.0))
                    continue

                call = record.samples[sample_name]
                allele = _call_homozygous_single_base_allele(record, call)
                if allele is None:
                    continue
                allele_to_labels[allele].append((founder_label, _call_confidence_weight(call)))

            if len(allele_to_labels) < 2:
                continue

            positions.append(int(record.pos) - 1)
            variant_supports.append(dict(allele_to_labels))
            informative_count += 1
            continue

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
            variant_supports.append(
                {
                    first_allele: [(label_a, 1.0)],
                    second_allele: [(label_b, 1.0)],
                }
            )
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
        variant_supports.append(
            {
                first_allele: [(label_a, 1.0)],
                second_allele: [(label_b, 1.0)],
            }
        )
        informative_count += 1

    return positions, variant_supports, informative_count


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
                _positions, _variant_supports, count = _build_variant_model(vcf_file, contig, assignment_config)
                if count:
                    informative_by_contig[contig] = count

    return dict(informative_by_contig), {item["name"]: len(informative_by_contig) for item in bam_metadata}


def _resolve_vcf_contig_alias(contig: str, vcf_contigs: set[str]) -> str | None:
    for candidate in _contig_fetch_candidates(contig):
        if candidate in vcf_contigs:
            return candidate
    return None


def _summarize_bam_vcf_contig_overlap(
    vcf_file: pysam.VariantFile,
    bam_metadata: list[dict],
) -> dict:
    vcf_contigs = set(vcf_file.header.contigs)
    bam_contigs: list[str] = []
    for item in bam_metadata:
        with pysam.AlignmentFile(item["path"], "rb") as bam_file:
            for contig in bam_file.references:
                if contig not in bam_contigs:
                    bam_contigs.append(contig)

    matched_pairs: list[tuple[str, str]] = []
    unmatched_bam_contigs: list[str] = []
    for contig in bam_contigs:
        matched_vcf_contig = _resolve_vcf_contig_alias(contig, vcf_contigs)
        if matched_vcf_contig is None:
            unmatched_bam_contigs.append(contig)
            continue
        matched_pairs.append((contig, matched_vcf_contig))

    if not matched_pairs:
        preview = ", ".join(unmatched_bam_contigs[:5]) or ", ".join(bam_contigs[:5]) or "(none)"
        raise HaplotypeInputError(
            "No overlapping contigs were found between the BAM inputs and the VCF after chr-prefix normalization. "
            f"Example BAM contigs: {preview}"
        )

    matched_bam_contigs = [bam_contig for bam_contig, _vcf_contig in matched_pairs]
    matched_vcf_contigs = [vcf_contig for _bam_contig, vcf_contig in matched_pairs]
    return {
        "matched_bam_contig_count": len(matched_bam_contigs),
        "matched_vcf_contig_count": len({contig for contig in matched_vcf_contigs}),
        "matched_bam_contigs_preview": matched_bam_contigs[:10],
        "matched_vcf_contigs_preview": matched_vcf_contigs[:10],
        "unmatched_bam_contig_count": len(unmatched_bam_contigs),
        "unmatched_bam_contigs_preview": unmatched_bam_contigs[:10],
    }


def _build_preflight_payload(
    *,
    assay_mode: str,
    bam_metadata: list[dict],
    assignment_config: dict,
    vcf_path: Path,
    requested_vcf_path: Path,
    vcf_auto_prepared: bool,
    contig_summary: dict,
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
            "requested_path": str(requested_vcf_path),
            "auto_prepared": bool(vcf_auto_prepared),
            "available_samples": assignment_config["available_samples"],
            "selected_samples": assignment_config["selected_samples"],
            "selected_sample_sources": assignment_config["selected_sample_sources"],
        },
        "labels": {
            "assignment_labels": assignment_config["assignment_labels"],
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
            "informative_variant_counts": {},
            "informative_contig_count": 0,
            "preflight_variant_scan_skipped": True,
            **contig_summary,
        },
        "execution_defaults": {
            "script_id": "haplotype_with_vcf/haplotype_with_vcf",
            "underlying_script_id": "haplotype_with_vcf/haplotype_with_vcf",
            "mode": assay_mode,
            "selected_samples": assignment_config["selected_samples"],
            "assignment_labels": assignment_config["assignment_labels"],
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
    variant_supports: list[dict[str, list[tuple[str, float]]]],
    *,
    assignment_config: dict,
    min_informative_sites: int,
    min_mapq: int,
) -> tuple[str, int, float]:
    ambiguous_label = assignment_config["ambiguous_label"]
    if read.is_unmapped or read.mapping_quality < min_mapq or not positions:
        return ambiguous_label, 0, 0.0

    if read.reference_start is None or read.reference_end is None:
        return ambiguous_label, 0, 0.0

    start_idx = bisect_left(positions, read.reference_start)
    end_idx = bisect_left(positions, read.reference_end)
    if start_idx >= end_idx:
        return ambiguous_label, 0, 0.0

    query_sequence = read.query_sequence or ""
    query_qualities = read.query_qualities or []
    support_by_label = {label: 0.0 for label in assignment_config["assignment_labels"]}
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
        matched_labels = variant_supports[variant_index].get(base) or []
        if matched_labels:
            informative_sites += 1
            for label, weight in matched_labels:
                support_by_label[label] += quality * float(weight)

        pair_index += 1
        variant_index += 1

    if informative_sites < min_informative_sites:
        return ambiguous_label, informative_sites, 0.0

    ranked_support = sorted(
        ((score, label) for label, score in support_by_label.items()),
        key=lambda item: (-item[0], item[1]),
    )
    top_score, top_label = ranked_support[0]
    if top_score <= 0:
        return ambiguous_label, informative_sites, 0.0

    runner_up_score = ranked_support[1][0] if len(ranked_support) > 1 else 0.0
    if abs(top_score - runner_up_score) < 1e-9:
        return ambiguous_label, informative_sites, 0.0
    return top_label, informative_sites, float(top_score - runner_up_score)


def _set_assignment_tags(
    read: pysam.AlignedSegment,
    *,
    status: str,
    label: str,
    informative_sites: int,
    score_delta: float,
    hp_value: int | None,
    founder_id: str | None,
) -> None:
    read.set_tag("ZL", label, value_type="Z")
    read.set_tag("ZS", status, value_type="Z")
    read.set_tag("ZI", int(informative_sites), value_type="i")
    read.set_tag("ZD", float(score_delta), value_type="f")
    if hp_value is not None:
        read.set_tag("HP", int(hp_value), value_type="i")
    elif read.has_tag("HP"):
        read.set_tag("HP", None)
    if founder_id:
        read.set_tag("ZF", founder_id, value_type="Z")
    elif read.has_tag("ZF"):
        read.set_tag("ZF", None)


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
    assignment_labels = list(assignment_config["assignment_labels"])
    label_a = assignment_config["label_a"]
    label_b = assignment_config["label_b"]
    ambiguous_label = assignment_config["ambiguous_label"]
    label_slugs = {label: _slugify(label) for label in assignment_labels}

    combined_path = output_dir / f"{output_stem}.haplotyped.bam"
    split_paths = {
        label: output_dir / f"{output_stem}.{label_slugs[label]}.haplotyped.bam"
        for label in assignment_labels
    }
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
        with ExitStack() as stack:
            combined_out = stack.enter_context(pysam.AlignmentFile(str(combined_path), "wb", template=bam_file))
            split_outputs = {
                label: stack.enter_context(pysam.AlignmentFile(str(path), "wb", template=bam_file))
                for label, path in split_paths.items()
            }
            ambiguous_out = stack.enter_context(pysam.AlignmentFile(str(ambiguous_path), "wb", template=bam_file))
            total_contigs = len(bam_file.references)
            for contig_index, contig in enumerate(bam_file.references, start=1):
                positions, variant_supports, informative_variant_count = _build_variant_model(
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
                    final_label, informative_sites, score_delta = _assign_read(
                        read,
                        positions,
                        variant_supports,
                        assignment_config=assignment_config,
                        min_informative_sites=min_informative_sites,
                        min_mapq=min_mapq,
                    )
                    contig_informative_sites += informative_sites

                    hp_value = assignment_config.get("hp_values", {}).get(final_label)
                    founder_id = (
                        final_label
                        if assignment_config["assignment_mode"] == "founder_panel" and final_label != ambiguous_label
                        else None
                    )
                    split_out = ambiguous_out if final_label == ambiguous_label else split_outputs[final_label]
                    status = "assigned" if final_label != ambiguous_label else "ambiguous"
                    _set_assignment_tags(
                        read,
                        status=status,
                        label=final_label,
                        informative_sites=informative_sites,
                        score_delta=score_delta,
                        hp_value=hp_value,
                        founder_id=founder_id,
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
                        *[contig_counts[label] for label in assignment_labels],
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
    split_indexes = {label: _index_bam(path) for label, path in split_paths.items()}
    ambiguous_index = _index_bam(ambiguous_path)
    _emit_progress("INDEX_END", bam=bam_path.name)

    chromosome_summary_path = output_dir / f"{output_stem}.chromosomes.tsv"
    _write_tsv(
        chromosome_summary_path,
        [
            "bam_name",
            "chromosome",
            "total_reads",
            *[label_slugs[label] for label in assignment_labels],
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
        ["bam_name", "total_reads", *[label_slugs[label] for label in assignment_labels], "ambiguous"],
        [[
            bam_path.name,
            global_counts["total_reads"],
            *[global_counts[label] for label in assignment_labels],
            global_counts[ambiguous_label],
        ]],
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
        {"type": "summary_tsv", "path": str(chromosome_summary_path)},
        {"type": "summary_tsv", "path": str(genome_summary_path)},
    ]
    for label in assignment_labels:
        artifacts.append({"type": "split_bam", "path": str(split_paths[label])})
        artifacts.append({"type": "bam_index", "path": str(split_indexes[label])})
    artifacts.append({"type": "split_bam", "path": str(ambiguous_path)})
    artifacts.append({"type": "bam_index", "path": str(ambiguous_index)})
    if gene_summary_path is not None:
        artifacts.append({"type": "gene_counts", "path": str(gene_summary_path)})
    if transcript_summary_path is not None:
        artifacts.append({"type": "transcript_counts", "path": str(transcript_summary_path)})

    return {
        "bam_name": bam_path.name,
        "input_bam": str(bam_path),
        "labels": {
            "assignment_labels": assignment_labels,
            "label_a": label_a,
            "label_b": label_b,
            "ambiguous": ambiguous_label,
        },
        "counts": {
            "total_reads": global_counts["total_reads"],
            "label_a": global_counts[label_a],
            "label_b": global_counts[label_b],
            "ambiguous": global_counts[ambiguous_label],
            "by_label": {label: global_counts[label] for label in assignment_labels},
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
    parser = argparse.ArgumentParser(
        description="Haplotype long-read BAMs with a VCF. Plain .vcf inputs are compressed and indexed automatically when needed."
    )
    parser.add_argument("--project-dir", default=".", help="Project directory containing workflow* folders.")
    parser.add_argument("--workflow-dir", action="append", default=[], help="Explicit workflow directory (repeatable).")
    parser.add_argument("--input-bam", action="append", default=[], help="Explicit BAM input path (repeatable).")
    parser.add_argument("--bam-name", action="append", default=[], help="Restrict workflow discovery to these BAM file names.")
    parser.add_argument("--mode", required=True, choices=["DNA", "RNA", "cDNA"], help="Assay mode.")
    parser.add_argument(
        "--vcf",
        required=True,
        help="VCF path. Plain .vcf inputs are bgzip-compressed and indexed automatically when needed.",
    )
    parser.add_argument(
        "--vcf-sample",
        action="append",
        default=[],
        help="Selected VCF sample or founder alias. Repeat or pass comma-separated founder pairs for founder-panel mode.",
    )
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

        requested_vcf_path = Path(_trim_path_token(args.vcf)).expanduser().resolve()
        vcf_path, vcf_auto_prepared = _prepare_vcf(requested_vcf_path)
        with _open_vcf(vcf_path) as vcf_file:
            assignment_config = _resolve_assignment_model(
                vcf_file,
                _split_requested_vcf_samples(list(args.vcf_sample or [])),
                args.label_a,
                args.label_b,
            )
            contig_summary = _summarize_bam_vcf_contig_overlap(vcf_file, bam_metadata)

        preflight_payload = _build_preflight_payload(
            assay_mode=assay_mode,
            bam_metadata=bam_metadata,
            assignment_config=assignment_config,
            vcf_path=vcf_path,
            requested_vcf_path=requested_vcf_path,
            vcf_auto_prepared=vcf_auto_prepared,
            contig_summary=contig_summary,
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
                    "requested_path": str(requested_vcf_path),
                    "auto_prepared": bool(vcf_auto_prepared),
                    "available_samples": assignment_config["available_samples"],
                    "selected_samples": assignment_config["selected_samples"],
                    "selected_sample_sources": assignment_config["selected_sample_sources"],
                },
                "labels": {
                    "assignment_labels": assignment_config["assignment_labels"],
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