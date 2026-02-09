"""
FastAPI dependencies for authentication and authorization.
"""

from fastapi import Request, HTTPException
from server1.models import User


def get_current_user(request: Request) -> User:
    """
    Get the current authenticated user from request.state.
    
    This should be used as a FastAPI dependency in endpoints that require authentication.
    The user is attached to request.state by the AuthMiddleware.
    """
    user = getattr(request.state, "user", None)
    
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please log in."
        )
    
    return user


def require_admin(request: Request) -> User:
    """
    Require the current user to be an admin.
    
    This should be used as a FastAPI dependency in admin-only endpoints.
    """
    user = get_current_user(request)
    
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required."
        )
    
    return user
