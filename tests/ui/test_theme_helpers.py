import ast
from pathlib import Path
from unittest.mock import MagicMock


THEME_PATH = Path(__file__).resolve().parents[2] / "ui" / "theme.py"


def _load_theme_namespace() -> dict:
    source = THEME_PATH.read_text()
    tree = ast.parse(source, filename=str(THEME_PATH))
    include_names = {
        "COLOR_BG_PRIMARY",
        "COLOR_BG_SECONDARY",
        "COLOR_BORDER",
        "COLOR_BORDER_SOFT",
        "COLOR_PLOT_PAPER",
        "COLOR_PLOT_SURFACE",
        "COLOR_PLOT_TEXT",
        "COLOR_PLOT_TEXT_MUTED",
        "COLOR_PLOT_GRID",
        "COLOR_PLOT_BORDER",
        "COLOR_PLOT_SERIES",
        "RADIUS_MD",
        "get_plotly_template",
        "inject_global_css",
    }

    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in include_names:
            selected_nodes.append(node)
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in include_names:
                    selected_nodes.append(node)
                    break

    namespace = {"st": MagicMock()}
    module = ast.Module(body=selected_nodes, type_ignores=[])
    exec(compile(module, filename=str(THEME_PATH), mode="exec"), namespace)
    return namespace


def test_get_plotly_template_uses_light_plot_surfaces():
    namespace = _load_theme_namespace()

    layout = namespace["get_plotly_template"]()["layout"]

    assert layout["paper_bgcolor"] == "#ffffff"
    assert layout["plot_bgcolor"] == "#f8fafc"
    assert layout["font"]["color"] == "#1f2937"
    assert layout["colorway"] == namespace["COLOR_PLOT_SERIES"]
    assert layout["title"]["x"] == 0.03
    assert layout["legend"]["bgcolor"] == "#ffffff"
    assert layout["xaxis"]["gridcolor"] == "#dbe4ee"
    assert layout["xaxis"]["automargin"] is True
    assert layout["yaxis"]["tickfont"]["color"] == "#475569"
    assert layout["margin"] == {"l": 84, "r": 24, "t": 72, "b": 72}


def test_inject_global_css_wraps_plot_and_image_blocks_in_light_surface():
    namespace = _load_theme_namespace()

    namespace["inject_global_css"]()

    markdown_call = namespace["st"].markdown.call_args
    assert markdown_call is not None

    css = markdown_call.args[0]
    assert '[data-testid="stPlotlyChart"]' in css
    assert '[data-testid="stImage"]' in css
    assert "background: rgba(255, 255, 255, 0.96);" in css
    assert "overflow: visible;" in css
    assert f"border-radius: {namespace['RADIUS_MD']};" in css
    assert markdown_call.kwargs["unsafe_allow_html"] is True