import time
import threading
import uuid
import requests
import datetime
import os
from datetime import timedelta
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from auth import require_auth, logout_button, make_authenticated_request, get_session_cookie

# --- CONFIG ---
# Use environment variable or default to localhost
API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="AGOUTIC v3.0", layout="wide")

# --- AUTHENTICATION ---
# Require authentication before showing any UI
user = require_auth(API_URL)

# --- 1. STATE MANAGEMENT ---
def _create_project_server_side(name: str = None) -> str:
    """Create a project via POST /projects and return the server-generated UUID."""
    project_name = name or f"Project {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    try:
        resp = make_authenticated_request(
            "POST",
            f"{API_URL}/projects",
            json={"name": project_name},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["id"]
    except Exception:
        pass
    # Fallback: if server is unreachable, generate a local UUID.
    # The /chat endpoint will auto-register it on first message.
    import uuid as _uuid
    return str(_uuid.uuid4())

# Check if we're creating a new project (flag set by New Project button)
if st.session_state.get("_create_new_project", False):
    # Create project via server-side endpoint (server generates UUID)
    new_id = _create_project_server_side()
    st.session_state.active_project_id = new_id
    st.session_state.blocks = []
    # Clear project-related data
    for key in ['loaded_conversation', 'selected_job', 'chat_history', 
                'skill_content', 'selected_skill', 'job_status', 'messages',
                '_max_visible_blocks', '_welcome_sent_for']:
        if key in st.session_state:
            del st.session_state[key]
    # Clear any widget keys left over from old block rendering
    # (form keys, checkbox keys, rejection state, etc.)
    stale_prefixes = ('params_form_', 'logs_', 'rejecting_', 'rejection_reason_',
                      'submit_reject_', 'cancel_reject_')
    for key in list(st.session_state.keys()):
        if any(key.startswith(p) for p in stale_prefixes):
            del st.session_state[key]
    # Reset the project ID text input widget so it doesn't hold the old value
    st.session_state["_project_id_input"] = new_id
    # Guard: prevent text_input comparison from reverting the ID on this rerun.
    # Use a counter (not a bool) so the guard survives multiple rerun cycles
    # while Streamlit's text_input widget syncs to the new value.
    st.session_state["_switch_grace_reruns"] = 3
    # Clear the flag
    del st.session_state["_create_new_project"]

# Initialize with user's last project or create new one
if "active_project_id" not in st.session_state:
    # Try to get user's last project
    try:
        resp = make_authenticated_request("GET", f"{API_URL}/user/last-project", timeout=3)
        if resp.status_code == 200:
            last_project = resp.json().get("last_project_id")
            if last_project:
                st.session_state.active_project_id = last_project
            else:
                # No previous project — create one via server
                st.session_state.active_project_id = _create_project_server_side()
        else:
            st.session_state.active_project_id = _create_project_server_side()
    except:
        st.session_state.active_project_id = _create_project_server_side()
    
# Initialize other state variables
if "blocks" not in st.session_state:
    st.session_state.blocks = []

# Detect project switch: clear stale blocks immediately so they never render
if st.session_state.get("_last_rendered_project") != st.session_state.active_project_id:
    st.session_state.blocks = []
    st.session_state._last_rendered_project = st.session_state.active_project_id
    st.session_state.pop("_welcome_sent_for", None)
    # Suppress auto-refresh for a few cycles after switching to avoid
    # Streamlit DOM-reuse artefacts (old messages blinking).
    st.session_state["_suppress_auto_refresh"] = 3

# --- 2. SIDEBAR ---
with st.sidebar:
    st.title("🧬 AGOUTIC")
    
    # User info
    st.caption(f"👤 {user.get('display_name', user.get('email'))}")
    if user.get('role') == 'admin':
        st.caption("🔑 Admin")
    
    logout_button(API_URL)
    
    st.divider()
    # [A] NEW PROJECT (Generates Random ID)
    if st.button("✨ New Project", use_container_width=True):
        # Set flag to create new project on next rerun
        st.session_state["_create_new_project"] = True
        st.rerun()

    # [B] PROJECT ID INPUT
    # Initialize the widget key if not set
    if "_project_id_input" not in st.session_state:
        st.session_state["_project_id_input"] = st.session_state.active_project_id
    
    user_input = st.text_input(
        "Project ID", 
        key="_project_id_input",
    )
    
    # Sync: if user manually edited the field, update active project.
    # Skip while the grace counter is active (prevents the text_input's stale
    # old value from silently reverting active_project_id back).
    grace = st.session_state.get("_switch_grace_reruns", 0)
    if grace > 0:
        st.session_state["_switch_grace_reruns"] = grace - 1
    elif user_input and user_input != st.session_state.active_project_id:
        st.session_state.active_project_id = user_input
        st.session_state.blocks = []
        st.session_state._last_rendered_project = user_input
        st.session_state.pop("_welcome_sent_for", None)
        st.rerun()

    st.divider()
    
    # [C] CHAT CONTROLS
    col_clear, col_refresh = st.columns(2)
    with col_clear:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            try:
                resp = make_authenticated_request(
                    "DELETE",
                    f"{API_URL}/projects/{st.session_state.active_project_id}/blocks",
                    timeout=5,
                )
                if resp.status_code == 200:
                    st.session_state.blocks = []
                    # Reset welcome flag so agent re-introduces itself
                    st.session_state.pop("_welcome_sent_for", None)
                    st.toast(f"Chat cleared ({resp.json().get('deleted', 0)} messages removed)")
                    st.rerun()
                else:
                    st.error(f"Failed to clear: {resp.status_code}")
            except Exception as e:
                st.error(f"Error: {e}")
    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    st.divider()
    
    # [D] PROJECT SWITCHER
    st.subheader("📁 Projects")
    try:
        # Get user's projects (server-side CRUD endpoint)
        proj_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/projects",
            timeout=3
        )
        if proj_resp.status_code == 200:
            user_projects = proj_resp.json().get("projects", [])
            # Cache for title display
            st.session_state["_cached_projects"] = user_projects
            if user_projects:
                st.caption(f"{len(user_projects)} project(s)")

                # Search/filter when many projects
                if len(user_projects) > 4:
                    _proj_filter = st.text_input(
                        "🔍 Filter", key="_proj_filter", placeholder="Search projects…",
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
                    is_current = proj_id == st.session_state.active_project_id

                    # Build label with job count if available
                    job_count = proj.get("job_count")
                    label_extra = f" · {job_count} job{'s' if job_count != 1 else ''}" if job_count else ""

                    if is_current:
                        st.info(f"📌 **{proj_name}**{label_extra}")
                    else:
                        col_name, col_archive = st.columns([5, 1])
                        with col_name:
                            if st.button(f"📂 {proj_name}{label_extra}", key=f"proj_{proj_id}", use_container_width=True):
                                # Switch to this project
                                st.session_state.active_project_id = proj_id
                                st.session_state.blocks = []
                                st.session_state._last_rendered_project = proj_id
                                st.session_state["_project_id_input"] = proj_id
                                st.session_state["_switch_grace_reruns"] = 3
                                st.session_state.pop("_welcome_sent_for", None)
                                st.rerun()
                        with col_archive:
                            if st.button("🗑", key=f"arch_{proj_id}", help=f"Archive '{proj_name}'"):
                                try:
                                    del_resp = make_authenticated_request(
                                        "DELETE",
                                        f"{API_URL}/projects/{proj_id}",
                                        timeout=5,
                                    )
                                    if del_resp.status_code == 200:
                                        st.toast(f"Archived: {proj_name}")
                                        st.rerun()
                                    else:
                                        st.error(f"Archive failed: {del_resp.status_code}")
                                except Exception as e:
                                    st.error(f"Error: {e}")
    except Exception:
        pass  # Silently fail if projects not available
    
    st.divider()
    
    # [E] CONVERSATION HISTORY
    st.subheader("💬 History")
    try:
        # Get conversations for this project
        conv_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/projects/{st.session_state.active_project_id}/conversations",
            timeout=3
        )
        if conv_resp.status_code == 200:
            conversations = conv_resp.json().get("conversations", [])
            if conversations:
                st.caption(f"{len(conversations)} conversation(s)")
                for conv in conversations[:5]:  # Show last 5
                    conv_title = conv.get("title", "Untitled")[:30]
                    if st.button(f"📝 {conv_title}...", key=f"conv_{conv['id']}", use_container_width=True):
                        # Load this conversation
                        msg_resp = make_authenticated_request(
                            "GET",
                            f"{API_URL}/conversations/{conv['id']}/messages",
                            timeout=3
                        )
                        if msg_resp.status_code == 200:
                            st.session_state.loaded_conversation = msg_resp.json()
                            st.rerun()
            else:
                st.caption("No history yet")
        
        # Get previous jobs
        job_resp = make_authenticated_request(
            "GET",
            f"{API_URL}/projects/{st.session_state.active_project_id}/jobs",
            timeout=3
        )
        if job_resp.status_code == 200:
            jobs = job_resp.json().get("jobs", [])
            if jobs:
                st.caption(f"📊 {len(jobs)} job(s)")
                for job in jobs[:3]:  # Show last 3
                    status_emoji = "✅" if job.get("status") == "COMPLETED" else "⏳"
                    job_name = job.get("sample_name", "Unknown")[:20]
                    if st.button(f"{status_emoji} {job_name}", key=f"job_{job['id']}", use_container_width=True):
                        st.session_state.selected_job = job
                        st.rerun()
            else:
                st.caption("No jobs yet")
    except Exception:
        pass  # Silently fail if history not available
    
    st.divider()

    # [E] TOKEN USAGE SUMMARY FOR THIS USER
    # Fetch OUTSIDE the expander so the data refreshes on every rerun,
    # regardless of whether the expander is open or closed.
    _tok_data = None
    try:
        _tok_resp = make_authenticated_request("GET", f"{API_URL}/user/token-usage", timeout=5)
        if _tok_resp.status_code == 200:
            _tok_data = _tok_resp.json()
    except Exception:
        pass

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
                st.line_chart(_df_tok["total_tokens"], use_container_width=True)
            _since = _tok_data.get("tracking_since")
            if _since:
                st.caption(f"Tracking since {_since[:10]}")
        else:
            st.caption("No token data yet — starts with next LLM call.")

    st.divider()
    
    model_choice = st.selectbox("Brain Model", ["default", "fast", "smart"], index=0)
    auto_refresh = st.toggle("Live Stream", value=True)
    poll_seconds = st.slider("Poll interval (sec)", 1, 5, 2)
    debug_mode = st.toggle("🐛 Debug", value=False)
    st.session_state["_debug_mode"] = debug_mode
    
    # DEBUG DISPLAY: Show exactly what ID the UI thinks it is using
    st.caption(f"**System ID:** `{st.session_state.active_project_id}`")


