"""
Active-state index for UI performance optimization.

Tracks which runs/tasks are actively being polled versus completed/failed/synced,
and provides rerun-aware identity tracking so the UI follows the newest active
attempt rather than stale history.

Key invariants:
- Active jobs/downloads update automatically at the selected poll interval.
- Completed, failed, cancelled, stale, and outputs-synced tasks render from
  cached block data until a new run, rerun, resume, or sync action makes them
  active again.
- If the same task or workflow is rerun multiple times, the UI follows the
  newest active run or transfer state rather than the previous attempt.
"""

import time
from typing import Optional

import streamlit as st
import pandas as pd

# States that indicate a job/transfer is still in progress and should be polled.
ACTIVE_JOB_STATUSES = {"RUNNING", "PENDING"}
ACTIVE_TRANSFER_STATES = {"pending_import", "downloading_outputs"}

# States that indicate a job/transfer has reached a terminal state and should
# NOT be polled unless reactivated (e.g., via rerun or resume).
TERMINAL_JOB_STATUSES = {"COMPLETED", "DONE", "FAILED", "CANCELLED", "DELETED", "STALE"}
TERMINAL_TRANSFER_STATES = {
    "outputs_downloaded",
    "transfer_failed",
    "sync_cancelled",
    "stale",
}

# Session state keys used by the active-state index.
_ACTIVE_STATE_INDEX_KEY = "_active_state_index"
_LATEST_SEQ_KEY = "_latest_seq"

# Session state keys for dataframe indexing
_DF_INDEX_KEY = "_df_index"


def _get_active_state_index(project_id: str) -> dict:
    """Get or initialize the active-state index for a project."""
    index_key = f"{_ACTIVE_STATE_INDEX_KEY}_{project_id}"
    if index_key not in st.session_state:
        st.session_state[index_key] = {
            "runs": {},       # run_uuid -> {"status": str, "type": str, "updated_at": float}
            "transfers": {},  # run_uuid -> {"transfer_state": str, "updated_at": float}
            "downloads": {},  # block_id -> {"status": str, "updated_at": float}
        }
    return st.session_state[index_key]


def _get_latest_seq(project_id: str) -> int:
    """Get the latest block sequence number for incremental streaming."""
    seq_key = f"{_LATEST_SEQ_KEY}_{project_id}"
    return int(st.session_state.get(seq_key, 0))


def _set_latest_seq(project_id: str, seq: int) -> None:
    """Store the latest block sequence number for incremental streaming."""
    seq_key = f"{_LATEST_SEQ_KEY}_{project_id}"
    st.session_state[seq_key] = seq


def is_job_active(job_status: dict | None, block_status: str = "") -> bool:
    """Check if a job is currently active and should be polled.

    Args:
        job_status: The live or persisted job status dict.
        block_status: The block-level status string (e.g., "RUNNING", "DONE").

    Returns:
        True if the job should continue to be polled for updates.
    """
    if not job_status:
        return block_status in ACTIVE_JOB_STATUSES

    status = str(job_status.get("status") or "").upper()
    transfer_state = str(job_status.get("transfer_state") or "").strip().lower()
    imported_source_kind = str(job_status.get("imported_source_kind") or "").strip().lower()

    # Check job status
    if status in ACTIVE_JOB_STATUSES:
        return True
    if status in TERMINAL_JOB_STATUSES:
        # For SLURM imports, check if transfer is still pending
        if imported_source_kind == "slurm" and transfer_state not in TERMINAL_TRANSFER_STATES:
            return True
        return False

    # Check transfer state
    if transfer_state in ACTIVE_TRANSFER_STATES:
        return True
    if transfer_state in TERMINAL_TRANSFER_STATES:
        return False

    # Fall back to block status
    return block_status in ACTIVE_JOB_STATUSES


def is_download_active(content: dict) -> bool:
    """Check if a download task is currently active and should be polled.

    Args:
        content: The block payload dict.

    Returns:
        True if the download should continue to be polled for updates.
    """
    dl_status = str(content.get("status") or "").upper()
    return dl_status == "RUNNING"


def is_staging_active(content: dict, block_status: str = "") -> bool:
    """Check if a staging task is currently active and should be polled.

    Args:
        content: The block payload dict.
        block_status: The block-level status string.

    Returns:
        True if the staging task should continue to be polled for updates.
    """
    return block_status == "RUNNING"


