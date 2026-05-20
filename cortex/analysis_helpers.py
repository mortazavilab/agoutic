"""
Analysis context / summary builders.

Extracted from cortex/app.py — pure functions that assemble markdown
or structured text for auto-analysis results.  No database or app
dependencies.

Functions:
    _build_auto_analysis_context    — structured LLM context from parsed data
    _build_static_analysis_summary  — fallback markdown when LLM call fails
"""

from __future__ import annotations

from cortex import config as cortex_config


def _normalized_workflow_key(summary_data: dict) -> str:
    value = str((summary_data or {}).get("workflow_key") or "dogme").strip().lower()
    return value or "dogme"


def _is_wf_pore_c_summary(summary_data: dict, wf_pore_c_enabled: bool | None = None) -> bool:
    if wf_pore_c_enabled is None:
        wf_pore_c_enabled = bool(cortex_config.WF_PORE_C_ENABLED)
    return bool(wf_pore_c_enabled) and _normalized_workflow_key(summary_data) == "wf_pore_c"


def _format_presence_label(present: bool) -> str:
    return "present" if present else "missing"


def _artifact_matches(artifact_data: dict) -> str:
    matches = artifact_data.get("matches") or []
    if not matches:
        return ""
    return f" ({', '.join(str(path) for path in matches)})"


def _build_wf_pore_c_context(summary_data: dict) -> str:
    parts: list[str] = []
    workflow_summary = summary_data.get("workflow_summary") or {}
    metadata = workflow_summary.get("metadata") or {}
    artifacts = workflow_summary.get("artifacts") or {}
    pairs_stats = workflow_summary.get("pairs_stats") or {}
    warnings = summary_data.get("warnings") or []

    sample_alias = workflow_summary.get("sample_alias") or summary_data.get("sample_name") or "Unknown"
    workflow_name = summary_data.get("workflow_key") or "wf_pore_c"
    parts.append(
        "## Workflow Summary\n"
        f"Workflow key: {workflow_name}\n"
        f"Sample alias: {sample_alias}\n"
        f"Revision: {metadata.get('workflow_version') or 'unknown'}\n"
        f"Reference: {metadata.get('reference_fasta') or 'unknown'}\n"
        f"Cutter: {metadata.get('cutter') or 'unknown'}"
    )

    if artifacts:
        parts.append("\n## Artifact Presence")
        for label, title in (
            ("report_html", "Workflow report"),
            ("pairs", "Pairs"),
            ("mcool", "Multi-resolution cooler"),
            ("hic", "Hi-C"),
        ):
            artifact_data = artifacts.get(label) or {}
            if not artifact_data.get("requested") and label != "report_html":
                continue
            parts.append(
                f"- {title}: {_format_presence_label(bool(artifact_data.get('present')))}"
                f"{_artifact_matches(artifact_data)}"
            )

    if pairs_stats:
        parts.append("\n## pairs.stats.txt Metrics")
        if pairs_stats.get("total_pairs") is not None:
            parts.append(f"- Total pairs: {pairs_stats['total_pairs']}")
        if pairs_stats.get("cis_trans_ratio") is not None:
            parts.append(f"- Cis/trans ratio: {pairs_stats['cis_trans_ratio']:.3f}")
        if pairs_stats.get("duplicate_rate") is not None:
            parts.append(f"- Duplicate rate: {pairs_stats['duplicate_rate']:.3%}")

    requested_outputs = workflow_summary.get("requested_outputs") or []
    if requested_outputs:
        parts.append("\n## Requested Outputs")
        for item in requested_outputs:
            status = "present" if item.get("present") else "missing"
            parts.append(f"- {item.get('expected')}: {status}")

    if warnings:
        parts.append("\n## Warnings")
        for warning in warnings:
            parts.append(f"- {warning}")

    return "\n".join(parts)


def _build_wf_pore_c_static_summary(sample_name: str, summary_data: dict, work_directory: str = "") -> str:
    workflow_summary = summary_data.get("workflow_summary") or {}
    metadata = workflow_summary.get("metadata") or {}
    artifacts = workflow_summary.get("artifacts") or {}
    pairs_stats = workflow_summary.get("pairs_stats") or {}
    warnings = summary_data.get("warnings") or []
    sample_alias = workflow_summary.get("sample_alias") or sample_name
    workflow_name = work_directory.rstrip("/").rsplit("/", 1)[-1] if work_directory else ""

    md = (
        f"### Contact Map Summary: {sample_alias}\n\n"
        f"**Workflow:** {workflow_name} &nbsp;|&nbsp; "
        f"**Workflow key:** wf_pore_c &nbsp;|&nbsp; "
        f"**Status:** {summary_data.get('status', 'COMPLETED')}\n\n"
        f"- **Revision:** {metadata.get('workflow_version') or 'unknown'}\n"
        f"- **Reference:** {metadata.get('reference_fasta') or 'unknown'}\n"
        f"- **Cutter:** {metadata.get('cutter') or 'unknown'}\n"
    )

    if artifacts:
        md += "\n**Artifacts**\n"
        for label, title in (
            ("report_html", "Workflow report"),
            ("pairs", "Pairs"),
            ("mcool", "Multi-resolution cooler"),
            ("hic", "Hi-C"),
        ):
            artifact_data = artifacts.get(label) or {}
            if not artifact_data.get("requested") and label != "report_html":
                continue
            md += f"- {title}: {_format_presence_label(bool(artifact_data.get('present')))}\n"

    if pairs_stats:
        md += "\n**pairs.stats.txt metrics**\n"
        if pairs_stats.get("total_pairs") is not None:
            md += f"- Total pairs: {pairs_stats['total_pairs']}\n"
        if pairs_stats.get("cis_trans_ratio") is not None:
            md += f"- Cis/trans ratio: {pairs_stats['cis_trans_ratio']:.3f}\n"
        if pairs_stats.get("duplicate_rate") is not None:
            md += f"- Duplicate rate: {pairs_stats['duplicate_rate']:.3%}\n"

    if warnings:
        md += "\n**Warnings**\n"
        for warning in warnings:
            md += f"- {warning}\n"

    md += (
        "\n");
    md += (
        "💡 *You can ask me to dive deeper — for example:*\n"
        "- \"Show me the pairs stats\"\n"
        "- \"Summarize the contact map outputs\"\n"
        "- \"Which requested outputs are missing?\"\n"
    )
    return md


