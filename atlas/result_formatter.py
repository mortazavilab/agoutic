"""
Result Formatter for MCP Tool Responses

Formats tool call results into user-facing markdown, driven by registry
configuration (table columns, count fields, etc.) rather than hardcoded
field names.
"""

import json
from collections import Counter
from typing import Any

from atlas.config import CONSORTIUM_REGISTRY


def format_results(source_key: str, results: list[dict], registry_entry: dict | None = None) -> str:
    """
    Format a list of tool call results into markdown for display.

    Each result dict has: {"tool": str, "params": dict, "data": Any}
    or {"tool": str, "params": dict, "error": str}

    Args:
        source_key: Key from CONSORTIUM_REGISTRY or SERVICE_REGISTRY
        results: List of tool result dicts
        registry_entry: Optional pre-looked-up registry entry dict.
                        If not provided, looks up from CONSORTIUM_REGISTRY.

    Returns:
        Markdown string ready to append to the chat response
    """
    if not results:
        return ""

    # Use provided entry or look up from consortium registry
    entry = registry_entry if registry_entry is not None else _get_registry_entry(source_key)
    emoji = entry.get("emoji", "📡")
    display_name = entry.get("display_name", source_key.upper())
    tool_names = {result.get("tool", "") for result in results}
    if tool_names == {"list_remote_files"}:
        display_name = "Remote Files"
        emoji = "📁"
    table_columns = entry.get("table_columns", [])
    count_field = entry.get("count_field")
    count_label = entry.get("count_label", "type")

    markdown = f"\n\n---\n\n### {emoji} {display_name}\n\n"

    for result in results:
        tool_name = result.get("tool", "unknown")
        params = result.get("params", {})

        # Header for this tool call
        markdown += f"**{tool_name.replace('_', ' ').title()}**"
        if params:
            display_params = {
                key: value for key, value in params.items()
                if not (source_key == "literature" and key == "user_id")
            }
            if display_params:
                markdown += f" ({', '.join(f'{k}={v}' for k, v in display_params.items())})"
        markdown += "\n\n"

        if "error" in result:
            markdown += f"❌ Error: {result['error']}\n\n"
        elif "data" in result:
            data = result["data"]
            if source_key == "literature" and tool_name == "search_literature":
                markdown += _format_literature_results(data)
            else:
                markdown += _format_data(data, table_columns, count_field, count_label)
        markdown += "\n"

    return markdown


