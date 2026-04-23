"""Analyzer-side adapter helpers for an upstream edgePython MCP server.

This module is the first migration step toward making analyzer/server4 the
Agoutic-facing DE adapter. It manages conversation-scoped MCP clients for the
upstream edgePython server and centralises project artifact relocation.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from analyzer.config import (
    DE_RESULTS_SUBDIR,
    DE_SESSION_TTL_SECONDS,
    EDGEPYTHON_UPSTREAM_URL,
)
from common.mcp_client import MCPHttpClient
from common.logging_config import get_logger

logger = get_logger(__name__)

_SAVED_PATH_PATTERN = re.compile(r"saved to:\s*(.+)$", re.IGNORECASE)


@dataclass
class EdgePythonSession:
    """Conversation-scoped client wrapper for upstream edgePython MCP."""

    conversation_id: str
    client: MCPHttpClient
    created_at: datetime
    last_used_at: datetime


_EDGEPYTHON_SESSIONS: dict[str, EdgePythonSession] = {}
_EDGEPYTHON_SESSION_LOCK = asyncio.Lock()


def _extract_saved_paths(result_text: str) -> list[str]:
    """Extract every artifact path from multiline 'saved to:' output."""
    paths: list[str] = []
    for raw_line in result_text.splitlines():
        match = _SAVED_PATH_PATTERN.search(raw_line.strip())
        if not match:
            continue
        path = match.group(1).strip()
        if path:
            paths.append(path)
    return paths


def _extract_saved_path(result_text: str) -> str | None:
    """Extract a filesystem path from tool output like 'Saved ... to: /x/y'."""
    paths = _extract_saved_paths(result_text)
    return paths[0] if paths else None


def _build_project_output_path(
    project_dir: str | Path,
    filename: str,
    *,
    subdir: str = DE_RESULTS_SUBDIR,
) -> Path:
    """Return the target artifact path inside the project's DE output folder."""
    return Path(project_dir) / subdir / filename


def relocate_edgepython_artifact(
    result_text: str,
    *,
    project_dir: str | Path,
    filename: str | None = None,
    filename_overrides: dict[str, str] | None = None,
) -> tuple[str, Path] | tuple[None, None]:
    """Move an upstream-generated artifact into the project DE output folder.

    Returns the rewritten result text and the final destination path.
    If no source path is found in the result text, returns (None, None).
    """
    source_path_strs = _extract_saved_paths(result_text)
    if not source_path_strs:
        return None, None

    suffix_overrides = {
        str(suffix).lower(): override
        for suffix, override in (filename_overrides or {}).items()
        if override
    }
    rewritten = result_text
    primary_target: Path | None = None

    for index, source_path_str in enumerate(source_path_strs):
        source_path = Path(source_path_str)
        if not source_path.exists():
            raise FileNotFoundError(f"edgePython artifact not found: {source_path}")

        target_name = suffix_overrides.get(source_path.suffix.lower())
        if target_name is None:
            target_name = filename if index == 0 and filename else source_path.name
        target_path = _build_project_output_path(project_dir, target_name)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if source_path.resolve() != target_path.resolve():
            shutil.move(str(source_path), str(target_path))

        rewritten = rewritten.replace(source_path_str, str(target_path))
        if primary_target is None:
            primary_target = target_path
        logger.info(
            "Relocated edgePython artifact",
            source_path=str(source_path),
            target_path=str(target_path),
        )

    return rewritten, primary_target


async def _connect_new_client(conversation_id: str) -> EdgePythonSession:
    client = MCPHttpClient(
        name=f"edgepython-{conversation_id}",
        base_url=EDGEPYTHON_UPSTREAM_URL,
    )
    await client.connect()
    now = datetime.utcnow()
    return EdgePythonSession(
        conversation_id=conversation_id,
        client=client,
        created_at=now,
        last_used_at=now,
    )


async def get_edgepython_session(conversation_id: str) -> EdgePythonSession:
    """Return a reusable upstream MCP client for a conversation."""
    async with _EDGEPYTHON_SESSION_LOCK:
        session = _EDGEPYTHON_SESSIONS.get(conversation_id)
        if session is None:
            session = await _connect_new_client(conversation_id)
            _EDGEPYTHON_SESSIONS[conversation_id] = session
        session.last_used_at = datetime.utcnow()
        return session


async def call_edgepython_tool(
    tool_name: str,
    *,
    conversation_id: str,
    **params: Any,
) -> Any:
    """Call the upstream edgePython MCP tool using a conversation-scoped client."""
    session = await get_edgepython_session(conversation_id)
    session.last_used_at = datetime.utcnow()
    return await session.client.call_tool(tool_name, **params)


async def cleanup_expired_edgepython_sessions() -> list[str]:
    """Disconnect and remove stale conversation sessions."""
    cutoff = datetime.utcnow() - timedelta(seconds=DE_SESSION_TTL_SECONDS)
    removed: list[str] = []

    async with _EDGEPYTHON_SESSION_LOCK:
        expired_ids = [
            conversation_id
            for conversation_id, session in _EDGEPYTHON_SESSIONS.items()
            if session.last_used_at < cutoff
        ]
        for conversation_id in expired_ids:
            session = _EDGEPYTHON_SESSIONS.pop(conversation_id)
            try:
                await session.client.disconnect()
            except Exception as exc:  # pragma: no cover - defensive cleanup logging
                logger.warning(
                    "Failed to disconnect expired edgePython session",
                    conversation_id=conversation_id,
                    error=str(exc),
                )
            removed.append(conversation_id)

    return removed


async def reset_edgepython_session(conversation_id: str) -> bool:
    """Disconnect and drop a conversation-scoped upstream session."""
    async with _EDGEPYTHON_SESSION_LOCK:
        session = _EDGEPYTHON_SESSIONS.pop(conversation_id, None)
    if session is None:
        return False
    await session.client.disconnect()
    return True