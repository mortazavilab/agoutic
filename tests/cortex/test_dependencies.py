"""
Tests for cortex/dependencies.py — Auth & RBAC dependency functions.

Uses in-memory SQLite and mock Request objects to test:
  - get_current_user
  - require_admin
  - require_project_access
"""

import uuid
import datetime

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from cortex.models import Base, User, Project, ProjectAccess
from cortex.dependencies import get_current_user, require_admin, require_project_access


@pytest.fixture()
def dep_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def dep_session(dep_engine):
    Session = sessionmaker(bind=dep_engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def user_in_db(dep_session):
    """Create and persist a regular user."""
    user = User(
        id="user-1", email="user@test.com", role="user",
        username="testuser", is_active=True,
    )
    dep_session.add(user)
    dep_session.commit()
    return user


@pytest.fixture()
def admin_in_db(dep_session):
    """Create and persist an admin user."""
    user = User(
        id="admin-1", email="admin@test.com", role="admin",
        username="admin", is_active=True,
    )
    dep_session.add(user)
    dep_session.commit()
    return user


@pytest.fixture()
def project_in_db(dep_session, user_in_db):
    """Create a project owned by user_in_db."""
    proj = Project(
        id="proj-1", name="Test Project", slug="test-project",
        owner_id=user_in_db.id,
    )
    dep_session.add(proj)
    dep_session.commit()
    return proj


@pytest.fixture()
def access_in_db(dep_session, user_in_db, project_in_db):
    """Create an owner access row."""
    access = ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=user_in_db.id,
        project_id=project_in_db.id,
        project_name=project_in_db.name,
        role="owner",
    )
    dep_session.add(access)
    dep_session.commit()
    return access


def _make_request(user):
    """Create a mock Request with state.user set."""
    req = MagicMock()
    req.state.user = user
    return req


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------
class TestGetCurrentUser:
    def test_returns_user_when_present(self, user_in_db):
        req = _make_request(user_in_db)
        assert get_current_user(req) is user_in_db

    def test_raises_401_when_no_user(self):
        req = MagicMock()
        req.state.user = None
        with pytest.raises(HTTPException) as exc:
            get_current_user(req)
        assert exc.value.status_code == 401

    def test_raises_401_when_no_state(self):
        req = MagicMock(spec=[])  # No .state attribute
        with pytest.raises((HTTPException, AttributeError)):
            get_current_user(req)


# ---------------------------------------------------------------------------
# require_admin
# ---------------------------------------------------------------------------
class TestRequireAdmin:
    def test_admin_passes(self, admin_in_db):
        req = _make_request(admin_in_db)
        assert require_admin(req) is admin_in_db

    def test_regular_user_denied(self, user_in_db):
        req = _make_request(user_in_db)
        with pytest.raises(HTTPException) as exc:
            require_admin(req)
        assert exc.value.status_code == 403

    def test_no_user_denied(self):
        req = MagicMock()
        req.state.user = None
        with pytest.raises(HTTPException):
            require_admin(req)


# ---------------------------------------------------------------------------
# require_project_access
# ---------------------------------------------------------------------------
class TestRequireProjectAccess:
    def test_admin_always_passes(self, admin_in_db, project_in_db, dep_session):
        """Admins bypass all project-level checks."""
        with patch("cortex.dependencies.SessionLocal", return_value=dep_session):
            result = require_project_access(project_in_db.id, admin_in_db, min_role="owner")
        assert result.role == "owner"

    def test_owner_has_access(self, user_in_db, project_in_db, access_in_db, dep_session):
        with patch("cortex.dependencies.SessionLocal", return_value=dep_session):
            result = require_project_access(project_in_db.id, user_in_db, min_role="viewer")
        assert result.role == "owner"

    def test_insufficient_role_denied(self, dep_session, project_in_db):
        """A viewer trying to get editor access should be denied."""
        viewer = User(
            id="viewer-1", email="viewer@test.com", role="user",
            username="viewer", is_active=True,
        )
        dep_session.add(viewer)
        viewer_access = ProjectAccess(
            id=str(uuid.uuid4()),
            user_id=viewer.id,
            project_id=project_in_db.id,
            project_name=project_in_db.name,
            role="viewer",
        )
        dep_session.add(viewer_access)
        dep_session.commit()

        with patch("cortex.dependencies.SessionLocal", return_value=dep_session):
            with pytest.raises(HTTPException) as exc:
                require_project_access(project_in_db.id, viewer, min_role="editor")
            assert exc.value.status_code == 403

    def test_no_access_row_denied(self, dep_session, project_in_db):
        """A user with no access row and who doesn't own the project is denied."""
        stranger = User(
            id="stranger-1", email="stranger@test.com", role="user",
            username="stranger", is_active=True,
        )
        dep_session.add(stranger)
        dep_session.commit()
        with patch("cortex.dependencies.SessionLocal", return_value=dep_session):
            with pytest.raises(HTTPException) as exc:
                require_project_access(project_in_db.id, stranger, min_role="viewer")
            assert exc.value.status_code == 403

    def test_owner_auto_heals_missing_access(self, user_in_db, project_in_db, dep_session):
        """If owner's access row is missing, it should be auto-created."""
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy import select
        engine = dep_session.get_bind()
        Session = sessionmaker(bind=engine, expire_on_commit=False)

        with patch("cortex.dependencies.SessionLocal", Session):
            result = require_project_access(project_in_db.id, user_in_db, min_role="owner")
        assert result.role == "owner"

        # Verify the row was created
        verify_session = Session()
        rows = verify_session.execute(
            select(ProjectAccess).where(ProjectAccess.user_id == user_in_db.id)
        ).scalars().all()
        verify_session.close()
        assert len(rows) == 1

    def test_public_project_viewer_access(self, dep_session):
        """Public project allows viewer access to anyone."""
        owner = User(id="owner-pub", email="owner@test.com", role="user",
                     username="owner", is_active=True)
        public_proj = Project(
            id="pub-1", name="Public", owner_id=owner.id, is_public=True,
        )
        stranger = User(id="stranger-pub", email="s@test.com", role="user",
                       username="stranger2", is_active=True)
        dep_session.add_all([owner, public_proj, stranger])
        dep_session.commit()

        with patch("cortex.dependencies.SessionLocal", return_value=dep_session):
            result = require_project_access("pub-1", stranger, min_role="viewer")
        assert result.role == "viewer"
