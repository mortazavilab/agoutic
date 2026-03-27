import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "skills" / "reconcile_bams" / "scripts" / "check_workflow_references.py"
RECONCILE = ROOT / "skills" / "reconcile_bams" / "scripts" / "reconcile_bams.py"


def _run_script(script: Path, args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


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
    gtf.write_text("chr1\tstub\n", encoding="utf-8")

    bam = tmp_path / "sample1.GRCh38.annotated.bam"
    bam.write_text("x", encoding="utf-8")

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

    workflow_dir = Path(payload["workflow"]["directory"])
    assert workflow_dir.name.startswith("workflow_reconcile_")
    assert (workflow_dir / "input").is_dir()

    links = payload["workflow"]["symlinks"]
    assert len(links) == 1
    link_path = Path(links[0]["link"])
    assert link_path.is_symlink()
    assert link_path.resolve() == bam.resolve()

    artifacts = payload["outputs"]["artifacts"]
    reconciled = [a for a in artifacts if a.get("type") == "reconciled_bam"]
    assert len(reconciled) == 1
    assert Path(reconciled[0]["path"]).is_file()


def test_reconcile_script_defaults_output_root_to_workflow_parent_when_omitted(tmp_path: Path):
    project_root = tmp_path / "project"
    wf2 = project_root / "workflow2" / "annot"
    wf3 = project_root / "workflow3" / "annot"
    wf2.mkdir(parents=True)
    wf3.mkdir(parents=True)

    bam1 = wf2 / "sample1.GRCh38.annotated.bam"
    bam2 = wf3 / "sample2.GRCh38.annotated.bam"
    bam1.write_text("x", encoding="utf-8")
    bam2.write_text("y", encoding="utf-8")

    data_root = tmp_path / "data"
    gtf = data_root / "references" / "GRCh38" / "gencode.v29.primary_assembly.annotation_UCSC_names.gtf"
    gtf.parent.mkdir(parents=True)
    gtf.write_text("chr1\tstub\n", encoding="utf-8")

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
    assert payload["outputs"]["output_root"] == str(project_root.resolve())
    assert Path(payload["workflow"]["directory"]).parent == project_root.resolve()


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
    bam.write_text("x", encoding="utf-8")
    manual_gtf = tmp_path / "manual.GRCh38.annotation.gtf"
    manual_gtf.write_text("chr1\tmanual\n", encoding="utf-8")

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
