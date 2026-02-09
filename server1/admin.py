"""
Admin endpoints for AGOUTIC.

Provides user management functionality:
- List all users
- Approve/revoke user access
- Promote users to admin
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select

from server1.db import SessionLocal
from server1.models import User
from server1.dependencies import require_admin


router = APIRouter(prefix="/admin", tags=["admin"])


class UserListItem(BaseModel):
    id: str
    email: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: str
    last_login: str | None


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
                role=user.role,
                is_active=user.is_active,
                created_at=user.created_at.isoformat() if user.created_at else None,
                last_login=user.last_login.isoformat() if user.last_login else None,
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
