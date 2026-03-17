"""
SSH connection manager — loads profiles from DB, creates connections.

Uses asyncssh for async SSH operations. Falls back to subprocess ssh/rsync
for file transfers.
"""
from __future__ import annotations

import asyncio
import os
import pwd
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.logging_config import get_logger
from launchpad.backends.local_auth_sessions import get_local_auth_session_manager
from launchpad.config import SSH_AGENT_FORWARDING, SSH_KNOWN_HOSTS

logger = get_logger(__name__)


@dataclass
class SSHCommandResult:
    stdout: str
    stderr: str
    exit_status: int


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
    local_username: str | None  # Local Unix user used for per-session SSH access via broker
    is_enabled: bool


class SSHConnectionManager:
    """Manages SSH connections using profile data from the database."""

    async def connect(self, profile: SSHProfileData, local_password: str = "") -> "SSHConnection":
        """Create an SSH connection from a profile."""
        if not profile.is_enabled:
            raise ConnectionError(f"SSH profile {profile.nickname or profile.id!r} is disabled")

        if profile.auth_method == "key_file" and profile.key_file_path:
            if profile.local_username:
                session_manager = get_local_auth_session_manager()
                if local_password:
                    await session_manager.create_or_replace_session(profile, local_password)
                session = await session_manager.get_active_session(profile)
                if session is not None:
                    return LocalBrokerSSHConnection(profile, session_manager, session)

            key_path = Path(resolve_key_file_path(profile))
            if not key_path.exists():
                raise FileNotFoundError(f"SSH key file not found: {key_path}")

            if os.access(key_path, os.R_OK):
                connect_kwargs = self._base_connect_kwargs(profile)
                connect_kwargs["client_keys"] = [str(key_path)]
            else:
                if profile.local_username:
                    raise PermissionError(
                        f"SSH key file is not readable by the AGOUTIC process: {key_path}. "
                        "Start a local auth session for this profile by entering the local Unix password in Remote Profiles, then retry the operation."
                    )
                raise PermissionError(
                    f"SSH key file is not readable by the AGOUTIC process: {key_path}. "
                    "Use a key readable by agoutic_runner or switch to ssh_agent."
                )
        elif profile.auth_method == "ssh_agent":
            connect_kwargs = self._base_connect_kwargs(profile)
            ssh_auth_sock = os.getenv("SSH_AUTH_SOCK")
            if ssh_auth_sock:
                connect_kwargs["agent_path"] = ssh_auth_sock
            if SSH_AGENT_FORWARDING:
                connect_kwargs["agent_forwarding"] = True
        else:
            raise ValueError(f"Unsupported auth method: {profile.auth_method}")

        try:
            import asyncssh
        except ImportError:
            raise ImportError("asyncssh is required for remote execution. Install with: pip install asyncssh")

        logger.info(f"Connecting to {profile.ssh_username}@{profile.ssh_host}:{profile.ssh_port}")
        conn = await asyncssh.connect(**connect_kwargs)
        return SSHConnection(conn, profile)

    @staticmethod
    def _base_connect_kwargs(profile: SSHProfileData) -> dict[str, Any]:
        connect_kwargs: dict[str, Any] = {
            "host": profile.ssh_host,
            "port": profile.ssh_port,
            "username": profile.ssh_username,
        }
        if SSH_KNOWN_HOSTS:
            connect_kwargs["known_hosts"] = str(Path(SSH_KNOWN_HOSTS).expanduser())
        return connect_kwargs

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


def resolve_key_file_path(profile: SSHProfileData) -> str:
    """Resolve a profile's key path, expanding `~` relative to the owning local user when needed."""
    if not profile.key_file_path:
        raise ValueError("SSH profile is missing key_file_path")

    raw_path = profile.key_file_path.strip()
    if profile.local_username and raw_path.startswith("~/"):
        user_home = pwd.getpwnam(profile.local_username).pw_dir
        raw_path = str(Path(user_home) / raw_path[2:])
    return str(Path(raw_path).expanduser())


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


class LocalBrokerSSHConnection:
    """Connection wrapper backed by a per-user local auth broker session."""

    def __init__(self, profile: SSHProfileData, session_manager: Any, session: Any):
        self.profile = profile
        self._session_manager = session_manager
        self._session = session

    async def run(self, command: str, check: bool = False) -> SSHCommandResult:
        response = await self._session_manager.invoke(
            self._session,
            {
                "op": "ssh_run",
                "profile": {
                    "ssh_host": self.profile.ssh_host,
                    "ssh_port": self.profile.ssh_port,
                    "ssh_username": self.profile.ssh_username,
                    "auth_method": self.profile.auth_method,
                    "key_file_path": self.profile.key_file_path,
                },
                "command": command,
            },
        )
        result = SSHCommandResult(
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
            exit_status=int(response.get("exit_status", 1)),
        )
        if check and result.exit_status != 0:
            raise RuntimeError(result.stderr or f"SSH command failed with exit status {result.exit_status}")
        return result

    async def run_checked(self, command: str) -> str:
        result = await self.run(command, check=True)
        return result.stdout or ""

    async def path_exists(self, path: str) -> bool:
        result = await self.run(f"test -e {path!r} && echo YES || echo NO")
        return (result.stdout or "").strip() == "YES"

    async def path_is_writable(self, path: str) -> bool:
        result = await self.run(f"test -w {path!r} && echo YES || echo NO")
        return (result.stdout or "").strip() == "YES"

    async def mkdir_p(self, path: str) -> None:
        await self.run(f"mkdir -p {path!r}", check=True)

    async def close(self) -> None:
        return None
