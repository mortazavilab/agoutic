"""Tests for skill-defined plan chains and planner integration."""

from cortex.plan_chains import load_chains_for_skill, match_chain, parse_chains_from_skill, render_chain_plan
from cortex.planner import classify_request


class TestParseChains:
    def test_parse_chains_from_skill_extracts_trigger_groups_and_steps(self):
        skill_text = """
# Skill: Example (`example`)

## Plan Chains

### search_and_plot
- description: Search first, then visualize
- trigger: search|get|find + plot|chart|visualize
- steps:
  1. SEARCH_DATA: Search for records
  2. GENERATE_PLOT: Plot the result
- auto_approve: true
- plot_hint: Use a bar chart for counts
"""

        chains = parse_chains_from_skill(skill_text)

        assert len(chains) == 1
        chain = chains[0]
        assert chain.name == "search_and_plot"
        assert chain.description == "Search first, then visualize"
        assert chain.trigger_groups == [["search", "get", "find"], ["plot", "chart", "visualize"]]
        assert [(step.order, step.kind, step.title) for step in chain.steps] == [
            (1, "SEARCH_DATA", "Search for records"),
            (2, "GENERATE_PLOT", "Plot the result"),
        ]
        assert chain.auto_approve is True
        assert chain.plot_hint == "Use a bar chart for counts"

    def test_render_chain_plan_includes_steps(self):
        chain = load_chains_for_skill("ENCODE_Search")[0]

        rendered = render_chain_plan(chain, "plot the K562 experiments by assay")

        assert "Plan" in rendered
        assert "1." in rendered
        assert "2." in rendered


class TestMatchChains:
    def test_load_chains_for_encode_search(self):
        chains = load_chains_for_skill("ENCODE_Search")

        assert {chain.name for chain in chains} == {
            "search_and_visualize",
            "visualize_existing",
            "search_filter_visualize",
        }

    def test_search_plot_phrasings_match_search_and_visualize(self):
        chains = load_chains_for_skill("ENCODE_Search")
        messages = [
            "get the K562 experiments in ENCODE and make a plot by assay type",
            "plot the K562 experiments in ENCODE by assay type",
            "plot by assay type the K562 experiments in encode",
        ]

        for message in messages:
            chain = match_chain(message, chains)
            assert chain is not None
            assert chain.name == "search_and_visualize"

    def test_search_only_query_does_not_match_plot_chain(self):
        chains = load_chains_for_skill("ENCODE_Search")

        chain = match_chain("get the K562 experiments in ENCODE", chains)

        assert chain is None


class TestPlannerChainClassification:
    def test_classify_request_returns_chain_multi_step_for_encode_plot_query(self):
        result = classify_request(
            "plot the K562 experiments in ENCODE by assay type",
            "ENCODE_Search",
            None,
        )

        assert result == "CHAIN_MULTI_STEP"

    def test_classify_request_returns_chain_multi_step_for_existing_df_plot_query(self):
        result = classify_request(
            "plot the existing results by assay type",
            "ENCODE_Search",
            None,
        )

        assert result == "CHAIN_MULTI_STEP"