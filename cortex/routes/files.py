"""
File download & upload routes — extracted from cortex/app.py (Tier 3).

Handles ENCODE file downloads, arbitrary URL downloads,
and local file uploads into project data directories.
"""

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import cortex.db as _db
import cortex.db_helpers as _dbh
from cortex.dependencies import require_project_access
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock
from common.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()

# In-memory tracking of active downloads (keyed by download_id)
_active_downloads: dict[str, dict] = {}


class DownloadRequest(BaseModel):
    """Request to download one or more files into the project."""
    source: str  # "encode" or "url"
    files: list[dict]  # [{"url": str, "filename": str, "size_bytes": int | None}]
    subfolder: Optional[str] = "data"  # relative to project root


@router.post("/projects/{project_id}/downloads")
async def initiate_download(project_id: str, req: DownloadRequest, request: Request):
    """Start downloading files into the project's data directory.

    Creates a DOWNLOAD_TASK block and kicks off a background download task.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    if not req.files:
        raise HTTPException(status_code=422, detail="No files to download")

    session = _db.SessionLocal()
    try:
        project_dir = _dbh._resolve_project_dir(session, user, project_id)
        target_dir = project_dir / (req.subfolder or "data")
        target_dir.mkdir(parents=True, exist_ok=True)

        download_id = str(uuid.uuid4())

        # Create a DOWNLOAD_TASK block
        file_summaries = [
            {"filename": f.get("filename", "unknown"), "url": f["url"],
             "size_bytes": f.get("size_bytes")}
            for f in req.files
        ]
        block = _dbh._create_block_internal(
            session,
            project_id,
            "DOWNLOAD_TASK",
            {
                "download_id": download_id,
                "source": req.source,
                "files": file_summaries,
                "target_dir": str(target_dir),
                "downloaded": 0,
                "total_files": len(req.files),
                "bytes_downloaded": 0,
                "status": "RUNNING",
            },
            status="RUNNING",
            owner_id=user.id,
        )

        _active_downloads[download_id] = {
            "block_id": block.id,
            "project_id": project_id,
            "cancelled": False,
        }

        # Fire and forget background download
        asyncio.create_task(
            _download_files_background(
                download_id=download_id,
                project_id=project_id,
                block_id=block.id,
                owner_id=user.id,
                files=req.files,
                target_dir=target_dir,
            )
        )

        return {
            "download_id": download_id,
            "block_id": block.id,
            "target_dir": str(target_dir),
            "file_count": len(req.files),
        }
    finally:
        session.close()


@router.get("/projects/{project_id}/downloads/{download_id}")
async def get_download_status(project_id: str, download_id: str, request: Request):
    """Poll download progress."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    info = _active_downloads.get(download_id)
    if not info:
        raise HTTPException(status_code=404, detail="Download not found")

    session = _db.SessionLocal()
    try:
        block = session.query(ProjectBlock).filter(ProjectBlock.id == info["block_id"]).first()
        if not block:
            raise HTTPException(status_code=404, detail="Download block not found")
        return get_block_payload(block)
    finally:
        session.close()


@router.delete("/projects/{project_id}/downloads/{download_id}")
async def cancel_download(project_id: str, download_id: str, request: Request):
    """Cancel an in-progress download."""
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    info = _active_downloads.get(download_id)
    if not info:
        raise HTTPException(status_code=404, detail="Download not found")
    info["cancelled"] = True
    return {"status": "cancelling", "download_id": download_id}