def _build_auto_analysis_context(
    sample_name: str, mode: str, run_uuid: str,
    summary_data: dict, parsed_csvs: dict,
    wf_pore_c_enabled: bool | None = None,
) -> str:
    """
    Build a structured text context from the Analyzer summary and parsed CSVs
    for the LLM to interpret.
    """
    if _is_wf_pore_c_summary(summary_data, wf_pore_c_enabled=wf_pore_c_enabled):
        return _build_wf_pore_c_context(summary_data)

    parts = []

    # File inventory
    if summary_data:
        all_counts = summary_data.get("all_file_counts", {})
        file_summary = summary_data.get("file_summary", {})
        total_files = all_counts.get("total_files", 0)
        csv_count = all_counts.get("csv_count", len(file_summary.get("csv_files", [])))
        bed_count = all_counts.get("bed_count", len(file_summary.get("bed_files", [])))
        txt_count = all_counts.get("txt_count", len(file_summary.get("txt_files", [])))
        parts.append(
            f"## File Inventory\n"
            f"Total files: {total_files} (CSV/TSV: {csv_count}, BED: {bed_count}, "
            f"Text/other: {txt_count})"
        )

        csv_files = file_summary.get("csv_files", [])
        if csv_files:
            parts.append("**Available CSV files:**")
            for f in csv_files[:12]:
                size_kb = f.get("size", 0) / 1024
                parts.append(f"- {f['name']} ({size_kb:.1f} KB)")
            if len(csv_files) > 12:
                parts.append(f"- …and {len(csv_files) - 12} more")

    # Parsed CSV data
    for fname, parse_result in parsed_csvs.items():
        rows = parse_result.get("data", [])
        columns = parse_result.get("columns", [])
        total_rows = parse_result.get("total_rows", len(rows))
        if not rows:
            continue
        parts.append(f"\n## Data: {fname} ({total_rows} rows)")
        if columns:
            parts.append("| " + " | ".join(str(c) for c in columns) + " |")
            parts.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in rows[:30]:  # cap at 30 rows for token economy
            if isinstance(row, dict):
                vals = [str(row.get(c, "")) for c in columns] if columns else [str(v) for v in row.values()]
            elif isinstance(row, (list, tuple)):
                vals = [str(v) for v in row]
            else:
                vals = [str(row)]
            parts.append("| " + " | ".join(vals) + " |")
        if total_rows > 30:
            parts.append(f"\n_(showing 30 of {total_rows} rows)_")

    return "\n".join(parts) if parts else "No analysis data available."


def _build_static_analysis_summary(
    sample_name: str, mode: str, run_uuid: str, summary_data: dict,
    work_directory: str = "",
    wf_pore_c_enabled: bool | None = None,
) -> str:
    """
    Build a static markdown summary (fallback when the LLM call fails).
    This is the original template that was used before the LLM pass was added.
    """
    if _is_wf_pore_c_summary(summary_data, wf_pore_c_enabled=wf_pore_c_enabled):
        return _build_wf_pore_c_static_summary(sample_name, summary_data, work_directory=work_directory)

    if summary_data:
        all_counts = summary_data.get("all_file_counts", {})
        file_summary = summary_data.get("file_summary", {})
        total_files = all_counts.get("total_files", 0)
        csv_count = all_counts.get("csv_count", len(file_summary.get("csv_files", [])))
        bed_count = all_counts.get("bed_count", len(file_summary.get("bed_files", [])))
        txt_count = all_counts.get("txt_count", len(file_summary.get("txt_files", [])))

        _wf_name = work_directory.rstrip("/").rsplit("/", 1)[-1] if work_directory else ""
        md = (
            f"### 📊 Analysis Ready: {sample_name}\n\n"
            f"**Workflow:** {_wf_name}\n"
            f"**Mode:** {summary_data.get('mode', mode)} &nbsp;|&nbsp; "
            f"**Status:** {summary_data.get('status', 'COMPLETED')} &nbsp;|&nbsp; "
            f"**Total files:** {total_files}\n\n"
            f"| Category | Count |\n"
            f"|----------|-------|\n"
            f"| CSV / TSV | {csv_count} |\n"
            f"| BED | {bed_count} |\n"
            f"| Text / other | {txt_count} |\n\n"
        )

        csv_files = file_summary.get("csv_files", [])
        if csv_files:
            md += "**Key result files:**\n"
            for f in csv_files[:8]:
                size_kb = f.get("size", 0) / 1024
                md += f"- `{f['name']}` ({size_kb:.1f} KB)\n"
            if len(csv_files) > 8:
                md += f"- _…and {len(csv_files) - 8} more_\n"
            md += "\n"
    else:
        md = (
            f"### 📊 Job Completed: {sample_name}\n\n"
            f"The {mode} job finished successfully. "
            f"I couldn't fetch the file summary automatically, but you can ask me to "
            f"analyze the results.\n\n"
        )

    md += (
        "💡 *You can ask me to dive deeper — for example:*\n"
        "- \"Show me the modification summary\"\n"
        "- \"Parse the CSV results\"\n"
        "- \"Give me a QC report\"\n"
    )
    return md
