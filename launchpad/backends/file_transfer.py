"""
File transfer over SSH — upload inputs and download outputs.
"""
from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path

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


class FileTransferManager:
    """Handles file transfers between local and remote systems."""

    async def _try_broker_transfer(
        self,
        profile: SSHProfileData,
        source: str,
        dest: str,
        exclude_patterns: list[str] | None,
        direction: str,
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
                "exclude_patterns": exclude_patterns or [],
                "timeout_seconds": LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS,
            },
        )
        if response.get("ok"):
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
        return await self._rsync_transfer(
            profile=profile,
            source=local_path,
            dest=f"{profile.ssh_username}@{profile.ssh_host}:{remote_path}",
            exclude_patterns=exclude_patterns,
            direction="upload",
        )

    async def download_outputs(
        self,
        profile: SSHProfileData,
        remote_path: str,
        local_path: str,
        exclude_patterns: list[str] | None = None,
    ) -> dict:
        """Download remote output files to local via rsync.

        Returns: {ok: bool, message: str, bytes_transferred: int}
        """
        # Ensure local destination exists
        Path(local_path).mkdir(parents=True, exist_ok=True)

        return await self._rsync_transfer(
            profile=profile,
            source=f"{profile.ssh_username}@{profile.ssh_host}:{remote_path}",
            dest=local_path,
            exclude_patterns=exclude_patterns,
            direction="download",
        )

    async def _rsync_transfer(
        self,
        profile: SSHProfileData,
        source: str,
        dest: str,
        exclude_patterns: list[str] | None,
        direction: str,
    ) -> dict:
        """Execute rsync transfer."""
        cmd = [
            "rsync", "-avz", "--partial", "--progress",
            "-e", self._build_ssh_command(profile),
        ]

        if exclude_patterns:
            for pat in exclude_patterns:
                cmd.extend(["--exclude", pat])

        # Ensure source directory has trailing slash for contents transfer
        if not source.endswith("/") and ":" in source:
            source += "/"
        elif not source.endswith("/"):
            source += "/"

        cmd.extend([source, dest])

        logger.info(f"Starting {direction}: {source} → {dest}")

        try:
            broker_result = await self._try_broker_transfer(
                profile=profile,
                source=source,
                dest=dest,
                exclude_patterns=exclude_patterns,
                direction=direction,
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
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                logger.info(f"{direction.title()} completed successfully")
                return {
                    "ok": True,
                    "message": f"{direction.title()} completed",
                    "bytes_transferred": self._parse_rsync_bytes(stdout.decode()),
                }
            else:
                error_msg = stderr.decode().strip()
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
