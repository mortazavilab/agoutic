#!/usr/bin/env python3
"""
Build gene reference TSV files from Gencode GTF annotations.

Extracts gene_id, gene_symbol, gene_name, and biotype from a Gencode GTF
file and writes a compact TSV lookup table for use by GeneAnnotator.

Usage:
    # Human (Gencode v44)
    python scripts/build_gene_reference.py \
        --gtf gencode.v44.annotation.gtf.gz \
        --organism human \
        --output data/reference/human_genes.tsv

    # Mouse (Gencode vM33)
    python scripts/build_gene_reference.py \
        --gtf gencode.vM33.annotation.gtf.gz \
        --organism mouse \
        --output data/reference/mouse_genes.tsv
"""

import argparse
import gzip
import re
import sys
from pathlib import Path


def _parse_gtf_attributes(attr_str: str) -> dict[str, str]:
    """Parse the GTF attribute column into a dict."""
    attrs: dict[str, str] = {}
    for item in attr_str.strip().rstrip(";").split(";"):
        item = item.strip()
        if not item:
            continue
        m = re.match(r'(\w+)\s+"([^"]*)"', item)
        if m:
            attrs[m.group(1)] = m.group(2)
    return attrs


def build_reference(gtf_path: str, output_path: str) -> int:
    """Read a Gencode GTF and write a gene reference TSV.

    Returns the number of genes written.
    """
    opener = gzip.open if gtf_path.endswith(".gz") else open
    genes: dict[str, dict[str, str]] = {}

    with opener(gtf_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            feature_type = parts[2]
            if feature_type != "gene":
                continue

            attrs = _parse_gtf_attributes(parts[8])
            gene_id_raw = attrs.get("gene_id", "")
            # Strip version suffix
            gene_id = gene_id_raw.split(".")[0] if "." in gene_id_raw else gene_id_raw
            if not gene_id:
                continue

            symbol = attrs.get("gene_name", "")
            name = attrs.get("gene_name", "")  # Gencode uses gene_name for symbol
            biotype = attrs.get("gene_type", attrs.get("gene_biotype", ""))

            genes[gene_id] = {
                "symbol": symbol,
                "name": name,
                "biotype": biotype,
            }

    # Write TSV
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as out:
        out.write("gene_id\tgene_symbol\tgene_name\tbiotype\n")
        for gid in sorted(genes):
            entry = genes[gid]
            out.write(f"{gid}\t{entry['symbol']}\t{entry['name']}\t{entry['biotype']}\n")

    return len(genes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gene reference TSV from Gencode GTF")
    parser.add_argument("--gtf", required=True, help="Path to Gencode GTF file (.gtf or .gtf.gz)")
    parser.add_argument("--organism", required=True, choices=["human", "mouse"],
                        help="Organism name (determines output filename if --output omitted)")
    parser.add_argument("--output", default=None,
                        help="Output TSV path (default: data/reference/<organism>_genes.tsv)")
    args = parser.parse_args()

    if args.output is None:
        args.output = f"data/reference/{args.organism}_genes.tsv"

    count = build_reference(args.gtf, args.output)
    print(f"Wrote {count:,} genes to {args.output}")


if __name__ == "__main__":
    main()
