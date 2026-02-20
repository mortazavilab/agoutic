"""
Admin endpoints for AGOUTIC.

Provides user management functionality:
- List all users
- Approve/revoke user access
- Promote users to admin
- Set/change usernames (admin only)
"""

import re
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select, text

from server1.config import AGOUTIC_DATA
from server1.db import SessionLocal
from server1.models import User, Conversation, ConversationMessage
from server1.dependencies import require_admin
from server1.user_jail import rename_user_dir


router = APIRouter(prefix="/admin", tags=["admin"])


class UserListItem(BaseModel):
    id: str
    email: str
    display_name: str | None
    username: str | None
    role: str
    is_active: bool
    created_at: str
    last_login: str | None
    token_limit: int | None = None


class UserUpdateRequest(BaseModel):
    is_active: bool | None = None
    role: str | None = None


@router.get("/users", response_model=List[UserListItem])
async def list_users(admin: User = Depends(require_admin)):
    """
    List all users in the system.
    Admin-only endpoint.
    """
    session = SessionLocal()
    try:
        result = session.execute(select(User))
        users = result.scalars().all()
        
        return [
            UserListItem(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                username=user.username,
                role=user.role,
                is_active=user.is_active,
                created_at=user.created_at.isoformat() if user.created_at else None,
                last_login=user.last_login.isoformat() if user.last_login else None,
                token_limit=user.token_limit,
            )
            for user in users
        ]
    finally:
        session.close()


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    update: UserUpdateRequest,
    admin: User = Depends(require_admin)
):
    """
    Update user permissions.
    Admin-only endpoint.
    
    Allows admins to:
    - Approve/revoke access (is_active)
    - Promote to admin (role)
    """
    session = SessionLocal()
    try:
        result = session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Prevent admin from deactivating themselves
        if user_id == admin.id and update.is_active is False:
            raise HTTPException(
                status_code=400,
                detail="Cannot deactivate your own account"
            )
        
        # Prevent admin from demoting themselves
        if user_id == admin.id and update.role == "user":
            raise HTTPException(
                status_code=400,
                detail="Cannot demote your own account"
            )
        
        # Apply updates
        if update.is_active is not None:
            user.is_active = update.is_active
        
        if update.role is not None:
            if update.role not in ("user", "admin"):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid role. Must be 'user' or 'admin'"
                )
            user.role = update.role
        
        session.commit()
        session.refresh(user)
        
        return {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "message": "User updated successfully"
        }
    finally:
        session.close()


@router.post("/users/{user_id}/approve")
async def approve_user(user_id: str, admin: User = Depends(require_admin)):
    """
    Approve a user (set is_active=True).
    Admin-only endpoint.
    """
    session = SessionLocal()
    try:
        result = session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.is_active = True
        session.commit()
        
        return {
            "id": user.id,
            "email": user.email,
            "is_active": True,
            "message": "User approved"
        }
    finally:
        session.close()


@router.post("/users/{user_id}/revoke")
async def revoke_user(user_id: str, admin: User = Depends(require_admin)):
    """
    Revoke a user's access (set is_active=False).
    Admin-only endpoint.
    """
    session = SessionLocal()
    try:
        if user_id == admin.id:
            raise HTTPException(
                status_code=400,
                detail="Cannot revoke your own access"
            )
        
        result = session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.is_active = False
        session.commit()
        
        return {
            "id": user.id,
            "email": user.email,
            "is_active": False,
            "message": "User access revoked"
        }
    finally:
        session.close()


@router.post("/users/{user_id}/promote")
async def promote_to_admin(user_id: str, admin: User = Depends(require_admin)):
    """
    Promote a user to admin.
    Admin-only endpoint.
    """
    session = SessionLocal()
    try:
        result = session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.role = "admin"
        session.commit()
        
        return {
            "id": user.id,
            "email": user.email,
            "role": "admin",
            "message": "User promoted to admin"
        }
    finally:
        session.close()