# --- 3. LOGIC ---

def get_sanitized_blocks(target_project_id):
    """
    Fetch blocks from server, but SCRUB any block that doesn't match the target ID.
    This guarantees no 'ghost' messages from other projects can appear.
    """
    try:
        # 1. Ask Server with authentication
        resp = make_authenticated_request(
            "GET",
            f"{API_URL}/blocks",
            params={"project_id": target_project_id, "since_seq": 0, "limit": 100},
            timeout=5,
        )
        if resp.status_code == 200:
            raw_blocks = resp.json().get("blocks", [])
            
            # 2. CLIENT-SIDE SANITATION (The Fix)
            # We explicitly filter the list. If the block's 'project_id' is not exactly
            # equal to our target, we throw it away.
            clean_blocks = [
                b for b in raw_blocks 
                if b.get("project_id") == target_project_id
            ]
            return clean_blocks
    except Exception:
        pass
    return []

def get_job_debug_info(run_uuid):
    """Fetch detailed debug information for a failed job via Server 1 proxy."""
    try:
        # Use Server 1 proxy instead of calling Server 3 directly
        resp = make_authenticated_request(
            "GET",
            f"{API_URL}/jobs/{run_uuid}/debug",
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        st.error(f"Failed to fetch debug info: {e}")
    return None


def _render_md_with_dataframes(md: str, block_id: str, section: str):
    """Render a markdown string, replacing any pipe tables with st.dataframe.

    The function splits the markdown on table blocks (contiguous lines that
    start with '|') and renders non-table text via st.markdown while each
    table block becomes an interactive dataframe.
    """
    import re as _re

    lines = md.splitlines(keepends=True)
    buf_text: list[str] = []
    buf_table: list[str] = []
    table_index = [0]

    def flush_text():
        chunk = "".join(buf_text).strip()
        if chunk:
            st.markdown(chunk)
        buf_text.clear()

    def flush_table():
        raw = "".join(buf_table)
        rows = [l for l in buf_table if l.strip() and not _re.match(r"^\s*\|[-| :]+\|\s*$", l)]
        if not rows:
            buf_table.clear()
            return
        try:
            import io
            import pandas as _pd
            # Parse header and data rows
            all_rows = [l.strip() for l in buf_table if l.strip()]
            # Header is first row, separator is second, rest are data
            header_row = all_rows[0]
            data_rows = [r for r in all_rows[2:] if r.startswith("|")]
            parse_row = lambda r: [c.strip() for c in r.strip("|").split("|")]
            headers = parse_row(header_row)
            records = [parse_row(r) for r in data_rows]
            # Pad short rows
            records = [r + [""] * (len(headers) - len(r)) for r in records]
            df = _pd.DataFrame(records, columns=headers)
            idx = table_index[0]
            table_index[0] += 1
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                height=min(400, 35 * len(df) + 38),
                key=f"_mdtbl_{block_id}_{section}_{idx}",
            )
        except Exception:
            # Fallback to plain markdown if parsing fails
            st.markdown(raw)
        buf_table.clear()

    in_code_block = False
    for line in lines:
        stripped = line.strip()
        # Track fenced code blocks — don't parse tables inside them
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            if buf_table:
                flush_table()
            buf_text.append(line)
            continue

        if in_code_block:
            buf_text.append(line)
            continue

        is_table_line = stripped.startswith("|")
        if is_table_line:
            if buf_text:
                flush_text()
            buf_table.append(line)
        else:
            if buf_table:
                flush_table()
            buf_text.append(line)

    if buf_table:
        flush_table()
    if buf_text:
        flush_text()


