import json
import subprocess
import sys
from pathlib import Path

import pysam


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "haplotype_with_vcf" / "scripts" / "haplotype_with_vcf.py"


def _make_bam(path: Path) -> None:
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr1", "LN": 1000}],
    }
    with pysam.AlignmentFile(str(path), "wb", header=header) as bam_file:
        reads = [
            ("read_parent_a", "AACCA"),
            ("read_parent_b", "AGCTA"),
            ("read_ambiguous", "AACTA"),
        ]
        for query_name, sequence in reads:
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


def _make_vcf(path: Path) -> Path:
    plain_vcf = path.with_suffix("")
    plain_vcf.write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                "##contig=<ID=chr1,length=1000>",
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tparentA\tparentB",
                "chr1\t12\trs1\tA\tG\t.\tPASS\t.\tGT\t0/0\t1/1",
                "chr1\t14\trs2\tC\tT\t.\tPASS\t.\tGT\t0/0\t1/1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    pysam.tabix_compress(str(plain_vcf), str(path), force=True)
    pysam.tabix_index(str(path), preset="vcf", force=True)
    plain_vcf.unlink()
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