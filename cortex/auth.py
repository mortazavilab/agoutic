"""
Authentication module for AGOUTIC using Google OAuth 2.0.

Provides endpoints for:
- /auth/login: Redirect to Google OAuth
- /auth/callback: Handle Google OAuth callback
- /auth/logout: Invalidate session
"""

import uuid
import json
import base64
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import re

from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import BaseModel
from sqlalchemy import select

from cortex.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    SUPER_ADMIN_EMAIL,
    FRONTEND_URL,
    LOCAL_UI_ALLOWED_ORIGINS,
    SESSION_EXPIRES_HOURS,
    SESSION_SECRET_KEY,
    ENVIRONMENT,
    AGOUTIC_DATA,
)
from cortex.db import SessionLocal
from cortex.models import User, Session as SessionModel
from cortex.middleware import get_request_session_token

router = APIRouter(prefix="/auth", tags=["auth"])

# Google OAuth configuration
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

_FLOW_COOKIE_NAME = "agoutic_auth_flow"
_FLOW_COOKIE_MAX_AGE_SECONDS = 600
_EXCHANGE_CODE_TTL_SECONDS = 300
_CLIENT_MODE_HOSTED = "hosted"
_CLIENT_MODE_LOCAL = "local"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}
_USED_EXCHANGE_CODES: dict[str, int] = {}


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(f"{data}{padding}")


def _normalized_origin(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _is_loopback_target(parsed) -> bool:
    return (parsed.hostname or "").strip().lower() in _LOOPBACK_HOSTS


def _normalize_return_target(return_to: str | None, client_mode: str | None) -> tuple[str, str]:
    frontend_target = FRONTEND_URL.rstrip("/")
    requested_mode = str(client_mode or "").strip().lower()

    if not return_to:
        return frontend_target, _CLIENT_MODE_HOSTED

    parsed = urlparse(return_to)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid return_to URL")

    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path or "",
        "",
        parsed.query or "",
        "",
    )).rstrip("/")

    frontend_origin = _normalized_origin(frontend_target)
    extra_local_origins = {origin for origin in LOCAL_UI_ALLOWED_ORIGINS if origin}
    return_origin = _normalized_origin(normalized)
    is_loopback = _is_loopback_target(parsed)
    is_allowed_local = is_loopback or return_origin in extra_local_origins

    if requested_mode == _CLIENT_MODE_LOCAL:
        if not is_allowed_local:
            raise HTTPException(status_code=400, detail="Local client return_to must use localhost, 127.0.0.1, or an allowlisted local origin")
        return normalized, _CLIENT_MODE_LOCAL

    if requested_mode == _CLIENT_MODE_HOSTED:
        if return_origin != frontend_origin:
            raise HTTPException(status_code=400, detail="Hosted client return_to must match FRONTEND_URL origin")
        return normalized, _CLIENT_MODE_HOSTED

    if is_allowed_local:
        return normalized, _CLIENT_MODE_LOCAL
    if return_origin == frontend_origin:
        return normalized, _CLIENT_MODE_HOSTED

    raise HTTPException(status_code=400, detail="return_to origin is not allowed")


def _encode_flow_cookie(return_to: str, client_mode: str) -> str:
    payload = json.dumps(
        {"return_to": return_to, "client_mode": client_mode},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _urlsafe_b64encode(payload)


def _decode_flow_cookie(raw_value: str | None) -> tuple[str, str]:
    if not raw_value:
        return FRONTEND_URL.rstrip("/"), _CLIENT_MODE_HOSTED
    try:
        payload = json.loads(_urlsafe_b64decode(raw_value).decode("utf-8"))
        return _normalize_return_target(payload.get("return_to"), payload.get("client_mode"))
    except HTTPException:
        raise
    except Exception:
        return FRONTEND_URL.rstrip("/"), _CLIENT_MODE_HOSTED


def _append_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    params.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(params)))


def _prune_used_exchange_codes(now_ts: int) -> None:
    expired_codes = [code for code, exp in _USED_EXCHANGE_CODES.items() if exp <= now_ts]
    for code in expired_codes:
        _USED_EXCHANGE_CODES.pop(code, None)


