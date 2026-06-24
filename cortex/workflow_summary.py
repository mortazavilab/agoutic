from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from fastapi.concurrency import run_in_threadpool

from cortex.agent_engine import AgentEngine


WORKFLOW_SUMMARY_BASE_TEMPLATE = "workflow_summary/system_prompt.md"
WORKFLOW_SUMMARY_OVERRIDE_DIR = "workflow_summary/overrides"
_ANALYSIS_REPORT_RE = re.compile(r"_(\d{8}_\d{6})_analysis\.md$", re.IGNORECASE)
_WORKFLOW_KEY_RE = re.compile(r"(?:\*\*Workflow key:\*\*|Workflow key:)\s*`?([A-Za-z0-9_./-]+)`?", re.IGNORECASE)


@dataclass(frozen=True)
class WorkflowSummaryTarget:
    workflow_ref: str
    work_dir: str
    workflow_family: str = ""
    workflow_label: str = ""


@dataclass(frozen=True)
class WorkflowReportInput:
    workflow_ref: str
    workflow_label: str
    work_dir: str
    workflow_family: str
    report_path: str
    markdown: str


@dataclass
class WorkflowSummaryResult:
    markdown: str
    warnings: list[str] = field(default_factory=list)
    used_report_paths: list[str] = field(default_factory=list)


def find_latest_analysis_report(workflow_dir: str | Path) -> Path | None:
    try:
        root = Path(workflow_dir).expanduser().resolve()
    except OSError:
        return None

    if not root.is_dir():
        return None

    candidates = [path for path in root.glob("*_analysis.md") if path.is_file()]
    if not candidates:
        return None

    def _sort_key(path: Path) -> tuple[int, dt.datetime, float, str]:
        match = _ANALYSIS_REPORT_RE.search(path.name)
        timestamp = dt.datetime.min
        has_timestamp = 0
        if match:
            try:
                timestamp = dt.datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
                has_timestamp = 1
            except ValueError:
                timestamp = dt.datetime.min
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return has_timestamp, timestamp, mtime, path.name

    return max(candidates, key=_sort_key)


def infer_workflow_family_from_markdown(markdown: str) -> str:
    match = _WORKFLOW_KEY_RE.search(str(markdown or ""))
    if not match:
        return ""
    return str(match.group(1) or "").strip().lower()


def _load_workflow_reports(targets: Sequence[WorkflowSummaryTarget]) -> tuple[list[WorkflowReportInput], list[str]]:
    reports: list[WorkflowReportInput] = []
    warnings: list[str] = []

    for target in targets:
        report_path = find_latest_analysis_report(target.work_dir)
        label = target.workflow_label or target.workflow_ref or Path(target.work_dir).name
        if report_path is None:
            warnings.append(f"No saved analysis markdown was found for `{label}`.")
            continue

        try:
            markdown = report_path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"Couldn't read `{report_path}` for `{label}`: {exc}.")
            continue

        workflow_family = str(target.workflow_family or "").strip().lower() or infer_workflow_family_from_markdown(markdown)
        reports.append(
            WorkflowReportInput(
                workflow_ref=target.workflow_ref,
                workflow_label=label,
                work_dir=target.work_dir,
                workflow_family=workflow_family,
                report_path=str(report_path),
                markdown=markdown,
            )
        )

    return reports, warnings


