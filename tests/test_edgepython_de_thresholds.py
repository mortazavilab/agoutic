import sys
import types

import pandas as pd


class _FakeFastMCP:
    def __init__(self, *_args, **_kwargs):
        pass

    def tool(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator


_fastmcp_module = types.ModuleType("fastmcp")
_fastmcp_module.FastMCP = _FakeFastMCP
sys.modules.setdefault("fastmcp", _fastmcp_module)
sys.modules.setdefault("edgepython", types.ModuleType("edgepython"))

from edgepython_mcp import edgepython_server as server


def _demo_table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "logFC": [1.5, -1.2, 0.4],
            "logCPM": [4.2, 3.8, 2.9],
            "PValue": [0.005, 0.009, 0.2],
            "FDR": [0.2, 0.25, 0.3],
            "Symbol": ["GENE1", "GENE2", "GENE3"],
        },
        index=["gene1", "gene2", "gene3"],
    )


def test_get_top_genes_can_filter_by_pvalue(monkeypatch):
    table = _demo_table()
    original_results = server._state.get("results")
    original_last_result = server._state.get("last_result")

    monkeypatch.setattr(server.ep, "top_tags", lambda _res, n: {"table": table.head(n)}, raising=False)
    server._state["results"] = {"demo": {"table": table}}
    server._state["last_result"] = "demo"

    try:
        result = server.get_top_genes(
            name="demo",
            n=3,
            significance_metric="pvalue",
            significance_threshold=0.01,
        )
    finally:
        server._state["results"] = original_results
        server._state["last_result"] = original_last_result

    assert "2 with p-value < 0.01" in result
    assert "GENE1" in result
    assert "GENE2" in result


def test_exact_test_can_report_pvalue_significance(monkeypatch):
    table = _demo_table()
    original_dgelist = server._state.get("dgelist")
    original_results = server._state.get("results")
    original_last_result = server._state.get("last_result")

    monkeypatch.setattr(server.ep, "exact_test", lambda *args, **kwargs: {"table": table}, raising=False)
    monkeypatch.setattr(server.ep, "top_tags", lambda _res, n: {"table": table.head(n)}, raising=False)
    server._state["dgelist"] = object()
    server._state["results"] = {}
    server._state["last_result"] = None

    try:
        result = server.exact_test(
            pair=["AD", "control"],
            name="demo",
            significance_metric="pvalue",
            significance_threshold=0.01,
        )
    finally:
        server._state["dgelist"] = original_dgelist
        server._state["results"] = original_results
        server._state["last_result"] = original_last_result

    assert "DE genes (p-value < 0.01): 1 up, 1 down, 1 NS" in result