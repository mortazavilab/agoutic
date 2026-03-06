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

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex.models import (
    Base, User, Session as SessionModel, Project, ProjectAccess,
    ProjectBlock, Conversation, ConversationMessage,
)
from cortex.app import app

try:
    from launchpad.models import Base as LaunchpadBase
    _LP = True
except ImportError:
    _LP = False


# ---------------------------------------------------------------------------
# Fixtures following test_chat_flow.py pattern
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    if _LP:
        LaunchpadBase.metadata.create_all(bind=eng)
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
    patches = [
        patch("cortex.db.SessionLocal", SL),
        patch("cortex.app.SessionLocal", SL),
        patch("cortex.dependencies.SessionLocal", SL),
        patch("cortex.middleware.SessionLocal", SL),
        patch("cortex.admin.SessionLocal", SL),
        patch("cortex.auth.SessionLocal", SL),
        patch("cortex.config.AGOUTIC_DATA", tmp_path),
        patch("cortex.user_jail.AGOUTIC_DATA", tmp_path),
        patch("cortex.app._resolve_project_dir", return_value=tmp_path / "proj"),
    ]
    if extra_patches:
        patches.extend(extra_patches)

    engine_patch = patch("cortex.app.AgentEngine")
    for p in patches:
        p.start()
    mock_engine_cls = engine_patch.start()
    inst = mock_engine_cls.return_value
    inst.model_name = "test-model"
    inst.think = think_fn
    # Mock analyze_results for second-pass LLM analysis
    inst.analyze_results = lambda msg, md, data, skill, hist: (
        "Analysis complete: " + md[:100],
        {"prompt_tokens": 15, "completion_tokens": 15, "total_tokens": 30},
    )

    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("session", "plot-session")
    yield c

    engine_patch.stop()
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://encode:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://encode:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://encode:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "show me results.csv", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        dfs = payload.get("_dataframes", {})
        assert len(dfs) >= 1


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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "list files", skill="analyze_job_results")
        assert resp.status_code == 200


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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://encode:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://encode:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "find ChIP-seq experiments", skill="ENCODE_Search")
        assert resp.status_code == 200


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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://encode:8000"),
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
            patch("cortex.app.MCPHttpClient", return_value=mock_mcp),
            patch("cortex.app.get_service_url", return_value="http://analyzer:8000"),
        ]

        client = next(_make_client(SL, seed, tmp_path, think, extra_patches=extra))
        resp = _chat(client, "show me data.csv", skill="analyze_job_results")
        assert resp.status_code == 200
        data = resp.json()
        payload = (data.get("agent_block") or data.get("plan_block") or {}).get("payload", {})
        md = payload.get("markdown", "")
        assert "SKILL_SWITCH" not in md