def _format_literature_results(data: Any) -> str:
    """Render literature records as cited summaries rather than abbreviated JSON."""
    if not isinstance(data, dict):
        return "PubMed returned an invalid literature result payload.\n"

    query = str(data.get("query") or "").strip()
    papers = data.get("papers")
    if not isinstance(papers, list):
        return "PubMed returned an invalid literature result payload.\n"
    if not papers:
        notice = str(data.get("notice") or "No PubMed records matched this query.")
        return f"**No papers found**{f' for “{query}”' if query else ''}. {notice}\n"

    lines = [
        f"Found **{len(papers)} paper(s)**{f' for “{query}”' if query else ''}, "
        "ranked by topic relevance (then recency and study-design cues).",
        "",
        "| # | Paper | Year | Organism/model | Study type | Key finding | Evidence |",
        "|---:|---|---:|---|---|---|---|",
    ]
    evidence_labels = {
        "full_text": "PMC open full text",
        "abstract": "PubMed abstract",
        "metadata": "Metadata only; no abstract/full text available",
    }
    for index, paper in enumerate(papers, start=1):
        if not isinstance(paper, dict):
            continue
        title = str(paper.get("title") or "Untitled article").strip()
        table_title = _table_cell(title, 100)
        year = str(paper.get("year") or str(paper.get("publication_date") or "")[:4])
        organisms = paper.get("organisms") or []
        organism_label = ", ".join(str(value) for value in organisms) if isinstance(organisms, list) else str(organisms)
        study_types = paper.get("study_type") or []
        study_label = ", ".join(str(value) for value in study_types[:2]) if isinstance(study_types, list) else str(study_types)
        evidence_data = paper.get("evidence")
        finding = ""
        if isinstance(evidence_data, dict):
            finding = str(evidence_data.get("findings") or evidence_data.get("conclusion") or "")
        if not finding:
            summary_value = paper.get("summary")
            finding = str(summary_value.get("text") or "") if isinstance(summary_value, dict) else str(summary_value or "")
        evidence_source = str(paper.get("evidence_source") or "metadata")
        evidence_label = {
            "full_text": "PMC full text",
            "abstract": "PubMed abstract",
            "metadata": "Metadata only",
        }.get(evidence_source, evidence_source)
        lines.append(
            f"| {index} | {table_title} | {_table_cell(year, 8)} | "
            f"{_table_cell(organism_label, 48)} | {_table_cell(study_label, 55)} | "
            f"{_table_cell(finding, 180)} | {_table_cell(evidence_label, 20)} |"
        )
    lines.append("")
    lines.append("### Paper details")
    lines.append("")
    for index, paper in enumerate(papers, start=1):
        if not isinstance(paper, dict):
            continue
        title = str(paper.get("title") or "Untitled article").strip()
        pubmed_url = str(paper.get("pubmed_url") or "").strip()
        if pubmed_url:
            title_link = f"[{title}]({pubmed_url})"
        else:
            title_link = title
        authors_value = paper.get("authors") or []
        authors = (
            ", ".join(str(author) for author in authors_value[:6])
            if isinstance(authors_value, list)
            else str(authors_value)
        )
        if isinstance(authors_value, list) and len(authors_value) > 6:
            authors += ", et al."
        journal = str(paper.get("journal") or "").strip()
        date = str(paper.get("publication_date") or "").strip()
        citation = " · ".join(value for value in (authors, journal, date) if value)
        evidence = str(paper.get("evidence_source") or "metadata")
        evidence_label = evidence_labels.get(evidence, "Evidence source not specified")
        availability = str(paper.get("availability") or "")
        if availability == "full_text":
            evidence_label = "PMC open full text"
        summary = paper.get("summary")
        if isinstance(summary, dict):
            summary_text = str(summary.get("text") or "").strip()
            summary_evidence = str(summary.get("evidence_source") or evidence)
            evidence_label = evidence_labels.get(summary_evidence, evidence_label)
        else:
            summary_text = str(summary or "").strip()
        identifiers = []
        pmid = str(paper.get("pmid") or "").strip()
        if pmid:
            identifiers.append(f"PMID: {pmid}")
        doi = str(paper.get("doi") or "").strip()
        if doi:
            identifiers.append(f"[DOI](https://doi.org/{doi})")
        pmc_url = str(paper.get("pmc_url") or "").strip()
        if pmc_url:
            identifiers.append(f"[PMC record]({pmc_url})")

        lines.append(f"### {index}. {title_link}")
        if citation:
            lines.append(citation)
        if identifiers:
            lines.append(" · ".join(identifiers))
        lines.append(f"**Summary evidence:** {evidence_label}")
        lines.append(summary_text or "No evidence-based summary is available for this record.")
        rationale = str(paper.get("rank_rationale") or "").strip()
        if rationale:
            lines.append(f"**Why it matched:** {rationale}")
        evidence_fields = paper.get("evidence")
        if isinstance(evidence_fields, dict):
            evidence_names = {
                "methods": "Methods",
                "findings": "Findings",
                "conclusion": "Conclusion",
                "limitations": "Limitations",
            }
            for key, label in evidence_names.items():
                value = str(evidence_fields.get(key) or "").strip()
                if value:
                    lines.append(f"**{label} ({evidence_label}):** {value[:700]}")
        sections = paper.get("abstract_sections")
        if isinstance(sections, list) and sections:
            labelled = [
                f"{section.get('label')}: {section.get('text')}"
                for section in sections
                if isinstance(section, dict) and section.get("label") and section.get("text")
            ]
            if labelled:
                lines.append("**Structured abstract:** " + " ".join(labelled))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _table_cell(value: str, max_length: int) -> str:
    """Keep Markdown table cells compact and prevent source text breaking rows."""
    return " ".join(str(value or "").replace("|", "\\|").split())[:max_length]


