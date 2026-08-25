"""
SSH connection manager — loads profiles from DB, creates connections.

Uses asyncssh for async SSH operations. Falls back to subprocess ssh/rsync
for file transfers.
"""
from __future__ import annotations

import asyncio
import json
import os
import pwd
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.logging_config import get_logger
from launchpad.backends.local_auth_sessions import get_local_auth_session_manager
from launchpad.config import LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS, SSH_AGENT_FORWARDING, SSH_CONNECT_TIMEOUT_SECONDS, SSH_KNOWN_HOSTS

logger = get_logger(__name__)
_SBANK_UNAVAILABLE_SENTINEL = "__AGOUTIC_SBANK_UNAVAILABLE__"
_TABLE_SPLIT_RE = re.compile(r"\s{2,}|\t+")
_PIPE_SPLIT_RE = re.compile(r"\s*\|\s*")
_NUMBER_TOKEN_RE = re.compile(r"^[\d,]+(?:\.\d+)?$")


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
    transfer_host: str | None = None
    remote_base_path: str | None = None
    default_slurm_account: str | None = None
    default_slurm_partition: str | None = None
    default_slurm_gpu_account: str | None = None
    default_slurm_gpu_partition: str | None = None


def _is_separator_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and re.fullmatch(r"[\s\-=+|]+", stripped) is not None


def _split_tabular_line(line: str) -> list[str]:
    stripped = line.strip().strip("|").strip()
    if not stripped:
        return []
    if "|" in stripped:
        return [cell.strip() for cell in _PIPE_SPLIT_RE.split(stripped) if cell.strip()]
    return [cell.strip() for cell in _TABLE_SPLIT_RE.split(stripped) if cell.strip()]


def _filter_balance_output(raw_output: str, username: str) -> str:
    lines = [line.rstrip() for line in raw_output.splitlines() if line.strip()]
    username_text = (username or "").strip()
    if not lines:
        return ""
    if not username_text:
        return "\n".join(lines)

    leading_username_re = re.compile(rf"^\s*\|?\s*{re.escape(username_text)}(?:\s+\*)?(?:\s|\||$)", re.IGNORECASE)
    matching_lines = [line for line in lines if leading_username_re.search(line)]
    if not matching_lines:
        username_re = re.compile(rf"\b{re.escape(username_text)}\b", re.IGNORECASE)
        matching_lines = [line for line in lines if username_re.search(line)]
    if matching_lines:
        return "\n".join(matching_lines)
    return "\n".join(lines)


