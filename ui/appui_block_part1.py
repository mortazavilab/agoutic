import os

import streamlit as st
from appui_config import SLURM_DEFAULT_CPU_MEMORY_GB
from components.cards import info_callout, metadata_row, section_header, status_chip
from components.forms import grouped_section, review_panel


_LEGACY_SLURM_DEFAULT_CPU_MEMORY_GB = 16


_DEFAULT_CLUSTER_MODKIT_BINARY_DIR = "/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0/dist_modkit_v0.5.0_5120ef7_tch"
_DEFAULT_CLUSTER_MODKIT_MODEL_NAME = "r1041_e82_400bps_hac_v5.2.0@v0.1.0"


def _split_cluster_modkit_paths(modkit_dir: str) -> tuple[str, str, str]:
    cleaned_dir = str(modkit_dir or "").strip().rstrip("/")
    if not cleaned_dir:
        return "", "", ""
    dist_leaf = os.path.basename(cleaned_dir)
    parent_dir = os.path.dirname(cleaned_dir).rstrip("/")
    if dist_leaf.startswith("dist_modkit_") and parent_dir:
        return parent_dir, cleaned_dir, dist_leaf
    return cleaned_dir, cleaned_dir, ""


def _default_cluster_modkit_bind_paths(modkit_dir: str) -> list[str]:
    modkit_base, binary_dir, dist_leaf = _split_cluster_modkit_paths(modkit_dir)
    if dist_leaf and modkit_base:
        if "tch" in dist_leaf.lower():
            return [
                modkit_base,
                "/lib64/libgomp.so.1",
                "/lib64/libstdc++.so.6",
                "/lib64/libgcc_s.so.1",
            ]
        return [modkit_base]
    if binary_dir:
        return [binary_dir]
    return []


def _build_cluster_modkit_profile(modkit_dir: str) -> str:
    modkit_base, _binary_dir, dist_leaf = _split_cluster_modkit_paths(modkit_dir)
    if not modkit_base:
        return ""
    path_export = "export PATH=${MODKITBASE}:${PATH}\n"
    model_export = f"export MODKITMODEL=${{MODKITBASE}}/models/{_DEFAULT_CLUSTER_MODKIT_MODEL_NAME}\n"
    libtorch_exports = ""
    if dist_leaf:
        path_export = f"export PATH=${{MODKITBASE}}/{dist_leaf}:${{PATH}}\n"
        model_export = f"export MODKITMODEL=${{MODKITBASE}}/{dist_leaf}/models/{_DEFAULT_CLUSTER_MODKIT_MODEL_NAME}\n"
        if "tch" in dist_leaf.lower():
            libtorch_exports = (
                "export LIBTORCH=${MODKITBASE}/libtorch\n"
                "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}\n"
                "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}\n"
            )
    return (
        f"export MODKITBASE={modkit_base}\n"
        f"{path_export}"
        f"{model_export}"
        f"{libtorch_exports}"
    )


_DEFAULT_CLUSTER_MODKIT_PROFILE = _build_cluster_modkit_profile(_DEFAULT_CLUSTER_MODKIT_BINARY_DIR)
_DEFAULT_CLUSTER_MODKIT_BIND_PATHS = _default_cluster_modkit_bind_paths(_DEFAULT_CLUSTER_MODKIT_BINARY_DIR)


def _extract_modkit_binary_dir_from_profile(profile_text: str) -> str:
    modkit_base = ""
    lines = str(profile_text or "").splitlines()

    for line in lines:
        cleaned = line.strip()
        if cleaned.startswith("export MODKITBASE="):
            modkit_base = cleaned.split("=", 1)[1].strip().rstrip("/")
            break

    for line in lines:
        cleaned = line.strip()
        if not cleaned.startswith("export PATH="):
            continue
        path_value = cleaned.split("=", 1)[1].strip()
        if modkit_base and path_value == "${MODKITBASE}:${PATH}":
            return modkit_base
        if modkit_base and path_value.startswith("${MODKITBASE}/") and path_value.endswith(":${PATH}"):
            relative_dir = path_value[len("${MODKITBASE}/"):-len(":${PATH}")].strip().strip("/")
            if relative_dir:
                return f"{modkit_base}/{relative_dir}"
        if path_value.endswith(":${PATH}"):
            first_segment = path_value[:-len(":${PATH}")].split(":", 1)[0].strip().rstrip("/")
            if first_segment.startswith("/") and "modkit" in first_segment.lower():
                return first_segment

    for line in lines:
        cleaned = line.strip()
        if not cleaned.startswith("export MODKITMODEL="):
            continue
        model_value = cleaned.split("=", 1)[1].strip().rstrip("/")
        if modkit_base and model_value.startswith("${MODKITBASE}/") and "/models/" in model_value:
            relative_dir = model_value[len("${MODKITBASE}/"):].split("/models/", 1)[0].strip().strip("/")
            if relative_dir:
                return f"{modkit_base}/{relative_dir}"
            return modkit_base
        if model_value.startswith("/") and "/models/" in model_value:
            return model_value.split("/models/", 1)[0].rstrip("/")

    return modkit_base


def _resolve_custom_cluster_modkit_values(
    *,
    modkit_dir: str,
    use_default_bind_paths: bool,
    custom_bind_paths_text: str,
    manual_profile_override: bool,
    manual_profile_text: str,
) -> dict:
    resolved_modkit_dir = str(modkit_dir or "").strip().rstrip("/") or _DEFAULT_CLUSTER_MODKIT_BINARY_DIR
    generated_profile = _build_cluster_modkit_profile(resolved_modkit_dir)
    default_bind_paths_text = _paths_to_text(_default_cluster_modkit_bind_paths(resolved_modkit_dir))
    resolved_bind_paths_text = default_bind_paths_text if use_default_bind_paths else _paths_to_text(custom_bind_paths_text)
    resolved_profile = generated_profile
    if manual_profile_override and str(manual_profile_text or "").strip():
        resolved_profile = str(manual_profile_text)
    return {
        "modkit_dir": resolved_modkit_dir,
        "generated_profile": generated_profile,
        "resolved_bind_paths_text": resolved_bind_paths_text,
        "resolved_profile": resolved_profile,
    }


def _paths_to_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return "\n".join(str(item).strip() for item in value if str(item or "").strip())


def _text_to_paths(value: str) -> list[str]:
    normalized: list[str] = []
    for line in str(value or "").replace(",", "\n").splitlines():
        cleaned = line.strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _pending_gate_slurm_default_refresh_payload(*, status, content: dict | None) -> dict | None:
    if str(status or "").strip().upper() != "PENDING":
        return None
    if not isinstance(content, dict):
        return None
    if isinstance(content.get("edited_params"), dict):
        return None

    extracted_params = content.get("extracted_params")
    if not isinstance(extracted_params, dict):
        return None
    if (extracted_params.get("execution_mode") or "local") != "slurm":
        return None

    current_memory_gb = extracted_params.get("slurm_memory_gb")
    if current_memory_gb not in {None, "", _LEGACY_SLURM_DEFAULT_CPU_MEMORY_GB}:
        return None

    refreshed_content = dict(content)
    refreshed_params = dict(extracted_params)
    refreshed_params["slurm_memory_gb"] = SLURM_DEFAULT_CPU_MEMORY_GB
    refreshed_content["extracted_params"] = refreshed_params
    return refreshed_content


def _prime_post_approval_refresh_state() -> None:
    st.session_state["_has_running_job"] = True
    st.session_state["_has_full_refresh_job"] = True
    st.session_state.pop("_job_finished_at", None)
    st.session_state["_suppress_auto_refresh_until"] = 0.0


