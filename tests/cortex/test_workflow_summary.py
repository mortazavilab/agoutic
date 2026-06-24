from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cortex.workflow_summary import (
    WorkflowSummaryTarget,
    build_summary_user_message,
    find_latest_analysis_report,
    save_workflow_summary_markdown,
    summarize_workflow_reports,
)


def test_find_latest_analysis_report_prefers_newest_timestamp(tmp_path):
    workflow_dir = tmp_path / "workflow7"
    workflow_dir.mkdir()
    older = workflow_dir / "workflow7_20250101_010101_analysis.md"
    newer = workflow_dir / "workflow7_20250101_020202_analysis.md"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")

    assert find_latest_analysis_report(workflow_dir) == newer


def test_build_summary_user_message_includes_focus_and_reports():
    message = build_summary_user_message(
        [
            type(
                "Report",
                (),
                {
                    "workflow_ref": "workflow7",
                    "workflow_label": "workflow7",
                    "work_dir": "/tmp/workflow7",
                    "workflow_family": "dogme",
                    "report_path": "/tmp/workflow7/workflow7_20250101_020202_analysis.md",
                    "markdown": "### Analysis\n\n**Workflow key:** dogme",
                },
            )()
        ],
        focus_text="mapping and QC",
        warnings=["No saved analysis markdown was found for `workflow8`."],
    )

    assert "Focus guidance: mapping and QC" in message
    assert "No saved analysis markdown was found for `workflow8`." in message
    assert "Workflow Report 1" in message
    assert "workflow7" in message


@pytest.mark.asyncio
async def test_summarize_workflow_reports_uses_llm_and_appends_footer(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow7"
    workflow_dir.mkdir()
    report = workflow_dir / "workflow7_20250101_020202_analysis.md"
    report.write_text("### Analysis\n\n**Workflow key:** dogme\n", encoding="utf-8")

    fake_prompt = "base prompt"
    fake_run = AsyncMock(return_value=("### Compared\n\n| Workflow | Value |\n| --- | --- |\n| workflow7 | ok |", {"total_tokens": 1}))

    class _FakeEngine:
        def __init__(self, model_key="default"):
            self.model_name = model_key

        def read_prompt_template(self, name: str) -> str:
            return fake_prompt if name == "workflow_summary/system_prompt.md" else "override"

        def run_custom_prompt(self, system_prompt, user_message, conversation_history=None, temperature=0.1):
            assert system_prompt.startswith("base prompt")
            assert "workflow7_20250101_020202_analysis.md" in user_message
            assert "mapping" in user_message
            return "### Compared\n\n| Workflow | Value |\n| --- | --- |\n| workflow7 | ok |", {"total_tokens": 1}

    monkeypatch.setattr("cortex.workflow_summary.AgentEngine", _FakeEngine)

    result = await summarize_workflow_reports(
        [WorkflowSummaryTarget(workflow_ref="workflow7", work_dir=str(workflow_dir), workflow_family="dogme")],
        model="default",
        focus_text="mapping",
    )

    assert "### Compared" in result.markdown
    assert "Summarized 1 report(s) across 1 workflow target(s)." in result.markdown
    assert result.used_report_paths == [str(report)]


@pytest.mark.asyncio
async def test_summarize_workflow_reports_falls_back_when_prompt_missing(monkeypatch, tmp_path):
    workflow_dir = tmp_path / "workflow9"
    workflow_dir.mkdir()
    report = workflow_dir / "workflow9_20250101_020202_analysis.md"
    report.write_text("### Analysis\n", encoding="utf-8")

    class _FakeEngine:
        def __init__(self, model_key="default"):
            self.model_name = model_key

        def read_prompt_template(self, name: str) -> str:
            raise FileNotFoundError(name)

    monkeypatch.setattr("cortex.workflow_summary.AgentEngine", _FakeEngine)

    result = await summarize_workflow_reports(
        [WorkflowSummaryTarget(workflow_ref="workflow9", work_dir=str(workflow_dir))],
        model="default",
    )

    assert "Found 1 saved analysis report(s) to compare." in result.markdown
    assert str(report) in result.markdown


def test_save_workflow_summary_markdown_creates_summaries_dir(tmp_path):
    path, warning = save_workflow_summary_markdown(str(tmp_path), "# Summary\n")

    assert warning is None
    assert path is not None
    assert (tmp_path / "summaries").is_dir()
    assert (tmp_path / "summaries" / Path(path).name).read_text(encoding="utf-8") == "# Summary\n"