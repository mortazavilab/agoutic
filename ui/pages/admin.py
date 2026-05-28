"""
Admin page for user management.
"""

from datetime import datetime, timedelta, timezone
import streamlit as st
import pandas as pd
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request
from components.cards import section_header, empty_state, stat_tile
import os
from common.database import SessionLocal
from cortex.maintenance_status import (
    DEFAULT_ACTIVE_JOB_MAX_AGE_HOURS,
    build_recommendation,
    build_snapshot,
    render_text_report,
)

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")


def _is_admin_user(user):
    return str((user or {}).get("role") or "").strip().lower() == "admin"


def _activity_refresh_interval(auto_refresh, interval_label):
    if not auto_refresh:
        return None
    seconds = {
        "15s": 15,
        "30s": 30,
        "60s": 60,
        "Off": None,
    }.get(str(interval_label or "30s"), 30)
    if seconds is None:
        return None
    return timedelta(seconds=seconds)


def _activity_chat_window_minutes(label):
    return {
        "5min": 5,
        "15min": 15,
        "60min": 60,
    }.get(str(label or "5min"), 5)


def _activity_max_age_hours(label, custom_hours=None):
    options = {
        "24h": 24,
        "168h (1 week)": 168,
        "720h (1 month)": 720,
        "Custom": "custom",
    }
    selected = options.get(str(label or "168h (1 week)"), 168)
    if selected == "custom":
        return max(int(custom_hours or DEFAULT_ACTIVE_JOB_MAX_AGE_HOURS), 1)
    return int(selected)


def _activity_snapshot(
    *,
    chat_window_minutes,
    active_job_max_age_hours,
    last_active_window_minutes=15,
    now=None,
):
    session = SessionLocal()
    try:
        return build_snapshot(
            session,
            last_active_window_minutes=last_active_window_minutes,
            chat_window_minutes=chat_window_minutes,
            active_job_max_age_hours=active_job_max_age_hours,
            now=now,
        )
    finally:
        session.close()


def _activity_recommendation(snapshot):
    return build_recommendation(snapshot)


def _activity_report_text(
    snapshot,
    *,
    chat_window_minutes,
    active_job_max_age_hours,
    last_active_window_minutes=15,
):
    return render_text_report(
        snapshot,
        last_active_window_minutes=last_active_window_minutes,
        chat_window_minutes=chat_window_minutes,
        active_job_max_age_hours=active_job_max_age_hours,
    )


def _parse_ui_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ui_relative_label(value, *, now):
    parsed = _parse_ui_datetime(value)
    if parsed is None:
        return "Unknown"
    seconds = max(int((now - parsed).total_seconds()), 0)
    if seconds < 60:
        return f"{seconds} sec ago"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} min ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hr ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def _ui_started_label(job, *, now):
    started_at = job.get("started_at")
    if started_at:
        return f"Started {_ui_relative_label(started_at, now=now)}"
    runtime_duration = str(job.get("runtime_duration") or "unknown")
    if runtime_duration != "unknown":
        return f"Started {runtime_duration} ago"
    return "Started unknown"


def _activity_users_dataframe(snapshot, *, now):
    return pd.DataFrame([
        {
            "Name": user.get("name", "—"),
            "Email": user.get("email", "—"),
            "Last activity": user.get("relative") or _ui_relative_label(user.get("last_activity_at"), now=now),
            "Source": str(user.get("source") or "—").title(),
        }
        for user in snapshot.get("users", [])
    ])


def _activity_jobs_dataframe(rows, *, now):
    return pd.DataFrame([
        {
            "Run UUID": row.get("run_uuid_short", "—"),
            "Workflow": row.get("workflow_type", "—"),
            "Owner": row.get("owner_email", "—"),
            "Project": row.get("project_name", "—"),
            "State": row.get("state", "—"),
            "Started": _ui_started_label(row, now=now),
            "Runtime": row.get("runtime_duration", "unknown"),
        }
        for row in rows
    ])


def _activity_chats_dataframe(snapshot, *, now):
    return pd.DataFrame([
        {
            "Owner": chat.get("owner_email", "—"),
            "Project": chat.get("project_name", "—"),
            "Last message": _ui_relative_label(chat.get("last_message_at"), now=now),
            "Messages": chat.get("message_count", 0),
        }
        for chat in snapshot.get("chats", [])
    ])


