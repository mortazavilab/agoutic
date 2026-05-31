import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pysam


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "haplotype_with_vcf" / "scripts" / "haplotype_with_vcf.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("haplotype_with_vcf_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

VCF_LINES = [
    "##fileformat=VCFv4.2",
    "##contig=<ID=chr1,length=1000>",
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tparentA\tparentB",
    "chr1\t12\trs1\tA\tG\t.\tPASS\t.\tGT\t0/0\t1/1",
    "chr1\t14\trs2\tC\tT\t.\tPASS\t.\tGT\t0/0\t1/1",
]

FOUNDER_VCF_LINES = [
    "##fileformat=VCFv4.2",
    "##contig=<ID=chr1,length=1000>",
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tA_J\tCAST_EiJ\tPWK_PhJ",
    "chr1\t12\tfounder1\tA\tG,C\t.\tPASS\t.\tGT:FI\t2/2:1\t1/1:1\t0/0:1",
    "chr1\t14\tfounder2\tC\tT,G\t.\tPASS\t.\tGT:FI\t2/2:1\t1/1:1\t0/0:1",
    "chr1\t16\tfounder3\tG\tA,T\t.\tPASS\t.\tGT:FI\t2/2:1\t0/0:1\t1/1:0",
    "chr1\t18\tfounder4\tT\tC,G\t.\tPASS\t.\tGT:FI\t2/2:1\t0/0:1\t1/1:0",
]

FOUNDER_READS = [
    ("read_b6", "AAACAGAT"),
    ("read_cast", "AGATAGAT"),
    ("read_aj", "ACAGATAG"),
    ("read_pwk", "AAACAAAC"),
    ("read_ambiguous", "ANANANAN"),
]


def _make_bam(path: Path, reads: list[tuple[str, str]] | None = None) -> None:
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr1", "LN": 1000}],
    }
    with pysam.AlignmentFile(str(path), "wb", header=header) as bam_file:
        read_records = reads or [
            ("read_parent_a", "AACCA"),
            ("read_parent_b", "AGCTA"),
            ("read_ambiguous", "AACTA"),
        ]
        for query_name, sequence in read_records:
            read = pysam.AlignedSegment()
            read.query_name = query_name
            read.query_sequence = sequence
            read.flag = 0
            read.reference_id = 0
            read.reference_start = 10
            read.mapping_quality = 60
            read.cigar = ((0, len(sequence)),)
            read.query_qualities = pysam.qualitystring_to_array("I" * len(sequence))
            read.set_tag("GX", "GENE1")
            read.set_tag("TX", "TX1")
            bam_file.write(read)
    pysam.index(str(path))


def _write_plain_vcf(path: Path, lines: list[str] | None = None) -> Path:
    path.write_text("\n".join(lines or VCF_LINES) + "\n", encoding="utf-8")
    return path


def _make_compressed_vcf_without_index(path: Path, lines: list[str] | None = None) -> Path:
    plain_vcf = _write_plain_vcf(path.with_suffix(""), lines=lines)
    pysam.tabix_compress(str(plain_vcf), str(path), force=True)
    plain_vcf.unlink()
    return path


def _make_vcf(path: Path, lines: list[str] | None = None) -> Path:
    _make_compressed_vcf_without_index(path, lines=lines)
    pysam.tabix_index(str(path), preset="vcf", force=True)
    return path


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_haplotype_with_vcf_preflight_discovers_rna_bam(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path)
    vcf_path = _make_vcf(tmp_path / "parents.vcf.gz")

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--output-dir", str(tmp_path / "workflow9"),
        "--preflight-only",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["mode"] == "RNA"
    assert payload["assignment_mode"] == "two_sample"
    assert payload["inputs"]["count"] == 1
    assert payload["inputs"]["bams"][0]["name"] == "sample1.mm39.annotated.bam"
    assert payload["vcf"]["selected_samples"] == ["parentA", "parentB"]


