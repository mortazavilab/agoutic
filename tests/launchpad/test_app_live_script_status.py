from pathlib import Path
from types import SimpleNamespace
import json

import launchpad.app as launchpad_app


def _write_log(path: Path, lines: list[str]) -> str:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_build_live_script_status_infers_haplotype_progress(tmp_path):
    stderr_path = tmp_path / "haplotype.stderr.log"
    stdout_path = tmp_path / "haplotype.stdout.log"
    _write_log(stdout_path, [])
    _write_log(
        stderr_path,
        [
            "HAPLOTYPE_PROGRESS\tBAM_START\tbam=sample1.annotated.bam\tbam_index=1\ttotal_bams=2",
            "HAPLOTYPE_PROGRESS\tCHROM_START\tbam=sample1.annotated.bam\tchrom=chr1\tbam_index=1\ttotal_bams=2\tchrom_index=1\ttotal_chroms=12\tinformative_variants=5421",
            "HAPLOTYPE_PROGRESS\tCHROM_PROGRESS\tbam=sample1.annotated.bam\tchrom=chr1\treads=200000\tassigned_a=121000\tassigned_b=71000\tambiguous=8000\tinformative_sites=250000",
        ],
    )

    job = SimpleNamespace(
        log_file=str(stdout_path),
        stderr_log=str(stderr_path),
        report_json=json.dumps({"script_id": "haplotype_with_vcf/haplotype_with_vcf"}),
    )

    status = launchpad_app._build_live_script_status(job)

    assert status["current_step"] == "Assigning reads"
    assert status["current_step_detail"] == "sample1.annotated.bam: chr1 processed 200000 reads"
    assert 10 <= status["progress_percent"] <= 93
    assert status["message"] == status["current_step_detail"]


def test_build_live_script_status_marks_haplotype_complete(tmp_path):
    stderr_path = tmp_path / "haplotype-complete.stderr.log"
    stdout_path = tmp_path / "haplotype-complete.stdout.log"
    _write_log(stdout_path, [])
    _write_log(
        stderr_path,
        [
            "HAPLOTYPE_PROGRESS\tCOMPLETE\ttotal_bams=3\tworkflow=/proj/workflow9",
        ],
    )

    job = SimpleNamespace(
        log_file=str(stdout_path),
        stderr_log=str(stderr_path),
        report_json=json.dumps({"script_path": "/Users/alim/vscode/agoutic/skills/haplotype_with_vcf/scripts/haplotype_with_vcf.py"}),
    )

    status = launchpad_app._build_live_script_status(job)

    assert status["current_step"] == "Haplotype complete"
    assert status["current_step_detail"] == "Finished 3 BAMs"
    assert status["progress_percent"] == 100