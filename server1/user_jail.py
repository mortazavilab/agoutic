"""
User jail / file system isolation for AGOUTIC.

Provides path resolution that scopes all file operations to /data/users/{user_id}/{project_id}/.
Prevents path traversal attacks and ensures users can only access their own data.
"""

from pathlib import Path
from server1.config import AGOUTIC_DATA


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
    # Base directory for this user's project
    base = AGOUTIC_DATA / "users" / user_id / project_id
    
    # Resolve the target path
    target = (base / relative_path).resolve()
    
    # Security check: ensure target is within base
    if not str(target).startswith(str(base.resolve())):
        raise PermissionError(
            f"Path traversal detected: {relative_path} resolves outside user jail"
        )
    
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
    """
    project_dir = AGOUTIC_DATA / "users" / user_id / project_id
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
    """
    user_dir = AGOUTIC_DATA / "users" / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir
