from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.config import get_service_url
from cortex.conversation_state import _extract_job_context_from_history
from cortex.path_helpers import _resolve_workflow_path

logger = get_logger(__name__)

_USAGE = (
    "Usage: /read-file <path> [--lines N] "
    "[--mode auto|plain|markdown|html_text|html_raw]"
)
_VALID_RENDER_MODES = {"auto", "plain", "markdown", "html_text", "html_raw"}
_NL_DOWNLOAD_RE = re.compile(r"^(?:please\s+)?download\s+(.+?)\s*$", re.IGNORECASE | re.DOTALL)


@dataclass
class FileCommand:
    action: Literal["read", "download"]
    file_ref: str = ""
    preview_lines: int | None = None
    render_mode: str | None = None
    error: str = ""


def parse_file_command(message: str) -> FileCommand | None:
    msg = str(message or "").strip()
    if not msg.startswith("/"):
        return None

    try:
        tokens = shlex.split(msg)
    except ValueError as exc:
        return FileCommand(action="read", error=f"Could not parse /read-file arguments: {exc}")

    if not tokens or tokens[0].lower() != "/read-file":
        return None

    file_tokens: list[str] = []
    preview_lines: int | None = None
    render_mode: str | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"--lines", "-n"}:
            if index + 1 >= len(tokens):
                return FileCommand(action="read", error="Missing value for --lines.")
            try:
                preview_lines = int(tokens[index + 1])
            except ValueError:
                return FileCommand(action="read", error="--lines must be an integer.")
            if preview_lines <= 0:
                return FileCommand(action="read", error="--lines must be greater than zero.")
            index += 2
            continue
        if token.startswith("--lines="):
            try:
                preview_lines = int(token.split("=", 1)[1])
            except ValueError:
                return FileCommand(action="read", error="--lines must be an integer.")
            if preview_lines <= 0:
                return FileCommand(action="read", error="--lines must be greater than zero.")
            index += 1
            continue
        if token == "--mode":
            if index + 1 >= len(tokens):
                return FileCommand(action="read", error="Missing value for --mode.")
            render_mode = tokens[index + 1].strip().lower()
            index += 2
            continue
        if token.startswith("--mode="):
            render_mode = token.split("=", 1)[1].strip().lower()
            index += 1
            continue
        if token == "--raw-html":
            render_mode = "html_raw"
            index += 1
            continue
        if token == "--html-text":
            render_mode = "html_text"
            index += 1
            continue
        if token.startswith("--"):
            return FileCommand(action="read", error=f"Unknown option: {token}")

        file_tokens.append(token)
        index += 1

    if render_mode and render_mode not in _VALID_RENDER_MODES:
        return FileCommand(
            action="read",
            file_ref=" ".join(file_tokens).strip(),
            preview_lines=preview_lines,
            error=(
                f"Unsupported render mode '{render_mode}'. "
                "Use auto, plain, markdown, html_text, or html_raw."
            ),
        )

    return FileCommand(
        action="read",
        file_ref=" ".join(file_tokens).strip(),
        preview_lines=preview_lines,
        render_mode=render_mode,
    )


def _file_usage_message(detail: str = "") -> str:
    if detail:
        return f"{detail}\n\n{_USAGE}"
    return _USAGE


