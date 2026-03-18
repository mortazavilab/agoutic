"""Session manager for per-profile local-user auth brokers."""
from __future__ import annotations

import asyncio
import errno
import json
import os
import pty
import re
import select
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common.logging_config import get_logger
from launchpad.config import (
    AGOUTIC_CODE,
    LOCAL_AUTH_HELPER_START_TIMEOUT_SECONDS,
    LOCAL_AUTH_SESSION_TTL_SECONDS,
    LOCAL_AUTH_SOCKET_DIR,
)

logger = get_logger(__name__)


@dataclass
class LocalAuthSession:
    session_id: str
    profile_id: str
    user_id: str
    local_username: str
    helper_host: str
    helper_port: int
    port_file: str
    pid_file: str
    log_file: str
    auth_token: str
    helper_pid: int | None
    created_at: datetime
    last_used_at: datetime

    @property
    def expires_at(self) -> datetime:
        return self.last_used_at + timedelta(seconds=LOCAL_AUTH_SESSION_TTL_SECONDS)


def _session_key(user_id: str, profile_id: str) -> tuple[str, str]:
    return (user_id, profile_id)


def _ensure_socket_dir() -> None:
    LOCAL_AUTH_SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    if LOCAL_AUTH_SOCKET_DIR == Path("/tmp"):
        return
    try:
        # The helper broker runs as the target local Unix user, so this runtime
        # directory must be writable across that boundary while remaining scoped
        # to AGOUTIC rather than the global /tmp namespace.
        os.chmod(LOCAL_AUTH_SOCKET_DIR, 0o1777)
    except PermissionError:
        logger.warning("Could not chmod local auth socket dir", path=str(LOCAL_AUTH_SOCKET_DIR))


def _read_helper_log_excerpt(log_file: str, max_chars: int = 4000) -> str:
    path = Path(log_file)
    if not path.exists():
        return ""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _safe_remove_runtime_file(path: str) -> None:
    file_path = Path(path)
    try:
        if file_path.exists():
            file_path.unlink()
    except PermissionError:
        logger.warning("Could not remove local auth runtime file owned by another user", path=str(file_path))
    except OSError:
        logger.warning("Could not remove local auth runtime file", path=str(file_path))


def _safe_path_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _session_metadata_path(user_id: str, profile_id: str) -> Path:
    _ensure_socket_dir()
    return LOCAL_AUTH_SOCKET_DIR / (
        f"agoutic-local-auth-session-{_safe_path_component(user_id)}-{_safe_path_component(profile_id)}.json"
    )


def _write_session_metadata(session: LocalAuthSession) -> None:
    metadata_path = _session_metadata_path(session.user_id, session.profile_id)
    payload = {
        "session_id": session.session_id,
        "profile_id": session.profile_id,
        "user_id": session.user_id,
        "local_username": session.local_username,
        "helper_host": session.helper_host,
        "helper_port": session.helper_port,
        "port_file": session.port_file,
        "pid_file": session.pid_file,
        "log_file": session.log_file,
        "auth_token": session.auth_token,
        "helper_pid": session.helper_pid,
        "created_at": session.created_at.isoformat(),
        "last_used_at": session.last_used_at.isoformat(),
    }
    tmp_path = metadata_path.with_name(f"{metadata_path.name}.{uuid.uuid4().hex}.tmp")
    fd = None
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, metadata_path)
    except Exception:
        _safe_remove_runtime_file(str(tmp_path))
        raise
    finally:
        if fd is not None:
            os.close(fd)


def _load_session_metadata(user_id: str, profile_id: str) -> LocalAuthSession | None:
    metadata_path = _session_metadata_path(user_id, profile_id)
    if not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return LocalAuthSession(
            session_id=payload["session_id"],
            profile_id=payload["profile_id"],
            user_id=payload["user_id"],
            local_username=payload["local_username"],
            helper_host=payload["helper_host"],
            helper_port=int(payload["helper_port"]),
            port_file=payload["port_file"],
            pid_file=payload["pid_file"],
            log_file=payload["log_file"],
            auth_token=payload["auth_token"],
            helper_pid=payload.get("helper_pid"),
            created_at=datetime.fromisoformat(payload["created_at"]),
            last_used_at=datetime.fromisoformat(payload["last_used_at"]),
        )
    except Exception:
        logger.warning("Could not load local auth session metadata", path=str(metadata_path))
        _safe_remove_runtime_file(str(metadata_path))
        return None


def _remove_session_metadata(user_id: str, profile_id: str) -> None:
    _safe_remove_runtime_file(str(_session_metadata_path(user_id, profile_id)))