def render_block_part1(
    *,
    btype,
    block,
    content,
    status,
    block_id,
    user,
    API_URL,
    LIVE_JOB_STATUS_TIMEOUT_SECONDS,
    make_authenticated_request,
    get_cached_job_status,
    _render_md_with_dataframes,
    _render_embedded_dataframes,
    _find_related_workflow_plan,
    _workflow_highlight_steps,
    _render_workflow_plot_payload,
    show_metadata,
    _load_user_ssh_profiles,
    _active_project_slug,
    _slugify_project_name,
    _render_profile_path_template,
    _block_timestamp,
):
    handled = False
    if btype == "USER_MESSAGE":
        handled = True
        with st.chat_message("user"):
            with st.container(border=True):
                _ts = _block_timestamp()
                _c1, _c2 = st.columns([3, 2])
                with _c1:
                    status_chip("pending", label="User", icon="👤")
                with _c2:
                    if _ts:
                        st.caption(_ts)
                st.write(content.get("text", ""))

    elif btype == "AGENT_PLAN":
        handled = True
        with st.chat_message("assistant", avatar="🤖"):
            section_header("Agent Response", "Summary first, details on demand", icon="🤖")
            show_metadata()
            st.divider()
            if "markdown" in content:
                md = content["markdown"]
                # Split out raw query results into a collapsible expander
                DETAILS_START = "<details><summary>"
                DETAILS_END = "</details>"
                if DETAILS_START in md and DETAILS_END in md:
                    main_part = md[:md.index(DETAILS_START)].rstrip().rstrip("---").rstrip()
                    details_block = md[md.index(DETAILS_START):md.index(DETAILS_END) + len(DETAILS_END)]
                    # Extract the summary text and body
                    import re as _re
                    details_match = _re.search(
                        r'<details><summary>(.*?)</summary>(.*)',
                        details_block, _re.DOTALL
                    )
                    if details_match:
                        summary_text = details_match.group(1).strip()
                        details_body = details_match.group(2).strip()
                        _render_md_with_dataframes(main_part, block_id, "main")
                        # ── Render visible DataFrames (with DF IDs) between answer and raw details ──
                        _dfs = content.get("_dataframes")
                        if _dfs and isinstance(_dfs, dict):
                            _render_embedded_dataframes(_dfs, block_id, only_visible=True)
                        with st.expander(summary_text, expanded=False):
                            # Non-visible (supplementary) DFs go inside raw details
                            if _dfs and isinstance(_dfs, dict):
                                _render_embedded_dataframes(_dfs, block_id, only_visible=False)
                            _render_md_with_dataframes(details_body, block_id, "det")
                    else:
                        _render_md_with_dataframes(md, block_id, "main")
                        _dfs = content.get("_dataframes")
                        if _dfs and isinstance(_dfs, dict):
                            _render_embedded_dataframes(_dfs, block_id)
                else:
                    _render_md_with_dataframes(md, block_id, "main")
                    # ── Render embedded DataFrames after plain markdown ──
                    _dfs = content.get("_dataframes")
                    if _dfs and isinstance(_dfs, dict):
                        _render_embedded_dataframes(_dfs, block_id)

            # ── Inline sync progress (visible right in the agent response) ──
            _sync_run_uuid = content.get("_sync_run_uuid", "")
            if _sync_run_uuid:
                _sync_detail = ""
                _sync_message = ""
                _sync_state = (
                    st.session_state.get(f"_transfer_state_{_sync_run_uuid}") or "downloading_outputs"
                ).strip().lower()
                _sj, _ = get_cached_job_status(_sync_run_uuid)
                if isinstance(_sj, dict):
                    _sync_state = (_sj.get("transfer_state") or _sync_state).strip().lower()
                    _sync_detail = (_sj.get("transfer_detail") or "").strip()
                    _sync_message = (_sj.get("message") or "").strip()
                if _sync_state == "downloading_outputs":
                    st.info(f"📥 **Sync in progress** — {_sync_detail or 'transferring files…'}", icon="⏳")
                elif _sync_state == "outputs_downloaded":
                    st.success("✅ Results synced successfully.")
                elif _sync_state == "transfer_failed":
                    _detail = _sync_detail or _sync_message
                    if _detail:
                        st.error(
                            f"❌ Sync failed: {_detail}\n\nYou can retry with **sync results locally with force**."
                        )
                    else:
                        st.error("❌ Sync failed. You can retry with **sync results locally with force**.")

            _all_blocks = st.session_state.get("blocks", [])
            _related_workflow = _find_related_workflow_plan(block, _all_blocks)
            _workflow_highlights = _workflow_highlight_steps(_related_workflow)
            if _workflow_highlights:
                st.divider()
                st.markdown("**Workflow Results**")
                for _wf_idx, _wf_step in enumerate(_workflow_highlights, start=1):
                    _wf_title = _wf_step.get("title") or _wf_step.get("kind") or f"Workflow step {_wf_idx}"
                    st.caption(_wf_title)
                    _wf_result = _wf_step.get("result")
                    if isinstance(_wf_result, dict):
                        _render_workflow_plot_payload(_wf_result, block_id, f"agent_{_wf_idx}")
                        _wf_markdown = _wf_result.get("markdown")
                        if isinstance(_wf_markdown, str) and _wf_markdown.strip():
                            st.markdown(_wf_markdown)

            # ── Render embedded images (DE plots, etc.) ──
            _images = content.get("_images")
            if _images and isinstance(_images, list):
                import base64
                for _img_idx, _img in enumerate(_images):
                    _b64 = _img.get("data_b64", "")
                    _label = _img.get("label", "Plot")
                    if _b64:
                        _img_bytes = base64.b64decode(_b64)
                        st.image(_img_bytes, caption=_label, use_container_width=True)

            # ── Per-message token count ──
            _msg_tokens = content.get("tokens")
            if _msg_tokens and _msg_tokens.get("total_tokens"):
                _tt = _msg_tokens["total_tokens"]
                _pt = _msg_tokens.get("prompt_tokens", 0)
                _ct = _msg_tokens.get("completion_tokens", 0)
                _mn = _msg_tokens.get("model", "")
                _tok_label = f"🪙 {_tt:,} tokens  (↑{_pt:,} prompt · ↓{_ct:,} completion)"
                if _mn:
                    _tok_label += f"  ·  `{_mn}`"
                st.caption(_tok_label)

            # ── Debug panel (only when debug toggle is on) ──
            _debug_info = content.get("_debug")
            if _debug_info and st.session_state.get("_debug_mode"):
                with st.expander("🐛 Debug Info", expanded=False):
                    import json as _json
                    st.code(_json.dumps(_debug_info, indent=2, default=str), language="json")

    elif btype == "APPROVAL_GATE":
        handled = True
        with st.chat_message("assistant", avatar="🚦"):
            refreshed_content = _pending_gate_slurm_default_refresh_payload(status=status, content=content)
            if refreshed_content is not None:
                try:
                    resp = make_authenticated_request(
                        "PATCH",
                        f"{API_URL}/block/{block_id}",
                        json={"payload": refreshed_content},
                    )
                    if getattr(resp, "status_code", None) == 200:
                        content = refreshed_content
                except Exception:
                    pass

            # Get extracted parameters and metadata
            extracted_params = content.get("extracted_params", {})
            approved_params = content.get("edited_params") or extracted_params
            manual_mode = content.get("manual_mode", False)
            attempt_number = content.get("attempt_number", 1)
            rejection_history = content.get("rejection_history", [])
            
            section_header("Approval Review", "Review and edit parameters before submission", icon="🚦")
            _chip = "pending"
            if status == "APPROVED":
                _chip = "complete"
            elif status == "REJECTED":
                _chip = "failed"
            status_chip(_chip, label=status.title(), icon="🧾")
            metadata_row({"Attempt": f"{attempt_number}/3", "Mode": "Manual" if manual_mode else "AI Extracted", "Block": block_id[:8]})
            st.divider()

            # Title based on mode
            if manual_mode:
                info_callout(
                    "The AI could not confidently extract parameters after multiple attempts. Please verify manually.",
                    kind="warning",
                    icon="⚠️",
                )
            else:
                st.caption(f"Approval required (attempt {attempt_number}/3)")
            
            st.write(content.get("label", "Approve this plan?"))
            st.caption(f"Block ID: `{block_id}`")

            _summary = {}
            _src_params = approved_params if isinstance(approved_params, dict) else extracted_params
            if isinstance(_src_params, dict):
                for _k in [
                    "sample_name",
                    "mode",
                    "input_type",
                    "input_directory",
                    "execution_mode",
                    "entry_point",
                    "result_destination",
                    "ssh_profile_nickname",
                    "remote_base_path",
                ]:
                    _v = _src_params.get(_k)
                    if _v not in (None, "", [], {}):
                        _summary[_k.replace("_", " ").title()] = _v
                _summary["Gate Action"] = extracted_params.get("gate_action") or content.get("gate_action", "job")
                if (extracted_params.get("gate_action") or content.get("gate_action")) == "compare_region_overlaps":
                    if _src_params.get("sample_a_label"):
                        _summary["Sample A"] = _src_params.get("sample_a_label")
                    if _src_params.get("sample_b_label"):
                        _summary["Sample B"] = _src_params.get("sample_b_label")
                    if _src_params.get("plot_title"):
                        _summary["Plot Title"] = _src_params.get("plot_title")
                    if _src_params.get("selected_file_a"):
                        _summary["Resolved BED A"] = _src_params.get("selected_file_a")
                    if _src_params.get("selected_file_b"):
                        _summary["Resolved BED B"] = _src_params.get("selected_file_b")
            if _summary:
                with st.expander("Plan Summary", expanded=True):
                    review_panel(_summary, title="Ready-to-Run Parameters")
            
            # Show rejection history if exists
            if rejection_history:
                with st.expander(f"📜 Rejection History ({len(rejection_history)} previous attempts)", expanded=False):
                    for i, hist in enumerate(rejection_history, 1):
                        st.text(f"Attempt {hist.get('attempt', i)}: {hist.get('reason', 'No reason')}")
                        st.caption(f"at {hist.get('timestamp', 'unknown time')}")

            if status == "APPROVED":
                st.success("✅ Approved")
                if isinstance(approved_params, dict):
                    _approved_custom_profile = str(approved_params.get("custom_dogme_profile") or "")
                    _approved_bind_paths = approved_params.get("custom_dogme_bind_paths") or []
                    if _approved_custom_profile.strip() or _approved_bind_paths:
                        with st.expander("🧬 Submitted Custom Dogme Settings", expanded=False):
                            st.caption("These are the custom DNA overrides that were approved for this run.")
                            st.markdown("**Bind Paths**")
                            st.code(_paths_to_text(_approved_bind_paths).strip() or "(none)", language="text")
                            st.markdown("**dogme.profile**")
                            st.code(_approved_custom_profile.strip() or "(empty)", language="bash")
                # Show what parameters were used
                if approved_params:
                    with st.expander("📋 Parameters Used", expanded=False):
                        st.json(approved_params)
                        
            elif status == "REJECTED":
                st.error("❌ Rejected")
                # Show rejection reason if available
                reason = content.get("rejection_reason", "No reason provided")
                st.caption(f"Reason: {reason}")
                
            else:
                # Pending approval - show editable parameter form
                _gate_action = extracted_params.get("gate_action") or content.get("gate_action", "job")

                if _gate_action == "local_sample_existing":
                    _src = extracted_params.get("input_directory", "")
                    _dst = extracted_params.get("staged_input_directory", "")
                    if _src:
                        st.caption(f"Source: `{_src}`")
                    if _dst:
                        st.caption(f"Staged folder: `{_dst}`")

                    col1, col2 = st.columns(2)
                    if col1.button("✅ Reuse Existing Copy", key=f"reuse_stage_{block_id}"):
                        payload_update = dict(content)
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "APPROVED", "payload": payload_update}
                        )
                        _prime_post_approval_refresh_state()
                        st.rerun()
                    if col2.button("♻️ Replace With Fresh Copy", key=f"replace_stage_{block_id}"):
                        payload_update = dict(content)
                        payload_update["rejection_reason"] = "Replace existing staged sample folder"
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "REJECTED", "payload": payload_update}
                        )
                        st.rerun()

                elif _gate_action == "download" and extracted_params.get("files"):
                    # ── Download approval form ──
                    _dl_files = extracted_params["files"]
                    _dl_total = extracted_params.get("total_size_bytes", 0)
                    _dl_target = extracted_params.get("target_dir", "data/")
                    _dl_mb = round(_dl_total / (1024 * 1024), 1) if _dl_total else "?"

                    st.write(f"**📥 Download Plan** — {len(_dl_files)} file(s), ~{_dl_mb} MB → `{_dl_target}`")
                    for _f in _dl_files:
                        _fname = _f.get("filename", "?")
                        _fsize = _f.get("size_bytes")
                        _fmb = f" ({round(_fsize / (1024 * 1024), 1)} MB)" if _fsize else ""
                        st.markdown(f"- `{_fname}`{_fmb}")

                    col1, col2 = st.columns(2)
                    if col1.button("✅ Approve Download", key=f"dl_approve_{block_id}"):
                        payload_update = dict(content)
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "APPROVED", "payload": payload_update}
                        )
                        _prime_post_approval_refresh_state()
                        st.rerun()
                    if col2.button("❌ Cancel", key=f"dl_reject_{block_id}"):
                        payload_update = dict(content)
                        payload_update["rejection_reason"] = "User cancelled download"
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "REJECTED", "payload": payload_update}
                        )
                        st.rerun()

                elif _gate_action == "remote_stage":
                    st.write("**📤 Remote Staging Plan**")

                    _current_user_id = user.get("id") or user.get("user_id", "")
                    _saved_profiles = _load_user_ssh_profiles(_current_user_id)
                    _profile_by_id = {
                        profile.get("id"): profile
                        for profile in _saved_profiles
                        if profile.get("id")
                    }

                    with st.form(key=f"remote_stage_form_{block_id}"):
                        grouped_section("Sample & Input")
                        sample_name = st.text_input(
                            "Sample Name",
                            value=extracted_params.get("sample_name", ""),
                            help="Name to register for the staged sample."
                        )

                        mode_options = ["DNA", "RNA", "CDNA"]
                        current_mode = extracted_params.get("mode", "DNA")
                        mode_index = mode_options.index(current_mode) if current_mode in mode_options else 0
                        mode = st.selectbox("Analysis Mode", mode_options, index=mode_index)

                        input_directory = st.text_input(
                            "Input Directory",
                            value=extracted_params.get("input_directory", ""),
                            help="Local source folder that will be staged to the remote data cache."
                        )

                        genome_options = ["GRCh38", "mm39"]
                        current_genomes = extracted_params.get("reference_genome", ["mm39"])
                        if isinstance(current_genomes, str):
                            if current_genomes.startswith("["):
                                try:
                                    import json as _json
                                    current_genomes = _json.loads(current_genomes)
                                except (ValueError, TypeError):
                                    current_genomes = [current_genomes]
                            else:
                                current_genomes = [current_genomes]
                        current_genomes = [g for g in current_genomes if g in genome_options] or ["mm39"]
                        reference_genomes = st.multiselect(
                            "Reference Genome(s)",
                            genome_options,
                            default=current_genomes,
                            help="Reference assets that should be available under the remote ref/ cache."
                        )

                        input_type = extracted_params.get("input_type", "pod5")
                        st.caption(f"Input type: `{input_type}`")

                        grouped_section("Remote Target")
                        ssh_profile_id = extracted_params.get("ssh_profile_id") or ""
                        ssh_profile_nickname = extracted_params.get("ssh_profile_nickname", "") or ""
                        remote_base_path = extracted_params.get("remote_base_path", "") or ""

                        _selected_profile_id = ssh_profile_id if ssh_profile_id in _profile_by_id else ""
                        if not _selected_profile_id and ssh_profile_nickname:
                            _nickname = ssh_profile_nickname.strip().lower()
                            _match = next(
                                (
                                    profile
                                    for profile in _saved_profiles
                                    if (
                                        (profile.get("nickname") or "").strip().lower() == _nickname
                                        or (profile.get("ssh_host") or "").strip().lower() == _nickname
                                    )
                                ),
                                None,
                            )
                            _selected_profile_id = (_match or {}).get("id") or ""
                        if not _selected_profile_id and len(_saved_profiles) == 1:
                            _selected_profile_id = _saved_profiles[0].get("id") or ""

                        _profile_options = [""] + list(_profile_by_id.keys())
                        _selected_profile_id = st.selectbox(
                            "Saved SSH Profile",
                            options=_profile_options,
                            index=_profile_options.index(_selected_profile_id) if _selected_profile_id in _profile_options else 0,
                            format_func=lambda profile_id: (
                                "(manual entry)"
                                if not profile_id
                                else (
                                    _profile_by_id[profile_id].get("nickname")
                                    or _profile_by_id[profile_id].get("ssh_host")
                                    or profile_id
                                )
                            ),
                            key=f"remote_stage_profile_{block_id}",
                            help="Choose the remote profile that will receive the staged sample data.",
                        )

                        _selected_profile = _profile_by_id.get(_selected_profile_id) if _selected_profile_id else None
                        if _selected_profile:
                            ssh_profile_id = _selected_profile_id
                            ssh_profile_nickname = (
                                _selected_profile.get("nickname")
                                or _selected_profile.get("ssh_host")
                                or ssh_profile_nickname
                            )
                            if not remote_base_path:
                                _project_slug = _active_project_slug()
                                _template_context = {
                                    "user_id": _current_user_id,
                                    "project_id": st.session_state.get("active_project_id", ""),
                                    "project_slug": _project_slug,
                                    "sample_name": sample_name or "sample",
                                    "workflow_slug": _slugify_project_name(sample_name or "sample"),
                                    "ssh_username": _selected_profile.get("ssh_username") or "agoutic",
                                }
                                remote_base_path = (
                                    _render_profile_path_template(
                                        _selected_profile.get("remote_base_path"),
                                        _template_context,
                                    )
                                    or ""
                                )

                        ssh_profile_nickname = st.text_input(
                            "SSH Profile Nickname",
                            value=ssh_profile_nickname,
                            help="Saved Remote Profile nickname, such as hpc3."
                        )
                        if ssh_profile_id:
                            st.caption(f"Existing SSH profile ID will be reused unless you change the nickname: {ssh_profile_id}")

                        remote_base_path = st.text_input(
                            "Remote Base Path",
                            value=remote_base_path,
                            help="Top-level remote folder that contains ref/, data/, and project workflow folders."
                        )

                        local_workflow_directory = extracted_params.get("local_workflow_directory", "") or ""
                        if local_workflow_directory:
                            st.caption(f"Local workflow folder: {local_workflow_directory}")

                        st.divider()
                        col1, col2 = st.columns(2)
                        submit_approve = col1.form_submit_button("✅ Approve Staging", width="stretch")
                        submit_reject = col2.form_submit_button("❌ Cancel Staging", width="stretch")

                        if submit_approve:
                            remote_input_path = extracted_params.get("remote_input_path") or ""
                            if not remote_input_path and isinstance(input_directory, str) and input_directory.lower().startswith("remote:"):
                                candidate = input_directory[len("remote:"):].strip()
                                if candidate.startswith("/"):
                                    remote_input_path = candidate.rstrip('.,;:!?')
                            edited_params = {
                                "sample_name": sample_name,
                                "mode": mode,
                                "input_type": input_type,
                                "input_directory": input_directory,
                                "reference_genome": reference_genomes,
                                "execution_mode": "slurm",
                                "remote_action": "stage_only",
                                "gate_action": "remote_stage",
                                "ssh_profile_id": ssh_profile_id or None,
                                "ssh_profile_nickname": ssh_profile_nickname or None,
                                "remote_base_path": remote_base_path or None,
                                "local_workflow_directory": local_workflow_directory or None,
                                "remote_input_path": remote_input_path or None,
                                "staged_remote_input_path": remote_input_path or None,
                                "result_destination": extracted_params.get("result_destination") or ("both" if remote_input_path else "local"),
                            }
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            if resp.status_code == 200:
                                _prime_post_approval_refresh_state()
                                st.rerun()
                            else:
                                try:
                                    error_detail = resp.json().get("detail") or resp.text
                                except Exception:
                                    error_detail = resp.text
                                st.error(f"Approval failed: {error_detail}")

                        if submit_reject:
                            payload_update = dict(content)
                            payload_update["rejection_reason"] = "User cancelled remote staging"
                            make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "REJECTED", "payload": payload_update}
                            )
                            st.rerun()

                elif _gate_action == "reconcile_bams" and extracted_params:
                    preflight_summary = extracted_params.get("preflight_summary") or {}
                    bam_inputs = extracted_params.get("bam_inputs") or []
                    reference = extracted_params.get("reference") or "unknown"
                    annotation_gtf = extracted_params.get("annotation_gtf") or "not resolved"
                    annotation_gtf_source = extracted_params.get("annotation_gtf_source") or "unknown"
                    annotation_evidence = extracted_params.get("annotation_evidence") or []
                    output_directory = extracted_params.get("output_directory") or extracted_params.get("input_directory") or ""
                    output_prefix = extracted_params.get("output_prefix") or extracted_params.get("sample_name") or "reconciled"
                    gene_prefix_default = extracted_params.get("gene_prefix") or "CONSG"
                    tx_prefix_default = extracted_params.get("tx_prefix") or "CONST"
                    id_tag_default = extracted_params.get("id_tag") or "TX"
                    gene_tag_default = extracted_params.get("gene_tag") or "GX"
                    _max_threads = int(os.environ.get("RECONCILE_BAMS_MAX_THREADS", "8"))
                    _default_threads = int(os.environ.get("RECONCILE_BAMS_DEFAULT_THREADS", "4"))
                    threads_default = min(int(extracted_params.get("threads") or _default_threads), _max_threads)
                    exon_merge_distance_default = int(extracted_params.get("exon_merge_distance") or 5)
                    min_tpm_default = float(extracted_params.get("min_tpm") if extracted_params.get("min_tpm") is not None else 1.0)
                    min_samples_default = int(extracted_params.get("min_samples") or 2)
                    filter_known_default = bool(extracted_params.get("filter_known"))
                    underlying_script_id = extracted_params.get("underlying_script_id") or "reconcile_bams/reconcileBams"

                    with st.form(key=f"reconcile_form_{block_id}"):
                        grouped_section("Reconcile Summary")
                        st.write(f"**Reference**: `{reference}`")
                        st.write(f"**Execution Script**: `{underlying_script_id}`")

                        grouped_section("Reconcile Settings")
                        output_directory = st.text_input("Workflow Root", value=output_directory, help="Parent project directory where the next workflowN folder will be created.")
                        output_prefix = st.text_input("Output Prefix", value=output_prefix)
                        annotation_gtf = st.text_input("Annotation GTF", value=annotation_gtf)
                        st.caption(f"GTF source: `{annotation_gtf_source}`")
                        col1, col2 = st.columns(2)
                        gene_prefix = col1.text_input("Gene Prefix", value=gene_prefix_default)
                        tx_prefix = col2.text_input("Transcript Prefix", value=tx_prefix_default)
                        col3, col4 = st.columns(2)
                        id_tag = col3.text_input("Transcript ID Tag", value=id_tag_default)
                        gene_tag = col4.text_input("Gene ID Tag", value=gene_tag_default)
                        col5, col6 = st.columns(2)
                        threads = col5.number_input("Threads", min_value=1, max_value=_max_threads, value=threads_default, step=1, help=f"Capped at {_max_threads} to avoid host starvation")
                        exon_merge_distance = col6.number_input("Exon Merge Distance", min_value=0, value=exon_merge_distance_default, step=1)
                        col7, col8 = st.columns(2)
                        min_tpm = col7.number_input("Min TPM", min_value=0.0, value=min_tpm_default, step=0.1, format="%.3f")
                        min_samples = col8.number_input("Min Samples", min_value=1, value=min_samples_default, step=1)
                        filter_known = st.checkbox("Filter Known Transcripts", value=filter_known_default)

                        grouped_section("Validated Inputs")
                        st.write(f"{len(bam_inputs)} annotated BAM(s) passed preflight validation.")
                        for bam in bam_inputs:
                            if not isinstance(bam, dict):
                                continue
                            sample_label = bam.get("sample") or "sample"
                            bam_path = bam.get("path") or ""
                            st.caption(f"{sample_label}: `{bam_path}`")

                        if annotation_evidence:
                            grouped_section("Annotation Provenance")
                            for item in annotation_evidence:
                                if not isinstance(item, dict):
                                    continue
                                evidence_file = item.get("file") or "config"
                                evidence_line = item.get("line")
                                configured_gtf = item.get("configured_annotation_gtf") or ""
                                evidence_gtf = item.get("annotation_gtf") or ""
                                if configured_gtf and evidence_gtf and configured_gtf != evidence_gtf:
                                    st.caption(
                                        f"{evidence_file}:{evidence_line} -> `{configured_gtf}` mapped to `{evidence_gtf}`"
                                    )
                                else:
                                    st.caption(f"{evidence_file}:{evidence_line} -> `{evidence_gtf}`")

                        message = preflight_summary.get("message") if isinstance(preflight_summary, dict) else None
                        if message:
                            st.info(message)

                        st.divider()
                        col1, col2 = st.columns(2)
                        submit_approve = col1.form_submit_button("✅ Approve Reconcile", width="stretch")
                        submit_reject = col2.form_submit_button("❌ Reject", width="stretch")

                        if submit_approve:
                            edited_params = dict(extracted_params)
                            edited_params.update(
                                {
                                    "output_directory": output_directory,
                                    "input_directory": output_directory,
                                    "output_prefix": output_prefix,
                                    "sample_name": output_prefix,
                                    "annotation_gtf": annotation_gtf,
                                    "gene_prefix": gene_prefix,
                                    "tx_prefix": tx_prefix,
                                    "id_tag": id_tag,
                                    "gene_tag": gene_tag,
                                    "threads": int(threads),
                                    "exon_merge_distance": int(exon_merge_distance),
                                    "min_tpm": float(min_tpm),
                                    "min_samples": int(min_samples),
                                    "filter_known": bool(filter_known),
                                    "script_id": "reconcile_bams/reconcile_bams",
                                }
                            )
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            if resp.status_code == 200:
                                _prime_post_approval_refresh_state()
                                st.rerun()
                            else:
                                try:
                                    error_detail = resp.json().get("detail") or resp.text
                                except Exception:
                                    error_detail = resp.text
                                st.error(f"Approval failed: {error_detail}")

                        if submit_reject:
                            st.session_state[f"rejecting_{block_id}"] = True
                            st.rerun()

                elif _gate_action == "compare_region_overlaps" and extracted_params:
                    sample_a_label_default = extracted_params.get("sample_a_label") or "Sample A"
                    sample_b_label_default = extracted_params.get("sample_b_label") or "Sample B"
                    selected_file_a = extracted_params.get("selected_file_a") or ""
                    selected_file_b = extracted_params.get("selected_file_b") or ""
                    selected_file_a_candidates = int(extracted_params.get("selected_file_a_candidates") or 0)
                    selected_file_b_candidates = int(extracted_params.get("selected_file_b_candidates") or 0)
                    output_directory_default = extracted_params.get("output_directory") or extracted_params.get("input_directory") or ""
                    script_args_default = extracted_params.get("script_args") or []
                    plot_title_default = extracted_params.get("plot_title") or ""
                    min_overlap_default = 1
                    plot_type_default = "venn"
                    if isinstance(script_args_default, list):
                        try:
                            if "--min-overlap-bp" in script_args_default:
                                _idx = script_args_default.index("--min-overlap-bp")
                                min_overlap_default = int(script_args_default[_idx + 1])
                        except Exception:
                            min_overlap_default = 1
                    plot_type_default = str(extracted_params.get("plot_type") or "venn").strip().lower() or "venn"

                    with st.form(key=f"overlap_form_{block_id}"):
                        grouped_section("Overlap Summary")
                        st.write(f"**Plot Type**: `{plot_type_default}`")
                        st.write("**Input Mode**: `BED overlap script`")
                        _script_id = extracted_params.get("script_id") or "analyze_job_results/compare_bed_region_overlaps"
                        st.write(f"**Execution Script**: `{_script_id}`")

                        grouped_section("Resolved BED Inputs")
                        st.write(f"**{sample_a_label_default}**")
                        st.code(selected_file_a or "No BED file resolved", language="text")
                        if selected_file_a_candidates:
                            st.caption(f"Candidate BED files found: {selected_file_a_candidates}")

                        st.write(f"**{sample_b_label_default}**")
                        st.code(selected_file_b or "No BED file resolved", language="text")
                        if selected_file_b_candidates:
                            st.caption(f"Candidate BED files found: {selected_file_b_candidates}")

                        grouped_section("Script Settings")
                        sample_a_label = st.text_input("Sample A Label", value=sample_a_label_default)
                        sample_b_label = st.text_input("Sample B Label", value=sample_b_label_default)
                        plot_title = st.text_input(
                            "Plot Title",
                            value=plot_title_default,
                            help="Optional title shown at the top of the venn/upset plot.",
                        )
                        output_directory = st.text_input(
                            "Output Directory",
                            value=output_directory_default,
                            help="Workflow folder where the overlap CSV outputs will be written.",
                        )
                        min_overlap_bp = st.number_input(
                            "Minimum Overlap (bp)",
                            min_value=1,
                            value=max(int(min_overlap_default), 1),
                            step=1,
                        )

                        st.divider()
                        col1, col2 = st.columns(2)
                        submit_approve = col1.form_submit_button("✅ Approve Overlap Script", width="stretch")
                        submit_reject = col2.form_submit_button("❌ Reject", width="stretch")

                        if submit_approve:
                            updated_script_args = []
                            if isinstance(script_args_default, list):
                                updated_script_args = list(script_args_default)

                            def _replace_flag(args: list[str], flag: str, value: str) -> list[str]:
                                result = []
                                skip_next = False
                                replaced = False
                                for idx, item in enumerate(args):
                                    if skip_next:
                                        skip_next = False
                                        continue
                                    if item == flag:
                                        result.extend([flag, value])
                                        skip_next = True
                                        replaced = True
                                        continue
                                    result.append(item)
                                if not replaced:
                                    result.extend([flag, value])
                                return result

                            updated_script_args = _replace_flag(updated_script_args, "--sample-a-label", sample_a_label)
                            updated_script_args = _replace_flag(updated_script_args, "--sample-b-label", sample_b_label)
                            updated_script_args = _replace_flag(updated_script_args, "--output-dir", output_directory)
                            updated_script_args = _replace_flag(updated_script_args, "--min-overlap-bp", str(int(min_overlap_bp)))

                            edited_params = dict(extracted_params)
                            edited_params.update(
                                {
                                    "sample_a_label": sample_a_label,
                                    "sample_b_label": sample_b_label,
                                    "plot_title": plot_title,
                                    "output_directory": output_directory,
                                    "input_directory": extracted_params.get("input_directory") or ".",
                                    "script_args": updated_script_args,
                                    "gate_action": "compare_region_overlaps",
                                }
                            )
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            if resp.status_code == 200:
                                _prime_post_approval_refresh_state()
                                st.rerun()
                            else:
                                try:
                                    error_detail = resp.json().get("detail") or resp.text
                                except Exception:
                                    error_detail = resp.text
                                st.error(f"Approval failed: {error_detail}")

                        if submit_reject:
                            st.session_state[f"rejecting_{block_id}"] = True
                            st.rerun()

                elif extracted_params:
                    st.write("**📋 Extracted Parameters** (edit if needed):")
                    
                    with st.form(key=f"params_form_{block_id}"):
                        grouped_section("Core Run Settings")
                        # Sample name
                        sample_name = st.text_input(
                            "Sample Name",
                            value=extracted_params.get("sample_name", ""),
                            help="Name for this sample"
                        )
                        
                        # Mode selection
                        mode_options = ["DNA", "RNA", "CDNA"]
                        current_mode = extracted_params.get("mode", "DNA")
                        mode_index = mode_options.index(current_mode) if current_mode in mode_options else 0
                        mode = st.selectbox("Analysis Mode", mode_options, index=mode_index)
                        
                        # Input type
                        input_type_options = ["pod5", "bam"]
                        current_input_type = extracted_params.get("input_type", "pod5")
                        input_type_index = input_type_options.index(current_input_type) if current_input_type in input_type_options else 0
                        input_type = st.selectbox("Input Type", input_type_options, index=input_type_index)
                        
                        # Entry point (Dogme workflow)
                        entry_point_options = ["(auto)", "basecall", "remap", "modkit", "annotateRNA", "reports"]
                        current_entry = extracted_params.get("entry_point") or "(auto)"
                        entry_index = entry_point_options.index(current_entry) if current_entry in entry_point_options else 0
                        entry_point = st.selectbox(
                            "Pipeline Entry Point",
                            entry_point_options,
                            index=entry_index,
                            help="main=(auto) full pipeline, basecall=only basecalling, remap=from unmapped BAM, modkit=modifications only, annotateRNA=transcript annotation, reports=generate reports"
                        )
                        
                        # Input directory
                        input_directory = st.text_input(
                            "Input Directory",
                            value=extracted_params.get("input_directory", ""),
                            help="Full path to input files"
                        )
                        
                        # Reference genomes (multi-select)
                        genome_options = ["GRCh38", "mm39"]  # TODO: fetch from /genomes endpoint
                        current_genomes = extracted_params.get("reference_genome", ["mm39"])
                        # Handle stringified JSON lists from DB (e.g. '["mm39"]')
                        if isinstance(current_genomes, str):
                            if current_genomes.startswith("["):
                                try:
                                    import json as _json
                                    current_genomes = _json.loads(current_genomes)
                                except (ValueError, TypeError):
                                    current_genomes = [current_genomes]
                            else:
                                current_genomes = [current_genomes]
                        # Filter to only valid options
                        current_genomes = [g for g in current_genomes if g in genome_options]
                        if not current_genomes:
                            current_genomes = ["mm39"]
                        reference_genomes = st.multiselect(
                            "Reference Genome(s)",
                            genome_options,
                            default=current_genomes,
                            help="Select one or more reference genomes"
                        )
                        
                        # Modifications (optional)
                        modifications = st.text_input(
                            "Modifications (optional)",
                            value=extracted_params.get("modifications", "") or "",
                            help="Comma-separated modification motifs (leave empty for auto)"
                        )
                        
                        # Max concurrent GPU tasks — visible at top level (not hidden in Advanced)
                        _gpu_raw = extracted_params.get("max_gpu_tasks")
                        _gpu_val = int(_gpu_raw) if _gpu_raw is not None else None
                        if _gpu_val is not None and _gpu_val < 1:
                            _gpu_val = 1
                        if _gpu_val is not None and _gpu_val > 16:
                            _gpu_val = 16
                        _gpu_options = [None, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
                        _gpu_idx = _gpu_options.index(_gpu_val) if _gpu_val in _gpu_options else 0
                        max_gpu_tasks = st.selectbox(
                            "🖥️ Max Concurrent GPU Tasks",
                            options=_gpu_options,
                            index=_gpu_idx,
                            format_func=lambda value: "No maximum" if value is None else str(value),
                            help="Maximum simultaneous dorado/GPU tasks within a pipeline run. Leave at 'No maximum' to let Nextflow manage concurrency.",
                        )

                        grouped_section("Execution")
                        execution_mode_options = ["local", "slurm"]
                        current_execution_mode = extracted_params.get("execution_mode", "local")
                        execution_mode_index = execution_mode_options.index(current_execution_mode) if current_execution_mode in execution_mode_options else 0
                        execution_mode = st.selectbox(
                            "Execution Mode",
                            execution_mode_options,
                            index=execution_mode_index,
                            format_func=lambda value: "Local" if value == "local" else "HPC3 / SLURM",
                            help="Choose whether this run stays on the AGOUTIC host or is submitted through a remote SLURM profile."
                        )

                        ssh_profile_id = extracted_params.get("ssh_profile_id")
                        ssh_profile_nickname = extracted_params.get("ssh_profile_nickname", "") or ""
                        slurm_account = extracted_params.get("slurm_account", "") or ""
                        slurm_partition = extracted_params.get("slurm_partition", "") or ""
                        slurm_gpu_account = extracted_params.get("slurm_gpu_account", "") or ""
                        slurm_gpu_partition = extracted_params.get("slurm_gpu_partition", "") or ""
                        slurm_cpus = int(extracted_params.get("slurm_cpus") or 4)
                        slurm_memory_gb = int(extracted_params.get("slurm_memory_gb") or SLURM_DEFAULT_CPU_MEMORY_GB)
                        slurm_walltime = extracted_params.get("slurm_walltime", "48:00:00") or "48:00:00"
                        slurm_gpus = max(int(extracted_params.get("slurm_gpus") or 1), 1)
                        slurm_gpu_type = extracted_params.get("slurm_gpu_type", "") or ""
                        remote_base_path = extracted_params.get("remote_base_path", "") or ""
                        local_workflow_directory = extracted_params.get("local_workflow_directory", "") or ""
                        result_destination_default = extracted_params.get("result_destination") or "local"
                        custom_dogme_profile_value = extracted_params.get("custom_dogme_profile") or ""
                        custom_dogme_bind_paths_text = _paths_to_text(extracted_params.get("custom_dogme_bind_paths"))
                        custom_modkit_binary_dir = (
                            _extract_modkit_binary_dir_from_profile(custom_dogme_profile_value)
                            or _DEFAULT_CLUSTER_MODKIT_BINARY_DIR
                        )
                        allow_custom_dogme_profile = False

                        if execution_mode == "slurm":
                            grouped_section("Remote Profile & SLURM")
                            st.caption("Remote execution uses one of your saved SSH profiles. You can refer to it by nickname, for example hpc3.")
                            _current_user_id = user.get("id") or user.get("user_id", "")
                            _saved_profiles = _load_user_ssh_profiles(_current_user_id)
                            _profile_by_id = {
                                profile.get("id"): profile
                                for profile in _saved_profiles
                                if profile.get("id")
                            }

                            _selected_profile_id = ssh_profile_id if ssh_profile_id in _profile_by_id else ""
                            if not _selected_profile_id and ssh_profile_nickname:
                                _nickname = ssh_profile_nickname.strip().lower()
                                _match = next(
                                    (
                                        profile
                                        for profile in _saved_profiles
                                        if (
                                            (profile.get("nickname") or "").strip().lower() == _nickname
                                            or (profile.get("ssh_host") or "").strip().lower() == _nickname
                                        )
                                    ),
                                    None,
                                )
                                _selected_profile_id = (_match or {}).get("id") or ""
                            if not _selected_profile_id and len(_saved_profiles) == 1:
                                _selected_profile_id = _saved_profiles[0].get("id") or ""

                            _profile_options = [""] + list(_profile_by_id.keys())
                            _selected_profile_id = st.selectbox(
                                "Saved SSH Profile",
                                options=_profile_options,
                                index=_profile_options.index(_selected_profile_id) if _selected_profile_id in _profile_options else 0,
                                format_func=lambda profile_id: (
                                    "(manual entry)"
                                    if not profile_id
                                    else (
                                        _profile_by_id[profile_id].get("nickname")
                                        or _profile_by_id[profile_id].get("ssh_host")
                                        or profile_id
                                    )
                                ),
                                key=f"slurm_profile_{block_id}",
                                help="Choose a saved profile to reuse its SLURM defaults and remote path templates.",
                            )

                            _selected_profile = _profile_by_id.get(_selected_profile_id) if _selected_profile_id else None
                            if _selected_profile:
                                ssh_profile_id = _selected_profile_id
                                ssh_profile_nickname = (
                                    _selected_profile.get("nickname")
                                    or _selected_profile.get("ssh_host")
                                    or ssh_profile_nickname
                                )

                                slurm_account = slurm_account or (_selected_profile.get("default_slurm_account") or "")
                                slurm_partition = slurm_partition or (_selected_profile.get("default_slurm_partition") or "")
                                slurm_gpu_account = slurm_gpu_account or (_selected_profile.get("default_slurm_gpu_account") or "")
                                slurm_gpu_partition = slurm_gpu_partition or (_selected_profile.get("default_slurm_gpu_partition") or "")

                                _ssh_username = _selected_profile.get("ssh_username") or "agoutic"
                                _project_slug = _active_project_slug()
                                _workflow_slug = _slugify_project_name(sample_name or "workflow")
                                _remote_root = f"/scratch/{_ssh_username}/agoutic/{_project_slug}/{_workflow_slug}"
                                _template_context = {
                                    "user_id": _current_user_id,
                                    "project_id": st.session_state.get("active_project_id", ""),
                                    "project_slug": _project_slug,
                                    "sample_name": sample_name or "workflow",
                                    "workflow_slug": _workflow_slug,
                                    "ssh_username": _ssh_username,
                                }

                                if not remote_base_path:
                                    remote_base_path = (
                                        _render_profile_path_template(
                                            _selected_profile.get("remote_base_path"),
                                            _template_context,
                                        )
                                        or ""
                                    )
                            elif not _saved_profiles:
                                st.caption("No saved SSH profiles found. Create one from Remote Profiles to avoid re-entering SLURM settings.")

                            if local_workflow_directory:
                                st.caption(f"Local workflow folder: {local_workflow_directory}")
                            ssh_profile_nickname = st.text_input(
                                "SSH Profile Nickname",
                                value=ssh_profile_nickname,
                                help="Saved Remote Profile nickname, such as hpc3."
                            )
                            if ssh_profile_id:
                                st.caption(f"Existing SSH profile ID will be reused unless you change the nickname: {ssh_profile_id}")

                            col_slurm_1, col_slurm_2 = st.columns(2)
                            with col_slurm_1:
                                slurm_account = st.text_input("SLURM Account", value=slurm_account)
                                slurm_gpu_account = st.text_input("GPU Account Override", value=slurm_gpu_account, help="Optional account to use when GPUs are requested.")
                                slurm_cpus = st.number_input("SLURM CPUs", min_value=1, max_value=256, value=slurm_cpus)
                                slurm_walltime = st.text_input("SLURM Walltime", value=slurm_walltime, help="Format HH:MM:SS or D-HH:MM:SS")
                                remote_base_path = st.text_input("Remote Base Path", value=remote_base_path)
                            with col_slurm_2:
                                slurm_partition = st.text_input("SLURM Partition", value=slurm_partition)
                                slurm_gpu_partition = st.text_input("GPU Partition Override", value=slurm_gpu_partition, help="Optional partition to use when GPUs are requested.")
                                slurm_memory_gb = st.number_input(
                                    "SLURM Memory (GB)",
                                    min_value=1,
                                    max_value=2048,
                                    value=slurm_memory_gb,
                                    key=f"slurm_memory_gb_{block_id}",
                                )
                                slurm_gpus = st.number_input("SLURM GPUs", min_value=1, max_value=32, value=slurm_gpus)

                            slurm_gpu_type = st.text_input("GPU Type (optional)", value=slurm_gpu_type)
                            result_destination = st.selectbox(
                                "Result Destination",
                                ["local", "remote", "both"],
                                index=["local", "remote", "both"].index(result_destination_default) if result_destination_default in ["local", "remote", "both"] else 0,
                                help="Choose whether results stay remote, sync back locally, or both."
                            )

                            if mode == "DNA":
                                st.caption(
                                    "DNA SLURM runs now use the shared Dogme SIF with the built-in OpenChromatin GPU runtime. "
                                    "Custom cluster modkit and bind-path overrides are no longer needed in this approval form."
                                )

                            if allow_custom_dogme_profile:
                                grouped_section("Custom Cluster Modkit")
                                use_custom_dogme_profile = st.checkbox(
                                    "Use a custom dogme.profile for this run",
                                    value=bool(custom_dogme_profile_value),
                                    help="Enable this when DNA Dogme should source a cluster-hosted modkit or extra environment variables instead of the default profile.",
                                )
                                custom_modkit_binary_dir = st.text_input(
                                    "Cluster Modkit Binary Directory",
                                    value=custom_modkit_binary_dir,
                                    help="Directory inside the HPC filesystem that contains the modkit binary Dogme should find first on PATH for this DNA run.",
                                ).strip()
                                current_bind_paths = _text_to_paths(custom_dogme_bind_paths_text)
                                _normalized_modkit_dir = custom_modkit_binary_dir or _DEFAULT_CLUSTER_MODKIT_BINARY_DIR
                                _generated_dogme_profile = _build_cluster_modkit_profile(_normalized_modkit_dir)
                                _default_bind_paths = _default_cluster_modkit_bind_paths(_normalized_modkit_dir)
                                _existing_manual_profile = (
                                    custom_dogme_profile_value
                                    if custom_dogme_profile_value.strip() and custom_dogme_profile_value.strip() != _generated_dogme_profile.strip()
                                    else ""
                                )
                                use_default_bind_paths = st.checkbox(
                                    "Bind the modkit directory automatically",
                                    value=not current_bind_paths or current_bind_paths == _default_bind_paths,
                                    help="When enabled, the required modkit path is submitted automatically as the extra Apptainer bind path set for this DNA run.",
                                )
                                manual_profile_override = st.checkbox(
                                    "Edit dogme.profile manually",
                                    value=bool(_existing_manual_profile.strip()),
                                    help="Enable this only when you need exports beyond the standard PATH-based template. Otherwise the generated profile below is used automatically.",
                                )
                                st.caption(
                                    "These host paths will be bound into the Apptainer container for this DNA run. "
                                    "Make sure every path referenced by the profile is visible on the compute nodes."
                                )
                                custom_dogme_bind_paths_text = st.text_area(
                                    "Custom Dogme Bind Paths",
                                    value=custom_dogme_bind_paths_text,
                                    height=80,
                                    help="One path per line. Ignored when automatic binding is enabled. For the tch build, binding the shared modkit root is what exposes both the binary and its sibling libtorch directory.",
                                )
                                manual_dogme_profile_text = st.text_area(
                                    "Manual dogme.profile Override",
                                    value=_existing_manual_profile or _generated_dogme_profile,
                                    height=180,
                                    help="Optional shell exports sourced before each Dogme task. Ignored unless manual editing is enabled.",
                                )
                                _resolved_custom_modkit = _resolve_custom_cluster_modkit_values(
                                    modkit_dir=custom_modkit_binary_dir,
                                    use_default_bind_paths=use_default_bind_paths,
                                    custom_bind_paths_text=custom_dogme_bind_paths_text,
                                    manual_profile_override=manual_profile_override,
                                    manual_profile_text=manual_dogme_profile_text,
                                )
                                with st.expander("Preview Submitted Custom Dogme Settings", expanded=use_custom_dogme_profile):
                                    if use_custom_dogme_profile:
                                        st.caption("These exact values will be submitted if you approve this DNA run.")
                                    else:
                                        st.caption("Enable the checkbox above to submit these custom DNA overrides.")
                                    st.markdown("**Bind Paths**")
                                    st.code(_resolved_custom_modkit["resolved_bind_paths_text"].strip() or "(none)", language="text")
                                    st.markdown("**dogme.profile**")
                                    st.code(_resolved_custom_modkit["resolved_profile"].strip() or "(empty)", language="bash")

                                if use_custom_dogme_profile:
                                    custom_dogme_profile_value = _resolved_custom_modkit["resolved_profile"]
                                    custom_dogme_bind_paths_text = _resolved_custom_modkit["resolved_bind_paths_text"]
                                else:
                                    custom_dogme_profile_value = ""
                                    custom_dogme_bind_paths_text = ""
                            else:
                                custom_dogme_profile_value = ""
                                custom_dogme_bind_paths_text = ""
                        else:
                            result_destination = None
                            custom_dogme_profile_value = ""
                            custom_dogme_bind_paths_text = ""
                        
                        # Advanced parameters in expander
                        with st.expander("⚙️ Advanced Parameters (optional)"):
                            st.caption("Leave empty to use defaults")
                            
                            # modkit_filter_threshold
                            modkit_threshold = st.number_input(
                                "Modkit Filter Threshold",
                                min_value=0.0,
                                max_value=1.0,
                                value=extracted_params.get("modkit_filter_threshold", 0.9),
                                step=0.05,
                                help="Modification calling threshold (default: 0.9)"
                            )
                            
                            # min_cov
                            min_cov_default = extracted_params.get("min_cov")
                            if min_cov_default is None:
                                # Show placeholder based on mode
                                min_cov_placeholder = 1 if mode == "DNA" else 3
                                st.caption(f"Min Coverage: (auto - will use {min_cov_placeholder} for {mode} mode)")
                                min_cov = None
                            else:
                                min_cov = st.number_input(
                                    "Minimum Coverage",
                                    min_value=1,
                                    max_value=100,
                                    value=min_cov_default,
                                    help="Minimum coverage for modification calls"
                                )
                            
                            # per_mod
                            per_mod = st.number_input(
                                "Per Mod Threshold",
                                min_value=1,
                                max_value=100,
                                value=extracted_params.get("per_mod", 5),
                                help="Percentage threshold for modifications (default: 5)"
                            )
                            
                            # accuracy
                            accuracy_options = ["sup", "hac", "fast"]
                            current_accuracy = extracted_params.get("accuracy", "sup")
                            accuracy_index = accuracy_options.index(current_accuracy) if current_accuracy in accuracy_options else 0
                            accuracy = st.selectbox(
                                "Basecalling Accuracy",
                                accuracy_options,
                                index=accuracy_index,
                                help="Model accuracy: sup=super accurate, hac=high accuracy, fast=fast mode"
                            )
                        
                        st.divider()
                        
                        # Action buttons
                        col1, col2 = st.columns(2)
                        
                        submit_approve = col1.form_submit_button("✅ Approve", width="stretch")
                        submit_reject = col2.form_submit_button("❌ Reject", width="stretch")
                        
                        if submit_approve:
                            # Build edited params
                            edited_params = {
                                "sample_name": sample_name,
                                "mode": mode,
                                "input_type": input_type,
                                "entry_point": entry_point if entry_point != "(auto)" else None,
                                "input_directory": input_directory,
                                "reference_genome": reference_genomes,
                                "modifications": modifications if modifications else None,
                                # Advanced parameters
                                "modkit_filter_threshold": modkit_threshold,
                                "min_cov": min_cov,
                                "per_mod": per_mod,
                                "accuracy": accuracy,
                                "max_gpu_tasks": max_gpu_tasks,
                                "custom_dogme_profile": (custom_dogme_profile_value.strip() or None) if allow_custom_dogme_profile else None,
                                "custom_dogme_bind_paths": _text_to_paths(custom_dogme_bind_paths_text) if allow_custom_dogme_profile else [],
                                "execution_mode": execution_mode,
                            }
                            if execution_mode == "slurm":
                                edited_params.update({
                                    "ssh_profile_id": ssh_profile_id,
                                    "ssh_profile_nickname": ssh_profile_nickname or None,
                                    "local_workflow_directory": local_workflow_directory or None,
                                    "slurm_account": slurm_account or None,
                                    "slurm_partition": slurm_partition or None,
                                    "slurm_gpu_account": slurm_gpu_account or None,
                                    "slurm_gpu_partition": slurm_gpu_partition or None,
                                    "slurm_cpus": int(slurm_cpus),
                                    "slurm_memory_gb": int(slurm_memory_gb),
                                    "slurm_walltime": slurm_walltime or None,
                                    "slurm_gpus": int(slurm_gpus),
                                    "slurm_gpu_type": slurm_gpu_type or None,
                                    "remote_base_path": remote_base_path or None,
                                    "result_destination": result_destination,
                                })
                            else:
                                edited_params.update({
                                    "local_workflow_directory": None,
                                    "ssh_profile_id": None,
                                    "ssh_profile_nickname": None,
                                    "slurm_account": None,
                                    "slurm_partition": None,
                                    "slurm_gpu_account": None,
                                    "slurm_gpu_partition": None,
                                    "slurm_cpus": None,
                                    "slurm_memory_gb": None,
                                    "slurm_walltime": None,
                                    "slurm_gpus": None,
                                    "slurm_gpu_type": None,
                                    "remote_base_path": None,
                                    "result_destination": None,
                                })
                            # Preserve resume_from_dir for resubmit-resume flow
                            if extracted_params.get("resume_from_dir"):
                                edited_params["resume_from_dir"] = extracted_params["resume_from_dir"]
                            
                            # Update block with edited params and approved status
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            
                            resp = make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            if resp.status_code == 200:
                                _prime_post_approval_refresh_state()
                                st.rerun()
                            else:
                                try:
                                    error_detail = resp.json().get("detail") or resp.text
                                except Exception:
                                    error_detail = resp.text
                                st.error(f"Approval failed: {error_detail}")
                        
                        if submit_reject:
                            st.session_state[f"rejecting_{block_id}"] = True
                            st.rerun()
                
                # Show rejection feedback form if user clicked reject
                if _gate_action != "local_sample_existing" and st.session_state.get(f"rejecting_{block_id}", False):
                    st.write("**💬 Why are you rejecting this plan?**")
                    rejection_reason = st.text_area(
                        "Feedback",
                        placeholder="E.g., 'Use GRCh38 instead of mm39' or 'Wrong input path'",
                        key=f"rejection_reason_{block_id}"
                    )
                    
                    col1, col2 = st.columns(2)
                    if col1.button("Submit Rejection", key=f"submit_reject_{block_id}"):
                        # Update block with rejection
                        payload_update = dict(content)
                        payload_update["rejection_reason"] = rejection_reason
                        payload_update["attempt_number"] = attempt_number
                        
                        make_authenticated_request(
                            "PATCH",
                            f"{API_URL}/block/{block_id}",
                            json={"status": "REJECTED", "payload": payload_update}
                        )
                        
                        # Clear rejection state
                        del st.session_state[f"rejecting_{block_id}"]
                        st.rerun()
                    
                    if col2.button("Cancel", key=f"cancel_reject_{block_id}"):
                        del st.session_state[f"rejecting_{block_id}"]
                        st.rerun()

    elif btype == "PENDING_ACTION":
        handled = True
        with st.chat_message("assistant", avatar="🧮"):
            summary = content.get("summary", "Saved dataframe action")
            action_call = content.get("action_call") or {}
            params = action_call.get("params") or {}

            section_header("Pending Dataframe Action", "Review and apply the saved transform", icon="🧮")
            chip_kind = "pending"
            if status in {"COMPLETED", "APPROVED", "CONFIRMED"}:
                chip_kind = "complete"
            elif status in {"FAILED", "REJECTED"}:
                chip_kind = "failed"
            status_chip(chip_kind, label=status.title(), icon="🧾")
            metadata_row({"Tool": str(action_call.get("tool") or "cortex"), "Block": block_id[:8]})
            st.divider()

            st.write(summary)
            if params:
                with st.expander("Action Parameters", expanded=False):
                    st.json(params)

            if status == "PENDING":
                info_callout(
                    "This saved dataframe action is bound to this block. Apply it directly or dismiss it without relying on a follow-up yes/no chat message.",
                    kind="info",
                    icon="ℹ️",
                )
                col1, col2 = st.columns(2)
                if col1.button("✅ Apply Action", key=f"pending_apply_{block_id}"):
                    resp = make_authenticated_request(
                        "PATCH",
                        f"{API_URL}/block/{block_id}",
                        json={"status": "APPROVED", "payload": dict(content)},
                    )
                    if resp.status_code == 200:
                        _prime_post_approval_refresh_state()
                        st.rerun()
                    st.error(f"Action failed: {resp.text}")
                if col2.button("❌ Dismiss", key=f"pending_reject_{block_id}"):
                    payload_update = dict(content)
                    payload_update["rejection_reason"] = "User dismissed saved dataframe action"
                    resp = make_authenticated_request(
                        "PATCH",
                        f"{API_URL}/block/{block_id}",
                        json={"status": "REJECTED", "payload": payload_update},
                    )
                    if resp.status_code == 200:
                        st.rerun()
                    st.error(f"Dismiss failed: {resp.text}")
            elif status == "REJECTED":
                st.error("❌ Dismissed")
                if content.get("rejection_reason"):
                    st.caption(f"Reason: {content.get('rejection_reason')}")
            elif status == "FAILED":
                st.error("❌ Action failed")
                if content.get("error"):
                    st.caption(content.get("error"))
            elif status == "COMPLETED":
                st.success("✅ Action applied")
                if content.get("result_df_id"):
                    st.caption(f"Created DF{content.get('result_df_id')}")

    return handled