def test_haplotype_with_vcf_preflight_auto_prepares_plain_vcf(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path)
    plain_vcf = _write_plain_vcf(tmp_path / "parents.vcf")
    prepared_vcf = tmp_path / "parents.vcf.gz"

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(plain_vcf),
        "--output-dir", str(tmp_path / "workflow9"),
        "--preflight-only",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["vcf"]["path"] == str(prepared_vcf)
    assert payload["vcf"]["requested_path"] == str(plain_vcf)
    assert payload["vcf"]["auto_prepared"] is True
    assert prepared_vcf.exists()
    assert Path(f"{prepared_vcf}.tbi").exists()
    assert plain_vcf.exists()


def test_haplotype_with_vcf_runs_and_writes_tagged_outputs(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path)
    vcf_path = _make_vcf(tmp_path / "parents.vcf.gz")
    output_workflow = tmp_path / "workflow9"

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--output-dir", str(output_workflow),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["workflow"]["directory"] == str(output_workflow)

    combined_bam = output_workflow / "sample1.mm39.annotated.haplotyped.bam"
    split_a_bam = output_workflow / "sample1.mm39.annotated.parenta.haplotyped.bam"
    split_b_bam = output_workflow / "sample1.mm39.annotated.parentb.haplotyped.bam"
    ambiguous_bam = output_workflow / "sample1.mm39.annotated.ambiguous.haplotyped.bam"
    for path in (combined_bam, split_a_bam, split_b_bam, ambiguous_bam):
        assert path.exists()
        assert Path(f"{path}.bai").exists()

    labels = {}
    hp_presence = {}
    with pysam.AlignmentFile(str(combined_bam), "rb") as bam_file:
        for read in bam_file.fetch(until_eof=True):
            labels[read.query_name] = read.get_tag("ZL")
            hp_presence[read.query_name] = read.has_tag("HP")

    assert labels == {
        "read_parent_a": "parentA",
        "read_parent_b": "parentB",
        "read_ambiguous": "ambiguous",
    }
    assert hp_presence == {
        "read_parent_a": False,
        "read_parent_b": False,
        "read_ambiguous": False,
    }

    assert (output_workflow / "sample1.mm39.annotated.summary.tsv").exists()
    assert (output_workflow / "sample1.mm39.annotated.chromosomes.tsv").exists()
    assert (output_workflow / "sample1.mm39.annotated.genes.tsv").exists()
    assert (output_workflow / "sample1.mm39.annotated.transcripts.tsv").exists()


def test_haplotype_with_vcf_preflight_auto_indexes_compressed_vcf(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path)
    vcf_path = _make_compressed_vcf_without_index(tmp_path / "parents.vcf.gz")

    assert not Path(f"{vcf_path}.tbi").exists()

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--output-dir", str(tmp_path / "workflow9"),
        "--preflight-only",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["vcf"]["path"] == str(vcf_path)
    assert payload["vcf"]["requested_path"] == str(vcf_path)
    assert payload["vcf"]["auto_prepared"] is True
    assert Path(f"{vcf_path}.tbi").exists()