def _activity_transfers_dataframe(rows):
    return pd.DataFrame([
        {
            "Source": row.get("source", "—"),
            "Identifier": row.get("identifier", "—"),
            "State": row.get("state", "—"),
            "Owner": row.get("owner_email", "—"),
            "Project": row.get("project_name", "—"),
            "Workflow": row.get("workflow_type", "—"),
            "Duration": row.get("duration", "unknown"),
        }
        for row in rows
    ])


def _render_activity_banner(recommendation):
    status = str(recommendation.get("status") or "SAFE TO RESTART")
    message = str(recommendation.get("message") or status)
    safe = status == "SAFE TO RESTART"
    background = "#1f7a4a" if safe else "#f1c26b"
    color = "#ffffff" if safe else "#2f2413"
    st.markdown(
        (
            f"<div style='background:{background};color:{color};padding:1rem 1.25rem;"
            "border-radius:0.75rem;margin:0 0 1rem 0;font-weight:700;font-size:1.15rem;'>"
            f"{message}</div>"
        ),
        unsafe_allow_html=True,
    )

st.set_page_config(page_title="AGOUTIC Admin", layout="wide")

# Require authentication
user = require_auth(API_URL)

# Check if user is admin
if not _is_admin_user(user):
    st.error("🚫 Admin access required")
    st.stop()

section_header("Admin - User Management", "Approvals, active users, and token controls", icon="🔑")

