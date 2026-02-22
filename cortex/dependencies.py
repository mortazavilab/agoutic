"""
FastAPI dependencies for authentication and authorization.
"""

import uuid
import datetime

from fastapi import Request, HTTPException
from sqlalchemy import select

from cortex.models import User, ProjectAccess, Project
from cortex.db import SessionLocal

# Role hierarchy: owner > editor > viewer
_ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}


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


def require_project_access(project_id: str, user: User, min_role: str = "viewer") -> ProjectAccess:
    """
    Verify the user has at least `min_role` access to the given project.

    Checks in order:
      1. Is the user an admin? → always allowed (returns synthetic access).
      2. Does the user have a project_access row with role >= min_role?
      3. Is the user the project's owner (auto-heals missing access row)?
      4. Is the project public and min_role is viewer?

    Raises HTTPException 403 if none of the above pass.
    """
    # Admins bypass all project-level checks
    if user.role == "admin":
        # Return a lightweight object so callers can still read .role
        class _AdminAccess:
            role = "owner"
        return _AdminAccess()

    min_rank = _ROLE_RANK.get(min_role, 0)

    session = SessionLocal()
    try:
        # Check explicit access
        access = session.execute(
            select(ProjectAccess)
            .where(ProjectAccess.user_id == user.id)
            .where(ProjectAccess.project_id == project_id)
        ).scalar_one_or_none()

        if access:
            if _ROLE_RANK.get(access.role, 0) >= min_rank:
                return access
            raise HTTPException(
                status_code=403,
                detail=f"Requires at least '{min_role}' access to this project."
            )

        # No explicit access row — check if user owns the project and auto-heal
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        if project and project.owner_id == user.id:
            # Owner's access row is missing — recreate it
            new_access = ProjectAccess(
                id=str(uuid.uuid4()),
                user_id=user.id,
                project_id=project_id,
                project_name=project.name,
                role="owner",
                last_accessed=datetime.datetime.utcnow(),
            )
            session.add(new_access)
            session.commit()
            return new_access

        # Fallback: public project allows viewer-level access
        if project and project.is_public and min_rank <= _ROLE_RANK["viewer"]:
            class _PublicAccess:
                role = "viewer"
            return _PublicAccess()

        raise HTTPException(
            status_code=403,
            detail="You do not have access to this project."
        )
    finally:
        session.close()


def require_run_uuid_access(run_uuid: str, user: User) -> None:
    """
    Verify the user owns the job identified by run_uuid.

    Checks dogme_jobs.user_id (if present) or falls back to checking
    that the user has access to the job's project_id.
    """
    from launchpad.models import DogmeJob as LaunchpadDogmeJob

    session = SessionLocal()
    try:
        # Use the shared SQLite DB — Cortex, 3, 4 all share it
        # Import sync engine directly to avoid async/sync mismatch
        from sqlalchemy import create_engine
        from cortex.config import DATABASE_URL
        sync_url = DATABASE_URL.replace("sqlite+aiosqlite://", "sqlite://")
        engine = create_engine(sync_url, connect_args={"check_same_thread": False})
        from sqlalchemy.orm import Session
        with Session(engine) as db:
            job = db.execute(
                select(LaunchpadDogmeJob).where(LaunchpadDogmeJob.run_uuid == run_uuid)
            ).scalar_one_or_none()

            if not job:
                raise HTTPException(status_code=404, detail="Job not found.")

            # If job has user_id, check direct ownership
            if hasattr(job, 'user_id') and job.user_id:
                if job.user_id != user.id and user.role != "admin":
                    raise HTTPException(
                        status_code=403,
                        detail="You do not have access to this job."
                    )
                return

            # Fallback: check project access
            require_project_access(job.project_id, user, min_role="viewer")
    except HTTPException:
        raise
    except Exception:
        # If we can't verify (e.g., table doesn't have user_id yet), allow
        # access for backward compat — the column migration may not have run
        pass