def _render_embedded_dataframes(dfs: dict, block_id: str, *, only_visible: bool = True):
    """Render embedded dataframes (search results, CSV/BED files) from a block's _dataframes dict.

    If ``only_visible=True`` (default), only renders dataframes that are
    visible (have a ``df_id``).  Call with ``only_visible=False`` to render
    the non-visible (supplementary) dataframes inside the details section.
    """
    import re as _re
    for _df_idx, (_fname, _fdata) in enumerate(dfs.items()):
        _meta = _fdata.get("metadata", {})
        _df_id = _meta.get("df_id")
        _has_id = _df_id is not None
        # Filter: visible mode shows only DFs with IDs;
        # non-visible mode shows only DFs without IDs.
        if only_visible and not _has_id:
            continue
        if not only_visible and _has_id:
            continue
        _is_visible = _has_id
        _rows = _fdata.get("data", [])
        _cols = _fdata.get("columns", [])
        _total = _fdata.get("row_count", len(_rows))
        _meta = _fdata.get("metadata", {})
        _df = pd.DataFrame(_rows)
        if _df.empty:
            st.info(f"📊 **{_fname}** — empty table")
            continue
        # Sanitize key: strip non-alphanumeric chars to avoid Streamlit widget key issues
        _safe_key = _re.sub(r"[^a-zA-Z0-9_]", "_", f"{block_id}_{_df_idx}")
        _df_id = _meta.get("df_id")
        _df_prefix = f"DF{_df_id}: " if _df_id else ""
        with st.expander(f"📊 {_df_prefix}**{_fname}** — {_total:,} rows × {len(_cols)} columns", expanded=_is_visible):
            # Column statistics popover
            _col_stats = _meta.get("column_stats", {})
            if _col_stats:
                with st.popover("📈 Column statistics"):
                    for _cn, _ci in _col_stats.items():
                        _dt = _ci.get("dtype", "")
                        _nl = _ci.get("nulls", 0)
                        _ln = f"**{_cn}** ({_dt})"
                        if _nl:
                            _ln += f" — {_nl} nulls"
                        if "mean" in _ci and _ci["mean"] is not None:
                            _ln += (
                                f"  \nmin={_ci.get('min')}  max={_ci.get('max')}  "
                                f"mean={_ci.get('mean'):.4g}  median={_ci.get('median'):.4g}"
                            )
                        elif "unique" in _ci:
                            _ln += f"  \n{_ci['unique']} unique values"
                        st.markdown(_ln)
            # Column filter
            if len(_cols) > 4:
                _sel_cols = st.multiselect(
                    "Columns to display",
                    _cols,
                    default=_cols,
                    key=f"dfchat_{_safe_key}",
                )
            else:
                _sel_cols = _cols
            _disp = _df[_sel_cols] if _sel_cols else _df
            st.dataframe(
                _disp,
                use_container_width=True,
                hide_index=True,
                height=min(400, 35 * len(_disp) + 38),
            )
            if _meta.get("is_truncated"):
                st.caption(f"Showing {len(_rows):,} of {_total:,} rows")
            _csv = _disp.to_csv(index=False).encode("utf-8")
            st.download_button(
                f"⬇️ Download {_fname}",
                data=_csv,
                file_name=_fname,
                mime="text/csv",
                key=f"dldfc_{_safe_key}",
            )


def _resolve_df_by_id(df_id: int, all_blocks: list):
    """Look up an embedded dataframe by its DF ID across all blocks.

    Scans all AGENT_PLAN blocks in ``all_blocks`` for a dataframe whose
    ``metadata.df_id`` matches *df_id*.

    Returns a tuple ``(pd.DataFrame, label_str)`` or ``(None, None)`` if
    not found.
    """
    for blk in reversed(all_blocks):
        if blk.get("type") != "AGENT_PLAN":
            continue
        dfs = blk.get("payload", {}).get("_dataframes", {})
        for fname, fdata in dfs.items():
            meta = fdata.get("metadata", {})
            if meta.get("df_id") == df_id:
                rows = fdata.get("data", [])
                cols = fdata.get("columns") or None
                df = pd.DataFrame(rows, columns=cols)
                return df, fname
    return None, None