# Fetch all users
try:
    resp = make_authenticated_request("GET", f"{API_URL}/admin/users")
    if resp.status_code == 200:
        users = resp.json()
        
        stat_tile("Total Users", len(users), icon="👥")
        
        # Filter users
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["Pending Approval", "Active Users", "All Users", "🪙 Token Usage", "Activity"])
        
        with tab1:
            st.subheader("Users Pending Approval")
            pending_users = [u for u in users if not u['is_active']]
            
            if not pending_users:
                empty_state("No users pending approval", "New registrations will appear here.", icon="✅")
            else:
                for u in pending_users:
                    with st.container():
                        col1, col2, col3 = st.columns([3, 1, 1])
                        
                        with col1:
                            st.write(f"**{u['display_name'] or 'No name'}**")
                            st.caption(f"Email: {u['email']}")
                            st.caption(f"Registered: {u['created_at'][:10]}")
                        
                        with col2:
                            if st.button("✅ Approve", key=f"approve_{u['id']}"):
                                resp = make_authenticated_request(
                                    "POST",
                                    f"{API_URL}/admin/users/{u['id']}/approve"
                                )
                                if resp.status_code == 200:
                                    st.success(f"Approved {u['email']}")
                                    st.rerun()
                                else:
                                    st.error(f"Failed: {resp.text}")
                        
                        with col3:
                            if st.button("❌ Reject", key=f"reject_{u['id']}"):
                                # For now, just don't approve. Could add a delete endpoint later
                                st.warning("User remains pending")
                        
                        st.divider()
        
        with tab2:
            st.subheader("Active Users")
            active_users = [u for u in users if u['is_active']]
            
            for u in active_users:
                with st.container():
                    col1, col2, col3, col4 = st.columns([3, 1, 1, 2])
                    
                    with col1:
                        st.write(f"**{u['display_name'] or 'No name'}**")
                        st.caption(f"Email: {u['email']}")
                        st.caption(f"Role: {u['role']} | Last login: {u.get('last_login', 'Never')[:10] if u.get('last_login') else 'Never'}")
                    
                    with col2:
                        if u['role'] != 'admin' and st.button("⬆️ Make Admin", key=f"promote_{u['id']}"):
                            resp = make_authenticated_request(
                                "POST",
                                f"{API_URL}/admin/users/{u['id']}/promote"
                            )
                            if resp.status_code == 200:
                                st.success(f"Promoted {u['email']} to admin")
                                st.rerun()
                            else:
                                st.error(f"Failed: {resp.text}")
                    
                    with col3:
                        if u['id'] != user['id'] and st.button("🚫 Revoke", key=f"revoke_{u['id']}"):
                            resp = make_authenticated_request(
                                "POST",
                                f"{API_URL}/admin/users/{u['id']}/revoke"
                            )
                            if resp.status_code == 200:
                                st.success(f"Revoked access for {u['email']}")
                                st.rerun()
                            else:
                                st.error(f"Failed: {resp.text}")

                    with col4:
                        _cur_limit = u.get('token_limit')
                        _limit_label = f"🪙 Limit: {_cur_limit:,}" if _cur_limit else "🪙 Limit: Unlimited"
                        with st.expander(_limit_label, expanded=False):
                            with st.form(key=f"limit_form_{u['id']}"):
                                _new_limit = st.number_input(
                                    "Token limit (0 = unlimited)",
                                    min_value=0,
                                    value=_cur_limit or 0,
                                    step=50000,
                                    key=f"limit_input_{u['id']}",
                                )
                                if st.form_submit_button("Save"):
                                    _payload = {"token_limit": int(_new_limit) if _new_limit > 0 else None}
                                    _lr = make_authenticated_request(
                                        "PATCH",
                                        f"{API_URL}/admin/users/{u['id']}/token-limit",
                                        json=_payload,
                                    )
                                    if _lr.status_code == 200:
                                        st.success("Saved")
                                        st.rerun()
                                    else:
                                        st.error(_lr.text)
                    
                    st.divider()
        
        with tab3:
            st.subheader("All Users")
            st.dataframe(
                users,
                column_config={
                    "id": st.column_config.TextColumn("ID", width="small"),
                    "email": st.column_config.TextColumn("Email", width="medium"),
                    "display_name": st.column_config.TextColumn("Name", width="medium"),
                    "role": st.column_config.TextColumn("Role", width="small"),
                    "is_active": st.column_config.CheckboxColumn("Active", width="small"),
                    "token_limit": st.column_config.NumberColumn("🪙 Token Limit", width="small", format="%d"),
                    "created_at": st.column_config.DatetimeColumn("Created", width="small"),
                    "last_login": st.column_config.DatetimeColumn("Last Login", width="small"),
                },
                hide_index=True,
                width="stretch"
            )

        with tab4:
            st.subheader("🪙 Token Usage")

            try:
                tok_resp = make_authenticated_request("GET", f"{API_URL}/admin/token-usage/summary")
                if tok_resp.status_code == 200:
                    tok_data = tok_resp.json()

                    # ── Global daily chart ─────────────────────────────
                    daily = tok_data.get("daily", [])
                    if daily:
                        st.markdown("#### Global Daily Token Usage")
                        df_daily = pd.DataFrame(daily)
                        df_daily["date"] = pd.to_datetime(df_daily["date"])
                        df_daily = df_daily.set_index("date")
                        st.line_chart(
                            df_daily[["prompt_tokens", "completion_tokens", "total_tokens"]],
                            width="stretch",
                        )
                    else:
                        st.info("No token data recorded yet. Token tracking starts with the next LLM call.")

                    # ── Per-user leaderboard with limit column ────────
                    st.markdown("#### Per-User Token Leaderboard")
                    user_tok = tok_data.get("users", [])

                    # Enrich with token_limit from the users list
                    _uid_to_limit = {u["id"]: u.get("token_limit") for u in users}

                    if user_tok:
                        df_users = pd.DataFrame(user_tok)
                        df_users["token_limit"] = df_users["user_id"].map(_uid_to_limit)
                        df_users["% used"] = df_users.apply(
                            lambda r: round(r["total_tokens"] / r["token_limit"] * 100, 1)
                            if r["token_limit"] else None,
                            axis=1,
                        )
                        df_show = df_users[df_users["total_tokens"] > 0].reset_index(drop=True)
                        if not df_show.empty:
                            st.dataframe(
                                df_show[[
                                    "email", "display_name",
                                    "total_tokens", "prompt_tokens", "completion_tokens",
                                    "message_count", "token_limit", "% used",
                                ]],
                                column_config={
                                    "email": st.column_config.TextColumn("Email"),
                                    "display_name": st.column_config.TextColumn("Name"),
                                    "total_tokens": st.column_config.NumberColumn("Total Tokens", format="%d"),
                                    "prompt_tokens": st.column_config.NumberColumn("Prompt", format="%d"),
                                    "completion_tokens": st.column_config.NumberColumn("Completion", format="%d"),
                                    "message_count": st.column_config.NumberColumn("Messages", format="%d"),
                                    "token_limit": st.column_config.NumberColumn("🪙 Limit", format="%d"),
                                    "% used": st.column_config.NumberColumn("% Used", format="%.1f%%"),
                                },
                                hide_index=True,
                                width="stretch",
                            )

                        # ── Set token limits ───────────────────────────────────
                        st.markdown("#### 🪙 Set Token Limits")
                        st.caption("Set a hard cap on total tokens per user. 0 = unlimited.")
                        _all_non_admin = [u for u in users if u["role"] != "admin"]
                        with st.form("_bulk_limit_form"):
                            _limit_rows = []
                            for _u in _all_non_admin:
                                _cur = _u.get("token_limit") or 0
                                _new = st.number_input(
                                    f"{_u['display_name'] or _u['email']}",
                                    min_value=0,
                                    value=_cur,
                                    step=50000,
                                    key=f"_bulk_limit_{_u['id']}",
                                    help=_u["email"],
                                )
                                _limit_rows.append((_u["id"], _new))
                            if st.form_submit_button("💾 Save All Limits"):
                                _ok, _fail = 0, 0
                                for _uid, _lval in _limit_rows:
                                    _payload = {"token_limit": int(_lval) if _lval > 0 else None}
                                    _r = make_authenticated_request(
                                        "PATCH",
                                        f"{API_URL}/admin/users/{_uid}/token-limit",
                                        json=_payload,
                                    )
                                    if _r.status_code == 200:
                                        _ok += 1
                                    else:
                                        _fail += 1
                                st.toast(f"Saved {_ok} limit(s)" + (f", {_fail} error(s)" if _fail else ""))
                                st.rerun()

                        # ── Per-user drill-down ────────────────────────────────
                        st.markdown("#### Drill Down by User")
                        drill_email = st.selectbox(
                            "Select a user to see per-conversation breakdown",
                            options=[r["email"] for r in user_tok if r["total_tokens"] > 0],
                            key="_admin_tok_drill",
                        )
                        if drill_email:
                            # Find user_id for the selected email
                            selected_user = next(
                                (u for u in users if u["email"] == drill_email), None
                            )
                            if selected_user:
                                detail_resp = make_authenticated_request(
                                    "GET",
                                    f"{API_URL}/admin/token-usage",
                                    params={"user_id": selected_user["id"]},
                                )
                                if detail_resp.status_code == 200:
                                    detail = detail_resp.json()
                                    convs = detail.get("by_conversation", [])
                                    if convs:
                                        st.dataframe(
                                            pd.DataFrame(convs)[
                                                ["title", "project_id", "total_tokens",
                                                 "prompt_tokens", "completion_tokens",
                                                 "last_message_at"]
                                            ],
                                            column_config={
                                                "title": st.column_config.TextColumn("Conversation"),
                                                "project_id": st.column_config.TextColumn("Project"),
                                                "total_tokens": st.column_config.NumberColumn("Total"),
                                                "prompt_tokens": st.column_config.NumberColumn("Prompt"),
                                                "completion_tokens": st.column_config.NumberColumn("Completion"),
                                                "last_message_at": st.column_config.TextColumn("Last Active"),
                                            },
                                            hide_index=True,
                                            width="stretch",
                                        )
                                    # Per-user daily chart
                                    user_daily = detail.get("daily", [])
                                    if user_daily:
                                        df_ud = pd.DataFrame(user_daily)
                                        df_ud["date"] = pd.to_datetime(df_ud["date"])
                                        df_ud = df_ud.set_index("date")
                                        st.line_chart(
                                            df_ud[["prompt_tokens", "completion_tokens"]],
                                            width="stretch",
                                        )
                    else:
                        st.info("No token data recorded yet.")
                else:
                    st.error(f"Failed to fetch token usage: {tok_resp.status_code}")
                    st.code(tok_resp.text)
            except Exception as e:
                st.error(f"Error fetching token usage: {e}")

        with tab5:
            st.subheader("Activity")

            controls_left, controls_right = st.columns([4, 3])
            with controls_right:
                _refresh_now = st.button("Refresh now", key="_admin_activity_refresh_now")
                _copy_report = st.button("Copy report", key="_admin_activity_copy_report")
                _auto_refresh = st.toggle("Auto-refresh", value=True, key="_admin_activity_auto_refresh")
                _interval_label = st.selectbox(
                    "Interval",
                    ["15s", "30s", "60s", "Off"],
                    index=1,
                    key="_admin_activity_interval",
                )
                _max_age_label = st.selectbox(
                    "Active-job age threshold",
                    ["24h", "168h (1 week)", "720h (1 month)", "Custom"],
                    index=1,
                    key="_admin_activity_max_age_label",
                )
                _custom_age_hours = DEFAULT_ACTIVE_JOB_MAX_AGE_HOURS
                if _max_age_label == "Custom":
                    _custom_age_hours = st.number_input(
                        "Custom threshold (hours)",
                        min_value=1,
                        value=DEFAULT_ACTIVE_JOB_MAX_AGE_HOURS,
                        step=24,
                        key="_admin_activity_custom_max_age",
                    )
                _chat_window_label = st.selectbox(
                    "Chat activity window",
                    ["5min", "15min", "60min"],
                    index=0,
                    key="_admin_activity_chat_window",
                )

            with controls_left:
                st.caption(
                    "Live maintenance view for admins. Recently active users are inferred from chat and job records, not a presence heartbeat."
                )

            if _refresh_now:
                st.rerun()

            _active_job_max_age_hours = _activity_max_age_hours(_max_age_label, _custom_age_hours)
            _chat_window_minutes = _activity_chat_window_minutes(_chat_window_label)
            _refresh_interval = _activity_refresh_interval(_auto_refresh, _interval_label)

            @st.fragment(run_every=_refresh_interval)
            def _render_activity_tab():
                snapshot = _activity_snapshot(
                    chat_window_minutes=_chat_window_minutes,
                    active_job_max_age_hours=_active_job_max_age_hours,
                )
                recommendation = _activity_recommendation(snapshot)
                report_text = _activity_report_text(
                    snapshot,
                    chat_window_minutes=_chat_window_minutes,
                    active_job_max_age_hours=_active_job_max_age_hours,
                )
                now_utc = datetime.now(timezone.utc)

                _render_activity_banner(recommendation)

                if _refresh_interval is not None:
                    st.caption(
                        f"Live auto-refresh every {_interval_label}. Last update: {datetime.now().strftime('%H:%M:%S')}"
                    )
                else:
                    st.caption(
                        f"Auto-refresh is off. Last update: {datetime.now().strftime('%H:%M:%S')}"
                    )

                if _copy_report:
                    st.code(report_text, language="text")

                st.markdown(
                    "#### Recently active users (approximated from chat and job activity — AGOUTIC does not track presence)"
                )
                users_df = _activity_users_dataframe(snapshot, now=now_utc)
                if users_df.empty:
                    empty_state(
                        "No recently active users",
                        "No chat or job activity detected in the current window.",
                        icon="🛌",
                    )
                else:
                    st.dataframe(users_df, hide_index=True, width="stretch")

                st.markdown("#### Currently running jobs")
                jobs_df = _activity_jobs_dataframe(snapshot.get("jobs", []), now=now_utc)
                if jobs_df.empty:
                    empty_state(
                        "No active jobs",
                        "Stale RUNNING/PENDING rows are excluded by the current threshold.",
                        icon="✅",
                    )
                else:
                    st.dataframe(jobs_df, hide_index=True, width="stretch")

                st.markdown("#### Active chat sessions")
                chats_df = _activity_chats_dataframe(snapshot, now=now_utc)
                if chats_df.empty:
                    empty_state(
                        "No active chats",
                        "No conversations have messages in the configured chat window.",
                        icon="💬",
                    )
                else:
                    st.dataframe(chats_df, hide_index=True, width="stretch")

                transfers_df = _activity_transfers_dataframe(snapshot.get("transfers", []))
                if not transfers_df.empty:
                    st.markdown("#### Active transfers and workflow imports")
                    st.dataframe(transfers_df, hide_index=True, width="stretch")

                stale_jobs_df = _activity_jobs_dataframe(snapshot.get("stale_jobs", []), now=now_utc)
                stale_transfers_df = _activity_transfers_dataframe(snapshot.get("stale_transfers", []))
                if not stale_jobs_df.empty or not stale_transfers_df.empty:
                    with st.expander("Stale rows excluded from recommendation", expanded=False):
                        if not stale_jobs_df.empty:
                            st.markdown("**Stale jobs**")
                            st.dataframe(stale_jobs_df, hide_index=True, width="stretch")
                        if not stale_transfers_df.empty:
                            st.markdown("**Stale transfers**")
                            st.dataframe(stale_transfers_df, hide_index=True, width="stretch")

            _render_activity_tab()

    else:
        st.error(f"Failed to fetch users: {resp.status_code}")
        st.code(resp.text)

except Exception as e:
    st.error(f"Error: {e}")
