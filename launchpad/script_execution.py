"""Helpers for safe standalone Python script execution in Launchpad."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from launchpad.config import SCRIPT_ALLOWLIST_IDS, SCRIPT_ALLOWLIST_ROOTS


@dataclass(frozen=True)
class ResolvedScript:
    """Resolved script execution request after allowlist checks."""

    script_id: str | None
    script_path: Path


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_explicit_script_path(script_path: str) -> Path:
    candidate = Path(script_path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("script_path must be an explicit absolute path")

    resolved = candidate.resolve()
    if not SCRIPT_ALLOWLIST_ROOTS:
        raise ValueError("script_path is not allowed: no allowlisted roots configured")

    if not any(_is_within_root(resolved, root) for root in SCRIPT_ALLOWLIST_ROOTS):
        raise ValueError("script_path is outside allowlisted roots")

    return resolved


def resolve_allowlisted_script(*, script_id: str | None, script_path: str | None) -> ResolvedScript:
    """Resolve script execution target using explicit allowlist inputs only.

    Rules:
    - Deny-by-default: caller must provide explicit script_id or explicit script_path.
    - script_id must map to configured allowlist entry.
    - script_path must be absolute and under configured allowlisted roots.
    - If both provided, they must resolve to the same file.
    """
    requested_id = (script_id or "").strip() or None
    requested_path = (script_path or "").strip() or None

    if not requested_id and not requested_path:
        raise ValueError("Provide explicit script_id or explicit script_path")

    resolved_from_id: Path | None = None
    resolved_from_path: Path | None = None

    if requested_id:
        mapped = SCRIPT_ALLOWLIST_IDS.get(requested_id)
        if not mapped:
            raise ValueError(f"script_id '{requested_id}' is not allowlisted")
        resolved_from_id = mapped.resolve()

    if requested_path:
        resolved_from_path = _validate_explicit_script_path(requested_path)

    if resolved_from_id and resolved_from_path and resolved_from_id != resolved_from_path:
        raise ValueError("script_id and script_path resolve to different files")

    resolved = resolved_from_id or resolved_from_path
    if resolved is None:
        raise ValueError("Unable to resolve script execution path")

    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"Resolved script does not exist: {resolved}")
    if resolved.suffix.lower() != ".py":
        raise ValueError("Only Python .py scripts are supported")

    return ResolvedScript(script_id=requested_id, script_path=resolved)


def validate_script_working_directory(path: str | None) -> Path | None:
    """Validate optional explicit script working directory.

    Working directory must be explicit, absolute, exist as a directory, and remain
    inside configured allowlisted roots.
    """
    if path is None:
        return None

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("script_working_directory must be an explicit absolute path")

    resolved = candidate.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"script_working_directory does not exist or is not a directory: {resolved}")

    if not SCRIPT_ALLOWLIST_ROOTS:
        raise ValueError("script_working_directory is not allowed: no allowlisted roots configured")

    if not any(_is_within_root(resolved, root) for root in SCRIPT_ALLOWLIST_ROOTS):
        raise ValueError("script_working_directory is outside allowlisted roots")

    return resolved


def normalize_script_args(args: list[str] | None) -> list[str]:
    """Validate and normalize explicit script arguments."""
    if args is None:
        return []
    if not isinstance(args, list):
        raise ValueError("script_args must be a list of strings")

    normalized: list[str] = []
    for item in args:
        if not isinstance(item, str):
            raise ValueError("script_args must contain only strings")
        normalized.append(item)
    return normalized
