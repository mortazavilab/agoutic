"""Collaborator query and mutation helpers for project sharing."""

from __future__ import annotations

import datetime
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from cortex.models import Project, ProjectAccess, User
from cortex.schemas import ProjectCollaboratorOut


_MUTABLE_ROLES = {"viewer", "editor"}
_ROLE_SORT = {"owner": 2, "editor": 1, "viewer": 0}


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _get_project_or_404(session, project_id: str) -> Project:
    project = session.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_membership_or_none(session, project_id: str, user_id: str) -> ProjectAccess | None:
    return session.execute(
        select(ProjectAccess)
        .where(ProjectAccess.project_id == project_id)
        .where(ProjectAccess.user_id == user_id)
    ).scalar_one_or_none()


def _get_user_by_email(session, email: str) -> User | None:
    normalized = email.strip().lower()
    return session.execute(
        select(User).where(User.email.ilike(normalized))
    ).scalar_one_or_none()


def _serialize_collaborator(
    access: ProjectAccess,
    user: User,
    *,
    owner_id: str,
    invited_by_email: str | None,
) -> ProjectCollaboratorOut:
    return ProjectCollaboratorOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        username=user.username,
        role=access.role,
        invited_by=access.invited_by,
        invited_by_email=invited_by_email,
        created_at=access.created_at.isoformat() if hasattr(access.created_at, "isoformat") else str(access.created_at),
        updated_at=access.updated_at.isoformat() if hasattr(access.updated_at, "isoformat") else str(access.updated_at),
        last_accessed=access.last_accessed.isoformat() if hasattr(access.last_accessed, "isoformat") else str(access.last_accessed),
        is_owner=user.id == owner_id,
    )


def list_project_collaborators(session, project_id: str, actor: User) -> tuple[Project, list[ProjectCollaboratorOut]]:
    project = _get_project_or_404(session, project_id)
    accesses = session.execute(
        select(ProjectAccess, User)
        .join(User, User.id == ProjectAccess.user_id)
        .where(ProjectAccess.project_id == project_id)
    ).all()

    inviter_ids = {access.invited_by for access, _ in accesses if access.invited_by}
    inviters = {}
    if inviter_ids:
        inviters = {
            user.id: user.email
            for user in session.execute(
                select(User).where(User.id.in_(inviter_ids))
            ).scalars().all()
        }

    collaborators = [
        _serialize_collaborator(
            access,
            user,
            owner_id=project.owner_id,
            invited_by_email=inviters.get(access.invited_by),
        )
        for access, user in accesses
    ]
    collaborators.sort(
        key=lambda item: (
            -_ROLE_SORT.get(item.role, -1),
            str(item.email).lower(),
            item.user_id,
        )
    )

    return project, collaborators


def create_project_collaborator(session, project_id: str, actor: User, email: str, role: str) -> tuple[Project, User, ProjectAccess]:
    if role not in _MUTABLE_ROLES:
        raise HTTPException(status_code=422, detail="Role must be 'viewer' or 'editor'.")

    project = _get_project_or_404(session, project_id)
    user = _get_user_by_email(session, email)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="No active approved AGOUTIC user matches that email. Ask them to sign up first.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=409,
            detail="That AGOUTIC account exists but is not approved yet. Ask them to sign in and wait for approval first.",
        )

    existing = _get_membership_or_none(session, project_id, user.id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="That user already has project access. Use role update instead.",
        )

    now = _utcnow()
    access = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=user.id,
        project_id=project_id,
        project_name=project.name,
        role=role,
        invited_by=actor.id,
        created_at=now,
        updated_at=now,
        last_accessed=now,
    )
    session.add(access)
    session.commit()
    session.refresh(access)
    return project, user, access


def update_project_collaborator_role(session, project_id: str, user_id: str, role: str) -> tuple[Project, User, ProjectAccess]:
    if role not in _MUTABLE_ROLES:
        raise HTTPException(status_code=422, detail="Role must be 'viewer' or 'editor'.")

    project = _get_project_or_404(session, project_id)
    access = _get_membership_or_none(session, project_id, user_id)
    if not access:
        raise HTTPException(status_code=404, detail="Collaborator not found")
    if access.role == "owner" or user_id == project.owner_id:
        raise HTTPException(status_code=409, detail="Owner role cannot be changed here.")

    user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    access.role = role
    access.updated_at = _utcnow()
    access.project_name = project.name
    session.commit()
    session.refresh(access)
    return project, user, access


def remove_project_collaborator(session, project_id: str, user_id: str) -> tuple[Project, User, str]:
    project = _get_project_or_404(session, project_id)
    access = _get_membership_or_none(session, project_id, user_id)
    if not access:
        raise HTTPException(status_code=404, detail="Collaborator not found")
    if access.role == "owner" or user_id == project.owner_id:
        raise HTTPException(status_code=409, detail="Owner removal is not supported.")

    user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    removed_role = access.role
    session.delete(access)
    session.commit()
    return project, user, removed_role


def leave_project(session, project_id: str, actor: User) -> tuple[Project, ProjectAccess]:
    project = _get_project_or_404(session, project_id)
    if actor.id == project.owner_id:
        raise HTTPException(status_code=409, detail="The project owner cannot leave without ownership transfer.")

    access = _get_membership_or_none(session, project_id, actor.id)
    if not access:
        raise HTTPException(status_code=404, detail="You do not have an explicit collaborator membership on this project.")
    if access.role == "owner":
        raise HTTPException(status_code=409, detail="Owner membership cannot be removed via leave.")

    session.delete(access)
    session.commit()
    return project, access