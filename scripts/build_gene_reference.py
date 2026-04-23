#!/usr/bin/env python3
"""Build colocated AGOUTIC gene reference TSV files from a GTF annotation."""

from __future__ import annotations

import argparse

from common.gtf_parser import (
    cache_path_for_gtf,
    parse_gtf,
    write_gene_cache,
    write_transcript_cache,
)


def build_reference(
    gtf_path: str,
    output_path: str | None = None,
    transcript_output_path: str | None = None,
    include_transcripts: bool = False,
) -> tuple[int, int]:
    """Read a GTF and write gene/transcript cache TSVs.

    Returns ``(n_genes, n_transcripts)``.
    """
    parsed = parse_gtf(gtf_path)
    gene_output = output_path or str(cache_path_for_gtf(gtf_path, ".genes.tsv"))
    write_gene_cache(parsed["genes"], gene_output)

    if include_transcripts:
        transcript_output = transcript_output_path or str(cache_path_for_gtf(gtf_path, ".transcripts.tsv"))
        write_transcript_cache(parsed["transcripts"], transcript_output)

    return parsed["gene_count"], parsed["transcript_count"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build AGOUTIC gene cache TSVs from a GTF")
    parser.add_argument("--gtf", required=True, help="Path to a GTF or GTF.GZ file")
    parser.add_argument("--organism", default=None, help="Optional organism label (retained for compatibility)")
    parser.add_argument(
        "--output",
        default=None,
        help="Gene-cache TSV path (default: next to the GTF as <stem>.genes.tsv)",
    )
    parser.add_argument(
        "--include-transcripts",
        action="store_true",
        help="Also write a colocated transcript cache TSV",
    )
    parser.add_argument("--transcript-output", default=None, help="Optional transcript-cache TSV path")
    args = parser.parse_args()

    gene_count, transcript_count = build_reference(
        args.gtf,
        output_path=args.output,
        transcript_output_path=args.transcript_output,
        include_transcripts=args.include_transcripts,
    )
    gene_output = args.output or str(cache_path_for_gtf(args.gtf, ".genes.tsv"))
    print(f"Wrote {gene_count:,} genes to {gene_output}")
    if args.include_transcripts:
        transcript_output = args.transcript_output or str(cache_path_for_gtf(args.gtf, ".transcripts.tsv"))
        print(f"Wrote {transcript_count:,} transcripts to {transcript_output}")


if __name__ == "__main__":
    main()
