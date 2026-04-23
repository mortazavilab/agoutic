"""
Remote path validation over SSH.
"""
from __future__ import annotations

from dataclasses import dataclass

from common.logging_config import get_logger
from launchpad.backends.ssh_manager import SSHConnection

logger = get_logger(__name__)


@dataclass
class PathValidationResult:
    """Result of validating a single remote path."""
    path: str
    exists: bool = False
    writable: bool = False
    created: bool = False
    error: str | None = None


async def validate_remote_paths(
    conn: SSHConnection,
    paths: dict[str, str],
    create_if_missing: bool = True,
) -> dict[str, PathValidationResult]:
    """Validate a set of named remote paths.

    Args:
        conn: Active SSH connection
        paths: Dict of {name: path} to validate, e.g. {"work": "/scratch/user/work"}
        create_if_missing: If True, attempt to create missing directories

    Returns:
        Dict of {name: PathValidationResult}
    """
    results: dict[str, PathValidationResult] = {}

    for name, path in paths.items():
        if not path:
            results[name] = PathValidationResult(path="", error="Path is empty")
            continue

        result = PathValidationResult(path=path)
        try:
            result.exists = await conn.path_exists(path)

            if not result.exists and create_if_missing:
                try:
                    await conn.mkdir_p(path)
                    result.exists = True
                    result.created = True
                    logger.info(f"Created remote directory: {path}")
                except Exception as e:
                    result.error = f"Failed to create directory: {e}"

            if result.exists:
                result.writable = await conn.path_is_writable(path)
                if not result.writable:
                    result.error = "Path exists but is not writable"
        except Exception as e:
            result.error = f"Validation error: {e}"

        results[name] = result

    return results


def check_all_paths_ok(results: dict[str, PathValidationResult]) -> tuple[bool, list[str]]:
    """Check if all path validations passed. Returns (ok, list_of_error_messages)."""
    errors: list[str] = []
    for name, r in results.items():
        if r.error:
            errors.append(f"{name} ({r.path}): {r.error}")
        elif not r.exists:
            errors.append(f"{name} ({r.path}): does not exist")
        elif not r.writable:
            errors.append(f"{name} ({r.path}): not writable")
    return len(errors) == 0, errors