def register_active_run(project_id: str, run_uuid: str, run_type: str = "job") -> None:
    """Register a run as active in the index.

    Args:
        project_id: The project ID.
        run_uuid: The run UUID.
        run_type: Type of run ("job", "transfer", etc.).
    """
    if not run_uuid:
        return
    index = _get_active_state_index(project_id)
    now = time.time()

    if run_type == "job":
        index["runs"][run_uuid] = {
            "status": "RUNNING",
            "type": run_type,
            "updated_at": now,
        }
    elif run_type == "transfer":
        index["transfers"][run_uuid] = {
            "transfer_state": "pending_import",
            "updated_at": now,
        }


def register_active_download(project_id: str, block_id: str) -> None:
    """Register a download task as active in the index.

    Args:
        project_id: The project ID.
        block_id: The block ID for the download task.
    """
    if not block_id:
        return
    index = _get_active_state_index(project_id)
    now = time.time()
    index["downloads"][block_id] = {
        "status": "RUNNING",
        "updated_at": now,
    }


def update_run_status(project_id: str, run_uuid: str, job_status: dict | None) -> bool:
    """Update the status of a run in the index and return whether it's still active.

    This is the key function for rerun-aware tracking: if a run transitions to
    a terminal state, it's removed from active polling. If a new run with the
    same workflow but different UUID appears, it gets registered as active.

    Args:
        project_id: The project ID.
        run_uuid: The run UUID.
        job_status: The current job status dict (may be None).

    Returns:
        True if the run is still active and should continue to be polled.
    """
    if not run_uuid:
        return False

    index = _get_active_state_index(project_id)
    now = time.time()
    is_active = is_job_active(job_status)

    if is_active:
        # Update or register as active
        status = str((job_status or {}).get("status") or "RUNNING").upper()
        transfer_state = str((job_status or {}).get("transfer_state") or "").strip().lower()

        index["runs"][run_uuid] = {
            "status": status,
            "type": "job",
            "updated_at": now,
        }

        if transfer_state:
            index["transfers"][run_uuid] = {
                "transfer_state": transfer_state,
                "updated_at": now,
            }
    else:
        # Remove from active tracking - it's terminal
        index["runs"].pop(run_uuid, None)
        index["transfers"].pop(run_uuid, None)

    return is_active


def update_download_status(project_id: str, block_id: str, content: dict) -> bool:
    """Update the status of a download task in the index.

    Args:
        project_id: The project ID.
        block_id: The block ID for the download task.
        content: The block payload dict.

    Returns:
        True if the download is still active and should continue to be polled.
    """
    if not block_id:
        return False

    index = _get_active_state_index(project_id)
    now = time.time()
    is_active = is_download_active(content)

    if is_active:
        index["downloads"][block_id] = {
            "status": "RUNNING",
            "updated_at": now,
        }
    else:
        # Remove from active tracking - it's terminal
        index["downloads"].pop(block_id, None)

    return is_active


def get_active_runs_to_poll(project_id: str) -> list[str]:
    """Get the list of run UUIDs that should be polled.

    Args:
        project_id: The project ID.

    Returns:
        List of run UUIDs that are currently active and need polling.
    """
    index = _get_active_state_index(project_id)
    return list(index["runs"].keys())


def get_active_downloads_to_poll(project_id: str) -> list[str]:
    """Get the list of download block IDs that should be polled.

    Args:
        project_id: The project ID.

    Returns:
        List of block IDs for downloads that are currently active and need polling.
    """
    index = _get_active_state_index(project_id)
    return list(index["downloads"].keys())


def has_any_active_items(project_id: str) -> bool:
    """Check if there are any active items in the project.

    Args:
        project_id: The project ID.

    Returns:
        True if there are any active runs, transfers, or downloads.
    """
    index = _get_active_state_index(project_id)
    return bool(index["runs"] or index["transfers"] or index["downloads"])


def clear_active_state(project_id: str) -> None:
    """Clear the active-state index for a project (e.g., on project switch).

    Args:
        project_id: The project ID.
    """
    index_key = f"{_ACTIVE_STATE_INDEX_KEY}_{project_id}"
    st.session_state.pop(index_key, None)
    seq_key = f"{_LATEST_SEQ_KEY}_{project_id}"
    st.session_state.pop(seq_key, None)


