import json
import os
import subprocess
import sys
import io
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "skills" / "reconcile_bams" / "scripts" / "check_workflow_references.py"
RECONCILE = ROOT / "skills" / "reconcile_bams" / "scripts" / "reconcile_bams.py"


def _load_reconcile_module():
    spec = importlib.util.spec_from_file_location("reconcile_bams_wrapper", RECONCILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_script(script: Path, args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


def _write_minimal_gtf(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                'chr1\ttest\tgene\t101\t300\t.\t+\t.\tgene_id "GX1"; gene_name "Gene1";',
                'chr1\ttest\ttranscript\t101\t300\t.\t+\t.\tgene_id "GX1"; transcript_id "TX1"; gene_name "Gene1";',
                'chr1\ttest\texon\t101\t150\t.\t+\t.\tgene_id "GX1"; transcript_id "TX1"; exon_number "1";',
                'chr1\ttest\texon\t251\t300\t.\t+\t.\tgene_id "GX1"; transcript_id "TX1"; exon_number "2";',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_annotated_bam(path: Path, sample_name: str) -> None:
    pysam = pytest.importorskip("pysam")
    header = {"HD": {"VN": "1.0"}, "SQ": [{"SN": "chr1", "LN": 10000}]}
    with pysam.AlignmentFile(path, "wb", header=header) as bam_out:
        read = pysam.AlignedSegment()
        read.query_name = sample_name
        read.query_sequence = "A" * 100
        read.flag = 0
        read.reference_id = 0
        read.reference_start = 100
        read.mapping_quality = 60
        read.cigartuples = [(0, 50), (3, 100), (0, 50)]
        read.query_qualities = pysam.qualitystring_to_array("I" * 100)
        read.set_tag("TX", "TX1")
        read.set_tag("GX", "GX1")
        read.set_tag("TT", "KNOWN")
        bam_out.write(read)


def test_reference_helper_detects_consistent_reference(tmp_path: Path):
    wf1 = tmp_path / "workflow1"
    wf2 = tmp_path / "workflow2"
    wf1.mkdir()
    wf2.mkdir()
    (wf1 / "nextflow.config").write_text("params.reference_genome = 'GRCh38'\n", encoding="utf-8")
    (wf2 / "nextflow.config").write_text("params.reference = 'hg38'\n", encoding="utf-8")

    result = _run_script(HELPER, ["--workflow-dir", str(wf1), "--workflow-dir", str(wf2), "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["consensus_reference"] == "GRCh38"


def test_reference_helper_rejects_mixed_references(tmp_path: Path):
    wf1 = tmp_path / "workflow1"
    wf2 = tmp_path / "workflow2"
    wf1.mkdir()
    wf2.mkdir()
    (wf1 / "nextflow.config").write_text("params.reference_genome = 'GRCh38'\n", encoding="utf-8")
    (wf2 / "nextflow.config").write_text("params.reference_genome = 'mm39'\n", encoding="utf-8")

    result = _run_script(HELPER, ["--workflow-dir", str(wf1), "--workflow-dir", str(wf2), "--json"])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("Mixed references" in err for err in payload["errors"])


def test_reference_helper_detects_reference_from_realistic_nextflow_config_tokens(tmp_path: Path):
    wf1 = tmp_path / "workflow1"
    wf2 = tmp_path / "workflow2"
    wf1.mkdir()
    wf2.mkdir()
    config_text = (
        "        [name: 'mm39', genome: '/media/backup_disk/agoutic_root/references/mm39/IGVFFI9282QLXO.fasta', annot: '/media/backup_disk/agoutic_root/references/mm39/IGVFFI4777RDZK.gtf']\n"
        "    kallistoIndex = '/media/backup_disk/agoutic_root/references/mm39/mm39GencM36_k63.idx'\n"
        "    t2g = '/media/backup_disk/agoutic_root/references/mm39/mm39GencM36_k63.t2g'\n"
    )
    (wf1 / "nextflow.config").write_text(config_text, encoding="utf-8")
    (wf2 / "nextflow.config").write_text(config_text, encoding="utf-8")

    result = _run_script(HELPER, ["--workflow-dir", str(wf1), "--workflow-dir", str(wf2), "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["consensus_reference"] == "mm39"
    assert payload["workflows"][0]["resolution_source"] == "config_token_fallback"
    assert payload["workflows"][1]["resolution_source"] == "config_token_fallback"


def test_reference_helper_falls_back_to_annotated_bam_names_when_configs_missing(tmp_path: Path):
    wf1 = tmp_path / "workflow1"
    wf2 = tmp_path / "workflow2"
    (wf1 / "annot").mkdir(parents=True)
    (wf2 / "annot").mkdir(parents=True)
    (wf1 / "annot" / "C2C12r1.mm39.annotated.bam").write_text("x", encoding="utf-8")
    (wf2 / "annot" / "C2C12r3.mm39.annotated.bam").write_text("x", encoding="utf-8")

    result = _run_script(HELPER, ["--workflow-dir", str(wf1), "--workflow-dir", str(wf2), "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["consensus_reference"] == "mm39"
    assert payload["workflows"][0]["resolution_source"] == "annotated_bam_filename"
    assert payload["workflows"][1]["resolution_source"] == "annotated_bam_filename"


def test_reference_helper_rejects_mixed_bam_name_references_when_configs_missing(tmp_path: Path):
    wf1 = tmp_path / "workflow1"
    wf2 = tmp_path / "workflow2"
    (wf1 / "annot").mkdir(parents=True)
    (wf2 / "annot").mkdir(parents=True)
    (wf1 / "annot" / "C2C12r1.mm39.annotated.bam").write_text("x", encoding="utf-8")
    (wf2 / "annot" / "C2C12r3.GRCh38.annotated.bam").write_text("x", encoding="utf-8")

    result = _run_script(HELPER, ["--workflow-dir", str(wf1), "--workflow-dir", str(wf2), "--json"])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("Mixed references" in err for err in payload["errors"])


def test_reconcile_script_enforces_filename_contract(tmp_path: Path):
    bad = tmp_path / "badname.bam"
    bad.write_text("x", encoding="utf-8")

    result = _run_script(RECONCILE, ["--input-bam", str(bad), "--json"])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "Expected '<sample>.<reference>.annotated.bam'" in payload["error"]


def test_reconcile_script_rejects_mixed_bam_references(tmp_path: Path):
    bam1 = tmp_path / "sample1.GRCh38.annotated.bam"
    bam2 = tmp_path / "sample2.mm39.annotated.bam"
    bam1.write_text("x", encoding="utf-8")
    bam2.write_text("x", encoding="utf-8")

    result = _run_script(
        RECONCILE,
        ["--input-bam", str(bam1), "--input-bam", str(bam2), "--json"],
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "Mixed BAM references detected" in payload["error"]


def test_reconcile_script_uses_default_gtf_and_creates_symlinked_workflow(tmp_path: Path):
    data_root = tmp_path / "data"
    gtf = data_root / "references" / "GRCh38" / "gencode.v29.primary_assembly.annotation_UCSC_names.gtf"
    gtf.parent.mkdir(parents=True)
    _write_minimal_gtf(gtf)

    bam = tmp_path / "sample1.GRCh38.annotated.bam"
    _write_annotated_bam(bam, "sample1")

    env = dict(os.environ)
    env["AGOUTIC_DATA"] = str(data_root)

    result = _run_script(
        RECONCILE,
        ["--input-bam", str(bam), "--output-dir", str(tmp_path), "--json"],
        env=env,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)

    assert payload["status"] == "completed"
    assert payload["gtf"]["source"] == "default"
    assert Path(payload["gtf"]["path"]) == gtf.resolve()
    assert payload["execution"]["script_id"] == "reconcile_bams/reconcileBams"

    workflow_dir = Path(payload["workflow"]["directory"])
    assert workflow_dir.name == "workflow1"
    assert (workflow_dir / "input").is_dir()
    assert payload["workflow"]["output_directory"] == str(workflow_dir)

    links = payload["workflow"]["symlinks"]
    assert len(links) == 1
    link_path = Path(links[0]["link"])
    assert link_path.is_symlink()
    assert link_path.resolve() == bam.resolve()

    artifacts = payload["outputs"]["artifacts"]
    reconciled = [a for a in artifacts if a.get("type") == "reconciled_bam"]
    assert reconciled
    assert all(Path(item["path"]).is_file() for item in reconciled)
    assert any(a.get("type") == "annotation_gtf" for a in artifacts)


def test_reconcile_script_defaults_output_root_to_workflow_parent_when_omitted(tmp_path: Path):
    project_root = tmp_path / "project"
    wf2 = project_root / "workflow2" / "annot"
    wf3 = project_root / "workflow3" / "annot"
    wf2.mkdir(parents=True)
    wf3.mkdir(parents=True)

    bam1 = wf2 / "sample1.GRCh38.annotated.bam"
    bam2 = wf3 / "sample2.GRCh38.annotated.bam"
    _write_annotated_bam(bam1, "sample1")
    _write_annotated_bam(bam2, "sample2")

    data_root = tmp_path / "data"
    gtf = data_root / "references" / "GRCh38" / "gencode.v29.primary_assembly.annotation_UCSC_names.gtf"
    gtf.parent.mkdir(parents=True)
    _write_minimal_gtf(gtf)
    (project_root / "workflow2" / "nextflow.config").write_text(
        f"params.genome_annot_refs = [[name: 'GRCh38', annot: '{gtf}']]\n",
        encoding="utf-8",
    )
    (project_root / "workflow3" / "nextflow.config").write_text(
        f"params.genome_annot_refs = [[name: 'GRCh38', annot: '{gtf}']]\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["AGOUTIC_DATA"] = str(data_root)

    result = _run_script(
        RECONCILE,
        [
            "--workflow-dir", str(project_root / "workflow2"),
            "--workflow-dir", str(project_root / "workflow3"),
            "--json",
        ],
        env=env,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"
    assert payload["gtf"]["source"] == "workflow_config"
    assert payload["outputs"]["output_root"] == str(project_root.resolve())
    assert Path(payload["workflow"]["directory"]).parent == project_root.resolve()
    assert Path(payload["workflow"]["directory"]).name == "workflow4"


def test_reconcile_script_requires_manual_gtf_when_default_missing(tmp_path: Path):
    bam = tmp_path / "sample1.GRCh38.annotated.bam"
    bam.write_text("x", encoding="utf-8")

    env = dict(os.environ)
    env["AGOUTIC_DATA"] = str(tmp_path / "empty_data")

    result = _run_script(RECONCILE, ["--input-bam", str(bam), "--json"], env=env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["status"] == "needs_manual_gtf"
    assert payload["required_input"]["field"] == "annotation_gtf"


def test_reconcile_script_preflight_ready_with_manual_gtf(tmp_path: Path):
    bam = tmp_path / "sample1.GRCh38.annotated.bam"
    _write_annotated_bam(bam, "sample1")
    manual_gtf = tmp_path / "manual.GRCh38.annotation.gtf"
    _write_minimal_gtf(manual_gtf)

    env = dict(os.environ)
    env["AGOUTIC_DATA"] = str(tmp_path / "empty_data")

    result = _run_script(
        RECONCILE,
        [
            "--input-bam",
            str(bam),
            "--annotation-gtf",
            str(manual_gtf),
            "--preflight-only",
            "--json",
        ],
        env=env,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "preflight_ready"
    assert payload["gtf"]["source"] == "manual"
    assert payload["execution_defaults"]["underlying_script_id"] == "reconcile_bams/reconcileBams"


def test_reconcile_command_uses_unbuffered_python():
    module = _load_reconcile_module()
    command = module._build_reconcile_command(
        script_path=Path("/tmp/reconcileBams.py"),
        bam_paths=[Path("/tmp/sample.GRCh38.annotated.bam")],
        annotation_gtf=Path("/tmp/annotation.gtf"),
        output_prefix="reconcile",
        output_dir=Path("/tmp/output"),
        gene_prefix="NOVG",
        tx_prefix="NOVT",
        id_tag="TX",
        gene_tag="GX",
        threads=4,
        exon_merge_distance=5,
        min_tpm=1.0,
        min_samples=1,
        filter_known=False,
    )
    assert command[:2] == [sys.executable, "-u"]


def test_run_reconcile_command_streams_stdout_and_stderr(monkeypatch, capsys):
    module = _load_reconcile_module()

    class _FakeProcess:
        def __init__(self):
            self.stdout = io.StringIO("step one\nstep two\n")
            self.stderr = io.StringIO("warning line\n")
            self.returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess())

    return_code, stdout_text, stderr_text = module._run_reconcile_command(["python", "demo.py"])
    captured = capsys.readouterr()

    assert return_code == 0
    assert stdout_text == "step one\nstep two\n"
    assert stderr_text == "warning line\n"
    assert "step one" in captured.out
    assert "step two" in captured.out
    assert "warning line" in captured.err
