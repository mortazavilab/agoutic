from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from common.logging_config import get_logger
from cortex.llm_validators import get_block_payload

logger = get_logger(__name__)


def is_truncated_dataframe_payload(payload: dict | None) -> bool:
    if not payload:
        return False
    metadata = payload.get("metadata") or {}
    if metadata.get("is_truncated"):
        return True
    stored_rows = len(payload.get("data") or [])
    declared_rows = payload.get("row_count") or metadata.get("row_count") or metadata.get("total_rows")
    return isinstance(declared_rows, int) and declared_rows > stored_rows


def find_dataframe_payload(
    df_id: int,
    history_blocks: list,
    *,
    current_dataframes: dict[str, dict] | None = None,
) -> dict[str, Any] | None:
    hidden_match: dict[str, Any] | None = None

    for payload in (current_dataframes or {}).values():
        metadata = payload.get("metadata") or {}
        if metadata.get("df_id") == df_id:
            if metadata.get("visible", True):
                return payload
            if hidden_match is None:
                hidden_match = payload

    for block in reversed(history_blocks or []):
        payload = get_block_payload(block)
        for df_payload in (payload.get("_dataframes") or {}).values():
            metadata = df_payload.get("metadata") or {}
            if metadata.get("df_id") == df_id:
                if metadata.get("visible", True):
                    return df_payload
                if hidden_match is None:
                    hidden_match = df_payload
    return hidden_match


def find_dataframe_parse_source(df_id: int, history_blocks: list) -> dict | None:
    hidden_fallback: dict | None = None

    for block in reversed(history_blocks or []):
        payload = get_block_payload(block)
        dataframes = payload.get("_dataframes") or {}
        matched_key = None
        matched_label = None
        matched_visible = False
        for key, df_payload in dataframes.items():
            metadata = df_payload.get("metadata") or {}
            if metadata.get("df_id") != df_id:
                continue
            if metadata.get("visible", True):
                matched_key = key
                matched_label = str(metadata.get("label") or key or "").strip()
                matched_visible = True
                break
            if matched_key is None:
                matched_key = key
                matched_label = str(metadata.get("label") or key or "").strip()
        if matched_key is None:
            continue

        candidates = {Path(str(matched_key)).name}
        if matched_label:
            candidates.add(Path(matched_label).name)

        parse_entries = [
            entry for entry in (payload.get("_provenance") or [])
            if entry.get("success")
            and entry.get("source") == "analyzer"
            and entry.get("tool") == "parse_csv_file"
        ]
        for entry in parse_entries:
            params = dict(entry.get("params") or {})
            file_path = str(params.get("file_path") or "").strip()
            if file_path and Path(file_path).name in candidates:
                if matched_visible:
                    return params
                if hidden_fallback is None:
                    hidden_fallback = params
                break
        if len(parse_entries) == 1:
            params = dict(parse_entries[0].get("params") or {})
            if matched_visible:
                return params
            if hidden_fallback is None:
                hidden_fallback = params

    return hidden_fallback


def merge_rehydrated_dataframe_payload(
    df_id: int,
    original_payload: dict[str, Any],
    full_result: dict[str, Any],
) -> dict[str, Any]:
    original_metadata = dict((original_payload or {}).get("metadata") or {})
    result_metadata = dict(full_result.get("metadata") or {})
    merged_metadata = dict(original_metadata)
    merged_metadata.update(result_metadata)
    merged_metadata["df_id"] = df_id
    if original_metadata.get("visible") is not None:
        merged_metadata["visible"] = original_metadata.get("visible")
    if original_metadata.get("label"):
        merged_metadata["label"] = original_metadata.get("label")
    if full_result.get("file_path"):
        merged_metadata["file_path"] = full_result.get("file_path")
    if full_result.get("work_dir"):
        merged_metadata["work_dir"] = full_result.get("work_dir")
    merged_metadata["row_count"] = int(full_result.get("row_count") or len(full_result.get("data") or []))

    return {
        "columns": full_result.get("columns", []),
        "data": full_result.get("data", []),
        "row_count": int(full_result.get("row_count") or len(full_result.get("data") or [])),
        "metadata": merged_metadata,
    }


def hydrate_dataframe_payload_locally(
    df_id: int,
    history_blocks: list,
    *,
    current_dataframes: dict[str, dict] | None = None,
    project_dir_path: Path | None = None,
) -> dict[str, Any] | None:
    payload = find_dataframe_payload(
        df_id,
        history_blocks,
        current_dataframes=current_dataframes,
    )
    if payload is None or not is_truncated_dataframe_payload(payload):
        return payload

    parse_source = find_dataframe_parse_source(df_id, history_blocks)
    full_result = load_full_parsed_dataframe_locally(
        parse_source,
        payload=payload,
        project_dir_path=project_dir_path,
    )
    if not full_result:
        return payload
    return merge_rehydrated_dataframe_payload(df_id, payload, full_result)


