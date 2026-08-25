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


def _effective_workflow_family(summary_data: dict, wf_pore_c_enabled: bool | None = None) -> str:
    workflow_key = _normalized_workflow_key(summary_data)
    if workflow_key == "wf_pore_c":
        if wf_pore_c_enabled is None:
            wf_pore_c_enabled = bool(cortex_config.WF_PORE_C_ENABLED)
        return "wf_pore_c" if wf_pore_c_enabled else "dogme"
    return workflow_key


def _build_reconcile_bams_context(summary_data: dict) -> str:
    workflow_summary = summary_data.get("workflow_summary") or {}
    metadata = workflow_summary.get("metadata") or {}
    artifacts = workflow_summary.get("artifacts") or {}
    parsed_reports = summary_data.get("parsed_reports") or {}
    warnings = summary_data.get("warnings") or []

    transcript_category_counts = metadata.get("transcript_category_counts") or {}
    novelty_category_totals = metadata.get("novelty_category_totals") or {}
    novel_model_counts_after_filtering = metadata.get("novel_model_counts_after_filtering") or {}

    parts = [
        "## Workflow Summary\n"
        f"Workflow key: reconcile_bams\n"
        f"Input BAM count: {metadata.get('input_bam_count') or 0}\n"
        f"Reference: {metadata.get('reference') or 'mixed or unknown'}"
    ]

    if transcript_category_counts:
        total_isoforms = sum(int(value or 0) for value in transcript_category_counts.values())
        parts.append(
            "\n## Isoform Summary\n"
            f"Total transcript models / isoforms: {total_isoforms}\n"
            + "\n".join(
                f"- {label}: {count}"
                for label, count in sorted(transcript_category_counts.items())
            )
        )

    abundance_gene_count = metadata.get("gene_count")
    abundance_isoform_count = metadata.get("isoform_count")
    abundance_transcript_novelty_counts = metadata.get("abundance_transcript_novelty_counts") or {}
    if abundance_gene_count is not None or abundance_isoform_count is not None:
        parts.append("\n## Reconciled Output Counts")
        if abundance_gene_count is not None:
            parts.append(f"- Genes in abundance table: {abundance_gene_count}")
        if abundance_isoform_count is not None:
            parts.append(f"- Isoforms in abundance table: {abundance_isoform_count}")
        for label, count in sorted(abundance_transcript_novelty_counts.items()):
            parts.append(f"- Abundance table {label} isoforms: {count}")

    gene_lines = []
    if metadata.get("novel_gene_count") is not None:
        gene_lines.append(f"- Novel genes: {metadata['novel_gene_count']}")
    if metadata.get("novel_transcript_count") is not None:
        gene_lines.append(f"- Novel isoforms: {metadata['novel_transcript_count']}")
    if metadata.get("solo_transcript_count") is not None:
        gene_lines.append(f"- Single-read solo transcripts: {metadata['solo_transcript_count']}")
    if metadata.get("strand_consolidated_count"):
        gene_lines.append(f"- Strand-corrected models consolidated: {metadata['strand_consolidated_count']}")
    if gene_lines:
        parts.append("\n## Gene and Isoform Counts\n" + "\n".join(gene_lines))

    if novelty_category_totals:
        parts.append("\n## Novelty Read Totals Across Samples")
        for label, count in sorted(novelty_category_totals.items()):
            parts.append(f"- {label}: {count}")

    top_novelty_samples = metadata.get("top_novelty_samples") or []
    if top_novelty_samples:
        parts.append("\n## Top Samples by Reconciled Read Count")
        for item in top_novelty_samples:
            parts.append(
                f"- {item.get('sample')}: {item.get('total_reads')} total reads; "
                f"dominant class {item.get('dominant_category') or 'unknown'}"
            )

    if novel_model_counts_after_filtering:
        parts.append("\n## Novel Model Types After Filtering")
        if metadata.get("total_novel_after_filtering") is not None:
            parts.append(f"- Total novel isoforms after filtering: {metadata['total_novel_after_filtering']}")
        for label, count in sorted(novel_model_counts_after_filtering.items()):
            parts.append(f"- {label}: {count}")

    if metadata.get("filter_min_tpm") is not None:
        parts.append(
            "\n## Filtering\n"
            f"- Filter scope: {metadata.get('filter_scope') or 'unknown'} transcripts\n"
            f"- min_TPM: {metadata.get('filter_min_tpm')} in >= {metadata.get('filter_min_samples') or 0} samples\n"
            f"- Removed novel isoforms: {metadata.get('filtered_novel_removed') or 0}\n"
            f"- Remaining total transcripts: {metadata.get('filtered_remaining_total') or 0}"
        )

    references = metadata.get("references") or []
    if references:
        parts.append("\n## References\n" + "\n".join(f"- {reference}" for reference in references))

    samples = metadata.get("samples") or []
    if samples:
        parts.append("\n## Samples\n" + "\n".join(f"- {sample}" for sample in samples))

    if artifacts:
        parts.append("\n## Artifact Presence")
        for label, title in (
            ("inputs_manifest", "Inputs manifest"),
            ("reconciled_bam", "Reconciled BAM outputs"),
            ("summary_report", "Reconciled summary report"),
            ("novelty_csv", "Novelty-by-sample CSV"),
            ("bam_index", "BAM indexes"),
            ("annotation_gtf", "Annotation GTF"),
            ("tsv_outputs", "TSV outputs"),
            ("txt_reports", "Text reports"),
        ):
            artifact_data = artifacts.get(label) or {}
            parts.append(
                f"- {title}: {_format_presence_label(bool(artifact_data.get('present')))}"
                f"{_artifact_matches(artifact_data)}"
            )

    reconciled_summary_text = str(parsed_reports.get("reconciled_summary") or "").strip()
    if reconciled_summary_text:
        preview_lines = reconciled_summary_text.splitlines()[:16]
        parts.append("\n## Reconciled Summary Report Preview\n" + "\n".join(preview_lines))

    if warnings:
        parts.append("\n## Warnings")
        for warning in warnings:
            parts.append(f"- {warning}")

    return "\n".join(parts)