def should_block_require_poll(block: dict) -> bool:
    """Determine if a block requires live polling based on its type and status.

    This is used to decide whether to include a block in the active-state index
    or skip it entirely (cold state).

    Args:
        block: The block dict.

    Returns:
        True if the block should be tracked for live polling.
    """
    btype = block.get("type")
    bstatus = str(block.get("status") or "").upper()
    content = block.get("payload", {}) if isinstance(block.get("payload"), dict) else {}

    if btype == "EXECUTION_JOB":
        job_status = content.get("job_status", {}) if isinstance(content.get("job_status"), dict) else {}
        return is_job_active(job_status, bstatus)

    if btype == "DOWNLOAD_TASK":
        return is_download_active(content)

    if btype == "STAGING_TASK":
        return is_staging_active(content, bstatus)

    return False


def merge_blocks_incremental(
    project_id: str,
    existing_blocks: list[dict],
    new_blocks: list[dict],
    max_visible: int = 30,
) -> tuple[list[dict], bool]:
    """Merge new blocks into existing blocks incrementally.

    This function handles the incremental merge logic for block streaming.
    It updates the active-state index and dataframe index as it processes new blocks.

    Args:
        project_id: The project ID.
        existing_blocks: The current list of blocks in session state.
        new_blocks: New blocks fetched from the server.
        max_visible: Maximum number of blocks to keep visible (for pagination).

    Returns:
        Tuple of (merged_blocks, has_changes).
        merged_blocks: The updated block list.
        has_changes: True if any new blocks were added or active state changed.
    """
    if not new_blocks:
        return existing_blocks, False

    # Build a map of existing blocks by ID for efficient lookup
    existing_by_id = {b.get("id"): b for b in existing_blocks}

    # Track changes
    has_changes = False
    merged_blocks = list(existing_blocks)

    for new_block in new_blocks:
        block_id = new_block.get("id")
        if not block_id:
            continue

        if block_id in existing_by_id:
            # Update existing block
            existing_by_id[block_id].update(new_block)
            has_changes = True
        else:
            # Add new block
            merged_blocks.append(new_block)
            existing_by_id[block_id] = new_block
            has_changes = True

        # Update active-state index for this block
        if should_block_require_poll(new_block):
            btype = new_block.get("type")
            content = new_block.get("payload", {}) if isinstance(new_block.get("payload"), dict) else {}

            if btype == "EXECUTION_JOB":
                run_uuid = content.get("run_uuid")
                if run_uuid:
                    job_status = content.get("job_status", {})
                    update_run_status(project_id, run_uuid, job_status)

            elif btype == "DOWNLOAD_TASK":
                register_active_download(project_id, block_id)

    # Update dataframe index with new blocks (incremental update)
    build_df_index_from_blocks(project_id, new_blocks)

    # Update latest_seq
    if new_blocks:
        latest_seq = max((b.get("seq", 0) for b in new_blocks), default=0)
        _set_latest_seq(project_id, latest_seq)

    return merged_blocks, has_changes


# Import helpers for TTL caching (used by resolve_df_by_index)
from appui_cache_ttl import get_cached_with_ttl, set_cached_with_ttl, evict_expired_caches

# Session state keys for dataframe indexing
_DF_INDEX_KEY = "_df_index"


def _get_df_index(project_id: str) -> dict:
    """Get or initialize the per-project dataframe index.

    The index maps df_id -> {fname, cols, rows, metadata, block_id}.
    """
    index_key = f"{_DF_INDEX_KEY}_{project_id}"
    if index_key not in st.session_state:
        st.session_state[index_key] = {}
    return st.session_state[index_key]


def build_df_index_from_blocks(project_id: str, blocks: list[dict]) -> dict:
    """Build a per-project dataframe index from a list of blocks.

    Scans all blocks once and builds an index mapping df_id -> metadata.
    This avoids repeated O(N) scans when resolving dataframes for plots.

    Args:
        project_id: The project ID.
        blocks: List of block dicts to scan.

    Returns:
        Dict mapping df_id -> {fname, cols, rows, metadata, block_id}.
    """
    index = _get_df_index(project_id)

    for blk in blocks:
        payload = blk.get("payload", {}) if isinstance(blk.get("payload"), dict) else {}
        dfs = payload.get("_dataframes", {})
        if not isinstance(dfs, dict):
            continue

        block_id = blk.get("id", "")
        for fname, fdata in dfs.items():
            if not isinstance(fdata, dict):
                continue
            meta = fdata.get("metadata", {}) if isinstance(fdata.get("metadata"), dict) else {}
            df_id = meta.get("df_id")
            if df_id is None:
                continue

            # Store index entry (most recent block wins for a given df_id)
            index[df_id] = {
                "fname": fname,
                "cols": fdata.get("columns"),
                "rows": fdata.get("data", []),
                "metadata": meta,
                "block_id": block_id,
                "payload_provenance": payload.get("_provenance"),
            }

    return index


