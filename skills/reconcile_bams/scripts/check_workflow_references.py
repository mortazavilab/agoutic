#!/usr/bin/env python3
"""Check that source workflows resolve to a single reference genome.

This script scans Nextflow config artifacts in one or more workflow directories,
extracts reference-like assignments, and reports whether all workflows agree.

Exit codes:
- 0: consistent reference resolution
- 1: mixed/missing/ambiguous resolution
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REFERENCE_ALIASES = {
    "hg38": "GRCh38",
    "grch38": "GRCh38",
    "human": "GRCh38",
    "mm39": "mm39",
    "mm10": "mm39",
    "mouse": "mm39",
}

REFERENCE_KEYS = {
    "reference",
    "reference_genome",
    "genome_ref",
    "genome",
    "genome_build",
    "genome_annot_ref",
    "genome_annot_refs",
}

ASSIGNMENT_PATTERNS = [
    re.compile(r"^\s*params\.(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?)\s*$"),
    re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?)\s*$"),
]

INCLUDE_PATTERN = re.compile(r"includeConfig\s+['\"](?P<path>[^'\"]+)['\"]")
TOKEN_PATTERN = re.compile(r"\b(GRCh38|hg38|grch38|mm39|mm10|human|mouse)\b", re.IGNORECASE)
ANNOTATED_BAM_PATTERN = re.compile(r"^(?P<sample>.+)\.(?P<reference>[^.]+)\.annotated\.bam$")


def _normalize_reference(raw: str) -> str | None:
    key = raw.strip().strip("\"'").lower()
    if not key:
        return None
    return REFERENCE_ALIASES.get(key) or ("GRCh38" if key == "grch38" else None)


def _extract_reference_tokens(value: str) -> list[str]:
    refs: list[str] = []
    for match in TOKEN_PATTERN.finditer(value or ""):
        normalized = _normalize_reference(match.group(1))
        if normalized:
            refs.append(normalized)
    return refs


def _clean_value(raw_value: str) -> str:
    # Trim inline comments and surrounding quotes.
    value = raw_value.split("//", 1)[0].strip()
    return value.strip("\"'")


def _parse_config_file(config_path: Path) -> tuple[list[dict], list[Path]]:
    findings: list[dict] = []
    includes: list[Path] = []

    try:
        lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return findings, includes

    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue

        include_match = INCLUDE_PATTERN.search(raw_line)
        if include_match:
            include_rel = include_match.group("path")
            include_path = (config_path.parent / include_rel).resolve()
            includes.append(include_path)

        for pattern in ASSIGNMENT_PATTERNS:
            match = pattern.match(raw_line)
            if not match:
                continue

            key = match.group("key")
            if key.lower() not in REFERENCE_KEYS:
                continue

            value = _clean_value(match.group("value"))
            refs = _extract_reference_tokens(value)
            findings.append(
                {
                    "file": str(config_path),
                    "line": index,
                    "key": key,
                    "value": value,
                    "references": refs,
                }
            )
            break

    return findings, includes


def _extract_references_from_config_tokens(config_paths: list[Path]) -> tuple[list[str], list[dict]]:
    references: set[str] = set()
    evidence: list[dict] = []

    for config_path in config_paths:
        try:
            lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for index, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue

            refs = _extract_reference_tokens(raw_line)
            if not refs:
                continue

            for ref in refs:
                references.add(ref)
            evidence.append(
                {
                    "file": str(config_path),
                    "line": index,
                    "content": line,
                    "references": sorted(set(refs)),
                    "source": "config_token_fallback",
                }
            )

    return sorted(references), evidence


def _collect_configs(workflow_dir: Path) -> list[Path]:
    candidates = [workflow_dir / "nextflow.config"]
    conf_dir = workflow_dir / "conf"
    if conf_dir.is_dir():
        candidates.extend(sorted(conf_dir.glob("*.config")))
    return [path.resolve() for path in candidates if path.is_file()]


def _extract_references_from_annotated_bams(workflow_dir: Path) -> tuple[list[str], list[dict]]:
    annot_dir = workflow_dir / "annot"
    if not annot_dir.is_dir():
        return [], []

    references: set[str] = set()
    evidence: list[dict] = []
    for bam_path in sorted(annot_dir.glob("*.annotated.bam")):
        match = ANNOTATED_BAM_PATTERN.match(bam_path.name)
        if not match:
            continue
        normalized = _normalize_reference(match.group("reference"))
        if not normalized:
            continue
        references.add(normalized)
        evidence.append(
            {
                "file": str(bam_path.resolve()),
                "sample": match.group("sample"),
                "reference": normalized,
                "source": "annotated_bam_filename",
            }
        )

    return sorted(references), evidence


def _resolve_workflow_references(workflow_dir: Path) -> dict:
    queue = _collect_configs(workflow_dir)
    visited: set[Path] = set()
    findings: list[dict] = []

    while queue:
        config = queue.pop(0)
        if config in visited:
            continue
        visited.add(config)

        file_findings, includes = _parse_config_file(config)
        findings.extend(file_findings)
        for include in includes:
            if include.is_file() and include not in visited:
                queue.append(include)

    resolved_refs: set[str] = set()
    for finding in findings:
        for ref in finding.get("references", []):
            resolved_refs.add(ref)

    config_token_refs: list[str] = []
    config_token_evidence: list[dict] = []
    if len(resolved_refs) == 0 and visited:
        config_token_refs, config_token_evidence = _extract_references_from_config_tokens(sorted(visited))
        resolved_refs.update(config_token_refs)

    fallback_refs: list[str] = []
    fallback_evidence: list[dict] = []
    if not findings and len(resolved_refs) == 0:
        fallback_refs, fallback_evidence = _extract_references_from_annotated_bams(workflow_dir)
        resolved_refs.update(fallback_refs)

    status = "ok"
    resolution_source = "config"
    if not findings:
        status = "missing"
    elif len(resolved_refs) == 0:
        status = "unresolved"
    elif len(resolved_refs) > 1:
        status = "ambiguous"

    if fallback_refs:
        if len(fallback_refs) == 1:
            status = "ok"
        elif len(fallback_refs) > 1:
            status = "ambiguous"
        resolution_source = "annotated_bam_filename"
    elif config_token_refs:
        if len(config_token_refs) == 1:
            status = "ok"
        elif len(config_token_refs) > 1:
            status = "ambiguous"
        resolution_source = "config_token_fallback"

    return {
        "workflow": str(workflow_dir.resolve()),
        "status": status,
        "references": sorted(resolved_refs),
        "findings": findings,
        "resolution_source": resolution_source,
        "config_token_evidence": config_token_evidence,
        "fallback_evidence": fallback_evidence,
    }


def _discover_workflows(project_dir: Path) -> list[Path]:
    if not project_dir.is_dir():
        return []
    return sorted(
        path for path in project_dir.iterdir() if path.is_dir() and path.name.startswith("workflow")
    )


def _build_summary(entries: list[dict], expected_reference: str | None) -> tuple[dict, int]:
    resolved = [entry for entry in entries if entry["status"] == "ok" and len(entry["references"]) == 1]
    unresolved = [entry for entry in entries if entry["status"] != "ok"]

    unique_refs = sorted({entry["references"][0] for entry in resolved})
    expected = _normalize_reference(expected_reference or "") if expected_reference else None

    errors: list[str] = []
    if unresolved:
        for entry in unresolved:
            errors.append(f"{entry['workflow']}: {entry['status']}")

    if len(unique_refs) > 1:
        errors.append(f"Mixed references detected: {', '.join(unique_refs)}")

    consensus = unique_refs[0] if len(unique_refs) == 1 else None
    if expected and consensus and expected != consensus:
        errors.append(f"Expected reference {expected} but resolved {consensus}")

    if expected and not consensus and not unresolved:
        consensus = expected

    summary = {
        "ok": len(errors) == 0,
        "consensus_reference": consensus,
        "expected_reference": expected,
        "workflows": entries,
        "errors": errors,
    }
    return summary, (0 if summary["ok"] else 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate reference consistency across workflow Nextflow configs.")
    parser.add_argument("--project-dir", help="Project directory containing workflow* folders.")
    parser.add_argument(
        "--workflow-dir",
        action="append",
        default=[],
        help="Explicit workflow directory (repeatable).",
    )
    parser.add_argument("--expected-reference", help="Trusted expected reference token (e.g. GRCh38, mm39).")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")

    args = parser.parse_args()

    workflows: list[Path] = [Path(p).expanduser().resolve() for p in args.workflow_dir]
    if not workflows and args.project_dir:
        workflows = _discover_workflows(Path(args.project_dir).expanduser().resolve())

    if not workflows:
        summary = {
            "ok": False,
            "consensus_reference": None,
            "workflows": [],
            "errors": ["No workflow directories were provided or discovered."],
        }
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print("ERROR: No workflow directories were provided or discovered.")
        return 1

    entries = [_resolve_workflow_references(workflow) for workflow in workflows]
    summary, code = _build_summary(entries, args.expected_reference)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        status = "OK" if summary["ok"] else "ERROR"
        print(f"{status}: consensus_reference={summary.get('consensus_reference')}")
        for item in summary["workflows"]:
            refs = ",".join(item.get("references", [])) or "<none>"
            print(f"- {item['workflow']}: status={item['status']} refs={refs}")
        if summary["errors"]:
            print("Errors:")
            for err in summary["errors"]:
                print(f"  - {err}")

    return code


if __name__ == "__main__":
    sys.exit(main())