def _build_haplotype_with_vcf_context(summary_data: dict) -> str:
    workflow_summary = summary_data.get("workflow_summary") or {}
    metadata = workflow_summary.get("metadata") or {}
    artifacts = workflow_summary.get("artifacts") or {}
    warnings = summary_data.get("warnings") or []

    assignment_labels = metadata.get("assignment_labels") or []
    parts = [
        "## Workflow Summary\n"
        f"Workflow key: haplotype_with_vcf\n"
        f"Haplotyped BAM count: {metadata.get('haplotyped_bam_count') or 0}\n"
        f"Assignment labels: {', '.join(str(label) for label in assignment_labels) or 'unknown'}"
    ]

    if artifacts:
        parts.append("\n## Artifact Presence")
        for label, title in (
            ("haplotyped_bam", "Haplotyped BAM outputs"),
            ("ambiguous_bam", "Ambiguous BAM outputs"),
            ("bam_index", "BAM indexes"),
            ("genome_summary", "Genome summary TSV"),
            ("chromosome_summary", "Chromosome summary TSV"),
            ("gene_counts", "Gene counts TSV"),
            ("transcript_counts", "Transcript counts TSV"),
        ):
            artifact_data = artifacts.get(label) or {}
            parts.append(
                f"- {title}: {_format_presence_label(bool(artifact_data.get('present')))}"
                f"{_artifact_matches(artifact_data)}"
            )

    if warnings:
        parts.append("\n## Warnings")
        for warning in warnings:
            parts.append(f"- {warning}")

    return "\n".join(parts)


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


def _build_reconcile_bams_static_summary(sample_name: str, summary_data: dict, work_directory: str = "") -> str:
    workflow_summary = summary_data.get("workflow_summary") or {}
    metadata = workflow_summary.get("metadata") or {}
    artifacts = workflow_summary.get("artifacts") or {}
    warnings = summary_data.get("warnings") or []
    workflow_name = work_directory.rstrip("/").rsplit("/", 1)[-1] if work_directory else ""

    md = (
        f"### Reconcile Summary: {sample_name}\n\n"
        f"**Workflow:** {workflow_name} &nbsp;|&nbsp; "
        f"**Workflow key:** reconcile_bams &nbsp;|&nbsp; "
        f"**Status:** {summary_data.get('status', 'COMPLETED')}\n\n"
        f"- **Input BAM count:** {metadata.get('input_bam_count') or 0}\n"
        f"- **Reference:** {metadata.get('reference') or 'mixed or unknown'}\n"
    )

    transcript_category_counts = metadata.get("transcript_category_counts") or {}
    if transcript_category_counts:
        total_isoforms = sum(int(value or 0) for value in transcript_category_counts.values())
        md += f"- **Total isoforms / transcript models:** {total_isoforms}\n"
        md += "- **Isoform classes:** " + ", ".join(
            f"{label}={count}" for label, count in sorted(transcript_category_counts.items())
        ) + "\n"

    if metadata.get("gene_count") is not None:
        md += f"- **Genes in abundance table:** {metadata.get('gene_count')}\n"
    if metadata.get("isoform_count") is not None:
        md += f"- **Isoforms in abundance table:** {metadata.get('isoform_count')}\n"

    if metadata.get("novel_gene_count") is not None:
        md += f"- **Novel genes:** {metadata.get('novel_gene_count')}\n"
    if metadata.get("novel_transcript_count") is not None:
        md += f"- **Novel isoforms:** {metadata.get('novel_transcript_count')}\n"

    novelty_category_totals = metadata.get("novelty_category_totals") or {}
    if novelty_category_totals:
        md += "- **Novelty read totals:** " + ", ".join(
            f"{label}={count}" for label, count in sorted(novelty_category_totals.items())
        ) + "\n"

    novel_model_counts_after_filtering = metadata.get("novel_model_counts_after_filtering") or {}
    if novel_model_counts_after_filtering:
        md += "- **Novel model types after filtering:** " + ", ".join(
            f"{label}={count}" for label, count in sorted(novel_model_counts_after_filtering.items())
        ) + "\n"

    if artifacts:
        md += "\n**Artifacts**\n"
        for label, title in (
            ("inputs_manifest", "Inputs manifest"),
            ("reconciled_bam", "Reconciled BAM outputs"),
            ("summary_report", "Reconciled summary report"),
            ("novelty_csv", "Novelty-by-sample CSV"),
            ("bam_index", "BAM indexes"),
            ("annotation_gtf", "Annotation GTF"),
            ("tsv_outputs", "TSV outputs"),
            ("txt_reports", "Text reports"),
        ):
            artifact_data = artifacts.get(label) or {}
            md += f"- {title}: {_format_presence_label(bool(artifact_data.get('present')))}\n"

    if warnings:
        md += "\n**Warnings**\n"
        for warning in warnings:
            md += f"- {warning}\n"

    md += (
        "\n"
        "You can ask me to dive deeper, for example:\n"
        "- \"Show me the reconcile manifest\"\n"
        "- \"Which BAM outputs were produced?\"\n"
        "- \"Summarize the reconcile report\"\n"
    )
    return md