def resolve_df_by_index(df_id: int, project_id: str) -> tuple:
    """Resolve a DataFrame by df_id using the per-project index.

    Uses cached index entries and rehydrates from disk only when needed.
    Rehydrated DataFrames are cached with TTL to avoid repeated file reads.

    Args:
        df_id: The dataframe ID to resolve.
        project_id: The project ID for cache scoping.

    Returns:
        Tuple of (DataFrame, label) or (None, None) if not found.
    """
    index = _get_df_index(project_id)
    entry = index.get(df_id)
    if not entry:
        return None, None

    fname = entry["fname"]
    meta = entry["metadata"]
    rows = entry["rows"]
    cols = entry["cols"]

    declared_rows = meta.get("row_count") or meta.get("total_rows") or len(rows)

    # Check if we need to rehydrate from disk (truncated data)
    needs_rehydration = meta.get("is_truncated") or (
        isinstance(declared_rows, int) and declared_rows > len(rows)
    )

    if needs_rehydration:
        # Try to rehydrate from disk using cached path resolution
        cache_key = f"_df_rehydrated_{project_id}_{df_id}"
        cached_df = get_cached_with_ttl(cache_key, ttl_seconds=300.0)  # 5 min TTL

        if cached_df is not None:
            return cached_df, fname

        # Rehydrate from disk (expensive - only do once per TTL window)
        full_path = _resolve_df_file_path(entry)
        if full_path is not None:
            sep = "\t" if full_path.suffix.lower() == ".tsv" else ","
            try:
                rehydrated = pd.read_csv(full_path, sep=sep, low_memory=False)
                if rehydrated is not None and not rehydrated.empty:
                    set_cached_with_ttl(cache_key, rehydrated, ttl_seconds=300.0)
                    return rehydrated, fname
            except Exception:
                pass

    # Fall back to in-memory data
    df = pd.DataFrame(rows, columns=cols)
    return df, fname


def _resolve_df_file_path(entry: dict) -> Optional[str]:
    """Resolve the file path for a dataframe entry.

    Args:
        entry: The index entry dict with metadata and provenance.

    Returns:
        Full file path string or None if not resolvable.
    """
    from pathlib import Path

    meta = entry["metadata"]
    fname = entry["fname"]
    label = str(meta.get("label") or "").strip()
    candidates = {Path(str(fname)).name}
    if label:
        candidates.add(Path(label).name)

    provenance = entry.get("payload_provenance") or []
    parse_entries = [
        e for e in provenance
        if isinstance(e, dict)
        and e.get("success")
        and e.get("source") == "analyzer"
        and e.get("tool") == "parse_csv_file"
    ]

    parse_source = None
    for entry_item in parse_entries:
        params = dict(entry_item.get("params") or {})
        file_path = str(params.get("file_path") or "").strip()
        if file_path and Path(file_path).name in candidates:
            parse_source = params
            break

    if parse_source is None and len(parse_entries) == 1:
        parse_source = dict(parse_entries[0].get("params") or {})

    if not parse_source:
        return None

    raw_file_path = str(parse_source.get("file_path") or label or fname).strip()
    raw_work_dir = str(parse_source.get("work_dir") or "").strip()
    candidate_path = Path(raw_file_path).expanduser()

    if candidate_path.is_absolute():
        if candidate_path.exists() and candidate_path.is_file():
            return str(candidate_path)
    elif raw_work_dir:
        work_dir = Path(raw_work_dir).expanduser()
        for path_try in ((work_dir / candidate_path), (work_dir / candidate_path.name)):
            if path_try.exists() and path_try.is_file():
                return str(path_try)

    return None


def clear_df_index(project_id: str) -> None:
    """Clear the dataframe index for a project (e.g., on project switch).

    Args:
        project_id: The project ID.
    """
    index_key = f"{_DF_INDEX_KEY}_{project_id}"
    st.session_state.pop(index_key, None)


def clear_rehydrated_df_cache(project_id: str) -> int:
    """Clear cached rehydrated DataFrames for a project.

    Args:
        project_id: The project ID.

    Returns:
        Number of entries cleared.
    """
    prefix = f"_df_rehydrated_{project_id}_"
    return evict_expired_caches(prefix=prefix, default_ttl=0)
