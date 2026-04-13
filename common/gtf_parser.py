"""Utilities for parsing GTF annotations into compact TSV caches.

The cache files are written alongside their source GTF so reference and
workflow-specific annotations stay colocated with the annotation they were
derived from.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
from pathlib import Path
from typing import Any


_ATTR_RE = re.compile(r'(\w+)\s+"([^"]*)"')
_ORGANISM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^ENSMUST\d"), "mouse"),
    (re.compile(r"^ENST\d"), "human"),
    (re.compile(r"^ENSMUSG\d"), "mouse"),
    (re.compile(r"^ENSG\d"), "human"),
]


def strip_version(identifier: str) -> str:
    """Remove a terminal numeric version suffix from an identifier."""
    value = str(identifier or "").strip()
    dot = value.find(".")
    if dot != -1 and value[dot + 1:].isdigit():
        return value[:dot]
    return value


def detect_organism(identifier: str) -> str | None:
    """Infer organism from a known Ensembl-style identifier."""
    value = strip_version(identifier)
    if not value:
        return None
    for pattern, organism in _ORGANISM_PATTERNS:
        if pattern.match(value):
            return organism
    return None


def parse_gtf_attributes(attr_str: str) -> dict[str, str]:
    """Parse the ninth GTF column into a dict."""
    attrs: dict[str, str] = {}
    for key, value in _ATTR_RE.findall(attr_str or ""):
        attrs[key] = value
    return attrs


def _cache_stem(path: str | Path) -> str:
    name = Path(path).name
    if name.endswith(".gtf.gz"):
        return name[:-7]
    if name.endswith(".gff.gz"):
        return name[:-7]
    if name.endswith(".gtf"):
        return name[:-4]
    if name.endswith(".gff"):
        return name[:-4]
    return Path(name).stem


def cache_path_for_gtf(gtf_path: str | Path, suffix: str = ".genes.tsv") -> Path:
    """Return the colocated cache path for a GTF."""
    gtf = Path(gtf_path).expanduser().resolve()
    return gtf.with_name(f"{_cache_stem(gtf)}{suffix}")


def write_gene_cache(genes: dict[str, dict[str, str]], output_path: str | Path) -> Path:
    """Write a gene cache TSV to *output_path*."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        handle.write("gene_id\tgene_symbol\tgene_name\tbiotype\n")
        for gene_id in sorted(genes):
            entry = genes[gene_id]
            handle.write(
                f"{gene_id}\t{entry.get('symbol', '')}\t{entry.get('name', '')}\t{entry.get('biotype', '')}\n"
            )
    return output