def _load_summary_system_prompt(engine: AgentEngine, reports: Sequence[WorkflowReportInput]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        system_prompt = engine.read_prompt_template(WORKFLOW_SUMMARY_BASE_TEMPLATE)
    except FileNotFoundError as exc:
        return "", [str(exc)]

    override_sections: list[str] = []
    seen_families: set[str] = set()
    for report in reports:
        family = str(report.workflow_family or "").strip().lower()
        if not family or family in seen_families:
            continue
        seen_families.add(family)
        template_name = f"{WORKFLOW_SUMMARY_OVERRIDE_DIR}/{family}.md"
        try:
            override_sections.append(engine.read_prompt_template(template_name).strip())
        except FileNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - defensive path
            warnings.append(f"Couldn't load workflow summary override `{template_name}`: {exc}.")

    if override_sections:
        system_prompt = system_prompt.rstrip() + "\n\n## Workflow-Family Emphasis\n\n" + "\n\n".join(override_sections)

    return system_prompt, warnings


def build_summary_user_message(reports: Sequence[WorkflowReportInput], *, focus_text: str = "", warnings: Sequence[str] | None = None) -> str:
    warning_lines = [str(item).strip() for item in (warnings or []) if str(item).strip()]
    focus_block = focus_text.strip() or "No extra focus supplied. Infer the most useful comparison points from the reports themselves."

    parts = [
        "Compare the following saved workflow analysis markdown reports.",
        "Use only the supplied report content and metadata.",
        f"Focus guidance: {focus_block}",
    ]

    if warning_lines:
        parts.append("Pre-existing warnings to acknowledge if relevant:")
        parts.extend(f"- {warning}" for warning in warning_lines)

    for index, report in enumerate(reports, start=1):
        family = report.workflow_family or "unknown"
        parts.extend(
            [
                "",
                f"## Workflow Report {index}",
                f"Workflow reference: {report.workflow_ref}",
                f"Workflow label: {report.workflow_label}",
                f"Workflow directory: {report.work_dir}",
                f"Workflow family: {family}",
                f"Report path: {report.report_path}",
                "Report markdown:",
                report.markdown.strip(),
            ]
        )

    return "\n".join(parts).strip() + "\n"


def _fallback_summary(reports: Sequence[WorkflowReportInput], warnings: Sequence[str]) -> str:
    lines = [
        "### Workflow Summary Comparison",
        "",
        f"Found {len(reports)} saved analysis report(s) to compare.",
        "",
        "| Workflow | Workflow Family | Report Path |",
        "| --- | --- | --- |",
    ]
    for report in reports:
        lines.append(
            f"| {report.workflow_label} | {report.workflow_family or 'unknown'} | {report.report_path} |"
        )
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    lines.append("The dedicated summary prompt could not be completed, so this fallback only lists the available reports.")
    return "\n".join(lines)


async def summarize_workflow_reports(
    targets: Sequence[WorkflowSummaryTarget],
    *,
    model: str = "default",
    focus_text: str = "",
) -> WorkflowSummaryResult:
    reports, warnings = _load_workflow_reports(targets)
    if not reports:
        return WorkflowSummaryResult(markdown=_fallback_summary([], warnings), warnings=warnings, used_report_paths=[])

    engine = AgentEngine(model_key=model or "default")
    system_prompt, prompt_warnings = _load_summary_system_prompt(engine, reports)
    warnings.extend(prompt_warnings)
    if not system_prompt:
        return WorkflowSummaryResult(
            markdown=_fallback_summary(reports, warnings),
            warnings=warnings,
            used_report_paths=[report.report_path for report in reports],
        )

    user_message = build_summary_user_message(reports, focus_text=focus_text, warnings=warnings)
    llm_md, _usage = await run_in_threadpool(
        engine.run_custom_prompt,
        system_prompt,
        user_message,
        None,
    )
    llm_md = str(llm_md or "").strip()
    if not llm_md or llm_md.startswith("❌ Brain Freeze"):
        return WorkflowSummaryResult(
            markdown=_fallback_summary(reports, warnings),
            warnings=warnings,
            used_report_paths=[report.report_path for report in reports],
        )

    footer = ["", f"_Summarized {len(reports)} report(s) across {len(targets)} workflow target(s)._"]
    if warnings:
        footer.extend(["", "Warnings:"])
        footer.extend(f"- {warning}" for warning in warnings)

    markdown = llm_md.rstrip() + "\n" + "\n".join(footer)
    return WorkflowSummaryResult(
        markdown=markdown,
        warnings=warnings,
        used_report_paths=[report.report_path for report in reports],
    )


def save_workflow_summary_markdown(project_dir: str | None, markdown: str) -> tuple[str | None, str | None]:
    raw_project_dir = str(project_dir or "").strip()
    if not raw_project_dir:
        return None, "No project directory was available, so the workflow summary was not saved to disk."

    try:
        root = Path(raw_project_dir).expanduser().resolve()
    except OSError as exc:
        return None, f"Couldn't resolve the project directory for saving the workflow summary: {exc}."

    try:
        summaries_dir = root / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = summaries_dir / f"workflow-summary-{timestamp}.md"
        output_path.write_text(str(markdown or ""), encoding="utf-8")
        return str(output_path), None
    except OSError as exc:
        return None, f"Couldn't save the workflow summary markdown: {exc}."