def _build_plotly_figure(chart_spec: dict, df: pd.DataFrame, df_label: str):
    """Build a single Plotly figure from a chart spec and dataframe.

    Returns a plotly Figure or None on error.
    """
    chart_type = chart_spec.get("type", "histogram")
    x_col = chart_spec.get("x")
    y_col = chart_spec.get("y")
    color_col = chart_spec.get("color")
    title = chart_spec.get("title", "")
    agg = chart_spec.get("agg")

    # Validate columns exist
    available = list(df.columns)
    if x_col and x_col not in available:
        # Try case-insensitive match
        match = [c for c in available if c.lower() == x_col.lower()]
        x_col = match[0] if match else None
    if y_col and y_col not in available:
        match = [c for c in available if c.lower() == y_col.lower()]
        y_col = match[0] if match else None
    if color_col and color_col not in available:
        match = [c for c in available if c.lower() == color_col.lower()]
        color_col = match[0] if match else None

    try:
        # Convert numeric-looking columns to numeric for plotting
        df_plot = df.copy()
        for col in [x_col, y_col]:
            if col and col in df_plot.columns:
                df_plot[col] = pd.to_numeric(df_plot[col], errors="ignore")

        if chart_type == "histogram":
            if not x_col:
                # Auto-pick first numeric column
                num_cols = df_plot.select_dtypes(include="number").columns
                x_col = num_cols[0] if len(num_cols) > 0 else available[0]
            # If x_col is categorical (non-numeric), px.histogram would count
            # occurrences per category (each row = 1), which is wrong for
            # pre-aggregated tables like Category/Count.
            # Detect: x_col is non-numeric AND there is exactly one numeric
            # column → treat as a bar chart using that numeric column as y.
            x_is_numeric = pd.api.types.is_numeric_dtype(df_plot[x_col])
            if not x_is_numeric:
                num_cols = df_plot.select_dtypes(include="number").columns.tolist()
                if num_cols:
                    # Prefer a column named "count", "value", "n", "total", etc.
                    _count_names = {"count", "counts", "value", "values",
                                    "n", "total", "freq", "frequency", "amount"}
                    _y_col = next(
                        (c for c in num_cols if c.lower() in _count_names),
                        num_cols[0]
                    )
                    fig = px.bar(df_plot, x=x_col, y=_y_col, color=color_col,
                                 title=title or f"{_y_col} by {x_col}")
                else:
                    # Truly categorical with no numeric column — count rows
                    counts = df_plot[x_col].value_counts().reset_index()
                    counts.columns = [x_col, "Count"]
                    fig = px.bar(counts, x=x_col, y="Count", color=color_col,
                                 title=title or f"Count by {x_col}")
            else:
                fig = px.histogram(df_plot, x=x_col, color=color_col,
                                   title=title or f"Distribution of {x_col}")

        elif chart_type == "scatter":
            if not x_col or not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                if len(num_cols) >= 2:
                    x_col = x_col or num_cols[0]
                    y_col = y_col or num_cols[1]
                else:
                    return None
            fig = px.scatter(df_plot, x=x_col, y=y_col, color=color_col,
                             title=title or f"{x_col} vs {y_col}")

        elif chart_type == "bar":
            if not x_col:
                # Auto-pick first non-numeric column
                cat_cols = df_plot.select_dtypes(exclude="number").columns
                x_col = cat_cols[0] if len(cat_cols) > 0 else available[0]
            # If y_col is absent and x is categorical, prefer a numeric companion
            # column (pre-aggregated table) over counting rows (which gives 1 each).
            x_is_cat = not pd.api.types.is_numeric_dtype(df_plot[x_col])
            if not y_col and x_is_cat and agg != "count":
                num_cols = df_plot.select_dtypes(include="number").columns.tolist()
                if num_cols:
                    _count_names = {"count", "counts", "value", "values",
                                    "n", "total", "freq", "frequency", "amount"}
                    y_col = next(
                        (c for c in num_cols if c.lower() in _count_names),
                        num_cols[0]
                    )
            if agg == "count" or not y_col:
                # Count occurrences of each x value
                counts = df_plot[x_col].value_counts().reset_index()
                counts.columns = [x_col, "Count"]
                fig = px.bar(counts, x=x_col, y="Count", color=color_col,
                             title=title or f"Count by {x_col}")
            else:
                if agg == "mean":
                    agg_df = df_plot.groupby(x_col, as_index=False)[y_col].mean()
                elif agg == "sum":
                    agg_df = df_plot.groupby(x_col, as_index=False)[y_col].sum()
                else:
                    agg_df = df_plot
                fig = px.bar(agg_df, x=x_col, y=y_col, color=color_col,
                             title=title or f"{y_col} by {x_col}")

        elif chart_type == "box":
            if not y_col:
                num_cols = df_plot.select_dtypes(include="number").columns
                y_col = num_cols[0] if len(num_cols) > 0 else None
            if not y_col:
                return None
            fig = px.box(df_plot, x=x_col, y=y_col, color=color_col,
                         title=title or f"Distribution of {y_col}" + (f" by {x_col}" if x_col else ""))

        elif chart_type == "heatmap":
            num_df = df_plot.select_dtypes(include="number")
            if num_df.shape[1] < 2:
                return None
            corr = num_df.corr()
            fig = px.imshow(corr, text_auto=".2f",
                            title=title or "Correlation Matrix",
                            color_continuous_scale="RdBu_r",
                            zmin=-1, zmax=1)

        elif chart_type == "pie":
            if not x_col:
                cat_cols = df_plot.select_dtypes(exclude="number").columns
                x_col = cat_cols[0] if len(cat_cols) > 0 else available[0]
            if y_col:
                fig = px.pie(df_plot, names=x_col, values=y_col,
                             title=title or f"{x_col} Proportions")
            else:
                counts = df_plot[x_col].value_counts().reset_index()
                counts.columns = [x_col, "Count"]
                fig = px.pie(counts, names=x_col, values="Count",
                             title=title or f"{x_col} Distribution")

        else:
            return None

        fig.update_layout(template="plotly_white")
        return fig

    except Exception:
        return None