def write_transcript_cache(transcripts: dict[str, dict[str, str]], output_path: str | Path) -> Path:
    """Write a transcript cache TSV to *output_path*."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        handle.write("transcript_id\tgene_id\tgene_symbol\tgene_name\tbiotype\n")
        for transcript_id in sorted(transcripts):
            entry = transcripts[transcript_id]
            handle.write(
                f"{transcript_id}\t{entry.get('gene_id', '')}\t{entry.get('symbol', '')}\t{entry.get('name', '')}\t{entry.get('biotype', '')}\n"
            )
    return output


def save_gene_cache(genes: dict[str, dict[str, str]], gtf_path: str | Path) -> Path:
    """Write a colocated gene cache for *gtf_path*."""
    return write_gene_cache(genes, cache_path_for_gtf(gtf_path, ".genes.tsv"))


def save_transcript_cache(transcripts: dict[str, dict[str, str]], gtf_path: str | Path) -> Path:
    """Write a colocated transcript cache for *gtf_path*."""
    return write_transcript_cache(transcripts, cache_path_for_gtf(gtf_path, ".transcripts.tsv"))


def load_gene_cache(cache_path: str | Path) -> dict[str, dict[str, str]]:
    """Load a gene cache TSV."""
    mapping: dict[str, dict[str, str]] = {}
    path = Path(cache_path)
    if not path.is_file():
        return mapping

    with path.open(encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        col_idx = {name: idx for idx, name in enumerate(header)}
        id_idx = col_idx.get("gene_id")
        sym_idx = col_idx.get("gene_symbol")
        if id_idx is None or sym_idx is None:
            return mapping
        name_idx = col_idx.get("gene_name")
        bio_idx = col_idx.get("biotype")

        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= id_idx:
                continue
            gene_id = strip_version(parts[id_idx].strip())
            if not gene_id:
                continue
            entry = {
                "symbol": parts[sym_idx].strip() if len(parts) > sym_idx else "",
                "name": parts[name_idx].strip() if name_idx is not None and len(parts) > name_idx else "",
                "biotype": parts[bio_idx].strip() if bio_idx is not None and len(parts) > bio_idx else "",
            }
            mapping[gene_id] = entry
    return mapping


def load_transcript_cache(cache_path: str | Path) -> dict[str, dict[str, str]]:
    """Load a transcript cache TSV."""
    mapping: dict[str, dict[str, str]] = {}
    path = Path(cache_path)
    if not path.is_file():
        return mapping

    with path.open(encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        col_idx = {name: idx for idx, name in enumerate(header)}
        tx_idx = col_idx.get("transcript_id")
        if tx_idx is None:
            return mapping
        gene_idx = col_idx.get("gene_id")
        sym_idx = col_idx.get("gene_symbol")
        name_idx = col_idx.get("gene_name")
        bio_idx = col_idx.get("biotype")

        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= tx_idx:
                continue
            transcript_id = strip_version(parts[tx_idx].strip())
            if not transcript_id:
                continue
            mapping[transcript_id] = {
                "gene_id": strip_version(parts[gene_idx].strip()) if gene_idx is not None and len(parts) > gene_idx else "",
                "symbol": parts[sym_idx].strip() if sym_idx is not None and len(parts) > sym_idx else "",
                "name": parts[name_idx].strip() if name_idx is not None and len(parts) > name_idx else "",
                "biotype": parts[bio_idx].strip() if bio_idx is not None and len(parts) > bio_idx else "",
            }
    return mapping


def parse_gtf(gtf_path: str | Path) -> dict[str, Any]:
    """Parse a GTF file into gene and transcript lookup tables."""
    gtf = Path(gtf_path).expanduser().resolve()
    if not gtf.is_file():
        raise FileNotFoundError(f"GTF not found: {gtf}")

    genes: dict[str, dict[str, str]] = {}
    transcripts: dict[str, dict[str, str]] = {}
    detected_organism: str | None = None
    opener = gzip.open if str(gtf).endswith(".gz") else open

    with opener(gtf, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue

            feature_type = parts[2]
            attrs = parse_gtf_attributes(parts[8])
            gene_id_raw = attrs.get("gene_id", "")
            transcript_id_raw = attrs.get("transcript_id", "")
            gene_name = (attrs.get("gene_name") or attrs.get("gene") or "").strip()
            biotype = (
                attrs.get("gene_type")
                or attrs.get("gene_biotype")
                or attrs.get("transcript_type")
                or attrs.get("transcript_biotype")
                or ""
            ).strip()

            gene_id = strip_version(gene_id_raw)
            transcript_id = strip_version(transcript_id_raw)
            if detected_organism is None:
                detected_organism = detect_organism(gene_id or transcript_id_raw)

            if gene_id:
                gene_entry = genes.setdefault(gene_id, {"symbol": "", "name": "", "biotype": ""})
                if gene_name and (feature_type == "gene" or not gene_entry.get("symbol")):
                    gene_entry["symbol"] = gene_name
                    gene_entry["name"] = gene_name
                if biotype and (feature_type == "gene" or not gene_entry.get("biotype")):
                    gene_entry["biotype"] = biotype

            if transcript_id:
                tx_entry = transcripts.setdefault(
                    transcript_id,
                    {"gene_id": gene_id, "symbol": "", "name": "", "biotype": biotype},
                )
                if gene_id and not tx_entry.get("gene_id"):
                    tx_entry["gene_id"] = gene_id
                if gene_name and (feature_type == "transcript" or not tx_entry.get("symbol")):
                    tx_entry["symbol"] = gene_name
                    tx_entry["name"] = gene_name
                if biotype and (feature_type == "transcript" or not tx_entry.get("biotype")):
                    tx_entry["biotype"] = biotype

    for transcript_id, entry in transcripts.items():
        gene_id = entry.get("gene_id", "")
        gene_entry = genes.get(gene_id)
        if gene_entry is None:
            continue
        if not entry.get("symbol") and gene_entry.get("symbol"):
            entry["symbol"] = gene_entry["symbol"]
        if not entry.get("name") and gene_entry.get("name"):
            entry["name"] = gene_entry["name"]
        if not entry.get("biotype") and gene_entry.get("biotype"):
            entry["biotype"] = gene_entry["biotype"]

    return {
        "gtf_path": str(gtf),
        "genes": genes,
        "transcripts": transcripts,
        "gene_count": len(genes),
        "transcript_count": len(transcripts),
        "organism": detected_organism,
    }


def _caches_are_fresh(gtf_path: Path, cache_paths: list[Path]) -> bool:
    if not cache_paths or any(not path.is_file() for path in cache_paths):
        return False
    gtf_mtime = gtf_path.stat().st_mtime_ns
    return all(path.stat().st_mtime_ns >= gtf_mtime for path in cache_paths)


def load_or_parse(gtf_path: str | Path) -> dict[str, Any]:
    """Load colocated cache files when fresh, otherwise parse and rebuild."""
    gtf = Path(gtf_path).expanduser().resolve()
    gene_cache = cache_path_for_gtf(gtf, ".genes.tsv")
    transcript_cache = cache_path_for_gtf(gtf, ".transcripts.tsv")

    if _caches_are_fresh(gtf, [gene_cache, transcript_cache]):
        genes = load_gene_cache(gene_cache)
        transcripts = load_transcript_cache(transcript_cache)
        organism = None
        for key in list(genes.keys())[:10] + list(transcripts.keys())[:10]:
            organism = detect_organism(key)
            if organism:
                break
        return {
            "gtf_path": str(gtf),
            "gene_cache_path": str(gene_cache),
            "transcript_cache_path": str(transcript_cache),
            "genes": genes,
            "transcripts": transcripts,
            "gene_count": len(genes),
            "transcript_count": len(transcripts),
            "organism": organism,
            "status": "cache_hit",
        }

    parsed = parse_gtf(gtf)
    save_gene_cache(parsed["genes"], gtf)
    save_transcript_cache(parsed["transcripts"], gtf)
    parsed.update(
        {
            "gene_cache_path": str(gene_cache),
            "transcript_cache_path": str(transcript_cache),
            "status": "rebuilt",
        }
    )
    return parsed


def ensure_reference_caches(reference_genomes: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ensure colocated caches exist for configured shared reference GTFs."""
    if reference_genomes is None:
        from launchpad.config import REFERENCE_GENOMES

        reference_genomes = REFERENCE_GENOMES

    results: list[dict[str, Any]] = []
    for genome_name, config in reference_genomes.items():
        if not isinstance(config, dict):
            continue
        gtf_value = config.get("gtf")
        if not gtf_value:
            continue
        gtf_path = Path(gtf_value).expanduser().resolve()
        if not gtf_path.is_file():
            results.append(
                {
                    "genome": genome_name,
                    "gtf_path": str(gtf_path),
                    "status": "missing_gtf",
                    "gene_count": 0,
                    "transcript_count": 0,
                }
            )
            continue
        parsed = load_or_parse(gtf_path)
        parsed["genome"] = genome_name
        results.append(parsed)
    return results


