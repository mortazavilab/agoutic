"""Per-user local broker for SSH and rsync operations.

This helper is launched under the target local Unix account via `su`, then
serves simple JSON requests over a loopback TCP socket so Launchpad can reuse
that user's SSH key access without storing the password.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_local_rsync_source(source: str) -> str:
    """Append a trailing slash only when the local rsync source is a directory."""
    candidate = Path(source).expanduser()
    normalized = str(candidate)
    if candidate.exists() and candidate.is_dir() and not normalized.endswith("/"):
        return normalized + "/"
    return normalized
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SSH_KNOWN_HOSTS = os.getenv("SSH_KNOWN_HOSTS", "").strip() or None
SSH_STRICT_HOST_KEY_CHECKING = os.getenv("SSH_STRICT_HOST_KEY_CHECKING", "true").strip().lower() not in {"0", "false", "no"}
SSH_CONNECT_TIMEOUT_SECONDS = int(os.getenv("SSH_CONNECT_TIMEOUT_SECONDS", "600"))
SSH_CONNECTION_ATTEMPTS = int(os.getenv("SSH_CONNECTION_ATTEMPTS", "1"))


def _resolve_key_file_path(raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    return str(Path(raw_path).expanduser())


def _build_ssh_transport(profile: dict[str, Any]) -> list[str]:
    parts = ["ssh", "-p", str(profile.get("ssh_port", 22))]
    key_path = _resolve_key_file_path(profile.get("key_file_path"))
    if profile.get("auth_method") == "key_file" and key_path:
        parts.extend(["-i", key_path])
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
    return parts


def _build_ssh_command(profile: dict[str, Any], remote_command: str) -> list[str]:
    parts = _build_ssh_transport(profile)
    parts.append(f"{profile['ssh_username']}@{profile['ssh_host']}")
    parts.append(remote_command)
    return parts


def _parse_rsync_bytes(output: str) -> int:
    for line in output.split("\n"):
        if "total size" in line.lower():
            parts = line.split("is")
            if len(parts) >= 2:
                num_str = parts[1].split()[0].replace(",", "")
                try:
                    return int(num_str)
                except ValueError:
                    return 0
    return 0


async def _run_subprocess(command: list[str], timeout_seconds: float | None = None) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if timeout_seconds and timeout_seconds > 0:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        else:
            stdout, stderr = await proc.communicate()
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        timeout_label = int(timeout_seconds) if timeout_seconds else timeout_seconds
        stderr_text = (stderr.decode() if stderr else "").strip()
        timeout_msg = f"Operation timed out after {timeout_label}s"
        if stderr_text:
            timeout_msg = f"{timeout_msg}: {stderr_text}"
        return {
            "ok": False,
            "stdout": stdout.decode(),
            "stderr": timeout_msg,
            "exit_status": 124,
        }
    return {
        "ok": proc.returncode == 0,
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
        "exit_status": proc.returncode,
    }


async def _handle_request(request: dict[str, Any], shutdown_event: asyncio.Event, auth_token: str) -> dict[str, Any]:
    if request.get("auth_token") != auth_token:
        return {"ok": False, "error": "Unauthorized local auth broker request"}

    op = request.get("op")
    if op == "ping":
        return {"ok": True, "pid": os.getpid()}

    if op == "shutdown":
        shutdown_event.set()
        return {"ok": True}

    if op == "ssh_run":
        profile = request["profile"]
        remote_command = request["command"]
        timeout_seconds = request.get("timeout_seconds")
        logger.info(
            "Local auth broker starting ssh_run host=%s user=%s timeout=%ss",
            profile.get("ssh_host"),
            profile.get("ssh_username"),
            timeout_seconds,
        )
        result = await _run_subprocess(_build_ssh_command(profile, remote_command), timeout_seconds=timeout_seconds)
        logger.info(
            "Local auth broker finished ssh_run host=%s user=%s timeout=%ss exit_status=%s",
            profile.get("ssh_host"),
            profile.get("ssh_username"),
            timeout_seconds,
            result.get("exit_status"),
        )
        return result

    if op == "rsync_transfer":
        profile = request["profile"]
        source = _normalize_local_rsync_source(request["source"])
        dest = request["dest"]
        include_patterns = request.get("include_patterns") or []
        exclude_patterns = request.get("exclude_patterns") or []
        timeout_seconds = request.get("timeout_seconds")
        copy_links = bool(request.get("copy_links"))

        cmd = [
            "rsync", "-avz", "--partial", "--progress",
            "-e", " ".join(_build_ssh_transport(profile)),
        ]
        if copy_links:
            cmd.append("--copy-links")
        for pattern in include_patterns:
            cmd.extend(["--include", pattern])
        for pattern in exclude_patterns:
            cmd.extend(["--exclude", pattern])

        cmd.extend([source, dest])

        logger.info(
            "Local auth broker starting rsync_transfer host=%s user=%s timeout=%ss source=%s dest=%s",
            profile.get("ssh_host"),
            profile.get("ssh_username"),
            timeout_seconds,
            source,
            dest,
        )
        result = await _run_subprocess(cmd, timeout_seconds=timeout_seconds)
        result["bytes_transferred"] = _parse_rsync_bytes(result.get("stdout", ""))
        logger.info(
            "Local auth broker finished rsync_transfer host=%s user=%s timeout=%ss exit_status=%s bytes=%s source=%s dest=%s",
            profile.get("ssh_host"),
            profile.get("ssh_username"),
            timeout_seconds,
            result.get("exit_status"),
            result.get("bytes_transferred"),
            source,
            dest,
        )
        return result

    return {"ok": False, "error": f"Unsupported broker operation: {op}"}


async def _client_connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, shutdown_event: asyncio.Event, auth_token: str) -> None:
    try:
        raw = await reader.readline()
        if not raw:
            return
        request = json.loads(raw.decode())
        response = await _handle_request(request, shutdown_event, auth_token)
    except Exception as exc:  # pragma: no cover - defensive broker boundary
        response = {"ok": False, "error": str(exc)}

    writer.write((json.dumps(response) + "\n").encode())
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _run_server(host: str, port_file: str, pid_file: str | None, auth_token: str) -> None:
    shutdown_event = asyncio.Event()
    port_path = Path(port_file)
    port_path.parent.mkdir(parents=True, exist_ok=True)
    if port_path.exists():
        port_path.unlink()

    server = await asyncio.start_server(
        lambda r, w: _client_connected(r, w, shutdown_event, auth_token),
        host=host,
        port=0,
    )
    socket_info = server.sockets[0].getsockname() if server.sockets else None
    if not socket_info:
        raise RuntimeError("Local auth broker did not expose a listening socket")
    listening_port = int(socket_info[1])
    port_path.write_text(str(listening_port))
    os.chmod(port_path, 0o644)

    if pid_file:
        pid_path = Path(pid_file)
        pid_path.write_text(str(os.getpid()))
        os.chmod(pid_path, 0o644)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:  # pragma: no cover - platform dependent
            pass

    logger.info("Local auth broker ready on %s:%s (pid=%s)", host, listening_port, os.getpid())
    await shutdown_event.wait()

    server.close()
    await server.wait_closed()
    if port_path.exists():
        port_path.unlink()
    if pid_file:
        pid_path = Path(pid_file)
        if pid_path.exists():
            pid_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="AGOUTIC local auth broker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port-file", required=True)
    parser.add_argument("--pid-file")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_run_server(args.host, args.port_file, args.pid_file, args.token))
    except Exception:
        logger.exception("Local auth broker failed during startup")
        raise


if __name__ == "__main__":
    main()