def _format_data(
    data: Any,
    table_columns: list[tuple[str, str]],
    count_field: str | None,
    count_label: str,
) -> str:
    """Format a single data result based on its type and column config."""

    # IGVF tools return {"total": N, "count": N, "results": [...]} or
    # {"total_files": N, "files": [...]}; unwrap so the list branch handles
    # them with proper table formatting.
    if isinstance(data, dict):
        if "results" in data and isinstance(data["results"], list):
            data = data["results"]
        elif "total_files" in data and isinstance(data.get("files"), list):
            data = data["files"]

    if isinstance(data, list):
        total = len(data)
        result = f"Found {total} result(s):\n\n"

        if data and isinstance(data[0], dict) and table_columns:
            # ── Summary first so the LLM always sees the totals even when the
            #    row table is truncated by MAX_DATA_CHARS downstream. ──────────
            if count_field:
                counts = Counter()
                for item in data:
                    val = item.get(count_field, "Unknown")
                    if val:
                        counts[val] += 1

                if counts:
                    result += f"**📊 Summary: {total} total, {len(counts)} unique {count_label}(s)**\n\n"
                    result += f"| {count_label.title()} | Count |\n"
                    result += "|---|---|\n"
                    for val, count in counts.most_common():
                        result += f"| {val} | {count} |\n"
                    result += f"\n**Total: {total} results**\n\n"

            # ── Full row table (may be truncated downstream) ─────────────────
            headers = [col[0] for col in table_columns]
            result += "| " + " | ".join(headers) + " |\n"
            result += "|" + "|".join(["---"] * len(headers)) + "|\n"

            for item in data:
                row_values = []
                for _, field_key in table_columns:
                    value = item.get(field_key, "")
                    # Handle list fields (e.g., targets)
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    row_values.append(str(value))
                result += "| " + " | ".join(row_values) + " |\n"

        elif data:
            # No table columns configured or items are not dicts — render as list
            for item in data:
                result += f"- {str(item)[:200]}\n"

        return result

    elif isinstance(data, dict):
        if data.get("script_id") == "analyze_job_results/count_bed":
            formatted_script = _format_bed_counter_script_result(data)
            if formatted_script:
                return formatted_script

        # Launchpad remote directory listing results
        if "entries" in data and isinstance(data["entries"], list) and "path" in data:
            entries = data["entries"]
            remote_path = data.get("path", "")
            result = f"**Remote directory: {remote_path}** ({len(entries)} entries)\n\n"
            result += "| Name | Type | Size |\n"
            result += "|---|---|---|\n"
            for entry in entries:
                if isinstance(entry, dict):
                    name = entry.get("name", "")
                    kind = entry.get("type", "")
                    size = entry.get("size")
                    if isinstance(size, (int, float)) and size > 0:
                        size_str = _human_size(int(size))
                    elif kind == "dir":
                        size_str = "-"
                    elif size == 0:
                        size_str = "0 B"
                    else:
                        size_str = "?"
                    result += f"| {name} | {kind} | {size_str} |\n"
                else:
                    result += f"| {entry} | | |\n"
            return result

        # Check if this is a files-by-type dict (e.g., {"bam": [...], "fastq": [...]})
        # where values are lists of file-like objects with 'accession' keys
        if data and all(isinstance(v, list) for v in data.values()):
            first_items = [v[0] for v in data.values() if v and isinstance(v[0], dict)]
            if first_items and "accession" in first_items[0]:
                return _format_files_by_type(data)

        # Check if this is a files-summary dict (e.g., {"bam": {"count": 12, "files": [...]}})
        # where values are dicts containing a 'files' list
        if data and all(isinstance(v, dict) and "files" in v for v in data.values()):
            # Unwrap to files-by-type format: {"bam": [...], "fastq": [...]}
            unwrapped = {}
            for ftype, info in data.items():
                files = info.get("files", [])
                if isinstance(files, list):
                    unwrapped[ftype] = files
            if unwrapped:
                first_items = [v[0] for v in unwrapped.values() if v and isinstance(v[0], dict)]
                if first_items and "accession" in first_items[0]:
                    return _format_files_by_type(unwrapped)

        # Parsed CSV/BED results — render as readable markdown table
        # instead of JSON so the second-pass LLM can produce useful summaries.
        if "columns" in data and "data" in data and isinstance(data["data"], list):
            cols = data["columns"]
            rows = data["data"]
            fname = data.get("file_path", "")
            row_count = data.get("row_count", len(rows))
            result = f"**Parsed file: {fname}** ({row_count} rows × {len(cols)} columns)\n\n"
            # Render as markdown table (cap columns for readability)
            MAX_DISPLAY_COLS = 12
            display_cols = cols[:MAX_DISPLAY_COLS]
            result += "| " + " | ".join(display_cols) + " |\n"
            result += "|" + "|".join(["---"] * len(display_cols)) + "|\n"
            for row in rows:
                vals = [str(row.get(c, "")) for c in display_cols]
                result += "| " + " | ".join(vals) + " |\n"
            if len(cols) > MAX_DISPLAY_COLS:
                result += f"\n*({len(cols) - MAX_DISPLAY_COLS} more columns not shown)*\n"
            return result

        # Analyzer file listing results — render as a readable table
        if "files" in data and isinstance(data["files"], list) and "file_count" in data:
            files = data["files"]
            work_dir = data.get("work_dir", "")
            total_count = data.get("file_count", len(files))
            total_bytes = data.get("total_size_bytes", 0)
            result = f"**Directory: {work_dir}** ({total_count} entries"
            if total_bytes:
                if total_bytes > 1_000_000_000:
                    result += f", {total_bytes / 1_000_000_000:.1f} GB"
                elif total_bytes > 1_000_000:
                    result += f", {total_bytes / 1_000_000:.1f} MB"
                elif total_bytes > 1_000:
                    result += f", {total_bytes / 1_000:.1f} KB"
            result += ")\n\n"
            result += "| Name | Path | Size |\n"
            result += "|---|---|---|\n"
            for f in files:
                if isinstance(f, dict):
                    name = f.get("name", "")
                    path = f.get("path", "")
                    size = f.get("size", 0)
                    if isinstance(size, (int, float)) and size > 0:
                        if size > 1_000_000_000:
                            size_str = f"{size / 1_000_000_000:.1f} GB"
                        elif size > 1_000_000:
                            size_str = f"{size / 1_000_000:.1f} MB"
                        elif size > 1_000:
                            size_str = f"{size / 1_000:.1f} KB"
                        else:
                            size_str = f"{size} B"
                    else:
                        size_str = "—" if name.endswith("/") else "0 B"
                    result += f"| {name} | {path} | {size_str} |\n"
                else:
                    result += f"| {f} | | |\n"
            return result

        if "content" in data and "file_path" in data:
            file_path = data.get("file_path", "")
            render_mode = str(data.get("render_mode") or "plain")
            source_extension = str(data.get("source_extension") or "")
            line_count = data.get("line_count")
            file_size = data.get("file_size_bytes", data.get("file_size"))
            details: list[str] = []
            if line_count is not None:
                details.append(f"{line_count} lines")
            if isinstance(file_size, (int, float)):
                details.append(_human_size(int(file_size)))
            details.append(f"rendered as {render_mode}")

            if render_mode == "html_raw":
                fence = "html"
            elif render_mode == "markdown" or source_extension in {".md", ".markdown"}:
                fence = "markdown"
            else:
                fence = "text"

            result = f"**File: {file_path}**"
            if details:
                result += f" ({', '.join(details)})"
            result += "\n\n"
            content = str(data.get("content") or "")
            if content:
                result += f"```{fence}\n{content.rstrip()}\n```\n"
            else:
                result += "*File is empty.*\n"
            if data.get("is_truncated"):
                result += "\n*Preview truncated.*\n"
            return result

        # Generic dict — show as compact JSON (strip deeply nested objects)
        # For analysis/parsing results (Analyzer), allow deeper nesting so row data is visible
        is_analysis_data = "records" in data or "preview_rows" in data
        depth_limit = 4 if is_analysis_data else 2
        compact = _compact_dict(data, max_depth=depth_limit)
        json_str = json.dumps(compact, indent=2)
        return f"```json\n{json_str}\n```\n"

    else:
        return f"{str(data)}\n"


