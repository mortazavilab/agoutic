#!/usr/bin/env python3
"""Reconcile annotated BAM inputs with strict preflight validation and execution.

This script performs:
- BAM discovery and validation
- shared-reference and shared-annotation enforcement
- workflow-config/manual annotation GTF selection
- workflowN directory creation with symlinked inputs
- preflight-only validation or full reconcileBams.py execution
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

# Ensure repository-local packages (e.g., launchpad) are importable when this
# script is executed directly from skills/reconcile_bams/scripts.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from launchpad.config import REFERENCE_GENOMES
from common.workflow_paths import next_workflow_number, workflow_dir_name


_BAM_PATTERN = re.compile(r"^(?P<sample>.+)\.(?P<reference>[^.]+)\.annotated\.bam$")
_INCLUDE_PATTERN = re.compile(r"includeConfig\s+['\"](?P<path>[^'\"]+)['\"]")
_GTF_PATH_PATTERN = re.compile(r"['\"](?P<path>[^'\"]+\.gtf(?:\.gz)?)['\"]", re.IGNORECASE)


def _bounded_reconcile_threads(raw_value: int | None = None) -> int:
    """Return a safe thread count, clamped to env-configurable bounds."""
    default = int(os.environ.get("RECONCILE_BAMS_DEFAULT_THREADS", "4"))
    cap = int(os.environ.get("RECONCILE_BAMS_MAX_THREADS", "8"))
    value = raw_value if raw_value is not None else default
    return max(1, min(value, cap))


class ReconcileInputError(Exception):
    pass


class ReconcileExecutionError(Exception):
    pass


def _discover_workflow_dirs(project_dir: Path, explicit_workflow_dirs: list[str]) -> list[Path]:
    if explicit_workflow_dirs:
        return [Path(item).expanduser().resolve() for item in explicit_workflow_dirs]
    if not project_dir.is_dir():
        return []
    return sorted(path for path in project_dir.iterdir() if path.is_dir() and path.name.startswith("workflow"))


def _validate_explicit_workflow_dirs(workflow_dirs: list[Path]) -> None:
    missing = [path for path in workflow_dirs if not path.is_dir()]
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise ReconcileInputError(f"Requested workflow directories do not exist: {joined}")


def _discover_bams(workflow_dirs: list[Path]) -> list[Path]:
    bams: list[Path] = []
    seen: set[Path] = set()
    for workflow_dir in workflow_dirs:
        annot_dir = workflow_dir / "annot"
        if not annot_dir.is_dir():
            continue
        for bam in sorted(annot_dir.glob("*.annotated.bam")):
            resolved = bam.expanduser().resolve()
            if resolved not in seen:
                seen.add(resolved)
                bams.append(resolved)
    return bams


def _parse_bam_metadata(path: Path) -> dict:
    match = _BAM_PATTERN.match(path.name)
    if not match:
        raise ReconcileInputError(
            f"Invalid BAM filename '{path.name}'. Expected '<sample>.<reference>.annotated.bam'."
        )
    sample = match.group("sample").strip()
    reference = match.group("reference").strip()
    if not sample or not reference:
        raise ReconcileInputError(
            f"Invalid BAM filename '{path.name}'. Sample and reference tokens must be non-empty."
        )
    return {
        "path": str(path),
        "sample": sample,
        "reference": reference,
    }


def _canonical_reference(reference_token: str) -> str:
    lowered = reference_token.lower()
    if lowered in {"grch38", "hg38", "human"}:
        return "GRCh38"
    if lowered in {"mm39", "mm10", "mouse"}:
        return "mm39"
    return reference_token


def _resolve_default_gtf(reference: str) -> Path | None:
    entry = REFERENCE_GENOMES.get(reference)
    if not isinstance(entry, dict):
        return None
    gtf = entry.get("gtf")
    if not gtf:
        return None
    gtf_path = Path(str(gtf)).expanduser().resolve()
    return gtf_path if gtf_path.exists() else None


def _normalized_gtf_name(path_like: str | Path) -> str:
    name = Path(str(path_like)).name.lower()
    if name.endswith(".gz"):
        name = name[:-3]
    return name


def _resolve_local_gtf_equivalent(raw_path: str | Path, reference: str) -> Path | None:
    """Map a remote config/manual GTF to the local reference copy when safe.

    Reconcile preflight runs locally, but workflow configs may point at the
    remote Watson reference tree (for example `/share/.../GRCh38/...gtf`).
    When that remote path names the same canonical GTF file as the local
    reference catalog, use the local copy instead of forcing manual approval.
    """
    default_gtf = _resolve_default_gtf(reference)
    if default_gtf is None:
        return None
    if _normalized_gtf_name(raw_path) == _normalized_gtf_name(default_gtf):
        return default_gtf
    return None


def _manual_gtf_matches_reference(manual_gtf: Path, reference: str) -> bool:
    manual_lower = str(manual_gtf).lower()
    ref_lower = reference.lower()
    if ref_lower in manual_lower:
        return True
    default_gtf = _resolve_default_gtf(reference)
    if default_gtf and default_gtf == manual_gtf:
        return True
    return False


def _next_workflow_number(project_dir: Path) -> int:
    return next_workflow_number(project_dir)


def _ensure_reconcile_workflow_dir(output_root: Path) -> Path:
    workflow_dir = output_root / workflow_dir_name(_next_workflow_number(output_root))
    input_dir = workflow_dir / "input"
    output_dir = workflow_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    return workflow_dir


def _collect_config_files(workflow_dir: Path) -> list[Path]:
    queue = [workflow_dir / "nextflow.config"]
    conf_dir = workflow_dir / "conf"
    if conf_dir.is_dir():
        queue.extend(sorted(conf_dir.glob("*.config")))

    visited: set[Path] = set()
    ordered: list[Path] = []
    while queue:
        path = Path(queue.pop(0)).resolve()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        ordered.append(path)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            include_match = _INCLUDE_PATTERN.search(raw_line)
            if not include_match:
                continue
            include_path = (path.parent / include_match.group("path")).resolve()
            if include_path not in visited:
                queue.append(include_path)
    return ordered


def _resolve_gtf_candidate(raw_path: str, base_dir: Path, reference: str) -> Path | None:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if candidate.exists() and candidate.is_file():
        return candidate
    return _resolve_local_gtf_equivalent(raw_path, reference)


def _discover_workflow_annotation_gtf(workflow_dirs: list[Path], reference: str) -> tuple[Path | None, list[dict], str | None]:
    discovered: list[tuple[str, Path]] = []
    evidence: list[dict] = []

    for workflow_dir in workflow_dirs:
        workflow_candidates: set[Path] = set()
        for config_path in _collect_config_files(workflow_dir):
            try:
                lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, raw_line in enumerate(lines, start=1):
                if ".gtf" not in raw_line.lower():
                    continue
                for match in _GTF_PATH_PATTERN.finditer(raw_line):
                    candidate = _resolve_gtf_candidate(match.group("path"), config_path.parent, reference)
                    if candidate is None:
                        continue
                    workflow_candidates.add(candidate)
                    evidence.append(
                        {
                            "workflow": str(workflow_dir.resolve()),
                            "file": str(config_path),
                            "line": line_no,
                            "configured_annotation_gtf": match.group("path"),
                            "annotation_gtf": str(candidate),
                            "source": "workflow_config",
                        }
                    )
        if len(workflow_candidates) > 1:
            return None, evidence, (
                f"Workflow {workflow_dir.resolve()} references multiple annotation GTF files: "
                f"{', '.join(str(path) for path in sorted(workflow_candidates))}"
            )
        if workflow_candidates:
            discovered.append((str(workflow_dir.resolve()), next(iter(workflow_candidates))))

    if not discovered:
        return None, evidence, None

    unique_paths = {path for _workflow, path in discovered}
    if len(unique_paths) > 1:
        return None, evidence, (
            "Selected workflows do not share one annotation GTF: "
            + ", ".join(str(path) for path in sorted(unique_paths))
        )

    selected = next(iter(unique_paths))
    if not _manual_gtf_matches_reference(selected, reference):
        return None, evidence, (
            f"Workflow config annotation GTF appears to mismatch BAM reference '{reference}': {selected}"
        )
    return selected, evidence, None


def _resolve_output_root(project_dir: Path, workflow_dirs: list[Path], requested_output_dir: str) -> Path:
    requested = (requested_output_dir or ".").strip()
    if requested and requested != ".":
        return Path(requested).expanduser().resolve()

    if workflow_dirs:
        workflow_parents = [str(path.parent) for path in workflow_dirs]
        return Path(os.path.commonpath(workflow_parents)).expanduser().resolve()

    script_dir = Path(__file__).resolve().parent
    if project_dir.is_dir() and project_dir != script_dir:
        return project_dir

    cwd = Path.cwd().resolve()
    if cwd != script_dir:
        return cwd

    raise ReconcileInputError(
        "Unable to determine a writable reconcile output directory automatically. "
        "Provide --output-dir explicitly."
    )


def _create_symlinks(workflow_dir: Path, bam_paths: list[Path]) -> list[dict]:
    input_dir = workflow_dir / "input"
    links: list[dict] = []
    seen: set[Path] = set()
    used_names: set[str] = set()
    for index, bam_path in enumerate(bam_paths, start=1):
        resolved = bam_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)

        link_name = bam_path.name
        if link_name in used_names:
            workflow_label = bam_path.parent.parent.name if bam_path.parent.name == "annot" else bam_path.parent.name
            link_name = f"{workflow_label}_{index:03d}_{bam_path.name}"
        used_names.add(link_name)
        link_path = input_dir / link_name
        link_path.symlink_to(resolved)
        links.append({"source": str(resolved), "link": str(link_path)})
    return links


def _build_error_payload(message: str) -> dict:
    return {
        "success": False,
        "status": "error",
        "error": message,
    }


def _resolve_selected_gtf(
    reference: str,
    annotation_gtf: str | None,
    workflow_annotation_gtf: Path | None,
    *,
    require_workflow_gtf: bool,
) -> tuple[Path | None, str, str | None]:
    """Return (selected_gtf, source, resolution_issue)."""
    if annotation_gtf:
        manual_gtf = Path(annotation_gtf).expanduser().resolve()
        if (not manual_gtf.exists() or not manual_gtf.is_file()) and annotation_gtf:
            mapped_gtf = _resolve_local_gtf_equivalent(annotation_gtf, reference)
            if mapped_gtf is not None:
                manual_gtf = mapped_gtf
        if not manual_gtf.exists() or not manual_gtf.is_file():
            return None, "manual", f"Manual annotation GTF does not exist or is not a file: {manual_gtf}"
        if not _manual_gtf_matches_reference(manual_gtf, reference):
            return (
                None,
                "manual",
                f"Manual annotation GTF appears to mismatch BAM reference '{reference}': {manual_gtf}",
            )
        return manual_gtf, "manual", None

    if workflow_annotation_gtf is not None:
        return workflow_annotation_gtf, "workflow_config", None

    if require_workflow_gtf:
        return None, "manual", (
            "Could not resolve the annotation GTF from the selected workflow configs. "
            "Provide an explicit annotation GTF before approval/execution can continue."
        )

    default_gtf = _resolve_default_gtf(reference)
    if default_gtf is None:
        return None, "manual", f"No default GTF is available for reference '{reference}'."

    return default_gtf, "default", None


def _build_reconcile_command(
    *,
    script_path: Path,
    bam_paths: list[Path],
    annotation_gtf: Path,
    output_prefix: str,
    output_dir: Path,
    gene_prefix: str,
    tx_prefix: str,
    id_tag: str,
    gene_tag: str,
    threads: int,
    exon_merge_distance: int,
    min_tpm: float,
    min_samples: int,
    filter_known: bool,
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(script_path),
        "--bams",
        *[str(path) for path in bam_paths],
        "--annotation",
        str(annotation_gtf),
        "--out_prefix",
        output_prefix,
        "--outdir",
        str(output_dir),
        "--gene_prefix",
        gene_prefix,
        "--tx_prefix",
        tx_prefix,
        "--id_tag",
        id_tag,
        "--gene_tag",
        gene_tag,
        "--threads",
        str(threads),
        "--exon_merge_distance",
        str(exon_merge_distance),
        "--min_tpm",
        str(min_tpm),
        "--min_samples",
        str(min_samples),
    ]
    if filter_known:
        command.append("--filter_known")
    return command


def _run_reconcile_command(
    command: list[str],
    *,
    stdout_sink=None,
    stderr_sink=None,
) -> tuple[int, str, str]:
    if stdout_sink is None:
        stdout_sink = sys.stdout
    if stderr_sink is None:
        stderr_sink = sys.stderr

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _forward_stream(stream, sink, chunks: list[str]) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
                if sink is not None:
                    sink.write(line)
                    sink.flush()
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=_forward_stream,
        args=(process.stdout, stdout_sink, stdout_chunks),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_forward_stream,
        args=(process.stderr, stderr_sink, stderr_chunks),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return return_code, "".join(stdout_chunks), "".join(stderr_chunks)


def _build_manual_gtf_needed_payload(
    *,
    reference: str,
    metadata: list[dict],
    reason: str,
    output_prefix: str,
    output_root: Path,
    annotation_evidence: list[dict] | None = None,
) -> dict:
    return {
        "success": True,
        "status": "needs_manual_gtf",
        "message": "Manual annotation GTF is required before approval/execution can continue.",
        "reference": reference,
        "inputs": {
            "count": len(metadata),
            "bams": metadata,
        },
        "required_input": {
            "field": "annotation_gtf",
            "description": "Provide an absolute path to a GTF file matching the BAM reference.",
            "reason": reason,
        },
        "annotation_evidence": annotation_evidence or [],
        "outputs": {
            "output_prefix": output_prefix,
            "output_root": str(output_root),
            "artifacts": [],
        },
    }


def _write_inputs_manifest(workflow_dir: Path, metadata: list[dict], symlinks: list[dict], output_prefix: str) -> Path:
    output_dir = workflow_dir / "output"
    manifest_path = output_dir / f"{output_prefix}.inputs.tsv"
    source_to_link = {item["source"]: item["link"] for item in symlinks}
    lines = ["sample\treference\tsource_bam\tsymlink_bam"]
    for item in metadata:
        source = str(Path(item["path"]).resolve())
        lines.append(
            "\t".join(
                [
                    str(item["sample"]),
                    str(item["reference"]),
                    source,
                    source_to_link.get(source, ""),
                ]
            )
        )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile annotated BAM files across workflows.")
    parser.add_argument("--project-dir", default=".", help="Project directory containing workflow* folders.")
    parser.add_argument("--workflow-dir", action="append", default=[], help="Explicit workflow directory (repeatable).")
    parser.add_argument("--input-bam", action="append", default=[], help="Annotated BAM input path (repeatable).")
    parser.add_argument("--output-prefix", default="reconciled", help="Output prefix for generated artifacts.")
    parser.add_argument("--output-dir", default=".", help="Parent directory where reconcile workflow directory is created.")
    parser.add_argument("--annotation-gtf", help="Manual annotation GTF path. Only required when default resolution fails.")
    parser.add_argument("--gene-prefix", "--gene_prefix", dest="gene_prefix", default="CONSG", help="Consolidated novel gene ID prefix.")
    parser.add_argument("--tx-prefix", "--tx_prefix", dest="tx_prefix", default="CONST", help="Consolidated novel transcript ID prefix.")
    parser.add_argument("--id-tag", "--id_tag", dest="id_tag", default="TX", help="BAM tag used for transcript IDs.")
    parser.add_argument("--gene-tag", "--gene_tag", dest="gene_tag", default="GX", help="BAM tag used for gene IDs.")
    parser.add_argument("--threads", type=int, default=_bounded_reconcile_threads(), help="Number of worker threads for reconcileBams.py (capped by RECONCILE_BAMS_MAX_THREADS).")
    parser.add_argument("--exon-merge-distance", "--exon_merge_distance", dest="exon_merge_distance", type=int, default=5, help="Merge exons separated by at most this many bases.")
    parser.add_argument("--min-tpm", "--min_tpm", dest="min_tpm", type=float, default=1.0, help="Minimum TPM required in a sample.")
    parser.add_argument("--min-samples", "--min_samples", dest="min_samples", type=int, default=2, help="Minimum number of samples that must meet min_tpm.")
    parser.add_argument("--filter-known", "--filter_known", dest="filter_known", action="store_true", help="Apply abundance filtering to known transcripts as well.")
    parser.add_argument("--preflight-only", action="store_true", help="Run validation only and stop before execution.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")

    args = parser.parse_args()
    args.threads = _bounded_reconcile_threads(args.threads)

    try:
        project_dir = Path(args.project_dir).expanduser().resolve()
        workflow_dirs = _discover_workflow_dirs(project_dir, list(args.workflow_dir or []))
        if args.workflow_dir:
            _validate_explicit_workflow_dirs(workflow_dirs)
        output_root = _resolve_output_root(project_dir, workflow_dirs, args.output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        input_bams: list[Path] = []
        for item in args.input_bam:
            bam_path = Path(item).expanduser().resolve()
            if not bam_path.exists() or not bam_path.is_file():
                raise ReconcileInputError(f"Input BAM does not exist or is not a file: {bam_path}")
            input_bams.append(bam_path)

        if not input_bams:
            input_bams = _discover_bams(workflow_dirs)

        if not input_bams:
            raise ReconcileInputError("No annotated BAM inputs found. Provide --input-bam or discoverable workflow annot outputs.")

        metadata = [_parse_bam_metadata(path) for path in input_bams]
        references = {_canonical_reference(item["reference"]) for item in metadata}
        if len(references) != 1:
            raise ReconcileInputError(f"Mixed BAM references detected: {sorted(references)}")
        reference = next(iter(references))

        workflow_gtf, annotation_evidence, workflow_gtf_issue = _discover_workflow_annotation_gtf(workflow_dirs, reference)
        if workflow_gtf_issue:
            raise ReconcileInputError(workflow_gtf_issue)

        selected_gtf, gtf_source, gtf_issue = _resolve_selected_gtf(
            reference,
            args.annotation_gtf,
            workflow_gtf,
            require_workflow_gtf=bool(workflow_dirs),
        )
        if selected_gtf is None:
            payload = _build_manual_gtf_needed_payload(
                reference=reference,
                metadata=metadata,
                reason=gtf_issue or "No usable GTF could be resolved.",
                output_prefix=args.output_prefix,
                output_root=output_root,
                annotation_evidence=annotation_evidence,
            )
        elif args.preflight_only:
            payload = {
                "success": True,
                "status": "preflight_ready",
                "message": "Reconcile preflight validation passed. Ready for approval.",
                "reference": reference,
                "gtf": {
                    "path": str(selected_gtf),
                    "source": gtf_source,
                },
                "annotation_evidence": annotation_evidence,
                "inputs": {
                    "count": len(metadata),
                    "bams": metadata,
                },
                "execution_defaults": {
                    "script_id": "reconcile_bams/reconcile_bams",
                    "underlying_script_id": "reconcile_bams/reconcileBams",
                    "gene_prefix": args.gene_prefix,
                    "tx_prefix": args.tx_prefix,
                    "id_tag": args.id_tag,
                    "gene_tag": args.gene_tag,
                    "threads": int(args.threads),
                    "exon_merge_distance": int(args.exon_merge_distance),
                    "min_tpm": float(args.min_tpm),
                    "min_samples": int(args.min_samples),
                    "filter_known": bool(args.filter_known),
                },
                "outputs": {
                    "output_prefix": args.output_prefix,
                    "output_root": str(output_root),
                    "artifacts": [],
                },
            }
        else:
            workflow_dir = _ensure_reconcile_workflow_dir(output_root)
            symlinks = _create_symlinks(workflow_dir, [Path(item["path"]) for item in metadata])

            manifest_path = _write_inputs_manifest(workflow_dir, metadata, symlinks, args.output_prefix)
            input_link_paths = [Path(item["link"]) for item in symlinks]
            output_dir = workflow_dir
            reconcile_script = Path(__file__).resolve().with_name("reconcileBams.py")
            command = _build_reconcile_command(
                script_path=reconcile_script,
                bam_paths=input_link_paths,
                annotation_gtf=selected_gtf,
                output_prefix=args.output_prefix,
                output_dir=output_dir,
                gene_prefix=args.gene_prefix,
                tx_prefix=args.tx_prefix,
                id_tag=args.id_tag,
                gene_tag=args.gene_tag,
                threads=int(args.threads),
                exon_merge_distance=int(args.exon_merge_distance),
                min_tpm=float(args.min_tpm),
                min_samples=int(args.min_samples),
                filter_known=bool(args.filter_known),
            )
            return_code, stdout_text, stderr_text = _run_reconcile_command(
                command,
                stdout_sink=sys.stderr if args.json else sys.stdout,
                stderr_sink=sys.stderr,
            )
            if return_code != 0:
                raise ReconcileExecutionError(
                    stderr_text.strip()
                    or stdout_text.strip()
                    or f"reconcileBams.py exited with code {return_code}"
                )

            artifacts = [
                {
                    "type": "inputs_manifest",
                    "path": str(manifest_path),
                },
            ]
            for output_path in sorted(output_dir.iterdir()):
                if not output_path.is_file():
                    continue
                if output_path == manifest_path:
                    continue
                artifact_type = "output_file"
                if output_path.suffix == ".bam":
                    artifact_type = "reconciled_bam"
                elif output_path.suffix == ".bai":
                    artifact_type = "bam_index"
                elif output_path.suffix == ".gtf":
                    artifact_type = "annotation_gtf"
                elif output_path.suffix == ".tsv":
                    artifact_type = "tsv"
                elif output_path.suffix == ".txt":
                    artifact_type = "report"
                artifacts.append({"type": artifact_type, "path": str(output_path)})

            payload = {
                "success": True,
                "status": "completed",
                "message": "Reconcile execution completed by running reconcileBams.py with workflow-scoped symlinked inputs.",
                "reference": reference,
                "gtf": {
                    "path": str(selected_gtf),
                    "source": gtf_source,
                },
                "annotation_evidence": annotation_evidence,
                "inputs": {
                    "count": len(metadata),
                    "bams": metadata,
                },
                "workflow": {
                    "directory": str(workflow_dir),
                    "input_directory": str(workflow_dir / "input"),
                    "output_directory": str(workflow_dir),
                    "symlinks": symlinks,
                },
                "execution": {
                    "script_id": "reconcile_bams/reconcileBams",
                    "script_path": str(reconcile_script),
                    "command": command,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                },
                "outputs": {
                    "output_prefix": args.output_prefix,
                    "output_root": str(output_root),
                    "artifacts": artifacts,
                },
            }
    except ReconcileInputError as exc:
        payload = _build_error_payload(str(exc))
    except ReconcileExecutionError as exc:
        payload = _build_error_payload(str(exc))

    if payload.get("success") is False:
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"ERROR: {payload['error']}")
        return 1

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(payload["message"])
        if payload.get("reference"):
            print(f"reference={payload['reference']}")
        if isinstance(payload.get("gtf"), dict):
            print(f"gtf_source={payload['gtf']['source']}")
            print(f"gtf_path={payload['gtf']['path']}")
        if isinstance(payload.get("workflow"), dict):
            print(f"workflow_dir={payload['workflow']['directory']}")
            print(f"symlink_count={len(payload['workflow']['symlinks'])}")
        print(f"status={payload['status']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
