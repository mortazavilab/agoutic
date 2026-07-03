"""
TTL-based cache management for UI Session State.

Provides helpers to manage cached data with time-to-live (TTL) expiration,
ensuring that large binary payloads (download files, export artifacts) are
evicted from Session State after a configurable timeout, while figures and
plot-related state remain eligible to stay in Session State.

Key invariants:
- Figures and plot-related state are allowed to remain in Session State.
- Large downloadable files and other non-figure binary payloads should not
  live in long-running Session State unless explicitly prepared on demand.
"""

import time
from typing import Any, Optional

import streamlit as st

# Default TTL values (in seconds)
DEFAULT_DOWNLOAD_CACHE_TTL = 300  # 5 minutes for download metadata
DEFAULT_EXPORT_PAYLOAD_TTL = 600  # 10 minutes for prepared export payloads
DEFAULT_JOB_STATUS_CACHE_TTL = 2.0  # Keep existing short-lived job status cache

# Cache key prefixes
DOWNLOAD_CACHE_PREFIX = "_project_download_cache_"
JOB_STATUS_CACHE_PREFIX = "_job_status_cache_"
EXPORT_PAYLOAD_PREFIX = "_publication_download_payload"


def get_cached_with_ttl(
    cache_key: str,
    ttl_seconds: float | None = None,
) -> Optional[Any]:
    """Get a cached value from Session State if it's still valid.

    Args:
        cache_key: The Session State key to look up.
        ttl_seconds: Time-to-live in seconds. If None, returns the value without TTL check.

    Returns:
        The cached value if it exists and is not expired, or None.
    """
    cached = st.session_state.get(cache_key)
    if cached is None:
        return None

    if ttl_seconds is None:
        return cached

    # Check if the cache entry has TTL metadata
    if isinstance(cached, dict) and "expires_at" in cached:
        expires_at = float(cached.get("expires_at", 0))
        if time.time() > expires_at:
            # Expired - remove it
            st.session_state.pop(cache_key, None)
            return None
        return cached.get("data")

    # No TTL metadata - return as-is (backward compatible)
    return cached


def set_cached_with_ttl(
    cache_key: str,
    value: Any,
    ttl_seconds: float,
) -> None:
    """Store a value in Session State with TTL expiration.

    Args:
        cache_key: The Session State key to use.
        value: The value to cache.
        ttl_seconds: Time-to-live in seconds.
    """
    st.session_state[cache_key] = {
        "data": value,
        "expires_at": time.time() + ttl_seconds,
        "cached_at": time.time(),
    }


def evict_expired_caches(
    prefix: str | None = None,
    default_ttl: float | None = None,
) -> int:
    """Evict all expired caches from Session State.

    Args:
        prefix: Optional key prefix to filter which caches to check.
        default_ttl: Default TTL to apply if a cache entry has no TTL metadata.

    Returns:
        Number of entries evicted.
    """
    now = time.time()
    evicted = 0

    keys_to_check = []
    for key in list(st.session_state.keys()):
        if prefix and not key.startswith(prefix):
            continue
        keys_to_check.append(key)

    for key in keys_to_check:
        cached = st.session_state.get(key)
        if not isinstance(cached, dict):
            continue

        expires_at = cached.get("expires_at")
        if expires_at is not None and now > float(expires_at):
            st.session_state.pop(key, None)
            evicted += 1
        elif default_ttl is not None:
            # Apply default TTL to entries without explicit expiration
            cached_at = cached.get("cached_at", 0)
            if now - float(cached_at) > default_ttl:
                st.session_state.pop(key, None)
                evicted += 1

    return evicted


def get_download_cache_metadata(
    project_id: str,
    path_value: str,
) -> Optional[dict]:
    """Get cached download metadata for a project file.

    This function returns only the metadata (file_name, mime, etc.) without
    loading the full binary payload into memory. The actual file bytes should
    be fetched on-demand when needed.

    Args:
        project_id: The project ID.
        path_value: The file path within the project.

    Returns:
        Dict with metadata keys (file_name, mime, error) or None if not cached.
    """
    cache_key = f"{DOWNLOAD_CACHE_PREFIX}{project_id}_{path_value}"
    cached = get_cached_with_ttl(cache_key, ttl_seconds=DEFAULT_DOWNLOAD_CACHE_TTL)

    if cached is None:
        return None

    if isinstance(cached, dict):
        # Return metadata only, not the full binary payload
        return {
            "file_name": cached.get("file_name"),
            "mime": cached.get("mime"),
            "error": cached.get("error"),
            "has_data": "data" in cached,
        }

    return None


def clear_download_cache_for_project(project_id: str) -> int:
    """Clear all download caches for a specific project.

    Args:
        project_id: The project ID.

    Returns:
        Number of entries cleared.
    """
    prefix = f"{DOWNLOAD_CACHE_PREFIX}{project_id}_"
    return evict_expired_caches(prefix=prefix, default_ttl=0)


def clear_export_payloads_for_project(project_id: str) -> int:
    """Clear all export payload caches for a specific project.

    Note: This clears prepared export bytes (PNG/SVG/PDF) but does NOT clear
    figure objects or plot-related state, which are allowed to remain in
    Session State per the figure exception policy.

    Args:
        project_id: The project ID.

    Returns:
        Number of entries cleared.
    """
    # Export payloads are keyed by plot_key_base, not project_id directly
    # We need to find and clear keys that match the export payload pattern
    evicted = 0
    for key in list(st.session_state.keys()):
        if EXPORT_PAYLOAD_PREFIX in key:
            cached = st.session_state.get(key)
            if isinstance(cached, dict) and "data" in cached:
                # This is a binary payload - clear it
                st.session_state.pop(key, None)
                evicted += 1

    return evicted


def get_session_state_footprint() -> dict:
    """Get an approximate footprint of Session State caches.

    Returns:
        Dict with cache statistics.
    """
    download_count = 0
    job_status_count = 0
    export_payload_count = 0
    other_count = 0

    for key in st.session_state.keys():
        if key.startswith(DOWNLOAD_CACHE_PREFIX):
            download_count += 1
        elif key.startswith(JOB_STATUS_CACHE_PREFIX):
            job_status_count += 1
        elif EXPORT_PAYLOAD_PREFIX in key:
            export_payload_count += 1
        else:
            other_count += 1

    return {
        "download_caches": download_count,
        "job_status_caches": job_status_count,
        "export_payloads": export_payload_count,
        "other_keys": other_count,
        "total_keys": len(st.session_state.keys()),
    }


def periodic_cache_maintenance() -> None:
    """Run periodic cache maintenance.

    This function should be called periodically (e.g., on each fragment refresh)
    to evict expired caches and keep Session State size bounded.
    """
    # Evict expired download caches
    evict_expired_caches(
        prefix=DOWNLOAD_CACHE_PREFIX,
        default_ttl=DEFAULT_DOWNLOAD_CACHE_TTL,
    )

    # Evict expired job status caches (these have short TTL already)
    evict_expired_caches(
        prefix=JOB_STATUS_CACHE_PREFIX,
        default_ttl=DEFAULT_JOB_STATUS_CACHE_TTL,
    )


# Initialize cache maintenance on import
def init_cache_maintenance() -> None:
    """Initialize periodic cache maintenance.

    Call this once during app startup to ensure caches are cleaned up regularly.
    """
    # Run initial cleanup
    periodic_cache_maintenance()