def _clean_file_ref(file_ref: str) -> str:
    cleaned = str(file_ref or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'", "`"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned.rstrip("?.!,;:")


def _looks_like_local_download_ref(file_ref: str) -> bool:
    cleaned = _clean_file_ref(file_ref)
    if not cleaned:
        return False

    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://")):
        return False
    if " from " in lowered:
        return False
    if os.path.isabs(cleaned):
        return True
    if re.match(r"^(workflow\d+|summaries|data)(?:/|$)", cleaned, re.IGNORECASE):
        return True
    if "/" in cleaned:
        return True

    return bool(re.search(r"\.[A-Za-z0-9]{1,8}(?:\.gz)?$", Path(cleaned).name))


def detect_file_intent(message: str) -> FileCommand | None:
    msg = str(message or "").strip()
    if not msg or msg.startswith("/"):
        return None

    match = _NL_DOWNLOAD_RE.match(msg)
    if not match:
        return None

    file_ref = _clean_file_ref(match.group(1))
    if not _looks_like_local_download_ref(file_ref):
        return None

    return FileCommand(action="download", file_ref=file_ref)


def _resolve_direct_target(
    file_ref: str,
    *,
    default_work_dir: str,
    project_dir: str,
    workflows: list[dict],
) -> tuple[str, str, bool]:
    stripped = str(file_ref or "").strip()
    if not stripped:
        return default_work_dir or project_dir, "", False

    if os.path.isabs(stripped):
        candidate = Path(stripped).resolve()
        base_paths = [wf.get("work_dir", "") for wf in workflows]
        base_paths.extend([default_work_dir, project_dir])
        unique_bases = sorted({base for base in base_paths if base}, key=len, reverse=True)
        for base in unique_bases:
            base_path = Path(base).resolve()
            try:
                relative = candidate.relative_to(base_path)
            except ValueError:
                continue
            return str(base_path), relative.as_posix(), True
        return "", "", True

    normalized = stripped.replace("\\", "/").strip("/")
    if re.match(r"^workflow\d+(?:/|$)", normalized, re.IGNORECASE):
        workflow_name, _, remainder = normalized.partition("/")
        workflow_dir = _resolve_workflow_path(workflow_name, project_dir or default_work_dir, workflows)
        return workflow_dir, remainder, True

    if "/" in normalized:
        return default_work_dir or project_dir, normalized, True

    return default_work_dir or project_dir, Path(normalized).name, False


def _format_file_read_response(result: dict, *, requested_path: str) -> str:
    resolved_path = str(result.get("file_path") or requested_path)
    render_mode = str(result.get("render_mode") or "plain")
    source_extension = str(result.get("source_extension") or Path(resolved_path).suffix.lower())
    file_size = result.get("file_size_bytes")
    line_count = result.get("line_count")
    content = str(result.get("content") or "")

    metadata: list[str] = []
    if line_count is not None:
        metadata.append(f"{line_count} lines")
    if isinstance(file_size, int) and file_size >= 0:
        metadata.append(f"{file_size} bytes")
    metadata.append(f"rendered as {render_mode}")

    if render_mode == "html_raw":
        fence = "html"
    elif render_mode == "markdown" or source_extension in {".md", ".markdown"}:
        fence = "markdown"
    else:
        fence = "text"

    header = f"**File: {resolved_path}**"
    if metadata:
        header += f" ({', '.join(metadata)})"

    body = "*File is empty.*"
    if content:
        body = f"```{fence}\n{content.rstrip()}\n```"

    if result.get("is_truncated"):
        body += "\n\n*Preview truncated.*"

    return f"{header}\n\n{body}".strip()


def _format_file_download_response(*, requested_path: str, resolved_path: str) -> str:
    return (
        f"**Ready to download:** `{Path(resolved_path).name}`\n\n"
        f"Path: `{resolved_path}`\n\n"
        "Use the download control below this message to save the file."
    )


async def execute_file_command(
    command: FileCommand,
    *,
    history_blocks: list | None = None,
    project_dir: str = "",
) -> str:
    if command.error:
        return _file_usage_message(command.error)
    if not command.file_ref:
        if command.action == "download":
            return "Provide a file path to download."
        return _file_usage_message("Provide a file path to read.")

    context = _extract_job_context_from_history(None, history_blocks=history_blocks)
    workflows = context.get("workflows") or []
    default_work_dir = str(context.get("work_dir") or "") or project_dir

    if not default_work_dir and not project_dir:
        if command.action == "download":
            return (
                "I could not resolve an active workflow or project directory for download. "
                "Use /use <workflow> first or provide a workflow-prefixed path such as "
                "workflow10/reconciled_summary.txt."
            )
        return (
            "I could not resolve an active workflow or project directory for /read-file. "
            "Use /use <workflow> first or provide a workflow-prefixed path such as "
            "workflow10/reconciled_summary.txt."
        )

    direct_work_dir, direct_path, direct_requested = _resolve_direct_target(
        command.file_ref,
        default_work_dir=default_work_dir,
        project_dir=project_dir,
        workflows=workflows,
    )

    if direct_requested and not direct_work_dir:
        if command.action == "download":
            return (
                "I can only download files inside the active project or workflow context. "
                "Use a workflow-relative path such as workflow10/report.html."
            )
        return (
            "I can only read files inside the active project or workflow context. "
            "Use a workflow-relative path such as workflow10/report.html."
        )
    if direct_requested and not direct_path:
        if command.action == "download":
            return "Provide a file path to download, not just a workflow directory."
        return _file_usage_message("Provide a file path, not just a workflow directory.")

    read_preview_lines = command.preview_lines or 120
    read_render_mode = command.render_mode or "auto"

    analyzer_url = get_service_url("analyzer")
    client = MCPHttpClient(name="analyzer", base_url=analyzer_url)
    await client.connect()
    try:
        if command.action == "download" and direct_requested:
            candidate_path = Path(direct_work_dir) / direct_path
            if candidate_path.exists() and candidate_path.is_file():
                return _format_file_download_response(
                    requested_path=command.file_ref,
                    resolved_path=str(candidate_path),
                )

        if direct_requested:
            direct_result = await client.call_tool(
                "read_file_content",
                file_path=direct_path,
                work_dir=direct_work_dir,
                preview_lines=read_preview_lines,
                render_mode=read_render_mode,
            )
            if isinstance(direct_result, dict) and direct_result.get("success") is not False:
                return _format_file_read_response(direct_result, requested_path=command.file_ref)

            detail = ""
            if isinstance(direct_result, dict):
                detail = str(direct_result.get("detail") or direct_result.get("error") or "")
            if "/" in command.file_ref or os.path.isabs(command.file_ref):
                return f"Could not read {command.file_ref}. {detail}".strip()

        search_work_dir = direct_work_dir or default_work_dir or project_dir
        if not search_work_dir:
            return "I could not resolve where to search for that file."

        find_result = await client.call_tool(
            "find_file",
            file_name=Path(command.file_ref).name,
            work_dir=search_work_dir,
        )
        if not isinstance(find_result, dict) or find_result.get("success") is False:
            detail = ""
            if isinstance(find_result, dict):
                detail = str(find_result.get("detail") or find_result.get("error") or "")
            return f"I could not find {command.file_ref}. {detail}".strip()

        primary_path = str(find_result.get("primary_path") or "")
        resolved_work_dir = str(find_result.get("work_dir") or search_work_dir)
        if not primary_path:
            return f"I could not resolve a concrete path for {command.file_ref}."

        if command.action == "download":
            candidate_path = Path(resolved_work_dir) / primary_path
            if not candidate_path.exists() or not candidate_path.is_file():
                return f"I found {primary_path} but the file is no longer available on disk."
            return _format_file_download_response(
                requested_path=command.file_ref,
                resolved_path=str(candidate_path),
            )

        read_result = await client.call_tool(
            "read_file_content",
            file_path=primary_path,
            work_dir=resolved_work_dir,
            preview_lines=read_preview_lines,
            render_mode=read_render_mode,
        )
        if not isinstance(read_result, dict) or read_result.get("success") is False:
            detail = ""
            if isinstance(read_result, dict):
                detail = str(read_result.get("detail") or read_result.get("error") or "")
            return f"I found {primary_path} but could not read it. {detail}".strip()

        return _format_file_read_response(read_result, requested_path=command.file_ref)
    except Exception as exc:
        logger.error("File command failed", file_ref=command.file_ref, error=str(exc))
        return f"/read-file failed: {exc}"
    finally:
        await client.disconnect()