"""Download orchestration and plan auto-execution background tasks.

Extracted from *app.py* to reduce its size.  Both functions are
``asyncio.create_task`` targets spawned from the ``update_block`` endpoint.
"""
from __future__ import annotations

import asyncio
import re
import uuid
import json

from pathlib import Path
from sqlalchemy import select

from common.logging_config import get_logger
from cortex.agent_engine import AgentEngine
from cortex.config import AGOUTIC_DATA
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock, User, Project
from cortex.db import SessionLocal
from cortex.db_helpers import _create_block_internal
from cortex.routes.files import _active_downloads, _download_files_background
from cortex.chat_sync_handler import _emit_progress
from cortex.chat_approval import (
    _handle_workflow_plan_remote_profile_auth,
    _ensure_workflow_plan_approval_gate,
)

logger = get_logger(__name__)


async def download_after_approval(project_id: str, gate_block_id: str):
    """Background task to start downloading files after approval.

    Downloads are written to the user's **central data folder** and symlinked
    into the project.  If the gate extracted_params contains a 'files' list
    (set by the download chain), use those directly.  Otherwise falls back to
    scanning conversation blocks for HTTP/HTTPS URLs.
    """
    session = SessionLocal()
    try:
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if not gate_block:
            logger.error("Gate block not found for download", gate_block_id=gate_block_id)
            return

        owner_id = gate_block.owner_id
        gate_payload = get_block_payload(gate_block)
        gate_params = gate_payload.get("extracted_params", {})

        # --- Preferred path: files list from download chain ---
        urls: list[dict] = []
        _target_dir_str: str | None = gate_params.get("target_dir")

        if gate_params.get("files"):
            for f in gate_params["files"]:
                urls.append({
                    "url": f["url"],
                    "filename": f.get("filename", f["url"].rsplit("/", 1)[-1].split("?")[0] or "file"),
                    "size_bytes": f.get("size_bytes"),
                })

        # --- Fallback: scan conversation blocks for URLs ---
        if not urls:
            query = select(ProjectBlock)\
                .where(ProjectBlock.project_id == project_id)\
                .order_by(ProjectBlock.seq.asc())
            blocks = session.execute(query).scalars().all()

            url_pattern = re.compile(r'https?://[^\s\)\"\'>\]]+')

            for blk in blocks:
                if blk.type not in ("AGENT_PLAN", "USER_MESSAGE"):
                    continue
                payload = get_block_payload(blk)
                text = payload.get("markdown", "") or payload.get("text", "")
                for match in url_pattern.finditer(text):
                    url = match.group(0).rstrip(".,;:")
                    # Skip obvious non-file URLs (API endpoints, portal pages)
                    if any(skip in url for skip in ["/api/", "/search/", "portal.encode"]):
                        continue
                    filename = url.rsplit("/", 1)[-1].split("?")[0] or "file"
                    # Avoid duplicates
                    if not any(u["url"] == url for u in urls):
                        urls.append({"url": url, "filename": filename})

        if not urls:
            _create_block_internal(
                session, project_id, "AGENT_PLAN",
                {
                    "markdown": "⚠️ No download URLs found in the conversation. "
                                "Please provide URLs or ENCODE file accessions and try again.",
                    "skill": "download_files",
                    "model": "system",
                },
                status="DONE",
                owner_id=owner_id,
            )
            return

        # Resolve target directory — prefer user's central data folder
        owner_user = session.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
        project_obj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        _username = getattr(owner_user, "username", None) if owner_user else None
        _slug = project_obj.slug if project_obj else None

        if _username:
            from cortex.user_jail import get_user_data_dir
            target_dir = get_user_data_dir(_username)
        elif _target_dir_str:
            target_dir = Path(_target_dir_str)
        else:
            if _username and _slug:
                project_dir = Path(AGOUTIC_DATA) / "users" / _username / _slug
            else:
                project_dir = Path(AGOUTIC_DATA) / "users" / owner_id / project_id
            target_dir = project_dir / "data"
        target_dir.mkdir(parents=True, exist_ok=True)

        download_id = str(uuid.uuid4())

        block = _create_block_internal(
            session, project_id, "DOWNLOAD_TASK",
            {
                "download_id": download_id,
                "source": "conversation",
                "files": [{"filename": u["filename"], "url": u["url"]} for u in urls],
                "target_dir": str(target_dir),
                "downloaded": 0,
                "total_files": len(urls),
                "bytes_downloaded": 0,
                "status": "RUNNING",
            },
            status="RUNNING",
            owner_id=owner_id,
        )

        _active_downloads[download_id] = {
            "block_id": block.id,
            "project_id": project_id,
            "cancelled": False,
        }

        asyncio.create_task(
            _download_files_background(
                download_id=download_id,
                project_id=project_id,
                block_id=block.id,
                owner_id=owner_id,
                files=urls,
                target_dir=target_dir,
                username=_username,
                project_slug=_slug,
                source="conversation",
            )
        )

    except Exception as e:
        logger.error("download_after_approval failed", error=str(e), exc_info=True)
    finally:
        session.close()


async def _auto_execute_plan_steps(
    project_id: str,
    workflow_block_id: str,
    user,
    model_name: str,
    *,
    request_id: str | None = None,
) -> None:
    """
    Background task: auto-execute safe initial steps of a plan.

    Opens a new DB session, loads the workflow block, and runs the plan
    executor for auto-executable steps. Stops at approval gates.
    """
    from cortex.plan_executor import execute_plan

    session = SessionLocal()
    try:
        workflow_block = session.execute(
            select(ProjectBlock).where(ProjectBlock.id == workflow_block_id)
        ).scalar_one_or_none()
        if not workflow_block:
            logger.warning("Plan execution: workflow block not found",
                          block_id=workflow_block_id)
            return

        engine = AgentEngine(model_key=model_name)
        await execute_plan(
            session, workflow_block,
            engine=engine,
            user=user,
            project_id=project_id,
            request_id=request_id,
            emit_progress=_emit_progress,
        )

        session.refresh(workflow_block)
        auth_result = await _handle_workflow_plan_remote_profile_auth(
            session,
            workflow_block,
            owner_id=user.id,
            model_name=engine.model_name,
        )
        if auth_result == "resume":
            session.refresh(workflow_block)
            await execute_plan(
                session,
                workflow_block,
                engine=engine,
                user=user,
                project_id=project_id,
                request_id=request_id,
                emit_progress=_emit_progress,
            )
            session.refresh(workflow_block)
        elif auth_result == "blocked":
            return

        await _ensure_workflow_plan_approval_gate(
            session,
            workflow_block,
            owner_id=user.id,
            model_name=engine.model_name,
        )
    except Exception as e:
        logger.error("Plan auto-execution failed", error=str(e), exc_info=True)
    finally:
        session.close()