def _render_plot_block(payload: dict, all_blocks: list, block_id: str):
    """Render an AGENT_PLOT block's charts using Plotly.

    ``payload["charts"]`` is a list of chart specs each with:
      type, df_id, x, y, color, title, agg

    DataFrames are resolved from prior AGENT_PLAN blocks via ``_resolve_df_by_id``.
    """
    import re as _re
    charts = payload.get("charts", [])
    if not charts:
        st.info("No chart specifications found in this plot block.")
        return

    # Check if multiple charts target the same df and type (multi-trace overlay)
    # Group by (df_id, type) for overlay rendering
    from collections import defaultdict
    groups = defaultdict(list)
    for chart in charts:
        key = (chart.get("df_id"), chart.get("type"))
        groups[key].append(chart)

    chart_idx = 0
    for (df_id, chart_type), chart_group in groups.items():
        if df_id is None:
            st.warning("Chart missing DataFrame reference (df=DFN).")
            continue

        df, df_label = _resolve_df_by_id(df_id, all_blocks)
        if df is None or df.empty:
            st.warning(f"DataFrame DF{df_id} not found in conversation history.")
            continue

        if len(chart_group) == 1:
            # Single chart
            fig = _build_plotly_figure(chart_group[0], df, df_label)
            if fig:
                _safe_key = _re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                st.plotly_chart(fig, use_container_width=True, key=_safe_key)
            else:
                st.warning(f"Could not render {chart_type} chart for DF{df_id}. "
                           "Check that the specified columns exist.")
        else:
            # Multi-trace overlay: build a combined figure
            combined = go.Figure()
            title_parts = []
            for spec in chart_group:
                fig = _build_plotly_figure(spec, df, df_label)
                if fig:
                    for trace in fig.data:
                        combined.add_trace(trace)
                    if spec.get("title"):
                        title_parts.append(spec["title"])
            if combined.data:
                combined.update_layout(
                    template="plotly_white",
                    title=" / ".join(title_parts) if title_parts else f"DF{df_id} — {chart_type}",
                )
                _safe_key = _re.sub(r"[^a-zA-Z0-9_]", "_", f"plot_{block_id}_{chart_idx}")
                st.plotly_chart(combined, use_container_width=True, key=_safe_key)
            else:
                st.warning(f"Could not render multi-trace {chart_type} chart for DF{df_id}.")
        chart_idx += 1