async def _download_files_background(
    download_id: str,
    project_id: str,
    block_id: str,
    owner_id: str,
    files: list[dict],
    target_dir: Path,
):
    """Background task that streams files from URLs into the target directory.

    Provides per-file byte-level progress updates so the UI can render a
    progress bar.  Expected total size is computed from file_spec['size_bytes']
    when available.
    """
    session = _db.SessionLocal()
    downloaded_files = []
    total_bytes = 0
    # Expected total bytes across all files (may be 0 if sizes are unknown)
    expected_total_bytes = sum(f.get("size_bytes") or 0 for f in files)
    # Throttle progress-block writes to avoid DB spam (update at most every 0.5s)
    _last_progress_write = 0.0
    import time as _time

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(600.0)) as client:
            for i, file_spec in enumerate(files):
                dl_info = _active_downloads.get(download_id, {})
                if dl_info.get("cancelled"):
                    _update_download_block(session, block_id, {
                        "status": "CANCELLED",
                        "downloaded": i,
                        "message": "Download cancelled by user",
                    }, status="FAILED")
                    return

                url = file_spec["url"]
                filename = file_spec.get("filename") or url.rsplit("/", 1)[-1].split("?")[0] or f"file_{i}"
                dest = target_dir / filename
                file_expected = file_spec.get("size_bytes") or 0

                try:
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        # Use Content-Length header if per-file size is unknown
                        if not file_expected:
                            _cl = resp.headers.get("content-length")
                            if _cl and _cl.isdigit():
                                file_expected = int(_cl)
                        file_bytes = 0
                        with open(dest, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
                                file_bytes += len(chunk)
                                total_bytes += len(chunk)

                                # Throttled per-chunk progress update
                                _now = _time.monotonic()
                                if _now - _last_progress_write >= 0.5:
                                    _last_progress_write = _now
                                    _update_download_block(session, block_id, {
                                        "downloaded": i,
                                        "bytes_downloaded": total_bytes,
                                        "expected_total_bytes": expected_total_bytes,
                                        "current_file": filename,
                                        "current_file_bytes": file_bytes,
                                        "current_file_expected": file_expected,
                                    })

                    downloaded_files.append({
                        "filename": filename,
                        "size_bytes": file_bytes,
                        "path": str(dest),
                    })

                    # Per-file completion update
                    _update_download_block(session, block_id, {
                        "downloaded": i + 1,
                        "bytes_downloaded": total_bytes,
                        "expected_total_bytes": expected_total_bytes,
                        "current_file": filename,
                        "current_file_bytes": file_bytes,
                        "current_file_expected": file_expected,
                    })

                except Exception as e:
                    logger.error("Download failed for file", url=url, error=str(e))
                    downloaded_files.append({
                        "filename": filename,
                        "error": str(e),
                    })

        # All done — update block to DONE
        _update_download_block(session, block_id, {
            "status": "DONE",
            "downloaded": len(files),
            "bytes_downloaded": total_bytes,
            "downloaded_files": downloaded_files,
        }, status="DONE")

        # Generate post-download suggestions
        await _post_download_suggestions(session, project_id, owner_id, downloaded_files, target_dir)

    except Exception as e:
        logger.error("Download background task failed", download_id=download_id, error=str(e))
        _update_download_block(session, block_id, {
            "status": "FAILED",
            "message": str(e),
            "downloaded_files": downloaded_files,
        }, status="FAILED")
    finally:
        _active_downloads.pop(download_id, None)
        session.close()


def _update_download_block(session, block_id: str, updates: dict, status: str | None = None):
    """Update a DOWNLOAD_TASK block's payload (and optionally status)."""
    block = session.query(ProjectBlock).filter(ProjectBlock.id == block_id).first()
    if not block:
        return
    payload = get_block_payload(block)
    payload.update(updates)
    block.payload_json = json.dumps(payload)
    if status:
        block.status = status
    session.commit()


async def _post_download_suggestions(
    session, project_id: str, owner_id: str,
    downloaded_files: list[dict], target_dir: Path,
):
    """After downloads complete, create an AGENT_PLAN block with smart suggestions."""
    # Categorize files by extension
    seq_files = []  # .pod5, .bam, .fastq, .fastq.gz, .fq, .fq.gz
    table_files = []  # .csv, .tsv, .bed, .bedgraph
    other_files = []

    seq_exts = {".pod5", ".bam", ".fastq", ".fq", ".fast5"}
    table_exts = {".csv", ".tsv", ".bed", ".bedgraph", ".txt"}

    for f in downloaded_files:
        if f.get("error"):
            continue
        fname = f.get("filename", "")
        # Handle .gz extensions
        ext = Path(fname).suffix.lower()
        if ext == ".gz":
            ext = Path(fname).stem
            ext = Path(ext).suffix.lower()

        if ext in seq_exts:
            seq_files.append(f)
        elif ext in table_exts:
            table_files.append(f)
        else:
            other_files.append(f)

    # Build suggestion markdown
    lines = [f"**Download Complete** — {len(downloaded_files)} file(s) saved to `{target_dir.name}/`\n"]

    if seq_files:
        fnames = ", ".join(f.get("filename", "?") for f in seq_files)
        lines.append(f"**Sequencing files detected**: {fnames}")
        lines.append("These look like sequencing data. I can help you run the **Dogme pipeline** for DNA, RNA, or cDNA analysis.")
        lines.append("Would you like to set up a pipeline run?\n")
        lines.append("[[SKILL_SWITCH_TO: analyze_local_sample]]")

    if table_files:
        fnames = ", ".join(f.get("filename", "?") for f in table_files)
        lines.append(f"**Tabular files detected**: {fnames}")
        lines.append("I can load these into a dataframe for exploration and analysis.")
        lines.append("Would you like me to read and summarize them?\n")
        lines.append("[[SKILL_SWITCH_TO: analyze_job_results]]")

    if other_files and not seq_files and not table_files:
        fnames = ", ".join(f.get("filename", "?") for f in other_files)
        lines.append(f"**Files**: {fnames}")
        lines.append("Let me know what you'd like to do with these files.")

    suggestion_md = "\n".join(lines)

    _dbh._create_block_internal(
        session,
        project_id,
        "AGENT_PLAN",
        {
            "markdown": suggestion_md,
            "skill": "download_files",
            "model": "system",
        },
        status="DONE",
        owner_id=owner_id,
    )


# --- File Upload ---

@router.post("/projects/{project_id}/upload")
async def upload_file(project_id: str, request: Request):
    """Upload files into the project's data/ directory.

    Accepts multipart/form-data with field name 'files'.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    session = _db.SessionLocal()
    try:
        project_dir = _dbh._resolve_project_dir(session, user, project_id)
        data_dir = project_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
    finally:
        session.close()

    form = await request.form()
    uploaded = []

    for key in form:
        file = form[key]
        if not hasattr(file, "read"):
            continue  # skip non-file fields
        filename = getattr(file, "filename", key)
        # Sanitize filename
        safe_name = re.sub(r"[^\w.\-]", "_", filename)
        dest = data_dir / safe_name

        content = await file.read()
        with open(dest, "wb") as f:
            f.write(content)

        uploaded.append({
            "filename": safe_name,
            "size_bytes": len(content),
            "path": str(dest),
        })

    if not uploaded:
        raise HTTPException(status_code=422, detail="No files uploaded")

    # Create a system message about the upload to trigger agent suggestions
    session2 = _db.SessionLocal()
    try:
        await _post_download_suggestions(
            session2, project_id, user.id, uploaded, data_dir,
        )
    finally:
        session2.close()

    return {
        "uploaded": uploaded,
        "count": len(uploaded),
        "target_dir": str(data_dir),
    }
