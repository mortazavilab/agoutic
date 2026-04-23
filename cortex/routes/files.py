"""
File download & upload routes — extracted from cortex/app.py (Tier 3).

Handles ENCODE file downloads, arbitrary URL downloads,
and local file uploads into project data directories.

Downloads and uploads now target the user's **central data folder**
(``AGOUTIC_DATA/users/{username}/data/``) and create symlinks inside
the project's ``data/`` directory.  Duplicate files (same filename +
MD5 hash) are detected and reused instead of re-downloaded.
"""

import asyncio
import hashlib
import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

import cortex.db as _db
import cortex.db_helpers as _dbh
from cortex.dependencies import require_project_access
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock, UserFile, UserFileProjectLink, User, Project
from cortex.user_jail import get_user_data_dir, create_project_file_symlink
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


# ---------------------------------------------------------------------------
# Helpers – user / project resolution & MD5 streaming
# ---------------------------------------------------------------------------

def _resolve_user_and_project(session, user, project_id: str):
    """Return (username, project_slug, project_dir) for the current user+project.

    Falls back to legacy UUID-based paths when slug metadata is missing.
    """
    project_obj = session.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one_or_none()
    username = getattr(user, "username", None)
    slug = project_obj.slug if project_obj else None

    if username and slug:
        from cortex.config import AGOUTIC_DATA
        project_dir = Path(AGOUTIC_DATA) / "users" / username / slug
    else:
        from cortex.config import AGOUTIC_DATA
        project_dir = Path(AGOUTIC_DATA) / "users" / user.id / project_id

    return username, slug, project_dir