def test_haplotype_with_vcf_preflight_discovers_dna_bam(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    bam_dir = workflow_dir / "bams"
    bam_dir.mkdir(parents=True)
    bam_path = bam_dir / "sample1.mapped.bam"
    _make_bam(bam_path)
    vcf_path = _make_vcf(tmp_path / "parents.vcf.gz")

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "DNA",
        "--vcf", str(vcf_path),
        "--output-dir", str(tmp_path / "workflow9"),
        "--preflight-only",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["mode"] == "DNA"
    assert payload["inputs"]["bams"][0]["workflow_type"] == "dogme_dna"
    assert payload["inputs"]["bams"][0]["name"] == "sample1.mapped.bam"


def test_haplotype_with_vcf_preflight_discovers_reconcile_root_bam(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    workflow_dir.mkdir(parents=True)
    bam_path = workflow_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path)
    vcf_path = _make_vcf(tmp_path / "parents.vcf.gz")

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--output-dir", str(tmp_path / "workflow9"),
        "--preflight-only",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["mode"] == "RNA"
    assert payload["inputs"]["bams"][0]["workflow_type"] == "reconcile"
    assert payload["inputs"]["bams"][0]["name"] == "sample1.mm39.annotated.bam"


def test_haplotype_with_vcf_runs_full_founder_panel_with_canonical_labels(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path, reads=FOUNDER_READS)
    vcf_path = _make_vcf(tmp_path / "founders.vcf.gz", lines=FOUNDER_VCF_LINES)
    output_workflow = tmp_path / "workflow9"

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--output-dir", str(output_workflow),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["assignment_mode"] == "founder_panel"
    assert payload["vcf"]["selected_samples"] == ["C57BL_6J", "A_J", "CAST_EiJ", "PWK_PhJ"]
    assert payload["labels"]["assignment_labels"] == ["C57BL_6J", "A_J", "CAST_EiJ", "PWK_PhJ"]

    expected_paths = [
        output_workflow / "sample1.mm39.annotated.c57bl-6j.haplotyped.bam",
        output_workflow / "sample1.mm39.annotated.a-j.haplotyped.bam",
        output_workflow / "sample1.mm39.annotated.cast-eij.haplotyped.bam",
        output_workflow / "sample1.mm39.annotated.pwk-phj.haplotyped.bam",
        output_workflow / "sample1.mm39.annotated.ambiguous.haplotyped.bam",
    ]
    for path in expected_paths:
        assert path.exists()
        assert Path(f"{path}.bai").exists()

    labels = {}
    founder_tags = {}
    hp_presence = {}
    with pysam.AlignmentFile(str(output_workflow / "sample1.mm39.annotated.haplotyped.bam"), "rb") as bam_file:
        for read in bam_file.fetch(until_eof=True):
            labels[read.query_name] = read.get_tag("ZL")
            founder_tags[read.query_name] = read.get_tag("ZF") if read.has_tag("ZF") else None
            hp_presence[read.query_name] = read.has_tag("HP")

    assert labels == {
        "read_b6": "C57BL_6J",
        "read_cast": "CAST_EiJ",
        "read_aj": "A_J",
        "read_pwk": "PWK_PhJ",
        "read_ambiguous": "ambiguous",
    }
    assert founder_tags == {
        "read_b6": "C57BL_6J",
        "read_cast": "CAST_EiJ",
        "read_aj": "A_J",
        "read_pwk": "PWK_PhJ",
        "read_ambiguous": None,
    }
    assert hp_presence == {
        "read_b6": False,
        "read_cast": False,
        "read_aj": False,
        "read_pwk": False,
        "read_ambiguous": False,
    }


def test_haplotype_with_vcf_runs_founder_pair_from_comma_aliases(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path, reads=FOUNDER_READS)
    vcf_path = _make_vcf(tmp_path / "founders.vcf.gz", lines=FOUNDER_VCF_LINES)
    output_workflow = tmp_path / "workflow9"

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--vcf-sample", "CAST/J,B6",
        "--output-dir", str(output_workflow),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["assignment_mode"] == "founder_panel"
    assert payload["vcf"]["selected_samples"] == ["C57BL_6J", "CAST_EiJ"]
    assert payload["labels"]["assignment_labels"] == ["C57BL_6J", "CAST_EiJ"]

    ref_bam = output_workflow / "sample1.mm39.annotated.c57bl-6j.haplotyped.bam"
    cast_bam = output_workflow / "sample1.mm39.annotated.cast-eij.haplotyped.bam"
    ambiguous_bam = output_workflow / "sample1.mm39.annotated.ambiguous.haplotyped.bam"
    assert ref_bam.exists()
    assert cast_bam.exists()
    assert ambiguous_bam.exists()
    assert not (output_workflow / "sample1.mm39.annotated.a-j.haplotyped.bam").exists()
    assert not (output_workflow / "sample1.mm39.annotated.pwk-phj.haplotyped.bam").exists()

    labels = {}
    founder_tags = {}
    with pysam.AlignmentFile(str(output_workflow / "sample1.mm39.annotated.haplotyped.bam"), "rb") as bam_file:
        for read in bam_file.fetch(until_eof=True):
            labels[read.query_name] = read.get_tag("ZL")
            founder_tags[read.query_name] = read.get_tag("ZF") if read.has_tag("ZF") else None

    assert labels["read_b6"] == "C57BL_6J"
    assert labels["read_cast"] == "CAST_EiJ"
    assert labels["read_aj"] == "ambiguous"
    assert labels["read_ambiguous"] == "ambiguous"
    assert founder_tags["read_b6"] == "C57BL_6J"
    assert founder_tags["read_cast"] == "CAST_EiJ"
    assert founder_tags["read_aj"] is None


def test_haplotype_with_vcf_preflight_accepts_founder_f1_shorthand(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path, reads=FOUNDER_READS)
    vcf_path = _make_vcf(tmp_path / "founders.vcf.gz", lines=FOUNDER_VCF_LINES)

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--vcf-sample", "CASTB6F1",
        "--output-dir", str(tmp_path / "workflow9"),
        "--preflight-only",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["assignment_mode"] == "founder_panel"
    assert payload["vcf"]["selected_samples"] == ["C57BL_6J", "CAST_EiJ"]
    assert payload["vcf"]["selected_sample_sources"] == {
        "C57BL_6J": None,
        "CAST_EiJ": "CAST_EiJ",
    }


def test_haplotype_with_vcf_matches_founder_vcf_without_chr_prefix(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path, reads=FOUNDER_READS)
    vcf_lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=1,length=1000>",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tA_J\tCAST_EiJ\tPWK_PhJ",
        "1\t12\tfounder1\tA\tG,C\t.\tPASS\t.\tGT:FI\t2/2:1\t1/1:1\t0/0:1",
        "1\t14\tfounder2\tC\tT,G\t.\tPASS\t.\tGT:FI\t2/2:1\t1/1:1\t0/0:1",
        "1\t16\tfounder3\tG\tA,T\t.\tPASS\t.\tGT:FI\t2/2:1\t0/0:1\t1/1:0",
        "1\t18\tfounder4\tT\tC,G\t.\tPASS\t.\tGT:FI\t2/2:1\t0/0:1\t1/1:0",
    ]
    vcf_path = _make_vcf(tmp_path / "founders-no-chr.vcf.gz", lines=vcf_lines)
    output_workflow = tmp_path / "workflow9"

    result = _run_script(
        "--workflow-dir", str(workflow_dir),
        "--mode", "RNA",
        "--vcf", str(vcf_path),
        "--vcf-sample", "B6CASTF1",
        "--output-dir", str(output_workflow),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is True

    labels = {}
    with pysam.AlignmentFile(str(output_workflow / "sample1.mm39.annotated.haplotyped.bam"), "rb") as bam_file:
        for read in bam_file.fetch(until_eof=True):
            labels[read.query_name] = read.get_tag("ZL")

    assert labels["read_b6"] == "C57BL_6J"
    assert labels["read_cast"] == "CAST_EiJ"
    assert labels["read_aj"] == "ambiguous"


def test_haplotype_with_vcf_preflight_skips_full_variant_counting(tmp_path, monkeypatch, capsys):
    workflow_dir = tmp_path / "workflow7"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    bam_path = annot_dir / "sample1.mm39.annotated.bam"
    _make_bam(bam_path)
    vcf_path = _make_vcf(tmp_path / "parents.vcf.gz")

    script_module = _load_script_module()

    def _unexpected_count(*args, **kwargs):
        raise AssertionError("preflight should not perform full informative variant counting")

    monkeypatch.setattr(script_module, "_count_informative_variants_for_bams", _unexpected_count)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT_PATH),
            "--workflow-dir", str(workflow_dir),
            "--mode", "RNA",
            "--vcf", str(vcf_path),
            "--output-dir", str(tmp_path / "workflow9"),
            "--preflight-only",
            "--json",
        ],
    )

    exit_code = script_module.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["success"] is True
    assert payload["contigs"]["preflight_variant_scan_skipped"] is True
    assert payload["contigs"]["matched_bam_contig_count"] == 1