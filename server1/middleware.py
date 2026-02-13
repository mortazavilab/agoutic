"""
Authentication middleware for AGOUTIC.

Validates session cookies on every request and attaches the user to request.state.
"""

from datetime import datetime
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select

from server1.db import SessionLocal
from server1.models import User, Session as SessionModel
from common.logging_config import get_logger

logger = get_logger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that validates session cookies and attaches user to request.state.
    
    Exempted paths:
    - /auth/* (login, callback, logout)
    - /health
    - /docs, /openapi.json (API documentation)
    """
    
    EXEMPT_PATHS = [
        "/auth/login",
        "/auth/callback",
        "/auth/logout",
        "/health",
        "/docs",
        "/openapi.json",
    ]
    
    async def dispatch(self, request: Request, call_next):
        # Check if path is exempted
        if any(request.url.path.startswith(path) for path in self.EXEMPT_PATHS):
            return await call_next(request)
        
        # Get session cookie
        session_id = request.cookies.get("session")
        
        if not session_id:
            logger.debug("Unauthenticated request", path=request.url.path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated. Please log in."}
            )
        
        # Validate session
        session = SessionLocal()
        try:
            # Get session from database
            result = session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            session_obj = result.scalar_one_or_none()
            
            if not session_obj:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid session. Please log in again."}
                )
            
            # Check if session is valid
            if not session_obj.is_valid:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Session has been invalidated. Please log in again."}
                )
            
            # Check if session is expired
            expires_at = session_obj.expires_at
            if not isinstance(expires_at, datetime):
                expires_at = datetime.fromisoformat(str(expires_at))
            
            if datetime.utcnow() > expires_at:
                # Invalidate expired session
                session_obj.is_valid = False
                session.commit()
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Session expired. Please log in again."}
                )
            
            # Get user
            result = session.execute(
                select(User).where(User.id == session_obj.user_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "User not found. Please log in again."}
                )
            
            # Check if user is active
            if not user.is_active:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "Your account is pending admin approval. Please contact the administrator.",
                        "user": {
                            "email": user.email,
                            "is_active": False,
                        }
                    }
                )
            
            # Attach user to request state
            request.state.user = user
            
            # Continue with request
            response = await call_next(request)
            return response
            
        except Exception as e:
            logger.error("Authentication error", error=str(e), path=request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": f"Authentication error: {str(e)}"}
            )
        finally:
            session.close()
