"""Tests for atlas/config.py."""

import pytest

import atlas.config as atlas_config


class TestRegistryLookup:
    def test_get_consortium_url_returns_encode_url(self):
        assert atlas_config.get_consortium_url("encode") == atlas_config.CONSORTIUM_REGISTRY["encode"]["url"]

    def test_get_consortium_entry_returns_registry_entry(self):
        entry = atlas_config.get_consortium_entry("encode")

        assert entry["display_name"] == "ENCODE Portal"
        assert "tool_aliases" in entry

    def test_unknown_consortium_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown consortium: missing"):
            atlas_config.get_consortium_url("missing")

        with pytest.raises(KeyError, match="Unknown consortium: missing"):
            atlas_config.get_consortium_entry("missing")


class TestMergedMappings:
    def test_collectors_merge_all_registry_entries(self, monkeypatch):
        registry = {
            "alpha": {
                "url": "http://alpha",
                "fallback_patterns": {r"Alpha\\(([^)]+)\\)": "alpha-repl"},
                "tool_aliases": {"bad_tool": "good_tool", "shared": "alpha_tool"},
                "param_aliases": {
                    "search_alpha": {"wrong": "right"},
                    "shared_tool": {"alpha_param": "alpha_value"},
                },
            },
            "beta": {
                "url": "http://beta",
                "fallback_patterns": {r"Beta\\(([^)]+)\\)": "beta-repl"},
                "tool_aliases": {"other_tool": "better_tool", "shared": "beta_tool"},
                "param_aliases": {
                    "search_beta": {"old": "new"},
                    "shared_tool": {"beta_param": "beta_value"},
                },
            },
        }
        monkeypatch.setattr(atlas_config, "CONSORTIUM_REGISTRY", registry)

        fallback_patterns = atlas_config.get_all_fallback_patterns()
        tool_aliases = atlas_config.get_all_tool_aliases()
        param_aliases = atlas_config.get_all_param_aliases()

        assert fallback_patterns == {
            r"Alpha\\(([^)]+)\\)": "alpha-repl",
            r"Beta\\(([^)]+)\\)": "beta-repl",
        }
        assert tool_aliases == {
            "bad_tool": "good_tool",
            "shared": "beta_tool",
            "other_tool": "better_tool",
        }
        assert param_aliases == {
            "search_alpha": {"wrong": "right"},
            "shared_tool": {
                "alpha_param": "alpha_value",
                "beta_param": "beta_value",
            },
            "search_beta": {"old": "new"},
        }