@router.patch("/users/{user_id}/token-limit")
async def set_user_token_limit(
    user_id: str,
    body: dict,
    admin: User = Depends(require_admin),
):
    """Set or clear a user's token limit.

    Body: {"token_limit": 500000}  — set a hard cap.
          {"token_limit": null}    — remove the limit (unlimited).
    """
    session = SessionLocal()
    try:
        result = session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        limit_val = body.get("token_limit", "__missing__")
        if limit_val == "__missing__":
            raise HTTPException(status_code=422, detail="token_limit field required")

        if limit_val is not None and (not isinstance(limit_val, int) or limit_val < 0):
            raise HTTPException(status_code=422, detail="token_limit must be a positive integer or null")

        user.token_limit = limit_val
        session.commit()

        return {
            "id": user.id,
            "email": user.email,
            "token_limit": user.token_limit,
            "message": "Token limit updated" if limit_val is not None else "Token limit removed (unlimited)",
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Username management (admin-only)
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


class SetUsernameAdminRequest(BaseModel):
    username: str


@router.patch("/users/{user_id}/username")
async def admin_set_username(
    user_id: str,
    body: SetUsernameAdminRequest,
    admin: User = Depends(require_admin),
):
    """Set or change a user's username (admin only).

    Handles the filesystem rename when changing an existing username.
    Also updates nextflow_work_dir paths in dogme_jobs if the shared DB has that table.
    """
    username = body.username.strip().lower()

    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="Invalid username. Use lowercase letters, numbers, hyphens, underscores. 2-31 chars.",
        )

    session = SessionLocal()
    try:
        user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Check uniqueness
        clash = session.execute(
            select(User).where(User.username == username).where(User.id != user_id)
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(status_code=409, detail="Username already taken.")

        old_username = user.username
        user.username = username
        session.commit()

        # Filesystem rename
        if old_username and old_username != username:
            try:
                rename_user_dir(old_username, username)
            except PermissionError as e:
                # Rollback the DB change if we can't move the dir
                user.username = old_username
                session.commit()
                raise HTTPException(status_code=409, detail=str(e))

            # Update nextflow_work_dir paths in dogme_jobs
            try:
                old_prefix = str(AGOUTIC_DATA / "users" / old_username)
                new_prefix = str(AGOUTIC_DATA / "users" / username)
                session.execute(
                    text(
                        "UPDATE dogme_jobs SET nextflow_work_dir = "
                        "REPLACE(nextflow_work_dir, :old, :new) "
                        "WHERE nextflow_work_dir LIKE :pattern"
                    ),
                    {"old": old_prefix, "new": new_prefix, "pattern": old_prefix + "%"},
                )
                session.commit()
            except Exception:
                pass  # dogme_jobs may not be in this DB
        elif not old_username:
            # First time setting — create home dir
            user_home = AGOUTIC_DATA / "users" / username
            user_home.mkdir(parents=True, exist_ok=True)

        return {
            "id": user.id,
            "email": user.email,
            "username": username,
            "old_username": old_username,
            "message": "Username updated successfully.",
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/token-usage/summary")
async def admin_token_usage_summary(admin: User = Depends(require_admin)):
    """Leaderboard of token usage across all users.

    Returns a list of users sorted by total tokens consumed (descending),
    plus a global daily time-series.
    """
    session = SessionLocal()
    try:
        # Per-user lifetime totals
        user_rows = session.execute(
            text("""
                SELECT
                    u.id                                        AS user_id,
                    u.email,
                    u.display_name,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens,
                    COUNT(DISTINCT cm.id)                       AS message_count
                FROM users u
                LEFT JOIN conversations c ON c.user_id = u.id
                LEFT JOIN conversation_messages cm
                    ON cm.conversation_id = c.id
                    AND cm.role = 'assistant'
                    AND cm.total_tokens IS NOT NULL
                GROUP BY u.id, u.email, u.display_name
                ORDER BY total_tokens DESC
            """)
        ).fetchall()

        # Global daily time-series
        daily_rows = session.execute(
            text("""
                SELECT
                    DATE(cm.created_at)                         AS date,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens
                FROM conversation_messages cm
                WHERE cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
                GROUP BY DATE(cm.created_at)
                ORDER BY DATE(cm.created_at) ASC
            """)
        ).fetchall()

        return {
            "users": [
                {
                    "user_id": r[0],
                    "email": r[1],
                    "display_name": r[2],
                    "prompt_tokens": r[3],
                    "completion_tokens": r[4],
                    "total_tokens": r[5],
                    "message_count": r[6],
                }
                for r in user_rows
            ],
            "daily": [
                {
                    "date": str(r[0]),
                    "prompt_tokens": r[1],
                    "completion_tokens": r[2],
                    "total_tokens": r[3],
                }
                for r in daily_rows
            ],
        }
    finally:
        session.close()


@router.get("/token-usage")
async def admin_token_usage(
    user_id: str | None = None,
    project_id: str | None = None,
    admin: User = Depends(require_admin),
):
    """Detailed token usage with optional filters.

    Query params:
      ?user_id=<id>      — filter to a single user
      ?project_id=<id>   — filter to a single project

    Returns per-conversation breakdown and daily time-series.
    """
    session = SessionLocal()
    try:
        # Build WHERE clause dynamically
        filters = ["cm.role = 'assistant'", "cm.total_tokens IS NOT NULL"]
        params: dict = {}
        if user_id:
            filters.append("c.user_id = :user_id")
            params["user_id"] = user_id
        if project_id:
            filters.append("c.project_id = :project_id")
            params["project_id"] = project_id
        where = " AND ".join(filters)

        conv_rows = session.execute(
            text(f"""
                SELECT
                    c.id                                        AS conversation_id,
                    c.project_id,
                    c.title,
                    u.email,
                    u.display_name,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens,
                    MAX(cm.created_at)                          AS last_message_at
                FROM conversations c
                JOIN users u ON u.id = c.user_id
                JOIN conversation_messages cm ON cm.conversation_id = c.id
                WHERE {where}
                GROUP BY c.id, c.project_id, c.title, u.email, u.display_name
                ORDER BY last_message_at DESC
            """),
            params
        ).fetchall()

        daily_rows = session.execute(
            text(f"""
                SELECT
                    DATE(cm.created_at)                         AS date,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE {where}
                GROUP BY DATE(cm.created_at)
                ORDER BY DATE(cm.created_at) ASC
            """),
            params
        ).fetchall()

        return {
            "filters": {"user_id": user_id, "project_id": project_id},
            "by_conversation": [
                {
                    "conversation_id": r[0],
                    "project_id": r[1],
                    "title": r[2],
                    "email": r[3],
                    "display_name": r[4],
                    "prompt_tokens": r[5],
                    "completion_tokens": r[6],
                    "total_tokens": r[7],
                    "last_message_at": str(r[8]) if r[8] else None,
                }
                for r in conv_rows
            ],
            "daily": [
                {
                    "date": str(r[0]),
                    "prompt_tokens": r[1],
                    "completion_tokens": r[2],
                    "total_tokens": r[3],
                }
                for r in daily_rows
            ],
        }
    finally:
        session.close()
