"""
Analysis context / summary builders.

Extracted from cortex/app.py — pure functions that assemble markdown
or structured text for auto-analysis results.  No database or app
dependencies.

Functions:
    _build_auto_analysis_context    — structured LLM context from parsed data
    _build_static_analysis_summary  — fallback markdown when LLM call fails
"""


def _build_auto_analysis_context(
    sample_name: str, mode: str, run_uuid: str,
    summary_data: dict, parsed_csvs: dict,
) -> str:
    """
    Build a structured text context from the Analyzer summary and parsed CSVs
    for the LLM to interpret.
    """
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
) -> str:
    """
    Build a static markdown summary (fallback when the LLM call fails).
    This is the original template that was used before the LLM pass was added.
    """
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