def render_block(block, expected_project_id: str = ""):
    """Render a single block.

    If expected_project_id is provided, silently skip blocks that belong
    to a different project (last line of defence against ghost content).
    """
    b_project = block.get("project_id", "???")
    if expected_project_id and b_project != expected_project_id:
        return  # ghost block – do not render

    btype = block["type"]
    content = block.get("payload", {})
    status = block.get("status", "NEW")
    block_id = block["id"]
    
    # Metadata
    b_skill = content.get("skill", "N/A")
    b_model = content.get("model", "N/A")

    def show_metadata():
        st.caption(f"📌 **Proj:** `{b_project}` | 🧠 **Model:** `{b_model}` | 🛠️ **Skill:** `{b_skill}`")

    if btype == "USER_MESSAGE":
        with st.chat_message("user"):
            st.write(content.get("text", ""))

    elif btype == "AGENT_PLAN":
        with st.chat_message("assistant", avatar="🤖"):
            show_metadata()
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
        with st.chat_message("assistant", avatar="🚦"):
            # Get extracted parameters and metadata
            extracted_params = content.get("extracted_params", {})
            manual_mode = content.get("manual_mode", False)
            attempt_number = content.get("attempt_number", 1)
            rejection_history = content.get("rejection_history", [])
            
            # Title based on mode
            if manual_mode:
                st.write("### ⚠️ Manual Configuration Required")
                st.warning(f"The AI couldn't understand your requirements after 3 attempts. Please verify these parameters manually.")
            else:
                st.write(f"### ✅ Approval Required (Attempt {attempt_number}/3)")
            
            st.write(content.get("label", "Approve this plan?"))
            st.caption(f"Block ID: `{block_id}`")
            
            # Show rejection history if exists
            if rejection_history:
                with st.expander(f"📜 Rejection History ({len(rejection_history)} previous attempts)", expanded=False):
                    for i, hist in enumerate(rejection_history, 1):
                        st.text(f"Attempt {hist.get('attempt', i)}: {hist.get('reason', 'No reason')}")
                        st.caption(f"at {hist.get('timestamp', 'unknown time')}")

            if status == "APPROVED":
                st.success("✅ Approved")
                # Show what parameters were used
                if extracted_params:
                    with st.expander("📋 Parameters Used", expanded=False):
                        st.json(extracted_params)
                        
            elif status == "REJECTED":
                st.error("❌ Rejected")
                # Show rejection reason if available
                reason = content.get("rejection_reason", "No reason provided")
                st.caption(f"Reason: {reason}")
                
            else:
                # Pending approval - show editable parameter form
                if extracted_params:
                    st.write("**📋 Extracted Parameters** (edit if needed):")
                    
                    with st.form(key=f"params_form_{block_id}"):
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
                        if isinstance(current_genomes, str):
                            current_genomes = [current_genomes]
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
                        
                        submit_approve = col1.form_submit_button("✅ Approve", use_container_width=True)
                        submit_reject = col2.form_submit_button("❌ Reject", use_container_width=True)
                        
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
                            }
                            
                            # Update block with edited params and approved status
                            payload_update = dict(content)
                            payload_update["edited_params"] = edited_params
                            
                            make_authenticated_request(
                                "PATCH",
                                f"{API_URL}/block/{block_id}",
                                json={"status": "APPROVED", "payload": payload_update}
                            )
                            st.rerun()
                        
                        if submit_reject:
                            st.session_state[f"rejecting_{block_id}"] = True
                            st.rerun()
                
                # Show rejection feedback form if user clicked reject
                if st.session_state.get(f"rejecting_{block_id}", False):
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

    
    elif btype == "EXECUTION_JOB":
        # Job execution monitoring with Nextflow progress visualization
        with st.chat_message("assistant", avatar="⚙️"):
            run_uuid = content.get("run_uuid", "")
            sample_name = content.get("sample_name", "Unknown")
            mode = content.get("mode", "Unknown")
            
            st.write(f"### 🧬 Nextflow Job: {sample_name} ({mode})")
            st.caption(f"Run UUID: `{run_uuid}`")
            
            # For RUNNING jobs, live-fetch status directly from Server 1
            # (bypasses stale block payload — always fresh from Server 3)
            job_status = content.get("job_status", {})
            block_status_str = block.get("status", "")
            if run_uuid and block_status_str == "RUNNING":
                try:
                    live_resp = make_authenticated_request(
                        "GET",
                        f"{API_URL}/jobs/{run_uuid}/status",
                        timeout=5,
                    )
                    if live_resp.status_code == 200:
                        job_status = live_resp.json()
                except Exception:
                    pass  # Fall back to block payload
            
            if job_status:
                status_str = job_status.get("status", content.get("status", "UNKNOWN"))
                progress = job_status.get("progress_percent", 0)
                message = job_status.get("message", content.get("message", ""))
                tasks = job_status.get("tasks", {})
                
                # Status indicator
                if status_str == "COMPLETED":
                    st.success(f"✅ {message}")
                    
                    # Show completed tasks summary
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        total = tasks.get("total", 0)
                        completed_count = tasks.get("completed_count", 0)
                        
                        st.metric("✅ Completed Tasks", f"{completed_count}/{total}")
                        
                        # Group and display completed tasks
                        if completed:
                            with st.expander(f"📋 Completed Tasks ({len(completed)} total)", expanded=False):
                                # Group tasks by base name
                                task_groups = {}
                                for task in completed:
                                    if '(' in task and ')' in task:
                                        base_name = task[:task.rfind('(')].strip()
                                        try:
                                            instance = int(task[task.rfind('(')+1:task.rfind(')')].strip())
                                            if base_name not in task_groups:
                                                task_groups[base_name] = []
                                            task_groups[base_name].append(instance)
                                        except ValueError:
                                            task_groups[task] = None
                                    else:
                                        task_groups[task] = None
                                
                                # Display grouped tasks
                                for base_name, instances in task_groups.items():
                                    if instances is None:
                                        st.text(f"✔ {base_name}")
                                    elif len(instances) == 1:
                                        st.text(f"✔ {base_name} ({instances[0]})")
                                    else:
                                        instances_sorted = sorted(instances)
                                        st.text(f"✔ {base_name} ({len(instances)}/{max(instances_sorted)})")
                    
                elif status_str == "FAILED":
                    st.error(f"❌ {message}")
                    
                    # Fetch detailed debug information
                    if run_uuid:
                        debug_info = get_job_debug_info(run_uuid)
                        
                        if debug_info:
                            # Show .nextflow.log errors prominently
                            logs_preview = debug_info.get("logs_preview", {})
                            nextflow_log = logs_preview.get(".nextflow.log", {})
                            
                            if nextflow_log.get("errors_and_warnings"):
                                with st.expander("🔥 Nextflow Errors (from .nextflow.log)", expanded=True):
                                    error_container = st.container(height=400)
                                    with error_container:
                                        for line in nextflow_log["errors_and_warnings"]:
                                            if line.strip():
                                                if "ERROR" in line.upper() or "Exception" in line:
                                                    st.error(line[:150])
                                                elif "WARN" in line.upper():
                                                    st.warning(line[:150])
                                                else:
                                                    st.text(line[:150])
                            
                            # Show failed tasks if available
                            failed_count = tasks.get("failed_count", 0) if tasks else 0
                            if failed_count > 0:
                                st.metric("❌ Failed Tasks", failed_count)
                            
                            # Show work directory and markers
                            with st.expander("🔍 Debug Information", expanded=False):
                                st.write(f"**Work Directory:** `{debug_info.get('work_directory', 'N/A')}`")
                                
                                markers = debug_info.get("markers", {})
                                if markers:
                                    st.write("**Job Markers:**")
                                    for marker_name, marker_info in markers.items():
                                        if marker_info.get("exists"):
                                            st.success(f"✅ {marker_name}: {marker_info.get('content', '')[:60]}")
                                        else:
                                            st.info(f"⬜ {marker_name}: Not present")
                                
                                # Show other log files
                                if logs_preview:
                                    st.write("**Available Log Files:**")
                                    for log_name, log_info in logs_preview.items():
                                        if log_name != ".nextflow.log" and log_info.get("exists"):
                                            size = log_info.get("size", 0)
                                            lines = log_info.get("lines", 0)
                                            st.text(f"📄 {log_name}: {size:,} bytes, {lines} lines")
                                            
                                            # Show last few lines for stderr/stdout
                                            if log_name in [".command.err", ".command.out"]:
                                                last_lines = log_info.get("last_20_lines", [])
                                                if last_lines:
                                                    with st.expander(f"Last lines from {log_name}", expanded=False):
                                                        for line in last_lines[-10:]:
                                                            if line.strip():
                                                                st.text(line[:120])
                    
                    # Also show regular logs from job_status
                    logs = content.get("logs", [])
                    if logs:
                        with st.expander("📋 Job Logs", expanded=False):
                            log_container = st.container(height=300)
                            with log_container:
                                for log in reversed(logs[-50:]):
                                    timestamp = log.get("timestamp", "")
                                    level = log.get("level", "INFO")
                                    msg = log.get("message", "")
                                    
                                    if level == "ERROR":
                                        st.error(f"[{timestamp}] {msg}")
                                    elif level == "WARN":
                                        st.warning(f"[{timestamp}] {msg}")
                                    else:
                                        st.text(f"[{timestamp}] {msg}")
                    
                elif status_str == "RUNNING":
                    st.info(f"🔄 {message}")
                    
                    # Job timing info
                    _job_created = block.get("created_at", "")
                    _last_upd = content.get("last_updated", "")
                    _timing_parts = []
                    if _job_created:
                        try:
                            _start_dt = datetime.datetime.fromisoformat(_job_created.replace("Z", "+00:00"))
                            _elapsed = datetime.datetime.now(datetime.timezone.utc) - _start_dt
                            _mins, _secs = divmod(int(_elapsed.total_seconds()), 60)
                            _timing_parts.append(f"⏱️ Running for {_mins}m {_secs}s")
                        except (ValueError, TypeError):
                            pass
                    if _last_upd:
                        try:
                            _upd_dt = datetime.datetime.fromisoformat(_last_upd.replace("Z", "+00:00"))
                            _ago = datetime.datetime.now(datetime.timezone.utc) - _upd_dt
                            _ago_secs = int(_ago.total_seconds())
                            _timing_parts.append(f"🔄 Updated {_ago_secs}s ago")
                        except (ValueError, TypeError):
                            pass
                    if _timing_parts:
                        st.caption(" | ".join(_timing_parts))
                    
                    # Progress bar
                    st.progress(progress / 100.0, text=f"Progress: {progress}%")
                    
                    # Nextflow-style task display
                    if tasks and isinstance(tasks, dict):
                        completed = tasks.get("completed", [])
                        running = tasks.get("running", [])
                        total = tasks.get("total", 0)
                        completed_count = tasks.get("completed_count", 0)
                        failed_count = tasks.get("failed_count", 0)
                        
                        # Summary metrics
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("✅ Completed", f"{completed_count}/{total}")
                        with col2:
                            st.metric("🏃 Running", len(running))
                        with col3:
                            if failed_count > 0:
                                st.metric("❌ Failed", failed_count)
                        
                        # Show running tasks in Nextflow style
                        if running:
                            st.write("**🏃 Currently Running:**")
                            for task in running[-5:]:  # Show last 5 running tasks
                                # Format like: [7b/34a1] mainWorkflow:doradoDownloadTask (1) [100%]
                                task_hash = task.split(':')[-1][:4] if ':' in task else "????"
                                st.code(f"[..../{task_hash}] {task}", language="bash")
                        
                        # Show recently completed with grouping
                        if completed:
                            with st.expander(f"✅ Recently Completed ({len(completed)} total)", expanded=False):
                                # Group tasks by base name
                                task_groups = {}
                                for task in completed:
                                    if '(' in task and ')' in task:
                                        base_name = task[:task.rfind('(')].strip()
                                        try:
                                            instance = int(task[task.rfind('(')+1:task.rfind(')')].strip())
                                            if base_name not in task_groups:
                                                task_groups[base_name] = []
                                            task_groups[base_name].append(instance)
                                        except ValueError:
                                            task_groups[task] = None
                                    else:
                                        task_groups[task] = None
                                
                                # Display grouped tasks
                                for base_name, instances in task_groups.items():
                                    if instances is None:
                                        st.text(f"✔ {base_name}")
                                    elif len(instances) == 1:
                                        st.text(f"✔ {base_name} ({instances[0]})")
                                    else:
                                        instances_sorted = sorted(instances)
                                        st.text(f"✔ {base_name} ({len(instances)}/{max(instances_sorted)})")
                else:
                    st.warning(f"⏳ Status: {status_str}")
                    if message:
                        st.caption(message)
            else:
                # Fallback for initial state
                init_message = content.get("message", "Job submitted")
                st.info(f"⏳ {init_message}")
            
            # Show logs toggle for non-failed jobs (failed jobs show logs above)
            if status_str != "FAILED":
                logs = content.get("logs", [])
                if logs and st.checkbox("Show Logs", key=f"logs_{block_id}"):
                    st.write("**Recent Logs:**")
                    log_container = st.container(height=300)
                    with log_container:
                        for log in reversed(logs[-30:]):  # Show last 30 in reverse chronological order
                            timestamp = log.get("timestamp", "")
                            level = log.get("level", "INFO")
                            msg = log.get("message", "")
                            
                            # Color-code by level
                            if level == "ERROR":
                                st.error(f"[{timestamp}] {msg}")
                            elif level == "WARN":
                                st.warning(f"[{timestamp}] {msg}")
                            else:
                                st.text(f"[{timestamp}] {msg}")
    
    elif btype == "AGENT_PLOT":
        with st.chat_message("assistant", avatar="📊"):
            show_metadata()
            # Render interactive Plotly charts
            # Pass all currently loaded blocks so the renderer can look up DFs by ID
            all_blocks = st.session_state.get("blocks", [])
            _render_plot_block(content, all_blocks, block_id)
    
    else:
        with st.chat_message("system", avatar="⚙️"):
            st.code(f"[{btype}] {content}")


