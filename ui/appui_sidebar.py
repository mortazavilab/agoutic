import datetime

import streamlit as st


def render_sidebar(
    *,
    user: dict,
    api_url: str,
    agoutic_version: str,
    request_fn,
    logout_button,
    pause_auto_refresh,
    slugify_project_name,
):
    """Render the sidebar and return runtime UI control values."""
    with st.sidebar:
        st.title("🧬 AGOUTIC")
        st.caption(f"v{agoutic_version}")

        st.caption(f"👤 {user.get('display_name', user.get('email'))}")
        if user.get("role") == "admin":
            st.caption("🔑 Admin")

        with st.expander("❓ Help", expanded=False):
            st.caption("**Workflows**")
            if st.button("help", key="help_prompt_help", width="stretch"):
                st.session_state["_help_prompt"] = "help"
                st.rerun()
            if st.button("show commands", key="help_prompt_commands", width="stretch"):
                st.session_state["_help_prompt"] = "show commands"
                st.rerun()
            if st.button("what can you do", key="help_prompt_capabilities", width="stretch"):
                st.session_state["_help_prompt"] = "what can you do"
                st.rerun()
            if st.button("how do I run a workflow", key="help_prompt_workflow", width="stretch"):
                st.session_state["_help_prompt"] = "how do i run a workflow"
                st.rerun()
            if st.button("how do I use remote slurm", key="help_prompt_slurm", width="stretch"):
                st.session_state["_help_prompt"] = "how do i use remote slurm"
                st.rerun()
            st.caption("**Skills**")
            if st.button("/skills", key="help_prompt_skills", width="stretch"):
                st.session_state["_help_prompt"] = "/skills"
                st.rerun()
            st.caption("`/skill <skill_key>`  ·  `/use-skill <skill_key>`")
            st.caption("**Workflow Slash Commands**")
            st.caption("`/use <workflow>`  ·  `/rerun <workflow>`")
            st.caption("`/rename <workflow> <new_name>`  ·  `/delete <workflow>`")
            st.caption("**Dataframes**")
            if st.button("list dfs", key="help_prompt_list_dfs", width="stretch"):
                st.session_state["_help_prompt"] = "list dfs"
                st.rerun()
            if st.button("how do I use dataframes", key="help_prompt_dataframe_help", width="stretch"):
                st.session_state["_help_prompt"] = "how do i use dataframes"
                st.rerun()
            st.caption("`head DF5`  ·  `head DF5 20`  ·  `head c2c12DF`")
            st.caption("`subset DF3 to columns sample, reads`  ·  `rename DF2 columns old to new`")
            st.caption("`summarize DF4 by sample and sum reads`  ·  `plot DF5 color by sample`")
            st.caption("**Differential Expression**")
            if st.button("how do I compare reconcile samples", key="help_prompt_reconcile_de", width="stretch"):
                st.session_state["_help_prompt"] = "how do i compare reconcile samples"
                st.rerun()
            st.caption("`compare the treated samples treated_1 and treated_2 to the control samples ctrl_1 and ctrl_2`")
            st.caption("`compare treated_1 and treated_2 to ctrl_1 and ctrl_2 from DF1 at transcript level`")
            st.caption("`/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2`")
            st.caption("**Memory**")
            if st.button("/memories", key="help_prompt_memories", width="stretch"):
                st.session_state["_help_prompt"] = "/memories"
                st.rerun()
            st.caption("`/remember <text>`  ·  `/remember-global <text>`")
            st.caption("`/remember-df DF5 as myDF`  ·  `/forget #<id>`")
            st.caption("`/pin #<id>`  ·  `/unpin #<id>`  ·  `/restore #<id>`")
            st.caption("`/annotate <sample> k=v`  ·  `/search-memories <query>`")
            st.caption("`/upgrade-to-global #<id>`  ·  `/make-global #<id>`")
            st.caption("**Shortcuts**")
            if st.button("try again", key="help_prompt_retry", width="stretch"):
                st.session_state["_help_prompt"] = "try again"
                st.rerun()

        logout_button(api_url)

        st.divider()
        with st.expander("✨ New Project", expanded=False):
            _default_slug = f"project-{datetime.datetime.now().strftime('%Y-%m-%d')}"
            _new_name = st.text_input(
                "Project name",
                value=_default_slug,
                key="_new_project_name_input",
                max_chars=40,
                help="Lowercase letters, numbers, hyphens. Will be auto-slugified.",
            )
            if st.button("Create", key="_create_project_btn", width="stretch"):
                _slug = slugify_project_name(_new_name or _default_slug)
                st.session_state["_create_new_project"] = _slug
                st.rerun()

        if "_project_id_input" not in st.session_state:
            st.session_state["_project_id_input"] = st.session_state.active_project_id

        def _on_project_id_change():
            new_val = st.session_state.get("_project_id_input", "")
            if new_val and new_val != st.session_state.get("active_project_id"):
                st.session_state["_project_switch_loading_for"] = new_val
                st.session_state.active_project_id = new_val
                st.session_state.blocks = []
                st.session_state._last_rendered_project = new_val
                st.session_state.pop("_welcome_sent_for", None)

        st.text_input("Project ID", key="_project_id_input", on_change=_on_project_id_change)

        st.divider()

        col_clear, col_refresh = st.columns(2)
        with col_clear:
            if st.button("🗑️ Clear Chat", width="stretch"):
                try:
                    resp = request_fn(
                        "DELETE",
                        f"{api_url}/projects/{st.session_state.active_project_id}/blocks",
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        st.session_state.blocks = []
                        st.session_state.pop("_welcome_sent_for", None)
                        st.toast(f"Chat cleared ({resp.json().get('deleted', 0)} messages removed)")
                        st.rerun()
                    else:
                        st.error(f"Failed to clear: {resp.status_code}")
                except Exception as e:
                    st.error(f"Error: {e}")
        with col_refresh:
            if st.button("🔄 Refresh", width="stretch"):
                st.rerun()

        st.divider()

        with st.container(key=f"sidebar_project_scope_{st.session_state.active_project_id}"):
            st.subheader("📁 Projects")
            try:
                proj_resp = request_fn("GET", f"{api_url}/projects", timeout=3)
                if proj_resp.status_code == 200:
                    user_projects = proj_resp.json().get("projects", [])
                    st.session_state["_cached_projects"] = user_projects
                    if user_projects:
                        st.caption(f"{len(user_projects)} project(s)")

                        if len(user_projects) > 4:
                            _proj_filter = st.text_input(
                                "🔍 Filter",
                                key="_proj_filter",
                                placeholder="Search projects…",
                                label_visibility="collapsed",
                            )
                        else:
                            _proj_filter = ""

                        filtered = user_projects
                        if _proj_filter:
                            _pf = _proj_filter.lower()
                            filtered = [p for p in user_projects if _pf in p.get("name", "").lower()]

                        for proj in filtered:
                            proj_id = proj.get("id", "")
                            proj_name = proj.get("name", proj_id)[:30]
                            proj_slug = proj.get("slug", "")
                            is_current = proj_id == st.session_state.active_project_id
                            archive_confirm_id = st.session_state.get("_confirm_archive_project_id")

                            job_count = proj.get("job_count")
                            label_extra = f" · {job_count} job{'s' if job_count != 1 else ''}" if job_count else ""

                            # Show slug suffix when disk folder differs from display name
                            slug_hint = ""
                            if proj_slug and proj_slug != slugify_project_name(proj_name):
                                slug_hint = f" `({proj_slug})`"

                            if is_current:
                                st.info(f"📌 **{proj_name}**{slug_hint}{label_extra}")
                            else:
                                col_name, col_archive = st.columns([5, 1])
                                with col_name:
                                    if archive_confirm_id == proj_id:
                                        st.warning(f"Archive {proj_name}?")
                                    elif st.button(f"📂 {proj_name}{slug_hint}{label_extra}", key=f"proj_{proj_id}", width="stretch"):
                                        st.session_state["_project_switch_loading_for"] = proj_id
                                        st.session_state.active_project_id = proj_id
                                        st.session_state.blocks = []
                                        st.session_state._last_rendered_project = proj_id
                                        st.session_state["_project_id_input"] = proj_id
                                        st.session_state.pop("_welcome_sent_for", None)
                                        try:
                                            request_fn(
                                                "PUT",
                                                f"{api_url}/user/last-project",
                                                json={"project_id": proj_id},
                                                timeout=3,
                                            )
                                        except Exception:
                                            pass
                                        st.rerun()
                                with col_archive:
                                    if archive_confirm_id == proj_id:
                                        if st.button("✅", key=f"arch_yes_{proj_id}", help=f"Confirm archive '{proj_name}'"):
                                            pause_auto_refresh(4)
                                            try:
                                                del_resp = request_fn("DELETE", f"{api_url}/projects/{proj_id}", timeout=5)
                                                if del_resp.status_code == 200:
                                                    st.session_state.pop("_confirm_archive_project_id", None)
                                                    st.toast(f"Archived: {proj_name}")
                                                    st.rerun()
                                                else:
                                                    st.error(f"Archive failed: {del_resp.status_code}")
                                            except Exception as e:
                                                st.error(f"Error: {e}")
                                        if st.button("✖", key=f"arch_no_{proj_id}", help="Cancel archive"):
                                            st.session_state.pop("_confirm_archive_project_id", None)
                                            st.rerun()
                                    elif st.button("🗑", key=f"arch_{proj_id}", help=f"Archive '{proj_name}'"):
                                        st.session_state["_confirm_archive_project_id"] = proj_id
                                        pause_auto_refresh(4)
                                        st.rerun()
            except Exception:
                pass

            st.divider()
            st.page_link("pages/tasks.py", label="🗂️ Task Center")
            st.caption("Running, recovered, and follow-up work across projects")

            st.divider()

            st.subheader("💬 History")
            try:
                conv_resp = request_fn(
                    "GET",
                    f"{api_url}/projects/{st.session_state.active_project_id}/conversations",
                    timeout=3,
                )
                if conv_resp.status_code == 200:
                    conversations = conv_resp.json().get("conversations", [])
                    if conversations:
                        st.caption(f"{len(conversations)} conversation(s)")
                        for conv in conversations[:5]:
                            conv_title = conv.get("title", "Untitled")[:30]
                            if st.button(f"📝 {conv_title}...", key=f"conv_{conv['id']}", width="stretch"):
                                msg_resp = request_fn(
                                    "GET",
                                    f"{api_url}/conversations/{conv['id']}/messages",
                                    timeout=3,
                                )
                                if msg_resp.status_code == 200:
                                    st.session_state.loaded_conversation = msg_resp.json()
                                    st.rerun()
                    else:
                        st.caption("No history yet")

                job_resp = request_fn(
                    "GET",
                    f"{api_url}/projects/{st.session_state.active_project_id}/jobs",
                    timeout=3,
                )
                if job_resp.status_code == 200:
                    jobs = job_resp.json().get("jobs", [])
                    if jobs:
                        st.caption(f"📊 {len(jobs)} job(s)")
                        for job in jobs[:3]:
                            status_emoji = "✅" if job.get("status") == "COMPLETED" else "⏳"
                            job_name = job.get("sample_name", "Unknown")[:20]
                            if st.button(f"{status_emoji} {job_name}", key=f"job_{job['id']}", width="stretch"):
                                st.session_state.selected_job = job
                                st.rerun()
                    else:
                        st.caption("No jobs yet")
            except Exception:
                pass

            st.divider()

        _tok_data = None
        try:
            _tok_resp = request_fn("GET", f"{api_url}/user/token-usage", timeout=5)
            if _tok_resp.status_code == 200:
                _tok_data = _tok_resp.json()
        except Exception:
            pass

        # ── Memory sidebar widget ──────────────────────────────────
        _mem_data = None
        try:
            _mem_resp = request_fn(
                "GET", f"{api_url}/memories",
                params={
                    "project_id": st.session_state.get("active_project_id", ""),
                    "include_global": True,
                    "pinned_only": False,
                    "limit": 10,
                },
                timeout=5,
            )
            if _mem_resp and _mem_resp.status_code == 200:
                _mem_data = _mem_resp.json()
        except Exception:
            pass

            _mem_list = (_mem_data or {}).get("memories", [])
            _mem_count = (_mem_data or {}).get("total", 0)
            _mem_label = f"🧠 Memories ({_mem_count})" if _mem_count else "🧠 Memories"

            with st.expander(_mem_label, expanded=False):
                if _mem_list:
                    for _m in _mem_list[:5]:
                        _pin = "⭐ " if _m.get("is_pinned") else ""
                        _scope = "🌐" if _m.get("project_id") is None else ""
                        st.caption(f"{_pin}{_scope}{_m.get('content', '')[:60]}")
                    if _mem_count > 5:
                        st.caption(f"*... and {_mem_count - 5} more*")
                    st.page_link("pages/memories.py", label="View all memories →")
                else:
                    st.caption("No memories yet.")
                    st.caption("Use `/remember <text>` in chat.")

        _lt = (_tok_data or {}).get("lifetime", {})
        _tok_total = _lt.get("total_tokens", 0)
        _tok_limit = (_tok_data or {}).get("token_limit")
        if _tok_limit:
            _pct = min(100, round(_tok_total / _tok_limit * 100))
            _expander_label = f"🪙 {_tok_total:,} / {_tok_limit:,} tokens ({_pct}%)"
        else:
            _expander_label = f"🪙 Tokens: {_tok_total:,}" if _tok_total else "🪙 Token Usage"

        with st.expander(_expander_label, expanded=False):
            if _tok_data and _tok_total:
                if _tok_limit:
                    _pct_val = min(100, round(_tok_total / _tok_limit * 100))
                    st.progress(_pct_val / 100, text=f"{_tok_total:,} / {_tok_limit:,} ({_pct_val}%)")
                    if _pct_val >= 90:
                        st.warning("⚠️ Approaching token limit — contact an admin.")
                else:
                    tcol1, tcol2 = st.columns(2)
                    tcol1.metric("Total", f"{_tok_total:,}")
                    tcol2.metric("Completion", f"{_lt.get('completion_tokens', 0):,}")
                _daily = _tok_data.get("daily", [])
                if len(_daily) > 1:
                    import pandas as _pd

                    _df_tok = _pd.DataFrame(_daily)
                    _df_tok["date"] = _pd.to_datetime(_df_tok["date"])
                    _df_tok = _df_tok.set_index("date")
                    st.line_chart(_df_tok["total_tokens"], width="stretch")
                _since = _tok_data.get("tracking_since")
                if _since:
                    st.caption(f"Tracking since {_since[:10]}")
            else:
                st.caption("No token data yet — starts with next LLM call.")

        st.divider()

        model_choice = st.selectbox("Brain Model", ["default", "fast", "smart"], index=0)
        auto_refresh = st.toggle("Live Stream", value=False, disabled=True)
        poll_seconds = st.slider("Poll interval (sec)", 1, 5, 2, disabled=True)
        st.caption("Live auto-refresh is temporarily disabled on project pages to avoid a Streamlit thread leak. Use the page-level refresh button while jobs are running.")
        debug_mode = st.toggle("🐛 Debug", value=False)
        st.session_state["_debug_mode"] = debug_mode

        st.caption(f"**System ID:** `{st.session_state.active_project_id}`")

    return model_choice, auto_refresh, poll_seconds, debug_mode
