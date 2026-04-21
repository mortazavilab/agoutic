import argparse
import csv
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analyzer.analysis_engine import compare_bed_region_overlaps  # noqa: E402


FILE_NAME_BY_KIND = {
    "shared_bed_overlap_components": "shared_overlap_components.csv",
    "bed_only_components_a": "sample_a_only_overlap_components.csv",
    "bed_only_components_b": "sample_b_only_overlap_components.csv",
    "bed_overlap_summary": "overlap_combination_counts.csv",
    "overlap_membership": "overlap_membership_components.csv",
}


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def _resolve_output_name(label: str, payload: dict, *, sample_a_label: str, sample_b_label: str) -> str:
    metadata = payload.get("metadata") or {}
    kind = str(metadata.get("kind") or "").strip().lower()
    sample_label = str(metadata.get("sample_label") or "").strip()

    if kind == "bed_only_components":
        if sample_label == sample_a_label:
            return FILE_NAME_BY_KIND["bed_only_components_a"]
        if sample_label == sample_b_label:
            return FILE_NAME_BY_KIND["bed_only_components_b"]
    if kind in FILE_NAME_BY_KIND:
        return FILE_NAME_BY_KIND[kind]

    slug = "_".join(
        chunk for chunk in "".join(ch if ch.isalnum() else "_" for ch in label).strip("_").lower().split("_") if chunk
    )
    return f"{slug or 'overlap_results'}.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build connected BED overlap CSV outputs for workflow plotting.")
    parser.add_argument("--bed-a-path")
    parser.add_argument("--bed-b-path")
    parser.add_argument("--folder-a")
    parser.add_argument("--folder-b")
    parser.add_argument("--pattern-a")
    parser.add_argument("--pattern-b")
    parser.add_argument("--sample-a-label")
    parser.add_argument("--sample-b-label")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--min-overlap-bp", type=int, default=1)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    result = compare_bed_region_overlaps(
        bed_a_path=args.bed_a_path,
        bed_b_path=args.bed_b_path,
        folder_a=args.folder_a,
        folder_b=args.folder_b,
        pattern_a=args.pattern_a,
        pattern_b=args.pattern_b,
        sample_a_label=args.sample_a_label,
        sample_b_label=args.sample_b_label,
        min_overlap_bp=args.min_overlap_bp,
        export_dir=str(output_dir / "bed_exports"),
    )

    bundle = result.get("_dataframes") or {}
    sample_a = ((result.get("sample_a") or {}).get("label") or args.sample_a_label or "Sample A").strip()
    sample_b = ((result.get("sample_b") or {}).get("label") or args.sample_b_label or "Sample B").strip()
    generated_files: dict[str, str] = {}

    for label, payload in bundle.items():
        if not isinstance(payload, dict):
            continue
        columns = payload.get("columns")
        rows = payload.get("data")
        if not isinstance(columns, list) or not isinstance(rows, list):
            continue
        filename = _resolve_output_name(label, payload, sample_a_label=sample_a, sample_b_label=sample_b)
        file_path = output_dir / filename
        _write_csv(file_path, columns, rows)
        generated_files[label] = str(file_path)

    manifest = {
        "comparison_label": result.get("comparison_label"),
        "sample_a": result.get("sample_a"),
        "sample_b": result.get("sample_b"),
        "counts": result.get("counts"),
        "output_dir": str(output_dir),
        "generated_csv_files": generated_files,
        "exported_files": result.get("exported_files"),
    }
    manifest_path = output_dir / "overlap_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())