def _compute_md5(path: Path, chunk_size: int = 65536) -> str:
    """Compute MD5 hex digest of a file on disk."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _register_user_file(
    session,
    *,
    user_id: str,
    filename: str,
    disk_path: Path,
    size_bytes: int,
    md5_hash: str,
    source: str = "url",
    source_url: str | None = None,
    encode_accession: str | None = None,
) -> UserFile:
    """Insert a UserFile row and return it."""
    uf = UserFile(
        id=str(uuid.uuid4()),
        user_id=user_id,
        filename=filename,
        md5_hash=md5_hash,
        size_bytes=size_bytes,
        source=source,
        source_url=source_url,
        encode_accession=encode_accession,
        disk_path=str(disk_path),
    )
    session.add(uf)
    session.commit()
    return uf


def _link_file_to_project(
    session,
    *,
    user_file: UserFile,
    project_id: str,
    symlink_path: Path,
) -> UserFileProjectLink:
    """Create a UserFileProjectLink row and return it."""
    link = UserFileProjectLink(
        id=str(uuid.uuid4()),
        user_file_id=user_file.id,
        project_id=project_id,
        symlink_path=str(symlink_path),
    )
    session.add(link)
    session.commit()
    return link


def _find_existing_user_file(session, user_id: str, filename: str) -> UserFile | None:
    """Look up an existing UserFile by user + filename."""
    return session.execute(
        select(UserFile).where(
            UserFile.user_id == user_id,
            UserFile.filename == filename,
        )
    ).scalar_one_or_none()


def _already_linked(session, user_file_id: str, project_id: str) -> bool:
    """Check if a UserFile is already linked to a project."""
    return session.execute(
        select(UserFileProjectLink).where(
            UserFileProjectLink.user_file_id == user_file_id,
            UserFileProjectLink.project_id == project_id,
        )
    ).scalar_one_or_none() is not None


@router.post("/projects/{project_id}/downloads")
async def initiate_download(project_id: str, req: DownloadRequest, request: Request):
    """Start downloading files into the user's central data directory.

    Files are written to ``AGOUTIC_DATA/users/{username}/data/`` and
    symlinked into the project's ``data/`` folder.  If a file with the
    same name and MD5 already exists, the download is skipped and a
    symlink is created instead.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    if not req.files:
        raise HTTPException(status_code=422, detail="No files to download")

    session = _db.SessionLocal()
    try:
        username, slug, project_dir = _resolve_user_and_project(session, user, project_id)

        # Central data directory for this user
        if username:
            central_dir = get_user_data_dir(username)
        else:
            # Legacy fallback — write directly into project
            central_dir = project_dir / (req.subfolder or "data")
        central_dir.mkdir(parents=True, exist_ok=True)

        # Also ensure project data/ exists (for symlinks)
        project_data_dir = project_dir / "data"
        project_data_dir.mkdir(parents=True, exist_ok=True)

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
                "target_dir": str(central_dir),
                "project_data_dir": str(project_data_dir),
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
                target_dir=central_dir,
                username=username,
                project_slug=slug,
                source=req.source,
            )
        )

        return {
            "download_id": download_id,
            "block_id": block.id,
            "target_dir": str(central_dir),
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
    username: str | None = None,
    project_slug: str | None = None,
    source: str = "url",
):
    """Background task that streams files from URLs into the central data dir.

    For each file:
    1. Check if an identical file already exists (same name + MD5 on disk).
       If so, skip the download and just create a project symlink.
    2. Otherwise download to ``target_dir`` (the user's central data folder),
       compute MD5, register in ``user_files`` table, and create a project
       symlink.

    Provides per-file byte-level progress updates so the UI can render a
    progress bar.
    """
    session = _db.SessionLocal()
    downloaded_files = []
    total_bytes = 0
    expected_total_bytes = sum(f.get("size_bytes") or 0 for f in files)
    _last_progress_write = 0.0
    import time as _time

    # Organise downloads into a date-based subfolder (YYYY-MM-DD)
    download_subdir = target_dir / date.today().isoformat()
    download_subdir.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(600.0)) as client:
            for i, file_spec in enumerate(files):
                dl_info = _active_downloads.get(download_id, {})
                if dl_info.get("cancelled"):
                    _cancel_parts = [f"Download cancelled by user. {i} of {len(files)} file(s) completed."]
                    _update_download_block(session, block_id, {
                        "status": "CANCELLED",
                        "downloaded": i,
                        "total_files": len(files),
                        "message": " ".join(_cancel_parts),
                        "downloaded_files": downloaded_files,
                    }, status="FAILED")
                    return

                url = file_spec["url"]
                filename = file_spec.get("filename") or url.rsplit("/", 1)[-1].split("?")[0] or f"file_{i}"
                dest = download_subdir / filename
                file_expected = file_spec.get("size_bytes") or 0

                # --- Dedup check ---------------------------------------------------
                existing_uf = _find_existing_user_file(session, owner_id, filename)
                if existing_uf and Path(existing_uf.disk_path).exists():
                    # File already in central folder — verify MD5 still matches
                    existing_md5 = _compute_md5(Path(existing_uf.disk_path))
                    if existing_md5 == existing_uf.md5_hash:
                        # Exact duplicate — skip download, just link to project
                        if username and project_slug:
                            sym = create_project_file_symlink(
                                username, project_slug, filename,
                                Path(existing_uf.disk_path),
                            )
                            if not _already_linked(session, existing_uf.id, project_id):
                                _link_file_to_project(
                                    session,
                                    user_file=existing_uf,
                                    project_id=project_id,
                                    symlink_path=sym,
                                )

                        downloaded_files.append({
                            "filename": filename,
                            "size_bytes": existing_uf.size_bytes or 0,
                            "path": existing_uf.disk_path,
                            "deduped": True,
                        })

                        _update_download_block(session, block_id, {
                            "downloaded": i + 1,
                            "bytes_downloaded": total_bytes,
                            "expected_total_bytes": expected_total_bytes,
                            "current_file": filename,
                            "current_file_status": "deduped",
                        })
                        continue
                # --- End dedup check -----------------------------------------------

                try:
                    md5_hasher = hashlib.md5()
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        if not file_expected:
                            _cl = resp.headers.get("content-length")
                            if _cl and _cl.isdigit():
                                file_expected = int(_cl)
                        file_bytes = 0
                        with open(dest, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                # Check for cancellation during streaming
                                _dl_info_inner = _active_downloads.get(download_id, {})
                                if _dl_info_inner.get("cancelled"):
                                    break

                                f.write(chunk)
                                md5_hasher.update(chunk)
                                file_bytes += len(chunk)
                                total_bytes += len(chunk)

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

                    # If cancelled mid-stream, clean up partial file and exit
                    _dl_info_check = _active_downloads.get(download_id, {})
                    if _dl_info_check.get("cancelled"):
                        _deleted_partial = None
                        if dest.exists():
                            dest.unlink(missing_ok=True)
                            _deleted_partial = filename
                            logger.info("Deleted partial download file", path=str(dest))
                        _cancel_parts = [f"Download cancelled by user. {i} of {len(files)} file(s) completed."]
                        if _deleted_partial:
                            _cancel_parts.append(f"Deleted partial file: {_deleted_partial}")
                        _update_download_block(session, block_id, {
                            "status": "CANCELLED",
                            "downloaded": i,
                            "total_files": len(files),
                            "message": " ".join(_cancel_parts),
                            "deleted_partial": _deleted_partial,
                            "downloaded_files": downloaded_files,
                        }, status="FAILED")
                        return

                    md5_hex = md5_hasher.hexdigest()

                    # Detect ENCODE accession from filename (ENCFF pattern)
                    _enc_match = re.match(r"(ENCFF\w+)", filename)
                    encode_acc = _enc_match.group(1) if _enc_match else None

                    # Register in user_files table
                    uf = _register_user_file(
                        session,
                        user_id=owner_id,
                        filename=filename,
                        disk_path=dest,
                        size_bytes=file_bytes,
                        md5_hash=md5_hex,
                        source=source if source in ("encode", "url", "upload", "local_intake") else "url",
                        source_url=url,
                        encode_accession=encode_acc,
                    )

                    # Create project symlink
                    if username and project_slug:
                        sym = create_project_file_symlink(
                            username, project_slug, filename, dest,
                        )
                        _link_file_to_project(
                            session,
                            user_file=uf,
                            project_id=project_id,
                            symlink_path=sym,
                        )

                    downloaded_files.append({
                        "filename": filename,
                        "size_bytes": file_bytes,
                        "path": str(dest),
                    })

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
                    # Clean up partial file on error
                    if dest.exists():
                        dest.unlink(missing_ok=True)
                        logger.info("Deleted partial file after error", path=str(dest))
                    downloaded_files.append({
                        "filename": filename,
                        "error": str(e),
                    })

        # All done
        _update_download_block(session, block_id, {
            "status": "DONE",
            "downloaded": len(files),
            "bytes_downloaded": total_bytes,
            "downloaded_files": downloaded_files,
        }, status="DONE")

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
    deduped = [f for f in downloaded_files if f.get("deduped")]
    fresh = [f for f in downloaded_files if not f.get("deduped") and not f.get("error")]

    lines = []
    if deduped and not fresh:
        lines.append(
            f"**Files already available** — {len(deduped)} file(s) linked to this project "
            f"(no re-download needed)\n"
        )
    elif deduped:
        lines.append(
            f"**Download Complete** — {len(fresh)} new file(s) downloaded, "
            f"{len(deduped)} reused from your data folder\n"
        )
    else:
        lines.append(f"**Download Complete** — {len(downloaded_files)} file(s) saved to `{target_dir.name}/`\n")

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
    """Upload files into the user's central data directory.

    Writes files to ``AGOUTIC_DATA/users/{username}/data/``, registers
    them in ``user_files``, and creates symlinks into the project's
    ``data/`` folder.  Duplicate uploads (same name + MD5) are reused.

    Accepts multipart/form-data with field name 'files'.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    session = _db.SessionLocal()
    try:
        username, slug, project_dir = _resolve_user_and_project(session, user, project_id)

        if username:
            central_dir = get_user_data_dir(username)
        else:
            central_dir = project_dir / "data"
        central_dir.mkdir(parents=True, exist_ok=True)

        # Organise uploads into a date-based subfolder (YYYY-MM-DD)
        upload_subdir = central_dir / date.today().isoformat()
        upload_subdir.mkdir(parents=True, exist_ok=True)

        project_data_dir = project_dir / "data"
        project_data_dir.mkdir(parents=True, exist_ok=True)
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
        dest = upload_subdir / safe_name

        content = await file.read()

        # Compute MD5 before writing
        md5_hex = hashlib.md5(content).hexdigest()

        # Dedup check
        session2 = _db.SessionLocal()
        try:
            existing_uf = _find_existing_user_file(session2, user.id, safe_name)
            if (
                existing_uf
                and Path(existing_uf.disk_path).exists()
                and existing_uf.md5_hash == md5_hex
            ):
                # Already have this exact file — just link to project
                if username and slug:
                    sym = create_project_file_symlink(
                        username, slug, safe_name,
                        Path(existing_uf.disk_path),
                    )
                    if not _already_linked(session2, existing_uf.id, project_id):
                        _link_file_to_project(
                            session2,
                            user_file=existing_uf,
                            project_id=project_id,
                            symlink_path=sym,
                        )
                uploaded.append({
                    "filename": safe_name,
                    "size_bytes": existing_uf.size_bytes or len(content),
                    "path": existing_uf.disk_path,
                    "deduped": True,
                })
                continue

            # Write new file
            with open(dest, "wb") as f:
                f.write(content)

            uf = _register_user_file(
                session2,
                user_id=user.id,
                filename=safe_name,
                disk_path=dest,
                size_bytes=len(content),
                md5_hash=md5_hex,
                source="upload",
            )

            if username and slug:
                sym = create_project_file_symlink(username, slug, safe_name, dest)
                _link_file_to_project(
                    session2,
                    user_file=uf,
                    project_id=project_id,
                    symlink_path=sym,
                )

            uploaded.append({
                "filename": safe_name,
                "size_bytes": len(content),
                "path": str(dest),
            })
        finally:
            session2.close()

    if not uploaded:
        raise HTTPException(status_code=422, detail="No files uploaded")

    # Create a system message about the upload to trigger agent suggestions
    session3 = _db.SessionLocal()
    try:
        await _post_download_suggestions(
            session3, project_id, user.id, uploaded, central_dir,
        )
    finally:
        session3.close()

    return {
        "uploaded": uploaded,
        "count": len(uploaded),
        "target_dir": str(central_dir),
    }
