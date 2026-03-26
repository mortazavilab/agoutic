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