def _normalize_balance_user(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""

    tokens = text.split()
    while tokens and _NUMBER_TOKEN_RE.fullmatch(tokens[-1]):
        tokens.pop()
    text = " ".join(tokens)
    text = re.sub(r"\s*\*+\s*$", "", text)
    return text.strip().lower()


def _split_suffix_number(value: str) -> tuple[str, str] | None:
    tokens = str(value or "").split()
    if len(tokens) < 2 or not _NUMBER_TOKEN_RE.fullmatch(tokens[-1]):
        return None
    return " ".join(tokens[:-1]).strip(), tokens[-1]


def _split_two_number_values(value: str) -> tuple[str, str] | None:
    tokens = str(value or "").split()
    if len(tokens) != 2 or not all(_NUMBER_TOKEN_RE.fullmatch(token) for token in tokens):
        return None
    return tokens[0], tokens[1]


def _expand_balance_cell(header: str, value: str) -> dict[str, str]:
    header_text = " ".join(str(header or "").split())
    header_key = header_text.lower()

    if header_key == "user usage":
        split_value = _split_suffix_number(value)
        if split_value:
            return {"User": split_value[0], "Usage": split_value[1]}
    elif header_key in {"account usage", "account remaining"}:
        split_value = _split_suffix_number(value)
        if split_value:
            metric_name = "Account Usage" if header_key == "account usage" else "Remaining"
            return {"Account": split_value[0], metric_name: split_value[1]}
    elif header_key == "account limit available (sus)":
        split_value = _split_two_number_values(value)
        if split_value:
            return {"Account Limit": split_value[0], "Available (SUs)": split_value[1]}

    return {header_text: str(value or "").strip()}


def _filter_balance_rows(rows: list[dict[str, str]], username: str) -> list[dict[str, str]]:
    username_text = (username or "").strip().lower()
    if not username_text or not rows:
        return rows

    keys = list(rows[0].keys())
    user_keys = [key for key in keys if any(token in key.lower() for token in ("user", "login", "owner"))]
    if user_keys:
        exact_rows = [
            row
            for row in rows
            if any(_normalize_balance_user(str(row.get(key, "")) or "") == username_text for key in user_keys)
        ]
        return exact_rows

    matching_rows = [
        row
        for row in rows
        if any(username_text in (str(value or "").strip().lower()) for value in row.values())
    ]
    return matching_rows or rows


def _parse_sbank_balance_rows(raw_output: str, username: str) -> list[dict[str, str]]:
    lines = [line.rstrip() for line in raw_output.splitlines() if line.strip()]
    if not lines:
        return []

    header_index: int | None = None
    headers: list[str] = []
    for index, line in enumerate(lines):
        if _is_separator_line(line):
            continue
        cells = _split_tabular_line(line)
        if len(cells) < 2:
            continue
        header_text = " ".join(cells).lower()
        if not any(token in header_text for token in ("account", "user", "balance", "credit", "allocation")):
            continue

        has_following_row = False
        for next_line in lines[index + 1:]:
            if _is_separator_line(next_line):
                continue
            if len(_split_tabular_line(next_line)) >= 2:
                has_following_row = True
            break
        if has_following_row:
            header_index = index
            headers = cells
            break

    if header_index is None or not headers:
        return []

    rows: list[dict[str, str]] = []
    for line in lines[header_index + 1:]:
        if _is_separator_line(line):
            continue
        cells = _split_tabular_line(line)
        if len(cells) < 2:
            if rows:
                break
            continue
        if len(cells) > len(headers):
            cells = cells[: len(headers) - 1] + [" ".join(cells[len(headers) - 1 :])]
        elif len(cells) < len(headers):
            cells = cells + [""] * (len(headers) - len(cells))

        expanded_row: dict[str, str] = {}
        for header, cell in zip(headers, cells):
            expanded_row.update(_expand_balance_cell(header, cell))
        rows.append(expanded_row)

    return _filter_balance_rows(rows, username)


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

    async def _load_slurm_balance_info(self, conn: "SSHConnection | LocalBrokerSSHConnection", remote_user: str) -> dict[str, Any]:
        username = (remote_user or "").strip()
        if not username:
            return {}

        balance_command = (
            "if command -v sbank >/dev/null 2>&1; then "
            f"sbank balance statement {shlex.quote(username)}; "
            f"else printf '{_SBANK_UNAVAILABLE_SENTINEL}\\n'; fi"
        )
        result = await conn.run(balance_command, timeout_seconds=float(SSH_CONNECT_TIMEOUT_SECONDS))
        stdout_text = (getattr(result, "stdout", "") or "").strip()
        stderr_text = (getattr(result, "stderr", "") or "").strip()
        exit_status = int(getattr(result, "exit_status", 1))

        if stdout_text == _SBANK_UNAVAILABLE_SENTINEL:
            return {}

        if exit_status != 0:
            if stdout_text:
                return {"slurm_balance_raw": stdout_text, "slurm_balance_error": stderr_text or f"sbank exited with status {exit_status}"}
            if stderr_text:
                return {"slurm_balance_error": stderr_text}
            return {"slurm_balance_error": f"sbank exited with status {exit_status}"}

        if not stdout_text:
            return {}

        rows = _parse_sbank_balance_rows(stdout_text, username)
        if rows:
            return {"slurm_balance_rows": rows}
        filtered_output = _filter_balance_output(stdout_text, username)
        if filtered_output:
            return {"slurm_balance_raw": filtered_output}
        return {}

    async def test_connection(self, profile: SSHProfileData, local_password: str = "") -> dict[str, Any]:
        """Test SSH connectivity. Returns {ok: bool, message: str, detail: ...}."""
        conn: SSHConnection | LocalBrokerSSHConnection | None = None
        try:
            conn = await asyncio.wait_for(self.connect(profile, local_password), timeout=float(SSH_CONNECT_TIMEOUT_SECONDS))
            result = await conn.run(
                "echo AGOUTIC_SSH_OK && hostname && whoami",
                timeout_seconds=float(SSH_CONNECT_TIMEOUT_SECONDS),
            )

            exit_status = getattr(result, "exit_status", 0)
            stderr_text = (getattr(result, "stderr", "") or "").strip()
            if exit_status != 0:
                if stderr_text:
                    return {"ok": False, "message": f"Connection failed: {stderr_text}"}
                return {"ok": False, "message": f"Connection failed with exit status {exit_status}"}

            lines = result.stdout.strip().split("\n") if result.stdout else []
            ok = len(lines) >= 1 and lines[0] == "AGOUTIC_SSH_OK"
            remote_user = lines[2] if len(lines) > 2 else None
            response: dict[str, Any] = {
                "ok": ok,
                "message": "Connection successful" if ok else "Unexpected response",
                "hostname": lines[1] if len(lines) > 1 else None,
                "remote_user": remote_user,
                "slurm_balance_rows": [],
            }
            if ok:
                response.update(await self._load_slurm_balance_info(conn, remote_user or profile.ssh_username))
            return response
        except asyncio.TimeoutError:
            return {"ok": False, "message": f"Connection timed out ({SSH_CONNECT_TIMEOUT_SECONDS}s)"}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {e}"}
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception as close_error:
                    logger.warning(f"Failed to close SSH test connection cleanly: {close_error}")


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

    async def run(self, command: str, check: bool = False, timeout_seconds: float | None = None) -> Any:
        """Run a command on the remote host."""
        if timeout_seconds and timeout_seconds > 0:
            result = await asyncio.wait_for(self._conn.run(command, check=check), timeout=timeout_seconds)
        else:
            result = await self._conn.run(command, check=check)
        return result

    async def run_checked(self, command: str, timeout_seconds: float | None = None) -> str:
        """Run a command and raise on non-zero exit. Returns stdout."""
        result = await self.run(command, check=True, timeout_seconds=timeout_seconds)
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

    async def list_dir(self, path: str) -> list[dict[str, str | int | None]]:
        """List one level of directory contents on the remote host."""
        script = (
            "python3 - <<'PY'\n"
            "import json, os\n"
            f"path = {path!r}\n"
            "entries = []\n"
            "with os.scandir(path) as it:\n"
            "    for entry in sorted(it, key=lambda item: item.name):\n"
            "        try:\n"
            "            stat = entry.stat(follow_symlinks=False)\n"
            "            kind = 'dir' if entry.is_dir(follow_symlinks=False) else 'symlink' if entry.is_symlink() else 'file'\n"
            "            entries.append({'name': entry.name, 'type': kind, 'size': int(stat.st_size)})\n"
            "        except OSError:\n"
            "            entries.append({'name': entry.name, 'type': 'unknown', 'size': None})\n"
            "print(json.dumps(entries))\n"
            "PY"
        )
        result = await self._conn.run(script, check=True)
        return json.loads(result.stdout or "[]")

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

    async def run(self, command: str, check: bool = False, timeout_seconds: float | None = None) -> SSHCommandResult:
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
                "timeout_seconds": timeout_seconds or LOCAL_AUTH_OPERATION_TIMEOUT_SECONDS,
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

    async def run_checked(self, command: str, timeout_seconds: float | None = None) -> str:
        result = await self.run(command, check=True, timeout_seconds=timeout_seconds)
        return result.stdout or ""

    async def path_exists(self, path: str) -> bool:
        result = await self.run(f"test -e {path!r} && echo YES || echo NO")
        return (result.stdout or "").strip() == "YES"

    async def path_is_writable(self, path: str) -> bool:
        result = await self.run(f"test -w {path!r} && echo YES || echo NO")
        return (result.stdout or "").strip() == "YES"

    async def mkdir_p(self, path: str) -> None:
        await self.run(f"mkdir -p {path!r}", check=True)

    async def list_dir(self, path: str) -> list[dict[str, str | int | None]]:
        script = (
            "python3 - <<'PY'\n"
            "import json, os\n"
            f"path = {path!r}\n"
            "entries = []\n"
            "with os.scandir(path) as it:\n"
            "    for entry in sorted(it, key=lambda item: item.name):\n"
            "        try:\n"
            "            stat = entry.stat(follow_symlinks=False)\n"
            "            kind = 'dir' if entry.is_dir(follow_symlinks=False) else 'symlink' if entry.is_symlink() else 'file'\n"
            "            entries.append({'name': entry.name, 'type': kind, 'size': int(stat.st_size)})\n"
            "        except OSError:\n"
            "            entries.append({'name': entry.name, 'type': 'unknown', 'size': None})\n"
            "print(json.dumps(entries))\n"
            "PY"
        )
        result = await self.run(script, check=True)
        return json.loads(result.stdout or "[]")

    async def close(self) -> None:
        return None