def _issue_local_exchange_code(session_id: str) -> str:
    expires_at = int((datetime.utcnow() + timedelta(seconds=_EXCHANGE_CODE_TTL_SECONDS)).timestamp())
    payload = json.dumps(
        {
            "sid": session_id,
            "exp": expires_at,
            "nonce": uuid.uuid4().hex,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(
        SESSION_SECRET_KEY.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    return f"{_urlsafe_b64encode(payload)}.{_urlsafe_b64encode(signature)}"


def _consume_local_exchange_code(code: str) -> str:
    now_ts = int(datetime.utcnow().timestamp())
    _prune_used_exchange_codes(now_ts)

    if not code or "." not in code:
        raise HTTPException(status_code=400, detail="Invalid exchange code")
    if code in _USED_EXCHANGE_CODES:
        raise HTTPException(status_code=400, detail="Exchange code has already been used")

    payload_b64, signature_b64 = code.split(".", 1)
    try:
        payload = _urlsafe_b64decode(payload_b64)
        signature = _urlsafe_b64decode(signature_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid exchange code") from exc

    expected_signature = hmac.new(
        SESSION_SECRET_KEY.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=400, detail="Invalid exchange code")

    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid exchange code") from exc

    expires_at = int(data.get("exp") or 0)
    if expires_at <= now_ts:
        raise HTTPException(status_code=400, detail="Exchange code expired")

    session_id = str(data.get("sid") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Invalid exchange code")

    _USED_EXCHANGE_CODES[code] = expires_at
    return session_id


def _resolve_request_session_id(request: Request) -> str | None:
    session_id, _transport = get_request_session_token(request)
    return session_id


def _apply_auth_flow_cookie(response: Response, *, return_to: str, client_mode: str) -> None:
    response.set_cookie(
        key=_FLOW_COOKIE_NAME,
        value=_encode_flow_cookie(return_to, client_mode),
        httponly=True,
        secure=ENVIRONMENT != "development",
        samesite="lax",
        max_age=_FLOW_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )


def _clear_auth_flow_cookie(response: Response) -> None:
    response.delete_cookie(key=_FLOW_COOKIE_NAME, path="/")


@router.get("/login")
async def login(return_to: str | None = None, client_mode: str | None = None):
    """
    Redirect user to Google OAuth login page.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )

    resolved_return_to, resolved_client_mode = _normalize_return_target(return_to, client_mode)
    
    # Build authorization URL
    client = AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    
    authorization_url, state = client.create_authorization_url(
        GOOGLE_AUTH_URL,
        scope="openid email profile",
    )
    
    response = RedirectResponse(authorization_url)
    _apply_auth_flow_cookie(response, return_to=resolved_return_to, client_mode=resolved_client_mode)
    return response


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

    return_to, client_mode = _decode_flow_cookie(request.cookies.get(_FLOW_COOKIE_NAME))
    
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
        
        if client_mode == _CLIENT_MODE_LOCAL:
            exchange_code = _issue_local_exchange_code(session_id)
            response = RedirectResponse(url=_append_query_param(return_to, "auth_code", exchange_code))
        else:
            # Set cookie — secure flags based on environment
            is_prod = ENVIRONMENT != "development"
            response = RedirectResponse(url=return_to)
            response.set_cookie(
                key="session",
                value=session_id,
                httponly=True,
                secure=is_prod,           # True in production (HTTPS only)
                samesite="lax",           # Prevents CSRF while allowing top-level navigations
                max_age=SESSION_EXPIRES_HOURS * 3600,
                path="/",
            )

        _clear_auth_flow_cookie(response)
        
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
    session_id = _resolve_request_session_id(request)
    
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


@router.post("/heartbeat")
async def heartbeat(request: Request):
    """
    Extend the current session's expiry.

    Called by the frontend while long-running jobs are active so the
    session does not expire and force the user to log in again.
    Returns the new ``expires_at`` timestamp.
    """
    session_id = _resolve_request_session_id(request)
    if not session_id:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    db = SessionLocal()
    try:
        result = db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )
        session_obj = result.scalar_one_or_none()
        if not session_obj or not session_obj.is_valid:
            return JSONResponse(status_code=401, content={"detail": "Invalid session"})

        new_expiry = datetime.utcnow() + timedelta(hours=SESSION_EXPIRES_HOURS)
        session_obj.expires_at = new_expiry
        db.commit()
        return {"status": "extended", "expires_at": new_expiry.isoformat()}
    finally:
        db.close()


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


class ExchangeCodeRequest(BaseModel):
    code: str


@router.post("/exchange")
async def exchange_code_for_session(req: ExchangeCodeRequest):
    """Exchange a short-lived local-client auth code for a bearer session."""
    session_id = _consume_local_exchange_code(req.code.strip())

    session = SessionLocal()
    try:
        session_obj = session.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        ).scalar_one_or_none()
        if not session_obj or not session_obj.is_valid:
            raise HTTPException(status_code=401, detail="Invalid session")

        expires_at = session_obj.expires_at
        if not isinstance(expires_at, datetime):
            expires_at = datetime.fromisoformat(str(expires_at))
        if datetime.utcnow() > expires_at:
            session_obj.is_valid = False
            session.commit()
            raise HTTPException(status_code=401, detail="Session expired")

        user = session.execute(
            select(User).where(User.id == session_obj.user_id)
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User is pending approval")

        return {
            "access_token": session_id,
            "token_type": "bearer",
            "expires_at": expires_at.isoformat(),
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "username": user.username,
                "role": user.role,
                "is_active": user.is_active,
            },
        }
    finally:
        session.close()


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
