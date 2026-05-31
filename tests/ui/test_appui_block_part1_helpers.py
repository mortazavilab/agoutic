import ast
import os
from pathlib import Path
from types import SimpleNamespace


PART1_PATH = Path(__file__).resolve().parents[2] / "ui" / "appui_block_part1.py"


def _load_part1_namespace() -> dict:
    source = PART1_PATH.read_text()
    tree = ast.parse(source, filename=str(PART1_PATH))
    include_names = {
        "_LEGACY_SLURM_DEFAULT_CPU_MEMORY_GB",
        "_DEFAULT_CLUSTER_MODKIT_BINARY_DIR",
        "_DEFAULT_CLUSTER_MODKIT_MODEL_NAME",
        "_DEFAULT_CLUSTER_MODKIT_PROFILE",
        "_DEFAULT_CLUSTER_MODKIT_BIND_PATHS",
        "_WF_PORE_C_DEFAULT_CUTTER",
        "_WF_PORE_C_OUTPUT_FLAG_ORDER",
        "_WF_PORE_C_OUTPUT_FLAG_LABELS",
        "_pending_gate_slurm_default_refresh_payload",
        "_prime_post_approval_refresh_state",
        "_wf_pore_c_ui_enabled",
        "_approval_workflow_key",
        "_workflow_input_path_label",
        "_path_looks_like_file",
        "_approval_input_path_label",
        "_approval_input_path_help",
        "_approval_path_summary_rows",
        "_approval_haplotype_sample_label",
        "_approval_haplotype_summary_rows",
        "_build_haplotype_approval_params",
        "_approval_project_data_inventory",
        "_approval_fastq_sample_name",
        "_approval_has_generic_sample_name",
        "_approval_dogme_fastq_state",
        "_wf_pore_c_output_flag_values",
        "_approval_gate_field_visibility",
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

    namespace: dict = {"os": os, "SLURM_DEFAULT_CPU_MEMORY_GB": 64}
    module = ast.Module(body=selected_nodes, type_ignores=[])
    exec(compile(module, filename=str(PART1_PATH), mode="exec"), namespace)
    return namespace


def test_default_cluster_modkit_profile_uses_tch_distribution_and_root_based_exports():
    namespace = _load_part1_namespace()

    assert namespace["_DEFAULT_CLUSTER_MODKIT_BIND_PATHS"] == [
        "/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0",
        "/lib64/libgomp.so.1",
        "/lib64/libstdc++.so.6",
        "/lib64/libgcc_s.so.1",
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

    assert bind_paths == [
        "/cluster/modkit",
        "/lib64/libgomp.so.1",
        "/lib64/libstdc++.so.6",
        "/lib64/libgcc_s.so.1",
    ]


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
    assert resolved["resolved_bind_paths_text"] == (
        "/cluster/modkit\n"
        "/lib64/libgomp.so.1\n"
        "/lib64/libstdc++.so.6\n"
        "/lib64/libgcc_s.so.1"
    )
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


def test_pending_gate_slurm_default_refresh_payload_updates_legacy_pending_memory():
    namespace = _load_part1_namespace()

    refreshed = namespace["_pending_gate_slurm_default_refresh_payload"](
        status="PENDING",
        content={
            "extracted_params": {
                "execution_mode": "slurm",
                "slurm_memory_gb": 16,
            }
        },
    )

    assert refreshed is not None
    assert refreshed["extracted_params"]["slurm_memory_gb"] == 64


def test_pending_gate_slurm_default_refresh_payload_skips_nonlegacy_or_edited_values():
    namespace = _load_part1_namespace()

    assert namespace["_pending_gate_slurm_default_refresh_payload"](
        status="PENDING",
        content={
            "extracted_params": {
                "execution_mode": "slurm",
                "slurm_memory_gb": 32,
            }
        },
    ) is None

    assert namespace["_pending_gate_slurm_default_refresh_payload"](
        status="PENDING",
        content={
            "edited_params": {"slurm_memory_gb": 16},
            "extracted_params": {
                "execution_mode": "slurm",
                "slurm_memory_gb": 16,
            },
        },
    ) is None


def test_prime_post_approval_refresh_state_marks_background_work_active():
    class _FakeStreamlit:
        def __init__(self):
            self.session_state = {
                "_job_finished_at": 123.0,
                "_suppress_auto_refresh_until": 999.0,
            }

    fake_st = _FakeStreamlit()
    namespace = _load_part1_namespace()
    namespace["st"] = fake_st

    namespace["_prime_post_approval_refresh_state"]()

    assert fake_st.session_state["_has_running_job"] is True
    assert fake_st.session_state["_has_full_refresh_job"] is True
    assert "_job_finished_at" not in fake_st.session_state
    assert fake_st.session_state["_suppress_auto_refresh_until"] == 0.0


def test_approval_gate_field_visibility_preserves_dogme_controls(monkeypatch):
    monkeypatch.delenv("WF_PORE_C_ENABLED", raising=False)
    namespace = _load_part1_namespace()

    visibility = namespace["_approval_gate_field_visibility"](
        {"workflow_key": "dogme"},
        gate_action="job",
    )

    assert visibility == {
        "workflow_key": "dogme",
        "show_mode": True,
        "show_entry_point": True,
        "show_modifications": True,
        "show_dogme_advanced": True,
        "show_reference_fasta": False,
        "show_vcf": False,
        "show_sample_sheet": False,
        "show_cutter": False,
        "show_output_flags": False,
    }


def test_approval_gate_field_visibility_switches_to_wf_pore_c_controls(monkeypatch):
    monkeypatch.setenv("WF_PORE_C_ENABLED", "true")
    namespace = _load_part1_namespace()

    visibility = namespace["_approval_gate_field_visibility"](
        {"workflow_key": "wf_pore_c"},
        gate_action="remote_stage",
    )

    assert visibility == {
        "workflow_key": "wf_pore_c",
        "show_mode": False,
        "show_entry_point": False,
        "show_modifications": False,
        "show_dogme_advanced": False,
        "show_reference_fasta": True,
        "show_vcf": True,
        "show_sample_sheet": True,
        "show_cutter": True,
        "show_output_flags": True,
    }


def test_approval_input_path_label_marks_reused_staged_sample_paths():
    namespace = _load_part1_namespace()

    label = namespace["_approval_input_path_label"](
        {
            "workflow_key": "dogme",
            "input_directory": "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip",
            "staged_remote_input_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam/data/fp-2",
        },
        gate_action="job",
    )
    help_text = namespace["_approval_input_path_help"](
        {
            "workflow_key": "dogme",
            "input_directory": "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip",
            "staged_remote_input_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam/data/fp-2",
        },
        gate_action="job",
    )

    assert label == "Staged Sample Source Directory"
    assert "reused staged sample" in help_text.lower()


def test_approval_input_path_label_marks_file_inputs():
    namespace = _load_part1_namespace()

    label = namespace["_approval_input_path_label"](
        {
            "workflow_key": "dogme",
            "input_directory": "/media/backup_disk/agoutic_root/users/elnaz-a/data/ENCFF921XAH.bam",
        },
        gate_action="job",
    )

    assert label == "Input File"


def test_approval_path_summary_rows_include_staged_remote_path():
    namespace = _load_part1_namespace()

    rows = namespace["_approval_path_summary_rows"](
        {
            "workflow_key": "dogme",
            "input_directory": "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip",
            "staged_remote_input_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam/data/fp-2",
        },
        gate_action="job",
    )

    assert rows == {
        "Staged Sample Source Directory": "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip",
        "Staged Remote Path": "/share/crsp/lab/seyedam/share/agoutic/seyedam/data/fp-2",
    }


def test_approval_haplotype_summary_rows_show_vcf_founders_and_bams():
    namespace = _load_part1_namespace()

    rows = namespace["_approval_haplotype_summary_rows"](
        {
            "mode": "RNA",
            "reference_genome": "mm39",
            "assignment_mode": "founder_panel",
            "vcf_defaulted": True,
            "vcf_path": "/proj/refs/mm39/mgp_REL2021_snps_founders.vcf.gz",
            "vcf_selected_samples": ["C57BL_6J", "CAST_EiJ"],
            "output_directory": "/proj/workflow9",
            "bam_inputs": [
                {"name": "sample1.mm39.annotated.bam"},
                {"path": "/proj/workflow7/annot/sample2.mm39.annotated.bam"},
            ],
        }
    )

    assert rows == {
        "Mode": "RNA",
        "Reference Genome": "mm39",
        "Assignment Mode": "Founder Panel",
        "Resolved Founder VCF": "/proj/refs/mm39/mgp_REL2021_snps_founders.vcf.gz",
        "Selected Founders": "C57BL_6J, CAST_EiJ",
        "Output Directory": "/proj/workflow9",
        "Input BAMs": "sample1.mm39.annotated.bam, sample2.mm39.annotated.bam",
    }


def test_build_haplotype_approval_params_updates_relevant_fields_only():
    namespace = _load_part1_namespace()

    edited = namespace["_build_haplotype_approval_params"](
        {
            "gate_action": "haplotype_with_vcf",
            "assignment_mode": "founder_panel",
            "vcf_path": "/proj/old.vcf.gz",
            "output_directory": "/proj/workflow9",
            "vcf_selected_samples": ["C57BL_6J", "CAST_EiJ"],
            "vcf_selected_sample_sources": {"C57BL_6J": None, "CAST_EiJ": "CAST_EiJ"},
            "assignment_labels": ["C57BL_6J", "CAST_EiJ"],
        },
        vcf_path=" /proj/new.vcf.gz ",
        output_directory=" /proj/workflow10 ",
        selected_samples_text="C57BL_6J, PWK_PhJ",
        min_informative_sites=3,
        min_mapq=12,
    )

    assert edited["gate_action"] == "haplotype_with_vcf"
    assert edited["vcf_path"] == "/proj/new.vcf.gz"
    assert edited["output_directory"] == "/proj/workflow10"
    assert edited["vcf_selected_samples"] == ["C57BL_6J", "PWK_PhJ"]
    assert edited["assignment_labels"] == ["C57BL_6J", "PWK_PhJ"]
    assert edited["vcf_selected_sample_sources"] == {
        "C57BL_6J": None,
        "PWK_PhJ": "PWK_PhJ",
    }
    assert edited["min_informative_sites"] == 3
    assert edited["min_mapq"] == 12


def test_wf_pore_c_output_flag_values_force_paired_end_when_bed_enabled():
    namespace = _load_part1_namespace()

    flags = namespace["_wf_pore_c_output_flag_values"]({"bed": True, "pairs": False})

    assert flags == {
        "pairs": False,
        "mcool": True,
        "hi_c": False,
        "bed": True,
        "chromunity": False,
        "coverage": False,
        "paired_end": True,
    }


def test_approval_project_data_inventory_filters_to_project_data_fastq_and_pod5():
    namespace = _load_part1_namespace()

    def fake_request(method, url, timeout=5):
        assert method == "GET"
        assert url.endswith("/projects/proj-1/files")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "files": [
                    {"path": "data/sample.fastq.gz", "name": "sample.fastq.gz"},
                    {"path": "data/sample.pod5", "name": "sample.pod5"},
                    {"path": "workflow1/report.html", "name": "report.html"},
                ]
            },
        )

    inventory = namespace["_approval_project_data_inventory"](
        "http://api.test",
        fake_request,
        "proj-1",
    )

    assert inventory == {
        "fastq_paths": ["data/sample.fastq.gz"],
        "pod5_paths": ["data/sample.pod5"],
    }