def _launch_helper_via_su(local_username: str, password: str, host: str, port_file: str, pid_file: str, log_file: str, auth_token: str) -> None:
    _ensure_socket_dir()
    broker_script = AGOUTIC_CODE / "launchpad" / "backends" / "local_user_broker.py"
    command = (
        f"cd {shlex.quote(str(AGOUTIC_CODE))} && "
        f"PYTHONPATH={shlex.quote(str(AGOUTIC_CODE))} "
        f"umask 022 && "
        f"nohup {shlex.quote(sys.executable)} {shlex.quote(str(broker_script))} "
        f"--host {shlex.quote(host)} --port-file {shlex.quote(port_file)} --pid-file {shlex.quote(pid_file)} --token {shlex.quote(auth_token)} "
        f">>{shlex.quote(log_file)} 2>&1 </dev/null &"
    )

    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["su", local_username, "-c", command],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    sent_password = False
    captured = bytearray()
    deadline = time.time() + LOCAL_AUTH_HELPER_START_TIMEOUT_SECONDS

    try:
        while time.time() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    # Linux PTYs commonly raise EIO when the slave side closes.
                    # Treat that as EOF rather than a launcher failure.
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                captured.extend(chunk)
                if not sent_password and b"password" in captured.lower():
                    os.write(master_fd, password.encode() + b"\n")
                    sent_password = True
            if proc.poll() is not None:
                break

        returncode = proc.wait(timeout=5)
    finally:
        os.close(master_fd)

    if returncode != 0:
        output = captured.decode(errors="replace").strip()
        helper_output = _read_helper_log_excerpt(log_file)
        if "authentication failure" in output.lower() or "sorry" in output.lower():
            raise PermissionError("Local account authentication failed. Check the local Unix password.")
        detail = helper_output or output or f"exit status {returncode}"
        raise RuntimeError(f"Failed to launch local auth broker via su: {detail}")


class LocalAuthSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, LocalAuthSession] = {}
        self._by_profile: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create_or_replace_session(self, profile: Any, local_password: str) -> LocalAuthSession:
        if not profile.local_username:
            raise ValueError("This SSH profile does not define a local_username")
        if not local_password:
            raise ValueError("local_password is required to start a local auth session")

        await self.cleanup_expired_sessions()
        key = _session_key(profile.user_id, profile.id)

        async with self._lock:
            existing_id = self._by_profile.get(key)
        if existing_id:
            await self.close_session(existing_id)
        else:
            existing_session = _load_session_metadata(profile.user_id, profile.id)
            if existing_session is not None:
                async with self._lock:
                    self._sessions[existing_session.session_id] = existing_session
                    self._by_profile[key] = existing_session.session_id
                await self.close_session(existing_session.session_id)

        session_id = uuid.uuid4().hex
        helper_host = "127.0.0.1"
        port_file = str(LOCAL_AUTH_SOCKET_DIR / f"agoutic-local-auth-{session_id}.port")
        pid_file = str(LOCAL_AUTH_SOCKET_DIR / f"agoutic-local-auth-{session_id}.pid")
        log_file = str(LOCAL_AUTH_SOCKET_DIR / f"agoutic-local-auth-{session_id}.log")
        auth_token = uuid.uuid4().hex

        await asyncio.to_thread(
            _launch_helper_via_su,
            profile.local_username,
            local_password,
            helper_host,
            port_file,
            pid_file,
            log_file,
            auth_token,
        )
        helper_pid, helper_port = await self._wait_until_ready(helper_host, port_file, pid_file, log_file, auth_token)

        now = datetime.now(timezone.utc)
        session = LocalAuthSession(
            session_id=session_id,
            profile_id=profile.id,
            user_id=profile.user_id,
            local_username=profile.local_username,
            helper_host=helper_host,
            helper_port=helper_port,
            port_file=port_file,
            pid_file=pid_file,
            log_file=log_file,
            auth_token=auth_token,
            helper_pid=helper_pid,
            created_at=now,
            last_used_at=now,
        )
        async with self._lock:
            self._sessions[session_id] = session
            self._by_profile[key] = session_id
        _write_session_metadata(session)
        return session

    async def get_active_session(self, profile: Any) -> LocalAuthSession | None:
        await self.cleanup_expired_sessions()
        key = _session_key(profile.user_id, profile.id)
        async with self._lock:
            session_id = self._by_profile.get(key)
            session = self._sessions.get(session_id) if session_id else None
        if session is None:
            session = _load_session_metadata(profile.user_id, profile.id)
            if session is not None:
                async with self._lock:
                    self._sessions[session.session_id] = session
                    self._by_profile[key] = session.session_id
        if session is None:
            return None
        if session.expires_at <= datetime.now(timezone.utc):
            try:
                await self.close_session(session.session_id)
            except Exception:
                logger.warning("Failed to close expired local auth session", session_id=session.session_id)
            return None
        try:
            await self.invoke(session, {"op": "ping"})
        except Exception:
            try:
                await self.close_session(session.session_id)
            except Exception:
                logger.warning("Failed to close invalid local auth session", session_id=session.session_id)
            return None
        return session

    async def get_session_metadata(self, profile: Any) -> dict[str, Any] | None:
        session = await self.get_active_session(profile)
        if session is None:
            return None
        return self._serialize_session(session)

    async def invoke(self, session: LocalAuthSession, payload: dict[str, Any]) -> dict[str, Any]:
        request = dict(payload)
        request["auth_token"] = session.auth_token
        reader, writer = await asyncio.open_connection(session.helper_host, session.helper_port)
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        raw = await reader.readline()
        writer.close()
        await writer.wait_closed()
        if not raw:
            raise RuntimeError("Local auth broker closed the connection unexpectedly")
        response = json.loads(raw.decode())
        session.last_used_at = datetime.now(timezone.utc)
        _write_session_metadata(session)
        return response

    async def close_session(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                self._by_profile.pop(_session_key(session.user_id, session.profile_id), None)
        if session is None:
            return False

        try:
            await self.invoke(session, {"op": "shutdown"})
        except Exception:
            if session.helper_pid:
                try:
                    os.kill(session.helper_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        for path in (session.port_file, session.pid_file, session.log_file):
            _safe_remove_runtime_file(path)
        _remove_session_metadata(session.user_id, session.profile_id)
        return True

    async def close_session_for_profile(self, profile: Any) -> bool:
        key = _session_key(profile.user_id, profile.id)
        async with self._lock:
            session_id = self._by_profile.get(key)
        if not session_id:
            return False
        return await self.close_session(session_id)

    async def close_all(self) -> None:
        async with self._lock:
            session_ids = list(self._sessions)
        for session_id in session_ids:
            await self.close_session(session_id)

    async def cleanup_expired_sessions(self) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=LOCAL_AUTH_SESSION_TTL_SECONDS)
        async with self._lock:
            expired_ids = [sid for sid, session in self._sessions.items() if session.last_used_at < cutoff]
        for session_id in expired_ids:
            await self.close_session(session_id)
        return expired_ids

    async def _wait_until_ready(self, helper_host: str, port_file: str, pid_file: str, log_file: str, auth_token: str) -> tuple[int | None, int]:
        deadline = time.time() + LOCAL_AUTH_HELPER_START_TIMEOUT_SECONDS
        while time.time() < deadline:
            port_path = Path(port_file)
            if port_path.exists():
                try:
                    helper_port = int(port_path.read_text().strip())
                    reader, writer = await asyncio.open_connection(helper_host, helper_port)
                    writer.write((json.dumps({"op": "ping", "auth_token": auth_token}) + "\n").encode())
                    await writer.drain()
                    raw = await reader.readline()
                    writer.close()
                    await writer.wait_closed()
                    if raw:
                        response = json.loads(raw.decode())
                        if response.get("ok"):
                            if Path(pid_file).exists():
                                try:
                                    return int(Path(pid_file).read_text().strip()), helper_port
                                except ValueError:
                                    return response.get("pid"), helper_port
                            return response.get("pid"), helper_port
                except (OSError, ValueError):
                    pass
            await asyncio.sleep(0.2)

        helper_output = _read_helper_log_excerpt(log_file)
        detail = helper_output or "No helper output was captured. The target local user may not be able to execute the AGOUTIC Python environment or read the AGOUTIC code directory."
        raise TimeoutError(f"Timed out waiting for local auth broker to start. {detail}")

    @staticmethod
    def _serialize_session(session: LocalAuthSession) -> dict[str, Any]:
        return {
            "active": True,
            "session_id": session.session_id,
            "profile_id": session.profile_id,
            "local_username": session.local_username,
            "created_at": session.created_at.isoformat(),
            "last_used_at": session.last_used_at.isoformat(),
            "expires_at": session.expires_at.isoformat(),
        }


_LOCAL_AUTH_MANAGER = LocalAuthSessionManager()


def get_local_auth_session_manager() -> LocalAuthSessionManager:
    return _LOCAL_AUTH_MANAGER