def _format_bed_counter_script_result(data: dict) -> str | None:
    """Format count_bed results so second-pass LLM sees totals before details."""
    dataframe = data.get("dataframe")
    if not isinstance(dataframe, dict):
        return None

    lines: list[str] = []
    exit_code = data.get("exit_code")
    header = "**BED count script result**"
    if isinstance(exit_code, int):
        header += f" (exit_code={exit_code})"
    lines.append(header)
    lines.append("")

    totals_markdown = str(data.get("modification_totals_markdown") or "").strip()
    if totals_markdown:
        lines.append(totals_markdown)
        lines.append("")

    metadata = dataframe.get("metadata") or {}
    label = metadata.get("label") or "BED chromosome counts"
    row_count = dataframe.get("row_count", len(dataframe.get("data") or []))
    col_count = len(dataframe.get("columns") or [])
    lines.append(f"Detailed dataframe: **{label}** ({row_count} rows x {col_count} columns).")

    input_files = metadata.get("input_files")
    if isinstance(input_files, list) and input_files:
        lines.append(f"Input files counted: {len(input_files)}.")

    stderr = str(data.get("stderr") or "").strip()
    if stderr:
        lines.append("")
        lines.append("stderr:")
        lines.append(f"```text\n{stderr}\n```")

    return "\n".join(lines).rstrip() + "\n"


