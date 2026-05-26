import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COUNT_BED = ROOT / "skills" / "analyze_job_results" / "scripts" / "count_bed.py"


def _load_count_bed_module():
    spec = importlib.util.spec_from_file_location("count_bed_script", COUNT_BED)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_script(args):
    return subprocess.run(
        [sys.executable, str(COUNT_BED), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_bed(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_bed_gz(path: Path, lines: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def test_parse_bed_metadata_supports_bed_gz_suffix():
    module = _load_count_bed_module()

    metadata = module._parse_bed_metadata("igvfr_698-04.mm39.plus.m6A.filtered.bed.gz")

    assert metadata["sample"] == "igvfr_698-04"
    assert metadata["genome"] == "mm39"
    assert metadata["strand"] == "plus"
    assert metadata["modification"] == "m6A"
    assert metadata["file_name"] == "igvfr_698-04.mm39.plus.m6A.filtered.bed.gz"


def test_count_bed_script_reads_plain_and_gzipped_bed_files(tmp_path: Path):
    plus_bed = tmp_path / "igvfr_698-04.mm39.plus.m6A.filtered.bed"
    minus_bed_gz = tmp_path / "igvfr_698-04.mm39.minus.m6A.filtered.bed.gz"

    _write_bed(
        plus_bed,
        [
            "track name=test",
            "chr1\t10\t20",
            "chr2\t30\t40",
        ],
    )
    _write_bed_gz(
        minus_bed_gz,
        [
            "browser position chr1",
            "chr1\t50\t60",
        ],
    )

    result = _run_script(["--json", str(plus_bed), str(minus_bed_gz)])

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == [
        {
            "Sample": "igvfr_698-04",
            "Genome": "mm39",
            "Modification": "m6A",
            "Chromosome": "chr1",
            "Count": 2,
        },
        {
            "Sample": "igvfr_698-04",
            "Genome": "mm39",
            "Modification": "m6A",
            "Chromosome": "chr2",
            "Count": 1,
        },
    ]
    assert payload["row_count"] == 2
    assert payload["metadata"]["input_files"] == [
        {
            "sample": "igvfr_698-04",
            "genome": "mm39",
            "strand": "plus",
            "modification": "m6A",
            "file_name": "igvfr_698-04.mm39.plus.m6A.filtered.bed",
            "file_path": str(plus_bed),
        },
        {
            "sample": "igvfr_698-04",
            "genome": "mm39",
            "strand": "minus",
            "modification": "m6A",
            "file_name": "igvfr_698-04.mm39.minus.m6A.filtered.bed.gz",
            "file_path": str(minus_bed_gz),
        },
    ]