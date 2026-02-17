"""
User jail / file system isolation for AGOUTIC.

Provides path resolution that scopes all file operations to /data/users/{user_id}/{project_id}/.
Prevents path traversal attacks and ensures users can only access their own data.
"""

import re
from pathlib import Path
from server1.config import AGOUTIC_DATA

# Characters that are never valid in a user_id or project_id
_UNSAFE_ID_PATTERN = re.compile(r'[/\\.\x00]')


def _validate_id(value: str, label: str) -> None:
    """
    Reject IDs that could be used for path traversal.

    Valid IDs contain only alphanumerics, hyphens, and underscores.
    Rejects: '/', '\\', '..', null bytes, and empty strings.
    """
    if not value:
        raise PermissionError(f"{label} must not be empty")
    if _UNSAFE_ID_PATTERN.search(value):
        raise PermissionError(
            f"Invalid {label}: contains disallowed characters (/, \\, ., or null)"
        )


def _ensure_within_jail(target: Path) -> None:
    """
    Belt-and-suspenders guard: verify the resolved target path
    is still a child of AGOUTIC_DATA.
    """
    agoutic_root = AGOUTIC_DATA.resolve()
    resolved = target.resolve()
    # Python 3.9+: PurePath.is_relative_to()
    try:
        resolved.relative_to(agoutic_root)
    except ValueError:
        raise PermissionError(
            f"Path traversal detected: resolved path escapes AGOUTIC_DATA ({resolved})"
        )


def resolve_user_path(user_id: str, project_id: str, relative_path: str) -> Path:
    """
    Resolve a user-scoped file path.
    
    All file operations should be scoped to /data/users/{user_id}/{project_id}/.
    This function validates that the resolved path stays within the user jail.
    
    Args:
        user_id: The user's ID
        project_id: The project's ID
        relative_path: The relative path within the project (e.g., "results/output.txt")
    
    Returns:
        Path: The resolved absolute path
    
    Raises:
        PermissionError: If the path attempts to escape the user jail
    
    Example:
        >>> resolve_user_path("user123", "proj456", "results/output.txt")
        Path("/data/users/user123/proj456/results/output.txt")
        
        >>> resolve_user_path("user123", "proj456", "../../../etc/passwd")
        PermissionError: Path traversal detected
    """
    _validate_id(user_id, "user_id")
    _validate_id(project_id, "project_id")

    # Base directory for this user's project
    base = AGOUTIC_DATA / "users" / user_id / project_id
    
    # Resolve the target path
    target = (base / relative_path).resolve()
    
    # Security check: ensure target is within base
    if not str(target).startswith(str(base.resolve())):
        raise PermissionError(
            f"Path traversal detected: {relative_path} resolves outside user jail"
        )
    
    # Final guard: must also stay within AGOUTIC_DATA
    _ensure_within_jail(target)

    return target


def get_user_project_dir(user_id: str, project_id: str) -> Path:
    """
    Get the root directory for a user's project.
    Creates the directory if it doesn't exist.
    
    Args:
        user_id: The user's ID
        project_id: The project's ID
    
    Returns:
        Path: The project's root directory
    
    Raises:
        PermissionError: If user_id or project_id would escape the jail
    """
    _validate_id(user_id, "user_id")
    _validate_id(project_id, "project_id")

    project_dir = AGOUTIC_DATA / "users" / user_id / project_id
    _ensure_within_jail(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def get_user_home_dir(user_id: str) -> Path:
    """
    Get the home directory for a user (contains all their projects).
    Creates the directory if it doesn't exist.
    
    Args:
        user_id: The user's ID
    
    Returns:
        Path: The user's home directory
    
    Raises:
        PermissionError: If user_id would escape the jail
    """
    _validate_id(user_id, "user_id")

    user_dir = AGOUTIC_DATA / "users" / user_id
    _ensure_within_jail(user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir
