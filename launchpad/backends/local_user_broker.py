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
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RSYNC_SKIP_COMPRESS_SUFFIXES = (
    "3g2", "3gp", "7z", "aac", "ace", "apk", "avi", "bam", "bai",
    "bigwig", "bw", "bz2", "cram", "crai", "deb", "dmg", "ear",
    "f4v", "fast5", "flac", "flv", "gpg", "gz", "h5", "hdf5",
    "iso", "jar", "jpeg", "jpg", "lrz", "lz", "lz4", "lzma",
    "lzo", "m1a", "m1v", "m2a", "m2ts", "m2v", "m4a", "m4b",
    "m4p", "m4r", "m4v", "mka", "mkv", "mov", "mp1", "mp2",
    "mp3", "mp4", "mpa", "mpeg", "mpg", "mpv", "mts", "npy",
    "npz", "odb", "odf", "odg", "odi", "odm", "odp", "ods",
    "odt", "oga", "ogg", "ogm", "ogv", "ogx", "opus", "otg",
    "oth", "otp", "ots", "ott", "oxt", "parquet", "pickle",
    "pkl", "png", "pod5", "qt", "rar", "rpm", "rz", "rzip",
    "spx", "squashfs", "sxc", "sxd", "sxg", "sxm", "sxw", "sz",
    "tbz", "tbz2", "tgz", "tlz", "ts", "txz", "tzo", "vob",
    "war", "webm", "webp", "xz", "z", "zip", "zst",
)
_RSYNC_SKIP_COMPRESS = "/".join(_RSYNC_SKIP_COMPRESS_SUFFIXES)
_RSYNC_PARTIAL_DIR = ".rsync-partial"
_RSYNC_PROGRESS_CHUNK_SIZE = 4096
_ACTIVE_PROCESSES: dict[str, asyncio.subprocess.Process] = {}


def _seconds_label(value: float | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _rsync_stall_stderr(idle_timeout_seconds: float | None) -> str:
    return (
        "Rsync transfer stalled "
        f"- no output for {_seconds_label(idle_timeout_seconds)}s. "
        f"Partial files are preserved for retry in {_RSYNC_PARTIAL_DIR}."
    )


def _rsync_timeout_stderr(timeout_seconds: float | None) -> str:
    return (
        "Rsync transfer exceeded its timeout budget "
        f"after {_seconds_label(timeout_seconds)}s while waiting for rsync to finish. "
        f"Partial files may already exist in {_RSYNC_PARTIAL_DIR} at the transfer destination."
    )


def _normalize_local_rsync_source(source: str) -> str:
    """Append a trailing slash only when the local rsync source is a directory."""
    candidate = Path(source).expanduser()
    normalized = str(candidate)
    if candidate.exists() and candidate.is_dir() and not normalized.endswith("/"):
        return normalized + "/"
    return normalized


def _build_rsync_command(
    *,
    ssh_command: str,
    source: str,
    dest: str,
    include_patterns: list[str] | None,
    exclude_patterns: list[str] | None,
    copy_links: bool,
    copy_dirlinks: bool = False,
    use_skip_compress: bool = True,
) -> list[str]:
    cmd = [
        "rsync", "-avz", "--omit-dir-times", "--no-perms", f"--partial-dir={_RSYNC_PARTIAL_DIR}", "--progress",
        "-e", ssh_command,
    ]
    if use_skip_compress:
        cmd.append(f"--skip-compress={_RSYNC_SKIP_COMPRESS}")

    if copy_links:
        cmd.append("--copy-links")
    elif copy_dirlinks:
        cmd.append("--copy-dirlinks")

    for pattern in include_patterns or []:
        cmd.extend(["--include", pattern])
    for pattern in exclude_patterns or []:
        cmd.extend(["--exclude", pattern])

    cmd.extend([source, dest])
    return cmd


def _should_retry_without_skip_compress(*, exit_code: int | None, stderr_text: str) -> bool:
    if exit_code not in {1, 4}:
        return False
    lowered = stderr_text.lower()
    if "skip-compress" not in lowered:
        return False
    retry_markers = (
        "unknown option",
        "unrecognized option",
        "invalid option",
        "option not supported",
        "protocol incompatibility",
    )
    return any(marker in lowered for marker in retry_markers)
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


def _remaining_timeout_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.001, deadline - asyncio.get_running_loop().time())


async def _terminate_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: float = 5.0,
) -> None:
    if proc.returncode is not None:
        return

    try:
        if proc.pid:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.terminate()
        except ProcessLookupError:
            return

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass

    if proc.returncode is not None:
        return

    try:
        if proc.pid:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        return

    await proc.wait()


async def _run_subprocess(
    command: list[str],
    timeout_seconds: float | None = None,
    request_id: str | None = None,
    idle_timeout_seconds: float | None = None,
    stall_message: str | None = None,
    timeout_message: str | None = None,
) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    if request_id:
        _ACTIVE_PROCESSES[request_id] = proc
    try:
        if idle_timeout_seconds and idle_timeout_seconds > 0:
            async def _communicate_with_idle_timeout() -> tuple[bytes, bytes, int]:
                stdout_chunks: list[bytes] = []
                stalled = False
                while True:
                    try:
                        raw_chunk = await asyncio.wait_for(
                            proc.stdout.read(_RSYNC_PROGRESS_CHUNK_SIZE), timeout=idle_timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        stalled = True
                        break
                    if not raw_chunk:
                        break
                    stdout_chunks.append(raw_chunk)

                if stalled:
                    await _terminate_process_tree(proc)

                stderr_bytes = await proc.stderr.read()
                await proc.wait()
                stdout_bytes = b"".join(stdout_chunks)
                if stalled:
                    stall_text = stall_message or _rsync_stall_stderr(idle_timeout_seconds)
                    stderr_text = stderr_bytes.decode().strip()
                    if stderr_text:
                        stall_text = f"{stall_text}: {stderr_text}"
                    return stdout_bytes, stall_text.encode(), 124
                return stdout_bytes, stderr_bytes, proc.returncode

            run_coro = _communicate_with_idle_timeout()
        else:
            async def _communicate() -> tuple[bytes, bytes, int]:
                stdout_bytes, stderr_bytes = await proc.communicate()
                return stdout_bytes, stderr_bytes, proc.returncode

            run_coro = _communicate()

        if timeout_seconds and timeout_seconds > 0:
            stdout, stderr, returncode = await asyncio.wait_for(run_coro, timeout=timeout_seconds)
        else:
            stdout, stderr, returncode = await run_coro
    except asyncio.CancelledError:
        await _terminate_process_tree(proc)
        await proc.communicate()
        raise
    except asyncio.TimeoutError:
        await _terminate_process_tree(proc)
        stdout, stderr = await proc.communicate()
        stderr_text = (stderr.decode() if stderr else "").strip()
        timeout_msg = timeout_message or f"Operation timed out after {_seconds_label(timeout_seconds)}s"
        if stderr_text:
            timeout_msg = f"{timeout_msg}: {stderr_text}"
        return {
            "ok": False,
            "stdout": stdout.decode(),
            "stderr": timeout_msg,
            "exit_status": 124,
        }
    finally:
        if request_id and _ACTIVE_PROCESSES.get(request_id) is proc:
            _ACTIVE_PROCESSES.pop(request_id, None)
    return {
        "ok": returncode == 0,
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
        "exit_status": returncode,
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

    if op == "cancel_request":
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            return {"ok": False, "error": "request_id is required", "cancelled": False}
        proc = _ACTIVE_PROCESSES.pop(request_id, None)
        if proc is None:
            return {"ok": False, "error": f"No active request {request_id}", "cancelled": False}
        await _terminate_process_tree(proc)
        return {"ok": True, "request_id": request_id, "cancelled": True}

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
        source = request["source"]
        dest = request["dest"]
        # Only normalize local sources (uploads).  Remote rsync sources
        # (containing ":") must keep their trailing slash intact so rsync
        # copies directory contents, not the directory itself.
        if ":" not in source:
            source = _normalize_local_rsync_source(source)
        include_patterns = request.get("include_patterns") or []
        exclude_patterns = request.get("exclude_patterns") or []
        timeout_seconds = request.get("timeout_seconds")
        idle_timeout_seconds = request.get("idle_timeout_seconds")
        copy_links = bool(request.get("copy_links"))
        copy_dirlinks = bool(request.get("copy_dirlinks"))
        use_skip_compress = bool(request.get("use_skip_compress", True))
        request_id = str(request.get("request_id") or "").strip() or uuid.uuid4().hex

        cmd = _build_rsync_command(
            ssh_command=" ".join(_build_ssh_transport(profile)),
            source=source,
            dest=dest,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            copy_links=copy_links,
            copy_dirlinks=copy_dirlinks,
            use_skip_compress=use_skip_compress,
        )

        logger.info(
            "Local auth broker starting rsync_transfer host=%s user=%s timeout=%ss source=%s dest=%s",
            profile.get("ssh_host"),
            profile.get("ssh_username"),
            timeout_seconds,
            source,
            dest,
        )
        deadline = None
        if timeout_seconds and timeout_seconds > 0:
            deadline = asyncio.get_running_loop().time() + timeout_seconds
        result = await _run_subprocess(
            cmd,
            timeout_seconds=_remaining_timeout_seconds(deadline),
            request_id=request_id,
            idle_timeout_seconds=idle_timeout_seconds,
            stall_message=_rsync_stall_stderr(idle_timeout_seconds),
            timeout_message=_rsync_timeout_stderr(timeout_seconds),
        )
        if _should_retry_without_skip_compress(
            exit_code=result.get("exit_status"),
            stderr_text=result.get("stderr", ""),
        ):
            logger.warning(
                "Local auth broker retrying rsync_transfer without --skip-compress host=%s user=%s source=%s dest=%s",
                profile.get("ssh_host"),
                profile.get("ssh_username"),
                source,
                dest,
            )
            fallback_cmd = _build_rsync_command(
                ssh_command=" ".join(_build_ssh_transport(profile)),
                source=source,
                dest=dest,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                copy_links=copy_links,
                copy_dirlinks=copy_dirlinks,
                use_skip_compress=False,
            )
            result = await _run_subprocess(
                fallback_cmd,
                timeout_seconds=_remaining_timeout_seconds(deadline),
                request_id=request_id,
                idle_timeout_seconds=idle_timeout_seconds,
                stall_message=_rsync_stall_stderr(idle_timeout_seconds),
                timeout_message=_rsync_timeout_stderr(timeout_seconds),
            )
        result["bytes_transferred"] = _parse_rsync_bytes(result.get("stdout", ""))
        result["request_id"] = request_id
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