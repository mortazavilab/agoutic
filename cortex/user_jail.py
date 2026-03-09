"""
User jail / file system isolation for AGOUTIC.

Provides path resolution that scopes all file operations to:
  New:    AGOUTIC_DATA/users/{username}/{project_slug}/
  Legacy: AGOUTIC_DATA/users/{user_id}/{project_id}/

Prevents path traversal attacks and ensures users can only access their own data.
"""

import re
import shutil
from pathlib import Path
from cortex.config import AGOUTIC_DATA

# Characters that are never valid in a slug / user-id / project-id
# Slugs may contain lowercase alphanum, hyphens, and underscores.
_UNSAFE_ID_PATTERN = re.compile(r'[/\\\x00]')
_SLUG_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]{0,60}$')


def _validate_id(value: str, label: str) -> None:
    """
    Reject IDs that could be used for path traversal.

    Valid IDs contain alphanumerics, hyphens, underscores, and dots
    (dots are allowed in UUIDs but the traversal guard catches '..').
    """
    if not value:
        raise PermissionError(f"{label} must not be empty")
    if _UNSAFE_ID_PATTERN.search(value):
        raise PermissionError(
            f"Invalid {label}: contains disallowed characters (/, \\, or null)"
        )
    if ".." in value:
        raise PermissionError(f"Invalid {label}: contains '..'")


def _validate_slug(value: str, label: str) -> None:
    """Validate that value is a proper filesystem slug."""
    if not value:
        raise PermissionError(f"{label} must not be empty")
    if not _SLUG_PATTERN.match(value):
        raise PermissionError(
            f"Invalid {label} '{value}': must match ^[a-z0-9][a-z0-9_-]{{0,60}}$"
        )


def _ensure_within_jail(target: Path) -> None:
    """
    Belt-and-suspenders guard: verify the resolved target path
    is still a child of AGOUTIC_DATA.
    """
    agoutic_root = AGOUTIC_DATA.resolve()
    resolved = target.resolve()
    try:
        resolved.relative_to(agoutic_root)
    except ValueError:
        raise PermissionError(
            f"Path traversal detected: resolved path escapes AGOUTIC_DATA ({resolved})"
        )


# ---------------------------------------------------------------------------
# New slug-based paths  (username / project_slug)
# ---------------------------------------------------------------------------

def resolve_user_path(username: str, project_slug: str, relative_path: str) -> Path:
    """
    Resolve a user-scoped file path using human-readable names.

    Scopes to AGOUTIC_DATA/users/{username}/{project_slug}/.

    Args:
        username:      The user's unique slug (e.g. "eli-garcia")
        project_slug:  The project's slug (e.g. "encode-experiment")
        relative_path: Path within the project (e.g. "data/file.fastq")

    Returns:
        Resolved absolute path

    Raises:
        PermissionError: If any component is invalid or traversal is detected
    """
    _validate_slug(username, "username")
    _validate_slug(project_slug, "project_slug")

    base = AGOUTIC_DATA / "users" / username / project_slug
    target = (base / relative_path).resolve()

    if not str(target).startswith(str(base.resolve())):
        raise PermissionError(
            f"Path traversal detected: {relative_path} resolves outside user jail"
        )
    _ensure_within_jail(target)
    return target


