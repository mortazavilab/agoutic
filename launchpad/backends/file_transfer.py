"""
File transfer over SSH — upload inputs and download outputs.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from common.logging_config import get_logger
from launchpad.backends.ssh_manager import SSHConnection, SSHProfileData

logger = get_logger(__name__)


class FileTransferManager:
    """Handles file transfers between local and remote systems."""

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
        parts = ["ssh", f"-p {profile.ssh_port}"]
        if profile.auth_method == "key_file" and profile.key_file_path:
            parts.append(f"-i {profile.key_file_path}")
        parts.append("-o StrictHostKeyChecking=no")  # TODO: configurable in production
        return " ".join(parts)

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