def _build_haplotype_with_vcf_static_summary(sample_name: str, summary_data: dict, work_directory: str = "") -> str:
    workflow_summary = summary_data.get("workflow_summary") or {}
    metadata = workflow_summary.get("metadata") or {}
    artifacts = workflow_summary.get("artifacts") or {}
    warnings = summary_data.get("warnings") or []
    workflow_name = work_directory.rstrip("/").rsplit("/", 1)[-1] if work_directory else ""
    assignment_labels = metadata.get("assignment_labels") or []

    md = (
        f"### Haplotype Summary: {sample_name}\n\n"
        f"**Workflow:** {workflow_name} &nbsp;|&nbsp; "
        f"**Workflow key:** haplotype_with_vcf &nbsp;|&nbsp; "
        f"**Status:** {summary_data.get('status', 'COMPLETED')}\n\n"
        f"- **Haplotyped BAM count:** {metadata.get('haplotyped_bam_count') or 0}\n"
        f"- **Assignment labels:** {', '.join(str(label) for label in assignment_labels) or 'unknown'}\n"
    )

    if artifacts:
        md += "\n**Artifacts**\n"
        for label, title in (
            ("haplotyped_bam", "Haplotyped BAM outputs"),
            ("ambiguous_bam", "Ambiguous BAM outputs"),
            ("bam_index", "BAM indexes"),
            ("genome_summary", "Genome summary TSV"),
            ("chromosome_summary", "Chromosome summary TSV"),
            ("gene_counts", "Gene counts TSV"),
            ("transcript_counts", "Transcript counts TSV"),
        ):
            artifact_data = artifacts.get(label) or {}
            md += f"- {title}: {_format_presence_label(bool(artifact_data.get('present')))}\n"

    if warnings:
        md += "\n**Warnings**\n"
        for warning in warnings:
            md += f"- {warning}\n"

    md += (
        "\n"
        "You can ask me to dive deeper, for example:\n"
        "- \"Show me the haplotype summary TSV\"\n"
        "- \"Summarize per-chromosome haplotype counts\"\n"
        "- \"Which BAMs were assigned ambiguously?\"\n"
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
    workflow_family = _effective_workflow_family(summary_data, wf_pore_c_enabled=wf_pore_c_enabled)
    if workflow_family == "wf_pore_c":
        return _build_wf_pore_c_context(summary_data)
    if workflow_family == "reconcile_bams":
        return _build_reconcile_bams_context(summary_data)
    if workflow_family == "haplotype_with_vcf":
        return _build_haplotype_with_vcf_context(summary_data)

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
    workflow_family = _effective_workflow_family(summary_data, wf_pore_c_enabled=wf_pore_c_enabled)
    if workflow_family == "wf_pore_c":
        return _build_wf_pore_c_static_summary(sample_name, summary_data, work_directory=work_directory)
    if workflow_family == "reconcile_bams":
        return _build_reconcile_bams_static_summary(sample_name, summary_data, work_directory=work_directory)
    if workflow_family == "haplotype_with_vcf":
        return _build_haplotype_with_vcf_static_summary(sample_name, summary_data, work_directory=work_directory)

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
