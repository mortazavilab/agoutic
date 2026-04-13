"""Tests for common.gtf_parser."""

from __future__ import annotations

import gzip
import os
from pathlib import Path

from common.gtf_parser import (
    cache_path_for_gtf,
    ensure_reference_caches,
    load_or_parse,
    parse_gtf,
)


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_gtf_human_preserves_case_and_strips_versions(tmp_path):
    gtf_path = _write_text(
        tmp_path / "references" / "GRCh38" / "gencode.test.gtf",
        (
            'chr11\tHAVANA\tgene\t17719568\t17722131\t.\t+\t.\tgene_id "ENSG00000129152.3"; gene_type "protein_coding"; gene_name "MYOD1";\n'
            'chr11\tHAVANA\ttranscript\t17719568\t17722131\t.\t+\t.\tgene_id "ENSG00000129152.3"; transcript_id "ENST00000378418.8"; gene_type "protein_coding"; gene_name "MYOD1"; transcript_name "MYOD1-201";\n'
        ),
    )

    parsed = parse_gtf(gtf_path)

    assert parsed["organism"] == "human"
    assert parsed["genes"]["ENSG00000129152"]["symbol"] == "MYOD1"
    assert parsed["transcripts"]["ENST00000378418"]["gene_id"] == "ENSG00000129152"
    assert parsed["transcripts"]["ENST00000378418"]["symbol"] == "MYOD1"


def test_parse_gtf_mouse_preserves_mixed_case(tmp_path):
    gtf_path = _write_text(
        tmp_path / "references" / "mm39" / "mouse.test.gtf",
        (
            'chr7\tHAVANA\tgene\t46025898\t46028523\t.\t+\t.\tgene_id "ENSMUSG00000009471.5"; gene_type "protein_coding"; gene_name "Myod1";\n'
            'chr7\tHAVANA\ttranscript\t46025898\t46028523\t.\t+\t.\tgene_id "ENSMUSG00000009471.5"; transcript_id "ENSMUST00000100495.1"; gene_type "protein_coding"; gene_name "Myod1"; transcript_name "Myod1-201";\n'
        ),
    )

    parsed = parse_gtf(gtf_path)

    assert parsed["organism"] == "mouse"
    assert parsed["genes"]["ENSMUSG00000009471"]["symbol"] == "Myod1"
    assert parsed["transcripts"]["ENSMUST00000100495"]["symbol"] == "Myod1"


def test_load_or_parse_writes_colocated_caches(tmp_path):
    gtf_path = _write_text(
        tmp_path / "references" / "GRCh38" / "gencode.test.gtf",
        'chr11\tHAVANA\tgene\t17719568\t17722131\t.\t+\t.\tgene_id "ENSG00000129152.3"; gene_type "protein_coding"; gene_name "MYOD1";\n',
    )

    first = load_or_parse(gtf_path)
    second = load_or_parse(gtf_path)
    gene_cache = cache_path_for_gtf(gtf_path, ".genes.tsv")
    transcript_cache = cache_path_for_gtf(gtf_path, ".transcripts.tsv")

    assert first["status"] == "rebuilt"
    assert second["status"] == "cache_hit"
    assert gene_cache.is_file()
    assert transcript_cache.is_file()


def test_load_or_parse_supports_gtf_gz(tmp_path):
    gtf_path = tmp_path / "references" / "GRCh38" / "gencode.test.gtf.gz"
    gtf_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(gtf_path, "wt", encoding="utf-8") as handle:
        handle.write(
            'chr11\tHAVANA\tgene\t17719568\t17722131\t.\t+\t.\tgene_id "ENSG00000129152.3"; gene_type "protein_coding"; gene_name "MYOD1";\n'
        )

    result = load_or_parse(gtf_path)

    assert result["gene_count"] == 1
    assert cache_path_for_gtf(gtf_path, ".genes.tsv").is_file()


def test_load_or_parse_rebuilds_when_gtf_is_newer(tmp_path):
    gtf_path = _write_text(
        tmp_path / "references" / "GRCh38" / "gencode.test.gtf",
        'chr11\tHAVANA\tgene\t17719568\t17722131\t.\t+\t.\tgene_id "ENSG00000129152.3"; gene_type "protein_coding"; gene_name "MYOD1";\n',
    )
    load_or_parse(gtf_path)

    gtf_path.write_text(
        (
            'chr11\tHAVANA\tgene\t17719568\t17722131\t.\t+\t.\tgene_id "ENSG00000129152.3"; gene_type "protein_coding"; gene_name "MYOD1";\n'
            'chr17\tHAVANA\tgene\t43044295\t43125483\t.\t-\t.\tgene_id "ENSG00000012048.23"; gene_type "protein_coding"; gene_name "BRCA1";\n'
        ),
        encoding="utf-8",
    )
    next_mtime = gtf_path.stat().st_mtime + 5
    os.utime(gtf_path, (next_mtime, next_mtime))

    rebuilt = load_or_parse(gtf_path)

    assert rebuilt["status"] == "rebuilt"
    assert rebuilt["gene_count"] == 2


def test_ensure_reference_caches_uses_configured_gtfs(tmp_path):
    human_gtf = _write_text(
        tmp_path / "references" / "GRCh38" / "gencode.test.gtf",
        'chr11\tHAVANA\tgene\t17719568\t17722131\t.\t+\t.\tgene_id "ENSG00000129152.3"; gene_type "protein_coding"; gene_name "MYOD1";\n',
    )
    mouse_gtf = _write_text(
        tmp_path / "references" / "mm39" / "mouse.test.gtf",
        'chr7\tHAVANA\tgene\t46025898\t46028523\t.\t+\t.\tgene_id "ENSMUSG00000009471.5"; gene_type "protein_coding"; gene_name "Myod1";\n',
    )

    results = ensure_reference_caches(
        {
            "GRCh38": {"gtf": str(human_gtf)},
            "mm39": {"gtf": str(mouse_gtf)},
            "default": "GRCh38",
        }
    )

    assert {result["genome"] for result in results} == {"GRCh38", "mm39"}
    assert all(result["gene_count"] == 1 for result in results)
    assert cache_path_for_gtf(human_gtf, ".genes.tsv").is_file()
    assert cache_path_for_gtf(mouse_gtf, ".genes.tsv").is_file()