def get_user_project_dir(username: str, project_slug: str) -> Path:
    """
    Get the root directory for a user's project (slug-based).
    Creates the directory if it doesn't exist.
    """
    _validate_slug(username, "username")
    _validate_slug(project_slug, "project_slug")

    project_dir = AGOUTIC_DATA / "users" / username / project_slug
    _ensure_within_jail(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def get_user_home_dir(username: str) -> Path:
    """
    Get the home directory for a user (contains all their projects).
    Creates the directory if it doesn't exist.
    """
    _validate_slug(username, "username")

    user_dir = AGOUTIC_DATA / "users" / username
    _ensure_within_jail(user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_user_data_dir(username: str) -> Path:
    """
    Get the central data directory for a user.

    All downloaded, uploaded, and locally-intaked files live here once.
    Individual projects reference them via symlinks.
    Layout: AGOUTIC_DATA/users/{username}/data/
    Creates the directory if it doesn't exist.
    """
    _validate_slug(username, "username")

    data_dir = AGOUTIC_DATA / "users" / username / "data"
    _ensure_within_jail(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def create_project_file_symlink(
    username: str,
    project_slug: str,
    filename: str,
    central_path: Path,
) -> Path:
    """
    Create a symlink inside a project's data/ directory pointing to
    a file in the user's central data folder.

    Args:
        username:      User slug
        project_slug:  Project slug
        filename:      Filename for the symlink (same basename)
        central_path:  Absolute path to the real file in central data/

    Returns:
        The absolute path of the created symlink.
    """
    _validate_slug(username, "username")
    _validate_slug(project_slug, "project_slug")

    project_data_dir = AGOUTIC_DATA / "users" / username / project_slug / "data"
    project_data_dir.mkdir(parents=True, exist_ok=True)
    _ensure_within_jail(project_data_dir)

    symlink_path = project_data_dir / filename
    # Remove existing symlink or file before creating
    if symlink_path.is_symlink() or symlink_path.exists():
        symlink_path.unlink()
    symlink_path.symlink_to(central_path.resolve())
    return symlink_path


# ---------------------------------------------------------------------------
# Legacy UUID-based paths (kept for backward compatibility)
# ---------------------------------------------------------------------------

def resolve_user_path_by_uuid(user_id: str, project_id: str, relative_path: str) -> Path:
    """Legacy: resolve path using UUIDs (for old data access)."""
    _validate_id(user_id, "user_id")
    _validate_id(project_id, "project_id")

    base = AGOUTIC_DATA / "users" / user_id / project_id
    target = (base / relative_path).resolve()

    if not str(target).startswith(str(base.resolve())):
        raise PermissionError(
            f"Path traversal detected: {relative_path} resolves outside user jail"
        )
    _ensure_within_jail(target)
    return target


def get_user_project_dir_by_uuid(user_id: str, project_id: str) -> Path:
    """Legacy: get project directory using UUIDs."""
    _validate_id(user_id, "user_id")
    _validate_id(project_id, "project_id")

    project_dir = AGOUTIC_DATA / "users" / user_id / project_id
    _ensure_within_jail(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def get_user_home_dir_by_uuid(user_id: str) -> Path:
    """Legacy: get user home directory using UUID."""
    _validate_id(user_id, "user_id")

    user_dir = AGOUTIC_DATA / "users" / user_id
    _ensure_within_jail(user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


# ---------------------------------------------------------------------------
# Rename helpers
# ---------------------------------------------------------------------------

def rename_user_dir(old_username: str, new_username: str) -> Path:
    """Rename a user's home directory on disk.

    Returns the new path.  Raises if the old dir doesn't exist or the
    new dir is already occupied.
    """
    _validate_slug(old_username, "old_username")
    _validate_slug(new_username, "new_username")

    old_dir = AGOUTIC_DATA / "users" / old_username
    new_dir = AGOUTIC_DATA / "users" / new_username

    if not old_dir.exists():
        # Nothing to move — just ensure new dir exists
        new_dir.mkdir(parents=True, exist_ok=True)
        return new_dir

    if new_dir.exists() and any(new_dir.iterdir()):
        raise PermissionError(
            f"Cannot rename: target directory {new_dir} already has content"
        )

    if new_dir.exists():
        new_dir.rmdir()
    shutil.move(str(old_dir), str(new_dir))
    return new_dir


def rename_project_dir(username: str, old_slug: str, new_slug: str) -> Path:
    """Rename a project directory on disk within a user's home.

    Returns the new path.
    """
    _validate_slug(username, "username")
    _validate_slug(old_slug, "old_slug")
    _validate_slug(new_slug, "new_slug")

    old_dir = AGOUTIC_DATA / "users" / username / old_slug
    new_dir = AGOUTIC_DATA / "users" / username / new_slug

    if not old_dir.exists():
        new_dir.mkdir(parents=True, exist_ok=True)
        (new_dir / "data").mkdir(exist_ok=True)
        return new_dir

    if new_dir.exists() and any(new_dir.iterdir()):
        raise PermissionError(
            f"Cannot rename: target directory {new_dir} already has content"
        )

    if new_dir.exists():
        new_dir.rmdir()
    shutil.move(str(old_dir), str(new_dir))
    return new_dir