def test_approval_dogme_fastq_state_defaults_to_fastq_cdna_when_project_has_only_fastq():
    namespace = _load_part1_namespace()

    def fake_request(method, url, timeout=5):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "files": [
                    {"path": "data/jamshid.fastq.gz", "name": "jamshid.fastq.gz"},
                ]
            },
        )

    state = namespace["_approval_dogme_fastq_state"](
        {"workflow_key": "dogme", "sample_name": "mouse_sample_proj-1"},
        api_url="http://api.test",
        request_fn=fake_request,
        project_id="proj-1",
    )

    assert state["default_input_type"] == "fastq"
    assert state["default_mode"] == "CDNA"
    assert state["default_entry_point"] == "fastqCDNA"
    assert state["default_sample_name"] == "jamshid"


def test_approval_dogme_fastq_state_keeps_pod5_default_when_project_has_pod5_and_fastq():
    namespace = _load_part1_namespace()

    def fake_request(method, url, timeout=5):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "files": [
                    {"path": "data/jamshid.fastq.gz", "name": "jamshid.fastq.gz"},
                    {"path": "data/run1.pod5", "name": "run1.pod5"},
                ]
            },
        )

    state = namespace["_approval_dogme_fastq_state"](
        {"workflow_key": "dogme", "sample_name": "mouse_sample_proj-1"},
        api_url="http://api.test",
        request_fn=fake_request,
        project_id="proj-1",
    )

    assert state["default_input_type"] == "pod5"
    assert state["default_mode"] == "DNA"


def test_approval_dogme_fastq_state_preserves_explicit_fastq_request_when_pod5_is_present():
    namespace = _load_part1_namespace()

    def fake_request(method, url, timeout=5):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "files": [
                    {"path": "data/jamshid.fastq.gz", "name": "jamshid.fastq.gz"},
                    {"path": "data/run1.pod5", "name": "run1.pod5"},
                ]
            },
        )

    state = namespace["_approval_dogme_fastq_state"](
        {
            "workflow_key": "dogme",
            "sample_name": "Jamshid",
            "input_type": "fastq",
            "input_type_explicit": True,
            "approval_prefill": {
                "input_type": "fastq",
                "entry_point": "fastqCDNA",
                "mode": "CDNA",
            },
        },
        api_url="http://api.test",
        request_fn=fake_request,
        project_id="proj-1",
    )

    assert state["default_input_type"] == "fastq"
    assert state["default_mode"] == "CDNA"
    assert state["default_entry_point"] == "fastqCDNA"