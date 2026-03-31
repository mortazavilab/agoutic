"""
File transfer over SSH — upload inputs and download outputs.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Callable

from common.logging_config import get_logger
from launchpad.backends.local_auth_sessions import get_local_auth_session_manager
from launchpad.config import (
    LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS,
    SSH_CONNECT_TIMEOUT_SECONDS,
    SSH_CONNECTION_ATTEMPTS,
    SSH_KNOWN_HOSTS,
    SSH_STRICT_HOST_KEY_CHECKING,
)
from launchpad.backends.ssh_manager import SSHConnection, SSHProfileData, resolve_key_file_path

logger = get_logger(__name__)


def _normalize_local_rsync_source(source: str) -> str:
    """Append a trailing slash only when the local rsync source is a directory."""
    candidate = Path(source).expanduser()
    normalized = str(candidate)
    if candidate.exists() and candidate.is_dir() and not normalized.endswith("/"):
        return normalized + "/"
    return normalized


class FileTransferManager:
    """Handles file transfers between local and remote systems."""

    # Regex for rsync --progress output parsing
    _RE_PROGRESS_LINE = re.compile(r"^\s+[\d,]+\s+(\d+)%\s+([\d.]+\S+/s)")
    _RE_OVERALL_PROGRESS = re.compile(r"\(xfr#(\d+),\s*(?:to|ir)-chk=(\d+)/(\d+)\)")
    _RE_FILENAME = re.compile(r"^[^\s].*[/.]")  # non-indented line with path separators or extension

    async def _try_broker_transfer(
        self,
        profile: SSHProfileData,
        source: str,
        dest: str,
        include_patterns: list[str] | None,
        exclude_patterns: list[str] | None,
        direction: str,
        copy_links: bool,
        on_progress: Callable[[dict], None] | None = None,
    ) -> dict | None:
        """Use a local auth broker session when one is active for the profile."""
        if profile.auth_method != "key_file" or not profile.local_username or not profile.key_file_path:
            return None

        session_manager = get_local_auth_session_manager()
        session = await session_manager.get_active_session(profile)
        if session is None:
            return None

        response = await session_manager.invoke(
            session,
            {
                "op": "rsync_transfer",
                "profile": {
                    "ssh_host": profile.ssh_host,
                    "ssh_port": profile.ssh_port,
                    "ssh_username": profile.ssh_username,
                    "auth_method": profile.auth_method,
                    "key_file_path": profile.key_file_path,
                },
                "source": source,
                "dest": dest,
                "include_patterns": include_patterns or [],
                "exclude_patterns": exclude_patterns or [],
                "copy_links": copy_links,
                "timeout_seconds": LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS,
            },
        )
        if response.get("ok"):
            if on_progress and response.get("stdout"):
                for line in response["stdout"].splitlines():
                    for segment in line.split("\r"):
                        info = self._parse_rsync_progress_line(segment)
                        if info:
                            on_progress(info)
            return {
                "ok": True,
                "message": f"{direction.title()} completed",
                "bytes_transferred": int(response.get("bytes_transferred", 0)),
            }
        return {
            "ok": False,
            "message": f"{direction.title()} failed: {response.get('stderr') or response.get('error') or 'unknown error'}",
            "bytes_transferred": 0,
        }

    async def upload_inputs(
        self,
        profile: SSHProfileData,
        local_path: str,
        remote_path: str,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> dict:
        """Upload local input files to the remote host via rsync.

        Returns: {ok: bool, message: str, bytes_transferred: int}
        """
        candidate = Path(local_path).expanduser()
        if not candidate.exists():
            return {
                "ok": False,
                "message": f"Local source path does not exist on the Launchpad server: {candidate}",
                "bytes_transferred": 0,
            }
        source = _normalize_local_rsync_source(local_path)
        return await self._rsync_transfer(
            profile=profile,
            source=source,
            dest=f"{profile.ssh_username}@{profile.ssh_host}:{remote_path}",
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            direction="upload",
            copy_links=True,
        )

    async def download_outputs(
        self,
        profile: SSHProfileData,
        remote_path: str,
        local_path: str,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> dict:
        """Download remote output files to local via rsync.

        Returns: {ok: bool, message: str, bytes_transferred: int}
        """
        # Ensure local destination exists
        Path(local_path).mkdir(parents=True, exist_ok=True)

        # Trailing slash on the remote source so rsync copies the *contents*
        # of the directory into local_path, not the directory itself (which
        # would create a nested subdirectory).
        _remote = remote_path.rstrip("/") + "/"

        return await self._rsync_transfer(
            profile=profile,
            source=f"{profile.ssh_username}@{profile.ssh_host}:{_remote}",
            dest=local_path,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            direction="download",
            # Remote workflows often contain symlinked artifacts (for example,
            # bams/annot links back to shared storage). For local copy-back,
            # materialize link targets so files exist under local workflow dirs.
            copy_links=True,
            on_progress=on_progress,
        )

    async def _rsync_transfer(
        self,
        profile: SSHProfileData,
        source: str,
        dest: str,
        include_patterns: list[str] | None,
        exclude_patterns: list[str] | None,
        direction: str,
        copy_links: bool,
        on_progress: Callable[[dict], None] | None = None,
    ) -> dict:
        """Execute rsync transfer."""
        cmd = [
            "rsync", "-avz", "--omit-dir-times", "--no-perms", "--partial", "--progress",
            "-e", self._build_ssh_command(profile),
        ]

        if copy_links:
            cmd.append("--copy-links")

        if include_patterns:
            for pat in include_patterns:
                cmd.extend(["--include", pat])

        if exclude_patterns:
            for pat in exclude_patterns:
                cmd.extend(["--exclude", pat])

        cmd.extend([source, dest])

        logger.info(f"Starting {direction}: {source} → {dest}")

        try:
            broker_result = await self._try_broker_transfer(
                profile=profile,
                source=source,
                dest=dest,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                direction=direction,
                copy_links=copy_links,
                on_progress=on_progress,
            )
            if broker_result is not None:
                return broker_result

            if profile.auth_method == "key_file" and profile.local_username and profile.key_file_path:
                resolved_key = Path(resolve_key_file_path(profile))
                if not resolved_key.exists():
                    return {
                        "ok": False,
                        "message": f"SSH key file not found: {resolved_key}",
                        "bytes_transferred": 0,
                    }
                if not resolved_key.is_file() or not os.access(resolved_key, os.R_OK):
                    return {
                        "ok": False,
                        "message": "No active local auth session for this profile. Unlock the profile in Remote Profiles first.",
                        "bytes_transferred": 0,
                    }

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if on_progress:
                # Stream stdout line-by-line for real-time progress reporting
                stdout_lines: list[str] = []
                async for raw_line in proc.stdout:
                    decoded = raw_line.decode(errors="replace")
                    stdout_lines.append(decoded)
                    # rsync may embed \r for in-place updates within a line
                    for segment in decoded.rstrip("\n").split("\r"):
                        info = self._parse_rsync_progress_line(segment)
                        if info:
                            on_progress(info)
                stderr_bytes = await proc.stderr.read()
                await proc.wait()
                stdout_text = "".join(stdout_lines)
                stderr_text = stderr_bytes.decode(errors="replace")
            else:
                stdout_bytes, stderr_bytes = await proc.communicate()
                stdout_text = stdout_bytes.decode(errors="replace")
                stderr_text = stderr_bytes.decode(errors="replace")

            if proc.returncode == 0:
                logger.info(f"{direction.title()} completed successfully")
                return {
                    "ok": True,
                    "message": f"{direction.title()} completed",
                    "bytes_transferred": self._parse_rsync_bytes(stdout_text),
                }
            else:
                error_msg = stderr_text.strip()
                logger.error(f"{direction.title()} failed: {error_msg}")
                return {
                    "ok": False,
                    "message": f"{direction.title()} failed: {error_msg}",
                    "bytes_transferred": 0,
                }
        except FileNotFoundError:
            return {
                "ok": False,
                "message": "rsync not found. Install rsync for file transfer.",
                "bytes_transferred": 0,
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"{direction.title()} error: {e}",
                "bytes_transferred": 0,
            }

    @classmethod
    def _parse_rsync_progress_line(cls, line: str) -> dict | None:
        """Parse a single segment of rsync --progress output.

        Returns a dict with keys like current_file, file_percent, speed,
        files_transferred, files_remaining, files_total — or None if the
        line contains no useful progress information.
        """
        stripped = line.strip()
        if not stripped:
            return None

        # Progress line: "  32,768 100%  31.25MB/s  0:00:00 (xfr#1, to-chk=5/8)"
        m_progress = cls._RE_PROGRESS_LINE.search(stripped)
        if m_progress:
            info: dict = {"file_percent": int(m_progress.group(1)), "speed": m_progress.group(2)}
            m_overall = cls._RE_OVERALL_PROGRESS.search(stripped)
            if m_overall:
                info["files_transferred"] = int(m_overall.group(1))
                info["files_remaining"] = int(m_overall.group(2))
                info["files_total"] = int(m_overall.group(3))
            return info

        # Skip rsync header / summary lines
        if stripped.startswith(("receiving ", "sending ", "created ", "total size", "sent ", "building ")):
            return None

        # Filename line: non-indented, contains path separator or extension
        if cls._RE_FILENAME.match(stripped):
            return {"current_file": stripped}

        return None

    def _build_ssh_command(self, profile: SSHProfileData) -> str:
        """Build the ssh command string for rsync -e."""
        parts = ["ssh", "-p", str(profile.ssh_port)]
        if profile.auth_method == "key_file" and profile.key_file_path:
            parts.extend(["-i", resolve_key_file_path(profile)])
            parts.extend(["-o", "IdentitiesOnly=yes"])
        parts.extend(["-o", "BatchMode=yes"])
        parts.extend(["-o", "PreferredAuthentications=publickey"])
        parts.extend(["-o", "PasswordAuthentication=no"])
        parts.extend(["-o", "KbdInteractiveAuthentication=no"])
        parts.extend(["-o", "GSSAPIAuthentication=no"])
        parts.extend(["-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}"])
        parts.extend(["-o", f"ConnectionAttempts={SSH_CONNECTION_ATTEMPTS}"])
        parts.extend(["-o", f"StrictHostKeyChecking={'yes' if SSH_STRICT_HOST_KEY_CHECKING else 'no'}"])
        if SSH_KNOWN_HOSTS:
            parts.extend(["-o", f"UserKnownHostsFile={str(Path(SSH_KNOWN_HOSTS).expanduser())}"])
        return " ".join(shlex.quote(part) for part in parts)

    @staticmethod
    def _parse_rsync_bytes(output: str) -> int:
        """Parse bytes transferred from rsync output."""
        for line in output.split("\n"):
            if "total size" in line.lower():
                # "total size is 1,234,567  speedup is 1.23"
                parts = line.split("is")
                if len(parts) >= 2:
                    num_str = parts[1].split()[0].replace(",", "")
                    try:
                        return int(num_str)
                    except ValueError:
                        pass
        return 0