# --- 4. MAIN RENDER LOOP ---

# Capture the ID once for this entire run
active_id = st.session_state.active_project_id

# Show project name in title (fall back to truncated UUID)
_active_project_name = active_id[:12] + "…"
for _p in st.session_state.get("_cached_projects", []):
    if _p.get("id") == active_id:
        _active_project_name = _p.get("name", _active_project_name)
        break
st.title(f"🧬 {_active_project_name}")

# Determine if the chat area needs periodic auto-refresh.
# This is evaluated ONCE per full script run; the fragment uses it.
_needs_auto_refresh = st.session_state.get("_has_running_job", False)
_refresh_interval = timedelta(seconds=min(poll_seconds, 2)) if _needs_auto_refresh else None


@st.fragment(run_every=_refresh_interval)
def _render_chat():
    """Fragment that renders all chat blocks.

    When ``run_every`` is set (job is running), Streamlit re-executes
    ONLY this function on a timer — the sidebar, title, and chat-input
    all stay stable so there is no visible page flash.
    """
    _active_id = st.session_state.active_project_id

    # 1. Fetch & Sanitize
    blocks = get_sanitized_blocks(_active_id)
    blocks = [b for b in blocks if b.get("project_id") == _active_id]
    st.session_state.blocks = blocks

    if not blocks:
        st.session_state["_has_running_job"] = False
        # Auto-send welcome prompt for empty projects
        if not st.session_state.get("_welcome_sent_for") or st.session_state["_welcome_sent_for"] != _active_id:
            st.session_state["_welcome_sent_for"] = _active_id
            try:
                resp = make_authenticated_request(
                    "POST",
                    f"{API_URL}/chat",
                    json={
                        "project_id": _active_id,
                        "message": "Hello, what can you help me with?",
                        "skill": "welcome",
                        "model": model_choice
                    }
                )
                if resp.status_code == 200:
                    time.sleep(0.5)
                    st.rerun()
            except Exception:
                pass
        st.info(f"👋 **Project `{_active_id}` is empty.**\n\nAsk Agoutic to start a task!")
        return

    # 2. Pagination
    max_visible = st.session_state.get("_max_visible_blocks", 30)
    if len(blocks) > max_visible:
        hidden_count = len(blocks) - max_visible
        if st.button(f"⬆️ Load {min(hidden_count, 30)} older messages ({hidden_count} hidden)"):
            st.session_state["_max_visible_blocks"] = max_visible + 30
            st.rerun()
        visible_blocks = blocks[-max_visible:]
    else:
        visible_blocks = blocks

    # 3. Scan ALL blocks for running jobs
    _has_running_job = False
    _has_pending_submission = False
    _has_finished_job = False
    for blk in blocks:
        btype = blk.get("type")
        bstatus = blk.get("status")
        if btype == "EXECUTION_JOB" and bstatus == "RUNNING":
            _has_running_job = True
        if btype == "EXECUTION_JOB" and bstatus in ("DONE", "FAILED"):
            _has_finished_job = True
        if btype == "APPROVAL_GATE" and bstatus == "APPROVED":
            _has_pending_submission = True

    # 4. Render visible blocks
    for blk in visible_blocks:
        render_block(blk, expected_project_id=_active_id)

    # 5. Determine if auto-refresh should stay active
    if _has_pending_submission and not _has_running_job and not _has_finished_job:
        _has_running_job = True

    # Grace window: keep refreshing 30s after completion to catch auto-analysis
    if _has_finished_job and not _has_running_job:
        last_finish = st.session_state.get("_job_finished_at")
        if last_finish is None:
            st.session_state["_job_finished_at"] = time.time()
            _has_running_job = True
        elif time.time() - last_finish < 30:
            _has_running_job = True
    elif _has_running_job:
        st.session_state.pop("_job_finished_at", None)

    st.session_state["_has_running_job"] = _has_running_job

    # Show refresh indicator inside the fragment
    if _needs_auto_refresh:
        st.caption(
            f"🔄 Live updating "
            f"(last: {datetime.datetime.now().strftime('%H:%M:%S')})"
        )


