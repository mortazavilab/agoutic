"""
Admin page for user management.
"""

import streamlit as st
import pandas as pd
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import require_auth, make_authenticated_request
import os

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="AGOUTIC Admin", layout="wide")

# Require authentication
user = require_auth(API_URL)

# Check if user is admin
if user.get('role') != 'admin':
    st.error("🚫 Admin access required")
    st.stop()

st.title("🔑 Admin - User Management")

# Fetch all users
try:
    resp = make_authenticated_request("GET", f"{API_URL}/admin/users")
    if resp.status_code == 200:
        users = resp.json()
        
        st.metric("Total Users", len(users))
        
        # Filter users
        tab1, tab2, tab3, tab4 = st.tabs(["Pending Approval", "Active Users", "All Users", "🪙 Token Usage"])
        
        with tab1:
            st.subheader("Users Pending Approval")
            pending_users = [u for u in users if not u['is_active']]
            
            if not pending_users:
                st.info("No users pending approval")
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
                    col1, col2, col3 = st.columns([3, 1, 1])
                    
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
                    "created_at": st.column_config.DatetimeColumn("Created", width="small"),
                    "last_login": st.column_config.DatetimeColumn("Last Login", width="small"),
                },
                hide_index=True,
                use_container_width=True
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
                            use_container_width=True,
                        )
                    else:
                        st.info("No token data recorded yet. Token tracking starts with the next LLM call.")

                    # ── Per-user leaderboard ───────────────────────────
                    st.markdown("#### Per-User Token Leaderboard")
                    user_tok = tok_data.get("users", [])
                    if user_tok:
                        df_users = pd.DataFrame(user_tok)
                        df_users = df_users[df_users["total_tokens"] > 0].reset_index(drop=True)
                        if not df_users.empty:
                            st.dataframe(
                                df_users[["email", "display_name", "total_tokens",
                                          "prompt_tokens", "completion_tokens", "message_count"]],
                                column_config={
                                    "email": st.column_config.TextColumn("Email"),
                                    "display_name": st.column_config.TextColumn("Name"),
                                    "total_tokens": st.column_config.NumberColumn("Total Tokens", format="%d"),
                                    "prompt_tokens": st.column_config.NumberColumn("Prompt", format="%d"),
                                    "completion_tokens": st.column_config.NumberColumn("Completion", format="%d"),
                                    "message_count": st.column_config.NumberColumn("Messages", format="%d"),
                                },
                                hide_index=True,
                                use_container_width=True,
                            )

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
                                                use_container_width=True,
                                            )
                                        # Per-user daily chart
                                        user_daily = detail.get("daily", [])
                                        if user_daily:
                                            df_ud = pd.DataFrame(user_daily)
                                            df_ud["date"] = pd.to_datetime(df_ud["date"])
                                            df_ud = df_ud.set_index("date")
                                            st.line_chart(
                                                df_ud[["prompt_tokens", "completion_tokens"]],
                                                use_container_width=True,
                                            )
                        else:
                            st.info("No token data recorded yet.")
                else:
                    st.error(f"Failed to fetch token usage: {tok_resp.status_code}")
                    st.code(tok_resp.text)
            except Exception as e:
                st.error(f"Error fetching token usage: {e}")

    else:
        st.error(f"Failed to fetch users: {resp.status_code}")
        st.code(resp.text)

except Exception as e:
    st.error(f"Error: {e}")