def load_full_parsed_dataframe_locally(
    parse_source: dict | None,
    *,
    payload: dict | None = None,
    project_dir_path: Path | None = None,
) -> dict | None:
    full_path = _resolve_local_parse_source_path(
        parse_source,
        payload=payload,
        project_dir_path=project_dir_path,
    )
    if full_path is None:
        return None

    file_path = _select_local_source_label(parse_source, payload, full_path)
    work_dir = str(full_path.parent)
    sep = "\t" if full_path.suffix.lower() == ".tsv" else ","
    try:
        df = pd.read_csv(full_path, sep=sep, low_memory=False)
    except Exception as exc:
        logger.warning(
            "Local dataframe rehydration failed",
            file_path=file_path,
            work_dir=work_dir,
            absolute_path=str(full_path),
            error=str(exc),
        )
        return None

    rows = json.loads(df.to_json(orient="records", default_handler=str))
    return {
        "file_path": file_path,
        "work_dir": work_dir,
        "columns": list(df.columns),
        "row_count": len(df),
        "preview_rows": len(rows),
        "data": rows,
        "metadata": {
            "separator": sep,
            "column_count": len(df.columns),
            "total_rows": len(df),
            "is_truncated": False,
            "local_rehydration": True,
            "resolved_path": str(full_path),
        },
    }


def resolve_dataframe_source_path_locally(
    parse_source: dict | None,
    *,
    payload: dict | None = None,
    project_dir_path: Path | None = None,
) -> Path | None:
    return _resolve_local_parse_source_path(
        parse_source,
        payload=payload,
        project_dir_path=project_dir_path,
    )


def _resolve_local_parse_source_path(
    parse_source: dict | None,
    *,
    payload: dict | None = None,
    project_dir_path: Path | None = None,
) -> Path | None:
    candidate_files = _candidate_local_source_files(parse_source, payload)
    if not candidate_files:
        return None

    work_dir = str((parse_source or {}).get("work_dir") or "").strip()
    work_root = Path(work_dir).expanduser() if work_dir else None
    project_root = project_dir_path.resolve() if isinstance(project_dir_path, Path) else None
    search_roots = _candidate_local_search_roots(project_root, work_root)

    for candidate in candidate_files:
        resolved = _resolve_candidate_source_path(candidate, search_roots)
        if resolved is not None:
            return resolved

    preferred_parent_name = work_root.name if work_root is not None and work_root.name else ""
    return _search_local_source_by_name(
        candidate_files,
        search_roots,
        preferred_parent_name=preferred_parent_name,
    )


def _candidate_local_source_files(parse_source: dict | None, payload: dict | None) -> list[Path]:
    seen: set[str] = set()
    candidates: list[Path] = []

    def _add(raw_value) -> None:
        for candidate in _extract_file_like_candidates(raw_value):
            key = str(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

    if parse_source:
        _add(parse_source.get("file_path"))

    metadata = dict((payload or {}).get("metadata") or {})
    _add(metadata.get("file_path"))
    _add(metadata.get("label"))
    return candidates


def _extract_file_like_candidates(raw_value) -> list[Path]:
    value = str(raw_value or "").strip()
    if not value:
        return []

    matches: list[str] = []
    suffixes = {".csv", ".tsv", ".txt"}
    direct_path = Path(value)
    if direct_path.suffix.lower() in suffixes:
        matches.append(value)

    for match in re.findall(r"[A-Za-z0-9_./\\ -]+\.(?:csv|tsv|txt)", value, re.IGNORECASE):
        cleaned = match.strip().strip('"\'')
        if cleaned:
            matches.append(cleaned)

    deduped: list[Path] = []
    seen: set[str] = set()
    for match in matches:
        key = match.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(Path(key))
    return deduped


def _candidate_local_search_roots(
    project_root: Path | None,
    work_root: Path | None,
) -> list[Path]:
    roots: list[Path] = []

    def _add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_dir():
            return
        if resolved not in roots:
            roots.append(resolved)

    _add(work_root)
    _add(project_root)
    if project_root is not None:
        _add(project_root / "results")
        _add(project_root / "outputs")
        _add(project_root / "data")
    return roots


def _resolve_candidate_source_path(candidate: Path, search_roots: list[Path]) -> Path | None:
    expanded = candidate.expanduser()
    if expanded.is_absolute():
        resolved = expanded.resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
        return None

    for root in search_roots:
        resolved = (root / candidate).resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
        resolved_by_name = (root / candidate.name).resolve()
        if resolved_by_name.exists() and resolved_by_name.is_file():
            return resolved_by_name
    return None


def _search_local_source_by_name(
    candidate_files: list[Path],
    search_roots: list[Path],
    *,
    preferred_parent_name: str = "",
) -> Path | None:
    target_names = {candidate.name for candidate in candidate_files if candidate.name}
    if not target_names:
        return None

    matches: list[Path] = []
    for root in search_roots:
        for target_name in target_names:
            try:
                matches.extend(path for path in root.rglob(target_name) if path.is_file())
            except Exception:
                continue
    if not matches:
        return None

    if preferred_parent_name:
        preferred = [path for path in matches if path.parent.name == preferred_parent_name]
        if preferred:
            matches = preferred
    matches.sort(key=lambda path: (len(path.parts), str(path)))
    return matches[0]


def _select_local_source_label(
    parse_source: dict | None,
    payload: dict | None,
    full_path: Path,
) -> str:
    for candidate in (
        (parse_source or {}).get("file_path"),
        ((payload or {}).get("metadata") or {}).get("file_path"),
        ((payload or {}).get("metadata") or {}).get("label"),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return full_path.name