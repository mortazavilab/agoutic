import ast
import os
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
        "_split_cluster_modkit_paths",
        "_default_cluster_modkit_bind_paths",
        "_build_cluster_modkit_profile",
        "_extract_modkit_binary_dir_from_profile",
        "_paths_to_text",
        "_resolve_custom_cluster_modkit_values",
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

    namespace: dict = {"os": os}
    module = ast.Module(body=selected_nodes, type_ignores=[])
    exec(compile(module, filename=str(PART1_PATH), mode="exec"), namespace)
    return namespace


def test_default_cluster_modkit_profile_uses_tch_distribution_and_root_based_exports():
    namespace = _load_part1_namespace()

    assert namespace["_DEFAULT_CLUSTER_MODKIT_BIND_PATHS"] == [
        "/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0",
        "/lib64/libgomp.so.1",
    ]
    assert "dist_modkit_v0.5.0_5120ef7_tch" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"].startswith(
        "export MODKITBASE=/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0\n"
    )
    assert "export PATH=${MODKITBASE}/dist_modkit_v0.5.0_5120ef7_tch:${PATH}" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "export MODKITMODEL=${MODKITBASE}/dist_modkit_v0.5.0_5120ef7_tch/models/" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "export LIBTORCH=${MODKITBASE}/libtorch" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]
    assert "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}" in namespace["_DEFAULT_CLUSTER_MODKIT_PROFILE"]


def test_build_cluster_modkit_profile_normalizes_trailing_slash():
    namespace = _load_part1_namespace()

    profile = namespace["_build_cluster_modkit_profile"]("/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch/")

    assert profile == (
        "export MODKITBASE=/cluster/modkit\n"
        "export PATH=${MODKITBASE}/dist_modkit_v0.5.0_5120ef7_tch:${PATH}\n"
        f"export MODKITMODEL=${{MODKITBASE}}/dist_modkit_v0.5.0_5120ef7_tch/models/{namespace['_DEFAULT_CLUSTER_MODKIT_MODEL_NAME']}\n"
        "export LIBTORCH=${MODKITBASE}/libtorch\n"
        "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}\n"
        "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}\n"
    )


def test_default_cluster_modkit_bind_paths_use_modkit_root_for_dist_builds():
    namespace = _load_part1_namespace()

    bind_paths = namespace["_default_cluster_modkit_bind_paths"](
        "/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch"
    )

    assert bind_paths == ["/cluster/modkit", "/lib64/libgomp.so.1"]


def test_extract_modkit_binary_dir_from_profile_reads_path_export():
    namespace = _load_part1_namespace()

    extracted = namespace["_extract_modkit_binary_dir_from_profile"](
        "export MODKITBASE=/cluster/modkit\n"
        "export PATH=${MODKITBASE}/dist_modkit_v0.5.0_5120ef7_tch:${PATH}\n"
        "export MODKITMODEL=${MODKITBASE}/dist_modkit_v0.5.0_5120ef7_tch/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0\n"
    )

    assert extracted == "/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch"


def test_extract_modkit_binary_dir_from_profile_preserves_old_style_profiles():
    namespace = _load_part1_namespace()

    assert namespace["_extract_modkit_binary_dir_from_profile"](
        "export MODKITBASE=/cluster/candle/modkit\n"
        "export PATH=${MODKITBASE}:${PATH}\n"
    ) == "/cluster/candle/modkit"


def test_extract_modkit_binary_dir_from_profile_returns_empty_when_missing():
    namespace = _load_part1_namespace()

    assert namespace["_extract_modkit_binary_dir_from_profile"]("export PATH=/usr/bin:${PATH}\n") == ""


def test_resolve_custom_cluster_modkit_values_prefers_generated_profile_and_auto_bind():
    namespace = _load_part1_namespace()

    resolved = namespace["_resolve_custom_cluster_modkit_values"](
        modkit_dir="/cluster/modkit/",
        use_default_bind_paths=True,
        custom_bind_paths_text="/ignored/path",
        manual_profile_override=False,
        manual_profile_text="export MODKITBASE=/manual/override\n",
    )

    assert resolved["modkit_dir"] == "/cluster/modkit"
    assert resolved["resolved_bind_paths_text"] == "/cluster/modkit"
    assert resolved["resolved_profile"] == (
        "export MODKITBASE=/cluster/modkit\n"
        "export PATH=${MODKITBASE}:${PATH}\n"
        f"export MODKITMODEL=${{MODKITBASE}}/models/{namespace['_DEFAULT_CLUSTER_MODKIT_MODEL_NAME']}\n"
    )


def test_resolve_custom_cluster_modkit_values_uses_root_based_tch_profile_when_binary_dir_is_dist():
    namespace = _load_part1_namespace()

    resolved = namespace["_resolve_custom_cluster_modkit_values"](
        modkit_dir="/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch",
        use_default_bind_paths=True,
        custom_bind_paths_text="",
        manual_profile_override=False,
        manual_profile_text="",
    )

    assert resolved["modkit_dir"] == "/cluster/modkit/dist_modkit_v0.5.0_5120ef7_tch"
    assert resolved["resolved_bind_paths_text"] == "/cluster/modkit\n/lib64/libgomp.so.1"
    assert resolved["resolved_profile"] == (
        "export MODKITBASE=/cluster/modkit\n"
        "export PATH=${MODKITBASE}/dist_modkit_v0.5.0_5120ef7_tch:${PATH}\n"
        f"export MODKITMODEL=${{MODKITBASE}}/dist_modkit_v0.5.0_5120ef7_tch/models/{namespace['_DEFAULT_CLUSTER_MODKIT_MODEL_NAME']}\n"
        "export LIBTORCH=${MODKITBASE}/libtorch\n"
        "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}\n"
        "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}\n"
    )


def test_resolve_custom_cluster_modkit_values_respects_manual_overrides():
    namespace = _load_part1_namespace()

    resolved = namespace["_resolve_custom_cluster_modkit_values"](
        modkit_dir="/cluster/modkit",
        use_default_bind_paths=False,
        custom_bind_paths_text="/cluster/modkit\n/cluster/models",
        manual_profile_override=True,
        manual_profile_text="export MODKITBASE=/manual/override\nexport PATH=${MODKITBASE}:${PATH}\n",
    )

    assert resolved["resolved_bind_paths_text"] == "/cluster/modkit\n/cluster/models"
    assert resolved["resolved_profile"] == (
        "export MODKITBASE=/manual/override\n"
        "export PATH=${MODKITBASE}:${PATH}\n"
    )