_render_chat()

# Bootstrap: if a running job was just detected but the fragment was NOT
# started with run_every (because _has_running_job was False before the
# fragment ran), trigger ONE full rerun so the fragment gets re-registered
# with auto-refresh enabled.  Likewise when auto-refresh should stop.
_running_now = st.session_state.get("_has_running_job", False)
if _running_now != _needs_auto_refresh:
    time.sleep(0.3)
    st.rerun()

st.write("---")

# 3. Chat Input
if prompt := st.chat_input("Ask Agoutic to do something..."):
    with st.chat_message("user"):
        st.write(prompt)

    # --- Threaded request with real-time progress polling ---
    request_id = str(uuid.uuid4())
    session_token = get_session_cookie()
    _result_holder = {"response": None, "error": None}

    def _send_chat_request():
        try:
            cookies = {"session": session_token} if session_token else {}
            _result_holder["response"] = requests.post(
                f"{API_URL}/chat",
                json={
                    "project_id": active_id,
                    "message": prompt,
                    "skill": "welcome",
                    "model": model_choice,
                    "request_id": request_id,
                },
                cookies=cookies,
                timeout=300,
            )
        except Exception as exc:
            _result_holder["error"] = exc

    thread = threading.Thread(target=_send_chat_request, daemon=True)
    thread.start()

    # Stage emoji map for nice display
    _stage_icons = {
        "waiting": "⏳",
        "thinking": "🧠",
        "switching": "🔄",
        "context": "📋",
        "tools": "🔌",
        "analyzing": "📊",
        "done": "✅",
    }

    with st.chat_message("assistant"):
        status_box = st.status("🧠 Thinking...", expanded=True)
        status_text = status_box.empty()
        start_time = time.time()
        last_detail = ""

        while thread.is_alive():
            elapsed = time.time() - start_time
            # Poll the backend for current processing stage
            try:
                cookies = {"session": session_token} if session_token else {}
                sr = requests.get(
                    f"{API_URL}/chat/status/{request_id}",
                    cookies=cookies,
                    timeout=3,
                )
                if sr.status_code == 200:
                    info = sr.json()
                    stage = info.get("stage", "thinking")
                    detail = info.get("detail", "")
                    icon = _stage_icons.get(stage, "⏳")
                    display = detail or "Processing..."
                    if detail != last_detail:
                        last_detail = detail
                    status_box.update(label=f"{icon} {display}")
                    status_text.caption(f"⏱️ {elapsed:.0f}s elapsed")
            except Exception:
                status_text.caption(f"⏱️ {elapsed:.0f}s elapsed")

            thread.join(timeout=1.5)

        thread.join()
        elapsed = time.time() - start_time

        if _result_holder["error"]:
            status_box.update(label="❌ Error", state="error", expanded=True)
            st.error(f"Failed to send message: {_result_holder['error']}")
        elif _result_holder["response"] is not None and _result_holder["response"].status_code == 429:
            status_box.update(label="🪙 Token Limit Reached", state="error", expanded=True)
            try:
                _detail = _result_holder["response"].json().get("detail", {})
                _used = _detail.get("tokens_used", 0)
                _limit = _detail.get("token_limit", 0)
                st.warning(
                    f"**🪙 Token quota exceeded.**\n\n"
                    f"You have used **{_used:,}** of your **{_limit:,}** token limit. "
                    "Please contact an admin to increase your quota."
                )
            except Exception:
                st.warning("🪙 You have reached your token limit. Please contact an admin.")
        elif _result_holder["response"] is not None and _result_holder["response"].status_code != 200:
            status_box.update(label="❌ Error", state="error", expanded=True)
            st.error(
                f"Chat request failed: {_result_holder['response'].status_code} "
                f"- {_result_holder['response'].text}"
            )
        else:
            status_box.update(
                label=f"✅ Done ({elapsed:.0f}s)",
                state="complete",
                expanded=False,
            )
            time.sleep(0.3)
            st.rerun()

# 4. Auto-Refresh (only for general "Live Stream" toggle, NOT for job monitoring)
# Job monitoring is handled by the @st.fragment(run_every=...) above — no
# full-page rerun needed.
_suppress = st.session_state.get("_suppress_auto_refresh", 0)
if _suppress > 0:
    st.session_state["_suppress_auto_refresh"] = _suppress - 1
elif auto_refresh and not st.session_state.get("_has_running_job", False):
    # General background refresh when Live Stream is on and no job is running.
    # (When a job IS running, the fragment handles its own refresh.)
    time.sleep(poll_seconds)
    st.rerun()