def _print_status(results: list[dict[str, Any]]) -> int:
    emitted = 0
    for result in results:
        genome = result.get("genome") or Path(result["gtf_path"]).parent.name or "reference"
        status = result.get("status", "unknown")
        if status == "missing_gtf":
            print(f"Gene cache: {genome} - missing GTF ({result['gtf_path']})")
            emitted += 1
            continue
        verb = "up to date" if status == "cache_hit" else "cached"
        print(
            f"Gene cache: {genome} - {verb} "
            f"({result['gene_count']:,} genes, {result['transcript_count']:,} transcripts)"
        )
        emitted += 1
    return emitted


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse GTF files into colocated AGOUTIC cache TSVs")
    parser.add_argument("--gtf", help="Path to a GTF or GTF.GZ file to cache")
    parser.add_argument("--ensure-caches", action="store_true", help="Ensure caches exist for configured shared reference GTFs")
    args = parser.parse_args()

    if args.ensure_caches:
        _print_status(ensure_reference_caches())
        return

    if not args.gtf:
        parser.error("Provide --gtf or use --ensure-caches.")

    result = load_or_parse(args.gtf)
    print(
        f"Gene cache: {_cache_stem(args.gtf)} - {result['status']} "
        f"({result['gene_count']:,} genes, {result['transcript_count']:,} transcripts)"
    )


if __name__ == "__main__":
    main()