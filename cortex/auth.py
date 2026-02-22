"""
Authentication module for AGOUTIC using Google OAuth 2.0.

Provides endpoints for:
- /auth/login: Redirect to Google OAuth
- /auth/callback: Handle Google OAuth callback
- /auth/logout: Invalidate session
"""

import uuid
import json
from datetime import datetime, timedelta
from typing import Optional

import re

from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import RedirectResponse
from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import BaseModel
from sqlalchemy import select

from cortex.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    SUPER_ADMIN_EMAIL,
    FRONTEND_URL,
    SESSION_EXPIRES_HOURS,
    ENVIRONMENT,
    AGOUTIC_DATA,
)
from cortex.db import SessionLocal
from cortex.models import User, Session as SessionModel

router = APIRouter(prefix="/auth", tags=["auth"])

# Google OAuth configuration
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


@router.get("/login")
async def login():
    """
    Redirect user to Google OAuth login page.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )
    
    # Build authorization URL
    client = AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    
    authorization_url, state = client.create_authorization_url(
        GOOGLE_AUTH_URL,
        scope="openid email profile",
    )
    
    # Redirect to Google
    return RedirectResponse(authorization_url)


@router.get("/callback")
async def callback(request: Request, code: str, response: Response):
    """
    Handle Google OAuth callback.
    
    1. Exchange authorization code for access token
    2. Fetch user info from Google
    3. Create or update user in database
    4. Create session
    5. Set session cookie
    6. Redirect to frontend
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth not configured"
        )
    
    # Exchange code for token
    client = AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    
    try:
        token = await client.fetch_token(
            GOOGLE_TOKEN_URL,
            code=code,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch token: {str(e)}")
    
    # Get user info from Google
    try:
        import httpx
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token['access_token']}"}
            )
            resp.raise_for_status()
            user_info = resp.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch user info: {str(e)}")
    
    email = user_info.get("email")
    google_sub_id = user_info.get("sub")
    display_name = user_info.get("name")
    
    if not email or not google_sub_id:
        raise HTTPException(status_code=400, detail="Invalid user info from Google")
    
    # Create or update user in database
    session = SessionLocal()
    try:
        # Check if user exists
        result = session.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        
        if user:
            # Update existing user
            user.google_sub_id = google_sub_id
            user.display_name = display_name
            user.last_login = datetime.utcnow()
            session.commit()
        else:
            # Create new user
            user_id = str(uuid.uuid4())
            
            # Check if this is the super admin
            is_super_admin = (email == SUPER_ADMIN_EMAIL)
            
            user = User(
                id=user_id,
                email=email,
                google_sub_id=google_sub_id,
                display_name=display_name,
                role="admin" if is_super_admin else "user",
                is_active=is_super_admin,  # Super admin is auto-approved
                created_at=datetime.utcnow(),
                last_login=datetime.utcnow(),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        
        # Create session token
        session_id = str(uuid.uuid4())
        expires_at = datetime.utcnow() + timedelta(hours=SESSION_EXPIRES_HOURS)
        
        session_obj = SessionModel(
            id=session_id,
            user_id=user.id,
            created_at=datetime.utcnow(),
            expires_at=expires_at,
            is_valid=True,
        )
        session.add(session_obj)
        session.commit()
        
        # Set cookie — secure flags based on environment
        is_prod = ENVIRONMENT != "development"
        response = RedirectResponse(url=FRONTEND_URL)
        response.set_cookie(
            key="session",
            value=session_id,
            httponly=True,
            secure=is_prod,           # True in production (HTTPS only)
            samesite="lax",           # Prevents CSRF while allowing top-level navigations
            max_age=SESSION_EXPIRES_HOURS * 3600,
            path="/",
        )
        
        return response
        
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        session.close()


@router.post("/logout")
async def logout(request: Request, response: Response):
    """
    Invalidate the current session and clear the cookie.
    """
    session_id = request.cookies.get("session")
    
    if session_id:
        session = SessionLocal()
        try:
            # Invalidate session in database
            result = session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            session_obj = result.scalar_one_or_none()
            
            if session_obj:
                session_obj.is_valid = False
                session.commit()
        finally:
            session.close()
    
    # Clear cookie
    response = Response(content=json.dumps({"status": "logged_out"}), media_type="application/json")
    response.delete_cookie(key="session", path="/")
    
    return response


@router.get("/me")
async def get_current_user_info(request: Request):
    """
    Get information about the currently logged-in user.
    Requires valid session (will be enforced by middleware).
    """
    # This will be set by the auth middleware
    user = getattr(request.state, "user", None)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
    }


# ---------------------------------------------------------------------------
# Username management
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


class SetUsernameRequest(BaseModel):
    username: str


@router.get("/check-username/{username}")
async def check_username_availability(username: str):
    """Check if a username is available and valid."""
    if not _USERNAME_RE.match(username):
        return {"available": False, "reason": "Invalid format. Use lowercase letters, numbers, hyphens, underscores. 2-31 chars."}

    session = SessionLocal()
    try:
        existing = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if existing:
            return {"available": False, "reason": "Username already taken."}
        return {"available": True, "reason": None}
    finally:
        session.close()


@router.post("/set-username")
async def set_username(req: SetUsernameRequest, request: Request):
    """Set username for the current user (first-time onboarding only).

    Users can only call this when their username is NULL.
    After the initial set, only admins can change usernames.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    username = req.username.strip().lower()

    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="Invalid username. Use lowercase letters, numbers, hyphens, underscores. 2-31 chars.",
        )

    session = SessionLocal()
    try:
        db_user = session.execute(
            select(User).where(User.id == user.id)
        ).scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")

        if db_user.username is not None:
            raise HTTPException(
                status_code=409,
                detail="Username already set. Contact an admin to change it.",
            )

        # Check uniqueness
        clash = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(status_code=409, detail="Username already taken.")

        db_user.username = username
        session.commit()

        # Create home directory
        user_home = AGOUTIC_DATA / "users" / username
        user_home.mkdir(parents=True, exist_ok=True)

        return {"username": username, "message": "Username set successfully."}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
