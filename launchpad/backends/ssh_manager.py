"""
SSH connection manager — loads profiles from DB, creates connections.

Uses asyncssh for async SSH operations. Falls back to subprocess ssh/rsync
for file transfers.
"""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SSHProfileData:
    """In-memory representation of an SSH profile (loaded from DB)."""
    id: str
    user_id: str
    nickname: str | None
    ssh_host: str
    ssh_port: int
    ssh_username: str
    auth_method: str  # "key_file" or "ssh_agent"
    key_file_path: str | None
    local_username: str | None  # OS user who owns the key file (for sudo access)
    is_enabled: bool


class SSHConnectionManager:
    """Manages SSH connections using profile data from the database."""

    async def connect(self, profile: SSHProfileData, local_password: str = "") -> "SSHConnection":
        """Create an SSH connection from a profile."""
        if not profile.is_enabled:
            raise ConnectionError(f"SSH profile {profile.nickname or profile.id!r} is disabled")

        try:
            import asyncssh
        except ImportError:
            raise ImportError("asyncssh is required for remote execution. Install with: pip install asyncssh")

        connect_kwargs: dict[str, Any] = {
            "host": profile.ssh_host,
            "port": profile.ssh_port,
            "username": profile.ssh_username,
            "known_hosts": None,  # TODO: configurable known_hosts in production
        }

        if profile.auth_method == "key_file" and profile.key_file_path:
            key_path = Path(profile.key_file_path).expanduser()
            if profile.local_username:
                # Read key via sudo as the owning OS user
                key_data = await self._read_key_as_user(str(key_path), profile.local_username, local_password)
                import asyncssh
                connect_kwargs["client_keys"] = [asyncssh.import_private_key(key_data)]
            else:
                if not key_path.exists():
                    raise FileNotFoundError(f"SSH key file not found: {key_path}")
                connect_kwargs["client_keys"] = [str(key_path)]
        elif profile.auth_method == "ssh_agent":
            connect_kwargs["agent_forwarding"] = True
        else:
            raise ValueError(f"Unsupported auth method: {profile.auth_method}")

        logger.info(f"Connecting to {profile.ssh_username}@{profile.ssh_host}:{profile.ssh_port}")
        conn = await asyncssh.connect(**connect_kwargs)
        return SSHConnection(conn, profile)

    async def test_connection(self, profile: SSHProfileData, local_password: str = "") -> dict[str, Any]:
        """Test SSH connectivity. Returns {ok: bool, message: str, detail: ...}."""
        try:
            conn = await asyncio.wait_for(self.connect(profile, local_password), timeout=15.0)
            result = await conn.run("echo AGOUTIC_SSH_OK && hostname && whoami")
            await conn.close()

            lines = result.stdout.strip().split("\n") if result.stdout else []
            ok = len(lines) >= 1 and lines[0] == "AGOUTIC_SSH_OK"
            return {
                "ok": ok,
                "message": "Connection successful" if ok else "Unexpected response",
                "hostname": lines[1] if len(lines) > 1 else None,
                "remote_user": lines[2] if len(lines) > 2 else None,
            }
        except asyncio.TimeoutError:
            return {"ok": False, "message": "Connection timed out (15s)"}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {e}"}

    @staticmethod
    async def _read_key_as_user(key_path: str, username: str, password: str = "") -> str:
        """Read an SSH private key file using sudo as the specified OS user.
        Password is passed via stdin to sudo -S (never stored)."""
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-S", "-u", username, "cat", key_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin_data = (password + "\n").encode() if password else b"\n"
        stdout, stderr = await proc.communicate(input=stdin_data)
        if proc.returncode != 0:
            err = stderr.decode().strip()
            # Strip the password prompt from error output
            err_lines = [l for l in err.splitlines() if not l.startswith("[sudo]")]
            err_msg = "\n".join(err_lines).strip() or "sudo authentication failed"
            raise PermissionError(f"Cannot read key as {username}: {err_msg}")
        return stdout.decode()


class SSHConnection:
    """Wraps an asyncssh connection with convenience methods."""

    def __init__(self, conn: Any, profile: SSHProfileData):
        self._conn = conn
        self.profile = profile

    async def run(self, command: str, check: bool = False) -> Any:
        """Run a command on the remote host."""
        result = await self._conn.run(command, check=check)
        return result

    async def run_checked(self, command: str) -> str:
        """Run a command and raise on non-zero exit. Returns stdout."""
        result = await self._conn.run(command, check=True)
        return result.stdout or ""

    async def path_exists(self, path: str) -> bool:
        """Check if a remote path exists."""
        result = await self._conn.run(f"test -e {path!r} && echo YES || echo NO")
        return (result.stdout or "").strip() == "YES"

    async def path_is_writable(self, path: str) -> bool:
        """Check if a remote path is writable."""
        result = await self._conn.run(f"test -w {path!r} && echo YES || echo NO")
        return (result.stdout or "").strip() == "YES"

    async def mkdir_p(self, path: str) -> None:
        """Create a remote directory (with parents)."""
        await self._conn.run(f"mkdir -p {path!r}", check=True)

    async def close(self) -> None:
        """Close the SSH connection."""
        self._conn.close()
        await self._conn.wait_closed()