def _format_files_by_type(data: dict) -> str:
    """
    Format a files-by-type dict into a clean summary table per file type.

    Input: {"bam": [file_objs...], "fastq": [file_objs...], "tar": [...], ...}
    Output: A markdown section per file type with a summary table.
    """
    result = ""

    # Summary overview
    type_counts = {ftype: len(files) for ftype, files in data.items() if files}
    total = sum(type_counts.values())
    result += f"**{total} files** across {len(type_counts)} types: "
    result += ", ".join(f"{ftype} ({count})" for ftype, count in sorted(type_counts.items()))
    result += "\n\n"

    for file_type, files in sorted(data.items()):
        if not files:
            continue

        result += f"#### {file_type.upper()} ({len(files)} file{'s' if len(files) != 1 else ''})\n\n"

        if isinstance(files[0], dict):
            result += "| Accession | Output Type | Replicate | Size | Status |\n"
            result += "|---|---|---|---|---|\n"

            for f in files:
                acc = f.get("accession", "?")
                output_type = f.get("output_type", "")
                reps = f.get("biological_replicates_formatted", "")
                if not reps:
                    bio_reps = f.get("biological_replicates", [])
                    reps = ", ".join(f"Rep {r}" for r in bio_reps) if bio_reps else ""
                size_bytes = f.get("file_size", 0)
                size = _human_size(size_bytes) if size_bytes else "?"
                status = f.get("status", "")
                result += f"| {acc} | {output_type} | {reps} | {size} | {status} |\n"
        else:
            for f in files:
                result += f"- {str(f)[:200]}\n"

        result += "\n"

    return result


def _human_size(nbytes: int) -> str:
    """Convert bytes to human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


def _compact_dict(d: dict, max_depth: int = 2, _depth: int = 0) -> Any:
    """
    Recursively compact a dict by truncating deeply nested objects.
    Prevents multi-thousand-line JSON dumps in the UI.
    """
    if _depth >= max_depth:
        if isinstance(d, dict):
            return {k: "..." for k in list(d.keys())[:5]}
        if isinstance(d, list):
            return f"[{len(d)} items]"
        return d

    if isinstance(d, dict):
        return {k: _compact_dict(v, max_depth, _depth + 1) for k, v in d.items()}
    if isinstance(d, list):
        if len(d) > 10:
            return [_compact_dict(item, max_depth, _depth + 1) for item in d[:10]] + [f"... +{len(d) - 10} more"]
        return [_compact_dict(item, max_depth, _depth + 1) for item in d]
    return d


def _get_registry_entry(key: str) -> dict:
    """Look up a registry entry from the consortium registry."""
    if key in CONSORTIUM_REGISTRY:
        return CONSORTIUM_REGISTRY[key]
    # Return sensible defaults for unknown sources
    return {
        "display_name": key.upper(),
        "emoji": "📡",
        "table_columns": [],
        "count_field": None,
        "count_label": "type",
    }
