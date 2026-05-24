import ast
from pathlib import Path


_APPUI_STATE_PATH = Path(__file__).resolve().parents[2] / "ui" / "appui_state.py"


def _load_function(name: str):
    source = _APPUI_STATE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_APPUI_STATE_PATH))
    fn_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace: dict = {}
    mod = ast.Module(body=[fn_node], type_ignores=[])
    exec(compile(mod, filename=str(_APPUI_STATE_PATH), mode="exec"), namespace)
    return namespace[name]


class TestLocalHelpIntent:
    def test_recognizes_explicit_local_help_shortcut(self):
        fn = _load_function("_is_help_intent")

        assert fn("show local help") is True

    def test_does_not_steal_bare_help_prompt(self):
        fn = _load_function("_is_help_intent")

        assert fn("help") is False

    def test_does_not_steal_prompt_coach_request(self):
        fn = _load_function("_is_help_intent")

        assert fn("how do i compare reconcile samples") is False