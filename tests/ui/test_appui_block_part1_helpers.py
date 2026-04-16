import ast
from pathlib import Path


PART1_PATH = Path(__file__).resolve().parents[2] / "ui" / "appui_block_part1.py"


def _load_part1_namespace() -> dict:
    source = PART1_PATH.read_text()
    tree = ast.parse(source, filename=str(PART1_PATH))
    include_names = {
        "_DEFAULT_CLUSTER_MODKIT_BINARY_DIR",
        "_DEFAULT_CLUSTER_MODKIT_MODEL_NAME",
        "_DEFAULT_CLUSTER_MODKIT_PROFILE",
        "_DEFAULT_CLUSTER_MODKIT_BIND_PATHS",
        "_build_cluster_modkit_profile",
        "_extract_modkit_base_from_profile",
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

    namespace: dict = {}
    module = ast.Module(body=selected_nodes, type_ignores=[])
    exec(compile(module, filename=str(PART1_PATH), mode="exec"), namespace)
    return namespace


def test_default_cluster_modkit_profile_uses_candle_distribution_and_path():
    namespace = _load_part1_namespace()

    assert namespace["_DEFAULT_CLUSTER_MODKIT_BIND_PATHS"] == [
        namespace["_DEFAULT_CLUSTER_MODKIT_BINARY_DIR"]
    ]
    assert "dist_modkit_v0.5.0_5120ef7_candle" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "export PATH=${MODKITBASE}:${PATH}" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "LIBTORCH" not in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "DYLD_LIBRARY_PATH" not in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "LD_LIBRARY_PATH" not in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]


def test_build_cluster_modkit_profile_normalizes_trailing_slash():
    namespace = _load_part1_namespace()

    profile = namespace["_build_cluster_modkit_profile"]("/cluster/modkit/")

    assert profile == (
        "export MODKITBASE=/cluster/modkit\n"
        "export PATH=${MODKITBASE}:${PATH}\n"
        f"export MODKITMODEL=${{MODKITBASE}}/models/{namespace['_DEFAULT_CLUSTER_MODKIT_MODEL_NAME']}\n"
    )


def test_extract_modkit_base_from_profile_reads_export_line():
    namespace = _load_part1_namespace()

    extracted = namespace["_extract_modkit_base_from_profile"](
        "export MODKITBASE=/cluster/candle/modkit\n"
        "export PATH=${MODKITBASE}:${PATH}\n"
    )

    assert extracted == "/cluster/candle/modkit"


def test_extract_modkit_base_from_profile_returns_empty_when_missing():
    namespace = _load_part1_namespace()

    assert namespace["_extract_modkit_base_from_profile"]("export PATH=/usr/bin:${PATH}\n") == ""