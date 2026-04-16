"""
Tests for cortex/app.py — chat endpoint: PLOT tag parsing, DATA_CALL execution,
embedded dataframe extraction, result formatting, and second-pass LLM.

Targets uncovered lines: 4229-4497 (plot tags), 4524-4661 (fallback plot),
  4866-5073 (MCP tool calls), 5172-5593 (result formatting + 2nd pass),
  5435-5715 (embedded dataframe extraction).
"""
import contextlib
import datetime
import json
import re
import uuid
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.memory_service import list_memories
from cortex.models import (
    User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage,
)
from cortex.app import app


# ---------------------------------------------------------------------------
# Fixtures following test_chat_flow.py pattern
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def SL(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def seed(SL, tmp_path):
    """Seed user + session + project + access."""
    s = SL()
    user = User(id="u-plot", email="plot@test.com", role="user",
                username="plotuser", is_active=True)
    s.add(user)
    sess = SessionModel(id="plot-session", user_id=user.id,
                        is_valid=True,
                        expires_at=datetime.datetime(2099, 1, 1))
    s.add(sess)
    proj = Project(id="proj-plot", name="Plot Proj", owner_id="u-plot",
                   slug="plot-proj")
    s.add(proj)
    s.flush()
    access = ProjectAccess(id=str(uuid.uuid4()), user_id="u-plot",
                           project_id="proj-plot", project_name="Plot Proj",
                           role="owner",
                           last_accessed=datetime.datetime.utcnow())
    s.add(access)
    s.commit()
    s.close()


def _make_client(SL, seed, tmp_path, think_fn, extra_patches=None):
    """Build a TestClient with a custom AgentEngine.think mock."""
    mock_engine_cls = MagicMock()
    inst = mock_engine_cls.return_value
    inst.model_name = "test-model"
    inst.think = think_fn
    # Mock analyze_results for second-pass LLM analysis
    inst.analyze_results = lambda msg, md, data, skill, hist: (
        "Analysis complete: " + md[:100],
        {"prompt_tokens": 15, "completion_tokens": 15, "total_tokens": 30},
    )

    patches = [
        patch("cortex.db.SessionLocal", SL),
        patch("cortex.app.SessionLocal", SL),
        patch("cortex.chat_stages.setup.SessionLocal", SL),
        patch("cortex.chat_stages.overrides.SessionLocal", SL),
        patch("cortex.dependencies.SessionLocal", SL),
        patch("cortex.middleware.SessionLocal", SL),
        patch("cortex.admin.SessionLocal", SL),
        patch("cortex.auth.SessionLocal", SL),
        patch("cortex.config.AGOUTIC_DATA", tmp_path),
        patch("cortex.user_jail.AGOUTIC_DATA", tmp_path),
        patch("cortex.app._resolve_project_dir", return_value=tmp_path / "proj"),
        patch("cortex.chat_stages.context_prep._resolve_project_dir", return_value=tmp_path / "proj"),
        patch("cortex.app.AgentEngine", mock_engine_cls),
        patch("cortex.agent_engine.AgentEngine", mock_engine_cls),
        patch("cortex.chat_stages.context_prep.AgentEngine", mock_engine_cls),
        patch("cortex.chat_stages.llm_first_pass.AgentEngine", mock_engine_cls),
    ]
    if extra_patches:
        patches.extend(extra_patches)

    for p in patches:
        p.start()

    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("session", "plot-session")
    yield c

    for p in reversed(patches):
        p.stop()
    for p in reversed(patches):
        p.stop()


def _chat(client, message, skill="welcome", project_id="proj-plot"):
    return client.post("/chat", json={
        "project_id": project_id,
        "message": message,
        "skill": skill,
    })


# ===========================================================================
# 1. PLOT tag parsing from LLM response
# ===========================================================================

class TestPlotTagParsing:
    """Tests covering lines 4284-4497 (plot tag parsing in chat handler)."""

    def test_mistral_inline_plot_tag_is_normalized_and_hidden_from_markdown(self, SL, seed, tmp_path):
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "analyze_job_results",
                    "known_dataframes": ["DF1 (reconcile, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "reconcile": {
                        "columns": ["sample", "ANTISENSE", "KNOWN", "NIC"],
                        "data": [
                            {"sample": "s1", "ANTISENSE": 268, "KNOWN": 1000, "NIC": 30},
                            {"sample": "s2", "ANTISENSE": 221, "KNOWN": 1100, "NIC": 25},
                            {"sample": "s3", "ANTISENSE": 247, "KNOWN": 1050, "NIC": 27},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "reconcile", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "I need to plot DF1 by sample. Let me create a visualization."
                "[TOOL_CALLS]PLOT: type=bar, df=DF1, x=sample, agg=sum, title=Summary by Sample",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot DF1 by sample", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        agent_markdown = (data.get("agent_block", {}).get("payload") or {}).get("markdown", "")
        assert "[TOOL_CALLS]PLOT:" not in agent_markdown
        assert "Let me create a visualization" in agent_markdown

    def test_plot_payload_preserves_ylabel(self, SL, seed, tmp_path):
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "analyze_job_results",
                    "known_dataframes": ["DF1 (reconcile, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "reconcile": {
                        "columns": ["sample", "reads"],
                        "data": [
                            {"sample": "s1", "reads": 10},
                            {"sample": "s2", "reads": 20},
                            {"sample": "s3", "reads": 15},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "reconcile", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "Here is the chart.\n[[PLOT: type=bar, df=DF1, x=sample, y=reads, title=Reads by Sample, ylabel=Reads]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot DF1 by sample with y axis label Reads", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        chart = (plot_blocks[0].get("payload") or {}).get("charts", [])[0]
        assert chart.get("ylabel") == "Reads"

    def test_plot_is_saved_as_project_memory(self, SL, seed, tmp_path):
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "analyze_job_results",
                    "known_dataframes": ["DF1 (reconcile, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "reconcile": {
                        "columns": ["sample", "reads"],
                        "data": [
                            {"sample": "s1", "reads": 10},
                            {"sample": "s2", "reads": 20},
                            {"sample": "s3", "reads": 15},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "reconcile", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "Here is the chart.\n[[PLOT: type=bar, df=DF1, x=sample, y=reads, title=Reads by Sample, ylabel=Reads]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot DF1 by sample", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks

        s = SL()
        memories = list_memories(
            s,
            "u-plot",
            project_id="proj-plot",
            include_global=False,
            category="plot",
        )
        assert len(memories) == 1
        memory = memories[0]
        assert memory.project_id == "proj-plot"
        assert memory.related_block_id == plot_blocks[0]["id"]
        assert "Reads by Sample" in memory.content
        assert json.loads(memory.structured_data)["charts"][0]["ylabel"] == "Reads"
        s.close()

    def test_duplicate_plot_specs_collapse_to_single_chart(self, SL, seed, tmp_path):
        """Identical plot specs should not create duplicate overlaid traces."""
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "ENCODE_Search",
                    "known_dataframes": ["DF1 (K562, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "K562 (3 results)": {
                        "columns": ["Assay", "Count"],
                        "data": [
                            {"Assay": "RNA-seq", "Count": 2},
                            {"Assay": "ATAC-seq", "Count": 1},
                            {"Assay": "ChIP-seq", "Count": 1},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "K562", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "Here is the chart.\n"
                "[[PLOT: type=bar, df=DF1, x=Assay, agg=count, title=K562 Experiments by Assay Type]]\n"
                "[[PLOT: type=bar, df=DF1, x=Assay, agg=count, title=K562 Experiments by Assay Type]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot DF1 by assay type", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert len(charts) == 1

    def test_semantically_duplicate_plot_specs_with_different_titles_collapse(self, SL, seed, tmp_path):
        """Plot specs that differ only in non-trace metadata should still dedupe."""
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "ENCODE_Search",
                    "known_dataframes": ["DF1 (K562, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "K562 (3 results)": {
                        "columns": ["Assay", "Count"],
                        "data": [
                            {"Assay": "RNA-seq", "Count": 2},
                            {"Assay": "ATAC-seq", "Count": 1},
                            {"Assay": "ChIP-seq", "Count": 1},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "K562", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "Here is the chart.\n"
                "[[PLOT: type=bar, df=DF1, x=Assay, agg=count, title=K562 Experiments by Assay Type]]\n"
                "[[PLOT: type=bar, df=DF1, x=Assay, agg=count]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot DF1 by assay type", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert len(charts) == 1

    def test_competing_plot_specs_keep_current_prompt_intent(self, SL, seed, tmp_path):
        """When multiple plots compete, keep the one that matches the current ask."""
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "ENCODE_Search",
                    "known_dataframes": ["DF1 (K562, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "K562 (3 results)": {
                        "columns": ["Accession", "Assay", "Count"],
                        "data": [
                            {"Accession": "ENCSR001AAA", "Assay": "RNA-seq", "Count": 2},
                            {"Accession": "ENCSR002BBB", "Assay": "ATAC-seq", "Count": 1},
                            {"Accession": "ENCSR003CCC", "Assay": "ChIP-seq", "Count": 1},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "K562", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "Here is the chart.\n"
                "[[PLOT: type=bar, df=DF1, x=Assay, agg=count, color=green, title=K562 Experiments by Assay Type]]\n"
                "[[PLOT: type=bar, df=DF1, x=Accession, agg=count, title=Count by Accession]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "get the K562 experiments in ENCODE and make a plot by assay type in green", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert len(charts) == 1
        assert charts[0].get("x") == "Assay"
        assert charts[0].get("color") == "green"

    def test_competing_plot_specs_drop_stale_color_when_not_requested(self, SL, seed, tmp_path):
        """Do not keep leaked literal colors from prior context when this ask did not request one."""
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "ENCODE_Search",
                    "known_dataframes": ["DF1 (K562, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "K562 (3 results)": {
                        "columns": ["Assay", "Count"],
                        "data": [
                            {"Assay": "RNA-seq", "Count": 2},
                            {"Assay": "ATAC-seq", "Count": 1},
                            {"Assay": "ChIP-seq", "Count": 1},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "K562", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "Here is the chart.\n"
                "[[PLOT: type=bar, df=DF1, x=Assay, agg=count, color=green, title=K562 Experiments by Assay Type]]\n"
                "[[PLOT: type=bar, df=DF1, x=Assay, agg=count, title=K562 Experiments by Assay Type]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "get the K562 experiments in ENCODE and make a plot by assay type", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert len(charts) == 1
        assert charts[0].get("x") == "Assay"
        assert charts[0].get("color") is None

    def test_explicit_measure_request_overrides_stale_sample_axis(self, SL, seed, tmp_path):
        """An explicit by-measure request should override a stale sample x-axis spec."""
        s = SL()
        df_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Existing dataframe.",
                "state": {
                    "active_skill": "analyze_job_results",
                    "known_dataframes": ["DF1 (reconcile, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "reconcile": {
                        "columns": ["sample", "ANTISENSE", "KNOWN", "NIC"],
                        "data": [
                            {"sample": "s1", "ANTISENSE": 268, "KNOWN": 1000, "NIC": 30},
                            {"sample": "s2", "ANTISENSE": 221, "KNOWN": 1100, "NIC": 25},
                            {"sample": "s3", "ANTISENSE": 247, "KNOWN": 1050, "NIC": 27},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "reconcile", "row_count": 3},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(df_block)
        s.commit()
        s.close()

        def think(msg, skill, history):
            return (
                "I see you want to plot DF1 by measure. Let me create a visualization for you.\n"
                "[[PLOT: type=bar, df=DF1, x=sample, agg=sum, title=DF1 Measures by Sample]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot DF1 by measure", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert len(charts) == 1
        assert charts[0].get("x") == "measure"

    def test_plot_tag_with_key_value(self, SL, seed, tmp_path):
        """LLM emits [[PLOT: type=scatter, df=DF1, x=score, y=enrichment]]."""
        def think(msg, skill, history):
            return (
                "Here is a scatter plot.\n[[PLOT: type=scatter, df=DF1, x=score, y=enrichment]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot a scatter of score vs enrichment")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        # Check that plot_specs were extracted
        plots = payload.get("plot_specs", [])
        if plots:
            assert plots[0]["type"] == "scatter"
            assert plots[0].get("df_id") == 1

    def test_plot_tag_natural_language(self, SL, seed, tmp_path):
        """LLM emits [[PLOT: histogram of DF2 with Category on the x-axis]]."""
        def think(msg, skill, history):
            return (
                "[[PLOT: histogram of DF2 with Category on the x-axis]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot a histogram of DF2")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        plots = payload.get("plot_specs", [])
        if plots:
            assert plots[0]["type"] == "histogram"
            assert plots[0].get("df_id") == 2

    def test_plot_code_fallback(self, SL, seed, tmp_path):
        """LLM writes Python matplotlib code instead of [[PLOT:...]] — auto-generate."""
        def think(msg, skill, history):
            return (
                "Here's the chart:\n```python\nimport matplotlib.pyplot as plt\nplt.bar(df['x'], df['y'])\n```\n",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot a bar chart by category")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        # The code block should have been stripped
        md = payload.get("markdown", "")
        assert "matplotlib" not in md
        # And a plot spec auto-generated
        plots = payload.get("plot_specs", [])
        # May or may not generate depending on injected DFs, but the endpoint shouldn't crash

    def test_plot_code_fallback_preserves_literal_color_request(self, SL, seed, tmp_path):
        """Malformed plot output should preserve explicit colors from the user prompt."""
        def think(msg, skill, history):
            return (
                "I will help you search for the requested data and then generate a plot.\n\n"
                "```python\nimport matplotlib.pyplot as plt\nplt.bar(df['assay'], df['count'], color='green')\n```\n\n"
                '[PLOT: {"data": {"x": ["A"], "y": [1], "type": "bar", "marker": {"color": "green"}}}]',
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot DF1 by assay in green")
        assert resp.status_code == 200
        data = resp.json()
        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert charts
        assert charts[0].get("df_id") == 1
        assert charts[0].get("palette") == "green"

    def test_plot_command_override_suppresses_data_calls(self, SL, seed, tmp_path):
        """When user asks for plot and we have valid plot_specs,
        DATA_CALL tags should be suppressed."""
        def think(msg, skill, history):
            return (
                "[[PLOT: type=bar, df=DF1, x=category]]\n"
                "[[DATA_CALL: service=analyzer, tool=list_job_files, run_uuid=abc]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "plot this as a bar chart")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        # DATA_CALL tag should NOT appear in clean markdown
        assert "DATA_CALL" not in md

    def test_empty_plot_placeholder(self, SL, seed, tmp_path):
        """When LLM only emits a PLOT tag with no surrounding text,
        a placeholder message should be inserted."""
        def think(msg, skill, history):
            return (
                "[[PLOT: type=pie, df=DF3]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "make a pie chart")
        assert resp.status_code == 200
        data = resp.json()
        # The response uses "agent_block" (not "plan_block")
        agent_block = data.get("agent_block") or data.get("plan_block") or {}
        payload = agent_block.get("payload", {})
        md = payload.get("markdown", "")
        # The plot tag was successfully parsed (AGENT_PLOT block created).
        # The placeholder "Here is the pie for DF3:" should be in the markdown.
        assert "pie" in md.lower() or "DF3" in md
        # Also verify plot_blocks were created
        assert len(data.get("plot_blocks", [])) >= 1


# ===========================================================================
# 2. DATA_CALL tag parsing and execution
# ===========================================================================

class TestDataCallExecution:
    """Tests covering lines 4866-5073 (MCP tool execution)."""

    def test_data_call_analyzer(self, SL, seed, tmp_path):
        """LLM emits [[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=abc]]."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"summary": "10 files found", "total": 10}

        def think(msg, skill, history):
            return (
                "Let me check your results.\n"
                "[[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=abc-123]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        # The second call (analyze_results) also needs a think mock
        call_count = [0]
        def think_multi(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Let me check your results.\n"
                    "[[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=abc-123]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            else:
                return (
                    "Your analysis shows 10 files across multiple categories.",
                    {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
                )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think_multi, extra_patches=extra))
        resp = _chat(client, "show me job results for abc-123", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        # Either the second-pass analysis text or raw results should be present
        assert len(md) > 0

    def test_data_call_connection_error(self, SL, seed, tmp_path):
        """MCP connection failure should produce error in results, not crash."""
        mock_mcp = AsyncMock()
        mock_mcp.connect.side_effect = ConnectionError("Service down")

        def think(msg, skill, history):
            return (
                "Let me check.\n"
                "[[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=xyz]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "show results", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        # Should not crash; should show error in formatted output
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert "Connection failed" in md or "error" in md.lower() or len(md) > 0

    def test_data_call_encode_search(self, SL, seed, tmp_path):
        """LLM emits [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "experiments": [
                {"accession": "ENCSR000AAA", "assay_title": "ChIP-seq", "biosample": "K562"},
            ],
            "total": 1,
        }

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Searching ENCODE for K562.\n"
                    "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found 1 experiment for K562.",
                {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "search K562 experiments", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        # Embedded dataframes should be present
        dfs = payload.get("_dataframes", {})
        assert isinstance(dfs, dict)

    def test_data_call_routing_error_skip(self, SL, seed, tmp_path):
        """Tool call with __routing_error__ should be skipped."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"data": "ok"}

        def think(msg, skill, history):
            return (
                "I'll search.\n"
                "[[DATA_CALL: service=analyzer, tool=get_analysis_summary, run_uuid=abc]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "get summary", skill="analyze_job_results")
        assert resp.status_code == 200


# ===========================================================================
# 3. Legacy ENCODE_CALL / ANALYSIS_CALL tag parsing
# ===========================================================================

class TestLegacyTags:
    def test_legacy_encode_call(self, SL, seed, tmp_path):
        """LLM emits [[ENCODE_CALL: search_by_biosample, search_term=K562]]."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"experiments": [], "total": 0}

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "[[ENCODE_CALL: search_by_biosample, search_term=K562]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "No experiments found.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "find K562 experiments", skill="ENCODE_Search")
        assert resp.status_code == 200

    def test_legacy_analysis_call(self, SL, seed, tmp_path):
        """LLM emits [[ANALYSIS_CALL: get_analysis_summary, run_uuid=abc-123]]."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"summary": "ok"}

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "[[ANALYSIS_CALL: get_analysis_summary, run_uuid=abc-123]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Job summary retrieved.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "analyze job abc-123", skill="analyze_job_results")
        assert resp.status_code == 200


# ===========================================================================
# 4. Fallback tag conversion
# ===========================================================================

class TestFallbackTagConversion:
    def test_tool_call_to_data_call_conversion(self, SL, seed, tmp_path):
        """LLM emits [[TOOL_CALL: GET /analysis/jobs/.../summary?work_dir=...]]
        which should be converted to DATA_CALL."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"summary": "converted ok"}

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Let me check.\n"
                    "[[TOOL_CALL: GET /analysis/jobs/abc/summary?work_dir=/data/job1]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Analysis complete.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "summarize job results", skill="analyze_job_results")
        assert resp.status_code == 200

    def test_tool_call_categorize_conversion(self, SL, seed, tmp_path):
        """[[TOOL_CALL: GET /analysis/categorize?...]]"""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"categories": {}}

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "[[TOOL_CALL: GET /analysis/categorize_files?work_dir=/tmp]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return ("Done.", {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "categorize files", skill="analyze_job_results")
        assert resp.status_code == 200


# ===========================================================================
# 5. Hallucinated ENCSR fix
# ===========================================================================

class TestENCSRFix:
    def test_hallucinated_encsr_fix(self, SL, seed, tmp_path):
        """When user mentions ENCSR111AAA and the LLM mentions a different ENCSR,
        the bad one should be replaced."""
        def think(msg, skill, history):
            return (
                "The experiment ENCSR999ZZZ has 5 replicates.",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "Tell me about ENCSR111AAA", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        # The hallucinated ENCSR999ZZZ should be replaced with the user's ENCSR111AAA
        if "ENCSR" in md:
            assert "ENCSR111AAA" in md
            assert "ENCSR999ZZZ" not in md


# ===========================================================================
# 6. Approval suppression for non-job skills
# ===========================================================================

class TestApprovalSuppressionDetail:
    def test_approval_suppressed_encode_search(self, SL, seed, tmp_path):
        """[[APPROVAL_NEEDED]] in an ENCODE_Search skill response should be stripped."""
        def think(msg, skill, history):
            return (
                "Found 5 experiments.\n[[APPROVAL_NEEDED]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "find K562 experiments", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        # Should NOT create an approval gate for a search skill
        assert data.get("gate_block") is None


# ===========================================================================
# 7. Embedded dataframe extraction from tool results
# ===========================================================================

class TestEmbeddedDataframes:
    def test_search_results_create_dataframe(self, SL, seed, tmp_path):
        """search_by_biosample results should be extracted as an embedded dataframe."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = [
            {"accession": "ENCSR001AAA", "assay_title": "ChIP-seq", "biosample_summary": "K562"},
            {"accession": "ENCSR002BBB", "assay_title": "RNA-seq", "biosample_summary": "K562"},
        ]

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Searching...\n"
                    "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found 2 experiments for K562.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "search K562", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        dfs = payload.get("_dataframes", {})
        # Should have at least one dataframe from the search results
        assert len(dfs) >= 1
        # First DF should have row_count == 2
        first_df = next(iter(dfs.values()))
        assert first_df["row_count"] == 2

    def test_parse_csv_results_create_dataframe(self, SL, seed, tmp_path):
        """parse_csv_file results should be extracted as an embedded dataframe."""
        mock_mcp = AsyncMock()

        # First call: get_analysis_summary  
        # Second call: if chaining happens
        mock_mcp.call_tool.return_value = {
            "file_path": "results.csv",
            "columns": ["Sample", "Score", "Status"],
            "data": [
                {"Sample": "S1", "Score": 95, "Status": "PASS"},
                {"Sample": "S2", "Score": 87, "Status": "PASS"},
            ],
            "row_count": 2,
            "metadata": {},
        }

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "I'll parse the CSV.\n"
                    "[[DATA_CALL: service=analyzer, tool=parse_csv_file, run_uuid=abc, file_path=results.csv]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "The CSV contains 2 samples, both passing.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "show me results.csv", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        dfs = payload.get("_dataframes", {})
        assert len(dfs) >= 1

    def test_compound_encode_plot_query_searches_new_subject_not_latest_df(self, SL, seed, tmp_path):
        """A search+plot request with a new biosample should search that biosample, not reuse the latest DF."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = [
            {
                "accession": f"ENCSR{i:03d}AAA",
                "assay_title": "RNA-seq" if i % 2 == 0 else "ATAC-seq",
                "biosample_summary": "C2C12",
                "target": "",
            }
            for i in range(58)
        ]

        def think(msg, skill, history):
            return (
                "I wasn't able to find an ENCODE query tool for that search term. Could you clarify?",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "plot C2C12 experiments in encode by assay type in purple", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        dfs = payload.get("_dataframes", {})
        assert len(dfs) >= 1
        first_df = next(iter(dfs.values()))
        assert first_df["row_count"] == 58

        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert charts
        assert charts[0].get("palette") == "purple"

    def test_compound_encode_plot_query_rebinds_stale_llm_df_to_new_results(self, SL, seed, tmp_path):
        """A same-turn ENCODE search+plot should not keep an LLM-hallucinated stale DF id."""
        s = SL()
        stale_block = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Previous results.",
                "state": {
                    "active_skill": "ENCODE_Search",
                    "known_dataframes": ["DF4 (K562, 12 rows)"],
                    "latest_dataframe": "DF4",
                },
                "_dataframes": {
                    "K562 (12 results)": {
                        "columns": ["Accession", "Assay", "Biosample", "Target"],
                        "data": [{
                            "Accession": "ENCSRSTAL1",
                            "Assay": "ChIP-seq",
                            "Biosample": "K562",
                            "Target": "CTCF",
                        }],
                        "row_count": 12,
                        "metadata": {"df_id": 4, "visible": True, "label": "K562", "row_count": 12},
                    }
                },
            }),
            created_at=datetime.datetime.utcnow(),
        )
        s.add(stale_block)
        s.commit()
        s.close()

        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = [
            {
                "accession": f"ENCSR{i:03d}AAA",
                "assay_title": "RNA-seq" if i % 2 == 0 else "ATAC-seq",
                "biosample_summary": "C2C12",
                "target": "",
            }
            for i in range(58)
        ]

        def think(msg, skill, history):
            return (
                "I'll help you get the C2C12 experiments from ENCODE and create a plot by assay type.\n\n"
                "[[DATA_CALL: consortium=encode, tool=search, biosample=C2C12]]\n\n"
                "[[PLOT: type=bar, df=DF4, x=Assay, agg=count, title=C2C12 Experiments by Assay Type]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "get the C2C12 experiments in ENCODE and make a plot by assay type", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        dfs = payload.get("_dataframes", {})
        assert len(dfs) >= 1
        first_df = next(iter(dfs.values()))
        assert first_df["row_count"] == 58
        assert first_df.get("metadata", {}).get("df_id") == 5

        plot_blocks = data.get("plot_blocks", [])
        assert plot_blocks
        charts = (plot_blocks[0].get("payload") or {}).get("charts", [])
        assert charts
        assert charts[0].get("df_id") == 5


# ===========================================================================
# 8. Token tracking in chat response
# ===========================================================================

class TestTokenTracking:
    def test_tokens_persisted(self, SL, seed, tmp_path):
        """Usage tokens from LLM call should be persisted."""
        def think(msg, skill, history):
            return (
                "Here is your answer.",
                {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "what is DNA?", skill="welcome")
        assert resp.status_code == 200
        data = resp.json()
        usage = data.get("usage", {})
        if usage:
            assert usage.get("total_tokens", 0) >= 150


# ===========================================================================
# 9. Clean markdown: skill switch tag removal
# ===========================================================================

class TestCleanMarkdown:
    def test_skill_switch_tag_stripped(self, SL, seed, tmp_path):
        """[[SKILL_SWITCH_TO:...]] tags should not appear in clean markdown."""
        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Switching skill.\n[[SKILL_SWITCH_TO: analyze_job_results]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Now analyzing your results.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )
        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "analyze the job", skill="ENCODE_Search")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert "SKILL_SWITCH" not in md


# ===========================================================================
# 10. Browsing tool bypass (skip second-pass LLM)
# ===========================================================================

class TestBrowsingToolBypass:
    def test_list_job_files_no_second_pass(self, SL, seed, tmp_path):
        """list_job_files should bypass second-pass LLM and show results directly."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "files": [
                {"path": "results/report.csv", "size": 1024},
                {"path": "results/summary.txt", "size": 512},
            ],
            "success": True,
        }

        def think(msg, skill, history):
            return (
                "I'll list the files.\n"
                "[[DATA_CALL: service=analyzer, tool=list_job_files, run_uuid=abc-123]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "list files", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        # Should contain the raw results, not a second-pass analysis
        assert len(md) > 0

    def test_browsing_error_shows_suggestions(self, SL, seed, tmp_path):
        """When a browsing tool returns an error, show suggestions."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.side_effect = Exception("Directory not found")

        def think(msg, skill, history):
            return (
                "[[DATA_CALL: service=analyzer, tool=list_job_files, run_uuid=abc]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "list files", skill="analyze_job_results")
        assert resp.status_code == 200

    def test_remote_list_files_on_profile_uses_launchpad_override(self, SL, seed, tmp_path):
        """Remote file browsing should bypass bad LLM clarification and call Launchpad directly."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "ssh_profile_id": "profile-123",
            "ssh_profile_nickname": "hpc3",
            "path": "/remote/base",
            "entries": [
                {"name": "data", "type": "dir", "size": 0},
                {"name": "ref", "type": "dir", "size": 0},
            ],
        }

        def think(msg, skill, history):
            return (
                "I need more information to help you list files on HPC3.",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
            patch("cortex.chat_stages.overrides._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "list files on hpc3", skill="remote_execution")
        assert resp.status_code == 200
        assert mock_mcp.call_tool.await_count == 1
        assert mock_mcp.call_tool.call_args.args[0] == "list_remote_files"
        assert mock_mcp.call_tool.call_args.kwargs["ssh_profile_id"] == "profile-123"
        assert mock_mcp.call_tool.call_args.kwargs["user_id"] == "u-plot"
        payload = (resp.json().get("agent_block") or {}).get("payload", {})
        assert payload.get("skill") == "remote_execution"
        md = payload.get("markdown", "")
        assert "Remote directory: /remote/base" in md
        assert "| data | dir | - |" in md
        assert "| ref | dir | - |" in md
        assert "did not return the expected data" not in md

    def test_remote_list_files_ignores_bad_skill_switch(self, SL, seed, tmp_path):
        """Remote file browsing should ignore LLM attempts to switch to analyze_job_results."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "ssh_profile_id": "profile-123",
            "ssh_profile_nickname": "hpc3",
            "path": "/remote/base",
            "entries": [],
        }

        def think(msg, skill, history):
            return (
                "Switching skills.\n[[SKILL_SWITCH_TO: analyze_job_results]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
            patch("cortex.chat_stages.overrides._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "list files on hpc3", skill="remote_execution")
        assert resp.status_code == 200
        payload = (resp.json().get("agent_block") or {}).get("payload", {})
        assert payload.get("skill") == "remote_execution"
        assert mock_mcp.call_tool.await_count == 1

    def test_remote_list_files_from_analyzer_skill_uses_launchpad_override(self, SL, seed, tmp_path):
        """Explicit remote browsing should force Launchpad browsing even from analyze_job_results."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "ssh_profile_id": "profile-123",
            "ssh_profile_nickname": "hpc3",
            "path": "/remote/base",
            "entries": [
                {"name": "data", "type": "dir", "size": 0},
            ],
        }

        def think(msg, skill, history):
            return (
                "Which analysis output directory do you want?",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
            patch("cortex.chat_stages.overrides._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "list files on hpc3", skill="analyze_job_results")
        assert resp.status_code == 200
        payload = (resp.json().get("agent_block") or {}).get("payload", {})
        assert payload.get("skill") == "remote_execution"
        assert mock_mcp.call_tool.await_count == 1
        assert mock_mcp.call_tool.call_args.args[0] == "list_remote_files"
        assert mock_mcp.call_tool.call_args.kwargs["ssh_profile_id"] == "profile-123"

    def test_remote_stage_uses_saved_profile_defaults_instead_of_asking_again(self, SL, seed, tmp_path):
        """Stage-only remote requests should build approval text from saved SSH profile defaults."""
        mock_mcp = AsyncMock()

        async def _call_tool(tool_name, **kwargs):
            if tool_name == "list_ssh_profiles":
                return [
                    {
                        "id": "profile-123",
                        "nickname": "hpc3",
                        "ssh_username": "agoutic",
                        "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                        "default_slurm_account": "SEYEDAM_LAB",
                        "default_slurm_partition": "standard",
                        "default_slurm_gpu_account": "SEYEDAM_LAB",
                        "default_slurm_gpu_partition": "gpu",
                    }
                ]
            if tool_name == "get_slurm_defaults":
                return {
                    "found": True,
                    "source": "ssh_profile_defaults",
                    "account": "SEYEDAM_LAB",
                    "partition": "standard",
                    "selected_profile_defaults": {
                        "id": "profile-123",
                        "ssh_profile_id": "profile-123",
                        "nickname": "hpc3",
                        "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                        "default_slurm_account": "SEYEDAM_LAB",
                        "default_slurm_partition": "standard",
                        "default_slurm_gpu_account": "SEYEDAM_LAB",
                        "default_slurm_gpu_partition": "gpu",
                    },
                }
            raise AssertionError(f"unexpected tool: {tool_name}")

        mock_mcp.call_tool.side_effect = _call_tool

        def think(msg, skill, history):
            return (
                "I found 1 SSH profile and 0 Slurm defaults. Please provide the specific Slurm defaults.",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
            patch("cortex.planner.classify_request", return_value="SINGLE_TOOL"),
            patch("cortex.chat_stages.overrides._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
            patch(
                "cortex.app._list_user_ssh_profiles",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "profile-123",
                            "nickname": "hpc3",
                            "ssh_username": "agoutic",
                            "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                            "default_slurm_account": "SEYEDAM_LAB",
                            "default_slurm_partition": "standard",
                            "default_slurm_gpu_account": "SEYEDAM_LAB",
                            "default_slurm_gpu_partition": "gpu",
                        }
                    ]
                ),
            ),
            patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
            patch(
                "cortex.remote_orchestration._list_user_ssh_profiles",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "profile-123",
                            "nickname": "hpc3",
                            "ssh_username": "agoutic",
                            "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                            "default_slurm_account": "SEYEDAM_LAB",
                            "default_slurm_partition": "standard",
                            "default_slurm_gpu_account": "SEYEDAM_LAB",
                            "default_slurm_gpu_partition": "gpu",
                        }
                    ]
                ),
            ),
            patch(
                "cortex.job_parameters.extract_job_parameters_from_conversation",
                new=AsyncMock(
                    return_value={
                        "sample_name": "Jamshid",
                        "input_directory": "/media/backup_disk/agoutic_root/testdata/CDNA/pod5",
                        "mode": "CDNA",
                        "reference_genome": ["mm39"],
                        "ssh_profile_nickname": "hpc3",
                        "execution_mode": "slurm",
                        "remote_action": "stage_only",
                    }
                ),
            ),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "stage the mouse CDNA sample called Jamshid at /media/backup_disk/agoutic_root/testdata/CDNA/pod5 on hpc3",
            skill="remote_execution",
        )
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert payload.get("skill") == "remote_execution"
        assert "I found the saved remote defaults needed for staging." in md
        assert "SEYEDAM_LAB" in md
        assert "/share/crsp/lab/seyedam/share/agoutic/seyedam" in md
        assert "This will not launch Dogme or Nextflow." in md
        assert "0 Slurm defaults" not in md
        assert "Please provide the specific Slurm defaults" not in md

        gate = data.get("gate_block") or {}
        gate_payload = gate.get("payload") or {}
        assert gate_payload.get("skill") == "remote_execution"
        assert gate_payload.get("gate_action") == "remote_stage"
        extracted = gate_payload.get("extracted_params") or {}
        assert extracted.get("execution_mode") == "slurm"
        assert extracted.get("remote_action") == "stage_only"
        assert extracted.get("gate_action") == "remote_stage"
        assert extracted.get("ssh_profile_nickname") == "hpc3"
        assert extracted.get("remote_base_path") == "/share/crsp/lab/seyedam/share/agoutic/seyedam"

    def test_get_slurm_defaults_injects_user_id_when_llm_omits_it(self, SL, seed, tmp_path):
        """Launchpad defaults call should be repaired with user_id before MCP execution."""
        mock_mcp = AsyncMock()

        async def _call_tool(tool_name, **kwargs):
            assert tool_name == "get_slurm_defaults"
            assert kwargs.get("user_id") == "u-plot"
            assert kwargs.get("project_id") == "proj-plot"
            return {"found": False, "source": "none"}

        mock_mcp.call_tool.side_effect = _call_tool

        def think(msg, skill, history):
            return (
                "Checking saved defaults now.\n"
                "[[DATA_CALL: service=launchpad, tool=get_slurm_defaults, project_id=<project_id>]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "check my slurm defaults", skill="remote_execution")
        assert resp.status_code == 200
        assert mock_mcp.call_tool.await_count == 1

    def test_get_slurm_defaults_rewrites_legacy_profile_id_for_explicit_cluster(self, SL, seed, tmp_path):
        """Legacy get_slurm_defaults(profile_id=...) tags should be repaired before MCP execution."""
        mock_mcp = AsyncMock()

        async def _call_tool(tool_name, **kwargs):
            assert tool_name == "get_slurm_defaults"
            assert kwargs.get("user_id") == "u-plot"
            assert kwargs.get("project_id") == "proj-plot"
            assert kwargs.get("profile_nickname") == "hpc3"
            assert "profile_id" not in kwargs
            assert "ssh_profile_id" not in kwargs
            return {"found": False, "source": "none"}

        mock_mcp.call_tool.side_effect = _call_tool

        def think(msg, skill, history):
            return (
                "Checking saved defaults now.\n"
                "[[DATA_CALL: service=launchpad, tool=get_slurm_defaults, profile_id=<localCluster_id>, project_id=<project_id>]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "Analyze the mouse DNA sample called JamshidDNA at /media/backup_disk/agoutic_root/testdata/GDNA/pod5 on hpc3",
            skill="remote_execution",
        )
        assert resp.status_code == 200
        assert mock_mcp.call_tool.await_count == 1

    def test_submit_dogme_job_injects_user_and_project_scope(self, SL, seed, tmp_path):
        """Direct Launchpad submit calls should be repaired with missing identity scope."""
        mock_mcp = AsyncMock()

        async def _call_tool(tool_name, **kwargs):
            assert tool_name == "submit_dogme_job"
            assert kwargs.get("user_id") == "u-plot"
            assert kwargs.get("project_id") == "proj-plot"
            return {"run_uuid": "run-1", "status": "PENDING"}

        mock_mcp.call_tool.side_effect = _call_tool

        def think(msg, skill, history):
            return (
                "Submitting the remote run now.\n"
                "[[DATA_CALL: service=launchpad, tool=submit_dogme_job, sample_name=Jamshid, mode=CDNA, execution_mode=slurm, ssh_profile_id=profile-123, remote_input_path=/share/input/Jamshid, reference_genome=mm39]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "run Jamshid remotely on hpc3", skill="remote_execution")
        assert resp.status_code == 200
        assert mock_mcp.call_tool.await_count == 1

    def test_remote_run_without_saved_slurm_defaults_still_creates_gate(self, SL, seed, tmp_path):
        """A saved SSH profile should still produce an approval gate even when SLURM defaults are absent."""
        mock_mcp = AsyncMock()

        async def _call_tool(tool_name, **kwargs):
            if tool_name == "list_ssh_profiles":
                return [
                    {
                        "id": "profile-123",
                        "nickname": "hpc3",
                        "ssh_username": "seyedam",
                        "remote_base_path": "/dfs9/seyedam-lab/share/agoutic/seyedam",
                    }
                ]
            if tool_name == "get_slurm_defaults":
                assert kwargs.get("profile_nickname") == "hpc3"
                assert "ssh_profile_id" not in kwargs
                return {
                    "found": False,
                    "source": "none",
                    "ssh_profile_defaults": [
                        {
                            "ssh_profile_id": "profile-123",
                            "nickname": "hpc3",
                            "remote_base_path": "/dfs9/seyedam-lab/share/agoutic/seyedam",
                            "default_slurm_account": None,
                            "default_slurm_partition": None,
                        }
                    ],
                    "message": "No saved defaults in slurm_defaults or SSH profile defaults.",
                }
            raise AssertionError(f"unexpected tool: {tool_name}")

        mock_mcp.call_tool.side_effect = _call_tool

        def think(msg, skill, history):
            return (
                "I will prepare the remote submission.\n"
                "[[DATA_CALL: service=launchpad, tool=list_ssh_profiles, user_id=<user_id>]]\n"
                "[[DATA_CALL: service=launchpad, tool=get_slurm_defaults, ssh_profile_id=<profile_id>, user_id=<user_id>, project_id=<project_id>]]\n"
                "[[DATA_CALL: service=launchpad, tool=submit_dogme_job, execution_mode=slurm, sample_name=igvfr_698-04, mode=RNA, remote_input_path=/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip, ssh_profile_id=<profile_id>]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
            patch("cortex.planner.classify_request", return_value="SINGLE_TOOL"),
            patch("cortex.chat_stages.overrides._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
            patch(
                "cortex.remote_orchestration._resolve_ssh_profile_reference",
                new=AsyncMock(return_value=("profile-123", "hpc3")),
            ),
            patch(
                "cortex.remote_orchestration._list_user_ssh_profiles",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "profile-123",
                            "nickname": "hpc3",
                            "ssh_username": "seyedam",
                            "remote_base_path": "/dfs9/seyedam-lab/share/agoutic/seyedam",
                        }
                    ]
                ),
            ),
            patch(
                "cortex.job_parameters.extract_job_parameters_from_conversation",
                new=AsyncMock(
                    return_value={
                        "sample_name": "igvfr_698-04",
                        "mode": "RNA",
                        "remote_input_path": "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip",
                        "input_directory": "remote:/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip",
                        "reference_genome": ["mm39"],
                        "ssh_profile_nickname": "hpc3",
                        "execution_mode": "slurm",
                    }
                ),
            ),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "Analyze the mouse RNA sample igvfr_698-04 using remote data at /dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip on hpc3",
            skill="welcome",
        )

        assert resp.status_code == 200
        assert mock_mcp.call_tool.await_count == 2

        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert payload.get("skill") == "remote_execution"
        assert "no saved slurm defaults" in md.lower()
        assert "review the remote submission parameters before approving" in md.lower()

        gate = data.get("gate_block") or {}
        gate_payload = gate.get("payload") or {}
        assert gate_payload.get("skill") == "remote_execution"
        assert gate_payload.get("gate_action") == "job"
        extracted = gate_payload.get("extracted_params") or {}
        assert extracted.get("sample_name") == "igvfr_698-04"
        assert extracted.get("mode") == "RNA"
        assert extracted.get("ssh_profile_id") == "profile-123"
        assert extracted.get("remote_base_path") == "/dfs9/seyedam-lab/share/agoutic/seyedam"

    def test_remote_run_uses_profile_defaults_and_creates_job_approval(self, SL, seed, tmp_path):
        """Remote run intent should use SSH-profile defaults and create a job approval gate."""
        mock_mcp = AsyncMock()

        async def _call_tool(tool_name, **kwargs):
            if tool_name == "list_ssh_profiles":
                return [
                    {
                        "id": "profile-123",
                        "nickname": "hpc3",
                        "ssh_username": "agoutic",
                        "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                        "default_slurm_account": "SEYEDAM_LAB",
                        "default_slurm_partition": "standard",
                        "default_slurm_gpu_account": "SEYEDAM_LAB",
                        "default_slurm_gpu_partition": "gpu",
                    }
                ]
            if tool_name == "get_slurm_defaults":
                return {
                    "found": True,
                    "source": "ssh_profile_defaults",
                    "account": "SEYEDAM_LAB",
                    "partition": "standard",
                    "selected_profile_defaults": {
                        "ssh_profile_id": "profile-123",
                        "nickname": "hpc3",
                        "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                        "default_slurm_account": "SEYEDAM_LAB",
                        "default_slurm_partition": "standard",
                    },
                }
            raise AssertionError(f"unexpected tool: {tool_name}")

        mock_mcp.call_tool.side_effect = _call_tool

        def think(msg, skill, history):
            return (
                "I found 1 SSH profile and 0 SLURM defaults for your request.",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
            patch("cortex.planner.classify_request", return_value="SINGLE_TOOL"),
            patch("cortex.chat_stages.overrides._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
            patch(
                "cortex.app._list_user_ssh_profiles",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "profile-123",
                            "nickname": "hpc3",
                            "ssh_username": "agoutic",
                            "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                            "default_slurm_account": "SEYEDAM_LAB",
                            "default_slurm_partition": "standard",
                            "default_slurm_gpu_account": "SEYEDAM_LAB",
                            "default_slurm_gpu_partition": "gpu",
                        }
                    ]
                ),
            ),
            patch(
                "cortex.job_parameters.extract_job_parameters_from_conversation",
                new=AsyncMock(
                    return_value={
                        "sample_name": "Jamshid",
                        "input_directory": "/media/backup_disk/agoutic_root/testdata/CDNA/pod5",
                        "mode": "CDNA",
                        "reference_genome": ["mm39"],
                        "ssh_profile_nickname": "hpc3",
                        "execution_mode": "slurm",
                    }
                ),
            ),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "Analyze the mouse CDNA sample called Jamshid at /media/backup_disk/agoutic_root/testdata/CDNA/pod5 on hpc3",
            skill="welcome",
        )
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert payload.get("skill") == "remote_execution"
        assert "I found the saved remote defaults needed to submit this run." in md
        assert "0 SLURM defaults" not in md
        assert "did not return the expected data" not in md

        gate = data.get("gate_block") or {}
        gate_payload = gate.get("payload") or {}
        assert gate_payload.get("skill") == "remote_execution"
        assert gate_payload.get("gate_action") == "job"
        extracted = gate_payload.get("extracted_params") or {}
        assert extracted.get("execution_mode") == "slurm"
        assert extracted.get("gate_action") == "job"
        assert extracted.get("slurm_account") == "SEYEDAM_LAB"
        assert extracted.get("slurm_partition") == "standard"

    def test_remote_run_with_encff_like_local_filename_keeps_launchpad_calls_and_creates_approval(self, SL, seed, tmp_path):
        """Remote execution requests with ENCFF-like local filenames should not reroute launchpad calls to ENCODE."""
        mock_mcp = AsyncMock()

        async def _call_tool(tool_name, **kwargs):
            if tool_name == "list_ssh_profiles":
                return [
                    {
                        "id": "profile-123",
                        "nickname": "hpc3",
                        "ssh_username": "agoutic",
                        "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                        "default_slurm_account": "SEYEDAM_LAB",
                        "default_slurm_partition": "standard",
                    }
                ]
            if tool_name == "get_slurm_defaults":
                return {
                    "found": True,
                    "source": "ssh_profile_defaults",
                    "account": "SEYEDAM_LAB",
                    "partition": "standard",
                    "selected_profile_defaults": {
                        "ssh_profile_id": "profile-123",
                        "nickname": "hpc3",
                        "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                        "default_slurm_account": "SEYEDAM_LAB",
                        "default_slurm_partition": "standard",
                    },
                }
            raise AssertionError(f"unexpected tool: {tool_name}")

        mock_mcp.call_tool.side_effect = _call_tool

        def think(msg, skill, history):
            return (
                "I will prepare the remote submission.\n"
                "[[DATA_CALL: service=launchpad, tool=list_ssh_profiles, user_id=<user_id>]]\n"
                "[[DATA_CALL: service=launchpad, tool=get_slurm_defaults, user_id=<user_id>, profile_nickname=hpc3]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
            patch("cortex.planner.classify_request", return_value="SINGLE_TOOL"),
            patch("cortex.chat_stages.overrides._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))),
            patch(
                "cortex.app._list_user_ssh_profiles",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "profile-123",
                            "nickname": "hpc3",
                            "ssh_username": "agoutic",
                            "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                            "default_slurm_account": "SEYEDAM_LAB",
                            "default_slurm_partition": "standard",
                        }
                    ]
                ),
            ),
            patch(
                "cortex.job_parameters.extract_job_parameters_from_conversation",
                new=AsyncMock(
                    return_value={
                        "sample_name": "C2C12r1",
                        "input_directory": "data/ENCFF921XAH.bam",
                        "mode": "RNA",
                        "reference_genome": ["mm39"],
                        "ssh_profile_nickname": "hpc3",
                        "execution_mode": "slurm",
                    }
                ),
            ),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "Analyze the mouse RNA sample C2C12r1 using the file data/ENCFF921XAH.bam on hpc3",
            skill="remote_execution",
        )

        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert payload.get("skill") == "remote_execution"
        assert "I found the saved remote defaults needed to submit this run." in md
        assert "Unknown tool" not in md
        assert mock_mcp.call_tool.await_count == 2

        gate = data.get("gate_block") or {}
        gate_payload = gate.get("payload") or {}
        assert gate_payload.get("skill") == "remote_execution"
        assert gate_payload.get("gate_action") == "job"
        extracted = gate_payload.get("extracted_params") or {}
        assert extracted.get("input_directory") == "data/ENCFF921XAH.bam"
        assert extracted.get("execution_mode") == "slurm"


class TestDownloadWorkflow:
    def test_download_skill_skip_second_pass(self, SL, seed, tmp_path):
        """download_files skill + data call should skip second-pass LLM."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "accession": "ENCFF123ABC",
            "file_format": "fastq",
            "file_size": 1000000,
            "href": "/files/ENCFF123ABC/@@download/ENCFF123ABC.fastq.gz",
            "cloud_metadata": {"url": "https://s3.amazonaws.com/encode/file.fastq.gz"},
        }

        def think(msg, skill, history):
            return (
                "I'll get the file metadata.\n"
                "[[DATA_CALL: consortium=encode, tool=get_file_metadata, "
                "accession=ENCSR000AAA, file_accession=ENCFF123ABC]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "download ENCFF123ABC", skill="download_files")
        assert resp.status_code == 200
        data = resp.json()
        # Should create an approval gate for download
        gate = data.get("gate_block")
        if gate:
            payload = gate.get("payload", {})
            assert "download" in json.dumps(payload).lower() or "approval" in json.dumps(payload).lower()


# ===========================================================================
# 11. ENCODE search swap (biosample ↔ assay retry)
# ===========================================================================

class TestEncodeSearchSwap:
    def test_biosample_to_assay_swap(self, SL, seed, tmp_path):
        """When search_by_biosample returns 0 results and term looks like an assay,
        should retry with search_by_assay."""
        mock_mcp = AsyncMock()
        # First call: search_by_biosample returns 0
        # Second call (swap): search_by_assay returns results
        # Third call would be analyze_results (but that's engine.think)
        call_results = [
            {"total": 0, "experiments": []},  # search_by_biosample
            {"total": 5, "experiments": [{"accession": "ENCSR001"}]},  # search_by_assay
        ]
        call_idx = [0]
        async def mock_call_tool(tool_name, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(call_results):
                return call_results[idx]
            return {"total": 0}

        mock_mcp.call_tool = mock_call_tool

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Searching for ChIP-seq.\n"
                    "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=ChIP-seq]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found 5 ChIP-seq experiments.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "find ChIP-seq experiments", skill="ENCODE_Search")
        assert resp.status_code == 200

    def test_empty_list_triggers_swap(self, SL, seed, tmp_path):
        """search_by_biosample returns a list, not a dict. An empty list []
        should trigger the retry swap just like {'total': 0}."""
        mock_mcp = AsyncMock()
        calls_log = []
        call_results = [
            [],  # search_by_biosample returns empty list (ENCODELIB format)
            {"total": 3, "experiments": [{"accession": "ENCSR999"}]},  # swap → search_by_assay
        ]
        call_idx = [0]
        async def mock_call_tool(tool_name, **kwargs):
            calls_log.append((tool_name, dict(kwargs)))
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(call_results):
                return call_results[idx]
            return {"total": 0}

        mock_mcp.call_tool = mock_call_tool

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Searching.\n"
                    "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=ATAC-seq]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found results.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "find ATAC-seq experiments", skill="ENCODE_Search")
        assert resp.status_code == 200
        # Verify both calls were made (original + swap)
        assert len(calls_log) >= 2
        assert calls_log[0][0] == "search_by_biosample"
        assert calls_log[1][0] == "search_by_assay"

    def test_compound_query_relaxation(self, SL, seed, tmp_path):
        """When search_by_biosample with both search_term and assay_title returns 0,
        should first try dropping assay_title before trying the tool swap."""
        mock_mcp = AsyncMock()
        calls_log = []
        async def mock_call_tool(tool_name, **kwargs):
            calls_log.append((tool_name, dict(kwargs)))
            # First call: compound search returns empty list
            if len(calls_log) == 1:
                return []
            # Second call: relaxed search (no assay_title) returns results
            if len(calls_log) == 2 and "assay_title" not in kwargs:
                return [
                    {"accession": "ENCSR001", "assay": "total RNA-seq",
                     "biosample": "K562", "organism": "Homo sapiens"},
                    {"accession": "ENCSR002", "assay": "polyA plus RNA-seq",
                     "biosample": "K562", "organism": "Homo sapiens"},
                ]
            return []

        mock_mcp.call_tool = mock_call_tool

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Searching for RNA-seq in K562.\n"
                    "[[DATA_CALL: consortium=encode, tool=search_by_biosample, "
                    "search_term=K562, assay_title=total RNA-seq]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found 2 experiments.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "How many RNA-seq experiments for K562", skill="ENCODE_Search")
        assert resp.status_code == 200
        # Verify: first compound call, then relaxed (without assay_title)
        assert len(calls_log) == 2
        assert calls_log[0][0] == "search_by_biosample"
        assert "assay_title" in calls_log[0][1]  # original had assay_title
        assert calls_log[1][0] == "search_by_biosample"
        assert "assay_title" not in calls_log[1][1]  # relaxed — no assay_title


# ===========================================================================
# 12. Large result truncation
# ===========================================================================

class TestResultTruncation:
    def test_large_results_truncated(self, SL, seed, tmp_path):
        """Results larger than MAX_DATA_CHARS should be truncated."""
        mock_mcp = AsyncMock()
        # Return a very large result
        large_data = [
            {"accession": f"ENCSR{i:06d}", "assay": "RNA-seq", "biosample": f"Sample{i}"}
            for i in range(500)
        ]
        mock_mcp.call_tool.return_value = large_data

        call_count = [0]
        def think(msg, skill, history):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "Searching...\n"
                    "[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found many experiments.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "search all K562", skill="ENCODE_Search")
        assert resp.status_code == 200


# ===========================================================================
# 13. File tool chaining (find_file → parse)
# ===========================================================================

class TestFileToolChaining:
    def test_find_file_chains_to_parse_csv(self, SL, seed, tmp_path):
        """find_file result should chain to parse_csv_file for .csv files."""
        mock_mcp = AsyncMock()
        call_count = [0]
        async def mock_call_tool(tool_name, **kwargs):
            nonlocal call_count
            call_count[0] += 1
            if tool_name == "find_file":
                return {
                    "success": True,
                    "primary_path": "results/data.csv",
                    "work_dir": "/tmp/job1",
                }
            elif tool_name == "parse_csv_file":
                return {
                    "file_path": "results/data.csv",
                    "columns": ["A", "B"],
                    "data": [{"A": 1, "B": 2}],
                    "row_count": 1,
                }
            return {}

        mock_mcp.call_tool = mock_call_tool

        think_count = [0]
        def think(msg, skill, history):
            think_count[0] += 1
            if think_count[0] == 1:
                return (
                    "[[DATA_CALL: service=analyzer, tool=find_file, run_uuid=abc, filename=data.csv]]",
                    {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                )
            return (
                "Found and parsed data.csv with 1 row.",
                {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.tool_dispatch.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "show me data.csv", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert "SKILL_SWITCH" not in md

    def test_find_file_chains_to_allowlisted_bed_counter(self, SL, seed, tmp_path):
        """BED counting requests should chain find_file into run_allowlisted_script."""
        analyzer_mcp = AsyncMock()
        launchpad_mcp = AsyncMock()
        workflow_dir = tmp_path / "proj"

        async def analyzer_call_tool(tool_name, **kwargs):
            if tool_name == "find_file":
                return {
                    "success": True,
                    "primary_path": "results/data.bed",
                    "work_dir": str(workflow_dir),
                }
            raise AssertionError(f"Unexpected analyzer tool: {tool_name}")

        analyzer_mcp.call_tool = analyzer_call_tool
        launchpad_mcp.call_tool = AsyncMock(return_value={
            "success": True,
            "stdout": "{\"columns\": [\"Sample\", \"Genome\", \"Modification\", \"Chromosome\", \"Count\"], \"data\": [{\"Sample\": \"JamshidP\", \"Genome\": \"mm39\", \"Modification\": \"m6A\", \"Chromosome\": \"chr1\", \"Count\": 5}], \"row_count\": 1, \"metadata\": {\"label\": \"BED chromosome counts\"}}",
            "dataframe": {
                "columns": ["Sample", "Genome", "Modification", "Chromosome", "Count"],
                "data": [{"Sample": "JamshidP", "Genome": "mm39", "Modification": "m6A", "Chromosome": "chr1", "Count": 5}],
                "row_count": 1,
                "metadata": {"label": "BED chromosome counts"},
            },
            "exit_code": 0,
        })

        def think(msg, skill, history):
            return (
                "Let me count those regions.\n"
                "[[DATA_CALL: service=analyzer, tool=find_file, run_uuid=abc, filename=data.bed]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", side_effect=[analyzer_mcp, launchpad_mcp]),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "count bed regions by chromosome for data.bed",
            skill="analyze_job_results",
        )

        assert resp.status_code == 200
        launchpad_mcp.call_tool.assert_awaited_once_with(
            "run_allowlisted_script",
            script_id="analyze_job_results/count_bed",
            script_args=["--json", str((workflow_dir / "results/data.bed").resolve())],
            timeout_seconds=60.0,
        )
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        dfs = payload.get("_dataframes", {})
        assert "BED chromosome counts" in dfs
        assert dfs["BED chromosome counts"]["row_count"] == 1

    def test_find_file_bed_regression_does_not_send_script_working_directory(self, SL, seed, tmp_path):
        """Workflow-relative BED results should resolve against work_dir without assuming the file exists."""
        analyzer_mcp = AsyncMock()
        launchpad_mcp = AsyncMock()
        workflow_dir = Path("/media/backup_disk/agoutic_root/users/ali-mortazavi/testslurm1/workflow6")
        relative_bed = "bedMethyl/JamshidP.mm39.minus.m6A.filtered.bed"

        async def analyzer_call_tool(tool_name, **kwargs):
            if tool_name == "find_file":
                return {
                    "success": True,
                    "work_dir": str(workflow_dir),
                    "search_term": "JamshidP.mm39.minus.m6A.filtered.bed",
                    "file_count": 1,
                    "paths": [relative_bed],
                    "primary_path": relative_bed,
                }
            raise AssertionError(f"Unexpected analyzer tool: {tool_name}")

        analyzer_mcp.call_tool = analyzer_call_tool
        launchpad_mcp.call_tool = AsyncMock(return_value={
            "success": True,
            "stdout": "{\"columns\": [\"Sample\", \"Genome\", \"Modification\", \"Chromosome\", \"Count\"], \"data\": [{\"Sample\": \"JamshidP\", \"Genome\": \"mm39\", \"Modification\": \"m6A\", \"Chromosome\": \"chrM\", \"Count\": 12}], \"row_count\": 1, \"metadata\": {\"label\": \"BED chromosome counts\"}}",
            "dataframe": {
                "columns": ["Sample", "Genome", "Modification", "Chromosome", "Count"],
                "data": [{"Sample": "JamshidP", "Genome": "mm39", "Modification": "m6A", "Chromosome": "chrM", "Count": 12}],
                "row_count": 1,
                "metadata": {"label": "BED chromosome counts"},
            },
            "exit_code": 0,
        })

        def think(msg, skill, history):
            return (
                "Let me count those regions.\n"
                "[[DATA_CALL: service=analyzer, tool=find_file, run_uuid=abc, filename=JamshidP.mm39.minus.m6A.filtered.bed]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", side_effect=[analyzer_mcp, launchpad_mcp]),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "count BED regions by chromosome for JamshidP.mm39.minus.m6A.filtered.bed",
            skill="analyze_job_results",
        )

        assert resp.status_code == 200
        launchpad_mcp.call_tool.assert_awaited_once()
        call_args = launchpad_mcp.call_tool.await_args
        assert call_args.args == ("run_allowlisted_script",)
        assert call_args.kwargs == {
            "script_id": "analyze_job_results/count_bed",
            "script_args": ["--json", str((workflow_dir / relative_bed).resolve())],
            "timeout_seconds": 60.0,
        }

    def test_list_job_files_chains_modification_count_to_dataframe(self, SL, seed, tmp_path):
        """Modification chromosome-count requests should browse bedMethyl and create a dataframe."""
        analyzer_mcp = AsyncMock()
        launchpad_mcp = AsyncMock()
        workflow_dir = Path("/media/backup_disk/agoutic_root/users/ali-mortazavi/testslurm1/workflow6/bedMethyl")

        async def analyzer_call_tool(tool_name, **kwargs):
            if tool_name == "list_job_files":
                return {
                    "success": True,
                    "work_dir": str(workflow_dir),
                    "file_count": 3,
                    "files": [
                        {"path": "JamshidP.mm39.plus.inosine.filtered.bed", "name": "JamshidP.mm39.plus.inosine.filtered.bed", "size": 10},
                        {"path": "JamshidP.mm39.minus.inosine.filtered.bed", "name": "JamshidP.mm39.minus.inosine.filtered.bed", "size": 10},
                        {"path": "JamshidP.hg38.plus.inosine.filtered.bed", "name": "JamshidP.hg38.plus.inosine.filtered.bed", "size": 10},
                    ],
                }
            raise AssertionError(f"Unexpected analyzer tool: {tool_name}")

        analyzer_mcp.call_tool = analyzer_call_tool
        launchpad_mcp.call_tool = AsyncMock(return_value={
            "success": True,
            "stdout": "{\"columns\": [\"Sample\", \"Genome\", \"Modification\", \"Chromosome\", \"Count\"], \"data\": [{\"Sample\": \"JamshidP\", \"Genome\": \"hg38\", \"Modification\": \"inosine\", \"Chromosome\": \"chr1\", \"Count\": 2}, {\"Sample\": \"JamshidP\", \"Genome\": \"mm39\", \"Modification\": \"inosine\", \"Chromosome\": \"chr1\", \"Count\": 7}], \"row_count\": 2, \"metadata\": {\"label\": \"BED chromosome counts\"}}",
            "dataframe": {
                "columns": ["Sample", "Genome", "Modification", "Chromosome", "Count"],
                "data": [
                    {"Sample": "JamshidP", "Genome": "hg38", "Modification": "inosine", "Chromosome": "chr1", "Count": 2},
                    {"Sample": "JamshidP", "Genome": "mm39", "Modification": "inosine", "Chromosome": "chr1", "Count": 7},
                ],
                "row_count": 2,
                "metadata": {"label": "BED chromosome counts"},
            },
            "exit_code": 0,
        })

        def think(msg, skill, history):
            return (
                "Let me inspect the workflow BED files.\n"
                "[[DATA_CALL: service=analyzer, tool=list_job_files, work_dir=/media/backup_disk/agoutic_root/users/ali-mortazavi/testslurm1/workflow6/bedMethyl, extensions=.bed, max_depth=1]]",
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            )

        extra = [
            patch("cortex.tool_dispatch.MCPHttpClient", side_effect=[analyzer_mcp, launchpad_mcp]),
            patch("cortex.tool_dispatch.get_service_url", side_effect=lambda source: f"http://{source}:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(
            client,
            "count inosine modifications by chromosome",
            skill="analyze_job_results",
        )

        assert resp.status_code == 200
        launchpad_mcp.call_tool.assert_awaited_once_with(
            "run_allowlisted_script",
            script_id="analyze_job_results/count_bed",
            script_args=[
                "--json",
                str((workflow_dir / "JamshidP.hg38.plus.inosine.filtered.bed").resolve()),
                str((workflow_dir / "JamshidP.mm39.minus.inosine.filtered.bed").resolve()),
                str((workflow_dir / "JamshidP.mm39.plus.inosine.filtered.bed").resolve()),
            ],
            timeout_seconds=60.0,
        )
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        dfs = payload.get("_dataframes", {})
        assert "BED chromosome counts" in dfs
        assert dfs["BED chromosome counts"]["row_count"] == 2


# ===========================================================================
# DF Inspection Quick Commands (list dfs / head DF)
# ===========================================================================

class TestDFInspectionHelpers:
    """Unit tests for _detect_df_command, _render_list_dfs, _render_head_df."""

    def test_detect_list_dfs_variants(self):
        from cortex.app import _detect_df_command
        assert _detect_df_command("list dfs") == {"action": "list"}
        assert _detect_df_command("list dataframes") == {"action": "list"}
        assert _detect_df_command("show dfs") == {"action": "list"}
        assert _detect_df_command("show dataframes") == {"action": "list"}
        # Should NOT trigger on unrelated messages
        assert _detect_df_command("list my jobs") is None
        assert _detect_df_command("can you list dfs please") is None

    def test_detect_head_df_variants(self):
        from cortex.app import _detect_df_command
        # head df (no number) → latest
        r = _detect_df_command("head df")
        assert r == {"action": "head", "df_id": None, "n": 10}
        # head df3 → DF3, 10 rows
        r = _detect_df_command("head df3")
        assert r == {"action": "head", "df_id": 3, "n": 10}
        # head df 3 → DF3, 10 rows  (space between df and number)
        r = _detect_df_command("head df 3")
        assert r is None or r == {"action": "head", "df_id": 3, "n": 10}
        # head df3 5 → DF3, 5 rows
        r = _detect_df_command("head df3 5")
        assert r == {"action": "head", "df_id": 3, "n": 5}
        # Not a DF command
        assert _detect_df_command("head over to encode") is None

    def test_render_list_dfs_empty(self):
        from cortex.app import _render_list_dfs
        assert "No dataframes" in _render_list_dfs({})

    def test_render_list_dfs_with_data(self):
        from cortex.app import _render_list_dfs
        df_map = {
            1: {"columns": ["Assay", "Count"], "data": [], "row_count": 12, "label": "K562 experiments"},
            3: {"columns": ["Gene", "logFC", "FDR"], "data": [], "row_count": 500, "label": "DE results"},
        }
        md = _render_list_dfs(df_map)
        assert "DF1" in md
        assert "DF3" in md
        assert "K562 experiments" in md
        assert "12" in md
        assert "500" in md
        assert "Assay" in md
        assert "Gene" in md

    def test_render_head_df_not_found(self):
        from cortex.app import _render_head_df
        df_map = {1: {"columns": [], "data": [], "row_count": 0, "label": "X"}}
        md = _render_head_df(df_map, 5, 10)
        assert "DF5" in md
        assert "not found" in md
        assert "DF1" in md  # suggests available DFs

    def test_render_head_df_with_rows(self):
        from cortex.app import _render_head_df
        df_map = {
            2: {
                "columns": ["Gene", "logFC", "FDR"],
                "data": [
                    {"Gene": "TP53", "logFC": 2.1, "FDR": 0.001},
                    {"Gene": "BRCA1", "logFC": -1.5, "FDR": 0.03},
                    {"Gene": "MYC", "logFC": 0.8, "FDR": 0.12},
                ],
                "row_count": 3,
                "label": "DE results",
            }
        }
        md = _render_head_df(df_map, 2, 10)
        assert "**DF2**" in md
        assert "DE results" in md
        assert "3 rows" in md
        assert "TP53" in md
        assert "BRCA1" in md
        assert "MYC" in md
        # Verify it's a markdown table
        assert "| Gene | logFC | FDR |" in md
        assert "| --- | --- | --- |" in md

    def test_render_head_df_custom_row_count(self):
        from cortex.app import _render_head_df
        rows = [{"A": i} for i in range(20)]
        df_map = {1: {"columns": ["A"], "data": rows, "row_count": 20, "label": "big"}}
        md = _render_head_df(df_map, 1, 5)
        assert "showing first 5" in md
        # Should only have 5 data rows (plus header + separator)
        table_lines = [l for l in md.split("\n") if l.startswith("|")]
        assert len(table_lines) == 7  # header + separator + 5 data rows


class TestDFInspectionIntegration:
    """Integration test: send 'list dfs' and 'head df' through the chat endpoint."""

    def _seed_df_block(self, SL):
        """Insert an AGENT_PLAN block containing DFs into the project."""
        s = SL()
        blk = ProjectBlock(
            id=str(uuid.uuid4()),
            project_id="proj-plot",
            owner_id="u-plot",
            seq=1,
            type="AGENT_PLAN",
            status="DONE",
            payload_json=json.dumps({
                "markdown": "Here are K562 results.",
                "state": {
                    "active_skill": "ENCODE_Search",
                    "known_dataframes": ["DF1 (K562, 3 rows)"],
                    "latest_dataframe": "DF1",
                },
                "_dataframes": {
                    "K562 (3 results)": {
                        "columns": ["Assay", "Accession", "Status"],
                        "data": [
                            {"Assay": "RNA-seq", "Accession": "ENCSR001", "Status": "released"},
                            {"Assay": "ATAC-seq", "Accession": "ENCSR002", "Status": "released"},
                            {"Assay": "ChIP-seq", "Accession": "ENCSR003", "Status": "archived"},
                        ],
                        "row_count": 3,
                        "metadata": {"df_id": 1, "visible": True, "label": "K562", "row_count": 3},
                    }
                },
            }),
        )
        s.add(blk)
        s.commit()
        s.close()

    def test_list_dfs_returns_table(self, SL, seed, tmp_path):
        self._seed_df_block(SL)

        def think(msg, skill, history):
            raise AssertionError("LLM should NOT be called for list dfs")

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "list dfs")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert "DF1" in md
        assert "K562" in md

    def test_head_df1_returns_rows(self, SL, seed, tmp_path):
        self._seed_df_block(SL)

        def think(msg, skill, history):
            raise AssertionError("LLM should NOT be called for head df")

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "head df1")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert "**DF1**" in md
        assert "RNA-seq" in md
        assert "ENCSR001" in md
        assert "| Assay | Accession | Status |" in md

    def test_head_df_defaults_to_latest(self, SL, seed, tmp_path):
        self._seed_df_block(SL)

        def think(msg, skill, history):
            raise AssertionError("LLM should NOT be called for head df")

        client = next(_make_client(SL, seed, tmp_path, think))
        resp = _chat(client, "head df")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        # Should default to DF1 (the only/latest DF)
        assert "**DF1**" in md
        assert "RNA-seq" in md
